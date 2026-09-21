# Provenance — what harness borrowed, and what it refused

tags: [harness, provenance, attribution]
status: current
updated: 2026-09-16

Harness is not an original idea end to end. Its loop, its reviewer roles, and
its planning voices were assembled from three prior agent-harness projects plus
a comparison of published AI code-review prompts. This document records, per
part of the harness flow, which upstream source it came from, what was taken,
what was stripped or refused, and the in-repo path that proves it.

**These are behavioral references, not vendored dependencies.** No upstream
code ships inside this repo. Harness owns the final prompts, routing, receipts,
and close semantics, and every import was rewritten against harness's own
contracts.

## Sources

| Source | Pinned revision | What it contributed |
|---|---|---|
| **gstack** | `a7593d70ef1b6500d1f6457c58cf7c9896cf6062` | Planning pipeline, review voices, adversarial review posture, debugging methodology, operational scripts |
| **oh-my-claudecode (OMC)** | `21a6e488ce12d79b9a22d37e1093ac8e79f21029` | Role boundaries between reviewer/security/QA, parallel agent fanout convention |
| **Ponytail** | `16f29800fd2681bdf24f3eb4ccffe38be3baec6b` | Minimum-sufficient implementation ladder, minimality review lens |
| Published AI code-review prompts (Codex, Claude Code material) | comparison study, 2026-09 | The non-attesting `defect-hunter` role |

Revisions are pinned in the frontmatter of
[`doc/designs/minimal-implementer-and-code-review-gate.md`](../designs/minimal-implementer-and-code-review-gate.md),
which is the authoritative record for review-pipeline provenance. This document
re-indexes that record by flow part and adds the planning, fanout, and script
lineage around it.

Runtime QA additionally depends on two external MCP servers — **chrome-devtools
MCP** (`plugin/agents/qa-browser.md`) and **x11-mcp**
(`plugin/agents/qa-desktop.md`) — which are integrations, not imports.

---

## setup

**Source:** gstack `office-hours`.

**Imported:** three features backported into the setup interview — Voice depth
(including the banned AI-vocabulary list at `plugin/skills/setup/SKILL.md:163`),
Context Recovery (`:173`), and Prior Learnings (`:186`).

**Refused:** the skill itself. `office-hours` is a SKIP in
[`IMPORT_LIST.md`](IMPORT_LIST.md) — its two modes are built around YC startup
methodology ("is this worth building", the YC plea, Boil the Lake framing),
which does not fit an engineering-workflow harness. Harness ships no
`office-hours` skill; setup's interactive intake fulfills the pre-planning
scope-sharpening role instead. The canonical mapping note lives in
`plugin/CLAUDE.md`.

**Evidence:** `plugin/skills/setup/SKILL.md`,
`doc/changes/2026-04-09-harness-setup-enrich.md`,
`doc/changes/2026-04-23-plan-skills-office-hours-and-outside-voice-polish.md`.

---

## plan

**Source:** gstack `autoplan`.

**Imported:** nine features brought to parity — Context Recovery briefing,
learnings load, end-of-session self-improvement append, completeness X/10
scoring in `AskUserQuestion`, the Completion Status Protocol
(`DONE` / `DONE_WITH_CONCERNS` / `BLOCKED` / `NEEDS_CONTEXT`), REPO_MODE
ownership policy, in-PLAN review report, the sequential-phase invariant, and
batched deferred-scope collection. A later orchestrator pass added the Voice
block, Confusion Protocol, Context Health, and the Anti-shortcut clause.

**Refused:** gstack's cross-model and Codex sections were excluded as not
applicable at import time. Harness later grew its own cross-model Voice B, which
routes through `omc ask codex|gemini` when available — an OMC integration, not a
gstack one (`plugin/skills/plan/intake.md`, `plugin/skills/plan/review-phases.md`).

**Evidence:** `plugin/skills/plan/SKILL.md`,
`doc/changes/2026-04-12-plan-skill-autoplan-parity-full.md`,
`doc/changes/2026-05-08-plan-orchestrator-gstack-alignment.md`.

---

## plan review sub-skills (ceo / design / eng / devex)

