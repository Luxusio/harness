---
name: autoplan
version: 1.0.0
description: |
  Auto-review pipeline — reads the full CEO, design, eng, and DX review skills from disk
  and runs them sequentially with auto-decisions using 6 decision principles. Surfaces
  taste decisions (close approaches, borderline scope, codex disagreements) at a final
  approval gate. One command, fully reviewed plan out.
  Use when asked to "auto review", "autoplan", "run all reviews", "review this plan
  automatically", or "make the decisions for me".
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - AskUserQuestion
---

## Preamble (run first)

```bash
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
echo "harness2 | PROJECT: $_PROJECT | BRANCH: $_BRANCH"
```

## AskUserQuestion Format

**ALWAYS follow this structure for every AskUserQuestion call:**
1. **Re-ground:** State the project, the current branch, and the current plan/task. (1-2 sentences)
2. **Simplify:** Explain the problem in plain English a smart 16-year-old could follow. No raw function names, no internal jargon, no implementation details. Use concrete examples and analogies. Say what it DOES, not what it's called.
3. **Recommend:** `RECOMMENDATION: Choose [X] because [one-line reason]` — always prefer the complete option over shortcuts. Include `Completeness: X/10` for each option. Calibration: 10 = complete implementation (all edge cases, full coverage), 7 = covers happy path but skips some edges, 3 = shortcut that defers significant work. If both options are 8+, pick the higher; if one is ≤5, flag it.
4. **Options:** Lettered options: `A) ... B) ... C) ...` — when an option involves effort, show both scales: `(human: ~X / CC: ~Y)`

Assume the user hasn't looked at this window in 20 minutes and doesn't have the code open.

## Completeness Principle — Boil the Lake

AI makes completeness near-free. Always recommend the complete option over shortcuts. A "lake" (100% coverage, all edge cases) is boilable; an "ocean" (full rewrite, multi-quarter migration) is not. Boil lakes, flag oceans.

**Effort reference** — always show both scales:

| Task type | Human team | CC | Compression |
|-----------|-----------|-----------|-------------|
| Boilerplate | 2 days | 15 min | ~100x |
| Tests | 1 day | 15 min | ~50x |
| Feature | 1 week | 30 min | ~30x |
| Bug fix | 4 hours | 15 min | ~20x |

Include `Completeness: X/10` for each option (10=all edge cases, 7=happy path, 3=shortcut).

## Repo Ownership — See Something, Say Something

Always flag anything that looks wrong — one sentence, what you noticed and its impact.

## Search Before Building

Before building anything unfamiliar, search first.
- **Layer 1** (tried and true) — don't reinvent. **Layer 2** (new and popular) — scrutinize. **Layer 3** (first principles) — prize above all.

## Completion Status Protocol

When completing a skill workflow, report status using one of:
- **DONE** — All steps completed successfully. Evidence provided for each claim.
- **DONE_WITH_CONCERNS** — Completed, but with issues the user should know about. List each concern.
- **BLOCKED** — Cannot proceed. State what is blocking and what was tried.
- **NEEDS_CONTEXT** — Missing information required to continue. State exactly what you need.

### Escalation

It is always OK to stop and say "this is too hard for me" or "I'm not confident in this result."

Bad work is worse than no work. You will not be penalized for escalating.
- If you have attempted a task 3 times without success, STOP and escalate.
- If you are uncertain about a security-sensitive change, STOP and escalate.
- If the scope of work exceeds what you can verify, STOP and escalate.

Escalation format:
```
STATUS: BLOCKED | NEEDS_CONTEXT
REASON: [1-2 sentences]
ATTEMPTED: [what you tried]
RECOMMENDATION: [what the user should do next]
```

## Plan Mode Safe Operations

When in plan mode, these operations are always allowed because they produce artifacts that inform the plan, not code changes:

- Writing to the plan file (already allowed by plan mode)
- Reading skill files from disk to follow review methodology

## Plan Status Footer

When about to wrap up, check if the plan file already has a `## REVIEW REPORT` section.
If it does NOT, write a placeholder table:

```markdown
## REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 0 | — | — |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | — | — |

**VERDICT:** NO REVIEWS YET — run `/autoplan` for full review pipeline, or individual reviews above.
```

## Step 0: Detect base branch

```bash
git remote get-url origin 2>/dev/null
```

- If the URL contains "github.com" → platform is **GitHub**
- Otherwise use git-native fallback

**If GitHub:**
1. `gh pr view --json baseRefName -q .baseRefName` — if succeeds, use it
2. `gh repo view --json defaultBranchRef -q .defaultBranchRef.name` — if succeeds, use it

