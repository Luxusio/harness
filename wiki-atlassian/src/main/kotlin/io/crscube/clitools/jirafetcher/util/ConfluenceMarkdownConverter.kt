package io.crscube.clitools.jirafetcher.util

import kotlinx.serialization.json.*
import org.jsoup.Jsoup
import org.jsoup.nodes.CDataNode
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import org.jsoup.parser.Parser
import java.net.URI
import java.net.URLDecoder
import java.net.URLEncoder

enum class ImageType {
    STANDARD_URL,       // <img src="http://...">
    CONFLUENCE_ATTACHMENT,  // <ri:attachment ri:filename="...">
    S3_MACRO            // <ac:structured-macro ac:name="s3-static-macro-image">
}

data class ImageInfo(
    val originalUrl: String,
    val filename: String,
    val type: ImageType = ImageType.STANDARD_URL
)

object ConfluenceMarkdownConverter {
    private val nativePageId = Regex("[1-9][0-9]*")
    private val currentPagePath = Regex("^/wiki/spaces/([^/]+)/pages/[1-9][0-9]*(?:/.*)?$")
    private val nativeLabelSyntax = Regex("([\\[\\]()*_`<>!~#])")

    fun extractImageUrls(html: String): List<ImageInfo> {
        val doc = Jsoup.parseBodyFragment(html)
        val images = mutableListOf<ImageInfo>()

        // Standard img tags
        doc.select("img[src]").forEach { img ->
            val src = img.attr("src")
            if (src.isNotEmpty()) {
                val filename = extractFilenameFromUrl(src)
                images.add(ImageInfo(src, filename, ImageType.STANDARD_URL))
            }
        }

        // Confluence ac:image tags with ri:attachment
        doc.select("ac|image ri|attachment[ri:filename]").forEach { attachment ->
            val filename = attachment.attr("ri:filename")
            if (filename.isNotEmpty()) {
                images.add(ImageInfo(filename, sanitizeFilename(filename), ImageType.CONFLUENCE_ATTACHMENT))
            }
        }

        // Also look for ri:attachment inside ac:image (broader selector)
        doc.select("ri|attachment[ri:filename]").forEach { attachment ->
            val filename = attachment.attr("ri:filename")
            if (filename.isNotEmpty() && images.none { it.originalUrl == filename }) {
                images.add(ImageInfo(filename, sanitizeFilename(filename), ImageType.CONFLUENCE_ATTACHMENT))
            }
        }

        // S3 static macro images (custom Confluence plugin)
        // Format: <ac:structured-macro ac:name="s3-static-macro-image">
        //           <ac:parameter ac:name="path">URL-encoded JSON</ac:parameter>
        //         </ac:structured-macro>
        doc.select("ac|structured-macro[ac:name=s3-static-macro-image]").forEach { macro ->
            val pathParam = macro.select("ac|parameter[ac:name=path]").first()
            val encodedPath = pathParam?.text() ?: ""
            if (encodedPath.isNotEmpty()) {
                val imageInfo = parseS3MacroPath(encodedPath)
                if (imageInfo != null && images.none { it.originalUrl == imageInfo.originalUrl }) {
                    images.add(imageInfo)
                }
            }
        }

        return images
    }

