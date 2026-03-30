# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-03-31

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

# Template sync rule (CRITICAL)
- This repo IS the harness plugin source. Every change to runtime behavior (paths, hook output schemas, gitignore patterns, agent definitions, critic rubrics, skill logic) MUST also update the corresponding setup templates under `plugin/skills/setup/templates/`.
- Affected template surfaces: `gitignore-harness`, `CLAUDE.md`, manifest.yaml template, critic playbook templates, launch.json, .mcp.json.
- If a script constant changes (e.g. TASK_DIR, MANIFEST path), grep `plugin/skills/` for matching references and update them.
- Setup skill (`plugin/skills/setup/SKILL.md`) procedure text must stay consistent with actual generated output.
