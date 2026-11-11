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
    private val project: Path get() = Files.createDirectories(home.resolve("project")).also { Files.createDirectories(it.resolve(".git")) }
    private fun manifest(root: Path = project): Path = Files.createDirectories(root.resolve("doc/harness")).resolve("manifest.yaml")
    private val selection = buildJsonObject { put("key", "COMMON"); put("rootPageId", "210") }
    private val public = buildJsonObject { put("enabled", true); put("site", credentials.baseUrl); put("spaces", JsonArray(listOf(selection))) }
    private fun document(value: JsonObject): String = buildJsonObject {
        put("version", 7); put("unrelated", buildJsonObject { put("nested", JsonArray(emptyList())) })
        put("wiki", JsonObject(mapOf("backend" to JsonPrimitive("atlassian")) + value))
    }.toString()
    private fun environment(): MutableMap<String, String> = mutableMapOf("ATLASSIAN_BASE_URL" to credentials.baseUrl,
        "ATLASSIAN_EMAIL" to credentials.email, "ATLASSIAN_API_TOKEN" to credentials.apiToken, "HOME" to home.toString())
    private fun configured(value: JsonObject = public, auth: Map<String, String> = environment()): KnowledgeProject {
        Files.writeString(manifest(), document(value)); return KnowledgeProject.fromProject(project, auth)!!
    }
    private fun raw(value: String, auth: Map<String, String> = environment()): KnowledgeProject? {
        Files.writeString(manifest(), "wiki:\n  backend: atlassian\n" + value.prependIndent("  ") + "\n")
        return KnowledgeProject.fromProject(project, auth)
    }
    private fun resolved(fields: Map<String, JsonElement> = emptyMap(), auth: JiraConfig = credentials): KnowledgeSettings = KnowledgeSettings(buildJsonObject {
        put("enabled", true); put("site", auth.baseUrl); put("space", "COMMON"); put("spaceId", "20"); put("rootPageId", "210")
        fields.forEach { (key, value) -> put(key, value) }
    }, auth, home.resolve("data"))

    @Test fun `absent disabled and old environment are inert before auth or cache`() {
        val old = mapOf("WIKI_ATLASSIAN_ENABLED" to "true", "WIKI_ATLASSIAN_SITE" to "invalid")
        assertNull(KnowledgeProject.fromProject(project, old))
        Files.writeString(project.resolve("wiki-atlassian.yaml"), public.toString())
        assertNull(KnowledgeProject.fromProject(project, old))
        assertNull(raw("enabled: false", emptyMap()))
        assertNull(raw("enabled: false\nspaces: [{key: COMMON}]", old))
        assertNotNull(configured(auth = environment() + old))
        assertFalse(Files.exists(home.resolve(".local/share/wiki-atlassian")))
    }

    @Test fun `public site spaces and private authentication are required when enabled`() {
        for (key in listOf("site", "spaces")) assertFailsWith<IllegalArgumentException> { configured(JsonObject(public - key)) }
        for (key in listOf("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "ATLASSIAN_BASE_URL")) {
            assertFailsWith<IllegalArgumentException> { configured(auth = environment() - key) }
            for (value in listOf("", " \t\n")) assertFailsWith<IllegalArgumentException> { configured(auth = environment() + (key to value)) }
        }
        for (pin in listOf("https://other.atlassian.net", "http://example.atlassian.net", "https://user:secret@example.atlassian.net"))
            assertFailsWith<IllegalArgumentException> { configured(auth = environment() + ("ATLASSIAN_BASE_URL" to pin)) }
        assertFalse(Files.exists(home.resolve(".local/share/wiki-atlassian")))
    }

    @Test fun `HTTPS origins preserve strict authority and normalize terminal slash`() {
        for (site in listOf("https://example.atlassian.net", "https://example.atlassian.net/", "https://example.atlassian.net:1", "https://[::1]:443"))
            assertEquals(site.trimEnd('/'), configured(JsonObject(public + ("site" to JsonPrimitive(site))), environment() + ("ATLASSIAN_BASE_URL" to site)).credentials.baseUrl)
        for (site in listOf("http://example.test", "HTTPS://example.test", "https://", "https://example.test/path", "https://user:password@example.test",
            "https://example.test?query", "https://example.test#fragment", "https://example.test:0", "https://example.test:65536", "https://example.test:",
            "https://[::ffff:0127.0.0.1]:9", "https://example.test:abc", "https://example.test\\path", " https://example.test", "https://example.test\n", "https://bad_host.test"))
            assertFailsWith<IllegalArgumentException> { configured(JsonObject(public + ("site" to JsonPrimitive(site)))) }
    }

    @Test fun `space keys optional roots and IDs retain strict scalar validation`() {
        fun selected(value: JsonObject) = configured(JsonObject(public + ("spaces" to JsonArray(listOf(value)))))
        for (key in listOf("a", "A_0-z", "a".repeat(100))) selected(buildJsonObject { put("key", key) })
        for (key in listOf("", "a".repeat(101), "with space", "a.b", "공통"))
            assertFailsWith<IllegalArgumentException> { selected(buildJsonObject { put("key", key) }) }
        for (field in listOf("rootPageId", "memoryPageId", "feedbackPageId")) {
            selected(JsonObject(selection + (field to JsonPrimitive("123456789012345678901234567890"))))
            for (id in listOf("", "0", "01", "-1", "+1", " 1", "1 ", "1.0", "١"))
                assertFailsWith<IllegalArgumentException> { selected(JsonObject(selection + (field to JsonPrimitive(id)))) }
            assertFailsWith<IllegalArgumentException> { selected(JsonObject(selection + (field to JsonPrimitive(123)))) }
        }
        for (field in listOf("space", "spaceId", "rootPageId"))
            assertFailsWith<IllegalArgumentException> { configured(JsonObject(public + (field to JsonPrimitive("20")))) }
        assertFailsWith<IllegalArgumentException> { selected(JsonObject(selection + ("spaceId" to JsonPrimitive("20")))) }
    }

    @Test fun `space selection list is bounded unique and entirely valid even when disabled`() {
        fun spaces(values: List<JsonElement>) = configured(JsonObject(public + ("spaces" to JsonArray(values))))
        assertFailsWith<IllegalArgumentException> { spaces(emptyList()) }
        assertFailsWith<IllegalArgumentException> { spaces(listOf(selection, selection)) }
        assertFailsWith<IllegalArgumentException> { spaces(listOf(JsonPrimitive("COMMON"))) }
        spaces((1..32).map { buildJsonObject { put("key", "SPACE$it") } })
        assertFailsWith<IllegalArgumentException> { spaces((1..33).map { buildJsonObject { put("key", "SPACE$it") } }) }
        assertFailsWith<IllegalArgumentException> { raw("enabled: false\nspaces: [{key: COMMON, rootPageId: 20}]") }
    }

    @Test fun `poll and comment watches retain exact bounds and belong to their declared scope`() {
        assertEquals(300L, configured().pollIntervalSeconds)
        for (poll in listOf(30, 300, 86400)) assertEquals(poll.toLong(), configured(JsonObject(public + ("pollIntervalSeconds" to JsonPrimitive(poll)))).pollIntervalSeconds)
        for (poll in listOf(JsonPrimitive(29), JsonPrimitive(86401), JsonPrimitive("300"), JsonPrimitive(true)))
            assertFailsWith<IllegalArgumentException> { configured(JsonObject(public + ("pollIntervalSeconds" to poll))) }
        val selected = JsonObject(selection + ("watchCommentPages" to JsonPrimitive("210,211")))
        assertNotNull(configured(JsonObject(public + ("spaces" to JsonArray(listOf(selected))))))
        assertFailsWith<IllegalArgumentException> { configured(JsonObject(public + ("watchCommentPages" to JsonPrimitive("210")))) }
        for (watch in listOf("", "0", "210,210", "210, 211"))
            assertFailsWith<IllegalArgumentException> { configured(JsonObject(public + ("spaces" to JsonArray(listOf(JsonObject(selection + ("watchCommentPages" to JsonPrimitive(watch)))))))) }
    }

    @Test fun `malformed tagged aliased nested and secret-bearing YAML stays redacted`() {
        val sentinel = "secret-do-not-print"
        for (value in listOf("", "[]", "enabled: 'false'", "enabled: null", "enabled: false\nenabled: true", "enabled: false\ntoken: $sentinel",
            "enabled: false\nsite: http://$sentinel", "enabled: false\nspaceId: 20", "enabled: false\npollIntervalSeconds: true",
            "enabled: &flag false", "enabled: false\nsite: *missing", "enabled: !!bool false", "!!java/object:$sentinel {}",
            "enabled: false\nsite: [a]", "enabled: false\nsite: {nested: '$sentinel'}", "enabled: false\n---\nenabled: false", "#" + "a".repeat(65536))) {
            val error = assertFailsWith<IllegalArgumentException> { raw(value) }
            assertFalse(error.stackTraceToString().contains(sentinel)); assertNull(error.cause)
        }
        Files.write(manifest(), byteArrayOf(0xc3.toByte(), 0x28))
        assertFailsWith<IllegalArgumentException> { KnowledgeProject.fromProject(project, environment()) }
    }

    @Test fun `nearest Git directory and gitfile isolate nested repositories`() {
        configured()
        val nested = Files.createDirectories(project.resolve("nested/deeper"))
        assertEquals("COMMON", KnowledgeProject.fromProject(nested, environment())!!.selections.single().getValue("key").jsonPrimitive.content)
        val boundary = nested.parent; Files.writeString(boundary.resolve(".git"), "gitdir: boundary-only")
        assertNull(KnowledgeProject.fromProject(nested, environment()))
        Files.writeString(manifest(boundary), "wiki: {enabled: false}"); assertNull(KnowledgeProject.fromProject(nested, emptyMap()))
    }

    @Test fun `no Git uses startup only and unrelated common mappings remain untouched`() {
        val noGit = Files.createDirectories(home.resolve("no-git/child"))
        Files.writeString(manifest(noGit.parent), document(public)); assertNull(KnowledgeProject.fromProject(noGit, environment()))
        val path = manifest(noGit); val source = "status_codes: {200: ok, 404: missing}\nwiki: " + JsonObject(public + ("backend" to JsonPrimitive("atlassian")))
        Files.writeString(path, source); assertNotNull(KnowledgeProject.fromProject(noGit, environment())); assertEquals(source, Files.readString(path))
        Files.writeString(path, "{version: 7, project: {name: fixture}}"); assertNull(KnowledgeProject.fromProject(noGit, emptyMap()))
    }

    @Test fun `manifest parent symlinks are rejected before configuration is read`() {
        val outside = Files.createDirectories(home.resolve("outside/harness")); Files.writeString(outside.resolve("manifest.yaml"), document(public))
        Files.createSymbolicLink(project.resolve("doc"), outside.parent); assertFailsWith<IllegalArgumentException> { KnowledgeProject.fromProject(project, environment()) }
        Files.delete(project.resolve("doc")); Files.createDirectory(project.resolve("doc")); Files.createSymbolicLink(project.resolve("doc/harness"), outside)
        assertFailsWith<IllegalArgumentException> { KnowledgeProject.fromProject(project, environment()) }
    }

    @Test fun `manifest leaf symlinks and nonregular files fail before authentication`() {
        val path = manifest(); Files.createDirectory(path); assertFailsWith<IllegalArgumentException> { KnowledgeProject.fromProject(project, environment()) }
        Files.delete(path); val target = home.resolve("outside.yaml"); Files.writeString(target, document(public)); Files.createSymbolicLink(path, target)
        assertFailsWith<IllegalArgumentException> { KnowledgeProject.fromProject(project, environment()) }
    }

    @Test fun `project and private values are immutable startup snapshots`() {
        val env = environment(); val bound = configured(auth = env); val initial = bound.read()
        Files.writeString(manifest(), "wiki: {enabled: false}"); env.clear()
        assertEquals(initial, bound.read()); assertEquals(credentials.apiToken, bound.credentials.apiToken)
        assertNull(KnowledgeProject.fromProject(project, emptyMap()))
        val spaced = configured(auth = environment() + mapOf("ATLASSIAN_EMAIL" to " fixture@example.test ", "ATLASSIAN_API_TOKEN" to " fixture-token "))
        assertEquals(" fixture-token ", spaced.credentials.apiToken); assertEquals(" fixture@example.test ", spaced.credentials.email)
    }

    @Test fun `resolved projections distinguish entire space and selected root without fake home`() {
        val rooted = resolved(mapOf("memoryPageId" to JsonPrimitive("301"), "feedbackPageId" to JsonPrimitive("302")))
        val native = rooted.connections().getValue("effective").jsonObject.getValue("confluence").jsonObject
        assertEquals("210", native.getValue("knowledgeRootPageId").jsonPrimitive.content); assertEquals(JsonPrimitive(false), native["wholeSpace"])
        assertEquals("301", native.getValue("memoryPageId").jsonPrimitive.content)
        val whole = KnowledgeSettings(JsonObject(rooted.read() - "rootPageId"), credentials, home.resolve("data"))
        assertTrue(whole.wholeSpace); assertEquals("", whole.connections().getValue("common").jsonObject.getValue("confluence").jsonObject.getValue("homePageId").jsonPrimitive.content)
        assertNotEquals(rooted.corpusRoot, whole.corpusRoot)
    }

    @Test fun `cache partition uses site account key and root independent of tokens or native IDs`() {
        val first = resolved(); val rotated = resolved(auth = credentials.copy(apiToken = "rotated"))
        assertEquals(first.corpusRoot, rotated.corpusRoot); assertNotEquals(first.credentialFingerprint, rotated.credentialFingerprint)
        assertEquals(first.corpusRoot, resolved(mapOf("spaceId" to JsonPrimitive("99"))).corpusRoot)
        val variants = listOf(resolved(auth = credentials.copy(email = "other@example.test")), resolved(mapOf("space" to JsonPrimitive("OTHER"))), resolved(mapOf("rootPageId" to JsonPrimitive("211"))))
        assertEquals(4, (variants.map { it.corpusRoot } + listOf(first.corpusRoot)).toSet().size)
        assertFalse(Files.exists(first.corpusRoot)); assertFalse(first.corpusRoot.toString().contains(credentials.apiToken))
        assertEquals(home.resolve(".local/share/wiki-atlassian"), KnowledgeSettings.defaultDataHome(environment() + ("XDG_DATA_HOME" to "/ignored")))
    }

    @Test fun `corpus preparation rejects symlinks writable metadata and preserves old partitions`() {
        val real = home.resolve("real"); KnowledgeSettings.prepareCorpus(real); assertEquals(PosixFilePermissions.fromString("rwx------"), Files.getPosixFilePermissions(real))
        val alias = home.resolve("alias"); Files.createSymbolicLink(alias, real); assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(alias.resolve("corpus")) }
        Files.setPosixFilePermissions(real, PosixFilePermissions.fromString("rwxrwx---")); assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(real) }
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.prepareCorpus(Path.of("relative")) }
        val old = Files.createDirectories(home.resolve("data/old-partition")); Files.writeString(old.resolve("manifest.json"), "preserved")
        resolved().prepareCorpus(); assertEquals("preserved", Files.readString(old.resolve("manifest.json")))
    }
}
