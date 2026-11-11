package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.config.JiraConfig
import io.crscube.clitools.jirafetcher.util.ConfluenceMarkdownConverter
import io.crscube.clitools.mcp.McpParam
import io.crscube.clitools.mcp.McpTool
import io.crscube.clitools.knowledge.KnowledgeSettings
import io.ktor.client.HttpClient
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.*
import org.jsoup.Jsoup
import org.jsoup.nodes.CDataNode
import org.jsoup.nodes.Document
import org.jsoup.nodes.Entities
import org.jsoup.nodes.TextNode
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.parser.Parser
import org.jsoup.select.NodeVisitor
import java.net.URI
import java.nio.file.Files
import java.nio.file.LinkOption
import java.nio.file.attribute.BasicFileAttributes
import java.nio.file.Path
import java.nio.file.Paths
import java.util.Base64
import java.util.UUID

// Includes the bounded native continuation plus its public search/comment envelope.
private const val WIKI_CURSOR_LIMIT = 32768

/** Storage elements whose text is not ordinary visible page text, or duplicates it. */
private val unselectable = setOf("ac:structured-macro", "ac:macro", "ac:inline-comment-marker", "ac:plain-text-body", "ac:parameter", "ac:adf-extension",
    "ac:task-list", "ac:task", "ac:task-body", "ac:task-id", "ac:task-uuid", "ac:task-status", "ac:decision-list", "ac:decision", "ac:placeholder",
    "pre", "code", "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext")

internal fun nativeDocumentHeadings(body: String): List<Element> {
    val parser = Parser.xmlParser().setTrackPosition(true).setTrackErrors(20)
    val parsed = Jsoup.parse(body, "", parser)
    if (parser.errors.isNotEmpty()) throw ContextRemoteException("section_context_required", "Malformed storage cannot provide a reliable outline")
    return parsed.select("h1,h2,h3,h4,h5,h6").filter { heading ->
        heading.parents().none { it.normalName() in unselectable }
    }.onEach { heading ->
        val opening = heading.sourceRange()
        val closing = heading.endSourceRange()
        if (!opening.isTracked || opening.isImplicit || !closing.isTracked || closing.isImplicit)
            throw ContextRemoteException("section_context_required", "A heading has no exact native source range")
    }
}

