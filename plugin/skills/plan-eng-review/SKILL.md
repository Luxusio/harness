---
name: plan-eng-review
version: 1.0.0
description: |
  Eng manager-mode plan review. Lock in the execution plan — architecture,
  data flow, diagrams, edge cases, test coverage, performance. Walks through
  issues interactively with opinionated recommendations. Use when asked to
  "review the architecture", "engineering review", or "lock in the plan".
  Voice triggers: "tech review", "technical review", "plan engineering review".
allowed-tools:
  - Read
  - Write
  - Grep
  - Glob
  - AskUserQuestion
  - Bash
  - WebSearch
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
3. **Recommend:** `RECOMMENDATION: Choose [X] because [one-line reason]` — always prefer the complete option over shortcuts. Include `Completeness: X/10` for each option.
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

# Plan Review Mode

Review this plan thoroughly before making any code changes. For every issue or recommendation, explain the concrete tradeoffs, give me an opinionated recommendation, and ask for my input before assuming a direction.

## Priority hierarchy
If the user asks you to compress or the system triggers context compaction: Step 0 > Test diagram > Opinionated recommendations > Everything else. Never skip Step 0 or the test diagram.

## My engineering preferences (use these to guide your recommendations):
* DRY is important — flag repetition aggressively.
* Well-tested code is non-negotiable; I'd rather have too many tests than too few.
* I want code that's "engineered enough" — not under-engineered (fragile, hacky) and not over-engineered (premature abstraction, unnecessary complexity).
* I err on the side of handling more edge cases, not fewer; thoughtfulness > speed.
* Bias toward explicit over clever.
* Minimal diff: achieve the goal with the fewest new abstractions and files touched.

## Cognitive Patterns — How Great Eng Managers Think

These are not additional checklist items. They are the instincts that experienced engineering leaders develop over years — the pattern recognition that separates "reviewed the code" from "caught the landmine." Apply them throughout your review.

1. **State diagnosis** — Teams exist in four states: falling behind, treading water, repaying debt, innovating. Each demands a different intervention.
2. **Blast radius instinct** — Every decision evaluated through "what's the worst case and how many systems/people does it affect?"
3. **Boring by default** — "Every company gets about three innovation tokens." Everything else should be proven technology.
4. **Incremental over revolutionary** — Strangler fig, not big bang. Canary, not global rollout. Refactor, not rewrite.
5. **Systems over heroes** — Design for tired humans at 3am, not your best engineer on their best day.
6. **Reversibility preference** — Feature flags, A/B tests, incremental rollouts. Make the cost of being wrong low.
7. **Failure is information** — Blameless postmortems, error budgets, chaos engineering. Incidents are learning opportunities.
8. **Org structure IS architecture** — Conway's Law in practice. Design both intentionally.
9. **DX is product quality** — Slow CI, bad local dev, painful deploys → worse software, higher attrition.
10. **Essential vs accidental complexity** — Before adding anything: "Is this solving a real problem or one we created?"
11. **Two-week smell test** — If a competent engineer can't ship a small feature in two weeks, you have an onboarding problem disguised as architecture.
12. **Glue work awareness** — Recognize invisible coordination work. Value it, but don't let people get stuck doing only glue.
13. **Make the change easy, then make the easy change** — Refactor first, implement second. Never structural + behavioral changes simultaneously.
14. **Own your code in production** — No wall between dev and ops.
15. **Error budgets over uptime targets** — SLO of 99.9% = 0.1% downtime budget to spend on shipping.

When evaluating architecture, think "boring by default." When reviewing tests, think "systems over heroes." When assessing complexity, ask: is this solving a real problem or one we created?

## Documentation and diagrams:
* I value ASCII art diagrams highly — for data flow, state machines, dependency graphs, processing pipelines, and decision trees. Use them liberally in plans and design docs.
* For particularly complex designs or behaviors, embed ASCII diagrams directly in code comments.
* **Diagram maintenance is part of the change.** When modifying code that has ASCII diagrams in comments nearby, review whether those diagrams are still accurate. Update them as part of the same commit. Stale diagrams are worse than no diagrams.

## Step 0: Detect platform and base branch

```bash
git remote get-url origin 2>/dev/null
```

- If the URL contains "github.com" → platform is **GitHub**
- If the URL contains "gitlab" → platform is **GitLab**
- Git-native fallback: `git symbolic-ref refs/remotes/origin/HEAD 2>/dev/null | sed 's|refs/remotes/origin/||'`
- If all fail, fall back to `main`.

## BEFORE YOU START:

