# harness2 runtime rules

Lightweight execution harness for Claude Code.
7-field TASK_STATE + on-the-fly routing + artifact-provenance.
Self-contained — no plugin-legacy dependency.

## 1. Canonical Loop

Every repo-mutating task:
```
plan → develop → verify → close
```
No step skipped. Smallest coherent diff per step.

## 2. MCP tools

**Core (coordinator):**
- `task_start` — create/resume task, return fresh context
- `task_context` — refresh task state (only when needed)
- `task_verify` — sync changed paths + check verification
- `task_close` — gate: runtime verdict PASS → close

**Artifact writes (subagent-only — coordinator must NOT call):**
- `write_critic_runtime` → CRITIC__runtime.md + runtime_verdict
- `write_handoff` → HANDOFF.md
- `write_doc_sync` → DOC_SYNC.md

Provenance = artifact existence. No counters.

## 3. TASK_STATE (7 fields only)

```yaml
task_id: TASK__xxx
status: created|planning|implementing|verifying|closed
runtime_verdict: pending|PASS|FAIL
touched_paths: []
plan_session_state: closed|context_open|write_open
closed_at: null
updated: 2026-04-14T00:00:00Z
```

Routing is computed on-the-fly from manifest + artifacts. Never stored in TASK_STATE.

## 4. Plan-first rule

Do not mutate source before PLAN.md exists.
Short approvals only authorize the last explicit transition proposed.

## 5. Artifact ownership

| Artifact | Owner |
|----------|-------|
| PLAN.md | plan-skill |
| CHECKS.yaml | plan-skill (create) + update_checks.py CLI (develop/qa updates) |
| source + HANDOFF.md + DOC_SYNC.md + distilled change doc | developer |
| CRITIC__runtime.md | qa-browser / qa-api / qa-cli |

Do not write another role's artifact. Prewrite gate enforces this.

## 6. Auto-routing

| Intent | Route to |
|--------|----------|
| Set up harness | `Skill(setup)` |
| New feature / build something | `Skill(harness:plan)` |
| Run full cycle | `Skill(harness:run)` |
| CEO review | `Skill(plan-ceo-review)` |
| Architecture review | `Skill(plan-eng-review)` |
| Design review | `Skill(plan-design-review)` |
| DX review | `Skill(plan-devex-review)` |
| Contract drift / "CLAUDE.md 정리" / "규약 정비" / post-upgrade cleanup | `Skill(maintain)` |
| SessionStart reported `[maintain-suggested]` in reminders | Propose `Skill(maintain)` to user |
| Explanation | Direct answer |

## 7. Verification

`task_verify` syncs paths and checks verification state.
Do not claim success from static inspection when runtime verification is required.

## 8. Finish cleanly

Runtime verdict must be PASS before close.
Use `task_close`. If blocked, fix the stated gate.

## 8a. Note freshness

Notes under `doc/**/*.md` may declare source dependencies in frontmatter:

```yaml
---
freshness: current        # current | suspect | stale | superseded
invalidated_by_paths:
  - path/or/prefix/that/invalidates/this/note
  - another/source/file.py
---
```

On every SessionStart (and whenever explicitly run), the hook
`scripts/note_freshness.py` scans `git diff HEAD~1 HEAD`. If any changed path
matches a note's `invalidated_by_paths`, that note's `freshness` flips from
`current` to `suspect` and `freshness_updated` is stamped.

Writer-role agents must verify `freshness: current` before citing a note as
authoritative. `suspect` notes are still readable but require re-validation
against current source before trust. Use `--paths` arg to invalidate against
an explicit file list when git history isn't the right source.

## 8b. Acceptance Ledger (CHECKS.yaml)

CHECKS.yaml is the per-task AC ledger. Plan-skill creates each AC with
`status: open`. The develop skill promotes ACs to
`implemented_candidate` after per-AC tests pass (Phase 3), then the
verification gate (Phase 7) promotes them to `passed` — or reopens them
to `failed` (auto-incrementing `reopen_count`). Only `passed` or
`deferred` ACs satisfy the close gate.

Writes go through `scripts/update_checks.py` only. Never edit CHECKS.yaml by
hand — the prewrite gate rejects direct writes.

## 9. Iron Law (bugfix ACs)

`kind: bugfix` ACs in CHECKS.yaml cannot be promoted to `implemented_candidate`
or `passed` unless `root_cause` is set. Enforced by `update_checks.py`:

```bash
python3 scripts/update_checks.py --task-dir TASK_DIR --ac AC-001 \
  --status implemented_candidate --root-cause "off-by-one in loop bound"
```

Without `--root-cause`, the command exits 1 with an Iron Law violation message.
Once set, `root_cause` persists across subsequent transitions.

## 10. Quality scripts

All scripts under `plugin/scripts/`. Stdlib only (PIL optional for canary).

| Script | Purpose | State file (gitignored) |
|--------|---------|------------------------|
| `health.py` | Weighted composite 0–10 score | `doc/harness/health-history.jsonl` |
| `benchmark.py` | Numeric metrics vs baseline, WARN/REGR thresholds | `doc/harness/benchmark/{baseline.json,history.jsonl}` |
| `audit.py` | Generic categorized audit (CSO-style) | `doc/harness/audits/<category>-history.jsonl` |
| `canary.py` | Visual regression baseline + sha/pixel diff | `doc/harness/visual-baselines/<task-id>/` |
| `search_learnings.py` | Keyword/type/skill/since search over Tier 3 | reads `doc/harness/learnings.jsonl` |
| `write_checkpoint.py` | Mid-task resume snapshot | `doc/harness/checkpoints/<task-id>.md` |
| `inject_checkpoint.py` | SessionStart hook — surface latest checkpoint | reads `doc/harness/checkpoints/` |
| `promote_learnings.py` | Tier 3→2 promotion + stale pruning | `doc/harness/patterns/<topic>.md` |
| `retro.py` | Weekly retrospective (git + learnings + health) | `doc/harness/retros/<date>.md` |

All activated via manifest optional keys: `health_components`, `benchmark_components`,
`audit_categories`. Health falls back to `test_command` when no components declared.
Benchmark and audit are inactive until their manifest keys exist.

## 11. Tiered Learning

Every skill logs discoveries. Three tiers:

```
CLAUDE.md                    # Tier 1: loaded every session. Key facts only.
doc/harness/patterns/*.md    # Tier 2: detailed patterns. Read when relevant.
doc/harness/learnings.jsonl  # Tier 3: raw signals. Session-specific, transient.
```

**All skills write to Tier 3.** When a signal repeats 2+ times, promote to Tier 2 doc. When a Tier 2 doc is referenced in 2+ tasks, promote the key fact to Tier 1 (CLAUDE.md).

**Tier 1 entries are one-liners.** Details stay in pattern docs.

Example:
```
# Tier 3 (learnings.jsonl)
{"key":"test-command","insight":"bun test, not npm test","task":"TASK__001"}

# Tier 2 (doc/harness/patterns/testing.md)
## Test command is bun test
This project uses Bun. All test commands use `bun test`.

# Tier 1 (CLAUDE.md)
## Testing
Test command: `bun test` (Bun runtime)
```

**When to log:** Any discovery that would save 5+ minutes in a future session.
**What to log:** Build quirks, env var requirements, ordering constraints, port numbers, framework specifics, wrong manifest fields.
**What NOT to log:** Code patterns (read from files), git history (read from git), task-specific details (in task dir).
