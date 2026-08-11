# Phase 6: Write PLAN artefacts

Sub-file for plan/SKILL.md Phase 6. Always runs.

---

## 6.1 Artifact writer

PLAN.md, PLAN.meta.json, and AUDIT_TRAIL.md are written through
the harness MCP `write_plan` tool. Do not use
`scripts/write_plan.py`; it is a legacy compatibility shim.

## 6.2 Assemble PLAN.md content

Materialise plan content from in-memory review state into `/tmp/plan_content.md`.

**Restore point comment** — if Phase 0.5 captured one, prepend as the very first line:
```
<!-- plan restore point: restore-points/pre-plan-<timestamp>.md -->
```
Omit if no restore point.

**Required sections:** objective, `Original Request / Intent Summary`, scope in, scope out, `NOT in scope`, `What already exists`, target files/surfaces, acceptance criteria (stable IDs AC-001+), verification contract, `Durable Docs Decision`, `Error & Rescue Registry`, `Failure Modes Registry`, `Dream state delta`, `Cross-phase themes`, doc-sync expectation, risk/rollback (if `risk_level: high`), next implementation step.

### Original Request / Intent Summary

Every PLAN.md must preserve the user's request or intent in a committed-process
artifact, because task-local `REQUEST.md` may be absent, thin, or gitignored. If
`REQUEST.md` exists, summarize it and cite it. If it is absent, gitignored, or
under 15 non-empty lines, write a concise intent summary from the current user
prompt and explicitly label the source as `conversation summary`. Do not rely on
`REQUEST.md` alone for future intent reconstruction.

### Durable Docs Decision

Every PLAN.md must classify documentation impact before develop starts. This is
a judgment step, not a blanket REQ requirement. Use one of these outcomes in the
reason: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`.
Then list which durable docs should be created or updated:

```md
## Durable Docs Decision
REQ: doc/<area>/REQ__<name>.md | n/a
GUIDE: doc/<area>/GUIDE__<name>.md | n/a
ADR: doc/<area>/ADR__<name>.md | n/a
POLICY: doc/<area>/POLICY__<name>.md | n/a
Reason: <one sentence>
```

Use `REQ` when the task changes behavior or constraints that implementation
and QA must satisfy: existing screen state, filters, search, sorting, loading,
empty/error states, localization/copy/visibility, click/input interactions,
API request or response shape, status codes, auth/session behavior, validation,
compatibility, externally consumed side effects, or observable bugfixes.
New pages, admin/backoffice screens, routes, controllers, and endpoints are REQ-required even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Do not write `REQ: n/a` when the plan creates or changes observable UI/API behavior; if uncertain, choose the REQ and let develop refine the exact path. A concrete REQ path is required before develop starts for observable UI/API/backoffice work. If target files or surfaces include observable UI, API, backoffice/admin screens, routes, controllers, or endpoints and the decision says `REQ: n/a`, treat that as a blocking plan defect: revise the PLAN before Phase 6 and do not defer this to close.

Happy path: when request text, user feedback, target surfaces, or known file
paths imply observable UI/API/mobile/native/desktop behavior, pick the REQ path
proactively. If no suitable REQ exists, select a new path such as
`doc/ui/REQ__mobile-reader-navigation.md`; develop will create/update it with
a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` before source edits. Safety gates exist
only to catch misses, not as the normal discovery mechanism.

When the change affects harness process, agent instructions, coding patterns, or
verification practice but not a product/runtime contract, prefer `GUIDE` or
skill/pattern docs and write `REQ: n/a` with `Pattern/skill doc enough` in the
reason. For purely mechanical, test-only, or internal refactors, write
`No durable doc needed` and name the unchanged durable knowledge surface.

Use `GUIDE` when the task establishes reusable coding, design, testing, or
implementation guidance. Use `ADR` when it makes a significant technical
choice with alternatives or tradeoffs. Use `POLICY` only for external
security, legal, data-handling, approval, licensing, or organizational
constraints that harness cannot fully enforce by itself.

Write docs under the DDD-style area or bounded-context folder, for example
`doc/ui/REQ__filter-bar.md`, `doc/api/REQ__oauth-login.md`,
`doc/auth/ADR__token-storage.md`, or `doc/common/GUIDE__coding-style.md`.
Use `n/a` when a type is not needed; the reason must say which durable
knowledge surfaces are unchanged. Reasons like "additive change" or "covered
by PLAN.md" are invalid because additive observable behavior still needs a
durable contract.

### Review Status table (end of PLAN.md)

Assemble from phase-transition summaries and AUDIT_TRAIL rows already produced
by the plan review phases. Do not create another chronological file.

