package io.crscube.clitools.knowledge

import kotlinx.serialization.json.*
import org.yaml.snakeyaml.DumperOptions
import org.yaml.snakeyaml.LoaderOptions
import org.yaml.snakeyaml.Yaml
import org.yaml.snakeyaml.constructor.SafeConstructor
import org.yaml.snakeyaml.events.*
import org.yaml.snakeyaml.representer.Representer
import java.io.StringReader
import java.nio.ByteBuffer
import java.nio.channels.FileChannel
import java.nio.charset.CodingErrorAction
import java.nio.file.*

/** Sole project selection authority. Never searches HOME, siblings or the plugin installation. */
internal object ProjectConnection {
    private const val LIMIT = 65536

    fun load(startupDirectory: Path): JsonObject? {
        val startup = startupDirectory.toRealPath()
        require(Files.isDirectory(startup))
        var cursor: Path? = startup
        var root = startup
        while (cursor != null) {
            val git = cursor.resolve(".git")
            if (Files.exists(git, LinkOption.NOFOLLOW_LINKS)) {
                require(!Files.isSymbolicLink(git) && (Files.isDirectory(git, LinkOption.NOFOLLOW_LINKS) ||
                    Files.isRegularFile(git, LinkOption.NOFOLLOW_LINKS)))
                root = cursor
                break
            }
            cursor = cursor.parent
        }
        var directory = root
        for (name in listOf("doc", "harness")) {
            directory = directory.resolve(name)
            if (!Files.exists(directory, LinkOption.NOFOLLOW_LINKS)) return null
            require(!Files.isSymbolicLink(directory) && Files.isDirectory(directory, LinkOption.NOFOLLOW_LINKS))
        }
        val path = directory.resolve("manifest.yaml")
        if (!Files.exists(path, LinkOption.NOFOLLOW_LINKS)) return null
        require(!Files.isSymbolicLink(path) && Files.isRegularFile(path, LinkOption.NOFOLLOW_LINKS) && Files.isReadable(path))
        val bytes = FileChannel.open(path, StandardOpenOption.READ, LinkOption.NOFOLLOW_LINKS).use { channel ->
            require(channel.size() in 1L..LIMIT.toLong())
            val buffer = ByteBuffer.allocate(LIMIT + 1)
            while (buffer.hasRemaining() && channel.read(buffer) != -1) { /* bounded even if the file grows */ }
            require(buffer.position() in 1..LIMIT)
            buffer.flip()
            Charsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .onUnmappableCharacter(CodingErrorAction.REPORT).decode(buffer).toString()
        }
        // Absence preflight only: conventional Harness manifests without wiki remain inert.
        // Ambiguous root syntax always goes through the bounded safe parser below.
        if (definitelyNoWiki(bytes)) return null
        val options = LoaderOptions().apply {
            isAllowDuplicateKeys = false
            maxAliasesForCollections = 0
            codePointLimit = LIMIT
            nestingDepthLimit = 20
        }
        val dumper = DumperOptions()
        val yaml = Yaml(SafeConstructor(options), Representer(dumper), dumper, options)
        var depth = 0
        var documents = 0
        for (event in yaml.parse(StringReader(bytes))) {
            require(event !is AliasEvent)
            if (event is NodeEvent) require(event.anchor == null)
            if (event is ScalarEvent) require(event.tag == null)
            if (event is CollectionStartEvent) { require(event.tag == null); depth++; require(depth <= 20) }
            if (event is CollectionEndEvent) depth--
            if (event is DocumentStartEvent) { documents++; require(documents == 1) }
        }
        val parsed = yaml.load<Any>(bytes) as? Map<*, *> ?: throw IllegalArgumentException()
        if (!parsed.containsKey("wiki")) return null
        val wiki = parsed["wiki"] as? Map<*, *> ?: throw IllegalArgumentException()
        val backend = wiki["backend"]
        require((!wiki.containsKey("backend") && wiki["enabled"] == false) || backend == "atlassian")
        val value = JsonObject(wiki.entries.filter { it.key != "backend" }.associate { (key, child) ->
            require(key is String)
            key to when (child) {
                is String -> JsonPrimitive(child)
                is Boolean -> JsonPrimitive(child)
                is Byte, is Short, is Int, is Long, is java.math.BigInteger ->
                    JsonPrimitive(child.toString().toLongOrNull() ?: throw IllegalArgumentException())
                else -> throw IllegalArgumentException()
            }
        })
        KnowledgeSettings.validate(value, requireEnabled = false)
        return value
    }

    /** Recognize only plain root keys; this deliberately does not validate YAML. */
    private fun definitelyNoWiki(text: String): Boolean {
        val plainKey = Regex("([A-Za-z_][A-Za-z0-9_-]*)[ \t]*:(?:[ \t].*|$)")
        var first = true
        var foundKey = false
        for (line in text.lineSequence()) {
            if (line.isBlank() || line.trimStart().startsWith("#")) continue
            if (line.startsWith(" ") || line.startsWith("\t")) continue
            if (first && line == "---") { first = false; continue }
            first = false
            val match = plainKey.matchEntire(line) ?: return false
            if (match.groupValues[1] == "wiki") return false
            foundKey = true
        }
        return foundKey
    }
}
