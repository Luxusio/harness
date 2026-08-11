# Project-specific contracts

This file is yours. The harness never touches it after setup creates the stub.
Add contracts numbered C-100 and above to keep clear of the managed block in
`CONTRACTS.md`.

Use the same four-field structure so `contract_lint.py` can validate them:

### C-100

**Title:** Bug report → REQ doc capturing expected normal behavior.
**When:** User reports a defect ("X is broken / appearing / not working / wrong"). Applies whether the fix is large or one-line, whether a task is open or not, and whether the bug is found through observation, automation, or user testing.
**Enforced by:** Convention (this contract) + the resolving task's PLAN.md Durable Docs Decision MUST select a REQ path naming the expected behavior. Develop refuses to close without the REQ written or an explicit `REQ: n/a` reason that states why the bug touches no observable contract.
**On violation:** soft-warn. The fix is incomplete until the REQ doc exists. Re-open or extend the task.
**Why:** A bug is an observed gap between current behavior and expected behavior. Without writing the expected behavior down, the fix is not durable knowledge — future regressions cannot be detected against a baseline that only exists in chat history. The morning of 2026-05-31's stale-install pollution bug was identified months later only because the user happened to look at git status; expected behavior ("hooks no-op outside harness-enabled repos") was not captured as a REQ until this contract was written.

### C-101

**Title:** Conversation requirements receive a durable-doc check before close.
**When:** Every task that changes durable docs or receives an explicit user requirement about observable behavior during development.
**Enforced by:** `plugin/skills/develop/SKILL.md` Phase 8.6 and the `critic-document` documentation-review agent. The developer incorporates current-conversation requirements into PLAN/REQUEST or the appropriate committed REQ/GUIDE/ADR/POLICY surface, then delegates the relevant context with the document review. C-100 covers user-reported defects; this contract covers new requirements stated while a task is active.
**On violation:** soft-warn. The task remains incomplete until the requirement is captured in a committed durable surface or explicitly classified as a one-off task directive.
**Why:** The current conversation is the authoritative source while work is active. Promoting requirements directly avoids a second prompt-capture ledger whose lifecycle and disposition can drift from the task plan.