    private fun parseS3MacroPath(encodedPath: String): ImageInfo? {
        return try {
            // URL decode: %5B%7B%22Key%22%3A%22TMF%2F... -> [{"Key":"TMF/...
            val decoded = URLDecoder.decode(encodedPath, "UTF-8")
            // Parse simple JSON: [{"Key":"TMF/4652236993/filename.png"}]
            val keyRegex = Regex(""""Key"\s*:\s*"([^"]+)"""")
            val match = keyRegex.find(decoded)
            if (match != null) {
                val s3Key = match.groupValues[1]
                val filename = s3Key.substringAfterLast("/")
                ImageInfo(s3Key, sanitizeFilename(filename), ImageType.S3_MACRO)
            } else {
                null
            }
        } catch (e: Exception) {
            null
        }
    }

    fun htmlToMarkdown(html: String, imagePathMapping: Map<String, String> = emptyMap(), currentPageUrl: String? = null): String {
        // XML preserves native link attributes and CDATA labels that the HTML parser can discard.
        // Replace only complete native links in this read-only projection, leaving other conversion unchanged.
        val native = Jsoup.parse(html, "", Parser.xmlParser().setTrackPosition(true))
        val links = native.getElementsByTag("ac:link").filter { link ->
            link.parents().none { it.tagName() == "ac:link" } &&
                (link.getElementsByTag("ri:page").isNotEmpty() || link.getElementsByTag("ri:user").isNotEmpty() ||
                    link.hasAttr("ac:anchor") || link.getElementsByTag("ac:plain-text-link-body").isNotEmpty()) &&
                link.sourceRange().isTracked && !link.sourceRange().isImplicit &&
                link.endSourceRange().isTracked && !link.endSourceRange().isImplicit
        }.sortedBy { it.sourceRange().start().pos() }
        val projection = buildString(html.length) {
            var previous = 0
            links.forEach { link ->
                val start = link.sourceRange().start().pos()
                val end = link.endSourceRange().end().pos()
                require(start >= previous && end in start..html.length)
                append(html, previous, start)
                append(Element("span").text(nativeLink(link, currentPageUrl, imagePathMapping)).outerHtml())
                previous = end
            }
            append(html, previous, html.length)
        }
        val doc = Jsoup.parseBodyFragment(projection)
        return convertContent(doc.body(), imagePathMapping)
    }

    private fun simpleBlockText(element: Element): String? {
        if (element.normalName() !in setOf("p", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "tr", "li", "ul", "ol", "blockquote", "pre", "div", "table")) return null
        val nodes = element.childNodes()
        if (nodes.isEmpty() || nodes.any {
                !(it is TextNode && it !is CDataNode || it is Element && it.normalName() == "br" && it.childNodeSize() == 0)
            }) return null
        return nodes.joinToString("") { if (it is TextNode) it.wholeText else "\n" }
    }

    private fun contentNodes(element: Element): List<Node> {
        val nodes = element.childNodes()
        return if (element.normalName() in setOf("body", "div", "section", "article", "ac:rich-text-body", "li", "td", "th", "tr") &&
            nodes.any { it is Element } && simpleBlockText(element) == null)
            nodes.dropWhile { it is TextNode && it.isBlank }.dropLastWhile { it is TextNode && it.isBlank }
        else nodes
    }

    private fun convertContent(element: Element, imagePathMapping: Map<String, String>): String {
        simpleBlockText(element)?.let { return it }
        val nodes = contentNodes(element)
        val markdown = trimStructuralBoundary(convertElement(element, imagePathMapping), nodes.firstOrNull(), true)
        return trimStructuralBoundary(markdown, nodes.lastOrNull(), false)
    }

    private fun trimStructuralBoundary(markdown: String, edge: Node?, leading: Boolean): String {
        if (edge is Element) {
            when (edge.normalName()) {
                "p", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "pre", "blockquote", "ul", "ol" ->
                    return if (leading) markdown.removePrefix("\n") else markdown.removeSuffix("\n")
                "div", "section", "article", "ac:rich-text-body" -> {
                    if (simpleBlockText(edge) != null) return markdown
                    val nodes = contentNodes(edge)
                    return trimStructuralBoundary(markdown, if (leading) nodes.firstOrNull() else nodes.lastOrNull(), leading)
                }
                "li", "td", "th", "tr" -> if (simpleBlockText(edge) != null) return markdown
                "br" -> return markdown
            }
        }
        return if (leading) markdown.trimStart() else markdown.trimEnd()
    }

    private fun nativeLink(link: Element, currentPageUrl: String?, imagePathMapping: Map<String, String>): String {
        fun escaped(value: String): String = value.replace("\\", "\\\\").replace(nativeLabelSyntax, "\\\\$1")
        fun encoded(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")
        val page = link.getElementsByTag("ri:page").firstOrNull()
        val user = link.children().firstOrNull { it.tagName() == "ri:user" }
        val id = page?.attr("ri:content-id").orEmpty()
        val title = page?.attr("ri:content-title").orEmpty()
        val space = page?.attr("ri:space-key").orEmpty()
        val anchor = link.attr("ac:anchor")
        val label = sequenceOf("ac:link-body", "ac:plain-text-link-body").mapNotNull { tag ->
            link.getElementsByTag(tag).firstOrNull()?.text()?.takeIf { it.isNotEmpty() }
        }.firstOrNull() ?: title.ifEmpty { anchor.ifEmpty { if (page == null) link.text() else "Confluence page $id" } }
        val imageBody = link.getElementsByTag("ac:link-body").firstOrNull()?.takeIf {
            it.getElementsByTag("ac:image").isNotEmpty() || it.getElementsByTag("img").isNotEmpty() ||
                it.getElementsByTag("ac:structured-macro").any { macro -> macro.attr("ac:name") == "s3-static-macro-image" }
        }
        val renderedLabel = imageBody?.let { convertElement(it, imagePathMapping).trim() }?.takeIf { it.isNotEmpty() } ?: escaped(label)
        val context = currentPageUrl?.let { runCatching { URI(it) }.getOrNull() }?.takeIf {
            it.scheme in setOf("https", "http") && it.host != null && it.rawUserInfo == null
        }
        val currentSpace = context?.rawPath?.let { currentPagePath.matchEntire(it)?.groupValues?.get(1) }
        val fragment = if (anchor.isEmpty()) "" else "#${encoded(anchor)}"
        val origin = context?.let { URI(it.scheme, null, it.host, it.port, null, null, null).toASCIIString() }.orEmpty()
        if (page == null && user != null) {
            fun bounded(name: String): String? = user.attr(name).takeIf {
                it.isNotBlank() && it.length <= 500 && it.none(Char::isISOControl)
            }
            bounded("ri:account-id")?.let { account ->
                return "[@${escaped(label.ifEmpty { account })}]($origin/wiki/people/${encoded(account)})"
            }
            val legacy = listOf("ri:userkey", "ri:username").mapNotNull { name -> bounded(name)?.let { "${name.removePrefix("ri:")}=$it" } }
            val display = if (label.isEmpty()) "" else "@${escaped(label)} "
            return "$display(Confluence user: ${if (legacy.isEmpty()) "unresolved" else escaped(legacy.joinToString("; ")) + "; unresolved"})"
        }
        if (page != null && nativePageId.matches(id)) {
            val selectedSpace = if (space.isNotEmpty()) encoded(space) else currentSpace
            val path = if (selectedSpace == null) "/wiki/pages/viewpage.action?pageId=$id" else "/wiki/spaces/$selectedSpace/pages/$id"
            return "[$renderedLabel]($origin$path$fragment)"
        }
        if (page == null) {
            return if (anchor.isEmpty()) renderedLabel else "[$renderedLabel](${context?.let { URI(it.scheme, null, it.host, it.port, it.path, it.query, null).toASCIIString() }.orEmpty()}$fragment)"
        }
        val details = listOfNotNull(title.takeIf { it.isNotEmpty() }?.let { "title=$it" },
            id.takeIf { it.isNotEmpty() }?.let { "id=$it" }, space.takeIf { it.isNotEmpty() }?.let { "space=$it" },
            anchor.takeIf { it.isNotEmpty() }?.let { "anchor=$it" }).joinToString("; ")
        return "$renderedLabel (Confluence page: ${escaped(details)}; unresolved)"
    }

    private fun convertElement(element: Element, imagePathMapping: Map<String, String>): String {
        simpleBlockText(element)?.let { return it }
        val sb = StringBuilder()

        for (node in contentNodes(element)) {
            when (node) {
                is TextNode -> sb.append(node.text())
                is Element -> sb.append(convertTag(node, imagePathMapping))
            }
        }

        return sb.toString()
    }

    private fun convertTag(element: Element, imagePathMapping: Map<String, String>): String {
        val tagName = element.tagName().lowercase()
        val content = convertElement(element, imagePathMapping)

        return when (tagName) {
            "h1" -> "\n# $content\n"
            "h2" -> "\n## $content\n"
            "h3" -> "\n### $content\n"
            "h4" -> "\n#### $content\n"
            "h5" -> "\n##### $content\n"
            "h6" -> "\n###### $content\n"
            "p" -> "\n$content\n"
            "br" -> "\n"
            "hr" -> "\n---\n"
            "strong", "b" -> "**$content**"
            "em", "i" -> "*$content*"
            "code" -> "`$content`"
            "pre" -> "\n```\n$content\n```\n"
            "blockquote" -> "\n> ${content.replace("\n", "\n> ")}\n"
            "a" -> {
                val href = element.attr("href")
                if (href.isNotEmpty()) "[$content]($href)" else content
            }
            "img" -> {
                val src = element.attr("src")
                val alt = element.attr("alt").ifEmpty { "image" }
                if (src.isNotEmpty()) {
                    val localPath = imagePathMapping[src] ?: src
                    "![$alt]($localPath)"
                } else ""
            }
            "ac:image" -> {
                // Handle Confluence image macro
                val attachment = element.selectFirst("ri|attachment[ri:filename]")
                val filename = attachment?.attr("ri:filename") ?: ""
                if (filename.isNotEmpty()) {
                    val localPath = imagePathMapping[filename] ?: filename
                    "![${filename}]($localPath)"
                } else content
            }
            "ul" -> "\n${convertList(element, ordered = false, imagePathMapping)}\n"
            "ol" -> "\n${convertList(element, ordered = true, imagePathMapping)}\n"
            "li" -> content
            "table" -> "\n${convertTable(element, imagePathMapping)}\n"
            "div", "span", "section", "article" -> content
            "ac:structured-macro" -> handleConfluenceMacro(element, imagePathMapping)
            "ac:rich-text-body" -> content
            "ac:parameter" -> "" // Skip parameter content - it's handled by parent macro
            "ac:link" -> {
                // Handle Confluence page links
                val linkBody = element.selectFirst("ac|link-body")?.text() ?: ""
                if (linkBody.isNotEmpty()) linkBody else content
            }
            else -> content
        }
    }

    private fun handleConfluenceMacro(element: Element, imagePathMapping: Map<String, String>): String {
        val macroName = element.attr("ac:name")
        return when (macroName) {
            "code", "noformat" -> {
                val body = element.selectFirst("ac|plain-text-body")?.text() ?: ""
                "\n```\n$body\n```\n"
            }
            "s3-static-macro-image" -> {
                // Handle S3 static macro images
                val pathParam = element.selectFirst("ac|parameter[ac:name=path]")?.text() ?: ""
                if (pathParam.isNotEmpty()) {
                    val imageInfo = parseS3MacroPath(pathParam)
                    if (imageInfo != null) {
                        val localPath = imagePathMapping[imageInfo.originalUrl] ?: imageInfo.originalUrl
                        "![${imageInfo.filename}]($localPath)"
                    } else {
                        "" // Can't parse, skip
                    }
                } else {
                    ""
                }
            }
            "panel", "info", "note", "warning", "tip" -> {
                // Handle Confluence info panels - just extract content
                val body = element.selectFirst("ac|rich-text-body")
                if (body != null) {
                    convertElement(body, imagePathMapping)
                } else {
                    convertElement(element, imagePathMapping)
                }
            }
            "jira" -> {
                // Handle Jira issue links
                val issueKey = element.selectFirst("ac|parameter[ac:name=key]")?.text() ?: ""
                if (issueKey.isNotEmpty()) {
                    "[$issueKey]"
                } else {
                    ""
                }
            }
            else -> convertElement(element, imagePathMapping)
        }
    }

    private fun convertList(element: Element, ordered: Boolean, imagePathMapping: Map<String, String>): String {
        val items = element.select("> li")
        return items.mapIndexed { index, li ->
            val prefix = if (ordered) "${index + 1}. " else "- "
            "$prefix${convertContent(li, imagePathMapping)}"
        }.joinToString("\n")
    }

    private fun convertTable(element: Element, imagePathMapping: Map<String, String>): String {
        val rows = element.select("tr")
        if (rows.isEmpty()) return ""

        val result = StringBuilder()
        rows.forEachIndexed { index, row ->
            val cells = row.select("th, td")
            val cellContents = cells.map {
                convertContent(it, imagePathMapping)
                    .replace("|", "\\|")
                    .replace("\r", "")
                    .replace("\n", "<br>")
            }
            result.appendLine("| ${cellContents.joinToString(" | ")} |")

            // Add header separator after first row
            if (index == 0) {
                result.appendLine("| ${cellContents.map { "---" }.joinToString(" | ")} |")
            }
        }
        return result.toString()
    }

    fun buildChildrenSection(parentTitle: String, parentId: String, childPages: List<Pair<String, String>>): String {
        return buildString {
            appendLine("## Children")
            appendLine()
            val parentFilename = "$parentId-${sanitizeFilename(parentTitle)}.md"
            appendLine("- [$parentTitle]($parentFilename)")
            for ((childId, childTitle) in childPages) {
                val childFilename = "$childId-${sanitizeFilename(childTitle)}.md"
                appendLine("  - [$childTitle]($childFilename)")
            }
        }
    }

    /**
     * Build a full hierarchy section showing all descendants with nested indentation.
     * @param parentTitle title of the root page
     * @param parentId id of the root page
     * @param descendants list of (page, depth) pairs where depth starts at 1 for direct children
     */
    fun buildHierarchySection(parentTitle: String, parentId: String, descendants: List<Triple<String, String, Int>>): String {
        return buildString {
            appendLine("## Hierarchy")
            appendLine()
            val parentFilename = "$parentId-${sanitizeFilename(parentTitle)}.md"
            appendLine("- [$parentTitle]($parentFilename)")
            for ((pageId, title, depth) in descendants) {
                val indent = "  ".repeat(depth)
                val filename = "$pageId-${sanitizeFilename(title)}.md"
                appendLine("$indent- [$title]($filename)")
            }
        }
    }

    fun sanitizeFilename(filename: String): String {
        return filename
            .replace(Regex("[^a-zA-Z0-9\\-_\\.\\s]"), "")
            .replace(Regex("\\s+"), "-")
            .replace(Regex("-+"), "-")
            .trim('-')
            .lowercase()
            .take(100)
    }

    private fun extractFilenameFromUrl(url: String): String {
        val filename = url.substringAfterLast("/").substringBefore("?")
        return if (filename.isNotEmpty()) sanitizeFilename(filename) else "image-${url.hashCode()}.png"
    }
}
