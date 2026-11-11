package io.crscube.clitools.knowledge

import io.crscube.clitools.mcp.McpException
import io.crscube.clitools.mcp.ToolRegistry
import kotlinx.serialization.json.*
import org.junit.jupiter.api.Test
import org.junit.jupiter.api.io.TempDir
import java.nio.file.Files
import java.nio.file.Path
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicReference
import kotlin.test.*

class PersonalFeedbackToolsTest {
    @TempDir lateinit var temporary: Path

    private val python: Path by lazy {
        val candidates = listOf("python3", "python")
        candidates.firstNotNullOfOrNull { command ->
            runCatching {
                val process = ProcessBuilder(command, "-I", "-c", "import sys; print(sys.executable)")
                    .redirectError(ProcessBuilder.Redirect.DISCARD).start()
                try {
                    check(process.waitFor(10, TimeUnit.SECONDS))
                    check(process.exitValue() == 0)
                    Path.of(process.inputStream.bufferedReader().readText().trim()).also { check(it.isAbsolute) }
                } finally {
                    if (process.isAlive) process.destroyForcibly()
                    process.inputStream.close()
                }
            }.getOrNull()
        } ?: error("Python is required to exercise the private feedback subprocess")
    }

    private fun helper(body: String): Path = Files.createTempFile(temporary, "feedback-", ".py")
        .also { Files.writeString(it, body.trimIndent() + "\n") }

    private fun result(tools: PersonalFeedbackTools, limit: Int = 10, cursor: String? = null): JsonObject =
        Json.parseToJsonElement(tools.read(limit, cursor)).jsonObject

    private fun assertUnavailable(value: JsonObject, sentinel: String? = null) {
        assertEquals("unavailable", value.getValue("status").jsonPrimitive.content)
        sentinel?.let { assertFalse(value.toString().contains(it)) }
    }

    @Test fun `MCP surface exposes only bounded read inputs and read-only annotations`() {
        val registry = ToolRegistry()
        registry.register(PersonalFeedbackTools(null, null))
        val tool = registry.listTools().single()
        assertEquals("read_personal_feedback", tool.getValue("name").jsonPrimitive.content)
        val schema = tool.getValue("inputSchema").jsonObject
        assertEquals(setOf("limit", "cursor"), schema.getValue("properties").jsonObject.keys)
        assertEquals("integer", schema.getValue("properties").jsonObject.getValue("limit").jsonObject.getValue("type").jsonPrimitive.content)
        assertEquals("string", schema.getValue("properties").jsonObject.getValue("cursor").jsonObject.getValue("type").jsonPrimitive.content)
        assertEquals(JsonPrimitive(false), schema.getValue("additionalProperties"))
        assertTrue(schema["required"] == null || schema.getValue("required").jsonArray.isEmpty())
        val annotations = tool.getValue("annotations").jsonObject
        assertEquals(JsonPrimitive(true), annotations.getValue("readOnlyHint"))
        assertEquals(JsonPrimitive(false), annotations.getValue("destructiveHint"))
        assertEquals(JsonPrimitive(true), annotations.getValue("idempotentHint"))
        assertFailsWith<McpException> {
            registry.callTool("read_personal_feedback", buildJsonObject { put("site", "https://unrelated.invalid") })
        }
    }

    @Test fun `invalid input is rejected before a helper starts`() {
        val marker = temporary.resolve("started")
        val script = helper("""
            from pathlib import Path
            Path(${JsonPrimitive(marker.toString())}).write_text('started')
            print('{"status":"read"}')
        """)
        val tools = PersonalFeedbackTools(python, script)
        for (limit in listOf(-1, 0, 26, Int.MAX_VALUE)) {
            assertFailsWith<IllegalArgumentException> { tools.read(limit) }
        }
        for (cursor in listOf("", "x".repeat(8193))) {
            assertFailsWith<IllegalArgumentException> { tools.read(cursor = cursor) }
        }
        assertFalse(Files.exists(marker))
    }

    @Test fun `missing and unpinned transports fail without searching the working directory`() {
        val script = helper("print('{\"status\":\"read\"}')")
        for (tools in listOf(
            PersonalFeedbackTools(null, script),
            PersonalFeedbackTools(python, null),
            PersonalFeedbackTools(Path.of("python3"), script),
            PersonalFeedbackTools(python, Path.of("personal_feedback.py")),
            PersonalFeedbackTools(python, temporary.resolve("missing.py")),
            PersonalFeedbackTools(python, temporary)
        )) assertUnavailable(result(tools))
    }

