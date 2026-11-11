package io.crscube.clitools.confluence

import kotlinx.serialization.json.*
import java.util.UUID

/** The manifest owns the sequence. The hook feed is a projection of a committed manifest. */
internal object WikiChanges {
    const val noticesKey = "wikiNotices"
    private const val watchKey = "wikiCommentWatch"
    private const val maxEvents = 64
    private const val maxWatchedPages = 64
    private val ready = JsonPrimitive("ready")
    private val unavailable = JsonPrimitive("unavailable")
    private val pageId = Regex("[1-9][0-9]{0,31}")
    private val epochPattern = Regex("[0-9a-f]{32}")
    private val fingerprintPattern = Regex("[0-9a-f]{64}")
    private val pageKinds = setOf("created", "updated", "comments")
    private val revisionKeys = listOf("version", "bodyHash", "path", "parentId", "title")

    fun watchPages(value: String?): List<String> {
        if (value == null) return emptyList()
        require(value.length <= 329) { "Comment watch accepts up to 10 distinct positive page IDs of at most 32 digits" }
        val pages = value.split(',')
        require(pages.size <= 10 && pages.toSet().size == pages.size && pages.all(pageId::matches)) {
            "Comment watch accepts up to 10 distinct positive page IDs of at most 32 digits"
        }
        return pages.sorted()
    }

    private fun complete(manifest: JsonObject): Boolean =
        manifest["inventoryComplete"]?.jsonPrimitive?.boolean ?: manifest.isNotEmpty()

