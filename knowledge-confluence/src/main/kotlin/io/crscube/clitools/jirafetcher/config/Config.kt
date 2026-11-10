package io.crscube.clitools.jirafetcher.config

data class JiraConfig(
    val baseUrl: String,
    val email: String,
    val apiToken: String
) {
    companion object {
        fun fromEnvironment(): JiraConfig {
            val baseUrl = System.getenv("JIRA_BASE_URL")
                ?: throw IllegalStateException("JIRA_BASE_URL environment variable is not set")
            val email = System.getenv("JIRA_EMAIL")
                ?: throw IllegalStateException("JIRA_EMAIL environment variable is not set")
            val apiToken = System.getenv("JIRA_API_TOKEN")
                ?: throw IllegalStateException("JIRA_API_TOKEN environment variable is not set")

            return JiraConfig(
                baseUrl = baseUrl.trimEnd('/'),
                email = email,
                apiToken = apiToken
            )
        }
    }

    val jiraApiUrl: String get() = "$baseUrl/rest/api/3"
    val confluenceApiUrl: String get() = "$baseUrl/wiki/api/v2"
}
