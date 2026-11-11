---
name: run
description: Route repository work in a Harness-enabled project through one WBS and purpose-appropriate checks. Use for implementation, fixes, tests/configuration, durable documents or planning deliverables; continuing an active WBS/native Goal; or a write-gate instruction. Read-only questions and discussion need no WBS. Documents/plans use relevant content checks; executable changes retain independent review, applicable QA and configured delivery.
---

# Harness Run

Use this skill as the single public Codex entry point for repository mutation.

Before entering this workflow, resolve the current project with the existing
Harness manifest guard. Run only in a project initialized by `harness:setup`
(`doc/harness/manifest.yaml`); a nested Git checkout needs its own setup.
Outside an initialized project, continue ordinary work without Harness WBS roots,
planning, routing reminders or automatic setup. Explicit setup remains available.

1. Read [the canonical run workflow](../../internal-skills/run/SKILL.md) completely before editing.
2. Execute that workflow in the current conversation. Do not merely summarize it.
3. Discover the current tool surface before declaring subagents unavailable.
4. Start or resume the Harness WBS before source changes. If a native Goal is active, open or resume that objective’s same WBS as directed by the canonical workflow.
5. Follow the returned effective work policy: documents/plans use content checks and optional required `review-document`; development uses independent code review, conditional security review and applicable QA. Use required independent roles when `spawn_agent` is available; reviewer PASS precedes actual QA.
6. Run `wbs(action="verify")` and `wbs(action="close")` against current required WBS checks. Their machine verdicts do not attest review or QA. Do not invent a fallback PASS.
   The active contract is `doc/harness/REQ__receipt-free-execution-and-completion.md`.

The internal plan, develop, review, and QA prompts remain implementation details. Load them only through the canonical run workflow.
