# harness plugin changelog

All notable changes to the harness Claude Code plugin.

## [Unreleased]

### Fixed

- `task_context`, `task_verify`, and `task_close` now deduplicate source-derived Git and review-fingerprint work within each request. `task_close` clears the request cache, requires the changed-path fingerprint map, HEAD, and final-gate receipt streams to remain stable and available, and reruns all freshness gates immediately before closing. Failed or timed-out Git path snapshots now fail closed.
- Git changed-path and receipt snapshots now use NUL-delimited names where applicable, preserve POSIX backslashes and other path identity end to end, fingerprint symlinks without following them, recheck pathname identity after reads, and reject unreadable or special path types instead of accepting ambiguous or blocking evidence.
- Every parent-index gitlink OID is fingerprinted, including uninitialized submodules; initialized submodules also include checkout HEAD and worktree identity. Staged gitlink updates and clean checkout moves cannot evade review routing or close-time freshness comparison, and symlinked gitlink worktrees fail closed.
- Codex root hooks now validate an existing lifecycle registration directly instead of recursively scanning every rollout and taking the registration lock on every event. Wrapper child work also shares an event-level deadline below the configured Codex hook timeout, preventing sequential gate checks from overrunning PreToolUse, UserPromptSubmit, or PostToolUse.
- Codex lifecycle task binding now accepts non-group/other-writable root-owned workspace ancestors common in container mounts while retaining no-symlink checks and current-user ownership for the task directory itself.
- Global Codex hooks no longer create or restore lifecycle watcher registrations in repositories without the nearest Git root's Harness setup manifest, so nested independent projects cannot inherit an outer opt-in.
- Initialized submodule `.git` control files must resolve inside the parent Git common directory and report the validated worktree, preventing external gitdir traversal for direct and nested gitlinks.
- Git-root detection consistently treats `.git` files as repository boundaries, and submodule worktree-binding validation is uncached so in-request gitdir retargeting fails closed.
- Git roots confirmed earlier in a request cannot fall back to synthetic empty snapshots if metadata disappears, and submodule HEAD reads are explicitly bound to one validated gitdir/worktree tuple.
- Harness setup detection canonicalizes symlinked working directories before selecting the nearest Git root, preventing lexical-path inheritance of an outer manifest.
- Codex now exposes one implicitly invocable `$harness:run` entry skill for repository mutation; hooks, setup routing, and write-gate recovery point to it while plan/develop/review prompts stay internal.
- Codex cachebuster installs preserve prior cache versions so hooks already loaded by running sessions keep valid executable paths until those sessions restart.
- Review routing now treats `AGENTS.md` and `CLAUDE.md` as behavioral artifacts and scans committed changes from the task baseline, including deleted security-sensitive lines.
- Codex setup finalization now requires the public run policy, implicit invocation, and the `$harness:run` mutation route before stamping setup complete.
- Setup now emits the runtime manifest schema (`version: 5`, `type`, nested `qa`) used by QA routing.
- Codex installs include every setup sub-file and template referenced by the setup skill.
- Setup finalization gitignores Goal, task-pack, review, runtime, and opt-in goal-payload artifacts from one canonical list.
- Repair/Upgrade migrates legacy flat manifests without dropping unknown fields and stamps `.version` only after verification passes.
- Routing migration and verification target `AGENTS.md` on Codex and `CLAUDE.md` on Claude Code.
- Setup validation now rejects future/ambiguous manifests, ineffective or tracked operational ignores, managed-path symlinks, placeholder critics, missing contracts, and unstated QA/runtime completion.
- Plugin payload updates are staged before activation, and setup writes preserve permissions and roll back on failure.
- Codex native `create_goal` calls now receive an immediate harness synchronization reminder through PostToolUse.
- UserPromptSubmit performs zero Git commands, labels its child as Codex, and keeps the outer hook timeout above the child deadline; authoritative diff freshness remains in task verification and close gates.
- Goal prompt metadata now records the actual runtime instead of labeling Codex events as Claude.

## [2.2.0] — 2026-04-16

### Removed
- `plugin/agents/harness.md` — the orchestrator agent is gone. The main Claude session now routes through native Goal orchestration and internal sub-skills directly. No more agent-switching.

### Changed
- `plugin/skills/setup/bootstrap.md` §3.4 — setup emits an idempotent `## Harness routing` block (marker: `<!-- harness:routing-injected -->`) into the user's CLAUDE.md that maps normal use to native Goal orchestration plus `Skill(harness:setup)`; child-task execution, plan, develop, and review helpers are internal.
- `plugin/skills/setup/bootstrap.md` §3.4 — added migration step that strips the legacy `Default agent is harness` line from existing CLAUDE.md on Repair/Upgrade runs.
- `plugin/skills/setup/SKILL.md` — routing-injection now references the bootstrap §3.4 template with idempotency marker.
- `plugin/skills/setup/verify-report.md` — verifies routing block presence and (new) pytest availability for CLI/library projects with pytest-based test_command.
- `plugin/CLAUDE.md` — reframed intro: harness rules apply to any caller running the canonical loop (skills, MCP clients), not a specific orchestrator agent.
- `CLAUDE.md` (repo root) and `CLAUDE_CODE_HARNESS_BLUEPRINT.md` — aligned with the routing-first wording.

### Migration (existing users)
Run `/harness:setup` and choose Repair or Upgrade. Setup will:
1. Strip any legacy `Default agent is harness` line from your CLAUDE.md
2. Inject the new `## Harness routing` block (idempotent — safe to re-run)
3. Stamp `doc/harness/.version` with 2.2.0

### Fixed
- `plugin/skills/setup/SKILL.md` — `_HARNESS_VERSION` was stuck at 2.0.0 despite plugin.json being 2.1.0. Now synced to 2.2.0.
- Removed 3 stale test files (`tests/test_plugin_agent_contracts.py`, `tests/test_prompt_budget.py`, `tests/test_workflow_surface_lock.py`) that referenced non-existent paths (`plugin/settings.json`, `plugin/agents/critic-runtime.md`, `plugin/scripts/hctl.py`, `plugin/docs/orchestration-modes.md`) that never existed on `feature/v3`.
