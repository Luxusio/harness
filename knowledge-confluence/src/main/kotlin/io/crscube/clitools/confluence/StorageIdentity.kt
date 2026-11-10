package io.crscube.clitools.confluence

import org.jsoup.parser.Parser
import org.jsoup.nodes.Entities

/** Serialization identity, not HTML/DOM equivalence. Never repair markup or normalize human text. */
internal object StorageIdentity {
    private val entity = Regex("&(?:#[0-9]+|#[xX][0-9A-Fa-f]+|[A-Za-z][A-Za-z0-9]+);")
    private val pageVersionAttribute = Regex("(?:^|[ \t\r\n])ri:version-at-save[ \t\r\n]*=[ \t\r\n]*$")
    private val positiveVersion = Regex("[1-9][0-9]*")

    fun equivalent(left: String, right: String): Boolean = left == right ||
        tokens(left)?.let { it == tokens(right) } == true

    /** Saved knowledge storage may add native macro identity; the requested storage remains authoritative. */
    fun nativePageReadback(saved: String, requested: String): Boolean {
        if (saved == requested) return true
        val actualMacros = mutableListOf<MacroTag>()
        val expectedMacros = mutableListOf<MacroTag>()
        val actual = tokens(saved, nativePageVersionHints = true, macros = actualMacros) ?: return false
        val expected = tokens(requested, nativePageVersionHints = true, macros = expectedMacros) ?: return false
        if (actual == expected) return true
        if (actualMacros.isEmpty() || actualMacros.size != expectedMacros.size) return false
        val replacements = actualMacros.zip(expectedMacros).map { (actualTag, expectedTag) ->
            val actualAttributes = macroAttributes(actualTag.text) ?: return false
            val expectedAttributes = macroAttributes(expectedTag.text) ?: return false
            val supplied = expectedAttributes.map { it.name }.toSet()
            val additions = actualAttributes.filter { it.name !in supplied }
            if (additions.any { attribute -> when (attribute.name) {
                    "ac:macro-id" -> !nativeMacroId.matches(attribute.value)
                    "ac:schema-version" -> attribute.value != "1"
                    else -> true
                } }) return false
            val stripped = additions.asReversed().fold(actualTag.text) { text, attribute ->
                text.removeRange(attribute.from, attribute.until)
            }
            val strippedTokens = tokens(stripped) ?: return false
            if (strippedTokens != tokens(expectedTag.text)) return false
            actualTag to stripped
        }
        val normalized = buildString(saved.length) {
            var from = 0
            replacements.forEach { (tag, replacement) ->
                append(saved, from, tag.from); append(replacement); from = tag.until
            }
            append(saved, from, saved.length)
        }
        return tokens(normalized, nativePageVersionHints = true) == expected
    }

    private data class MacroTag(val from: Int, val until: Int, val text: String)
    private data class MacroAttribute(val name: String, val value: String, val from: Int, val until: Int)
    private const val macroStart = "<ac:structured-macro"
    private val attributeName = Regex("[A-Za-z_][A-Za-z0-9_.:-]*")
    private val nativeMacroId = Regex("[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")

    /** Parse positions only. Do not reorder attributes, repair markup, or rewrite existing separators. */
    private fun macroAttributes(tag: String): List<MacroAttribute>? {
        val attributes = mutableListOf<MacroAttribute>()
        val names = mutableSetOf<String>()
        var cursor = macroStart.length
        while (cursor < tag.length) {
            val from = cursor
            while (tag.getOrNull(cursor)?.let { it in " \t\r\n" } == true) cursor++
            if (cursor == tag.lastIndex && tag[cursor] == '>' ||
                cursor == tag.lastIndex - 1 && tag[cursor] == '/' && tag[cursor + 1] == '>')
                return attributes.takeIf { "ac:name" in names }
            if (cursor == from) return null
            val name = attributeName.find(tag, cursor)?.takeIf { it.range.first == cursor } ?: return null
            if (!names.add(name.value)) return null
            cursor = name.range.last + 1
            while (tag.getOrNull(cursor)?.let { it in " \t\r\n" } == true) cursor++
            if (tag.getOrNull(cursor++) != '=') return null
            while (tag.getOrNull(cursor)?.let { it in " \t\r\n" } == true) cursor++
            val quote = tag.getOrNull(cursor++) ?: return null
            if (quote != '\'' && quote != '"') return null
            val end = tag.indexOf(quote, cursor).takeIf { it >= 0 } ?: return null
            val value = tag.substring(cursor, end)
            if ('<' in value) return null
            cursor = end + 1
            attributes += MacroAttribute(name.value, value, from, cursor)
        }
        return null
    }

