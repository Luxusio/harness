# Changelog

## 1.0.0 (2026-03-20)

First stable release. harness is a Claude Code plugin that turns any single-root repository into an AI operating system with persistent memory, automatic workflow routing, and validation loops.

### Phase 1: Plugin skeleton
- 8 specialized agents (orchestrator, requirements-curator, brownfield-mapper, implementation-engineer, test-engineer, refactor-engineer, docs-scribe, browser-validator)
- 12 skills (setup + validate + 10 hidden procedures: feature, bugfix, test-expansion, refactor, docs-sync, decision-capture, brownfield-adoption, validation-loop, architecture-guardrails, repo-memory-policy)
- SessionStart hook for context injection and Stop hook for session verification
- Plugin manifest, settings, and directory conventions

### Phase 2: Setup outputs
- Inference-application guide with 3 confidence tiers (HIGH/MEDIUM/LOW)
- Greenfield/brownfield detection and branching
- 24 template files for control plane generation
- Dynamic approvals population from actual project structure
- Dynamic docs/index.md generation
- Idempotency handling (initial/overwrite/incremental modes)
- Placeholder reference table with 19 substitution variables

### Phase 3: Runtime loop
- Orchestrator wired to invoke skills by file path (read-and-follow protocol)
- CLAUDE.md as declarative reference surface (routing tables, memory rules, format specs)
- SessionStart hook upgraded to inject actual file content (manifest, approvals, decisions)
- Working-state tracking via current-task.yaml
- Formalized memory write conventions (recent-decisions, unknowns, session summary)
- Structured handoff protocol between skills (Handoff/Result blocks)
- Orchestrator reads skill procedures directly via file paths

### Phase 4: Hardening
- arch-check.sh with real enforcement (Node.js/ESLint/madge, Python/ruff, Go/go-vet)
- Architecture boundary rules via arch-rules.yaml (fallback for repos without native lint)
- Memory compaction (50-entry threshold with archival for recent-decisions)
- Multi-session continuity via last-session-summary.md
- Graceful degradation when tools are not installed

### Phase 5: Release readiness
- Packaging with documented install paths (project-scope and user-scope)
- README rewritten as user-facing product page
- `/harness:validate` diagnostic skill for control plane health checks
- Version bumped to 1.0.0
- LICENSE copyright updated
- Final consistency sweep
- Removed redundant workflow summaries — orchestrator reads skills directly

### Known limitations (v1.0)
- Monorepo/polyglot projects not supported (planned for v1.1)
- Browser validation requires external tooling
- Memory promotion (hypothesis → confirmed) is manual
- System is prompt-driven and depends on Claude Code infrastructure
