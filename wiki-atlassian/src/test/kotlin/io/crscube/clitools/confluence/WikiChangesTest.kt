package io.crscube.clitools.confluence

import io.crscube.clitools.jirafetcher.config.JiraConfig
import io.crscube.clitools.knowledge.KnowledgeSettings
import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.respond
import io.ktor.http.*
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.io.IOException
import java.nio.file.Files
import java.nio.file.NoSuchFileException
import java.nio.file.Path
import java.nio.file.attribute.PosixFilePermissions
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean
import kotlin.test.*

class WikiChangesTest {
    @TempDir lateinit var root: Path
    private val origin = "https://example.atlassian.net"
    private val config = ContextConfig(origin, "COMMON", "20", "210", "", "", "knowledge", knowledgeRootPageId = "210")
    private val control get() = root.resolve("confluence/COMMON/.sync")
    private val feedPath get() = control.resolve("changes.json")
    private val denialPath get() = control.resolve(WikiChanges.noticeDenialMarker)
    private fun manifest() = json(control.resolve("manifest.json"))
    private fun json(path: Path) = Json.parseToJsonElement(Files.readString(path)).jsonObject
    private fun events(feed: JsonObject) = feed.getValue("events").jsonArray.map { it.jsonObject }
    private fun notice(seq: Long, kind: String, id: String? = null, version: Int = 1) = buildJsonObject {
        put("seq", seq); put("kind", kind)
        if (id != null) { put("id", id); put("version", version) }
    }

    private class Page(val id: String, var parentId: String? = "210", var title: String = "Private title $id",
                       var body: String = "<p>Private body $id</p>", var version: Int = 1,
                       var spaceId: String = "20", var status: String = "current") {
        fun native() = RemotePage(RemoteNode(id, title, parentId, "page", spaceId, version, status), body)
        fun json(storage: Boolean) = buildJsonObject {
            put("id", id); put("title", title); put("type", "page"); put("spaceId", spaceId); put("status", status)
            parentId?.let { put("parentId", it) }
            putJsonObject("version") { put("number", version) }
            if (storage) putJsonObject("body") { putJsonObject("storage") { put("value", body) } }
        }
    }

    private class Comment(val id: String, val page: String, val kind: String, val parent: String? = null,
                          var version: Int = 1, var resolution: String = "open") {
        fun json() = buildJsonObject {
            put("id", id); put("pageId", page); put("status", "current")
            parent?.let { put("parentCommentId", it) }
            putJsonObject("version") { put("number", version); put("createdAt", "2026-11-06T12:00:00Z") }
            putJsonObject("body") { putJsonObject("storage") { put("value", "<p>Private comment $id</p>") } }
            if (kind == "inline") put("resolutionStatus", resolution)
        }
    }

    private inner class Provider(token: String = "fixture-token") : AutoCloseable {
        val pages = linkedMapOf("210" to Page("210", null), "211" to Page("211"))
        val comments = mutableListOf(Comment("310", "211", "footer"), Comment("320", "211", "inline"))
        val denied = mutableSetOf<String>()
        var unavailablePath: String? = null
        var overrideStatus: suspend (String) -> HttpStatusCode? = { null }
        var afterStorage: (String) -> Unit = {}
        var commentReads = 0
        val pageCommentReads = mutableMapOf<String, Int>()
        private fun listing(items: List<JsonObject>) = buildJsonObject {
            put("results", JsonArray(items)); putJsonObject("_links") {}
        }
        private val client = HttpClient(MockEngine { request ->
            val path = request.url.encodedPath.removePrefix("/wiki/api/v2/")
            val parts = path.split('/')
            val id = parts[1]
            if ("-comments" in path) commentReads++
            if (parts[0] == "pages" && "-comments" in path) pageCommentReads.merge(id, 1, Int::plus)
            val responseHeaders = headersOf(HttpHeaders.ContentType, "application/json")
            overrideStatus(path)?.let { return@MockEngine respond("{}", it, responseHeaders) }
            if (path == unavailablePath) return@MockEngine respond("{}", HttpStatusCode.ServiceUnavailable, responseHeaders)
            if (parts[0] == "folders" || parts[0] == "pages" && id !in pages)
                return@MockEngine respond("{}", HttpStatusCode.NotFound, responseHeaders)
            if (parts[0] == "pages" && id in denied) return@MockEngine respond("{}", HttpStatusCode.Forbidden, responseHeaders)
            val result = when {
                parts[0] == "pages" && parts.size == 2 -> pages.getValue(id).json(request.url.parameters["body-format"] == "storage")
                parts[0] == "pages" && parts[2] == "direct-children" -> listing(pages.values.filter {
                    it.parentId == id && it.id !in denied
                }.map { it.json(false) })
                parts[0] == "pages" && parts[2] in setOf("inline-comments", "footer-comments") -> listing(comments.filter {
                    it.page == id && it.parent == null && parts[2] == "${it.kind}-comments"
                }.map { it.json() })
                parts[0] in setOf("inline-comments", "footer-comments") && parts[2] == "children" -> listing(comments.filter {
                    it.parent == id && parts[0] == "${it.kind}-comments"
                }.map { it.json() })
                else -> error("Unexpected fixture endpoint $path")
            }
            if (request.url.parameters["body-format"] == "storage" && parts[0] == "pages" && parts.size == 2) afterStorage(id)
            respond(result.toString(), HttpStatusCode.OK, responseHeaders)
        })
        private val remote = ConfluenceRemote(JiraConfig(origin, "fixture@example.test", token), client)
        val mirror = ContextMirror(root, remote)
        override fun close() { remote.close(); client.close() }
    }

