package io.crscube.clitools.mcp

import kotlinx.serialization.json.*
import java.io.BufferedReader
import java.io.InputStreamReader
import java.io.PrintStream
import kotlin.reflect.KClass

class McpServer(
    private val serverName: String,
    private val serverVersion: String,
    private val callGuard: ((String, JsonObject) -> Unit)? = null,
    private val instructions: String? = null
) {
    private val registry = ToolRegistry()
    private val json = Json { ignoreUnknownKeys = true }
    private lateinit var stdout: PrintStream
    private lateinit var log: PrintStream

    fun addTools(provider: Any): McpServer {
        registry.register(provider)
        return this
    }

    fun addUnavailableTools(provider: KClass<*>, reason: String): McpServer {
        registry.registerUnavailable(provider, reason)
        return this
    }

    fun start() {
        stdout = PrintStream(System.out, false, Charsets.UTF_8.name())
        log = System.err
        // Redirect System.out to stderr so tool code's println() doesn't corrupt protocol
        System.setOut(log)

        val reader = BufferedReader(InputStreamReader(System.`in`, Charsets.UTF_8))

        log.println("[$serverName] MCP server started (stdio)")

        while (true) {
            val line = reader.readLine() ?: break
            if (line.isBlank()) continue

            val parsed = try { json.parseToJsonElement(line) } catch (_: Exception) {
                log.println("[$serverName] Invalid JSON")
                emit(buildError(JsonNull, -32700, "Parse error"))
                continue
            }
            val request = parsed as? JsonObject
            val response = if (request == null) buildError(JsonNull, -32600, "Invalid request") else try {
                handleMessage(request)
            } catch (_: Exception) {
                log.println("[$serverName] Request processing failed")
                val id = request["id"]?.takeIf { it is JsonPrimitive && it !is JsonNull && (it.isString || it.booleanOrNull == null) }
                if (id == null) null else buildError(id, -32603, "Internal error")
            }
            emit(response)
        }

        log.println("[$serverName] MCP server stopped")
    }

    private fun emit(response: JsonObject?) {
        if (response == null) return
        stdout.println(json.encodeToString(JsonObject.serializer(), response))
        stdout.flush()
    }

    private fun handleMessage(request: JsonObject): JsonObject? {
        val id = request["id"]
        val validId = id == null || id is JsonNull || id is JsonPrimitive && (id.isString || id.booleanOrNull == null)
        if (!validId) return buildError(JsonNull, -32600, "Invalid request ID")
        val method = (request["method"] as? JsonPrimitive)?.takeIf { it.isString && it.content.isNotBlank() }?.content
            ?: return buildError(id ?: JsonNull, -32600, "Missing or invalid method")
        if (request.containsKey("jsonrpc") && request["jsonrpc"] != JsonPrimitive("2.0"))
            return buildError(id ?: JsonNull, -32600, "Invalid JSON-RPC version")

        // Notifications (no id) don't get responses
        if (id == null || id is JsonNull) {
            log.println("[$serverName] Notification: $method")
            return null
        }
        if (request.containsKey("params") && request["params"] !is JsonObject)
            return buildError(id, -32602, "params must be an object")

        return when (method) {
            "initialize" -> handleInitialize(id)
            "tools/list" -> handleToolsList(id)
            "tools/call" -> handleToolsCall(id, request["params"] as? JsonObject)
            "ping" -> buildResult(id, buildJsonObject { })
            else -> buildError(id, -32601, "Method not found: $method")
        }
    }

    private fun handleInitialize(id: JsonElement): JsonObject {
        log.println("[$serverName] Client connected")
        return buildResult(id, buildJsonObject {
            instructions?.let { put("instructions", it) }
            put("protocolVersion", "2024-11-05")
            put("capabilities", buildJsonObject {
                put("tools", buildJsonObject { })
            })
            put("serverInfo", buildJsonObject {
                put("name", serverName)
                put("version", serverVersion)
            })
        })
    }

    private fun handleToolsList(id: JsonElement): JsonObject {
        val tools = registry.listTools()
        log.println("[$serverName] Listed ${tools.size} tools")
        return buildResult(id, buildJsonObject {
            put("tools", JsonArray(tools))
        })
    }

    private fun handleToolsCall(id: JsonElement, params: JsonObject?): JsonObject {
        if (params == null) return buildError(id, -32602, "Missing tool name")
        val toolName = (params["name"] as? JsonPrimitive)?.takeIf { it.isString && it.content.isNotBlank() }?.content
            ?: return buildError(id, -32602, "Missing tool name")
        if (params.containsKey("arguments") && params["arguments"] !is JsonObject)
            return buildError(id, -32602, "arguments must be an object")
        val arguments = params["arguments"] as? JsonObject ?: buildJsonObject { }

        log.println("[$serverName] Calling tool: $toolName")

        return try {
            callGuard?.invoke(toolName, arguments)
            val result = registry.callTool(toolName, arguments)
            buildResult(id, buildJsonObject {
                put("content", JsonArray(listOf(
                    buildJsonObject {
                        put("type", "text")
                        put("text", result)
                    }
                )))
            })
        } catch (e: McpException) {
            buildError(id, e.code, e.message)
        } catch (e: Exception) {
            log.println("[$serverName] Tool error: ${e.message}")
            buildResult(id, buildJsonObject {
                put("content", JsonArray(listOf(
                    buildJsonObject {
                        put("type", "text")
                        put("text", "Error: ${e.message}")
                    }
                )))
                put("isError", true)
            })
        }
    }

    private fun buildResult(id: JsonElement, result: JsonObject): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0")
        put("id", id)
        put("result", result)
    }

    private fun buildError(id: JsonElement, code: Int, message: String): JsonObject = buildJsonObject {
        put("jsonrpc", "2.0")
        put("id", id)
        put("error", buildJsonObject {
            put("code", code)
            put("message", message)
        })
    }
}
