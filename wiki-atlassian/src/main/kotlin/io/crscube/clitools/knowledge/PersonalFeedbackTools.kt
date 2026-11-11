package io.crscube.clitools.knowledge

import io.crscube.clitools.mcp.McpParam
import io.crscube.clitools.mcp.McpTool
import kotlinx.serialization.json.*
import java.nio.file.Files
import java.nio.file.LinkOption.NOFOLLOW_LINKS
import java.nio.file.Path
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit
import kotlin.concurrent.thread

/** Explicit reads only. The canonical helper owns private configuration and authentication. */
class PersonalFeedbackTools(private val python: Path?, private val helper: Path?) {
    @McpTool(name = "read_personal_feedback", readOnly = true,
        description = "Read the personally configured Harness improvement inbox, separately from project wiki credentials. Returns bounded native page/comment content and scoped read/next/replies cursors. No arbitrary destination, writes, acknowledgement or automatic fetching.")
    fun read(@McpParam(description = "Comments per page, 1..25", required = false) limit: Int = 10,
             @McpParam(description = "Unchanged cursor from a returned read, next or replies locator", required = false) cursor: String? = null): String {
        require(limit in 1..25 && (cursor == null || cursor.length in 1..8192)) { "Use limit 1..25 and an unchanged returned cursor" }
        val request = buildJsonObject { put("limit", limit); cursor?.let { put("cursor", it) } }
        if (python == null || helper == null || !python.isAbsolute || !helper.isAbsolute ||
            !Files.isRegularFile(python) || !Files.isRegularFile(helper, NOFOLLOW_LINKS)) return unavailable()
        var process: Process? = null
        return try {
            val owned = ProcessBuilder(python.toString(), "-I", "-B", "-X", "utf8", helper.toString(), "read")
                .directory(helper.parent.toFile()).redirectError(ProcessBuilder.Redirect.DISCARD).start()
            process = owned
            val output = CompletableFuture<ByteArray>()
            thread(isDaemon = true, name = "personal-feedback-output") {
                try { output.complete(owned.inputStream.readNBytes(MAX_OUTPUT + 1)) }
                catch (e: Exception) { output.completeExceptionally(e) }
            }
            owned.outputStream.use { it.write(request.toString().toByteArray(Charsets.UTF_8)) }
            val bytes = output.get(90, TimeUnit.SECONDS)
            if (bytes.size > MAX_OUTPUT || !owned.waitFor(5, TimeUnit.SECONDS)) return unavailable()
            val result = Json.parseToJsonElement(bytes.toString(Charsets.UTF_8)) as? JsonObject ?: return unavailable()
            val status = result["status"]?.jsonPrimitive?.content
            if (status !in setOf("read", "unconfigured", "unavailable") || owned.exitValue() !in 0..1 ||
                status == "read" && owned.exitValue() != 0) return unavailable()
            result.toString()
        } catch (e: Exception) {
            if (e is InterruptedException) Thread.currentThread().interrupt()
            unavailable()
        }
        finally {
            process?.let { owned ->
                runCatching { owned.descendants().use { children -> children.forEach { child -> child.destroyForcibly() } } }
                runCatching { owned.destroyForcibly(); owned.waitFor(5, TimeUnit.SECONDS) }
                runCatching { owned.inputStream.close() }
                runCatching { owned.outputStream.close() }
            }
        }
    }

    companion object {
        private const val MAX_OUTPUT = 2 * 1024 * 1024
        fun configured(): Boolean {
            val home = System.getenv("HOME") ?: System.getProperty("user.home")
            return Files.exists(Path.of(home, ".config", "harness", "feedback.yaml"), NOFOLLOW_LINKS)
        }
        fun installed(): PersonalFeedbackTools = PersonalFeedbackTools(
            System.getProperty("wiki.atlassian.feedbackPython")?.let(Path::of),
            System.getProperty("wiki.atlassian.feedbackHelper")?.let(Path::of))
        private fun unavailable(): String = """{"status":"unavailable","message":"Personal feedback reader unavailable; start the installed wiki-atlassian launcher and check its private setup. No project credentials or alternative connector were used."}"""
    }
}
