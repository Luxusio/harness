# CLAUDE.md
tags: [root, harness, bootstrap]
summary: Project entry point. Operating rules and doc registry reference.
always_load: [doc/CLAUDE.md]
updated: {{SETUP_DATE}}

@doc/CLAUDE.md

# Operating mode
- Default agent is harness — an execution harness with verdict invalidation.
- `doc/harness/manifest.yaml` is the initialization marker.
- Every repo-mutating task follows: plan -> critic-plan PASS -> implement -> runtime QA -> writer/DOC_SYNC -> critic-document (when needed) -> close.
- The only hard gate is at task completion: critic verdicts must PASS. Stale PASS (after file changes) does not count.
- DOC_SYNC.md is mandatory for all repo-mutating tasks.
- Browser-first QA is default for web frontend projects when manifest declares browser_qa_supported.
- Work in plain language. The harness routes requests and gates completion.
- Execution mode is selected per task: light (docs/small), standard (default), sprinted (cross-root/destructive).
- Runtime critic produces evidence bundles — structured proof required for every PASS verdict.
- Notes carry freshness metadata (current/suspect/stale); file changes automatically mark affected notes suspect.
- Maintain-lite runs at session end to detect entropy (stale tasks, orphan notes, broken chains) without writes.
- Protected artifacts have role ownership: PLAN.md=plan-skill, HANDOFF.md=developer, DOC_SYNC.md=writer, CRITIC__*.md=respective critic. Enforced by prewrite gate.
- Pre-plan source reads are blocked until plan session opens. Capability firewall prevents silent collapsed mode.
- User directives are captured in DIRECTIVES_PENDING.yaml and must be promoted before task close.
- Only one repo-mutating task may hold write focus at a time. If a second mutating request arrives, create or resume a separate task and keep it queued until the user explicitly switches focus or the current task closes.
- Short approvals such as `ㅇㅇ ㄱ` approve only the last explicit transition the harness proposed; they never authorize skipping task creation, planning, or critic gates.
- When an answer-lane exchange turns into repo mutation, the harness must first make the lane switch explicit and open planning before implementation.
- Use `mcp__plugin_harness_harness__task_context` as the canonical routing source before starting any task.
- Workflow control surface files (plugin/CLAUDE.md, hctl.py, hooks.json, setup templates, agent prompts) are write-locked for normal tasks; only maintenance tasks (maintenance_task=true) may modify them.