**Source:** gstack's same-named review skills.

**Imported:** a shared four-part voice-and-gating core across all four —
a Voice section with an explicit AI-vocabulary blocklist and a no-em-dashes
rule, the Confusion Protocol (STOP on high-stakes ambiguity in architecture,
data model, destructive scope, or missing context), the Anti-shortcut clause
that routes findings through `AskUserQuestion` instead of dumping them into
PLAN.md, and gstack's tighter escape-hatch wording
("a finding with an 'obvious fix' is still a finding").
`plan-devex-review` additionally adapts gstack's design-review reference
library into `dx-hall-of-fame.md`, keeping the industry examples unchanged and
rewriting the tooling-specific entries.

**Refused:** gstack's `D<N>/ELI10/✅❌/Net line` AskUserQuestion format, because
`plugin/skills/plan/decision-principles.md` owns a deliberately different shape
(`[Re-ground]/[Simplify]/[Recommend]/[Options]`). Also refused: the ~80-line
Writing Style and curated jargon glossary, which added weight without
proportionate value under the CONTRACTS C-13 budget.

**Evidence:** `plugin/skills/plan-ceo-review/SKILL.md`,
`plugin/skills/plan-devex-review/dx-hall-of-fame.md`,
`doc/changes/2026-05-07-plan-ceo-review-gstack-voice-alignment.md`,
`doc/changes/2026-05-08-plan-design-review-gstack-alignment.md`,
`doc/changes/2026-05-08-eng-and-devex-review-gstack-alignment.md`.

---

## develop — the implementer

**Source:** Ponytail `skills/ponytail/SKILL.md`.

**Imported:** the minimum-sufficient ladder now carried verbatim in role terms
by `plugin/agents/developer.md:34` — understand and trace the real flow first,
then stop at the first sufficient rung: current necessity, existing-code reuse,
standard library, native platform, already-installed dependency, smallest clear
local expression, minimum new code. Plus the operational rules: inspect direct
and sibling callers, fix a bug once at the shared root cause, prefer deletion
and boring existing primitives after comprehension, and leave one focused
runnable regression check.

**Refused:** raw line count as the objective. The harness standard is named
**minimum sufficient code, not minimum LOC**. Also refused: forced one-liners,
max-three-line handoffs, intensity/session modes, framework-free tests,
`ponytail:` source comments, Ponytail injection into QA or reviewers, and
shipping a reduced interpretation when PLAN or user intent already requires the
complete behavior. Trust-boundary validation, data-loss prevention, security,
accessibility, and requested behavior are explicitly not simplifiable — harness
expands that exception list to authorization, transaction, concurrency,
cleanup, and error-propagation invariants.

**Evidence:** `plugin/agents/developer.md`,
`doc/designs/minimal-implementer-and-code-review-gate.md`,
`doc/changes/2026-07-22-ponytail-developer-reviewer-prompt-parity.md`.

---

## develop — parallel fanout

**Source:** oh-my-claudecode `skills/team/SKILL.md`.

**Imported:** the spawn-all-in-one-message rule, cited inline at
`plugin/skills/develop/parallel-fanout.md:26` — parallel tool calls in one
assistant message run concurrently; sequential messages serialize the spawn.
Also imported: the model-tier discipline for delegated lanes, retained
AS-DECLARED rather than downgraded.

**Adapted:** the stage-to-agent mapping is harness's own, covering Phase 3 AC
fanout, Phase 4.5 audit inputs, Phase 6.6 review lenses, Phase 7 multi-lens QA,
and the Phase 7.7 dogfooder. Speedup comes from parallelism, not from cheaper
model tiers.

**Evidence:** `plugin/skills/develop/parallel-fanout.md`,
`doc/changes/2026-05-12-parallel-fanout-and-lens-aware-qa-merge.md`.

---

## develop — the debugging loop

**Source:** gstack `investigate`.

**Imported:** the four-phase methodology (investigate → analyze → hypothesize →
implement) and the Iron Law — no fixes without a root cause — rewritten as
`plugin/skills/develop/hypothesis-driven-debugging.md` and bound to the
verification-failure loop rather than offered as a standalone skill.

