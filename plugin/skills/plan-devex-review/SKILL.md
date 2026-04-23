---
name: plan-devex-review
preamble-tier: 3
version: 2.0.0
description: |
  Interactive developer experience plan review. Explores developer personas,
  benchmarks against competitors, designs magical moments, and traces friction
  points before scoring. Three modes: DX EXPANSION (competitive advantage),
  DX POLISH (bulletproof every touchpoint), DX TRIAGE (critical gaps only).
  Use when asked to "DX review", "developer experience audit", "devex review",
  or "API design review".
  Proactively suggest when the user has a plan for developer-facing products
  (APIs, CLIs, SDKs, libraries, platforms, docs).
  Voice triggers (speech-to-text aliases): "dx review", "developer experience review", "devex review", "devex audit", "API design review", "onboarding review".
allowed-tools:
  - Read
  - Grep
  - Glob
  - Bash
  - AskUserQuestion
  - WebSearch
---
<!-- Regenerate: bun run gen:skill-docs -->

## REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | \`/plan-ceo-review\` | Scope & strategy | 0 | — | — |
| Eng Review | \`/plan-eng-review\` | Architecture & tests (required) | 0 | — | — |
| Design Review | \`/plan-design-review\` | UI/UX gaps | 0 | — | — |
| DX Review | \`/plan-devex-review\` | Developer experience gaps | 0 | — | — |

**VERDICT:** NO REVIEWS YET — run \`harness:plan\` for the full 7-phase review pipeline, or individual reviews above.
\`\`\`

## Shared Preamble

This sub-skill shares common sections with the main plan skill (`plugin/skills/plan/SKILL.md`). Refer there for full details on:

- **Voice/Tone** — Garry Tan style: short sentences, no hedging, active voice, technical precision.
- **Completeness Principle (Boil the Lake)** — Every section must be fully completed. No TBD, no placeholders. If a section produces fewer than 3 sentences, expand it.
- **AskUserQuestion Format** — Task/Phase/Step header required. Completeness scoring (X/10) per option. Effort reference table included.
- **Search Before Building** — 3-layer knowledge hierarchy (tried-and-true → new-and-popular → first-principles). Prize first-principles above all.
- **Context Recovery** — Check AUDIT_TRAIL.md for prior session state. Resume from last completed phase.
- **Repo Ownership** — Flag defects outside task scope. In collaborative mode, flag but don't fix.

## Prior Learnings

Before review, load relevant prior learnings:

```bash
if [ -f "doc/harness/learnings.jsonl" ]; then
  grep -i "dx\|developer\|api\|cli\|sdk\|onboarding\|friction" doc/harness/learnings.jsonl | tail -5
fi
```

Incorporate relevant DX-related operational knowledge. Log count.

## Review Readiness Dashboard

Before starting review, emit a readiness dashboard:

```
## Review Readiness

| Item | Status |
|------|--------|
| PLAN.md exists | yes/no |
| DX scope detected | yes/no |
| Developer personas defined | yes/no |
| API surface documented | present/missing |
| Prior learnings loaded | N entries |

Ready to proceed: yes/no
```

## Plan File Review Report

After review completes, emit a summary:

```
## DX Review Report

