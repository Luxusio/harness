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
- [REQ process plan-skill-review-pipeline](REQ__process__plan-skill-review-pipeline.md) — plan skill conservatively selects compact or full planning; compact may skip plan-time review, while implementation `review-code` remains mandatory
- [GUIDE document taxonomy](GUIDE__document-taxonomy.md) — durable project knowledge uses typed documents under `doc/<area>/`
- [GUIDE LLM wiki and context engineering](GUIDE__llm-wiki-context-engineering-research.md) — 공개 구현·논문·운영 경험의 비교와 평가 조건 (2026-11-11)
- [OBS HarnessMM paper audit](OBS__harnessmm-2610-32813.md) — arXiv 2610.32813 v2의 토큰 절감·통계·선행 연구 검토
- [INF wiki/context improvement candidates](INF__wiki-context-improvement-candidates.md) — 현행 코드와 대조한 미채택 개선 후보·작은 검증 순서
- [REQ process subagent-receipt-binding](REQ__process__subagent-receipt-binding.md) — every hook-observed subagent stop records exactly one completion receipt; provenance checks must match the transcript shape the runtime actually emits (2026-08-27)
- [GUIDE mcp tool-naming](GUIDE__mcp-tool-naming.md) — Claude plugin uses `mcp__plugin_harness_harness__`, Codex uses bare names, `mcp__harness__` is legacy/banned in `plugin/`; dev sessions exposing bare names see qa-* relay (expected, not a bug)
- [GUIDE harness how-it-works](../harness/GUIDE__how-harness-works.md) — 하네스 전체 동작 안내(한국어): 과제 수명주기, MCP, 훅·prewrite gate, 영수증·마감 게이트, 스킬 단계, 에이전트, Goal, batch, 설치·setup, 계약, 메모리