**Git-native fallback:**
1. `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
2. If that fails: `git rev-parse --verify origin/main 2>/dev/null` → use `main`
3. If that fails: use `master`

---

# /autoplan — Auto-Review Pipeline

One command. Rough plan in, fully reviewed plan out.

/autoplan reads the full CEO, design, eng, and DX review skill files from disk and follows
them at full depth — same rigor, same sections, same methodology as running each skill
manually. The only difference: intermediate AskUserQuestion calls are auto-decided using
the 6 principles below. Taste decisions (where reasonable people could disagree) are
surfaced at a final approval gate.

---

## The 6 Decision Principles

These rules auto-answer every intermediate question:

1. **Choose completeness** — Ship the whole thing. Pick the approach that covers more edge cases.
2. **Boil lakes** — Fix everything in the blast radius (files modified by this plan + direct importers). Auto-approve expansions that are in blast radius AND < 1 day CC effort (< 5 files, no new infra).
3. **Pragmatic** — If two options fix the same thing, pick the cleaner one. 5 seconds choosing, not 5 minutes.
4. **DRY** — Duplicates existing functionality? Reject. Reuse what exists.
5. **Explicit over clever** — 10-line obvious fix > 200-line abstraction. Pick what a new contributor reads in 30 seconds.
6. **Bias toward action** — Merge > review cycles > stale deliberation. Flag concerns but don't block.

**Conflict resolution (context-dependent tiebreakers):**
- **CEO phase:** P1 (completeness) + P2 (boil lakes) dominate.
- **Eng phase:** P5 (explicit) + P3 (pragmatic) dominate.
- **Design phase:** P5 (explicit) + P1 (completeness) dominate.

---

## Decision Classification

Every auto-decision is classified:

**Mechanical** — one clearly right answer. Auto-decide silently.
Examples: run evals (always yes), reduce scope on a complete plan (always no).

**Taste** — reasonable people could disagree. Auto-decide with recommendation, but surface at the final gate. Three natural sources:
1. **Close approaches** — top two are both viable with different tradeoffs.
2. **Borderline scope** — in blast radius but 3-5 files, or ambiguous radius.
3. **Model disagreements** — outside voice recommends differently and has a valid point.

**User Challenge** — both models agree the user's stated direction should change.
This is qualitatively different from taste decisions. When Claude and an outside voice both
recommend merging, splitting, adding, or removing features/skills/workflows that
the user specified, this is a User Challenge. It is NEVER auto-decided.

User Challenges go to the final approval gate with richer context than taste
decisions:
- **What the user said:** (their original direction)
- **What both models recommend:** (the change)
- **Why:** (the models' reasoning)
- **What context we might be missing:** (explicit acknowledgment of blind spots)
- **If we're wrong, the cost is:** (what happens if the user's original direction was right and we changed it)

The user's original direction is the default. The models must make the case for
change, not the other way around.

**Exception:** If both models flag the change as a security vulnerability or
feasibility blocker (not a preference), the AskUserQuestion framing explicitly
warns: "Both models believe this is a security/feasibility risk, not just a
preference." The user still decides, but the framing is appropriately urgent.

---

## Sequential Execution — MANDATORY

Phases MUST execute in strict order: CEO → Design → Eng → DX.
Each phase MUST complete fully before the next begins.
NEVER run phases in parallel — each builds on the previous.

Between each phase, emit a phase-transition summary and verify that all required
outputs from the prior phase are written before starting the next.

---

## What "Auto-Decide" Means

Auto-decide replaces the USER'S judgment with the 6 principles. It does NOT replace
the ANALYSIS. Every section in the loaded skill files must still be executed at the
same depth as the interactive version. The only thing that changes is who answers the
AskUserQuestion: you do, using the 6 principles, instead of the user.

**Two exceptions — never auto-decided:**
1. Premises (Phase 1) — require human judgment about what problem to solve.
2. User Challenges — when both models agree the user's stated direction should change.

**You MUST still:**
- READ the actual code, diffs, and files each section references
- PRODUCE every output the section requires (diagrams, tables, registries, artifacts)
- IDENTIFY every issue the section is designed to catch
- DECIDE each issue using the 6 principles (instead of asking the user)
- LOG each decision in the audit trail
- WRITE all required artifacts to disk

**You MUST NOT:**
- Compress a review section into a one-liner table row
- Write "no issues found" without showing what you examined
- Skip a section because "it doesn't apply" without stating what you checked and why
- Produce a summary instead of the required output

"No issues found" is a valid output for a section — but only after doing the analysis.
State what you examined and why nothing was flagged (1-2 sentences minimum).
"Skipped" is never valid for a non-skip-listed section.

---

## Phase 0: Intake + Restore Point

### Step 1: Capture restore point

Before doing anything, save the plan file's current state. Record the path and timestamp.

Write the plan file's full contents to a restore file with this header:
```
# /autoplan Restore Point
Captured: [timestamp] | Branch: [branch] | Commit: [short hash]

