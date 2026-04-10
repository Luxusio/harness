# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-04-07

@doc/CLAUDE.md

# Operating mode
- Default agent is harness — an execution harness with verdict invalidation.
- `doc/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> runtime QA -> critic-intent -> writer/DOC_SYNC -> critic-document (when needed) -> close.
- The only hard gate is at task completion: critic verdicts must PASS. Stale PASS (after file changes) does not count.
- DOC_SYNC.md is mandatory for all repo-mutating tasks.
- Browser-first QA is default for web frontend projects when manifest declares browser_qa_supported.
- Work in plain language. The harness routes requests and gates completion.
- Execution mode is selected per task: light (docs/small), standard (default), sprinted (cross-root/destructive).
- Runtime critic produces evidence bundles — structured proof required for every PASS verdict.
- Notes carry freshness metadata (current/suspect/stale); file changes automatically mark affected notes suspect.
- Maintain-lite runs at session end to detect entropy (stale tasks, orphan notes, broken chains) without writes.
- Protected artifacts have role ownership: PLAN.md=plan-skill, HANDOFF.md=developer, DOC_SYNC.md=writer, CRITIC__*.md=respective critic. Enforced by prewrite gate. For team tasks with an explicit synthesis owner, final runtime verification artifacts and final HANDOFF refresh are reserved to that synthesis owner once TEAM_SYNTHESIS.md is ready. `TEAM_PLAN.md` may also optionally declare `## Documentation Ownership` so `DOC_SYNC.md` / `CRITIC__document.md` are pinned to specific workers during the documentation pass.
- Pre-plan source reads are blocked until plan session opens. Capability firewall prevents silent collapsed mode.
- User directives are captured in DIRECTIVES_PENDING.yaml and must be promoted before task close.
- Only one repo-mutating task may hold write focus at a time. If a second mutating request arrives, create or resume a separate task and keep it queued until the user explicitly switches focus or the current task closes.
- Short approvals such as `ㅇㅇ ㄱ` approve only the last explicit transition the harness proposed; they never authorize skipping task creation, planning, or critic gates.
- When an answer-lane exchange turns into repo mutation, the harness must first make the lane switch explicit and open planning before implementation.

# Template sync rule (CRITICAL)
- This repo IS the harness plugin source. Every change to runtime behavior (paths, hook output schemas, gitignore patterns, agent definitions, critic rubrics, skill logic) MUST also update the corresponding setup templates under `plugin-legacy/skills/setup/templates/`.
- Affected template surfaces: `gitignore-harness`, `CLAUDE.md`, manifest.yaml template, critic playbook templates, launch.json, .mcp.json.
- If a script constant changes (e.g. TASK_DIR, MANIFEST path), grep `plugin-legacy/skills/` for matching references and update them.
- Setup skill (`plugin-legacy/skills/setup/SKILL.md`) procedure text must stay consistent with actual generated output.

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