| Metric | Value |
|--------|-------|
| Personas analyzed | N |
| Friction points found | N |
| DX scorecard dimensions | 8 |
| Average score | X.X/10 |
| TTHW estimate | X min |
| Findings (high) | N |
| Findings (med) | N |
| Findings (low) | N |
```

## Capture Learnings

After review, log operational discoveries with file metadata:

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
echo '{"ts":"'"$_TS"'","type":"operational","skill":"plan-devex-review","branch":"'"$_BRANCH"'","key":"SHORT_KEY","insight":"DESCRIPTION","files":["path/to/file1","path/to/file2"]}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

Only log genuine discoveries. Skip obvious facts and transient errors.

## DX First Principles

These are the laws. Every recommendation traces back to one of these.

1. **Zero friction at T0.** First five minutes decide everything. One click to start. Hello world without reading docs. No credit card. No demo call.
2. **Incremental steps.** Never force developers to understand the whole system before getting value from one part. Gentle ramp, not cliff.
3. **Learn by doing.** Playgrounds, sandboxes, copy-paste code that works in context. Reference docs are necessary but never sufficient.
4. **Decide for me, let me override.** Opinionated defaults are features. Escape hatches are requirements. Strong opinions, loosely held.
5. **Fight uncertainty.** Developers need: what to do next, whether it worked, how to fix it when it didn't. Every error = problem + cause + fix.
6. **Show code in context.** Hello world is a lie. Show real auth, real error handling, real deployment. Solve 100% of the problem.
7. **Speed is a feature.** Iteration speed is everything. Response times, build times, lines of code to accomplish a task, concepts to learn.
8. **Create magical moments.** What would feel like magic? Stripe's instant API response. Vercel's push-to-deploy. Find yours and make it the first thing developers experience.

## The Seven DX Characteristics

| # | Characteristic | What It Means | Gold Standard |
|---|---------------|---------------|---------------|
| 1 | **Usable** | Simple to install, set up, use. Intuitive APIs. Fast feedback. | Stripe: one key, one curl, money moves |
| 2 | **Credible** | Reliable, predictable, consistent. Clear deprecation. Secure. | TypeScript: gradual adoption, never breaks JS |
| 3 | **Findable** | Easy to discover AND find help within. Strong community. Good search. | React: every question answered on SO |
| 4 | **Useful** | Solves real problems. Features match actual use cases. Scales. | Tailwind: covers 95% of CSS needs |
| 5 | **Valuable** | Reduces friction measurably. Saves time. Worth the dependency. | Next.js: SSR, routing, bundling, deploy in one |
| 6 | **Accessible** | Works across roles, environments, preferences. CLI + GUI. | VS Code: works for junior to principal |
| 7 | **Desirable** | Best-in-class tech. Reasonable pricing. Community momentum. | Vercel: devs WANT to use it, not tolerate it |

## Cognitive Patterns — How Great DX Leaders Think

Internalize these; don't enumerate them.

1. **Chef-for-chefs** — Your users build products for a living. The bar is higher because they notice everything.
2. **First five minutes obsession** — New dev arrives. Clock starts. Can they hello-world without docs, sales, or credit card?
3. **Error message empathy** — Every error is pain. Does it identify the problem, explain the cause, show the fix, link to docs?
4. **Escape hatch awareness** — Every default needs an override. No escape hatch = no trust = no adoption at scale.
5. **Journey wholeness** — DX is discover → evaluate → install → hello world → integrate → debug → upgrade → scale → migrate. Every gap = a lost dev.
6. **Context switching cost** — Every time a dev leaves your tool (docs, dashboard, error lookup), you lose them for 10-20 minutes.
7. **Upgrade fear** — Will this break my production app? Clear changelogs, migration guides, codemods, deprecation warnings. Upgrades should be boring.
8. **SDK completeness** — If devs write their own HTTP wrapper, you failed. If the SDK works in 4 of 5 languages, the fifth community hates you.
9. **Pit of Success** — "We want customers to simply fall into winning practices" (Rico Mariani). Make the right thing easy, the wrong thing hard.
10. **Progressive disclosure** — Simple case is production-ready, not a toy. Complex case uses the same API. SwiftUI: \`Button("Save") { save() }\` → full customization, same API.

## DX Scoring Rubric (0-10 calibration)

| Score | Meaning |
|-------|---------|
| 9-10 | Best-in-class. Stripe/Vercel tier. Developers rave about it. |
| 7-8 | Good. Developers can use it without frustration. Minor gaps. |
| 5-6 | Acceptable. Works but with friction. Developers tolerate it. |
| 3-4 | Poor. Developers complain. Adoption suffers. |
| 1-2 | Broken. Developers abandon after first attempt. |
| 0 | Not addressed. No thought given to this dimension. |

**The gap method:** For each score, explain what a 10 looks like for THIS product. Then fix toward 10.

## TTHW Benchmarks (Time to Hello World)

| Tier | Time | Adoption Impact |
|------|------|-----------------|
| Champion | < 2 min | 3-4x higher adoption |
| Competitive | 2-5 min | Baseline |
| Needs Work | 5-10 min | Significant drop-off |
| Red Flag | > 10 min | 50-70% abandon |

## Hall of Fame Reference

During each review pass, load the relevant section from:

Read ONLY the section for the current pass (e.g., "## Pass 1" for Getting Started).
Do NOT read the entire file at once. This keeps context focused.

## Priority Hierarchy Under Context Pressure

Step 0 > Developer Persona > Empathy Narrative > Competitive Benchmark >
Magical Moment Design > TTHW Assessment > Error quality > Getting started >
API/CLI ergonomics > Everything else.

Never skip Step 0, the persona interrogation, or the empathy narrative. These are
the highest-leverage outputs.

## Auto-Detect Product Type + Applicability Gate

Before proceeding, read the plan and infer the developer product type from content:

- Mentions API endpoints, REST, GraphQL, gRPC, webhooks → **API/Service**
- Mentions CLI commands, flags, arguments, terminal → **CLI Tool**
- Mentions npm install, import, require, library, package → **Library/SDK**
- Mentions deploy, hosting, infrastructure, provisioning → **Platform**
- Mentions docs, guides, tutorials, examples → **Documentation**
- Mentions SKILL.md, skill template, Claude Code, AI agent, MCP → **Claude Code Skill**

If NONE of the above: the plan has no developer-facing surface. Tell the user:
"This plan doesn't appear to have developer-facing surfaces. /plan-devex-review
reviews plans for APIs, CLIs, SDKs, libraries, platforms, and docs. Consider
/plan-eng-review or /plan-design-review instead." Exit gracefully.

If detected: State your classification and ask for confirmation. Do not ask from
scratch. "I'm reading this as a CLI Tool plan. Correct?"

A product can be multiple types. Identify the primary type for the initial assessment.
Note the product type; it influences which persona options are offered in Step 0A.

---

## PRE-REVIEW SYSTEM AUDIT (before Step 0)

Two pre-flight audits run before Step 0 to gather DX evidence and surface prior context. Both are fast and fail-safe.

### Retrospective check

Look for prior DX reviews on overlapping surfaces. If the plan touches CLI/API/SDK/docs that were reviewed recently, prior findings must inform this review:

```bash
git log --oneline -30 --all -- 'README*' 'docs/**' 'cli/**' '*api*' '*cli*' 2>/dev/null | grep -iE "dx|devex|docs|cli|api|onboarding|getting.started|error.message" | head -10
```

Surface any prior DX-related commits. If prior commits addressed similar concerns, the reviewer MUST read the commit messages and avoid re-flagging what was already decided.

### DX artifact scan

Scan the repo for DX-relevant evidence so Step 0 (persona, TTHW, magical moment) can reference concrete artifacts, not vibes:

```bash
# Getting-started surface area
ls README* INSTALL* QUICKSTART* GETTING_STARTED* 2>/dev/null | head -5

