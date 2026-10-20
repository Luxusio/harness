---
status: active
freshness: current
---

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
while unreadable, malformed or unsafe markers and unavailable task authority
are failures. An unavailable workspace registry is a discovery failure at
every registration publication read. Flush failure feedback before advisory
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
