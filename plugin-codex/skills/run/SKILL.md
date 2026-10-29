---
name: run
description: Run the complete Harness workflow for repository-mutating work in a Harness-enabled project. Within such an initialized project, use automatically when Codex is asked to implement, fix, refactor, build, migrate, or otherwise change source code, tests, configuration, or durable project documentation; when continuing an active Harness task or native Goal; or when a write gate says to enter Harness. Do not use for read-only questions, explanations, or status checks. This entry starts or resumes the task and enforces planning when needed, minimal implementation, independent code review, conditional security review, required QA, verification, installation when configured, and close.
---

# Harness Run

Use this skill as the single public Codex entry point for repository mutation.

Before entering this workflow, resolve the current project with the existing
Harness manifest guard. Run only in a project initialized by `harness:setup`
(`doc/harness/manifest.yaml`); a nested Git checkout needs its own setup.
Outside an initialized project, continue ordinary work without Harness tasks,
planning, routing reminders or automatic setup. Explicit setup remains available.

1. Read [the canonical run workflow](../../internal-skills/run/SKILL.md) completely before editing.
2. Execute that workflow in the current conversation. Do not merely summarize it.
3. Discover the current tool surface before declaring subagents unavailable.
4. Start or resume the Harness task before source changes. If a native Goal is active, open or resume that objective’s same WBS as directed by the canonical workflow.
5. Use the required independent code reviewer, conditional security reviewer, and QA subagents when `spawn_agent` is available. Await actual reviewer PASS finals before actual QA.
6. Run `wbs(action="verify")` and `wbs(action="close")` against current required WBS checks. Their machine verdicts do not attest review or QA. Do not invent a fallback PASS.
   The active contract is `doc/harness/REQ__receipt-free-execution-and-completion.md`.

The internal plan, develop, review, and QA prompts remain implementation details. Load them only through the canonical run workflow.
