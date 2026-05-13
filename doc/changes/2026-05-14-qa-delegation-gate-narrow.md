---
date: 2026-05-14
task: TASK__narrow-c18-to-test-mcp-only
commit: post-de09e78
tags: [c-18, qa-delegation, gate, behavior-change]
---

# C-18 gate narrowed: Bash test runners allowed inline, only `mcp__chrome-devtools__*` blocked

Prior to this change (de09e78, v1), the C-18 Verification delegation gate blocked a broad list of Bash test runner commands (pytest, vitest, pnpm test, npm test, yarn test, bun test, jest, mocha, cargo test, go test, rspec, phpunit, and others) in addition to `mcp__chrome-devtools__*` MCP tool calls. User feedback confirmed that Bash test runner output is bounded (a single PASS/FAIL line) and does not bloat main-session context, making the Bash block an over-aggressive false positive. As of this task, the gate now blocks only `mcp__chrome-devtools__*` tool calls — the actual context-bloat source (DOM snapshots, screenshots, and evaluate payloads add thousands of structured tokens per call) — and silently allows all Bash test runner invocations to proceed inline. The `HARNESS_SKIP_QA_DELEGATION=1` bypass env var and the `learnings.jsonl type=qa-delegation-warn` logging row are unchanged. A known v1 limitation remains: qa-browser's own `mcp__chrome-devtools__*` calls hit the same gate (no subagent detection yet); set `HARNESS_SKIP_QA_DELEGATION=1` when spawning qa-browser if friction surfaces.