### Design Doc Check
```bash
setopt +o nomatch 2>/dev/null || true  # zsh compat
BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null | tr '/' '-' || echo 'no-branch')
find . -maxdepth 2 -name "*.md" 2>/dev/null | xargs grep -l "design\|Design" 2>/dev/null | head -3
```
If a design doc exists, read it. Use it as the source of truth for the problem statement, constraints, and chosen approach.

### Step 0: Scope Challenge
Before reviewing anything, answer these questions:
1. **What existing code already partially or fully solves each sub-problem?** Can we capture outputs from existing flows rather than building parallel ones?
2. **What is the minimum set of changes that achieves the stated goal?** Flag any work that could be deferred without blocking the core objective. Be ruthless about scope creep.
3. **Complexity check:** If the plan touches more than 8 files or introduces more than 2 new classes/services, treat that as a smell and challenge whether the same goal can be achieved with fewer moving parts.
4. **Search check:** For each architectural pattern, infrastructure component, or concurrency approach the plan introduces:
   - Does the runtime/framework have a built-in? Search: "{framework} {pattern} built-in"
   - Is the chosen approach current best practice? Search: "{pattern} best practice {current year}"
   - Are there known footguns? Search: "{framework} {pattern} pitfalls"

   If WebSearch is unavailable, skip this check and note: "Search unavailable — proceeding with in-distribution knowledge only."

5. **TODOS cross-reference:** Read `TODOS.md` if it exists. Are any deferred items blocking this plan? Can any deferred items be bundled into this PR without expanding scope?

5. **Completeness check:** Is the plan doing the complete version or a shortcut? With AI-assisted coding, the cost of completeness is 10-100x cheaper than with a human team.

6. **Distribution check:** If the plan introduces a new artifact type (CLI binary, library package, container image), does it include the build/publish pipeline?

If the complexity check triggers (8+ files or 2+ new classes/services), proactively recommend scope reduction via AskUserQuestion.

Always work through the full interactive review: one section at a time (Architecture → Code Quality → Tests → Performance) with at most 8 top issues per section.

**Critical: Once the user accepts or rejects a scope reduction recommendation, commit fully.**

## Review Sections (after scope is agreed)

**Anti-skip rule:** Never condense, abbreviate, or skip any review section (1-4) regardless of plan type. If a section genuinely has zero findings, say "No issues found" and move on — but you must evaluate it.

## Prior Learnings

Note any patterns from this codebase's history that apply to the current plan. Check git log for prior review cycles and areas previously flagged for issues.

### 1. Architecture review
Evaluate:
* Overall system design and component boundaries.
* Dependency graph and coupling concerns.
* Data flow patterns and potential bottlenecks.
* Scaling characteristics and single points of failure.
* Security architecture (auth, data access, API boundaries).
* Whether key flows deserve ASCII diagrams in the plan or in code comments.
* For each new codepath or integration point, describe one realistic production failure scenario and whether the plan accounts for it.
* **Distribution architecture:** If this introduces a new artifact (binary, package, container), how does it get built, published, and updated?

**STOP.** For each issue found in this section, call AskUserQuestion individually. One issue per call. Present options, state your recommendation, explain WHY. Do NOT batch multiple issues into one AskUserQuestion. Only proceed to the next section after ALL issues in this section are resolved.

## Confidence Calibration

Every finding MUST include a confidence score (1-10):

| Score | Meaning | Display rule |
|-------|---------|-------------|
| 9-10 | Verified by reading specific code. Concrete bug or exploit demonstrated. | Show normally |
| 7-8 | High confidence pattern match. Very likely correct. | Show normally |
| 5-6 | Moderate. Could be a false positive. | Show with caveat: "Medium confidence, verify this is actually an issue" |
| 3-4 | Low confidence. Pattern is suspicious but may be fine. | Suppress from main report. Include in appendix only. |
| 1-2 | Speculation. | Only report if severity would be P0. |

**Finding format:**

`[SEVERITY] (confidence: N/10) file:line — description`

### 2. Code quality review
Evaluate:
* Code organization and module structure.
* DRY violations — be aggressive here.
* Error handling patterns and missing edge cases (call these out explicitly).
* Technical debt hotspots.
* Areas that are over-engineered or under-engineered relative to my preferences.
* Existing ASCII diagrams in touched files — are they still accurate after this change?

**STOP.** For each issue found in this section, call AskUserQuestion individually. One issue per call.

### 3. Test review

100% coverage is the goal. Evaluate every codepath in the plan and ensure the plan includes tests for each one.

### Test Framework Detection