# CLI help (if this is a CLI product)
find . -type f \( -name "*.rs" -o -name "*.go" -o -name "*.ts" -o -name "*.py" \) -path "*/cli/*" 2>/dev/null | head -5

# Examples / tutorials
find examples/ docs/examples/ tutorials/ -type f 2>/dev/null | head -10

# Error messages grep (look for actual error text quality)
grep -riE "^[[:space:]]*(error|Error|ERROR).*[:!]" --include='*.py' --include='*.ts' --include='*.go' --include='*.rs' src/ cli/ lib/ 2>/dev/null | head -10
```

If artifacts are found: read them before Step 0. The reviewer's Step 0 persona and TTHW must reference these artifacts by name. If zero DX artifacts found: flag as critical gap (this is a DX emergency — the product has no public DX surface).

## Step 0: DX Investigation (before scoring)

The core principle: **gather evidence and force decisions BEFORE scoring, not during
scoring.** Steps 0A through 0G build the evidence base. Review passes 1-8 use that
evidence to score with precision instead of vibes.

### 0A. Developer Persona Interrogation

Before anything else, identify WHO the target developer is. Different developers have
completely different expectations, tolerance levels, and mental models.

**Gather evidence first:** Read README.md for "who is this for" language. Check
package.json description/keywords. Check design doc for user mentions. Check docs/
for audience signals.

Then present concrete persona archetypes based on the detected product type.

AskUserQuestion:

> "Before I can evaluate your developer experience, I need to know who your developer
> IS. Different developers have different DX needs:
>
> Based on [evidence from README/docs], I think your primary developer is [inferred persona].
>
> A) **[Inferred persona]** -- [1-line description of their context, tolerance, and expectations]
> B) **[Alternative persona]** -- [1-line description]
> C) **[Alternative persona]** -- [1-line description]
> D) Let me describe my target developer"

Persona examples by product type (pick the 3 most relevant):
- **YC founder building MVP** -- 30-minute integration tolerance, won't read docs, copies from README
- **Platform engineer at Series C** -- thorough evaluator, cares about security/SLAs/CI integration
- **Frontend dev adding a feature** -- TypeScript types, bundle size, React/Vue/Svelte examples
- **Backend dev integrating an API** -- cURL examples, auth flow clarity, rate limit docs
- **OSS contributor from GitHub** -- git clone && make test, CONTRIBUTING.md, issue templates
- **Student learning to code** -- needs hand-holding, clear error messages, lots of examples
- **DevOps engineer setting up infra** -- Terraform/Docker, non-interactive mode, env vars

After the user responds, produce a persona card:

```
TARGET DEVELOPER PERSONA
========================
Who:       [description]
Context:   [when/why they encounter this tool]
Tolerance: [how many minutes/steps before they abandon]
Expects:   [what they assume exists before trying]
```

**STOP.** Do NOT proceed until user responds. This persona shapes the entire review.

### 0B. Empathy Narrative as Conversation Starter

Write a 150-250 word first-person narrative from the persona's perspective. Walk
through the ACTUAL getting-started path from the README/docs. Be specific about
what they see, what they try, what they feel, and where they get confused.

Use the persona from 0A. Reference real files and content from the pre-review audit.
Not hypothetical. Trace the actual path: "I open the README. The first heading is
[actual heading]. I scroll down and find [actual install command]. I run it and see..."

Then SHOW it to the user via AskUserQuestion:

> "Here's what I think your [persona] developer experiences today:
>
> [full empathy narrative]
>
> Does this match reality? Where am I wrong?
>
> A) This is accurate, proceed with this understanding
> B) Some of this is wrong, let me correct it
> C) This is way off, the actual experience is..."

**STOP.** Incorporate corrections into the narrative. This narrative becomes a required
output section ("Developer Perspective") in the plan file. The implementer should read
it and feel what the developer feels.

### 0C. Competitive DX Benchmarking

Before scoring anything, understand how comparable tools handle DX. Use WebSearch to
find real TTHW data and onboarding approaches.

Run three searches:
1. "[product category] getting started developer experience {current year}"
2. "[closest competitor] developer onboarding time"
3. "[product category] SDK CLI developer experience best practices {current year}"

If WebSearch is unavailable: "Search unavailable. Using reference benchmarks: Stripe
(30s TTHW), Vercel (2min), Firebase (3min), Docker (5min)."

Produce a competitive benchmark table:

```
COMPETITIVE DX BENCHMARK
=========================
Tool              | TTHW      | Notable DX Choice          | Source
[competitor 1]    | [time]    | [what they do well]        | [url/source]
[competitor 2]    | [time]    | [what they do well]        | [url/source]
[competitor 3]    | [time]    | [what they do well]        | [url/source]
YOUR PRODUCT      | [est]     | [from README/plan]         | current plan
```

AskUserQuestion:

> "Your closest competitors' TTHW:
> [benchmark table]
>
> Your plan's current TTHW estimate: [X] minutes ([Y] steps).
>
> Where do you want to land?
>
> A) Champion tier (< 2 min) -- requires [specific changes]. Stripe/Vercel territory.
> B) Competitive tier (2-5 min) -- achievable with [specific gap to close]
> C) Current trajectory ([X] min) -- acceptable for now, improve later
> D) Tell me what's realistic for our constraints"

**STOP.** The chosen tier becomes the benchmark for Pass 1 (Getting Started).

### 0D. Magical Moment Design

Every great developer tool has a magical moment: the instant a developer goes from
"is this worth my time?" to "oh wow, this is real."

for gold standard examples.

Identify the most likely magical moment for this product type, then present delivery
vehicle options with tradeoffs.

AskUserQuestion:

> "For your [product type], the magical moment is: [specific moment, e.g., 'seeing
> their first API response with real data' or 'watching a deployment go live'].
>
> How should your [persona from 0A] experience this moment?
>
> A) **Interactive playground/sandbox** -- zero install, try in browser. Highest
>    conversion but requires building a hosted environment.
>    (human: ~1 week / CC: ~2 hours). Examples: Stripe's API explorer, Supabase SQL editor.
>
> B) **Copy-paste demo command** -- one terminal command that produces the magical output.
>    Low effort, high impact for CLI tools, but requires local install first.
>    (human: ~2 days / CC: ~30 min). Examples: `npx create-next-app`, `docker run hello-world`.
>
> C) **Video/GIF walkthrough** -- shows the magic without requiring any setup.
>    Passive (developer watches, doesn't do), but zero friction.
>    (human: ~1 day / CC: ~1 hour). Examples: Vercel's homepage deploy animation.
>
> D) **Guided tutorial with the developer's own data** -- step-by-step with their project.
>    Deepest engagement but longest time-to-magic.
>    (human: ~1 week / CC: ~2 hours). Examples: Stripe's interactive onboarding.
>
> E) Something else -- describe what you have in mind.
>
> RECOMMENDATION: [A/B/C/D] because for [persona], [reason]. Your competitor [name]
> uses [their approach]."

**STOP.** The chosen delivery vehicle is tracked through the scoring passes.

### 0E. Mode Selection

How deep should this DX review go?

Present three options:

AskUserQuestion:

> "How deep should this DX review go?
>
> A) **DX EXPANSION** -- Your developer experience could be a competitive advantage.
>    I'll propose ambitious DX improvements beyond what the plan covers. Every expansion
>    is opt-in via individual questions. I'll push hard.
>
> B) **DX POLISH** -- The plan's DX scope is right. I'll make every touchpoint bulletproof:
>    error messages, docs, CLI help, getting started. No scope additions, maximum rigor.
>    (recommended for most reviews)
>
> C) **DX TRIAGE** -- Focus only on the critical DX gaps that would block adoption.
>    Fast, surgical, for plans that need to ship soon.
>
> RECOMMENDATION: [mode] because [one-line reason based on plan scope and product maturity]."

Context-dependent defaults:
* New developer-facing product → default DX EXPANSION
* Enhancement to existing product → default DX POLISH
* Bug fix or urgent ship → default DX TRIAGE

Once selected, commit fully. Do not silently drift toward a different mode.

**STOP.** Do NOT proceed until user responds.

### 0F. Developer Journey Trace with Friction-Point Questions

Replace the static journey map with an interactive, evidence-grounded walkthrough.
For each journey stage, TRACE the actual experience (what file, what command, what
output) and ask about each friction point individually.

For each stage (Discover, Install, Hello World, Real Usage, Debug, Upgrade):

1. **Trace the actual path.** Read the README, docs, package.json, CLI help, or
   whatever the developer would encounter at this stage. Reference specific files
   and line numbers.

2. **Identify friction points with evidence.** Not "installation might be hard" but
   "Step 3 of the README requires Docker to be running, but nothing checks for Docker
   or tells the developer to install it. A [persona] without Docker will see [specific
   error or nothing]."

3. **AskUserQuestion per friction point.** One question per friction point found.
   Do NOT batch multiple friction points into one question.

   > "Journey Stage: INSTALL
   >
   > I traced the installation path. Your README says:
   > [actual install instructions]
   >
   > Friction point: [specific issue with evidence]
   >
   > A) Fix in plan -- [specific fix]
   > B) [Alternative approach]
   > C) Document the requirement prominently
   > D) Acceptable friction -- skip"

**DX TRIAGE mode:** Only trace Install and Hello World stages. Skip the rest.
**DX POLISH mode:** Trace all stages.
**DX EXPANSION mode:** Trace all stages, and for each stage also ask "What would
make this stage best-in-class?"

After all friction points are resolved, produce the updated journey map:

```
STAGE           | DEVELOPER DOES              | FRICTION POINTS      | STATUS
----------------|-----------------------------|--------------------- |--------
1. Discover     | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
2. Install      | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
3. Hello World  | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
4. Real Usage   | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
5. Debug        | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
6. Upgrade      | [action]                    | [resolved/deferred]  | [fixed/ok/deferred]
```

### 0G. First-Time Developer Roleplay

Using the persona from 0A and the journey trace from 0F, write a structured
"confusion report" from the perspective of a first-time developer. Include
timestamps to simulate real time passing.

```
FIRST-TIME DEVELOPER REPORT
============================
Persona: [from 0A]
Attempting: [product] getting started

