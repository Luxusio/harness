package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.config.JiraConfig
import io.crscube.clitools.mcp.McpException
import io.crscube.clitools.mcp.ToolRegistry
import io.crscube.clitools.knowledge.KnowledgeSettings
import io.crscube.clitools.knowledge.LocalCorpus
import kotlinx.coroutines.runBlocking
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.http.*
import io.ktor.http.content.OutgoingContent
import kotlinx.serialization.json.*
import org.junit.jupiter.api.BeforeEach
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.IOException
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.*

/** Native edit/comment/history regressions adapted from the Talos handoff snapshot. */
class NativeDocumentContractTest {
    private lateinit var root: Path
    @TempDir lateinit var runtimeHome: Path
    private val credentials = JiraConfig("https://example.atlassian.net", "not-an-account@example.test", "private-fixture-token")
    private lateinit var settings: KnowledgeSettings
    private val headers = headersOf(HttpHeaders.ContentType, "application/json")

    @BeforeEach fun configuredGlobalConnection() {
        val config = buildJsonObject {
            put("enabled", true)
            put("site", credentials.baseUrl); put("space", "COMMON"); put("spaceId", "20"); put("rootPageId", "210")
        }
        settings = KnowledgeSettings(config, credentials, runtimeHome.resolve("data"))
        root = settings.corpusRoot
        Files.createDirectories(root)
    }

    private fun arguments(vararg values: Pair<String, JsonElement>) = JsonObject(values.toMap())
    private fun strings(vararg values: Pair<String, String>) = arguments(*values.map { it.first to JsonPrimitive(it.second) }.toTypedArray())
    private fun contextEdits(vararg edits: Pair<String, String>) = JsonArray(edits.map { (old, new) -> strings("old_text" to old, "new_text" to new) })
    private fun evidence(vararg documents: Pair<String, Int>) = JsonArray(documents.map { (id, version) -> arguments("document" to JsonPrimitive(id), "version" to JsonPrimitive(version)) })
    private fun call(registry: ToolRegistry, operation: String, data: JsonObject): JsonObject =
        Json.parseToJsonElement(registry.callTool(operation, data)).jsonObject

    // Tests of native attribution/storage explicitly request detail; ordinary UX tests use call().
    private fun callWithBody(registry: ToolRegistry, operation: String, data: JsonObject): JsonObject =
        call(registry, operation, JsonObject(data + ("includeBody" to JsonPrimitive(true))))

