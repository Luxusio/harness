---
tags: [harness, receipts, watcher, diagnostics, fail-closed]
summary: 읽지 못한 워처 상태는 "기록 가능"이 아니다. 영수증은 capability warning 이라는 휴리스틱 하나만 반증할 수 있으며, session identity 부재나 읽을 수 없는 worker 상태까지 뒤집으면 fail-open 이다.
updated: 2026-09-17
freshness: suspect
invalidated_by_paths:
  - plugin/mcp/harness_server.py
  - tests/test_receipt_watcher_fail_closed.py
  - doc/harness/REQ__subagent-receipt-session-binding.md
freshness_updated: 2026-09-18T08:10:49Z
---

# REQ — an unreadable worker state is not "recordable"

## Context

`tests/test_receipt_watcher_fail_closed.py::TestReadinessIsTriState::
test_earlier_receipt_does_not_mask_live_worker_error` was red from commit
`a01bf3f` ("fix(codex): bind receipt watcher to exact session") until
2026-09-17. Bisected: `a01bf3f~1` passes, `a01bf3f` fails. That commit edited
the same test file and landed with the test failing, and every session since
reported it as "pre-existing" — including four separate reports earlier in the
session that fixed it.

`a01bf3f`'s own change was right and is kept: `_watcher_status` will not query
`watcher_manager.worker_error` unless this process owns a thread identity,
because the diagnostics file is attacker-influenced and must never select whose
worker state is read. `test_diagnostic_root_id_cannot_hide_current_worker_error`
pins that and passes throughout.

What it left behind was a contradiction inside one test class. For the **same**
state — Codex runtime, no session identity, a manager reporting an error,
receipts already written for the run — two tests demanded opposite answers:

| test | demanded | status |
|---|---|---|
| `test_earlier_receipt_does_not_mask_live_worker_error` | `receipts_recordable is False` | red since `a01bf3f` |
| `test_worker_error_lookup_prefers_the_authoritative_identity` | `receipts_recordable is True` | green |

Neither was right, and the green one was the more harmful: it pinned a
fail-open. With no identity, `last_watcher_error` stays empty, no branch sets
`False`, `recordable` is left `None`, and then

```python
if recordable is None and _run_has_receipts(task_dir, run_id, snapshot):
    recordable = True
```

promoted it. The override's own comment already said "a receipt disproves only
the heuristic capability warning" — but a bare `None` does not record *which*
question went unanswered, so the guard could not honour its own comment.

## What settles it

`doc/harness/REQ__subagent-receipt-session-binding.md` already owned the answer,
and neither test matched it:

> When the MCP host lacks an exact thread environment, watcher readiness is
> unknown until a session hook can establish or disprove it.

Unknown — so `None`, not `True` and not `False`. A receipt written earlier in
the run is not a session hook and does not establish identity. `False` would be
just as wrong in the other direction: `_watcher_status`'s docstring reserves it
for a *positively observed* failure, and here a read was refused rather than a
failure seen. That docstring also records that collapsing unknown into `False`
is what once produced a self-deadlock.

## Requirement

1. **Three distinct unknowns.** A heuristic capability warning; a missing exact
   session identity; and an unreadable worker state — a manager exposing
   `worker_error` exists and could not be queried, whether because no identity
   was available or because the query raised.
2. **A receipt disproves exactly one of them:** the capability warning, the
   heuristic that guesses from plugin registration instead of observing writes.
   The other two ask questions a receipt does not answer, because the worker may
   have failed *after* that receipt was written.
3. **The unknown states say so.** `receipts_unrecordable_summary` names the
   condition rather than leaving a bare `null` for the reader to interpret.
4. **`a01bf3f`'s property is untouched.** No identity is ever borrowed from the
   diagnostics file to query a worker.

## Enforcement

Requirement 2 is enforced by branch structure, not by a promotion rule — worth
stating precisely, because the first version of this fix added a promotion guard
keyed on the unknown's cause and a review found it unreachable, leaving this
section naming a line that never ran:

- `harness_server._watcher_status` raises the capability warning only under
  `capability_warning and not _run_has_receipts(...)`, so a run with receipts
  never enters that branch and `recordable` keeps its initializer. That *is* the
  receipt disproving the warning. No second promotion follows, and re-adding one
  is what `test_a_receipt_does_not_settle_a_missing_session_identity` fails on.
- The `worker_state_unreadable` flag covers both unreadable arms — the missing
  identity and the raising query — and forces `recordable` to `None`. Its guard
  is `is not False`, not `is None`, so it downgrades a `True` initializer as
  well: a host that owns an identity and whose worker query *raises* has read
  nothing either, and an earlier version of this guard reported exactly that
  state as recordable while this document claimed the arm was covered.
- The trailing summary block is guarded on the reason as well as the summary.
  Keyed on the summary alone it overwrote the capability warning's text —
  remediation sentence included — with a cause that had not fired.
- The trailing `recordable is None and not unrecordable_summary` block gives the
  remaining unknown, a missing session identity, a summary of its own.
- `tests/test_receipt_watcher_fail_closed.py` — three cases, each
  mutation-checked against the exact pre-change source line:
  `test_a_receipt_does_not_settle_a_missing_session_identity`,
  `test_earlier_receipt_does_not_mask_unreadable_worker_state` (renamed from the
  red test, intent preserved, value reconciled to `None`), and
  `test_a_raising_worker_query_is_unreadable_not_silent` — whose surviving
  mutant was `except Exception: pass`, i.e. an ordinary revert would have
  restored the fail-open with nothing going red.

## Note for the next reader

A red test that ships is not a known issue, it is an unresolved contradiction
with a countdown. This one shipped beside a green test asserting the opposite,
so the suite encoded both answers and enforced the wrong one for two days while
four separate reports called it pre-existing and moved on. When a commit leaves
a test red, the question to answer is not "was this already failing" but "which
of the two things the suite now claims is true".

**This test has now shipped red twice.** `doc/harness/qa/QA_KNOWLEDGE.yaml`'s
`pre_existing_test_failure` entry records the first: it "shipped red in
`a095c76` (the commit that introduced it) and stayed red through `3e4585b`",
until `5a33299` fixed it. `a01bf3f` then broke it again. Both times the red
outlived several sessions because the recorded knowledge said *baseline*, and
"baseline" is a word that stops people looking. Both stale notes now carry a
SUPERSEDED marker for that reason — a known-red note that outlives its red is
not neutral, it is an excuse waiting for the next genuine failure.

The same shape appeared three times inside this task alone, and each time a
review lens caught it: an enforcement point named in a durable note that never
ran; a new summary block whose condition was wider than its comment claimed; a
flag documented as covering an arm it did not reach. Writing the rule down is
not the same as enforcing it, and a document that names the wrong enforcement
point is worse than one that names none.