/** Typed MCP methods share this application boundary and its policies. */
class WikiService(private val remote: ConfluenceRemote, private val origin: String,
                  private val launchRoot: Path, private val runtimeSettings: KnowledgeSettings? = null) {
    var localReads: ((String, JsonObject) -> JsonObject?)? = null
    var replicaRepair: ((String) -> Unit)? = null
    private val policy = ContextPolicy(remote)
    private fun JsonObject.text(key: String): String = getValue(key).jsonPrimitive.also { require(it.isString) }.content
    private fun JsonObject.optional(key: String): String? = get(key)?.takeUnless { it is JsonNull }?.jsonPrimitive?.also { require(it.isString) }?.content
    private fun JsonObject.number(key: String, default: Int): Int = get(key)?.jsonPrimitive?.also { require(!it.isString) }?.int ?: default
    private fun JsonObject.flag(key: String, default: Boolean = false): Boolean = get(key)?.jsonPrimitive?.also {
        require(!it.isString && it.booleanOrNull != null) { "$key must be a JSON boolean" }
    }?.boolean ?: default
    private fun storage(text: String, format: String): String {
        require(format in setOf("storage", "text")) { "Use storage or text for new content" }
        if (format == "storage") return text
        require(text.isNotBlank()) { "New text must not be blank" }
        return text.replace("\r\n", "\n").replace('\r', '\n').split("\n\n").joinToString("") { paragraph ->
            "<p>" + paragraph.split('\n').joinToString("<br />") { Element("span").text(it).html() } + "</p>"
        }
    }
    private fun inlineText(text: String): String = text.replace("\r\n", "\n").replace('\r', '\n').split('\n').joinToString("<br />") {
        Entities.escape(it, Document.OutputSettings().prettyPrint(false).charset(Charsets.UTF_8))
    }
    private fun recordingAccount(account: JsonObject): String {
        val id = account.text("accountId").replace("&", "&amp;").replace("\"", "&quot;")
            .replace("'", "&apos;").replace("<", "&lt;").replace(">", "&gt;")
        return "<p><ac:link><ri:user ri:account-id=\"$id\" /></ac:link></p>"
    }
    private data class Environment(val root: Path, val connections: JsonObject, val installation: String)
    private suspend fun publishReplica(env: Environment, config: ContextConfig, page: RemotePage, acknowledgement: JsonObject,
                               observedBefore: RemotePage? = null): JsonObject = try {
        unchanged(env)
        ContextMirror(env.root, remote).publishVerified(config, page, observedBefore) { unchanged(env) }
        acknowledgement
    } catch (e: CancellationException) { throw e }
    catch (_: Exception) {
        // Only a confirmed remote write reaches publication. Notify the existing synchronizer;
        // never retry the mutation, including when this transient notification cannot be delivered.
        runCatching { replicaRepair?.invoke(page.node.id) }
        JsonObject(acknowledgement + buildJsonObject {
            put("syncRequired", true)
            put("next", "Saved remotely. Automatic synchronization will repair the local page; do not repeat the write.")
        })
    }
    private fun environment(): Environment {
        val settings = runtimeSettings ?: error("Resolved Atlassian runtime settings are required")
        require(settings.corpusRoot == launchRoot.toAbsolutePath().normalize()) { "Connection corpus changed; reconnect MCP" }
        return Environment(settings.corpusRoot, settings.connections(), settings.identity())
    }
    private fun unchanged(before: Environment) { require(environment() == before) { "Installation or connection policy changed; read the target again" } }
    private fun configs(env: Environment): List<ContextConfig> = listOf("project", "common").mapNotNull { ContextConfig.load(env.root, runtimeSettings, it) }
        .distinct().also { require(it.isNotEmpty()) { "Configure a registered Confluence connection" } }
    private val activeConfig by lazy { ContextConfig.load(launchRoot, runtimeSettings) }
    private fun rootConfig(id: String): ContextConfig? = activeConfig?.takeIf {
        it.isKnowledge && (id == it.homePageId || id == it.knowledgeRootPageId)
    }
    private suspend fun nativeNode(id: String): RemoteNode {
        val root = rootConfig(id)
        suspend fun read(type: String): RemoteNode = if (root == null) remote.scopeNode(id, type)
            else remote.connectionScopeNode(root, id, type)
        return try { read("page") } catch (e: ContextRemoteException) {
            if (e.code != "http_404") throw e
            read("folder")
        }
    }
    private suspend fun nativePage(node: RemoteNode): RemotePage = remote.wikiPage(node, rootConfig(node.id))
    /** Observations live for one read only; final authorization never uses cached observations. */
    private inner class ReadGraph {
        val nodes = linkedMapOf<String, RemoteNode>()
        val finalChecks = mutableListOf<() -> Unit>()
        fun seed(node: RemoteNode) {
            require(nodes.putIfAbsent(node.id, node)?.let { it == node } != false) { "Document changed during read" }
        }
        suspend fun node(id: String): RemoteNode = nodes[id] ?: nativeNode(id).also(::seed)
        suspend fun verify() {
            // Search may seed several leaves; validate every consumed descendant before its ancestors.
            fun depth(node: RemoteNode): Int {
                var parent = node.parentId
                var count = 0
                val seen = mutableSetOf(node.id)
                while (parent != null && seen.add(parent)) { count++; parent = nodes[parent]?.parentId }
                return count
            }
            nodes.values.sortedByDescending(::depth).forEach {
                require(nativeNode(it.id) == it) { "Document ancestry changed during read" }
            }
            finalChecks.forEach { it() }
        }
    }
    private suspend fun chain(pageId: String, graph: ReadGraph? = null): List<RemoteNode> {
        val result = mutableListOf<RemoteNode>()
        var current: String? = pageId
        while (current != null) {
            require(result.none { it.id == current } && result.size < 100) { "Cyclic or excessive document ancestry" }
            val node = graph?.node(current) ?: nativeNode(current)
            result += node; current = node.parentId
        }
        return result
    }
    /** Specific registered namespaces govern even when the caller arrived through a broader common root. */
    private suspend fun admitted(env: Environment, pageId: String, graph: ReadGraph? = null): Pair<ContextConfig, RemoteNode> {
        val leaf = graph?.node(pageId) ?: nativeNode(pageId)
        configs(env).singleOrNull { it.wholeSpace && it.site.trimEnd('/') == origin && it.spaceId == leaf.spaceId }?.let { selected ->
            require(leaf.status == "current") { "Current content in the registered space is required" }
            return selected to leaf
        }
        val path = chain(pageId, graph)
        fun boundary(config: ContextConfig): String = when {
            config.isKnowledge -> config.knowledgeRootPageId ?: config.homePageId
            config.structurePolicy == "pilot" -> config.pilotRootPageId!!
            else -> config.homePageId
        }
        val candidates = configs(env).filter { config ->
            config.site.trimEnd('/') == origin && path.first().spaceId == config.spaceId &&
                path.any { it.id == config.homePageId } && path.any { it.id == boundary(config) }
        }
        val selected = candidates.minByOrNull { config -> path.indexOfFirst { it.id == boundary(config) } }
            ?: error("Document is outside registered Confluence roots")
        require(path.all { it.spaceId == selected.spaceId && it.status == "current" }) { "Current ancestry in the registered space is required" }
        return selected to path.first()
    }

    private data class Relocation(val config: ContextConfig, val source: List<RemoteNode>, val destination: List<RemoteNode>)
    private suspend fun relocation(env: Environment, config: ContextConfig, node: RemoteNode, parent: String): Relocation {
        require(config.isKnowledge && node.type == "page" && node.id !in setOf(config.homePageId, config.knowledgeRootPageId)) {
            "Only a knowledge page below its registered ROOT may be moved"
        }
        val id = document(parent)
        checkDocumentReference(parent, config)
        val graph = ReadGraph()
        val source = chain(node.id, graph)
        require(source.first() == node && admitted(env, node.id, graph).first == config) { "Moving document changed" }
        val (governing, destination) = admitted(env, id, graph)
        require(governing == config && destination.spaceId == node.spaceId && destination.type in setOf("page", "folder")) {
            "Move destination must remain in the same registered knowledge namespace and space"
        }
        val path = chain(id, graph)
        require(path.none { it.id == node.id }) { "A page cannot move into itself or its descendants" }
        require(path.map { it.id }.containsAll(source.drop(1).map { it.id })) {
            "Move must retain every existing ancestor to preserve inherited access restrictions"
        }
        graph.verify()
        unchanged(env)
        return Relocation(config, source, path)
    }
    private suspend fun checkRelocation(env: Environment, move: Relocation, saved: RemoteNode? = null) {
        val source = saved?.let { listOf(it) + move.destination } ?: move.source
        val graph = ReadGraph()
        require(chain(source.first().id, graph) == source && chain(move.destination.first().id, graph) == move.destination) {
            "Move source or destination ancestry changed"
        }
        require(admitted(env, source.first().id, graph) == (move.config to source.first()) &&
            admitted(env, move.destination.first().id, graph) == (move.config to move.destination.first())) {
            "Move source or destination namespace changed"
        }
        graph.verify()
        unchanged(env)
    }

    private suspend fun appendDestination(env: Environment, purpose: String): String {
        unchanged(env)
        fun selection(): Nothing = throw ContextRemoteException("selection_required", "A unique current recording page could not be selected",
            next = "Select an existing page explicitly; no page was created and no write was performed.")
        val common = ContextConfig.load(env.root, runtimeSettings, "common") ?: selection()
        require(common.isKnowledge && common.site.trimEnd('/') == origin) { "Common knowledge connection at the authenticated origin required" }
        val root = common.knowledgeRootPageId ?: common.homePageId
        suspend fun child(parent: String, titles: Set<String>): JsonObject {
            val batch = try { withTimeoutOrNull(30_000) { remote.feedbackCandidates(common, parent, titles) } ?: selection() }
                catch (e: ContextRemoteException) { if (e !is RootScopeDenial && e.code == "http_404") selection() else throw e }
                catch (_: IllegalArgumentException) { selection() }
            val results = batch.getValue("results").jsonArray
            if (batch["complete"] != JsonPrimitive(true) || results.size != 1) selection()
            return results.single().jsonObject
        }
        val binding = env.connections.getValue("common").jsonObject.getValue("confluence").jsonObject
        val id = binding.optional(if (purpose == "memory") "memoryPageId" else if (purpose == "feedback") "maintenancePageId" else error("Unknown recording destination")) ?: selection()
        val (governing, node) = admitted(env, id)
        require(node.type == "page" && node.status == "current" && governing == common) { "Recording destination is outside the configured root" }
        unchanged(env)
        return id
    }

    /** Reuse ordinary Wiki namespace selection for one explicit feedback workspace. */
    internal fun knowledgeAdmission(): suspend (String) -> Unit {
        val env = environment()
        return { pageId ->
            unchanged(env)
            require(admitted(env, pageId).first.isKnowledge) { "Feedback target is governed by a regulated project namespace" }
            unchanged(env)
        }
    }
    private fun numeric(value: String): String = value.also { require(it.matches(Regex("[1-9][0-9]*"))) { "Exact positive native ID required" } }
    private fun uri(value: String): URI {
        val result = URI(value)
        val expected = URI(origin)
        require(result.scheme == expected.scheme && result.host == expected.host && result.port == expected.port &&
            result.rawUserInfo == null && result.normalize().rawPath == result.rawPath && '%' !in result.rawPath) { "Resource URL must use the configured origin" }
        return result
    }
    private fun document(value: String): String {
        if (value.matches(Regex("[1-9][0-9]*"))) return value
        return numeric(nativePageUrlId(value, origin, allowApi = true))
    }
    /** Fragment removal belongs only to review navigation; ordinary document inputs stay strict. */
    private fun reviewDocumentLink(value: String): String {
        val parsed = URI(value)
        val expected = URI(origin)
        require(parsed.scheme == expected.scheme && parsed.host == expected.host && parsed.port == expected.port && parsed.rawUserInfo == null) {
            "Review link must use the configured origin"
        }
        return document(if (parsed.rawFragment == null) value else value.substringBefore('#'))
    }
    private fun checkDocumentReference(value: String, config: ContextConfig) {
        if (value.startsWith("https://")) nativePageUrlId(value, origin, config.space, allowApi = true)
    }
    private data class CommentRef(val kind: String, val id: String, val issue: String? = null)
    private fun comment(value: String): CommentRef {
        Regex("^(footer|inline):([1-9][0-9]*)$").matchEntire(value)?.let { return CommentRef(it.groupValues[1], it.groupValues[2]) }
        if (value.matches(Regex("[1-9][0-9]*"))) return CommentRef("auto", value)
        val parsed = uri(value)
        val api = Regex("^/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]*)$").matchEntire(parsed.rawPath)
        if (api != null && parsed.rawQuery == null && parsed.rawFragment == null) return CommentRef(api.groupValues[1], api.groupValues[2])
        val jira = Regex("^/rest/api/3/issue/([A-Z][A-Z0-9_]*-[1-9][0-9]*)/comment/([1-9][0-9]*)$").matchEntire(parsed.rawPath)
        if (jira != null && parsed.rawQuery == null && parsed.rawFragment == null) return CommentRef("jira", jira.groupValues[2], jira.groupValues[1])
        error("Use a native comment ID, footer:ID, inline:ID or exact comment API URL")
    }
    private suspend fun resolveComment(value: String): CommentRef {
        val selected = comment(value)
        if (selected.kind != "auto") return selected
        try {
            remote.wikiCommentLocation(selected.id, "footer")
            return selected.copy(kind = "footer")
        } catch (e: ContextRemoteException) { if (e.code != "http_404") throw e }
        remote.wikiCommentLocation(selected.id, "inline")
        return selected.copy(kind = "inline")
    }
    private fun issue(value: String): String? {
        if (value.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*"))) return value
        if (!value.startsWith("https://") || !URI(value).rawPath.startsWith("/browse/")) return null
        val parsed = uri(value)
        return if (parsed.rawQuery == null && parsed.rawFragment == null) Regex("^/browse/([A-Z][A-Z0-9_]*-[1-9][0-9]*)$").matchEntire(parsed.rawPath)?.groupValues?.get(1) else null
    }
    private fun admitIssue(env: Environment, key: String) {
        val project = key.substringBeforeLast('-')
        val bindings = listOf("project", "common").mapNotNull { env.connections[it]?.jsonObject?.get("jira") as? JsonObject }
        require(bindings.any { binding ->
            val site = binding["site"]?.jsonPrimitive?.content ?: origin
            val projects = listOfNotNull(binding["project"]?.jsonPrimitive?.content, binding["pmProject"]?.jsonPrimitive?.content) +
                (binding["devProjects"] as? JsonArray)?.map { it.jsonPrimitive.content }.orEmpty()
            site.trimEnd('/') == origin && project in projects
        }) { "Jira issue project is not explicitly registered on this origin" }
    }
    private fun reference(item: JsonObject, target: String, format: String = "storage", metadata: Boolean = false,
                          includeBody: Boolean = true, pageUrl: String? = null): JsonObject {
        val kind = item.getValue("kind").jsonPrimitive.content
        val id = item.getValue("id").jsonPrimitive.content
        val result = item.filterKeys { metadata || it in setOf("id", "body", "format", "version") }.toMutableMap()
        result.remove("inlineContext")
        (item["inlineContext"] as? JsonObject)?.let { context ->
            val visible = context.filterKeys {
                when (it) {
                    "resolutionStatus" -> true
                    "inlineOriginalSelection" -> includeBody
                    else -> metadata
                }
            }
            if (visible.isNotEmpty()) result["inlineContext"] = JsonObject(visible)
        }
        result["reference"] = JsonPrimitive(if (kind == "jira") "$origin/rest/api/3/issue/$target/comment/$id" else "$kind:$id")
        if (metadata) result["target"] = JsonPrimitive(target)
        if (!includeBody) { result.remove("body"); result.remove("format") }
        else if (kind != "jira" && format == "markdown") {
            result["body"] = JsonPrimitive(ConfluenceMarkdownConverter.htmlToMarkdown(item.text("body"), currentPageUrl = pageUrl))
            result["format"] = JsonPrimitive("markdown")
        }
        return JsonObject(result)
    }
    private suspend fun documentResult(env: Environment, config: ContextConfig, page: RemotePage, status: String, offset: Int = 0, length: Int = 4000,
                                       expectedCurrent: RemoteNode = page.node, account: JsonObject? = null, format: String = "storage",
                                       metadata: Boolean = false, includeBody: Boolean = true, graph: ReadGraph? = null, section: String? = null): JsonObject {
        require(offset >= 0 && length in 1..12000 && page.body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "Bounded document read required" }
        require(format in setOf("markdown", "storage", "outline") && !(section != null && format == "outline")) { "Use markdown/storage for a section or outline for the document" }
        val version = remote.wikiVersion(page.node.id, page.node.version)
        if ((version.optional("message") ?: "") != page.versionMessage) throw ContextRemoteException("readback_mismatch",
            "Native version message differs from the document readback", details = buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("message")))) })
        if (account != null && version["authorId"] != account["accountId"]) throw ContextRemoteException("readback_mismatch",
            "Saved native version author differs from the authenticated account", details = buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("authorId")))) })
        val selectedBody = if (section == null) page.body else sectionRange(page.body, section).let { page.body.substring(it.first, it.second) }
        val representation = when {
            includeBody && format == "outline" -> documentSections(page.body).joinToString("\n") { (key, heading) ->
                "${"  ".repeat(key.count { it == '.' })}$key ${heading.text().replace(Regex("\\s+"), " ")}" }
            includeBody && format == "markdown" -> ConfluenceMarkdownConverter.htmlToMarkdown(selectedBody,
                currentPageUrl = "$origin/wiki/spaces/${config.space}/pages/${page.node.id}")
            else -> selectedBody
        }
        val total = representation.codePointCount(0, representation.length)
        require(offset <= total) { "Offset exceeds document length" }
        val end = offset + minOf(length, total - offset)
        val content = representation.substring(representation.offsetByCodePoints(0, offset), representation.offsetByCodePoints(0, end))
        require(admitted(env, page.node.id, graph) == (config to expectedCurrent)) { "Current namespace changed before the document could be returned" }
        if (account != null) {
            policy.checkKnowledge(config, page.node, page.node.title, page.body, page)
            require(remote.currentUser() == account) { "Authenticated account changed before readback completed" }
        }
        unchanged(env)
        return buildJsonObject {
            if (metadata || status != "read") put("status", status)
            put("id", page.node.id); put("title", page.node.title); put("version", page.node.version)
            section?.let { put("section", it) }
            if (includeBody) {
                put("format", format); put("content", content); put("truncated", end < total)
                if (end < total) put("nextOffset", end)
            }
            if (metadata) {
                put("url", "$origin/wiki/spaces/${config.space}/pages/${page.node.id}")
                put("pageId", page.node.id); page.node.parentId?.let { put("parentId", it) }; put("spaceId", page.node.spaceId)
                put("authorId", version.getValue("authorId")); put("createdAt", version.getValue("createdAt")); put("message", page.versionMessage)
                put("bodyHash", ContextFiles.hash(page.body))
                if (account != null) put("verified", true)
                if (includeBody) { put("offset", offset); put("nextOffset", end); put("totalLength", total); put("offsetUnit", "unicode_code_points") }
            }
        }
    }
    /** Positions belong to this native body only; no persistent IDs or Markdown-derived boundaries. */
    private fun documentSections(body: String): List<Pair<String, Element>> {
        val ancestors = mutableListOf<Pair<Int, String>>()
        val counts = mutableMapOf<String, Int>()
        return nativeDocumentHeadings(body).map { heading ->
            val level = heading.normalName().drop(1).toInt()
            while (ancestors.lastOrNull()?.first?.let { it >= level } == true) ancestors.removeAt(ancestors.lastIndex)
            val parent = ancestors.lastOrNull()?.second.orEmpty()
            val next = (counts[parent] ?: 0) + 1
            counts[parent] = next
            val key = if (parent.isEmpty()) "$next" else "$parent.$next"
            ancestors += level to key
            key to heading
        }
    }
    private fun sectionRange(body: String, key: String): Pair<Int, Int> {
        if (key.length > 80 || !key.matches(Regex("[1-9][0-9]*(\\.[1-9][0-9]*)*")))
            throw ContextRemoteException("invalid_section", "Use an exact section position returned by format:outline")
        val headings = documentSections(body)
        val heading = headings.singleOrNull { it.first == key }?.second
            ?: throw ContextRemoteException("section_not_found", "Section position is absent in this document version",
                next = "Read format:outline for the intended version; documents without headings use ordinary content reads.")
        val parent = heading.parent()!!
        val level = heading.normalName().drop(1).toInt()
        val next = heading.nextElementSiblings().firstOrNull {
            it.normalName().matches(Regex("h[1-6]")) && it.normalName().drop(1).toInt() <= level }
        val from = heading.endSourceRange().end().pos()
        val until = next?.sourceRange()?.start()?.pos() ?: if (parent is Document) body.length else {
            val closing = parent.endSourceRange()
            if (!closing.isTracked || closing.isImplicit) throw ContextRemoteException("section_context_required", "Section container has no exact closing boundary")
            closing.start().pos()
        }
        val globalNext = headings.firstOrNull { (_, other) -> other.sourceRange().start().pos() >= from &&
            other.normalName().drop(1).toInt() <= level }?.second
        val crossing = next !== globalNext || parent !is Document && next == null &&
            body.substring(parent.endSourceRange().end().pos()).isNotBlank()
        if (from !in 0..until || until > body.length || crossing)
            throw ContextRemoteException("section_context_required", "Section crosses native containers; use ordinary reads and contextual edits")
        parent.root().allElements.filter { it.sourceRange().isTracked && it.sourceRange().start().pos() in from until until }.forEach {
            val opening = it.sourceRange(); val closing = it.endSourceRange()
            val selfClosing = body.substring(opening.start().pos(), opening.end().pos()).trimEnd().endsWith("/>")
            if (opening.isImplicit || opening.end().pos() > until || !selfClosing && (!closing.isTracked || closing.isImplicit || closing.end().pos() > until))
                throw ContextRemoteException("section_context_required", "Section contains incomplete native elements; use a complete contextual edit")
        }
        return from to until
    }
    private fun sectionBody(original: String, key: String, replacement: String): String {
        val (from, until) = sectionRange(original, key)
        val previous = original.substring(from, until)
        fun retained(body: String): Map<String, Int> {
            val parser = Parser.xmlParser().setTrackPosition(true).setTrackErrors(20)
            val parsed = Jsoup.parse(body, "", parser)
            if (parser.errors.isNotEmpty()) throw ContextRemoteException("section_context_required", "Replacement must be a complete native section body")
            parsed.allElements.filter { it !== parsed }.forEach { element ->
                val opening = element.sourceRange(); val closing = element.endSourceRange()
                val selfClosing = opening.isTracked && body.substring(opening.start().pos(), opening.end().pos()).trimEnd().endsWith("/>")
                if (!opening.isTracked || opening.isImplicit || !selfClosing && (!closing.isTracked || closing.isImplicit))
                    throw ContextRemoteException("section_context_required", "Section body must contain complete native elements")
            }
            // XML parsing silently drops stray closing tags; they must never escape the selected container.
            val ranges = mutableListOf<Pair<Int, Int>>()
            parsed.traverse(object : NodeVisitor {
                override fun head(node: Node, depth: Int) {
                    val opening = node.sourceRange()
                    if (opening.isTracked) ranges += opening.start().pos() to opening.end().pos()
                    if (node is Element) {
                        val closing = node.endSourceRange()
                        if (closing.isTracked) ranges += closing.start().pos() to closing.end().pos()
                    }
                }
                override fun tail(node: Node, depth: Int) = Unit
            })
            var covered = 0
            ranges.sortedBy { it.first }.forEach { (from, until) ->
                if (from > covered && body.substring(covered, from).isNotBlank())
                    throw ContextRemoteException("section_context_required", "Section contains discarded native markup")
                covered = maxOf(covered, until)
            }
            if (body.substring(covered).isNotBlank())
                throw ContextRemoteException("section_context_required", "Section contains discarded native markup")
            return parsed.allElements.filter { element -> element.parents().none { it.normalName() in literalLinkParents ||
                it.normalName() in setOf("ac:structured-macro", "ac:macro", "ac:adf-extension", "ac:placeholder") } &&
                (element.normalName() in setOf("ac:image", "ri:attachment", "ac:structured-macro", "ac:macro", "ac:adf-extension", "ac:placeholder", "ac:inline-comment-marker") ||
                    listOf("id", "local-id", "ac:local-id").any { element.hasAttr(it) } || element.normalName() == "a" && element.hasAttr("name"))
            }.groupingBy { it.outerHtml() }.eachCount()
        }
        val fresh = retained(replacement)
        if (retained(previous).any { (fragment, count) -> (fresh[fragment] ?: 0) < count })
            throw ContextRemoteException("section_reference_removal", "Section replacement would change or remove an image, attachment, macro, anchor or inline comment",
                next = "Read this section as storage and preserve its native references, or use an explicit contextual edit.")
        return original.replaceRange(from, until, replacement)
    }
    /** Match source positions only: parsing must not repair or reserialize the writable range. */
    private fun patchBlocks(parsed: Element): List<Element> {
        val blocks = setOf("p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "li", "ul", "ol", "blockquote", "pre", "div", "table")
        return parsed.allElements.filter { element ->
            val opening = element.sourceRange()
            val closing = element.endSourceRange()
            element.normalName() in blocks && opening.isTracked && !opening.isImplicit && closing.isTracked && !closing.isImplicit &&
                element.parents().none { it.normalName() in setOf("ac:plain-text-body", "ac:parameter", "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext") }
        }
    }
    private fun completePatchContext(blocks: List<Element>, from: Int, until: Int): Boolean = blocks.any { element ->
        val opening = element.sourceRange()
        val closing = element.endSourceRange()
        (opening.start().pos() == from && closing.end().pos() == until) ||
            (element.childNodes().isNotEmpty() && element.childNodes().all { it is TextNode } &&
                opening.end().pos() == from && closing.start().pos() == until)
    }
    private fun plainBlocks(blocks: List<Element>): List<Pair<Element, String>> = blocks.filter { element ->
        element.childNodes().isNotEmpty() && element.childNodes().all {
            it is TextNode && it !is CDataNode || it is Element && it.normalName() == "br" && it.childNodeSize() == 0
        }
    }.map { element ->
        element to element.childNodes().joinToString("") { if (it is TextNode) it.wholeText else "\n" }
    }
    private fun occurrences(text: String, selection: String, overlapping: Boolean): Int {
        var count = 0
        var from = text.indexOf(selection)
        while (from >= 0) { count++; from = text.indexOf(selection, from + if (overlapping) 1 else selection.length) }
        return count
    }
    /** Matches wholly inside one ordinary text node, in document order, optionally only those before [stopAt]. */
    private fun selectableMatches(root: Node, selection: String, stopAt: Node? = null): Int {
        var excluded = 0
        var count = 0
        var stopped = false
        root.traverse(object : NodeVisitor {
            override fun head(node: Node, depth: Int) {
                if (stopped) return
                if (node === stopAt) { stopped = true; return }
                if (node.nodeName() in unselectable) excluded++
                else if (excluded == 0 && node is TextNode && node !is CDataNode) count += occurrences(node.wholeText, selection, false)
            }
            override fun tail(node: Node, depth: Int) { if (!stopped && node.nodeName() in unselectable) excluded-- }
        })
        return count
    }
    private fun checkSelection(selection: String) {
        if (selection.isBlank() || selection.length > 1000 || selection != selection.trim() ||
            selection.any { it.isISOControl() || it == '\u2028' || it == '\u2029' })
            throw ContextRemoteException("invalid_selection", "selection must be 1..1000 UTF-16 units of exact single-line page text without surrounding whitespace")
    }
    /** Zero-based match index and match count. The provider's matching is undocumented, so every ambiguity is rejected before a write. */
    private fun selectionAnchor(storage: String, selection: String, occurrence: Int?): Pair<Int, Int> {
        val parsed = Jsoup.parse(storage, "", Parser.xmlParser())
        val count = selectableMatches(parsed, selection)
        val details = buildJsonObject { put("matchCount", count) }
        if (count != occurrences(parsed.wholeText(), selection, true)) throw ContextRemoteException("selection_context_required",
            "selection crosses formatting, overlaps itself, or appears inside a macro, task or decision item, literal body or existing inline comment", details = details,
            next = "Choose exact text that lies inside one plain text run and occurs only in ordinary page text. No write was performed.")
        if (count == 0) throw ContextRemoteException("selection_not_found", "selection does not occur in the current page text", details = details,
            next = "Read the current document and copy the exact visible text. No write was performed.")
        if (occurrence == null && count > 1) throw ContextRemoteException("selection_ambiguous", "selection occurs more than once; pass occurrence", details = details,
            next = "Pass occurrence 1..$count or a longer unique selection. No write was performed.")
        if (occurrence != null && occurrence !in 1..count) throw ContextRemoteException("selection_occurrence_out_of_range", "occurrence must be 1..$count", details = details)
        return (occurrence ?: 1) - 1 to count
    }
    /** The saved marker must wrap exactly the requested occurrence in the page as it now reads. */
    private fun selectionAnchored(storage: String, selection: String, index: Int, markerRef: String?): Boolean {
        val parsed = Jsoup.parse(storage, "", Parser.xmlParser())
        val marker = parsed.getElementsByTag("ac:inline-comment-marker").singleOrNull { it.attr("ac:ref") == markerRef } ?: return false
        return markerRef != null && marker.parents().none { it.nodeName() in unselectable } &&
            marker.wholeText() == selection && selectableMatches(parsed, selection, marker) == index
    }
    private data class Patch(val from: Int, val until: Int, val replacement: String)
    private data class LinkTarget(val id: String, val config: ContextConfig)
    private data class LinkEdit(val body: String, val targets: List<LinkTarget>)
    private val literalLinkParents = setOf("code", "pre", "ac:plain-text-body", "ac:parameter", "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext")

    private fun quotedLinkTag(current: String, element: Element): Boolean {
        val range = element.sourceRange()
        if (!range.isTracked || range.isImplicit) return false
        val tag = current.substring(range.start().pos(), range.end().pos())
        if (tag.length < 3 || !tag.startsWith("<a") || tag[2] !in " \t\r\n>/") return false
        val attribute = Regex("([A-Za-z_:][A-Za-z0-9_.:-]*)[ \\t\\r\\n]*=[ \\t\\r\\n]*([\"'])(.*?)\\2", RegexOption.DOT_MATCHES_ALL)
        val names = mutableSetOf<String>()
        var at = 2
        while (at < tag.length) {
            while (at < tag.length && tag[at] in " \t\r\n") at++
            if (tag.substring(at) in setOf(">", "/>")) return "href" in names
            val match = attribute.matchAt(tag, at) ?: return false
            if ('<' in match.groupValues[3]) return false
            if (!names.add(match.groupValues[1].lowercase())) return false
            at = match.range.last + 1
            if (at < tag.length && tag[at] !in " \t\r\n>/") return false
        }
        return false
    }

    /** Edit only tracked href value bytes; examples, labels and all other storage stay unchanged. */
    private suspend fun linkBody(env: Environment, current: String, links: JsonObject): LinkEdit {
        if (links.size !in 1..20 || links.keys.any { it.isBlank() || it.length > 4096 || it.any(Char::isISOControl) } ||
            links.values.any { (it as? JsonPrimitive)?.isString != true })
            throw ContextRemoteException("invalid_links", "links must contain 1..20 exact existing href to document-reference strings")
        val parser = Parser.xmlParser().setTrackPosition(true).setTrackErrors(20)
        val parsed = Jsoup.parse(current, "", parser)
        if (parser.errors.isNotEmpty()) throw ContextRemoteException("link_context_required", "Malformed source attributes cannot be edited safely")
        val found = links.keys.associateWith { mutableListOf<Pair<Int, Int>>() }
        parsed.getElementsByTag("a").filter { it.parents().none { parent -> parent.normalName() in literalLinkParents } }.forEach { element ->
            val selected = found[element.attr("href")] ?: return@forEach
            if (!quotedLinkTag(current, element)) throw ContextRemoteException("link_context_required", "A well-formed anchor with unique quoted attributes is required")
            val range = element.attributes().sourceRange("href").valueRange()
            val from = range.start().pos()
            val until = range.end().pos()
            if (!range.isTracked || from <= 0 || until < from || until >= current.length ||
                current[from - 1] !in setOf('\'', '"') || current[until] != current[from - 1] ||
                Parser.unescapeEntities(current.substring(from, until), true) != element.attr("href"))
                throw ContextRemoteException("link_context_required", "A uniquely tracked quoted href value is required")
            selected += from to until
        }
        if (found.values.any { it.isEmpty() }) throw ContextRemoteException("link_not_found", "Every links key must match an existing document link",
            details = buildJsonObject { put("missingCount", found.values.count { it.isEmpty() }) },
            next = "Use the exact decoded href from the current document. Code examples and image sources are not document links.")
        val graph = ReadGraph()
        val targets = mutableListOf<LinkTarget>()
        val patches = links.flatMap { (old, value) ->
            val reference = value.jsonPrimitive.content
            val id: String
            val fragment: String?
            try {
                val parsedReference = URI(reference)
                fragment = parsedReference.rawFragment
                require(fragment == null || fragment.length <= 3000 && fragment.none(Char::isISOControl))
                id = document(if (fragment == null) reference else reference.substringBefore('#'))
            } catch (_: Exception) { throw ContextRemoteException("invalid_link_target", "Use an exact native document ID or same-origin page URL with an optional fragment") }
            val (config, node) = admitted(env, id, graph)
            checkDocumentReference(reference.substringBefore('#'), config)
            require(node.type == "page") { "A document link target must be a current native page" }
            targets += LinkTarget(id, config)
            val href = "$origin/wiki/spaces/${config.space}/pages/$id" + fragment?.let { "#$it" }.orEmpty()
            val escaped = href.replace("&", "&amp;").replace("\"", "&quot;").replace("'", "&apos;").replace("<", "&lt;").replace(">", "&gt;")
            found.getValue(old).map { (from, until) -> Patch(from, until, escaped) }
        }.sortedBy { it.from }
        if (patches.zipWithNext().any { (left, right) -> left.until > right.from })
            throw ContextRemoteException("link_context_required", "Link attribute ranges must not overlap")
        graph.verify()
        unchanged(env)
        return LinkEdit(patches.asReversed().fold(current) { body, patch -> body.replaceRange(patch.from, patch.until, patch.replacement) }, targets.distinct())
    }

    private suspend fun checkLinkTargets(env: Environment, targets: List<LinkTarget>) {
        unchanged(env)
        val graph = ReadGraph()
        targets.forEach { target ->
            val (config, node) = admitted(env, target.id, graph)
            require(config == target.config && node.type == "page") { "A link target left its registered namespace" }
        }
        graph.verify()
        unchanged(env)
    }
    private fun patchBody(current: String, edits: JsonArray, format: String): String {
        if (edits.size !in 1..20) throw ContextRemoteException("invalid_edit", "edits must contain 1..20 old_text/new_text objects")
        val blocks = patchBlocks(Jsoup.parse(current, "", Parser.xmlParser().setTrackPosition(true)))
        val textBlocks = if (format == "text") plainBlocks(blocks) else emptyList()
        val patches = edits.mapIndexed { index, entry ->
            val item = entry as? JsonObject
            if (item == null || item.keys != setOf("old_text", "new_text") || item.values.any { (it as? JsonPrimitive)?.isString != true })
                throw ContextRemoteException("invalid_edit", "Each edit must contain exactly old_text and new_text strings",
                    details = buildJsonObject { put("editIndex", index) })
            val old = item.text("old_text")
            if (old.isEmpty()) throw ContextRemoteException("invalid_edit", "old_text must not be empty",
                details = buildJsonObject { put("editIndex", index) })
            if (format == "text") {
                val matches = textBlocks.filter { it.second == old }
                if (matches.size != 1) throw ContextRemoteException(if (matches.isEmpty()) "patch_not_found" else "patch_ambiguous",
                    "old_text must match exactly one entire simple block text",
                    details = buildJsonObject { put("matchCount", matches.size); put("editIndex", index) },
                    next = "Use the exact full text of a simple paragraph or cell; use format:storage for rich markup or structural edits.")
                val element = matches.single().first
                Patch(element.sourceRange().end().pos(), element.endSourceRange().start().pos(), inlineText(item.text("new_text")))
            } else {
                var matches = 0
                var at = current.indexOf(old)
                val first = at
                while (at >= 0) {
                    matches++
                    at = current.indexOf(old, at + 1)
                }
                if (matches != 1) throw ContextRemoteException(if (matches == 0) "patch_not_found" else "patch_ambiguous",
                    "old_text must match exactly once in the current storage content",
                    details = buildJsonObject { put("matchCount", matches); put("editIndex", index) },
                    next = "Read the current document and choose a unique exact old_text.")
                if (!completePatchContext(blocks, first, first + old.length)) throw ContextRemoteException("patch_context_required",
                    "old_text must select a complete paragraph, table cell or block, or its entire plain text",
                    details = buildJsonObject { put("editIndex", index) },
                    next = "Read get_document with format:storage and select the complete paragraph or table cell; do not select a word fragment or attribute.")
                Patch(first, first + old.length, item.text("new_text"))
            }
        }.sortedBy { it.from }
        if (patches.zipWithNext().any { (left, right) -> left.until > right.from })
            throw ContextRemoteException("patch_overlap", "Edit ranges must not overlap",
                next = "Choose disjoint complete blocks from the same original document.")
        return patches.asReversed().fold(current) { body, patch -> body.replaceRange(patch.from, patch.until, patch.replacement) }
    }
    private fun documentReferences(body: String): Map<Pair<String, List<String>>, Int> {
        val parsed = Jsoup.parse(body, "", Parser.xmlParser())
        fun attachment(element: Element): List<String> = listOf("attachment", element.attr("ri:filename"),
            element.attr("ri:content-id"), element.attr("ri:content-title"), element.attr("ri:space-key")) +
            element.children().filter { it.tagName() in setOf("ri:page", "ri:space") }.map {
                listOf(it.tagName(), it.attr("ri:content-id"), it.attr("ri:content-title"), it.attr("ri:space-key"))
            }.sortedBy { JsonArray(it.map(::JsonPrimitive)).toString() }.flatten()
        return parsed.allElements.filter { element ->
            element.parents().none { it.normalName() in setOf("ac:plain-text-body", "ac:parameter", "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext") }
        }.mapNotNull { element ->
            when (element.tagName()) {
                "ri:attachment" -> "attachments" to attachment(element)
                "ac:image" -> {
                    val target = element.children().firstOrNull { it.tagName() in setOf("ri:attachment", "ri:url") }
                    "images" to if (target == null) listOf("unresolved")
                        else if (target.tagName() == "ri:attachment") attachment(target)
                        else listOf("url", target.attr("ri:value"))
                }
                "ac:structured-macro", "ac:macro" -> "macros" to listOf(element.attr("ac:name"), element.attr("ac:macro-id"))
                else -> null
            }
        }.groupingBy { it }.eachCount()
    }
    private fun preserveDocumentReferences(before: String, after: String) {
        val fresh = documentReferences(after)
        val removed = mutableMapOf<String, Int>()
        documentReferences(before).forEach { (reference, count) ->
            val missing = count - (fresh[reference] ?: 0)
            if (missing > 0) removed[reference.first] = (removed[reference.first] ?: 0) + missing
        }
        if (removed.isNotEmpty()) throw ContextRemoteException("reference_removal",
            "Whole-body replacement would remove existing images, attachments or macros",
            details = buildJsonObject { put("removedReferences", buildJsonObject { removed.forEach { (kind, count) -> put(kind, count) } }) },
            next = "Preserve the references or use contextual edits. Set allowReferenceRemoval:true only for an intentional whole-body removal.")
    }
    private suspend fun guard(env: Environment, config: ContextConfig, target: RemoteNode, title: String, body: String, before: RemotePage?, account: JsonObject) {
        val current = admitted(env, target.id)
        if (current.first == config && current.second.copy(version = target.version) == target && current.second.version != target.version)
            throw ContextRemoteException("version_conflict", "Native document version changed before saving", details = buildJsonObject {
                put("expectedVersion", target.version); put("currentVersion", current.second.version)
            }, next = "Read the current document and retain the original request; no write was performed.")
        require(current.first == config && current.second == target) { "Governing namespace or document changed" }
        if (before != null) require(remote.page(target.id, target, config, freshStorage = true) == before) { "Document content changed" }
        policy.checkKnowledge(config, target, title, body, before)
        require(remote.currentUser() == account) { "Authenticated account changed" }
        unchanged(env)
    }
    private data class SourceVersion(val id: String, val version: Int, val reference: String)
    private fun basedOn(data: JsonObject): List<SourceVersion> {
        if (!data.containsKey("basedOn")) return emptyList()
        val values = data["basedOn"] as? JsonArray ?: error("basedOn must be an array")
        require(values.size in 1..10) { "basedOn requires 1..10 document versions" }
        return values.map { value ->
            val entry = value as? JsonObject ?: error("basedOn entries must be objects")
            require(entry.keys == setOf("document", "version")) { "basedOn entries require only document and version" }
            val reference = entry.text("document")
            val version = (entry["version"] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
            require(version != null && version > 0) { "basedOn versions must be positive native integers" }
            SourceVersion(document(reference), version, reference)
        }.also { require(it.map { source -> source.id }.distinct().size == it.size) { "basedOn documents must be unique" } }
    }
    private suspend fun checkSources(env: Environment, sources: List<SourceVersion>) {
        if (sources.isEmpty()) return
        val graph = ReadGraph()
        sources.forEach { source ->
            val (config, node) = admitted(env, source.id, graph)
            checkDocumentReference(source.reference, config)
            require(node.type == "page") { "basedOn requires native pages" }
            if (node.version != source.version) throw ContextRemoteException("source_changed", "A basedOn document version changed",
                details = buildJsonObject { put("document", source.id); put("expectedVersion", source.version); put("currentVersion", node.version) },
                next = "Read the changed source and reconsider the derived content. Do not automatically repeat the write.")
        }
        graph.verify()
        unchanged(env)
    }

    /** Native links are navigation observations, never an inferred logical premise or a complete graph. */
    private suspend fun review(env: Environment, data: JsonObject, graph: ReadGraph): JsonObject {
        val entries = data["documents"] as? JsonArray ?: error("documents must be an array")
        require(entries.size in 1..25) { "Select 1..25 documents" }
        val selected = entries.map { value ->
            val entry = value as? JsonObject ?: error("documents entries must be objects")
            require("document" in entry && entry.keys.all { it in setOf("document", "version") }) { "Use document and an optional native version" }
            val id = document(entry.text("document"))
            val observed = if ("version" in entry) (entry["version"] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
                ?.also { require(it > 0) } ?: error("version must be a positive integer") else null
            Triple(id, observed, entry.text("document"))
        }
        require(selected.map { it.first }.distinct().size == selected.size) { "Selected documents must be unique" }
        val ids = selected.map { it.first }.toSet()
        val changed = mutableSetOf<String>()
        val links = linkedSetOf<Pair<String, String>>()
        val unresolved = mutableListOf<JsonObject>()
        var truncated = false
        val titleTargets = mutableMapOf<Pair<String, String>, String?>()
        suspend fun resolveTitle(space: String, title: String): String? {
            val key = space to title
            if (titleTargets.containsKey(key)) return titleTargets[key]
            if (titleTargets.size >= 10 || title.isBlank() || title.length > 1000 || title.any(Char::isISOControl) ||
                !space.matches(Regex("[A-Za-z0-9_-]{1,100}"))) { truncated = true; return null }
            titleTargets[key] = null
            val registered = configs(env).filter { it.site.trimEnd('/') == origin && it.space == space }
            if (registered.isEmpty()) { truncated = true; return null }
            fun quoted(value: String): String = "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
            val roots = registered.map { config ->
                when {
                    config.isKnowledge -> config.knowledgeRootPageId ?: config.homePageId
                    config.structurePolicy == "pilot" -> config.pilotRootPageId!!
                    else -> config.homePageId
                }
            }.distinct().joinToString(" or ") { "(id = $it or ancestor = $it)" }
            try {
                val batch = remote.wikiBatch("/wiki/rest/api/content/search", mapOf(
                    "cql" to "space = ${quoted(space)} and type = page and ($roots) and title = ${quoted("\"$title\"")}",
                    "cqlcontext" to """{"contentStatuses":["current"]}""", "expand" to "space,version,ancestors", "limit" to "2"), null)
                val rows = batch.getValue("results").jsonArray
                if (batch["complete"] != JsonPrimitive(true) || batch.containsKey("cursor")) { truncated = true; return null }
                if (rows.isEmpty()) { truncated = true; return null }
                if (rows.size != 1) { truncated = true; return null }
                val row = rows.single().jsonObject
                require(row.text("title") == title && row["type"] == JsonPrimitive("page") && row["status"] == JsonPrimitive("current"))
                val id = numeric(row.text("id"))
                val spaceId = normalizeNativeSpaceId(row)
                require(registered.any { it.spaceId == spaceId })
                val version = row.getValue("version").jsonObject.getValue("number").jsonPrimitive
                require(!version.isString && (version.intOrNull ?: 0) > 0)
                val ancestors = row["ancestors"]?.jsonArray?.map { numeric(it.jsonObject.text("id")) }
                require(ancestors == null || ancestors.distinct().size == ancestors.size && id !in ancestors)
                val parent = row.optional("parentId")
                parent?.let(::numeric)
                require(parent != null || ancestors != null)
                require(!row.containsKey("parentId") || ancestors == null || parent == ancestors.lastOrNull())
                require(parent != id)
                val node = RemoteNode(id, title, parent ?: ancestors?.lastOrNull(), "page", spaceId, version.int, "current")
                graph.seed(node)
                require(admitted(env, id, graph).second == node)
                if (id !in ids) truncated = true
                titleTargets[key] = id
                return id
            } catch (e: CancellationException) { throw e }
            catch (e: RootScopeDenial) { throw e }
            catch (_: Exception) { truncated = true; return null }
        }
        var bytes = 0L
        val documents = selected.map { (id, observed, reference) ->
            val (governing, node) = admitted(env, id, graph)
            require(node.type == "page") { "Review requires registered native pages" }
            checkDocumentReference(reference, governing)
            require(observed == null || observed <= node.version) { "Observed version exceeds the current native version" }
            if (observed != null && observed != node.version) changed += id
            val page = nativePage(node)
            bytes += page.body.toByteArray(Charsets.UTF_8).size
            require(bytes <= 16L * 1024 * 1024) { "Selected bodies exceed the bounded read budget" }
            val parsed = Jsoup.parse(page.body, "", Parser.xmlParser())
            for (element in parsed.allElements) {
                val href = element.attr("href")
                val nativeAnchor = element.tagName() == "a" && (href.startsWith("/wiki/") || href.startsWith("$origin/wiki/"))
                if (element.tagName() == "ri:page" || nativeAnchor) {
                    val target = if (element.tagName() == "ri:page") {
                        if (element.hasAttr("ri:content-id")) element.attr("ri:content-id").takeIf { it.matches(Regex("[1-9][0-9]*")) }
                        else resolveTitle(if (element.hasAttr("ri:space-key")) element.attr("ri:space-key") else governing.space,
                            element.attr("ri:content-title"))
                    } else try { reviewDocumentLink(if (href.startsWith("/wiki/")) origin + href else href) } catch (_: Exception) { null }
                    if (target != null && target in ids) links += id to target
                    else {
                        truncated = true
                        if (unresolved.size < 100) unresolved += buildJsonObject {
                            put("from", id)
                            if (target != null) put("reference", target)
                            put("reason", if (target == null) "unresolved_native_link" else "outside_selection")
                        }
                    }
                }
            }
            buildJsonObject {
                put("id", id); put("version", node.version)
                observed?.let { put("observedVersion", it); put("changed", it != node.version) }
            }
        }
        val affected = linkedSetOf<String>()
        var frontier = changed.toSet()
        while (frontier.isNotEmpty()) {
            val next = links.filter { it.second in frontier }.map { it.first }.filter { it !in changed && it !in affected }.toSet()
            affected += next; frontier = next
        }
        val cycles = ids.filter { start ->
            val seen = mutableSetOf<String>()
            val pending = ArrayDeque<String>()
            links.filter { it.first == start }.forEach { pending.add(it.second) }
            var cyclic = false
            while (pending.isNotEmpty()) {
                val current = pending.removeFirst()
                if (current == start) { cyclic = true; break }
                if (seen.add(current)) links.filter { it.first == current }.forEach { pending.add(it.second) }
            }
            cyclic
        }
        return buildJsonObject {
            put("documents", JsonArray(documents))
            put("links", JsonArray(links.map { (from, to) -> buildJsonObject { put("from", from); put("to", to) } }))
            put("affectedCandidates", JsonArray(affected.map(::JsonPrimitive))); put("cycles", JsonArray(cycles.map(::JsonPrimitive)))
            put("unresolved", JsonArray(unresolved)); put("complete", false); put("selectedOnly", true); put("truncated", truncated)
            put("limitations", JsonArray(listOf("Selected native links are navigation, not logical premises or proof of truth.",
                "Unselected, unresolved and inaccessible pages are outside this review; this is not global impact closure.",
                "Reconsider AND/OR conditions, retractions and independent alternative support in the source before editing.").map(::JsonPrimitive)))
        }
    }

    /** Rebuildable corpus relations are discovery hints; validate each source before traversing it. */
    private suspend fun knowledgeGraph(env: Environment, data: JsonObject, graph: ReadGraph): JsonObject {
        val scope = data.optional("scope") ?: "effective"
        val config = ContextConfig.load(env.root, runtimeSettings, scope) ?: error("Selected Confluence scope is not configured")
        require(config.site.trimEnd('/') == origin)
        val reference = data.text("document")
        val start = document(reference)
        checkDocumentReference(reference, config)
        val depth = data.number("depth", 2); val limit = data.number("limit", 25)
        require(depth in 1..5 && limit in 1..25) { "Graph requires depth 1..5 and limit 1..25" }
        val (governing, startNode) = admitted(env, start, graph)
        require(governing == config && startNode.type == "page") { "Graph start must use the selected governing namespace" }
        val mirror = ContextMirror(env.root, remote)
        val path = ContextFiles.safe(env.root, "confluence/${config.space}/.sync/manifest.json")
        fun snapshot(): JsonObject {
            if (!Files.exists(path)) return buildJsonObject {}
            require(Files.isRegularFile(path) && Files.size(path) <= 32L * 1024 * 1024) { "Graph manifest exceeds the read budget" }
            return mirror.manifest(config)
        }
        val manifest = snapshot()
        val pages = manifest["pages"] as? JsonObject ?: buildJsonObject {}
        val unavailable = (manifest["unavailable"] as? JsonArray)?.map { it.jsonPrimitive.content }?.toSet().orEmpty()
        var syncRequired = manifest["inventoryComplete"] != JsonPrimitive(true) ||
            manifest["structurePolicy"] != JsonPrimitive(config.structurePolicy) ||
            manifest["knowledgeRootPageId"]?.jsonPrimitive?.content != config.knowledgeRootPageId ||
            manifest["pilotRootPageId"]?.jsonPrimitive?.content != config.pilotRootPageId ||
            start !in pages || unavailable.isNotEmpty()
        var truncated = false
        val edges = mutableListOf<JsonObject>()
        val titles = pages.entries.groupBy { it.value.jsonObject["title"]?.jsonPrimitive?.content }
        for ((id, value) in pages) {
            if (id in unavailable) continue
            val relations = value.jsonObject["relations"] as? JsonObject
            if (relations == null) { syncRequired = true; continue }
            if (relations["complete"] != JsonPrimitive(true)) truncated = true
            for (item in relations.getValue("items").jsonArray) {
                if (edges.size >= 50000) { truncated = true; break }
                val relation = item.jsonObject
                val target = relation["to"]?.jsonPrimitive?.content ?: titles[relation["title"]?.jsonPrimitive?.content]
                    ?.singleOrNull()?.key
                if (target == null || target !in pages || target in unavailable) { truncated = true; continue }
                edges += buildJsonObject {
                    put("from", id); put("to", target); put("type", relation.getValue("type")); put("reason", relation.getValue("reason"))
                    relation["fragment"]?.let { put("fragment", it) }
                }
            }
        }
        val bySource = edges.groupBy { it.text("from") }
        val byTarget = edges.groupBy { it.text("to") }
        val sources = mutableMapOf<String, Boolean>()
        val visible = linkedMapOf(start to startNode)
        var attempts = 0
        var bytes = 0L
        suspend fun current(id: String): Boolean {
            sources[id]?.let { return it }
            sources[id] = false
            if (id in unavailable || id !in pages) { syncRequired = true; return false }
            if (++attempts > 40) { truncated = true; return false }
            val previous = graph.nodes.keys.toSet()
            try {
                val (owner, node) = admitted(env, id, graph)
                require(owner == config && node.type == "page")
                val record = pages.getValue(id).jsonObject
                val page = nativePage(node)
                bytes += page.body.toByteArray(Charsets.UTF_8).size
                require(bytes <= 16L * 1024 * 1024) { "Graph source bodies exceed the read budget" }
                if (record["id"] != JsonPrimitive(node.id) || record["title"] != JsonPrimitive(node.title) ||
                    record["parentId"]?.jsonPrimitive?.content != node.parentId || record["spaceId"] != JsonPrimitive(node.spaceId) ||
                    record["type"] != JsonPrimitive(node.type) || record["status"] != JsonPrimitive(node.status) ||
                    record["version"] != JsonPrimitive(node.version) || record["bodyHash"] != JsonPrimitive(ContextFiles.hash(page.body)) ||
                    record["relations"] != knowledgeRelations(page.body, origin, config.space)) {
                    syncRequired = true
                    return false
                }
                visible[id] = node; sources[id] = true
                return true
            } catch (e: CancellationException) { throw e }
            catch (e: ContextRemoteException) {
                if (e is RootScopeDenial || e.code !in setOf("http_403", "http_404")) throw e
                truncated = true
            } catch (_: IllegalArgumentException) { truncated = true }
            // A denied candidate is never a bridge or an exported observation.
            graph.nodes.keys.removeAll { it !in previous }
            return false
        }
        val selected = linkedSetOf(start)
        val selectedEdges = linkedSetOf<JsonObject>()
        val queue = ArrayDeque<Pair<String, Int>>()
        queue.add(start to 0)
        while (queue.isNotEmpty()) {
            val (id, hop) = queue.removeFirst()
            val fresh = current(id)
            if (!fresh && id != start) continue
            val adjacent = (if (fresh) bySource[id].orEmpty() else emptyList()) + byTarget[id].orEmpty()
            if (hop == depth) { if (adjacent.any { it.text("from") !in selected || it.text("to") !in selected }) truncated = true; continue }
            for (edge in adjacent) {
                if (selectedEdges.size >= 100 || attempts >= 40) { truncated = true; break }
                val source = edge.text("from"); val target = edge.text("to")
                val next = if (source == id) target else source
                if (next !in selected && selected.size >= limit) { truncated = true; continue }
                if (!current(source) || !current(target)) continue
                selectedEdges += edge
                if (selected.add(next)) queue.add(next to hop + 1)
            }
        }
        val affected = linkedSetOf<String>()
        var frontier = setOf(start)
        while (frontier.isNotEmpty()) {
            val next = selectedEdges.filter { it.text("to") in frontier && it.text("type") in setOf("evidence", "depends_on") }
                .map { it.text("from") }.filter { it != start && it !in affected }.toSet()
            affected += next; frontier = next
        }
        graph.finalChecks += { require(snapshot() == manifest) { "Sync generation changed during graph read; retry the read" } }
        return buildJsonObject {
            put("document", start)
            put("nodes", JsonArray(selected.mapNotNull { id -> visible[id]?.let { node -> buildJsonObject {
                put("id", id); put("title", node.title); put("version", node.version)
            } } }))
            put("relations", JsonArray(selectedEdges.map { edge -> JsonObject(edge +
                ("version" to JsonPrimitive(visible.getValue(edge.text("from")).version))) }))
            put("affectedCandidates", JsonArray(affected.map(::JsonPrimitive)))
            put("syncRequired", syncRequired); put("truncated", truncated); put("complete", false)
            put("limitations", JsonArray(listOf("One synchronized scope and bounded explicit relations; not global logical closure.",
                "Affected candidates depend on the starting document; verify premises, dates and deployment evidence before editing.").map(::JsonPrimitive)))
        }
    }

    private fun cursor(value: JsonObject): String = Base64.getUrlEncoder().withoutPadding()
        .encodeToString(value.toString().toByteArray(Charsets.UTF_8)).also {
            require(it.length <= WIKI_CURSOR_LIMIT) { "Cursor is oversized" }
        }
    private fun decode(value: String): JsonObject {
        require(value.length <= WIKI_CURSOR_LIMIT) { "Cursor is oversized" }
        return Json.parseToJsonElement(String(Base64.getUrlDecoder().decode(value), Charsets.UTF_8)).jsonObject
    }
    suspend fun execute(operation: String, data: JsonObject): JsonObject {
        var rootDenied: ContextConfig? = null
        val result = remote.wikiOperation {
            if (operation in setOf("search_documents", "get_document", "list_document_versions", "list_comments", "review", "graph"))
                return@wikiOperation withTimeoutOrNull(30_000) { executeBounded(operation, data) { rootDenied = it } }
                    ?: buildJsonObject { put("status", "error"); put("code", "read_timeout"); put("message", "Read exceeded its time budget; reduce the scope or retry the read") }
            executeBounded(operation, data) { rootDenied = it }
        }
        rootDenied?.let { ContextMirror(launchRoot, remote).recordNoticeRootDenial(it) }
        return result
    }
    private suspend fun executeBounded(operation: String, data: JsonObject, rootDenied: (ContextConfig) -> Unit): JsonObject {
        var effect = 0
        var nativeVersionMessage: String? = null
        var resolvedDocument: String? = null
        var observedBaseVersion: Int? = null
        var observedReference: String? = null
        try {
            val fields = when (operation) {
                "search_documents" -> setOf("query", "scope", "limit", "cursor", "metadata", "root", "snippetLength")
                "review" -> setOf("documents")
                "graph" -> setOf("document", "scope", "depth", "limit")
                "get_document" -> setOf("document", "version", "offset", "length", "format", "metadata", "section", "snapshot")
                "create_document" -> setOf("parent", "title", "content", "format", "metadata", "includeBody", "basedOn")
                "update_document" -> setOf("document", "version", "parent", "content", "title", "old_text", "new_text", "append", "edits", "links", "allowReferenceRemoval", "format", "metadata", "includeBody", "basedOn", "section")
                "list_comments" -> setOf("target", "limit", "cursor", "format", "metadata", "includeReplies", "repliesPerComment")
                "create_comment" -> setOf("target", "text", "replyTo", "selection", "occurrence", "format", "metadata", "includeBody")
                "update_comment" -> setOf("comment", "version", "text", "format", "metadata", "includeBody")
                "list_document_versions" -> setOf("document", "limit", "cursor")
                else -> error("Unknown wiki operation")
            }
            require(data.keys.all { it in fields }) { "Unknown wiki argument" }
            listOf("metadata", "includeBody", "includeReplies", "allowReferenceRemoval").filter { it in data }.forEach { data.flag(it) }
            environment()
            localReads?.invoke(operation, data)?.let { return it }
            if (operation == "get_document" && "snapshot" in data) {
                throw ContextRemoteException("snapshot_unavailable", "A search range requires its local Markdown snapshot. Search again against a readable corpus.")
            }
            val env = environment()
            val graph = if (operation in setOf("search_documents", "get_document", "list_document_versions", "review", "graph")) ReadGraph() else null
            val result = when (operation) {
                "review" -> review(env, data, graph!!)
                "graph" -> knowledgeGraph(env, data, graph!!)
                "search_documents" -> {
                    val scope = data.optional("scope") ?: "effective"
                    val config = ContextConfig.load(env.root, runtimeSettings, scope) ?: error("Selected Confluence scope is not configured")
                    require(config.site.trimEnd('/') == origin)
                    val query = data.text("query")
                    val limit = data.number("limit", 10); require(limit in 1..25)
                    val snippetLength = data["snippetLength"]?.let { value ->
                        (value as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull?.also { require(it in 1..500) }
                            ?: error("snippetLength must be a positive integer")
                    }
                    val root = data.optional("root")?.let(::document)
                    val binding = buildJsonObject {
                        put("query", query); put("scope", scope); put("limit", limit)
                        root?.let { put("root", it) }; snippetLength?.let { put("snippetLength", it) }
                    }
                    val previous = data.optional("cursor")?.let(::decode)
                    require(previous == null || previous.keys == setOf("binding", "native") && previous["binding"] == binding) { "Search cursor conditions changed" }
                    if (root != null) {
                        val (governing, _) = admitted(env, root, graph)
                        require(governing == config) { "Search root must use the selected governing namespace" }
                        checkDocumentReference(data.text("root"), config)
                    }
                    val (batch, leaves) = remote.wikiSearch(config, query, if (snippetLength != null) minOf(limit, 5) else limit,
                        previous?.text("native"), root)
                    leaves.forEach { graph!!.seed(it) }
                    var bytes = 0L
                    val rows = batch.getValue("results").jsonArray.map { value ->
                        val row = value.jsonObject
                        val (governing, node) = admitted(env, row.text("id"), graph)
                        if (root != null) require(chain(node.id, graph).any { it.id == root }) { "Search result is outside selected root" }
                        val visible = row.filterKeys { data.flag("metadata") || it in setOf("id", "title", "version") }.toMutableMap()
                        if (snippetLength != null) {
                            val page = nativePage(node)
                            bytes += page.body.toByteArray(Charsets.UTF_8).size
                            require(bytes <= 16L * 1024 * 1024) { "Selected bodies exceed the bounded read budget" }
                            val markdown = ConfluenceMarkdownConverter.htmlToMarkdown(page.body,
                                currentPageUrl = "$origin/wiki/spaces/${governing.space}/pages/${node.id}")
                            val at = markdown.indexOf(query)
                            if (at >= 0) {
                                val offset = markdown.codePointCount(0, at)
                                val end = markdown.offsetByCodePoints(at, minOf(snippetLength, markdown.codePointCount(at, markdown.length)))
                                visible["snippet"] = buildJsonObject { put("text", markdown.substring(at, end)); put("offset", offset); put("format", "markdown") }
                            }
                        }
                        JsonObject(visible)
                    }
                    JsonObject(batch + buildJsonObject {
                        if (data.flag("metadata")) put("status", "searched")
                        put("results", JsonArray(rows))
                        batch["cursor"]?.takeUnless { it is JsonNull }?.let { next ->
                            put("cursor", cursor(buildJsonObject { put("binding", binding); put("native", next) }))
                        }
                    })
                }
                "get_document", "list_document_versions" -> {
                    val id = document(data.text("document"))
                    val (config, node) = admitted(env, id, graph)
                    checkDocumentReference(data.text("document"), config)
                    if (operation == "list_document_versions") {
                        val limit = data.number("limit", 20); require(limit in 1..50)
                        val batch = remote.wikiBatch("/wiki/api/v2/pages/$id/versions", mapOf("limit" to limit.toString()), data.optional("cursor"))
                        val versions = batch.getValue("results").jsonArray
                        require(versions.map { it.jsonObject["number"]?.jsonPrimitive?.intOrNull }.let { nums -> nums.all { it != null && it > 0 && it <= node.version } && nums.distinct().size == nums.size })
                        versions.forEach { require(!it.jsonObject.optional("authorId").isNullOrBlank() && !it.jsonObject.optional("createdAt").isNullOrBlank()) }
                        require(admitted(env, id, graph) == (config to node)) { "Current document namespace changed during history read" }
                        JsonObject(batch.filterKeys { it != "results" } + mapOf("status" to JsonPrimitive("listed"), "document" to JsonPrimitive(id), "versions" to versions))
                    } else {
                        val format = data.optional("format") ?: "markdown"
                        val section = data.optional("section")
                        require(format in setOf("markdown", "storage", "outline") && !(section != null && format == "outline")) { "Use markdown/storage for a section or outline for the document" }
                        val selected = data["version"]?.jsonPrimitive?.int
                        require(selected == null || selected in 1..node.version)
                        require(section == null || selected != null) { "Bind section positions to the native version returned by format:outline" }
                        val offset = data.number("offset", 0)
                        require(offset == 0 || selected != null) { "Bind continuation to a native document version" }
                        val page = if (selected == null || selected == node.version) nativePage(node) else remote.wikiHistoricalPage(id, selected)
                        require(page.node.spaceId == node.spaceId) { "Historical document identity changed" }
                        documentResult(env, config, page, "read", offset, data.number("length", 4000), node, format = format, metadata = data.flag("metadata"), graph = graph, section = section)
                    }
                }
                "create_document", "update_document" -> {
                    val creating = operation == "create_document"
                    val sources = basedOn(data)
                    if (creating && "parent" !in data) {
                        val config = ContextConfig.load(env.root, runtimeSettings) ?: error("Resolved knowledge scope required")
                        require(config.wholeSpace) { "Parentless creation requires a selected whole space" }
                        val title = data.text("title")
                        val body = storage(data.text("content"), data.optional("format") ?: "storage")
                        val account = remote.currentUser()
                        checkSources(env, sources)
                        policy.checkKnowledgeCreation(config, title, body)
                        val message = "wiki create document ${UUID.randomUUID()}"
                        nativeVersionMessage = message
                        val callback: suspend () -> Unit = {
                            policy.checkKnowledgeCreation(config, title, body)
                            require(remote.currentUser() == account) { "Authenticated account changed" }
                            unchanged(env); checkSources(env, sources); effect = 1
                        }
                        val saved = remote.create(config.spaceId, null, title, body, message, config, callback)
                        effect = 2; observedReference = numeric(saved.node.id)
                        val acknowledgement = documentResult(env, config, saved, "created", account = account,
                            metadata = data.flag("metadata"), includeBody = data.flag("includeBody"))
                        checkSources(env, sources)
                        return publishReplica(env, config, saved, acknowledgement)
                    }
                    val pureAppend = !creating && data.containsKey("append") &&
                        listOf("content", "title", "parent", "old_text", "new_text", "edits", "links", "section").none { data.containsKey(it) } && !data.flag("allowReferenceRemoval")
                    val supplied = if (creating) data.text("parent") else if (data.containsKey("document")) data.text("document") else null
                    val logical = if (!creating && (supplied == null || supplied in setOf("memory", "feedback"))) supplied ?: "memory" else null
                    if (!creating && (!pureAppend && (logical != null || !data.containsKey("version"))))
                        throw ContextRemoteException("invalid_edit", "Only a pure append may omit document or version", next = "Other edits require an exact page ID and a positive native version.")
                    val expected = if (!creating && data.containsKey("version")) {
                        val value = data["version"] as? JsonPrimitive
                        value?.takeIf { !it.isString }?.intOrNull?.takeIf { it > 0 }
                            ?: throw ContextRemoteException("invalid_version", "version must be a positive native integer; omit it only for a pure append")
                    } else null
                    val id = if (logical != null) appendDestination(env, logical) else document(supplied ?: error("A document is required"))
                    if (!creating && sources.any { it.id == id }) throw ContextRemoteException("invalid_source",
                        "An update target cannot also be an unchanged basedOn source",
                        next = "Use the target version for its CAS condition and exclude the target from basedOn. No write was performed.")
                    checkSources(env, sources)
                    if (!creating) resolvedDocument = id
                    val (config, node) = admitted(env, id)
                    require(logical == null || config.isKnowledge) { "Recording destination is governed by a regulated project namespace" }
                    if (logical == null) checkDocumentReference(supplied!!, config)
                    val before = if (creating) null else remote.page(id, node, config, freshStorage = true)
                    if (before != null) {
                        observedBaseVersion = before.node.version
                        if (expected != null && expected != before.node.version) throw ContextRemoteException("version_conflict", "Native document version changed", details = buildJsonObject {
                            put("expectedVersion", expected); put("currentVersion", before.node.version)
                        }, next = "Read the current document before preparing another edit.")
                    }
                    val move = if (!creating && data.containsKey("parent")) relocation(env, config, node, data.text("parent")) else null
                    var linked: LinkEdit? = null
                    val title = data.optional("title") ?: before?.node?.title ?: error("A title is required")
                    val format = data.optional("format") ?: if (pureAppend && (logical != null || expected == null)) "text" else "storage"
                    require(format in setOf("storage", "text")) { "Use storage or text for new content" }
                    var recorder: JsonObject? = null
                    val body = if (creating) storage(data.text("content"), format) else {
                        val whole = data.containsKey("content")
                        val patch = data.containsKey("old_text") || data.containsKey("new_text")
                        val append = data.containsKey("append")
                        val batch = data.containsKey("edits")
                        val link = data.containsKey("links")
                        val section = data.optional("section")
                        val modes = listOf(whole, patch, append, batch, link).count { it }
                        if (modes > 1 || modes == 0 && !data.containsKey("title") && move == null || patch && !(data.containsKey("old_text") && data.containsKey("new_text")))
                            throw ContextRemoteException("invalid_edit", "Provide content, append, edits, links, one old_text/new_text pair, title or parent", next = "Choose one body edit form; title and parent may accompany it.")
                        if (data.flag("allowReferenceRemoval") && !whole)
                            throw ContextRemoteException("invalid_edit", "allowReferenceRemoval applies only to a complete content replacement")
                        if (section != null && (!whole || data.flag("allowReferenceRemoval")))
                            throw ContextRemoteException("invalid_edit", "section requires content and preserves native references; use contextual edits for rich restructuring")
                        val names = when { whole -> listOf("content"); append -> listOf("append"); patch -> listOf("old_text", "new_text"); else -> emptyList() }
                        if (names.any { (data[it] as? JsonPrimitive)?.isString != true })
                            throw ContextRemoteException("invalid_edit", "Edit fields must be JSON strings", next = "Use strings, including an empty new_text for deletion.")
                        if (whole) storage(data.text("content"), format).also {
                            require(it.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "A bounded storage body is required" }
                            if (section == null && !data.flag("allowReferenceRemoval")) preserveDocumentReferences(before!!.body, it)
                        }.let { if (section == null) it else sectionBody(before!!.body, section, it) } else if (append) {
                            val addition = data.text("append")
                            if (addition.isBlank()) throw ContextRemoteException("invalid_edit", "append must not be blank")
                            val added = storage(addition, format).let { stored ->
                                if (logical == null) stored else {
                                    recorder = remote.currentUser()
                                    recordingAccount(recorder!!) + stored
                                }
                            }
                            val original = before!!.body
                            if (logical == "feedback") {
                                val section = try { ContextFeedback.section(original) } catch (_: Exception) {
                                    throw ContextRemoteException("selection_required", "A unique safe 개선 의견 section is required", next = "Select or repair the existing feedback section before recording.")
                                }
                                original.substring(0, section.end) + added + original.substring(section.end)
                            } else original + added
                        } else if (link) {
                            val replacements = data["links"] as? JsonObject ?: throw ContextRemoteException("invalid_links", "links must be an object of exact href to document-reference strings")
                            linkBody(env, before!!.body, replacements).also { linked = it }.body
                        } else if (!patch && !batch) before!!.body else {
                            val edits = if (batch) {
                                data["edits"] as? JsonArray ?: throw ContextRemoteException("invalid_edit", "edits must be a JSON array")
                            } else JsonArray(listOf(buildJsonObject { put("old_text", data.getValue("old_text")); put("new_text", data.getValue("new_text")) }))
                            patchBody(before!!.body, edits, format)
                        }
                    }
                    if (pureAppend && body.toByteArray(Charsets.UTF_8).size > 4 * 1024 * 1024)
                        throw ContextRemoteException("memory_capacity", "The recording page has no capacity for this append; its existing content is preserved",
                            details = buildJsonObject { put("document", id); put("limitBytes", 4 * 1024 * 1024) },
                            next = "Select an existing topic or domain original page explicitly and append there; connect it through the existing native index. No page was created or changed. Do not repeat an unknown write.")
                    if (before != null && body == before.body && title == before.node.title && (move == null || move.destination.first().id == before.node.parentId))
                        throw ContextRemoteException("invalid_edit", "The document edit makes no change")
                    val account = recorder ?: remote.currentUser()
                    if (logical == null) guard(env, config, node, title, body, before, account)
                    val message = "wiki ${if (creating) "create" else "update"} document ${UUID.randomUUID()}"
                    nativeVersionMessage = message
                    val callback: suspend () -> Unit = {
                        if (logical != null && appendDestination(env, logical) != id)
                            throw ContextRemoteException("selection_required", "Recording destination changed; select it again")
                        guard(env, config, node, title, body, before, account)
                        checkSources(env, sources)
                        linked?.let { checkLinkTargets(env, it.targets) }
                        move?.let { checkRelocation(env, it) }; effect = 1
                    }
                    val saved = if (creating) remote.create(config.spaceId, id, title, body, message, config, callback)
                        else remote.updateDocument(before!!, body, message, config, title, callback, allowUnknownReadback = true, parentId = move?.destination?.first()?.id)
                    effect = 2
                    move?.let { checkRelocation(env, it, saved.node) }
                    if (creating) observedReference = numeric(saved.node.id)
                    val acknowledgement = documentResult(env, config, saved, if (creating) "created" else "updated", account = account,
                        metadata = data.flag("metadata"), includeBody = data.flag("includeBody"))
                    checkSources(env, sources)
                    linked?.let { checkLinkTargets(env, it.targets) }
                    return publishReplica(env, config, saved, acknowledgement)
                }
                "list_comments", "create_comment", "update_comment" -> comments(env, operation, data) { observed, reference ->
                    effect = if (observed) 2 else 1
                    if (reference != null) observedReference = reference
                }
                else -> error("Unknown wiki operation")
            }
            graph?.verify()
            unchanged(env)
            return result
        } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            (e as? RootScopeDenial ?: e.cause as? RootScopeDenial)?.let { rootDenied(it.config) }
            val unknown = (e as? ContextRemoteException)?.outcomeUnknown == true || (effect == 2 || effect == 1 && e !is ContextRemoteException)
            return buildJsonObject {
                put("status", if (unknown) "unknown" else "error")
                put("code", (e as? ContextRemoteException)?.code ?: if (unknown) "unknown_outcome" else "invalid_request")
                val details = (e as? ContextRemoteException)?.details ?: buildJsonObject {}
                val recovery = if (unknown) observedReference else null
                if (details.isNotEmpty() || recovery != null) put("details", JsonObject(details + buildJsonObject {
                    recovery?.let { put("reference", it) }
                }))
                (e as? ContextRemoteException)?.next?.let { put("next", it) }
                put("message", if (e is ContextRemoteException || e is IllegalArgumentException || e is IllegalStateException) e.message ?: "Wiki operation unavailable" else "Wiki operation unavailable")
                if (unknown) {
                    put("operation", operation)
                    put("request", buildJsonObject {
                        data.filterKeys { it in setOf("document", "parent", "target", "comment", "version") }.forEach { (key, value) -> put(key, value) }
                        resolvedDocument?.let { put("document", it) }
                        observedBaseVersion?.let { put("version", it) }
                    })
                    put("requestHash", ContextFiles.hash(data.toString()))
                    nativeVersionMessage?.let { put("nativeVersionMessage", it) }
                    put("next", "Any details.reference is an unverified read-only recovery clue. Inspect the document, native history or comments using read-only tools. Retain the original request; do not automatically repeat the write.")
                }
            }
        }
    }

    private suspend fun comments(env: Environment, operation: String, data: JsonObject, effect: (Boolean, String?) -> Unit): JsonObject {
        val updating = operation == "update_comment"
        val ref = if (updating) resolveComment(data.text("comment")) else null
        val rawTarget = if (updating) ref!!.let { it.issue ?: "${it.kind}:${it.id}" } else data.text("target")
        val issue = ref?.issue ?: issue(rawTarget)
        val metadata = data.flag("metadata")
        if (issue != null) {
            admitIssue(env, issue)
            if (updating) throw ContextRemoteException("native_comment_cas_unavailable", "Jira supports editing but has no documented atomic native comment version; this version-checked update is unsupported. Use the native provider UI for an intentional edit")
            if (operation == "list_comments") {
                require(data.optional("format") == null) { "Jira comments retain native ADF; format applies to Confluence comments" }
                require(!data.flag("includeReplies")) { "Native Jira comment replies are unsupported" }
                val offset = data.optional("cursor")?.toIntOrNull() ?: if (data.optional("cursor") == null) 0 else error("Invalid Jira cursor")
                val batch = remote.wikiJiraComments(issue, data.number("limit", 20), offset)
                unchanged(env)
                return JsonObject(batch + buildJsonObject {
                    put("status", "listed"); put("target", issue)
                    put("comments", JsonArray(batch.getValue("comments").jsonArray.map { reference(it.jsonObject, issue, metadata = metadata) }))
                    if (metadata) {
                        put("supportedActions", JsonArray(listOf("list_comments", "create_comment").map(::JsonPrimitive)))
                        put("limitations", "Jira has no documented atomic comment version or native reply creation")
                    }
                })
            }
            if (data.optional("replyTo") != null) throw ContextRemoteException("native_comment_reply_unavailable", "Jira reply creation is unsupported by the published request schema")
            if ("selection" in data || "occurrence" in data) throw ContextRemoteException("native_inline_comment_unavailable", "A text selection applies to Confluence pages; Jira comments have none")
            require(data.optional("format") in setOf(null, "text")) { "Jira comments use plain text" }
            val text = data.text("text"); policy.checkCommentText(text)
            require(env.connections["role"]?.jsonPrimitive?.contentOrNull in setOf("개발", "QA", "PM")) { "An explicit author role is required" }
            val account = remote.wikiJiraUser()
            // A native parent read establishes issue access before the single create.
            remote.wikiJiraComments(issue, 1, 0)
            val saved = remote.wikiCreateComment("jira", issue, text, null, account.getValue("accountId").jsonPrimitive.content, null) {
                remote.wikiJiraComments(issue, 1, 0); admitIssue(environment(), issue)
                require(remote.wikiJiraUser() == account); unchanged(env); effect(false, null)
            }
            val observedId = saved.optional("id")?.takeIf { it.matches(Regex("[1-9][0-9]*")) }
            effect(true, observedId?.let { "$origin/rest/api/3/issue/$issue/comment/$it" })
            require(remote.wikiJiraUser() == account); unchanged(env)
            return JsonObject(reference(saved, issue, metadata = metadata, includeBody = data.flag("includeBody")) + ("status" to JsonPrimitive("created")))
        }
        val targetRef = ref ?: if (rawTarget.startsWith("footer:") || rawTarget.startsWith("inline:") || rawTarget.contains("-comments/")) resolveComment(rawTarget) else null
        val selection = if (operation == "create_comment") data.optional("selection") else null
        if (operation == "create_comment") {
            if (selection == null && "occurrence" in data) throw ContextRemoteException("invalid_selection", "occurrence requires selection")
            if (selection != null && (targetRef != null || data.optional("replyTo") != null)) throw ContextRemoteException("invalid_selection",
                "selection starts a new inline thread on a page; it cannot be combined with replyTo or a comment target")
            selection?.let(::checkSelection)
        }
        val location = targetRef?.let { remote.wikiCommentLocation(it.id, it.kind) }
        val pageId = location?.first ?: document(rawTarget)
        val (config, node) = admitted(env, pageId)
        if (targetRef == null) checkDocumentReference(rawTarget, config)
        val pageUrl = "$origin/wiki/spaces/${config.space}/pages/$pageId"
        if (operation == "list_comments") {
            val limit = data.number("limit", 20); require(limit in 1..50)
            val format = data.optional("format") ?: "markdown"
            require(format in setOf("markdown", "storage")) { "Use markdown for reading or storage for editing comments" }
            val includeReplies = data.flag("includeReplies")
            val repliesPerComment = data.number("repliesPerComment", 3)
            require(repliesPerComment in 1..10 && (!includeReplies || limit * repliesPerComment <= 100)) { "Bounded replies require 1..10 per comment and limit * repliesPerComment <= 100" }
            require(!includeReplies || targetRef == null) { "includeReplies applies to page root comments; a comment reference already reads its direct replies" }
            val continuation = data.optional("cursor")?.let(::decode)
            fun cursorBinding(ref: CommentRef?, size: Int, expanded: Boolean): String =
                "$pageId:${ref?.kind}:${ref?.id}:$size:$format:$expanded:${if (expanded) repliesPerComment else 0}:${ContextFiles.hash(env.connections.toString())}"
            val binding = cursorBinding(targetRef, limit, includeReplies)
            require(continuation == null || continuation.optional("binding") == binding) { "Cursor belongs to a different target or configuration" }
            val kind = targetRef?.kind ?: continuation?.optional("kind") ?: "footer"
            require(kind in setOf("footer", "inline"))
            val selectedComment = targetRef?.let { remote.commentDetail("confluence", pageId, it.id, it.kind, location!!.second, node, config) }
            val batch = remote.wikiComments(pageId, kind, targetRef?.id, limit, continuation?.optional("next"))
            val items = batch.getValue("results").jsonArray.map { raw ->
                val item = raw.jsonObject
                val projected = reference(item, pageId, format, metadata, pageUrl = pageUrl)
                if (!includeReplies) projected else {
                    val replyTarget = CommentRef(kind, item.text("id"))
                    val replies = remote.wikiComments(pageId, kind, replyTarget.id, repliesPerComment, null)
                    val replyNext = replies.optional("cursor")
                    JsonObject(projected + ("replies" to buildJsonObject {
                        put("target", "$kind:${replyTarget.id}"); put("limit", repliesPerComment); put("format", format)
                        put("comments", JsonArray(replies.getValue("results").jsonArray.map { reference(it.jsonObject, pageId, format, metadata, pageUrl = pageUrl) }))
                        put("hasMore", replyNext != null); put("complete", replyNext == null)
                        if (replyNext != null) put("cursor", cursor(buildJsonObject {
                            put("binding", cursorBinding(replyTarget, repliesPerComment, false)); put("kind", kind); put("next", replyNext)
                        }))
                    }))
                }
            }
            val next = batch.optional("cursor")
            val more = next != null || targetRef == null && kind == "footer"
            require(admitted(env, pageId) == (config to node)); unchanged(env)
            return buildJsonObject {
                put("status", "listed"); put("target", pageId); put("comments", JsonArray(items)); put("hasMore", more); put("complete", !more)
                selectedComment?.let { put("comment", reference(it, pageId, format, metadata, pageUrl = pageUrl)) }
                put("threadScope", if (targetRef != null) "direct native replies" else if (includeReplies) "page root comments with one bounded page of direct replies per root" else "page root comments; replies not included")
                if (more) put("cursor", cursor(buildJsonObject { put("binding", binding); put("kind", if (next == null) "inline" else kind); next?.let { put("next", it) } }))
            }
        }
        val page = remote.page(pageId, node, config, freshStorage = true)
        val text = storage(data.text("text"), data.optional("format") ?: "storage"); policy.checkCommentText(text)
        val account = remote.currentUser()
        val before = if (updating) remote.commentDetail("confluence", pageId, ref!!.id, ref.kind, location!!.second, node, config) else null
        if (before != null) {
            require(before["status"] == JsonPrimitive("current")) { "Only a current comment may be edited" }
            require(before["version"]?.jsonPrimitive?.intOrNull == data.number("version", 0)) { "Native comment version conflict; read the thread again" }
        }
        val reply = if (updating) null else (data.optional("replyTo")?.let { resolveComment(it) } ?: targetRef)
        val replyLocation = reply?.let { remote.wikiCommentLocation(it.id, it.kind) }
        require(replyLocation == null || replyLocation.first == pageId) { "Reply belongs to another document" }
        val replySnapshot = reply?.let { remote.commentDetail("confluence", pageId, it.id, it.kind, replyLocation!!.second, node, config) }
        require(replySnapshot == null || replySnapshot["status"] == JsonPrimitive("current")) { "Reply target is not current" }
        if (before != null) policy.checkKnowledge(config, node, node.title, text, RemotePage(node, before.getValue("body").jsonPrimitive.content))
        guard(env, config, node, node.title, page.body, page, account)
        val anchor = selection?.let { selectionAnchor(page.body, it, if ("occurrence" in data) data.number("occurrence", 0) else null) }
        val callback: suspend () -> Unit = {
            if (reply != null) {
                require(remote.wikiCommentLocation(reply.id, reply.kind) == replyLocation) { "Reply thread changed" }
                require(remote.commentDetail("confluence", pageId, reply.id, reply.kind, replyLocation!!.second, node, config) == replySnapshot) { "Reply target changed" }
            }
            if (before != null) require(remote.commentDetail("confluence", pageId, ref!!.id, ref.kind, location!!.second, node, config) == before) { "Comment changed" }
            guard(env, config, node, node.title, page.body, page, account)
            effect(false, null)
        }
        val saved = if (before == null) remote.wikiCreateComment("confluence", pageId, text, reply?.id, account.getValue("accountId").jsonPrimitive.content, config,
                if (anchor != null) "inline" else reply?.kind ?: "footer", anchor?.let { Triple(selection!!, it.first, it.second) }, callback)
            else remote.wikiUpdateComment(pageId, before, text, config, callback)
        val observedId = saved.optional("id")?.takeIf { it.matches(Regex("[1-9][0-9]*")) }
        val observedKind = saved.optional("kind")?.takeIf { it in setOf("footer", "inline") }
        effect(true, if (before == null && observedId != null && observedKind != null) "$observedKind:$observedId" else null)
        if (saved["editorId"] != account["accountId"]) throw ContextRemoteException("readback_mismatch",
            "Comment readback editor differs from the authenticated account", details = buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("editorId")))) })
        var markedPage: RemotePage? = null
        if (anchor == null) {
            require(admitted(env, pageId) == (config to node))
            policy.checkKnowledge(config, node, node.title, page.body, page)
        } else {
            // The provider writes the marker into the page itself, so only that body and at most one version step may differ.
            val (currentConfig, current) = admitted(env, pageId)
            require(currentConfig == config && current.copy(version = node.version) == node && current.version - node.version in 0..1) { "Governing namespace or document changed" }
            val marked = remote.page(pageId, current, config, freshStorage = true)
            policy.checkKnowledge(config, current, current.title, marked.body, marked)
            val markerRef = (saved["inlineContext"] as? JsonObject)?.get("inlineMarkerRef")?.jsonPrimitive?.contentOrNull
            if (!selectionAnchored(marked.body, selection!!, anchor.first, markerRef)) throw ContextRemoteException("unknown_create_comment",
                "Created inline comment position could not be verified; inspect the page before any new write", true,
                buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("position")))); observedId?.let { put("reference", "inline:$it") } })
            markedPage = marked
        }
        require(remote.currentUser() == account); unchanged(env)
        val acknowledgement = JsonObject(reference(saved, pageId, metadata = metadata, includeBody = data.flag("includeBody")) +
            ("status" to JsonPrimitive(if (updating) "updated" else "created")))
        return markedPage?.let { publishReplica(env, config, it, acknowledgement, page) } ?: acknowledgement
    }
}

