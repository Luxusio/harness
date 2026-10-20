# Phase 6: Write PLAN artefacts

Sub-file for plan/SKILL.md Phase 6. Always runs.

---

## 6.1 Artifact writer

PLAN.md is written through `wbs(action="plan", wbs_id=...)`; the private `write_plan` handler is its internal owner. The same call
stores the canonical required lens set in `TASK.json`.

### Measured WBS

Use the acceptance outcomes as the WBS; do not write a second task breakdown.
Include one `wbs` JSON fence in PLAN, with a small number of measurable leaves:

```wbs
[{"id":"AC-001","title":"Invalid input is rejected with an actionable error","weight":1}]
```

Every leaf needs an observable outcome and positive finite weight. Add an
assertion argv as `check` when ready to verify; do not freeze test commands or
implementation layout at intake. WBS has no edited-file lists. Optional
`parent` and `depends_on` name node IDs. A group has no `check`; its children's weights sum
to its weight (for example, split weight 1 into 0.5 and 0.5). Choose coarse, understandable weights, not invented effort
precision. One leaf suffices for small work. `wbs(action="plan")` atomically publishes
the baseline and runtime WBS evidence without changing TASK's schema.
Use `wbs(action="check", node_id=...)` only when an outcome is ready to
check. Context/open/next and plan updates do not hash source: current progress
is unmeasured and historical credit is labelled separately. Never report that
history as current completion. Explicit check and final verify/close measure
current inputs. Never author a percentage or count task
additions as accomplishment. Passing commands still need review for adequacy.
To refine, keep the pending leaf's ID and weight as a group, remove its check
and add concrete children conserving weight. Adapt pending outcomes, checks
and dependencies as discovery requires; changed definitions lose old credit.
Start with large results and elaborate only work about to be done. Splitting
earns nothing and never restarts the progress clock. File ownership belongs
only to actual concurrent execution, separately from progress measurement.
When verification inputs become known, optionally publish one `wbs-inputs`
JSON fence with literal `source` roots, `artifacts` files, `exclude` and `deny`
paths. This is an adaptable verification policy, never an initial edit plan.
Include actual common/build/lockfile/resource inputs; independent review owns
adequacy. Denied required inputs stay unavailable; do not omit them to obtain
PASS. Policy changes preserve history without promoting old proof. See the
REQ for exact bounds and no-read semantics. Park an external blocker with
`wbs(action="block", node_id=..., blocked_reason=..., unblock_condition=...)`
so independent work remains ready; `open` with that node_id clears the local
block without restoring old credit. Omit node_id for root lifecycle parking.
See `doc/harness/REQ__measurable-wbs-progress.md` for the observable contract.

## 6.2 Assemble PLAN.md content

Materialise plan content from in-memory review state into `/tmp/plan_content.md`.

**Restore point comment** — if Phase 0.5 captured one, prepend as the very first line:
```
<!-- plan restore point: restore-points/pre-plan-<timestamp>.md -->
```
Omit if no restore point.

**Required sections:** objective, `Original Request / Intent Summary`, scope in, scope out, `NOT in scope`, `What already exists`, target files/surfaces, acceptance criteria (stable IDs AC-001+), verification contract, `Durable Docs Decision`, `Error & Rescue Registry`, `Failure Modes Registry`, `Dream state delta`, `Cross-phase themes`, doc-sync expectation, risk/rollback (if `risk_level: high`), next implementation step.

### Original Request / Intent Summary

Every PLAN.md must preserve the user's request or intent in a committed-process
artifact, because task-local `REQUEST.md` may be absent, thin, or gitignored. If
`REQUEST.md` exists, summarize it and cite it. If it is absent, gitignored, or
under 15 non-empty lines, write a concise intent summary from the current user
prompt and explicitly label the source as `conversation summary`. Do not rely on
`REQUEST.md` alone for future intent reconstruction.

