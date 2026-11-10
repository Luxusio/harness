package io.crscube.clitools.knowledge

import io.crscube.clitools.confluence.*
import io.crscube.clitools.mcp.McpServer
import kotlinx.coroutines.*
import kotlinx.coroutines.channels.Channel

fun main(args: Array<String>) {
    require(args.isEmpty()) { "wiki-atlassian is an MCP stdio server" }
    val environment = System.getenv().toMap()
    if (!KnowledgeSettings.enabled(environment)) {
        McpServer("atlassian", "0.1.0").start()
        return
    }
    val settings = KnowledgeSettings.fromEnvironment(environment)
    KnowledgeSettings.prepareCorpus(settings.corpusRoot)
    val remote = ConfluenceRemote(settings.credentials)
    val corpus = LocalCorpus(settings, remote)
    val native = WikiTools(settings.credentials, launchRoot = settings.corpusRoot, runtimeSettings = settings)
    native.service.localReads = corpus::execute
    val wake = Channel<Unit>(Channel.CONFLATED)
    corpus.onRepair = { wake.trySend(Unit) }
    native.service.replicaRepair = corpus::requestRepair
    val worker = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    val syncing = worker.launch {
        var authoritativeBodies = true
        while (isActive) {
            try { corpus.synchronize(authoritativeBodies) }
            catch (e: CancellationException) { throw e }
            catch (e: Exception) { corpus.syncFailed((e as? ContextRemoteException)?.code ?: "sync_failed") }
            authoritativeBodies = withTimeoutOrNull(settings.pollIntervalSeconds * 1000) { wake.receive(); false } ?: true
        }
    }
    try { McpServer("atlassian", "0.1.0", instructions = "Use the atlassian MCP to search, read and update business knowledge. A shared local read replica is synchronized automatically; do not edit replica files or duplicate remote documents in project folders.").addTools(native).addTools(corpus).start() }
    finally { runBlocking { syncing.cancelAndJoin() }; wake.close(); worker.cancel(); native.close(); remote.close() }
}
