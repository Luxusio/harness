package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.config.JiraConfig
import io.ktor.client.HttpClient
import io.ktor.client.engine.cio.CIO
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.request.*
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsChannel
import io.ktor.http.*
import io.ktor.utils.io.core.readBytes
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.coroutines.withContext
import kotlin.coroutines.AbstractCoroutineContextElement
import kotlin.coroutines.CoroutineContext
import kotlin.coroutines.coroutineContext
import kotlinx.serialization.json.*
import java.net.URI

data class RemoteNode(val id: String, val title: String, val parentId: String?, val type: String = "page",
                      val spaceId: String = "", val version: Int = 0, val status: String = "current")
data class RemotePage(val node: RemoteNode, val body: String, val versionMessage: String = "")

/** The optional title slug carries no authority: every API request is built from the literal native ID. */
internal fun nativePageUrlId(value: String, origin: String, space: String? = null, allowApi: Boolean = false): String {
    require(value.length <= 4096) { "Bounded native page URL required" }
    val uri = URI(value)
    val expected = URI(origin)
    require(uri.scheme == "https" && uri.scheme == expected.scheme && uri.host == expected.host && uri.port == expected.port &&
        uri.rawUserInfo == null && uri.rawQuery == null && uri.rawFragment == null && uri.normalize().rawPath == uri.rawPath) {
        "Document URL must be an exact URL on the configured origin"
    }
    val page = Regex("^/wiki/spaces/([A-Za-z0-9_-]+)/pages/([1-9][0-9]*)(?:/([^/]*))?$").matchEntire(uri.rawPath)
    val api = if (allowApi) Regex("^/wiki/api/v2/pages/([1-9][0-9]*)(?:/([^/]*))?$").matchEntire(uri.rawPath) else null
    require(page != null || api != null) { "Use a literal native page ID or exact page URL" }
    require(page == null || space == null || page.groupValues[1] == space) { "Page URL space differs from its admitted namespace" }
    val slug = page?.groupValues?.get(3) ?: api!!.groupValues[2]
    val decoded = java.net.URLDecoder.decode(slug.replace("+", "%2B"), "UTF-8")
    require(decoded !in setOf(".", "..") && decoded.none { it == '/' || it == '\\' || it.isISOControl() } &&
        !Regex("%2e|%2f|%5c", RegexOption.IGNORE_CASE).containsMatchIn(decoded)) { "Page title slug contains traversal or path separators" }
    return page?.groupValues?.get(2) ?: api!!.groupValues[1]
}

/** Safe error text only: response bodies may contain credentials or private document content. */
class ContextRemoteException(val code: String, message: String, val outcomeUnknown: Boolean = false,
                             val details: JsonObject = buildJsonObject {}, val next: String? = null) : RuntimeException(message)

/** V1 Space.id is int64; V2 spaceId is a string. Neither representation relaxes native identity checks. */
internal fun normalizeNativeSpaceId(metadata: JsonObject): String {
    fun invalid(): Nothing = throw ContextRemoteException("malformed_list", "Native space identity is incomplete or invalid")
    fun parse(value: JsonElement?, stringOnly: Boolean): String {
        val primitive = value as? JsonPrimitive ?: invalid()
        if (stringOnly && !primitive.isString) invalid()
        val text = primitive.content
        if (!text.matches(Regex("[1-9][0-9]*"))) invalid()
        return text.toLongOrNull()?.takeIf { it > 0 }?.toString() ?: invalid()
    }
    val direct = if (metadata.containsKey("spaceId")) parse(metadata["spaceId"], true) else null
    val space = if (metadata.containsKey("space")) metadata["space"] as? JsonObject ?: invalid() else null
    val nested = space?.let { parse(it["id"], false) }
    if (direct != null && nested != null && direct != nested) invalid()
    return direct ?: nested ?: invalid()
}

