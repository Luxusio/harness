package io.crscube.clitools.mcp

import kotlinx.serialization.json.*
import java.lang.reflect.InvocationTargetException
import kotlin.reflect.KClass
import kotlin.reflect.KFunction
import kotlin.reflect.KParameter
import kotlin.reflect.full.findAnnotation
import kotlin.reflect.full.memberFunctions
import kotlin.reflect.jvm.javaType

class ToolRegistry {

    data class ToolParameter(
        val name: String,
        val description: String,
        val type: String,
        val required: Boolean,
        val kParam: KParameter,
        val hasDefault: Boolean
    )

    data class RegisteredTool(
        val name: String,
        val description: String,
        val parameters: List<ToolParameter>,
        val instance: Any?,
        val function: KFunction<*>,
        val unavailableReason: String?,
        val readOnly: Boolean
    )

    private val tools = mutableMapOf<String, RegisteredTool>()

    fun register(provider: Any) {
        register(provider::class, provider, null)
    }

    fun registerUnavailable(provider: KClass<*>, reason: String) {
        require(reason.isNotBlank())
        register(provider, null, reason)
    }

    private fun register(provider: KClass<*>, instance: Any?, unavailableReason: String?) {
        for (func in provider.memberFunctions) {
            val annotation = func.findAnnotation<McpTool>() ?: continue
            val name = annotation.name.ifEmpty { func.name.toSnakeCase() }

            val params = func.parameters
                .filter { it.kind == KParameter.Kind.VALUE }
                .map { param ->
                    val paramAnno = param.findAnnotation<McpParam>()
                    ToolParameter(
                        name = param.name ?: "arg${param.index}",
                        description = paramAnno?.description ?: "",
                        type = mapKotlinType(param.type.javaType),
                        required = paramAnno?.required ?: !param.isOptional,
                        kParam = param,
                        hasDefault = param.isOptional
                    )
                }

            tools[name] = RegisteredTool(
                name = name,
                description = annotation.description,
                parameters = params,
                instance = instance,
                function = func,
                unavailableReason = unavailableReason,
                readOnly = annotation.readOnly
            )
        }
    }

    fun listTools(): List<JsonObject> = tools.values.map { tool ->
        buildJsonObject {
            put("name", tool.name)
            put("description", tool.description)
            put("inputSchema", buildInputSchema(tool))
            if (tool.readOnly) put("annotations", buildJsonObject {
                put("readOnlyHint", true); put("destructiveHint", false); put("idempotentHint", true)
            })
        }
    }

    fun callTool(name: String, arguments: JsonObject): String {
        val tool = tools[name]
            ?: throw McpException(-32602, "Unknown tool: $name")

        tool.unavailableReason?.let { throw IllegalStateException(it) }
        if (arguments.keys.any { name -> tool.parameters.none { it.name == name } }) {
            throw McpException(-32602, "Unknown parameter; use the tool's declared input schema")
        }

        val args = mutableMapOf<KParameter, Any?>()
        // instance parameter
        args[tool.function.parameters[0]] = tool.instance

        for (param in tool.parameters) {
            val jsonValue = arguments[param.name]
            if (jsonValue is JsonNull) {
                throw McpException(-32602, "Parameter ${param.name} must use its declared JSON type; omit optional values instead of null")
            }
            if (jsonValue == null) {
                if (param.required && !param.hasDefault) {
                    throw McpException(-32602, "Missing required parameter: ${param.name}")
                }
                // skip optional/default params so Kotlin uses the default value
                continue
            }
            args[param.kParam] = convertJsonValue(jsonValue, param.kParam.type.javaType)
        }

        val result = try {
            tool.function.callBy(args)
        } catch (e: InvocationTargetException) {
            throw e.cause ?: e
        }

        return result?.toString() ?: ""
    }

    private fun buildInputSchema(tool: RegisteredTool): JsonObject = buildJsonObject {
        put("type", "object")
        put("additionalProperties", false)
        put("properties", buildJsonObject {
            for (param in tool.parameters) {
                put(param.name, buildJsonObject {
                    put("type", param.type)
                    if (param.type == "array") put("items", buildJsonObject {})
                    if (param.description.isNotEmpty()) {
                        put("description", param.description)
                    }
                })
            }
        })
        val requiredParams = tool.parameters.filter { it.required && !it.hasDefault }
        if (requiredParams.isNotEmpty()) {
            put("required", JsonArray(requiredParams.map { JsonPrimitive(it.name) }))
        }
    }

    private fun mapKotlinType(javaType: java.lang.reflect.Type): String = when {
        javaType == JsonObject::class.java -> "object"
        javaType == JsonArray::class.java -> "array"
        javaType == String::class.java || javaType == java.lang.String::class.java -> "string"
        javaType == Int::class.java || javaType == java.lang.Integer::class.java ||
        javaType == Long::class.java || javaType == java.lang.Long::class.java -> "integer"
        javaType == Double::class.java || javaType == java.lang.Double::class.java ||
        javaType == Float::class.java || javaType == java.lang.Float::class.java -> "number"
        javaType == Boolean::class.java || javaType == java.lang.Boolean::class.java -> "boolean"
        else -> "string"
    }

    private fun convertJsonValue(element: JsonElement, targetType: java.lang.reflect.Type): Any {
        if (targetType == JsonObject::class.java) {
            return element as? JsonObject ?: throw McpException(-32602, "Expected a JSON object")
        }
        if (targetType == JsonArray::class.java) {
            return element as? JsonArray ?: throw McpException(-32602, "Expected a JSON array")
        }
        val primitive = element as? JsonPrimitive
            ?: throw McpException(-32602, "Expected the declared JSON scalar type")
        val value: Any? = when {
            targetType == String::class.java || targetType == java.lang.String::class.java ->
                primitive.takeIf { it.isString }?.content
            primitive.isString -> null
            targetType == Int::class.java || targetType == java.lang.Integer::class.java -> primitive.intOrNull
            targetType == Long::class.java || targetType == java.lang.Long::class.java -> primitive.longOrNull
            targetType == Double::class.java || targetType == java.lang.Double::class.java -> primitive.doubleOrNull?.takeIf { it.isFinite() }
            targetType == Float::class.java || targetType == java.lang.Float::class.java -> primitive.floatOrNull?.takeIf { it.isFinite() }
            targetType == Boolean::class.java || targetType == java.lang.Boolean::class.java -> primitive.booleanOrNull
            else -> null
        }
        return value ?: throw McpException(-32602, "Parameter must use its declared JSON type")
    }
}

class McpException(val code: Int, override val message: String) : Exception(message)

private fun String.toSnakeCase(): String =
    replace(Regex("([a-z])([A-Z])"), "$1_$2").lowercase()