    private fun callDetailed(registry: ToolRegistry, operation: String, data: JsonObject): JsonObject {
        val detail = data.toMutableMap()
        if (operation != "list_document_versions") detail["metadata"] = JsonPrimitive(true)
        if (operation in setOf("create_document", "update_document", "create_comment", "update_comment")) detail["includeBody"] = JsonPrimitive(true)
        val target = (data["target"] as? JsonPrimitive)?.content.orEmpty()
        if (operation == "list_comments" && !target.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*")) && "/browse/" !in target)
            detail["format"] = JsonPrimitive("storage")
        return call(registry, operation, JsonObject(detail))
    }

    private fun rejected(registry: ToolRegistry, operation: String, data: JsonObject): JsonObject? {
        val value = try { call(registry, operation, data) } catch (_: McpException) { return null }
        assertEquals("error", value["status"]?.jsonPrimitive?.content, "Unsafe input must be rejected: $value")
        return value
    }

    private data class Page(
        val id: String, var parent: String?, var title: String = "Knowledge $id", var content: String = "<p>Knowledge $id</p>",
        var version: Int = 1, var space: String = "20", var status: String = "current", var message: String = "",
        var author: String = "native-editor", val kind: String = "page"
    ) {
        fun json(storage: Boolean = false) = buildJsonObject {
            put("id", id); put("title", title); put("spaceId", space); put("status", status); put("type", kind)
            parent?.let { put("parentId", it) }
            putJsonObject("version") { put("number", version); put("message", message); put("authorId", author); put("createdAt", "2026-11-03T10:00:00.000Z") }
            if (storage) putJsonObject("body") { putJsonObject("storage") { put("value", content); put("representation", "storage") } }
        }
        fun revision() = buildJsonObject {
            put("number", version); put("authorId", author); put("createdAt", "2026-11-03T10:00:00.000Z"); put("message", message)
        }
    }

    private data class Comment(
        val id: String, val page: String, val kind: String = "footer", var parent: String? = null,
        val originalAuthor: String = "native-account", var editor: String = originalAuthor,
        var version: Int = 1, var content: String = "<p>Comment $id</p>", var selection: String = "quoted source", var resolutionStatus: String? = null
    ) {
        fun json(storage: Boolean = true) = buildJsonObject {
            put("id", id); put("pageId", page); put("status", "current"); put("title", "Re: knowledge")
            parent?.let { put("parentCommentId", it) }
            putJsonObject("version") { put("number", version); put("authorId", editor); put("createdAt", "2026-11-03T10:00:00.000Z"); put("message", "") }
            if (storage) putJsonObject("body") { putJsonObject("storage") { put("value", content); put("representation", "storage") } }
            if (kind == "inline") putJsonObject("properties") {
                put("inlineMarkerRef", "marker-$id"); put("inlineOriginalSelection", selection)
            }
            if (kind == "inline") {
                resolutionStatus?.let { put("resolutionStatus", it) }
                put("resolutionLastModifierId", "native-resolver")
                put("resolutionLastModifiedAt", "2026-11-06T12:00:00Z")
            }
        }
    }

    private inner class Provider {
        val pages = linkedMapOf(
            "900" to Page("900", null, "Company documents"),
            "200" to Page("200", "900", "Common home"),
            "210" to Page("210", "200", "Registered knowledge root"),
            "201" to Page("201", "210"),
            "202" to Page("202", "200", "Unregistered sibling"),
            "220" to Page("220", "200", "Project home"),
            "221" to Page("221", "220", "Project knowledge")
        )
        val history = mutableMapOf("201" to mutableListOf(pages.getValue("201").copy()))
        val folders = linkedMapOf<String, Page>()
        val childOverrides = mutableMapOf<String, List<JsonObject>>()
        val childNext = mutableMapOf<String, String>()
        val comments = linkedMapOf(
            "301" to Comment("301", "201"),
            "302" to Comment("302", "201", originalAuthor = "another-author", editor = "native-account", version = 2),
            "303" to Comment("303", "201", editor = "another-editor", version = 2),
            "304" to Comment("304", "202"),
            "305" to Comment("305", "201", parent = "301"),
            "311" to Comment("311", "201", kind = "inline")
        )
        val requests = mutableListOf<HttpRequestData>()
        val effects = mutableListOf<Pair<HttpRequestData, JsonObject>>()
        val denied = mutableMapOf<String, HttpStatusCode>()
        val deniedMutations = mutableMapOf<String, HttpStatusCode>()
        var account = "native-account"
        var accountAvailable = true
        var draft: Page? = null
        var beforeRequest: (HttpRequestData) -> Unit = {}
        var transform: (HttpRequestData, JsonObject) -> JsonObject = { _, result -> result }
        var serializePageBody: (String) -> String = { it }
        var serializeCommentBody: (String) -> String = { it }
        var loseWriteResponse = false
        var commitLostWrite = true
        var inlineVersionStep = 1
        var inlineMarkerShift = 0
        var inlineWritesMarker = true
        var inlineMarkerHidden = false
        var inlineMarkerInLegacyMacro = false
        var inlineResolution: String? = "open"
        var conflictOnUpdate = false
        var failureBody = "{}"
        var historyNext: String? = null
        var searchSelection: List<String>? = null
        var searchNext: String? = null
        var numericSearchSpace = false
        var jiraBody = "Existing Jira comment"
        var jiraNativeBody: JsonObject? = null
        var jiraId = "401"
        var jiraAuthor = "native-account"
        var jiraEditor = "different-editor"
        var jiraAccount = "native-account"
        var jiraIdentity: JsonObject? = null

        fun nativeHistory() {
            history["201"] = mutableListOf(
                Page("201", "210", content = "<p>Original native version</p>", version = 1, message = "Original statement", author = "original-author"),
                Page("201", "210", content = "<p>Second native version</p>", version = 2, message = "Human correction", author = "second-author"),
                Page("201", "210", content = "<p>Current native version</p>", version = 3, message = "Latest correction", author = "latest-editor")
            )
            pages["201"] = history.getValue("201").last().copy()
        }

        private fun body(request: HttpRequestData): JsonObject {
            val text = when (val outgoing = request.body) {
                is OutgoingContent.ByteArrayContent -> String(outgoing.bytes(), Charsets.UTF_8)
                else -> throw AssertionError("Expected native JSON body for ${request.method} ${request.url}: $outgoing")
            }
            return Json.parseToJsonElement(text).jsonObject
        }

        private fun listing(values: List<JsonObject>, next: String? = null) = buildJsonObject {
            put("results", JsonArray(values)); putJsonObject("_links") { next?.let { put("next", it) } }
        }

        private fun jiraComment() = buildJsonObject {
            put("id", jiraId); put("self", "${credentials.baseUrl}/rest/api/3/issue/DEV-1/comment/$jiraId")
            put("body", jiraNativeBody ?: buildJsonObject {
                put("type", "doc"); put("version", 1)
                putJsonArray("content") { add(buildJsonObject { put("type", "paragraph"); putJsonArray("content") {
                    add(buildJsonObject { put("type", "text"); put("text", jiraBody) })
                } }) }
            })
            putJsonObject("author") { put("accountId", jiraAuthor) }; putJsonObject("updateAuthor") { put("accountId", jiraEditor) }
            put("created", "2026-11-02T09:00:00.000+0000"); put("updated", "2026-11-03T10:00:00.000+0000")
        }

        private fun get(request: HttpRequestData): JsonObject {
            val path = request.url.encodedPath
            val directChildren = Regex("/wiki/api/v2/(pages|folders)/([1-9][0-9]*)/direct-children").matchEntire(path)
            if (directChildren != null) {
                val parent = directChildren.groupValues[2]
                val children = childOverrides[parent] ?: (pages.values + folders.values).filter { it.parent == parent }.map { it.json() }
                return listing(children, childNext[parent])
            }
            val folderMatch = Regex("/wiki/api/v2/folders/([1-9][0-9]*)").matchEntire(path)
            if (folderMatch != null) return folders.getValue(folderMatch.groupValues[1]).json()
            val pageMatch = Regex("/wiki/api/v2/pages/([1-9][0-9]*)").matchEntire(path)
            if (pageMatch != null) {
                val id = pageMatch.groupValues[1]
                val selectedVersion = request.url.parameters["version"]?.toInt()
                val selected = when {
                    selectedVersion != null -> history[id]?.singleOrNull { it.version == selectedVersion }
                    request.url.parameters["get-draft"] == "true" && draft != null -> draft
                    else -> pages[id]
                } ?: throw AssertionError("Unexpected page/version read: ${request.url}")
                return selected.json(request.url.parameters["body-format"] == "storage")
            }
            if (path == "/rest/api/3/myself" && jiraIdentity != null) return jiraIdentity!!
            if (path == "/wiki/rest/api/user/current" || path == "/rest/api/3/myself") return if (accountAvailable) buildJsonObject {
                put("type", "known"); put("accountId", if (path.endsWith("myself")) jiraAccount else account); put("displayName", "Native account")
            } else buildJsonObject { put("displayName", "Email is not identity"); put("emailAddress", credentials.email) }
            if (path == "/wiki/api/v2/spaces") return listing(listOf(buildJsonObject { put("id", "20"); put("key", "COMMON"); put("name", "Common"); put("homepageId", "200") }))
            val versions = Regex("/wiki/api/v2/pages/([1-9][0-9]*)/versions(?:/([1-9][0-9]*))?").matchEntire(path)
            if (versions != null) {
                val id = versions.groupValues[1]
                val native = history[id] ?: mutableListOf(pages.getValue(id))
                val number = versions.groupValues[2].toIntOrNull()
                if (number != null) return native.single { it.version == number }.revision()
                val offset = request.url.parameters["cursor"]?.toInt() ?: 0
                val limit = request.url.parameters["limit"]?.toInt() ?: 20
                val selected = native.sortedByDescending { it.version }.drop(offset).take(limit)
                val next = historyNext ?: if (offset + selected.size < native.size) "$path?limit=$limit&cursor=${offset + selected.size}" else null
                return listing(selected.map(Page::revision), next)
            }
            val roots = Regex("/wiki/api/v2/pages/([1-9][0-9]*)/(footer|inline)-comments").matchEntire(path)
            val children = Regex("/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]*)/children").matchEntire(path)
            if (roots != null || children != null) {
                val selected = if (roots != null) comments.values.filter { it.page == roots.groupValues[1] && it.kind == roots.groupValues[2] && it.parent == null }
                    else comments.values.filter { it.kind == children!!.groupValues[1] && it.parent == children.groupValues[2] }
                val offset = request.url.parameters["cursor"]?.toInt() ?: 0
                val limit = request.url.parameters["limit"]?.toInt() ?: 20
                val batch = selected.drop(offset).take(limit)
                return listing(batch.map { it.json() }, if (offset + batch.size < selected.size) "$path?limit=$limit&cursor=${offset + batch.size}" else null)
            }
            val comment = Regex("/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]*)(?:/versions/([1-9][0-9]*))?").matchEntire(path)
            if (comment != null) {
                val value = comments[comment.groupValues[2]] ?: throw AssertionError("Unexpected comment ${request.url}")
                assertEquals(value.kind, comment.groupValues[1])
                return if (comment.groupValues[3].isEmpty()) value.json(request.url.parameters["body-format"] == "storage") else buildJsonObject {
                    put("number", comment.groupValues[3].toInt()); put("authorId", value.originalAuthor); put("createdAt", "2026-11-02T09:00:00.000Z"); put("message", "")
                }
            }
            if (path == "/rest/api/3/issue/DEV-1") return buildJsonObject {
                put("id", "5001"); put("key", "DEV-1"); putJsonObject("fields") {
                    put("summary", "Registered issue"); putJsonObject("status") { put("name", "In Progress"); putJsonObject("statusCategory") { put("key", "indeterminate") } }
                }
            }
            if (path == "/rest/api/3/issue/DEV-1/comment") return buildJsonObject {
                put("startAt", request.url.parameters["startAt"]?.toInt() ?: 0); put("maxResults", 20); put("total", 1); putJsonArray("comments") { add(jiraComment()) }
            }
            if (path == "/rest/api/3/issue/DEV-1/comment/$jiraId") return jiraComment()
            if (path == "/wiki/rest/api/content/search") {
                val cql = request.url.parameters["cql"] ?: throw AssertionError("Search requires CQL")
                assertTrue("text" in cql || "title" in cql)
                val chosen = searchSelection?.map { pages.getValue(it) } ?: listOf(if ("221" in cql || "220" in cql) pages.getValue("221") else pages.getValue("201"))
                val limit = request.url.parameters["limit"]?.toInt() ?: 10
                val next = if (searchNext == "same-query-next") URLBuilder(request.url).apply { parameters["start"] = "5" }.buildString() else searchNext
                return listing(chosen.take(limit).map { selected -> buildJsonObject {
                    put("id", selected.id); put("type", "page"); put("title", selected.title); put("status", selected.status)
                    putJsonObject("space") { put("id", if (numericSearchSpace) JsonPrimitive(selected.space.toLong()) else JsonPrimitive(selected.space)); put("key", "COMMON") }; put("version", selected.json().getValue("version"))
                    putJsonArray("ancestors") {
                        var parent = selected.parent
                        val visited = mutableSetOf<String>()
                        val ancestors = mutableListOf<Page>()
                        while (parent != null && visited.add(parent)) {
                            val ancestor = pages[parent] ?: folders[parent] ?: break
                            ancestors += ancestor
                            parent = ancestor.parent
                        }
                        ancestors.asReversed().forEach { ancestor -> add(buildJsonObject { put("id", ancestor.id); put("title", ancestor.title); put("type", ancestor.kind); put("status", ancestor.status) }) }
                    }
                } }, next)
            }
            throw AssertionError("Unexpected GET ${request.url}")
        }

        private fun mutate(request: HttpRequestData): JsonObject {
            val payload = body(request)
            effects += request to payload
            if (loseWriteResponse && !commitLostWrite) throw IOException("Lost before observing a result")
            val path = request.url.encodedPath
            val result = when {
                request.method == HttpMethod.Post && path == "/wiki/rest/api/content" -> {
                    assertEquals("COMMON", payload.getValue("space").jsonObject.getValue("key").jsonPrimitive.content)
                    val value = Page("250", payload.getValue("ancestors").jsonArray.single().jsonObject.getValue("id").jsonPrimitive.content, payload.getValue("title").jsonPrimitive.content,
                        serializePageBody(payload.getValue("body").jsonObject.getValue("storage").jsonObject.getValue("value").jsonPrimitive.content),
                        message = payload["version"]?.jsonObject?.get("message")?.jsonPrimitive?.content ?: "wiki create document", author = account)
                    pages[value.id] = value; history[value.id] = mutableListOf(value.copy()); value.json(true)
                }
                request.method == HttpMethod.Put && path.matches(Regex("/wiki/api/v2/pages/[1-9][0-9]*")) -> {
                    val id = path.substringAfterLast('/'); val value = pages.getValue(id)
                    assertEquals(id, payload.getValue("id").jsonPrimitive.content)
                    assertEquals(value.version + 1, payload.getValue("version").jsonObject.getValue("number").jsonPrimitive.int, "Native CAS must advance exactly once")
                    value.content = serializePageBody(payload.getValue("body").jsonObject.getValue("value").jsonPrimitive.content)
                    value.title = payload.getValue("title").jsonPrimitive.content; payload["parentId"]?.let { value.parent = it.jsonPrimitive.content }; value.version++
                    value.message = payload.getValue("version").jsonObject["message"]?.jsonPrimitive?.content ?: "wiki update document"
                    value.author = account; history.getOrPut(id) { mutableListOf() }.add(value.copy()); value.json(true)
                }
                request.method == HttpMethod.Post && path in setOf("/wiki/api/v2/footer-comments", "/wiki/api/v2/inline-comments") -> {
                    val parent = payload["parentCommentId"]?.jsonPrimitive?.content
                    val page = payload["pageId"]?.jsonPrimitive?.content ?: comments.getValue(parent!!).page
                    val value = Comment("350", page, if ("inline" in path) "inline" else "footer", parent,
                        originalAuthor = account, content = serializeCommentBody(payload.getValue("body").jsonObject.getValue("value").jsonPrimitive.content))
                    val anchor = payload["inlineCommentProperties"]?.jsonObject
                    if ("inline" in path && parent == null) {
                        // The native provider highlights the selection by rewriting the page itself.
                        val selected = anchor!!.getValue("textSelection").jsonPrimitive.content
                        val raw = selected.replace("&", "&amp;")
                        val target = pages.getValue(page)
                        val starts = Regex(Regex.escape(raw)).findAll(target.content).map { it.range.first }.toList()
                        assertEquals(starts.size, anchor.getValue("textSelectionMatchCount").jsonPrimitive.int, "The native match count must describe the page")
                        val start = starts[anchor.getValue("textSelectionMatchIndex").jsonPrimitive.int + inlineMarkerShift]
                        if (inlineWritesMarker) target.content = target.content.substring(0, start) +
                            "<ac:inline-comment-marker ac:ref=\"marker-350\">$raw</ac:inline-comment-marker>" + target.content.substring(start + raw.length)
                        if (inlineMarkerHidden) target.content = "<ac:task-list><ac:task><ac:task-body>${target.content}</ac:task-body></ac:task></ac:task-list>"
                        if (inlineMarkerInLegacyMacro) target.content = "<ac:macro ac:name=\"info\"><ac:rich-text-body>${target.content}</ac:rich-text-body></ac:macro>"
                        target.version += inlineVersionStep
                        value.selection = selected; value.resolutionStatus = inlineResolution
                    } else assertNull(anchor, "Only a new inline thread carries a selection")
                    comments[value.id] = value; value.json()
                }
                request.method == HttpMethod.Put && path.matches(Regex("/wiki/api/v2/(footer|inline)-comments/[1-9][0-9]*")) -> {
                    val value = comments.getValue(path.substringAfterLast('/'))
                    assertTrue(payload.keys.all { it in setOf("body", "version") }, "Comment edits cannot change parent, kind, author or inline selection")
                    assertEquals(value.version + 1, payload.getValue("version").jsonObject.getValue("number").jsonPrimitive.int)
                    value.content = serializeCommentBody(payload.getValue("body").jsonObject.getValue("value").jsonPrimitive.content)
                    value.version++; value.editor = account; value.json()
                }
                request.method == HttpMethod.Post && path == "/rest/api/3/issue/DEV-1/comment" -> {
                    jiraNativeBody = payload.getValue("body").jsonObject; jiraId = "450"; jiraAuthor = jiraAccount; jiraEditor = jiraAccount; jiraComment()
                }
                else -> throw AssertionError("Unexpected mutation ${request.method} ${request.url}")
            }
            if (loseWriteResponse) throw IOException("Provider committed, response lost")
            return result
        }

        fun <T> use(launch: Path = root, connection: KnowledgeSettings = settings, cache: ConfluencePageCache? = null, context: Boolean = false, local: Boolean = false, synchronize: Boolean = true, observeCorpus: ((LocalCorpus) -> Unit)? = null, action: (ToolRegistry) -> T): T {
            HttpClient(MockEngine { request ->
                assertEquals("example.atlassian.net", request.url.host, "Credentials cannot follow foreign resources/cursors")
                requests += request; beforeRequest(request)
                val commentResource = Regex("/wiki/api/v2/(footer|inline)-comments/([1-9][0-9]*)(?:/versions/[1-9][0-9]*)?").matchEntire(request.url.encodedPath)
                val wrongCommentKind = commentResource != null && comments[commentResource.groupValues[2]]?.kind != commentResource.groupValues[1]
                val selectedVersion = request.url.parameters["version"]?.toIntOrNull()
                val selectedPageId = Regex("/wiki/api/v2/pages/([1-9][0-9]*)").matchEntire(request.url.encodedPath)?.groupValues?.get(1)
                val missingVersion = selectedPageId != null && selectedVersion != null && history[selectedPageId]?.none { it.version == selectedVersion } != false
                val nativeFolderId = Regex("/wiki/api/v2/folders/([1-9][0-9]*)").matchEntire(request.url.encodedPath)?.groupValues?.get(1)
                val folderMissing = nativeFolderId != null && nativeFolderId !in folders
                val pageIsFolder = selectedPageId != null && selectedPageId in folders && selectedPageId !in pages
                val failure = (if (request.method != HttpMethod.Get) deniedMutations[request.url.encodedPath] else null)
                    ?: denied[request.url.encodedPath] ?: if (wrongCommentKind || missingVersion || folderMissing || pageIsFolder) HttpStatusCode.NotFound else null
                if (failure != null) respond(failureBody, failure, headers)
                else if (conflictOnUpdate && request.method == HttpMethod.Put) respond("{}", HttpStatusCode.Conflict, headers)
                else respond(transform(request, if (request.method == HttpMethod.Get) get(request) else mutate(request)).toString(), headers = headers)
            }).use { client ->
                WikiTools(connection.credentials, client, launch, cache = cache, runtimeSettings = connection).use { tools ->
                    val registry = ToolRegistry(); registry.register(tools)
                    if (local) ConfluenceRemote(connection.credentials, client).use { remote ->
                        val corpus = LocalCorpus(connection, remote)
                        if (synchronize) runBlocking { corpus.synchronize() }
                        tools.service.localReads = corpus::execute
                        tools.service.replicaRepair = corpus::requestRepair
                        observeCorpus?.invoke(corpus)
                        registry.register(corpus)
                        return action(registry)
                    }
                    return action(registry)
                }
            }
        }

        fun assertNoEffects() {
            assertTrue(effects.isEmpty(), "Rejected operation reached native mutation: ${effects.map { it.first.url }}")
            assertTrue(requests.all { it.method == HttpMethod.Get }, "Rejected operation sent POST/PUT despite no accepted mutation")
        }
        fun storageRead(id: String) = requests.any { it.url.encodedPath == "/wiki/api/v2/pages/$id" && it.url.parameters["body-format"] == "storage" }
    }


    @Test fun `section outline identifies duplicate headings without code or macro examples`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<p>Preamble</p><h2>Same</h2><p>First</p><h4>세부 😀</h4><p>Detail</p>" +
            "<ac:structured-macro ac:name='expand'><ac:rich-text-body><h1>Hidden</h1></ac:rich-text-body></ac:structured-macro>" +
            "<pre><code>&lt;h1&gt;Example&lt;/h1&gt;</code></pre><h2>Same</h2><p>Last</p>"
        provider.use { registry ->
            val outline = call(registry, "get_document", strings("document" to "201", "format" to "outline"))
            assertEquals("1 Same\n  1.1 세부 😀\n2 Same", outline.getValue("content").jsonPrimitive.content)
            assertEquals(1, outline.getValue("version").jsonPrimitive.int)
            assertFalse(outline.containsKey("url")); assertFalse(outline.containsKey("status"))
            val body = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("2"), "format" to JsonPrimitive("storage")))
            assertEquals("<p>Last</p>", body.getValue("content").jsonPrimitive.content)
            assertEquals("2", body.getValue("section").jsonPrimitive.content)
            rejected(registry, "get_document", strings("document" to "201", "section" to "2"))
        }
        provider.assertNoEffects()
    }

    @Test fun `section reads include descendants and paginate selected Unicode content at a pinned version`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<h2>Top</h2><p>First</p><h5>Child</h5><p>가😀나다</p><h2>Next</h2><p>Outside</p>"
        provider.use { registry ->
            val parent = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("1"), "format" to JsonPrimitive("storage")))
            assertEquals("<p>First</p><h5>Child</h5><p>가😀나다</p>", parent.getValue("content").jsonPrimitive.content)
            val first = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("1.1"), "length" to JsonPrimitive(2)))
            assertEquals("가😀", first.getValue("content").jsonPrimitive.content)
            assertTrue(first.getValue("truncated").jsonPrimitive.boolean)
            val second = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("1.1"), "offset" to first.getValue("nextOffset")))
            assertEquals("나다", second.getValue("content").jsonPrimitive.content)
        }
        provider.assertNoEffects()
    }

    @Test fun `section replacement sends only new text and preserves heading preamble and following native bytes`() {
        val provider = Provider()
        val prefix = "<p>Preamble 😀 &amp; exact</p><h2 local-id='keep'>Same</h2><p>First</p><h2 local-id='chosen'>Same</h2>"
        val suffix = "<h2>Next</h2><p><ac:inline-comment-marker ac:ref='outside'>Retain</ac:inline-comment-marker></p>"
        provider.pages.getValue("201").content = prefix + "<p>Old</p>" + suffix
        provider.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("2"), "content" to JsonPrimitive("새 <값>& 😀"), "format" to JsonPrimitive("text")))
            assertEquals("updated", result.getValue("status").jsonPrimitive.content)
            assertEquals(2, result.getValue("version").jsonPrimitive.int)
            assertFalse(result.containsKey("content"))
            assertEquals(prefix + "<p>새 &lt;값&gt;&amp; 😀</p>" + suffix, provider.pages.getValue("201").content)
        }
        assertEquals(1, provider.effects.size)
    }

    @Test fun `section replacement retains rich references and rejects removal or hidden marker copies`() {
        val assets = listOf("<ac:image><ri:attachment ri:filename='keep.png'/></ac:image>",
            "<ac:structured-macro ac:name='note' ac:macro-id='keep'><ac:rich-text-body><p>Keep macro</p></ac:rich-text-body></ac:structured-macro>",
            "<p><ac:inline-comment-marker ac:ref='keep'>Keep comment</ac:inline-comment-marker></p>",
            "<ac:adf-extension><ac:adf-fallback><ac:structured-macro ac:name='toc' ac:macro-id='keep'/></ac:adf-fallback></ac:adf-extension>",
            "<ac:adf-extension><ac:adf-fallback><ac:image><ri:attachment ri:filename='keep.png'/></ac:image></ac:adf-fallback></ac:adf-extension>",
            "<ac:placeholder><ac:structured-macro ac:name='toc' ac:macro-id='keep'/></ac:placeholder>",
            "<ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[<h1>Code</h1>]]></ac:plain-text-body></ac:structured-macro>",
            "<p><a name='keep'>Keep anchor</a></p>", "<h3 local-id='keep'>Keep heading anchor</h3><p>Nested</p>")
        for (asset in assets) {
            val provider = Provider()
            provider.pages.getValue("201").content = "<h2>Top</h2>" + asset + "<p>Old</p><h2>Other</h2>"
            provider.use { registry ->
                for (replacement in listOf("<p>Missing references</p>", "<pre><code>" + asset + "</code></pre>")) {
                    val error = rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                        "section" to JsonPrimitive("1"), "content" to JsonPrimitive(replacement)))
                    assertEquals("section_reference_removal", error?.getValue("code")?.jsonPrimitive?.content)
                }
                provider.assertNoEffects()
                val saved = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "section" to JsonPrimitive("1"), "content" to JsonPrimitive(asset + "<p>New</p>")))
                assertEquals("updated", saved.getValue("status").jsonPrimitive.content)
                assertEquals("<h2>Top</h2>" + asset + "<p>New</p><h2>Other</h2>", provider.pages.getValue("201").content)
            }
            assertEquals(1, provider.effects.size)
        }
    }

    @Test fun `section selectors unsafe ranges conflicting modes and stale versions never write`() {
        val bodies = listOf("<p>No headings</p>", "<h2>Top</h2><div><h2>Other</h2><p>Nested peer</p></div>",
            "<div><h2>Top</h2><p>Inside</p></div><p>Still Top</p><h2>Other</h2><p>Next</p>",
            "<div><h2>Top</h2><p>Inside</p></div><p>Still Top without a following heading</p>",
            "<h2>Top</h2><p>Unclosed", "<h2>Top", "<ac:macro ac:name='expand'><ac:rich-text-body><h2>Inert</h2></ac:rich-text-body></ac:macro>")
        for (body in bodies) {
            val provider = Provider(); provider.pages.getValue("201").content = body
            provider.use { registry ->
                rejected(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("1")))
                rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("1"), "content" to JsonPrimitive("<p>New</p>")))
            }
            provider.assertNoEffects(); assertEquals(body, provider.pages.getValue("201").content)
        }
        val provider = Provider(); provider.pages.getValue("201").content = "<h2>Top</h2><p>Old</p>"
        provider.use { registry ->
            for (key in listOf("0", "01", "1.0", "1..1", "Top", "999", "1\n")) {
                rejected(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive(key)))
                rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive(key), "content" to JsonPrimitive("<p>New</p>")))
            }
            for (mode in listOf(strings("append" to "New"), strings("old_text" to "Old", "new_text" to "New"),
                arguments("content" to JsonPrimitive("<p>New</p>"), "allowReferenceRemoval" to JsonPrimitive(true)),
                strings("content" to "<p>Unclosed"), strings("content" to "</div><p>New</p>"),
                strings("content" to "<p>New</p></div>"))) {
                rejected(registry, "update_document", JsonObject(mode + arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("1"))))
            }
            rejected(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("1"), "format" to JsonPrimitive("outline")))
            rejected(registry, "update_document", strings("document" to "201", "section" to "1", "content" to "<p>New</p>"))
            val conflict = rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(9), "section" to JsonPrimitive("1"), "content" to JsonPrimitive("<p>New</p>")))
            assertEquals("version_conflict", conflict?.getValue("code")?.jsonPrimitive?.content)
        }
        provider.assertNoEffects()
    }

    @Test fun `section writes refuse same-version inline loss and preserve fresh content checks`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<h2>Top</h2><p>Old</p>"
        provider.use { registry ->
            call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("1")))
            provider.pages.getValue("201").content = "<h2>Top</h2><p><ac:inline-comment-marker ac:ref='new'>Old</ac:inline-comment-marker></p>"
            val error = rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "section" to JsonPrimitive("1"), "content" to JsonPrimitive("New"), "format" to JsonPrimitive("text")))
            assertEquals("section_reference_removal", error?.getValue("code")?.jsonPrimitive?.content)
        }
        provider.assertNoEffects()
        val changed = Provider(); changed.pages.getValue("201").content = "<h2>Top</h2><p>Old</p>"
        var reads = 0
        changed.beforeRequest = { request ->
            if (request.url.encodedPath == "/wiki/api/v2/pages/201" && request.url.parameters["body-format"] == "storage" && ++reads == 2)
                changed.pages.getValue("201").content += "<p>Concurrent human edit</p>"
        }
        changed.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
            "section" to JsonPrimitive("1"), "content" to JsonPrimitive("<p>New</p>"))) }
        changed.assertNoEffects()
        assertTrue(changed.pages.getValue("201").content.endsWith("<p>Concurrent human edit</p>"))
    }

    @Test fun `section positions use actual historical headings under current access`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<h2>Before</h2><p>Old first</p><h2>Before</h2><p>Old second</p>"
        provider.history["201"] = mutableListOf(provider.pages.getValue("201").copy())
        provider.pages.getValue("201").apply { content = "<h2>Now</h2><p>Current</p>"; version = 2 }
        provider.history.getValue("201") += provider.pages.getValue("201").copy()
        provider.use { registry ->
            val old = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("2")))
            assertEquals("Old second", old.getValue("content").jsonPrimitive.content)
            assertEquals(1, old.getValue("version").jsonPrimitive.int)
            rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("2"), "content" to JsonPrimitive("<p>New</p>")))
        }
        provider.assertNoEffects()
        provider.denied["/wiki/api/v2/pages/201"] = HttpStatusCode.Forbidden
        provider.use { registry -> rejected(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "section" to JsonPrimitive("2"))) }
    }

    @Test fun `seven contextual edits use original ranges and one native page version`() {
        val provider = Provider()
        val original = "<p>A😀</p><p>B</p><table><tbody><tr><td>C</td><td>D</td><td>E</td><td>F</td><td>G</td></tr></tbody></table>"
        provider.pages.getValue("201").content = original
        val edits = listOf("<p>A😀</p>" to "<p>B</p>", "<p>B</p>" to "<p>A😀 changed</p>") +
            listOf("C", "D", "E", "F", "G").map { "<td>$it</td>" to "<td>$it changed</td>" }
        provider.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "edits" to contextEdits(*edits.reversed().toTypedArray())))
            assertEquals("updated", result.getValue("status").jsonPrimitive.content)
            assertEquals(2, result.getValue("version").jsonPrimitive.int)
            assertEquals("<p>B</p><p>A😀 changed</p><table><tbody><tr><td>C changed</td><td>D changed</td><td>E changed</td><td>F changed</td><td>G changed</td></tr></tbody></table>",
                provider.pages.getValue("201").content)
        }
        assertEquals(1, provider.effects.size)
        assertEquals(1, provider.requests.count { it.method == HttpMethod.Put })
        assertEquals(listOf(1, 2), provider.history.getValue("201").map { it.version })
        val maximum = Provider()
        maximum.pages.getValue("201").content = (1..20).joinToString("") { "<p>Item $it</p>" }
        maximum.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "edits" to contextEdits(*(1..20).map { "<p>Item $it</p>" to "<p>Changed $it</p>" }.toTypedArray())))
            assertEquals("updated", result.getValue("status").jsonPrimitive.content)
            assertEquals((1..20).joinToString("") { "<p>Changed $it</p>" }, maximum.pages.getValue("201").content)
        }
        assertEquals(1, maximum.effects.size)
    }

    @Test fun `invalid contextual batches never partially write or use newly introduced matches`() {
        val original = "<div><p>A😀</p><p>B</p></div><p>윈스턴</p><p>Repeated</p><p>Repeated</p>"
        val valid = strings("old_text" to "<p>A😀</p>", "new_text" to "<p>New</p>")
        val variants = listOf<JsonElement>(
            JsonArray(emptyList()), JsonArray(List(21) { valid }), JsonPrimitive("[]"), JsonObject(emptyMap()), JsonNull,
            JsonArray(listOf(JsonNull)), JsonArray(listOf(JsonPrimitive("not an object"))),
            JsonArray(listOf(strings("old_text" to "" , "new_text" to "new"))),
            JsonArray(listOf(strings("old_text" to "<p>A😀</p>"))),
            JsonArray(listOf(strings("new_text" to "new"))),
            JsonArray(listOf(JsonObject(valid + ("typo" to JsonPrimitive("ignored?"))))),
            JsonArray(listOf(JsonObject(valid + ("old_text" to JsonNull)))),
            JsonArray(listOf(JsonObject(valid + ("old_text" to JsonPrimitive(7))))),
            JsonArray(listOf(JsonObject(valid + ("new_text" to JsonPrimitive(1))))),
            contextEdits("<p>A😀</p>" to "<p>New</p>", "<p>Missing</p>" to "<p>Never</p>"),
            contextEdits("<p>A😀</p>" to "<p>Fresh</p>", "<p>Fresh</p>" to "<p>Chained</p>"),
            contextEdits("<p>A😀</p>" to "<p>First</p>", "<p>A😀</p>" to "<p>Second</p>"),
            contextEdits("<div><p>A😀</p><p>B</p></div>" to "<div>Outer</div>", "<p>B</p>" to "<p>Inner</p>"),
            contextEdits("<p>Repeated</p>" to "<p>Ambiguous</p>"), contextEdits("스턴" to "기절")
        ).map { mapOf("edits" to it) } + listOf(
            mapOf("edits" to JsonArray(listOf(valid)), "content" to JsonPrimitive("<p>Whole</p>")),
            mapOf("edits" to JsonArray(listOf(valid)), "append" to JsonPrimitive("<p>Append</p>")),
            mapOf("edits" to JsonArray(listOf(valid)), "old_text" to JsonPrimitive("<p>B</p>"), "new_text" to JsonPrimitive("<p>Mixed</p>")),
            mapOf("edits" to JsonArray(listOf(valid)), "format" to JsonPrimitive("text")),
            mapOf("edits" to JsonArray(listOf(valid)), "allowReferenceRemoval" to JsonPrimitive(true))
        )
        for (edit in variants) {
            val provider = Provider(); provider.pages.getValue("201").content = original
            provider.use { registry -> rejected(registry, "update_document", JsonObject(mapOf("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1)) + edit)) }
            provider.assertNoEffects()
            assertEquals(original, provider.pages.getValue("201").content)
            assertEquals(1, provider.pages.getValue("201").version)
        }
    }

    @Test fun `whole replacement refuses lost resource identities and multiplicities without echoing them`() {
        val image = "<ac:image ac:width='100'><ri:attachment ri:filename='private-diagram.png'><ri:page ri:content-title='Source' ri:space-key='DOC'/></ri:attachment><ac:caption><p>Caption</p></ac:caption></ac:image>"
        val external = "<ac:image><ri:url ri:value='https://images.example.invalid/a.png?mode=1&amp;scale=2'/></ac:image>"
        val attachment = "<ac:link><ri:attachment ri:filename='private-report.pdf'/></ac:link>"
        val macro = "<ac:structured-macro ac:name='info' ac:macro-id='native-macro-1'><ac:parameter ac:name='title'>Original</ac:parameter><ac:rich-text-body><p>Body</p></ac:rich-text-body></ac:structured-macro>"
        val noId = "<ac:structured-macro ac:name='warning'><ac:rich-text-body><p>Notice</p></ac:rich-text-body></ac:structured-macro>"
        val cases = listOf(
            image to "<p>Gone</p>", image to image.replace("private-diagram.png", "different.png"),
            image to image.replace("ri:space-key='DOC'", "ri:space-key='OTHER'"),
            image to image.replace("ri:content-title='Source'", "ri:content-title='Other'"),
            external to external.replace("a.png", "b.png"), attachment to "<p>Gone</p>",
            macro to macro.replace("native-macro-1", "other-macro"), macro to macro.replace("ac:name='info'", "ac:name='tip'"),
            macro.replace("ac:structured-macro", "ac:macro") to "<p>Legacy macro removed</p>",
            (image + image) to image, (noId + noId) to noId,
            image to "<p>&lt;ac:image&gt;&lt;ri:attachment ri:filename='private-diagram.png'/&gt;&lt;/ac:image&gt;</p>",
            image to "<p><![CDATA[$image]]></p>", image to "<!-- $image --><p>Comment is not a resource</p>"
        )
        for ((original, replacement) in cases) {
            val provider = Provider(); provider.pages.getValue("201").content = original
            provider.use { registry ->
                val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive(replacement)))
                assertEquals("error", result.getValue("status").jsonPrimitive.content)
                assertEquals("reference_removal", result.getValue("code").jsonPrimitive.content)
                val missing = result.getValue("details").jsonObject.getValue("removedReferences").jsonObject
                assertTrue(missing.isNotEmpty() && missing.values.all { it.jsonPrimitive.int > 0 })
                for (privateReference in listOf("private-diagram.png", "private-report.pdf", "images.example.invalid", "native-macro-1"))
                    assertFalse(result.toString().contains(privateReference), "Diagnostic must not reflect private reference identities")
            }
            provider.assertNoEffects(); assertEquals(original, provider.pages.getValue("201").content)
        }
    }

    @Test fun `reference preserving full replacement permits rendering and macro parameter changes`() {
        val original = "<p>Before</p><ac:image ac:width='100'><ri:url ri:value='https://images.example.invalid/a.png?x=1&amp;y=2'/><ac:caption><p>Old caption</p></ac:caption></ac:image>" +
            "<ac:structured-macro ac:name='info' ac:macro-id='macro-1'><ac:parameter ac:name='title'>Old title</ac:parameter><ac:rich-text-body><p>Old body</p></ac:rich-text-body></ac:structured-macro>" +
            "<ac:structured-macro ac:name='warning'><ac:parameter ac:name='title'>Old anonymous title</ac:parameter></ac:structured-macro>"
        val changed = original.replace("Before", "After").replace("width='100'", "width='300'").replace("Old", "New").replace("&amp;", "&#38;")
        val provider = Provider(); provider.pages.getValue("201").content = original
        provider.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive(changed)))
            assertEquals("updated", result.getValue("status").jsonPrimitive.content)
        }
        assertEquals(changed, provider.pages.getValue("201").content); assertEquals(1, provider.effects.size)
    }

    @Test fun `text edits match whole decoded leaf text once and preserve surrounding source bytes`() {
        data class Case(val open: String, val encoded: String, val logical: String, val close: String)
        val cases = listOf(
            Case("<p local-id='native-p' data-keep='yes'>", "A&middot;B", "A·B", "</p>"),
            Case("<td colspan='2' local-id='native-cell'>", "A&#xB7;B", "A·B", "</td>"),
            Case("<th class='exact'>", "A&#183;😀", "A·😀", "</th>"),
            Case("<h2 local-id='heading'>", "한글 😀", "한글 😀", "</h2>"),
            Case("<li value='3'>", "One &amp; two", "One & two", "</li>"),
            Case("<p>", "A&amp;middot;B", "A&middot;B", "</p>"),
            Case("<p>", "&lt;b&gt;literal&lt;/b&gt;", "<b>literal</b>", "</p>"),
            Case("<p data-preserve='whitespace'>", " \tA&nbsp;  😀\nB ", " \tA\u00a0  😀\nB ", "</p>")
        )
        for (case in cases) {
            val provider = Provider()
            val prefix = "<!-- byte exact -->\n<ac:structured-macro ac:name='note' ac:macro-id='untouched'><ac:parameter ac:name='title'>Keep &amp; exact</ac:parameter></ac:structured-macro>"
            val suffix = "\n<p local-id='untouched'>No  normalization &amp; no changes</p>"
            provider.pages.getValue("201").content = prefix + case.open + case.encoded + case.close + suffix
            provider.use { registry ->
                val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive(case.logical), "new_text" to JsonPrimitive("**Changed** 😀"), "format" to JsonPrimitive("text")))
                assertEquals("updated", result.getValue("status").jsonPrimitive.content, case.toString())
                assertEquals(2, result.getValue("version").jsonPrimitive.int)
            }
            assertEquals(prefix + case.open + "**Changed** 😀" + case.close + suffix, provider.pages.getValue("201").content, case.toString())
            assertEquals(1, provider.effects.size)
            assertEquals(HttpMethod.Put, provider.effects.single().first.method)
        }
    }

    @Test fun `default reads do not make duplicate exact whitespace text edits unambiguous`() {
        val provider = Provider()
        val logical = "  Same  text\u00a0 "
        val original = "<p local-id='first'>  Same  text&nbsp; </p><p local-id='second'>  Same  text&#160; </p>"
        provider.pages.getValue("201").content = original
        provider.use { registry ->
            val read = call(registry, "get_document", strings("document" to "201"))
            val projected = read.getValue("content").jsonPrimitive.content
            assertEquals(2, Regex(Regex.escape(logical)).findAll(projected).count())
            val start = projected.indexOf(logical)
            val error = rejected(registry, "update_document", arguments("document" to read.getValue("id"), "version" to read.getValue("version"),
                "old_text" to JsonPrimitive(projected.substring(start, start + logical.length)), "new_text" to JsonPrimitive("Changed"), "format" to JsonPrimitive("text")))
            if (error != null) assertEquals("patch_ambiguous", error.getValue("code").jsonPrimitive.content)
        }
        provider.assertNoEffects()
        assertEquals(original, provider.pages.getValue("201").content)
    }

    @Test fun `text edit refuses fragments rich or inert content implicit ranges and normalized selections before writes`() {
        val cases = listOf(
            "<p>윈스턴</p>" to "스턴",
            "<p data-label='Selected'>Other</p>" to "Selected",
            "<p>Selected <strong>rich</strong></p>" to "Selected rich",
            "<p><![CDATA[Selected]]></p>" to "Selected",
            "<ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[Selected]]></ac:plain-text-body></ac:structured-macro>" to "Selected",
            "<ac:structured-macro ac:name='note'><ac:parameter ac:name='title'><p>Selected</p></ac:parameter></ac:structured-macro>" to "Selected",
            "<script><p>Selected</p></script>" to "Selected",
            "<p>Selected<!--hidden--></p>" to "Selected",
            "<p>Selected<img src='untouched'/></p>" to "Selected",
            "<p>Selected<br>child</br></p>" to "Selected\nchild",
            "<p>Selected" to "Selected",
            "Selected" to "Selected",
            "<p>A&amp;middot;B</p>" to "A·B",
            "<p> Selected </p>" to "Selected",
            "<p>A&nbsp;B</p>" to "A B",
            "<p>A  B</p>" to "A B",
            "<p>A\nB</p>" to "A B",
            "<p>A<br/>B</p>" to "A\r\nB"
        )
        for ((source, selection) in cases) {
            val provider = Provider(); provider.pages.getValue("201").content = source
            provider.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "old_text" to JsonPrimitive(selection), "new_text" to JsonPrimitive("Replacement"), "format" to JsonPrimitive("text"))) }
            provider.assertNoEffects(); assertEquals(source, provider.pages.getValue("201").content)
        }
        val strictStorage = Provider(); strictStorage.pages.getValue("201").content = "<p>A&middot;B</p>"
        strictStorage.use { registry ->
            val error = rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "old_text" to JsonPrimitive("A·B"), "new_text" to JsonPrimitive("Changed")))
            if (error != null) assertEquals("patch_not_found", error.getValue("code").jsonPrimitive.content)
        }
        strictStorage.assertNoEffects()
    }

    @Test fun `text batches select original ranges atomically and reject duplicate decoded or overlapping selections`() {
        val original = "<p local-id='a'>A&middot;B</p><td local-id='b'>B😀</td><p>Unchanged</p>"
        val provider = Provider(); provider.pages.getValue("201").content = original
        provider.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "format" to JsonPrimitive("text"),
                "edits" to contextEdits("B😀" to "<i>literal</i> &", "A·B" to "B😀")))
            assertEquals("updated", result.getValue("status").jsonPrimitive.content)
            assertEquals(2, result.getValue("version").jsonPrimitive.int)
        }
        assertEquals("<p local-id='a'>B😀</p><td local-id='b'>&lt;i&gt;literal&lt;/i&gt; &amp;</td><p>Unchanged</p>", provider.pages.getValue("201").content)
        assertEquals(1, provider.effects.size)
        for (edits in listOf(contextEdits("A·B" to "First", "Absent" to "Last"),
            contextEdits("A·B" to "Introduced", "Introduced" to "Chained"),
            contextEdits("A·B" to "First", "A·B" to "Second"))) {
            val blocked = Provider(); blocked.pages.getValue("201").content = original
            blocked.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "format" to JsonPrimitive("text"), "edits" to edits)) }
            blocked.assertNoEffects(); assertEquals(original, blocked.pages.getValue("201").content)
        }
        val ambiguous = Provider(); ambiguous.pages.getValue("201").content = "<p>A&middot;B</p><p>A·B</p>"
        ambiguous.use { registry ->
            val error = rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "format" to JsonPrimitive("text"), "old_text" to JsonPrimitive("A·B"), "new_text" to JsonPrimitive("Changed")))
            if (error != null) assertEquals("patch_ambiguous", error.getValue("code").jsonPrimitive.content)
        }
        ambiguous.assertNoEffects()
        val stale = Provider(); stale.pages.getValue("201").content = original
        stale.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(2), "format" to JsonPrimitive("text"), "old_text" to JsonPrimitive("A·B"), "new_text" to JsonPrimitive("Changed"))) }
        stale.assertNoEffects()
        val loss = Provider(); loss.pages.getValue("201").content = original
        loss.serializePageBody = { it.replace("Replacement", "Provider changed") }
        loss.use { registry ->
            val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "format" to JsonPrimitive("text"), "old_text" to JsonPrimitive("A·B"), "new_text" to JsonPrimitive("Replacement")))
            assertEquals("unknown", result.getValue("status").jsonPrimitive.content)
        }
        assertEquals(1, loss.effects.size, "Readback disagreement must not replay a text patch")
    }

    @Test fun `exact storage patch edits Unicode table cells and deletion with one native CAS`() {
        val before = "<p data-keep='yes'>&#160;Human 😀</p>\n<table><tbody><tr><td>A😀BC</td><td>unchanged &amp; exact</td></tr></tbody></table><p>remove me</p>"
        for ((old, replacement) in listOf("A😀BC" to "새 값😀", "<td>unchanged &amp; exact</td>" to "<td><strong>새 셀</strong></td>", "<p>remove me</p>" to "<p>문맥을 가진 수정</p>", "<p>remove me</p>" to "")) {
            val provider = Provider(); provider.pages.getValue("201").content = before
            provider.use { registry ->
                val result = callWithBody(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive(old), "new_text" to JsonPrimitive(replacement)))
                assertEquals("updated", result.getValue("status").jsonPrimitive.content)
                assertEquals(2, result.getValue("version").jsonPrimitive.int)
                assertEquals("storage", result.getValue("format").jsonPrimitive.content)
                assertEquals(before.replace(old, replacement), result.getValue("content").jsonPrimitive.content)
            }
            assertEquals(before.replace(old, replacement), provider.pages.getValue("201").content)
            assertEquals("Knowledge 201", provider.pages.getValue("201").title)
            assertEquals(1, provider.effects.size)
            assertEquals(2, provider.history.getValue("201").size)
            assertTrue(provider.requests.none { it.url.parameters["get-draft"] != null })
        }
    }

    @Test fun `complete paragraphs remain editable inside native layouts and rich text macros`() {
        val old = "<p>Human <strong>문맥</strong> 😀</p>"
        for ((prefix, suffix) in listOf(
            "<ac:layout><ac:layout-section ac:type='single'><ac:layout-cell>" to "</ac:layout-cell></ac:layout-section></ac:layout>",
            "<ac:structured-macro ac:name='info'><ac:rich-text-body>" to "</ac:rich-text-body></ac:structured-macro>"
        )) {
            val provider = Provider(); val before = prefix + old + suffix
            provider.pages.getValue("201").content = before
            val replacement = "<p>Changed <strong>문맥</strong> 😀</p>"
            provider.use { registry ->
                val result = callWithBody(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive(old), "new_text" to JsonPrimitive(replacement)))
                assertEquals("updated", result.getValue("status").jsonPrimitive.content)
                assertEquals(prefix + replacement + suffix, result.getValue("content").jsonPrimitive.content)
            }
            assertEquals(1, provider.effects.size)
            assertEquals(prefix + replacement + suffix, provider.pages.getValue("201").content)
        }
    }

    @Test fun `unique substrings attributes partial tags and inline word fragments never become edits`() {
        val unsafe = listOf(
            "<p>윈스턴이 맞았습니다</p>" to "스턴",
            "<p>이 효과는 스턴이 있습니다 윈스턴이 맞았습니다</p>" to "스턴이 있습니다",
            "<p>윈<strong>스턴</strong>이 맞았습니다</p>" to "스턴",
            "<p>윈<strong>스턴</strong>이 맞았습니다</p>" to "<strong>스턴</strong>",
            "<p title='스턴'>단락 전체</p>" to "스턴",
            "<p title='스턴'>단락 전체</p>" to "title='스턴'",
            "<p>전체 문맥</p>" to "<p>",
            "<p>전체 문맥</p>" to "문맥</p>",
            "<script><p>스턴</p></script>" to "<p>스턴</p>",
            "<ac:structured-macro ac:name='code'><ac:plain-text-body><![CDATA[<p>스턴</p>]]></ac:plain-text-body></ac:structured-macro>" to "<p>스턴</p>"
        )
        for ((body, old) in unsafe) {
            val provider = Provider(); provider.pages.getValue("201").content = body
            provider.use { registry ->
                val error = requireNotNull(rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive(old), "new_text" to JsonPrimitive("기절"))))
                assertEquals("patch_context_required", error.getValue("code").jsonPrimitive.content, "Unsafe source selection: $old")
                assertTrue(error.getValue("next").jsonPrimitive.content.isNotBlank())
            }
            provider.assertNoEffects(); assertEquals(body, provider.pages.getValue("201").content)
        }
    }

    @Test fun `patch refuses absent repeated and overlapping matches with useful counts before writing`() {
        for ((body, old, count) in listOf(Triple("<p>source</p>", "missing", 0), Triple("<p>x x</p>", "x", 2), Triple("<p>aaa</p>", "aa", 2))) {
            val provider = Provider(); provider.pages.getValue("201").content = body
            provider.use { registry ->
                val error = requireNotNull(rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive(old), "new_text" to JsonPrimitive("replacement"))))
                assertEquals(if (count == 0) "patch_not_found" else "patch_ambiguous", error.getValue("code").jsonPrimitive.content)
                assertEquals(count, error.getValue("details").jsonObject.getValue("matchCount").jsonPrimitive.int)
                assertTrue(error.getValue("next").jsonPrimitive.content.isNotBlank())
            }
            provider.assertNoEffects(); assertEquals(body, provider.pages.getValue("201").content)
        }
    }

    @Test fun `patch requires one complete typed edit mode and does not accept empty old text`() {
        val modes = listOf(
            emptyMap(), mapOf("old_text" to JsonPrimitive("Knowledge")), mapOf("new_text" to JsonPrimitive("change")),
            mapOf("old_text" to JsonPrimitive(""), "new_text" to JsonPrimitive("change")),
            mapOf("content" to JsonPrimitive("<p>full</p>"), "old_text" to JsonPrimitive("Knowledge"), "new_text" to JsonPrimitive("change")),
            mapOf("content" to JsonPrimitive("<p>full</p>"), "old_text" to JsonPrimitive("Knowledge")),
            mapOf("old_text" to JsonPrimitive(1), "new_text" to JsonPrimitive("change")),
            mapOf("old_text" to JsonPrimitive("Knowledge"), "new_text" to JsonPrimitive(false)),
            mapOf("old_text" to JsonNull, "new_text" to JsonPrimitive("change"))
        )
        for (edit in modes) {
            val provider = Provider()
            provider.use { registry ->
                rejected(registry, "update_document", JsonObject(mapOf("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1)) + edit))
            }
            provider.assertNoEffects()
        }
    }

    @Test fun `patch stale version and outside root fail before provider mutation or foreign storage`() {
        val stale = Provider(); stale.pages.getValue("201").version = 3
        stale.use { registry ->
            val error = requireNotNull(rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "old_text" to JsonPrimitive("Knowledge 201"), "new_text" to JsonPrimitive("Changed"))))
            assertEquals("version_conflict", error.getValue("code").jsonPrimitive.content)
            val details = error.getValue("details").jsonObject
            assertEquals(1, details.getValue("expectedVersion").jsonPrimitive.int); assertEquals(3, details.getValue("currentVersion").jsonPrimitive.int)
        }
        stale.assertNoEffects()
        val outside = Provider(); outside.pages.getValue("202").content = "OUTSIDE_ROOT_SENTINEL"
        outside.use { registry ->
            val error = rejected(registry, "update_document", arguments("document" to JsonPrimitive("202"), "version" to JsonPrimitive(1),
                "old_text" to JsonPrimitive("anything"), "new_text" to JsonPrimitive("change")))
            assertFalse(error.toString().contains("OUTSIDE_ROOT_SENTINEL"))
        }
        outside.assertNoEffects(); assertFalse(outside.storageRead("202"))
    }

    @Test fun `native patch errors retain status and safe provider reason without unrelated response fields`() {
        for ((status, reason) in listOf(HttpStatusCode.BadRequest to "Invalid storage XHTML: mismatched closing tag", HttpStatusCode.Forbidden to "User does not have permission to update this page")) {
            val provider = Provider(); provider.deniedMutations["/wiki/api/v2/pages/201"] = status
            provider.failureBody = buildJsonObject {
                put("code", "INVALID_STORAGE"); put("message", reason)
                put("body", "OUTSIDE_ROOT_SENTINEL"); put("authorization", "Bearer provider-secret")
                put("credentials", credentials.apiToken)
            }.toString()
            provider.use { registry ->
                val error = requireNotNull(rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive("Knowledge 201"), "new_text" to JsonPrimitive("Changed"))))
                assertEquals("http_${status.value}", error.getValue("code").jsonPrimitive.content)
                val rendered = error.toString()
                assertTrue(rendered.contains(if (status == HttpStatusCode.BadRequest) "storage" else "permission", ignoreCase = true), rendered)
                assertTrue(error.getValue("next").jsonPrimitive.content.isNotBlank())
                for (sensitive in listOf("OUTSIDE_ROOT_SENTINEL", "provider-secret", credentials.apiToken)) assertFalse(rendered.contains(sensitive))
                assertTrue(rendered.length < 3000)
            }
            assertEquals(1, provider.requests.count { it.method == HttpMethod.Put }); assertTrue(provider.effects.isEmpty())
        }
    }

    @Test fun `patch revalidates native account and unknown readback names changed fields without replay`() {
        val changed = Provider(); var accountReads = 0
        changed.beforeRequest = { request ->
            if (request.url.encodedPath == "/wiki/rest/api/user/current" && ++accountReads == 2) changed.account = "changed-before-patch-effect"
        }
        changed.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
            "old_text" to JsonPrimitive("Knowledge 201"), "new_text" to JsonPrimitive("Changed"))) }
        assertTrue(accountReads >= 2); changed.assertNoEffects()
        for ((field, replacement) in listOf("content" to "<p>Human altered saved content</p>", "message" to "Other writer change reason")) {
            val uncertain = Provider()
            uncertain.beforeRequest = { request ->
                if (uncertain.effects.isNotEmpty() && request.method == HttpMethod.Get && request.url.encodedPath == "/wiki/api/v2/pages/201") {
                    if (field == "content") uncertain.pages.getValue("201").content = replacement
                    else uncertain.pages.getValue("201").message = replacement
                }
            }
            uncertain.use { registry ->
                val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                    "old_text" to JsonPrimitive("Knowledge 201"), "new_text" to JsonPrimitive("Changed")))
                assertEquals("unknown", result.getValue("status").jsonPrimitive.content)
                val fields = result.getValue("details").jsonObject.getValue("mismatchFields").jsonArray.map { it.jsonPrimitive.content }
                assertTrue(field in fields, "Unknown must identify the failed verification field: $result")
                assertFalse(result.toString().contains(replacement))
            }
            assertEquals(1, uncertain.effects.size); assertEquals(1, uncertain.requests.count { it.method == HttpMethod.Put })
        }
    }

    @Test fun `stale version and native CAS conflict never overwrite the winner`() {
        val stale = Provider(); stale.pages.getValue("201").version = 2
        stale.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive("<p>Loser</p>"))) }
        stale.assertNoEffects(); assertEquals("<p>Knowledge 201</p>", stale.pages.getValue("201").content)
        val conflict = Provider(); conflict.conflictOnUpdate = true
        conflict.use { registry -> rejected(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive("<p>Loser</p>"))) }
        assertEquals(1, conflict.requests.count { it.method == HttpMethod.Put }); assertEquals(1, conflict.pages.getValue("201").version)
        assertTrue(conflict.effects.isEmpty(), "Provider CAS rejected the write before committing")
    }

    @Test fun `lost create response committed or absent stays unknown and never automatically replays`() {
        for (committed in listOf(true, false)) {
            val provider = Provider(); provider.loseWriteResponse = true; provider.commitLostWrite = committed
            provider.use { registry ->
                val result = call(registry, "create_document", strings("parent" to "210", "title" to "Uncertain create", "content" to "<p>Evidence</p>"))
                assertEquals("unknown", result.getValue("status").jsonPrimitive.content)
                assertTrue(result.toString().contains("210"), "Keep original target context for a read-only reconciliation")
                assertFalse(result.toString().contains(credentials.apiToken))
            }
            assertEquals(1, provider.requests.count { it.method == HttpMethod.Post }); assertEquals(1, provider.effects.size)
            assertEquals(committed, provider.pages.containsKey("250"))
        }
    }

    @Test fun `lost update response is verified only by exact unique native readback and never automatically replays`() {
        for (mode in listOf("verified", "absent", "mismatched", "unreadable")) {
            val provider = Provider(); provider.loseWriteResponse = true; provider.commitLostWrite = mode != "absent"
            provider.beforeRequest = { request ->
                if (provider.effects.isNotEmpty() && request.method == HttpMethod.Get && request.url.encodedPath == "/wiki/api/v2/pages/201") {
                    if (mode == "mismatched") provider.pages.getValue("201").message = "Another writer produced the same body"
                    if (mode == "unreadable") provider.denied[request.url.encodedPath] = HttpStatusCode.Forbidden
                }
            }
            provider.use { registry ->
                val result = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive("<p>Uncertain update</p>")))
                assertEquals(if (mode == "verified") "updated" else "unknown", result.getValue("status").jsonPrimitive.content)
                assertTrue(result.toString().contains("201"), "Retain native target context for explicit read-only inspection")
            }
            assertEquals(1, provider.requests.count { it.method == HttpMethod.Put }); assertEquals(1, provider.effects.size)
            assertEquals(if (mode == "absent") 1 else 2, provider.pages.getValue("201").version)
            val message = provider.effects.single().second.getValue("version").jsonObject.getValue("message").jsonPrimitive.content
            assertTrue(message.isNotBlank()); assertNotEquals("wiki update document", message, "A shared constant cannot prove which writer committed the version")
        }
    }

    @Test fun `native history pages are bounded and exact past bodies retain original version attribution`() {
        val provider = Provider(); provider.nativeHistory()
        provider.use { registry ->
            val first = callDetailed(registry, "list_document_versions", arguments("document" to JsonPrimitive("201"), "limit" to JsonPrimitive(1)))
            assertEquals("listed", first.getValue("status").jsonPrimitive.content)
            assertEquals(listOf(3), first.getValue("versions").jsonArray.map { it.jsonObject.getValue("number").jsonPrimitive.int })
            assertTrue(first.getValue("hasMore").jsonPrimitive.boolean); assertFalse(first.getValue("complete").jsonPrimitive.boolean)
            val cursor = first.getValue("cursor")
            assertEquals(1, provider.requests.count { it.url.encodedPath == "/wiki/api/v2/pages/201/versions" }, "A single bounded page must not eager-load all native history")
            val second = callDetailed(registry, "list_document_versions", arguments("document" to JsonPrimitive("201"), "limit" to JsonPrimitive(1), "cursor" to cursor))
            assertEquals(2, second.getValue("versions").jsonArray.single().jsonObject.getValue("number").jsonPrimitive.int)
            val past = callDetailed(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "format" to JsonPrimitive("storage")))
            assertEquals("read", past.getValue("status").jsonPrimitive.content)
            assertEquals(1, past.getValue("version").jsonPrimitive.int); assertEquals("<p>Original native version</p>", past.getValue("content").jsonPrimitive.content)
            assertEquals("original-author", past.getValue("authorId").jsonPrimitive.content)
            val readable = callDetailed(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1)))
            assertEquals("markdown", readable.getValue("format").jsonPrimitive.content)
            assertEquals("Original native version", readable.getValue("content").jsonPrimitive.content.trim())
            assertEquals(past.getValue("authorId"), readable.getValue("authorId"))
        }
        provider.assertNoEffects()
    }

    @Test fun `history cannot bypass current root access identity version or cursor boundaries`() {
        for (mode in listOf("moved", "denied", "wrong-id", "wrong-version", "foreign-next", "cycle", "malformed", "foreign-cursor")) {
            val provider = Provider(); provider.nativeHistory()
            when (mode) {
                "moved" -> provider.pages.getValue("201").parent = "202"
                "denied" -> provider.denied["/wiki/api/v2/pages/201"] = HttpStatusCode.Forbidden
                "wrong-id", "wrong-version" -> provider.transform = { request, body ->
                    if (request.url.parameters["version"] == "1") JsonObject(body + if (mode == "wrong-id") mapOf("id" to JsonPrimitive("202"))
                        else mapOf("version" to buildJsonObject { put("number", 2) })) else body
                }
                "foreign-next" -> provider.historyNext = "https://other.example/wiki/api/v2/pages/201/versions?cursor=1"
                "cycle" -> provider.historyNext = "/wiki/api/v2/pages/201/versions?limit=1"
                "malformed" -> provider.transform = { request, body -> if (request.url.encodedPath.endsWith("/versions")) JsonObject(body + ("results" to JsonPrimitive("not-an-array"))) else body }
            }
            provider.use { registry ->
                when (mode) {
                    "foreign-next", "cycle", "malformed" -> rejected(registry, "list_document_versions", arguments("document" to JsonPrimitive("201"), "limit" to JsonPrimitive(1)))
                    "foreign-cursor" -> rejected(registry, "list_document_versions", strings("document" to "201", "cursor" to "https://other.example/wiki/api/v2/pages/201/versions?cursor=1"))
                    else -> rejected(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1)))
                }
            }
            if (mode in setOf("moved", "denied")) assertTrue(provider.requests.none { it.url.parameters["version"] != null }, "Current authorization must precede historical access")
            provider.assertNoEffects()
        }
    }

    @Test fun `Confluence comment list retains footer inline replies original author and actual editor`() {
        val provider = Provider()
        provider.use { registry ->
            val observed = mutableListOf<JsonObject>(); var cursor: JsonElement? = null; var pages = 0
            do {
                val data = buildJsonObject { put("target", "201"); cursor?.let { put("cursor", it) } }
                val result = callDetailed(registry, "list_comments", data)
                assertEquals("listed", result.getValue("status").jsonPrimitive.content)
                observed += result.getValue("comments").jsonArray.map { it.jsonObject }
                cursor = if (result.getValue("hasMore").jsonPrimitive.boolean) result.getValue("cursor") else null
                assertTrue(++pages <= 8, "Native comment pagination must terminate")
            } while (cursor != null)
            val comments = observed.associateBy { it.getValue("id").jsonPrimitive.content }
            assertTrue(comments.keys.containsAll(setOf("301", "302", "303", "311")))
            assertEquals("another-author", comments.getValue("302").getValue("authorId").jsonPrimitive.content)
            assertEquals("native-account", comments.getValue("302").getValue("editorId").jsonPrimitive.content)
            assertEquals("inline", comments.getValue("311").getValue("kind").jsonPrimitive.content)
            val thread = callDetailed(registry, "list_comments", strings("target" to "footer:301"))
            val selected = thread.getValue("comment").jsonObject
            assertEquals("301", selected.getValue("id").jsonPrimitive.content)
            assertEquals("<p>Comment 301</p>", selected.getValue("body").jsonPrimitive.content)
            assertEquals("native-account", selected.getValue("authorId").jsonPrimitive.content)
            assertEquals(1, selected.getValue("version").jsonPrimitive.int)
            val reply = thread.getValue("comments").jsonArray.single().jsonObject
            assertEquals("305", reply.getValue("id").jsonPrimitive.content)
            assertEquals("301", (reply["parentId"] ?: reply.getValue("parentCommentId")).jsonPrimitive.content)
        }
        assertTrue(provider.requests.any { it.url.encodedPath == "/wiki/api/v2/footer-comments/302/versions/1" })
        provider.assertNoEffects()
    }

    @Test fun `selected footer and inline comments return their own body even when there are no replies`() {
        val provider = Provider()
        provider.use { registry ->
            for (ref in listOf("footer:302", "inline:311")) {
                val result = callDetailed(registry, "list_comments", strings("target" to ref))
                val selected = result.getValue("comment").jsonObject
                val id = ref.substringAfter(':')
                assertEquals(id, selected.getValue("id").jsonPrimitive.content)
                assertEquals(ref, selected.getValue("reference").jsonPrimitive.content)
                assertEquals("<p>Comment $id</p>", selected.getValue("body").jsonPrimitive.content)
                assertEquals(provider.comments.getValue(id).originalAuthor, selected.getValue("authorId").jsonPrimitive.content)
                assertEquals(provider.comments.getValue(id).editor, selected.getValue("editorId").jsonPrimitive.content)
                assertEquals(0, result.getValue("comments").jsonArray.size)
                assertTrue(result.getValue("complete").jsonPrimitive.boolean)
                assertFalse(result.getValue("hasMore").jsonPrimitive.boolean)
                assertTrue(result.getValue("threadScope").jsonPrimitive.content.contains("direct"))
            }
        }
        provider.assertNoEffects()
    }

    @Test fun `comment pagination is bounded rather than taking a complete inventory before truncation`() {
        val provider = Provider()
        provider.use { registry ->
            val first = call(registry, "list_comments", arguments("target" to JsonPrimitive("201"), "limit" to JsonPrimitive(1)))
            assertEquals("listed", first.getValue("status").jsonPrimitive.content)
            assertEquals(1, first.getValue("comments").jsonArray.size)
            assertTrue(first.getValue("hasMore").jsonPrimitive.boolean); assertFalse(first.getValue("complete").jsonPrimitive.boolean)
            assertTrue(provider.requests.none { it.url.parameters["cursor"] != null }, "Do not traverse native continuation merely to truncate an inventory")
            val cursor = first.getValue("cursor")
            val next = call(registry, "list_comments", arguments("target" to JsonPrimitive("201"), "limit" to JsonPrimitive(1), "cursor" to cursor))
            assertEquals(1, next.getValue("comments").jsonArray.size)
            assertNotEquals(first.getValue("comments").jsonArray.single().jsonObject.getValue("id"), next.getValue("comments").jsonArray.single().jsonObject.getValue("id"))
            rejected(registry, "list_comments", arguments("target" to JsonPrimitive("221"), "limit" to JsonPrimitive(1), "cursor" to cursor))
        }
        provider.assertNoEffects()
    }

    @Test fun `own native comment update and reply preserve author parent and thread scope`() {
        val provider = Provider()
        provider.use { registry ->
            val updated = call(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:303"), "version" to JsonPrimitive(2), "text" to JsonPrimitive("My corrected statement")))
            assertEquals("updated", updated.getValue("status").jsonPrimitive.content)
            assertEquals(3, updated.getValue("version").jsonPrimitive.int)
            val reply = call(registry, "create_comment", strings("target" to "201", "text" to "Reply in the same thread", "replyTo" to "footer:301"))
            assertEquals("created", reply.getValue("status").jsonPrimitive.content)
            assertEquals("301", provider.comments.getValue("350").parent)
            assertEquals("201", provider.comments.getValue("350").page)
            assertEquals("native-account", provider.comments.getValue("350").originalAuthor)
        }
        assertEquals(listOf(HttpMethod.Put, HttpMethod.Post), provider.effects.map { it.first.method })
        assertEquals("native-account", provider.comments.getValue("303").originalAuthor)
        val reads = provider.requests.filter { it.method == HttpMethod.Get && it.url.encodedPath == "/wiki/api/v2/footer-comments/303" }
        assertNull(reads.first().url.parameters["body-format"], "Resolve the native parent using metadata before authorizing its body")
        assertTrue(reads.any { it.url.parameters["body-format"] == "storage" }, "An admitted own edit must still read the real comment storage")
    }

    @Test fun `native comment entity serialization verifies create and update without replay`() {
        for (updating in listOf(false, true)) {
            val provider = Provider()
            val text = "Planning · develop & review Ω"
            val serialized = "Planning &middot; develop &amp; review &#937;"
            provider.serializeCommentBody = { value ->
                value.replace("&", "&amp;").replace("·", "&middot;").replace("Ω", "&#937;")
            }
            provider.use { registry ->
                val result = if (updating) callDetailed(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:303"), "version" to JsonPrimitive(2), "text" to JsonPrimitive(text)))
                    else callDetailed(registry, "create_comment", strings("target" to "201", "text" to text, "replyTo" to "footer:301"))
                assertEquals(if (updating) "updated" else "created", result.getValue("status").jsonPrimitive.content)
                assertEquals(if (updating) 3 else 1, result.getValue("version").jsonPrimitive.int)
                assertEquals("native-account", result.getValue("authorId").jsonPrimitive.content)
                assertEquals("native-account", result.getValue("editorId").jsonPrimitive.content)
            }
            val saved = provider.comments.getValue(if (updating) "303" else "350")
            assertEquals(serialized, saved.content, "Verify the provider's persisted serialization, not the submitted bytes")
            assertEquals("201", saved.page)
            assertEquals(if (updating) null else "301", saved.parent)
            assertEquals(listOf(if (updating) HttpMethod.Put else HttpMethod.Post), provider.effects.map { it.first.method })
            assertEquals(1, provider.requests.count { it.method != HttpMethod.Get }, "Successful readback must not replay a native write")
        }
    }

    @Test fun `comment readback still rejects changed human text and page link version hints without replay`() {
        for (updating in listOf(false, true)) for (change in listOf("human-text", "page-hint")) {
            val provider = Provider()
            val text = if (change == "human-text") "Planning · develop & review" else
                "<ac:link><ri:page ri:content-title=\"Knowledge\" ri:version-at-save=\"1\" /></ac:link>"
            provider.serializeCommentBody = { value ->
                if (change == "human-text") value.replace("develop", "deploy")
                else value.replace("ri:version-at-save=\"1\"", "ri:version-at-save=\"2\"")
            }
            provider.use { registry ->
                val result = if (updating) call(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:303"), "version" to JsonPrimitive(2), "text" to JsonPrimitive(text)))
                    else call(registry, "create_comment", strings("target" to "201", "text" to text))
                assertEquals("unknown", result.getValue("status").jsonPrimitive.content, "$change must not be treated as comment serialization identity")
            }
            assertNotEquals(text, provider.comments.getValue(if (updating) "303" else "350").content)
            assertEquals(listOf(if (updating) HttpMethod.Put else HttpMethod.Post), provider.effects.map { it.first.method })
            assertEquals(1, provider.requests.count { it.method != HttpMethod.Get }, "An uncertain native write must never be replayed")
        }
    }

    @Test fun `own inline native comment edit and reply preserve inline kind and original selection`() {
        val provider = Provider()
        provider.use { registry ->
            val updated = call(registry, "update_comment", arguments("comment" to JsonPrimitive("inline:311"), "version" to JsonPrimitive(1), "text" to JsonPrimitive("Corrected inline statement")))
            assertEquals("updated", updated.getValue("status").jsonPrimitive.content)
            assertEquals(2, updated.getValue("version").jsonPrimitive.int)
            val reply = call(registry, "create_comment", strings("target" to "201", "text" to "Reply to selected inline statement", "replyTo" to "inline:311"))
            assertEquals("created", reply.getValue("status").jsonPrimitive.content)
        }
        assertEquals("inline", provider.comments.getValue("350").kind)
        assertEquals("311", provider.comments.getValue("350").parent)
        assertEquals("201", provider.comments.getValue("350").page)
        assertEquals("marker-311", provider.comments.getValue("311").json().getValue("properties").jsonObject.getValue("inlineMarkerRef").jsonPrimitive.content)
        assertEquals(listOf("/wiki/api/v2/inline-comments/311", "/wiki/api/v2/inline-comments"), provider.effects.map { it.first.url.encodedPath })
        val reads = provider.requests.filter { it.method == HttpMethod.Get && it.url.encodedPath == "/wiki/api/v2/inline-comments/311" }
        assertNull(reads.first().url.parameters["body-format"], "Inline parent discovery must not download storage before page admission")
        assertTrue(reads.any { it.url.parameters["body-format"] == "storage" })
    }

    @Test fun `outside-root footer inline and nested comment references discover metadata without private body or author reads`() {
        for (reference in listOf("footer:304", "inline:314", "footer:306")) for (operation in listOf("list_comments", "update_comment")) {
            val provider = Provider()
            provider.comments["314"] = Comment("314", "202", kind = "inline")
            provider.comments["306"] = Comment("306", "202", parent = "304")
            provider.use { registry ->
                val input = if (operation == "list_comments") strings("target" to reference)
                    else arguments("comment" to JsonPrimitive(reference), "version" to JsonPrimitive(1), "text" to JsonPrimitive("An own comment is still outside registered roots"))
                rejected(registry, operation, input)
            }
            val expected = reference.substringAfter(':')
            assertTrue(provider.requests.any { it.url.encodedPath.endsWith("-comments/$expected") }, "Must resolve the actual native comment before denying its page")
            if (expected == "306") assertTrue(provider.requests.any { it.url.encodedPath == "/wiki/api/v2/footer-comments/304" }, "Nested parent discovery must also remain metadata-only")
            assertTrue(provider.requests.all { it.url.parameters["body-format"] == null }, "An unauthorized comment or page body must never be requested")
            assertTrue(provider.requests.none { "/versions" in it.url.encodedPath || it.url.encodedPath.endsWith("/children") }, "Admission must precede original-author history and reply bodies")
            assertTrue(provider.requests.none { it.url.encodedPath == "/wiki/rest/api/user/current" || it.url.encodedPath == "/rest/api/3/myself" })
            provider.assertNoEffects()
        }
    }

    @Test fun `native permitted comment edit retains unknown original author instead of inventing identity`() {
        for (failure in listOf(HttpStatusCode.Forbidden, HttpStatusCode.NotFound)) {
            val provider = Provider(); provider.denied["/wiki/api/v2/footer-comments/302/versions/1"] = failure
            provider.use { registry ->
                val listed = callDetailed(registry, "list_comments", strings("target" to "201"))
                assertEquals("listed", listed.getValue("status").jsonPrimitive.content)
                val comment = listed.getValue("comments").jsonArray.map { it.jsonObject }.single { it.getValue("id") == JsonPrimitive("302") }
                assertEquals(JsonNull, comment.getValue("authorId"), "A missing originator is unknown even when the authenticated account is latest editor")
                assertEquals("native-account", comment.getValue("editorId").jsonPrimitive.content)
                val updated = callDetailed(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:302"), "version" to JsonPrimitive(2), "text" to JsonPrimitive("Native permission authorizes the edit, not authorship")))
                assertEquals("updated", updated.getValue("status").jsonPrimitive.content)
                assertEquals(JsonNull, updated.getValue("authorId"))
                assertEquals("native-account", updated.getValue("editorId").jsonPrimitive.content)
                assertEquals(3, updated.getValue("version").jsonPrimitive.int)
            }
            assertEquals(1, provider.effects.size)
            assertEquals("another-author", provider.comments.getValue("302").originalAuthor)
        }
    }

    @Test fun `native permission allows another authors comment edit while foreign replies and stale versions cannot mutate`() {
        val allowed = Provider()
        allowed.use { registry ->
            val updated = callDetailed(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:302"), "version" to JsonPrimitive(2), "text" to JsonPrimitive("A permitted common knowledge edit")))
            assertEquals("updated", updated.getValue("status").jsonPrimitive.content)
            assertEquals("another-author", updated.getValue("authorId").jsonPrimitive.content)
            assertEquals("native-account", updated.getValue("editorId").jsonPrimitive.content)
        }
        assertEquals(1, allowed.effects.size)
        assertEquals("another-author", allowed.comments.getValue("302").originalAuthor)
        for (mode in listOf("foreign-thread", "stale", "ambiguous-comment", "inline-foreign")) {
            val provider = Provider()
            provider.use { registry ->
                when (mode) {
                    "foreign-thread" -> rejected(registry, "create_comment", strings("target" to "201", "text" to "Wrong thread", "replyTo" to "footer:304"))
                    "stale" -> rejected(registry, "update_comment", arguments("comment" to JsonPrimitive("footer:303"), "version" to JsonPrimitive(1), "text" to JsonPrimitive("Stale edit")))
                    "ambiguous-comment" -> rejected(registry, "update_comment", arguments("comment" to JsonPrimitive("My last comment"), "version" to JsonPrimitive(1), "text" to JsonPrimitive("No guessed target")))
                    else -> rejected(registry, "create_comment", strings("target" to "201", "text" to "Wrong kind/thread", "replyTo" to "inline:304"))
                }
            }
            provider.assertNoEffects()
        }
    }

    @Test fun `native forbidden common writes are not bypassed or retried`() {
        for ((operation, path, input) in listOf(
            Triple("update_document", "/wiki/api/v2/pages/201", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1), "content" to JsonPrimitive("<p>Denied by Confluence</p>"))),
            Triple("update_comment", "/wiki/api/v2/footer-comments/302", arguments("comment" to JsonPrimitive("footer:302"), "version" to JsonPrimitive(2), "text" to JsonPrimitive("Denied by Confluence")))
        )) {
            val provider = Provider(); provider.deniedMutations[path] = HttpStatusCode.Forbidden
            provider.use { registry -> rejected(registry, operation, input) }
            assertEquals(1, provider.requests.count { it.method == HttpMethod.Put }, operation)
            assertTrue(provider.effects.isEmpty())
            assertEquals(1, provider.pages.getValue("201").version)
            assertEquals(2, provider.comments.getValue("302").version)
        }
    }

    @Test fun `new inline comment anchors the exact selected occurrence and verifies the saved marker`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<p>same and same</p><p>Alpha beta &amp; gamma</p><p>same</p>"
        provider.use { registry ->
            val unique = callWithBody(registry, "create_comment", strings("target" to "201", "text" to "Unique note", "format" to "text", "selection" to "beta & gamma"))
            assertEquals("created", unique.getValue("status").jsonPrimitive.content)
            assertEquals("inline:350", unique.getValue("reference").jsonPrimitive.content)
            assertEquals("beta & gamma", unique.getValue("inlineContext").jsonObject.getValue("inlineOriginalSelection").jsonPrimitive.content)
        }
        val (request, payload) = provider.effects.single()
        assertEquals("/wiki/api/v2/inline-comments", request.url.encodedPath)
        assertEquals("201", payload.getValue("pageId").jsonPrimitive.content)
        assertFalse(payload.containsKey("parentCommentId"))
        assertEquals(buildJsonObject { put("textSelection", "beta & gamma"); put("textSelectionMatchIndex", 0); put("textSelectionMatchCount", 1) },
            payload.getValue("inlineCommentProperties"))
        assertEquals(2, provider.pages.getValue("201").version, "The provider's own marker revision must not turn a verified create into unknown")
        assertEquals(1, provider.requests.count { it.method != HttpMethod.Get })

        val unversioned = Provider(); unversioned.inlineVersionStep = 0
        unversioned.use { registry ->
            assertEquals("created", call(registry, "create_comment", strings("target" to "201", "text" to "Marker without a page revision", "selection" to "Knowledge 201")).getValue("status").jsonPrimitive.content)
        }
        assertEquals(1, unversioned.pages.getValue("201").version)

        val repeated = Provider()
        repeated.pages.getValue("201").content = "<p>same and same</p><p>same</p>"
        repeated.use { registry ->
            val ambiguous = rejected(registry, "create_comment", strings("target" to "201", "text" to "Which one", "selection" to "same"))!!
            assertEquals("selection_ambiguous", ambiguous.getValue("code").jsonPrimitive.content)
            assertEquals(3, ambiguous.getValue("details").jsonObject.getValue("matchCount").jsonPrimitive.int)
            for (occurrence in listOf(0, 4)) assertEquals("selection_occurrence_out_of_range", rejected(registry, "create_comment",
                arguments("target" to JsonPrimitive("201"), "text" to JsonPrimitive("Out of range"), "selection" to JsonPrimitive("same"), "occurrence" to JsonPrimitive(occurrence)))!!.getValue("code").jsonPrimitive.content)
            repeated.assertNoEffects()
            val third = call(registry, "create_comment", arguments("target" to JsonPrimitive("201"), "text" to JsonPrimitive("Third"), "selection" to JsonPrimitive("same"), "occurrence" to JsonPrimitive(3)))
            assertEquals("created", third.getValue("status").jsonPrimitive.content)
            assertFalse(third.getValue("inlineContext").jsonObject.containsKey("inlineOriginalSelection"), "Selected content is returned only with includeBody")
        }
        val properties = repeated.effects.single().second.getValue("inlineCommentProperties").jsonObject
        assertEquals(2, properties.getValue("textSelectionMatchIndex").jsonPrimitive.int)
        assertEquals(3, properties.getValue("textSelectionMatchCount").jsonPrimitive.int)
        assertEquals("<p>same and same</p><p><ac:inline-comment-marker ac:ref=\"marker-350\">same</ac:inline-comment-marker></p>", repeated.pages.getValue("201").content)
    }

    @Test fun `inline selection that cannot be anchored unambiguously is rejected before any write`() {
        val macro = "<ac:structured-macro ac:name=\"info\"><ac:rich-text-body><p>hidden term</p></ac:rich-text-body></ac:structured-macro><p>hidden term</p>"
        val cases = listOf(
            Triple("<p>Plain text</p>", "missing", "selection_not_found"),
            Triple("<p>Hello <strong>bold</strong> world</p>", "Hello bold", "selection_context_required"),
            Triple("<p>aaa</p>", "aa", "selection_context_required"),
            Triple(macro, "hidden term", "selection_context_required"),
            Triple("<ac:macro ac:name=\"info\"><ac:rich-text-body><p>legacy term</p></ac:rich-text-body></ac:macro>", "legacy term", "selection_context_required"),
            Triple("<ac:macro ac:name=\"info\"><ac:rich-text-body><p>legacy term</p></ac:rich-text-body></ac:macro><p>legacy term</p>", "legacy term", "selection_context_required"),
            Triple("<p>seen <ac:inline-comment-marker ac:ref=\"old\">term</ac:inline-comment-marker> and term</p>", "term", "selection_context_required"),
            Triple("<ac:structured-macro ac:name=\"code\"><ac:plain-text-body><![CDATA[literal]]></ac:plain-text-body></ac:structured-macro><p>literal</p>", "literal", "selection_context_required"),
            Triple("<p><![CDATA[raw]]></p><p>raw</p>", "raw", "selection_context_required"),
            Triple("<ac:task-list><ac:task><ac:task-id>1</ac:task-id><ac:task-status>incomplete</ac:task-status><ac:task-body>work is incomplete</ac:task-body></ac:task></ac:task-list><p>still incomplete</p>", "incomplete", "selection_context_required"),
            Triple("<ac:task-list><ac:task><ac:task-body>unique task text</ac:task-body></ac:task></ac:task-list>", "unique task text", "selection_context_required"),
            Triple("<ac:task-list><ac:task><ac:task-body>task text</ac:task-body></ac:task></ac:task-list><p>task text</p>", "task text", "selection_context_required"),
            Triple("<ac:decision-list><ac:decision>decision text</ac:decision></ac:decision-list>", "decision text", "selection_context_required"),
            Triple("<pre><code>literal code</code></pre><p>literal code</p>", "literal code", "selection_context_required"),
            Triple("<ac:adf-extension><ac:adf-node type=\"decision-list\"><ac:adf-content>Decided X</ac:adf-content></ac:adf-node><ac:adf-fallback><p>Decided X</p></ac:adf-fallback></ac:adf-extension><p>Decided X</p>", "Decided X", "selection_context_required"),
            Triple("<p>Plain text</p>", " Plain", "invalid_selection"),
            Triple("<p>Plain text</p>", "Plain\ntext", "invalid_selection"),
            Triple("<p>Plain\u2028text</p>", "Plain\u2028text", "invalid_selection"),
            Triple("<p>Plain\u2029text</p>", "Plain\u2029text", "invalid_selection"),
            Triple("<p>Plain text</p>", " ", "invalid_selection"),
            Triple("<p>Plain text</p>", "x".repeat(1001), "invalid_selection"))
        for ((content, selection, code) in cases) {
            val provider = Provider()
            provider.pages.getValue("201").content = content
            provider.use { registry ->
                assertEquals(code, rejected(registry, "create_comment", strings("target" to "201", "text" to "Never written", "selection" to selection))!!.getValue("code").jsonPrimitive.content, selection.take(20))
            }
            if (code == "invalid_selection") assertTrue(provider.requests.isEmpty(), "Malformed selections must fail before remote reads")
            provider.assertNoEffects()
            assertEquals(content, provider.pages.getValue("201").content)
        }
        val legacyDuplicate = Provider()
        legacyDuplicate.pages.getValue("201").content = macro.replace("ac:structured-macro", "ac:macro")
        legacyDuplicate.use { registry ->
            assertEquals("selection_context_required", rejected(registry, "create_comment", arguments(
                "target" to JsonPrimitive("201"), "text" to JsonPrimitive("Never written"),
                "selection" to JsonPrimitive("hidden term"), "occurrence" to JsonPrimitive(2)))!!.getValue("code").jsonPrimitive.content)
        }
        legacyDuplicate.assertNoEffects()
        val provider = Provider()
        provider.use { registry ->
            assertEquals("invalid_selection", rejected(registry, "create_comment", arguments("target" to JsonPrimitive("201"), "text" to JsonPrimitive("No selection"), "occurrence" to JsonPrimitive(1)))!!.getValue("code").jsonPrimitive.content)
            assertEquals("invalid_selection", rejected(registry, "create_comment", strings("target" to "201", "text" to "Reply", "replyTo" to "inline:311", "selection" to "Knowledge"))!!.getValue("code").jsonPrimitive.content)
            assertEquals("invalid_selection", rejected(registry, "create_comment", strings("target" to "inline:311", "text" to "Reply", "selection" to "Knowledge"))!!.getValue("code").jsonPrimitive.content)
        }
        provider.assertNoEffects()

    }

    @Test fun `inline create whose saved anchor cannot be verified is unknown and never replayed`() {
        for (mode in listOf("misplaced", "no-marker", "hidden-marker", "legacy-hidden-marker", "dangling", "foreign-revision", "lost-response")) {
            val provider = Provider()
            provider.pages.getValue("201").content = "<p>same and same</p>"
            when (mode) {
                "misplaced" -> provider.inlineMarkerShift = 1
                "no-marker" -> provider.inlineWritesMarker = false
                "hidden-marker" -> provider.inlineMarkerHidden = true
                "legacy-hidden-marker" -> provider.inlineMarkerInLegacyMacro = true
                "dangling" -> provider.inlineResolution = "dangling"
                "foreign-revision" -> provider.inlineVersionStep = 2
                else -> provider.loseWriteResponse = true
            }
            provider.use { registry ->
                val result = call(registry, "create_comment", arguments("target" to JsonPrimitive("201"), "text" to JsonPrimitive("Uncertain"), "selection" to JsonPrimitive("same"), "occurrence" to JsonPrimitive(1)))
                assertEquals("unknown", result.getValue("status").jsonPrimitive.content, mode)
                val details = result["details"]?.jsonObject
                if (mode in setOf("misplaced", "no-marker", "hidden-marker", "legacy-hidden-marker")) assertEquals(listOf("position"), details!!.getValue("mismatchFields").jsonArray.map { it.jsonPrimitive.content }, mode)
                if (mode == "dangling") assertEquals(listOf("resolutionStatus"), details!!.getValue("mismatchFields").jsonArray.map { it.jsonPrimitive.content })
                if (mode != "lost-response") assertEquals("inline:350", details!!.getValue("reference").jsonPrimitive.content, mode)
            }
            assertEquals(1, provider.requests.count { it.method != HttpMethod.Get }, "$mode must not replay the native write")
        }
    }

    @Test fun `local corpus searches and reads after synchronization without further remote requests`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<p>공유 지식 😀 exact retained source</p>"
        provider.use(local = true) { registry ->
            provider.requests.clear()
            val search = call(registry, "search_documents", strings("query" to "공유 지식"))
            assertEquals("201", search.getValue("results").jsonArray.single().jsonObject.getValue("id").jsonPrimitive.content)
            assertTrue(search.getValue("results").jsonArray.single().jsonObject.getValue("excerpt").jsonPrimitive.content.contains("retained source"))
            val read = call(registry, "get_document", strings("document" to "201"))
            assertTrue(read.getValue("content").jsonPrimitive.content.contains("공유 지식 😀"))
            assertEquals("local_replica", read.getValue("freshness").jsonObject.getValue("source").jsonPrimitive.content)
            assertEquals("as_of_last_sync", read.getValue("freshness").jsonObject.getValue("permissionScope").jsonPrimitive.content)
            assertTrue(provider.requests.isEmpty(), "Warm document queries must read shared corpus rather than native API")
        }
        assertFalse(Files.exists(runtimeHome.resolve(".claude")))
    }

    @Test fun `ranked corpus search distinguishes native IDs and returns original multilingual evidence`() {
        val provider = Provider()
        provider.pages.getValue("201").apply {
            title = "운영 기록"
            content = "<h2>업무</h2><h3>승인</h3><p>Cafe\u0301 😀 한 운영 절차와 승인 기록. ${"후속 내용 ".repeat(10)}</p>"
        }
        provider.pages["203"] = Page("203", "210", "한 운영", "<p>Related document mentioning 201 and 733.</p>")
        provider.pages["204"] = Page("204", "210", "한 보고서", "<p>운영 기준과 연결합니다.</p>")
        provider.use(local = true) { registry ->
            provider.requests.clear()
            fun search(query: String, vararg options: Pair<String, JsonElement>) =
                call(registry, "search_documents", arguments("query" to JsonPrimitive(query), *options))
            fun JsonObject.hits() = getValue("results").jsonArray.map { it.jsonObject }
            val ranked = search("한 운영", "limit" to JsonPrimitive(1), "snippetLength" to JsonPrimitive(14))
            assertEquals("203", ranked.hits().single().getValue("id").jsonPrimitive.content)
            val page2 = search("한 운영", "limit" to JsonPrimitive(1), "snippetLength" to JsonPrimitive(14), "cursor" to ranked.getValue("nextCursor"))
            val hit = page2.hits().single()
            assertEquals("201", hit.getValue("id").jsonPrimitive.content)
            assertEquals("body", hit.getValue("match").jsonPrimitive.content)
            assertEquals(listOf("업무", "승인"), hit.getValue("headingPath").jsonArray.map { it.jsonPrimitive.content })
            val excerpt = hit.getValue("excerpt").jsonPrimitive.content
            assertTrue("😀 한 운영" in excerpt, excerpt)
            assertTrue(excerpt.codePointCount(0, excerpt.length) <= 14)
            val locator = hit.getValue("read").jsonObject
            val read = call(registry, "get_document", locator)
            assertEquals(excerpt, read.getValue("content").jsonPrimitive.content)
            assertEquals(locator.getValue("snapshot"), read.getValue("snapshot"))
            val continuation = call(registry, "get_document", JsonObject(locator + ("offset" to read.getValue("nextOffset"))))
            assertEquals(read.getValue("nextOffset"), continuation.getValue("offset"))
            assertTrue(continuation.getValue("content").jsonPrimitive.content.isNotBlank())
            val page3 = search("한 운영", "limit" to JsonPrimitive(1), "snippetLength" to JsonPrimitive(14), "cursor" to page2.getValue("nextCursor"))
            assertEquals("title_and_body", page3.hits().single().getValue("match").jsonPrimitive.content)
            assertFalse(page3.getValue("truncated").jsonPrimitive.boolean)
            assertEquals(listOf("201"), search("한 운영", "root" to JsonPrimitive("201")).hits().map { it.getValue("id").jsonPrimitive.content })

            val direct = search("201").hits().single()
            assertEquals("201", direct.getValue("id").jsonPrimitive.content)
            assertEquals("native_id", direct.getValue("match").jsonPrimitive.content)
            assertEquals("body", search("733").hits().single().getValue("match").jsonPrimitive.content)
            assertEquals("203", search("201", "root" to JsonPrimitive("203")).hits().single().getValue("id").jsonPrimitive.content)
            assertEquals(3, search("한").hits().size)
            assertEquals("201", search("CAFÉ").hits().single().getValue("id").jsonPrimitive.content)
            for (absent in listOf("page_id:", "example.atlassian.net", "2015", "한 미존재")) assertTrue(search(absent).hits().isEmpty(), absent)
            assertTrue(provider.requests.isEmpty())
        }
    }

    @Test fun `search ranges reject same-version native and Markdown projection changes without native fallback`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<h2>Evidence</h2><p>Original local range text</p>"
        var corpus: LocalCorpus? = null
        provider.use(local = true, observeCorpus = { corpus = it }) { registry ->
            fun locator() = call(registry, "search_documents", strings("query" to "201"))
                .getValue("results").jsonArray.single().jsonObject.getValue("read").jsonObject
            fun assertStale(read: JsonObject) {
                assertEquals("snapshot_changed", rejected(registry, "get_document", read)!!.getValue("code").jsonPrimitive.content)
                assertTrue(provider.requests.isEmpty(), "A stale search range must not fall through to a native read")
            }
            val old = locator()
            provider.pages.getValue("201").content = "<h2>Evidence</h2><p>Replacement local range text</p>"
            corpus!!.requestRepair("201")
            runBlocking { corpus!!.synchronize(authoritativeBodies = false) }
            provider.requests.clear()
            assertStale(old)
            val current = locator()
            assertEquals(old["version"], current["version"])
            assertNotEquals(old["snapshot"], current["snapshot"])

            val base = root.resolve("confluence/COMMON")
            val manifest = Json.parseToJsonElement(Files.readString(base.resolve(".sync/manifest.json"))).jsonObject
            val page = manifest.getValue("pages").jsonObject.getValue("201").jsonObject
            val projection = base.resolve(page.getValue("path").jsonPrimitive.content)
            Files.writeString(projection, Files.readString(projection).replace("Replacement", "Reprojected"))
            assertStale(current)
            val reprojected = locator()
            assertStale(JsonObject(reprojected + ("version" to JsonPrimitive(2))))
            assertEquals("invalid_snapshot", rejected(registry, "get_document", JsonObject(reprojected +
                ("section" to JsonPrimitive("1"))))!!.getValue("code").jsonPrimitive.content)
            assertTrue(provider.requests.isEmpty())
        }
    }

    @Test fun `scoped local search follows nested folders and stops at the configured root`() {
        val provider = Provider()
        provider.folders["231"] = Page("231", "210", "Operations", kind = "folder")
        provider.folders["232"] = Page("232", "231", "Records", kind = "folder")
        provider.pages["205"] = Page("205", "232", "Field notes", "<p>Local scope marker</p>")
        provider.pages.getValue("201").content = "<p>Local scope marker elsewhere</p>"
        provider.use(local = true) { registry ->
            provider.requests.clear()
            fun matches(root: String, query: String = "Local scope marker") =
                call(registry, "search_documents", strings("query" to query, "root" to root))
                    .getValue("results").jsonArray.map { it.jsonObject.getValue("id").jsonPrimitive.content }
            assertEquals(listOf("205"), matches("231"))
            assertEquals(listOf("205"), matches("232"))
            assertEquals(listOf("205"), matches("231", "205"))
            assertEquals(listOf("201", "205"), matches("210"))
            assertTrue(matches("200").isEmpty())
            assertTrue(matches("200", "205").isEmpty())
            assertTrue(provider.requests.isEmpty())
        }
    }

    @Test fun `local search omits heading context when Markdown promotes a native paragraph`() {
        val provider = Provider()
        provider.pages.getValue("201").content = "<h2>True header</h2><p>## Literal paragraph</p><p>topic-probe-evidence</p>"
        provider.use(local = true) { registry ->
            provider.requests.clear()
            val hit = call(registry, "search_documents", strings("query" to "topic-probe-evidence"))
                .getValue("results").jsonArray.single().jsonObject
            assertFalse("headingPath" in hit, "A literal Markdown heading line makes native section context ambiguous")
            val excerpt = hit.getValue("excerpt")
            assertTrue(excerpt.jsonPrimitive.content.contains("topic-probe-evidence"))
            val read = call(registry, "get_document", hit.getValue("read").jsonObject)
            assertEquals(excerpt, read.getValue("content"))
            assertTrue(provider.requests.isEmpty())
        }
    }

    @Test fun `another MCP instance reuses persisted corpus without resynchronizing each project`() {
        val provider = Provider()
        provider.use(local = true) { registry ->
            assertTrue(call(registry, "get_document", strings("document" to "201")).getValue("content").jsonPrimitive.content.contains("Knowledge 201"))
        }
        provider.requests.clear()
        provider.use(local = true, synchronize = false) { registry ->
            assertTrue(call(registry, "get_document", strings("document" to "201")).getValue("content").jsonPrimitive.content.contains("Knowledge 201"))
            assertTrue(provider.requests.isEmpty())
        }
    }

    @Test fun `verified native change is visible in shared corpus before update returns`() {
        val provider = Provider()
        provider.use(local = true) { registry ->
            val update = call(registry, "update_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1),
                "append" to JsonPrimitive("<p>Newest shared conclusion</p>"), "format" to JsonPrimitive("storage")))
            assertEquals("updated", update.getValue("status").jsonPrimitive.content)
            assertFalse(update.containsKey("syncRequired"))
            val count = provider.requests.size
            val read = call(registry, "get_document", strings("document" to "201"))
            assertTrue(read.getValue("content").jsonPrimitive.content.contains("Newest shared conclusion"))
            assertEquals(2, read.getValue("version").jsonPrimitive.int)
            assertEquals(count, provider.requests.size)
        }
        assertEquals(1, provider.effects.size)
    }

    @Test fun `native historical version remains readable when shared corpus holds current version`() {
        val provider = Provider(); provider.nativeHistory()
        provider.use(local = true) { registry ->
            provider.requests.clear()
            val old = call(registry, "get_document", arguments("document" to JsonPrimitive("201"), "version" to JsonPrimitive(1)))
            assertEquals(1, old.getValue("version").jsonPrimitive.int)
            assertTrue(old.getValue("content").jsonPrimitive.content.contains("Original native version"))
            assertTrue(provider.requests.any { it.url.parameters["version"] == "1" })
        }
        provider.assertNoEffects()
    }

    @Test fun `busy corpus refuses partial read rather than mixing manifest generations`() {
        val provider = Provider()
        provider.use(local = true) { registry ->
            val lockFile = root.resolve("confluence/COMMON/.sync/sync.lock")
            checkNotNull(CorpusFileLock.tryAcquire(lockFile)).use {
                val read = call(registry, "get_document", strings("document" to "201"))
                assertEquals("error", read.getValue("status").jsonPrimitive.content)
                assertFalse(read.containsKey("content"))
            }
        }
        provider.assertNoEffects()
    }

    private fun sameVersionInlineRepair(restart: Boolean) {
        val provider = Provider().apply { inlineVersionStep = 0 }
        var activeCorpus: LocalCorpus? = null
        var repairSignals = 0
        provider.use(local = true, observeCorpus = { activeCorpus = it; it.onRepair = { repairSignals++ } }) { registry ->
            val before = call(registry, "get_document", strings("document" to "201", "format" to "storage"))
            assertEquals(1, before.getValue("version").jsonPrimitive.int)
            assertFalse(before.getValue("content").jsonPrimitive.content.contains("inline-comment-marker"))
            val lockFile = root.resolve("confluence/COMMON/.sync/sync.lock")
            checkNotNull(CorpusFileLock.tryAcquire(lockFile)).use {
                val created = call(registry, "create_comment", strings("target" to "201", "text" to "Keep this observation", "selection" to "Knowledge 201"))
                assertEquals("created", created.getValue("status").jsonPrimitive.content)
                assertEquals(JsonPrimitive(true), created["syncRequired"], "Native write succeeded while local publication was locked")
            }
            assertEquals(1, repairSignals, "Confirmed publication failure must wake the existing synchronizer")
            assertEquals(1, provider.pages.getValue("201").version, "Inline provider did not create a new page version")
            assertEquals(1, provider.requests.count { it.method == HttpMethod.Post })
            assertTrue(provider.pages.getValue("201").content.contains("inline-comment-marker"))
            if (!restart) {
                runBlocking { activeCorpus!!.synchronize(authoritativeBodies = false) }
                val repaired = call(registry, "get_document", strings("document" to "201", "format" to "storage"))
                assertEquals(1, repaired.getValue("version").jsonPrimitive.int)
                assertTrue(repaired.getValue("content").jsonPrimitive.content.contains("marker-350"))
            }
        }
        if (restart) {
            // A restarted process has lost transient repair IDs. The shared body's
            // bounded polling checkpoint must eventually force an authoritative read.
            val checkpoint = root.resolve("confluence/COMMON/.sync/state.json")
            val prior = Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject
            Files.writeString(checkpoint, JsonObject(prior + ("bodyRefreshedAt" to JsonPrimitive(0))).toString())
            provider.use(local = true, synchronize = false, observeCorpus = { activeCorpus = it }) { registry ->
                runBlocking { activeCorpus!!.synchronize() }
                val repaired = call(registry, "get_document", strings("document" to "201", "format" to "storage"))
                assertEquals(1, repaired.getValue("version").jsonPrimitive.int)
                assertTrue(repaired.getValue("content").jsonPrimitive.content.contains("marker-350"))
            }
        }
        assertEquals(1, provider.requests.count { it.method == HttpMethod.Post }, "Synchronization must not replay native comment creation")
        assertEquals(1, provider.requests.count { it.method != HttpMethod.Get }, "Recovery is read-only against Confluence")
    }

    @Test fun `failed inline replica publication is automatically repaired at same native version without another POST`() {
        sameVersionInlineRepair(restart = false)
    }

    @Test fun `restarted corpus repairs persisted same version inline marker without a durable retry queue`() {
        sameVersionInlineRepair(restart = true)
    }

    @Test fun `exact current token legacy seed preserves originals and reauthorizes without unchanged body transfer`() {
        val provider = Provider()
        provider.use(local = true) { }
        val source = settings.legacyCorpusRoot.resolve("confluence/COMMON/.sync/manifest.json")
        Files.move(root, settings.legacyCorpusRoot)
        val original = Files.readString(source)
        checkNotNull(CorpusFileLock.tryAcquire(source.parent.resolve("sync.lock"))).use {
            settings.prepareCorpus()
            assertFalse(Files.exists(root.resolve("confluence/COMMON/.sync/manifest.json")), "Active legacy writer prevents reuse")
        }
        // A fresh process may reuse the now-idle source.
        KnowledgeSettings(settings.read(), credentials, runtimeHome.resolve("data")).prepareCorpus()
        val copied = root.resolve("confluence/COMMON/.sync/manifest.json")
        val seed = Json.parseToJsonElement(Files.readString(copied)).jsonObject
        assertEquals(JsonPrimitive(false), seed["inventoryComplete"])
        assertNull(seed["credentialBinding"])
        assertNull(seed[WikiChanges.noticesKey])
        assertFalse(Files.exists(copied.resolveSibling("state.json")), "Old body-refresh checkpoint is not admission evidence")
        provider.requests.clear()
        provider.use(local = true, synchronize = false, observeCorpus = { corpus ->
            assertEquals(JsonPrimitive("credential_reauthorization_pending"), corpus.execute("get_document", strings("document" to "201"))?.get("code"))
            runBlocking { corpus.synchronize() }
            assertFalse(provider.requests.any { it.url.parameters["body-format"] == "storage" })
            assertNull(Json.parseToJsonElement(Files.readString(copied.resolveSibling("state.json"))).jsonObject["bodyRefreshedAt"])
        }) { registry -> assertTrue(call(registry, "get_document", strings("document" to "201")).containsKey("content")) }
        assertEquals(original, Files.readString(source), "Migration must preserve the legacy original")
        provider.assertNoEffects()
    }

    @Test fun `already replaced token cannot guess another opaque legacy directory`() {
        val provider = Provider()
        provider.use(local = true) { }
        Files.move(root, settings.legacyCorpusRoot)
        val rotated = KnowledgeSettings(settings.read(), credentials.copy(apiToken = "already-replaced-token"), runtimeHome.resolve("data"))
        rotated.prepareCorpus()
        assertFalse(Files.exists(root.resolve("confluence/COMMON/.sync/manifest.json")))
        assertTrue(Files.isRegularFile(settings.legacyCorpusRoot.resolve("confluence/COMMON/.sync/manifest.json")))
    }

    @Test fun `rotated token waits for inventory then reuses unchanged bodies without advancing sweep freshness`() {
        val provider = Provider()
        provider.use(local = true) { }
        val checkpoint = root.resolve("confluence/COMMON/.sync/state.json")
        val oldState = Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject
        Files.writeString(checkpoint, JsonObject(oldState + ("bodyRefreshedAt" to JsonPrimitive(0))).toString())
        val rotated = KnowledgeSettings(settings.read(), credentials.copy(apiToken = "rotated-fixture-token"), runtimeHome.resolve("data"))
        assertEquals(root, rotated.corpusRoot)
        provider.requests.clear()
        provider.use(connection = rotated, local = true, synchronize = false, observeCorpus = { corpus ->
            val status = Json.parseToJsonElement(corpus.status()).jsonObject
            assertEquals(JsonPrimitive(false), status["ready"])
            assertEquals(JsonPrimitive("credential_reauthorization_pending"), status["reason"])
            assertEquals(JsonPrimitive("credential_reauthorization_pending"), corpus.execute("get_document", strings("document" to "201"))?.get("code"))
            provider.denied["/wiki/api/v2/pages/210"] = HttpStatusCode.Forbidden
            assertFailsWith<ContextRemoteException> { runBlocking { corpus.synchronize() } }
            assertEquals(oldState["credentialBinding"], Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject["credentialBinding"])
            provider.denied.clear(); provider.requests.clear()
            runBlocking { corpus.synchronize() }
            assertTrue(provider.requests.isNotEmpty(), "Rotation must reauthorize metadata and complete inventory")
            assertFalse(provider.requests.any { it.url.parameters["body-format"] == "storage" }, "Same-version validated local bodies avoid transfer during token admission")
            val admitted = Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject
            assertEquals(JsonPrimitive(rotated.credentialFingerprint), admitted["credentialBinding"])
            assertEquals(JsonPrimitive(0), admitted["bodyRefreshedAt"], "Inventory reuse cannot claim authoritative body refresh")
            provider.requests.clear()
            provider.pages.getValue("201").content += "<p>Same version marker discovered by normal sweep</p>"
            runBlocking { corpus.synchronize() }
            assertTrue(provider.requests.any { it.url.parameters["body-format"] == "storage" })
        }) { registry ->
            val document = call(registry, "get_document", strings("document" to "201"))
            assertTrue(document.getValue("content").jsonPrimitive.content.contains("Same version marker discovered by normal sweep"))
        }
        provider.assertNoEffects()
    }

    @Test fun `another corpus instance skips recent authoritative body sweep but expires it at the shared checkpoint`() {
        val provider = Provider().apply { pages.getValue("201").title = "OLD Archive reference" }
        provider.use(local = true) { registry ->
            assertTrue(call(registry, "get_document", strings("document" to "201")).containsKey("content"))
        }
        assertTrue(provider.requests.any { it.url.parameters["body-format"] == "storage" })
        provider.requests.clear()
        provider.use(local = true) { registry ->
            assertTrue(call(registry, "get_document", strings("document" to "201")).containsKey("content"))
        }
        assertFalse(provider.requests.any { it.url.parameters["body-format"] == "storage" }, "Recent shared checkpoint prevents duplicate body transfer across MCP instances")
        provider.pages.getValue("201").content += "<p>Changed without native page revision</p>"
        val checkpoint = root.resolve("confluence/COMMON/.sync/state.json")
        val prior = Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject
        Files.writeString(checkpoint, JsonObject(prior + ("bodyRefreshedAt" to JsonPrimitive(0))).toString())
        provider.requests.clear()
        provider.use(local = true) { registry ->
            assertTrue(call(registry, "get_document", strings("document" to "201")).getValue("content").jsonPrimitive.content.contains("Changed without native page revision"))
        }
        assertTrue(provider.requests.any { it.url.parameters["body-format"] == "storage" }, "Expired shared checkpoint revalidates body even when native version is unchanged")
        val manifest = Json.parseToJsonElement(Files.readString(checkpoint.resolveSibling("manifest.json"))).jsonObject
        assertTrue(manifest.getValue("explicitPages").jsonArray.isEmpty(), "An automatic refresh must preserve the explicit page selection")
        provider.assertNoEffects()
    }

    @Test fun `invalid negative future and malformed sweep timestamps cannot suppress fresh body retrieval`() {
        val provider = Provider()
        provider.use(local = true) { registry -> assertTrue(call(registry, "get_document", strings("document" to "201")).containsKey("content")) }
        val checkpoint = root.resolve("confluence/COMMON/.sync/state.json")
        val invalidStamps = listOf<JsonElement>(JsonPrimitive(Long.MIN_VALUE), JsonPrimitive(Long.MAX_VALUE),
            JsonPrimitive("not-a-timestamp"), JsonPrimitive(Long.MAX_VALUE.toString()), buildJsonObject { put("invalid", true) }, JsonArray(emptyList()))
        for ((index, stamp) in invalidStamps.withIndex()) {
            provider.pages.getValue("201").content = "<p>Authoritative replacement $index without page revision</p>"
            val prior = Json.parseToJsonElement(Files.readString(checkpoint)).jsonObject
            Files.writeString(checkpoint, JsonObject(prior + ("bodyRefreshedAt" to stamp)).toString())
            provider.requests.clear()
            provider.use(local = true) { registry ->
                val read = call(registry, "get_document", strings("document" to "201"))
                assertTrue(read.getValue("content").jsonPrimitive.content.contains("Authoritative replacement $index"), "Invalid checkpoint $stamp must not suppress native body refresh")
            }
            assertTrue(provider.requests.any { it.url.parameters["body-format"] == "storage" }, "Invalid checkpoint $stamp requires fresh native storage")
        }
        provider.assertNoEffects()
    }

    @Test fun `cold status distinguishes initial pending from failed synchronization and sanitizes failure codes`() {
        val provider = Provider()
        provider.use(local = true, synchronize = false, observeCorpus = { corpus ->
            val initial = Json.parseToJsonElement(corpus.status()).jsonObject
            assertEquals("pending", initial.getValue("status").jsonPrimitive.content)
            assertEquals("replica_missing", initial.getValue("reason").jsonPrimitive.content)
            assertEquals(JsonNull, initial.getValue("syncError"))
            corpus.syncFailed("http_401")
        }) { registry ->
            val failed = call(registry, "connection_status", buildJsonObject {})
            assertEquals("failed", failed.getValue("status").jsonPrimitive.content)
            assertEquals(JsonPrimitive(false), failed.getValue("ready"))
            assertEquals("http_401", failed.getValue("code").jsonPrimitive.content)
            assertTrue(failed.getValue("syncError").jsonPrimitive.content.contains("http_401"))
            assertFalse(failed.getValue("message").jsonPrimitive.content.contains("running"))
        }
        provider.use(local = true, synchronize = false, observeCorpus = { it.syncFailed("http_401\nAuthorization: Bearer fixture-private-value") }) { registry ->
            val redacted = call(registry, "connection_status", buildJsonObject {})
            assertEquals("sync_failed", redacted.getValue("code").jsonPrimitive.content)
            assertFalse(redacted.toString().contains("fixture-private-value"))
            assertFalse(redacted.toString().contains("Authorization"))
        }
        assertTrue(provider.requests.isEmpty(), "Status must expose the recorded failure without contacting the service")
    }

    @Test fun `successful cold synchronization clears a previous failure and reports a readable snapshot`() {
        val provider = Provider()
        provider.use(local = true, synchronize = false, observeCorpus = { corpus ->
            corpus.syncFailed("http_401")
            assertEquals("failed", Json.parseToJsonElement(corpus.status()).jsonObject.getValue("status").jsonPrimitive.content)
            runBlocking { corpus.synchronize() }
        }) { registry ->
            val ready = call(registry, "connection_status", buildJsonObject {})
            assertEquals("ready", ready.getValue("status").jsonPrimitive.content)
            assertEquals(JsonPrimitive(true), ready.getValue("ready"))
            assertFalse(ready.containsKey("syncError"))
            assertEquals(JsonNull, ready.getValue("freshness").jsonObject.getValue("syncError"))
        }
        assertTrue(provider.requests.any { it.url.parameters["body-format"] == "storage" })
        provider.assertNoEffects()
    }

    @Test fun `status reports active writer as busy without hiding a previous synchronization error`() {
        val provider = Provider()
        provider.use(local = true, observeCorpus = { it.syncFailed("http_403") }) { registry ->
            val lockFile = root.resolve("confluence/COMMON/.sync/sync.lock")
            checkNotNull(CorpusFileLock.tryAcquire(lockFile)).use {
                val busy = call(registry, "connection_status", buildJsonObject {})
                assertEquals("busy", busy.getValue("status").jsonPrimitive.content)
                assertEquals("replica_busy", busy.getValue("reason").jsonPrimitive.content)
                assertEquals(JsonPrimitive(false), busy.getValue("ready"))
                assertEquals("http_403", busy.getValue("code").jsonPrimitive.content)
                assertTrue(busy.getValue("syncError").jsonPrimitive.content.contains("http_403"))
            }
        }
        provider.assertNoEffects()
    }

    @Test fun `new corpus reports paused comment coverage after confirmed root denial`() {
        settings = KnowledgeSettings(JsonObject(settings.read() + ("watchCommentPages" to JsonPrimitive("201"))),
            credentials, runtimeHome.resolve("data"))
        val provider = Provider()
        val control = root.resolve("confluence/COMMON/.sync")
        val manifest = control.resolve("manifest.json")
        provider.use(local = true, observeCorpus = { corpus ->
            val ready = WikiChanges.comments(Json.parseToJsonElement(Files.readString(manifest)).jsonObject,
                1, setOf("201"), mapOf("201" to "a".repeat(64)))
            ContextFiles.atomic(manifest, ready.toString())
            assertEquals(JsonPrimitive("ready"), Json.parseToJsonElement(corpus.status()).jsonObject["commentWatch"])
            provider.denied["/wiki/api/v2/pages/210"] = HttpStatusCode.Forbidden
            assertFailsWith<ContextRemoteException> { runBlocking { corpus.synchronize() } }
        }) { }
        provider.requests.clear()
        provider.use(local = true, synchronize = false) { registry ->
            val paused = call(registry, "connection_status", buildJsonObject {})
            assertEquals(JsonPrimitive("failed"), paused["status"])
            assertEquals(JsonPrimitive("root_scope_denied"), paused["code"])
            assertEquals(JsonPrimitive("unavailable"), paused["commentWatch"])
            assertEquals(JsonPrimitive(true), paused["ready"], "Last committed replica remains readable")
        }
        assertTrue(provider.requests.isEmpty())
    }

    @Test fun `native comment read records denied root before returning to the host hook`() {
        val provider = Provider()
        val marker = root.resolve("confluence/COMMON/.sync/${WikiChanges.noticeDenialMarker}")
        provider.use(local = true) { registry ->
            provider.denied["/wiki/api/v2/pages/201"] = HttpStatusCode.Forbidden
            val childDenied = call(registry, "list_comments", strings("target" to "201"))
            assertEquals(JsonPrimitive("http_403"), childDenied["code"])
            assertFalse(Files.exists(marker))
            provider.denied.clear()
            provider.denied["/wiki/api/v2/pages/210"] = HttpStatusCode.Forbidden
            checkNotNull(CorpusFileLock.tryAcquire(marker.parent.resolve("sync.lock"), shared = true)).use {
                val rootDenied = call(registry, "list_comments", strings("target" to "201"))
                assertEquals(JsonPrimitive("http_403"), rootDenied["code"])
                assertTrue(Files.isRegularFile(marker))
            }
        }
        provider.assertNoEffects()
    }

    @Test fun `root comment detail records denial after the initial ancestry read succeeds`() {
        val provider = Provider()
        provider.comments["399"] = Comment("399", "210")
        provider.use(local = true) { registry ->
            var rootReads = 0
            provider.beforeRequest = { request ->
                val rootPath = "/wiki/api/v2/pages/210"
                if (request.url.encodedPath == rootPath && ++rootReads == 2) {
                    provider.denied[rootPath] = HttpStatusCode.Forbidden
                }
            }
            val failed = call(registry, "list_comments", strings("target" to "footer:399"))
            assertEquals(JsonPrimitive("http_403"), failed["code"])
            assertEquals(2, rootReads)
            assertTrue(Files.isRegularFile(root.resolve("confluence/COMMON/.sync/${WikiChanges.noticeDenialMarker}")))
        }
        provider.assertNoEffects()
    }

}
