# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-04-15

@CONTRACTS.md
@doc/CLAUDE.md

## Harness routing
<!-- harness:routing-injected -->

**Default = harness task routing.** On Codex, invoke the public `$harness:run`
entry skill for repository mutation before editing; it loads the internal
canonical workflow. Native `/goal` owns explicit goals and broad work. Plain
repo-mutating requests are also valid task intake. Hooks inject context; they
do not create tasks automatically. Do not use legacy autopilot commands.

- Repo-mutating intent (feature, fix, refactor, behavior change) → Codex uses
  `$harness:run`; the workflow syncs a native Goal when present and otherwise
  opens or resumes a harness task directly.
- Broad goals grow child tasks as bugs, pages, domains, or follow-up gaps are
  discovered.
- Focused goals can remain a single child task.
- Bootstrap harness in a new project / repair existing → `Skill(harness:setup)`.
- Read-only question or explanation → answer directly, no harness task.

# Operating mode
- `doc/harness/manifest.yaml` is the initialization marker.
- Canonical loop for every repo-mutating child task: **plan when needed → develop → verify → close**. Smallest coherent diff per step. No verification skipped. See `plugin/CLAUDE.md` for the authoritative runtime rules.
- In this harness plugin source repo, a successful repo-mutating development task runs `python3 plugin/scripts/install_verified.py --task-dir doc/harness/tasks/<task_id>` automatically after current-run review+QA PASS and before `task_close`, unless the user explicitly opts out. The helper securely invokes `python3 install.py --force` once per receipt run and stable payload fingerprint. Post-close self-improvement may then commit the completed diff; it must not introduce a second install phase. Report the commit hash when applicable and the pre-close install result.
- The hard gate at task completion is receipt-backed `runtime_verdict: PASS` for the current task run. Source edits and scope drift after review/QA are developer-owned.
- Durable user requirements and reusable discoveries must be promoted to the
  right committed surface: REQ/GUIDE/ADR/POLICY, skill/pattern docs, or tests.
  Do not create narrative task artifacts for routine task
  evidence.
- Browser-first QA is default for web frontend projects when `browser_qa_supported: true` in manifest.
- Acceptance criteria live in `PLAN.md`; current-run review and QA authority
  comes only from lifecycle-owned `RECEIPTS.jsonl` entries. Receipt acquisition
  and storage/gate contracts are owned respectively by
  `doc/harness/patterns/ADR__single-direct-codex-receipt-protocol.md` and
  `doc/harness/patterns/ADR__consolidated-task-artifacts.md`.
- Notes under `doc/**/*.md` may carry `freshness: current|suspect|stale` + optional `invalidated_by_paths`. Run `plugin/scripts/note_freshness.py --paths ...` explicitly when maintaining them; SessionStart does not inspect Git.
- Protected artifacts (enforced by `plugin/scripts/prewrite_gate.py`):
  PLAN.md/PLAN.meta.json/AUDIT_TRAIL.md via `write_plan`, and RECEIPTS.jsonl via
  Codex/Claude lifecycle hooks. CONVERSATION.md is append-only runtime history
  owned by UserPromptSubmit/Subagent hooks.
- Pre-plan source writes are blocked until PLAN.md exists on the active task (plan-first rule).
- Only one repo-mutating task may hold write focus at a time. A second mutating request creates or resumes a separate task that stays queued until the user switches focus or the current task closes.
- Short approvals such as `ㅇㅇ ㄱ` approve only the last explicit transition the harness proposed; they never authorize skipping task creation, planning, or verify gates.
- When an answer-lane exchange turns into repo mutation, make the lane switch
  explicit and sync/open the native Goal child task or a direct harness task
  before implementation.

# Template sync rule (CRITICAL)
- This repo IS the harness plugin source. Runtime lives under `plugin/`. Every change to runtime behavior (paths, hook schemas, agent definitions, skill logic, script APIs) MUST stay internally consistent across `plugin/` — grep for the constant/path before landing the change.
- When a script API changes (e.g. `_lib.SCHEMA_FIELDS`), grep `plugin/skills/`
  for every SKILL.md that calls the script and update the example invocations.
- The setup skill lives at `plugin/skills/setup/SKILL.md`; its procedure text must stay consistent with actual generated output and with the current runtime loop described above.

## Memory

Project-applicable lessons live here as one-line bullets so contributors share the same context.

**Routing rule** — ask: "Does another contributor need this to avoid the same mistake?"
- Yes (project-applicable) → add a one-line bullet here.
- No (personal-only) → keep it in `~/.claude` auto-memory (private to user).

**Lazy-overflow rule** — when a topic accumulates 3+ related bullets, OR any single entry would exceed ~5 lines, split it out to `doc/memory/<topic>.md` and leave a one-line pointer here.

<!-- bullets accumulate below; section starts empty -->
