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
_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
_PHASE_ROWS=$(grep "phase-summary" doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md 2>/dev/null | tail -10)
_ROW_COUNT=$(echo -n "$_PHASE_ROWS" | grep -c . || echo "0")
_OPEN_TASTE=$(grep -c '|taste|' doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md 2>/dev/null || echo "0")
_OPEN_CHALLENGE=$(grep -c '|challenge|' doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md 2>/dev/null || echo "0")
echo "BRANCH=$_BRANCH ROWS=$_ROW_COUNT TASTE=$_OPEN_TASTE CHALLENGE=$_OPEN_CHALLENGE"
```

If `_ROW_COUNT` is zero → fresh task; skip recovery and go to Phase 0.1.

If `_ROW_COUNT ≥ 1` → emit a **welcome-back synthesis** (2-3 sentences) before resuming. Derive it strictly from the AUDIT_TRAIL phase-summary rows above; never fabricate a phase that didn't run:

```
Welcome back to TASK__<id> on branch <_BRANCH>.
Last completed phase: <highest N from phase-summary rows>. Consensus tallies: confirmed=<X>, disagree=<Y>, taste=<T>, challenges=<C>.
Outstanding: <"no blockers" if _OPEN_TASTE=0 AND _OPEN_CHALLENGE=0; else "<_OPEN_CHALLENGE> user challenges queued" or "<_OPEN_TASTE> taste items awaiting Phase 5 gate">.
```

Then load prior consensus summaries as `## Prior phase findings` for remaining phases and skip all phases ≤ N. Never blocks; on unreadable AUDIT_TRAIL fall back to the short `Resuming TASK__<id>: last completed phase = <N>` form and continue.

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

## Phase 0.3: Cross-model Voice B availability probe

Probe whether an external model (Codex or Gemini) is available for Voice B in the dual-voice phases. If available and not disabled, Phase 1/3/4 Voice B spawns routes through `omc ask codex|gemini` instead of the Agent tool — giving genuine cross-model adversariality instead of same-model Agent-B.

```bash
_CODEX_AVAIL=false
_GEMINI_AVAIL=false
_OMC_ASK_AVAIL=false
if command -v codex >/dev/null 2>&1; then _CODEX_AVAIL=true; fi
if command -v gemini >/dev/null 2>&1; then _GEMINI_AVAIL=true; fi
if command -v omc >/dev/null 2>&1 && omc ask --help 2>&1 | grep -q "claude\|codex\|gemini"; then
  _OMC_ASK_AVAIL=true
fi

# Kill switch honored first
if [ "${HARNESS_DISABLE_CROSS_MODEL:-}" = "1" ]; then
  _CROSS_MODEL_VOICE="agent"
elif [ "$_OMC_ASK_AVAIL" = "true" ] && [ "$_CODEX_AVAIL" = "true" ]; then
  _CROSS_MODEL_VOICE="codex"       # preferred: codex CLI via `omc ask codex`
elif [ "$_OMC_ASK_AVAIL" = "true" ] && [ "$_GEMINI_AVAIL" = "true" ]; then
  _CROSS_MODEL_VOICE="gemini"      # fallback: gemini CLI via `omc ask gemini`
elif [ "$_CODEX_AVAIL" = "true" ]; then
  _CROSS_MODEL_VOICE="codex-direct" # no omc ask; call `codex exec` directly
else
  _CROSS_MODEL_VOICE="agent"       # final fallback: same-model Agent tool
fi
echo "CROSS_MODEL_VOICE=$_CROSS_MODEL_VOICE"
```

**Store `_CROSS_MODEL_VOICE` in PLAN_SESSION.json** as `cross_model_voice` so Phase 1/3/4 Voice B spawn reads it without re-probing:

```bash
python3 - <<PY
import json, pathlib
p = pathlib.Path("doc/harness/tasks/TASK__<id>/PLAN_SESSION.json")
d = json.loads(p.read_text())
d["cross_model_voice"] = "$_CROSS_MODEL_VOICE"
p.write_text(json.dumps(d))
PY
```

**Kill switch:** `HARNESS_DISABLE_CROSS_MODEL=1` forces `agent` regardless of CLI availability. Session-wide while set.

**Never blocks.** If the probe errors out entirely, default to `agent` and log one row to `learnings.jsonl` with `type=operational` + `key=cross-model-probe-fail`.

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

1. Copy the block below (`## Original Plan State`) back over `doc/harness/tasks/TASK__<id>/PLAN.md`.
2. Clear AUDIT_TRAIL.md and CHECKS.yaml so the new run starts with a fresh ledger:
   `rm -f doc/harness/tasks/TASK__<id>/AUDIT_TRAIL.md doc/harness/tasks/TASK__<id>/CHECKS.yaml`
3. Re-invoke the plan skill with the original task slug:
   `Skill("harness:plan", "<original-task-slug>")`

The re-run will pick up the restored PLAN.md as its starting point and rebuild AUDIT_TRAIL + CHECKS from scratch. Phase-transition summaries from the prior run are not replayed — only the plan content is.

## Original Plan State

(verbatim prior PLAN.md contents — this file IS the restore payload)
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
