# harness2 runtime rules

Lightweight execution harness for Claude Code.
8-field TASK_STATE + on-the-fly routing + artifact-provenance.
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

## 3. TASK_STATE (6 fields only)

```yaml
task_id: TASK__xxx
status: created|planning|implementing|verifying|closed
runtime_verdict: pending|PASS|FAIL
touched_paths: []
plan_session_state: closed|open
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
| source + HANDOFF.md + DOC_SYNC.md + distilled change doc | developer |
| CRITIC__runtime.md | qa-browser / qa-api / qa-cli |

Do not write another role's artifact. Prewrite gate enforces this.

## 7. Auto-routing

| Intent | Route to |
|--------|----------|
| Set up harness | `Skill(setup)` |
| New feature / build something | `Skill(harness:plan)` |
| Run full cycle | `Skill(harness:run)` |
| CEO review | `Skill(plan-ceo-review)` |
| Architecture review | `Skill(plan-eng-review)` |
| Design review | `Skill(plan-design-review)` |
| DX review | `Skill(plan-devex-review)` |
| Explanation | Direct answer |

## 8. Verification

`task_verify` syncs paths and checks verification state.
Do not claim success from static inspection when runtime verification is required.

## 9. Finish cleanly

Runtime verdict must be PASS before close.
Use `task_close`. If blocked, fix the stated gate.

## 10. Tiered Learning

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
