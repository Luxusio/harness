package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.util.ConfluenceMarkdownConverter
import kotlinx.serialization.json.*
import java.nio.file.*
import java.text.Normalizer
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.time.format.DateTimeFormatter
import java.util.UUID
import java.net.URI
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeoutOrNull
import org.jsoup.Jsoup
import org.jsoup.parser.Parser

/** Explicit authored relations only. Ordinary hyperlinks remain navigation. */
internal fun knowledgeRelations(body: String, origin: String, space: String): JsonObject {
    val relations = linkedSetOf<JsonObject>()
    var complete = true
    var section = 0
    val types = mapOf("근거" to "evidence", "의존" to "depends_on", "반박" to "contradicts",
        "대체" to "supersedes", "관련" to "related")
    val prefix = Regex("^(근거|의존|반박|대체|관련)\\s*:\\s*")
    val excluded = setOf("pre", "code", "blockquote", "q", "ac:structured-macro", "ac:macro", "ac:plain-text-body")
    val parsed = Jsoup.parse(body, "", Parser.xmlParser())
    for (element in parsed.allElements) {
        if (element.parents().any { it.tagName() in excluded }) continue
        val level = element.tagName().takeIf { it.matches(Regex("h[1-6]")) }?.last()?.digitToInt()
        if (level != null) {
            if (section != 0 && level <= section) section = 0
            if (element.text() == "지식 연결" && element.allElements.none { it.tagName() in excluded }) section = level
            continue
        }
        if (section == 0 || element.tagName() !in setOf("p", "li") || element.parents().any { it.tagName() == "li" }) continue
        val text = element.text()
        val kind = prefix.find(text) ?: continue
        val links = element.getElementsByTag("ri:page").filter { page ->
            page.parent()?.tagName() == "ac:link" && page.parents().none { it.tagName() in setOf("ac:image", "ri:attachment") }
        } + element.getElementsByTag("a").filter { it.hasAttr("href") }
        val anchor = links.singleOrNull()?.let { link -> link.parent()?.takeIf { it.tagName() == "ac:link" } ?: link }
        val suffix = anchor?.let { element.html().substringAfter(it.outerHtml(), "") }.orEmpty()
        val reason = Regex("^[—–-]\\s+(.+)$").matchEntire(Jsoup.parse(suffix).text())?.groupValues?.get(1).orEmpty().trim()
        if (links.size != 1 || reason.isEmpty() || reason.length > 1000 || relations.size >= 100 ||
            element.allElements.any { it.tagName() in excluded }) {
            complete = false; continue
        }
        try {
            val link = links.single()
            val relation = buildJsonObject {
                put("type", types.getValue(kind.groupValues[1])); put("reason", reason)
                if (link.tagName() == "ri:page") {
                    val linkedSpace = link.attr("ri:space-key").ifEmpty { space }
                    require(linkedSpace == space)
                    if (link.hasAttr("ri:content-id")) {
                        val id = link.attr("ri:content-id"); require(id.matches(Regex("[1-9][0-9]*"))); put("to", id)
                    } else {
                        val title = link.attr("ri:content-title"); require(title.isNotBlank() && title.length <= 1000)
                        put("title", title)
                    }
                    link.parent()?.takeIf { it.tagName() == "ac:link" }?.attr("ac:anchor")?.takeIf { it.isNotEmpty() }?.let {
                        require(it.length <= 1000 && it.none(Char::isISOControl)); put("fragment", it)
                    }
                } else {
                    val href = link.attr("href").let { if (it.startsWith("/wiki/")) origin + it else it }
                    val uri = URI(href)
                    put("to", nativePageUrlId(href.substringBefore('#'), origin, space))
                    uri.rawFragment?.let { require(it.length <= 1000 && it.none(Char::isISOControl)); put("fragment", it) }
                }
            }
            relations += relation
        } catch (_: Exception) { complete = false }
    }
    return buildJsonObject { put("items", JsonArray(relations.toList())); put("complete", complete) }
}

