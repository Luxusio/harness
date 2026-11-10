package io.crscube.clitools.confluence

import kotlinx.serialization.json.*
import org.jsoup.Jsoup
import org.jsoup.parser.Parser
import java.net.URI
import java.time.LocalDate
import java.time.format.DateTimeFormatter

/** Read-only resolution. Its selection is reusable, but every publication must revalidate the binding. */
class ContextResolver(private val remote: ConfluenceRemote) {
    private val suffixes = linkedMapOf("plan" to "개발 계획", "impact" to "영향도", "decisions" to "결정 사항",
        "confirmation" to "확인 결과", "urs" to "URS 초안", "tc" to "TC", "tc-review" to "TC Review", "review" to "리뷰 결과")

    suspend fun resolve(config: ContextConfig, pmIssueKey: String, request: JsonObject): JsonObject {
        if (config.isKnowledge) return resolveKnowledge(config.forIssue(pmIssueKey), pmIssueKey, request)
        require(pmIssueKey.matches(Regex("${Regex.escape(config.pmProject)}-[1-9][0-9]*"))) { "Configured PM issue required" }
        val issue = remote.publishingIssue(pmIssueKey)
        val fields = issue.getValue("fields").jsonObject
        val versions = fields.getValue("fixVersions").jsonArray.map { value ->
            val v = value.jsonObject
            buildJsonObject { put("id", v.text("id")); put("name", v.text("name")); v.optional("releaseDate")?.let { put("releaseDate", it) } }
        }.sortedBy { it.text("id") }
        val nodes = remote.inventory(config).associateBy { it.id }
        // Complete space inventory also contains archived/orphan content unrelated to this issue.
        // Determine membership by IDs first; validate status only on the hierarchy we may use.
        fun isBelow(id: String, ancestor: String): Boolean {
            var current: RemoteNode? = nodes[id]
            val seen = mutableSetOf<String>()
            while (current != null) {
                require(seen.add(current.id) && seen.size <= 100) { "Cyclic/deep Confluence hierarchy" }
                if (current.id == ancestor) return true
                current = current.parentId?.let { nodes[it] }
            }
            return false
        }
        fun chain(id: String): List<RemoteNode> {
            val result = mutableListOf<RemoteNode>()
            var current = nodes[id] ?: error("Selected Confluence content is absent from complete inventory")
            val seen = mutableSetOf<String>()
            while (current.id != config.homePageId) {
                require(seen.add(current.id) && seen.size <= 100 && current.spaceId == config.spaceId && current.status == "current") {
                    "Hierarchy identity or status is invalid"
                }
                result.add(0, current)
                current = nodes[current.parentId] ?: error("Hierarchy does not reach configured home")
            }
            require(current.spaceId == config.spaceId && current.status == "current") { "Configured hierarchy home is invalid" }
            return result
        }
        val linkedPages = linkedPages(config, fields.getValue("description"))
        val selectedRelease = request.optional("releaseId")
        selectedRelease?.let(::positive)
        val selectedVersion = request.optional("versionId")
        selectedVersion?.let { require(versions.any { version -> version.text("id") == it }) { "Selected Jira version is not assigned to this issue" } }
        val candidates = nodes.values.filter { n -> selectedRelease == null || n.id == selectedRelease }.mapNotNull { n ->
            val date = releaseDate(n.title) ?: return@mapNotNull null
            if (selectedRelease == null && !isBelow(n.id, config.homePageId)) return@mapNotNull null
            val ancestry = chain(n.id)
            if (ancestry.size != 3 || ancestry[0].title != "60. Maintenance") return@mapNotNull null
            val year = Regex("(?<![0-9])(?:19|20|21)[0-9]{2}(?![0-9])").findAll(ancestry[1].title).map { it.value }.toList()
            if (year != listOf(date.take(4))) return@mapNotNull null
            n to date
        }
        require(candidates.isNotEmpty()) { "No release matches the real Maintenance/year/release hierarchy" }
        val matches = mutableListOf<Pair<RemoteNode, String>>()
        val evidence = mutableMapOf<String, String>()
        for ((node, date) in candidates) {
            val body = if (node.type == "page") remote.page(node.id).body else ""
            val text = Jsoup.parse(body, "", Parser.xmlParser()).text()
            val exactIssueLink = linkedPages.any { it in nodes && isBelow(it, node.id) && chain(it).any { ancestor -> ancestor.id == node.id } }
            val matchedVersions = versions.filter { version ->
                (selectedVersion == null || version.text("id") == selectedVersion) &&
                    (version.optional("releaseDate")?.replace("-", "")?.let { it == date } ?: true) &&
                    (version.optional("releaseDate")?.replace("-", "") == date || exactToken(text, version.text("name")) ||
                        Regex("(?i)(?:fixVersionId|versionId|버전\\s*ID)\\s*[:=]\\s*${Regex.escape(version.text("id"))}(?![0-9])").containsMatchIn(text) ||
                        (version.optional("releaseDate") == null && exactIssueLink))
            }
            if (versions.isNotEmpty()) {
                if (matchedVersions.size == 1) {
                    matches += node to date
                    evidence[node.id] = "exact-jira-version:${matchedVersions.single().text("id")}"
                }
            } else {
                // No release inference from today's date or a title similarity. An explicit confirmed date is required.
                val confirmed = request.optional("releaseDate")
                if (selectedRelease == node.id && confirmed == date &&
                    (exactIssueLink || exactToken(text, date) || exactToken(text, LocalDate.parse(date, DateTimeFormatter.BASIC_ISO_DATE).toString()))) {
                    matches += node to date
                    evidence[node.id] = if (exactIssueLink) "explicit-release-and-jira-page-link" else "explicit-release-date-confirmed-in-remote-body"
                }
            }
        }
        require(matches.size == 1) { "Release association is missing or ambiguous; supply a proven release, never a substitute location" }
        val (release, date) = matches.single()
        val content = nodes.values.filter { it.parentId == release.id && it.status == "current" && it.title == "${date}_이관 내용" }
        require(content.size == 1) { "The selected release requires exactly one existing release-content container" }
        val contentNode = content.single()
        val selectedCenter = request.optional("centerId")
        selectedCenter?.let(::positive)
        val descendants = nodes.values.filter { n -> n.id != contentNode.id && isBelow(n.id, contentNode.id) }
        descendants.forEach { chain(it.id) }
        val possible = descendants.filter { n -> n.type == "page" &&
            (selectedCenter == null || n.id == selectedCenter) && suffixes.values.none { n.title == "$pmIssueKey $it" } &&
            !Regex("^${Regex.escape(pmIssueKey)} 리뷰 결과 [1-9][0-9]*차$").matches(n.title) }
        val centers = mutableListOf<RemotePage>()
        val uncertainCenters = mutableListOf<String>()
        for (node in possible) {
            val page = remote.page(node.id)
            require(page.node == node) { "Center metadata changed during resolution" }
            val links = issueLinks(config, page.body)
            val exactKey = Regex("(?<![A-Z0-9_-])${Regex.escape(pmIssueKey)}(?![A-Z0-9_-])")
            if (links == setOf(pmIssueKey) && (selectedCenter != null || node.id in linkedPages || exactKey.containsMatchIn(node.title))) centers += page
            else if (pmIssueKey in links || exactKey.containsMatchIn(page.body) || exactKey.containsMatchIn(node.title)) uncertainCenters += node.id
        }
        require(selectedCenter != null || uncertainCenters.isEmpty()) {
            "Potential existing issue planning content requires explicit center selection and verified issue linkage; absence is not proven"
        }
        require(selectedCenter == null || centers.size == 1) { "Selected center is not a unique exact-PM-linked descendant of release content" }
        require(centers.size <= 1) { "Multiple issue centers; select the existing planning center explicitly" }
        val center = centers.singleOrNull()
        val docs = buildJsonObject {
            if (center != null) suffixes.forEach { (kind, suffix) ->
                val parent = if (kind == "tc-review") descendants.singleOrNull { it.parentId == center.node.id && it.title == "$pmIssueKey TC" }?.id else center.node.id
                val found = descendants.filter { it.parentId == parent && it.type == "page" && it.title == "$pmIssueKey $suffix" }
                require(found.size <= 1) { "Duplicate canonical $kind documents require reconciliation" }
                put(kind, found.singleOrNull()?.let { document(it) } ?: JsonNull)
            }
        }
        val selection = buildJsonObject {
            put("releaseId", release.id); request.optional("releaseDate")?.let { put("releaseDate", it) }
            selectedVersion?.let { put("versionId", it) }
            center?.let { put("centerId", it.node.id) }
        }
        val identity = buildJsonObject {
            put("pmIssueKey", pmIssueKey); put("site", config.site); put("spaceId", config.spaceId); put("homePageId", config.homePageId)
            put("jiraVersions", JsonArray(versions)); put("releaseId", release.id); put("contentId", contentNode.id)
            center?.let { put("centerId", it.node.id) }; put("releaseDate", date)
            put("releaseEvidence", evidence.getValue(release.id))
            put("jiraDescriptionHash", ContextFiles.hash(fields.getValue("description").toString()))
            put("releaseBodyHash", if (release.type == "page") ContextFiles.hash(remote.page(release.id).body) else "folder")
            put("hierarchy", JsonArray((center?.let { chain(it.node.id) } ?: chain(contentNode.id)).map { n ->
                buildJsonObject { put("id", n.id); put("parentId", n.parentId); put("title", n.title); put("type", n.type); put("spaceId", n.spaceId) }
            }))
            center?.let { put("centerBodyHash", ContextFiles.hash(it.body)) }
        }
        return buildJsonObject {
            identity.forEach { (k, v) -> put(k, v) }
            put("status", if (center == null) "center_missing" else "resolved")
            put("bindingId", ContextFiles.hash(identity.toString())); put("selection", selection); put("documents", docs)
        }
    }

