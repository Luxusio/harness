package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.*
import io.crscube.clitools.mcp.McpTool
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.*
import java.net.URI
import java.nio.file.Files
import java.util.Base64

/** One site's selected spaces reuse their own native authorization and replica transactions. */
class MultiSpaceKnowledge(private val project: KnowledgeProject, private val remote: ConfluenceRemote) : AutoCloseable {
    private data class Running(val settings: KnowledgeSettings, val corpus: LocalCorpus, val service: WikiService)
    private class Slot(val selection: JsonObject) {
        val lock = Mutex()
        val wake = Channel<Unit>(Channel.CONFLATED)
        @Volatile var running: Running? = null
        @Volatile var failure: String? = null
        @Volatile var denied = false
    }
    private val slots = project.selections.associate { it.getValue("key").jsonPrimitive.content to Slot(it) }

    suspend fun synchronize(key: String, authoritativeBodies: Boolean = true) {
        val slot = slots[key] ?: throw IllegalArgumentException("Select a configured space key")
        slot.lock.withLock {
            var resolving = true
            try {
                val settings = project.resolve(remote, key)
                resolving = false
                val prior = slot.running
                if (prior != null && prior.settings.read()["spaceId"] != settings.read()["spaceId"])
                    throw ContextRemoteException("space_identity_changed", "The selected space identity changed; preserve and inspect its previous replica")
                val running = prior ?: run {
                    settings.prepareCorpus()
                    val corpus = LocalCorpus(settings, remote)
                    val service = WikiService(remote, project.credentials.baseUrl.trimEnd('/'), settings.corpusRoot, settings)
                    service.localReads = corpus::execute
                    service.replicaRepair = corpus::requestRepair
                    corpus.onRepair = { slot.wake.trySend(Unit) }
                    Running(settings, corpus, service).also { slot.running = it }
                }
                val config = ContextConfig.load(settings.corpusRoot, settings)!!
                val manifest = ContextFiles.safe(settings.corpusRoot, "confluence/$key/.sync/manifest.json")
                if (Files.isRegularFile(manifest) && Files.size(manifest) <= 16L * 1024 * 1024 &&
                    ContextMirror.readSnapshot(manifest)["spaceId"] != settings.read()["spaceId"])
                    throw ContextRemoteException("space_identity_changed", "The selected space identity changed; preserve and inspect its previous replica")
                running.corpus.synchronize(authoritativeBodies)
                if (settings.watchedCommentPages.isNotEmpty())
                    ContextMirror(settings.corpusRoot, remote).pollComments(config, settings.watchedCommentPages, settings.pollIntervalSeconds)
                slot.failure = null
                slot.denied = false
            } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val failure = (e as? ContextRemoteException)?.code?.takeIf { it.matches(Regex("[a-z][a-z0-9_]{0,63}")) } ?: "sync_failed"
                slot.failure = failure
                slot.running?.corpus?.syncFailed(failure)
                if (e is ContextRemoteException && (e is RootScopeDenial || failure == "space_identity_changed" ||
                        resolving && (failure == "space_unavailable" || e.isScopeDenial()))) {
                    slot.denied = true
                    recordConfiguredDenial(key)
                    if (failure == "http_401") denyCredential()
                }
                throw e
            }
        }
    }

    /** Pause only the exact observed old admission, including after an MCP restart. */
    private suspend fun recordConfiguredDenial(key: String) {
        try {
            val slot = slots.getValue(key)
            val root = project.corpusRoot(key)
            KnowledgeSettings.safePath(root)
            val file = ContextFiles.safe(root, "confluence/$key/.sync/manifest.json")
            if (!Files.isRegularFile(file) || Files.size(file) > 16L * 1024 * 1024) return
            val manifest = ContextMirror.readSnapshot(file)
            val rootId = slot.selection["rootPageId"]?.jsonPrimitive?.content
            val whole = rootId == null
            if (manifest["site"]?.jsonPrimitive?.content?.trimEnd('/') != project.credentials.baseUrl.trimEnd('/') ||
                manifest["homePageId"] != JsonPrimitive(rootId ?: "") ||
                (manifest["wholeSpace"] ?: JsonPrimitive(false)) != JsonPrimitive(whole) ||
                manifest["knowledgeRootPageId"] != slot.selection["rootPageId"]) return
            val id = manifest["spaceId"]?.jsonPrimitive?.content?.takeIf { it.matches(Regex("[1-9][0-9]*")) } ?: return
            val observed = ContextConfig(project.credentials.baseUrl.trimEnd('/'), key, id, rootId ?: "", "", "", "knowledge",
                knowledgeRootPageId = rootId, legacyPmAssociation = false, wholeSpace = whole)
            ContextMirror(root, remote).recordNoticeRootDenial(observed)
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { /* An unsafe/unwritable cache never changes the original provider result. */ }
    }

    private suspend fun denyCredential() {
        slots.forEach { (key, slot) ->
            slot.denied = true; slot.failure = "http_401"; recordConfiguredDenial(key)
        }
    }

    suspend fun awaitNext(key: String): Boolean = withTimeoutOrNull(project.pollIntervalSeconds * 1000) {
        slots.getValue(key).wake.receive(); false
    } ?: true

    @McpTool(name = "connection_status", description = "Show each configured space's scope, destinations, local replica readiness and synchronization failure. Partial readiness does not certify unavailable spaces.")
    fun status(): String {
        val entries = slots.map { (key, slot) ->
            val current = slot.running?.corpus?.status()?.let { Json.parseToJsonElement(it).jsonObject }
            buildJsonObject {
                put("key", key); put("wholeSpace", "rootPageId" !in slot.selection)
                slot.selection.filterKeys { it != "key" }.forEach { (name, value) -> put(name, value) }
                slot.running?.settings?.read()?.get("spaceId")?.let { put("spaceId", it) }
                current?.forEach { (name, value) -> put(name, value) }
                if (slot.failure != null) { put("status", "failed"); put("code", slot.failure); put("syncError", "Automatic synchronization for this space failed") }
                if (current == null || slot.denied) put("ready", false)
                if (current == null && slot.failure == null) put("status", "pending")
            }
        }
        val ready = entries.count { it["ready"] == JsonPrimitive(true) }
        val healthy = entries.count { it["ready"] == JsonPrimitive(true) && it["status"] == JsonPrimitive("ready") }
        return buildJsonObject {
            put("enabled", true); put("site", project.credentials.baseUrl.trimEnd('/')); put("ready", ready > 0)
            put("complete", healthy == entries.size)
            put("status", when { healthy == entries.size -> "ready"; ready > 0 -> "partial"; entries.any { it["status"] == JsonPrimitive("failed") } -> "failed"; else -> "pending" })
            put("spaces", JsonArray(entries))
        }.toString()
    }

    private fun error(code: String, message: String): JsonObject = buildJsonObject { put("status", "error"); put("code", code); put("message", message) }
    private fun key(data: JsonObject): String? = data["space"]?.let {
        require(it is JsonPrimitive && it.isString && it.content in slots) { "Select a configured space key" }; it.content
    }
    private fun running(key: String): Running {
        val slot = slots.getValue(key)
        if (slot.denied) throw ContextRemoteException("space_unavailable", "The selected space is no longer admitted")
        return slot.running ?: throw ContextRemoteException("space_pending", "The selected space is still resolving; inspect connection_status")
    }
    private fun pageId(value: String): String = if (value.matches(Regex("[1-9][0-9]*"))) value
        else nativePageUrlId(value, project.credentials.baseUrl.trimEnd('/'), allowApi = true)

    private suspend fun documentSpace(value: String, supplied: String?, cachedRead: Boolean = false): String {
        val id = pageId(value)
        val cached = if (cachedRead) slots.filter { !it.value.denied && it.value.running?.corpus?.containsDocument(id) == true }.keys else emptySet()
        val selected = if (cached.size == 1) cached.single() else {
            val node = try { remote.scopeNode(id) } catch (e: ContextRemoteException) {
                if (e.code != "http_404") throw e
                remote.scopeNode(id, "folder")
            }
            val owner = slots.filter { !it.value.denied && it.value.running?.settings?.read()?.get("spaceId") == JsonPrimitive(node.spaceId) }.keys.singleOrNull()
                ?: throw ContextRemoteException("scope_outside_connection", "The document is outside the configured or currently admitted spaces")
            if (cachedRead) {
                val context = running(owner)
                remote.requireScope(ContextConfig.load(context.settings.corpusRoot, context.settings)!!, id)
                if (context.corpus.containsDocument(id) && !context.corpus.matchesNode(node))
                    throw ContextRemoteException("snapshot_changed", "The known native identity differs from its cached document; synchronize and read it again")
            }
            owner
        }
        if (supplied != null && supplied != selected)
            throw ContextRemoteException("space_mismatch", "The explicit space differs from the document's native space")
        return selected
    }

    private suspend fun commentPage(value: String): String {
        val direct = Regex("^(footer|inline):([1-9][0-9]*)$").matchEntire(value)
        val url = if (value.startsWith("https://")) {
            val uri = URI(value); val origin = URI(project.credentials.baseUrl)
            require(uri.scheme == origin.scheme && uri.rawAuthority == origin.rawAuthority && uri.rawQuery == null && uri.rawFragment == null && uri.normalize().rawPath == uri.rawPath)
            Regex("^/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]*)$").matchEntire(uri.rawPath)
        } else null
        val kind = (direct ?: url)?.groupValues?.get(1)
        val id = (direct ?: url)?.groupValues?.get(2) ?: value.also { require(it.matches(Regex("[1-9][0-9]*"))) }
        if (kind != null) return remote.wikiCommentLocation(id, kind).first
        return try { remote.wikiCommentLocation(id, "footer").first } catch (e: ContextRemoteException) {
            if (e.code != "http_404") throw e
            remote.wikiCommentLocation(id, "inline").first
        }
    }

    suspend fun execute(operation: String, data: JsonObject): JsonObject = try {
        val supplied = key(data)
        val arguments = JsonObject(data - "space")
        val result = if (operation == "search_documents") search(arguments, supplied)
        else {
            val selected = when (operation) {
                "create_document" -> arguments["parent"]?.jsonPrimitive?.content?.let { documentSpace(it, supplied) }
                    ?: supplied ?: throw ContextRemoteException("selection_required", "Choose a space or an explicit parent before creating a document")
                "update_document" -> {
                    val document = arguments["document"]?.jsonPrimitive?.content ?: "memory"
                    if (document in setOf("memory", "feedback")) {
                        val field = if (document == "memory") "memoryPageId" else "feedbackPageId"
                        val candidates = slots.filter { field in it.value.selection && (supplied == null || it.key == supplied) }.keys
                        candidates.singleOrNull() ?: throw ContextRemoteException("selection_required", "Choose a space with one configured recording destination")
                    } else documentSpace(document, supplied)
                }
                "get_document", "list_document_versions", "graph" -> documentSpace(arguments.getValue("document").jsonPrimitive.content, supplied, operation == "get_document")
                "update_comment" -> documentSpace(commentPage(arguments.getValue("comment").jsonPrimitive.content), supplied)
                "list_comments", "create_comment" -> {
                    val target = arguments.getValue("target").jsonPrimitive.content
                    val page = if (target.startsWith("footer:") || target.startsWith("inline:") || "-comments/" in target) commentPage(target) else target
                    documentSpace(page, supplied)
                }
                else -> throw IllegalArgumentException("Unknown wiki operation")
            }
            val context = running(selected)
            val adjusted = if (operation == "create_document" && "parent" !in arguments && !context.settings.wholeSpace)
                JsonObject(arguments + ("parent" to context.settings.read().getValue("rootPageId"))) else arguments
            labelled(context.service.execute(operation, adjusted), selected, context.settings, operation in setOf("get_document", "create_document", "update_document"))
        }
        if (result["code"] == JsonPrimitive("http_401")) denyCredential()
        result
    } catch (e: CancellationException) { throw e }
    catch (e: Exception) {
        if (e is ContextRemoteException && e.code == "http_401") denyCredential()
        val code = (e as? ContextRemoteException)?.code?.takeIf { it.matches(Regex("[a-z][a-z0-9_]{0,63}")) }
            ?: if (e.message == "Search snapshot changed") "snapshot_changed" else "invalid_request"
        error(code, when (code) {
            "selection_required" -> "Choose an explicit configured space or native document/parent; no write was performed"
            "space_mismatch" -> "The explicit space and native target disagree; no write was performed"
            "space_pending", "space_unavailable" -> "The selected space is unavailable; inspect connection_status"
            else -> "The request could not be admitted in the configured spaces"
        })
    }

    private fun labelled(value: JsonObject, key: String, settings: KnowledgeSettings, pageResponse: Boolean = false): JsonObject = JsonObject(value + buildJsonObject {
        put("space", key); put("spaceId", settings.read().getValue("spaceId"))
        if (pageResponse && "url" !in value)
            value["id"]?.jsonPrimitive?.content?.let { put("url", "${project.credentials.baseUrl.trimEnd('/')}/wiki/spaces/$key/pages/$it") }
    })

    private suspend fun search(arguments: JsonObject, supplied: String?): JsonObject {
        val keys = if (arguments["root"] != null) listOf(documentSpace(arguments.getValue("root").jsonPrimitive.content, supplied, true))
            else if (supplied != null) listOf(supplied) else slots.keys.toList()
        val limit = arguments["limit"]?.jsonPrimitive?.int ?: 10
        require(limit in 1..25)
        val binding = ContextFiles.hash(buildJsonObject {
            put("arguments", JsonObject((arguments - "cursor").toSortedMap())); put("spaces", JsonArray(keys.map(::JsonPrimitive)))
            put("project", project.read())
        }.toString())
        val snapshots = buildJsonObject { keys.forEach { selected ->
            val slot = slots.getValue(selected)
            put(selected, if (slot.denied) JsonNull else slot.running?.corpus?.snapshotIdentity()?.let(::JsonPrimitive) ?: JsonNull)
        } }
        val previous = arguments["cursor"]?.jsonPrimitive?.content?.let { encoded ->
            require(encoded.length <= 32768)
            Json.parseToJsonElement(String(Base64.getUrlDecoder().decode(encoded), Charsets.UTF_8)).jsonObject.also {
                require(it.keys == setOf("binding", "snapshots", "states") && it["binding"] == JsonPrimitive(binding) && it["snapshots"] == snapshots) { "Search snapshot changed" }
            }
        }
        val states = previous?.getValue("states")?.jsonObject.orEmpty().toMutableMap()
        require(states.keys.all { it in keys })
        val results = mutableListOf<JsonObject>()
        val coverage = mutableListOf<JsonObject>()
        var hasMore = false
        for (selected in keys) {
            val context = slots.getValue(selected).running
            if (context == null || snapshots[selected] is JsonNull) {
                coverage += buildJsonObject { put("space", selected); put("status", "unavailable"); put("code", slots.getValue(selected).failure ?: "replica_pending") }
                continue
            }
            coverage += buildJsonObject { put("space", selected); put("status", "ready") }
            val state = states[selected]?.jsonObject ?: buildJsonObject {}
            require(state.keys.all { it in setOf("cursor", "skip", "done") })
            if (state["done"] == JsonPrimitive(true)) continue
            if (results.size >= limit) { hasMore = true; continue }
            val skip = state["skip"]?.jsonPrimitive?.int ?: 0
            require(skip in 0..limit)
            val request = JsonObject(arguments - "cursor" + buildJsonObject { put("limit", limit); state["cursor"]?.let { put("cursor", it) } })
            val response = context.service.execute("search_documents", request)
            if (response["status"] == JsonPrimitive("error")) {
                coverage[coverage.lastIndex] = buildJsonObject { put("space", selected); put("status", "unavailable"); put("code", response["code"] ?: JsonPrimitive("replica_unavailable")) }
                continue
            }
            val hits = response.getValue("results").jsonArray
            require(skip <= hits.size)
            val take = minOf(limit - results.size, hits.size - skip)
            hits.drop(skip).take(take).forEach { hit ->
                val row = hit.jsonObject
                results += JsonObject(labelled(row, selected, context.settings, pageResponse = true) + buildJsonObject {
                    row["read"]?.jsonObject?.let { put("read", JsonObject(it + ("space" to JsonPrimitive(selected)))) }
                })
            }
            val consumed = skip + take
            val next = response["nextCursor"] ?: response["cursor"]?.takeUnless { it is JsonNull }
            states[selected] = when {
                consumed < hits.size -> JsonObject(state + ("skip" to JsonPrimitive(consumed)))
                next != null -> buildJsonObject { put("cursor", next); put("skip", 0) }
                else -> buildJsonObject { put("done", true) }
            }
            if (consumed < hits.size || next != null) hasMore = true
        }
        // Each selected snapshot is checked again after the collected results are read.
        keys.forEach { selected ->
            val slot = slots.getValue(selected)
            val current = if (slot.denied) null else slot.running?.corpus?.snapshotIdentity()
            require(snapshots[selected] == (current?.let(::JsonPrimitive) ?: JsonNull)) { "Search snapshot changed" }
        }
        val partial = coverage.any { it["status"] != JsonPrimitive("ready") }
        return buildJsonObject {
            put("results", JsonArray(results)); put("spaces", JsonArray(coverage)); put("partial", partial)
            put("truncated", hasMore)
            if (hasMore) {
                val cursor = Base64.getUrlEncoder().withoutPadding().encodeToString(buildJsonObject {
                    put("binding", binding); put("snapshots", snapshots); put("states", JsonObject(states))
                }.toString().toByteArray(Charsets.UTF_8))
                require(cursor.length <= 32768)
                put("nextCursor", cursor)
            }
            if (coverage.none { it["status"] == JsonPrimitive("ready") }) {
                put("status", "error"); put("code", "replica_unavailable"); put("message", "No selected space has a readable replica; inspect connection_status")
            }
        }
    }

    override fun close() { slots.values.forEach { it.wake.close() } }
}
