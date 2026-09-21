---
tags: [harness, receipts, diagnostics, subagents, verdicts]
summary: verdict 가 바인딩되지 않은 completion 은 실제 원인을 지목해야 한다. named spawn 은 리포트 형식 문제로 보이지만 형식은 원인이 아니며, 형식 진단을 따르면 같은 실패를 재현하고 끝난 태스크를 잘못 park 한다.
updated: 2026-09-17
freshness: suspect
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/scripts/subagent_lifecycle.py
  - plugin/skills/develop/parallel-fanout.md
  - tests/test_named_spawn_diagnosis.py
freshness_updated: 2026-09-18T08:10:49Z
---

# REQ — an unbound verdict names the spawn shape, not just the report shape

## Context

2026-09-17, `TASK__install-strips-host-write-bits` (run `01a0acca`). The task's
work was finished, tested, and independently reviewed. Five consecutive lens
runs returned substantive verdicts and **none** bound:

| lens run | spawn | `FIRST_LINE` recorded by the hook | receipt |
|---|---|---|---|
| review-code-3 | `name=` | `## Verdict: **PASS**` | row, `PENDING` |
| review-code-b | `name=` | `Verified by reading the diff, …` | row, `PENDING` |
| review-code-c | `name=` | `Review complete. I did not edit any files.` | row, `PENDING` |
| qa-cli | `name=` | `QA complete. Verdict below.` | row, `PENDING` |
| review-code-d | `name=` | `## Verdict: PASS (lens: review-code) — 2 medium…` | row, `PENDING` |
| control | `name=review-control` | — | **no row at all**, 2 breadcrumbs |
| next run | **no `name=`** | `VERDICT: FAIL` | **bound** |

The control is the third shape and completes the picture: a name that encodes
no lens leaves no receipt and writes `named-spawn-shadows-agent-type` to
`learnings.jsonl` (start and stop each contribute one). The write side had
already detected the condition and said so in the ledger. The read side — the
surface a coordinator actually reads at the moment of failure — did not.

The task was parked with the missing-attestation reason — a park that says the
lens ran and could not attest, on a task whose lens was never given the chance.

### Two diagnoses were derived and both were wrong

1. **Stale plugin cache.** `installed_plugins.json` records an installPath of
   `~/.claude/plugins/cache/harness/harness/2.3.0` frozen at 2026-09-09, whose
   `code-reviewer.md` is 128 lines against the source's 204. Falsified:
   `harness:defect-hunter` is offered in the session's agent list and exists
   only in `~/.claude/harness-dev`, not in that cache, so the definitions load
   from `harness-dev` — where `code-reviewer.md` is byte-identical to source.
2. **Recency — the first-line rule is stated once, early, in a 204-line
   definition.** A tail restatement was written across all 12 lens definitions
   to test it, then reverted: the unnamed spawn that bound ran on the *pre-fix*
   definition and said so. ~1440 words of prompt for a cause that was not the
   cause.

### The actual cause was already documented

`plugin/skills/develop/parallel-fanout.md` records the same measurement from
2026-09-10: the CLI puts the display name in the agent-type position and drops
the resolved type, so the hook cannot infer the lens. A name that happens to
encode one (`review-code-3` → `review-code`) still infers it — which is why
these five wrote receipt rows instead of nothing, and why the failure presented
as a format problem rather than as the documented one.

## Requirement

1. **A completion that binds no verdict is diagnosed by what the receipt shows,
   not by the most common cause.** When the completion's agent id carries a
   display name, the diagnosis says so, states the fix (respawn with no `name=`,
   lane label in the prompt), and points at the skill doc that owns the rule.
2. **The generic report-shape wording stays exactly as it is** for completions
   whose agent id is unnamed. The named branch must not swallow the case it does
   not explain — that substitution is the defect, one level down.
3. **A named-spawn failure does not route to the missing-attestation park**
   until an unnamed spawn has also failed to bind. The park text asserts the
   lens ran and could not attest; for a named spawn that statement is false, and
   this repo does not write false statements into `BLOCKED.md` — see
   `REQ__gate-does-not-demand-impossible-evidence.md`.
4. **The id encoding is advisory on both sides.** It selects wording; it never
   admits or refuses a receipt. If the CLI changes the encoding, the cost is a
   misworded sentence, not a lost PASS.

## Enforcement

- `_lib.UNNAMED_AGENT_ID_RE` — one source for the discriminator, read by the
  write side (`subagent_lifecycle._lens_absent`, choosing between silence and a
  breadcrumb) and the read side (`nonparsing_completion_note`).
- The `shape_named` kind and its branch in `nonparsing_completion_note`.
- `tests/test_named_spawn_diagnosis.py` — drives `nonparsing_completion_lenses`
  over real started/completed pairs written into an opened task, for all three
  cases: a named Claude id yields `shape_named`, an unnamed one yields `shape`,
  and a Codex row whose path-shaped id also fails the pattern stays `shape`.
  Verified by mutation: deleting the branch fails the first, deleting the
  `source` gate fails the third. Also pins that the single permitted restatement
  of the regex is the degraded-import fallback.

  This bullet once claimed coverage the tests did not have — every assertion
  handed the kind string to the note function or exercised the regex, so
  deleting the branch left the suite green. Caught in review by exactly that
  mutation. A durable note that overstates its enforcement is worse than one
  that admits a gap, because the next reader stops checking.

## Note for the next reader

The rule was documented, the discriminator existed, and the failure still cost
five lens runs and a false park — because the surface that a coordinator
actually reads at the moment of failure said something else. Documentation one
directory away does not reach the reader who is being told a different cause by
the runtime. That is the general lesson, and it is the same one
`REQ__runtime-surfaces-name-the-actual-blocker.md` records from a different
incident.