    private fun entities(value: String): String = entity.replace(value) { match ->
        val raw = match.value
        if (raw.startsWith("&#")) {
            val digits = raw.substring(2, raw.length - 1)
            val number = if (digits.startsWith("x", true)) digits.substring(1).toLongOrNull(16) else digits.toLongOrNull()
            // Invalid XML character references must not become replacement characters or repaired text.
            if (number != null && (number in 0x20L..0xD7FFL || number in 0xE000L..0xFFFDL ||
                    number in 0x10000L..0x10FFFFL))
                String(Character.toChars(number.toInt())) else raw
        } else if (Entities.isNamedEntity(raw.substring(1, raw.length - 1))) {
            val decoded = Parser.unescapeEntities(raw, false)
            // XML normalizes literal line whitespace differently from character references.
            if (decoded.any { it in "\t\r\n" }) raw else decoded
        } else raw
    }

    private fun tokens(value: String, nativePageVersionHints: Boolean = false, macros: MutableList<MacroTag>? = null): List<Pair<String, String>>? {
        val result = mutableListOf<Pair<String, String>>()
        var index = 0
        while (index < value.length) {
            if (value[index] != '<') {
                val end = value.indexOf('<', index).takeIf { it >= 0 } ?: value.length
                result += "text" to entities(value.substring(index, end))
                index = end
                continue
            }
            val special = when {
                value.startsWith("<!--", index) -> "-->"
                value.startsWith("<![CDATA[", index) -> "]]>"
                value.startsWith("<?", index) -> "?>"
                else -> null
            }
            if (special != null) {
                val end = value.indexOf(special, index + 2).takeIf { it >= 0 } ?: return null
                result += "literal" to value.substring(index, end + special.length)
                index = end + special.length
                continue
            }
            // Declarations/DTDs are outside storage serialization equivalence. No entity expansion is performed.
            if (value.startsWith("<!", index)) return null
            val pageLink = nativePageVersionHints && value.startsWith("<ri:page", index) &&
                value.getOrNull(index + 8)?.let { it in " \t\r\n>" } == true
            var part = index
            var cursor = index + 1
            while (cursor < value.length && value[cursor] != '>') {
                val quote = value[cursor]
                if (quote == '\'' || quote == '"') {
                    result += "markup" to value.substring(part, cursor + 1)
                    val end = value.indexOf(quote, cursor + 1).takeIf { it >= 0 } ?: return null
                    val attribute = value.substring(cursor + 1, end)
                    if ('<' in attribute) return null // Literal '<' is not legal XML attribute storage.
                    val normalized = entities(attribute)
                    val pageVersion = pageLink && pageVersionAttribute.containsMatchIn(value.substring(part, cursor)) &&
                        positiveVersion.matches(normalized)
                    result += if (pageVersion) "native-page-version" to "" else "attribute" to normalized
                    part = end
                    cursor = end + 1
                } else cursor++
            }
            if (cursor >= value.length) return null
            result += "markup" to value.substring(part, cursor + 1)
            if (macros != null && value.startsWith(macroStart, index) &&
                value.getOrNull(index + macroStart.length)?.let { it in " \t\r\n/>" } == true)
                macros += MacroTag(index, cursor + 1, value.substring(index, cursor + 1))
            index = cursor + 1
        }
        return result
    }
}