**Stripped:** the entire gstack preamble surface — `gstack-update-check`,
`gstack-config`, `gstack-slug`, `gstack-telemetry-log`, `gstack-timeline-log`,
`gstack-repo-mode`, `gstack-learnings-search`, `~/.gstack/` paths, and the
`PROACTIVE` / `SKILL_PREFIX` / `LAKE_INTRO` / `TEL_PROMPTED` /
`HAS_ROUTING` / `VENDORED_GSTACK` dialog flags. This stripping template applies
to every gstack import and is recorded in
[`IMPORT_LIST.md`](IMPORT_LIST.md) § Stripping Template.

**Evidence:** `plugin/skills/develop/hypothesis-driven-debugging.md:4`,
`doc/harness/IMPORT_LIST.md`.

---

## review pipeline

The most heavily synthesized part of the harness. Four roles, four different
lineages. Full role-by-role table in
[`doc/designs/minimal-implementer-and-code-review-gate.md`](../designs/minimal-implementer-and-code-review-gate.md)
§ Agent behavior provenance.

### `defect-hunter`

**Source:** no single upstream. Synthesized from a comparison of prominent AI
code-review prompts, including Codex and Claude Code material where obtainable,
after the observation that repeated harness review behaved like a yes-man and
emitted bare PASS verdicts without useful findings.

**Imported:** fresh read-only defect discovery with source anchors and evidence.

**Harness-specific:** the hunter is **non-attesting**. Its entire output is one
JSON array of at most 20 three-field objects (`anchor`, `issue`, `evidence`) —
no verdict, no severity, no confidence, no proposed fix, no lifecycle
authority. Its findings are untrusted leads handed to a separate formal
reviewer. Invocation count is deterministic: LIGHT runs zero hunters, STANDARD
one selected focus, DEEP both.

**Evidence:** `plugin/agents/defect-hunter.md`,
`doc/harness/tasks/TASK__independent-defect-discovery-review-evidence/PLAN.md`.

### `code-reviewer`

**Sources:** three, combined into one role.

- **OMC `agents/code-reviewer.md`** — the read-only independent verifier that
  never approves its own implementation context, and spec compliance before
  style. Harness adaptation: treats hunter output as untrusted leads,
  independently sweeps for misses, and mechanically derives one authoritative
  verdict.
- **Ponytail `skills/ponytail-review/SKILL.md`** — the minimality lens
  (deletion, reuse, stdlib/native replacement, speculative abstraction), now at
  `plugin/agents/code-reviewer.md:145`. Harness adaptation: made one paired lens
  inside a broader correctness/architecture review, never a standalone
  completion verdict.
- **gstack `ship/SKILL.md` and `review/checklist.md`** — the always-on
  adversarial lens (production failure, races, leaks, silent corruption,
  swallowed errors, trust-boundary violations), file-and-line evidence plus a
  concrete failure scenario, claim verification before findings, and confidence
  calibration that suppresses unsupported blockers. Harness adaptation: missing
  review **fails closed** rather than degrading to gstack's informational
  fallback, and evidence binds to the current task run and ordered lifecycle
  receipts rather than to Git state — harness commonly reviews uncommitted work.

**Refused:** generic SOLID enforcement, fixed function-length limits, and
mandatory cyclomatic thresholds, because those rules manufacture abstractions
that do not fit the project. Also refused: measuring quality by net line count,
and a mutating simplifier after implementation — minimality is a read-only
review lens, and fixes return to the original implementer.

### `security-reviewer`

**Sources:** OMC `agents/security-reviewer.md` for the separate read-only
OWASP/trust-boundary specialist prioritizing exploitability and blast radius;
gstack `ship/SKILL.md` for the insurance-control principle — security and
migration specialists must not be disabled by a history of finding nothing.

**Harness adaptation:** runs only when protected task/PLAN routing declares
`review-security`; baseline security stays in the always-on code reviewer.
Conditional on current scope, but never adaptive-hit-rate gated.

**Refused:** a full OWASP and dependency audit on every typo or docs-only diff.

### Review / QA separation

