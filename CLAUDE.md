# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-04-15

@doc/CLAUDE.md

# Operating mode
- Default agent is harness — an execution harness with verdict invalidation.
- `doc/harness/manifest.yaml` is the initialization marker.
- Canonical loop for every repo-mutating task: **plan → develop → verify → close**. Smallest coherent diff per step. No step skipped. See `plugin/CLAUDE.md` for the authoritative runtime rules.
- The only hard gate at task completion is `runtime_verdict: PASS`. Stale PASS (after touched-path changes) does not count — `task_verify` re-syncs and re-gates.
- DOC_SYNC.md + HANDOFF.md are mandatory developer outputs for all repo-mutating tasks.
- Browser-first QA is default for web frontend projects when `browser_qa_supported: true` in manifest.
- CHECKS.yaml is the per-task acceptance ledger. Plan creates ACs at `status: open`; develop promotes to `implemented_candidate`; the verification gate promotes to `passed` or reopens to `failed`. All CHECKS writes go through `plugin/scripts/update_checks.py`.
- Notes under `doc/**/*.md` carry `freshness: current|suspect|stale` + optional `invalidated_by_paths`. The SessionStart hook runs `plugin/scripts/note_freshness.py` and flips `current → suspect` on matching path changes.
- Protected artifacts (enforced by `plugin/scripts/prewrite_gate.py`): PLAN.md=plan-skill, CHECKS.yaml=plan-skill + update_checks CLI, HANDOFF.md=developer, DOC_SYNC.md=developer, CRITIC__runtime.md=qa-browser/qa-api/qa-cli.
- Pre-plan source writes are blocked until PLAN.md exists on the active task (plan-first rule).
- Only one repo-mutating task may hold write focus at a time. A second mutating request creates or resumes a separate task that stays queued until the user switches focus or the current task closes.
- Short approvals such as `ㅇㅇ ㄱ` approve only the last explicit transition the harness proposed; they never authorize skipping task creation, planning, or verify gates.
- When an answer-lane exchange turns into repo mutation, the harness must first make the lane switch explicit and open planning before implementation.

# Template sync rule (CRITICAL)
- This repo IS the harness plugin source. Runtime lives under `plugin/`. Every change to runtime behavior (paths, hook schemas, agent definitions, skill logic, script APIs) MUST stay internally consistent across `plugin/` — grep for the constant/path before landing the change.
- When a script API changes (e.g. `scripts/update_checks.py` flags, `_lib.SCHEMA_FIELDS`), grep `plugin/skills/` for every SKILL.md that calls the script and update the example invocations.
- The setup skill lives at `plugin/skills/setup/SKILL.md`; its procedure text must stay consistent with actual generated output and with the current runtime loop described above.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
