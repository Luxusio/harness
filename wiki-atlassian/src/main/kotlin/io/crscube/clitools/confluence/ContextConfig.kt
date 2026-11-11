package io.crscube.clitools.confluence

import org.yaml.snakeyaml.LoaderOptions
import org.yaml.snakeyaml.DumperOptions
import io.crscube.clitools.knowledge.KnowledgeSettings
import kotlinx.serialization.json.*
import org.yaml.snakeyaml.Yaml
import org.yaml.snakeyaml.constructor.SafeConstructor
import org.yaml.snakeyaml.representer.Representer
import java.net.URI
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.Paths
import java.nio.file.StandardCopyOption
import java.security.MessageDigest

data class ContextConfig(val site: String, val space: String, val spaceId: String, val homePageId: String, val role: String,
                         val pmProject: String = "PMISSUE", val structurePolicy: String = "strict", val pilotRootPageId: String? = null,
                         val knowledgeRootPageId: String? = null, val legacyPmAssociation: Boolean = structurePolicy != "knowledge", val jiraSite: String? = null) {
    init {
        require(jiraSite == null || validOrigin(jiraSite)) { "Jira site must be an HTTPS origin" }
        require(validOrigin(site)) { "Confluence site must be an HTTPS origin" }
        require(space.matches(Regex("[A-Za-z0-9_-]{1,100}"))) { "Invalid space key" }
        require(spaceId.matches(Regex("[1-9][0-9]*")) && homePageId.matches(Regex("[1-9][0-9]*"))) { "Invalid space/home ID" }
        require(role.isEmpty() || role in setOf("개발", "QA", "PM", "관리자")) { "Configure a valid user role" }
        require((isKnowledge && pmProject.isEmpty()) || pmProject.matches(Regex("[A-Z][A-Z0-9_]*"))) { "Invalid PM project key" }
        require(structurePolicy in setOf("strict", "pilot", "knowledge")) { "Invalid structurePolicy" }
        require(if (structurePolicy == "pilot") pilotRootPageId?.matches(Regex("[1-9][0-9]*")) == true && pilotRootPageId != homePageId
            else pilotRootPageId == null) { "pilot requires a distinct positive pilotRootPageId; other policies have no pilot root" }
        require(knowledgeRootPageId == null || (isKnowledge && knowledgeRootPageId.matches(Regex("[1-9][0-9]*")))) { "Knowledge root must be an existing positive content ID" }
    }

    val isKnowledge: Boolean get() = structurePolicy == "knowledge"

    fun forIssue(issue: String): ContextConfig {
        require(issue.matches(Regex("[A-Z][A-Z0-9_]*-[1-9][0-9]*"))) { "Valid work issue required" }
        return if (isKnowledge) copy(pmProject = issue.substringBeforeLast('-')) else this
    }

    companion object {
        fun validOrigin(value: String): Boolean = try {
            val uri = URI(value)
            uri.scheme == "https" && !uri.host.isNullOrBlank() && uri.rawUserInfo == null &&
                uri.rawQuery == null && uri.rawFragment == null && uri.rawPath in listOf("", "/") &&
                uri.port in -1..65535
        } catch (_: Exception) { false }

        fun connections(root: Path, runtimeSettings: KnowledgeSettings? = null): JsonObject =
            (runtimeSettings ?: KnowledgeSettings.fromProject(root)
                ?: error("Atlassian project connection is unavailable")).connections()

        fun load(root: Path, runtimeSettings: KnowledgeSettings? = null, scope: String = "effective"): ContextConfig? {
            require(scope in setOf("effective", "project", "common")) { "Unknown connection scope" }
            val selected = connections(root, runtimeSettings).getValue(scope).jsonObject
            val value = selected["confluence"]?.jsonObject ?: return null
            return ContextConfig(value.text("site"), value.text("space"), value.text("spaceId"),
                value.text("homePageId"), "", "", "knowledge", knowledgeRootPageId = value.text("knowledgeRootPageId"), legacyPmAssociation = false)
        }

        private fun JsonObject.text(name: String): String = optional(name) ?: error("Missing connection $name")
        private fun JsonObject.optional(name: String): String? = get(name)?.let {
            require(it is JsonPrimitive && it.isString && it.content.isNotBlank()) { "Invalid connection $name" }; it.content
        }
        internal fun json(value: Any?): JsonElement = when (value) {
            null -> JsonNull
            is Map<*, *> -> JsonObject(value.entries.associate { (key, child) -> (key as String) to json(child) })
            is List<*> -> JsonArray(value.map(::json))
            is String -> JsonPrimitive(value)
            is Number -> JsonPrimitive(value)
            is Boolean -> JsonPrimitive(value)
            else -> error("Unsupported configuration value")
        }

        private fun yaml(path: Path): Map<*, *> {
            require(Files.isRegularFile(path) && Files.size(path) in 1..65536) { "Missing or oversized Talos configuration" }
            return yaml(Files.readAllBytes(path))
        }

        private fun yaml(bytes: ByteArray): Map<*, *> {
            require(bytes.size in 1..65536) { "Missing or oversized Talos configuration" }
            val options = LoaderOptions().apply {
                isAllowDuplicateKeys = false
                maxAliasesForCollections = 0
                codePointLimit = 65536
                nestingDepthLimit = 20
            }
            val parsed = try {
                val dumper = DumperOptions()
                Yaml(SafeConstructor(options), Representer(dumper), dumper, options)
                    .load<Any>(String(bytes, Charsets.UTF_8))
            }
            catch (_: Exception) { error("Invalid Talos YAML configuration") }
            val result = parsed as? Map<*, *> ?: error("Talos configuration must be a mapping")
            fun rejectSecrets(value: Any?) {
                when (value) {
                    is Map<*, *> -> value.forEach { (key, child) ->
                        require(key is String && !Regex("(?i)(password|passwd|secret|token|api.?key|private.?key)").containsMatchIn(key)) {
                            "Credentials belong in environment variables"
                        }
                        rejectSecrets(child)
                    }
                    is List<*> -> value.forEach(::rejectSecrets)
                }
            }
            rejectSecrets(result)
            return result
        }
    }
}

object ContextFiles {
    fun safe(root: Path, relative: String): Path {
        val name = Paths.get(relative)
        require(!name.isAbsolute && name.none { it.toString() == ".." }) { "Contained relative path required" }
        val base = root.toRealPath()
        val result = base.resolve(name).normalize()
        require(result.startsWith(base)) { "Path escapes workspace" }
        var cursor = base
        base.relativize(result).forEach { part ->
            cursor = cursor.resolve(part)
            require(!Files.isSymbolicLink(cursor)) { "Symlink paths are not permitted" }
        }
        return result
    }

    fun atomic(path: Path, text: String) {
        var cursor: Path? = path.toAbsolutePath()
        while (cursor != null) {
            require(!Files.isSymbolicLink(cursor)) { "Symlink paths are not permitted" }
            cursor = cursor.parent
        }
        Files.createDirectories(path.parent)
        val temp = Files.createTempFile(path.parent, ".context-", ".tmp")
        try {
            Files.write(temp, text.toByteArray(Charsets.UTF_8))
            Files.move(temp, path, StandardCopyOption.ATOMIC_MOVE, StandardCopyOption.REPLACE_EXISTING)
        } finally { Files.deleteIfExists(temp) }
    }

    fun hash(text: String): String = hash(text.toByteArray(Charsets.UTF_8))

    fun hash(bytes: ByteArray): String = MessageDigest.getInstance("SHA-256").digest(bytes)
        .joinToString("") { "%02x".format(it) }
}
