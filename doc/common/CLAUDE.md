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
- [INF harness spec-exists](INF__harness__spec-exists.md) — harness architecture spec at doc/harness/ (SPEC.md, IMPORT_LIST.md, AUTO_ROUTING.md)
- [REQ process plan-skill-review-pipeline](REQ__process__plan-skill-review-pipeline.md) — plan skill must run 7-phase dual-voice pipeline; old linear procedure retired (2026-04-10)
- [GUIDE document taxonomy](GUIDE__document-taxonomy.md) — durable project knowledge uses typed documents under `doc/<area>/`
- [REQ process bash-guard-script-execution](REQ__process__bash-guard-script-execution.md) — the Bash guard gates protected-artifact file mutation, not script execution; execution inspection was removed as bypassable (2026-08-26)
- [REQ process subagent-receipt-binding](REQ__process__subagent-receipt-binding.md) — every hook-observed subagent stop records exactly one completion receipt; provenance checks must match the transcript shape the runtime actually emits (2026-08-27)
- [GUIDE mcp tool-naming](GUIDE__mcp-tool-naming.md) — Claude plugin uses `mcp__plugin_harness_harness__`, Codex uses bare names, `mcp__harness__` is legacy/banned in `plugin/`; dev sessions exposing bare names see qa-* relay (expected, not a bug)
