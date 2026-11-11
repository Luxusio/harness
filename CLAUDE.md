# CLAUDE.md
tags: [root, harness, bootstrap]
summary: 프로젝트 진입점. 운영 규칙과 doc registry 참조.
always_load: [doc/CLAUDE.md]
updated: 2026-04-15

@CONTRACTS.md
@doc/CLAUDE.md

## Harness routing
<!-- harness:routing-injected -->

On Codex, invoke `$harness:run` before repository mutation. One WBS uses the
effective document/planning/development policy; native `/goal` supplies the
objective and continuation. Documents/plans use relevant content checks and
configured document review. Development retains independent code/conditional
security review before applicable QA. Executable/unknown impact promotes the
same WBS/run before editing. A plan is not product completion.
Bootstrap/repair uses `$harness:setup`; read-only questions/discussion need no WBS.
Protected artifact writers, source ownership and current WBS checks still apply.

# Operating mode
- `doc/harness/manifest.yaml` is the initialization marker. Outside initialized
  projects, Harness hooks and normal lifecycle calls remain inactive.
- One WBS selects document, planning or development execution through the
  shared work policy. Document/planning deliverables use relevant content
  checks and configured document review; development retains independent code
  and conditional security review before applicable QA. Current checks must PASS before close. See
  `plugin/CLAUDE.md` for authoritative runtime rules.
- In this Harness source repository, run `python3 plugin/scripts/install_verified.py
  --if-changed --wbs-dir doc/harness/wbs/<wbs_id>` after applicable current
  review/QA PASS and before `wbs(action="close")` for development. Project `install: manual` skips
  automatic delivery without claiming deployment. The helper verifies
  the stable source snapshot. It invokes `python3 install.py`, whose default run skips
  synchronized runtimes and refreshes only stale runtime payloads.
  It creates no install receipt or deduplication state; retries recompute actual
  payload equality. Batch leads skip install; the coordinator installs once
  after integration. Post-close learning must not introduce a second install phase.
  Before the final response, commit the completed diff unless the user explicitly
  opts out; report the commit hash and pre-close conditional verified-delivery result.
  An already-current source commit need not be repeated after installation.
- PLAN.md owns acceptance intent. WBS.json owns outcome definitions and measured
  checks; WBS.control.json owns the execution run, required lenses and close state.
  Public replies expose `wbs_id`, `wbs_dir` and `context`. Normal execution does
  not read retired Goal/Task controls; only explicit migration imports them.
- Review and QA authority comes from actual independent final responses, never
  historical receipt files or coordinator-authored substitutes. A machine PASS
  cannot override current actual FAIL or BLOCKED_ENV. Source changes invalidate
  affected verification scope; retain applicable unaffected evidence.
- Promote durable user requirements and reusable discoveries to committed
  REQ/GUIDE/ADR/POLICY, skills or tests. Do not create duplicate narrative ledgers.
- Learning and self-improvement follow each WBS close. Shared runbook memory and
  validated learning-candidate reporting remain active; neither creates an
  unauthorized follow-up root or blocks completion for missing recording.
- Browser-first QA applies when the manifest and changed outcomes require it.
- Notes under `doc/**/*.md` may declare freshness and invalidated paths. Run
  `plugin/scripts/note_freshness.py --paths ...` explicitly when maintaining them;
  SessionStart does not inspect Git.
- PLAN.md, WBS.control.json and WBS.json belong to their public WBS owners.
  Historical receipt/review bytes and migration archives remain protected.
- Source writes require PLAN.md and the current exact session/run ownership;
  development/concurrent writes retain their execution assignment. A single
  narrative document writer needs no host dispatch or temporary worktree.
  One WBS holds this session's write focus per checkout; close or park it before
  opening another. Each registered worktree retains its own isolated assignment.
- Short approvals such as `ㅇㅇ ㄱ` authorize only the last explicit transition,
  retaining the stages and checks required by that transition's work policy.
- When an answer becomes repository mutation, open/resume the WBS before editing.

# Template sync rule (CRITICAL)
- This repo IS the harness plugin source. Runtime lives under `plugin/`. Every change to runtime behavior (paths, hook schemas, agent definitions, skill logic, script APIs) MUST stay internally consistent across `plugin/` — grep for the constant/path before landing the change.
- When a script API changes (e.g. `_lib.SCHEMA_FIELDS`), grep `plugin/skills/`
  for every SKILL.md that calls the script and update the example invocations.
- The setup skill lives at `plugin/skills/setup/SKILL.md`; its procedure text must stay consistent with actual generated output and with the current runtime loop described above.
- Changes to managed contracts, Harness skills, or `contract_lint.py` must keep the real-tree contract and skill-weight tests passing.

## Memory

Project-applicable lessons live here as one-line bullets so contributors share the same context.

**Routing rule** — ask: "Does another contributor need this to avoid the same mistake?"
- Yes (project-applicable) → add a one-line bullet here.
- No (personal-only) → keep it in `~/.claude` auto-memory (private to user).

**Lazy-overflow rule** — when a topic accumulates 3+ related bullets, OR any single entry would exceed ~5 lines, split it out to `doc/memory/<topic>.md` and leave a one-line pointer here.

<!-- bullets accumulate below; section starts empty -->
