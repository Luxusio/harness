package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import java.io.ByteArrayOutputStream
import java.io.IOException
import java.net.URI
import java.nio.ByteBuffer
import java.nio.file.*
import java.nio.file.attribute.PosixFileAttributeView
import java.nio.file.attribute.PosixFilePermissions
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.attribute.UserPrincipal
import java.security.MessageDigest
import java.util.Locale

/** A disposable, principal-bound body replica. Access/version checks belong to the remote reader. */
class ConfluencePageCache private constructor(
    private val directory: Path,
    private val workspace: Path?,
    private val payloadRoot: Path?
) {
    companion object {
        private const val MAX_BYTES = 8 * 1024 * 1024
        private const val MAX_TOTAL_BYTES = 64L * 1024 * 1024
        private const val MAX_FILES = 1024
        private val PRIVATE_DIRECTORY = PosixFilePermissions.fromString("rwx------")
        private val PRIVATE_FILE = PosixFilePermissions.fromString("rw-------")
        private fun projectBoundary(path: Path): Boolean = Files.exists(path.resolve(".git"), LinkOption.NOFOLLOW_LINKS)
        fun at(directory: Path): ConfluencePageCache = ConfluencePageCache(directory, null, null)

    }

    private fun validatePath(path: Path) {
        require((workspace == null || !path.startsWith(workspace)) && (payloadRoot == null || !path.startsWith(payloadRoot))) {
            "Document cache must be outside the workspace and plugin payload"
        }
        var cursor = path.root
        for (part in path) {
            cursor = cursor.resolve(part)
            require(!Files.isSymbolicLink(cursor)) { "Symlink cache paths are not permitted" }
            // Detect child projects and projects installed after this process started, too.
            require(!projectBoundary(cursor)) { "Document cache must be outside project directories" }
            require(!Files.exists(cursor, LinkOption.NOFOLLOW_LINKS) || cursor == path || Files.isDirectory(cursor, LinkOption.NOFOLLOW_LINKS)) {
                "Invalid cache directory"
            }
        }
    }

    private fun file(id: String): Path {
        require(id.matches(Regex("[1-9][0-9]*"))) { "Positive page ID required" }
        return directory.resolve("$id.json").also(::validatePath)
    }

    private fun identity(node: RemoteNode, versionMessage: String) = buildJsonObject {
        put("id", node.id); put("title", node.title); node.parentId?.let { put("parentId", it) }
        put("type", node.type); put("spaceId", node.spaceId); put("version", node.version)
        put("status", node.status); put("versionMessage", versionMessage)
    }

    fun read(node: RemoteNode, versionMessage: String): RemotePage? {
        val path = file(node.id) // Unsafe paths fail rather than becoming an ordinary miss.
        return try {
            if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.size(path) > MAX_BYTES) return null
            privatePermissions(directory, true)
            privatePermissions(path, false)
            val value = envelope(path) ?: return null
            if (value.keys != setOf("schemaVersion", "identity", "body", "bodyHash") ||
                value["schemaVersion"]?.jsonPrimitive?.intOrNull != 1 || value["identity"] != identity(node, versionMessage)) return null
            val body = value["body"]?.jsonPrimitive?.takeIf { it.isString }?.content ?: return null
            if (value["bodyHash"]?.jsonPrimitive?.content != ContextFiles.hash(body)) return null
            RemotePage(node, body, versionMessage)
        } catch (_: IOException) { null }
        catch (_: kotlinx.serialization.SerializationException) { null }
        catch (_: IllegalStateException) { null }
        catch (_: IllegalArgumentException) { null }
    }

    private fun envelope(path: Path): JsonObject? {
        if (!Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) || Files.size(path) > MAX_BYTES) return null
        val bytes = Files.newInputStream(path, LinkOption.NOFOLLOW_LINKS).use { input ->
            val output = ByteArrayOutputStream()
            val buffer = ByteArray(8192)
            while (true) {
                val count = input.read(buffer, 0, minOf(buffer.size, MAX_BYTES + 1 - output.size()))
                if (count < 0) break
                output.write(buffer, 0, count)
                if (output.size() > MAX_BYTES) return null
            }
            output.toByteArray()
        }
        return Json.parseToJsonElement(Charsets.UTF_8.newDecoder().decode(ByteBuffer.wrap(bytes)).toString()).jsonObject
    }

    private fun privatePermissions(path: Path, directory: Boolean) {
        if (Files.getFileAttributeView(path, PosixFileAttributeView::class.java, LinkOption.NOFOLLOW_LINKS) != null)
            Files.setPosixFilePermissions(path, if (directory) PRIVATE_DIRECTORY else PRIVATE_FILE)
    }

    fun write(page: RemotePage) {
        val path = file(page.node.id)
        val value = buildJsonObject {
            put("schemaVersion", 1); put("identity", identity(page.node, page.versionMessage))
            put("body", page.body); put("bodyHash", ContextFiles.hash(page.body))
        }.toString().toByteArray(Charsets.UTF_8)
        if (value.size > MAX_BYTES) return // A large page remains readable from its source.
        try {
            // Only the Talos subtree is private-managed; never chmod the user's runtime home.
            val talos = directory.parent.parent
            validatePath(directory)
            val attributes = if (Files.getFileAttributeView(existingAncestor(talos), PosixFileAttributeView::class.java) != null)
                arrayOf(PosixFilePermissions.asFileAttribute(PRIVATE_DIRECTORY)) else emptyArray()
            Files.createDirectories(directory, *attributes)
            listOf(talos, directory.parent, directory).forEach { entry -> validatePath(entry); privatePermissions(entry, true) }
            val temp = if (attributes.isNotEmpty()) Files.createTempFile(directory, ".page-", ".tmp", PosixFilePermissions.asFileAttribute(PRIVATE_FILE))
                else Files.createTempFile(directory, ".page-", ".tmp")
            try {
                Files.write(temp, value)
                validatePath(path)
                Files.move(temp, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
                privatePermissions(path, false)
                try { cleanup(path) } catch (_: Exception) { /* Disposable quota maintenance cannot invalidate a verified source read. */ }
            } finally { Files.deleteIfExists(temp) }
        } catch (_: IOException) { /* The source read still succeeded; a replica is optional. */ }
    }

    private fun owned(path: Path, owner: UserPrincipal, directory: Boolean): BasicFileAttributes? {
        validatePath(path)
        val attributes = Files.readAttributes(path, BasicFileAttributes::class.java, LinkOption.NOFOLLOW_LINKS)
        if ((if (directory) !attributes.isDirectory else !attributes.isRegularFile) || Files.getOwner(path, LinkOption.NOFOLLOW_LINKS) != owner) return null
        val posix = Files.getFileAttributeView(path, PosixFileAttributeView::class.java, LinkOption.NOFOLLOW_LINKS)
        if (posix != null && posix.readAttributes().permissions() != (if (directory) PRIVATE_DIRECTORY else PRIVATE_FILE)) return null
        return attributes
    }

    private fun disposable(path: Path): Boolean {
        val value = envelope(path) ?: return false
        if (value.keys != setOf("schemaVersion", "identity", "body", "bodyHash") || value["schemaVersion"] != JsonPrimitive(1)) return false
        val identity = value["identity"] as? JsonObject ?: return false
        val required = setOf("id", "title", "type", "spaceId", "version", "status", "versionMessage")
        if (identity.keys != required && identity.keys != required + "parentId") return false
        fun text(name: String): String? = (identity[name] as? JsonPrimitive)?.takeIf { it.isString }?.content
        val id = text("id") ?: return false
        val title = text("title") ?: return false
        if (!id.matches(Regex("[1-9][0-9]*")) || path.fileName.toString() != "$id.json" ||
            text("type") != "page" || text("status") != "current" || title.isBlank() || title.length > 1000 ||
            text("spaceId")?.matches(Regex("[1-9][0-9]*")) != true || text("versionMessage") == null) return false
        if (identity.containsKey("parentId") && text("parentId")?.matches(Regex("[1-9][0-9]*")) != true) return false
        val version = identity["version"] as? JsonPrimitive ?: return false
        if (version.isString || (version.intOrNull ?: 0) <= 0) return false
        val body = (value["body"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return false
        val hash = (value["bodyHash"] as? JsonPrimitive)?.takeIf { it.isString }?.content ?: return false
        return hash == ContextFiles.hash(body)
    }

    /** Soft, oldest-write quotas across principal partitions; no record content is read while under quota. */
    private fun cleanup(current: Path) {
        val root = directory.parent
        validatePath(root)
        val owner = Files.getOwner(directory, LinkOption.NOFOLLOW_LINKS)
        if (owned(root, owner, true) == null) return
        val partitions = mutableListOf<Path>()
        val candidates = mutableListOf<Pair<Path, BasicFileAttributes>>()
        Files.newDirectoryStream(root).use { entries ->
            for (partition in entries) {
                if (!partition.fileName.toString().matches(Regex("[a-f0-9]{64}"))) continue
                try {
                    if (owned(partition, owner, true) == null) continue
                    partitions.add(partition)
                    Files.newDirectoryStream(partition).use { files ->
                        for (path in files) {
                            if (!path.fileName.toString().matches(Regex("[1-9][0-9]*\\.json"))) continue
                            try { owned(path, owner, false)?.let { candidates.add(path to it) } } catch (_: Exception) { /* An unverified entry is not ours to delete. */ }
                        }
                    }
                } catch (_: Exception) { /* Unknown directories and unavailable partitions are preserved. */ }
            }
        }
        var bytes = candidates.sumOf { it.second.size() }
        var count = candidates.size
        val oldest = candidates.sortedWith(compareBy<Pair<Path, BasicFileAttributes>> { it.first == current }
            .thenBy { it.second.lastModifiedTime() }.thenBy { it.first.toString() })
        for ((path, before) in oldest) {
            if (bytes <= MAX_TOTAL_BYTES && count <= MAX_FILES) break
            try {
                if (path == current || owned(path, owner, false) == null) continue
                val recognized = try { disposable(path) } catch (_: Exception) { false }
                if (!recognized) {
                    // Unknown data is retained and is not part of the managed disposable quota.
                    bytes -= before.size()
                    count--
                    continue
                }
                val after = owned(path, owner, false) ?: continue
                if (before.fileKey() != after.fileKey() || before.creationTime() != after.creationTime() ||
                    before.size() != after.size() || before.lastModifiedTime() != after.lastModifiedTime()) continue
                Files.delete(path)
                bytes -= before.size()
                count--
            } catch (_: Exception) { /* Concurrent replacement or cleanup failure is a cache miss, not source failure. */ }
        }
        for (partition in partitions) if (partition != directory) {
            try { if (owned(partition, owner, true) != null) Files.delete(partition) }
            catch (_: Exception) { /* Nonempty, changed or unowned directories are retained. */ }
        }
    }

    private fun existingAncestor(path: Path): Path {
        var current = path
        while (!Files.exists(current, LinkOption.NOFOLLOW_LINKS)) current = current.parent
        return current
    }
}
