---
tags: [harness, prewrite-gate, hooks, c-05, c-12]
summary: C-05 checks read the whole payload instead of truncating at 64 KiB. Codex retries those checks after gate-child failure, subject to memory and outer-hook timeout limits; a killed hook can still allow the write.
updated: 2026-09-28
freshness: current
invalidated_by_paths:
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/hook_pre_tool_use.py
  - plugin/hooks/hooks.json
source: task: TASK__prewrite-gate-hardening
freshness_updated: 2026-10-19T06:06:13Z
---

# REQ — protected-artifact denial survives payload size and hook timeout

## Requirement

1. **Whole payload.** The prewrite gate decides on the complete hook payload.
   A Write, Edit, MultiEdit, or apply_patch to a C-05 protected artifact
   (TASK.json, PLAN.md, RECEIPTS.jsonl, task-local REVIEWS.jsonl,
   `doc/harness/goals/*.json`, the focus markers, runtime transcripts) is
   checked without the former fixed 64 KiB truncation and denied when the
   payload can be read, parsed and checked within the memory and runtime
   limits below, with no explicit escape set. The shared
   `_lib.read_hook_input` 64 KiB default stays for other hooks; the gate passes
   an exact `max_chars` for the payload it has read in full. The payload bytes
   decode as UTF-8 with `surrogateescape` whatever the locale, so a stray
   invalid byte cannot empty the payload. The Codex wrapper decodes the tool
   name the same way before it dispatches, and the gate child and the Codex
   fallback parse the same bytes with the same function.
2. **Codex child timeout or crash.** When the Codex PreToolUse wrapper's gate
   child times out, exits nonzero with no output, or cannot start, the wrapper
   still denies C-05 protected-artifact targets. It uses prewrite_gate's own
   classifiers through `prewrite_gate.protected_artifact_decision`; no second
   copy of the protected table exists. Every other write stays fail-open
   (C-12), and `HARNESS_SKIP_PREWRITE` is honored.
3. **Budgets.** The Claude PreToolUse prewrite timeout is 10 s, the C-12
   maximum. The Codex gate child budget is 3.0 s, at least 1.5 s below the
   Codex outer hook timeout (5 s, owned by `install.py` and part of the Codex
   hook trust hash). The remaining 2 s is reserved for wrapper startup, child
   termination and fallback; it does not guarantee they finish before the
   outer timeout (see accepted residual risk).
4. **Escape semantics are stated as implemented.** `HARNESS_SKIP_PREWRITE` and
   `HARNESS_DISABLE_SCOPE_LOCK` apply to every write while set in the
   runtime's environment. No surface calls them one-shot. The deny hint
   `escape: HARNESS_SKIP_PREWRITE=1 <retry>` is unchanged.

## Evidence (2026-09-28)

- A scratch harness repo denied a 10-byte Write to `TASK__x/TASK.json` with
  C-05 but allowed a 70,000-byte Write to the same path and to `PLAN.md`
  (recorded in `doc/harness/GUIDE__how-harness-works.md` §4.6).
- `sys.stdin.read(n)` on a pipe can allocate `n` bytes for a read. On Python
  3.12.14 with a 200,000-character mixed ASCII/non-ASCII pipe payload,
  `read(256 MiB)` and `read(1 GiB)` raised `MemoryError` under a 512 MiB
  address-space limit, and `read(sys.maxsize)` raised it with no limit. With a
  small ASCII payload only `read(1 GiB)` failed. The result depends on payload
  shape and interpreter internals, and `read_hook_input` turns a
  `MemoryError` into `{}` and an allow, so the gate reads stdin to EOF and
  passes the exact length instead of a fixed huge cap.
- One idle gate call on an installed tree without `.pyc` measured about 0.65 s
  against the old 1.5 s Codex child budget and 3 s Claude timeout.

## Accepted residual risk

- **Killed Claude hook.** Claude Code runs the tool when a hook is killed at
  its timeout. The decision rides on stdout, so a killed gate cannot deny. The
  10 s budget makes this unlikely; it is not closed.
- **Killed Codex wrapper.** If Codex kills the whole wrapper at 5 s, or the
  in-process fallback itself fails, the write proceeds. The fallback parses
  the same payload again, so when the payload's own size is what made the
  child slow, the fallback can also run past the 2 s reserve.
- **Payload too large for memory.** A payload that cannot be read or parsed
  in memory still parses to `{}` and is allowed; the gate cannot name the
  targets of a payload it cannot read. The failure threshold depends on
  available memory, payload shape and interpreter overhead; no minimum
  payload size is guaranteed safe from memory exhaustion.
- **Explicit escape.** `HARNESS_SKIP_PREWRITE=1` still bypasses C-05. Making
  C-05 non-bypassable needs a user decision and is out of scope here.
- **Shell writes.** Bash and shell file mutation are outside the gate (C-05).

## Verification

- `tests/test_prewrite_gate_payload_size_and_timeout.py`: large Write, Edit,
  MultiEdit, multi-MiB and non-ASCII payloads denied directly; a payload with
  an invalid UTF-8 byte denied by the gate, the wrapper and the fallback under
  a strict stdin decoder; large Write and
  apply_patch denied through `hook_pre_tool_use.py`; the fallback matrix for
  timeout, nonzero exit, spawn failure, real timeout, real crash, pass-through,
  SKIP, non-harness directories and fallback failure; the no-duplicate-table
  check; the env-escape wording and pattern-doc checks.
- `tests/test_hooks_json.py`: the prewrite PreToolUse timeout is 10.
- `tests/test_codex_hook_wrappers.py`: the child budget leaves at least 1.5 s of
  the outer Codex timeout.
- Normative pattern docs: `doc/harness/patterns/prewrite-gate.md`,
  `doc/harness/patterns/scope-lock.md`.

## Explicit migration control data

Derived from the current user request to remove compatibility execution and
retain migration only: the entire `doc/harness/archive/goal-migration/` tree,
including originals, manifest and lock, is protected by C-05 under
`legacy-data-migration`. Retired Goal records use that same owner. Route
conversion through `wbs(action="migrate")`; direct edits must not alter the
resumable transaction or fabricate evidence. The meaningful regression is
`test_migration_manifest_and_originals_are_hook_protected`.

Neutral batch pool metadata under `doc/harness/runtime/batches/` is protected
under `batch-pool-runtime`; use `batch_state.py` lifecycle commands. This
preserves the new immutable WBS owner association independently of a copied
caller binding. The owner field is a derived proof mechanism, not an
additional user requirement.
