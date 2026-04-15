# Phase 0: Intake + Context

Sub-file for plan/SKILL.md Phase 0. Loaded at skill start.

---

## Phase 0.0-S: Spawned Session Detection (always first)

```bash
_SPAWNED="false"
if grep -q "^spawned_session: true" doc/harness/tasks/TASK__<id>/TASK_STATE.yaml 2>/dev/null; then
  _SPAWNED="true"
fi
[ "${HARNESS_SPAWNED:-}" = "1" ] && _SPAWNED="true"
```

If spawned: set `auto_decide: true` in `PLAN_SESSION.json`, auto-resolve ALL AskUserQuestion (including premise gate), suppress upgrade/telemetry prompts, emit prose completion at end. Log: `[spawned-mode] Auto-decide ON.`

## Phase 0.0: Session Recovery (resume case)

```bash
grep "phase-summary" doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md 2>/dev/null | tail -10
```

If phase-summary rows present: extract highest completed N, emit `Resuming TASK__<id>: last completed phase = <N>. Continuing from Phase <N+1>.`, load prior consensus summaries as `## Prior phase findings` for remaining phases, skip all phases ≤ N. Never blocks.

## Phase 0.1: Open session

Write `PLAN_SESSION.json`:
```json
{"state": "context_open", "phase": "context", "source": "plan-skill"}
```
Set `plan_session_state: context_open` in TASK_STATE.yaml.

## Phase 0.1.5: Load project learnings

`tail -5 doc/harness/learnings.jsonl` — incorporate relevant operational knowledge. Log count.

## Phase 0.2: task_start

```
mcp__harness__task_start { task_id: "<ARGUMENTS>" }
```
Extract: `risk_level`, `planning_mode`, `compat.execution_mode`, `workflow_locked`, `maintenance_task`, `ui_scope`, `dx_scope`, `must_read`.

## Phase 0.4: Read task pack

Read in order: `TASK_STATE.yaml`, `REQUEST.md` (if exists), existing `PLAN.md` (if exists), files in `must_read`.

## Phase 0.4.1: Git context intake

```bash
git log --oneline -20 2>/dev/null || true
git diff --stat HEAD 2>/dev/null || git diff --stat 2>/dev/null || true
```
Store as `GIT_CONTEXT`. Prepend `## Git context` block to Voice A/B briefs in Phases 1 and 3.

## Phase 0.4.2: Base branch detection

```bash
_REMOTE=$(git remote get-url origin 2>/dev/null || echo "")
if echo "$_REMOTE" | grep -q "github.com"; then
  _BASE=$(gh pr view --json baseRefName -q .baseRefName 2>/dev/null || gh repo view --json defaultBranchRef -q .defaultBranchRef.name 2>/dev/null || echo "")
elif echo "$_REMOTE" | grep -q "gitlab"; then
  _BASE=$(glab mr view -F json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('target_branch',''))" 2>/dev/null || echo "")
fi
if [ -z "$_BASE" ]; then
  _BASE=$(git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||' || git rev-parse --verify origin/main 2>/dev/null && echo "main" || git rev-parse --verify origin/master 2>/dev/null && echo "master" || echo "main")
fi
```
Store as `BASE_BRANCH`.

## Phase 0.4.5: Prerequisite offer

**Trigger:** REQUEST.md absent OR < 15 non-empty lines.

If `plugin/skills/office-hours/SKILL.md` exists: emit one AskUserQuestion:
- A) Run office-hours prerequisite → invoke, resume at 0.5 with output
- B) Skip → proceed to 0.5
- C) Clarify inline → 3 goal-sharpening questions (outcome / NOT in scope / success)

If office-hours absent: emit C directly.

After office-hours: `find doc/ -name "*design*.md" -newer TASK_STATE.yaml` — if found, read and append as `## Design Context` to task pack. Log discovery to AUDIT_TRAIL via `--artifact audit`.

Skip cleanly if trigger not met. Never loop.

## Phase 0.5: Restore point

If prior PLAN.md exists:
```bash
_TS=$(date -u +%Y%m%dT%H%M%SZ)
mkdir -p doc/harness/tasks/TASK__<id>/restore-points/
cp doc/harness/tasks/TASK__<id>/PLAN.md \
   doc/harness/tasks/TASK__<id>/restore-points/pre-plan-${_TS}.md
cat >> doc/harness/tasks/TASK__<id>/restore-points/pre-plan-${_TS}.md << 'EOF'

## Re-run Instructions

Copy this file back over PLAN.md, then re-run plan skill with same task slug.
EOF
```
Record relative path in memory for Phase 6.2 restore-point comment.

## Phase 0.6: Scope detection

Read task pack text. Scan keywords (no grep bash).

**UI keywords:** ui_scope, frontend, component, css, html, react, vue, design system, stylesheet, layout, visual, button, modal, dashboard, sidebar, nav, dialog

**DX keywords:** dx_scope, api, cli, sdk, devex, developer experience, ergonomics, tooling, integration, plugin, endpoint, REST, GraphQL, gRPC, webhook, command, flag, argument, terminal, shell, library, package, npm, pip, import, require, developer docs, getting started, onboarding, debug, implement, error message

**2+ match threshold** per scope. False-positive exclusions: `\bpage\b` alone, `\bUI\b` acronym (thread/process), `\bapi\b` in API-keys, `\bcli\b` for non-developer tool.

**Structural DX overrides (set dx_scope=true immediately):** "product IS a developer tool"; "AI agent is primary user".

Honor existing `ui_scope:true` or `dx_scope:true` in task pack without re-eval.

## Phase 0.7: Execution mode branch

Read `compat.execution_mode`:
- **light**: skip dual voices in 1+3 (single-voice reasoning); skip 2 and 4 entirely regardless of scopes; narrow contract.
- **standard** (default): full pipeline; Phase 2 if ui_scope; Phase 4 if dx_scope.

**Auto-decide detection:** check `auto_decide: true` in task pack or flag. Independent of execution_mode. If set: record in PLAN_SESSION.json, CEO defaults SELECTIVE EXPANSION, DX defaults DX POLISH, apply "What Auto-Decide Means" rules.
