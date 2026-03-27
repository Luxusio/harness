---
name: maintain
description: Run periodic doc hygiene and structure maintenance, then request critic-document approval for semantic changes.
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
- Verify root `CLAUDE.md` registry matches actual roots on disk
- Use root `CLAUDE.md` registry + root-specific `CLAUDE.md` (not `doc/CLAUDE.md`)

### 3. Mechanical cleanup (auto-apply)
These changes are safe and do not need critic approval:
- Normalize note headers (tags, summary, updated fields)
- Rebuild root CLAUDE.md indexes to match actual files
- Update root `CLAUDE.md` registry to match actual roots
- Add missing superseded_by links where chains are obvious

### 4. Semantic proposals (need critic-document)
These changes need approval — prepare a proposal:
- Merging duplicate notes
- Archiving stale notes
- Creating new roots from overgrown common/
- Compacting note chains (collapsing superseded sequences)
- Deleting notes

For each proposal, delegate to `harness:critic-document` with:
```
Handoff:
  from: maintain
  proposed_action: <merge|archive|new-root|compact|delete>
  affected_notes: <list>
  rationale: <why this helps>
```

Only apply if critic-document returns PASS.

### 5. Optional architecture checks
If `.claude/harness/constraints/check-architecture.*` exists, invoke it and report findings.

### 6. Report
End with:
- **Cleaned**: mechanical fixes applied
- **Proposed**: semantic changes submitted to critic-document
- **Approved**: changes that passed critic-document
- **Rejected**: changes that failed critic-document
- **Stats**: total notes by type, notes per root, stale count
