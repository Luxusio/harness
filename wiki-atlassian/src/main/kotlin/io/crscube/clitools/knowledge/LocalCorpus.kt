package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.*
import io.crscube.clitools.mcp.McpTool
import kotlinx.serialization.json.*
import java.net.URI
import java.nio.channels.FileChannel
import java.nio.channels.OverlappingFileLockException
import java.nio.file.*
import java.nio.file.StandardOpenOption.*
import java.security.MessageDigest
import java.time.Instant
import java.util.HexFormat
import java.util.PriorityQueue
import java.util.concurrent.ConcurrentHashMap

/** Reads one locked manifest generation; never a second mutable document authority. */
class LocalCorpus(private val settings: KnowledgeSettings, private val remote: ConfluenceRemote) {
    private val pendingRepair = ConcurrentHashMap.newKeySet<String>()
    @Volatile var onRepair: (() -> Unit)? = null
    fun requestRepair(pageId: String) {
        require(pageId.matches(Regex("[1-9][0-9]*"))) { "Native page ID required" }
        pendingRepair.add(pageId)
        onRepair?.invoke()
    }
    private enum class ReadState(val code: String) {
        MISSING("replica_missing"), BUSY("replica_busy"), RECOVERY("replica_recovery_pending"),
        INCOMPLETE("replica_incomplete"), UNAVAILABLE("replica_unavailable")
    }
    private class ReadFailure(val reason: ReadState) : RuntimeException(reason.code)
    private class RetrievalFailure(val code: String, message: String) : RuntimeException(message)
    private data class SyncFailure(val code: String) {
        val message: String get() = "Automatic synchronization failed ($code); this replica may be stale"
    }
    @Volatile private var lastFailure: SyncFailure? = null
    @Volatile private var lastSuccess: String? = null
    fun syncFailed(code: String = "sync_failed") {
        val safeCode = code.takeIf { it.matches(Regex("[a-z][a-z0-9_]{0,63}")) } ?: "sync_failed"
        lastFailure = SyncFailure(safeCode)
    }
    private fun checkpoint(control: Path): JsonObject {
        val path = ContextFiles.safe(control, "state.json")
        if (!Files.exists(path)) return buildJsonObject {}
        require(Files.isRegularFile(path) && Files.size(path) <= 65536) { "Invalid synchronization checkpoint" }
        return Json.parseToJsonElement(Files.readString(path)).jsonObject
    }
    private fun bodyTimestamp(state: JsonObject): Long? = (state["bodyRefreshedAt"] as? JsonPrimitive)?.takeUnless { it.isString }?.longOrNull
    suspend fun synchronize(authoritativeBodies: Boolean = true) {
        val before = settings.identity()
        val config = ContextConfig.load(settings.corpusRoot, settings)!!
        val control = ContextFiles.safe(settings.corpusRoot, "confluence/${config.space}/.sync")
        KnowledgeSettings.prepareCorpus(control)
        val sweepPath = ContextFiles.safe(control, "sweep.lock")
        FileChannel.open(sweepPath, CREATE, WRITE, LinkOption.NOFOLLOW_LINKS).use { channel ->
            val lease = try { channel.tryLock() } catch (_: OverlappingFileLockException) { null }
            if (lease == null) return // Another project owns this connection's current pass.
            lease.use {
                val mirror = ContextMirror(settings.corpusRoot, remote)
                val existing = mirror.manifest(config)["pages"]?.jsonObject?.keys.orEmpty()
                val started = System.currentTimeMillis()
                val previous = bodyTimestamp(checkpoint(control))
                val due = previous == null || previous < 0 || previous > started || started - previous >= settings.pollIntervalSeconds * 1000
                mirror.sync(config)
                val requested = pendingRepair.toSet()
                val pages = mirror.manifest(config)["pages"]?.jsonObject.orEmpty()
                // Native inline markers may change without a page revision. One leased pass
                // authoritatively refreshes existing bodies per interval across all projects.
                // A known publish failure bypasses the timestamp only for its exact pages.
                val fullPass = authoritativeBodies && due
                val authoritative = ((if (fullPass) existing else emptySet()) + requested).filter { it in pages }
                for (batch in authoritative.filter { pages[it]?.jsonObject?.get("type") == JsonPrimitive("page") }.chunked(100)) {
                    require(settings.identity() == before) { "Connection changed during synchronization" }
                    mirror.sync(config, mode = "pages", pageIds = batch, refreshRequestedBodies = true)
                    pendingRepair.removeAll(batch.toSet())
                }
                pendingRepair.removeAll(requested.filter { it !in pages }.toSet())
                require(settings.identity() == before) { "Connection changed during synchronization" }
                if (fullPass) {
                    val lockPath = ContextFiles.safe(control, "sync.lock")
                    FileChannel.open(lockPath, CREATE, WRITE, LinkOption.NOFOLLOW_LINKS).use { bodyLock ->
                        val lock = try { bodyLock.tryLock() } catch (_: OverlappingFileLockException) { null }
                        require(lock != null) { "Corpus busy before authoritative checkpoint" }
                        lock.use {
                            require(!Files.exists(ContextFiles.safe(control, "transaction.json"))) { "Corpus recovery pending" }
                            val state = checkpoint(control)
                            ContextFiles.atomic(ContextFiles.safe(control, "state.json"), JsonObject(state + ("bodyRefreshedAt" to JsonPrimitive(started))).toString())
                        }
                    }
                }
                lastSuccess = Instant.now().toString(); lastFailure = null
            }
        }
    }
    private fun freshness(manifest: JsonObject): JsonObject = buildJsonObject {
        put("source", "local_replica"); put("lastSuccessfulSync", lastSuccess ?: manifest["cursor"]?.jsonPrimitive?.contentOrNull ?: "unknown")
        put("permissionScope", "as_of_last_sync"); put("bodyRefresh", "shared_authoritative_sweep_per_interval")
        val config = ContextConfig.load(settings.corpusRoot, settings)!!
        val refreshed = bodyTimestamp(checkpoint(ContextFiles.safe(settings.corpusRoot, "confluence/${config.space}/.sync")))
        put("lastAuthoritativeBodySync", refreshed?.let { JsonPrimitive(Instant.ofEpochMilli(it).toString()) } ?: JsonNull); put("syncError", lastFailure?.message?.let(::JsonPrimitive) ?: JsonNull)
    }
    @McpTool(name = "connection_status", description = "Show local corpus readiness, freshness and synchronization errors without reading credentials.")
    fun status(): String = try { withManifest { manifest, _ ->
        val failure = lastFailure
        buildJsonObject {
            put("enabled", true); put("ready", true)
            put("status", if (failure == null) "ready" else "failed")
            put("documents", manifest["pages"]?.jsonObject?.size ?: 0)
            put("freshness", freshness(manifest))
            (manifest["wikiCommentWatch"] as? JsonObject)?.get("status")?.let { status ->
                if (status in setOf(JsonPrimitive("ready"), JsonPrimitive("unavailable"))) put("commentWatch", status)
            }
            failure?.let { put("code", it.code); put("syncError", it.message) }
        }
    }.toString() } catch (e: Exception) {
        val reason = (e as? ReadFailure)?.reason ?: ReadState.UNAVAILABLE
        val failure = lastFailure
        val status = when {
            reason == ReadState.BUSY -> "busy"
            failure != null -> "failed"
            reason in setOf(ReadState.MISSING, ReadState.RECOVERY, ReadState.INCOMPLETE) -> "pending"
            else -> "unavailable"
        }
        buildJsonObject {
            put("enabled", true); put("ready", false); put("status", status); put("reason", reason.code)
            put("code", failure?.code ?: reason.code)
            put("syncError", failure?.message?.let(::JsonPrimitive) ?: JsonNull)
            put("message", when (status) {
                "busy" -> "A corpus writer is active; retry the read after synchronization"
                "failed" -> "Synchronization failed; check the reported code and connection configuration. No readable snapshot is available"
                "pending" -> "No complete readable snapshot is available yet; automatic synchronization will retry"
                else -> "Corpus or connection metadata is unavailable; check configuration and restart MCP if the connection changed"
            })
        }.toString()
    }
    private fun <T> withManifest(block: (JsonObject, Path) -> T): T {
        KnowledgeSettings.safePath(settings.corpusRoot)
        val identity = settings.identity()
        val config = ContextConfig.load(settings.corpusRoot, settings)!!
        val base = ContextFiles.safe(settings.corpusRoot, "confluence/${config.space}")
        if (!Files.exists(base, LinkOption.NOFOLLOW_LINKS)) throw ReadFailure(ReadState.MISSING)
        if (!Files.isDirectory(base, LinkOption.NOFOLLOW_LINKS)) throw ReadFailure(ReadState.UNAVAILABLE)
        val manifest = ContextFiles.safe(base, ".sync/manifest.json")
        if (!Files.exists(manifest, LinkOption.NOFOLLOW_LINKS)) throw ReadFailure(ReadState.MISSING)
        if (!Files.isRegularFile(manifest) || Files.size(manifest) > 16L * 1024 * 1024) throw ReadFailure(ReadState.UNAVAILABLE)
        val lockPath = ContextFiles.safe(base, ".sync/sync.lock")
        FileChannel.open(lockPath, CREATE, READ, WRITE).use { channel ->
            val lock = try { channel.tryLock(0, Long.MAX_VALUE, true) } catch (_: OverlappingFileLockException) { null }
            if (lock == null) throw ReadFailure(ReadState.BUSY)
            lock.use {
                if (Files.exists(ContextFiles.safe(base, ".sync/transaction.json"))) throw ReadFailure(ReadState.RECOVERY)
                val record = ContextMirror(settings.corpusRoot, remote).manifest(config)
                if (record["inventoryComplete"] != JsonPrimitive(true)) throw ReadFailure(ReadState.INCOMPLETE)
                val result = block(record, base)
                require(settings.identity() == identity) { "Connection changed during read" }
                return result
            }
        }
    }
    private fun bodyPath(base: Path, row: JsonObject, format: String): Path =
        ContextFiles.safe(base, row.getValue(if (format == "storage") "storage" else "path").jsonPrimitive.content)

