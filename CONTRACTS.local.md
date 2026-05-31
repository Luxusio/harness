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

**Title:** User-feedback retrospective REQ check at close.
**When:** Every task close. The `critic-document` agent runs unconditionally as a close-time gate when the closing task has any of: (a) changed `doc/<area>/REQ__*.md` / `GUIDE__*.md` / `ADR__*.md` / `POLICY__*.md` artefacts, OR (b) a non-empty `<task_dir>/USER_FEEDBACK.jsonl` (populated by `plugin/scripts/prompt_memory.py` on every UserPromptSubmit while the task was active).
**Enforced by:** `plugin/skills/develop/SKILL.md` Phase 8.6 + `plugin/CLAUDE.md` trigger description. `critic-document.md` agent prompt instructs the agent to read USER_FEEDBACK.jsonl, apply the "imperative + observable-surface + missing-from-current-REQs" heuristic, and call `mcp__harness__write_req_doc` with `status: candidate` to author each missed REQ. The MCP write helper accepts an optional `status` field (default `accepted`); writes the value into the REQ frontmatter. C-100 (bug → REQ) covers the case where a user reported a defect explicitly; this contract covers the case where the user stated a requirement that the task closed without explicitly capturing.
**On violation:** soft-warn. If a closed task left a candidate-REQ-worthy prompt unrecorded, the next bug report citing the same gap re-opens the topic; harness can re-extract by re-running critic-document against the prior USER_FEEDBACK.jsonl.
**Why:** SessionStart banner (from C-100 work in TASK__session-start-req-reminder-and-drift-warn) gives an upfront nudge. Users still forget. The retrospective net at close uses LLM judgment against the structured per-turn capture that prompt_memory.py already maintains — no new capture surface, no new hook, no new file. `status: candidate` is the discard path for LLM false positives: the REQ doc lands on disk for review without claiming `accepted` status until a future task confirms.