## Re-run Instructions
1. Copy "Original Plan State" below back to your plan file
2. Invoke /autoplan

## Original Plan State
[verbatim plan file contents]
```

### Step 2: Read context

- Read CLAUDE.md, git log -30, git diff against the base branch --stat
- Detect UI scope: grep the plan for view/rendering terms (component, screen, form,
  button, modal, layout, dashboard, sidebar, nav, dialog). Require 2+ matches.
- Detect DX scope: grep the plan for developer-facing terms (API, endpoint, REST,
  GraphQL, gRPC, webhook, CLI, command, flag, argument, terminal, shell, SDK, library,
  package, npm, pip, import, require, SKILL.md, skill template, Claude Code, MCP, agent,
  action, developer docs, getting started, onboarding, integration, debug,
  implement, error message). Require 2+ matches.

### Step 3: Load skill files from disk

Read each file using the Read tool:
- `plugin2/skills/plan-ceo-review/SKILL.md`
- `plugin2/skills/plan-design-review/SKILL.md` (only if UI scope detected)
- `plugin2/skills/plan-eng-review/SKILL.md`
- `plugin2/skills/plan-devex-review/SKILL.md` (only if DX scope detected)

**Section skip list — when following a loaded skill file, SKIP these sections
(they are already handled by /autoplan):**
- Preamble (run first)
- AskUserQuestion Format
- Completeness Principle — Boil the Lake
- Search Before Building
- Completion Status Protocol
- Step 0: Detect base branch
- Plan Status Footer

Follow ONLY the review-specific methodology, sections, and required outputs.

Output: "Here's what I'm working with: [plan summary]. UI scope: [yes/no]. DX scope: [yes/no].
Loaded review skills from disk. Starting full review pipeline with auto-decisions."

---

## Phase 1: CEO Review (Strategy & Scope)

Follow plan-ceo-review/SKILL.md — all sections, full depth.
Override: every AskUserQuestion → auto-decide using the 6 principles.

**Override rules:**
- Mode selection: SELECTIVE EXPANSION
- Premises: accept reasonable ones (P6), challenge only clearly wrong ones
- **GATE: Present premises to user for confirmation** — this is the ONE AskUserQuestion
  that is NOT auto-decided. Premises require human judgment.
- Alternatives: pick highest completeness (P1). If tied, pick simplest (P5).
  If top 2 are close → mark TASTE DECISION.
- Scope expansion: in blast radius + <1d CC → approve (P2). Outside → defer (P3).
  Duplicates → reject (P4). Borderline (3-5 files) → mark TASTE DECISION.
- All review sections: run fully, auto-decide each issue, log every decision.

**Required execution checklist (CEO):**

Step 0 (0A-0F) — run each sub-step and produce:
- 0A: Premise challenge with specific premises named and evaluated
- 0B: Existing code leverage map (sub-problems → existing code)
- 0C: Dream state diagram (CURRENT → THIS PLAN → 12-MONTH IDEAL)
- 0C-bis: Implementation alternatives table (2-3 approaches with effort/risk/pros/cons)
- 0D: Mode-specific analysis with scope decisions logged
- 0E: Temporal interrogation (HOUR 1 → HOUR 6+)
- 0F: Mode selection confirmation

Sections 1-10 — for EACH section, run the evaluation criteria from the loaded skill file:
- Sections WITH findings: full analysis, auto-decide each issue, log to audit trail
- Sections with NO findings: 1-2 sentences stating what was examined and why nothing was flagged

**Mandatory outputs from Phase 1:**
- "NOT in scope" section with deferred items and rationale
- "What already exists" section mapping sub-problems to existing code
- Error & Rescue Registry table (from Section 2)
- Failure Modes Registry table (from review sections)
- Dream state delta (where this plan leaves us vs 12-month ideal)
- Completion Summary (the full summary table from the CEO skill)

**PHASE 1 COMPLETE.** Emit phase-transition summary:
> **Phase 1 complete.** [N concerns found]. Passing to Phase 2.

Do NOT begin Phase 2 until all Phase 1 outputs are written and the premise gate has been passed.

---

## Phase 2: Design Review (conditional — skip if no UI scope)

Follow plan-design-review/SKILL.md — all 7 dimensions, full depth.
Override: every AskUserQuestion → auto-decide using the 6 principles.

**Override rules:**
- Structural issues (missing states, broken hierarchy): auto-fix (P5)
- Aesthetic/taste issues: mark TASTE DECISION

**Required execution checklist (Design):**

1. Step 0 (Design Scope): Rate completeness 0-10. Map existing patterns.
2. Passes 1-7: Run each from loaded skill. Rate 0-10. Auto-decide each issue.

**PHASE 2 COMPLETE.** Emit phase-transition summary:
> **Phase 2 complete.** Design: [N concerns]. Passing to Phase 3.

Do NOT begin Phase 3 until all Phase 2 outputs (if run) are written.

---

## Phase 3: Eng Review + Dual Voices

Follow plan-eng-review/SKILL.md — all sections, full depth.
Override: every AskUserQuestion → auto-decide using the 6 principles.

**Override rules:**
- Scope challenge: never reduce (P2)
- Architecture choices: explicit over clever (P5). If outside voice disagrees with valid reason → TASTE DECISION.
- Evals: always include all relevant suites (P1)

**Required execution checklist (Eng):**

1. Step 0 (Scope Challenge): Read actual code referenced by the plan. Map each sub-problem to existing code.
2. Section 1 (Architecture): Produce ASCII dependency graph showing new components and their relationships to existing ones.
3. Section 2 (Code Quality): Identify DRY violations, naming issues, complexity. Reference specific files.
4. **Section 3 (Test Review) — NEVER SKIP OR COMPRESS.**
   - Read the diff or the plan's affected files
   - Build the test diagram: list every NEW UX flow, data flow, codepath, and branch
   - For each item: what type of test covers it? Does one exist? Gaps?
   - Write the test plan artifact to disk
5. Section 4 (Performance): Evaluate N+1 queries, memory, caching, slow paths.

**Mandatory outputs from Phase 3:**
- Architecture ASCII diagram (Section 1)
- Test diagram mapping codepaths to test coverage (Section 3)
- Test plan artifact written to disk (Section 3)
- Failure modes registry with critical gap flags
- Completion Summary

**PHASE 3 COMPLETE.** Emit phase-transition summary:
> **Phase 3 complete.** Eng: [N concerns]. Passing to Phase 3.5 (DX Review) or Phase 4 (Final Gate).

---

## Phase 3.5: DX Review (conditional — skip if no developer-facing scope)

Follow plan-devex-review/SKILL.md — all 8 DX dimensions, full depth.
Override: every AskUserQuestion → auto-decide using the 6 principles.

**Skip condition:** If DX scope was NOT detected in Phase 0, skip this phase entirely.
Log: "Phase 3.5 skipped — no developer-facing scope detected."

**Override rules:**
- Mode selection: DX POLISH
- Persona: infer from README/docs, pick the most common developer type (P6)
- Getting started friction: always optimize toward fewer steps (P5)
- Error message quality: always require problem + cause + fix (P1)
- API/CLI naming: consistency wins over cleverness (P5)
- DX taste decisions: mark TASTE DECISION

**Required execution checklist (DX):**

1. Step 0 (DX Scope Assessment): Auto-detect product type. Map the developer journey. Rate initial DX completeness 0-10.
2. Passes 1-8: Run each from loaded skill. Rate 0-10. Auto-decide each issue.
3. DX Scorecard: Produce the full scorecard with all 8 dimension scores.

**Mandatory outputs from Phase 3.5:**
- Developer journey map (9-stage table)
- Developer empathy narrative (first-person perspective)
- DX Scorecard with all 8 dimension scores
- DX Implementation Checklist
- TTHW assessment with target

**PHASE 3.5 COMPLETE.** Emit phase-transition summary:
> **Phase 3.5 complete.** DX overall: [N]/10. TTHW: [N] min → [target] min.

---

## Decision Audit Trail

After each auto-decision, append a row to the plan file using Edit:

```markdown
<!-- AUTONOMOUS DECISION LOG -->
## Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|-----------|-----------|----------|---------|
```

Write one row per decision incrementally (via Edit). This keeps the audit on disk,
not accumulated in conversation context.

---

## Pre-Gate Verification

Before presenting the Final Approval Gate, verify that required outputs were actually
produced. Check the plan file and conversation for each item.

**Phase 1 (CEO) outputs:**
- [ ] Premise challenge with specific premises named
- [ ] All applicable review sections have findings OR explicit "examined X, nothing flagged"
- [ ] Error & Rescue Registry table produced
- [ ] Failure Modes Registry table produced
- [ ] "NOT in scope" section written
- [ ] "What already exists" section written
- [ ] Dream state delta written
- [ ] Completion Summary produced

**Phase 2 (Design) outputs — only if UI scope detected:**
- [ ] All 7 dimensions evaluated with scores
- [ ] Issues identified and auto-decided

**Phase 3 (Eng) outputs:**
- [ ] Architecture ASCII diagram produced
- [ ] Test diagram mapping codepaths to test coverage
- [ ] Test plan artifact written to disk
- [ ] "NOT in scope" section written
- [ ] "What already exists" section written
- [ ] Failure modes registry with critical gap assessment
- [ ] Completion Summary produced

**Phase 3.5 (DX) outputs — only if DX scope detected:**
- [ ] All 8 DX dimensions evaluated with scores
- [ ] Developer journey map produced
- [ ] Developer empathy narrative written
- [ ] TTHW assessment with target
- [ ] DX Implementation Checklist produced

**Audit trail:**
- [ ] Decision Audit Trail has at least one row per auto-decision (not empty)

If ANY checkbox above is missing, go back and produce the missing output. Max 2
attempts — if still missing after retrying twice, proceed to the gate with a warning
noting which items are incomplete. Do not loop indefinitely.

---

## Phase 4: Final Approval Gate

**STOP here and present the final state to the user.**

Present as a message, then use AskUserQuestion:

```
## /autoplan Review Complete