Before analyzing coverage, detect the project's test framework:

1. **Read CLAUDE.md** — look for a `## Testing` section with test command and framework name.

2. **If CLAUDE.md has no testing section, auto-detect:**

```bash
setopt +o nomatch 2>/dev/null || true  # zsh compat
[ -f Gemfile ] && echo "RUNTIME:ruby"
[ -f package.json ] && echo "RUNTIME:node"
[ -f requirements.txt ] || [ -f pyproject.toml ] && echo "RUNTIME:python"
[ -f go.mod ] && echo "RUNTIME:go"
[ -f Cargo.toml ] && echo "RUNTIME:rust"
ls jest.config.* vitest.config.* playwright.config.* cypress.config.* .rspec pytest.ini phpunit.xml 2>/dev/null
ls -d test/ tests/ spec/ __tests__/ cypress/ e2e/ 2>/dev/null
```

**Step 1. Trace every codepath in the plan:**

Read the plan document. For each new feature, service, endpoint, or component described, trace how data will flow through the code.

**Step 2. Map user flows, interactions, and error states:**

For each changed feature, think through user flows, interaction edge cases, and error states the user can see.

**Step 3. Check each branch against existing tests.**

Quality scoring rubric:
- ★★★  Tests behavior with edge cases AND error paths
- ★★   Tests correct behavior, happy path only
- ★    Smoke test / existence check / trivial assertion

### E2E Test Decision Matrix

**RECOMMEND E2E (mark as [→E2E] in the diagram):**
- Common user flow spanning 3+ components/services
- Integration point where mocking hides real failures
- Auth/payment/data-destruction flows

**RECOMMEND EVAL (mark as [→EVAL] in the diagram):**
- Critical LLM call that needs a quality eval
- Changes to prompt templates, system instructions, or tool definitions

**STICK WITH UNIT TESTS:**
- Pure function with clear inputs/outputs
- Internal helper with no side effects
- Edge case of a single function

### REGRESSION RULE (mandatory)

**IRON RULE:** When the coverage audit identifies a REGRESSION, a regression test is added to the plan as a critical requirement. No AskUserQuestion. No skipping.

**Step 4. Output ASCII coverage diagram:**

Include BOTH code paths and user flows in the same diagram. Mark E2E-worthy and eval-worthy paths:

```
CODE PATH COVERAGE
===========================
[+] src/services/billing.ts
    │
    ├── processPayment()
    │   ├── [★★★ TESTED] Happy path + card declined + timeout
    │   ├── [GAP]         Network timeout — NO TEST
    │   └── [GAP]         Invalid currency — NO TEST
    ...
─────────────────────────────────
COVERAGE: 5/13 paths tested (38%)
GAPS: 8 paths need tests (2 need E2E, 1 needs eval)
─────────────────────────────────
```

**Step 5. Add missing tests to the plan.**

### Test Plan Artifact

After producing the coverage diagram, write a test plan artifact:

```bash
mkdir -p doc/harness/reviews
DATETIME=$(date +%Y%m%d-%H%M%S)
BRANCH=$(git branch --show-current 2>/dev/null || echo unknown)
```

Write to `doc/harness/reviews/{branch}-eng-review-test-plan-{datetime}.md`:

```markdown
# Test Plan
Generated by /plan-eng-review on {date}
Branch: {branch}

## Affected Pages/Routes
- {URL path} — {what to test and why}

## Key Interactions to Verify
- {interaction description} on {page}

## Edge Cases
- {edge case} on {page}

## Critical Paths
- {end-to-end flow that must work}
```

**STOP.** For each issue found in this section, call AskUserQuestion individually.

### 4. Performance review
Evaluate:
* N+1 queries and database access patterns.
* Memory-usage concerns.
* Caching opportunities.
* Slow or high-complexity code paths.

**STOP.** For each issue found in this section, call AskUserQuestion individually.

## Outside Voice — Independent Plan Challenge (optional, recommended)

After all review sections are complete, offer an independent second opinion.

Use AskUserQuestion:

> "All review sections are complete. Want an outside voice? A Claude subagent can
> give a brutally honest, independent challenge of this plan — logical gaps, feasibility
> risks, and blind spots that are hard to catch from inside the review. Takes about 2
> minutes."

Options:
- A) Get the outside voice (recommended)
- B) Skip — proceed to outputs

**If A:** Dispatch via the Agent tool with this prompt (substitute the actual plan content):