CONFUSION LOG:
T+0:00  [What they do first. What they see.]
T+0:30  [Next action. What surprised or confused them.]
T+1:00  [What they tried. What happened.]
T+2:00  [Where they got stuck or succeeded.]
T+3:00  [Final state: gave up / succeeded / asked for help]
```

Ground this in the ACTUAL docs and code from the pre-review audit. Not hypothetical.
Reference specific README headings, error messages, and file paths.

AskUserQuestion:

> "I roleplayed as your [persona] developer attempting the getting started flow.
> Here's what confused me:
>
> [confusion report]
>
> Which of these should we address in the plan?
>
> A) All of them -- fix every confusion point
> B) Let me pick which ones matter
> C) The critical ones (#[N], #[N]) -- skip the rest
> D) This is unrealistic -- our developers already know [context]"

**STOP.** Do NOT proceed until user responds.

---

## The 0-10 Rating Method

For each DX section, rate the plan 0-10. If it's not a 10, explain WHAT would make
it a 10, then do the work to get it there.

**Critical rule:** Every rating MUST reference evidence from Step 0. Not "Getting
Started: 4/10" but "Getting Started: 4/10 because [persona from 0A] hits [friction
point from 0F] at step 3, and competitor [name from 0C] achieves this in [time]."

Pattern:
1. **Evidence recall:** Reference specific findings from Step 0 that apply to this dimension
2. Rate: "Getting Started Experience: 4/10"
3. Gap: "It's a 4 because [evidence]. A 10 would be [specific description for THIS product]."
4. Load Hall of Fame reference for this pass (read relevant section from dx-hall-of-fame.md)
5. Fix: Edit the plan to add what's missing
6. Re-rate: "Now 7/10, still missing [specific gap]"
7. AskUserQuestion if there's a genuine DX choice to resolve
8. Fix again until 10 or user says "good enough, move on"

**Mode-specific behavior:**
- **DX EXPANSION:** After fixing to 10, also ask "What would make this dimension
  best-in-class? What would make [persona] rave about it?" Present expansions as
  individual opt-in AskUserQuestions.
- **DX POLISH:** Fix every gap. No shortcuts. Trace each issue to specific files/lines.
- **DX TRIAGE:** Only flag gaps that would block adoption (score below 5). Skip gaps
  that are nice-to-have (score 5-7).

## Review Sections (8 passes, after Step 0 is complete)

**Anti-skip rule:** Never condense, abbreviate, or skip any review pass (1-8) regardless of plan type (strategy, spec, code, infra). Every pass in this skill exists for a reason. "This is a strategy doc so DX passes don't apply" is always wrong — DX gaps are where adoption breaks down. If a pass genuinely has zero findings, say "No issues found" and move on — but you must evaluate it.

## Prior Learnings

Search for relevant learnings from previous sessions:

```bash
echo "CROSS_PROJECT: $_CROSS_PROJ"
if [ "$_CROSS_PROJ" = "true" ]; then
else
fi
```

If `CROSS_PROJECT` is `unset` (first time): Use AskUserQuestion:

smarter on their codebase over time.

### DX Trend Check

Before starting review passes, check for prior DX reviews on this project:

```bash
```

If prior reviews exist, display the trend:
```
DX TREND (prior reviews):
  Dimension        | Prior Score | Notes
  Getting Started  | 4/10        | from 2026-03-15
  ...
