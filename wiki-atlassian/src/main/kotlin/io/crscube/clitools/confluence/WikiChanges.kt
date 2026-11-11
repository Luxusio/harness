package io.crscube.clitools.confluence

import kotlinx.serialization.json.*
import java.util.UUID

/** The manifest owns the sequence. The hook feed is a projection of a committed manifest. */
internal object WikiChanges {
    const val noticesKey = "wikiNotices"
    const val watchKey = "wikiCommentWatch"
    private const val maxEvents = 64
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
        previous[watchKey]?.jsonObject?.let { watch ->
            val observations = watch.getValue("observations").jsonObject
            val retained = JsonObject(observations.filterKeys { it in newPages })
            result[watchKey] = if (observations == retained) watch else JsonObject(watch + mapOf(
                "observations" to retained, "status" to JsonPrimitive("unavailable")))
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

    fun comments(previous: JsonObject, selection: String, attemptedAt: Long,
                 targets: Set<String>, observed: Map<String, String>): JsonObject {
        val old = previous[watchKey]?.jsonObject?.get("observations")?.jsonObject.orEmpty()
        val pages = admitted(previous)
        val notices = observed.toSortedMap().mapNotNull { (id, hash) ->
            if (old[id] == null || old[id] == JsonPrimitive(hash)) null else pageNotice("comments", id, pages.getValue(id))
        }
        val watch = buildJsonObject {
            put("selection", selection)
            put("attemptedAt", attemptedAt)
            put("status", if (observed.keys == targets) "ready" else "unavailable")
            put("observations", JsonObject(old.filterKeys { it in targets } + observed.mapValues { JsonPrimitive(it.value) }))
        }
        return JsonObject(previous + mapOf(noticesKey to append(previous, pages, notices), watchKey to watch))
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
        manifest[noticesKey]?.jsonObject?.let { state ->
            require(state.keys == setOf("epoch", "head", "events"))
            val epoch = state.getValue("epoch").jsonPrimitive
            require(epoch.isString && epochPattern.matches(epoch.content))
            val head = number(state.getValue("head"))
            require(head >= 0)
            val events = state.getValue("events").jsonArray
            require(events.size <= maxEvents)
            var last = 0L
            val pages = admitted(manifest)
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
            require(watch.keys == setOf("selection", "attemptedAt", "status", "observations"))
            val selection = watch.getValue("selection").jsonPrimitive
            require(selection.isString && fingerprintPattern.matches(selection.content) && number(watch.getValue("attemptedAt")) >= 0)
            require(watch.getValue("status") in setOf(JsonPrimitive("ready"), JsonPrimitive("unavailable")))
            val observations = watch.getValue("observations").jsonObject
            val pages = admitted(manifest)
            require(observations.size <= 10 && observations.all { (id, value) ->
                val hash = value.jsonPrimitive
                id in pages && hash.isString && fingerprintPattern.matches(hash.content)
            })
        }
    }

    private fun number(value: JsonElement): Long = value.jsonPrimitive.also { require(!it.isString) }.long
}
