package io.crscube.clitools.knowledge

import io.crscube.clitools.jirafetcher.config.JiraConfig
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import kotlin.test.*

class KnowledgeSettingsTest {
    @TempDir lateinit var home: Path
    private val credentials = JiraConfig("https://example.atlassian.net", "fixture@example.test", "fixture-credential")
    private fun environment(): MutableMap<String, String> = mutableMapOf(
        "WIKI_ATLASSIAN_ENABLED" to "true", "WIKI_ATLASSIAN_SITE" to credentials.baseUrl,
        "WIKI_ATLASSIAN_SPACE" to "COMMON", "WIKI_ATLASSIAN_SPACE_ID" to "20",
        "WIKI_ATLASSIAN_ROOT_PAGE_ID" to "210", "ATLASSIAN_EMAIL" to credentials.email,
        "ATLASSIAN_API_TOKEN" to credentials.apiToken, "HOME" to home.toString())
    private fun settings(extra: Map<String, String> = emptyMap()) = KnowledgeSettings.fromEnvironment(environment() + extra)

    @Test fun `activation is explicit and shared credentials or launcher settings do not activate`() {
        assertFalse(KnowledgeSettings.enabled(emptyMap()))
        assertFalse(KnowledgeSettings.enabled(mapOf("ATLASSIAN_EMAIL" to "shared", "ATLASSIAN_API_TOKEN" to "shared",
            "WIKI_ATLASSIAN_JAVA" to "/missing/java", "WIKI_ATLASSIAN_CONFIG" to "/ignored/config.json")))
        for (key in listOf("SITE", "SPACE", "SPACE_ID", "ROOT_PAGE_ID", "MEMORY_PAGE_ID", "FEEDBACK_PAGE_ID", "POLL_INTERVAL_SECONDS")) {
            assertFailsWith<IllegalArgumentException>(key) { KnowledgeSettings.enabled(mapOf("WIKI_ATLASSIAN_$key" to "")) }
        }
        for (flag in listOf("", "TRUE", "False", " true", "true ", "1")) {
            assertFailsWith<IllegalArgumentException>(flag) { KnowledgeSettings.enabled(mapOf("WIKI_ATLASSIAN_ENABLED" to flag)) }
        }
        assertFalse(KnowledgeSettings.enabled(environment() + mapOf("WIKI_ATLASSIAN_ENABLED" to "false",
            "WIKI_ATLASSIAN_SITE" to "invalid", "WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS" to "invalid")))
        assertFalse(Files.exists(home.resolve("data")))
    }

