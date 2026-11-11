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
    private fun document(value: JsonObject): String = buildJsonObject {
        put("schema_version", 1); put("project", buildJsonObject { put("name", "fixture"); put("unrelated", JsonArray(emptyList())) })
        put("wiki", JsonObject(mapOf("backend" to JsonPrimitive("atlassian")) + value))
    }.toString()
    private val public = buildJsonObject {
        put("enabled", true); put("site", credentials.baseUrl); put("space", "COMMON")
        put("spaceId", "20"); put("rootPageId", "210")
    }
    private fun environment(): MutableMap<String, String> = mutableMapOf(
        "ATLASSIAN_BASE_URL" to credentials.baseUrl, "ATLASSIAN_EMAIL" to credentials.email,
        "ATLASSIAN_API_TOKEN" to credentials.apiToken, "HOME" to home.toString())
    private fun settings(extra: Map<String, JsonElement> = emptyMap(), auth: Map<String, String> = environment()): KnowledgeSettings {
        Files.writeString(manifest(), document(JsonObject(public + extra)))
        return KnowledgeSettings.fromProject(project, auth)!!
    }
    private fun raw(value: String, auth: Map<String, String> = environment()): KnowledgeSettings? {
        Files.writeString(manifest(), "wiki:\n  backend: atlassian\n" + value.prependIndent("  ") + "\n")
        return KnowledgeSettings.fromProject(project, auth)
    }

    @Test fun `absent and disabled are inert before auth or corpus and obsolete env is ignored`() {
        val old = mapOf("WIKI_ATLASSIAN_ENABLED" to "true", "WIKI_ATLASSIAN_SITE" to "invalid",
            "WIKI_ATLASSIAN_CONFIG" to "/ignored/config.json", "ATLASSIAN_EMAIL" to "shared")
        assertNull(KnowledgeSettings.fromProject(project, old))
        Files.writeString(project.resolve("wiki-atlassian.yaml"), public.toString())
        assertNull(KnowledgeSettings.fromProject(project, old))
        assertNull(raw("enabled: false", emptyMap()))
        assertNull(raw("enabled: false\nspace: COMMON\nspaceId: '20'", old))
        assertEquals("210", settings(auth = environment() + old).read().getValue("rootPageId").jsonPrimitive.content)
        assertFalse(Files.exists(home.resolve(".local/share/wiki-atlassian")))
    }

    @Test fun `public fields and all three private authentication variables are required when enabled`() {
        for (key in listOf("site", "space", "spaceId", "rootPageId")) {
            Files.writeString(manifest(), document(JsonObject(public - key)))
            assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
        }
        for (key in listOf("ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "ATLASSIAN_BASE_URL")) {
            assertFailsWith<IllegalArgumentException> { settings(auth = environment() - key) }
            for (value in listOf("", " \t\n")) assertFailsWith<IllegalArgumentException> { settings(auth = environment() + (key to value)) }
        }
        for (pin in listOf("https://other.atlassian.net", "http://example.atlassian.net", "https://user:secret@example.atlassian.net")) {
            assertFailsWith<IllegalArgumentException> { settings(auth = environment() + ("ATLASSIAN_BASE_URL" to pin)) }
        }
        assertFalse(Files.exists(home.resolve(".local/share/wiki-atlassian")))
    }

    @Test fun `HTTPS origins preserve strict authority and port validation and normalize terminal slash`() {
        for (site in listOf("https://example.atlassian.net", "https://example.atlassian.net/",
            "https://example.atlassian.net:1", "https://example.atlassian.net:65535/", "https://[::1]:443", "https://[::ffff:127.0.0.1]:9")) {
            assertEquals(site.trimEnd('/'), settings(mapOf("site" to JsonPrimitive(site)), environment() + ("ATLASSIAN_BASE_URL" to site)).credentials.baseUrl)
        }
        for (site in listOf("http://example.test", "HTTPS://example.test", "https://", "https://example.test/path",
            "https://user:password@example.test", "https://example.test?query", "https://example.test#fragment",
            "https://example.test:0", "https://example.test:65536", "https://example.test:", "https://example.test:-1",
            "https://[::ffff:0127.0.0.1]:9", "https://example.test:abc", "https://example.test\\path", " https://example.test", "https://example.test\n",
            "https://example..test", "https://bad_host.test", "https://example.test/?", "https://example.test/#")) {
            assertFailsWith<IllegalArgumentException> { settings(mapOf("site" to JsonPrimitive(site))) }
        }
    }

    @Test fun `space native IDs and poll interval preserve bounds and actual scalar types`() {
        for (space in listOf("a", "A_0-z", "a".repeat(100))) settings(mapOf("space" to JsonPrimitive(space)))
        for (space in listOf("", "a".repeat(101), "with space", "a.b", "공통"))
            assertFailsWith<IllegalArgumentException> { settings(mapOf("space" to JsonPrimitive(space))) }
        for (key in listOf("spaceId", "rootPageId", "memoryPageId", "feedbackPageId")) {
            settings(mapOf(key to JsonPrimitive("123456789012345678901234567890")))
            for (id in listOf("", "0", "01", "-1", "+1", " 1", "1 ", "1.0", "١"))
                assertFailsWith<IllegalArgumentException> { settings(mapOf(key to JsonPrimitive(id))) }
            assertFailsWith<IllegalArgumentException> { settings(mapOf(key to JsonPrimitive(123))) }
        }
        assertEquals(300L, settings().pollIntervalSeconds)
        for (seconds in listOf(30, 300, 86400)) assertEquals(seconds.toLong(), settings(mapOf("pollIntervalSeconds" to JsonPrimitive(seconds))).pollIntervalSeconds)
        for (value in listOf(JsonPrimitive(29), JsonPrimitive(86401), JsonPrimitive("300"), JsonPrimitive(true), JsonPrimitive(30.0)))
            assertFailsWith<IllegalArgumentException> { settings(mapOf("pollIntervalSeconds" to value)) }
    }

    @Test fun `YAML rejects unsafe duplicate alias nested secret and ambiguous input with redacted errors`() {
        val sentinel = "secret-do-not-print"
        val cases = listOf("", "[]", "enabled: 'false'", "enabled: null", "enabled: false\nenabled: true",
            "enabled: false\ntoken: $sentinel", "enabled: false\nsite: http://$sentinel", "enabled: false\nspaceId: 20",
            "enabled: false\npollIntervalSeconds: true", "enabled: false\nwatchCommentPages: ''",
            "enabled: &flag false", "enabled: false\nsite: *missing", "enabled: !!bool false", "!!java/object:$sentinel {}",
            "enabled: false\nsite: [a]", "enabled: false\nsite: {nested: '$sentinel'}",
            "enabled: false\nsite: " + "[".repeat(25) + "'x'" + "]".repeat(25),
            "enabled: false\n---\nenabled: false", "#" + "a".repeat(65536))
        for (value in cases) {
            val error = assertFailsWith<IllegalArgumentException> { raw(value) }
            assertFalse(error.stackTraceToString().contains(sentinel))
            assertNull(error.cause)
        }
        Files.write(manifest(), byteArrayOf(0xc3.toByte(), 0x28))
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
        assertFalse(Files.exists(home.resolve(".local/share/wiki-atlassian")))
    }

    @Test fun `selection uses nearest Git directory or gitfile and no Git uses startup only`() {
        settings()
        val nested = Files.createDirectories(project.resolve("nested/deeper"))
        assertEquals("210", KnowledgeSettings.fromProject(nested, environment())!!.read().getValue("rootPageId").jsonPrimitive.content)
        val boundary = nested.parent
        Files.writeString(boundary.resolve(".git"), "gitdir: ignored-boundary-only")
        assertNull(KnowledgeSettings.fromProject(nested, environment()))
        Files.writeString(manifest(boundary), "wiki:\n  enabled: false")
        assertNull(KnowledgeSettings.fromProject(nested, emptyMap()))
        val noGit = Files.createDirectories(home.resolve("no-git/child"))
        Files.writeString(manifest(noGit.parent), document(public))
        assertNull(KnowledgeSettings.fromProject(noGit, environment()))
        Files.writeString(manifest(noGit), document(public))
        assertNotNull(KnowledgeSettings.fromProject(noGit, environment()))
    }

    @Test fun `common manifest without wiki stays inert and unrelated Harness fields remain allowed`() {
        val path = manifest()
        Files.writeString(path, "---\nschema_version: 3\nproject:\n  name: fixture\n  arbitrary: [a, b]\n")
        assertNull(KnowledgeSettings.fromProject(project, emptyMap()))
        Files.writeString(path, "{schema_version: 3, project: {name: fixture}}")
        assertNull(KnowledgeSettings.fromProject(project, emptyMap()))
        assertNotNull(settings())
        Files.writeString(path, "created: 2026-11-11\nother: {nested: [1, true, null]}\nwiki: " +
            JsonObject(public + ("backend" to JsonPrimitive("atlassian"))).toString())
        assertNotNull(KnowledgeSettings.fromProject(project, environment()))
        assertFailsWith<IllegalArgumentException> { settings(mapOf("backend" to JsonPrimitive("other"))) }
        for (value in listOf("wiki: []", "wiki: null", "wiki: {enabled: true}",
            "wiki: {backend: other, enabled: false}", "wiki: {backend: false, enabled: false}",
            "'wiki': {enabled: true}", "{wiki: {enabled: true}}")) {
            Files.writeString(path, value)
            assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
        }
        Files.writeString(path, "wiki: {enabled: false}")
        assertNull(KnowledgeSettings.fromProject(project, emptyMap()))
    }

    @Test fun `manifest parent symlinks are rejected before reading configuration`() {
        val outside = Files.createDirectories(home.resolve("outside/harness"))
        Files.writeString(outside.resolve("manifest.yaml"), document(public))
        Files.createSymbolicLink(project.resolve("doc"), outside.parent)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
        Files.delete(project.resolve("doc"))
        Files.createDirectory(project.resolve("doc"))
        Files.createSymbolicLink(project.resolve("doc/harness"), outside)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
    }

    @Test fun `nonregular symlink and invalid UTF8 project files are unavailable`() {
        val path = manifest()
        Files.createDirectory(path)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
        Files.delete(path)
        val target = home.resolve("outside.yaml")
        Files.writeString(target, document(public))
        Files.createSymbolicLink(path, target)
        assertFailsWith<IllegalArgumentException> { KnowledgeSettings.fromProject(project, environment()) }
    }

    @Test fun `startup YAML and authentication are immutable snapshots with compatible direct constructor`() {
        val env = environment()
        val bound = settings(auth = env)
        val initial = bound.read(); val identity = bound.identity(); val corpus = bound.corpusRoot
        Files.writeString(manifest(), "wiki:\n  enabled: false")
        env["ATLASSIAN_API_TOKEN"] = "changed"; env.clear()
        assertEquals(initial, bound.read()); assertEquals(identity, bound.identity()); assertEquals(corpus, bound.corpusRoot)
        assertNull(KnowledgeSettings.fromProject(project, emptyMap()))
        val values = initial.toMutableMap()
        val direct = KnowledgeSettings(JsonObject(values), credentials, home.resolve("direct"))
        values["rootPageId"] = JsonPrimitive("999")
        assertEquals("210", direct.read().getValue("rootPageId").jsonPrimitive.content)
        val spaced = settings(auth = environment() + mapOf("ATLASSIAN_EMAIL" to " fixture@example.test ", "ATLASSIAN_API_TOKEN" to " fixture-token "))
        assertEquals(" fixture-token ", spaced.credentials.apiToken); assertEquals(" fixture@example.test ", spaced.credentials.email)
    }

    @Test fun `connection projections preserve native scope and optional document IDs`() {
        val connections = settings(mapOf("memoryPageId" to JsonPrimitive("301"), "feedbackPageId" to JsonPrimitive("302"))).connections()
        assertEquals(connections["common"], connections["project"]); assertEquals(connections["project"], connections["effective"])
        val native = connections.getValue("effective").jsonObject.getValue("confluence").jsonObject
        assertEquals("210", native.getValue("knowledgeRootPageId").jsonPrimitive.content)
        assertEquals("301", native.getValue("memoryPageId").jsonPrimitive.content)
        assertEquals("302", native.getValue("maintenancePageId").jsonPrimitive.content)
        assertEquals(JsonNull, connections.getValue("effective").jsonObject["jira"])
    }

    @Test fun `same HOME shares corpus across projects and token rotation while identity changes isolate`() {
        val first = settings()
        val another = Files.createDirectories(home.resolve("another"))
        Files.writeString(manifest(another), document(public))
        assertEquals(first.corpusRoot, KnowledgeSettings.fromProject(another, environment())!!.corpusRoot)
        val rotated = settings(auth = environment() + ("ATLASSIAN_API_TOKEN" to "other-fixture"))
        assertEquals(first.corpusRoot, rotated.corpusRoot); assertNotEquals(first.credentialFingerprint, rotated.credentialFingerprint)
        assertEquals(first.corpusRoot, settings(mapOf("site" to JsonPrimitive(credentials.baseUrl + "/"))).corpusRoot)
        assertEquals(home.resolve(".local/share/wiki-atlassian"), KnowledgeSettings.defaultDataHome(environment() + ("XDG_DATA_HOME" to "/ignored")))
        val variants = listOf(settings(auth = environment() + ("ATLASSIAN_EMAIL" to "other@example.test")),
            settings(mapOf("site" to JsonPrimitive("https://other.atlassian.net")), environment() + ("ATLASSIAN_BASE_URL" to "https://other.atlassian.net")),
            settings(mapOf("spaceId" to JsonPrimitive("21"))), settings(mapOf("rootPageId" to JsonPrimitive("211"))))
        assertEquals(5, (variants.map { it.corpusRoot } + listOf(first.corpusRoot)).toSet().size)
        assertFalse(first.corpusRoot.toString().contains(credentials.email)); assertFalse(first.corpusRoot.toString().contains(credentials.apiToken))
        assertFalse(Files.exists(first.corpusRoot))
    }

    @Test fun `legacy JSON TOML and env path overrides cannot configure a connection`() {
        val legacy = home.resolve("config.json"); Files.writeString(legacy, "invalid secret-bearing JSON")
        Files.writeString(project.resolve("mise.local.toml"), "not-valid TOML")
        val env = environment() + ("WIKI_ATLASSIAN_CONFIG" to legacy.toString())
        assertNull(KnowledgeSettings.fromProject(project, env))
        val bound = settings(auth = env); Files.writeString(legacy, "changed")
        assertEquals("210", bound.read().getValue("rootPageId").jsonPrimitive.content)
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
