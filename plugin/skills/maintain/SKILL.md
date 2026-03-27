---
name: maintain
description: Run periodic doc hygiene and structure maintenance, then request structure-critic approval for semantic changes.
argument-hint: [optional focus area]
context: fork
agent: Explore
user-invocable: true
allowed-tools: Read, Glob, Grep, Write, Edit, Agent
---

Inspect and clean the durable knowledge structure.

Optional focus from user: `$ARGUMENTS`

## Procedure

### 1. Scan note health
- Find all REQ__/OBS__/INF__ files across doc roots
- Check for: stale notes, duplicates, orphaned notes (not indexed), superseded chains

### 2. Scan index health
- Verify each root CLAUDE.md index matches actual files on disk
- Verify doc/CLAUDE.md registry matches actual roots on disk

### 3. Mechanical cleanup (auto-apply)
These changes are safe and do not need critic approval:
- Normalize note headers (tags, summary, updated fields)
- Rebuild root CLAUDE.md indexes to match actual files
- Update doc/CLAUDE.md registry to match actual roots
- Add missing superseded_by links where chains are obvious

### 4. Semantic proposals (need critic-structure)
These changes need approval — prepare a proposal:
- Merging duplicate notes
- Archiving stale notes
- Creating new roots from overgrown common/
- Compacting note chains (collapsing superseded sequences)
- Deleting notes

For each proposal, delegate to `harness:critic-structure` with:
```
Handoff:
  from: maintain
  proposed_action: <merge|archive|new-root|compact|delete>
  affected_notes: <list>
  rationale: <why this helps>
```

Only apply if critic-structure returns PASS.

### 5. Report
End with:
- **Cleaned**: mechanical fixes applied
- **Proposed**: semantic changes submitted to critic-structure
- **Approved**: changes that passed critic-structure
- **Rejected**: changes that failed critic-structure
- **Stats**: total notes by type, notes per root, stale count