    @Test fun `helper receives fixed read action and JSON stdin under isolated Python`() {
        // Even a sibling import with the stdlib module's name must be ignored.
        Files.writeString(temporary.resolve("json.py"), "raise RuntimeError('project-import-poison')\n")
        val script = helper("""
            import json, os, sys
            value = json.load(sys.stdin)
            print(json.dumps({'status':'read', 'argv':sys.argv[1:], 'request':value,
                              'isolated':sys.flags.isolated, 'bytecode':sys.dont_write_bytecode,
                              'cwd':os.getcwd(), 'path':sys.path}))
        """)
        val cursor = "literal-'\";\u0024(shell)-cursor"
        val response = result(PersonalFeedbackTools(python, script), 7, cursor)
        assertEquals("read", response.getValue("status").jsonPrimitive.content)
        assertEquals(listOf("read"), response.getValue("argv").jsonArray.map { it.jsonPrimitive.content })
        assertEquals(buildJsonObject { put("limit", 7); put("cursor", cursor) }, response.getValue("request"))
        assertEquals(1, response.getValue("isolated").jsonPrimitive.int)
        assertEquals(JsonPrimitive(true), response.getValue("bytecode"))
        assertEquals(script.parent.toString(), response.getValue("cwd").jsonPrimitive.content)
        assertFalse(response.getValue("path").jsonArray.any { it.jsonPrimitive.content == script.parent.toString() })
        assertFalse(response.toString().contains("project-import-poison"))
    }

    @Test fun `malformed output nonzero read and raw stderr never become a successful read`() {
        val sentinel = "private-protocol-error-sentinel"
        val scripts = listOf(
            "import sys; sys.stderr.write('$sentinel' * 10000); print('$sentinel')",
            "import sys; print('{\"status\":\"read\",\"secret\":\"$sentinel\"}'); sys.exit(7)",
            "import sys; print('{\"status\":\"read\",\"secret\":\"$sentinel\"}'); sys.exit(1)",
            "print('[\"$sentinel\"]')",
            "print('{\"status\":\"sent\",\"secret\":\"$sentinel\"}')"
        )
        for (body in scripts) assertUnavailable(result(PersonalFeedbackTools(python, helper(body))), sentinel)
    }

    @Test fun `controlled unavailable and unconfigured helper results stay truthful`() {
        for (status in listOf("unavailable", "unconfigured")) {
            val script = helper("""
                import json, sys
                print(json.dumps({'status': '$status', 'message':'synthetic setup unavailable'}))
                sys.exit(1)
            """)
            val response = result(PersonalFeedbackTools(python, script))
            assertEquals(status, response.getValue("status").jsonPrimitive.content)
            assertEquals("synthetic setup unavailable", response.getValue("message").jsonPrimitive.content)
        }
    }

    @Test fun `stdout overflow stops the owned helper without echoing its payload`() {
        val script = helper("""
            import sys, time
            sys.stdout.write('private-output-sentinel' + 'x' * (2 * 1024 * 1024 + 1))
            sys.stdout.flush()
            time.sleep(60)
        """)
        val start = System.nanoTime()
        assertUnavailable(result(PersonalFeedbackTools(python, script)), "private-output-sentinel")
        assertTrue(TimeUnit.NANOSECONDS.toSeconds(System.nanoTime() - start) < 15,
            "Overflow must not wait for the helper's 60-second sleep")
    }

    @Test fun `interruption stops and reaps the owned helper and terminates its descendant`() {
        val pids = temporary.resolve("owned-pids.json")
        val script = helper("""
            import json, os, signal, subprocess, sys, time
            from pathlib import Path
            if hasattr(signal, 'SIGCHLD'):
                signal.signal(signal.SIGCHLD, signal.SIG_IGN)
            child = subprocess.Popen([sys.executable, '-I', '-c', 'import time; time.sleep(60)'],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            target = Path(${JsonPrimitive(pids.toString())})
            pending = target.with_suffix('.pending')
            pending.write_text(json.dumps([os.getpid(), child.pid]))
            os.replace(pending, target)
            time.sleep(60)
        """)
        val response = AtomicReference<String>()
        val failure = AtomicReference<Throwable>()
        val caller = Thread {
            try { response.set(PersonalFeedbackTools(python, script).read()) }
            catch (error: Throwable) { failure.set(error) }
        }
        var owned = emptyList<Long>()
        caller.start()
        try {
            val ready = System.nanoTime() + TimeUnit.SECONDS.toNanos(10)
            while (!Files.exists(pids) && caller.isAlive && System.nanoTime() < ready) Thread.sleep(25)
            assertTrue(Files.exists(pids), "Synthetic helper did not publish its owned process identities")
            owned = Json.parseToJsonElement(Files.readString(pids)).jsonArray.map { it.jsonPrimitive.long }
            assertTrue(owned.all { ProcessHandle.of(it).map(ProcessHandle::isAlive).orElse(false) })
            caller.interrupt()
            caller.join(10000)
            assertFalse(caller.isAlive, "The interrupted read must finish without the normal 90-second deadline")
            assertNull(failure.get())
            assertUnavailable(Json.parseToJsonElement(assertNotNull(response.get())).jsonObject)
            val stopped = System.nanoTime() + TimeUnit.SECONDS.toNanos(5)
            while (owned.any { ProcessHandle.of(it).map(ProcessHandle::isAlive).orElse(false) } && System.nanoTime() < stopped) Thread.sleep(25)
            assertTrue(owned.none { ProcessHandle.of(it).map(ProcessHandle::isAlive).orElse(false) },
                "Owned helper and descendant must no longer be alive")
        } finally {
            owned.forEach { pid -> ProcessHandle.of(pid).ifPresent { it.destroyForcibly() } }
            caller.interrupt()
            caller.join(5000)
        }
    }
}
