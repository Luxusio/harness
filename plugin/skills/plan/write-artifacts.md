# Phase 6: Write PLAN artefacts

Sub-file for plan/SKILL.md Phase 6. Always runs.

---

## 6.1 Transition session to write_open

Update `PLAN_SESSION.json`:
```json
{"state": "write_open", "phase": "write", "source": "plan-skill"}
```
Set `plan_session_state: write_open` in TASK_STATE.yaml.

## 6.2 Assemble PLAN.md content

Materialise plan content from in-memory review state into `/tmp/plan_content.md`.

**Restore point comment** — if Phase 0.5 captured one, prepend as the very first line:
```
<!-- plan restore point: restore-points/pre-plan-<timestamp>.md -->
```
Omit if no restore point.

**Required sections:** objective, scope in, scope out, `NOT in scope`, `What already exists`, target files/surfaces, acceptance criteria (stable IDs AC-001+), verification contract, `Error & Rescue Registry`, `Failure Modes Registry`, `Dream state delta`, `Cross-phase themes`, doc-sync expectation, risk/rollback (if `risk_level: high`), next implementation step.

### Review Status table (end of PLAN.md)

Assemble from phase-transition summaries and `REVIEW_LOG.jsonl`:
```bash
_RL="doc/harness/tasks/TASK__<id>/REVIEW_LOG.jsonl"
_RL1=$(grep '"phase":"1"' "$_RL" 2>/dev/null | tail -1 || echo "")
_RL2=$(grep '"phase":"2"' "$_RL" 2>/dev/null | tail -1 || echo "")
_RL3=$(grep '"phase":"3"' "$_RL" 2>/dev/null | tail -1 || echo "")
_RL4=$(grep '"phase":"4"' "$_RL" 2>/dev/null | tail -1 || echo "")
```

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

## 6.3 Write PLAN.md via CLI

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact plan \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_content.md
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

## 6.5 Write PLAN.meta.json via CLI

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact plan-meta \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/plan_meta.json
```

## 6.6 Assemble CHECKS.yaml content

Write `/tmp/checks_content.yaml` with all acceptance criteria from PLAN.md.

**Schema per AC (Acceptance Ledger):**
```yaml
- id: AC-001
  title: "<what passes when this AC is satisfied>"
  status: open                    # open | implemented_candidate | passed | failed | deferred
  kind: functional                # functional | verification | doc | performance | security | bugfix
  owner: developer                # developer | qa-browser | qa-api | qa-cli
  completeness: 7                 # 0-10 plan-time completeness score (3=shortcut, 7=happy path, 10=all cases). Immutable after plan close.
  root_cause: ""                  # REQUIRED when kind=bugfix. One-line confirmed cause (Iron Law). Update_checks blocks promotion to implemented_candidate without it.
  reopen_count: 0                 # auto-increments on transition into 'failed'
  last_updated: <ISO8601>
  evidence: ""                    # file:line | test name | HANDOFF section (filled by develop)
  note: ""                        # optional free-form
```

All ACs start `status: open`, `reopen_count: 0`. Later skills (develop, qa) mutate via `${CLAUDE_PLUGIN_ROOT}/scripts/update_checks.py` — **never direct edit** (prewrite gate rejects).

## 6.7 Write CHECKS.yaml via CLI

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact checks \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --checks /tmp/checks_content.yaml
```

## 6.8 Learnings write-back (non-blocking)

Reflect on session. Log operational discoveries that would save 5+ minutes in a future session. Good candidates: build quirks, ordering constraints, env var requirements, path assumptions, concurrency issues from Phase 3, project-specific patterns that differ from defaults.

Skip obvious facts and transient errors.

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
# One JSON line per learning:
# echo '{"ts":"'"$_TS"'","type":"operational","skill":"plan","branch":"'"$_BRANCH"'","key":"SHORT_KEY","insight":"DESCRIPTION","source":"observed"}' >> doc/harness/learnings.jsonl
```

Creates file if absent. Silent-fail on write error. Never blocks.

## 6.9 Close session

```json
{"state": "closed", "phase": "closed", "source": "plan-skill"}
```
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
Review log:        <N> entries (see REVIEW_LOG.jsonl)
```

- **DONE_WITH_CONCERNS** — any of: phase ran single-voice degraded; User Challenge unresolved; convergence guard issues.
- **BLOCKED** — Phase 6 CLI write failed. (Review findings alone are never BLOCKED — use DONE_WITH_CONCERNS.)