    fun admitted(manifest: JsonObject): Map<String, JsonObject> {
        if (!complete(manifest)) return emptyMap()
        val unavailable = manifest["unavailable"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
        return manifest["pages"]?.jsonObject.orEmpty().mapNotNull { (id, value) ->
            val row = value.jsonObject
            if (pageId.matches(id) && id !in unavailable && row["type"] == JsonPrimitive("page") &&
                row["status"] == JsonPrimitive("current")) id to row else null
        }.toMap()
    }

    private fun pageNotice(kind: String, id: String, row: JsonObject) = buildJsonObject {
        put("kind", kind)
        put("id", id)
        put("version", row.getValue("version").jsonPrimitive.int)
    }

    fun documents(previous: JsonObject, next: JsonObject): JsonObject {
        val oldPages = admitted(previous)
        val newPages = admitted(next)
        val notices = mutableListOf<JsonObject>()
        if (complete(previous) && complete(next)) {
            val oldRows = previous["pages"]?.jsonObject.orEmpty()
            newPages.toSortedMap().forEach { (id, row) ->
                val before = oldPages[id]
                when {
                    before == null -> notices += pageNotice(if (id in oldRows) "updated" else "created", id, row)
                    revisionKeys.any { before[it] != row[it] } -> notices += pageNotice("updated", id, row)
                }
            }
        }
        if (oldPages.keys.any { it !in newPages }) notices += buildJsonObject { put("kind", "scope") }
        val result = next.toMutableMap()
        result[noticesKey] = append(previous, newPages, notices)
        if (watchKey in previous) {
            result[watchKey] = JsonObject(checkpoints(previous).filterKeys { it in newPages })
        }
        return JsonObject(result)
    }

    fun commentFingerprint(inventory: JsonObject): String {
        val comments = inventory.getValue("comments").jsonArray.map { it.jsonObject }
            .sortedBy { it.getValue("id").jsonPrimitive.content }
        val metadata = comments.map { comment -> buildJsonObject {
            listOf("id", "kind", "parentId", "version", "status").forEach { key -> comment[key]?.let { put(key, it) } }
            val resolution = comment["inlineContext"]?.jsonObject
            listOf("resolutionStatus", "resolutionLastModifiedAt").forEach { key -> resolution?.get(key)?.let { put(key, it) } }
        } }
        return ContextFiles.hash(JsonArray(metadata).toString())
    }

    private fun checkpoints(manifest: JsonObject): Map<String, JsonElement> = manifest[watchKey]?.jsonObject.orEmpty()

    fun commentStatus(manifest: JsonObject, watchedPages: List<String>): String? {
        if (watchedPages.isEmpty()) return null
        val current = checkpoints(manifest)
        val pages = admitted(manifest)
        if (watchedPages.any { it !in pages }) return unavailable.content
        if (watchedPages.none { it in current }) return null
        return if (watchedPages.all { current[it]?.jsonObject?.get("status") == ready }) ready.content else unavailable.content
    }

    fun commentAttemptedAt(manifest: JsonObject, id: String): Long? =
        checkpoints(manifest)[id]?.jsonObject?.get("attemptedAt")?.let(::number)

    fun commentDue(manifest: JsonObject, id: String, now: Long, intervalMillis: Long): Boolean =
        commentAttemptedAt(manifest, id)?.let { it > now || now - it >= intervalMillis } ?: true

    fun comments(previous: JsonObject, attemptedAt: Long,
                 targets: Set<String>, observed: Map<String, String>): JsonObject {
        val old = checkpoints(previous)
        val pages = admitted(previous)
        val notices = observed.toSortedMap().mapNotNull { (id, hash) ->
            val before = old[id]?.jsonObject?.get("fingerprint")
            if (before == null || before == JsonPrimitive(hash)) null else pageNotice("comments", id, pages.getValue(id))
        }
        val watch = targets.filter { it in pages }.associateWith { id ->
            val fingerprint = observed[id]?.let(::JsonPrimitive)
            buildJsonObject {
                put("attemptedAt", attemptedAt)
                put("status", if (fingerprint == null) unavailable else ready)
                (fingerprint ?: old[id]?.jsonObject?.get("fingerprint"))?.let { put("fingerprint", it) }
            }
        }.toMutableMap<String, JsonElement>()
        // Retain current attempts first even when the system clock has moved backwards.
        old.entries.asSequence().filter { (id, _) -> id in pages && id !in watch }
            .sortedWith(compareByDescending<Map.Entry<String, JsonElement>> { (_, row) -> number(row.jsonObject.getValue("attemptedAt")) }
                .thenBy { it.key })
            .take(maxWatchedPages - watch.size).forEach { (id, row) -> watch[id] = row }
        return JsonObject(previous + mapOf(noticesKey to append(previous, pages, notices), watchKey to JsonObject(watch)))
    }

    private fun append(previous: JsonObject, pages: Map<String, JsonObject>, changes: List<JsonObject>): JsonObject {
        val state = previous[noticesKey]?.jsonObject
        val epoch = state?.getValue("epoch")?.jsonPrimitive?.content ?: UUID.randomUUID().toString().replace("-", "")
        var head = state?.getValue("head")?.jsonPrimitive?.long ?: 0L
        val retained = state?.getValue("events")?.jsonArray.orEmpty().filter { value ->
            val id = value.jsonObject["id"]?.jsonPrimitive?.content
            id == null || id in pages
        }
        // Changes can outnumber the feed, including in one transaction. Count every sequence,
        // and construct only the events that can survive the retention limit.
        val keep = changes.takeLast(maxEvents)
        head = Math.addExact(head, (changes.size - keep.size).toLong())
        val newest = keep.map { change ->
            head = Math.addExact(head, 1L)
            JsonObject(mapOf("seq" to JsonPrimitive(head)) + change)
        }
        return buildJsonObject {
            put("epoch", epoch)
            put("head", head)
            put("events", JsonArray((retained + newest).takeLast(maxEvents)))
        }
    }

    fun validate(manifest: JsonObject) {
        val pages by lazy { admitted(manifest) }
        manifest[noticesKey]?.jsonObject?.let { state ->
            require(state.keys == setOf("epoch", "head", "events"))
            val epoch = state.getValue("epoch").jsonPrimitive
            require(epoch.isString && epochPattern.matches(epoch.content))
            val head = number(state.getValue("head"))
            require(head >= 0)
            val events = state.getValue("events").jsonArray
            require(events.size <= maxEvents)
            var last = 0L
            for (value in events) {
                val event = value.jsonObject
                val seq = number(event.getValue("seq"))
                require(seq > last && seq <= head)
                last = seq
                val kind = event.getValue("kind").jsonPrimitive
                require(kind.isString)
                if (kind.content == "scope") require(event.keys == setOf("seq", "kind")) else {
                    require(kind.content in pageKinds && event.keys == setOf("seq", "kind", "id", "version"))
                    val id = event.getValue("id").jsonPrimitive
                    require(id.isString && id.content in pages && number(event.getValue("version")) in 1..Int.MAX_VALUE.toLong())
                }
            }
        }
        manifest[watchKey]?.jsonObject?.let { watch ->
            require(watch.size <= maxWatchedPages)
            watch.forEach { (id, value) ->
                val row = value.jsonObject
                val status = row.getValue("status")
                val hash = row["fingerprint"]?.jsonPrimitive
                require(id in pages && row.keys.all { it in setOf("attemptedAt", "status", "fingerprint") } &&
                    number(row.getValue("attemptedAt")) >= 0 && status in setOf(ready, unavailable) &&
                    (hash != null || status == unavailable) && (hash == null || hash.isString && fingerprintPattern.matches(hash.content)))
            }
        }
    }

    private fun number(value: JsonElement): Long = value.jsonPrimitive.also { require(!it.isString) }.long
}