When an explicit clarification, approval, or consolidated decision answer
authorizes a material premise or scope, summarize that authority here and keep
the corresponding rationale in the existing Decision Audit Trail. Do not add
an approval field, ledger, or sidecar.

### Durable Docs Decision

For each new or changed normative claim, preserve provenance in the same REQ:
user_explicit/user_confirmed with identifiable source and relevant exact quote;
derived with its existing requirement/constraint and reasoning; or non-binding
assumption/proposal/unattributed. A source label alone does not prove the claim
matches the user's intent. Compare each added detail with the actual source;
do not turn a plausible interpretation into a user request. Confirmed claims
name the particular approved proposal; “continue” never approves unrelated
assumptions. Keep implementation choices in PLAN/ADR. Do not use candidate
claims as mandatory WBS outcomes, acceptance or close criteria. Ask only for
material unresolved intent/scope, not routine implementation choices.
req_scaffold defaults candidate; accepted generation requires per-item evidence
through --requirements-file. Direct edits follow the same provenance rule.
Only user_explicit/scoped user_confirmed rows may be accepted user requirements;
an accepted document does not accept its derived or candidate rows. Separate
source locators and exact excerpts from authored claims and confirmation scope.
Derived source/basis links the existing requirement or standing constraint and
explains necessary work. Supported implementation work may enter WBS and be
tested to satisfy that accepted outcome autonomously; it cannot introduce a
new product outcome, service, cost or user-facing constraint by being labelled
derived. Missing grounding stays unverified/non-binding. Apply this distinction
when user feedback or development discoveries revise WBS, not only at intake.
See doc/harness/REQ__requirement-provenance.md; no extra approval ledger.

Every PLAN.md must classify documentation impact before develop starts. This is
a judgment step, not a blanket REQ requirement. Use one of these outcomes in the
reason: `REQ needed`, `Pattern/skill doc enough`, or `No durable doc needed`.
Then list which durable docs should be created or updated:

```md
## Durable Docs Decision
REQ: doc/<area>/REQ__<name>.md | n/a
GUIDE: doc/<area>/GUIDE__<name>.md | n/a
ADR: doc/<area>/ADR__<name>.md | n/a
POLICY: doc/<area>/POLICY__<name>.md | n/a
Reason: <one sentence>
```

Use `REQ` when the task changes behavior or constraints that implementation
and QA must satisfy: existing screen state, filters, search, sorting, loading,
empty/error states, localization/copy/visibility, click/input interactions,
API request or response shape, status codes, auth/session behavior, validation,
compatibility, externally consumed side effects, or observable bugfixes.
New pages, admin/backoffice screens, routes, controllers, and endpoints are REQ-required even when additive. PLAN.md acceptance criteria are task-local artifacts and never substitute for a durable `REQ`. Do not write `REQ: n/a` when the plan creates or changes observable UI/API behavior; if uncertain, choose the REQ and let develop refine the exact path. A concrete REQ path is required before develop starts for observable UI/API/backoffice work. If target files or surfaces include observable UI, API, backoffice/admin screens, routes, controllers, or endpoints and the decision says `REQ: n/a`, treat that as a blocking plan defect: revise the PLAN before Phase 6 and do not defer this to close.

Happy path: when request text, user feedback, target surfaces, or known file
paths imply observable UI/API/mobile/native/desktop behavior, pick the REQ path
proactively. If no suitable REQ exists, select a new path such as
`doc/ui/REQ__mobile-reader-navigation.md`; develop will create/update it with
a direct `doc/<area>/REQ__*.md` update or `req_scaffold.py` before source edits. Safety gates exist
only to catch misses, not as the normal discovery mechanism.

When the change affects harness process, agent instructions, coding patterns, or
verification practice but not a product/runtime contract, prefer `GUIDE` or
skill/pattern docs and write `REQ: n/a` with `Pattern/skill doc enough` in the
reason. For purely mechanical, test-only, or internal refactors, write
`No durable doc needed` and name the unchanged durable knowledge surface.

