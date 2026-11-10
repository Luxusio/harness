package io.crscube.clitools.confluence

import org.jsoup.Jsoup
import org.jsoup.nodes.Document
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.parser.Parser
import kotlinx.serialization.json.*
import java.net.URI
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/** Fresh remote authorization. The caller cannot turn a protected page into a writable document by changing kind. */
class ContextPolicy(private val remote: ConfluenceRemote) {
    private val development = listOf(
        listOf("20. Design & Development", "01. Development Environment"),
        listOf("20. Design & Development", "02. System Architecture", "03. Software Specification"),
        listOf("20. Design & Development", "02. System Architecture", "04. User Account Specification"),
        listOf("20. Design & Development", "03. Software Description (Module Specification)", "1. Module Specification"),
        listOf("20. Design & Development", "03. Software Description (Module Specification)", "2. Module Interface Diagram"),
        listOf("20. Design & Development", "04. Application Description (Optional)", "01. Menu Architecture/List"),
        listOf("20. Design & Development", "04. Application Description (Optional)", "02. Screen List/Specification"),
        listOf("20. Design & Development", "04. Application Description (Optional)", "03. API List/Specification"),
        listOf("20. Design & Development", "05. Data Model")
    )
    private val suffixes = mapOf("plan" to "개발 계획", "impact" to "영향도", "decisions" to "결정 사항",
        "confirmation" to "확인 결과", "urs" to "URS 초안", "tc" to "TC", "tc-review" to "TC Review", "review" to "리뷰 결과")
    private val qualificationTitle = Regex("(?:[0-9]+\\.\\s*)?(?:Operational Qualification Test|Performance Qualification Test)", RegexOption.IGNORE_CASE)

    private fun rejectQualificationWrite(title: String) {
        require(!qualificationTitle.matches(title.trim())) { "Official OQ/PQ documents and their descendants are read-only" }
    }

