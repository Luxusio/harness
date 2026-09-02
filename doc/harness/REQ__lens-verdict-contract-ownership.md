# REQ — the lens verdict contract belongs to the agent definition

tags: [harness, receipts, review, qa]
summary: 스폰 프롬프트는 판정 포맷을 재진술하지 않는다. 포맷 불일치로 바인딩된 완료는 미실행이 아니라 포맷 실패로 보고된다.
updated: 2026-09-02
freshness: current
invalidated_by_paths:
  - plugin/agents/code-reviewer.md
  - plugin/agents/qa-cli.md
  - plugin/scripts/_lib.py
  - plugin/mcp/harness_server.py
  - plugin/skills/develop/SKILL.md
  - plugin/skills/run/SKILL.md

## Expected normal behavior

1. **The agent definition is the sole owner of the verdict contract.** Every
   review and QA lens definition states that the final response begins with
   `VERDICT: PASS|FAIL|BLOCKED_ENV`, and for review lenses that
   `FINDING_COUNTS: FIX_NOW=<n> INVESTIGATE=<n> OPTIONAL=<n>` is the second
   line. `_lib.normalize_receipt_completion` enforces exactly that shape.
   A coordinator spawning a lens describes **what to review**, never **how to
   format the verdict**. It must not restate, relocate, paraphrase, or
   "helpfully" repeat the contract in the spawn prompt.

2. **A completion whose shape was rejected is reported as a format failure, not
   as an unrun lens.** When a lens completes for the current run and binds as
   `VERDICT: PENDING` / `FINDING_COUNTS: INVALID`, the pending guidance from
   `task_verify` and task context names the lens, says the final did not satisfy
   the verdict contract, and states that this is neither an unrun lens nor a
   missing receipt. The remedy it gives is rerunning that lens without
   restating the format.

3. **Verdict authority is positional, and a mention is not a verdict.** Line 1
   binds, or nothing does. A later line voids the result only when it is itself
   a bare verdict line naming a *different* verdict; the same holds for the
   review counts line, where only a second bare counts line with different
   numbers is ambiguous. Prose that mentions these tokens inline, or in any line
   that is not itself a bare verdict or counts line, never invalidates anything.
   A quoted example rendered as its own bare line still counts as a candidate
   authority and still voids when it disagrees. The parser splits on lines and
   strips each one, so it has no notion of fences or indentation: a fenced or
   indented example line is **not** exempt. Quote differing examples inline
   within a sentence. Reviewing this subsystem requires discussing these
   tokens; a rule that punishes that makes the lens unable to review its own
   contract.

4. **The label changes nothing at the gate.** A non-parsing completion stays
   non-attesting: `runtime_verdict` remains `PENDING`, `task_close` still
   refuses, and no new path authorizes PASS. The requirement is diagnostic
   honesty, not a relaxation.

## Why this is a requirement and not a style note

A prompt saying "end your response with this format" produces an agent that
complies with the prompt and violates the contract. The lens does its full job,
the hook records a completion, and the verdict evaporates — the work is paid for
and thrown away, and the resulting bare `PENDING` is indistinguishable from
"the lens never ran", so the coordinator's next move is to hunt for a missing
receipt instead of rerunning one lens.

Three observed occurrences, each a different cause:

- 2026-08-27 — three review rounds, six agents, discarded because
  `INVESTIGATE=` was omitted from the counts line.
  (`doc/harness/HANDOFF__parked-receipt-tasks.md` §1.1)
- 2026-09-02 — a complete PASS review discarded because the coordinator's spawn
  prompt requested the verdict block at the end of the response instead of the
  beginning.
- 2026-09-02 — the corrected re-run of that same review, correctly shaped on
  lines 1 and 2, discarded because the report mentioned `FINDING_COUNTS:` a
  second time while discussing the verdict-contract code under review. The old
  rule voided a completion on any repeat occurrence anywhere in the message.

All three were reported to the operator as a generic pending state.

## Verification

`tests/test_receipt_watcher_fail_closed.py::TestNonParsingCompletionIsNamed`
pins both directions: a trailing-verdict completion is named as a format
failure while `runtime_verdict` stays `PENDING`, and a well-formed completion
adds no note. `TestVerdictAuthorityIsPositional` in the same file pins rule 3 —
prose mentions keep their verdict, repeated identical lines are harmless,
conflicting verdict or counts lines still void, and a trailing verdict still
binds `PENDING`. Skill prose is pinned by
`tests/test_feedback_rule_skill_docs.py`.