    private fun body(base: Path, row: JsonObject, format: String): String {
        val path = bodyPath(base, row, format)
        require(Files.isRegularFile(path) && Files.size(path) <= 8L * 1024 * 1024) { "Replica body missing or oversized; wait for synchronization" }
        val text = Files.readString(path)
        if (format == "storage") require(ContextFiles.hash(text) == row.getValue("bodyHash").jsonPrimitive.content) { "Native replica hash mismatch" }
        return text
    }
    private fun pageId(value: String): String {
        if (value.matches(Regex("[1-9][0-9]*"))) return value
        val uri = URI(value); val config = ContextConfig.load(settings.corpusRoot, settings)!!
        require(uri.scheme == "https" && uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null &&
            URI(config.site).authority == uri.authority) { "Use a document ID or configured Confluence URL" }
        return Regex("^/wiki/(?:spaces/[^/]+/)?pages/([1-9][0-9]*)(?:/[^/]*)?$").matchEntire(uri.rawPath)?.groupValues?.get(1)
            ?: error("Unsupported document URL")
    }

    private data class SearchKey(val rank: Int, val id: String)
    private data class SearchHit(val key: SearchKey, val result: JsonObject)
    private val searchOrder = compareByDescending<SearchKey> { it.rank }.thenBy { it.id }
    private val nativeId = Regex("[1-9][0-9]*")
    private val sha256 = Regex("[a-f0-9]{64}")

