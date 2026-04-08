# common root
tags: [root, common]
summary: Shared project knowledge — goals, observations, inferences
updated: 2026-03-30

## Notes
- [REQ project primary-goals](REQ__project__primary-goals.md)
- [REQ project template-sync](REQ__project__template-sync.md) — runtime changes must propagate to setup templates
- [REQ source anthropic-harness-design](REQ__source__anthropic-harness-design.md) — Anthropic foundational requirements (2026-03-24)
- [REQ source openai-harness-engineering](REQ__source__openai-harness-engineering.md) — OpenAI foundational requirements (2026-02-11)
- [OBS repo workspace-layout](OBS__repo__workspace-layout.md)
- [INF arch initial-stack-assumptions](INF__arch__initial-stack-assumptions.md)
- [REQ process cli-artifact-writes](REQ__process__cli-artifact-writes.md) — agents must use CLI tool for protected artifact writes; direct inline writes waste 500-2000 tokens
- [REQ process task-set-fields-via-mcp](REQ__process__task-set-fields-via-mcp.md) — coordinator must use task_set_fields MCP tool to update coordinator-settable TASK_STATE fields; direct YAML writes prohibited
- [REQ process browser-required-enforcement](REQ__process__browser-required-enforcement.md) — browser_required: true in TASK_STATE.yaml enforces browser verification across critic-runtime, plan skill, and setup template