Use `GUIDE` when the task establishes reusable coding, design, testing, or
implementation guidance. Use `ADR` when it makes a significant technical
choice with alternatives or tradeoffs. Use `POLICY` only for external
security, legal, data-handling, approval, licensing, or organizational
constraints that harness cannot fully enforce by itself.

Write docs under the DDD-style area or bounded-context folder, for example
`doc/ui/REQ__filter-bar.md`, `doc/api/REQ__oauth-login.md`,
`doc/auth/ADR__token-storage.md`, or `doc/common/GUIDE__coding-style.md`.
Use `n/a` when a type is not needed; the reason must say which durable
knowledge surfaces are unchanged. Reasons like "additive change" or "covered
by PLAN.md" are invalid because additive observable behavior still needs a
durable contract.

### Review Status table (end of PLAN.md)

Assemble from the selected procedure's truthful assessment state and decisions
kept in working context. Do not create another chronological file.

Full procedure:

```markdown
## Review Status

| Phase | Ran | Reviewer | Findings | User Challenges |
|-------|-----|----------|----------|-----------------|
| 1 Plan Review | yes | <subagent/coordinator-only> | <N> | <N> |

**Auto-decided:** <N> | **Taste recorded:** <N> | **User Challenges:** <N>
**Planning procedure:** <compact/full>
**Execution mode:** <standard/micro>
```

Compact procedure:

```markdown
## Review Status

| Assessment | Ran | Evidence | Escalations after inspection | User Challenges |
|------------|-----|----------|------------------------------|-----------------|
| Compact code/context assessment | yes | <named files/flows> | none | <N> |

**Planning procedure:** compact
**Execution mode:** standard
```

### Plan Review Report

Full procedure:

```markdown
## Plan Review Report

| Phase | Ran | Status | Findings |
|-------|-----|--------|----------|
| 1 Plan Review | yes | complete | <N> confirmed |

**VERDICT:** REVIEWED — plan has passed the full review pipeline. If Phase 1
ran coordinator-only, use `REVIEWED_DEGRADED — Phase 1 ran coordinator-only`
and set completion status DONE_WITH_CONCERNS instead.
```

Compact procedure:

```markdown
## Plan Review Report

| Assessment | Status | Findings |
|------------|--------|----------|
| Compact code/context assessment | complete | <N> confirmed; post-inspection escalation recheck clear |

**VERDICT:** ASSESSED_COMPACT — low-risk plan passed the bounded code/context assessment; no full review is claimed.
```

If review state is unavailable, use a placeholder table with all "—" and a
verdict directing the operator to rerun planning.

No harness policy boilerplate. Keep concise and executable.

## 6.3 Write PLAN.md via MCP

```text
wbs { action: "plan",
  wbs_id: "WBS__<id>",
  plan: "<PLAN.md content>",
  required_lenses: ["review-code", "review-security", "qa-cli"]
}
```

Choose only the applicable supported lenses. `review-code` is always required;
add `review-security` when the task has a security boundary. Select QA lenses
from `qa-cli`, `qa-api`, `qa-browser`, and `qa-desktop`.

## 6.6 Acceptance criteria

Keep the stable acceptance criteria directly in PLAN.md. Do not assemble or
write a second status ledger; review and QA completion is recorded in the
unified `RECEIPTS.jsonl` lifecycle stream.

Planning decisions from earlier phases are included directly in PLAN.md's
Decision Audit Trail before the single MCP publication.

### Per-AC shape

For rolling outcome-based WBS, leave implementation file/test annotations
out until current code and actual execution ownership are known. Do not invent
file lists, helper structure or test commands merely to calculate progress.
The shape below is for execution-ready ACs; WBS outcomes may stay coarse and
adapt throughout implementation. Add ownership only when assigning writers.

