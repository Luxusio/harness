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
    val project = KnowledgeProject.fromProject(startup)
    if (project == null) {
        McpServer("atlassian", "0.1.0").start()
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
        McpServer("atlassian", "0.1.0", instructions = "Use atlassian MCP for the configured site's selected spaces. Search results and connection_status identify spaces. Choose an explicit space or native parent for new documents; never guess an ambiguous destination. Shared read replicas synchronize automatically; do not edit replica files.")
            .addTools(WikiTools(documents::execute)).addTools(documents).start()
    } finally {
        runBlocking { jobs.forEach { it.cancel() }; jobs.joinAll() }
        worker.cancel(); documents.close(); remote.close()
    }
}