    private fun snapshot(connection: String, id: String, row: JsonObject, markdown: String): String =
        ContextFiles.hash(listOf(connection, id, row.getValue("version").toString(),
            row.getValue("bodyHash").jsonPrimitive.content, ContextFiles.hash(markdown)).joinToString(":"))

    private fun searchResult(id: String, row: JsonObject, match: LocalSearchQuery.Match, markdown: String,
                             prefix: Int, snippetLength: Int, snapshot: String): JsonObject = buildJsonObject {
        put("id", id); put("title", row.getValue("title")); put("version", row.getValue("version")); put("match", match.field)
        val at = if (match.bodyOffset >= 0) prefix + match.bodyOffset
            else markdown.indexOfFirstFrom(prefix) { !it.isWhitespace() }
        if (at >= 0) {
            val before = minOf(markdown.codePointCount(prefix, at), minOf(100, snippetLength / 4))
            val start = markdown.offsetByCodePoints(at, -before)
            val length = minOf(snippetLength, markdown.codePointCount(start, markdown.length))
            put("excerpt", markdown.substring(start, markdown.offsetByCodePoints(start, length)))
            put("excerptOnly", true)
            val headings = headingPath(markdown, prefix, at)
            if (headings.isNotEmpty()) put("headingPath", JsonArray(headings.map(::JsonPrimitive)))
            putJsonObject("read") {
                put("document", id); put("version", row.getValue("version")); put("format", "markdown")
                put("offset", markdown.codePointCount(0, start)); put("length", length); put("snapshot", snapshot)
            }
        }
    }

    private inline fun String.indexOfFirstFrom(start: Int, predicate: (Char) -> Boolean): Int {
        for (index in start until length) if (predicate(this[index])) return index
        return -1
    }

