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
    val corpusRoot: Path = dataHome.resolve(ContextFiles.hash(JsonArray(listOf(credentials.baseUrl.trimEnd('/'),
        credentials.email, initial.getValue("rootPageId").jsonPrimitive.content,
        initial.getValue("spaceId").jsonPrimitive.content).map(::JsonPrimitive)).toString()))
    internal val legacyCorpusRoot: Path = dataHome.resolve(ContextFiles.hash(listOf(credentials.baseUrl,
        credentials.email, credentials.apiToken, initial.getValue("rootPageId").jsonPrimitive.content,
        initial.getValue("spaceId").jsonPrimitive.content).joinToString("\u0000")))
    @Volatile private var legacyImportFailed = false

    /** Only the exact current credential's legacy directory is a provable reuse candidate. */
    fun prepareCorpus() {
        prepareCorpus(corpusRoot)
        if (legacyImportFailed) return
        try { importLegacyCorpus() }
        catch (_: Exception) {
            legacyImportFailed = true
            // Reuse is optional. An unsafe, corrupt or oversized seed must not block fresh authorization.
            System.err.println("Atlassian legacy cache reuse was unavailable; synchronization will rebuild the replica")
        }
    }

    private fun importLegacyCorpus() {
        val space = initial.getValue("space").jsonPrimitive.content
        val destination = corpusRoot.resolve("confluence/$space/.sync")
        if (Files.exists(destination.resolve("manifest.json")) || !Files.exists(legacyCorpusRoot)) return
        safePath(legacyCorpusRoot); owned(legacyCorpusRoot)
        val sourceBase = ContextFiles.safe(legacyCorpusRoot, "confluence/$space")
        val sourceControl = ContextFiles.safe(legacyCorpusRoot, "confluence/$space/.sync")
        if (!Files.isDirectory(sourceControl)) return
        prepareCorpus(destination)
        CorpusFileLock.tryAcquire(destination.resolve("sync.lock"))?.use {
            if (Files.exists(destination.resolve("manifest.json"))) return
            CorpusFileLock.tryAcquire(ContextFiles.safe(sourceControl, "sync.lock"))?.use {
                if (Files.exists(ContextFiles.safe(sourceControl, "transaction.json"))) return
                val manifestPath = ContextFiles.safe(sourceControl, "manifest.json")
                if (!Files.isRegularFile(manifestPath) || Files.size(manifestPath) > 16L * 1024 * 1024) return
                val manifest = ContextMirror.readSnapshot(manifestPath)
                require(manifest["spaceId"] == initial["spaceId"] && manifest["homePageId"] == initial["rootPageId"] &&
                    manifest["site"]?.jsonPrimitive?.content?.trimEnd('/') == credentials.baseUrl.trimEnd('/')) { "Legacy corpus identity differs" }
                val pages = manifest["pages"]?.jsonObject ?: return
                require(pages.size <= 20000) { "Legacy corpus exceeds page budget" }
                var totalBytes = 0L
                val seededPages = linkedMapOf<String, JsonElement>()
                for ((id, value) in pages) {
                    require(id.matches(Regex("[1-9][0-9]*")))
                    val row = value.jsonObject
                    require(row["spaceId"] == initial["spaceId"]) { "Legacy body belongs to another space" }
                    val markdown = row.getValue("path").jsonPrimitive.content
                    val storage = row.getValue("storage").jsonPrimitive.content
                    require(markdown.startsWith("pages/") && markdown.endsWith(".md") &&
                        storage.startsWith(".sync/storage/") && storage.endsWith(".xhtml")) { "Invalid legacy body path" }
                    val safeMarkdown = "pages/legacy-$id.md"
                    val safeStorage = ".sync/storage/$id-${row.getValue("version").jsonPrimitive.int}-${row.getValue("bodyHash").jsonPrimitive.content}.xhtml"
                    require(row.getValue("bodyHash").jsonPrimitive.content.matches(Regex("[a-f0-9]{64}"))) { "Invalid legacy body hash" }
                    for ((relative, target) in listOf(markdown to safeMarkdown, storage to safeStorage)) {
                        val source = ContextFiles.safe(sourceBase, relative)
                        require(Files.isRegularFile(source) && Files.size(source) <= 8L * 1024 * 1024)
                        totalBytes += Files.size(source)
                        require(totalBytes <= 256L * 1024 * 1024) { "Legacy corpus exceeds byte budget" }
                        val body = Files.readString(source)
                        if (relative == storage) require(ContextFiles.hash(body) == row["bodyHash"]?.jsonPrimitive?.content)
                        ContextFiles.atomic(ContextFiles.safe(corpusRoot.resolve("confluence/$space"), target), body)
                    }
                    seededPages[id] = JsonObject(row.filterKeys { it in setOf("id", "title", "parentId", "type", "spaceId", "version", "status") } +
                        mapOf("path" to JsonPrimitive(safeMarkdown), "storage" to JsonPrimitive(safeStorage), "bodyHash" to row.getValue("bodyHash")))
                }
                // The seed carries bytes, never old selection, cursor, paths, authorization or operations.
                // Omitting the old policy forces fresh inventory selection before using any old ancestry.
                val seed = buildJsonObject {
                    put("site", credentials.baseUrl.trimEnd('/')); put("spaceId", initial.getValue("spaceId"))
                    put("homePageId", initial.getValue("rootPageId")); put("inventoryComplete", false)
                    put("pages", JsonObject(seededPages))
                }
                ContextFiles.atomic(destination.resolve("manifest.json"), seed.toString())
            }
        }
    }
    val pollIntervalSeconds: Long get() = initial["pollIntervalSeconds"]?.jsonPrimitive?.long ?: 300L
    val watchedCommentPages: List<String> = WikiChanges.watchPages(initial["watchCommentPages"]?.jsonPrimitive?.content)
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
                if (key in value || (enabled && key in setOf("spaceId", "rootPageId")))
                    require(text(key).matches(Regex("[1-9][0-9]*"))) { "Positive native ID required" }
            }
            value["pollIntervalSeconds"]?.let { require(it is JsonPrimitive && !it.isString && it.longOrNull in 30L..86400L) { "Polling interval must be 30..86400 seconds" } }
            if ("watchCommentPages" in value) WikiChanges.watchPages(text("watchCommentPages"))
        }

        /** Missing/disabled YAML is inert; invalid YAML/auth never becomes a local fallback. */
        fun fromProject(startupDirectory: Path = Paths.get("").toAbsolutePath(),
                        environment: Map<String, String> = System.getenv()): KnowledgeSettings? {
            try {
                val value = ProjectConnection.load(startupDirectory) ?: return null
                if (value["enabled"] == JsonPrimitive(false)) return null
                val snapshot = environment.toMap()
                fun credential(key: String): String = snapshot[key]?.takeIf { it.isNotBlank() }
                    ?: throw IllegalArgumentException("Atlassian authentication is unavailable")
                val pin = credential("ATLASSIAN_BASE_URL")
                require(validOrigin(pin) && pin.trimEnd('/') == value.getValue("site").jsonPrimitive.content.trimEnd('/'))
                val credentials = JiraConfig(pin.trimEnd('/'), credential("ATLASSIAN_EMAIL"), credential("ATLASSIAN_API_TOKEN"))
                return KnowledgeSettings(value, credentials, defaultDataHome(snapshot))
            } catch (_: Exception) {
                // Parser/I/O exception causes can contain the entire input or a secret-bearing path.
                throw IllegalArgumentException("Atlassian project connection or authentication is unavailable")
            }
        }
    }
}