    @Test fun `initial complete snapshot is quiet and admitted row changes are sequenced`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            assertFailsWith<IllegalArgumentException> { mirror.sync(config, "pages", listOf("211")) }
            assertFalse(Files.exists(feedPath), "Partial publication cannot establish credential admission")
            mirror.sync(config)
            val baseline = json(feedPath)
            assertEquals(setOf("epoch", "head", "events"), baseline.keys)
            assertTrue(baseline.getValue("epoch").jsonPrimitive.content.matches(Regex("[0-9a-f]{32}")))
            assertEquals(emptyList(), events(baseline))

            provider.pages.getValue("211").body = "<p>Same-version native storage update</p>"
            mirror.sync(config, "pages", listOf("211"))
            provider.pages.getValue("211").title = "Renamed private title"
            provider.pages["212"] = Page("212")
            mirror.sync(config)
            val updated = json(feedPath)
            assertEquals(baseline["epoch"], updated["epoch"])
            assertEquals(listOf(notice(1, "updated", "211"), notice(2, "updated", "211"), notice(3, "created", "212")), events(updated))
            assertFalse(updated.toString().contains("private", ignoreCase = true))

            provider.denied += "211"
            mirror.sync(config)
            assertEquals(listOf(notice(3, "created", "212"), notice(4, "scope")), events(json(feedPath)))
            mirror.sync(config)
            assertEquals(4L, json(feedPath).getValue("head").jsonPrimitive.long)
            provider.pages.getValue("212").body = "<p>Updated while sibling access is denied</p>"
            mirror.refreshAdmittedBodies(config, listOf("212"))
            assertEquals(notice(5, "updated", "212"), events(json(feedPath)).last())
            assertEquals(JsonArray(listOf(JsonPrimitive("211"))), manifest().getValue("unavailable"))
            provider.denied.clear()
            mirror.sync(config)
            assertEquals(notice(6, "updated", "211"), events(json(feedPath)).last())
        }
    }

    @Test fun `foreign credential partial writers wait for full inventory and narrowed admission excludes old bodies`() = runBlocking {
        Provider().use { active ->
            active.mirror.sync(config)
            val baseline = manifest()
            Provider("rotated-token").use { rotated ->
                assertFailsWith<IllegalArgumentException> { rotated.mirror.refreshAdmittedBodies(config, listOf("211")) }
                assertFailsWith<IllegalArgumentException> { rotated.mirror.publishVerified(config, rotated.pages.getValue("211").native()) }
                rotated.mirror.pollComments(config, listOf("211"), 0)
                assertEquals(0, rotated.commentReads, "Foreign watcher must not select targets from another credential's admission")
                assertEquals(baseline, manifest())
                rotated.denied += "211"
                rotated.mirror.sync(config)
                assertNotEquals(baseline["credentialBinding"], manifest()["credentialBinding"])
                assertEquals(JsonArray(listOf(JsonPrimitive("211"))), manifest()["unavailable"])
                assertFalse("211" in WikiChanges.admitted(manifest()))
                assertEquals(manifest()["credentialBinding"], json(control.resolve("state.json"))["credentialBinding"])
            }
        }
    }

    @Test fun `retired credential cannot overwrite active denial marker or late comment checkpoint`() = runBlocking {
        Provider().use { retired ->
            retired.mirror.sync(config)
            val sent = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            retired.overrideStatus = { path ->
                if (path == "pages/211/footer-comments") {
                    sent.complete(Unit); release.await()
                }
                null
            }
            val watch = async { retired.mirror.pollComments(config, listOf("211"), 0) }
            try {
                withTimeout(5_000) { sent.await() }
                Provider("rotated-token").use { active ->
                    active.mirror.sync(config)
                    val admitted = manifest()
                    active.mirror.recordNoticeRootDenial(config)
                    val marker = Files.readString(denialPath)
                    retired.mirror.recordNoticeRootDenial(config)
                    assertEquals(marker, Files.readString(denialPath))
                    release.complete(Unit); watch.await()
                    assertEquals(admitted, manifest(), "In-flight old watcher cannot commit under the new credential binding")
                    assertEquals(marker, Files.readString(denialPath))
                    active.mirror.sync(config)
                    assertFalse(Files.exists(denialPath))
                    retired.mirror.recordNoticeRootDenial(config)
                    assertFalse(Files.exists(denialPath), "Late retired denial cannot pause the admitted credential")
                }
            } finally { release.complete(Unit) }
        }
    }

    @Test fun `rotated full sync recovers retired partial journal before fresh admission and rejects cross bound journal`() = runBlocking {
        Provider().use { retired ->
            retired.mirror.sync(config)
            val oldBinding = manifest().getValue("credentialBinding")
            val baseline = json(feedPath)
            val page = retired.pages.getValue("211")
            val before = page.native()
            page.body = "<p>Retired credential verified storage</p>"
            Files.delete(feedPath); Files.createDirectory(feedPath)
            assertFailsWith<IOException> { retired.mirror.publishVerified(config, page.native(), before) }
            val journal = json(control.resolve("transaction.json"))
            assertNotNull(journal["files"], "Exercise an actual interrupted partial publication")
            val staged = control.resolve(journal.getValue("stage").jsonPrimitive.content).resolve("manifest.json")
            val stagedManifest = json(staged)
            Files.delete(feedPath); ContextFiles.atomic(feedPath, baseline.toString())
            Provider("rotated-token").use { active ->
                active.pages.getValue("211").body = page.body
                val newBinding = JsonPrimitive(KnowledgeSettings.credentialFingerprint(JiraConfig(origin, "fixture@example.test", "rotated-token")))
                ContextFiles.atomic(staged, JsonObject(stagedManifest + ("credentialBinding" to newBinding)).toString())
                assertFailsWith<IllegalArgumentException> { active.mirror.sync(config) }
                assertEquals(oldBinding, manifest()["credentialBinding"], "A partial journal cannot switch credential authority")
                ContextFiles.atomic(staged, stagedManifest.toString())
                var freshInventory = false
                active.overrideStatus = { path ->
                    if (path == "pages/210/direct-children") {
                        freshInventory = true
                        assertFalse(Files.exists(control.resolve("transaction.json")), "Local recovery completes before fresh inventory")
                        assertEquals(oldBinding, manifest()["credentialBinding"], "Recovered data retains the retired binding until full admission")
                    }
                    null
                }
                active.mirror.sync(config)
                assertTrue(freshInventory)
                assertEquals(newBinding, manifest()["credentialBinding"])
                assertEquals(newBinding, json(control.resolve("state.json"))["credentialBinding"])
                assertFalse(Files.exists(control.resolve("transaction.json")))
            }
        }
    }

    @Test fun `verified write recovers its original event after interrupted feed publication`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            val baseline = json(feedPath)
            val page = provider.pages.getValue("211")
            val before = page.native()
            page.body = "<p>Same-version verified write</p>"
            Files.delete(feedPath)
            Files.createDirectory(feedPath)
            assertFailsWith<IOException> { mirror.publishVerified(config, page.native(), before) }
            val committed = manifest().getValue(WikiChanges.noticesKey).jsonObject
            assertEquals(baseline["epoch"], committed["epoch"])
            assertEquals(listOf(notice(1, "updated", "211")), events(committed))
            assertTrue(Files.exists(control.resolve("transaction.json")))
            Files.delete(feedPath)
            ContextFiles.atomic(feedPath, baseline.toString())
            provider.denied += "210"
            assertFailsWith<ContextRemoteException> { mirror.sync(config) }
            assertFailsWith<ContextRemoteException> {
                mirror.publishVerified(config, page.native(), before) {
                    throw ContextRemoteException("http_403", "Fixture denial")
                }
            }
            assertEquals(baseline, json(feedPath))
            assertTrue(Files.exists(control.resolve("transaction.json")))
            assertTrue(Files.isRegularFile(denialPath))
            provider.denied.clear()
            provider.unavailablePath = "pages/210"
            assertFailsWith<ContextRemoteException> { mirror.sync(config) }
            assertTrue(Files.isRegularFile(denialPath))
            assertTrue(Files.exists(control.resolve("transaction.json")))
            provider.unavailablePath = null
            mirror.sync(config)
            assertFalse(Files.exists(control.resolve("transaction.json")))
            assertFalse(Files.exists(denialPath))
            mirror.publishVerified(config, page.native(), before)
            mirror.sync(config)
            assertEquals(committed, json(feedPath))
        }
    }

    @Test fun `comment observations track replies and resolution and preserve baseline after failure`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            provider.pages["212"] = Page("212")
            mirror.sync(config)
            mirror.pollComments(config, listOf("211", "212"), 0)
            val baseline = manifest()
            assertEquals("ready", WikiChanges.commentStatus(baseline, listOf("211", "212")))
            assertEquals(emptyList(), events(json(feedPath)))
            assertFalse(baseline.toString().contains("Private comment"))

            provider.comments += Comment("311", "211", "footer", "310")
            provider.comments += Comment("330", "212", "footer")
            val previousReads = provider.commentReads
            mirror.pollComments(config, listOf("211", "212"), 300)
            assertNotNull(CorpusFileLock.tryAcquire(control.resolve("comments.lock"))).use {
                mirror.pollComments(config, listOf("211", "212"), 0)
            }
            assertEquals(previousReads, provider.commentReads)
            provider.unavailablePath = "pages/212/inline-comments"
            mirror.pollComments(config, listOf("211", "212"), 0)
            val failed = manifest()
            assertEquals("unavailable", WikiChanges.commentStatus(failed, listOf("211", "212")))
            fun fingerprint(manifest: JsonObject) = manifest["wikiCommentWatch"]?.jsonObject?.get("212")?.jsonObject?.get("fingerprint")
            assertNotNull(fingerprint(baseline))
            assertEquals(fingerprint(baseline), fingerprint(failed))
            assertEquals(listOf(notice(1, "comments", "211")), events(json(feedPath)))

            provider.unavailablePath = null
            mirror.pollComments(config, listOf("211", "212"), 0)
            assertEquals(listOf(notice(1, "comments", "211"), notice(2, "comments", "212")), events(json(feedPath)))
            provider.comments.single { it.id == "320" }.resolution = "resolved"
            mirror.pollComments(config, listOf("211", "212"), 0)
            mirror.pollComments(config, listOf("211", "212"), 0)
            assertEquals(listOf(notice(1, "comments", "211"), notice(2, "comments", "212"), notice(3, "comments", "211")), events(json(feedPath)))
            provider.denied += "211"
            mirror.sync(config)
            mirror.pollComments(config, listOf("211", "212"), 0)
            assertEquals(listOf(notice(2, "comments", "212"), notice(4, "scope")), events(json(feedPath)))
        }
    }

    @Test fun `projects preserve each other's comment baselines and share cadence on overlapping pages`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            provider.pages["212"] = Page("212")
            provider.pages["213"] = Page("213")
            mirror.sync(config)
            mirror.pollComments(config, listOf("211", "213"), 300)
            val commonReads = provider.pageCommentReads.getValue("213")
            mirror.pollComments(config, listOf("212", "213"), 300)
            assertEquals(commonReads, provider.pageCommentReads.getValue("213"))
            assertEquals("ready", WikiChanges.commentStatus(manifest(), listOf("211", "213")))
            assertEquals("ready", WikiChanges.commentStatus(manifest(), listOf("212", "213")))
            assertTrue(events(json(feedPath)).isEmpty())

            provider.comments += Comment("331", "211", "footer")
            provider.comments += Comment("332", "212", "footer")
            provider.comments += Comment("333", "213", "footer")
            mirror.pollComments(config, listOf("211", "213"), 0)
            mirror.pollComments(config, listOf("212", "213"), 0)
            assertEquals(listOf(notice(1, "comments", "211"), notice(2, "comments", "213"), notice(3, "comments", "212")),
                events(json(feedPath)))
        }
    }

    @Test fun `deep authorized page can establish a comment baseline among ten watched pages`() = runBlocking {
        Provider().use { provider ->
            for (id in 212..219) provider.pages[id.toString()] = Page(id.toString())
            for (id in 220..232) {
                provider.pages[id.toString()] = Page(id.toString(), if (id == 220) "210" else (id - 1).toString())
            }
            val watched = (211..219).map(Int::toString) + "232"
            provider.mirror.sync(config)
            provider.mirror.pollComments(config, watched, 300)
            assertEquals("ready", WikiChanges.commentStatus(manifest(), watched))
            assertEquals(2, provider.pageCommentReads["232"])
        }
    }

    @Test fun `shared request budget rotates interrupted pages ahead of completed pages`() = runBlocking {
        Provider().use { provider ->
            val watched = (212..215).map(Int::toString)
            watched.forEachIndexed { index, page ->
                provider.pages[page] = Page(page)
                repeat(130) { provider.comments += Comment((1000 + index * 200 + it).toString(), page, "footer") }
            }
            provider.mirror.sync(config)
            provider.mirror.pollComments(config, watched, 0)
            assertEquals("ready", WikiChanges.commentStatus(manifest(), listOf("212", "213", "214")))
            assertNull(WikiChanges.commentAttemptedAt(manifest(), "215"))
            provider.mirror.pollComments(config, watched, 0)
            assertEquals("ready", WikiChanges.commentStatus(manifest(), watched))
            assertTrue(events(json(feedPath)).isEmpty())
        }
    }

    @Test fun `watcher persists confirmed root denial and distinguishes child or transient failure`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            provider.pages.getValue("211").version++
            mirror.sync(config)
            val feed = json(feedPath)
            assertEquals(listOf(notice(1, "updated", "211", 2)), events(feed))

            provider.denied += "211"
            mirror.pollComments(config, listOf("211"), 0)
            assertFalse(Files.exists(denialPath))
            assertEquals("unavailable", WikiChanges.commentStatus(manifest(), listOf("211")))

            provider.denied.clear()
            provider.unavailablePath = "pages/210"
            mirror.pollComments(config, listOf("211"), 0)
            assertFalse(Files.exists(denialPath))

            var rootReads = 0
            provider.overrideStatus = { path ->
                if (path != "pages/210") null else if (++rootReads == 1) HttpStatusCode.Forbidden
                else HttpStatusCode.ServiceUnavailable
            }
            mirror.pollComments(config, listOf("211"), 0)
            assertTrue(Files.isRegularFile(denialPath))
            assertEquals(feed, json(feedPath))
        }
    }

    @Test fun `authorized inventory clears only the denial recorded before it began`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            mirror.recordNoticeRootDenial(config)
            val earlier = Files.readString(denialPath)
            val sent = CompletableDeferred<Unit>()
            val release = CompletableDeferred<Unit>()
            val first = AtomicBoolean(true)
            provider.overrideStatus = { path ->
                if (path == "pages/210" && first.compareAndSet(true, false)) {
                    sent.complete(Unit)
                    release.await()
                }
                null
            }
            val inventory = async { mirror.sync(config) }
            val newer = try {
                withTimeout(5_000) { sent.await() }
                mirror.recordNoticeRootDenial(config)
                Files.readString(denialPath)
            } finally { release.complete(Unit) }
            assertNotEquals(earlier, newer)
            inventory.await()
            assertEquals(newer, Files.readString(denialPath))
            mirror.sync(config)
            assertFalse(Files.exists(denialPath))
        }
    }

    @Test fun `root denial during body verification pauses the previous feed`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            provider.pages.getValue("211").version++
            mirror.sync(config)
            val feed = json(feedPath)
            provider.pages.getValue("211").version++
            provider.afterStorage = { if (it == "211") provider.denied += "210" }
            assertEquals("http_403", assertFailsWith<ContextRemoteException> { mirror.sync(config) }.code)
            assertTrue(Files.isRegularFile(denialPath))
            assertEquals(feed, json(feedPath))
        }
    }

    @Test fun `one denied root read during final inventory verification pauses the previous feed`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            provider.pages.getValue("211").version++
            mirror.sync(config)
            val feed = json(feedPath)
            assertEquals(listOf(notice(1, "updated", "211", 2)), events(feed))

            var leafEnumerated = false
            var deniedOnce = false
            provider.overrideStatus = { path ->
                if (path == "pages/211/direct-children") leafEnumerated = true
                if (path == "pages/210" && leafEnumerated && !deniedOnce) {
                    deniedOnce = true
                    HttpStatusCode.Forbidden
                } else null
            }
            assertEquals("http_403", assertFailsWith<RootScopeDenial> { mirror.sync(config) }.code)
            assertTrue(deniedOnce)
            assertTrue(Files.isRegularFile(denialPath))
            assertEquals(feed, json(feedPath))
            mirror.sync(config)
            assertFalse(Files.exists(denialPath))
            assertEquals(feed, json(feedPath))
            provider.overrideStatus = { path ->
                if (path == "pages/210/direct-children") HttpStatusCode.Forbidden else null
            }
            assertEquals("http_403", assertFailsWith<RootScopeDenial> { mirror.sync(config) }.code)
            assertTrue(Files.isRegularFile(denialPath))
            assertEquals(feed, json(feedPath))
        }
    }

    @Test fun `root space and status changes pause the feed on explicit metadata evidence`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            mirror.sync(config)
            val home = provider.pages.getValue("210")
            home.spaceId = "99"
            assertEquals("scope_outside_connection", assertFailsWith<ContextRemoteException> { mirror.sync(config) }.code)
            assertTrue(Files.isRegularFile(denialPath))
            home.spaceId = "20"
            mirror.sync(config)
            assertFalse(Files.exists(denialPath))
            home.status = "archived"
            mirror.pollComments(config, listOf("211"), 0)
            assertTrue(Files.isRegularFile(denialPath))
        }
    }

    @Test fun `fresh root check distinguishes a move within home from a move outside home`() = runBlocking {
        Provider().use { provider ->
            val mirror = provider.mirror
            val nested = config.copy(knowledgeRootPageId = "211")
            val boundary = provider.pages.getValue("211")
            provider.pages["212"] = Page("212", "211")
            provider.pages["218"] = Page("218")
            provider.pages["299"] = Page("299", null)
            mirror.sync(nested)
            provider.pages.getValue("212").version++
            provider.afterStorage = { if (it == "212") boundary.parentId = "218" }
            assertEquals("scope_metadata_changed", assertFailsWith<ContextRemoteException> { mirror.sync(nested) }.code)
            assertFalse(Files.exists(denialPath))
            provider.afterStorage = {}
            mirror.sync(nested)
            provider.pages.getValue("212").version++
            provider.afterStorage = { if (it == "212") boundary.parentId = "299" }
            assertEquals("scope_metadata_changed", assertFailsWith<ContextRemoteException> { mirror.sync(nested) }.code)
            assertTrue(Files.isRegularFile(denialPath))
        }
    }

    @Test fun `same JVM contention cannot release a lock held against another process`() {
        val lock = root.resolve("corpus.lock")
        val source = root.resolve("ProbeLock.java")
        Files.writeString(source, """
            import java.nio.channels.FileChannel;
            import java.nio.file.Path;
            import static java.nio.file.StandardOpenOption.WRITE;
            class ProbeLock {
                public static void main(String[] args) throws Exception {
                    try (var channel = FileChannel.open(Path.of(args[0]), WRITE);
                         var lease = channel.tryLock()) {
                        System.out.println(lease == null ? "busy" : "acquired");
                    }
                }
            }
        """.trimIndent())
        fun otherProcess(): String {
            val java = Path.of(System.getProperty("java.home"), "bin", "java").toString()
            val process = ProcessBuilder(java, source.toString(), lock.toString()).redirectErrorStream(true).start()
            try {
                assertTrue(process.waitFor(15, TimeUnit.SECONDS), "External record-lock probe timed out")
                val output = process.inputStream.bufferedReader().use { it.readText() }.trim()
                assertEquals(0, process.exitValue(), output)
                return output
            } finally {
                if (process.isAlive) process.destroyForcibly()
            }
        }
        assertNotNull(CorpusFileLock.tryAcquire(lock)).use {
            if ("posix" in lock.fileSystem.supportedFileAttributeViews()) {
                assertEquals(PosixFilePermissions.fromString("rw-------"), Files.getPosixFilePermissions(lock))
            }
            assertEquals("busy", otherProcess())
            assertNull(CorpusFileLock.tryAcquire(root.resolve("./corpus.lock"), shared = true))
            assertEquals("busy", otherProcess())
        }
        assertEquals("acquired", otherProcess())

        val missing = root.resolve("missing/corpus.lock")
        assertFailsWith<NoSuchFileException> { CorpusFileLock.tryAcquire(missing) }
        Files.createDirectory(missing.parent)
        assertNotNull(CorpusFileLock.tryAcquire(missing)).close()
    }

    @Test fun `one publication retains only the newest 64 notices and validates configuration`() {
        fun manifest(pages: Map<String, JsonObject>) = buildJsonObject {
            put("inventoryComplete", true); put("pages", JsonObject(pages))
        }
        val initial = manifest(emptyMap())
        val populated = manifest((100..199).associate { id -> id.toString() to buildJsonObject {
            put("type", "page"); put("status", "current"); put("version", 1)
        } })
        val result = WikiChanges.documents(initial, populated)
        WikiChanges.validate(result)
        val feed = result.getValue(WikiChanges.noticesKey).jsonObject
        assertEquals(100L, feed.getValue("head").jsonPrimitive.long)
        assertEquals(64, events(feed).size)
        assertEquals(notice(37, "created", "136"), events(feed).first())
        var watched = result
        for (id in 100..164) {
            watched = WikiChanges.comments(watched, id.toLong(), setOf(id.toString()), mapOf(id.toString() to "a".repeat(64)))
        }
        WikiChanges.validate(watched)
        assertTrue(WikiChanges.commentDue(watched, "100", 164, 1000))
        assertFalse(WikiChanges.commentDue(watched, "101", 164, 1000))
        assertEquals("ready", WikiChanges.commentStatus(watched, listOf("164")))

        val env = mapOf("ATLASSIAN_BASE_URL" to origin,
            "ATLASSIAN_EMAIL" to "fixture@example.test", "ATLASSIAN_API_TOKEN" to "fixture-token", "HOME" to root.toString())
        val project = Files.createDirectories(root.resolve("project"))
        fun settings(watch: String? = null): KnowledgeSettings {
            val value = buildJsonObject {
                put("enabled", true); put("site", origin); put("space", "COMMON"); put("spaceId", "20"); put("rootPageId", "210")
                watch?.let { put("watchCommentPages", it) }
            }
            val manifest = Files.createDirectories(project.resolve("doc/harness")).resolve("manifest.yaml")
            Files.writeString(manifest, buildJsonObject {
                put("wiki", JsonObject(value + ("backend" to JsonPrimitive("atlassian"))))
            }.toString())
            return KnowledgeSettings.fromProject(project, env)!!
        }
        assertEquals(emptyList(), settings().watchedCommentPages)
        assertEquals(listOf("211", "212"), settings("212,211").watchedCommentPages)
        for (invalid in listOf("", "211,211", "211, 212", "0", "211,", "1".repeat(33), (1..11).joinToString(","))) {
            assertFailsWith<IllegalArgumentException> { settings(invalid) }
        }
    }
}
