package io.crscube.clitools.knowledge

import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.*

class KnowledgeSettingsTest {
    @TempDir lateinit var home: Path
    private val credentials = JiraConfig("https://example.atlassian.net", "fixture@example.test", "fixture-credential")
    private fun config(root: String = "210", extra: Map<String, JsonElement> = emptyMap()): Path {
        val path = home.resolve("config-$root-${extra.hashCode()}.json")
        Files.writeString(path, JsonObject(mapOf("schemaVersion" to JsonPrimitive(1), "enabled" to JsonPrimitive(true),
            "site" to JsonPrimitive(credentials.baseUrl), "space" to JsonPrimitive("COMMON"),
            "spaceId" to JsonPrimitive("20"), "rootPageId" to JsonPrimitive(root)) + extra).toString())
        return path
    }
    @Test fun `same connection shares corpus while credential and root changes isolate it`() {
        val path = config()
        val data = home.resolve("data")
        val first = KnowledgeSettings(path, credentials, data)
        val second = KnowledgeSettings(path, credentials, data)
        assertEquals(first.corpusRoot, second.corpusRoot)
        val tokenChanged = KnowledgeSettings(path, credentials.copy(apiToken = "other-fixture"), data)
        val userChanged = KnowledgeSettings(path, credentials.copy(email = "other@example.test"), data)
        val rootChanged = KnowledgeSettings(config("211"), credentials, data)
        assertEquals(4, setOf(first.corpusRoot, tokenChanged.corpusRoot, userChanged.corpusRoot, rootChanged.corpusRoot).size)
        assertFalse(first.corpusRoot.toString().contains(credentials.email))
        assertFalse(first.corpusRoot.toString().contains(credentials.apiToken))
    }
    @Test fun `configured identity changes invalidate an already bound connection`() {
        val path = config()
        val bound = KnowledgeSettings(path, credentials, home.resolve("data"))
        val before = Json.parseToJsonElement(Files.readString(path)).jsonObject
        Files.writeString(path, JsonObject(before + ("rootPageId" to JsonPrimitive("211"))).toString())
        assertFailsWith<IllegalArgumentException> { bound.read() }
    }
    @Test fun `invalid present configuration never becomes an absent connection`() {
        for (extra in listOf(mapOf("enabled" to JsonPrimitive(false)), mapOf("schemaVersion" to JsonPrimitive(2)),
            mapOf("spaceId" to JsonPrimitive("0")), mapOf("site" to JsonPrimitive("http://example.test")),
            mapOf("tokenEnv" to JsonPrimitive("TOKEN=secret")), mapOf("pollIntervalSeconds" to JsonPrimitive(0)),
            mapOf("unexpected" to JsonPrimitive(true)))) {
            assertFails { KnowledgeSettings(config(extra = extra), credentials, home.resolve("data")) }
        }
    }
    @Test fun `configuration symlink is not accepted as the connection authority`() {
        val path = config()
        val link = home.resolve("link.json")
        Files.createSymbolicLink(link, path)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings(link, credentials, home.resolve("data")) }
    }
    @Test fun `group writable configuration is rejected before it can select a corpus`() {
        val path = config()
        Files.setPosixFilePermissions(path, java.nio.file.attribute.PosixFilePermissions.fromString("rw-rw-r--"))
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings(path, credentials, home.resolve("data")) }
    }
    @Test fun `symlink ancestor cannot supply the configuration authority`() {
        val real = home.resolve("real")
        Files.createDirectories(real)
        val source = config()
        Files.copy(source, real.resolve("config.json"))
        val link = home.resolve("alias")
        Files.createSymbolicLink(link, real)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings(link.resolve("config.json"), credentials, home.resolve("data")) }
    }
    @Test fun `oversized configuration is refused rather than silently truncated`() {
        val path = config()
        Files.writeString(path, " ".repeat(65537))
        assertFails { KnowledgeSettings(path, credentials, home.resolve("data")) }
    }

}