Write each acceptance criterion in this shape:

```text
**AC-00N: <outcome>.**
**Files:** `<source path>`, ... | none
**Tests:** `<test path>`, ... | none
**Depends on:** none | AC-00M, ...
- <criteria>
- Verify: `<command>`
```

`**Files:**` lists the source paths the AC's implementer writes. `**Tests:**` holds test paths only; develop may give them to a separate test author working beside the implementer. No path appears in both `**Files:**` and `**Tests:**`.

Cut ACs along disjoint file seams: when two ACs' `**Files:**` and `**Tests:**` sets share no path, develop runs them as parallel lanes; one AC that bundles independent seams runs as a single lane, so split it into one AC per seam instead.

A change that several ACs need is its own AC rather than living inside any one consumer: it is a helper-extract AC that each consumer lists in `**Depends on:**`, and develop runs it before those consumers.

Tests that pin text or output the implementation itself produces belong in a test-only AC (`**Files:** none`) that depends on the implementing AC, rather than living inside the implementing AC.

## 6.8 Learnings write-back (capture-when-fresh, non-blocking)

When you discover something genuinely useful during the task — a real bug, a workaround that saved time, a pattern that surprised you, a tooling gotcha — log it the moment you find it, **while it's fresh**. Log only concrete, reusable facts at discovery time; leave the log untouched when there is no durable learning.

A good entry names a concrete fact + a concrete fix, both groundable in files / commands / outputs. Examples of what passes the bar:

- `/plugin marketplace add` does NOT expand bash subshell `$(pwd)` — `/plugin` is a Claude Code slash command, not a shell command. Use `./` or a literal path.
- A duplicated task ledger can drift from PLAN.md; keep acceptance intent in one document.
- Plan artifacts are MCP-owned; direct Write/Edit and legacy CLI handshakes are not the canonical path.

Examples that do NOT pass the bar (do not log these):
- "I completed Phase 3" — narration, not learning.
- "The plan was clear" — vague reflection, not a durable fact.
- "I used the Edit tool" — tool usage is not insight.

Schema (one JSON line per entry, append to `doc/harness/learnings.jsonl`):

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
# echo '{"ts":"'"$_TS"'","type":"operational|pitfall|eureka|feedback","skill":"plan","branch":"'"$_BRANCH"'","key":"SHORT_KEY","insight":"FACT + FIX","source":"observed","task":"TASK__<id>","task_run_id":"<TASK.json run_id>"}' >> doc/harness/learnings.jsonl
```

`type=operational` for tooling/syntax/path facts. `type=pitfall` for traps to avoid. `type=eureka` for first-principles discoveries that contradict conventional wisdom. `type=feedback` for user-stated preferences that should shape future behavior. Creates file if absent. Silent-fail on write error. Never blocks task close.

## 6.9 Remove optional planning scratch

After every successful `wbs(action="plan")`, remove task-local PLAN_SESSION.json when it
exists. PLAN.md and TASK.json are sufficient for resume. Leave scratch in place
only when publication failed and it carries recovery value.

## 6.10 Completion report

```
STATUS: <DONE | DONE_WITH_CONCERNS | BLOCKED>

Task:    TASK__<id>
Plan:    doc/harness/tasks/TASK__<id>/PLAN.md

Phases run:        <list, e.g. 0, 1, 5, 6>
Planning procedure:<compact/full>
Execution mode:    <standard/micro>
Auto-decided:      <N> decisions
Taste recorded:    <N> items
User Challenges:   <N> items
Deferred scope:    <N> items (see deferred-scope.md)
Review summary:    <N> phase-summary rows
```

- **DONE_WITH_CONCERNS** — any of: phase ran coordinator-only; User Challenge unresolved; convergence guard issues.
- **BLOCKED** — Phase 6 MCP artifact write failed. (Review findings alone are never BLOCKED — use DONE_WITH_CONCERNS.)
