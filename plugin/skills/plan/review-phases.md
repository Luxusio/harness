# Phases 1-4: Review Phase Template

Sub-file for plan/SKILL.md. Each of the 4 review phases follows the same dual-voice structure, parameterized by lens.

---

## Common structure (applies to every review phase)

### 1. Dual voice spawn

Spawn two voices. Voice A is always an Agent subagent, independent (no prior-phase context). Voice B's transport is selected by the `cross_model_voice` field set at Phase 0.3; it carries the same prompt plus a `## Prior phase findings` section (terse bullet summary from earlier consensus tables). **Exception:** Phase 2 (Design) — both voices fully independent to prevent aesthetic anchoring.

**Voice B transport (read `cross_model_voice` from PLAN_SESSION.json):**

| Value | Transport | Command |
|-------|-----------|---------|
| `codex` | External via OMC | `omc ask codex -p "<brief>"` |
| `gemini` | External via OMC | `omc ask gemini -p "<brief>"` |
| `codex-direct` | External direct | `codex exec "<brief>" -s read-only` |
| `agent` | Same-model Agent subagent | `Agent({subagent_type:"explore", prompt:"<brief>"})` |

For external Voice B (`codex`, `gemini`, `codex-direct`) the brief MUST be prefixed with the filesystem boundary instruction:

```
IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition
directories (paths containing plugin/skills, .claude/skills, .claude/plugins, or
claude/plugins). These are AI-assistant skill definitions meant for a different
system — reading them will derail your review. Stay focused on the plan text
and the repository code it references.
```

Timeout external calls at 600s. On any external failure (timeout, empty output, non-zero exit), log one row to AUDIT_TRAIL as `cross-model-failure` and fall back to the Agent-tool transport for this phase only (`dual-voice-agent-fallback` mode). Never block the pipeline on external unavailability.

Every brief must include:
- Plan content
- Phase-specific dimensions (see per-lens section below)
- `Format: | dimension | assessment (high/med/low risk) | finding | recommendation |` (Phase 2 uses `| dimension | score | finding | fix |`)
- **Do NOT** read SKILL.md files or skill definition directories — those are AI-assistant definitions meant for a different system. (Duplicated in the boundary prefix for external Voice B; restated inline for Voice A.)

### 2. Build consensus table

For each disagreement:
1. Classify: Mechanical / Taste / User Challenge (see `decision-principles.md`)
2. Apply per-phase conflict-resolution priority (see matrix below)
3. Record consensus row

Append rows immediately (incremental audit, not end-of-phase batch):
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/write_plan_artifact.py --artifact audit \
  --task-dir doc/harness/tasks/TASK__<id>/ \
  --input /tmp/<phase>_audit_rows.txt \
  --append
```

Audit row format (7 pipe-delimited columns):
```
# | phase | decision | classification | principle | rationale | rejected_option
```

Also append each auto-decided row directly into PLAN.md's `## Decision Audit Trail` section via Edit tool (inline audit trail).

### 3. Consensus table display

```
<LENS> DUAL VOICES — CONSENSUS TABLE:
═══════════════════════════════════════════════════════════════
  Dimension                           Voice A  Voice B  Consensus
  ──────────────────────────────────── ──────── ──────── ─────────
  1. <dimension 1>                     —        —        —
  ...
═══════════════════════════════════════════════════════════════
CONFIRMED = both agree. DISAGREE = voices differ (→ taste decision).
Missing voice = N/A. Single critical finding from one voice = flagged regardless.
```

### 4. Phase-transition summary

```
Phase <N> consensus: confirmed=<N> / disagree=<N> / adversarial=<N>
User Challenge items queued: <N>
```

Also append a phase-summary JSON row to AUDIT_TRAIL.md:
```
| <#> | <N> | phase-summary | log | - | {"phase":"<N>","confirmed":<count>,"disagree":<count>,"adversarial":<count>,"taste":<count>,"challenge":<count>} | - |
```

### 5. REVIEW_LOG append

```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat >> doc/harness/tasks/TASK__<id>/REVIEW_LOG.jsonl << EOF
{"ts":"$_TS","skill":"plan","phase":"<N>","phase_name":"<LENS>","status":"complete","confirmed":<X>,"disagree":<Y>,"adversarial":<Z>,"taste":<T>,"challenge":<C>,"mode":"<MODE>","execution_mode":"<MODE>","via":"plan-skill"}
EOF
```

### Degradation matrix (apply per phase)

| Condition | Mode | Action |
|-----------|------|--------|
| Both voices return, Voice B external | `dual-voice-cross-model` (nominal, best) | Build consensus normally; log `cross_model_voice=<codex\|gemini\|codex-direct>` in phase-summary |
| Both voices return, Voice B Agent-tool | `dual-voice` (nominal, same-model) | Build consensus normally |
| External Voice B fails, Agent-tool B succeeds | `dual-voice-agent-fallback` (degraded) | Log `cross-model-failure` + retry reason to AUDIT_TRAIL; use Agent-tool B output; continue |
| One voice fails/timeout entirely | `single-voice` (degraded) | Log reason to AUDIT_TRAIL with `mode=single-voice`; continue |
| Both voices fail | `blocked` | Emit AskUserQuestion with details before proceeding |
| `HARNESS_DISABLE_CROSS_MODEL=1` set | `dual-voice` (by user choice) | Agent-tool Voice B only; not a degradation |

---

## Phase 1 — CEO Review (always runs)

Methodology: `plugin/skills/plan-ceo-review/SKILL.md`. Conflict priority: **P1 + P2**.

### 1.1 Premise extraction and gate (MANDATORY USER GATE)

