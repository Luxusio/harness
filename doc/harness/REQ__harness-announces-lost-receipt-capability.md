---
freshness: current
invalidated_by_paths:
  - plugin/scripts/hook_tree_health.py
  - plugin/scripts/drift_warn.py
  - plugin/mcp/harness_server.py
  - plugin/hooks/hooks.json
---

# REQ — a harness that cannot record receipts must say so

tags: [harness, receipts, drift, verification]
summary: When the loaded hook tree lacks the receipt subsystem, the harness must announce it at the first moment it can know, instead of presenting as healthy while receipts silently never record.
updated: 2026-09-01

## Expected normal behavior

Receipts are lifecycle-owned by contract (C-14). In Claude,
`SubagentStart`/`SubagentStop` run `background_hook.py`, the authorized writer
for that runtime; Codex uses `codex_lifecycle_watcher.py`. If the tree a Claude
session loaded its hooks from does not contain the Claude machinery, then:

1. No Claude receipt can be written for any subagent in that session.
2. `task_verify` can never reach `runtime_verdict: PASS`.
3. No standard task can close.

When that is true, the harness must **say so**, naming the offending tree while
keeping task actions separate from out-of-band maintenance. Specifically:

- `task_start` returns a `RECEIPT_HOOKS_UNAVAILABLE` warning that names the
  resolved hook root and states that receipts cannot be recorded and close is
  therefore unreachable. Its task action is to continue substantive review and
  QA, then verify once and park on missing attestation; plugin repair and session
  restart are out-of-band maintainer choices, not the current task's remedy.
- The warning is advisory. `task_start` still creates the task: planning and
  implementation remain legitimate work in a session that cannot close.
- `drift_warn.py` compares repo source against the scripts directory it is
  **itself executing from**, and names that directory in its warning.

If a session cannot produce receipts and nothing reports it, the harness is
defective — regardless of whether every other gate is behaving correctly.

The diagnosis is runtime-scoped. Claude receipt capability is determined from
Claude's registered hook tree. Codex receipt capability is determined by the
MCP-hosted lifecycle watcher and its per-thread registration. A stale Claude
cache must never be presented as evidence that the active Codex watcher is
unavailable. Explicit inspection of a supplied Claude config directory remains
supported for diagnostics and tests, regardless of the caller's runtime.

## Why remaining gates deliberately keep firing

The obvious reading of "stop the gates firing while the harness is half-dead" is
to disable `prewrite_gate.py` / `stop_gate.py` when receipts are unavailable.
That is rejected. Direct Write/Edit ownership and open-task warnings remain
useful independent signals. Bash/shell mutation is outside Harness PreToolUse
enforcement and is not part of this guarantee.

The defect was never that the remaining gates run. It was that their running
was read as evidence of receipt health. The fix supplies the missing signal.

## The split-tree failure mode

MCP and hooks can resolve to **different plugin trees**, and this is what makes
the failure both possible and diagnosable:

- Hooks come from the tree registered in `installed_plugins.json` for
  `harness@harness`.
- The MCP server can simultaneously run from a different, current tree.

On 2026-08-26 the registered hook tree was
`~/.claude/plugins/cache/harness/harness/2.3.0` (installed 2026-05-21, never
re-resolved after the marketplace was repointed at `~/.claude/harness-dev` on
2026-08-25T08:19:35Z — about one minute after the last receipt ever recorded).
The MCP server meanwhile ran current code: `task_start` returned
`required_lenses` and `close_receipt_fingerprint`, neither of which appears
anywhere in the 2.3.0 server.

This dictates **where the check must live**. A SessionStart or PreToolUse hook
carrying the check would be loaded from the same stale tree it needs to indict,
so in the failure mode the checking code is not on disk and cannot run. The MCP
server is the surface proven to execute current code, so `task_start` owns the
warning. `drift_warn.py` remains a second, independent detector for any tree new
enough to contain it.

