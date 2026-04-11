# harness2 runtime rules

harness2 = lightweight execution harness for Claude Code.
Canonical loop enforcement + setup skill for project bootstrapping.

## 1. Canonical Loop

Every repo-mutating task follows this sequence:

```
plan → develop → verify(intent adequacy) → document → close
```

No step may be skipped. Use the smallest coherent diff per step.

## 2. MCP-first control

For every new or resumed task, start with `task_start`.
Use `task_context` only for refresh or personalized worker view.

**9 MCP tools:**

Core (coordinator):
- `task_start` — create/resume task, compile routing
- `task_context` — refresh task state
- `task_verify` — auto-sync + run verification suite
- `task_close` — run completion gate

Subagent write (role-restricted — coordinator must NOT call these):
- `write_critic_plan` → critic-plan only
- `write_critic_runtime` → critic-runtime only
- `write_critic_document` → critic-document only
- `write_handoff` → developer only
- `write_doc_sync` → writer only

**Provenance:** Artifact writes ARE the provenance. No record_agent_run needed.

## 3. Auto-routing

When user describes intent in natural language, route accordingly:

| Intent pattern | Route to |
|---------------|----------|
| Set up harness / bootstrap / initialize | `Skill(setup)` |
| New feature / task / build something | `Skill(harness:plan)` (loop start, 7-phase dual-voice review pipeline) |
| CEO review / think bigger / scope review | `Skill(plan-ceo-review)` |
| Architecture review / engineering review | `Skill(plan-eng-review)` |
| Design review / design critique | `Skill(plan-design-review)` |
| DX review / devex review / API design review | `Skill(plan-devex-review)` |
| Explanation / lookup | Direct answer (no routing) |

**Announce routing:** `Routing to setup — bootstrapping harness2 for this project.`

## 4. Plan-first rule

Do not mutate source before PLAN.md exists and critic-plan PASS is recorded.
Short approvals only authorize the last explicit transition proposed.

## 5. Artifact ownership

- PLAN.md → plan-skill
- source files + HANDOFF.md → developer
- DOC_SYNC.md + durable notes → writer
- CRITIC__plan.md → critic-plan
- CRITIC__runtime.md → critic-runtime
- CRITIC__document.md → critic-document

## 6. Document step — distilled change docs

At the document step (after verify, before close), the writer produces:
1. `DOC_SYNC.md` — standard change tracking (task-local)
2. `doc/changes/YYYY-MM-DD-<slug>.md` — distilled change doc (committed)

The distilled doc extracts key decisions, changes, caveats, and verification
results from task artifacts. Task directories (`doc/harness/tasks/`) are
gitignored — the distilled doc is the permanent record.

## 7. Skills

Skills live in `plugin/skills/`. Currently available:
- `setup` — bootstrap harness2 in a project (interactive, AskUserQuestion-based)
- `plan-ceo-review` — CEO/founder mode plan review (scope modes: expansion/selective/hold/reduction)
- `plan-eng-review` — engineering plan review (architecture, data flow, error maps, test coverage)
- `plan-design-review` — design plan review (0-10 scoring per dimension, fix-to-10 loop)
- `plan-devex-review` — developer experience plan review (personas, friction points, DX benchmarks)

State files stored under `doc/harness/` in project root.

## 8. Verification rule

`task_verify` is the normal verification entry point.
Do not claim success from static inspection alone when runtime verification is required.

## 9. Finish cleanly

Before closing: required critics must have PASS, no pending blockers.
Use `task_close` (auto-syncs changed paths first).
If `task_close` blocks, fix the stated gate.
