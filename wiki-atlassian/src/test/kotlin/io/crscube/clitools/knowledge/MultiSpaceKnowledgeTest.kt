package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.ConfluenceRemote
import io.crscube.clitools.jirafetcher.config.JiraConfig
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.client.request.HttpRequestData
import io.ktor.http.*
import kotlinx.coroutines.runBlocking
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import kotlin.test.*

/** Resolve user-facing keys using the native API without coupling public YAML to native IDs. */
class MultiSpaceKnowledgeTest {
    @TempDir lateinit var home: Path
    private val credentials = JiraConfig("https://example.atlassian.net", "fixture@example.test", "first-fixture-token")
    private val responses = headersOf(HttpHeaders.ContentType, "application/json")

    private fun selection(key: String, root: String? = null) = buildJsonObject {
        put("key", key); root?.let { put("rootPageId", it) }
    }
    private fun project(vararg selections: JsonObject, auth: JiraConfig = credentials): KnowledgeProject = KnowledgeProject(buildJsonObject {
        put("enabled", true); put("site", credentials.baseUrl); put("spaces", JsonArray(selections.toList()))
    }, auth, home.resolve("data"))
    private fun nativeSpace(id: String, key: String) = buildJsonObject {
        put("id", id); put("key", key); put("name", key)
    }
    private fun <T> remote(spaces: () -> JsonArray, requests: MutableList<HttpRequestData> = mutableListOf(), auth: JiraConfig = credentials, action: (ConfluenceRemote) -> T): T {
        return HttpClient(MockEngine { request ->
            requests += request
            assertEquals(HttpMethod.Get, request.method)
            assertEquals("example.atlassian.net", request.url.host)
            assertEquals("/wiki/api/v2/spaces", request.url.encodedPath, "Resolving a key must not read a guessed homepage or document")
            respond(buildJsonObject { put("results", spaces()); putJsonObject("_links") {} }.toString(), headers = responses)
        }).use { client -> ConfluenceRemote(auth, client).use(action) }
    }

    @Test fun `space keys resolve independently to native IDs without requiring a home page`() {
        val requestLog = mutableListOf<HttpRequestData>()
        val selected = project(selection("COMMON"), selection("DEV", "410"))
        remote({ JsonArray(listOf(nativeSpace("20", "COMMON"), nativeSpace("30", "DEV"))) }, requestLog) { api ->
            val common = runBlocking { selected.resolve(api, "COMMON") }
            val development = runBlocking { selected.resolve(api, "DEV") }
            assertEquals("20", common.read().getValue("spaceId").jsonPrimitive.content)
            assertFalse(common.read().containsKey("rootPageId"), "Whole-space scope must not fabricate a native home/ROOT ID")
            assertEquals("30", development.read().getValue("spaceId").jsonPrimitive.content)
            assertEquals("410", development.read().getValue("rootPageId").jsonPrimitive.content)
            assertNotEquals(common.corpusRoot, development.corpusRoot)
        }
        assertTrue(requestLog.isNotEmpty())
        assertFalse(Files.exists(home.resolve("data")), "Resolution does not imply a synchronized or authorized replica")
    }

    @Test fun `missing or ambiguous native keys never silently select the first returned space`() {
        val selected = project(selection("COMMON"), selection("DEV"))
        for (spaces in listOf(
            JsonArray(listOf(nativeSpace("20", "COMMON"))),
            JsonArray(listOf(nativeSpace("30", "DEV"), nativeSpace("31", "DEV")))
        )) {
            remote({ spaces }) { api ->
                assertFails { runBlocking { selected.resolve(api, "DEV") } }
            }
        }
        remote({ JsonArray(listOf(nativeSpace("20", "COMMON"), nativeSpace("30", "DEV"))) }) { api ->
            assertFails { runBlocking { selected.resolve(api, "NOT_CONFIGURED") } }
            assertEquals("20", runBlocking { selected.resolve(api, "COMMON") }.read().getValue("spaceId").jsonPrimitive.content)
        }
        assertFalse(Files.exists(home.resolve("data")))
    }

    @Test fun `an unavailable key can resolve later without replacing another selected scope`() {
        val selected = project(selection("COMMON"), selection("DEV"))
        var spaces = JsonArray(listOf(nativeSpace("20", "COMMON")))
        remote({ spaces }) { api ->
            val original = runBlocking { selected.resolve(api, "COMMON") }
            assertFails { runBlocking { selected.resolve(api, "DEV") } }
            spaces = JsonArray(listOf(nativeSpace("20", "COMMON"), nativeSpace("30", "DEV")))
            val recovered = runBlocking { selected.resolve(api, "DEV") }
            val common = runBlocking { selected.resolve(api, "COMMON") }
            assertEquals(original.corpusRoot, common.corpusRoot)
            assertEquals("30", recovered.read().getValue("spaceId").jsonPrimitive.content)
        }
    }

    @Test fun `token replacement shares a partition while account and whole versus subtree selection stay isolated`() {
        val selected = selection("COMMON")
        val rotated = credentials.copy(apiToken = "replacement-fixture-token")
        val otherAccount = credentials.copy(email = "another-fixture@example.test")
        val spaces = { JsonArray(listOf(nativeSpace("20", "COMMON"))) }
        fun resolved(auth: JiraConfig, selectedScope: JsonObject): KnowledgeSettings = remote(spaces, auth = auth) { api ->
            runBlocking { project(selectedScope, auth = auth).resolve(api, "COMMON") }
        }
        val first = resolved(credentials, selected)
        val second = resolved(rotated, selected)
        val other = resolved(otherAccount, selected)
        val subtree = resolved(credentials, selection("COMMON", "210"))
        assertEquals(first.corpusRoot, second.corpusRoot)
        assertNotEquals(first.credentialFingerprint, second.credentialFingerprint)
        assertNotEquals(first.corpusRoot, other.corpusRoot)
        assertNotEquals(first.corpusRoot, subtree.corpusRoot)
    }
}
