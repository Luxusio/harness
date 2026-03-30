# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-03-30

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