    private suspend fun resolveKnowledge(config: ContextConfig, key: String, request: JsonObject): JsonObject {
        val root = config.knowledgeRootPageId ?: error("Common publishing requires a registered existing knowledgeRootPageId")
        val issue = remote.publishingIssue(key) // Access failure is never proof that a center is absent.
        val nodes = remote.inventory(config).associateBy { it.id }
        fun chain(id: String): List<RemoteNode> {
            val path = mutableListOf<RemoteNode>()
            var current = nodes[id] ?: error("Registered content is absent from complete inventory")
            val seen = mutableSetOf<String>()
            while (true) {
                require(seen.add(current.id) && seen.size <= 100 && current.status == "current" && current.spaceId == config.spaceId) {
                    "Knowledge hierarchy must be current and remain in the registered space"
                }
                path.add(0, current)
                if (current.id == root) return path
                current = nodes[current.parentId] ?: error("Knowledge root must descend from the registered home")
            }
        }
        val rootChain = chain(root)
        require(rootChain.last().type in setOf("page", "folder")) { "Knowledge root must be an existing page or folder" }
        fun below(node: RemoteNode): Boolean {
            var current: RemoteNode? = node
            val seen = mutableSetOf<String>()
            while (current != null) {
                require(seen.add(current.id) && seen.size <= 100) { "Invalid knowledge hierarchy" }
                if (current.id == root) return true
                current = current.parentId?.let { nodes[it] }
            }
            return false
        }
        val descendants = nodes.values.filter { it.id != root && below(it) }
        descendants.forEach { chain(it.id) }
        val selectedCenter = request.optional("centerId")?.also(::positive)
        val exactKey = Regex("(?<![A-Z0-9_-])${Regex.escape(key)}(?![A-Z0-9_-])")
        val centers = mutableListOf<RemotePage>()
        val uncertain = mutableListOf<String>()
        val candidates = descendants.filter { it.type == "page" && (selectedCenter == null || it.id == selectedCenter) &&
            suffixes.values.none { suffix -> it.title == "$key $suffix" } && !Regex("^${Regex.escape(key)} 리뷰 결과 [1-9][0-9]*차$").matches(it.title) }
        for (node in candidates) {
            val page = remote.scopedPage(config, node.id)
            require(page.node == node) { "Knowledge center metadata changed during resolution" }
            val links = issueLinks(config, page.body)
            if (links == setOf(key) && (selectedCenter != null || exactKey.containsMatchIn(node.title))) centers += page
            else if (key in links || exactKey.containsMatchIn(node.title) || exactKey.containsMatchIn(page.body)) uncertain += node.id
        }
        require(selectedCenter != null || uncertain.isEmpty()) { "Potential existing issue content requires explicit verified center selection" }
        require(centers.size <= 1 && (selectedCenter == null || centers.size == 1)) { "Select one existing exact-issue-linked knowledge center" }
        val center = centers.singleOrNull()
        val documents = buildJsonObject {
            if (center != null) suffixes.forEach { (kind, suffix) ->
                val tc = descendants.filter { it.parentId == center.node.id && it.title == "$key TC" && it.type == "page" }
                require(tc.size <= 1) { "Duplicate canonical TC documents require reconciliation" }
                val parent = if (kind == "tc-review") tc.singleOrNull()?.id else center.node.id
                val found = descendants.filter { it.parentId == parent && it.type == "page" && it.title == "$key $suffix" }
                require(found.size <= 1) { "Duplicate canonical $kind documents require reconciliation" }
                put(kind, found.singleOrNull()?.let(::document) ?: JsonNull)
            }
        }
        val identity = buildJsonObject {
            put("mode", "knowledge"); put("knowledgeRootPageId", root); put("contentId", root)
            put("site", config.site); put("spaceId", config.spaceId); put("homePageId", config.homePageId)
            put("pmIssueKey", key); put("issueKey", key); issue["id"]?.let { put("jiraIssueId", it) }
            put("jiraDescriptionHash", ContextFiles.hash(issue.getValue("fields").jsonObject.getValue("description").toString()))
            center?.let { put("centerId", it.node.id); put("centerBodyHash", ContextFiles.hash(it.body)) }
            put("hierarchy", JsonArray((center?.let { chain(it.node.id) } ?: rootChain).map { node -> buildJsonObject {
                put("id", node.id); put("parentId", node.parentId); put("title", node.title); put("type", node.type); put("spaceId", node.spaceId)
            } }))
        }
        return buildJsonObject {
            identity.forEach { (name, value) -> put(name, value) }
            put("status", if (center == null) "center_missing" else "resolved")
            put("bindingId", ContextFiles.hash(identity.toString()))
            put("selection", buildJsonObject { center?.let { put("centerId", it.node.id) } })
            put("documents", documents)
        }
    }

