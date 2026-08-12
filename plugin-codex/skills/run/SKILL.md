---
name: run
description: Run the complete Harness workflow for repository-mutating work in a Harness-enabled project. Use automatically when Codex is asked to implement, fix, refactor, build, migrate, or otherwise change source code, tests, configuration, or durable project documentation; when continuing an active Harness task or native Goal; or when a write gate says to enter Harness. Do not use for read-only questions, explanations, or status checks. This entry starts or resumes the task and enforces planning when needed, minimal implementation, independent code review, conditional security review, required QA, verification, installation when configured, and close.
---

# Harness Run

Use this skill as the single public Codex entry point for repository mutation.

1. Read [the canonical run workflow](../../internal-skills/run/SKILL.md) completely before editing.
2. Execute that workflow in the current conversation. Do not merely summarize it.
3. Discover the current tool surface before declaring subagents unavailable.
4. Start or resume the Harness task before source changes. If a native Goal is active, synchronize it and attach the child task as directed by the canonical workflow.
5. Use the required independent code reviewer, conditional security reviewer, and QA subagents when `spawn_agent` is available. Await their final verdicts; a start receipt is not a PASS.
6. Let `task_verify` and `task_close` enforce fresh review and QA evidence. Never write receipt files or invent a fallback PASS.
   Normative receipt acquisition and storage/gate contracts live in
   `doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md` and
   `doc/harness/patterns/ADR__consolidated-task-artifacts.md` respectively.

The internal plan, develop, review, and QA prompts remain implementation details. Load them only through the canonical run workflow.
