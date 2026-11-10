package io.crscube.clitools.confluence

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.*
import org.jsoup.Jsoup
import org.jsoup.nodes.Element
import org.jsoup.parser.Parser
import java.net.URI
import java.util.UUID

/** Bounded historical feedback reads and read-only reconciliation. */
class ContextFeedback(private val remote: ConfluenceRemote, private val origin: String,
                      private val configLoader: () -> ContextConfig?,
                      private val maintenanceLoader: () -> String? = { null },
                      private val namespaceAdmission: suspend (String) -> Unit) {
    suspend fun execute(data: JsonObject): JsonObject {
        val envelope = data["envelope"] as? JsonObject
        val suppliedEnvelope = envelope != null
        var payload = data["payload"] as? JsonObject
        var validationField = "request"
        fun response(status: String, code: String? = null): JsonObject = buildJsonObject {
            put("status", status); code?.let { put("code", it) }
            if (status in setOf("verified", "unknown")) { envelope?.let { put("envelope", it) }; payload?.let { put("payload", it) } }
            else if (suppliedEnvelope) envelope?.let { put("envelope", it) }
            if (status == "unknown") put("next", "Use explicit read-only reconcile with the original envelope and payload; do not retry or rebase.")
        }
        try {
            val operation = data.text("operation") ?: "read"
            if (operation !in setOf("discover", "read", "reconcile")) return buildJsonObject {
                put("status", "error"); put("code", "unsupported_operation")
                put("message", "Feedback record/result writes have been retired")
                put("next", "Read the maintenance page with get_document, then use update_document append with format:text for new body entries. Existing block corrections use format:storage. Comments are for requested discussion or supplements.")
            }
            data["dryRun"]?.let { require(it is JsonPrimitive && !it.isString && it.booleanOrNull != null) { "dryRun must be a JSON boolean" } }
            validationField = "connection"
            val config = configLoader() ?: return response("setup_required", "common_connection_required")
            require(config.isKnowledge && config.site.trimEnd('/') == origin.trimEnd('/')) { "Common knowledge connection at the authenticated origin required" }
            val configuredPage = maintenanceLoader()
            validationField = "destination"
            val pageId = destination(config, configuredPage)
            require(envelope?.text("pageId") == null || envelope?.text("pageId") == pageId) { "Operation target conflicts with the current maintenance destination" }
            require(data.text("pageId") == null || data.text("pageId") == pageId) { "Explicit page conflicts with the current maintenance destination" }
            val selected = target(config, pageId)
            fun currentBinding() {
                require(configLoader() == config && maintenanceLoader() == configuredPage) { "Common binding changed" }
            }
            currentBinding()
            val page = remote.page(pageId, selected, config)
            validationField = "section"
            val section = section(page.body)
            require(target(config, pageId) == page.node) { "Feedback target changed during read" }
            currentBinding()
            val url = "${config.site}/wiki/spaces/${config.space}/pages/$pageId"
            if (operation == "discover") return buildJsonObject {
                put("status", "discovered"); put("pageId", pageId); put("title", page.node.title); put("version", page.node.version)
                put("bodyHash", ContextFiles.hash(page.body)); put("url", url); put("section", "개선 의견")
            }
            if (operation == "read") {
                val entry = data.text("entryAnchor")?.let { entry(section, it) }
                val raw = if (entry == null) page.body.substring(section.start, section.end) else page.body.substring(start(entry), end(entry))
                val projection = Jsoup.parse(raw, "", Parser.xmlParser())
                projection.allElements.filter { anchorName(it)?.startsWith("talos-feedback-operation-") == true }.forEach { it.remove() }
                projection.select("a[href]").forEach {
                    val href = it.attr("href")
                    if (href.isNotBlank() && !it.text().contains(href)) it.appendText(" ($href)")
                }
                val text = projection.text()
                val total = text.codePointCount(0, text.length)
                val length = data["length"]?.jsonPrimitive?.int ?: 4000
                require(length in 1..12000)
                val offset = data["offset"]?.jsonPrimitive?.int ?: maxOf(0, total - length)
                require(offset in 0..total)
                require(!data.containsKey("offset") || offset == 0 || data.containsKey("expectedVersion") || data.containsKey("expectedBodyHash"))
                data["expectedVersion"]?.let {
                    require(it is JsonPrimitive && !it.isString && it.intOrNull?.let { number -> number > 0 } == true) { "Expected version must be a positive JSON integer" }
                }
                if ((data["expectedVersion"] != null && data["expectedVersion"]?.jsonPrimitive?.int != page.node.version) ||
                    (data.text("expectedBodyHash") != null && data.text("expectedBodyHash") != ContextFiles.hash(page.body)))
                    throw DestinationException("conflict", "source_changed")
                val next = minOf(total, offset + length)
                return buildJsonObject {
                    put("status", "read"); put("pageId", pageId); put("version", page.node.version); put("bodyHash", ContextFiles.hash(page.body)); put("url", url)
                    put("text", text.substring(text.offsetByCodePoints(0, offset), text.offsetByCodePoints(0, next)))
                    put("offsetUnit", "unicode_code_points"); put("offset", offset); put("nextOffset", next); put("totalLength", total)
                    put("truncated", offset > 0 || next < total); put("hasEarlier", offset > 0); put("hasMore", next < total)
                }
            }
            validationField = "account"
            val account = try { remote.currentUser() } catch (e: CancellationException) { throw e }
                catch (_: Exception) { throw DestinationException("account_unavailable", "account_unavailable") }
            val pinned = envelope ?: error("Historical reconcile requires the original envelope")
            val originalPayload = payload ?: error("Historical reconcile requires the original payload")
            validationField = "payload"
            val canonical = canonical(originalPayload, pinned.text("operation") ?: error("Original operation required"),
                originalPayload.text("recorderDisplayName") ?: error("Original recorder name required"))
            require(originalPayload == canonical) { "Canonical payload changed" }
            payload = canonical
            validationField = "envelope"
            validateEnvelope(pinned, canonical, config, pageId, account)
            namespaceAdmission(pageId)
            validationField = "immutableContent"
            return if (existing(section, pinned, canonical, page.body)) response("verified") else response("unknown", "marker_absent")
        } catch (e: CancellationException) { throw e }
        catch (e: DestinationException) { return response(if (data["operation"] == JsonPrimitive("reconcile")) "unknown" else e.status, e.code) }
        catch (e: ContextRemoteException) { return JsonObject(response(if (e.outcomeUnknown || data["operation"] == JsonPrimitive("reconcile")) "unknown" else "unavailable", e.code) + buildJsonObject {
            put("message", e.message ?: "Historical feedback read failed")
            if (e.details.isNotEmpty()) put("details", e.details)
            e.next?.let { put("next", it) }
        }) }
        catch (e: Exception) { return JsonObject(response(if (data["operation"] == JsonPrimitive("reconcile")) "unknown" else "rejected", "precondition_changed") + buildJsonObject {
            put("message", "Historical feedback validation failed; verify the original envelope, payload, destination and immutable fields")
            putJsonObject("details") { put("field", validationField) }
        }) }
    }

    private class DestinationException(val status: String, val code: String) : RuntimeException()
    private suspend fun destination(config: ContextConfig, configuredPage: String?): String {
        if (configuredPage != null) return configuredPage
        val rules = select(config, config.knowledgeRootPageId ?: config.homePageId, setOf("AI 운영 규칙"))
        return select(config, rules, setOf("유지보수", "AI 유지보수"))
    }

    private suspend fun select(config: ContextConfig, parent: String, titles: Set<String>): String {
        val candidates = withTimeoutOrNull(30_000) { remote.feedbackCandidates(config, parent, titles) }
            ?: throw DestinationException("selection_required", "discovery_timeout")
        if (candidates["complete"] != JsonPrimitive(true)) throw DestinationException("selection_required", "discovery_truncated")
        val found = candidates.getValue("results").jsonArray
        if (found.isEmpty()) throw DestinationException("setup_required", "destination_missing")
        if (found.size != 1) throw DestinationException("selection_required", "destination_ambiguous")
        return found.single().jsonObject.getValue("pageId").jsonPrimitive.content
    }

    private suspend fun target(config: ContextConfig, pageId: String): RemoteNode {
        namespaceAdmission(pageId)
        val selected = remote.requireScope(config, pageId)
        require(selected.type == "page" && selected.status == "current")
        var cursor = selected
        var rules = false
        val root = config.knowledgeRootPageId ?: config.homePageId
        val seen = mutableSetOf<String>()
        while (cursor.id != root) {
            require(seen.add(cursor.id) && seen.size <= 100)
            if (cursor.id != selected.id && cursor.title == "AI 운영 규칙") rules = true
            val parent = cursor.parentId ?: error("Missing ROOT ancestor")
            cursor = try { remote.scopeNode(parent) } catch (e: ContextRemoteException) {
                if (e.code != "http_404") throw e
                remote.scopeNode(parent, "folder")
            }
            require(cursor.spaceId == config.spaceId && cursor.status == "current")
        }
        require(rules) { "Existing maintenance page must be below common ROOT / AI 운영 규칙" }
        require(remote.requireScope(config, pageId) == selected)
        return selected
    }

    companion object {
        internal data class Section(val start: Int, val end: Int, val elements: List<Element>)
        private fun parse(body: String) = Jsoup.parse(body, "", Parser.xmlParser().setTrackPosition(true))
        private fun start(element: Element): Int {
            require(element.sourceRange().isTracked && !element.sourceRange().isImplicit)
            return element.sourceRange().start().pos()
        }
        private fun end(element: Element): Int {
            require(element.endSourceRange().isTracked && !element.endSourceRange().isImplicit)
            return element.endSourceRange().end().pos()
        }
        internal fun section(body: String): Section {
            val doc = parse(body)
            val headings = doc.select("h1,h2,h3,h4,h5,h6").filter {
                it.parents().none { parent -> parent.normalName() in setOf("ac:plain-text-body", "ac:parameter", "script", "style", "textarea", "title", "xmp", "iframe", "noembed", "noframes", "plaintext") } }
            val headers = headings.filter { it.text().trim() == "개선 의견" }
            if (headers.isEmpty()) throw DestinationException("setup_required", "section_missing")
            if (headers.size != 1) throw DestinationException("selection_required", "section_ambiguous")
            val heading = headers.single()
            val parent = heading.parent() ?: error("Missing section container")
            val siblings = parent.children().toList()
            val index = siblings.indexOf(heading)
            val level = heading.tagName().drop(1).toInt()
            val next = siblings.drop(index + 1).firstOrNull { it.tagName().matches(Regex("h[1-6]")) && it.tagName().drop(1).toInt() <= level }
            val until = next?.let(::start) ?: if (parent === doc) body.length else parent.endSourceRange().start().pos().also {
                require(parent.endSourceRange().isTracked && !parent.endSourceRange().isImplicit)
            }
            val from = end(heading)
            require(from in 0..until && until <= body.length)
            if (headings.any { it.parent() !== parent && it.tagName().drop(1).toInt() <= level && start(it) in from until until })
                throw DestinationException("selection_required", "section_ambiguous")
            // XML repair must never turn a missing close into a larger writable section.
            doc.allElements.filter { it !== doc && it.sourceRange().isTracked && it.sourceRange().start().pos() in from until until }.forEach {
                val opening = it.sourceRange()
                val closing = it.endSourceRange()
                require(!opening.isImplicit && opening.end().pos() <= until)
                val selfClosing = body.substring(opening.start().pos(), opening.end().pos()).trimEnd().endsWith("/>")
                require(selfClosing || closing.isTracked && !closing.isImplicit && closing.end().pos() <= until) {
                    "Feedback section has an unclosed or ambiguous storage element"
                }
            }
            val elements = doc.allElements.filter { it !== doc && it.sourceRange().isTracked && !it.sourceRange().isImplicit && it.endSourceRange().isTracked && !it.endSourceRange().isImplicit && start(it) >= from && end(it) <= until }
            val anchors = elements.filter { it.tagName() == "ac:structured-macro" && it.attr("ac:name") == "anchor" }
                .map { it.children().singleOrNull { child -> child.tagName() == "ac:parameter" }?.text().orEmpty() }
                .filter { it.startsWith("talos-feedback-") }
            require(anchors.size == anchors.toSet().size) { "Duplicate feedback anchors" }
            return Section(from, until, elements)
        }
        private fun anchorName(element: Element): String? = if (element.tagName() == "ac:structured-macro" && element.attr("ac:name") == "anchor")
            element.children().singleOrNull { it.tagName() == "ac:parameter" }?.text() else null
        private fun entry(section: Section, anchor: String): Element {
            require(anchor.matches(Regex("talos-feedback-[0-9a-f-]{36}")))
            val macro = section.elements.singleOrNull { anchorName(it) == anchor } ?: error("Unique generated original entry required")
            val parent = macro.parent() ?: error("Missing generated entry")
            require(parent.tagName() == "div" && section.elements.contains(parent) && parent.children().firstOrNull() === macro)
            return parent
        }
        private fun operationId(value: String): String = UUID.fromString(value).toString().also { require(it == value) }
        private fun JsonObject.text(name: String): String? = get(name)?.let { (it as? JsonPrimitive)?.takeIf { p -> p.isString }?.content ?: error("String required") }
        private fun plain(value: String?, maximum: Int = 4000): String = value?.also {
            require(it.isNotBlank() && it.codePointCount(0, it.length) <= maximum && it.none { c -> c.isISOControl() && c !in "\n\r\t" })
        } ?: error("Required plain text missing")
        private fun canonical(input: JsonObject, operation: String, display: String): JsonObject {
            require(operation in setOf("record", "result"))
            require(display.isNotBlank() && display.length <= 500 && display.none(Char::isISOControl)) { "Invalid recorder name snapshot" }
            val evidence = (input["evidence"] ?: JsonArray(emptyList())).jsonArray
            require(evidence.size <= 10)
            val links = evidence.map { value ->
                val link = plain(value.jsonPrimitive.content, 2000)
                val uri = URI(link)
                require(uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.rawUserInfo == null && link.none(Char::isISOControl))
                JsonPrimitive(link)
            }
            return buildJsonObject {
                put("operation", operation)
                put("operationId", operationId(input.text("operationId") ?: error("Original operation UUID required")))
                if (operation == "record") {
                    val source = input.text("source") ?: "ai"; require(source in setOf("ai", "user")); put("source", source)
                    put("situation", plain(input.text("situation"))); put("expected", plain(input.text("expected")))
                } else {
                    val anchor = input.text("entryAnchor") ?: error("Original entry required")
                    require(anchor.matches(Regex("talos-feedback-[0-9a-f-]{36}")))
                    put("entryAnchor", anchor); put("result", plain(input.text("result")))
                }
                put("evidence", JsonArray(links)); put("recorderDisplayName", display)
            }
        }
        private fun validateEnvelope(value: JsonObject, payload: JsonObject, config: ContextConfig, pageId: String, account: JsonObject) {
            require(value.keys == setOf("origin", "rootPageId", "pageId", "baseVersion", "baseBodyHash", "accountId", "operationId", "payloadHash", "operation") + if (payload.containsKey("entryAnchor")) setOf("entryAnchor") else emptySet<String>())
            require(value.text("origin") == config.site.trimEnd('/') && value.text("rootPageId") == (config.knowledgeRootPageId ?: config.homePageId) && value.text("pageId") == pageId)
            require(value["accountId"] == account["accountId"])
            val baseVersion = value["baseVersion"] as? JsonPrimitive
            require(baseVersion != null && !baseVersion.isString && baseVersion.intOrNull?.let { it in 1 until Int.MAX_VALUE } == true)
            require(value.text("baseBodyHash")?.matches(Regex("[a-f0-9]{64}")) == true)
            operationId(value.text("operationId") ?: error("Missing operation UUID"))
            require(value["operationId"] == payload["operationId"])
            require(value.text("payloadHash") == ContextFiles.hash(payload.toString()) && value["operation"] == payload["operation"] && value["entryAnchor"] == payload["entryAnchor"])
        }
        private fun escape(value: String): String = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\"", "&quot;").replace("'", "&#39;")
        private fun anchor(value: String) = "<ac:structured-macro ac:name=\"anchor\"><ac:parameter ac:name=\"\">$value</ac:parameter></ac:structured-macro>"
        private fun fragment(envelope: JsonObject, payload: JsonObject): String {
            val id = envelope.text("operationId")!!
            val fingerprint = ContextFiles.hash(buildJsonObject { put("envelope", JsonObject(envelope.toSortedMap())); put("payload", payload) }.toString())
            val record = payload.text("operation") == "record"
            return buildString {
                append("<div>"); append(anchor(if (record) "talos-feedback-$id" else "talos-feedback-result-$id"))
                append(anchor("talos-feedback-operation-$id-$fingerprint"))
                append("<p>${if (record) if (payload.text("source") == "ai") "AI 관찰" else "사용자 전달 (발언자 인증 아님)" else "처리 결과 (보고된 관찰; 검증·승인 아님)"}</p>")
                append("<p>기록 계정: ${escape(payload.text("recorderDisplayName")!!)} (${escape(envelope.text("accountId")!!)})</p>")
                if (record) { append("<p>상황: ${escape(payload.text("situation")!!)}</p>"); append("<p>기대 동작: ${escape(payload.text("expected")!!)}</p>") }
                else append("<p>결과: ${escape(payload.text("result")!!)}</p>")
                payload.getValue("evidence").jsonArray.forEach { append("<p>근거: <a href=\"${escape(it.jsonPrimitive.content)}\">${escape(it.jsonPrimitive.content)}</a></p>") }
                append("</div>")
            }
        }
        private fun existing(section: Section, envelope: JsonObject, payload: JsonObject, body: String): Boolean {
            val id = envelope.text("operationId")!!
            val found = section.elements.filter { anchorName(it) in setOf("talos-feedback-$id", "talos-feedback-result-$id") }
            val anywhere = parse(body).allElements.count { anchorName(it) in setOf("talos-feedback-$id", "talos-feedback-result-$id") }
            require(anywhere == found.size) { "Operation UUID already exists at another location" }
            if (found.isEmpty()) return false
            require(found.size == 1)
            val parent = found.single().parent() ?: error("Missing operation")
            val expected = fragment(envelope, payload)
            val actual = if (payload.text("operation") == "record") {
                // Later results may follow, but every original field and its serialization must still agree.
                val count = parse(expected).children().single().children().size
                val last = parent.children().getOrNull(count - 1) ?: error("Original feedback fields are missing")
                body.substring(start(parent), end(last)) + body.substring(parent.endSourceRange().start().pos(), end(parent))
            } else body.substring(start(parent), end(parent))
            require(StorageIdentity.equivalent(actual, expected)) { "Operation UUID conflicts with its immutable content" }
            if (payload.text("operation") == "result") require(parent.parent() === entry(section, payload.text("entryAnchor")!!))
            return true
        }
    }
}