    @Test fun `enabled connection requires all settings and nonblank Atlassian credentials`() {
        for (key in listOf("WIKI_ATLASSIAN_SITE", "WIKI_ATLASSIAN_SPACE", "WIKI_ATLASSIAN_SPACE_ID",
            "WIKI_ATLASSIAN_ROOT_PAGE_ID", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN")) {
            assertFailsWith<IllegalArgumentException>(key) { KnowledgeSettings.fromEnvironment(environment() - key) }
            for (value in listOf("", " \t\n")) assertFailsWith<IllegalArgumentException>(key) { settings(mapOf(key to value)) }
        }
        assertFailsWith<IllegalArgumentException> {
            KnowledgeSettings.fromEnvironment(environment() - setOf("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN") +
                mapOf("JIRA_EMAIL" to "legacy", "JIRA_API_TOKEN" to "legacy"))
        }
        assertFalse(Files.exists(home.resolve("data")))
    }

    @Test fun `origin accepts HTTPS origins and rejects unsafe authority path and port forms`() {
        for (site in listOf("https://example.atlassian.net", "https://example.atlassian.net/",
            "https://example.atlassian.net:1", "https://example.atlassian.net:65535/", "https://[::1]:443", "https://[::ffff:127.0.0.1]:9")) {
            assertEquals(site.trimEnd('/'), settings(mapOf("WIKI_ATLASSIAN_SITE" to site)).credentials.baseUrl)
        }
        for (site in listOf("http://example.test", "HTTPS://example.test", "https://", "https://example.test/path",
            "https://user:password@example.test", "https://example.test?query", "https://example.test#fragment",
            "https://example.test:0", "https://example.test:65536", "https://example.test:", "https://example.test:-1",
            "https://[::ffff:0127.0.0.1]:9", "https://example.test:abc", "https://example.test\\path", " https://example.test", "https://example.test\n",
            "https://example..test", "https://bad_host.test", "https://example.test/?", "https://example.test/#")) {
            assertFailsWith<IllegalArgumentException>(site) { settings(mapOf("WIKI_ATLASSIAN_SITE" to site)) }
        }
        assertFalse(Files.exists(home.resolve("data")))
    }

    @Test fun `space and every native ID have strict boundaries including supplied optional IDs`() {
        for (space in listOf("a", "A_0-z", "a".repeat(100))) settings(mapOf("WIKI_ATLASSIAN_SPACE" to space))
        for (space in listOf("", "a".repeat(101), "with space", "a.b", "공통")) {
            assertFailsWith<IllegalArgumentException> { settings(mapOf("WIKI_ATLASSIAN_SPACE" to space)) }
        }
        for (key in listOf("WIKI_ATLASSIAN_SPACE_ID", "WIKI_ATLASSIAN_ROOT_PAGE_ID", "WIKI_ATLASSIAN_MEMORY_PAGE_ID", "WIKI_ATLASSIAN_FEEDBACK_PAGE_ID")) {
            settings(mapOf(key to "123456789012345678901234567890"))
            for (id in listOf("", "0", "01", "-1", "+1", " 1", "1 ", "1.0", "١")) {
                assertFailsWith<IllegalArgumentException>(key) { settings(mapOf(key to id)) }
            }
        }
    }

    @Test fun `poll interval defaults to300 and validates decimal bounds`() {
        assertEquals(300L, settings().pollIntervalSeconds)
        for (seconds in listOf("30", "300", "86400", "00030")) {
            assertEquals(seconds.toLong(), settings(mapOf("WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS" to seconds)).pollIntervalSeconds)
        }
        for (seconds in listOf("", "29", "86401", "0", "-30", "+30", " 30", "30 ", "30.0", "٣٠", "9".repeat(100))) {
            assertFailsWith<IllegalArgumentException>(seconds) { settings(mapOf("WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS" to seconds)) }
        }
    }

    @Test fun `environment and JSON inputs are defensively snapshotted`() {
        val env = environment()
        val bound = KnowledgeSettings.fromEnvironment(env)
        val initial = bound.read()
        val identity = bound.identity()
        val corpus = bound.corpusRoot
        env["WIKI_ATLASSIAN_ROOT_PAGE_ID"] = "211"
        env["ATLASSIAN_API_TOKEN"] = "changed"
        env.clear()
        assertEquals(initial, bound.read())
        assertEquals(identity, bound.identity())
        assertEquals(corpus, bound.corpusRoot)
        val values = initial.toMutableMap()
        val direct = KnowledgeSettings(JsonObject(values), credentials, home.resolve("direct"))
        values["rootPageId"] = JsonPrimitive("999")
        assertEquals("210", direct.read().getValue("rootPageId").jsonPrimitive.content)
        val spaced = settings(mapOf("ATLASSIAN_EMAIL" to " fixture@example.test ", "ATLASSIAN_API_TOKEN" to " fixture-token "))
        assertEquals(" fixture-token ", spaced.credentials.apiToken)
        assertEquals(" fixture@example.test ", spaced.credentials.email)
    }

    @Test fun `connection projections preserve native scope and optional document IDs`() {
        val connections = settings(mapOf("WIKI_ATLASSIAN_MEMORY_PAGE_ID" to "301", "WIKI_ATLASSIAN_FEEDBACK_PAGE_ID" to "302")).connections()
        assertEquals(connections["common"], connections["project"])
        assertEquals(connections["project"], connections["effective"])
        val native = connections.getValue("effective").jsonObject.getValue("confluence").jsonObject
        assertEquals("210", native.getValue("knowledgeRootPageId").jsonPrimitive.content)
        assertEquals("301", native.getValue("memoryPageId").jsonPrimitive.content)
        assertEquals("302", native.getValue("maintenancePageId").jsonPrimitive.content)
        assertEquals(JsonNull, connections.getValue("effective").jsonObject["jira"])
    }

    @Test fun `token rotation shares corpus while account site space and root changes isolate it`() {
        val first = settings()
        assertEquals(first.corpusRoot, settings().corpusRoot)
        assertEquals(first.corpusRoot, settings(mapOf("ATLASSIAN_API_TOKEN" to "other-fixture")).corpusRoot)
        assertNotEquals(first.credentialFingerprint, settings(mapOf("ATLASSIAN_API_TOKEN" to "other-fixture")).credentialFingerprint)
        assertEquals(first.corpusRoot, settings(mapOf("WIKI_ATLASSIAN_SITE" to credentials.baseUrl + "/")).corpusRoot)
        assertEquals(home.resolve(".local/share/wiki-atlassian"), KnowledgeSettings.defaultDataHome(environment() + ("XDG_DATA_HOME" to "/ignored")))
        val variants = listOf(mapOf("ATLASSIAN_EMAIL" to "other@example.test"),
            mapOf("WIKI_ATLASSIAN_SITE" to "https://other.atlassian.net"), mapOf("WIKI_ATLASSIAN_SPACE_ID" to "21"),
            mapOf("WIKI_ATLASSIAN_ROOT_PAGE_ID" to "211"))
        assertEquals(5, (variants.map { settings(it).corpusRoot } + listOf(first.corpusRoot)).toSet().size)
        assertFalse(first.corpusRoot.toString().contains(credentials.email))
        assertFalse(first.corpusRoot.toString().contains(credentials.apiToken))
        assertFalse(Files.exists(first.corpusRoot))
    }

    @Test fun `legacy JSON and TOML files cannot configure or mutate a connection`() {
        Files.createDirectories(home.resolve(".config/wiki-atlassian"))
        val legacy = home.resolve(".config/wiki-atlassian/config.json")
        Files.writeString(legacy, "invalid secret-bearing JSON")
        Files.writeString(home.resolve("mise.local.toml"), "not a valid config either")
        val env = environment() + mapOf("HOME" to home.toString(), "WIKI_ATLASSIAN_CONFIG" to legacy.toString())
        val bound = KnowledgeSettings.fromEnvironment(env)
        Files.writeString(legacy, "changed")
        assertEquals("210", bound.read().getValue("rootPageId").jsonPrimitive.content)
        assertFalse(KnowledgeSettings.enabled(mapOf("HOME" to home.toString(), "WIKI_ATLASSIAN_CONFIG" to legacy.toString())))
    }

    @Test fun `invalid connection diagnostics contain no supplied values and create no corpus`() {
        val sentinel = "secret-do-not-print"
        for (key in listOf("WIKI_ATLASSIAN_SITE", "WIKI_ATLASSIAN_SPACE", "WIKI_ATLASSIAN_SPACE_ID",
            "WIKI_ATLASSIAN_ROOT_PAGE_ID", "WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS", "WIKI_ATLASSIAN_ENABLED")) {
            val error = assertFailsWith<IllegalArgumentException> { settings(mapOf(key to "?$sentinel")) }
            assertFalse(error.stackTraceToString().contains(sentinel))
        }
        assertFalse(Files.exists(home.resolve("data")))
    }

    @Test fun `corrupt and symlink legacy candidates cannot block a fresh empty corpus`() {
        val bound = settings()
        val source = bound.legacyCorpusRoot.resolve("confluence/COMMON/.sync/manifest.json")
        KnowledgeSettings.prepareCorpus(source.parent)
        Files.writeString(source, "not-json-private-legacy")
        bound.prepareCorpus()
        assertTrue(Files.isDirectory(bound.corpusRoot))
        assertFalse(Files.exists(bound.corpusRoot.resolve("confluence/COMMON/.sync/manifest.json")))
        assertEquals("not-json-private-legacy", Files.readString(source))
        Files.delete(source)
        val outside = home.resolve("outside.json")
        Files.writeString(outside, "private-source")
        Files.createSymbolicLink(source, outside)
        settings().prepareCorpus()
        assertFalse(Files.exists(bound.corpusRoot.resolve("confluence/COMMON/.sync/manifest.json")))
        assertEquals("private-source", Files.readString(outside))
        // Once failed, the same process must not retry large/corrupt optional migration on each poll.
        Files.delete(source)
        Files.writeString(source, "still-invalid")
        Files.delete(source.parent.resolve("sync.lock"))
        bound.prepareCorpus()
        assertFalse(Files.exists(source.parent.resolve("sync.lock")), "Repeated polling must not revisit the legacy source")
    }

    @Test fun `corpus preparation rejects symlink paths and group writable metadata`() {
        val real = home.resolve("real")
        KnowledgeSettings.prepareCorpus(real)
        assertEquals(PosixFilePermissions.fromString("rwx------"), Files.getPosixFilePermissions(real))
        val alias = home.resolve("alias")
        Files.createSymbolicLink(alias, real)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(alias.resolve("corpus")) }
        Files.setPosixFilePermissions(real, PosixFilePermissions.fromString("rwxrwx---"))
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(real) }
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(Path.of("relative")) }
    }
}
