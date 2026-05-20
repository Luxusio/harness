---
name: maintain
description: |
  Inspection-only: display REVIEW pile + confirm Tier C contract drift.
  Background hygiene runs at SessionStart via hygiene_scan.py. This skill
  handles only user-judgment items: Tier C drift, REVIEW queue, runbook
  candidates.

  Trigger keywords: "maintain", "contract drift", "CLAUDE.md 정리",
  "규약 정비", "contracts 꼬임", "harness upgrade cleanup".
user-invocable: true
allowed-tools: Read, Bash, Edit, AskUserQuestion
---

## Voice

Direct, terse. Show diffs, ask once per item, apply. Never bulk-rewrite.
No subagent spawn. No oh-my-claudecode:writer dependency.

## When to run

- User says "maintain", [hygiene-review] fired, Tier C drift is pending, or REVIEW queue is wanted.
- User wants a discovered setup command remembered, or [harness-runbooks] reports pending candidates.

## Flow

### Phase 0: Load pending state

```bash
python3 - <<'PY'
import json, pathlib
p=pathlib.Path("doc/harness/.maintain-pending.json")
d=json.load(open(p)) if p.exists() else []
print(f"Pending: {len(d)} item(s)")
for e in d[:5]: print(f"  [{e.get('kind','?')}] {e.get('path','?')}")
PY
python3 plugin/scripts/runbook_memory.py list
```

If no pending items and no runbook candidates: report clean state, exit.

### Phase 1: REVIEW queue inspection (read-only display)

For each entry with `kind == "review"` in `.maintain-pending.json`:
- Read the file (if it still exists).
- Display: path, freshness, reference_count, superseded_by/distilled_to signals.
- DO NOT auto-edit or auto-remove. Display only.

User may then add frontmatter fields (`superseded_by`, `distilled_to`).

### Phase 2: Tier C drift confirmation (one item at a time)

For each entry with `kind == "tier_c_drift"`:

```
AskUserQuestion:
  Question: "Contract drift detected: <reason>. How to proceed?"
  Options:
    - A) Apply — I will make the Edit now
    - B) Defer — keep in pending for next session
    - C) Skip — remove from pending without applying
```

On A: apply via Edit to CONTRACTS.md managed block only. Re-run lint to verify.
On B: leave entry in `.maintain-pending.json`.
On C: remove entry from `.maintain-pending.json` (atomic write).

Never batch multiple Tier C items into one AskUserQuestion.

### Phase 2.5: Staged hygiene archives (batch commit)

Detect staged archive renames:

```bash
_STAGED=$(git status --porcelain | awk '/^R/ && / -> .*\/_archive\// {print}')
_N=$(echo -n "$_STAGED" | grep -c .)
```

If `_N == 0`: skip. If `_N >= 1`:

```
AskUserQuestion:
  Question: "Hygiene has staged N archive move(s). Commit them in one batch?"
  Options:
    - A) Commit batch — single commit "hygiene: batch archive (N files)"
    - B) Skip — keep staged for later
```

On A: commit with body listing each `src -> dest` and restore command.
On B: no-op. The renames stay staged.

### Phase 2.6: Runbook candidates

If `runbook_memory.py list` reports candidates, read
`plugin/skills/maintain/runbook-candidates.md` and follow it exactly. Ask one
candidate at a time; approve moves it to `doc/harness/runbooks.yaml`, defer
leaves it pending, skip removes it.

### Phase 3: Update pending file

Rewrite `.maintain-pending.json` with remaining entries only (atomic write).

### Phase 4: Report

```
Maintain report
  REVIEW items displayed: N
  Tier C applied: X  deferred: Y  skipped: Z
  Hygiene archives committed: K (or "skipped: K staged")
  Runbook candidates approved: A  deferred: B  skipped: C
  Pending remaining: M
```

## Safety invariants

- Never bulk-rewrite CONTRACTS.md — Edit managed block only.
- Never touch CONTRACTS.local.md.
- Never spawn subagents.
- REVIEW display is read-only; Tier C asks one AskUserQuestion per item, never batched.
