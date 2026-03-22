---
name: docs-sync
description: Use after meaningful changes to keep repo-local memory, indexes, and durable notes aligned with the codebase.
allowed-tools: Read, Glob, Grep, Write, Edit, Bash
user-invocable: false
---

## Trigger

Activate after any workflow that changed:
- Code behavior or structure
- Project rules or constraints
- Domain knowledge
- Architecture boundaries
- Operational procedures

## Procedure

### 1. Identify what changed
Review the work just completed:
- New behavior → domain docs may need updating
- New decision → constraint or ADR needed
- Bug fixed → runbook or findings update
- Architecture changed → architecture docs update
- Risk discovered → unknowns or approvals update
- New feature requested → requirement spec created or status updated

### 2. Update the right files
Delegate to `harness:docs-scribe` with specific instructions:

**Domain knowledge changes:**
- Update or create the relevant `harness/docs/domains/` file
- Keep focused on what Claude needs to know for future work

**Constraint or rule changes:**
- Update `harness/docs/constraints/project-constraints.md`
- Only for confirmed rules, not hypotheses

**Operational knowledge:**
- Update `harness/docs/runbooks/development.md` or create specific runbooks
- Focus on debugging insights, setup steps, common pitfalls

**Architecture changes:**
- Update `harness/docs/architecture/` if boundaries or patterns changed
- Mark inferred patterns as hypotheses

**Requirement status changes:**
- If a feature was implemented, update the REQ file status from `accepted` to `implemented`
- If validation passed, update from `implemented` to `verified` and tick acceptance criteria
- Update the `## History` section with a timestamped status transition entry

### 3. Update the index
- Review `harness/docs/index.md` — add new files, remove stale ones
- Keep the index navigable (grouped by purpose)
- Include all files in `harness/` that a future session might need to find, including gitignored runtime files (`current-task.yaml`, `last-session-summary.md`) — mark them as "(gitignored)" in the index
- Do NOT include template files from `plugin/skills/setup/templates/`

### 4. Update recent decisions
- Append to `harness/state/recent-decisions.md` for any durable change
- Format: `- [YYYY-MM-DD] <type>: <short description>`

### 5. Update unknowns
- Move resolved unknowns out of `harness/state/unknowns.md`
- Add new unknowns discovered during work
- Keep unknowns scoped and actionable

### 6. Verify sync quality
- No stale references
- No hypotheses recorded as facts
- No duplicate entries
- Index matches actual files

## Guardrails

- Do not create docs for the sake of docs — only if it helps future work
- Do not repeat code line-by-line in docs
- Keep every entry concise: prefer one clear sentence over a paragraph
- Do not store noise from transient chat