    suspend fun revalidate(config: ContextConfig, pmIssueKey: String, binding: JsonObject): JsonObject {
        val current = resolve(config, pmIssueKey, binding.getValue("selection").jsonObject)
        require(current.text("bindingId") == binding.text("bindingId")) { "Confluence binding is stale; resolve and reconcile before publishing" }
        return current
    }

    private fun releaseDate(title: String): String? {
        val value = Regex("^(?:[0-9]+\\.\\s*)?([0-9]{8})(?:\\s*이관)?$").matchEntire(title)?.groupValues?.get(1) ?: return null
        return try { LocalDate.parse(value, DateTimeFormatter.BASIC_ISO_DATE); value } catch (_: Exception) { null }
    }
    private fun exactToken(text: String, token: String): Boolean = Regex("(?<![\\p{L}\\p{N}_.-])${Regex.escape(token)}(?![\\p{L}\\p{N}_.-])").containsMatchIn(text)
    private fun positive(value: String) = require(value.matches(Regex("[1-9][0-9]*"))) { "Positive Confluence IDs required" }
    private fun JsonObject.text(name: String) = getValue(name).jsonPrimitive.content
    private fun JsonObject.optional(name: String) = get(name)?.jsonPrimitive?.contentOrNull
    private fun document(node: RemoteNode) = buildJsonObject {
        put("pageId", node.id); put("parentId", node.parentId); put("title", node.title); put("version", node.version)
    }

