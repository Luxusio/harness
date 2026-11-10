package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.ContextConfig
import io.crscube.clitools.confluence.ContextFiles
import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import java.nio.file.*
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.PosixFilePermissions

/** One immutable connection snapshot from the environment, independent of project cwd. */
class KnowledgeSettings(value: JsonObject, val credentials: JiraConfig,
                        dataHome: Path = defaultDataHome()) {
    private val initial = JsonObject(value.toMap())
    init {
        validate(initial)
        require(credentials.email.isNotBlank() && credentials.apiToken.isNotBlank()) { "Atlassian credentials are unavailable" }
        require(initial.getValue("site").jsonPrimitive.content.trimEnd('/') == credentials.baseUrl.trimEnd('/')) {
            "Credential origin differs from configured connection"
        }
    }
    val corpusRoot: Path = dataHome.resolve(ContextFiles.hash(listOf(credentials.baseUrl,
        credentials.email, credentials.apiToken, initial.getValue("rootPageId").jsonPrimitive.content,
        initial.getValue("spaceId").jsonPrimitive.content).joinToString("\u0000")))
    val pollIntervalSeconds: Long get() = initial["pollIntervalSeconds"]?.jsonPrimitive?.long ?: 300L
    fun identity(): String = ContextFiles.hash(initial.toString())
    fun read(): JsonObject = initial
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
        fun defaultDataHome(environment: Map<String, String> = System.getenv()): Path = try {
            val base = environment["XDG_DATA_HOME"]?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
                ?: environment["HOME"]?.takeIf { it.isNotBlank() }?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
                    ?.resolve(".local/share") ?: Paths.get(System.getProperty("user.home")).resolve(".local/share")
            base.resolve("wiki-atlassian")
        } catch (_: Exception) { throw IllegalArgumentException("Invalid corpus data home") }

        private val connectionVariables = mapOf(
            "WIKI_ATLASSIAN_SITE" to "site", "WIKI_ATLASSIAN_SPACE" to "space",
            "WIKI_ATLASSIAN_SPACE_ID" to "spaceId", "WIKI_ATLASSIAN_ROOT_PAGE_ID" to "rootPageId",
            "WIKI_ATLASSIAN_MEMORY_PAGE_ID" to "memoryPageId", "WIKI_ATLASSIAN_FEEDBACK_PAGE_ID" to "feedbackPageId",
            "WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS" to "pollIntervalSeconds")

        /** Disabled connections require no credentials, data directories or clients. */
        fun enabled(environment: Map<String, String>): Boolean = when (environment["WIKI_ATLASSIAN_ENABLED"]) {
            "true" -> true
            "false" -> false
            null -> {
                require(connectionVariables.keys.none { environment.containsKey(it) }) { "Atlassian connection requires WIKI_ATLASSIAN_ENABLED" }
                false
            }
            else -> throw IllegalArgumentException("WIKI_ATLASSIAN_ENABLED must be true or false")
        }

        private fun validOrigin(site: String): Boolean {
            if (!ContextConfig.validOrigin(site) || site.any { it.isWhitespace() || it.isISOControl() || it == '\\' }) return false
            val uri = java.net.URI(site)
            if (uri.port == 0 || uri.rawAuthority.endsWith(":")) return false
            // Match Python's IPv6 parser: embedded IPv4 uses canonical decimal octets.
            if (uri.host.startsWith("[")) {
                val address = uri.host.removeSurrounding("[", "]").substringBefore('%')
                if ('.' in address && address.substringAfterLast(':').split('.').any {
                        it.length > 1 && it.startsWith('0') }) return false
            }
            return true
        }
        fun validate(value: JsonObject) {
            require(value.keys.all { it in setOf("enabled", "site", "space", "spaceId", "rootPageId", "memoryPageId", "feedbackPageId", "pollIntervalSeconds") }) { "Unknown connection setting" }
            require(value["enabled"] == JsonPrimitive(true)) { "Atlassian backend is not enabled" }
            fun text(key: String): String = (value[key] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: throw IllegalArgumentException("Missing string $key")
            require(validOrigin(text("site"))) { "HTTPS origin required" }
            require(text("space").matches(Regex("[A-Za-z0-9_-]{1,100}"))) { "Invalid space" }
            for (key in listOf("spaceId", "rootPageId") + listOf("memoryPageId", "feedbackPageId").filter { value.containsKey(it) })
                require(text(key).matches(Regex("[1-9][0-9]*"))) { "Positive native ID required: $key" }
            value["pollIntervalSeconds"]?.let { require(it is JsonPrimitive && !it.isString && it.longOrNull in 30L..86400L) { "Polling interval must be 30..86400 seconds" } }
        }
        fun fromEnvironment(environment: Map<String, String> = System.getenv()): KnowledgeSettings {
            val snapshot = environment.toMap()
            require(enabled(snapshot)) { "Atlassian connection is disabled" }
            val value = buildJsonObject {
                put("enabled", true)
                for ((variable, key) in connectionVariables) snapshot[variable]?.let { raw ->
                    if (key == "pollIntervalSeconds") {
                        require(raw.matches(Regex("[0-9]{1,20}"))) { "Polling interval must be 30..86400 seconds" }
                        val seconds = raw.toLongOrNull()
                        require(seconds != null && seconds in 30L..86400L) { "Polling interval must be 30..86400 seconds" }
                        put(key, seconds)
                    } else put(key, raw)
                }
            }
            validate(value)
            fun credential(key: String): String = snapshot[key]?.takeIf { it.isNotBlank() }
                ?: throw IllegalArgumentException("Atlassian credentials are unavailable")
            val credentials = JiraConfig(value.getValue("site").jsonPrimitive.content.trimEnd('/'),
                credential("ATLASSIAN_EMAIL"), credential("ATLASSIAN_API_TOKEN"))
            return KnowledgeSettings(value, credentials, defaultDataHome(snapshot))
        }
    }
}