```

### Pass 1: Getting Started Experience (Zero Friction)

**Load reference:** Read the `## Pass 1` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Can a developer go from zero to hello world in under 5 minutes?

**Evidence recall:** Reference the competitive benchmark from 0C (target tier), the
magical moment from 0D (delivery vehicle), and any Install/Hello World friction
points from 0F.

Evaluate:
- **Installation**: One command? One click? No prerequisites?
- **First run**: Does the first command produce visible, meaningful output?
- **Sandbox/Playground**: Can developers try before installing?
- **Free tier**: No credit card, no sales call, no company email?
- **Quick start guide**: Copy-paste complete? Shows real output?
- **Auth/credential bootstrapping**: How many steps between "I want to try" and "it works"?
- **Magical moment delivery**: Is the vehicle chosen in 0D actually in the plan?
- **Competitive gap**: How far is the TTHW from the target tier chosen in 0C?

FIX TO 10: Write the ideal getting started sequence. Specify exact commands,
expected output, and time budget per step. Target: 3 steps or fewer, under the
time chosen in 0C.

Stripe test: Can a [persona from 0A] go from "never heard of this" to "it worked"
in one terminal session without leaving the terminal?

**STOP.** AskUserQuestion once per issue. Recommend + WHY. Reference the persona.

### Pass 2: API/CLI/SDK Design (Usable + Useful)