    private fun issueLinks(config: ContextConfig, body: String): Set<String> = Jsoup.parse(body, "", Parser.xmlParser()).select("a[href]").mapNotNull {
        val url = sameOrigin(config, it.attr("href")) ?: return@mapNotNull null
        Regex(if (config.isKnowledge) "^/browse/([A-Z][A-Z0-9_]*-[1-9][0-9]*)$" else "^/browse/(${Regex.escape(config.pmProject)}-[1-9][0-9]*)$").matchEntire(url.path)?.groupValues?.get(1)
    }.toSet()
    private fun sameOrigin(config: ContextConfig, value: String): URI? = try {
        val origin = URI(config.site)
        val uri = origin.resolve(value)
        uri.takeIf { it.scheme == origin.scheme && it.host == origin.host && it.port == origin.port && it.rawUserInfo == null && it.rawFragment == null }
    } catch (_: Exception) { null }
    private fun linkedPages(config: ContextConfig, description: JsonElement): Set<String> {
        val strings = mutableListOf<String>()
        fun collect(value: JsonElement) {
            when (value) {
                is JsonObject -> value.values.forEach(::collect)
                is JsonArray -> value.forEach(::collect)
                is JsonPrimitive -> value.contentOrNull?.let(strings::add)
            }
        }
        collect(description)
        return strings.flatMap { Regex("https?://[^\\s<>\\\"\\)]+").findAll(it).map { match -> match.value }.toList() }.mapNotNull { value ->
            val uri = sameOrigin(config, value) ?: return@mapNotNull null
            Regex("^/wiki/spaces/${Regex.escape(config.space)}/pages/([1-9][0-9]*)(?:/.*)?$").matchEntire(uri.path)?.groupValues?.get(1)
        }.toSet()
    }
}