/** Storage is authoritative. Markdown is a disposable search projection, never a write input. */
class ContextMirror(private val root: Path, private val remote: ConfluenceRemote) {
    /** Publish an already verified write without re-reading the remote inventory or bodies. */
    suspend fun publishVerified(config: ContextConfig, page: RemotePage, observedBefore: RemotePage? = null, recheck: () -> Unit = {}) {
        val manifestPath = ContextFiles.safe(root, "confluence/${config.space}/.sync/manifest.json")
        if (!Files.exists(manifestPath)) return
        val control = ContextFiles.safe(root, "confluence/${config.space}/.sync")
        val base = ContextFiles.safe(root, "confluence/${config.space}")
        CorpusFileLock.tryAcquire(ContextFiles.safe(control, "sync.lock")).let { lock ->
            require(lock != null) { "Confluence sync busy; retry later" }
            lock.use {
                recheck()
                requireCredentialBinding(readManifest(control.resolve("manifest.json"), config), config)
                if (Files.exists(ContextFiles.safe(control, "transaction.json"))) {
                    require(!noticeAccessDenied(control)) { "A current inventory is required before mirror recovery" }
                    if (config.isKnowledge) requireRootAccess(control, config, knowledgeRoot(config))
                }
                recover(base, control, config)
                val previous = readManifest(control.resolve("manifest.json"), config)
                require((previous["structurePolicy"]?.jsonPrimitive?.content ?: "strict") == config.structurePolicy &&
                    previous["pilotRootPageId"]?.jsonPrimitive?.content == config.pilotRootPageId &&
                    previous["knowledgeRootPageId"]?.jsonPrimitive?.content == config.knowledgeRootPageId &&
                    (previous["wholeSpace"]?.jsonPrimitive?.boolean ?: false) == config.wholeSpace) { "Mirror selection changed; sync first" }
                val node = page.node
                require(node.id.matches(Regex("[1-9][0-9]*")) && node.type == "page" && node.status == "current" &&
                    node.spaceId == config.spaceId && node.version > 0 && page.body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) {
                    "A verified current page in the configured space is required"
                }
                val old = previous.getValue("pages").jsonObject
                pathOwners(old)
                val prior = old[node.id]?.jsonObject
                if (prior != null && prior.getValue("version").jsonPrimitive.int > node.version) return
                val hash = ContextFiles.hash(page.body)
                if (prior?.get("version")?.jsonPrimitive?.int == node.version) {
                    val priorHash = prior["bodyHash"]?.jsonPrimitive?.content
                    // Inline markers may change storage without a page revision. Compare against the
                    // page actually read before that write, never an unrelated same-version local body.
                    val observedTransition = observedBefore?.let { it.node == node && ContextFiles.hash(it.body) == priorHash } == true
                    require(decode(prior) == node && (priorHash == hash || observedTransition)) {
                        "Same-version mirror content differs from the verified write"
                    }
                }
                val oldPath = prior?.get("path")?.jsonPrimitive?.content
                val oldDirectory = oldPath?.removeSuffix(".md")
                val unavailable = previous["unavailable"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty().toSet() - node.id
                val descendants = if (oldDirectory == null) emptyMap() else old.filterValues {
                    it.jsonObject.getValue("path").jsonPrimitive.content.startsWith("$oldDirectory/")
                }
                fun entries(path: String): JsonObject = JsonObject(old.toMutableMap().apply {
                    if (oldDirectory != null && oldPath != path) descendants.forEach { (id, value) ->
                        require(id !in unavailable) { "Unavailable descendant needs a full sync before relocation" }
                        val row = value.jsonObject
                        put(id, JsonObject(row + ("path" to JsonPrimitive(path.removeSuffix(".md") +
                            row.getValue("path").jsonPrimitive.content.removePrefix(oldDirectory)))))
                    }
                    put(node.id, JsonObject(encode(node, path, ".sync/storage/${node.id}-${node.version}-$hash.xhtml", hash) +
                        ("relations" to knowledgeRelations(page.body, config.site.trimEnd('/'), config.space))))
                })
                val path = if (config.wholeSpace) "pages/${segment(node.title)}~${node.id}.md"
                    else if (prior != null && prior["title"]?.jsonPrimitive?.content == node.title &&
                    prior["parentId"]?.jsonPrimitive?.contentOrNull == node.parentId) oldPath!! else {
                    val parentPath = when {
                        prior != null && prior["parentId"]?.jsonPrimitive?.contentOrNull == node.parentId -> oldPath!!.substringBeforeLast('/')
                        old[node.parentId]?.jsonObject != null -> old.getValue(node.parentId!!).jsonObject.getValue("path").jsonPrimitive.content.removeSuffix(".md")
                        node.parentId == config.homePageId && !config.isKnowledge -> "pages"
                        else -> error("The verified parent is not in the local mirror; sync its structure first")
                    }
                    require(oldDirectory == null || parentPath != oldDirectory && !parentPath.startsWith("$oldDirectory/")) { "Cyclic mirror relocation" }
                    val original = segment(node.title)
                    var candidate = "$parentPath/$original.md"
                    var attempt = 0
                    while (true) {
                        try { pathOwners(entries(candidate)); break } catch (e: IllegalArgumentException) {
                            require(attempt++ < 100) { "Unresolvable mirror filename collision" }
                            candidate = "$parentPath/$original~${node.id}${if (attempt > 1) "-$attempt" else ""}.md"
                        }
                    }
                    candidate
                }
                val nextPages = entries(path)
                pathOwners(nextPages)
                val changed = nextPages.filter { (id, value) -> old[id] != value }.keys + node.id
                val ownedPaths = old.values.map { it.jsonObject.getValue("path").jsonPrimitive.content }.toSet()
                changed.forEach { id ->
                    val destination = nextPages.getValue(id).jsonObject.getValue("path").jsonPrimitive.content
                    require(destination in ownedPaths || !Files.exists(ContextFiles.safe(base, destination))) { "Unowned file obstructs mirror publication" }
                }
                val pinned = previous["explicitPages"]?.jsonArray?.map { it.jsonPrimitive.content }.orEmpty().toMutableSet()
                if (prior == null) pinned += node.id
                require(pinned.size <= 500) { "Explicit mirror selection exceeds the bounded page limit" }
                val next = WikiChanges.documents(previous, JsonObject(previous + buildJsonObject {
                    put("pages", nextPages); put("explicitPages", JsonArray(pinned.map(::JsonPrimitive)))
                    put("unavailable", JsonArray(unavailable.map(::JsonPrimitive)))
                    put("changes", JsonArray(changed.map { id -> buildJsonObject {
                        val row = nextPages.getValue(id).jsonObject
                        put("pageId", id); old[id]?.jsonObject?.get("version")?.let { put("oldVersion", it) }
                        put("version", row.getValue("version")); put("title", row.getValue("title")); put("path", row.getValue("path"))
                        put("structural", old[id]?.jsonObject?.get("path") != row["path"])
                    } }))
                }))
                val stage = ContextFiles.safe(control, "stage-${UUID.randomUUID()}")
                Files.createDirectories(stage)
                try {
                    changed.forEach { id ->
                        val row = nextPages.getValue(id).jsonObject
                        val markdown = if (id == node.id) "---\npage_id: \"${node.id}\"\nversion: ${node.version}\nurl: ${config.site}/wiki/spaces/${config.space}/pages/${node.id}\n---\n\n" +
                            ConfluenceMarkdownConverter.htmlToMarkdown(page.body) else {
                            val source = ContextFiles.safe(base, old.getValue(id).jsonObject.getValue("path").jsonPrimitive.content)
                            require(Files.isRegularFile(source)) { "Relocated mirror page has no retained projection" }
                            String(Files.readAllBytes(source), Charsets.UTF_8)
                        }
                        ContextFiles.atomic(ContextFiles.safe(stage, row.getValue("path").jsonPrimitive.content), markdown)
                    }
                    recheck()
                    ContextFiles.atomic(ContextFiles.safe(base, nextPages.getValue(node.id).jsonObject.getValue("storage").jsonPrimitive.content), page.body)
                    ContextFiles.atomic(stage.resolve("manifest.json"), next.toString())
                    // The optional workset reuses the same journal while staging only changed content.
                    ContextFiles.atomic(control.resolve("transaction.json"), buildJsonObject {
                        put("stage", stage.fileName.toString()); put("files", JsonArray(changed.map { nextPages.getValue(it).jsonObject.getValue("path") }))
                    }.toString())
                    recover(base, control, config)
                } finally {
                    if (!Files.exists(control.resolve("transaction.json"))) deleteTree(stage)
                }
            }
        }
    }

    suspend fun sync(config: ContextConfig, mode: String = "incremental", pageIds: List<String> = emptyList(),
                     refreshRequestedBodies: Boolean = true): JsonObject =
        sync(config, mode, pageIds, refreshRequestedBodies, admittedRefresh = false)

    internal suspend fun refreshAdmittedBodies(config: ContextConfig, pageIds: List<String>): JsonObject =
        sync(config, "pages", pageIds, refreshRequestedBodies = true, admittedRefresh = true)

    private suspend fun sync(config: ContextConfig, mode: String, pageIds: List<String>,
                             refreshRequestedBodies: Boolean, admittedRefresh: Boolean): JsonObject {
        require(mode in setOf("incremental", "full", "pages")) { "Unknown sync mode" }
        if (mode == "pages") require(pageIds.isNotEmpty() && pageIds.size <= 100 && pageIds.size == pageIds.toSet().size &&
            pageIds.all { it.matches(Regex("[1-9][0-9]*")) }) { "Declare 1..100 unique positive page IDs" }
        val requested = if (mode == "pages") pageIds.toSet() else emptySet()
        val scopeRoot = knowledgeRoot(config)
        val base = ContextFiles.safe(root, "confluence/${config.space}")
        val control = ContextFiles.safe(root, "confluence/${config.space}/.sync")
        Files.createDirectories(control)
        CorpusFileLock.tryAcquire(ContextFiles.safe(control, "sync.lock")).let { lock ->
            require(lock != null) { "Confluence sync busy; retry later" }
            withSyncRootAccess(lock, control, config, scopeRoot) {
                val priorDenial = if (config.isKnowledge && mode != "pages") noticeDenialToken(control) else null
                if (config.isKnowledge) requireRootAccess(control, config, scopeRoot)
                if (mode == "pages") requireCredentialBinding(readManifest(control.resolve("manifest.json"), config), config)
                recover(base, control, config, allowCredentialSwitch = mode != "pages")
                val manifestPath = control.resolve("manifest.json")
                val previous = readManifest(manifestPath, config)
                val old = previous["pages"]?.jsonObject ?: buildJsonObject {}
                if (admittedRefresh) {
                    val unavailable = previous["unavailable"]?.jsonArray?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
                    require(config.isKnowledge && previous["inventoryComplete"] == JsonPrimitive(true) &&
                        requested.all { it in old && it !in unavailable && old.getValue(it).jsonObject["status"] == JsonPrimitive("current") }) {
                        "Authoritative body refresh requires previously admitted current knowledge pages"
                    }
                }
                val nodes = linkedMapOf<String, RemoteNode>()
                old.forEach { (id, value) -> nodes[id] = decode(value.jsonObject) }
                val now = Instant.now()
                val selectionChanged = (previous["structurePolicy"]?.jsonPrimitive?.content ?: "strict") != config.structurePolicy ||
                    previous["pilotRootPageId"]?.jsonPrimitive?.content != config.pilotRootPageId ||
                    previous["knowledgeRootPageId"]?.jsonPrimitive?.content != config.knowledgeRootPageId ||
                    (previous["wholeSpace"]?.jsonPrimitive?.boolean ?: false) != config.wholeSpace
                val pinned = if (selectionChanged) linkedSetOf<String>() else previous["explicitPages"]?.jsonArray
                    ?.map { it.jsonPrimitive.content }?.toCollection(linkedSetOf()) ?: linkedSetOf()
                require(pinned.size <= 500 && pinned.all { it.matches(Regex("[1-9][0-9]*")) && it in old }) { "Invalid explicit mirror selection" }
                if (!admittedRefresh) pinned.addAll(requested)
                require(pinned.size <= 500) { "Explicit mirror selection exceeds the bounded page limit" }
                val inventoryComplete = previous["inventoryComplete"]?.jsonPrimitive?.boolean ?: previous.isNotEmpty()
                // A write-through of one page must not wait for every default-scope body in a new workspace.
                val full = (config.isKnowledge && mode != "pages") || mode == "full" || (mode != "pages" && (old.isEmpty() || selectionChanged || !inventoryComplete))
                val changed = when {
                    full -> remote.inventory(config)
                    mode == "pages" -> {
                        pageIds.map { if (config.isKnowledge) remote.requireScope(config, it) else remote.node(it) }
                    }
                    config.isKnowledge -> remote.inventory(config)
                    else -> {
                        val cursor = previous["cursor"]?.jsonPrimitive?.content?.let { Instant.parse(it) } ?: now
                        val date = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm").withZone(ZoneOffset.UTC).format(cursor.minusSeconds(300))
                        remote.search("space = \"${config.space}\" and type = page and lastmodified >= \"$date\"")
                    }
                }
                val unresolved = if (admittedRefresh) previous["unavailable"]?.jsonArray.orEmpty()
                    .map { it.jsonPrimitive.content }.toCollection(linkedSetOf()) else linkedSetOf()
                if (full) {
                    val ids = changed.map { it.id }.toSet()
                    // An absent inventory entry can be a permission change. Only positive metadata proves removal.
                    old.keys.filter { it !in ids && !selectionChanged }.forEach { id ->
                        try { nodes[id] = if (config.isKnowledge) remote.requireScope(config, id) else remote.node(id, old.getValue(id).jsonObject["type"]?.jsonPrimitive?.content ?: "page") }
                        catch (e: CancellationException) { throw e }
                        catch (e: RootScopeDenial) { throw e }
                        catch (_: Exception) { unresolved += id }
                    }
                }
                if (selectionChanged) nodes.clear()
                changed.forEach { node ->
                    require(node.spaceId == config.spaceId) { "Cross-space inventory entry" }
                    nodes[node.id] = node
                }
                // Explicit published pages remain selected after later background/full syncs, including future releases.
                // Re-read their metadata; a permission failure is not positive evidence that the page was removed.
                pinned.filter { it !in requested }.forEach { id ->
                    try {
                        val latest = if (config.isKnowledge) remote.requireScope(config, id) else remote.node(id)
                        unresolved.remove(id)
                        if (latest.spaceId != config.spaceId || latest.status != "current") {
                            pinned.remove(id); nodes.remove(id)
                        } else nodes[id] = latest
                    } catch (e: CancellationException) { throw e }
                    catch (e: RootScopeDenial) { throw e }
                    catch (_: Exception) { unresolved += id }
                }
                if (!config.wholeSpace) {
                    val home = if (config.isKnowledge) remote.requireScope(config, scopeRoot) else remote.node(config.homePageId)
                    require(home.spaceId == config.spaceId && home.status == "current") { "Configured mirror home is not current in the configured space" }
                    nodes[scopeRoot] = home
                }
                // Resolve native folder parents instead of treating missing parents as orphans.
                for (node in if (config.wholeSpace) emptyList() else nodes.values.toList()) {
                    if (admittedRefresh && node.id in unresolved) continue
                    var parent = if (node.id == scopeRoot) null else node.parentId
                    val seen = mutableSetOf(node.id)
                    while (parent != null && parent != scopeRoot) {
                        require(seen.add(parent) && seen.size <= 100) { "Cyclic/deep Confluence ancestry" }
                        var ancestor = nodes[parent]
                        if (ancestor == null || node.id in pinned) {
                            ancestor = try {
                                try {
                                    if (config.isKnowledge) remote.connectionScopeNode(config, parent, "folder")
                                    else remote.node(parent, "folder")
                                } catch (e: ContextRemoteException) {
                                    if (e.code != "http_404") throw e
                                    if (config.isKnowledge) remote.connectionScopeNode(config, parent) else remote.node(parent, "page")
                                }
                            } catch (e: CancellationException) { throw e }
                            catch (e: RootScopeDenial) { throw e }
                            catch (e: Exception) {
                                if (node.id in pinned && node.id !in requested && node.id in old) {
                                    unresolved += node.id; break
                                }
                                throw e
                            }
                            if (ancestor.spaceId != config.spaceId || (node.id in pinned && ancestor.status != "current")) {
                                if (node.id in pinned && node.id !in requested) { pinned.remove(node.id); nodes.remove(node.id); break }
                                error("Cross-space or non-current parent")
                            }
                            nodes[parent] = ancestor
                        }
                        parent = ancestor.parentId
                    }
                }
                fun chain(node: RemoteNode): List<RemoteNode>? {
                    // A whole-space projection must not require access to a hidden parent.
                    if (config.wholeSpace) return listOf(node)
                    val result = mutableListOf<RemoteNode>()
                    var current: RemoteNode? = node
                    val seen = mutableSetOf<String>()
                    while (current != null && current.id != scopeRoot) {
                        require(seen.add(current.id) && seen.size <= 100) { "Cyclic/deep Confluence ancestry" }
                        result.add(0, current)
                        current = current.parentId?.let { nodes[it] }
                    }
                    return if (current?.id == scopeRoot) { if (config.isKnowledge) result.add(0, current); result } else null
                }
                if (config.isKnowledge && config.knowledgeRootPageId != null) {
                    val knowledgeRoot = nodes[config.knowledgeRootPageId] ?: error("Registered knowledge root is absent from inventory")
                    val ancestry = chain(knowledgeRoot)
                    require(!ancestry.isNullOrEmpty() && ancestry.all { it.status == "current" && it.spaceId == config.spaceId }) {
                        "Registered knowledge root must be current and descend from the configured home"
                    }
                }
                pinned.toList().filter { it !in unresolved }.forEach { id ->
                    val node = nodes[id]
                    val ancestry = node?.let(::chain)
                    val allowed = node?.type == "page" && node.status == "current" && !ancestry.isNullOrEmpty() &&
                        ancestry.all { it.status == "current" && it.spaceId == config.spaceId && !it.title.contains(Regex("OLD|Delete|삭제|\\(Locked\\)")) } &&
                        (config.structurePolicy != "pilot" || ancestry.any { it.id == config.pilotRootPageId }) &&
                        (!config.isKnowledge || config.knowledgeRootPageId == null || ancestry.any { it.id == config.knowledgeRootPageId })
                    if (id in requested && !admittedRefresh) require(allowed) { "Explicit pages must be current and below the configured home and policy root" }
                    else if (!allowed) pinned.remove(id)
                }
                val selected = nodes.values.filter { node ->
                    val ancestry = chain(node)
                    val scope = if (config.isKnowledge) ancestry != null && ancestry.isNotEmpty() &&
                        (config.knowledgeRootPageId == null || ancestry.any { it.id == config.knowledgeRootPageId }) &&
                        ancestry.all { it.status == "current" && it.spaceId == config.spaceId }
                        else if (config.structurePolicy == "pilot") ancestry != null &&
                        ancestry.any { it.id == config.pilotRootPageId } && ancestry.all { it.status == "current" && it.spaceId == config.spaceId }
                        else inScope(ancestry) || node.id in pinned
                    node.id !in unresolved && node.type == "page" && node.status == "current" && scope &&
                        (mode != "pages" || node.id in old || node.id in requested)
                }
                // A page owns both <title>.md and a potential <title>/ subtree. Reserve both names
                // together so e.g. sibling titles A and A.md can never need one path as file and directory.
                val retained = JsonObject(unresolved.associateWith { old.getValue(it) })
                val (retainedFiles, retainedDirectories) = pathOwners(retained, old)
                val segments = mutableMapOf<String, String>()
                nodes.values.groupBy { it.parentId }.values.sortedBy { chain(it.first())?.size ?: Int.MAX_VALUE }.forEach { siblings ->
                    val occupied = mutableSetOf<String>()
                    siblings.sortedBy { it.id }.forEach { node ->
                        val original = segment(node.title) + if (config.wholeSpace) "~${node.id}" else ""
                        var candidate = original
                        var attempt = 0
                        fun names() = listOf(canonical(candidate), canonical("$candidate.md"))
                        val ancestry = chain(node)
                        val parentPath = ancestry?.dropLast(1)?.joinToString("/") { segments.getValue(it.id) }
                        fun retainedCollision(): Boolean {
                            if (ancestry.isNullOrEmpty()) return false
                            val directory = canonical("pages/" + if (parentPath.isNullOrEmpty()) candidate else "$parentPath/$candidate")
                            val file = "$directory.md"
                            return directory in retainedFiles || file in retainedDirectories ||
                                (directory in retainedDirectories && retainedDirectories[directory] != node.id) ||
                                (file in retainedFiles && retainedFiles[file] != node.id)
                        }
                        while (names().any { it in occupied } || retainedCollision()) {
                            require(attempt++ < 100) { "Unresolvable mirror filename collision" }
                            candidate = "$original~${node.id}" + if (attempt > 1) "-$attempt" else ""
                        }
                        require(candidate.toByteArray(Charsets.UTF_8).size <= 240) { "Mirror component exceeds filename budget" }
                        occupied.addAll(names()); segments[node.id] = candidate
                    }
                }
                val paths = selected.associate { node ->
                    node.id to (chain(node)!!.joinToString("/") { segments.getValue(it.id) } + ".md")
                }
                val staged = control.resolve("stage-${UUID.randomUUID()}")
                Files.createDirectories(staged)
                val next = linkedMapOf<String, JsonElement>()
                val changes = mutableListOf<JsonElement>()
                try {
                    selected.forEach { node ->
                        if (config.isKnowledge && remote.requireScope(config, node.id) != node)
                            throw ContextRemoteException("scope_metadata_changed", "Mirror page left its admitted knowledge hierarchy")
                        val prior = old[node.id]?.jsonObject
                        val oldStorage = prior?.get("storage")?.jsonPrimitive?.content?.let { ContextFiles.safe(base, it) }
                        val refreshBody = refreshRequestedBodies && mode == "pages" && node.id in requested
                        val cached = if (!refreshBody && prior?.get("version")?.jsonPrimitive?.int == node.version && oldStorage != null &&
                            Files.isRegularFile(oldStorage) && Files.size(oldStorage) <= 4L * 1024 * 1024)
                            String(Files.readAllBytes(oldStorage), Charsets.UTF_8) else null
                        val page = if (cached != null && ContextFiles.hash(cached) == prior?.get("bodyHash")?.jsonPrimitive?.content)
                            RemotePage(node, cached) else remote.page(node.id, if (config.isKnowledge) node else null, config, freshStorage = refreshBody)
                        require(page.node == node) { "Page identity, hierarchy or version changed during sync; retry" }
                        val path = "pages/${paths.getValue(node.id)}"
                        val storage = ".sync/storage/${node.id}-${node.version}-${ContextFiles.hash(page.body)}.xhtml"
                        ContextFiles.atomic(ContextFiles.safe(base, storage).also { Files.createDirectories(it.parent) }, page.body)
                        val markdown = "---\npage_id: \"${node.id}\"\nversion: ${node.version}\nurl: ${config.site}/wiki/spaces/${config.space}/pages/${node.id}\n---\n\n" +
                            ConfluenceMarkdownConverter.htmlToMarkdown(page.body)
                        val stagedFile = ContextFiles.safe(staged, path)
                        Files.createDirectories(stagedFile.parent)
                        ContextFiles.atomic(stagedFile, markdown)
                        next[node.id] = JsonObject(encode(node, path, storage, ContextFiles.hash(page.body)) +
                            ("relations" to knowledgeRelations(page.body, config.site.trimEnd('/'), config.space)))
                        if (prior != null && (prior["version"]?.jsonPrimitive?.int != node.version || prior["path"]?.jsonPrimitive?.content != path ||
                                prior["bodyHash"]?.jsonPrimitive?.content != ContextFiles.hash(page.body))) {
                            changes += buildJsonObject {
                                put("pageId", node.id); put("oldVersion", prior.getValue("version")); put("version", node.version)
                                put("title", node.title); put("path", path)
                                put("structural", prior["path"]?.jsonPrimitive?.content != path)
                            }
                        }
                    }
                    retained.forEach { (id, prior) -> next[id] = prior }
                    pathOwners(JsonObject(next), reservedDirectories = retainedDirectories)
                    retained.forEach { (_, value) ->
                        val prior = value.jsonObject
                        val priorPath = ContextFiles.safe(base, prior.getValue("path").jsonPrimitive.content)
                        require(Files.isRegularFile(priorPath)) { "Unavailable mirror page has no retained projection" }
                        val dest = ContextFiles.safe(staged, prior.getValue("path").jsonPrimitive.content)
                        Files.createDirectories(dest.parent)
                        Files.copy(priorPath, dest)
                    }
                    val complete = buildJsonObject {
                        put("schemaVersion", 1); put("site", origin(config.site)); put("spaceId", config.spaceId); put("homePageId", config.homePageId)
                        put("wholeSpace", config.wholeSpace)
                        put("structurePolicy", config.structurePolicy); config.pilotRootPageId?.let { put("pilotRootPageId", it) }
                        config.knowledgeRootPageId?.let { put("knowledgeRootPageId", it) }
                        put("explicitPages", JsonArray(pinned.map(::JsonPrimitive)))
                        put("inventoryComplete", full || (!selectionChanged && inventoryComplete))
                        if (config.isKnowledge) put("credentialBinding", remote.credentialFingerprint)
                        put("cursor", if ((mode == "pages" || unresolved.isNotEmpty()) && previous["cursor"] != null) previous.getValue("cursor") else JsonPrimitive(now.toString()))
                        put("pages", JsonObject(next)); put("unavailable", JsonArray(unresolved.map(::JsonPrimitive)))
                        val folderNodes = if (config.wholeSpace) nodes.values.asSequence()
                            else selected.asSequence().flatMap { chain(it).orEmpty().asSequence() }
                        val folders = folderNodes.filter { it.type == "folder" && it.id != scopeRoot && it.parentId != null }
                            .distinctBy { it.id }.associate { it.id to JsonPrimitive(requireNotNull(it.parentId)) }
                        put("folderParents", JsonObject(folders))
                        put("changes", JsonArray(changes))
                    }
                    ContextFiles.atomic(staged.resolve("manifest.json"), WikiChanges.documents(previous, complete).toString())
                    // Once this pointer exists, recovery only finishes local projection; never repeats remote writes.
                    ContextFiles.atomic(control.resolve("transaction.json"), buildJsonObject { put("stage", staged.fileName.toString()) }.toString())
                    withNoticeAccessLock(control) {
                        recoverLocked(base, control, config, allowCredentialSwitch = full)
                        if (full && (previous["credentialBinding"] != JsonPrimitive(remote.credentialFingerprint) ||
                                priorDenial != null && readDenialToken(control) == priorDenial))
                            Files.deleteIfExists(noticeDenialPath(control))
                    }
                    return buildJsonObject {
                        put("status", if (unresolved.isEmpty()) "synced" else "synced_with_unavailable")
                        put("pages", next.size); put("changes", JsonArray(changes)); put("unavailable", JsonArray(unresolved.map(::JsonPrimitive)))
                        put("manifest", manifestPath.toString())
                    }
                } finally {
                    if (!Files.exists(control.resolve("transaction.json"))) deleteTree(staged)
                }
            }
        }
    }

    private fun noticeDenialPath(control: Path) = ContextFiles.safe(control, WikiChanges.noticeDenialMarker)
    private fun noticeAccessDenied(control: Path) = Files.exists(noticeDenialPath(control))
    private fun knowledgeRoot(config: ContextConfig) =
        if (config.isKnowledge) config.knowledgeRootPageId ?: config.homePageId else config.homePageId

    private fun readDenialToken(control: Path): String? {
        val path = noticeDenialPath(control)
        return if (Files.isRegularFile(path) && Files.size(path) <= 64) Files.readString(path) else null
    }

    private suspend inline fun <T> withNoticeAccessLock(control: Path, block: () -> T): T =
        awaitCorpusLock(ContextFiles.safe(control, "notice-access.lock")).use { block() }

    private suspend fun noticeDenialToken(control: Path): String? =
        withNoticeAccessLock(control) { readDenialToken(control) }

    private suspend fun recordRootDenial(control: Path, config: ContextConfig) = withNoticeAccessLock(control) {
        val manifest = ContextFiles.safe(control, "manifest.json")
        if (!Files.isRegularFile(manifest)) return@withNoticeAccessLock
        val current = read(manifest)
        // A retired credential or previous native identity cannot revoke a newer admission.
        if (current["credentialBinding"] != JsonPrimitive(remote.credentialFingerprint)) return@withNoticeAccessLock
        try { validateIdentity(current, config) } catch (_: IllegalArgumentException) { return@withNoticeAccessLock }
        if ((current["structurePolicy"]?.jsonPrimitive?.content ?: "strict") != config.structurePolicy ||
            current["pilotRootPageId"]?.jsonPrimitive?.content != config.pilotRootPageId ||
            current["knowledgeRootPageId"]?.jsonPrimitive?.content != config.knowledgeRootPageId) return@withNoticeAccessLock
        ContextFiles.atomic(noticeDenialPath(control), UUID.randomUUID().toString())
    }

    internal suspend fun recordNoticeRootDenial(config: ContextConfig) {
        try {
            val control = ContextFiles.safe(root, "confluence/${config.space}/.sync")
            if (Files.isDirectory(control)) recordRootDenial(control, config)
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { /* Preserve the original provider result if the replica is unwritable. */ }
    }

    private fun shouldCheckRoot(failure: ContextRemoteException) = failure.isScopeDenial() || failure.code == "scope_metadata_changed"

    private suspend fun requireRootAccess(control: Path, config: ContextConfig, rootId: String) {
        try {
            if (config.wholeSpace) remote.requireSpace(config) else remote.requireScope(config, rootId)
        } catch (failure: ContextRemoteException) {
            if (failure.isScopeDenial()) recordRootDenial(control, config)
            throw failure
        }
    }

    private suspend fun recheckRootAccess(control: Path, config: ContextConfig, rootId: String) {
        try { withTimeoutOrNull(3_000) { requireRootAccess(control, config, rootId) } }
        catch (e: CancellationException) { throw e }
        catch (_: ContextRemoteException) { /* A confirmed denial has already been recorded. */ }
        catch (_: IllegalArgumentException) { /* Ambiguous metadata leaves the existing marker unchanged. */ }
    }

    private suspend inline fun <T> withSyncRootAccess(lock: CorpusFileLock, control: Path, config: ContextConfig,
                                                       rootId: String, block: () -> T): T = lock.use {
        try { block() } catch (failure: ContextRemoteException) {
            if (config.isKnowledge) {
                if (failure is RootScopeDenial) recordRootDenial(control, config)
                else if (!noticeAccessDenied(control) && shouldCheckRoot(failure)) recheckRootAccess(control, config, rootId)
            }
            throw failure
        }
    }

    internal suspend fun pollComments(config: ContextConfig, pageIds: List<String>, intervalSeconds: Long) {
        if (pageIds.isEmpty()) return
        val base = ContextFiles.safe(root, "confluence/${config.space}")
        val control = ContextFiles.safe(base, ".sync")
        if (!Files.isDirectory(control)) return
        CorpusFileLock.tryAcquire(ContextFiles.safe(control, "comments.lock"))?.use {
            pollAdmittedComments(base, control, config, pageIds, intervalSeconds)
        }
    }

    private suspend fun pollAdmittedComments(base: Path, control: Path, config: ContextConfig,
                                             pageIds: List<String>, intervalSeconds: Long) {
        val manifest = ContextFiles.safe(control, "manifest.json")
        val started = System.currentTimeMillis()
        val watchedRows = withSyncLock(control) {
            if (noticeAccessDenied(control) || Files.exists(ContextFiles.safe(control, "transaction.json"))) return@withSyncLock null
            val current = readManifest(manifest, config)
            if (current["inventoryComplete"] != JsonPrimitive(true) || !credentialBindingMatches(current, config)) return@withSyncLock null
            val admitted = WikiChanges.admitted(current)
            pageIds.sortedBy { WikiChanges.commentAttemptedAt(current, it) ?: Long.MIN_VALUE }.mapNotNull { id ->
                admitted[id]?.takeIf { WikiChanges.commentDue(current, id, started, intervalSeconds * 1000) }?.let { id to it }
            }.toMap()
        } ?: return
        if (watchedRows.isEmpty()) return
        val attempted = linkedSetOf<String>()
        val observed = linkedMapOf<String, String>()
        var rootCheckNeeded = false
        var rootDenialObserved = false
        // An interrupted page keeps its earlier attempt time when the prior targets consumed the budget.
        withTimeoutOrNull(30_000) {
            remote.wikiOperation(maxRequests = 512) {
                for ((id, row) in watchedRows) {
                    if (!remote.wikiBudgetRemaining()) break
                    val expected = decode(row)
                    val fingerprint = try {
                        withTimeoutOrNull(8_000) {
                            require(remote.requireScope(config, id) == expected)
                            val inventory = remote.commentInventory("confluence", id, expected, config)
                            require(remote.requireScope(config, id) == expected)
                            WikiChanges.commentFingerprint(inventory)
                        }
                    } catch (e: CancellationException) { throw e } catch (failure: ContextRemoteException) {
                        if (failure.code in setOf("wiki_request_limit", "wiki_body_limit")) {
                            // A page exhausting a full scan alone must still yield to its peers next time.
                            if (attempted.isEmpty()) attempted += id
                            break
                        }
                        if (shouldCheckRoot(failure)) rootCheckNeeded = true
                        if (failure is RootScopeDenial) {
                            rootDenialObserved = true
                            break
                        }
                        null
                    } catch (_: Exception) { null }
                    attempted += id
                    if (fingerprint != null) observed[id] = fingerprint
                }
            }
        }
        if (rootDenialObserved) {
            recordRootDenial(control, config)
            return
        }
        if (rootCheckNeeded && !confirmWatchRootAccess(control, config)) return
        if (attempted.isEmpty()) return
        withSyncLock(control) {
            if (noticeAccessDenied(control) || Files.exists(ContextFiles.safe(control, "transaction.json"))) return@withSyncLock
            val current = readManifest(manifest, config)
            if (current["inventoryComplete"] != JsonPrimitive(true) || !credentialBindingMatches(current, config)) return@withSyncLock
            val currentPages = WikiChanges.admitted(current)
            val confirmed = observed.filterKeys { currentPages[it] == watchedRows[it] }
            val next = WikiChanges.comments(current, started, attempted, confirmed)
            val stage = ContextFiles.safe(control, "stage-${UUID.randomUUID()}")
            val journal = ContextFiles.safe(control, "transaction.json")
            Files.createDirectories(stage)
            try {
                ContextFiles.atomic(ContextFiles.safe(stage, "manifest.json"), next.toString())
                ContextFiles.atomic(journal, buildJsonObject {
                    put("stage", stage.fileName.toString()); put("files", JsonArray(emptyList()))
                }.toString())
                recover(base, control, config)
            } finally {
                if (!Files.exists(journal)) deleteTree(stage)
            }
        }
    }

    private suspend fun confirmWatchRootAccess(control: Path, config: ContextConfig): Boolean =
        withTimeoutOrNull(5_000) {
            val lockPath = ContextFiles.safe(control, "sync.lock")
            awaitCorpusLock(lockPath).use {
                if (!credentialBindingMatches(readManifest(control.resolve("manifest.json"), config), config)) return@use false
                if (!noticeAccessDenied(control)) recheckRootAccess(control, config, knowledgeRoot(config))
                !noticeAccessDenied(control)
            }
        } ?: false

    private suspend fun awaitCorpusLock(path: Path): CorpusFileLock {
        while (true) {
            CorpusFileLock.tryAcquire(path)?.let { return it }
            delay(25)
        }
    }

    private suspend inline fun <T> withSyncLock(control: Path, block: () -> T): T? =
        CorpusFileLock.tryAcquire(ContextFiles.safe(control, "sync.lock"))?.use { block() }

    fun manifest(config: ContextConfig): JsonObject {
        val path = ContextFiles.safe(root, "confluence/${config.space}/.sync/manifest.json")
        return readManifest(path, config).also {
            val journal = path.parent.resolve("transaction.json")
            if (Files.exists(journal)) {
                val stage = read(journal).getValue("stage").jsonPrimitive.content
                require(stage.matches(Regex("stage-[a-f0-9-]+"))) { "Invalid mirror recovery state" }
                validateIdentity(read(ContextFiles.safe(path.parent, "$stage/manifest.json")), config)
            }
        }
    }

    private fun origin(site: String): String {
        require(ContextConfig.validOrigin(site)) { "Invalid mirror site origin" }
        val uri = URI(site)
        return "https://${uri.host.lowercase(Locale.ROOT)}" + if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
    }

    private fun validateIdentity(manifest: JsonObject, config: ContextConfig) {
        val site = manifest["site"]?.jsonPrimitive?.content
        require(site != null && origin(site) == origin(config.site)) {
            "Mirror site is missing or differs from the configured origin; archive the disposable mirror directory and rebuild it for this site"
        }
        require(manifest["spaceId"]?.jsonPrimitive?.content == config.spaceId &&
            manifest["homePageId"]?.jsonPrimitive?.content == config.homePageId &&
            (manifest["wholeSpace"]?.jsonPrimitive?.boolean ?: false) == config.wholeSpace) { "Mirror belongs to another space/home or selection kind" }
    }

    private fun readManifest(path: Path, config: ContextConfig): JsonObject =
        if (Files.exists(path)) read(path).also { validateIdentity(it, config) } else buildJsonObject {}

    private fun canonical(value: String) = Normalizer.normalize(value, Normalizer.Form.NFC).lowercase(Locale.ROOT)

    private fun pathOwners(pages: JsonObject, knownPages: JsonObject = pages,
                           reservedDirectories: Map<String, String?> = emptyMap()): Pair<Map<String, String>, Map<String, String?>> {
        fun pagePath(value: JsonElement): String {
            val path = value.jsonObject.getValue("path").jsonPrimitive.content
            require(path.startsWith("pages/") && path.endsWith(".md") && '\\' !in path &&
                path.split('/').all { it.isNotEmpty() && it != "." && it != ".." }) { "Invalid mirror Markdown path" }
            return canonical(path)
        }
        val knownDirectories = mutableMapOf<String, String>()
        knownPages.forEach { (id, value) ->
            val directory = pagePath(value).removeSuffix(".md")
            require(knownDirectories.put(directory, id) == null) { "Mirror subtree has multiple owners" }
        }
        val files = linkedMapOf<String, String>()
        val directories = mutableMapOf<String, String?>()
        fun reserveDirectory(path: String, owner: String?) {
            require(owner == null || path !in reservedDirectories || reservedDirectories[path] == owner) {
                "Mirror subtree conflicts with a retained page"
            }
            if (path in directories) {
                val existing = directories[path]
                require(existing == null || owner == null || existing == owner) { "Mirror subtree has multiple owners" }
                if (owner != null) directories[path] = owner
            } else directories[path] = owner
        }
        pages.forEach { (id, value) ->
            val path = pagePath(value)
            require(files.put(path, id) == null) { "Mirror Markdown path has multiple owners" }
            reserveDirectory(path.removeSuffix(".md"), id)
            var directory = path.substringBeforeLast('/')
            val parentId = value.jsonObject["parentId"]?.jsonPrimitive?.content
            var directParent = true
            while (directory != "pages") {
                val known = knownDirectories[directory]
                val parent = if (directParent) parentId else null
                require(known == null || parent == null || known == parent) { "Mirror subtree has multiple owners" }
                reserveDirectory(directory, parent ?: known)
                directory = directory.substringBeforeLast('/')
                directParent = false
            }
        }
        require(files.keys.none { it in directories }) { "Mirror path is both a file and directory" }
        return files to directories
    }

    private fun credentialBindingMatches(manifest: JsonObject, config: ContextConfig): Boolean =
        !config.isKnowledge || manifest["credentialBinding"] == JsonPrimitive(remote.credentialFingerprint)

    private fun requireCredentialBinding(manifest: JsonObject, config: ContextConfig) {
        require(credentialBindingMatches(manifest, config)) { "Credential reauthorization requires a complete inventory sync" }
    }

    private suspend fun recover(base: Path, control: Path, config: ContextConfig, allowCredentialSwitch: Boolean = false) =
        withNoticeAccessLock(control) { recoverLocked(base, control, config, allowCredentialSwitch) }

    private fun recoverLocked(base: Path, control: Path, config: ContextConfig, allowCredentialSwitch: Boolean = false) {
        val previous = readManifest(ContextFiles.safe(control, "manifest.json"), config)
        val journal = ContextFiles.safe(control, "transaction.json")
        if (!Files.exists(journal)) return
        val transaction = read(journal)
        val stageName = transaction.getValue("stage").jsonPrimitive.content
        require(stageName.matches(Regex("stage-[a-f0-9-]+"))) { "Invalid mirror recovery state" }
        val stage = ContextFiles.safe(control, stageName)
        val next = read(ContextFiles.safe(stage, "manifest.json"))
        validateIdentity(next, config)
        if (!allowCredentialSwitch) {
            requireCredentialBinding(previous, config)
            requireCredentialBinding(next, config)
        } else if (config.isKnowledge && transaction["files"] != null) {
            // Finish a retired credential's local journal without changing its authority.
            // Only the subsequent complete inventory may admit the current credential.
            require(previous["credentialBinding"] == next["credentialBinding"]) { "Partial mirror transaction changes credential binding" }
        }
        val pages = next.getValue("pages").jsonObject
        val priorPages = previous["pages"]?.jsonObject ?: buildJsonObject {}
        val retained = JsonObject(next["unavailable"]?.jsonArray?.map { it.jsonPrimitive.content }?.distinct()?.associateWith { id ->
            val prior = priorPages.getValue(id)
            require(pages[id] == prior) { "Unavailable mirror page differs from the previous checkpoint" }
            prior
        } ?: emptyMap())
        val (_, retainedDirectories) = pathOwners(retained, priorPages)
        pathOwners(pages, reservedDirectories = retainedDirectories)
        val desired = pages.values.map { it.jsonObject.getValue("path").jsonPrimitive.content }.toSet()
        val workset = transaction["files"]?.jsonArray?.map { it.jsonPrimitive.content }?.let { files ->
            require(files.size == files.toSet().size && files.all { it in desired } && pages.keys.containsAll(priorPages.keys)) { "Invalid partial mirror transaction" }
            pages.forEach { (id, row) ->
                require(row == priorPages[id] || row.jsonObject.getValue("path").jsonPrimitive.content in files) { "Unstaged mirror change" }
            }
            files.toSet()
        } ?: desired
        desired.forEach { relative ->
            if (relative !in workset) require(Files.isRegularFile(ContextFiles.safe(base, relative))) { "Unchanged mirror projection is unavailable" }
            ContextFiles.safe(base, relative)
        }
        workset.forEach { relative ->
            require(Files.isRegularFile(ContextFiles.safe(stage, relative))) { "Incomplete mirror transaction" }
            ContextFiles.safe(base, relative)
        }
        // The complete desired generation is already staged. Remove only obsolete owned files
        // before creating new directories; recovery may safely repeat this after any interruption.
        previous["pages"]?.jsonObject?.values?.forEach { value ->
            val name = value.jsonObject.getValue("path").jsonPrimitive.content
            if (name !in desired) {
                val obsolete = ContextFiles.safe(base, name)
                Files.deleteIfExists(obsolete)
                var parent = obsolete.parent
                val pagesRoot = base.resolve("pages")
                while (parent != null && parent != pagesRoot && parent.startsWith(pagesRoot) && Files.isDirectory(parent)) {
                    val empty = Files.newDirectoryStream(parent).use { !it.iterator().hasNext() }
                    if (!empty) break
                    Files.delete(parent); parent = parent.parent
                }
            }
        }
        workset.forEach { relative ->
            val source = ContextFiles.safe(stage, relative)
            val destination = ContextFiles.safe(base, relative)
            Files.createDirectories(destination.parent)
            ContextFiles.atomic(destination, String(Files.readAllBytes(source), Charsets.UTF_8))
        }
        ContextFiles.atomic(control.resolve("manifest.json"), next.toString())
        val priorState = control.resolve("state.json").takeIf { Files.exists(it) }?.let(::read)
        ContextFiles.atomic(control.resolve("state.json"), buildJsonObject {
            put("cursor", next.getValue("cursor")); put("status", "synced")
            next["credentialBinding"]?.let { put("credentialBinding", it) }
            // Inventory and write-through do not establish authoritative body freshness.
            priorState?.get("bodyRefreshedAt")?.let { put("bodyRefreshedAt", it) }
        }.toString())
        // A recovery replays the staged sequence, including when the manifest already reached disk.
        next[WikiChanges.noticesKey]?.let { feed ->
            val text = feed.toString()
            require(text.toByteArray(Charsets.UTF_8).size <= 64 * 1024) { "Wiki notification feed exceeds its byte budget" }
            ContextFiles.atomic(ContextFiles.safe(control, "changes.json"), text)
        }
        Files.delete(journal)
        deleteTree(stage)
    }

    private fun inScope(chain: List<RemoteNode>?): Boolean {
        if (chain.isNullOrEmpty()) return false
        if (chain.any { it.title.contains(Regex("OLD|Delete|삭제|\\(Locked\\)")) }) return false
        val first = chain.first().title
        if (first.startsWith("10.") || first.startsWith("20.") || first.startsWith("30.")) return true
        if (first != "60. Maintenance" || chain.size < 2) return false
        // Read legacy year folders too; exact standard matching is a write policy, not a read restriction.
        val years = Regex("(?<![0-9])[12][0-9]{3}(?![0-9])").findAll(chain[1].title).map { it.value.toInt() }.toList()
        val year = years.singleOrNull() ?: return false
        val current = LocalDate.now(ZoneOffset.UTC).year
        return year == current || year == current - 1
    }

    private fun segment(title: String): String {
        val normalized = Normalizer.normalize(title, Normalizer.Form.NFC)
        val name = normalized.replace(Regex("[\\x00-\\x1f<>:\"/\\\\|?*]"), "_").trimEnd(' ', '.')
        val safe = if (name.isBlank() || name == "." || name == "..") "_" else name
        require(safe.toByteArray(Charsets.UTF_8).size <= 200) { "Page title exceeds mirror filename budget" }
        return safe
    }

    private fun encode(n: RemoteNode, path: String, storage: String, bodyHash: String) = buildJsonObject {
        put("id", n.id); put("title", n.title); n.parentId?.let { put("parentId", it) }
        put("type", n.type); put("spaceId", n.spaceId); put("version", n.version); put("status", n.status)
        put("path", path); put("storage", storage); put("bodyHash", bodyHash)
    }
    private fun read(path: Path): JsonObject = readSnapshot(path)

    internal companion object {
        private fun decode(o: JsonObject) = RemoteNode(o.getValue("id").jsonPrimitive.content, o.getValue("title").jsonPrimitive.content,
            o["parentId"]?.jsonPrimitive?.contentOrNull, o.getValue("type").jsonPrimitive.content, o.getValue("spaceId").jsonPrimitive.content,
            o.getValue("version").jsonPrimitive.int, o.getValue("status").jsonPrimitive.content)
        internal fun readSnapshot(path: Path): JsonObject {
            val text = String(Files.readAllBytes(path), Charsets.UTF_8)
            return try {
                val value = Json.parseToJsonElement(text).jsonObject
                val nativeId = Regex("[1-9][0-9]*")
                val pages = value["pages"]?.jsonObject
                pages?.forEach { (id, item) ->
                    val record = item.jsonObject
                    val node = decode(record)
                    require(nativeId.matches(id) && node.id == id && nativeId.matches(node.spaceId) &&
                        (node.parentId == null || nativeId.matches(node.parentId)) && node.type == "page" && node.version > 0)
                    listOf("path", "storage").forEach { key -> record[key]?.jsonPrimitive?.content?.let {
                        val relative = Paths.get(it)
                        require(!relative.isAbsolute && relative.none { part -> part.toString() == ".." })
                    } }
                }
                listOf("explicitPages", "unavailable").forEach { key -> value[key]?.jsonArray?.forEach {
                    val id = it.jsonPrimitive.content
                    require(nativeId.matches(id))
                } }
                value["folderParents"]?.jsonObject?.let { folders ->
                    require(folders.size <= 20000)
                    val home = value["homePageId"]?.jsonPrimitive?.content
                    val knowledgeRoot = value["knowledgeRootPageId"]?.jsonPrimitive?.content
                    folders.forEach { (id, parent) ->
                        val parentId = parent.jsonPrimitive
                        require(id != home && id != knowledgeRoot && id !in pages.orEmpty() && nativeId.matches(id) &&
                            parentId.isString && parentId.content != id && nativeId.matches(parentId.content))
                    }
                }
                value["credentialBinding"]?.let { require(it is JsonPrimitive && it.isString && it.content.matches(Regex("[a-f0-9]{64}"))) }
                value["wholeSpace"]?.let {
                    require(it is JsonPrimitive && !it.isString && it.booleanOrNull != null)
                    if (it.boolean) require(value["homePageId"] == JsonPrimitive("") && value["knowledgeRootPageId"] == null)
                }
                value["inventoryComplete"]?.jsonPrimitive?.boolean
                value["cursor"]?.jsonPrimitive?.content?.let(Instant::parse)
                WikiChanges.validate(value)
                value
            } catch (_: RuntimeException) {
                throw ContextRemoteException("invalid_mirror_snapshot", "Synchronized mirror data is malformed; archive the disposable mirror and run sync")
            }
        }
    }

    private fun deleteTree(path: Path) {
        if (!Files.exists(path)) return
        Files.walk(path).use { stream -> stream.sorted(Comparator.reverseOrder()).forEach { Files.deleteIfExists(it) } }
    }
}
