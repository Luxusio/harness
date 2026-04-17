---
freshness: current
---

# Auto-Maintenance Pattern

The harness post-close pipeline (run/self-improvement.md) automatically fires
retrospectives and hygiene audits after each task close, surfacing output in the
developer's HANDOFF.

## Retro auto-trigger semantics

Threshold: `>= 3 tasks closed since the mtime of the most recent doc/harness/retros/*.md`.

- If no prior retros exist: threshold seeds from first task close (first 3 closes triggers first retro).
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

## HANDOFF Auto-ran section format

Every HANDOFF.md must include:

```markdown
## Auto-ran
- retro: doc/harness/retros/2026-04-17.md
- hygiene: 2 warnings (see stderr during promote_learnings run)
```

Or when nothing fired:
```markdown
## Auto-ran
- retro: (none, threshold not met — 1/3 tasks since last retro)
- hygiene: (none)
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
