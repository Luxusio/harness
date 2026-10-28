---
status: active
freshness: superseded
---

> **Historical — superseded 2026-10-28.** [Receipt-free execution and completion](REQ__receipt-free-execution-and-completion.md) replaces this document’s receipt collection, attestation, naming, storage and completion instructions. The body below is retained history, not active workflow guidance. Actual independent review PASS before actual QA and current required WBS checks remain required.


# Codex watcher recovery after unavailable binding reads

## User outcome

A temporary inability to read the exact active task binding must not silently
disable receipt collection for the rest of a valid registration. Unknown binding
grants no receipt authority. A validated change to a different task/run retires
the previous worker normally.

## Required behavior

- Outer watch checks and spawn/start/completion publication checks distinguish
  unavailable binding from a positively established different generation.
- During uncertainty, no lifecycle is observed or receipt appended. The first
  unavailable binding makes the worker fail retryably, without an inline retry.
  The manager must independently validate the persisted registration before
  restarting from its immutable offset. Idle limits, stop requests and leases
  still bound work.
- A worker pins its immutable registration generation and checks it again at
  lifecycle publication boundaries under the session transaction. Replacement
  retires the old worker even when the task/run pair stayed the same. A missing
  or unsafe registration cannot grant authority or become a successful read.
- Exact lifecycle correlation, protected receipt ownership, deduplication and
  duplicate-terminal rejection remain unchanged. Conflict fencing invalidates
  the old registration; authorized rebinding starts at a new current offset.
- An older recorded review/QA FAIL stays FAIL when a remedial retry lacks
  receipts. Guidance first requires remediation. Only subsequently delivered
  required review PASS followed by QA PASS, with no actual outstanding failure
  or environment blocker, permits the canonical missing-attestation endgame
  after one fresh verification. Direct finals cannot close a task.
- The fixed parking text remains owned by `_lib.py`; no additional copy lives
  in this requirement. No manual receipt repair or historical scan is allowed.

## Incident evidence and limits

Dogfooding on 2026-09-28 observed missing latest QA start/completion receipts,
while prior review receipts existed. The live MCP process had its manager but
no root watcher thread or rollout descriptor. A new task registration naturally
created a worker. Code inspection found that an empty binding made the worker
return success and permanently suppressed its registration within that manager.
This mechanism is reproducible; the exact event that triggered the live worker
exit was not captured and remains unknown.

## Verification

Deterministic tests cover unavailable reads at each lifecycle boundary,
same-generation recovery, sustained uncertainty, validated task/run changes,
conflict fencing, trusted-file replacement rejection and exact receipt
deduplication. Gate tests retain FAIL and close refusal while exposing conditional
canonical parking guidance. Independent security review and full QA precede
verified installation.

## Known ceiling

Fresh-process projection tests verify the changed Python implementation.
Installing updated files does not prove that an existing MCP process reloaded
already-imported modules. Native Claude is unavailable in this environment;
Claude projection checks do not constitute native Claude execution.

## Registration liveness and early diagnosis (2026-10-20)

User intent (conversation): “증거수집도 버그 고쳐”, followed by “왜 또 왜 또
고장났어” and “아니 진짜 맨날 watcher는 맨날 고장나더라? 뭐 제대로 돌아가는
꼴을 본 적이 없어”. These request reliable collection and explain the recurring
failure. The mechanics below are derived implementation requirements, not
claims that the user selected a timeout or filesystem algorithm.

This incident confirmed valid native identity and supported direct lifecycle
events, but a registration still naming a previous closed task. With three
linked workspaces, binding discovery alone took 0.59–0.64 seconds against a
0.5-second registration budget. Profiling found redundant control-directory
root resolution and duplicate marker reads. This identifies a reproducible
registration failure; it does not establish the cause of every past outage.

Registration must handle this ordinary linked-worktree path within a bounded
caller deadline. Reuse a root resolution only within one validation call;
retain component confinement checks, current-run resolution and conflict
revalidation. Missing markers need no second binding read. A shared registration
budget must fit the enclosing hook's remaining deadline, never reset or add to
it. Hooks remain fail-open and evidence completion remains fail-closed.

Deadline exhaustion, validation exceptions, actual conflicts, absent bindings
and publication rejection must not collapse into the same diagnosis. Report
the failed stage and a bounded, non-sensitive reason immediately through the
existing hook diagnostics. PostToolUse must not silently discard registration
failures. Registration success alone does not establish a live watcher or an
observed lifecycle. Diagnostic files never supply authoritative native identity.
Registration uses strict binding reads: a missing marker is benign absence,
while unreadable or unsafe markers, invalid JSON/session identity, partial or
inconsistent binding fields, and unavailable task authority are failures.
Valid neutral markers and valid stale-run bindings grant no authority and
remain benign. Conflict fences retain their separate validation. Reuse the
validated workspace registry while holding its binding transaction; an
unavailable registry at discovery is a failure. Flush failure feedback before advisory
diagnostic I/O and bound that I/O by the remaining hook deadline; interrupted
writes clean up their owned temporary files.
When later readiness in the same MCP response positively disproves an earlier
capability warning, remove that superseded warning; preserve unrelated warnings
and never clear a warning on unknown readiness alone.

Regression evidence includes three real linked worktrees, slow discovery,
timeout/exception classification, enclosing deadline intersection, absent
markers and conflict fences, immutable offsets and exact future native
lifecycle collection. No past receipt is reconstructed and no previous task
is reopened merely to manufacture missing evidence.

## Final verification and asynchronous completion (2026-10-20)

User intent: “왜 또 영수증 누락이야 또 고쳐” and “영수증 이거 진짜 제대로 사용
가능한거 맞아? 맨날 누락되고 그러는데?”. The synchronization mechanics below
are derived repairs, not user-selected timing requirements.

The observed native QA final arrived at 06:38:28.207Z, verification was requested
at 06:38:31.913Z, and the watcher appended the authentic completion at
06:38:32.953460Z. Verification retained a receipt snapshot taken before expensive
WBS measurement and returned stale missing-completion guidance after the
completion existed. This incident is a stale-read race, not permanent loss.

A final verify measures WBS once, then evaluates receipts from a final coherent
view. An already-started required current-run lifecycle may settle within a
short bounded reader wait. No receipt/session writer lock is held while waiting
or measuring. No writer, replay, watcher restart, coordinator retry or synthetic
event is added. Integrity errors, genuine absence, wrong generations, unordered
QA and terminal negatives never become PASS through waiting. Task authority and
WBS changes during the operation invalidate that result. Verdict, missing items,
review/QA routing, watcher diagnosis and next action use the same final snapshot.