**Source:** OMC `skills/autopilot/SKILL.md` — architecture, security, quality
review, and runtime QA as independent responsibilities.

**Harness adaptation:** review is not another name for QA. Review judges whether
the solution is the right shape, safe, and proportionate; QA verifies that the
final solution actually works. After a source edit the developer starts a fresh
review cycle before QA, so runtime verification is not spent on a statically
rejected design.

**Refused:** many always-on agents whose findings overlap and create fix churn.

### Harness-native, no upstream

Review **depth selection** (LIGHT / STANDARD / DEEP, the two-cycle discovery
cap, and the rebase-LIGHT predicate set) has no recorded upstream source. Nor
does the receipt-backed close gate — ordered hook-owned reviewer and QA
completion receipts, `task_verify`, and the PASS-only `task_close`. Those are
harness's answer to the self-authored-verdict problem and were designed here.

**Evidence:** `doc/harness/tasks/TASK__risk-proportional-review-depth/PLAN.md`,
`doc/harness/patterns/ADR__consolidated-task-artifacts.md`.

---

## QA lenses

**Source:** integration, not import. `qa-browser` drives chrome-devtools MCP;
`qa-desktop` drives an x11-mcp server. `qa-api` and `qa-cli` use ordinary
tooling.

**Harness-specific:** QA agents never hold `Edit`/`Write` on source files, and
each lens returns findings in its final response while lifecycle hooks own the
receipt. Reviewer persona propagation follows the lesson recorded from Ponytail
`hooks/ponytail-subagent.js` — parent-session context does not reliably reach
subagents, so roles are explicit named files rather than inherited context.

**Evidence:** `plugin/agents/qa-browser.md`, `plugin/agents/qa-desktop.md`.

---

## Operational scripts

Four gstack skills were imported as **scripts rather than skills**, because
their value is a computation, not a conversation:

| gstack skill | Harness form | What it does |
|---|---|---|
| `health` | `plugin/scripts/health.py` | Composite 0-10 project health score from manifest-declared components |
| `retro` | `plugin/scripts/retro.py` | Weekly retrospective from git log, receipt-backed task closures, and learnings |
| `checkpoint` | `plugin/scripts/write_checkpoint.py` | Mid-task resume snapshot under `doc/harness/checkpoints/` |
| `learn` | `plugin/scripts/promote_learnings.py` | Aggregates `learnings.jsonl` and reports recurring keys as reviewed candidates |

All four dropped their gstack storage paths (`~/.gstack/projects/SLUG/...`) for
repo-local harness state, and `gstack-learnings-search` was replaced with direct
local file search.

**Note on [`IMPORT_LIST.md`](IMPORT_LIST.md):** that document is still marked
`status: draft` and dated 2026-04-09. It records these four as IMPORT without
specifying a shipped form, and lists a `review` skill import that instead became
the `code-reviewer` agent described above. Read it as the original decision
record; read this document for what actually shipped.

---

## What was refused, in one place

| Refused | Source | Why |
|---|---|---|
| `office-hours` skill | gstack | YC startup framing does not fit an engineering-workflow harness; setup's intake covers the role |
| `document-release` skill | gstack | Harness already updates durable docs and reviews them against the diff; this would be a redundant parallel path |
| `D<N>/ELI10/✅❌` question format | gstack | Conflicts with harness's own `decision-principles.md` shape |
| Writing Style + jargon glossary (~80 lines) | gstack | Weight without proportionate value under CONTRACTS C-13 |
| Raw LOC minimization, forced one-liners, `ponytail:` comments | Ponytail | Minimum *sufficient* code is the standard, not minimum lines |
| Ponytail injection into QA and reviewers | Ponytail | A simplification-only review is not a completion gate |
| Generic SOLID / function-length / cyclomatic thresholds | OMC | Manufactures abstractions that do not fit the project |
| Blanket OWASP + dependency audit on every diff | OMC | Disproportionate for typo and docs-only changes |
| Mutating post-implementation simplifier | OMC | Minimality belongs in read-only review; fixes return to the implementer |
| All gstack preamble infrastructure | gstack | Telemetry, config binaries, vendoring checks, and routing dialogs are host-specific and carry no portable judgment |