/** Eight resource verbs; workspace and policy are inferred from the installed launch project. */
class WikiTools private constructor(private val ownedRemote: ConfluenceRemote?, private val directService: WikiService?,
                                    private val operation: (suspend (String, JsonObject) -> JsonObject)? = null) : AutoCloseable {
    val service: WikiService get() = requireNotNull(directService) { "A multi-space provider dispatches operations rather than owning one space" }
    constructor(operation: suspend (String, JsonObject) -> JsonObject) : this(null, null, operation)
    constructor(credentials: JiraConfig, client: HttpClient? = null,
                launchRoot: Path = Paths.get("").toAbsolutePath().toRealPath(), cache: ConfluencePageCache? = null,
                runtimeSettings: KnowledgeSettings? = null) : this(ConfluenceRemote(credentials, client, cache), credentials.baseUrl.trimEnd('/'), launchRoot, runtimeSettings)
    private constructor(remote: ConfluenceRemote, origin: String, launchRoot: Path, runtimeSettings: KnowledgeSettings?) :
        this(remote, WikiService(remote, origin, launchRoot, runtimeSettings))
    constructor(service: WikiService) : this(null, service)
    private fun call(name: String, data: JsonObject): String = runBlocking { (operation?.invoke(name, data) ?: service.execute(name, data)).toString() }

    @McpTool(name = "search_documents", description = "Search the shared local corpus by exact native page ID or ranked text. Results include version, real excerpts, heading context when available, and a snapshot-bound read object for get_document.")
    fun searchDocuments(@McpParam(description = "Exact native page ID or text, 1..200 Unicode code points. Each text term must match the title or authored Markdown.") query: String,
                        @McpParam(description = "Native namespace label; select a configured space using the space parameter", required = false) scope: String = "effective",
                        @McpParam(description = "Results per page, 1..25", required = false) limit: Int = 10,
                        @McpParam(description = "Returned continuation", required = false) cursor: String? = null,
                        @McpParam(description = "Include detailed native metadata; normally omitted", required = false) metadata: Boolean = false,
                        @McpParam(description = "Optional native domain root ID or URL within the selected registered namespace", required = false) root: String? = null,
                        @McpParam(description = "Optional excerpt size, 1..500 code points; local search returns real excerpts with native identity", required = false) snippetLength: Int? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("search_documents", buildJsonObject {
        space?.let { put("space", it) }
        put("query", query); put("scope", scope); put("limit", limit); cursor?.let { put("cursor", it) }; put("metadata", metadata)
        root?.let { put("root", it) }; snippetLength?.let { put("snippetLength", it) }
    })

    @McpTool(name = "document_relations", description = "Read explicit authored knowledge relations and affected review candidates. Links are navigation evidence, not automatic truth propagation.")
    fun documentRelations(@McpParam(description = "Native page ID or URL") document: String,
                          @McpParam(description = "Depth 1..3", required = false) depth: Int = 2,
                          @McpParam(description = "Bounded result limit", required = false) limit: Int = 25,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("graph", buildJsonObject {
        space?.let { put("space", it) }
        put("document", document); put("scope", "common"); put("depth", depth); put("limit", limit)
    })

    @McpTool(name = "get_document", description = "Read Markdown content or pass the read object from search_documents to read its exact local range. format:outline gives version-local positions for native section reads and edits. storage retains rich structure. metadata:true adds audit data. Optional version reads actual history under current access.")
    fun getDocument(@McpParam(description = "Exact page ID or Confluence URL") document: String,
                    @McpParam(description = "Native version; required for a continued read", required = false) version: Int? = null,
                    @McpParam(description = "Unicode code point offset", required = false) offset: Int = 0,
                    @McpParam(description = "Maximum code points, 1..12000", required = false) length: Int = 4000,
                    @McpParam(description = "markdown (default), storage for native structure, outline for heading positions; keep format, section and version on continuation", required = false) format: String = "markdown",
                    @McpParam(description = "Include URL, read status, author, time, hashes and detailed range metadata", required = false) metadata: Boolean = false,
                    @McpParam(description = "Exact position from format:outline, e.g. 1.2; requires version. Returns only the body below that heading including subsections", required = false) section: String? = null,
                    @McpParam(description = "Snapshot from search_documents read object. Keep it with the version and Markdown format for continued reads; a changed snapshot requires a new search", required = false) snapshot: String? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("get_document", buildJsonObject {
        space?.let { put("space", it) }
        put("document", document); version?.let { put("version", it) }; put("offset", offset); put("length", length); put("format", format); put("metadata", metadata)
        section?.let { put("section", it) }
        snapshot?.let { put("snapshot", it) }
    })

    @McpTool(name = "create_document", description = "Create a document under an explicit knowledge parent. Use format:text for simple writing or native storage for rich content. One write and verified readback; refreshes an existing local replica and returns a short acknowledgement.")
    fun createDocument(@McpParam(description = "Exact parent page ID or URL; otherwise choose space explicitly", required = false) parent: String? = null,
                       @McpParam(description = "Document title") title: String,
                       @McpParam(description = "New document content") content: String,
                       @McpParam(description = "storage (default) or text for escaped plain paragraphs", required = false) format: String? = null,
                       @McpParam(description = "Include detailed native metadata", required = false) metadata: Boolean = false,
                       @McpParam(description = "Include bounded saved storage content", required = false) includeBody: Boolean = false,
                       @McpParam(description = "Optional AND version conditions for actual source pages: [{\"document\":\"123\",\"version\":4}], 1..10. Not permission to republish", required = false) basedOn: JsonArray? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("create_document", buildJsonObject {
        space?.let { put("space", it) }
        parent?.let { put("parent", it) }; put("title", title); put("content", content); format?.let { put("format", it) }; put("metadata", metadata); put("includeBody", includeBody)
        basedOn?.let { put("basedOn", it) }
    })

    @McpTool(name = "update_document", description = "Append a record without a preceding read: omit document or use memory for common originals, feedback for the existing improvement section. These logical records include the authenticated account's native mention; explicit document IDs append without that header. Pure append may omit version and defaults to text. Other edits require a page and read version. links retargets exact document hrefs without resending the body; parent nests deeper while retaining every existing ancestor. Verified writes refresh an existing local replica. Never retries or rebases.")
    fun updateDocument(@McpParam(description = "Exact page ID or URL; pure append also accepts memory (default) or feedback", required = false) document: String? = null,
                       @McpParam(description = "Positive native version; pure append may omit it for one fresh CAS write", required = false) version: Int? = null,
                       @McpParam(description = "Replacement body: selected section when section is supplied, otherwise the whole document; exclusive with append/edits/links/old_text", required = false) content: String? = null,
                       @McpParam(description = "New title; omitted preserves title", required = false) title: String? = null,
                       @McpParam(description = "format:text uses exact whole decoded simple block text; storage uses an exact complete block or its entire raw text. Never a word fragment", required = false) old_text: String? = null,
                       @McpParam(description = "format:text escapes literal text and preserves outer tags; empty clears their inner text. storage replaces the selected source, including empty deletion", required = false) new_text: String? = null,
                       @McpParam(description = "Content to append; feedback inserts at the end of its existing 개선 의견 section", required = false) append: String? = null,
                       @McpParam(description = "text defaults for logical or versionless append; otherwise storage. Explicit format wins", required = false) format: String? = null,
                       @McpParam(description = "Include detailed native metadata", required = false) metadata: Boolean = false,
                       @McpParam(description = "Include bounded saved storage content", required = false) includeBody: Boolean = false,
                       @McpParam(description = "1..20 disjoint edits against the same original, saved in one version. With format:text use whole visible simple block text: [{\"old_text\":\"Old\",\"new_text\":\"New\"}]; storage uses exact source", required = false) edits: JsonArray? = null,
                       @McpParam(description = "Allow intentional removal of existing image, attachment or macro references in a full content replacement only", required = false) allowReferenceRemoval: Boolean = false,
                       @McpParam(description = "Optional AND version conditions for actual source pages: [{\"document\":\"123\",\"version\":4}], 1..10. Not permission to republish", required = false) basedOn: JsonArray? = null,
                       @McpParam(description = "New parent page or folder ID/URL in the same knowledge namespace; must retain all existing ancestors. Requires explicit document and version; omitted preserves location", required = false) parent: String? = null,
                       @McpParam(description = "1..20 exact existing href to native document ID or same-origin page URL (optional #fragment) mappings. Replaces all matching real links together, preserving labels/code/images; exclusive with other body edits. Requires document and version", required = false) links: JsonObject? = null,
                       @McpParam(description = "Exact outline position at version, e.g. 1.2. With content replaces only its body; heading/outside bytes and existing native references are preserved", required = false) section: String? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("update_document", buildJsonObject {
        space?.let { put("space", it) }
        document?.let { put("document", it) }; version?.let { put("version", it) }; content?.let { put("content", it) }; title?.let { put("title", it) }
        old_text?.let { put("old_text", it) }; new_text?.let { put("new_text", it) }
        append?.let { put("append", it) }; format?.let { put("format", it) }; put("metadata", metadata); put("includeBody", includeBody)
        edits?.let { put("edits", it) }; put("allowReferenceRemoval", allowReferenceRemoval); basedOn?.let { put("basedOn", it) }; parent?.let { put("parent", it) }
        links?.let { put("links", it) }
        section?.let { put("section", it) }
    })

    @McpTool(name = "list_comments", description = "Read comment content. Confluence defaults to Markdown; a comment reference includes that comment and direct replies. Page reads may include bounded replies. Jira retains native ADF.")
    fun listComments(@McpParam(description = "Page ID/URL, returned comment reference to read it and replies, or configured Jira issue key/URL") target: String,
                     @McpParam(description = "Page size, 1..50", required = false) limit: Int = 20,
                     @McpParam(description = "Returned continuation", required = false) cursor: String? = null,
                     @McpParam(description = "Confluence only: markdown (default) or storage before editing", required = false) format: String? = null,
                     @McpParam(description = "Include detailed native audit metadata", required = false) metadata: Boolean = false,
                     @McpParam(description = "For Confluence page root lists, include one bounded direct-reply page per comment", required = false) includeReplies: Boolean = false,
                     @McpParam(description = "Replies per root, 1..10; limit times this must be <=100 when included", required = false) repliesPerComment: Int = 3,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("list_comments", buildJsonObject {
        space?.let { put("space", it) }
        put("target", target); put("limit", limit); cursor?.let { put("cursor", it) }; format?.let { put("format", it) }
        put("metadata", metadata); put("includeReplies", includeReplies); put("repliesPerComment", repliesPerComment)
    })

    @McpTool(name = "create_comment", description = "Create a native footer comment, a footer/inline reply, a new inline comment anchored to exact page text with selection, or a configured Jira issue comment. Jira replies and selections are unsupported. Never automatically repeat unknown writes.")
    fun createComment(@McpParam(description = "Page ID/URL or configured Jira issue key/URL") target: String,
                      @McpParam(description = "Comment content") text: String,
                      @McpParam(description = "Native footer/inline comment reference on the same page", required = false) replyTo: String? = null,
                      @McpParam(description = "Confluence: storage (default) or text; Jira always plain text", required = false) format: String? = null,
                      @McpParam(description = "Include detailed native metadata", required = false) metadata: Boolean = false,
                      @McpParam(description = "Include saved native comment content", required = false) includeBody: Boolean = false,
                      @McpParam(description = "Exact visible page text inside one plain text run to anchor a new inline comment, 1..1000 characters; exclusive with replyTo", required = false) selection: String? = null,
                      @McpParam(description = "Which match of selection to anchor, from 1; required when it occurs more than once", required = false) occurrence: Int? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("create_comment", buildJsonObject {
        space?.let { put("space", it) }
        put("target", target); put("text", text); replyTo?.let { put("replyTo", it) }; format?.let { put("format", it) }
        put("metadata", metadata); put("includeBody", includeBody); selection?.let { put("selection", it) }; occurrence?.let { put("occurrence", it) }
    })

    @McpTool(name = "update_comment", description = "Edit an existing Confluence footer/inline comment with its native version and native permissions. Original author and thread stay fixed. Jira has no documented atomic version and is unsupported.")
    fun updateComment(@McpParam(description = "Native ID or returned footer/inline reference or exact comment API URL") comment: String,
                      @McpParam(description = "Native version from the preceding comment read") version: Int,
                      @McpParam(description = "Complete replacement comment content") text: String,
                      @McpParam(description = "storage (default) or text for plain paragraphs", required = false) format: String? = null,
                      @McpParam(description = "Include detailed native metadata", required = false) metadata: Boolean = false,
                      @McpParam(description = "Include saved native comment content", required = false) includeBody: Boolean = false,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("update_comment", buildJsonObject {
        space?.let { put("space", it) }
        put("comment", comment); put("version", version); put("text", text); format?.let { put("format", it) }
        put("metadata", metadata); put("includeBody", includeBody)
    })

    @McpTool(name = "list_document_versions", description = "Read one bounded page of actual document versions, native author/time/message and continuation. Current registered access is checked first.")
    fun listDocumentVersions(@McpParam(description = "Exact page ID or URL") document: String,
                             @McpParam(description = "Page size, 1..50", required = false) limit: Int = 20,
                             @McpParam(description = "Returned continuation", required = false) cursor: String? = null,
                       @McpParam(description = "Exact configured space key; validates a native target or selects an otherwise ambiguous destination", required = false) space: String? = null): String = call("list_document_versions", buildJsonObject {
        space?.let { put("space", it) }
        put("document", document); put("limit", limit); cursor?.let { put("cursor", it) }
    })
    override fun close() { ownedRemote?.close() }
}
