---
name: autopilot
description: |
  Turn a product idea into a complete shipped implementation plan and run it
  through repeated harness cycles. Use when the user asks to build a product,
  app, SaaS, tool, game, website, platform, or end-to-end feature and wants the
  agent to clarify direction, choose a technical stack, define the feature set,
  then keep planning, developing, QA/UX reviewing, and filling gaps until the
  product is shippable or a real blocker/user stop occurs.
argument-hint: <product-idea-or-goal>
user-invocable: true
---

# Autopilot Product Builder

Use this skill for product-building work that is too broad for a single
implementation prompt. The job is to turn an underspecified product request
into a direction-locked product brief, choose a stack with the user, decompose
the work into shippable slices, and run harness loops until the product reaches
the agreed bar.

> **Codex runtime notes**
> - Codex does not have Claude's `Skill()` chaining primitive. When this skill
>   says to invoke `harness:run`, read `plugin-codex/skills/run/SKILL.md` and
>   execute its phases inline, or resume the active harness task via MCP tools.
> - AskUserQuestion is conversational on Codex. Ask the same questions in prose
>   with clear options and wait for the user's reply.
> - Use bare MCP tool names on Codex: `task_start`, `task_context`,
>   `task_verify`, `task_close`, `write_critic_qa`, `write_critic_ux`,
>   `write_handoff`, and `write_doc_sync`.
> - If `spawn_agent` is available, use it for independent QA/UX/review voices.
>   If not, run the methodology inline and record the same harness artifacts.

Autopilot never bypasses harness. Each implementation slice goes through the
harness run flow. QA/UX failures feed back into development. Missing product,
design, technical, or test coverage discovered late becomes new planned work
unless it is outside the agreed product boundary.

## Operating Principles

- Clarify before building. Do not start source work until product direction,
  target user, success criteria, and stack are explicit enough to defend.
- Ask hard questions early, then stop asking routine implementation questions.
  Once direction and stack are locked, make conservative product and engineering
  calls inside the agreed boundary.
- Prefer complete product behavior over demo-only scaffolding. Define onboarding,
  core workflows, empty states, error states, persistence, admin/ops needs,
  observability, docs, and tests when they are relevant to the product.
- Treat QA and UX failures as development inputs. Fix them and re-run the
  relevant harness verification instead of explaining around them.
- Keep autonomy bounded. Continue until done, blocked by environment or missing
  user authority, or explicitly stopped by the user.

## Phase 0: Repo And Harness Readiness

Check current repo state:

```bash
git status --short
test -f doc/harness/manifest.yaml && sed -n '1,160p' doc/harness/manifest.yaml || true
find . -maxdepth 2 -type f \( -name package.json -o -name pyproject.toml -o -name go.mod -o -name Cargo.toml -o -name Gemfile -o -name "*.sln" \) -print
```

If harness is not set up, ask to run `/harness:setup` first. If there is an
active task, call `task_context` and decide whether to resume it or create a new
autopilot product track.

## Phase 1: Product Direction Lock

Ask questions until the following are explicit. Bundle related questions in one
reply when possible.

Required product decisions:

- Target user and buyer, if different.
- Core problem and desired outcome.
- Product category and primary workflow.
- Must-have MVP behaviors.
- Explicit non-goals for the first version.
- Success criteria: what must be true for "done".
- Data sensitivity, auth, roles, compliance, and risk constraints.
- UX bar: utilitarian internal tool, polished SaaS, consumer app, game, landing
  page, API/developer tool, or another concrete experience type.

When the user gives a vague request, produce a short product brief and ask:

```text
I think the product direction is:
- User:
- Core workflow:
- MVP:
- Non-goals:
- Done means:

A) Lock this direction and choose stack
B) Adjust the brief
C) Narrow the MVP
```

Do not claim perfect certainty. "Direction locked" means the remaining unknowns
are normal implementation details, not product-defining choices.

## Phase 2: Technical Stack Lock

Inspect the repo before suggesting a stack. Prefer the existing stack when it can
ship the product cleanly. If there is no existing stack, propose 2-3 viable
options with tradeoffs.

Cover these decisions:

- Runtime/framework/language.
- UI framework and styling system, if UI exists.
- Data model, storage, migrations, seed/demo data.
- Auth/session strategy, if applicable.
- API boundary and client/server split.
- Test strategy: unit, integration, browser, API, CLI, desktop.
- Dev command, test command, build command, and deployment assumptions.

Ask the user to lock the stack:

```text
Recommended stack: <stack>
Why: <short reason>
Tradeoffs: <short tradeoffs>

A) Lock recommended stack
B) Choose alternative: <option>
C) I will specify the stack
```

After stack lock, do not revisit it unless QA/UX or implementation evidence
shows the chosen stack cannot meet the product goal.

## Phase 3: Product Backlog And Slice Plan

Define the full feature set the product needs for the agreed bar, but only
detail the nearest 1-3 slices. Treat backlog items as user-value hypotheses,
not implementation tasks. See `doc/harness/patterns/autopilot-agile-loop.md`.
Each slice must be a thin vertical workflow that harness can run independently.

For each slice, define:

- User-visible behavior.
- User value.
- Hypothesis.
- Data/API behavior.
- UX states: loading, empty, error, success, permissions, responsive states.
- Acceptance criteria.
- Required tests and QA/UX lens.
- Dependencies on earlier slices.

Classify slices:

- `MVP` means required before the product can be considered shippable.
- `Hardening` means required for reliability, security, or maintainability.
- `Follow-up` means useful but outside the agreed first release.