**Load reference:** Read the `## Pass 2` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Is the interface intuitive, consistent, and complete?

**Evidence recall:** Does the API surface match [persona from 0A]'s mental model?
A YC founder expects `tool.do(thing)`. A platform engineer expects
`tool.configure(options).execute(thing)`.

Evaluate:
- **Naming**: Guessable without docs? Consistent grammar?
- **Defaults**: Every parameter has a sensible default? Simplest call gives useful result?
- **Consistency**: Same patterns across the entire API surface?
- **Completeness**: 100% coverage or do devs drop to raw HTTP for edge cases?
- **Discoverability**: Can devs explore from CLI/playground without docs?
- **Reliability/trust**: Latency, retries, rate limits, idempotency, offline behavior?
- **Progressive disclosure**: Simple case is production-ready, complexity revealed gradually?
- **Persona fit**: Does the interface match how [persona] thinks about the problem?

Good API design test: Can a [persona] use this API correctly after seeing one example?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 3: Error Messages & Debugging (Fight Uncertainty)

**Load reference:** Read the `## Pass 3` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: When something goes wrong, does the developer know what happened, why,
and how to fix it?

**Evidence recall:** Reference any error-related friction points from 0F and confusion
points from 0G.

**Trace 3 specific error paths** from the plan or codebase. For each, evaluate against
the three-tier system from the Hall of Fame:
- **Tier 1 (Elm):** Conversational, first person, exact location, suggested fix
- **Tier 2 (Rust):** Error code links to tutorial, primary + secondary labels, help section
- **Tier 3 (Stripe API):** Structured JSON with type, code, message, param, doc_url

For each error path, show what the developer currently sees vs. what they should see.

Also evaluate:
- **Permission/sandbox/safety model**: What can go wrong? How clear is the blast radius?
- **Debug mode**: Verbose output available?
- **Stack traces**: Useful or internal framework noise?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 4: Documentation & Learning (Findable + Learn by Doing)

**Load reference:** Read the `## Pass 4` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Can a developer find what they need and learn by doing?

**Evidence recall:** Does the docs architecture match [persona from 0A]'s learning
style? A YC founder needs copy-paste examples front and center. A platform engineer
needs architecture docs and API reference.

Evaluate:
- **Information architecture**: Find what they need in under 2 minutes?
- **Progressive disclosure**: Beginners see simple, experts find advanced?
- **Code examples**: Copy-paste complete? Work as-is? Real context?
- **Interactive elements**: Playgrounds, sandboxes, "try it" buttons?
- **Versioning**: Docs match the version dev is using?
- **Tutorials vs references**: Both exist?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 5: Upgrade & Migration Path (Credible)

**Load reference:** Read the `## Pass 5` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Can developers upgrade without fear?

Evaluate:
- **Backward compatibility**: What breaks? Blast radius limited?
- **Deprecation warnings**: Advance notice? Actionable? ("use newMethod() instead")
- **Migration guides**: Step-by-step for every breaking change?
- **Codemods**: Automated migration scripts?
- **Versioning strategy**: Semantic versioning? Clear policy?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 6: Developer Environment & Tooling (Valuable + Accessible)

**Load reference:** Read the `## Pass 6` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Does this integrate into developers' existing workflows?

**Evidence recall:** Does local dev setup work for [persona from 0A]'s typical
environment?

Evaluate:
- **Editor integration**: Language server? Autocomplete? Inline docs?
- **CI/CD**: Works in GitHub Actions, GitLab CI? Non-interactive mode?
- **TypeScript support**: Types included? Good IntelliSense?
- **Testing support**: Easy to mock? Test utilities?
- **Local development**: Hot reload? Watch mode? Fast feedback?
- **Cross-platform**: Mac, Linux, Windows? Docker? ARM/x86?
- **Local env reproducibility**: Works across OS, package managers, containers, proxies?
- **Observability/testability**: Dry-run mode? Verbose output? Sample apps? Fixtures?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 7: Community & Ecosystem (Findable + Desirable)

**Load reference:** Read the `## Pass 7` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Is there a community, and does the plan invest in ecosystem health?

Evaluate:
- **Open source**: Code open? Permissive license?
- **Community channels**: Where do devs ask questions? Someone answering?
- **Examples**: Real-world, runnable? Not just hello world?
- **Plugin/extension ecosystem**: Can devs extend it?
- **Contributing guide**: Process clear?
- **Pricing transparency**: No surprise bills?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Pass 8: DX Measurement & Feedback Loops (Implement + Refine)

**Load reference:** Read the `## Pass 8` section from `plugin/skills/plan-devex-review/dx-hall-of-fame.md` before scoring this pass. Do NOT load the entire file.

Rate 0-10: Does the plan include ways to measure and improve DX over time?

