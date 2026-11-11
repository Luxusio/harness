package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.ContextConfig
import io.crscube.clitools.confluence.ContextFiles
import io.crscube.clitools.confluence.ContextMirror
import io.crscube.clitools.confluence.CorpusFileLock
import io.crscube.clitools.confluence.WikiChanges
import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import java.nio.file.*
import java.nio.file.attribute.PosixFilePermission
import java.nio.file.attribute.PosixFilePermissions

/** One immutable connection snapshot from project YAML and private authentication. */
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
    val credentialFingerprint: String = credentialFingerprint(credentials)
    val wholeSpace: Boolean get() = "rootPageId" !in initial
    val corpusRoot: Path = corpusPath(dataHome, credentials, initial.getValue("space").jsonPrimitive.content,
        initial["rootPageId"]?.jsonPrimitive?.content)

    fun prepareCorpus() = prepareCorpus(corpusRoot)

    val pollIntervalSeconds: Long get() = initial["pollIntervalSeconds"]?.jsonPrimitive?.long ?: 300L
    val watchedCommentPages: List<String> = WikiChanges.watchPages(initial["watchCommentPages"]?.jsonPrimitive?.content)
    fun identity(): String = ContextFiles.hash(initial.toString())
    fun read(): JsonObject = initial
    fun connections(): JsonObject {
        val value = read()
        val binding = buildJsonObject {
            put("site", value.getValue("site")); put("space", value.getValue("space")); put("spaceId", value.getValue("spaceId"))
            put("homePageId", value["rootPageId"] ?: JsonPrimitive("")); put("wholeSpace", wholeSpace)
            value["rootPageId"]?.let { put("knowledgeRootPageId", it) }; put("structurePolicy", "knowledge")
            value["memoryPageId"]?.let { put("memoryPageId", it) }
            value["feedbackPageId"]?.let { put("maintenancePageId", it) }
        }
        val connection = buildJsonObject { put("confluence", binding); put("jira", JsonNull) }
        return buildJsonObject { put("common", connection); put("project", connection); put("effective", connection); put("role", "") }
    }
    companion object {
        internal fun corpusPath(dataHome: Path, credentials: JiraConfig, key: String, rootPageId: String?): Path =
            dataHome.resolve(ContextFiles.hash(JsonArray(listOf(credentials.baseUrl.trimEnd('/'), credentials.email,
                rootPageId ?: "*", key).map(::JsonPrimitive)).toString()))
        fun credentialFingerprint(credentials: JiraConfig): String = ContextFiles.hash(JsonArray(listOf(
            credentials.baseUrl.trimEnd('/'), credentials.email, credentials.apiToken).map(::JsonPrimitive)).toString())
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
            val base = environment["HOME"]?.takeIf { it.isNotBlank() }?.let { Paths.get(it).also { p -> require(p.isAbsolute) } }
                    ?.resolve(".local/share") ?: Paths.get(System.getProperty("user.home")).resolve(".local/share")
            base.resolve("wiki-atlassian")
        } catch (_: Exception) { throw IllegalArgumentException("Invalid corpus data home") }

        private val connectionFields = setOf("enabled", "site", "space", "spaceId", "rootPageId",
            "memoryPageId", "feedbackPageId", "pollIntervalSeconds", "watchCommentPages")

        internal fun validOrigin(site: String): Boolean {
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
        fun validate(value: JsonObject, requireEnabled: Boolean = true) {
            require(value.keys.all { it in connectionFields }) { "Unknown connection setting" }
            require(value["enabled"] == JsonPrimitive(true) || value["enabled"] == JsonPrimitive(false)) { "Boolean enabled required" }
            val enabled = value["enabled"] == JsonPrimitive(true)
            require(!requireEnabled || enabled) { "Atlassian backend is not enabled" }
            fun text(key: String): String = (value[key] as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?: throw IllegalArgumentException("Missing string connection setting")
            if (enabled || "site" in value) require(validOrigin(text("site"))) { "HTTPS origin required" }
            if (enabled || "space" in value) require(text("space").matches(Regex("[A-Za-z0-9_-]{1,100}"))) { "Invalid space" }
            for (key in listOf("spaceId", "rootPageId", "memoryPageId", "feedbackPageId")) {
                if (key in value || (enabled && key == "spaceId"))
                    require(text(key).matches(Regex("[1-9][0-9]*"))) { "Positive native ID required" }
            }
            value["pollIntervalSeconds"]?.let { require(it is JsonPrimitive && !it.isString && it.longOrNull in 30L..86400L) { "Polling interval must be 30..86400 seconds" } }
            if ("watchCommentPages" in value) WikiChanges.watchPages(text("watchCommentPages"))
        }

    }
}
