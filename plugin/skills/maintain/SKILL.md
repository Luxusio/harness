---
name: maintain
description: Core loop phase — entropy control for durable knowledge. Detects staleness, broken links, drift, dead artifacts, and supersede chain breaks.
argument-hint: [optional focus area]
context: fork
agent: Explore
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Agent
---

You are executing the **maintain** phase of the universal loop runtime.

Maintenance is not a side task. It is a core phase that keeps the knowledge base truthful and the repository navigable. Every completed task should leave the maintenance queue smaller, not larger.

Optional focus from user: `$ARGUMENTS`

## Entropy sources

These are the specific forms of entropy you detect and control:

| Entropy type | Signal | Action |
|-------------|--------|--------|
| Stale notes | `status: active` but `last_verified_at` is old or missing | Mark `freshness: stale`, queue re-verification |
| Broken supersede chains | `superseded_by` points to non-existent note | Fix link or flag for investigation |
| Orphaned notes | Note exists on disk but not in any root CLAUDE.md index | Add to index or flag for archive |
| Duplicate notes | Two active notes making the same claim | Propose merge (needs critic-document) |
| Drifted docs | Documentation contradicts current code/runtime behavior | Propose update or supersede |
| Dead task artifacts | Task folders with `status: closed` older than threshold | Archive or clean |
| Missing freshness fields | Notes without `status`, `freshness`, `last_verified_at` | Normalize headers |
| INF debris | Low-confidence INF notes never verified | Propose archive (needs critic-document) |
| Index drift | Root CLAUDE.md index doesn't match files on disk | Rebuild index |
| Registry drift | Root `CLAUDE.md` registry doesn't match actual roots | Update registry |

## Procedure

### 1. Scan note health
- Find all REQ__/OBS__/INF__ files across doc roots
- Check for: stale notes, duplicates, orphaned notes, broken supersede chains
- Check freshness fields: `status`, `freshness`, `last_verified_at`, `confidence`
- Identify INF notes that should have been verified by now (`verify_by` is actionable)

### 2. Scan index health
- Verify each root CLAUDE.md index matches actual files on disk
- Verify root `CLAUDE.md` registry matches actual roots on disk
- Check that note status in indexes matches note status in files

### 3. Scan task artifact health
- Check `.claude/harness/tasks/` for abandoned tasks (no RESULT.md, no recent updates)
- Check for tasks with `status: blocked_env` that may be unblocked now
- Check maintenance queue (`.claude/harness/maintenance/QUEUE.md`) for pending items

### 4. Mechanical cleanup (auto-apply)
These changes are safe and do not need critic approval:
- Normalize note headers (add missing `status`, `freshness`, `last_verified_at` fields)
- Rebuild root CLAUDE.md indexes to match actual files
- Update root `CLAUDE.md` registry to match actual roots
- Add missing `superseded_by` links where chains are obvious
- Mark notes as `freshness: stale` when `last_verified_at` is outdated
- Clear resolved items from maintenance queue

### 5. Semantic proposals (need critic-document)
These changes need approval — prepare a proposal for each:
- Merging duplicate notes
- Archiving stale notes
- Archiving unverified INF debris
- Creating new roots from overgrown common/
- Compacting note chains (collapsing long superseded sequences)
- Deleting notes (prefer archive)

For each proposal, delegate to `harness:critic-document` with:
```
Handoff:
  from: maintain
  proposed_action: <merge|archive|new-root|compact|delete>
  affected_notes: <list>
  rationale: <why this helps>
  entropy_type: <which entropy type this addresses>
```

Only apply if critic-document returns PASS.

### 6. Optional architecture checks
If `.claude/harness/constraints/check-architecture.*` exists, invoke it and report findings.

### 7. Update maintenance queue
- Remove completed items from QUEUE.md
- Add newly discovered items
- Prioritize: broken chains > stale active notes > orphans > dead artifacts > INF debris

### 8. Report

End with:
- **Entropy found**: counts by type
- **Auto-fixed**: mechanical fixes applied
- **Proposed**: semantic changes submitted to critic-document
- **Approved**: changes that passed critic-document
- **Rejected**: changes that failed critic-document
- **Queue**: remaining maintenance items (prioritized)
- **Stats**: total notes by type, notes per root, stale count, freshness distribution