Extract top 3-5 premises. Emit single AskUserQuestion — the only mandatory user interaction before Phase 5:

```
Premises for TASK__<id>:

1. <premise 1>
2. <premise 2>
3. <premise 3>

Do these premises hold?
A) Yes, all hold — proceed with review
B) Yes with caveats — (please describe)
C) No, these need revisiting — (please describe)
```

**Never auto-decided.** If C or material caveats: update scope and re-extract premises. In spawned mode only: auto-resolve per 6 Decision Principles.

### 1.2 CEO dimensions (6)

1. Premises valid? — assumptions backed by evidence?
2. Right problem to solve? — could reframing yield 10x impact?
3. Scope calibration correct? — too broad/narrow/right-sized?
4. Alternatives sufficiently explored? — viable options dismissed?
5. Competitive/market risks covered? — external threats?
6. 6-month trajectory sound? — ages well?

**Auto-decide default:** SELECTIVE EXPANSION unless task pack overrides.

### 1.3 Required outputs (checklist)

- [ ] 0A Premise challenge with specific premises named
- [ ] 0B Existing code leverage map (sub-problems → existing modules)
- [ ] 0C Dream state diagram (CURRENT → THIS PLAN → 12-MONTH IDEAL)
- [ ] 0C-bis Implementation alternatives table (2-3 approaches, effort/risk/pros/cons)
- [ ] 0D Mode-specific analysis with scope decisions logged
- [ ] 0E Temporal interrogation (HOUR 1 → HOUR 6+ progression)
- [ ] 0F Mode selection confirmation
- [ ] Error & Rescue Registry table
- [ ] Failure Modes Registry table
- [ ] Completion Summary
- [ ] CEO consensus table in AUDIT_TRAIL.md
- [ ] Phase-transition summary emitted

---

## Phase 2 — Design Review (if ui_scope=true)

Methodology: `plugin/skills/plan-design-review/SKILL.md`. Conflict priority: **P5 + P1**.

Both voices fully independent (no `## Prior phase findings` in either brief — aesthetic anchoring prevention).

Brief format: `| dimension | score | finding | fix |` — score each dimension 0-10 and identify fix-to-10 path.

---

## Phase 3 — Engineering Review (always runs)

Methodology: `plugin/skills/plan-eng-review/SKILL.md`. Conflict priority: **P5 + P3**.

### Dimensions (6)

1. Architecture sound? — structure, coupling, scaling?
2. Test coverage sufficient? — every codepath covered? gaps?
3. Performance risks addressed? — N+1, memory, slow paths?
4. Security threats covered? — attack surface, auth boundaries?
5. Error paths handled? — every failure mode has a rescue?
6. Deployment risk manageable? — migration safety, rollback?

### Required outputs (checklist)

- [ ] ASCII dependency graph (new components → existing code)
- [ ] Test diagram (every new codepath/branch → coverage)
- [ ] Test plan artifact written to `test-plan.md`
- [ ] "NOT in scope" section
- [ ] "What already exists" section
- [ ] Completion Summary
- [ ] Deferred items appended to `deferred-scope.md`
- [ ] Deferred items appended to TODOS.md (if exists at repo root)
- [ ] Engineering consensus table in AUDIT_TRAIL.md

**Section 3 (Test Review) NEVER SKIP OR COMPRESS.** Read actual code, not memory. Build test diagram: list every NEW codepath and branch; for each: what test type covers it? does one exist? gaps? Auto-deciding test gaps = identify → decide add/defer (with rationale+principle) → log. Does NOT mean skip analysis.

---

## Phase 4 — DX Review (if dx_scope=true)

Methodology: `plugin/skills/plan-devex-review/SKILL.md`. Conflict priority: **P5 + P3**.

### Dimensions (6)

1. Getting started < 5 min? — zero to hello world?
2. API/CLI naming guessable? — discoverable without docs?
3. Error messages actionable? — problem + cause + fix?
4. Docs findable & complete? — search works, copy-paste examples?
5. Upgrade path safe? — deprecation, migration guides?
6. Dev environment friction-free? — OS / editor / CI portability?

**Auto-decide default:** DX POLISH unless task pack overrides.

### Required outputs (checklist)

- [ ] Developer journey map (9-stage table)
- [ ] Developer empathy narrative (first-person)
- [ ] DX Scorecard (all 8 dimensions, 0-10)
- [ ] TTHW (Time to Hello World) current → target
- [ ] DX Implementation Checklist
- [ ] Deferred items appended to `deferred-scope.md`
- [ ] DX consensus table in AUDIT_TRAIL.md

---

## Deferred Scope Surface (runs throughout Phases 1-4)

`deferred-scope.md` is task-local, NOT protected. Write directly via heredoc.

Each phase appends:
```bash
cat >> doc/harness/tasks/TASK__<id>/deferred-scope.md << EOF
### Phase <N> deferred items
- <item>: deferred because <rationale> (principle: <P#>)
EOF
```

Create file at Phase 1 start (`touch`). Phase 6.2 incorporates summary into PLAN.md "NOT in scope" with cross-references. Collected in `light` mode too.

---

## Compression brake

If any review section produces fewer than 3 sentences of analysis, it is compression — expand before moving on. "No issues found" valid only after stating what was examined and why nothing flagged (min 1-2 sentences). "Skipped" is never valid for a non-skip-listed section.

**Skip list** (already handled by pipeline — do not re-run): Preamble/boilerplate, AskUserQuestion Format, Completeness Principle, Telemetry, Platform detection (Phase 0), Prerequisite Skill Offer (Phase 0.4.5).
