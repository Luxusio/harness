package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.ContextConfig
import io.crscube.clitools.confluence.ContextFiles
import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import java.nio.file.*
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.PosixFilePermissions
import java.nio.channels.FileChannel
import java.nio.ByteBuffer

/** Explicit global connection, independent of project cwd and runtime controller. */
class KnowledgeSettings(val configFile: Path, val credentials: JiraConfig,
                        private val dataHome: Path = defaultDataHome()) {
    private var boundIdentity: JsonObject? = null
    private val initial = read()
    val corpusRoot: Path = dataHome.resolve(ContextFiles.hash(listOf(credentials.baseUrl,
        credentials.email, credentials.apiToken, initial.getValue("rootPageId").jsonPrimitive.content,
        initial.getValue("spaceId").jsonPrimitive.content).joinToString("\u0000")))
    init { boundIdentity = initial }
    val pollIntervalSeconds: Long get() = read()["pollIntervalSeconds"]?.jsonPrimitive?.long ?: 300L
    fun identity(): String = ContextFiles.hash(read().toString())
    fun read(): JsonObject {
        val value = readConfig(configFile)
        validate(value)
        require(value.getValue("site").jsonPrimitive.content.trimEnd('/') == credentials.baseUrl.trimEnd('/')) { "Credential origin differs from configured connection" }
        require(initialOrNull(value)) { "Connection identity changed; reconnect MCP" }
        return value
    }
    private fun initialOrNull(value: JsonObject): Boolean = boundIdentity?.let { bound ->
        listOf("site", "space", "spaceId", "rootPageId", "emailEnv", "tokenEnv").all { value[it] == bound[it] }
    } ?: true
    fun connections(): JsonObject {
        val value = read()
        val binding = buildJsonObject {
            put("site", value.getValue("site")); put("space", value.getValue("space")); put("spaceId", value.getValue("spaceId"))
            put("homePageId", value.getValue("rootPageId")); put("knowledgeRootPageId", value.getValue("rootPageId")); put("structurePolicy", "knowledge")
            value["memoryPageId"]?.let { put("memoryPageId", it) }
            value["feedbackPageId"]?.let { put("maintenancePageId", it) }
        }
        val connection = buildJsonObject { put("confluence", binding); put("jira", JsonNull) }
        return buildJsonObject { put("common", connection); put("project", connection); put("effective", connection); put("role", "") }
    }
    companion object {
        fun home(): Path = System.getenv("HOME")?.takeIf { it.isNotBlank() }?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
            ?: Paths.get(System.getProperty("user.home"))
        private fun owned(path: Path) {
            if (Files.getFileStore(path).supportsFileAttributeView("posix")) {
                require(Files.getOwner(path, LinkOption.NOFOLLOW_LINKS) == Files.getOwner(Paths.get(System.getProperty("user.home")))) { "Metadata path is owned by another user" }
                val mode = Files.getPosixFilePermissions(path, LinkOption.NOFOLLOW_LINKS)
                require(PosixFilePermission.GROUP_WRITE !in mode && PosixFilePermission.OTHERS_WRITE !in mode) { "Metadata path is writable by other users" }
            }
        }
        fun safePath(path: Path) {
            require(path.isAbsolute && path.normalize() == path) { "Canonical absolute metadata path required" }
            var current = path.root
            for (part in path) {
                current = current.resolve(part)
                require(!Files.isSymbolicLink(current)) { "Symlink metadata paths are unsupported" }
                if (Files.exists(current) && Files.getFileStore(current).supportsFileAttributeView("posix")) {
                    val mode = Files.getPosixFilePermissions(current, LinkOption.NOFOLLOW_LINKS)
                    if (PosixFilePermission.GROUP_WRITE in mode || PosixFilePermission.OTHERS_WRITE in mode) {
                        val unixMode = runCatching { (Files.getAttribute(current, "unix:mode", LinkOption.NOFOLLOW_LINKS) as Number).toInt() }.getOrDefault(0)
                        require(Files.isDirectory(current) && unixMode and 512 != 0) { "Unsafe writable metadata ancestor" }
                    }
                }
            }
        }
        fun prepareCorpus(path: Path) {
            safePath(path)
            val missing = mutableListOf<Path>(); var cursor: Path? = path
            while (cursor != null && !Files.exists(cursor)) { missing.add(cursor); cursor = cursor.parent }
            for (directory in missing.asReversed()) {
                try { Files.createDirectory(directory, PosixFilePermissions.asFileAttribute(PosixFilePermissions.fromString("rwx------"))) }
                catch (_: UnsupportedOperationException) { Files.createDirectory(directory) }
                catch (_: FileAlreadyExistsException) { /* Another MCP may have initialized the same private corpus. */ }
                safePath(directory); owned(directory)
            }
            require(Files.isDirectory(path, LinkOption.NOFOLLOW_LINKS)) { "Corpus path is not a directory" }
            owned(path)
        }
        fun readConfig(path: Path): JsonObject {
            safePath(path); owned(path)
            val before = Files.readAttributes(path, BasicFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
            require(before.isRegularFile && before.size() in 1..65536) { "Connection config must contain 1..65536 bytes" }
            val bytes = ByteBuffer.allocate(65537)
            FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS).use { channel ->
                require(channel.size() == before.size()) { "Connection config changed" }
                while (bytes.hasRemaining() && channel.read(bytes) != -1) { }
                require(bytes.position() == before.size().toInt() && channel.size() == before.size()) { "Connection config changed or exceeded budget" }
            }
            val after = Files.readAttributes(path, BasicFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
            require(after.isRegularFile && after.fileKey() == before.fileKey() && after.size() == before.size() &&
                after.lastModifiedTime() == before.lastModifiedTime()) { "Connection config changed while reading" }
            safePath(path); owned(path)
            return Json.parseToJsonElement(String(bytes.array(), 0, bytes.position(), Charsets.UTF_8)).jsonObject
        }
        fun defaultDataHome(): Path {
            val base = System.getenv("XDG_DATA_HOME")?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
                ?: home().resolve(".local/share")
            return base.resolve("knowledge-confluence")
        }
        fun configPath(): Path = System.getenv("KNOWLEDGE_CONFLUENCE_CONFIG")?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
            ?: home().resolve(".config/knowledge-confluence/config.json")
        fun validate(value: JsonObject) {
            require(value.keys.all { it in setOf("schemaVersion", "enabled", "site", "space", "spaceId", "rootPageId", "memoryPageId", "feedbackPageId", "emailEnv", "tokenEnv", "pollIntervalSeconds") }) { "Unknown connection setting" }
            require(value["schemaVersion"] == null || value["schemaVersion"] == JsonPrimitive(1)) { "Unsupported connection schema" }
            require(value["enabled"] == JsonPrimitive(true)) { "Confluence backend is not enabled" }
            fun text(key: String): String = (value[key] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: error("Missing string $key")
            require(ContextConfig.validOrigin(text("site"))) { "HTTPS origin required" }
            require(text("space").matches(Regex("[A-Za-z0-9_-]{1,100}"))) { "Invalid space" }
            for (key in listOf("spaceId", "rootPageId") + listOf("memoryPageId", "feedbackPageId").filter { value.containsKey(it) })
                require(text(key).matches(Regex("[1-9][0-9]*"))) { "Positive native ID required: $key" }
            for (key in listOf("emailEnv", "tokenEnv").filter { value.containsKey(it) })
                require(text(key).matches(Regex("[A-Za-z_][A-Za-z0-9_]*"))) { "Credential setting must name an environment variable" }
            value["pollIntervalSeconds"]?.let { require(it is JsonPrimitive && !it.isString && it.longOrNull in 30L..86400L) { "Polling interval must be 30..86400 seconds" } }
        }
        fun fromEnvironment(): KnowledgeSettings {
            val path = configPath()
            val value = readConfig(path)
            validate(value)
            fun env(key: String, default: String): String = System.getenv(value[key]?.jsonPrimitive?.content ?: default)
                ?.takeIf { it.isNotBlank() } ?: error("Configured credential environment variable is unavailable")
            return KnowledgeSettings(path, JiraConfig(value.getValue("site").jsonPrimitive.content.trimEnd('/'), env("emailEnv", "JIRA_EMAIL"), env("tokenEnv", "JIRA_API_TOKEN")))
        }
    }
}
