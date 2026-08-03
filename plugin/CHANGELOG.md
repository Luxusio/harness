# harness plugin changelog

All notable changes to the harness Claude Code plugin.

## [Unreleased]

### Fixed

- Setup no longer asks users to choose proactive routing, runtime-document injection, contract import, Health scoring, audience, prior workflow, Harness scope, or the final failure-avoidance policy. It consistently enables the recommended full loop, inserts a missing `@CONTRACTS.md` import without rewriting unrelated project-document content, configures Health scoring from every detected API/frontend test command, and writes the fixed C-100 scope constraint while retaining only useful purpose and verification discovery.
- Harness can now use a non-Git control workspace with explicit `source_git_roots`. Codex hooks and Claude write/Bash/Stop/subagent lifecycle entrypoints bind child-repository sessions to the parent task, lifecycle registrations separate control root from rollout cwd, and task baselines plus receipt HEAD/diff fingerprints cover every configured repository. Bounded parent behavioral files such as `AGENTS.md`, `CLAUDE.md`, contracts, and the manifest are baseline-tracked and close-fingerprinted so stale QA or review PASS evidence is invalidated safely.
- Git-backed control repositories now treat explicit `source_git_roots` additively: the parent remains a source binding, and only exact initialized direct gitlinks are added as independently scanned leaf services. Registration is never inferred or propagated to nested gitlinks, and independently owned linked worktrees must pass reciprocal gitdir, common-directory, checkout-backreference, and snapshot-stability validation. Existing active tasks whose binding set differs must be restarted under a new task ID without editing `TASK_BASELINE.json`; rolling back to a runtime with the former replacement semantics likewise requires new task IDs.
- Multi-Git setup and resume now fail closed for moved, missing, symlinked, nested, or shell-unsafe source-root names. Parent behavioral symlinks and protected-artifact symlink aliases are rejected, and setup routes project-document routing/import changes through one no-follow atomic helper.
- Codex review and QA completion no longer requires `list_agents`. Identity-bearing `wait_agent` status maps are accepted directly, while the lifecycle watcher also recognizes exec-wrapped `multi_agent_v1__spawn_agent` calls and matching `<subagent_notification>` completions without weakening start-time fingerprint freshness.
- `task_start` now reuses one bounded request-local Git snapshot, including HEAD, ancestry, and committed-path comparisons, preserves causal Git diagnostics, revalidates the baseline on resume, accurately distinguishes creation from resume, leaves concurrent Git index locks untouched, safely bounds optional manifest probing and atomically replaces its snapshot output, and returns an actionable `ready_with_warnings` result only after a valid scaffold exists. `write_plan` also accepts either audit rows or a complete Markdown audit table with natural spacing, normalizes the heading/header automatically, and explains incomplete or mismatched rows instead of rejecting natural input without useful guidance.
- Setup now gitignores unapproved runbook-candidate staging and the local marker that enables sensitive Goal hook-payload capture, and rejects either artifact when it is already tracked.
- `task_close` now writes a receipt-stream-bound close attestation that remains valid while later Goal children change the repository, and `task_start` clears it before reopening a closed task. Goal completion requires that bounded, no-follow artifact, rechecks it immediately before the terminal write, and fails closed for missing, damaged, or hand-labeled state.
- Task-owned source paths now survive clean commits from a validated, no-follow task baseline through review and verified install; Git-backed tasks require a generated baseline to pass the bounded reader contract before state creation, missing or unavailable baselines always fail closed with explicit task restart as the migration path, and unchanged pre-task dirt stays excluded after commit. Closing a task synchronizes its active Goal child, Goal completion requires fresh receipt-backed QA for every closed child, and explicitly restarting a terminal Goal reactivates it without discarding its queue. All registered Claude hooks are fail-safe, Codex QA prompts use fresh uniquely suffixed receipt-bound task names, and intermediate child messages cannot poison later `FINAL_ANSWER` completion.
- MCP task and Goal selectors are now confined to canonical repository-owned control-plane paths. `write_plan` validates bundled artifacts before any write, `task_blocked` no longer creates orphan packs for unknown tasks, and TASK_STATE, Goal, CHECKS, AUDIT, and active-marker control reads reject symlinked or special-file leaves without blocking. Present-invalid CHECKS ledgers fail closed, AC reconciliation preserves timestamps and indentation, and identity-mismatched or terminal per-session markers no longer shadow a live legacy task.
- Claude and Codex developer/code/security reviewer prompts now share tested byte-identical behavioral cores. The developer and named AC worker follow Ponytail's trace-first necessity/reuse/stdlib/native/existing-dependency ladder, lane-safe shared-root-cause proof loop, deletion/boring-clear preference, and known-ceiling discipline. `needs-coordinator-review` now branches before generic rollback so ownership is reassigned, the lane/AC is amended, or the user is escalated instead of retrying unchanged ownership; Codex worker spawn prompts explicitly load the developer role and produce that exact status. Upstream prerequisites and package dependencies are distinct, and package admission requires manifest/lockfile ownership. Reviewers add AC/scope claim verification, calibrated evidence, prompt-instruction isolation, setup-to-production-path-to-assertion test-effect checks, and applicable filesystem/process/TOCTOU security lenses.
- `task_context`, `task_verify`, and `task_close` now deduplicate source-derived Git and review-fingerprint work within each request. `task_close` clears the request cache, requires the changed-path fingerprint map, HEAD, and final-gate receipt streams to remain stable and available, and reruns all freshness gates immediately before closing. Failed or timed-out Git path snapshots now fail closed.
- Git changed-path and receipt snapshots now use NUL-delimited names where applicable, preserve POSIX backslashes and other path identity end to end, fingerprint symlinks without following them, recheck pathname identity after reads, and reject unreadable or special path types instead of accepting ambiguous or blocking evidence.
- Every parent-index gitlink OID is fingerprinted, including uninitialized submodules; initialized submodules also include checkout HEAD and worktree identity. Staged gitlink updates and clean checkout moves cannot evade review routing or close-time freshness comparison, and symlinked gitlink worktrees fail closed.
- Codex root hooks now validate an existing lifecycle registration directly instead of recursively scanning every rollout and taking the registration lock on every event. Wrapper child work shares an event-level deadline below the configured outer timeout, registration root resolution uses that same hard budget, and the single lifecycle watcher exclusively owns heavy review/QA receipt fingerprinting so slow Git I/O cannot overrun PreToolUse, UserPromptSubmit, or PostToolUse.
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