Ask once before implementation:

```text
I will implement these MVP slices in order:
1. ...
2. ...
3. ...

Deferred follow-ups:
- ...

A) Start autopilot implementation
B) Reorder or adjust slices
C) Stop at planning
```

When the user wants long-running unattended execution, persist the locked queue
before implementation:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/autopilot_runner.py init \
  --product "<locked product brief>" \
  --stack "<locked stack>" \
  --slice "slice-001:<first MVP slice>" \
  --slice "slice-002:<second MVP slice>"
```

Use `--force` only when the user explicitly wants to replace the current
`doc/harness/autopilot.yaml`.

## Phase 4: Harness Execution Loop

For each MVP and hardening slice:

1. Start or resume a harness task with a clear slug for the slice.
2. Execute the harness run flow from `plugin-codex/skills/run/SKILL.md`.
3. Let harness perform plan -> develop -> verify -> close.
4. If QA returns FAIL, send the findings back through harness develop. Keep the
   retry loop active until PASS, BLOCKED_ENV, or the run skill's retry limit.
5. If UX returns FAIL or `task_close` reports `ux-* PASS in CRITIC__ux.md`,
   treat the UX finding as required product work for that slice. Fix it and
   re-run the relevant UX/QA verification.
6. If harness discovers missing durable docs, REQ gaps, tests, or product
   behavior, add them to the current slice unless they are outside the locked
   product boundary.

Do not collapse multiple unrelated slices into one task just to move faster.
Preserve harness evidence and close gates.

For 24h-style runs, use the persistent runner after Phase 1-3 are locked:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/autopilot_runner.py loop \
  --max-hours 24 \
  --require-harness-close \
  --require-review-before-next \
  --command-template 'codex exec --full-auto "{prompt}"'
```

The runner stops on done, `BLOCKED_ENV`, `USER_DECISION_REQUIRED`,
`AUTOPILOT_STOP`, max attempts, stale heartbeat recovery, or the time budget.
It writes `doc/harness/autopilot-events.jsonl` and
`doc/harness/runtime/autopilot-heartbeat.json`. Use `recover` after an
interrupted terminal to move stale `running` slices back to retry/block state.
Failure classes and retry policy are documented in
`doc/harness/patterns/autopilot-failure-policy.md`; the runner stores
`failure_class`, `recommended_action`, and `retryable` on each failed slice.
It is a queue/checkpoint runner, not permission to invent missing product
decisions.

Before unattended execution, run preflight. Treat `preflight: WARN` as guidance:
fix the issue when it would improve the next slice, but do not stop the loop for
warning-only findings. Treat `preflight: BLOCK` as a required pause because the
next slice would build on missing review evidence or known failed evidence:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/autopilot_runner.py preflight \
  --require-review-before-next
```

## Phase 5: Iteration Review And Backlog Rewrite

After each harness-closed slice, perform an iteration review before continuing:

1. Demo the current product state from the target user's perspective.
2. Record whether the user workflow is complete, partial, or blocked.
3. Compare QA and UX evidence against the slice hypothesis.
4. Capture learnings and backlog changes.
5. Select the next highest-value thin vertical slice and state why.

Use the runner to persist this learning:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/autopilot_runner.py review \
  --slice-id "<slice-id>" \
  --demo-result pass \
  --user-workflow-status complete \
  --qa-result PASS \
  --ux-result PASS \
  --learning "<what changed about product understanding>" \
  --backlog-change "<what changed in scope/order>" \
  --next-slice-id "<next-slice-id>" \
  --next-slice-reason "<why this is now the highest-value slice>"

python3 ${HARNESS_PLUGIN_ROOT}/scripts/autopilot_runner.py replan \
  --next-slice-id "<next-slice-id>" \
  --next-slice-reason "<why this slice is next>"
```

The runner stores the review quality as `review_quality`,
`quality_warnings`, and `quality_blockers`. Warnings are pre-collision guidance:
missing learnings, missing backlog rationale, partial demo/workflow evidence, or
UX findings should usually become backlog changes before the next run. Blockers
are hard evidence that the current workflow failed or QA is not passable. Finish
or rework the review before enabling `--require-review-before-next`.

Natural flow preference: use review and preflight to surface problems before the
agent collides with harness close gates. Hard gates exist for unattended safety,
but the normal loop should make the next action obvious before a command fails.

## Phase 6: Gap Discovery Loop

After all planned MVP slices pass:

1. Re-read the product brief, stack decision, PLAN/HANDOFF files, QA/UX critics,
   and changed files.
2. Look for missing workflows, broken end-to-end continuity, untested critical
   paths, rough UX states, placeholder content, security holes, and deployment
   gaps.
3. Create additional harness slices for any gap that blocks the agreed "done"
   criteria.
4. Continue until no blocking gaps remain.

Use dogfooding where relevant: run the product like the target user would. For
browser or desktop products, actually interact with the UI when tools are
available. For APIs and CLIs, execute realistic commands and failure cases.

## Stop Conditions

Autopilot stops only when one of these is true:

- All MVP and hardening slices are closed with fresh QA PASS and required UX
  PASS, and gap discovery finds no blocker.
- A genuine environment blocker is recorded with `task_blocked` or harness
  returns `BLOCKED_ENV`.
- The user explicitly stops, narrows, or pauses the product.
- A high-risk product or architecture decision appears that was not covered by
  the direction/stack lock.

Final report:

```text
AUTOPILOT DONE
Product:
Stack:
Closed tasks:
Remaining follow-ups:
Verification:
Known risks:
```