/** Complete reads, origin-bound credentials, and single-attempt mutations. No Markdown roundtrips. */
class ConfluenceRemote(private val config: JiraConfig, client: HttpClient? = null,
                       private val cache: ConfluencePageCache? = null) : AutoCloseable {
    private val origin = config.baseUrl.trimEnd('/').also {
        require(ContextConfig.validOrigin(it)) { "Configured Atlassian URL must be an HTTPS origin" }
    }
    private val originUri = URI(origin)
    private val http = if (client == null) HttpClient(CIO) { transport() } else client.config { transport() }

    private class WikiBudget : AbstractCoroutineContextElement(Key) {
        companion object Key : CoroutineContext.Key<WikiBudget>
        private val started = System.nanoTime()
        private var requests = 0
        private var bytes = 0L
        fun request() {
            if (++requests > 256 || System.nanoTime() - started > 45_000_000_000L)
                throw ContextRemoteException("wiki_request_limit", "Operation exceeded its request/time budget; narrow the target or retry a read")
        }
        fun accept(size: Int, mutating: Boolean) {
            bytes += size
            if (bytes > 32L * 1024 * 1024)
                throw ContextRemoteException("wiki_body_limit", "Operation exceeded its response body budget", mutating)
        }
    }
    internal suspend fun <T> wikiOperation(block: suspend () -> T): T = withContext(WikiBudget()) { block() }

    private fun io.ktor.client.HttpClientConfig<*>.transport() {
        followRedirects = false
        expectSuccess = false
        install(HttpTimeout) { requestTimeoutMillis = 60_000; connectTimeoutMillis = 15_000; socketTimeoutMillis = 60_000 }
    }

    private fun id(value: String): String {
        require(value.matches(Regex("[1-9][0-9]*"))) { "Positive decimal Confluence ID required" }
        return value
    }

    private fun observedId(response: JsonObject): String? {
        val value = response.string("id") ?: return null
        return try { id(value) } catch (_: IllegalArgumentException) { null }
    }

    private fun url(value: String, base: String = origin): String {
        val candidate = if ((value.startsWith("/rest/api/") && !value.startsWith("/rest/api/3/issue/") && value != "/rest/api/3/myself") || value.startsWith("/api/v2/")) "/wiki$value" else value
        val uri = try { URI(base).resolve(candidate) } catch (_: Exception) { throw ContextRemoteException("invalid_next", "Invalid Confluence request URL") }
        require(uri.scheme == originUri.scheme && uri.host == originUri.host && uri.port == originUri.port &&
            uri.rawUserInfo == null && uri.rawFragment == null &&
            (uri.rawPath.startsWith("/wiki/api/v2/") || uri.rawPath.startsWith("/wiki/rest/api/") ||
                (uri.rawPath == "/rest/api/3/myself" || uri.rawPath.matches(Regex("/rest/api/3/issue/[A-Z][A-Z0-9_]*-[1-9][0-9]*(?:/comment(?:/[1-9][0-9]*)?)?")))) &&
            !uri.rawPath.contains("%", ignoreCase = true) && uri.normalize().rawPath == uri.rawPath) {
            "Confluence request must remain inside the configured API origin"
        }
        return uri.toASCIIString()
    }

    private fun endpoint(path: String, parameters: Map<String, String> = emptyMap()): String = URLBuilder(url(path)).apply {
        parameters.forEach { (key, value) -> this.parameters.append(key, value) }
    }.buildString()

    private suspend fun request(method: HttpMethod, target: String, payload: JsonObject? = null): Pair<HttpResponse, JsonObject> {
        coroutineContext[WikiBudget]?.request()
        val mutating = method != HttpMethod.Get
        val response = try {
            http.request(url(target)) {
                this.method = method
                basicAuth(config.email, config.apiToken)
                accept(ContentType.Application.Json)
                if (payload != null) { contentType(ContentType.Application.Json); setBody(payload.toString()) }
            }
        } catch (e: CancellationException) { throw e }
        catch (_: Exception) { throw ContextRemoteException("transport_failed", "Confluence request failed; mutation outcomes require reconciliation", mutating) }
        if (!response.status.isSuccess()) {
            val details = try { errorDetails(response, mutating && response.status.value >= 500) }
            catch (e: CancellationException) { throw e }
            catch (_: Exception) { buildJsonObject {} }
            val next = when (response.status.value) {
                400 -> "Check the storage content and required fields before preparing a corrected request."
                401, 403 -> "Check native account access and edit permission for the admitted target."
                409 -> "Read the current native version before preparing another edit; do not automatically rebase."
                else -> if (mutating && response.status.value >= 500) "Inspect native content and history using read-only tools; do not automatically repeat the write."
                    else "Check the target and native access before retrying a read."
            }
            throw ContextRemoteException("http_${response.status.value}", "Confluence request failed (HTTP ${response.status.value})",
                mutating && response.status.value >= 500, details, next)
        }
        val bytes = try { response.bodyAsChannel().readRemaining(8L * 1024 * 1024 + 1).readBytes() }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { throw ContextRemoteException("response_failed", "Confluence response could not be read", mutating) }
        if (bytes.size > 8 * 1024 * 1024) throw ContextRemoteException("response_limit", "Confluence response exceeds the size limit", mutating)
        coroutineContext[WikiBudget]?.accept(bytes.size, mutating)
        val result = try { Json.parseToJsonElement(String(bytes, Charsets.UTF_8)).jsonObject }
        catch (_: Exception) { throw ContextRemoteException("malformed_response", "Confluence returned invalid JSON", mutating) }
        return response to result
    }

    /** Provider messages can reflect private content even in named error fields. Return only recognized reasons. */
    private suspend fun errorDetails(response: HttpResponse, outcomeUnknown: Boolean): JsonObject {
        val bytes = try { withTimeoutOrNull(2_000) { response.bodyAsChannel().readRemaining(64 * 1024L + 1).readBytes() } }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { null }
        if (bytes == null) return buildJsonObject {}
        coroutineContext[WikiBudget]?.accept(bytes.size, outcomeUnknown)
        if (bytes.size > 64 * 1024) return buildJsonObject {}
        val value = try { Json.parseToJsonElement(String(bytes, Charsets.UTF_8)) as? JsonObject }
        catch (_: Exception) { null } ?: return buildJsonObject {}
        val errors = listOf(value) + (value["errors"] as? JsonArray).orEmpty().take(8).mapNotNull { it as? JsonObject }
        val credentials = listOf(config.email, config.apiToken,
            java.util.Base64.getEncoder().encodeToString("${config.email}:${config.apiToken}".toByteArray(Charsets.UTF_8)))
        fun safe(value: String): String {
            var text = value.take(2048)
            credentials.filter { it.isNotEmpty() }.forEach { text = text.replace(it, "[redacted]", ignoreCase = true) }
            return text.replace(Regex("(?i)(?:basic|bearer)\\s+[^\\s,;]+"), "[redacted]")
                .replace(Regex("(?i)(?:authorization|password|token|secret|api[_-]?key)\\s*[:=]\\s*[^\\s,;]+"), "[redacted]").lowercase()
        }
        val allowedCodes = setOf("INVALID_STORAGE", "BAD_REQUEST", "FORBIDDEN", "VERSION_CONFLICT", "CONFLICT", "PERMISSION_DENIED", "UNAUTHORIZED", "NOT_FOUND",
            "DUPLICATE_TITLE", "TITLE_ALREADY_EXISTS", "duplicateTitle", "INVALID_PARENT", "INVALID_SPACE")
        val codes = errors.flatMap { error -> listOfNotNull(error["code"], error["errorCode"]) }
            .mapNotNull { (it as? JsonPrimitive)?.takeIf { code -> code.isString }?.content }
            .filter { it in allowedCodes && credentials.none { credential -> credential.isNotEmpty() && it.contains(credential, true) } }.distinct().take(3)
        val messages = errors.flatMap { error ->
            listOf("message", "detail", "title").mapNotNull { (error[it] as? JsonPrimitive)?.takeIf { message -> message.isString }?.content } +
                (error["errorMessages"] as? JsonArray).orEmpty().take(8).mapNotNull { (it as? JsonPrimitive)?.takeIf { message -> message.isString }?.content }
        }
        val reasons = (messages + codes).map(::safe).mapNotNull { message -> when {
                "title" in message && listOf("duplicate", "already exists", "already_exists", "alreadyexists", "same title").any { it in message } -> "A page with this title already exists in the native space."
                listOf("invalid parent", "invalid_parent", "parent is invalid", "parent not found", "parent does not exist", "parent id is invalid").any { it in message } -> "The native parent is invalid or unavailable."
                listOf("invalid space", "invalid_space", "space is invalid", "space not found", "space does not exist", "space id is invalid").any { it in message } -> "The native space is invalid or unavailable."
                ("storage" in message || "xhtml" in message) && listOf("invalid", "mismatch", "parse", "malformed").any { it in message } -> "Native storage XHTML is invalid or malformed."
                listOf("permission", "forbidden", "not authorized", "access denied").any { it in message } -> "The native account lacks permission for this operation."
                "version" in message && listOf("conflict", "current", "latest", "mismatch").any { it in message } -> "The requested native version conflicts with the current version."
                else -> null
            } }.distinct().take(3)
        return buildJsonObject {
            if (codes.isNotEmpty()) put("providerCodes", JsonArray(codes.map(::JsonPrimitive)))
            if (reasons.isNotEmpty()) put("providerMessages", JsonArray(reasons.map(::JsonPrimitive)))
        }
    }

    private fun JsonObject.string(name: String): String? = (this[name] as? JsonPrimitive)?.contentOrNull
    private fun parseNode(obj: JsonObject, expectedType: String? = null, parent: String? = null): RemoteNode {
        try {
            val nodeId = id(obj.string("id") ?: error("id"))
            val title = obj.string("title")?.takeIf { it.isNotBlank() && it.length <= 1000 } ?: error("title")
            val suppliedType = obj.string("type")
            require(expectedType == null || !obj.containsKey("type") || suppliedType == expectedType)
            val type = expectedType ?: suppliedType ?: "page"
            require(type in setOf("page", "folder"))
            val spaceId = obj.string("spaceId") ?: (obj["space"] as? JsonObject)?.string("id") ?: error("space")
            id(spaceId)
            val version = (obj["version"] as? JsonObject)?.get("number")?.jsonPrimitive?.intOrNull ?: 0
            require(type != "page" || version > 0)
            val ancestors = obj["ancestors"] as? JsonArray
            val parentId = obj.string("parentId") ?: ancestors?.lastOrNull()?.jsonObject?.string("id") ?: parent
            parentId?.let(::id)
            return RemoteNode(nodeId, title, parentId, type, spaceId, version, obj.string("status") ?: "current")
        } catch (e: ContextRemoteException) { throw e }
        catch (_: Exception) { throw ContextRemoteException("malformed_node", "Confluence metadata is incomplete or invalid") }
    }

    private fun parsePage(obj: JsonObject, expectedId: String? = null): RemotePage {
        val node = parseNode(obj, "page")
        if (expectedId != null && node.id != expectedId) throw ContextRemoteException("identity_mismatch", "Confluence returned a different page")
        val body = (obj["body"] as? JsonObject)?.get("storage")?.jsonObject?.string("value")
            ?: throw ContextRemoteException("missing_storage", "Confluence did not return storage content")
        val message = (obj["version"] as? JsonObject)?.string("message") ?: ""
        return RemotePage(node, body, message)
    }

    @JvmOverloads
    suspend fun page(id: String, expectedNode: RemoteNode? = null, scope: ContextConfig? = null, freshStorage: Boolean = false): RemotePage {
        val path = "/wiki/api/v2/pages/${id(id)}"
        if (cache == null && expectedNode == null && scope?.isKnowledge != true) return parsePage(request(HttpMethod.Get, endpoint(path, mapOf("body-format" to "storage"))).second, id)
        return pageStorage(id, expectedNode, scope, freshStorage)
    }

    /** WikiService owns fresh namespace admission before and after this permission/version-bound read. */
    internal suspend fun wikiPage(expectedNode: RemoteNode): RemotePage {
        require(expectedNode.type == "page" && expectedNode.status == "current")
        return pageStorage(expectedNode.id, expectedNode, null, false)
    }

    private suspend fun pageStorage(id: String, expectedNode: RemoteNode?, scope: ContextConfig?, freshStorage: Boolean,
                                    admission: Pair<BodyScope, RemoteNode>? = null): RemotePage {
        val path = "/wiki/api/v2/pages/${id(id)}"
        // Bind the fresh permission check to the exact metadata admitted by the selected root.
        val metadata = request(HttpMethod.Get, endpoint(path)).second
        val node = parseNode(metadata, "page")
        require(node.id == id && metadata.string("status") == "current") { "Current page metadata required" }
        require(expectedNode == null || node == expectedNode) { "Page left its authorized identity or hierarchy; retry scope admission" }
        // A body read owns one authorization generation. Its final pass remains fresh,
        // including on a cache hit; no observation is shared with a later write phase.
        val authorization = if (scope?.isKnowledge == true) admission?.first ?: bodyScope(scope) else null
        val validation = authorization?.admission
        if (validation != null) {
            require((admission?.second ?: validation(id)) == node) { "Page left the selected root before its storage read" }
            if (admission == null) validation.validateConsumed()
        }
        val message = (metadata["version"] as? JsonObject)?.string("message") ?: ""
        (if (freshStorage) null else cache?.read(node, message))?.let {
            authorization?.finish?.invoke()
            return it
        }
        val storage = request(HttpMethod.Get, endpoint(path, mapOf("body-format" to "storage"))).second
        require(storage.string("status") == "current") { "Current page storage required" }
        val page = parsePage(storage, id)
        require(page.node == node && page.versionMessage == message) { "Page changed during read; retry" }
        authorization?.finish?.invoke()
        cache?.write(page)
        return page
    }

    suspend fun scopedPage(config: ContextConfig, pageId: String, freshStorage: Boolean = false): RemotePage =
        if (config.isKnowledge) mutationPage(pageId, config, freshStorage = freshStorage) else page(pageId, freshStorage = freshStorage)

    /** One bounded authorization graph for one explicit document evidence operation. */
    internal suspend fun freshScopedPages(config: ContextConfig, pageIds: List<String>): List<RemotePage> {
        require(config.isKnowledge && pageIds.size in 1..100 && pageIds.distinct().size == pageIds.size) { "Bounded unique scoped page selection required" }
        val admission = scopeValidator(config)
        var bytes = 0L
        val pages = pageIds.map { selected ->
            page(selected, admission(selected), freshStorage = true).also {
                val size = it.body.toByteArray(Charsets.UTF_8).size
                bytes += size
                require(size <= 4 * 1024 * 1024 && bytes <= 16L * 1024 * 1024) { "Selected document bodies exceed the capture budget; reduce the selection" }
            }
        }
        admission.validateConsumed()
        return pages
    }

    /** Native history confirms the exact readback version; no synthetic history or Git copy. */
    suspend fun requirePageVersion(page: RemotePage) {
        val value = request(HttpMethod.Get, endpoint("/wiki/api/v2/pages/${id(page.node.id)}/versions/${page.node.version}")).second
        require(value["number"]?.jsonPrimitive?.intOrNull == page.node.version) { "Native page version evidence is unavailable" }
        require(value["authorId"]?.jsonPrimitive?.contentOrNull?.isNotBlank() == true &&
            value["createdAt"]?.jsonPrimitive?.contentOrNull?.isNotBlank() == true) { "Native page history is incomplete" }
    }

    /** Complete issue inputs for resolving the publishing hierarchy; unavailable fields are never absence. */
    suspend fun publishingIssue(key: String): JsonObject {
        require(key.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*"))) { "Valid Jira issue key required" }
        val result = request(HttpMethod.Get, endpoint("/rest/api/3/issue/$key",
            mapOf("fields" to "summary,description,fixVersions"))).second
        require(result.string("key") == key) { "Jira returned another publishing issue" }
        val fields = result["fields"] as? JsonObject ?: error("Jira publishing fields unavailable")
        require(fields["summary"] is JsonPrimitive && fields["fixVersions"] is JsonArray && fields.containsKey("description")) {
            "Jira publishing fields are incomplete"
        }
        fields.getValue("fixVersions").jsonArray.forEach {
            val version = it as? JsonObject ?: error("Invalid Jira version")
            require(version.string("id")?.matches(Regex("[1-9][0-9]*")) == true && !version.string("name").isNullOrBlank()) {
                "Jira version identity unavailable"
            }
            version.string("releaseDate")?.let { java.time.LocalDate.parse(it) }
        }
        return result
    }

    /** Keep a local development task while proving the shared page key is one of its linked PM issues. */
    suspend fun requireLinkedPmIssue(localIssue: String, pmIssue: String, pmProject: String) {
        val key = Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*")
        require(key.matches(localIssue) && key.matches(pmIssue) && pmIssue.substringBeforeLast('-') == pmProject) { "Valid local and PM issue keys required" }
        val issue = request(HttpMethod.Get, endpoint("/rest/api/3/issue/$localIssue", mapOf("fields" to "issuelinks"))).second
        require(issue.string("key") == localIssue) { "Jira returned a different development issue" }
        val links = (issue["fields"] as? JsonObject)?.get("issuelinks") as? JsonArray
            ?: throw ContextRemoteException("jira_links_unavailable", "Jira did not return a complete linked-issue list")
        val linked = links.flatMap { element ->
            val link = element as? JsonObject ?: throw ContextRemoteException("jira_links_unavailable", "Jira linked-issue list is invalid")
            listOf("inwardIssue", "outwardIssue").mapNotNull { field -> (link[field] as? JsonObject)?.string("key") }
        }
        require(pmIssue in linked) { "Selected PM issue is not linked to this development issue" }
    }

    /** Authenticated recorder identity; never infer this from the credential email. */
    suspend fun currentUser(): JsonObject {
        val value = request(HttpMethod.Get, endpoint("/wiki/rest/api/user/current")).second
        if (value.containsKey("type") && value["type"] != JsonPrimitive("known"))
            throw ContextRemoteException("account_unavailable", "Authenticated Confluence account is unavailable")
        fun field(name: String): String = (value[name] as? JsonPrimitive)?.takeIf {
            it.isString && it.content.isNotBlank() && it.content.length <= 500 && it.content.none(Char::isISOControl)
        }?.content ?: throw ContextRemoteException("account_unavailable", "Authenticated Confluence account is unavailable")
        return buildJsonObject { put("accountId", field("accountId")); put("displayName", field("displayName")) }
    }

    /** A single bounded child response, never a paginated inventory or an absence proof when truncated. */
    suspend fun feedbackCandidates(config: ContextConfig, parentId: String, titles: Set<String>, limit: Int = 25): JsonObject {
        require(limit in 1..25 && config.site.trimEnd('/') == origin)
        val validation = scopeValidator(config)
        val parent = validation(parentId)
        val target = endpoint("/wiki/api/v2/${if (parent.type == "folder") "folders" else "pages"}/${id(parentId)}/direct-children", mapOf("limit" to limit.toString()))
        val (response, body) = request(HttpMethod.Get, target)
        val batch = body["results"] as? JsonArray ?: throw ContextRemoteException("malformed_list", "Feedback candidates are unavailable")
        require(batch.size <= limit) { "Feedback discovery exceeded its bound" }
        val links = if (body.containsKey("_links")) body["_links"] as? JsonObject
            ?: throw ContextRemoteException("malformed_list", "Feedback pagination metadata is invalid") else null
        val total = body["totalSize"]?.jsonPrimitive?.intOrNull
        require(total == null || total >= batch.size)
        val more = links?.containsKey("next") == true || response.headers[HttpHeaders.Link]?.contains("next", true) == true ||
            (total ?: batch.size) > batch.size
        val found = batch.map { it.jsonObject }.mapNotNull {
            val type = (it["type"] as? JsonPrimitive)?.takeIf { value -> value.isString && value.content.isNotBlank() }?.content
                ?: throw ContextRemoteException("malformed_child", "Confluence child has no content type")
            if (type !in setOf("page", "folder")) return@mapNotNull null
            val candidate = validation(it.string("id") ?: error("Missing candidate ID"))
            require(candidate.type == type && candidate.parentId == parentId)
            buildJsonObject { put("pageId", candidate.id); put("title", candidate.title); put("type", candidate.type) }
        }.filter { it["title"]?.jsonPrimitive?.content in titles }
        require(validation(parentId) == parent) { "Feedback branch changed during discovery" }
        if (config.isKnowledge) validation.validateConsumed()
        return buildJsonObject { put("complete", !more); put("results", JsonArray(found)) }
    }

    suspend fun node(id: String, type: String = "page"): RemoteNode {
        require(type in setOf("page", "folder")) { "Only pages and folders are supported" }
        val parsed = parseNode(request(HttpMethod.Get, endpoint("/wiki/api/v2/${if (type == "folder") "folders" else "pages"}/${id(id)}")).second, type)
        if (parsed.id != id) throw ContextRemoteException("identity_mismatch", "Confluence returned a different node")
        return parsed
    }

    private suspend fun list(target: String, beforeRequest: () -> Unit = {}, expectedPath: String? = null,
                             maximumResults: Int = 20000, consume: ((JsonObject) -> Unit)? = null): List<JsonObject> {
        val results = mutableListOf<JsonObject>()
        val visited = mutableSetOf<String>()
        var count = 0
        var next: String? = target
        while (next != null) {
            val current = url(next)
            if (expectedPath != null && URI(current).rawPath != expectedPath)
                throw ContextRemoteException("invalid_next", "Comment pagination changed its target endpoint")
            if (!visited.add(current) || visited.size > 2000) throw ContextRemoteException("pagination_loop", "Confluence pagination did not complete")
            beforeRequest()
            val (response, body) = request(HttpMethod.Get, current)
            val batch = body["results"] as? JsonArray ?: throw ContextRemoteException("malformed_list", "Confluence list is incomplete")
            count += batch.size
            if (count > maximumResults) throw ContextRemoteException("inventory_limit", "Confluence inventory exceeds the limit")
            batch.forEach { element ->
                val obj = element as? JsonObject ?: throw ContextRemoteException("malformed_list", "Confluence list is invalid")
                if (consume == null) results.add(obj) else consume(obj)
            }
            val links = if (body.containsKey("_links")) body["_links"] as? JsonObject
                ?: throw ContextRemoteException("invalid_next", "Confluence pagination links are invalid") else null
            val fromBody = if (links?.containsKey("next") == true) (links["next"] as? JsonPrimitive)
                ?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
                ?: throw ContextRemoteException("invalid_next", "Confluence pagination continuation is invalid") else null
            val header = response.headers[HttpHeaders.Link]
            val fromHeader = header?.split(',')?.firstOrNull { Regex("rel\\s*=\\s*\"?next\"?").containsMatchIn(it) }
                ?.substringAfter('<', "")?.substringBefore('>')?.takeIf { it.isNotBlank() }
            if (header != null && header.contains("next") && fromHeader == null)
                throw ContextRemoteException("invalid_next", "Confluence pagination header is invalid")
            if (fromBody != null && fromHeader != null && url(fromBody, current) != url(fromHeader, current))
                throw ContextRemoteException("invalid_next", "Confluence pagination links disagree")
            next = (fromBody ?: fromHeader)?.let { url(it, current) }
        }
        return results
    }

    private class CommentBudget {
        private var requests = 0
        private var comments = 0
        private var bytes = 0L
        fun request() {
            if (++requests > 256) throw ContextRemoteException("comment_limit", "Comment request budget exceeded; previous observations remain current")
        }
        fun accept(comment: JsonObject) {
            bytes += comment.toString().toByteArray(Charsets.UTF_8).size
            if (++comments > 2000 || bytes > 8L * 1024 * 1024)
                throw ContextRemoteException("comment_limit", "Comment inventory exceeds the bounded read budget")
        }
    }

    private suspend fun boundedComments(read: suspend (CommentBudget) -> JsonObject): JsonObject =
        withTimeoutOrNull(30_000) { read(CommentBudget()) }
            ?: throw ContextRemoteException("comment_timeout", "Comment read did not finish; retry without replacing previous observations")

    private suspend fun commentRequest(budget: CommentBudget, path: String, parameters: Map<String, String> = emptyMap()): JsonObject {
        budget.request()
        return request(HttpMethod.Get, endpoint(path, parameters)).second
    }

    private fun commentText(value: JsonElement?, required: Boolean = true, maximum: Int = 4096): String? {
        if (value == null || value is JsonNull) {
            require(!required) { "Required comment metadata is unavailable" }
            return null
        }
        val text = (value as? JsonPrimitive)?.takeIf { it.isString }?.content
        require(text != null && text.isNotBlank() && text.length <= maximum && text.none { it.isISOControl() }) { "Invalid comment metadata" }
        return text
    }

    /** Parent access is a separate read; a successful comment list never substitutes for it. */
    private suspend fun commentParent(provider: String, target: String, budget: CommentBudget, expectedNode: RemoteNode? = null): Pair<JsonObject, String?> {
        require(provider in setOf("jira", "confluence")) { "Unsupported comment provider" }
        if (provider == "jira") {
            require(target.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*"))) { "Invalid watched Jira issue" }
            val result = commentRequest(budget, "/rest/api/3/issue/$target", mapOf("fields" to "status,summary"))
            require(result.string("key") == target) { "Comment parent issue identity mismatch" }
            val fields = result["fields"] as? JsonObject ?: error("Comment parent fields unavailable")
            val status = fields["status"] as? JsonObject ?: error("Comment parent status unavailable")
            val category = commentText((status["statusCategory"] as? JsonObject)?.get("key"))!!
            require(category in setOf("new", "indeterminate", "done")) { "Comment parent status category unavailable" }
            val title = commentText(fields["summary"])!!
            val issueId = result.string("id")?.let(::id)
            return buildJsonObject {
                put("id", target); put("title", title); put("status", commentText(status["name"], required = false) ?: category)
                put("closed", category == "done")
            } to issueId
        }
        id(target)
        val metadata = commentRequest(budget, "/wiki/api/v2/pages/$target")
        val node = parseNode(metadata, "page")
        require(node.id == target) { "Comment parent page identity mismatch" }
        require(expectedNode == null || metadata.string("status") == "current" && node == expectedNode) { "Comment parent left its authorized identity or hierarchy" }
        return buildJsonObject {
            put("id", target); put("title", node.title); put("status", node.status); put("closed", node.status != "current")
        } to null
    }

    private fun jiraComment(raw: JsonObject, target: String, issueId: String?): JsonObject {
        val commentId = id(commentText(raw["id"])!!)
        raw["self"]?.let { source ->
            val uri = try { URI(commentText(source)!!) } catch (_: Exception) { error("Invalid Jira comment identity URL") }
            val allowed = listOfNotNull(target, issueId).map { "/rest/api/3/issue/$it/comment/$commentId" }
            require(uri.scheme == originUri.scheme && uri.host == originUri.host && uri.port == originUri.port &&
                uri.rawUserInfo == null && uri.rawFragment == null && uri.rawQuery == null && uri.rawPath in allowed) { "Jira comment belongs to another source" }
        }
        val body = raw["body"] as? JsonObject ?: error("Comment ADF body unavailable")
        require(body.string("type") == "doc" && body["content"] is JsonArray) { "Comment ADF body is invalid" }
        val author = commentText((raw["author"] as? JsonObject)?.get("accountId"), required = false, maximum = 256)
        val editor = commentText((raw["updateAuthor"] as? JsonObject)?.get("accountId"), required = false, maximum = 256)
        return buildJsonObject {
            put("id", commentId); put("kind", "jira"); put("body", body); put("format", "adf"); put("version", JsonNull)
            put("updatedAt", commentText(raw["updated"], maximum = 100)!!)
            put("authorId", author?.let(::JsonPrimitive) ?: JsonNull); put("editorId", editor?.let(::JsonPrimitive) ?: JsonNull)
            put("status", "current")
            raw["visibility"]?.takeUnless { it is JsonNull }?.let { require(it is JsonObject) { "Invalid comment visibility" }; put("visibility", it) }
        }
    }

    private fun confluenceComment(raw: JsonObject, target: String, kind: String, expectedParentId: String? = null,
                                  root: Boolean = false): JsonObject {
        require(kind in setOf("footer", "inline")) { "Unsupported Confluence comment kind" }
        val commentId = id(commentText(raw["id"])!!)
        val pageId = commentText(raw["pageId"], required = false)
        val parentId = commentText(raw["parentCommentId"], required = false)?.let(::id)
        require(pageId == null || pageId == target) { "Comment belongs to another page" }
        require(listOf("blogPostId", "attachmentId", "customContentId").none { raw[it]?.let { value -> value !is JsonNull } == true }) { "Only comments on the selected page are supported" }
        if (root) require(pageId == target && parentId == null) { "Root comment page identity mismatch" }
        if (expectedParentId != null) require(parentId == expectedParentId) { "Comment reply parent mismatch" }
        val version = raw["version"] as? JsonObject ?: error("Comment version unavailable")
        val number = (version["number"] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
        require(number != null && number > 0) { "Invalid comment version" }
        val editor = commentText(version["authorId"], required = false, maximum = 256)
        val storage = (raw["body"] as? JsonObject)?.get("storage") as? JsonObject ?: error("Comment storage body unavailable")
        val body = storage["value"] as? JsonPrimitive
        require(body != null && body.isString) { "Comment storage body unavailable" }
        return buildJsonObject {
            put("id", commentId); put("kind", kind); parentId?.let { put("parentId", it) }
            put("body", body); put("format", "storage"); put("version", number)
            put("updatedAt", commentText(version["createdAt"], maximum = 100)!!)
            put("authorId", if (number == 1) editor?.let(::JsonPrimitive) ?: JsonNull else JsonNull)
            put("editorId", editor?.let(::JsonPrimitive) ?: JsonNull)
            put("status", commentText(raw["status"])!!)
            if (kind == "inline") putJsonObject("inlineContext") {
                listOf("resolutionStatus", "resolutionLastModifierId", "resolutionLastModifiedAt").forEach { name ->
                    raw[name]?.takeUnless { it is JsonNull }?.let { put(name, commentText(it)!!) }
                }
                val properties = raw["properties"] as? JsonObject
                listOf("inlineMarkerRef", "inlineOriginalSelection").forEach { name ->
                    properties?.get(name)?.takeUnless { it is JsonNull }?.let { value ->
                        val text = value as? JsonPrimitive
                        require(text != null && text.isString && text.content.length <= 65536) { "Invalid inline comment context" }
                        put(name, text)
                    }
                }
            }
        }
    }

    /** Complete current observations only. Failure/absence never becomes an authenticated deletion. */
    suspend fun commentInventory(provider: String, target: String, expectedNode: RemoteNode? = null): JsonObject = boundedComments { budget ->
        val (parent, issueId) = commentParent(provider, target, budget, expectedNode)
        val comments = linkedMapOf<String, JsonObject>()
        fun accept(comment: JsonObject) {
            val key = comment.getValue("id").jsonPrimitive.content
            require(key !in comments) { "Comment inventory has a duplicate or cycle" }
            budget.accept(comment); comments[key] = comment
        }
        if (provider == "jira") {
            var offset = 0
            var expectedTotal: Int? = null
            do {
                val result = commentRequest(budget, "/rest/api/3/issue/$target/comment",
                    mapOf("startAt" to offset.toString(), "maxResults" to "100", "orderBy" to "created"))
                fun number(name: String) = (result[name] as? JsonPrimitive)?.takeIf { !it.isString }?.intOrNull
                val total = number("total") ?: error("Comment total unavailable")
                val maximum = number("maxResults") ?: error("Comment page size unavailable")
                val entries = result["comments"] as? JsonArray ?: error("Comment page is incomplete")
                require(number("startAt") == offset && total in 0..2000 && maximum in 0..2000 && entries.size <= maximum && offset + entries.size <= total) { "Comment pagination metadata is invalid" }
                require(expectedTotal == null || total == expectedTotal) { "Comment inventory changed during pagination; retry" }
                expectedTotal = total
                require(entries.isNotEmpty() || offset == total) { "Comment pagination ended before its declared total" }
                entries.forEach { accept(jiraComment(it as? JsonObject ?: error("Invalid comment item"), target, issueId)) }
                offset += entries.size
                result["isLast"]?.let { flag -> require(flag is JsonPrimitive && !flag.isString && flag.booleanOrNull == (offset == total)) { "Comment pagination completion disagrees" } }
            } while (offset < expectedTotal!!)
        } else {
            for (kind in listOf("footer", "inline")) {
                val pending = java.util.ArrayDeque<String>()
                suspend fun collect(path: String, parentId: String?) {
                    list(endpoint(path, mapOf("body-format" to "storage", "limit" to "100")),
                        beforeRequest = budget::request, expectedPath = path, maximumResults = 2000) { raw ->
                        val comment = confluenceComment(raw, target, kind, parentId, root = parentId == null)
                        accept(comment); pending.add(comment.getValue("id").jsonPrimitive.content)
                    }
                }
                collect("/wiki/api/v2/pages/$target/$kind-comments", null)
                while (pending.isNotEmpty()) {
                    val parentId = pending.removeFirst()
                    collect("/wiki/api/v2/$kind-comments/$parentId/children", parentId)
                }
            }
        }
        require(commentParent(provider, target, budget, expectedNode).first == parent) { "Comment parent changed during inventory; retry" }
        buildJsonObject { put("provider", provider); put("target", target); put("parent", parent); put("comments", JsonArray(comments.values.toList())) }
    }

    /** Reread only a caller-bound watched comment; no self/next URL from content is followed. */
    suspend fun commentDetail(provider: String, target: String, commentId: String, kind: String,
                              expectedParentId: String? = null, expectedNode: RemoteNode? = null): JsonObject = boundedComments { budget ->
        id(commentId); expectedParentId?.let(::id)
        val (parent, issueId) = commentParent(provider, target, budget, expectedNode)
        if (provider == "jira") {
            require(kind == "jira" && expectedParentId == null) { "Invalid watched Jira comment kind or parent" }
            val comment = jiraComment(commentRequest(budget, "/rest/api/3/issue/$target/comment/$commentId"), target, issueId)
            require(comment.string("id") == commentId) { "Comment identity mismatch" }
            budget.accept(comment)
            require(commentParent(provider, target, budget, expectedNode).first == parent) { "Comment parent changed during reread" }
            comment
        } else {
            require(kind in setOf("footer", "inline")) { "Invalid watched Confluence comment kind" }
            val raw = commentRequest(budget, "/wiki/api/v2/$kind-comments/$commentId", mapOf("body-format" to "storage"))
            val comment = confluenceComment(raw, target, kind, expectedParentId).toMutableMap()
            require((comment["id"] as? JsonPrimitive)?.content == commentId) { "Comment identity mismatch" }
            require((comment["parentId"] as? JsonPrimitive)?.contentOrNull == expectedParentId) { "Comment thread parent changed" }
            // Reply reads can omit pageId; prove the selected page through the actual parent chain.
            var current = raw
            val ancestors = mutableSetOf(commentId)
            while (current.string("pageId") == null) {
                val ancestor = id(commentText(current["parentCommentId"])!!)
                require(ancestors.add(ancestor)) { "Comment ancestor cycle" }
                current = commentRequest(budget, "/wiki/api/v2/$kind-comments/$ancestor", mapOf("body-format" to "storage"))
                val observed = confluenceComment(current, target, kind)
                require(observed.string("id") == ancestor) { "Comment ancestor identity mismatch" }
                budget.accept(observed)
            }
            require(current.string("pageId") == target) { "Comment page scope is unverified" }
            if (comment.getValue("version").jsonPrimitive.int > 1) {
                val original = try {
                    commentRequest(budget, "/wiki/api/v2/$kind-comments/$commentId/versions/1").also {
                        require((it["number"] as? JsonPrimitive)?.let { n -> !n.isString && n.intOrNull == 1 } == true) { "Original comment version identity mismatch" }
                    }
                } catch (failure: ContextRemoteException) {
                    if (failure.code !in setOf("http_403", "http_404")) throw failure
                    null
                }
                comment["authorId"] = commentText(original?.get("authorId"), required = false, maximum = 256)?.let(::JsonPrimitive) ?: JsonNull
            }
            val normalized = JsonObject(comment)
            budget.accept(normalized)
            require(commentParent(provider, target, budget, expectedNode).first == parent) { "Comment parent changed during reread" }
            normalized
        }
    }

    internal suspend fun scopeNode(contentId: String, type: String = "page"): RemoteNode {
        val value = request(HttpMethod.Get, endpoint("/wiki/api/v2/${if (type == "folder") "folders" else "pages"}/${id(contentId)}")).second
        require(value.string("status") == "current") { "Knowledge content status must be explicitly current" }
        val result = parseNode(value, type)
        require(result.id == contentId) { "Knowledge content identity changed" }
        return result
    }

    internal class ScopeValidation(private val authorize: suspend (String) -> RemoteNode,
                                   private val verifyConsumed: suspend () -> Unit) {
        suspend operator fun invoke(pageId: String): RemoteNode = authorize(pageId)
        suspend fun validateConsumed() = verifyConsumed()
    }

    private data class BodyScope(val admission: ScopeValidation, val finish: suspend () -> Unit)

    /** Only one body's final pass gets its own bounded generation; group admission keeps its cumulative cap. */
    private suspend fun bodyScope(config: ContextConfig): BodyScope {
        var finish: (suspend () -> Unit)? = null
        val admission = scopeValidator(config) { finish = it }
        return BodyScope(admission, requireNotNull(finish))
    }

    /** Observations live for one operation; exported groups require a fresh final ancestor generation. */
    internal suspend fun scopeValidator(config: ContextConfig): ScopeValidation = scopeValidator(config, null)

    private suspend fun scopeValidator(config: ContextConfig, bodyFinalizer: ((suspend () -> Unit) -> Unit)?): ScopeValidation {
        require(config.site.trimEnd('/') == origin) { "Selected site differs from the credential origin" }
        val observed = linkedMapOf<String, RemoteNode>()
        var requests = 0
        suspend fun fetch(id: String, type: String): RemoteNode {
            if (++requests > 256) throw ContextRemoteException("scope_limit", "Knowledge authorization exceeds the metadata request budget; reduce the selected watch or search scope")
            return scopeNode(id, type)
        }
        suspend fun content(id: String, fresh: Boolean = false): RemoteNode {
            if (!fresh) observed[id]?.let { return it }
            val node = try { fetch(id, "page") } catch (failure: ContextRemoteException) {
                if (failure.code != "http_404") throw failure
                fetch(id, "folder")
            }
            require(node.spaceId == config.spaceId && node.status == "current") { "Knowledge scope requires current content in the registered space" }
            require(observed[id] == null || observed[id] == node) { "Knowledge metadata changed during authorization" }
            observed[id] = node
            return node
        }
        val home = content(config.homePageId)
        val boundaryId = if (config.isKnowledge) config.knowledgeRootPageId ?: home.id else home.id
        val boundary = content(boundaryId)
        suspend fun reaches(start: RemoteNode, ancestor: String) {
            var cursor = start
            val seen = mutableSetOf<String>()
            while (true) {
                require(seen.add(cursor.id) && seen.size <= 100) { "Cyclic or excessive knowledge hierarchy" }
                if (cursor.id == ancestor) return
                require(cursor.id != home.id) { "Content is outside the registered knowledge root" }
                val parent = cursor.parentId ?: error("Content is outside the registered knowledge scope")
                require(parent != home.parentId) { "Content is outside the registered knowledge home" }
                cursor = content(parent)
            }
        }
        if (config.isKnowledge) reaches(boundary, home.id)
        val consumed = linkedSetOf(boundary.id)
        suspend fun verifyConsumed(fetchCurrent: suspend (String, String) -> RemoteNode) {
            val graph = linkedMapOf<String, RemoteNode>()
            consumed.forEach { selectedId ->
                var cursor = observed.getValue(selectedId)
                val seen = mutableSetOf<String>()
                while (true) {
                    require(seen.add(cursor.id)) { "Cyclic consumed knowledge ancestry" }
                    graph[cursor.id] = cursor
                    if (cursor.id == home.id) break
                    cursor = observed[cursor.parentId] ?: error("Consumed knowledge ancestry is incomplete")
                }
            }
            fun depth(node: RemoteNode): Int {
                var cursor = node
                var depth = 0
                val seen = mutableSetOf<String>()
                while (cursor.id != home.id) {
                    require(seen.add(cursor.id)) { "Cyclic consumed knowledge ancestry" }
                    cursor = graph[cursor.parentId] ?: error("Consumed knowledge ancestry is incomplete")
                    depth++
                }
                return depth
            }
            // Compare the original observations; never replace them with a newly authorized path.
            graph.values.sortedByDescending(::depth).forEach { previous ->
                require(fetchCurrent(previous.id, previous.type) == previous) { "Knowledge ancestry changed before its observations could be exported" }
            }
        }
        bodyFinalizer?.invoke {
            var finalRequests = 0
            verifyConsumed { id, type ->
                if (++finalRequests > 256) throw ContextRemoteException("scope_limit", "Knowledge final body verification exceeds the metadata request budget")
                scopeNode(id, type)
            }
        }
        return ScopeValidation(authorize = { pageId ->
            val selected = content(pageId, fresh = true)
            if (config.isKnowledge) reaches(selected, boundary.id)
            consumed += selected.id
            selected
        }, verifyConsumed = { verifyConsumed { id, type -> fetch(id, type) } })
    }

    /** Fresh authorization within the registered home; its known parent is never traversed. */
    suspend fun requireScope(config: ContextConfig, pageId: String): RemoteNode {
        val validation = scopeValidator(config)
        val selected = validation(pageId)
        if (config.isKnowledge) validation.validateConsumed()
        return selected
    }

    private suspend fun knowledgeInventory(config: ContextConfig): List<RemoteNode> {
        val boundary = requireScope(config, config.knowledgeRootPageId ?: config.homePageId)
        val nodes = linkedMapOf(boundary.id to boundary)
        val pending = java.util.ArrayDeque<Pair<RemoteNode, Int>>()
        pending.add(boundary to 0)
        while (pending.isNotEmpty()) {
            val (parent, depth) = pending.removeFirst()
            require(depth <= 100) { "Knowledge inventory exceeds the hierarchy depth limit" }
            // Re-admit the complete current ancestry even if this parent's own metadata is unchanged.
            require(requireScope(config, parent.id) == parent) { "Knowledge parent left its admitted ancestry before descendant traversal" }
            for (child in children(parent.id, parent.type)) {
                require(scopeNode(child.id, child.type) == child) { "Knowledge child metadata changed during inventory" }
                require(child.spaceId == config.spaceId && child.status == "current" && child.parentId == parent.id) { "Knowledge inventory left its registered scope" }
                require(child.id !in nodes && nodes.size < 20000) { "Cyclic or excessive knowledge inventory" }
                nodes[child.id] = child
                pending.add(child to depth + 1)
            }
        }
        // A target can be unchanged while an ancestor moves during descendant consumption.
        // Refresh descendants before their ancestors, then validate that complete fresh generation.
        // The final metadata pass is linear; each earlier ancestry admission is separately depth-bounded.
        // There is no shared search/watch request budget across this 20,000-node inventory.
        val verified = linkedMapOf<String, RemoteNode>()
        nodes.values.toList().asReversed().forEach { observed ->
            val current = scopeNode(observed.id, observed.type)
            require(current == observed) { "Knowledge hierarchy changed during inventory; retry the complete generation" }
            verified[current.id] = current
        }
        verified.values.forEach { node ->
            var cursor = node
            val seen = mutableSetOf<String>()
            var depth = 0
            while (true) {
                require(seen.add(cursor.id) && depth <= 100 && cursor.spaceId == config.spaceId && cursor.status == "current") {
                    "Knowledge inventory has an invalid fresh ancestry"
                }
                if (cursor.id == boundary.id) break
                cursor = verified[cursor.parentId] ?: error("Knowledge inventory no longer reaches its registered root")
                depth++
            }
        }
        require(requireScope(config, boundary.id) == boundary) { "Registered knowledge root changed during inventory" }
        return nodes.values.toList()
    }

    /** One bounded metadata response. This intentionally does not reuse the complete inventory crawler. */
    suspend fun searchContext(config: ContextConfig, query: String, limit: Int = 10): JsonObject =
        withTimeoutOrNull(30_000) { searchContextBounded(config, query, limit) }
            ?: throw ContextRemoteException("search_timeout", "Confluence search did not finish within its read budget")

    private suspend fun searchContextBounded(config: ContextConfig, query: String, limit: Int): JsonObject {
        require(config.site.trimEnd('/') == origin) { "Project site and configured Atlassian origin differ" }
        require(query.isNotBlank() && query.length <= 200 && query.none { it.isISOControl() } && limit in 1..25) {
            "Supply a search query of 1..200 characters and a limit of 1..25"
        }
        val authorize = if (config.isKnowledge) scopeValidator(config) else null
        val scope = if (config.isKnowledge) " and (id = ${config.knowledgeRootPageId ?: config.homePageId} or ancestor = ${config.knowledgeRootPageId ?: config.homePageId})" else ""
        val escaped = query.replace("\\", "\\\\").replace("\"", "\\\"")
        val target = endpoint("/wiki/rest/api/content/search", mapOf(
            "cql" to "space = \"${config.space}\" and type = page$scope and text ~ \"$escaped\"",
            "cqlcontext" to """{"contentStatuses":["current"]}""",
            "expand" to "space,version,ancestors", "limit" to limit.toString()))
        val (response, body) = request(HttpMethod.Get, target)
        val batch = body["results"] as? JsonArray
            ?: throw ContextRemoteException("malformed_list", "Confluence search is incomplete")
        require(batch.size <= limit) { "Confluence search exceeded the requested bound" }
        val nodes = batch.map { value ->
            val metadata = value as? JsonObject
                ?: throw ContextRemoteException("malformed_list", "Confluence search metadata is invalid")
            val node = parseNode(metadata, "page")
            require(node.spaceId == config.spaceId && metadata.string("status") == "current") { "Search returned an out-of-scope page" }
            if (authorize != null) require(authorize(node.id) == node) { "Knowledge search metadata changed during authorization" }
            node
        }
        require(nodes.map { it.id }.toSet().size == nodes.size) { "Duplicate search result identity" }
        val links = if (body.containsKey("_links")) body["_links"] as? JsonObject
            ?: throw ContextRemoteException("invalid_next", "Confluence search links are invalid") else null
        val fromBody = if (links?.containsKey("next") == true) (links["next"] as? JsonPrimitive)
            ?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
            ?: throw ContextRemoteException("invalid_next", "Confluence search continuation is invalid") else null
        val header = response.headers[HttpHeaders.Link]
        val nextHeaders = header?.split(',')?.filter { Regex("""rel\s*=\s*"?next"?""").containsMatchIn(it) } ?: emptyList()
        require(nextHeaders.size <= 1) { "Ambiguous Confluence search continuation" }
        val fromHeader = nextHeaders.firstOrNull()?.substringAfter('<', "")?.substringBefore('>')?.takeIf { it.isNotBlank() }
        if (header != null && header.contains("next", ignoreCase = true) && fromHeader == null)
            throw ContextRemoteException("invalid_next", "Confluence search continuation header is invalid")
        fun continuation(value: String): String = url(value, target).also {
            require(URI(it).rawPath == "/wiki/rest/api/content/search") { "Confluence search continuation changed endpoint" }
        }
        val bodyNext = fromBody?.let(::continuation)
        val headerNext = fromHeader?.let(::continuation)
        require(bodyNext == null || headerNext == null || bodyNext == headerNext) { "Confluence search links disagree" }
        val total = if (body.containsKey("totalSize")) {
            val reported = body["totalSize"] as? JsonPrimitive
            require(reported != null && !reported.isString) { "Invalid Confluence search total" }
            reported.intOrNull ?: throw ContextRemoteException("malformed_list", "Confluence search total is invalid")
        } else null
        require(total == null || total >= nodes.size) { "Invalid Confluence search total" }
        val hasMore = bodyNext != null || headerNext != null || (total != null && total > nodes.size)
        authorize?.validateConsumed()
        return buildJsonObject {
            put("status", "searched"); put("count", nodes.size); put("hasMore", hasMore); put("complete", !hasMore)
            put("results", JsonArray(nodes.map { node -> buildJsonObject {
                put("pageId", node.id); put("title", node.title); put("version", node.version); put("spaceId", node.spaceId)
                node.parentId?.let { put("parentId", it) }
                put("url", "$origin/wiki/spaces/${config.space}/pages/${node.id}")
            } }))
        }
    }

    suspend fun search(cql: String): List<RemoteNode> {
        require(cql.isNotBlank() && cql.length <= 10000) { "Bounded CQL required" }
        val objects = list(endpoint("/wiki/rest/api/content/search", mapOf("cql" to cql, "expand" to "space,version,ancestors", "limit" to "250")))
        return objects.map { candidate ->
            val type = candidate.string("type") ?: "page"
            require(type in setOf("page", "folder")) { "Unsupported Confluence content type" }
            try { parseNode(candidate, type) }
            catch (_: ContextRemoteException) { node(candidate.string("id") ?: error("Missing Confluence result ID"), type) }
        }.distinctBy { it.id }
    }

    suspend fun children(id: String, type: String = "page"): List<RemoteNode> {
        require(type in setOf("page", "folder")) { "Only page/folder children are supported" }
        val children = list(endpoint("/wiki/api/v2/${if (type == "folder") "folders" else "pages"}/${id(id)}/direct-children", mapOf("limit" to "250")))
        return children.mapNotNull { child ->
            val childType = child.string("type") ?: throw ContextRemoteException("malformed_child", "Confluence child has no content type")
            if (childType !in setOf("page", "folder")) return@mapNotNull null
            val result = node(child.string("id") ?: error("Missing child ID"), childType)
            if (result.parentId != id) throw ContextRemoteException("hierarchy_changed", "Confluence hierarchy changed during listing")
            result
        }.distinctBy { it.id }
    }

    suspend fun inventory(config: ContextConfig): List<RemoteNode> {
        if (config.isKnowledge) return knowledgeInventory(config)
        require(config.site.trimEnd('/') == origin) { "Project site and configured Atlassian origin differ" }
        val all = linkedMapOf<String, RemoteNode>()
        val pages = list(endpoint("/wiki/api/v2/spaces/${id(config.spaceId)}/pages", mapOf("limit" to "250")))
        pages.forEach { entry -> val n = parseNode(entry, "page"); all[n.id] = n }
        search("space = \"${config.space}\" and type = folder").forEach { all[it.id] = it }
        val home = node(config.homePageId)
        require(home.spaceId == config.spaceId) { "Configured home belongs to another space" }
        all[home.id] = home
        val pending = java.util.ArrayDeque<String>()
        all.values.mapNotNull { it.parentId }.filter { it !in all }.forEach(pending::add)
        while (pending.isNotEmpty()) {
            val parentId = pending.removeFirst()
            if (parentId in all) continue
            if (all.size >= 20000) throw ContextRemoteException("inventory_limit", "Confluence inventory exceeds the limit")
            val parent = try { node(parentId, "folder") } catch (e: ContextRemoteException) {
                if (e.code != "http_404") throw e
                node(parentId)
            }
            require(parent.spaceId == config.spaceId) { "Confluence parent belongs to another space" }
            all[parent.id] = parent
            parent.parentId?.takeIf { it !in all }?.let(pending::add)
        }
        require(all.values.all { it.spaceId == config.spaceId }) { "Confluence inventory contains another space" }
        all.values.forEach { n ->
            var current: RemoteNode? = n
            val seen = mutableSetOf<String>()
            while (current != null) {
                require(seen.add(current.id) && seen.size <= 100) { "Confluence parent cycle or excessive depth" }
                current = current.parentId?.let { all[it] ?: error("Confluence inventory has a missing parent") }
            }
        }
        return all.values.toList()
    }

    private suspend fun mutationPage(pageId: String, scope: ContextConfig?, expectedNode: RemoteNode? = null, freshStorage: Boolean = false): RemotePage {
        val authorization = if (scope?.isKnowledge == true) bodyScope(scope) else null
        val validation = authorization?.admission
        val admitted = validation?.invoke(pageId) ?: expectedNode
        if (expectedNode != null && admitted != expectedNode) throw ContextRemoteException("readback_mismatch",
            "Mutation page differs from its expected native identity", details = buildJsonObject {
                put("mismatchFields", JsonArray((if (admitted == null) listOf("identity") else nodeMismatch(admitted, expectedNode)).map(::JsonPrimitive)))
            })
        // Complete admission before consuming independent metadata or cached/body content.
        // The body reader also performs a separate fresh final generation before returning.
        validation?.validateConsumed()
        return if (validation == null) page(pageId, admitted, scope, freshStorage)
            else pageStorage(pageId, admitted, scope, freshStorage, requireNotNull(authorization) to requireNotNull(admitted))
    }

    @JvmOverloads
    suspend fun ensureNoDraft(id: String, expectedNode: RemoteNode? = null, scope: ContextConfig? = null) {
        val before = mutationPage(id, scope, expectedNode)
        require(before.node.status == "current") { "Only published current pages may be updated" }
        fun draftIdentity(node: RemoteNode): RemoteNode {
            require(node.status in setOf("current", "draft") && node.version in 1..before.node.version) { "Draft identity or revision is invalid" }
            return if (node.status == "draft") node.copy(status = "current", version = before.node.version) else node
        }
        val draft = try {
            // Admit the draft's identity before requesting its storage; a current page can have a divergent draft.
            val path = "/wiki/api/v2/pages/${id(id)}"
            val metadata = request(HttpMethod.Get, endpoint(path, mapOf("get-draft" to "true"))).second
            require(metadata.string("status") in setOf("current", "draft")) { "Draft status is unavailable" }
            val node = parseNode(metadata, "page")
            require(draftIdentity(node) == before.node) { "Draft left the expected page identity or hierarchy" }
            val message = (metadata["version"] as? JsonObject)?.string("message") ?: ""
            if (scope?.isKnowledge == true) require(requireScope(scope, id) == before.node) { "Page left the selected root during draft verification" }
            val storage = request(HttpMethod.Get, endpoint(path, mapOf("body-format" to "storage", "get-draft" to "true"))).second
            require(storage.string("status") == node.status) { "Draft changed during its storage read" }
            parsePage(storage, id).also {
                require(it.node == node && it.versionMessage == message) { "Draft changed during its storage read" }
                if (scope?.isKnowledge == true) require(requireScope(scope, id) == before.node) { "Page left the selected root during draft verification" }
            }
        } catch (e: ContextRemoteException) {
            // A 404 can mask a permission failure. Current-page access alone cannot prove draft absence.
            if (e.code == "http_404") throw ContextRemoteException("draft_unverified", "Draft absence could not be verified; automatic update is blocked")
            throw e
        }
        // Automatic drafts can retain an older independent revision after publication.
        // Ignore only that bounded revision and its empty message, never content or identity.
        val identicalAutomaticDraft = draft.node.status == "draft" && draft.node.version in 1..before.node.version &&
            draft.node.copy(status = "current", version = before.node.version) == before.node && StorageIdentity.equivalent(draft.body, before.body) &&
            draft.versionMessage in setOf("", before.versionMessage)
        require((draft.node.status == "current" && samePublishedSnapshot(draft, before)) || identicalAutomaticDraft) {
            "Unpublished or divergent draft prevents automatic update"
        }
    }

    suspend fun spaces(): JsonArray = JsonArray(list(endpoint("/wiki/api/v2/spaces", mapOf("limit" to "250"))).map { entry ->
        val spaceId = id(entry.string("id") ?: error("Missing space ID"))
        val key = entry.string("key")?.takeIf { it.matches(Regex("[A-Za-z0-9_-]{1,100}")) } ?: error("Invalid space key")
        val home = entry.string("homepageId") ?: entry.string("homePageId")
        buildJsonObject {
            put("id", spaceId); put("key", key); put("name", entry.string("name") ?: key)
            home?.let { put("homePageId", id(it)) }
        }
    })

    @JvmOverloads
    suspend fun create(spaceId: String, parentId: String, title: String, body: String, message: String, scope: ContextConfig? = null,
                       beforeEffect: suspend () -> Unit = {}): RemotePage {
        id(spaceId); id(parentId)
        if (scope?.isKnowledge == true) {
            require(scope.spaceId == spaceId) { "Creation space differs from the selected knowledge space" }
            requireScope(scope, parentId)
        }
        require(title.isNotBlank() && body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024 && message.isNotBlank()) { "Bounded document and change reason required" }
        val space = spaces().map { it.jsonObject }.singleOrNull { it.string("id") == spaceId }
            ?: throw ContextRemoteException("space_unavailable", "Configured space is not uniquely accessible")
        // Official Content API examples still document this create endpoint. V2 create lacks version.message.
        val payload = buildJsonObject {
            put("type", "page"); put("status", "current"); put("title", title)
            putJsonObject("space") { put("key", space.string("key")!!) }
            putJsonArray("ancestors") { add(buildJsonObject { put("id", parentId) }) }
            putJsonObject("body") { putJsonObject("storage") { put("representation", "storage"); put("value", body) } }
            putJsonObject("version") { put("number", 1); put("message", message) }
        }
        if (scope?.isKnowledge == true) requireScope(scope, parentId)
        beforeEffect()
        val response = request(HttpMethod.Post, endpoint("/wiki/rest/api/content"), payload).second
        val createdId = observedId(response) ?: throw ContextRemoteException("unknown_create", "Creation response has no valid page ID; reconcile before retrying", true)
        val expected = RemoteNode(createdId, title, parentId, spaceId = spaceId, version = 1)
        val saved = try { mutationPage(createdId, scope, expected) } catch (e: CancellationException) { throw e }
            catch (e: Exception) {
                val details = (e as? ContextRemoteException)?.details?.takeIf { it.isNotEmpty() }
                    ?: buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("readback")))) }
                throw ContextRemoteException("unknown_create", "Created page readback could not be verified", true,
                    JsonObject(details + ("reference" to JsonPrimitive(createdId))))
            }
        val mismatches = nodeMismatch(saved.node, expected) +
            listOfNotNull("content".takeUnless { writeReadbackMatches(scope, saved.body, body) }, "message".takeUnless { saved.versionMessage == message })
        if (mismatches.isNotEmpty()) throw ContextRemoteException("unknown_create", "Created page readback differs from the requested fields", true,
            buildJsonObject { put("mismatchFields", JsonArray(mismatches.map(::JsonPrimitive))); put("reference", createdId) })
        return saved
    }

    @JvmOverloads
    suspend fun update(before: RemotePage, body: String, message: String, scope: ContextConfig? = null,
                       beforeEffect: suspend () -> Unit = {}): RemotePage =
        updateDocument(before, body, message, scope, before.node.title, beforeEffect, allowUnknownReadback = true)

    /** Moving deeper may add restrictions, but must never discard an inherited audience boundary. */
    private suspend fun relocationAncestry(config: ContextConfig, sourceId: String, parentId: String): Pair<List<RemoteNode>, List<RemoteNode>> {
        val observed = linkedMapOf<String, RemoteNode>()
        suspend fun node(id: String): RemoteNode = observed[id] ?: (try { scopeNode(id) } catch (e: ContextRemoteException) {
            if (e.code != "http_404") throw e
            scopeNode(id, "folder")
        }).also { current ->
            require(current.spaceId == config.spaceId && current.status == "current") { "Move ancestry must remain current in the registered space" }
            observed[id] = current
        }
        suspend fun path(id: String): List<RemoteNode> {
            val result = mutableListOf<RemoteNode>()
            var current = id
            while (true) {
                require(result.size < 100 && result.none { it.id == current }) { "Cyclic or excessive move ancestry" }
                val selected = node(current); result += selected
                if (selected.id == config.homePageId) return result
                current = selected.parentId ?: error("Move ancestry left its registered home")
            }
        }
        val source = path(sourceId)
        val destination = path(parentId)
        require(destination.none { it.id == sourceId }) { "A page cannot move into itself or its descendants" }
        require(destination.map { it.id }.containsAll(source.drop(1).map { it.id })) {
            "Move must retain every existing ancestor to preserve inherited access restrictions"
        }
        // Both paths share observations; verify descendants before their shared ancestors.
        observed.values.sortedByDescending { current ->
            var depth = 0
            var parent = current.parentId
            while (parent != null && parent in observed) { depth++; parent = observed[parent]?.parentId }
            depth
        }.forEach { require(scopeNode(it.id, it.type) == it) { "Move ancestry changed during authorization" } }
        return source to destination
    }

    internal suspend fun updateDocument(before: RemotePage, body: String, message: String, scope: ContextConfig?,
                                        title: String, beforeEffect: suspend () -> Unit, allowUnknownReadback: Boolean = false,
                                        parentId: String? = null): RemotePage {
        val move = parentId?.let {
            id(it)
            require(scope?.isKnowledge == true && before.node.id !in setOf(scope.homePageId, scope.knowledgeRootPageId) && it != before.node.id) {
                "Only a knowledge page below its registered ROOT may be moved"
            }
            val parent = requireScope(scope, it)
            require(parent.spaceId == before.node.spaceId && parent.type in setOf("page", "folder")) { "Move destination must remain in the registered space" }
            relocationAncestry(scope, before.node.id, it).also { paths ->
                require(paths.first.first() == before.node && paths.second.first() == parent) { "Move source or destination changed" }
            }
        }
        require(title.isNotBlank() && title.length <= 1000 && title.none(Char::isISOControl))
        require(before.node.version in 1 until Int.MAX_VALUE && before.node.status == "current" && message.isNotBlank() &&
            body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "Published bounded document and change reason required" }
        if (scope?.isKnowledge != true) ensureNoDraft(before.node.id, before.node, scope)
        require(samePublishedSnapshot(mutationPage(before.node.id, scope, before.node), before)) { "Page changed before update; prepare a fresh preview" }
        val payload = buildJsonObject {
            put("id", before.node.id); put("title", title); put("status", "current")
            parentId?.let { put("parentId", it) }
            putJsonObject("body") { put("representation", "storage"); put("value", body) }
            putJsonObject("version") { put("number", before.node.version + 1); put("message", message) }
        }
        if (move != null) require(relocationAncestry(scope!!, before.node.id, parentId!!) == move) { "Move source or destination changed before update" }
        beforeEffect()
        try { request(HttpMethod.Put, endpoint("/wiki/api/v2/pages/${id(before.node.id)}"), payload) }
        catch (e: ContextRemoteException) { if (!e.outcomeUnknown || !allowUnknownReadback) throw e }
        val saved = try { mutationPage(before.node.id, scope, before.node.copy(title = title, parentId = parentId ?: before.node.parentId, version = before.node.version + 1)) } catch (e: CancellationException) { throw e }
            catch (e: Exception) { throw ContextRemoteException("unknown_update", "Updated page readback could not be verified", true,
                (e as? ContextRemoteException)?.details?.takeIf { it.isNotEmpty() } ?: buildJsonObject { put("mismatchFields", JsonArray(listOf(JsonPrimitive("readback")))) }) }
        val mismatches = nodeMismatch(saved.node, before.node.copy(title = title, parentId = parentId ?: before.node.parentId, version = before.node.version + 1)) +
            listOfNotNull("content".takeUnless { writeReadbackMatches(scope, saved.body, body) }, "message".takeUnless { saved.versionMessage == message })
        if (mismatches.isNotEmpty()) throw ContextRemoteException("unknown_update", "Updated page readback differs from the requested fields", true,
            buildJsonObject { put("mismatchFields", JsonArray(mismatches.map(::JsonPrimitive))) })
        return saved
    }

    /** One native page, never an eager inventory. Continuations can only change native paging parameters. */
    internal suspend fun wikiBatch(path: String, parameters: Map<String, String>, cursor: String? = null): JsonObject {
        val initial = endpoint(path, parameters)
        fun validate(value: String): String {
            require(value.length <= 8192) { "Continuation is oversized" }
            val checked = url(if (value.startsWith("?")) path + value else value, initial)
            val parsed = Url(checked)
            require(URI(checked).rawPath == path) { "Continuation changed endpoint" }
            require(parsed.parameters.names().all { it in parameters.keys || it in setOf("cursor", "start", "startAt") }) { "Continuation changed query" }
            parameters.forEach { (key, expected) -> require(parsed.parameters.getAll(key) == null || parsed.parameters.getAll(key) == listOf(expected)) { "Continuation changed scope or limit" } }
            val paging = parsed.parameters.names().filter { it !in parameters }.associateWith { key ->
                val values = parsed.parameters.getAll(key)!!
                require(values.size == 1 && values.single().isNotBlank() && values.single().length <= 4096) { "Invalid native paging parameter" }
                values.single()
            }
            return endpoint(path, parameters + paging)
        }
        val state = cursor?.let {
            require(it.length <= 16000) { "Continuation is oversized" }
            Json.parseToJsonElement(String(java.util.Base64.getUrlDecoder().decode(it), Charsets.UTF_8)).jsonObject
        }
        val visited = state?.get("seen")?.jsonArray?.map { it.jsonPrimitive.content } ?: emptyList()
        require(visited.size <= 32 && visited.all { it.matches(Regex("[a-f0-9]{64}")) } && visited.distinct().size == visited.size)
        val target = state?.get("next")?.jsonPrimitive?.content?.let(::validate) ?: initial
        val targetHash = ContextFiles.hash(target)
        require(targetHash !in visited) { "Cyclic native continuation" }
        val consumed = visited + targetHash
        val (response, body) = request(HttpMethod.Get, target)
        val items = body["results"] as? JsonArray ?: throw ContextRemoteException("malformed_list", "Native page results are unavailable")
        val maximum = parameters.getValue("limit").toInt()
        require(items.size <= maximum) { "Provider exceeded the requested limit" }
        val links = body["_links"]?.let { it as? JsonObject ?: throw ContextRemoteException("invalid_next", "Native continuation metadata is invalid") }
        val bodyNext = links?.get("next")?.let {
            (it as? JsonPrimitive)?.takeIf { value -> value.isString && value.content.isNotBlank() }?.content
                ?: throw ContextRemoteException("invalid_next", "Native continuation is invalid")
        }?.let(::validate)
        val header = response.headers[HttpHeaders.Link]
        val nextLinks = header?.split(',')?.filter { Regex("""rel\s*=\s*"?next"?""").containsMatchIn(it) } ?: emptyList()
        require(nextLinks.size <= 1) { "Ambiguous continuation" }
        val headerNext = nextLinks.singleOrNull()?.substringAfter('<', "")?.substringBefore('>')?.let(::validate)
        require(bodyNext == null || headerNext == null || bodyNext == headerNext) { "Provider continuations disagree" }
        val next = bodyNext ?: headerNext
        require(next == null || (ContextFiles.hash(next) !in consumed && consumed.size < 32)) { "Cyclic or excessive native continuation; narrow the query" }
        return buildJsonObject {
            put("results", items); put("hasMore", next != null); put("complete", next == null)
            next?.let { nextUrl -> put("cursor", java.util.Base64.getUrlEncoder().withoutPadding().encodeToString(buildJsonObject {
                put("next", nextUrl); put("seen", JsonArray(consumed.map(::JsonPrimitive)))
            }.toString().toByteArray(Charsets.UTF_8))) }
        }
    }

    /** Native leaf observations are internal seeds only; the caller must freshly validate the consumed ancestry before export. */
    internal suspend fun wikiSearch(scope: ContextConfig, query: String, limit: Int, cursor: String?,
                                    root: String? = null): Pair<JsonObject, List<RemoteNode>> {
        require(scope.site.trimEnd('/') == origin) { "Selected search site differs from the credential origin" }
        require(query.isNotBlank() && query.length <= 200 && query.none(Char::isISOControl) && limit in 1..25)
        val escaped = query.replace("\\", "\\\\").replace("\"", "\\\"")
        val registeredRoot = id(if (scope.isKnowledge) scope.knowledgeRootPageId ?: scope.homePageId else scope.homePageId)
        val domain = root?.let { " and (id = ${id(it)} or ancestor = ${id(it)})" } ?: ""
        val batch = wikiBatch("/wiki/rest/api/content/search", mapOf(
            "cql" to "space = \"${scope.space}\" and type = page and (id = $registeredRoot or ancestor = $registeredRoot)$domain and text ~ \"$escaped\"",
            "cqlcontext" to """{"contentStatuses":["current"]}""", "expand" to "space,version,ancestors", "limit" to limit.toString()), cursor)
        val nodes = batch.getValue("results").jsonArray.map { value ->
            val metadata = value as? JsonObject ?: throw ContextRemoteException("malformed_list", "Native search metadata is incomplete")
            fun text(value: JsonElement?): String = (value as? JsonPrimitive)?.takeIf { it.isString }?.content
                ?: throw ContextRemoteException("malformed_list", "Native search metadata is incomplete")
            require(metadata["type"] == JsonPrimitive("page") && metadata["status"] == JsonPrimitive("current")) { "Search requires explicit current page metadata" }
            val pageId = id(text(metadata["id"]))
            text(metadata["title"])
            val version = (metadata["version"] as? JsonObject)?.get("number") as? JsonPrimitive
            require(version != null && !version.isString && (version.intOrNull ?: 0) > 0) { "Search requires an exact native version" }
            val space = normalizeNativeSpaceId(metadata)
            require(space == scope.spaceId) { "Search returned another space" }
            val ancestors = metadata["ancestors"]?.let { it as? JsonArray ?: error("Search ancestry is incomplete") }
            val ancestryIds = ancestors?.map { id(text((it as? JsonObject)?.get("id"))) }
            require(ancestryIds == null || ancestryIds.distinct().size == ancestryIds.size && pageId !in ancestryIds) { "Search ancestry is cyclic" }
            val parent = metadata["parentId"]?.takeUnless { it is JsonNull }?.let { id(text(it)) }
            require(parent != null || ancestors != null) { "Search parent metadata is incomplete" }
            require(!metadata.containsKey("parentId") || ancestors == null || parent == ancestryIds?.lastOrNull()) { "Search parent metadata disagrees" }
            require(parent != pageId) { "Search page cannot be its own parent" }
            parseNode(metadata, "page")
        }
        require(nodes.map { it.id }.distinct().size == nodes.size)
        return JsonObject(batch + ("results" to JsonArray(nodes.map { node -> buildJsonObject {
            put("id", node.id); put("pageId", node.id); put("title", node.title); put("version", node.version)
            put("url", "$origin/wiki/spaces/${scope.space}/pages/${node.id}")
        } }))) to nodes
    }

    internal suspend fun wikiVersion(pageId: String, version: Int): JsonObject {
        require(version > 0)
        val result = request(HttpMethod.Get, endpoint("/wiki/api/v2/pages/${id(pageId)}/versions/$version")).second
        require(result["number"]?.jsonPrimitive?.intOrNull == version && !result.string("authorId").isNullOrBlank() && !result.string("createdAt").isNullOrBlank()) { "Exact native version metadata unavailable" }
        return result
    }

    internal suspend fun wikiHistoricalPage(pageId: String, version: Int): RemotePage {
        require(version > 0)
        val result = parsePage(request(HttpMethod.Get, endpoint("/wiki/api/v2/pages/${id(pageId)}",
            mapOf("body-format" to "storage", "version" to version.toString()))).second, pageId)
        require(result.node.version == version) { "Provider returned another historical version" }
        require(result.body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "Historical body exceeds the read budget" }
        return result
    }

    /** Resolve the actual thread before admitting its page, without trusting a caller-supplied parent. */
    internal suspend fun wikiCommentLocation(commentId: String, kind: String): Pair<String, String?> {
        require(kind in setOf("footer", "inline")); id(commentId)
        var current = commentId
        var immediate: String? = null
        var claimedPage: String? = null
        val seen = mutableSetOf<String>()
        while (true) {
            require(seen.add(current) && seen.size <= 32) { "Cyclic or excessive comment ancestry" }
            val raw = request(HttpMethod.Get, endpoint("/wiki/api/v2/$kind-comments/$current")).second
            require(raw.string("id") == current) { "Comment identity changed" }
            val parent = raw.string("parentCommentId")?.let(::id)
            if (current == commentId) immediate = parent
            val page = raw.string("pageId")?.let(::id)
            if (page != null) {
                require(claimedPage == null || claimedPage == page) { "Native comment ancestors disagree about their page" }
                claimedPage = page
            }
            if (parent == null) return (claimedPage ?: error("Native comment page is unavailable")) to immediate
            current = parent
        }
    }

    internal suspend fun wikiComments(pageId: String, kind: String, parent: String?, limit: Int, cursor: String?): JsonObject {
        require(limit in 1..50 && kind in setOf("footer", "inline")); id(pageId)
        val path = if (parent == null) "/wiki/api/v2/pages/$pageId/$kind-comments" else "/wiki/api/v2/$kind-comments/${id(parent)}/children"
        val batch = wikiBatch(path, mapOf("body-format" to "storage", "limit" to limit.toString()), cursor)
        val comments = batch.getValue("results").jsonArray.map { value ->
            val item = confluenceComment(value.jsonObject, pageId, kind, parent, root = parent == null)
            if (item.getValue("version").jsonPrimitive.int == 1) item else {
                val original = try {
                    request(HttpMethod.Get, endpoint("/wiki/api/v2/$kind-comments/${item.getValue("id").jsonPrimitive.content}/versions/1")).second.also {
                        require(it["number"]?.jsonPrimitive?.intOrNull == 1) { "Original comment version identity mismatch" }
                    }
                } catch (e: ContextRemoteException) {
                    if (e.code !in setOf("http_403", "http_404")) throw e
                    null
                }
                val author = commentText(original?.get("authorId"), required = false, maximum = 256)
                JsonObject(item + mapOf("authorId" to (author?.let(::JsonPrimitive) ?: JsonNull),
                    "authorStatus" to JsonPrimitive(if (author == null) "unavailable" else "native_original_version")))
            }
        }
        require(comments.map { it.getValue("id") }.distinct().size == comments.size)
        return JsonObject(batch + ("results" to JsonArray(comments)))
    }

    internal suspend fun wikiJiraUser(): JsonObject {
        val user = request(HttpMethod.Get, endpoint("/rest/api/3/myself")).second
        val accountId = commentText(user["accountId"], maximum = 256)!!
        // Some existing provider projections omit active; a supplied value must prove an active account.
        user["active"]?.let { active -> require(active is JsonPrimitive && !active.isString && active.booleanOrNull == true) { "Current Jira account is inactive or invalid" } }
        return buildJsonObject {
            put("accountId", accountId)
            commentText(user["displayName"], required = false, maximum = 500)?.let { put("displayName", it) }
        }
    }

    internal suspend fun wikiJiraComments(target: String, limit: Int, offset: Int): JsonObject = boundedComments { budget ->
        require(limit in 1..50 && offset in 0..1000000)
        val (parent, issueId) = commentParent("jira", target, budget)
        val raw = commentRequest(budget, "/rest/api/3/issue/$target/comment", mapOf("startAt" to offset.toString(), "maxResults" to limit.toString(), "orderBy" to "created"))
        val entries = raw["comments"] as? JsonArray ?: error("Native Jira comments unavailable")
        val total = raw["total"]?.jsonPrimitive?.intOrNull ?: error("Native Jira total unavailable")
        require(raw["startAt"]?.jsonPrimitive?.intOrNull == offset && entries.size <= limit && offset + entries.size <= total && (entries.isNotEmpty() || offset == total))
        val comments = entries.map { jiraComment(it.jsonObject, target, issueId) }
        require(comments.map { it.getValue("id") }.distinct().size == comments.size)
        require(commentParent("jira", target, budget).first == parent)
        buildJsonObject { put("comments", JsonArray(comments)); put("hasMore", offset + entries.size < total); put("complete", offset + entries.size == total)
            if (offset + entries.size < total) put("cursor", (offset + entries.size).toString()) }
    }

    internal suspend fun wikiCreateComment(provider: String, target: String, body: String, replyTo: String?, accountId: String,
                                           scope: ContextConfig?, kind: String = "footer", selection: Triple<String, Int, Int>? = null,
                                           beforeEffect: suspend () -> Unit): JsonObject {
        require(provider in setOf("confluence", "jira") && kind in setOf("footer", "inline"))
        require(selection == null || provider == "confluence" && kind == "inline" && replyTo == null && selection.second in 0 until selection.third) { "Invalid inline selection" }
        if (provider == "jira") require(replyTo == null) { "Jira reply creation is unsupported by the published request schema" }
        val payload = buildJsonObject {
            if (provider == "confluence") {
                if (replyTo == null) put("pageId", id(target)) else put("parentCommentId", id(replyTo))
                putJsonObject("body") { put("representation", "storage"); put("value", body) }
                if (selection != null) putJsonObject("inlineCommentProperties") {
                    put("textSelection", selection.first); put("textSelectionMatchIndex", selection.second); put("textSelectionMatchCount", selection.third)
                }
            } else put("body", jiraText(body))
        }
        val path = if (provider == "jira") "/rest/api/3/issue/$target/comment" else "/wiki/api/v2/$kind-comments"
        beforeEffect()
        val raw = request(HttpMethod.Post, endpoint(path), payload).second
        val commentId = observedId(raw) ?: throw ContextRemoteException("unknown_create_comment", "Created comment ID unavailable; inspect comments before any new write", true)
        val reference = if (provider == "jira") "$origin/rest/api/3/issue/$target/comment/$commentId" else "$kind:$commentId"
        val saved = try { commentDetail(provider, target, commentId, if (provider == "jira") "jira" else kind, replyTo,
            scope?.let { requireScope(it, target) }) } catch (e: CancellationException) { throw e }
        catch (e: Exception) {
            val details = (e as? ContextRemoteException)?.details ?: buildJsonObject {}
            throw ContextRemoteException("unknown_create_comment", "Created comment readback unavailable; inspect comments before any new write", true,
                JsonObject(details + ("reference" to JsonPrimitive(reference))))
        }
        val bodyMatches = if (provider == "jira") saved["body"] == jiraText(body) else
            (saved["body"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.let { StorageIdentity.equivalent(it, body) } == true
        val mismatches = listOfNotNull("authorId".takeUnless { saved["authorId"] == JsonPrimitive(accountId) },
            "content".takeUnless { bodyMatches }, "status".takeUnless { saved["status"] == JsonPrimitive("current") },
            "version".takeIf { provider == "confluence" && saved["version"]?.jsonPrimitive?.intOrNull != 1 }) + (selection?.let {
            val context = saved["inlineContext"] as? JsonObject
            listOfNotNull("selection".takeUnless { _ -> context?.get("inlineOriginalSelection") == JsonPrimitive(it.first) },
                "resolutionStatus".takeUnless { context?.get("resolutionStatus") == JsonPrimitive("open") },
                "inlineMarkerRef".takeIf { (context?.get("inlineMarkerRef") as? JsonPrimitive)?.content.isNullOrBlank() })
        } ?: emptyList())
        if (mismatches.isNotEmpty()) throw ContextRemoteException("unknown_create_comment", "Created comment readback differs from the requested fields", true,
            buildJsonObject { put("mismatchFields", JsonArray(mismatches.map(::JsonPrimitive))); put("reference", reference) })
        return saved
    }

    internal suspend fun wikiUpdateComment(target: String, before: JsonObject, body: String, scope: ContextConfig,
                                           beforeEffect: suspend () -> Unit): JsonObject {
        val commentId = id(before.getValue("id").jsonPrimitive.content)
        val kind = before.getValue("kind").jsonPrimitive.content
        require(kind in setOf("footer", "inline")) { "Jira has no documented atomic native comment version; versioned edits are unsupported" }
        val parent = before["parentId"]?.jsonPrimitive?.contentOrNull
        require(commentDetail("confluence", target, commentId, kind, parent, requireScope(scope, target)) == before) { "Comment changed before update" }
        val version = before.getValue("version").jsonPrimitive.int
        require(version in 1 until Int.MAX_VALUE)
        val payload = buildJsonObject {
            putJsonObject("version") { put("number", version + 1) }
            putJsonObject("body") { put("representation", "storage"); put("value", body) }
        }
        beforeEffect()
        request(HttpMethod.Put, endpoint("/wiki/api/v2/$kind-comments/$commentId"), payload)
        val saved = try { commentDetail("confluence", target, commentId, kind, parent, requireScope(scope, target)) }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { throw ContextRemoteException("unknown_update_comment", "Updated comment readback unavailable; inspect comments before any new write", true) }
        val bodyMatches = (saved["body"] as? JsonPrimitive)?.takeIf { it.isString }?.content?.let { StorageIdentity.equivalent(it, body) } == true
        val mismatches = listOfNotNull("version".takeUnless { saved["version"]?.jsonPrimitive?.intOrNull == version + 1 },
            "authorId".takeUnless { saved["authorId"] == before["authorId"] }, "parentId".takeUnless { saved["parentId"] == before["parentId"] },
            "inlineContext".takeUnless { saved["inlineContext"] == before["inlineContext"] }, "status".takeUnless { saved["status"] == JsonPrimitive("current") },
            "content".takeUnless { bodyMatches })
        if (mismatches.isNotEmpty()) throw ContextRemoteException("unknown_update_comment", "Updated comment readback differs from the requested fields", true,
            buildJsonObject { put("mismatchFields", JsonArray(mismatches.map(::JsonPrimitive))) })
        return saved
    }

    private fun jiraText(text: String): JsonObject = buildJsonObject {
        put("type", "doc"); put("version", 1); putJsonArray("content") {
            add(buildJsonObject { put("type", "paragraph"); putJsonArray("content") { add(buildJsonObject { put("type", "text"); put("text", text) }) } })
        }
    }

    private fun nodeMismatch(actual: RemoteNode, expected: RemoteNode): List<String> = listOfNotNull(
        "id".takeUnless { actual.id == expected.id }, "spaceId".takeUnless { actual.spaceId == expected.spaceId },
        "parentId".takeUnless { actual.parentId == expected.parentId }, "title".takeUnless { actual.title == expected.title },
        "type".takeUnless { actual.type == expected.type }, "status".takeUnless { actual.status == expected.status },
        "version".takeUnless { actual.version == expected.version })

    private fun samePublishedSnapshot(left: RemotePage, right: RemotePage): Boolean =
        left.node == right.node && left.versionMessage == right.versionMessage && StorageIdentity.equivalent(left.body, right.body)

    private fun writeReadbackMatches(scope: ContextConfig?, left: String, right: String): Boolean =
        if (scope?.isKnowledge == true) StorageIdentity.nativePageReadback(left, right) else StorageIdentity.equivalent(left, right)

    override fun close() = http.close()
}
