# Decision Principles & Classification

Sub-file for plan/SKILL.md. Covers: 6 Decision Principles, classification, auto-decide rules, completion status, repo ownership, search layers.

---

## The 6 Decision Principles

Applied to every contested item between Voice A and Voice B. First applicable wins.

| Code | Name | Rule |
|------|------|------|
| P1 | Choose completeness | Ship the whole thing. Cover more edge cases. |
| P2 | Boil the lake | Complete every section fully. Fix everything in blast radius. |
| P3 | Pragmatic | Ship working over elegant theory. Cleaner of two options. |
| P4 | DRY | No repetition across plan sections. Reuse what exists. |
| P5 | Explicit over clever | Readable over terse. 10-line obvious fix > 200-line abstraction. |
| P6 | Bias toward action | Forward progress over paralysis. Flag concerns, don't block. |

**Per-phase priority:**
- Phase 1 (CEO): P6 + P3
- Phase 2 (Design): P5 + P6
- Phase 3 (Engineering): P5 + P3
- Phase 4 (DX): P5 + P3

---

## Decision Classification

**Mechanical** — Objectively correct answer exists (wrong import, broken ref, missing required field). Auto-decide silently and retain any material rationale in PLAN.md.

**Taste** — Two reasonable approaches with tradeoffs (naming, structure, sequencing). Auto-decide via principles. Surface at Phase 5.2 for user awareness.

**User Challenge / unresolved material decision** — Review needs a user-owned
choice about product outcome, material scope, risk acceptance, irreversible
behavior, external state, or changing a user-stated direction. Reviewer
agreement strengthens a recommendation but is not required for a genuinely
unresolved choice. **Never auto-decided.** Collect it for the single Phase 5.3
decision interaction.

**Scope decisions** within the explicitly authorized outcome (implementation
partitioning, tests, docs, and complete handling of named behavior) are
Mechanical by default. A material outcome or scope expansion beyond the request
or clarification is a User Challenge; P1 never grants authority to add it.

If voices disagree on classification, escalate to higher tier (Taste vs. User Challenge → User Challenge).

**Adversarial** — Rows from fresh-context reviewers. Retain informational rows
in PLAN.md; surface them only when they create an unresolved material decision.

---

## What Auto-Decide Means

When `auto_decide` is active:

**MUST:**
- Resolve every Mechanical and Taste via 6 Principles (first applicable wins).
- Keep every auto-decision in working context and materialize it in PLAN.md.
- Surface all auto-decided Taste items at Phase 5.2.
- Default CEO to SELECTIVE EXPANSION; DX to DX POLISH.
- Complete all mandatory phase outputs at full depth.

**MUST NOT:**
- Auto-decide unresolved material premises or User Challenge items.
- Reduce Voice A/B depth or skip any mandatory output.
- Redirect to interactive mid-pipeline. All decisions accumulate and surface at Phase 5.

**User-owned decisions are never auto-decided.** Existing request,
clarification, and explicit delegation may already authorize a decision; model
agreement or a planning mode cannot create that authority.

**Spawned mode:** `spawned_session: true` (or `HARNESS_SPAWNED=1`) may reuse
authority explicitly delegated by the parent context. If a material choice is
not delegated, return or relay the unresolved decision bundle; do not select it
with the Principles. See intake.md Phase 0.0-S.

---

## Completion Status Protocol

Use exactly one of:

- **DONE** — All steps complete. Evidence provided.
- **DONE_WITH_CONCERNS** — Completed with issues. List each.
- **BLOCKED** — Cannot proceed:
  ```
  STATUS: BLOCKED
  REASON: [1-2 sentences]
  ATTEMPTED: [what was tried]
  RECOMMENDATION: [what user should do next]
  ```
- **NEEDS_CONTEXT** — Missing info:
  ```
  STATUS: NEEDS_CONTEXT
  MISSING: [exactly what]
  IMPACT: [what is blocked]
  ```

**Escalation rule:** 3 attempts on any phase without success → STOP and emit `STATUS: BLOCKED`. Bad work is worse than no work.

---

## Repo Ownership — See Something, Say Something

`REPO_MODE` (from task context; default `unknown` → treat as `collaborative`):

- **solo** — You own everything. Investigate proactively; offer to fix. One-sentence note: what you noticed and impact.
- **collaborative** — Others may own adjacent code. Add a material ownership or
scope choice to the consolidated decision bundle; do not fix it without
authorization.
- **unknown** — Treat as collaborative.

