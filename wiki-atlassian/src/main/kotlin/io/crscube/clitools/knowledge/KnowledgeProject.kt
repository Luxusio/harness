package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.ConfluenceRemote
import io.crscube.clitools.confluence.ContextRemoteException
import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import java.nio.file.Path
import java.nio.file.Paths

/** Immutable public selections. Native identities are resolved by the owning space worker. */
class KnowledgeProject(value: JsonObject, val credentials: JiraConfig,
                       private val dataHome: Path = KnowledgeSettings.defaultDataHome()) {
    private val initial = Json.parseToJsonElement(value.toString()).jsonObject
    init {
        validate(initial)
        require(initial["enabled"] == JsonPrimitive(true))
        require(credentials.email.isNotBlank() && credentials.apiToken.isNotBlank())
        require(KnowledgeSettings.validOrigin(credentials.baseUrl) &&
            initial.getValue("site").jsonPrimitive.content.trimEnd('/') == credentials.baseUrl.trimEnd('/'))
    }
    val selections: List<JsonObject> = initial.getValue("spaces").jsonArray.map { it.jsonObject }
    val pollIntervalSeconds: Long get() = initial["pollIntervalSeconds"]?.jsonPrimitive?.long ?: 300L
    fun read(): JsonObject = initial
    internal fun corpusRoot(key: String): Path {
        val selection = selections.single { it["key"] == JsonPrimitive(key) }
        return KnowledgeSettings.corpusPath(dataHome, credentials, key, selection["rootPageId"]?.jsonPrimitive?.content)
    }
    suspend fun resolve(remote: ConfluenceRemote, key: String): KnowledgeSettings {
        val selection = selections.singleOrNull { it.getValue("key") == JsonPrimitive(key) }
            ?: throw ContextRemoteException("space_not_configured", "Select a configured space key")
        val native = remote.spaces().map { it.jsonObject }.singleOrNull { it["key"] == JsonPrimitive(key) }
            ?: throw ContextRemoteException("space_unavailable", "The selected space is not uniquely accessible")
        return KnowledgeSettings(buildJsonObject {
            put("enabled", true); put("site", initial.getValue("site")); put("space", key)
            put("spaceId", native.getValue("id")); put("pollIntervalSeconds", pollIntervalSeconds)
            selection.filterKeys { it != "key" }.forEach { (name, field) -> put(name, field) }
        }, credentials, dataHome)
    }
    companion object {
        private val fields = setOf("enabled", "site", "spaces", "pollIntervalSeconds")
        private val spaceFields = setOf("key", "rootPageId", "memoryPageId", "feedbackPageId", "watchCommentPages")
        fun validate(value: JsonObject) {
            require(value.keys.all { it in fields })
            require(value["enabled"] == JsonPrimitive(true) || value["enabled"] == JsonPrimitive(false))
            val enabled = value["enabled"] == JsonPrimitive(true)
            if (enabled || "site" in value) {
                val site = value["site"] as? JsonPrimitive ?: throw IllegalArgumentException()
                require(site.isString && KnowledgeSettings.validOrigin(site.content))
            }
            val poll = value["pollIntervalSeconds"]
            if (poll != null) require(poll is JsonPrimitive && !poll.isString && poll.longOrNull in 30L..86400L)
            if (enabled || "spaces" in value) {
                val spaces = value["spaces"] as? JsonArray ?: throw IllegalArgumentException()
                require(spaces.size in 1..32)
                val keys = mutableSetOf<String>()
                spaces.forEach { item ->
                    val selected = item as? JsonObject ?: throw IllegalArgumentException()
                    require(selected.keys.all { it in spaceFields })
                    val key = selected["key"] as? JsonPrimitive ?: throw IllegalArgumentException()
                    require(key.isString && key.content.matches(Regex("[A-Za-z0-9_-]{1,100}")) && keys.add(key.content))
                    // Reuse the same native scalar checks without requiring resolved IDs.
                    KnowledgeSettings.validate(buildJsonObject {
                        put("enabled", false); put("space", key)
                        selected.filterKeys { it != "key" }.forEach { (name, field) -> put(name, field) }
                    }, requireEnabled = false)
                }
            }
        }
        fun fromProject(startupDirectory: Path = Paths.get("").toAbsolutePath(),
                        environment: Map<String, String> = System.getenv()): KnowledgeProject? {
            try {
                val value = ProjectConnection.load(startupDirectory) ?: return null
                if (value["enabled"] == JsonPrimitive(false)) return null
                val snapshot = environment.toMap()
                fun credential(key: String): String = snapshot[key]?.takeIf { it.isNotBlank() }
                    ?: throw IllegalArgumentException()
                val pin = credential("ATLASSIAN_BASE_URL")
                require(KnowledgeSettings.validOrigin(pin) && pin.trimEnd('/') == value.getValue("site").jsonPrimitive.content.trimEnd('/'))
                return KnowledgeProject(value, JiraConfig(pin.trimEnd('/'), credential("ATLASSIAN_EMAIL"), credential("ATLASSIAN_API_TOKEN")),
                    KnowledgeSettings.defaultDataHome(snapshot))
            } catch (_: Exception) { throw IllegalArgumentException("Atlassian project connection or authentication is unavailable") }
        }
    }
}