```
## Review Status

| Phase | Ran | Voices | Confirmed | Disagree | User Challenges |
|-------|-----|--------|-----------|----------|-----------------|
| 1 CEO | yes | dual | <N> | <N> | <N> |
| 2 Design | <yes/skipped> | <dual/—> | <N/—> | <N/—> | <N/—> |
| 3 Eng | yes | dual | <N> | <N> | <N> |
| 4 DX | <yes/skipped> | <dual/—> | <N/—> | <N/—> | <N/—> |

**Auto-decided:** <N> | **Taste surfaced:** <N> | **User Challenges:** <N>
**Execution mode:** <light/standard>
```

### Plan Review Report

```
## Plan Review Report

| Phase | Ran | Status | Findings |
|-------|-----|--------|----------|
| 1 CEO Review | yes | complete | <N> confirmed |
| 2 Design Review | <yes/no (no UI scope)> | — | — |
| 3 Eng Review | yes | complete | <N> confirmed |
| 4 DX Review | <yes/no (no DX scope)> | — | — |

**VERDICT:** REVIEWED — plan has passed the full dual-voice pipeline.
```

If AUDIT_TRAIL.md absent/unreadable: placeholder table with all "—" and verdict `NO AUDIT TRAIL — run /plan for full review pipeline.`

No harness policy boilerplate. Keep concise and executable.

## 6.3 Write PLAN.md via MCP

```text
write_plan {
  task_id: "TASK__<id>",
  artifact: "plan",
  content: "<PLAN.md content>"
}
```

## 6.4 Assemble PLAN.meta.json

Write `/tmp/plan_meta.json`:
```json
{
  "author_role": "plan-skill",
  "planning_mode": "<value from task pack>",
  "execution_mode": "<light|standard>",
  "dual_voice_phases": ["phase1", "phase2", "phase3", "phase4"],
  "critic_plan": "removed"
}
```

## 6.5 Write PLAN.meta.json via MCP

```text
write_plan {
  task_id: "TASK__<id>",
  artifact: "plan-meta",
  content: "<PLAN.meta.json object as JSON>",
  meta: { "execution_mode": "<light|standard>" }
}
```

## 6.6 Acceptance criteria

Keep the stable acceptance criteria directly in PLAN.md. Do not assemble or
write a second status ledger; review and QA completion is recorded in the
unified `RECEIPTS.jsonl` lifecycle stream.

Audit rows from earlier phases are also written through MCP:

```text
write_plan {
  task_id: "TASK__<id>",
  artifact: "audit",
  content: "<AUDIT_TRAIL.md table row>"
}
```

## 6.8 Learnings write-back (capture-when-fresh, non-blocking)

When you discover something genuinely useful during the task — a real bug, a workaround that saved time, a pattern that surprised you, a tooling gotcha — log it the moment you find it, **while it's fresh**. Log only concrete, reusable facts at discovery time; leave the log untouched when there is no durable learning.

A good entry names a concrete fact + a concrete fix, both groundable in files / commands / outputs. Examples of what passes the bar:

- `/plugin marketplace add` does NOT expand bash subshell `$(pwd)` — `/plugin` is a Claude Code slash command, not a shell command. Use `./` or a literal path.
- A duplicated task ledger can drift from PLAN.md; keep acceptance intent in one document.
- Plan artifacts are MCP-owned; direct Write/Edit and legacy CLI handshakes are not the canonical path.

Examples that do NOT pass the bar (do not log these):
- "I completed Phase 3" — narration, not learning.
- "The plan was clear" — vague reflection, not a durable fact.
- "I used the Edit tool" — tool usage is not insight.

Schema (one JSON line per entry, append to `doc/harness/learnings.jsonl`):

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
# echo '{"ts":"'"$_TS"'","type":"operational|pitfall|eureka|feedback","skill":"plan","branch":"'"$_BRANCH"'","key":"SHORT_KEY","insight":"FACT + FIX","source":"observed"}' >> doc/harness/learnings.jsonl
```

`type=operational` for tooling/syntax/path facts. `type=pitfall` for traps to avoid. `type=eureka` for first-principles discoveries that contradict conventional wisdom. `type=feedback` for user-stated preferences that should shape future behavior. Creates file if absent. Silent-fail on write error. Never blocks task close.

## 6.9 Close session

Set `plan_session_state: closed` in TASK_STATE.yaml. Task is now ready for implementation.

## 6.10 Completion report

```
STATUS: <DONE | DONE_WITH_CONCERNS | BLOCKED>

Task:    TASK__<id>
Plan:    doc/harness/tasks/TASK__<id>/PLAN.md

Phases run:        <list, e.g. 0, 1, 2, 3, 4, 5, 6>
Execution mode:    <light/standard>
Auto-decided:      <N> decisions
Taste surfaced:    <N> items
User Challenges:   <N> items
Deferred scope:    <N> items (see deferred-scope.md)
Review summary:    <N> phase-summary rows
```

- **DONE_WITH_CONCERNS** — any of: phase ran single-voice degraded; User Challenge unresolved; convergence guard issues.
- **BLOCKED** — Phase 6 MCP artifact write failed. (Review findings alone are never BLOCKED — use DONE_WITH_CONCERNS.)