Always flag anything wrong, even in collaborative mode. One sentence. Never silently ignore a visible defect.

---

## Search Before Building — 3 Layers

Apply when Voice A/B briefs prompt reviewers to evaluate technical choices. Include one-line "Layer X reasoning" note in brief when choice is non-obvious.

- **Layer 1 (tried-and-true):** Well-established patterns with years of production validation. Prize these. Reuse existing modules/patterns/conventions before proposing new.
- **Layer 2 (new-and-popular):** Recently popular, growing adoption. Scrutinise: popular because it solves a real problem, or because it is new? Check codebase before recommending.
- **Layer 3 (first-principles):** Reasoning from fundamentals. Prize above Layers 1-2 when they conflict. When first-principles contradicts conventional wisdom, name it and log Eureka.

### Eureka logging

When first-principles reaches a conclusion contradicting conventional wisdom:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
mkdir -p doc/harness 2>/dev/null || true
echo '{"ts":"'"$_TS"'","type":"eureka","skill":"plan","branch":"'"$_BRANCH"'","key":"SHORT_KEY","insight":"ONE_LINE_SUMMARY","source":"first-principles","task":"TASK__<id>","task_run_id":"<TASK.json run_id>"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```
Only genuine first-principles discoveries. Non-blocking.

---

## AskUserQuestion Format

Every AskUserQuestion from this skill MUST begin with a one-line orientation header and follow the 4-rule structure.

**One-line header (mandatory first line of every question body):**

```
Task: TASK__<id> | Phase: <current> | Step: <name>
```

Applies to: prerequisite offer (0.4.5) and the consolidated decision interaction (5.3).

**Exception — an explicitly requested pre-code approval (§5.4.1):** uses the
§5.1 outcome-only template, not the 4-rule body. It intentionally hides internal
review state.

**4-rule question structure:** every AskUserQuestion body MUST follow this four-part shape:

1. **Re-ground** — restate the project, active task, and current phase in one or two sentences. The user may not have looked at the window in 20 minutes; assume zero context carryover.
2. **Simplify** — explain the decision in plain language a new contributor could follow. No internal jargon, no implementation-detail names. Say what it DOES, not what it's CALLED.
3. **Recommend** — one line: `RECOMMENDATION: <option> because <one-line reason>`. Always prefer the completeness-dominant option (see Principle P1). Include `Completeness: X/10` per option (scoring below). Flag explicitly if any option is ≤5.
4. **Options** — lettered 2-4 options (AskUserQuestion's schema caps at 4). For effort-heavy options, show both scales: `(human: ~X / plan-skill: ~Y)`.

**Concrete example** (from the consolidated decision interaction):

```
Task: TASK__<id> | Phase: 5 | Step: Unresolved decisions

[Re-ground] We reviewed the plan for <feature> in <repo>. Everything already authorized by the request or clarification is settled.

[Simplify] The items below still change outcome, scope, or risk and need your decision before implementation.

Decisions:
1. <decision + recommendation>
2. <decision + recommendation>

[Recommend] RECOMMENDATION: A because it preserves the requested outcome with the smallest risk. Completeness: 10/10 (A), 7/10 (B), 7/10 (C).

[Options]
A) Accept the recommended bundle               (Completeness: 10/10)
B) Keep the original directions                (Completeness: 7/10)
C) Modify item by item in the reply             (Completeness: 7/10)
```

Keep the bracketed labels (`[Re-ground]`, `[Simplify]`, `[Recommend]`, `[Options]`) literally in the question body — they help the user scan long questions after a context gap.

Do NOT expand beyond 4 rules. Do NOT add a lengthy recap above the header — the one-line header is the whole orientation line.

**Completeness scoring per option (required):**
- **10** — Complete: all edges, full coverage, no follow-up
- **7** — Happy path: main flow, skips some edges
- **3** — Shortcut: defers significant work

If both options 8+: recommend the higher. If one ≤5: flag explicitly. For effort-heavy options, show both scales: `(human: ~X days / plan-skill: ~Y min)`.

**Effort reference:**

| Task type | Human | Plan-skill | Compression |
|-----------|-------|-----------|-------------|
| Boilerplate | 2 days | 15 min | ~100× |
| Tests | 1 day | 15 min | ~50× |
| Feature | 1 week | 30 min | ~30× |
| Bug fix | 4 hours | 15 min | ~20× |
