---
freshness: current
updated: 2026-08-13
---

# Auto-Maintenance Pattern

The harness post-close pipeline (run/self-improvement.md) conditionally evaluates
learning candidate reporting and retrospectives after close. It stays non-gating and only
surfaces output that changed a durable artifact or needs attention.

## Learning candidate-reporting semantics

Automatic reporting requires at least one validated learning row bound to the
just-closed task id and TASK.json run_id. Knowledge rows use an allowlisted type,
canonical timestamp, bounded key and human insight; feedback rules also contain
trigger, action, and verification. One valid row from each distinct
receipt-verified closed task/run may meet the repetition threshold, but backlog
alone cannot trigger a run and duplicate rows from one run count once.
Diagnostic gate/crash/bypass/stop/codifier rows are not candidates. The raw
learnings ledger is append-only and the close-time pass never rewrites or prunes
it. Qualifying keys are reported only; the pass performs no durable pattern
writes. Applying a candidate requires a separately reviewed Harness task.

## Retro auto-trigger semantics

Threshold: `>= 3 receipt-verified tasks closed since the mtime of the most recent doc/harness/retros/*.md`.

- If no prior retros exist: threshold seeds from first task close (first 3 closes triggers first retro).
- A close counts only when TASK.json is safely readable, its fingerprint matches
  RECEIPTS.jsonl, and TASK.json publication mtime is newer than the cutoff. The
  TASK/status/TASK identity check stays inside one receipt transaction.
- Open, blocked, reopened, invalid, unsafe, and merely touched task directories do not count.
- The tasks root is descriptor-bound and rejects symlink, writable, or rebound state.
- The auto-trigger and retro report share `retro.py`'s verified-close predicate.
- Retro reads only validated metadata from a descriptor-bound, owner-controlled
  regular learning ledger; free-form insight text is never rendered.
- `--save` publishes through a descriptor-bound, no-follow retros directory and
  rejects unsafe existing report leaves.
- `retro.py --save` writes to `doc/harness/retros/<date>.md`.
- First-ever fire emits banner: `Auto-retro enabled. Silence with HARNESS_DISABLE_RETRO=1.`
- Pipeline wraps call in `|| true` — retro failure never blocks task close.

## Hygiene warn-only philosophy

Hygiene audits (`promote_learnings.py` `_audit_stale_files` + `_audit_contradictions`)
are warn-only. They write to stderr and never mutate `learnings.jsonl`.

Design intent: keep noise low. Warnings are actionable signals, not gates.

Filter rules:
- **stale-file**: flags entries whose `files[]` list contains paths that no longer exist.
- **contradiction**: flags same-key entries that are recent (<30 days apart) OR from the same source.
  Long-term evolution across different sources is intentionally not flagged.

## Auto-ran output format

The close-time pipeline prints:

```markdown
Auto-ran: retro=doc/harness/retros/2026-04-17.md
Auto-ran: hygiene=2 warnings
```

Or when nothing fired:
```markdown
Auto-ran: retro=(none, threshold not met — 1/3 tasks since last retro)
Auto-ran: hygiene=(none)
```

## HARNESS_DISABLE_* env vars

| Variable | Effect | Semantics |
|----------|--------|-----------|
| `HARNESS_DISABLE_RETRO` | Skip auto-retro | session-wide while set |
| `HARNESS_DISABLE_HYGIENE` | Skip hygiene audit | session-wide while set |
| `HARNESS_DISABLE_SCOPE_LOCK` | One-shot scope gate bypass | cleared after one bypass |
| `HARNESS_SKIP_INTERVIEW` | Setup skill auto-accepts defaults | session-wide while set |
| `HARNESS_SPAWNED` | Orchestrator-spawned session: auto-resolve prompts | session-wide while set |

See `plugin/CLAUDE.md §12` for the authoritative table.

## Pattern entries

| Pattern | Discovered | Source |
|---------|------------|--------|
| retro-threshold-semantics | 2026-04-17 | TASK__gstack-ideas-adoption |
| hygiene-warn-only | 2026-04-17 | TASK__gstack-ideas-adoption |
| handoff-auto-ran-format | 2026-04-17 | TASK__gstack-ideas-adoption |