### Plan Summary
[1-3 sentence summary]

### Decisions Made: [N] total ([M] auto-decided, [K] taste choices, [J] user challenges)

### User Challenges (both models disagree with your stated direction)
[For each user challenge:]
**Challenge [N]: [title]** (from [phase])
You said: [user's original direction]
Both models recommend: [the change]
Why: [reasoning]
What we might be missing: [blind spots]
If we're wrong, the cost is: [downside of changing]

Your call — your original direction stands unless you explicitly change it.

### Your Choices (taste decisions)
[For each taste decision:]
**Choice [N]: [title]** (from [phase])
I recommend [X] — [principle]. But [Y] is also viable:
  [1-sentence downstream impact if you pick Y]

### Auto-Decided: [M] decisions [see Decision Audit Trail in plan file]

### Review Scores
- CEO: [summary]
- Design: [summary or "skipped, no UI scope"]
- Eng: [summary]
- DX: [summary or "skipped, no developer-facing scope"]

### Cross-Phase Themes
[For any concern that appeared in 2+ phases independently:]
**Theme: [topic]** — flagged in [Phase 1, Phase 3]. High-confidence signal.
[If no themes span phases:] "No cross-phase themes — each phase's concerns were distinct."

### Deferred to TODOS
[Items auto-deferred with reasons]
```

**Cognitive load management:**
- 0 user challenges: skip "User Challenges" section
- 0 taste decisions: skip "Your Choices" section
- 1-7 taste decisions: flat list
- 8+: group by phase. Add warning: "This plan had unusually high ambiguity ([N] taste decisions). Review carefully."

AskUserQuestion options:
- A) Approve as-is (accept all recommendations)
- B) Approve with overrides (specify which taste decisions to change)
- B2) Approve with user challenge responses (accept or reject each challenge)
- C) Interrogate (ask about any specific decision)
- D) Revise (the plan itself needs changes)
- E) Reject (start over)

**Option handling:**
- A: mark APPROVED, suggest next implementation steps
- B: ask which overrides, apply, re-present gate
- C: answer freeform, re-present gate
- D: make changes, re-run affected phases. Max 3 cycles.
- E: start over

---

## Important Rules

- **Never abort.** The user chose /autoplan. Respect that choice. Surface all taste decisions, never redirect to interactive review.
- **Two gates.** The non-auto-decided AskUserQuestions are: (1) premise confirmation in Phase 1, and (2) User Challenges — when both models agree the user's stated direction should change. Everything else is auto-decided using the 6 principles.
- **Log every decision.** No silent auto-decisions. Every choice gets a row in the audit trail.
- **Full depth means full depth.** Do not compress or skip sections from the loaded skill files (except the skip list in Phase 0). If you catch yourself writing fewer than 3 sentences for any review section, you are likely compressing.
- **Artifacts are deliverables.** Test plan artifact, failure modes registry, error/rescue table, ASCII diagrams — these must exist on disk or in the plan file when the review completes.
