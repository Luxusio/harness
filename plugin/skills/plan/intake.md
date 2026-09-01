# Phase 0: Intake + Context

Sub-file for plan/SKILL.md Phase 0. Loaded at skill start.

---

## Phase 0.0-S: Spawned Session Detection (always first)

```bash
_SPAWNED="false"
[ "${HARNESS_SPAWNED:-}" = "1" ] && _SPAWNED="true"
```

If spawned: keep `auto_decide: true` in working context (and optional recovery scratch when one exists), auto-resolve ALL AskUserQuestion (including premise gate), suppress upgrade/usage-stats prompts, emit prose completion at end. Log: `[spawned-mode] Auto-decide ON.`

## Phase 0.0: Session Recovery (resume case)

```bash
_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
_PLAN_STATUS=$(grep -A8 '^## Review Status' doc/harness/tasks/TASK__<id>/PLAN.md 2>/dev/null || true)
echo "BRANCH=$_BRANCH ROWS=$_ROW_COUNT TASTE=$_OPEN_TASTE CHALLENGE=$_OPEN_CHALLENGE"
```

If `_PLAN_STATUS` is empty, treat the planning review as fresh. If it is
present, emit a short welcome-back synthesis strictly from PLAN.md and resume
without fabricating partially persisted review phases:

```
Welcome back to TASK__<id> on branch <_BRANCH>.
The existing PLAN.md contains a completed Review Status section. Re-run the
review only when the user requested plan changes; otherwise continue to develop.
```

Never infer partial review progress from another file.

## Phase 0.1: Open planning context

Use working context by default. Create PLAN_SESSION.json only when the plan must
survive a turn/context boundary or delegated planning requires shared recovery
state. Its absence is normal and never an error.

## Phase 0.1.5: Load project learnings

`tail -5 doc/harness/learnings.jsonl` — incorporate relevant operational knowledge. Log count.

## Phase 0.2: task_start

```
mcp__plugin_harness_harness__task_start { task_id: "<ARGUMENTS>" }
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

Keep `_CROSS_MODEL_VOICE` in working context. If optional recovery scratch is
already in use, also store it as a `cross_model_voice` hint. A resumed Phase
1/3/4 MUST re-probe current availability and re-apply
`HARNESS_DISABLE_CROSS_MODEL` before accepting that hint:

```bash
python3 - <<PY
import json, pathlib
p = pathlib.Path("doc/harness/tasks/TASK__<id>/PLAN_SESSION.json")
if p.is_file():
    try:
        d = json.loads(p.read_text())
        if not isinstance(d, dict):
            raise ValueError("scratch root must be an object")
        d["cross_model_voice"] = "$_CROSS_MODEL_VOICE"
        p.write_text(json.dumps(d))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass  # malformed legacy scratch is equivalent to absent scratch
PY
```

**Kill switch:** `HARNESS_DISABLE_CROSS_MODEL=1` forces `agent` regardless of CLI availability. Session-wide while set.

**Never blocks.** If the probe errors out entirely, default to `agent` and log one row to `learnings.jsonl` with `type=operational` + `key=cross-model-probe-fail`.

## Phase 0.4: Read task pack

Read in order: `TASK.json`, `REQUEST.md` (if exists), existing `PLAN.md` (if exists), files in `must_read`.

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

**Trigger:** REQUEST.md absent, gitignored, OR < 15 non-empty lines.

Before emitting anything, provisionally apply the same fail-closed eligibility
rules as Phase 0.7 to the current conversation plus code/task context. When
intent, scope, acceptance, and verification are already explicit and every
compact predicate passes, use the conversation summary and skip this offer.
If any input is ambiguous or an escalation trigger appears, select full and
continue with the offer below. Phase 0.7 rechecks the provisional compact choice
after source/context inspection.

Emit one AskUserQuestion:
- A) Clarify inline → 3 goal-sharpening questions (outcome / NOT in scope / success)
- B) Skip → proceed to 0.5 with thin REQUEST.md (premise challenge will surface the gap)
- C) Re-run setup first → user has unfinished project framing; `Skill(harness:setup)` owns pre-plan scope-sharpening in harness

After setup (if chosen): `find doc/ -name "*design*.md" -newer doc/harness/tasks/TASK__<id>/TASK.json` — if found, read and append it as `## Design Context` to the final PLAN.md.

Skip cleanly if the trigger is not met or provisional compact eligibility
passes. Never loop.

**Note:** harness does not ship a separate `office-hours` skill. `Skill(harness:setup)` fills the pre-planning / scope-sharpening role through its interactive intake flow.

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
2. Re-invoke the plan skill with the original task slug:
   `Skill("harness:plan", "<original-task-slug>")`

The re-run will pick up the restored PLAN.md as its starting point and rebuild
its embedded Review Status and Decision Audit Trail.

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

## Phase 0.7: Planning procedure branch

Do not add or infer a new TASK.json execution mode. `compat.execution_mode`
remains `standard` or `micro`; micro keeps its existing explicit no-plan
semantics. For a standard task, select one planning procedure:

- **compact** only when the request is bounded, unambiguous, low blast radius,
  and every relevant acceptance/test/scope decision is already evident.
- **full** when the user asks for a full plan, classification inputs are absent
  or uncertain, or any escalation trigger applies.

Escalation triggers are security/auth/permissions/secrets, data/schema/
migration work, public API or observable UI behavior, destructive operations,
dependency/platform/configuration/workflow-control changes, unclear acceptance
or a material user choice, cross-component scope, and high-risk maintenance.
Unknown means full. File count alone never proves low risk.

The compact branch performs a single code/context assessment, asks only genuine
User Challenges, and then publishes the same canonical PLAN.md contract. It
must include objective, in/out scope, stable ACs, allowed/test/forbidden paths,
verification, and Durable Docs Decision. Runtime review, conditional security
review, QA, receipts, close fingerprint, Goal continuation, and install checks
are identical to full planning.

Before compact publication, inspect the named code/docs and check each
escalation family again against discovered dependencies, callers, data flows,
configuration, and observable effects. Record the assessment as one truthful
compact review row. If inspection reveals any escalation trigger, missing
context, cross-component impact, or unresolved decision, abandon compact and
restart at full Phase 1 before Phase 5/6. The initial prompt classification is
never sufficient by itself.

**Auto-decide detection:** check `auto_decide: true` in task pack or flag. Independent of planning procedure. If set, retain it in working context and optional scratch, CEO defaults SELECTIVE EXPANSION, DX defaults DX POLISH, and apply "What Auto-Decide Means" rules.