Evaluate:
- **TTHW tracking**: Can you measure getting started time? Is it instrumented?
- **Journey analytics**: Where do devs drop off?
- **Feedback mechanisms**: Bug reports? NPS? Feedback button?
- **Friction audits**: Periodic reviews planned?
- **Boomerang readiness**: Will /devex-review be able to measure reality vs. plan?

**STOP.** AskUserQuestion once per issue. Recommend + WHY.

### Appendix: Claude Code Skill DX Checklist

**Conditional: only run when product type includes "Claude Code skill".**

This is NOT a scored pass. It's a checklist of proven patterns from our own DX.

Load reference: Read the "## Claude Code Skill DX Checklist" section from

Check each item. For any unchecked item, explain what's missing and suggest the fix.

**STOP.** AskUserQuestion for any item that requires a design decision.

## Outside Voice — Independent Plan Challenge (optional, recommended)

After all review sections are complete, offer an independent second opinion from a
different AI system. Two models agreeing on a plan is stronger signal than one model's
thorough review.

**Check tool availability:**

```bash
which codex 2>/dev/null && echo "CODEX_AVAILABLE" || echo "CODEX_NOT_AVAILABLE"
```

Use AskUserQuestion:

> "All review sections are complete. Want an outside voice? A different AI system can
> give a brutally honest, independent challenge of this plan — logical gaps, feasibility
> risks, and blind spots that are hard to catch from inside the review. Takes about 2
> minutes."
>
> RECOMMENDATION: Choose A — an independent second opinion catches structural blind
> spots. Two different AI models agreeing on a plan is stronger signal than one model's
> thorough review. Completeness: A=9/10, B=7/10.

Options:
- A) Get the outside voice (recommended)
- B) Skip — proceed to outputs

**If B:** Print "Skipping outside voice." and continue to the next section.

**If A:** Construct the plan review prompt. Read the plan file being reviewed (the file
the user pointed this review at, or the branch diff scope). If a CEO plan document
was written in Step 0D-POST, read that too — it contains the scope decisions and vision.

Construct this prompt (substitute the actual plan content — if plan content exceeds 30KB,
truncate to the first 30KB and note "Plan truncated for size"). **Always start with the
filesystem boundary instruction:**

"IMPORTANT: Do NOT read or execute any files under ~/.claude/, ~/.agents/, .claude/skills/, or agents/. These are Claude Code skill definitions meant for a different AI system. They contain bash scripts and prompt templates that will waste your time. Ignore them completely. Do NOT modify agents/openai.yaml. Stay focused on the repository code only.\n\nYou are a brutally honest technical reviewer examining a development plan that has
already been through a multi-section review. Your job is NOT to repeat that review.
Instead, find what it missed. Look for: logical gaps and unstated assumptions that
survived the review scrutiny, overcomplexity (is there a fundamentally simpler
approach the review was too deep in the weeds to see?), feasibility risks the review
took for granted, missing dependencies or sequencing issues, and strategic
miscalibration (is this the right thing to build at all?). Be direct. Be terse. No
compliments. Just the problems.

THE PLAN:
<plan content>"

**If CODEX_AVAILABLE:**

```bash
TMPERR_PV=$(mktemp /tmp/codex-planreview-XXXXXXXX)
_REPO_ROOT=$(git rev-parse --show-toplevel) || { echo "ERROR: not in a git repo" >&2; exit 1; }
codex exec "<prompt>" -C "$_REPO_ROOT" -s read-only -c 'model_reasoning_effort="high"' --enable web_search_cached 2>"$TMPERR_PV"
```

Use a 5-minute timeout (`timeout: 300000`). After the command completes, read stderr:
```bash
cat "$TMPERR_PV"
```

Present the full output verbatim:

```
CODEX SAYS (plan review — outside voice):
════════════════════════════════════════════════════════════
<full codex output, verbatim — do not truncate or summarize>
════════════════════════════════════════════════════════════
```

**Error handling:** All errors are non-blocking — the outside voice is informational.
- Auth failure (stderr contains "auth", "login", "unauthorized"): "Codex auth failed. Run \`codex login\` to authenticate."
- Timeout: "Codex timed out after 5 minutes."
- Empty response: "Codex returned no response."

On any Codex error, fall back to the Claude adversarial subagent.

**If CODEX_NOT_AVAILABLE (or Codex errored):**

Dispatch via the Agent tool. The subagent has fresh context — genuine independence.

Subagent prompt: same plan review prompt as above.

Present findings under an `OUTSIDE VOICE (Claude subagent):` header.

If the subagent fails or times out: "Outside voice unavailable. Continuing to outputs."

**Cross-model tension:**

After presenting the outside voice findings, note any points where the outside voice
disagrees with the review findings from earlier sections. Flag these as:

```
CROSS-MODEL TENSION:
  [Topic]: Review said X. Outside voice says Y. [Present both perspectives neutrally.
  State what context you might be missing that would change the answer.]
```

**User Sovereignty:** Do NOT auto-incorporate outside voice recommendations into the plan.
Present each tension point to the user. The user decides. Cross-model agreement is a
strong signal — present it as such — but it is NOT permission to act. You may state
which argument you find more compelling, but you MUST NOT apply the change without
explicit user approval.

