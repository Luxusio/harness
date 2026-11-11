package io.crscube.clitools.knowledge

import java.text.Normalizer
import java.text.Normalizer.Form.NFC
import java.util.regex.Pattern

internal class LocalSearchQuery(query: String) {
    private val words = Normalizer.normalize(query, NFC).split(Regex("(?U)\\s+")).filter(String::isNotEmpty)
        .also { require(it.isNotEmpty()) { "Query must contain searchable text" } }
    private val phrase = pattern(words.joinToString(" "))
    private val terms = words.distinct().map(::pattern)

    data class Match(val rank: Int, val field: String, val bodyOffset: Int = -1)

    fun match(title: String, content: String): Match? {
        val normalizedTitle = Normalizer.normalize(title, NFC)
        val normalizedBody = Normalizer.normalize(content, NFC)
        val phraseInTitle = phrase.matcher(normalizedTitle)
        val phraseInBody = phrase.startIn(normalizedBody)
        val titleTerms = terms.map { it.startIn(normalizedTitle) >= 0 }
        val titleRank = when {
            phraseInTitle.matches() -> 6
            phraseInTitle.find() -> 5
            titleTerms.all { it } -> 4
            else -> 0
        }
        if (titleRank > 0) {
            val start = if (phraseInBody >= 0) phraseInBody else terms.firstNotNullOfOrNull {
                it.startIn(normalizedBody).takeIf { at -> at >= 0 }
            } ?: -1
            return Match(titleRank, "title", originalOffset(content, normalizedBody, start))
        }
        if (phraseInBody >= 0) return Match(3, "body", originalOffset(content, normalizedBody, phraseInBody))
        var allInBody = true
        var firstBody = Int.MAX_VALUE
        var firstBodyOnly = Int.MAX_VALUE
        for (index in terms.indices) {
            val start = terms[index].startIn(normalizedBody)
            if (start < 0) {
                if (!titleTerms[index]) return null
                allInBody = false
            } else {
                firstBody = minOf(firstBody, start)
                if (!titleTerms[index]) firstBodyOnly = minOf(firstBodyOnly, start)
            }
        }
        val start = if (allInBody) firstBody else firstBodyOnly
        return Match(if (allInBody) 2 else 1, if (allInBody) "body" else "title_and_body", originalOffset(content, normalizedBody, start))
    }

    private fun pattern(value: String): Pattern {
        val literal = Pattern.quote(value)
        val source = if (value.matches(Regex("[0-9]+"))) "(?<![0-9])$literal(?![0-9])" else literal
        return Pattern.compile(source, Pattern.CASE_INSENSITIVE or Pattern.UNICODE_CASE)
    }

    private fun Pattern.startIn(value: String): Int = matcher(value).let { if (it.find()) it.start() else -1 }

    private fun originalOffset(content: String, normalized: String, offset: Int): Int {
        if (offset <= 0 || content == normalized) return offset
        val clusters = grapheme.matcher(content)
        var position = 0
        while (clusters.find()) {
            position += Normalizer.normalize(clusters.group(), NFC).length
            if (position > offset) return clusters.start()
        }
        error("Normalized match is outside the authored projection")
    }

    companion object {
        private val grapheme = Pattern.compile("\\X")
    }
}

// The replica writes this exact prefix. Keep offsets relative to the complete Markdown read.
private val replicaHeader = Regex("\\A---\\npage_id: \"[1-9][0-9]*\"\\nversion: [0-9]+\\nurl: [^\\n]+\\n---\\n\\n")

internal fun authoredStart(markdown: String): Int = replicaHeader.find(markdown)?.range?.last?.plus(1) ?: 0

internal fun headingPath(content: String, start: Int, at: Int): List<String> {
    val headings = mutableListOf<Pair<Int, String>>()
    var fenced = false
    var line = start
    while (line <= at && line < content.length) {
        val end = content.indexOf('\n', line).takeIf { it >= 0 } ?: content.length
        if (content.startsWith("```", line)) fenced = !fenced
        if (!fenced && content[line] == '#') {
            var marker = line
            while (marker < minOf(end, line + 6) && content[marker] == '#') marker++
            if (marker < end && content[marker] == ' ') {
                val level = marker - line
                val titleStart = marker + 1
                val last = content.offsetByCodePoints(titleStart, minOf(120, content.codePointCount(titleStart, end)))
                val title = content.substring(titleStart, last).trim()
                if (title.isNotEmpty()) {
                    while (headings.lastOrNull()?.first?.let { it >= level } == true) headings.removeLast()
                    headings.add(level to title)
                }
            }
        }
        line = end + 1
    }
    return headings.map { it.second }
}
