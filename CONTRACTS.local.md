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

### C-102

**Title:** C-11 and C-13 are enforced against this repository by the test suite.
**When:** Any change to `CONTRACTS.md`, to a `SKILL.md` under `plugin/` or `plugin-codex/`, or to `contract_lint.py` itself.
**Enforced by:** `tests/test_contract_lint_real_tree.py` — runs `contract_lint.lint()` on the repository's own `CONTRACTS.md` (asserting no hard *and* no soft issues) and `check_skill_weights()` on every discovered plugin root. The hard-drift and weight assertions are each paired with a mutation case that must trip them; the soft channel is fixture-proven in `tests/test_contract_lint.py`, which is load-bearing coverage rather than a redundant duplicate. `contract_lint.py` is deliberately **not** registered as a hook: the managed block's own § 0 prefers the lightest path that preserves the gate, and a suite already runs on every change.
**On violation:** hard-block — the suite fails. Fix the drift or the over-budget file; do not relax the assertion.
**Why:** Until 2026-09-03 both contracts named `contract_lint.py` as their enforcement while nothing ran it: it appeared in no `hooks.json` event, and both existing test files exercised only `tempfile` fixtures. C-11 further claimed a "SessionStart hook" that did not exist. The managed block declares that a prose-only rule is commentary; these two contracts had become exactly that. This contract lives in the local file because the managed block is regenerated from the setup template on upgrade, and the template must not claim a test that a downstream user's project does not contain. See `doc/harness/REQ__contract-enforcement-claims-are-executable.md`.