    suspend fun check(config: ContextConfig, issueKey: String, kind: String, parentId: String, title: String,
                      body: String, before: RemotePage? = null, binding: JsonObject? = null) {
        require(issueKey.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*"))) { "A valid PM Jira key is required" }
        require(config.isKnowledge || issueKey.substringBeforeLast('-') == config.pmProject) { "Shared document keys must belong to the configured PM Jira project" }
        require(kind in suffixes.keys + setOf("center", "development", "tc-results")) { "Unknown development document kind" }
        require(config.role in setOf("개발", "QA", "PM", "관리자")) { "Publishing requires an explicitly configured author role" }
        require(config.role != "관리자") { "Administrators inspect structure; document content is written by its author role" }
        when (kind) {
            "urs" -> require(config.role == "PM") { "Only PM may write URS drafts" }
            "tc" -> require(config.role == "QA") { "Only QA may write TC drafts" }
            "tc-review" -> require(config.role in setOf("QA", "PM")) { "Only QA or PM may write TC reviews" }
            "tc-results" -> require(config.role in setOf("개발", "QA") && before != null) { "TC results require an existing TC and developer/QA role" }
        }
        require(body.isNotBlank() && body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "A bounded storage body is required" }
        rejectSecrets(body)
        val path = if (before != null) {
            require(before.node.title == title && before.node.parentId == parentId && remote.page(before.node.id, before.node, config) == before) {
                "Page content, version or hierarchy changed; prepare the edit again"
            }
            ancestry(config, before.node, writing = true)
        } else {
            rejectQualificationWrite(title)
            ancestry(config, resolve(parentId), writing = true) + RemoteNode("new", title, parentId, spaceId = config.spaceId, version = 1)
        }
        val titles = path.map { it.title }
        require(!config.isKnowledge || (binding != null && kind != "development")) { "Knowledge writes require a resolved actual-issue binding" }
        if (binding != null && kind != "development") {
            checkBoundIssuePath(config, issueKey, kind, path, body, before, binding)
        } else if (config.structurePolicy == "pilot") {
            val rootIndex = path.indexOfFirst { it.id == config.pilotRootPageId }
            require(rootIndex >= 0 && rootIndex < path.lastIndex) { "Pilot documents must be below the configured existing issue center" }
            val center = remote.page(path[rootIndex].id)
            require(center.node == path[rootIndex] && hasIssueLink(config, issueKey, center.body)) { "Pilot center must link this exact PM issue" }
            val suffix = if (kind == "development") "개발 문서" else suffixes[kind]
            require(suffix != null && Regex("^\\[Talos pilot [^\\]\\r\\n]+] ${Regex.escape(issueKey)} ${Regex.escape(suffix)}$").matches(title)) {
                "Pilot test document title must bind the exact PM key and document kind"
            }
        } else if (kind == "development") {
            val authorizedTitles = if (before == null) titles.dropLast(1) else titles
            require(development.any { authorizedTitles.take(it.size) == it }) { "The complete title path is outside writable development leaves" }
        } else {
            checkIssuePath(config, issueKey, kind, path, body, before)
        }
        if (before != null) {
            if (kind == "tc-results") preserveTcDefinitions(config, before.body, body) else preserveMacros(before.body, body)
            if (kind == "tc") preserveQaFailureHistory(config, before.body, body)
            if (kind == "center") require(body == before.body) { "The PM center body is read-only; publish issue documents underneath it" }
            if (kind == "impact" && xml(before.body).select("h1,h2,h3,h4,h5,h6").any { it.text().contains("[확정]") }) {
                require(body.startsWith(before.body)) { "Confirmed impact history must remain an exact storage prefix" }
            }
            if (kind == "decisions") require(body.startsWith(before.body)) {
                "Preserve existing decisions and human approval fields; append new items only"
            }
            val reviewRound = if (binding != null) path.dropLast(1).lastOrNull()?.title == "$issueKey 리뷰 결과" else path.size == 7
            if (kind == "review") require(config.structurePolicy != "pilot" && !reviewRound && body.startsWith(before.body)) {
                "Published review rounds are immutable; append a new round and preserve the review index history"
            }
        }
        if (kind == "plan") {
            val document = xml(body)
            require(document.select("h1,h2").map { it.text() } == listOf("1. 개요", "2. 변경 요약", "3. 결과")) {
                "Shared PLAN contains exactly overview, change summary and results sections"
            }
            require(!Regex("AC-[0-9]+|상세 구현 계획|Acceptance Criteria", RegexOption.IGNORE_CASE).containsMatchIn(document.text())) {
                "Detailed implementation and ACs belong in local PLAN.md"
            }
        }
        if (kind == "decisions") validateNewDecisions(if (before == null) body else body.removePrefix(before.body))
        if (kind == "development") checkAddedDefinitions(config, path, title, body, before)
    }

    /** Ordinary knowledge editing uses native permissions within its registered root. */
    suspend fun checkKnowledge(config: ContextConfig, target: RemoteNode, title: String, body: String,
                               before: RemotePage? = null) {
        require(config.isKnowledge) { "Ordinary writes require a knowledge connection; regulated publishing keeps its own gate" }
        require(title.isNotBlank() && title.length <= 1000 && title.none(Char::isISOControl)) { "A bounded document title is required" }
        require((body.isNotBlank() || before?.body == body) && body.toByteArray(Charsets.UTF_8).size <= 4 * 1024 * 1024) { "A bounded storage body is required" }
        rejectSecrets(body)
        require(remote.requireScope(config, target.id) == target) { "Knowledge target identity, version or hierarchy changed" }
        if (before != null) require(before.node == target) { "Document identity changed" }
    }

    fun checkCommentText(text: String) {
        require(text.isNotBlank() && text.toByteArray(Charsets.UTF_8).size <= 64 * 1024) { "Comment text must contain 1..65536 UTF-8 bytes" }
        rejectSecrets(text)
    }

    /** Existing tc-run keeps its preview/approval contract; this prevents using that endpoint as a development-write bypass. */
    suspend fun checkLegacyTc(config: ContextConfig, pageId: String, body: String, expectedNode: RemoteNode? = null): RemotePage {
        require(config.role in setOf("개발", "QA", "PM")) { "TC writes require an explicit author role" }
        val admitted = if (config.isKnowledge) remote.requireScope(config, pageId) else expectedNode ?: remote.node(pageId)
        require(expectedNode == null || admitted == expectedNode) { "TC left its selected namespace or admitted identity" }
        val before = remote.page(pageId, admitted, config)
        val issue = Regex("^([A-Z][A-Z0-9_]*-[1-9][0-9]*) TC$").matchEntire(before.node.title)?.groupValues?.get(1)
            ?: error("Managed generic writes are reserved for canonical issue TC results; use the development publisher")
        val parent = before.node.parentId ?: error("TC has no parent")
        val binding = if (config.isKnowledge) ContextResolver(remote).resolve(config.forIssue(issue), issue,
            buildJsonObject { put("centerId", parent) }) else null
        check(config.forIssue(issue), issue, "tc-results", parent, before.node.title, body, before, binding)
        return before
    }

    private suspend fun resolve(id: String, requireCurrent: Boolean = false): RemoteNode =
        try { if (requireCurrent) remote.scopeNode(id) else remote.node(id) }
        catch (e: ContextRemoteException) {
            if (e.code != "http_404") throw e
            if (requireCurrent) remote.scopeNode(id, "folder") else remote.node(id, "folder")
        }

    private suspend fun ancestry(config: ContextConfig, target: RemoteNode, writing: Boolean = false): List<RemoteNode> {
        val path = mutableListOf<RemoteNode>()
        var current = target
        val seen = mutableSetOf<String>()
        var homeSeen = false
        while (true) {
            require(seen.add(current.id) && seen.size <= 100) { "Invalid/deep page ancestry" }
            require(current.spaceId == config.spaceId && current.status == "current") { "Writing requires current content in the configured space" }
            if (writing) rejectQualificationWrite(current.title)
            if (current.id == config.homePageId) {
                if (!writing) return if (config.isKnowledge) listOf(current) + path else path
                if (config.isKnowledge) path.add(0, current)
                homeSeen = true
            } else if (!homeSeen) path.add(0, current)
            val parent = current.parentId
            if (parent == null) {
                require(homeSeen) { "Target does not descend from the configured space home" }
                return path
            }
            current = resolve(parent, requireCurrent = writing && homeSeen)
        }
    }

    private suspend fun checkIssuePath(config: ContextConfig, key: String, kind: String, path: List<RemoteNode>, body: String, before: RemotePage?) {
        require(path.size >= 5 && path[0].title == "60. Maintenance") { "Issue documents belong under the standard Maintenance release content" }
        val year = Regex("[0-9]{2}\\. ([0-9]{4})").matchEntire(path[1].title)?.groupValues?.get(1)
            ?: error("Non-standard Maintenance year folder")
        val release = Regex("[0-9]{2}\\. ([0-9]{8})").matchEntire(path[2].title)?.groupValues?.get(1)
            ?: error("Non-standard Maintenance release folder")
        try { LocalDate.parse(release, DateTimeFormatter.BASIC_ISO_DATE) } catch (_: Exception) { error("Invalid release date") }
        require(release.startsWith(year) && path[3].title == "${release}_이관 내용") { "Release year/date/content paths must agree exactly" }
        if (kind == "center") {
            require(path.size == 5 && Regex("^\\[[^\\]\\r\\n]+] ${Regex.escape(key)} .+$").matches(path.last().title)) { "Issue centers use [type] PM-KEY title directly under release content" }
            require(hasIssueLink(config, key, body)) { "An issue center must link its PM Jira issue" }
            return
        }
        require(path.size >= 6) { "Publish documents beneath the PM issue center, not the release folder" }
        val center = remote.page(path[4].id)
        require(center.node == path[4] && hasIssueLink(config, key, center.body)) { "The center must link this exact PM issue" }
        val expected = if (kind == "tc-results") "$key TC" else "$key ${suffixes.getValue(kind)}"
        when (kind) {
            "tc-review" -> require(path.size == 7 && path[5].title == "$key TC" && path[6].title == expected) { "TC Review belongs under this issue's TC" }
            "review" -> require((path.size == 6 && path[5].title == expected) ||
                (path.size == 7 && path[5].title == expected && Regex("${Regex.escape(expected)} [1-9][0-9]*차").matches(path[6].title))) {
                "Review rounds belong under the canonical review page"
            }
            else -> require(path.size == 6 && path[5].title == expected) { "Issue document kind, title and parent must agree" }
        }
        require(before == null || path.last().id == before.node.id) { "Unexpected update target" }
    }

    private suspend fun checkBoundIssuePath(config: ContextConfig, key: String, kind: String, path: List<RemoteNode>, body: String,
                                            before: RemotePage?, binding: JsonObject) {
        val current = ContextResolver(remote).revalidate(config, key, binding)
        fun id(name: String) = current.getValue(name).jsonPrimitive.content
        require(current.getValue("pmIssueKey").jsonPrimitive.content == key) { "Publishing issue conflicts with resolved hierarchy" }
        if (kind == "center" && before == null) {
            require(current.getValue("status").jsonPrimitive.content == "center_missing" && path[path.lastIndex - 1].id == id("contentId") &&
                Regex("^\\[[^\\]\\r\\n]+] ${Regex.escape(key)} .+$").matches(path.last().title) && hasIssueLink(config, key, body)) {
                "Create an issue center only after complete absence proof below the resolved release content"
            }
            return
        }
        require(current.getValue("status").jsonPrimitive.content == "resolved") { "Resolve an existing issue center before publishing its documents" }
        val center = path.indexOfFirst { it.id == id("centerId") }
        require(center >= 0 && path.take(center).any { it.id == id("contentId") } &&
            (config.isKnowledge || path.take(center).any { it.id == id("releaseId") })) { "Document is outside the resolved issue bundle" }
        val relative = path.drop(center + 1)
        if (kind == "center") { require(before != null && relative.isEmpty()) { "Unexpected center target" }; return }
        val expected = if (kind == "tc-results") "$key TC" else "$key ${suffixes.getValue(kind)}"
        when (kind) {
            "tc-review" -> require(relative.size == 2 && relative[0].title == "$key TC" && relative[1].title == expected) {
                "TC Review belongs below the resolved issue TC"
            }
            "review" -> require((relative.size == 1 && relative[0].title == expected) ||
                (relative.size == 2 && relative[0].title == expected && Regex("${Regex.escape(expected)} [1-9][0-9]*차").matches(relative[1].title))) {
                "Review rounds belong below the resolved review index"
            }
            else -> require(relative.size == 1 && relative[0].title == expected) { "Document kind and title must match the resolved issue center" }
        }
    }

    private fun hasIssueLink(config: ContextConfig, key: String, body: String): Boolean = xml(body).select("a[href]").any {
        try {
            val origin = URI(config.site)
            val url = origin.resolve(it.attr("href"))
            url.scheme == origin.scheme && url.host == origin.host && url.port == origin.port && url.rawUserInfo == null &&
                url.path == "/browse/$key" && url.rawFragment == null
        } catch (_: Exception) { false }
    }

    private fun xml(body: String): Document = Jsoup.parse(body, "", Parser.xmlParser()).also { it.outputSettings().prettyPrint(false) }

    private fun preserveMacros(before: String, after: String) {
        fun macros(text: String): Map<String, Int> = xml(text).allElements.filter {
            it.tagName() == "ac:structured-macro" || it.tagName() == "ac:macro"
        }.map { it.outerHtml() }.groupingBy { it }.eachCount()
        val fresh = macros(after)
        require(macros(before).all { (macro, count) -> (fresh[macro] ?: 0) >= count }) { "Original macro subtrees and parameters must be preserved" }
    }

    private fun rejectSecrets(body: String) {
        val document = Jsoup.parse(body, "", Parser.xmlParser().setTrackPosition(true))
        val text = document.text()
        val patterns = listOf(
            Regex("(?i)Authorization\\s*:\\s*Bearer\\s+[^\\s<>]{8,}"),
            Regex("-----BEGIN (?:[A-Z ]+ )?PRIVATE KEY-----"),
            Regex("(?<![A-Z0-9])(?:AKIA|ASIA)[A-Z0-9]{16}(?![A-Z0-9])"),
            Regex("(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|ATATT[A-Za-z0-9_=-]{20,}|sk-[A-Za-z0-9_-]{20,})"),
            Regex("eyJ[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}\\.[A-Za-z0-9_-]{8,}"),
            Regex("(?i)(?:password|passwd|api[_ -]?key|(?:access[_ -]?)?token|secret|비밀번호)\\s*[:=]\\s*[\"']?(?!<|\\$\\{|환경|없음|REDACTED|redacted|placeholder|example|\\*{3})[^\\s\"'<>]{4,}")
        )
        val regexSecret = patterns.any { it.containsMatchIn(body) || it.containsMatchIn(text) }
        fun credential(value: String) = value.trim().lowercase().replace(Regex("[_ -]+"), " ") in
            setOf("password", "passwd", "비밀번호", "api key", "token", "access token", "secret", "private key")
        fun redacted(cell: Element) = cell.text().isBlank() || cell.text().trim() in setOf("REDACTED", "환경 변수", "없음", "***")

        // Diagnostics contain only ordinals and fixed vocabulary, never text from the rejected document.
        val locations = linkedMapOf<String, JsonObject>()
        val expandedCodes = mutableSetOf<String>()
        var truncated = false
        var message = if (regexSecret) "Secret-like values must not be published" else null
        fun field(label: String): String = when (label.trim().lowercase().replace(Regex("[_ -]+"), " ")) {
            "password", "passwd", "비밀번호" -> "password"
            "api key" -> "api_key"
            "access token", "token" -> "token"
            "private key" -> "private_key"
            else -> "secret"
        }
        fun matchField(pattern: Int, match: MatchResult): String = when (pattern) {
            0 -> "authorization"; 1 -> "private_key"; 2 -> "access_key"; 3, 4 -> "token"
            else -> field(match.value.substringBefore(':').substringBefore('='))
        }
        fun reject(documentOnly: Boolean = false): Nothing = throw ContextRemoteException("secret_detected", message ?: "Secret-like values must not be published",
            details = buildJsonObject {
                put("locations", if (documentOnly) buildJsonArray { add(buildJsonObject {
                    put("block", "document"); put("index", 1); put("field", "secret")
                }) } else JsonArray(locations.values.toList()))
                if (truncated && !documentOnly) put("truncated", true)
            }, next = "Replace the indicated values with REDACTED or an environment reference such as \${PASSWORD}. Keep credentials outside the Wiki.")

        // The authoritative raw/visible and table checks are unchanged. Only optional location
        // enrichment is bounded, BEFORE any selector, ancestry copy or subtree-text diagnostics.
        val detailedLocations = run {
            var current: Node? = document
            var depth = 0
            var visited = 0
            while (current != null) {
                if (++visited > 2048 || depth > 64) return@run false
                if (current.childNodeSize() != 0) {
                    current = current.childNode(0); depth++
                } else {
                    while (current != null && current.nextSibling() == null) { current = current.parentNode(); depth-- }
                    current = current?.nextSibling()
                }
            }
            true
        }
        if (regexSecret && !detailedLocations) reject(documentOnly = true)
        var remainingDiagnostics = 8192
        fun diagnosticStep() { if (--remainingDiagnostics < 0) reject(documentOnly = true) }
        fun code(element: Element) = element.tagName() == "pre" || element.tagName() == "ac:plain-text-body" ||
            (element.tagName() in setOf("ac:structured-macro", "ac:macro") && element.attr("ac:name") == "code")
        val elements by lazy(LazyThreadSafetyMode.NONE) { document.allElements.toList() }
        val codes by lazy(LazyThreadSafetyMode.NONE) { elements.filter { code(it) && it.parents().none(::code) } }
        val tables = document.select("table").toList()
        val paragraphs by lazy(LazyThreadSafetyMode.NONE) { document.select("p").toList() }
        val items by lazy(LazyThreadSafetyMode.NONE) { document.select("li").toList() }
        fun owner(element: Element): Element {
            diagnosticStep()
            val chain = listOf(element) + element.parents()
            return chain.lastOrNull(::code) ?: chain.firstOrNull { it.tagName() in setOf("td", "th") }
                ?: chain.firstOrNull { it.tagName() == "p" } ?: chain.firstOrNull { it.tagName() == "li" }
                ?: chain.firstOrNull { it.tagName() == "table" } ?: document
        }
        fun record(element: Element, name: String, pattern: Int? = null) {
            if (!detailedLocations) reject(documentOnly = true)
            diagnosticStep()
            val block = owner(element)
            val table = block.closest("table")
            val kind = when {
                block in codes -> "code"
                table != null -> "table"
                block.tagName() == "p" -> "paragraph"
                block.tagName() == "li" -> "list_item"
                else -> "document"
            }
            val index = when (kind) {
                "code" -> codes.indexOf(block) + 1
                "table" -> tables.indexOf(table) + 1
                "paragraph" -> paragraphs.indexOf(block) + 1
                "list_item" -> items.indexOf(block) + 1
                else -> 1
            }
            val row = if (kind == "table") block.closest("tr") else null
            val rowIndex = row?.let { table!!.select("tr").filter { it.closest("table") === table }.indexOf(it) + 1 }
            val cellIndex = if (row != null && block.tagName() in setOf("td", "th"))
                row.children().filter { it.tagName() in setOf("td", "th") }.indexOf(block) + 1 else null
            val lines: List<Int?> = if (kind == "code" && pattern != null) {
                // Expand each request-local code/field/pattern once. Repeated raw and visible
                // detections must not rescan a large block or collapse distinct offending lines.
                if (!expandedCodes.add("$index/$name/$pattern")) return
                val payload = if (block.tagName() in setOf("pre", "ac:plain-text-body")) block
                    else block.children().singleOrNull { it.tagName() == "ac:plain-text-body" }
                val content = payload?.wholeText()?.replace("\r\n", "\n")?.replace('\r', '\n')
                val found = linkedSetOf<Int>()
                if (content != null) {
                    var offset = 0
                    var line = 1
                    for (match in patterns[pattern].findAll(content)) {
                        if (matchField(pattern, match) != name) continue
                        while (offset < match.range.first) { if (content[offset] == '\n') line++; offset++ }
                        found += line
                        if (found.size == 11) break
                    }
                }
                if (found.isEmpty()) listOf(null) else found.toList()
            } else listOf(null)
            for (line in lines) {
                val key = "$kind/$index/$rowIndex/$cellIndex/$name/$line"
                if (key in locations) continue
                if (locations.size == 10) { truncated = true; return }
                locations[key] = buildJsonObject {
                    put("block", kind); put("index", index); put("field", name)
                    line?.let { put("line", it) }
                    rowIndex?.takeIf { it > 0 }?.let { put("row", it) }
                    cellIndex?.takeIf { it > 0 }?.let { put("cell", it) }
                }
            }
        }
        if (regexSecret) {
            val starts = java.util.TreeMap<Int, Element>()
            elements.filter { it.sourceRange().isTracked }.forEach { starts[it.sourceRange().start().pos()] = it }
            patterns.forEachIndexed { number, pattern ->
                for (match in pattern.findAll(body)) {
                    diagnosticStep()
                    val element = starts.floorEntry(match.range.first)?.value?.let { candidate ->
                        (listOf(candidate) + candidate.parents()).firstOrNull {
                            val opening = it.sourceRange(); val closing = it.endSourceRange()
                            opening.isTracked && opening.start().pos() <= match.range.first &&
                                (if (closing.isTracked) closing.end().pos() else opening.end().pos()) > match.range.first
                        }
                    } ?: document
                    record(element, matchField(number, match), number)
                    if (truncated) reject()
                }
                // Decoded entities and inline elements may hide a match in raw storage. The global
                // document.text check above remains authoritative, including matches across blocks.
                if (pattern.containsMatchIn(text)) {
                    var located = false
                    elements.map(::owner).distinct().filter { it !== document }.forEach { block ->
                        for (match in pattern.findAll(block.text())) {
                            diagnosticStep()
                            if (block.allElements.any { child -> owner(child) !== block && child.text().contains(match.value) }) continue
                            record(block, matchField(number, match), number)
                            located = true
                            if (truncated) reject()
                        }
                    }
                    if (!located && locations.isEmpty()) record(document, matchField(number, pattern.find(text)!!))
                }
            }
        }
        tables.forEach { table ->
            val rows = table.select("tr").filter { it.closest("table") === table }
            val grid = try { tableGrid(rows) } catch (failure: RuntimeException) {
                if (message != null) reject()
                throw failure
            }
            rows.forEachIndexed { index, row ->
                val cells = row.children().filter { it.tagName() in setOf("td", "th") }
                cells.forEachIndexed { column, label ->
                    if (credential(label.text())) {
                        // Vertical key/value rows and horizontal (possibly merged) headers are both common.
                        val vertical = cells.getOrNull(column + 1)?.tagName() == "td"
                        if (vertical && !redacted(cells[column + 1])) {
                            if (message == null) message = "Credential values cannot be published in tables"
                            record(cells[column + 1], field(label.text()))
                        }
                        if (!vertical) grid[index].filterValues { it === label }.keys.forEach { col ->
                            grid.drop(index + 1).mapNotNull { it[col] }.distinct().filter { it !== label }.forEach { value ->
                                if (value.tagName() == "th" && Regex("(?i)^(?:dev(?:elopment)?|개발|qa)(?:\\s*[1-9][0-9]*차)?$").matches(value.text().trim())) return@forEach
                                if (!redacted(value)) {
                                    if (message == null) message = "Credential values cannot be published under table headers"
                                    record(value, field(label.text()))
                                }
                            }
                        }
                    }
                }
            }
        }
        if (message != null) reject()
    }

    private fun validateNewDecisions(added: String) {
        if (added.isBlank()) return
        val doc = xml(added)
        val allowed = setOf("#root", "h2", "h3", "p", "ul", "ol", "li", "blockquote", "q", "a", "span", "strong", "em", "b", "i", "br",
            "ac:structured-macro", "ac:parameter", "ac:link", "ri:user", "ri:page")
        require(doc.allElements.all { it.tagName() in allowed } &&
            doc.children().all { it.tagName() in setOf("h2", "h3", "p", "ul", "ol", "blockquote") } &&
            doc.textNodes().all { it.text().isBlank() }) {
            "Decision proposals use heading and paragraph/list items; unsupported human-field layouts are not writable"
        }
        require(doc.select("a[href]").isNotEmpty() && (doc.select("blockquote,q").isNotEmpty() || Regex("[\"“][^\"”]+[\"”]").containsMatchIn(doc.text()))) {
            "Decision proposals require a source quotation and link"
        }
        require(!Regex("결정됨|반영됨").containsMatchIn(doc.text())) { "New proposals cannot assert a human decision" }
        val lines = doc.select("p,li").filter { Regex("^결정(?:\\s|[:(（])").containsMatchIn(it.text()) }
        require(lines.isNotEmpty() && lines.all { line ->
            val value = line.text()
            Regex("^결정(?:\\s*[(（][^:：]*[)）])?\\s*[:：]\\s*$").matches(value)
        }) { "New decision items must leave the human decision line empty" }
        require(doc.select("h3").isNotEmpty()) { "New decision proposals require an item heading" }
        doc.select("h3").forEach { heading ->
            val section = heading.nextElementSiblings().takeWhile { it.tagName() != "h3" && it.tagName() != "h2" }
            val item = xml(section.joinToString("") { it.outerHtml() })
            val itemLines = item.select("p,li")
            require(itemLines.count { Regex("^결정(?:\\s|[:(（])").containsMatchIn(it.text()) } == 1 &&
                itemLines.any { it.text().startsWith("근거") } && itemLines.any { it.text().startsWith("문제") } &&
                itemLines.any { it.text().startsWith("개발 제안") }) { "Each new proposal needs evidence, problem, proposal and one empty human decision line" }
            require(item.select("a[href]").isNotEmpty() && (item.select("blockquote,q").isNotEmpty() ||
                Regex("[\"“][^\"”]+[\"”]").containsMatchIn(item.text()))) { "Each proposal requires its own source link and quotation" }
        }
    }

    /** Expand spans once so credential and TC-result checks interpret the same actual columns. */
    private fun tableGrid(rows: List<Element>): List<Map<Int, Element>> {
        require(rows.size <= 5000) { "Table is too large to validate safely" }
        val grid = List(rows.size) { linkedMapOf<Int, Element>() }
        rows.forEachIndexed { rowIndex, row ->
            var column = 0
            row.children().filter { it.tagName() in setOf("td", "th") }.forEach { cell ->
                while (grid[rowIndex].containsKey(column)) column++
                val width = cell.attr("colspan").ifBlank { "1" }.toIntOrNull() ?: error("Ambiguous table column span")
                val height = cell.attr("rowspan").ifBlank { "1" }.toIntOrNull() ?: error("Ambiguous table row span")
                require(width in 1..100 && height in 1..5000 && column + width <= 100 && rowIndex + height <= rows.size) { "Invalid table spans" }
                for (r in rowIndex until rowIndex + height) for (c in column until column + width) {
                    require(grid[r].put(c, cell) == null) { "Overlapping table spans" }
                }
                column += width
            }
        }
        return grid
    }

    private data class ResultCell(val key: String, val lane: String, val element: Element, val start: Int, val end: Int)

    private fun resultCells(config: ContextConfig, body: String): List<ResultCell> {
        val doc = Jsoup.parse(body, "", Parser.xmlParser().setTrackPosition(true))
        val result = mutableListOf<ResultCell>()
        fun field(text: String): String? = when (text.trim().lowercase().replace(Regex("[_ -]+"), " ").replace(Regex("^(?:dev(?:elopment)?|개발|qa)\\s*(?:[1-9][0-9]*차\\s*)?"), "")) {
            "result", "결과" -> "result"; "evidence", "증적" -> "evidence"; else -> null
        }
        fun stage(text: String): String? {
            val dev = Regex("(?i)(?<![a-z])dev(?:elopment)?(?![a-z])|개발").containsMatchIn(text)
            val qa = Regex("(?i)(?<![a-z])qa(?![a-z])").containsMatchIn(text)
            return if (dev == qa) null else if (dev) "dev" else "qa"
        }
        doc.select("table").forEachIndexed { tableIndex, table ->
            // An example table embedded in a Step/Expected cell is TC content, not an execution destination.
            if (table.parents().any { it.tagName() == "table" }) return@forEachIndexed
            val rows = table.select("tr").filter { it.closest("table") === table }
            if (rows.isEmpty()) return@forEachIndexed
            val grid = tableGrid(rows)
            val headers = rows.takeWhile { row ->
                val cells = row.children().filter { it.tagName() in setOf("td", "th") }
                cells.isNotEmpty() && (cells.all { it.tagName() == "th" } ||
                    (row === rows.first() && cells.any { field(it.text()) != null } && cells.any {
                        Regex("(?i)^(?:TC(?: ID)?|ID|Step(?:s)?|Test Steps|Expected(?: Result)?|절차|기대결과|번호)$").matches(it.text().trim())
                    }))
            }.size
            if (headers == 0) return@forEachIndexed
            val columns = linkedMapOf<Int, Pair<String, String>>()
            val nearbyHeading = table.previousElementSiblings().firstOrNull { it.tagName().matches(Regex("h[1-6]")) }?.text() ?: ""
            grid.take(headers).flatMap { it.keys }.distinct().forEach { column ->
                val labels = grid.take(headers).mapNotNull { it[column] }.distinct().map { it.text() }
                val kind = labels.lastOrNull()?.let(::field) ?: return@forEach
                val identity = labels.joinToString(" ") + " " + nearbyHeading
                val laneStage = stage(identity) ?: return@forEach
                val rounds = Regex("([1-9][0-9]*)차|(?i:round)\\s*([1-9][0-9]*)").findAll(identity)
                    .map { it.groupValues[1].ifBlank { it.groupValues[2] } }.toSet()
                require(rounds.size <= 1) { "Ambiguous TC round header" }
                val lane = "$laneStage/${rounds.singleOrNull() ?: "1"}"
                columns[column] = lane to kind
            }
            require(columns.values.groupingBy { it }.eachCount().values.all { it == 1 }) { "Ambiguous duplicate TC result/evidence headers" }
            val allowedStage = if (config.role == "개발") "dev" else "qa"
            val seen = mutableSetOf<Element>()
            rows.indices.drop(headers).forEach { row ->
                grid[row].forEach { (column, cell) ->
                    val identity = columns[column] ?: return@forEach
                    if (!identity.first.startsWith("$allowedStage/") || !seen.add(cell)) return@forEach
                    require(cell.tagName() == "td" && cell.select("table").isEmpty()) { "Ambiguous nested TC result cell" }
                    require(grid[row].filterValues { it === cell }.keys.all { columns[it] == identity }) { "Result cell crosses protected columns" }
                    val opening = cell.sourceRange()
                    val closing = cell.endSourceRange()
                    require(opening.isTracked) { "Cannot locate exact TC result storage range" }
                    val end = if (closing.isTracked) closing.end().pos() else opening.end().pos()
                    require(end > opening.start().pos()) { "Cannot locate complete TC result cell" }
                    result += ResultCell("$tableIndex/$row/$column/${identity.second}", identity.first, cell, opening.start().pos(), end)
                }
            }
        }
        return result
    }

    private fun preserveTcDefinitions(config: ContextConfig, before: String, after: String) {
        if (before == after) return
        val original = resultCells(config, before)
        val edited = resultCells(config, after)
        require(original.isNotEmpty() && original.map { it.key to it.lane } == edited.map { it.key to it.lane }) {
            "TC results require unambiguous existing stage/round Result/Evidence table cells"
        }
        val changedLanes = mutableSetOf<String>()
        original.zip(edited).forEach { (a, b) ->
            require(a.element.tagName() == b.element.tagName() && a.element.attributes().html() == b.element.attributes().html()) {
                "TC result cell structure and attributes must not change"
            }
            if (before.substring(a.start, a.end) != after.substring(b.start, b.end)) changedLanes += a.lane
        }
        require(changedLanes.size <= 1) { "One TC publication may update only one stage and round" }
        if (config.role == "QA") preserveQaFailureHistory(config, before, after)
        fun mask(body: String, cells: List<ResultCell>): String {
            val out = StringBuilder()
            var cursor = 0
            cells.sortedBy { it.start }.forEach { cell ->
                require(cell.start >= cursor && cell.end <= body.length) { "Ambiguous overlapping TC cell ranges" }
                out.append(body, cursor, cell.start).append("<talos-result-cell key=\"").append(cell.key).append("\"/>")
                cursor = cell.end
            }
            return out.append(body, cursor, body.length).toString()
        }
        require(mask(before, original) == mask(after, edited)) { "TC definitions, Steps, Expected, human fields and other lanes must remain exact storage" }
    }

    /** QA authoring and execution updates share one immutable failure-history boundary. */
    private fun preserveQaFailureHistory(config: ContextConfig, before: String, after: String) {
        if (before == after) return
        val qa = config.copy(role = "QA")
        val original = resultCells(qa, before)
        val edited = resultCells(qa, after).associateBy { it.key }
        val fail = Regex("(?<![A-Za-z0-9_])FAIL(?![A-Za-z0-9_])", RegexOption.IGNORE_CASE)
        // XML's text() may join adjacent block contents into "PASSFAIL". HTML block semantics retain the verdict boundary.
        require(original.isNotEmpty() || !fail.containsMatchIn(Jsoup.parse(before).text())) {
            "Existing failure history requires an unambiguous QA Result/Evidence table mapping"
        }
        fun inner(source: String, cell: ResultCell): String {
            val opening = cell.element.sourceRange()
            val closing = cell.element.endSourceRange()
            return if (closing.isTracked) source.substring(opening.end().pos(), closing.start().pos()) else ""
        }
        fun retain(cell: ResultCell) {
            val next = edited[cell.key]
            require(next != null && next.lane == cell.lane && inner(after, next).startsWith(inner(before, cell))) {
                "Prior QA FAIL result history and evidence must be retained as exact storage prefixes"
            }
        }
        original.filter { it.key.endsWith("/result") && fail.containsMatchIn(Jsoup.parse(it.element.outerHtml()).text()) }.forEach { failure ->
            retain(failure)
            val parts = failure.key.split('/')
            val row = parts[1].toInt()
            val span = failure.element.attr("rowspan").ifBlank { "1" }.toInt()
            original.filter { cell ->
                val key = cell.key.split('/')
                key[0] == parts[0] && key[1].toInt() in row until row + span && cell.lane == failure.lane && key.last() == "evidence"
            }.forEach(::retain)
        }
    }

    private data class DefinitionScope(val category: String, val group: RemoteNode)
    private fun definitionScope(path: List<RemoteNode>): DefinitionScope? {
        val names = path.map { it.title }
        val leaf = development.firstOrNull { names.take(it.size) == it && it.last() in setOf("1. Module Specification", "02. Screen List/Specification", "03. API List/Specification") } ?: return null
        val group = path.getOrNull(leaf.size) ?: return null
        val category = when (leaf.last()) { "1. Module Specification" -> "Module"; "02. Screen List/Specification" -> "Screen"; else -> "API" }
        val pattern = when (category) { "Screen" -> "SC-[A-Z0-9]+\\..+"; "API" -> "AP-[A-Z0-9]+\\..+"; else -> "[A-Za-z0-9_-]+\\..+" }
        return if (Regex(pattern).matches(group.title)) DefinitionScope(category, group) else null
    }

    private fun definitions(scope: DefinitionScope, title: String, body: String): Set<String> {
        val pattern = when (scope.category) {
            "Screen" -> "SC-[A-Z0-9]+-[0-9]+(?:-[0-9]+)*"
            "API" -> "AP-[A-Z0-9]+-[0-9]+(?:-[0-9]+)*"
            else -> "(?:[A-Z][A-Z0-9]*-)+[0-9]+(?:-[0-9]+)*"
        }
        val regex = Regex("(?<![A-Za-z0-9_-])(?:$pattern)(?![A-Za-z0-9_-])")
        fun ids(value: String) = regex.findAll(value).map { it.value }.filter {
            scope.category != "Module" || !Regex("^(?:SC|AP|R|UA|PMISSUE)-").containsMatchIn(it)
        }.toList()
        val doc = xml(body)
        val result = ids(title).toMutableSet()
        doc.select("h1,h2,h3,h4,h5,h6").forEach { result += ids(it.text()) }
        doc.select("table").forEach { table ->
            var columns = emptySet<Int>()
            table.select("tr").forEach { row ->
                val cells = row.children().filter { it.tagName() in setOf("td", "th") }
                val labels = cells.map { it.text().trim() }
                val accepted = setOf("ID", "${scope.category} ID", "${scope.category} Name")
                if (labels.any { it in accepted }) columns = labels.indices.filter { labels[it] in accepted }.toSet()
                columns.forEach { index -> cells.getOrNull(index)?.let { result += ids(it.text()) } }
                if (cells.size >= 2 && labels.first() in accepted) cells.drop(1).forEach { result += ids(it.text()) }
            }
        }
        return result
    }

    private suspend fun checkAddedDefinitions(config: ContextConfig, path: List<RemoteNode>, title: String, body: String, before: RemotePage?) {
        val scope = definitionScope(path) ?: return
        val added = definitions(scope, title, body) - (before?.let { definitions(scope, it.node.title, it.body) } ?: emptySet())
        if (added.isEmpty()) return
        added.forEach { id ->
            val candidates = remote.search("space = \"${config.space}\" and (title ~ \"$id\" or text ~ \"$id\")")
            candidates.filter { it.type == "page" && it.id != before?.node?.id }.forEach { candidate ->
                val page = remote.page(candidate.id)
                val candidateScope = definitionScope(ancestry(config, page.node))
                if (candidateScope != null && candidateScope.category == scope.category) {
                    require(id !in definitions(candidateScope, page.node.title, page.body)) { "A newly added ID is already defined remotely" }
                }
            }
        }
        if (scope.group.id == "new") return
        val pending = java.util.ArrayDeque<RemoteNode>()
        pending.add(scope.group)
        val seen = mutableSetOf<String>()
        val existing = mutableSetOf<String>()
        while (pending.isNotEmpty()) {
            val node = pending.removeFirst()
            require(seen.add(node.id) && seen.size <= 5000) { "Definition group is cyclic or too large" }
            if (node.type == "page") {
                val page = remote.page(node.id)
                require(page.node.spaceId == config.spaceId && page.node.status == "current") { "Definition group changed" }
                existing += definitions(scope, page.node.title, page.body)
            }
            remote.children(node.id, node.type).forEach { child ->
                require(child.spaceId == config.spaceId) { "Definition group crosses spaces" }
                pending.add(child)
            }
        }
        added.sorted().forEach { id -> requireSuccessor(existing, id); existing += id }
    }

    private fun requireSuccessor(existing: Set<String>, next: String) {
        val numbers = Regex("[0-9]+")
        fun shape(value: String) = numbers.replace(value) { "#${it.value.length}" }
        val peers = existing.filter { shape(it) == shape(next) }
        if (peers.isEmpty()) {
            require(existing.none { numbers.replace(it, "#") == numbers.replace(next, "#") }) { "New ID must preserve the established numeric width" }
            return
        }
        val parts = numbers.findAll(next).map { it.value }.toList()
        val values = peers.map { numbers.findAll(it).map { match -> match.value }.toList() }
        require(values.all { row -> row.size == parts.size && row.indices.all { row[it].length == parts[it].length } }) { "New ID must preserve the established numeric width" }
        val varying = parts.indices.filter { index -> values.map { it[index] }.toSet().size > 1 }
        val slot = if (parts.size == 1) 0 else varying.singleOrNull()
            ?: error("The group's successor pattern is ambiguous; do not guess an ID")
        require(parts.indices.all { it == slot || values.all { row -> row[it] == parts[it] } }) { "New ID must continue the same group sequence" }
        val ordered = values.map { it[slot].toLong() }.distinct().sorted()
        val step = if (parts.size == 1) 1L else ordered.zipWithNext { a, b -> b - a }.distinct().singleOrNull()
            ?: error("The group's numeric step is ambiguous")
        require(parts[slot].toLong() == ordered.last() + step) { "New IDs must follow the group maximum without filling gaps" }
    }
}
