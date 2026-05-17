---
name: maintain
description: |
  Inspection-only: display REVIEW pile + confirm Tier C contract drift.
  Background hygiene (Tier A/B auto-apply, doc classification) runs
  automatically at SessionStart via hygiene_scan.py. This skill handles
  only what requires user judgment: Tier C drift and REVIEW-queue items.

  Trigger keywords: "maintain", "contract drift", "CLAUDE.md 정리",
  "규약 정비", "contracts 꼬임", "harness upgrade cleanup".
user-invocable: true
allowed-tools: Read, Bash, Edit, AskUserQuestion
---

## Voice

Direct, terse. Show diffs, ask once per item, apply. Never bulk-rewrite.
No subagent spawn. No oh-my-claudecode:writer dependency.

## When to run

- User says "maintain", SessionStart emitted [hygiene-review], Tier C drift is pending, or user wants the REVIEW queue.

## Flow

### Phase 0: Load pending state

```bash
_PENDING="doc/harness/.maintain-pending.json"
[ -f "$_PENDING" ] && python3 -c "
import json, sys
data = json.load(open('$_PENDING'))
print(f'Pending: {len(data)} item(s)')
for e in data[:5]:
    print(f'  [{e.get(\"kind\",\"?\")}] {e.get(\"path\",\"?\")}')
" || echo "No pending items."
```

If no pending items: report clean state, exit.

### Phase 1: REVIEW queue inspection (read-only display)

For each entry with `kind == "review"` in `.maintain-pending.json`:
- Read the file (if it still exists).
- Display: path, freshness, reference_count, superseded_by/distilled_to signals.
- DO NOT auto-edit or auto-remove. Display only.

User can then manually act or add frontmatter fields (`superseded_by`,
`distilled_to`) to influence next hygiene cycle classification.

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

`doc_hygiene.py` stages archive moves at SessionStart but does NOT commit
(see C-16 "Commit timing"). Commit accumulates here, on user demand.

Detect staged archive renames:

```bash
_STAGED=$(git status --porcelain | awk '/^R/ && / -> .*\/_archive\// {print}')
_N=$(echo -n "$_STAGED" | grep -c .)
```

If `_N == 0`: skip this phase.

If `_N >= 1`:

```
AskUserQuestion:
  Question: "Hygiene has staged N archive move(s). Commit them in one batch?"
  Options:
    - A) Commit batch — single commit "hygiene: batch archive (N files)"
    - B) Skip — keep staged for later
```

On A: list each staged rename in the commit body, then
`git commit -m "hygiene: batch archive (<N> files)" -m "<body>"`.
Body lists each `src -> dest` and the maintain_restore.py command.

On B: no-op. The renames stay staged.

### Phase 3: Update pending file

Rewrite `.maintain-pending.json` with remaining entries only (atomic write via python3 json.dump + tempfile).

### Phase 4: Report

```
Maintain report
  REVIEW items displayed: N
  Tier C applied: X  deferred: Y  skipped: Z
  Hygiene archives committed: K (or "skipped: K staged")
  Pending remaining: M
```

## Safety invariants

- Never bulk-rewrite CONTRACTS.md — Edit managed block only.
- Never touch CONTRACTS.local.md.
- Never spawn subagents.
- REVIEW display is read-only; Tier C asks one AskUserQuestion per item, never batched.