For each substantive tension point, use AskUserQuestion:

> "Cross-model disagreement on [topic]. The review found [X] but the outside voice
> argues [Y]. [One sentence on what context you might be missing.]"
>
> RECOMMENDATION: Choose [A or B] because [one-line reason explaining which argument
> is more compelling and why]. Completeness: A=X/10, B=Y/10.

Options:
- A) Accept the outside voice's recommendation (I'll apply this change)
- B) Keep the current approach (reject the outside voice)
- C) Investigate further before deciding
- D) Add to TODOS.md for later

Wait for the user's response. Do NOT default to accepting because you agree with the
outside voice. If the user chooses B, the current approach stands — do not re-argue.

If no tension points exist, note: "No cross-model tension — both reviewers agree."

**Persist the result:**
```bash
```

Substitute: STATUS = "clean" if no findings, "issues_found" if findings exist.
SOURCE = "codex" if Codex ran, "claude" if subagent ran.

**Cleanup:** Run `rm -f "$TMPERR_PV"` after processing (if Codex was used).

---

When constructing the outside voice prompt, include the Developer Persona from Step 0A
and the Competitive Benchmark from Step 0C. The outside voice should critique the plan
in the context of who is using it and what they're competing against.

## CRITICAL RULE — How to ask questions

Follow the AskUserQuestion format from the Preamble above. Additional rules for
DX reviews:

* **One issue = one AskUserQuestion call.** Never combine multiple issues.
* **Ground every question in evidence.** Reference the persona, competitive benchmark,
  empathy narrative, or friction trace. Never ask a question in the abstract.
* **Frame pain from the persona's perspective.** Not "developers would be frustrated"
  but "[persona from 0A] would hit this at minute [N] of their getting-started flow
  and [specific consequence: abandon, file an issue, hack a workaround]."
* Present 2-3 options. For each: effort to fix, impact on developer adoption.
* **Map to DX First Principles above.** One sentence connecting your recommendation
  to a specific principle (e.g., "This violates 'zero friction at T0' because
  [persona] needs 3 extra config steps before their first API call").
* **Escape hatch:** If a section has no issues, say so and move on. If a gap has an
  obvious fix, state what you'll add and move on, don't waste a question.
* Assume the user hasn't looked at this window in 20 minutes. Re-ground every question.

## Required Outputs

### Developer Persona Card
The persona card from Step 0A. This goes at the top of the plan's DX section.

### Developer Empathy Narrative
The first-person narrative from Step 0B, updated with user corrections.
Use the template from the **Empathy Narrative** section below. Mandatory output — not optional.

### Competitive DX Benchmark
The benchmark table from Step 0C, updated with the product's post-review scores.

### Magical Moment Specification
The chosen delivery vehicle from Step 0D with implementation requirements.

### Developer Journey Map
The journey map from Step 0F, updated with all friction point resolutions.
Produce the full 9-stage table from the **Developer Journey Map** section below.

### First-Time Developer Confusion Report
The roleplay report from Step 0G, annotated with which items were addressed.

### "NOT in scope" section
DX improvements considered and explicitly deferred, with one-line rationale each.

### "What already exists" section
Existing docs, examples, error handling, and DX patterns that the plan should reuse.

### TODOS.md updates
After all review passes are complete, present each potential TODO as its own individual
AskUserQuestion. Never batch. For DX debt: missing error messages, unspecified upgrade
paths, documentation gaps, missing SDK languages. Each TODO gets:
* **What:** One-line description
* **Why:** The concrete developer pain it causes
* **Pros:** What you gain (adoption, retention, satisfaction)
* **Cons:** Cost, complexity, or risks
* **Context:** Enough detail for someone to pick this up in 3 months
* **Depends on / blocked by:** Prerequisites

Options: **A)** Add to TODOS.md **B)** Skip **C)** Build it now

### DX Scorecard

Produce the 8-dimension scorecard from the **DX Scorecard** section below.
Each dimension: score 0-10, finding, recommendation.

Also include the TTHW row from the **TTHW Assessment** section below.

If all passes 8+: "DX plan is solid. Developers will have a good experience."
If any below 6: Flag as critical DX debt with specific impact on adoption.
If TTHW > 10 min: Flag as blocking issue.

### DX Implementation Checklist

```
DX IMPLEMENTATION CHECKLIST
============================
[ ] Time to hello world < [target from 0C]
[ ] Installation is one command
[ ] First run produces meaningful output
[ ] Magical moment delivered via [vehicle from 0D]
[ ] Every error message has: problem + cause + fix + docs link
[ ] API/CLI naming is guessable without docs
[ ] Every parameter has a sensible default
[ ] Docs have copy-paste examples that actually work
[ ] Examples show real use cases, not just hello world
[ ] Upgrade path documented with migration guide
[ ] Breaking changes have deprecation warnings + codemods
[ ] TypeScript types included (if applicable)
[ ] Works in CI/CD without special configuration
[ ] Free tier available, no credit card required
[ ] Changelog exists and is maintained
[ ] Search works in documentation
[ ] Community channel exists and is monitored
```

### Unresolved Decisions
If any AskUserQuestion goes unanswered, note here. Never silently default.
