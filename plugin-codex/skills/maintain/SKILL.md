---
name: maintain
description: |
  Inspection-only: display REVIEW pile + confirm Tier C contract drift on
  Codex. Background hygiene (Tier A/B auto-apply, doc classification) runs at
  SessionStart via hygiene_scan.py exactly as on Claude. This skill handles
  what requires user judgment: Tier C drift and REVIEW-queue items.

  Trigger keywords: "maintain", "contract drift", "CLAUDE.md 정리",
  "규약 정비", "contracts 꼬임", "harness upgrade cleanup".
---

> **Codex runtime notes** (delta from Claude maintain):
> - **No `AskUserQuestion` structured tool.** Where Claude emits an AskUserQuestion at Tier C drift confirmation (Phase 2) and at the hygiene batch-commit gate (Phase 2.5), Codex emits the question + numbered options as plain prose and reads the user's reply on the next turn.
> - **MCP tool names are bare** on Codex; this skill calls no MCP write tools, so the rename is informational only.
> - **Env var is `HARNESS_PLUGIN_ROOT`** when sub-scripts are invoked, not `CLAUDE_PLUGIN_ROOT`.
> - **`apply_patch` replaces `Edit`** for the CONTRACTS.md managed-block patch in Phase 2 option A. The managed-block boundary still gates the patch — apply_patch must not cross the marker comments.
> - **Sub-file fallback.** No Codex-native sub-files in v1.5; this SKILL.md is the entire flow.

## Voice

Direct, terse. Show diffs, ask once per item, apply. Never bulk-rewrite. No subagent spawn.

## When to run

- User says "maintain" or SessionStart emitted `[hygiene-review]` in reminders.
- Tier C drift (HARD) is pending in `.maintain-pending.json`.
- User wants to inspect the REVIEW queue.

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

For each entry with `kind == "tier_c_drift"`, ask the user via conversational prose:

```
Contract drift detected: <reason>.
A) Apply — patch CONTRACTS.md managed block now (apply_patch).
B) Defer — keep the entry in .maintain-pending.json for next session.
C) Skip — drop the entry from pending without applying.
Reply A / B / C, or describe a different action.
```

Wait for the user's next turn before acting.

On A: apply_patch the CONTRACTS.md managed block (between the `harness:managed-begin` and `harness:managed-end` markers — NEVER outside). Re-run `contract_lint.py` to verify the patch resolved the drift without introducing a new violation.

On B: leave entry in `.maintain-pending.json`.

On C: remove entry from `.maintain-pending.json` (atomic write — read, filter, write to tempfile, rename).

Never batch multiple Tier C items into one ask. One conversational ask per item, sequentially, with full evidence between.

### Phase 2.5: Staged hygiene archives (batch commit)

`doc_hygiene.py` stages archive moves at SessionStart but does NOT commit
(see CONTRACTS.md C-16 "Commit timing"). Commit accumulates here, on user demand.

Detect staged archive renames:

```bash
_STAGED=$(git status --porcelain | awk '/^R/ && / -> .*\/_archive\// {print}')
_N=$(echo -n "$_STAGED" | grep -c .)
```

If `_N == 0`: skip this phase.

If `_N >= 1`, ask the user via prose:

```
Hygiene has staged <N> archive move(s). Commit them in one batch?
A) Commit batch — single commit "hygiene: batch archive (<N> files)".
B) Skip — keep staged for later.
Reply A / B.
```

On A: list each staged rename in the commit body, then
`git commit -m "hygiene: batch archive (<N> files)" -m "<body>"`.
Body lists each `src -> dest` and the `maintain_restore.py` command per entry.

On B: no-op. The renames stay staged.

### Phase 3: Update pending file

After processing all items, rewrite `.maintain-pending.json` with remaining
entries only (atomic write via python3 json.dump + tempfile + os.replace).

### Phase 4: Report

```
Maintain report
  REVIEW items displayed: N
  Tier C applied: X  deferred: Y  skipped: Z
  Hygiene archives committed: K (or "skipped: K staged")
  Pending remaining: M
```

## Safety invariants

- Never bulk-rewrite CONTRACTS.md — apply_patch the managed block only, bounded by the `harness:managed-begin` / `harness:managed-end` markers.
- Never touch CONTRACTS.local.md.
- Never spawn subagents (Codex has no Agent primitive in this skill's scope anyway; the invariant is stated explicitly so future ports don't drift).
- REVIEW display is read-only — no automated edits to doc files.
- Tier C: one conversational ask per item, never batched.

---

