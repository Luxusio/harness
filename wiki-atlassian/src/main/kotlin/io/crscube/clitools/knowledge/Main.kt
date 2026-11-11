package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.*
import io.crscube.clitools.mcp.McpServer
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel

fun main(args: Array<String>) {
    val startup = when {
        args.isEmpty() -> java.nio.file.Paths.get("").toAbsolutePath()
        args.size == 2 && args[0] == "--project-root" -> try {
            java.nio.file.Paths.get(args[1]).also { require(it.isAbsolute) }
        } catch (_: Exception) { throw IllegalArgumentException("Invalid project startup directory") }
        else -> throw IllegalArgumentException("wiki-atlassian accepts only --project-root with an absolute startup directory")
    }
    val settings = KnowledgeSettings.fromProject(startup)
    if (settings == null) {
        McpServer("atlassian", "0.1.0").start()
        return
    }
    settings.prepareCorpus()
    val remote = ConfluenceRemote(settings.credentials)
    val corpus = LocalCorpus(settings, remote)
    val native = WikiTools(settings.credentials, launchRoot = settings.corpusRoot, runtimeSettings = settings)
    native.service.localReads = corpus::execute
    val wake = Channel<Unit>(Channel.CONFLATED)
    corpus.onRepair = { wake.trySend(Unit) }
    native.service.replicaRepair = corpus::requestRepair
    val worker = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val commentTicks = Channel<Unit>(Channel.CONFLATED)
    val watching = settings.watchedCommentPages.takeIf { it.isNotEmpty() }?.let { pageIds ->
        val mirror = ContextMirror(settings.corpusRoot, remote)
        worker.launch {
            while (isActive) {
                try {
                    val config = ContextConfig.load(settings.corpusRoot, settings)!!
                    mirror.pollComments(config, pageIds, settings.pollIntervalSeconds)
                } catch (e: CancellationException) { throw e }
                catch (_: Exception) { System.err.println("Atlassian comment watch could not publish its checkpoint") }
                withTimeoutOrNull(settings.pollIntervalSeconds * 1000) { commentTicks.receive() }
            }
        }
    }
    val syncing = worker.launch {
        var authoritativeBodies = true
        while (isActive) {
            try {
                corpus.synchronize(authoritativeBodies)
                if (watching != null) commentTicks.trySend(Unit)
            }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { corpus.syncFailed((e as? ContextRemoteException)?.code ?: "sync_failed") }
            authoritativeBodies = withTimeoutOrNull(settings.pollIntervalSeconds * 1000) { wake.receive(); false } ?: true
        }
    }
    try { McpServer("atlassian", "0.1.0", instructions = "Use the atlassian MCP to search, read and update business knowledge. A shared local read replica is synchronized automatically; do not edit replica files or duplicate remote documents in project folders.").addTools(native).addTools(corpus).start() }
    finally {
        runBlocking { syncing.cancelAndJoin(); watching?.cancelAndJoin() }
        wake.close()
        commentTicks.close()
        worker.cancel()
        native.close()
        remote.close()
    }
}
