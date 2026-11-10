package io.crscube.clitools.mcp

@Target(AnnotationTarget.FUNCTION)
@Retention(AnnotationRetention.RUNTIME)
annotation class McpTool(
    val name: String = "",
    val description: String
)

@Target(AnnotationTarget.VALUE_PARAMETER)
@Retention(AnnotationRetention.RUNTIME)
annotation class McpParam(
    val description: String = "",
    val required: Boolean = true
)
