package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.*
import io.crscube.clitools.mcp.McpServer
import kotlinx.coroutines.*
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.*

fun main(args: Array<String>) {
    val startup = when {
        args.isEmpty() -> java.nio.file.Paths.get("").toAbsolutePath()
        args.size == 2 && args[0] == "--project-root" -> try {
            java.nio.file.Paths.get(args[1]).also { require(it.isAbsolute) }
        } catch (_: Exception) { throw IllegalArgumentException("Invalid project startup directory") }
        else -> throw IllegalArgumentException("wiki-atlassian accepts only --project-root with an absolute startup directory")
    }
    val personal = if (PersonalFeedbackTools.configured()) PersonalFeedbackTools.installed() else null
    val project = try { KnowledgeProject.fromProject(startup) } catch (error: Exception) {
        if (personal == null) throw error
        McpServer("atlassian", "0.1.0", instructions = "The project wiki connection is unavailable. The separate private Harness inbox can still be read with read_personal_feedback; no project credential fallback is used.")
            .addTools(personal).addUnavailableTools(MultiSpaceKnowledge::class, "The project wiki connection is unavailable; correct its configuration or authentication separately from the private inbox.")
            .start()
        return
    }
    if (project == null) {
        val server = McpServer("atlassian", "0.1.0",
            instructions = if (personal != null) "Use read_personal_feedback for the privately configured Harness improvement inbox; do not use project credentials or another connector." else null)
        personal?.let(server::addTools)
        server.start()
        return
    }
    val remote = ConfluenceRemote(project.credentials)
    val documents = MultiSpaceKnowledge(project, remote)
    val worker = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val permit = kotlinx.coroutines.sync.Semaphore(4)
    val jobs = project.selections.map { selection ->
        val key = selection.getValue("key").jsonPrimitive.content
        worker.launch {
            var authoritativeBodies = true
            while (isActive) {
                permit.withPermit {
                    try { documents.synchronize(key, authoritativeBodies) }
                    catch (e: CancellationException) { throw e }
                    catch (_: Exception) { /* Per-space status retains a sanitized failure; siblings continue. */ }
                }
                authoritativeBodies = documents.awaitNext(key)
            }
        }
    }
    try {
        val server = McpServer("atlassian", "0.1.0", instructions = "Use atlassian MCP for the configured site's selected spaces. Search results and connection_status identify spaces. Choose an explicit space or native parent for new documents; never guess an ambiguous destination. Shared read replicas synchronize automatically; do not edit replica files. Use read_personal_feedback for the separate private Harness inbox when that tool is available.")
            .addTools(WikiTools(documents::execute)).addTools(documents)
        personal?.let(server::addTools)
        server.start()
    } finally {
        runBlocking { jobs.forEach { it.cancel() }; jobs.joinAll() }
        worker.cancel(); documents.close(); remote.close()
    }
}