    private fun search(data: JsonObject, manifest: JsonObject, base: Path, pages: JsonObject, denied: Set<String>): JsonObject {
        val query = data.getValue("query").jsonPrimitive.content.trim()
        require(query.isNotEmpty() && query.codePointCount(0, query.length) <= 200) { "Query must contain 1..200 characters" }
        val limit = data["limit"]?.jsonPrimitive?.int ?: 10
        val snippetLength = data["snippetLength"]?.jsonPrimitive?.int ?: 400
        require(limit in 1..25 && snippetLength in 1..500) { "Query limits must be within 1..25 results and 1..500 excerpt code points" }
        require((data["scope"]?.jsonPrimitive?.content ?: "effective") in setOf("effective", "common", "project")) { "Query uses one configured global scope" }
        val requestedRoot = data["root"]?.jsonPrimitive?.content?.let(::pageId)
        val binding = ContextFiles.hash(listOf(query, requestedRoot.orEmpty(), limit, snippetLength).joinToString("\u0000"))
        val cursor = data["cursor"]?.jsonPrimitive?.content?.split(':', limit = 4)
        val after = cursor?.let {
            require(it.size == 4 && it[0].matches(sha256) && it[1] == binding && it[3].matches(nativeId)) { "Search snapshot changed; start a new search" }
            val rank = it[2].toIntOrNull()?.takeIf { rank -> rank in 1..6 } ?: error("Search snapshot changed; start a new search")
            SearchKey(rank, it[3])
        }
        fun inside(id: String): Boolean {
            if (id in denied) return false
            if (requestedRoot == null) return true
            var next: String? = id
            val seen = mutableSetOf<String>()
            while (next != null && next !in denied && seen.add(next)) {
                if (next == requestedRoot) return true
                next = pages[next]?.jsonObject?.get("parentId")?.jsonPrimitive?.contentOrNull
            }
            return false
        }
        val connection = settings.identity()
        fun response(results: List<JsonObject>, next: String? = null) = buildJsonObject {
            put("results", JsonArray(results)); put("freshness", freshness(manifest)); put("truncated", next != null)
            next?.let { put("nextCursor", it) }
        }
        val exact = pages[query]?.jsonObject?.takeIf { query.matches(nativeId) && inside(query) && it["type"] == JsonPrimitive("page") }
        if (exact != null) {
            require(after == null) { "Search snapshot changed; start a new search" }
            val markdown = body(base, exact, "markdown")
            return response(listOf(searchResult(query, exact, LocalSearchQuery.Match(0, "native_id"), markdown,
                authoredStart(markdown), snippetLength, snapshot(connection, query, exact, markdown))))
        }

        val lexical = LocalSearchQuery(query)
        val generation = MessageDigest.getInstance("SHA-256")
        // Hold at most one response page plus one continuation witness while visiting each body once.
        val hits = PriorityQueue<SearchHit>(limit + 1) { a, b -> searchOrder.compare(b.key, a.key) }
        var scannedBytes = 0L
        var scannedPages = 0
        for (id in pages.keys.sorted()) {
            if (!inside(id)) continue
            val row = pages.getValue(id).jsonObject
            if (row["type"] != JsonPrimitive("page")) continue
            val path = bodyPath(base, row, "markdown")
            val size = Files.size(path)
            require(Files.isRegularFile(path) && size <= 8L * 1024 * 1024) { "Replica body missing or oversized; wait for synchronization" }
            scannedBytes += size
            scannedPages++
            if (scannedBytes > 64L * 1024 * 1024 || scannedPages > 5_000) {
                throw RetrievalFailure("search_scope_too_large", "Search exceeded the local scan limit. Retry with a narrower root or an exact page ID.")
            }
            val markdown = Files.readString(path)
            val projection = snapshot(connection, id, row, markdown)
            val title = row.getValue("title").jsonPrimitive.content
            generation.update(projection.toByteArray(Charsets.UTF_8))
            generation.update(row.getValue("title").toString().toByteArray(Charsets.UTF_8))
            generation.update(0)
            val prefix = authoredStart(markdown)
            val match = lexical.match(title, markdown.substring(prefix)) ?: continue
            val key = SearchKey(match.rank, id)
            if (after != null && searchOrder.compare(key, after) <= 0) continue
            if (hits.size > limit && searchOrder.compare(key, hits.peek().key) >= 0) continue
            if (hits.size > limit) hits.remove()
            hits.add(SearchHit(key, searchResult(id, row, match, markdown, prefix, snippetLength, projection)))
        }
        val digest = HexFormat.of().formatHex(generation.digest())
        require(cursor == null || cursor[0] == digest) { "Search snapshot changed; start a new search" }
        val ordered = hits.sortedWith(compareBy(searchOrder) { it.key })
        val next = if (ordered.size > limit) ordered[limit - 1].key.let { "$digest:$binding:${it.rank}:${it.id}" } else null
        return response(ordered.take(limit).map { it.result }, next)
    }