## Detection rules

`hook_tree_health.receipt_capability_warning()` reports "cannot record" when the
registered tree lacks `background_hook.py`/`subagent_lifecycle.py`, or when its
`hooks.json` does not register both `SubagentStart` and `SubagentStop`. It
accepts either directory layout (`scripts/` at tree root, or nested under
`plugin/`), so a healthy tree of any vintage is never falsely indicted.

With no explicit config directory, the helper first identifies the active
runtime. Under Codex (`HARNESS_RUNTIME=codex` or a valid `CODEX_THREAD_ID`) it
does not inspect `~/.claude/plugins/installed_plugins.json`; Codex readiness is
clean only when the current root thread has a live, validated lifecycle-watcher
registration. Missing, failed, or indeterminate registration is non-clean and
must warn without preventing review/QA lens launch. Under Claude, or when a Claude config
directory is explicitly supplied, the original registered-tree inspection
applies.

The Codex MCP host may not receive `CODEX_THREAD_ID`. In that process the helper
uses the repository's validated current-session hint, written by trusted Codex
hooks, and requires an exact match in the watcher's validated registrations.
It must not weaken the check to “some registration exists.”

Every failure path returns **no finding**: absent, unreadable, or unexpectedly
shaped config; an unresolvable registration; a registered path that does not
exist. An unknown shape is not a broken one. A missed warning is a regression; an
exception raised into `task_start` would be an outage.

## Open question — a registered path that does not exist

Today a registered `installPath` that is absent from disk produces **no
warning**, on the "cannot inspect it, cannot indict it" rule above. QA flagged
2026-08-26 that this may be wrong: unlike an unparseable config, a registration
pointing at a missing directory is unambiguous — hooks certainly did not load
from it, so receipts are certainly impossible, which is the same outage this REQ
exists to report.

The counter-argument is transient states: a plugin mid-reinstall, or a tree on a
mount that is not up yet, would warn spuriously at `task_start`.

Left as-is deliberately for now, because changing it after review and QA had
already passed would ship behavior neither lens verified. Whoever resolves this
should pick one rule and pin it with a test either way; silence and warning are
both defensible, an unexamined default is not.

## Fail-closed property preserved

This REQ adds a *signal*, never a bypass. A harness that cannot record receipts
must still refuse to close. Any future change that lets missing receipt
capability produce a PASS — or that treats this warning as grounds to skip
verification — is strictly worse than the outage it reports. See
`doc/harness/REQ__subagent-completion-receipt-transcript-shape.md`.

## Test obligation

`tests/test_hook_tree_health.py` covers healthy trees in both layouts, missing
and partial receipt modules, each missing subagent event, and every
unresolvable-config path. `tests/test_drift_warn.py` runs a *copy* of the script
from a fake loaded tree, because running it from the repo checkout cannot
exercise the bug. A green suite that only ever executes the script from source
is not evidence this works.

## History

- **2026-08-26** — every subagent ran and returned a verdict, and no receipt was
  written all session. There was no `binding-miss` breadcrumb either, because
  `background_hook.py` was never on disk to run. The surviving gates fired
  normally throughout, so the harness presented as fully operational; the only
  symptom was an absence. `TASK__install-verified-session-hint` was complete and
  twice PASSed but had to be parked `BLOCKED_ENV`. Diagnosis consumed most of a
  session.
- The check that existed to catch exactly this — `drift_warn.py` — was
  structurally incapable of it: it hardcoded `~/.claude/harness-dev` as "the
  installed copy", which is the directory `install.py --force` faithfully
  updates. Source and that tree agreed byte for byte, so it reported no drift
  while the loaded tree was three months stale.
- **2026-08-28** — a Codex session ran the no-argument health command and the
  helper inspected the unrelated stale Claude cache. Runtime scoping was added
  so cross-runtime state cannot create a false `RECEIPT_HOOKS_UNAVAILABLE`.