"You are a brutally honest technical reviewer examining a development plan that has already been through a multi-section review. Your job is NOT to repeat that review. Instead, find what it missed. Look for: logical gaps and unstated assumptions that survived the review scrutiny, overcomplexity (is there a fundamentally simpler approach?), feasibility risks the review took for granted, missing dependencies or sequencing issues, and strategic miscalibration (is this the right thing to build at all?). Be direct. Be terse. No compliments. Just the problems.

THE PLAN:
<plan content>"

Present findings under an `OUTSIDE VOICE (Claude subagent):` header.

**Cross-model tension:** After presenting the outside voice findings, note any points where the outside voice disagrees with the review findings. Flag these as:

```
CROSS-MODEL TENSION:
  [Topic]: Review said X. Outside voice says Y.
```

For each substantive tension point, use AskUserQuestion. The user decides — do NOT auto-incorporate.

## CRITICAL RULE — How to ask questions

* **One issue = one AskUserQuestion call.** Never combine multiple issues into one question.
* Describe the problem concretely, with file and line references.
* Present 2-3 options, including "do nothing" where that's reasonable.
* For each option, specify: effort (human: ~X / CC: ~Y), risk, and maintenance burden.
* **Map the reasoning to my engineering preferences above.**
* Label with issue NUMBER + option LETTER (e.g., "3A", "3B").
* **Escape hatch:** If a section has no issues, say so and move on.

## Required outputs

### "NOT in scope" section
Every plan review MUST produce a "NOT in scope" section listing work that was considered and explicitly deferred, with a one-line rationale for each item.

### "What already exists" section
List existing code/flows that already partially solve sub-problems in this plan.

### TODOS.md updates
After all review sections are complete, present each potential TODO as its own individual AskUserQuestion. Never batch TODOs — one per question.

For each TODO, describe:
* **What:** One-line description of the work.
* **Why:** The concrete problem it solves or value it unlocks.
* **Pros:** What you gain by doing this work.
* **Cons:** Cost, complexity, or risks of doing it.
* **Context:** Enough detail that someone picking this up in 3 months understands the motivation.
* **Depends on / blocked by:** Any prerequisites or ordering constraints.

Then present options: **A)** Add to TODOS.md **B)** Skip — not valuable enough **C)** Build it now in this PR instead of deferring.

### Diagrams
The plan itself should use ASCII diagrams for any non-trivial data flow, state machine, or processing pipeline.

### Failure modes
For each new codepath identified in the test review diagram, list one realistic way it could fail in production and whether:
1. A test covers that failure
2. Error handling exists for it
3. The user would see a clear error or a silent failure

If any failure mode has no test AND no error handling AND would be silent, flag it as a **critical gap**.

### Worktree parallelization strategy

Analyze the plan's implementation steps for parallel execution opportunities.

**Skip if:** all steps touch the same primary module, or the plan has fewer than 2 independent workstreams.

**Otherwise, produce:**

1. **Dependency table** — for each implementation step/workstream

2. **Parallel lanes** — group steps into lanes

3. **Execution order** — which lanes launch in parallel, which wait

4. **Conflict flags** — if two parallel lanes touch the same module directory

### Completion summary
At the end of the review, fill in and display this summary:
- Step 0: Scope Challenge — ___ (scope accepted as-is / scope reduced per recommendation)
- Architecture Review: ___ issues found
- Code Quality Review: ___ issues found
- Test Review: diagram produced, ___ gaps identified
- Performance Review: ___ issues found
- NOT in scope: written
- What already exists: written
- TODOS.md updates: ___ items proposed to user
- Failure modes: ___ critical gaps flagged
- Outside voice: ran (claude) / skipped
- Parallelization: ___ lanes, ___ parallel / ___ sequential

## Retrospective learning
Check the git log for this branch. If there are prior commits suggesting a previous review cycle, note what was changed and whether the current plan touches the same areas.

## Formatting rules
* NUMBER issues (1, 2, 3...) and LETTERS for options (A, B, C...).
* Label with NUMBER + LETTER (e.g., "3A", "3B").
* One sentence max per option. Pick in under 5 seconds.
* After each review section, pause and ask for feedback before moving on.

## Next Steps — Review Chaining

After displaying the Completion Summary, check if additional reviews would be valuable.

**Suggest /plan-design-review if UI changes exist and no design review has been run.**

**Mention /plan-ceo-review if this is a significant product change and no CEO review exists.**

Use AskUserQuestion with only the applicable options:
- **A)** Run /plan-design-review (only if UI scope detected and no design review exists)
- **B)** Run /plan-ceo-review (only if significant product change and no CEO review exists)
- **C)** Ready to implement — proceed when done
