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

**Mechanical** — Objectively correct answer exists (wrong import, broken ref, missing required field). Auto-decide silently. Append one audit row.

**Taste** — Two reasonable approaches with tradeoffs (naming, structure, sequencing). Auto-decide via principles. Surface at Phase 5.2 for user awareness.

**User Challenge** — Both voices independently recommend changing a user-stated direction. **Never auto-decided.** Surface at Phase 5.3 with full framing (user direction / dual-model recommendation / reasoning / blind spots / downside cost).

If voices disagree on classification, escalate to higher tier (Taste vs. User Challenge → User Challenge).

**Adversarial** — Rows from fresh-context reviewers at the approval gate. Surface at Phase 5 for informational review; never auto-applied.

---

## What Auto-Decide Means

When `auto_decide` is active:

**MUST:**
- Resolve every Mechanical and Taste via 6 Principles (first applicable wins).
- Log every auto-decision to AUDIT_TRAIL.md immediately, one row per.
- Surface all auto-decided Taste items at Phase 5.2.
- Default CEO to SELECTIVE EXPANSION; DX to DX POLISH.
- Complete all mandatory phase outputs at full depth.

**MUST NOT:**
- Auto-decide premise confirmation (Phase 1.1).
- Auto-decide User Challenge items (Phase 5.3).
- Reduce Voice A/B depth or skip any mandatory output.
- Redirect to interactive mid-pipeline. All decisions accumulate and surface at Phase 5.

**Two gates never auto-decided:**
1. Phase 1.1 premise confirmation.
2. Phase 5.3 User Challenge items.

**Spawned mode override:** `spawned_session: true` (or `HARNESS_SPAWNED=1`) forces auto-decide AND auto-resolves ALL AskUserQuestion including the premise gate, using recommended option or Principles. See intake.md Phase 0.0-S.

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

`REPO_MODE` (from task pack or TASK_STATE.yaml `repo_mode`; default `unknown` → treat as `collaborative`):

- **solo** — You own everything. Investigate proactively; offer to fix. One-sentence note: what you noticed and impact.
- **collaborative** — Others may own adjacent code. Flag via AskUserQuestion; do NOT fix without approval.
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
echo '{"ts":"'"$_TS"'","type":"eureka","skill":"plan","branch":"'"$_BRANCH"'","insight":"ONE_LINE_SUMMARY","source":"first-principles"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```
Only genuine first-principles discoveries. Non-blocking.

---

## AskUserQuestion Format

Every AskUserQuestion from this skill MUST begin with this header as the first line:

```
Task: TASK__<id> | Phase: <current> | Step: <name>

<question body>

A) ...
B) ...
```

Applies to: premise gate (1.1), prerequisite offer (0.4.5), User Challenge (5.3), gate options (5.4.1).

Do NOT add lengthy recap. One-line header is sufficient orientation.

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