    fun execute(operation: String, data: JsonObject): JsonObject? {
        if (operation !in setOf("search_documents", "get_document")) return null
        val snapshotArgument = data["snapshot"]
        // Native historical/outline/section reads retain exact version and rich-range validation.
        if (operation == "get_document" && snapshotArgument == null && (data["section"] != null || data["format"] == JsonPrimitive("outline"))) return null
        return try {
            val locator = (snapshotArgument as? JsonPrimitive)?.takeIf { it.isString }?.content
            if (snapshotArgument != null) {
                val expected = (data["version"] as? JsonPrimitive)?.takeUnless { it.isString }?.intOrNull
                if (locator == null || !locator.matches(sha256) || data["section"] != null ||
                    (data["format"] != null && data["format"] != JsonPrimitive("markdown")) || expected == null || expected <= 0) {
                    throw RetrievalFailure("invalid_snapshot", "Use the search result's Markdown read arguments with its snapshot and native version.")
                }
            }
            withManifest { manifest, base ->
                val pages = manifest.getValue("pages").jsonObject
                val denied = manifest["unavailable"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
                if (operation == "search_documents") search(data, manifest, base, pages, denied)
                else read(data, manifest, base, pages, denied, locator)
            }
        } catch (e: Exception) {
            val message = e.message.orEmpty()
            val (code, next) = when {
                e is RetrievalFailure -> e.code to message
                e is ReadFailure -> when (e.reason) {
                    ReadState.MISSING -> "replica_missing" to "No snapshot exists yet; check connection_status for synchronization errors"
                    ReadState.BUSY -> "replica_busy" to "Retry after the active synchronization finishes"
                    ReadState.RECOVERY -> "replica_recovery_pending" to "Automatic synchronization must finish the interrupted publication"
                    ReadState.INCOMPLETE -> "replica_incomplete" to "Wait for a complete synchronization; partial inventory is not readable"
                    ReadState.UNAVAILABLE -> "replica_unavailable" to "Check connection_status and the configured corpus location"
                }
                message.contains("Query") || message.contains("Continued") -> "invalid_query" to "Use a nonempty query or a bounded read with its returned version"
                message.contains("snapshot changed") -> "snapshot_changed" to "Restart search against the current snapshot"
                message.contains("busy") -> "replica_busy" to "Retry after the active synchronization finishes"
                message.contains("unavailable at last") -> "document_unavailable" to "The document was unavailable at last synchronization"
                message.contains("absent from") -> "document_not_found" to "Search the configured corpus for a current document ID"
                else -> "replica_unavailable" to "Check connection_status; automatic synchronization has not established a readable replica"
            }
            buildJsonObject { put("status", "error"); put("code", code); put("message", next) }
        }
    }

    private fun read(data: JsonObject, manifest: JsonObject, base: Path, pages: JsonObject, denied: Set<String>, locator: String?): JsonObject? {
        val id = pageId(data.getValue("document").jsonPrimitive.content)
        require(id !in denied) { "Document unavailable at last synchronization" }
        val row = pages[id]?.jsonObject ?: error("Document absent from configured corpus")
        val expected = data["version"]?.jsonPrimitive?.int
        fun stale(): Nothing = throw RetrievalFailure("snapshot_changed", "The document projection changed. Search again to obtain a current read range.")
        if (expected != null && expected != row.getValue("version").jsonPrimitive.int) {
            if (locator != null) stale()
            return null
        }
        val format = data["format"]?.jsonPrimitive?.content ?: "markdown"
        require(format in setOf("markdown", "storage"))
        val text = body(base, row, format)
        if (locator != null && locator != snapshot(settings.identity(), id, row, text)) stale()
        val total = text.codePointCount(0, text.length)
        val offset = data["offset"]?.jsonPrimitive?.int ?: 0
        val length = data["length"]?.jsonPrimitive?.int ?: 4000
        require(offset in 0..total && length in 1..12000 && (offset == 0 || expected != null)) { "Continued reads require a version and bounded offset" }
        val end = minOf(total, offset + length)
        return buildJsonObject {
            put("id", id); put("title", row.getValue("title")); put("version", row.getValue("version")); put("format", format)
            put("content", text.substring(text.offsetByCodePoints(0, offset), text.offsetByCodePoints(0, end)))
            put("offset", offset); put("offsetUnit", "unicode_code_points"); put("truncated", end < total)
            if (end < total) put("nextOffset", end)
            locator?.let { put("snapshot", it) }
            put("freshness", freshness(manifest))
        }
    }
}
