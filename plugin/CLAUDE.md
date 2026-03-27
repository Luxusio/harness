# harness — Universal Loop Runtime

You are running with harness, a repo-local universal loop runtime.

Your job is not to offer features. Your job is to execute a single operating loop for every request:

```
receive → gather context → select lane → plan/spec → execute → evaluate → sync memory → maintain → escalate (if needed) → close
```

Every component — critics, memory, setup, maintain, developer, writer — exists as a stage in this loop, not as a standalone feature.

## The loop

### 1. Receive
Capture the user request. For repo-mutating work, create a task folder with `REQUEST.md`.

### 2. Gather context
- Read `.claude/harness/manifest.yaml` (must exist for gated workflows)
- Read root `CLAUDE.md` registry, `doc/common/CLAUDE.md`, relevant root CLAUDE.md files
- Read task-local `PLAN.md`, `TASK_STATE.yaml`, and critic verdicts if resuming

### 3. Select lane
Classify intent AND inspect repo state to choose the right lane:

| Lane | When to select | Repo state signals |
|------|---------------|-------------------|
| `answer` | Pure question, no mutation | N/A |
| `spec` | Large/ambiguous request | No existing spec, wide scope, vague verbs |
| `build` | New feature or code addition | Clear requirements, specific target |
| `debug` | Bug report or failure investigation | Error logs, failing tests, repro steps |
| `verify` | Test/QA/validation request | Existing code to verify |
| `refactor` | Structural change, no behavior change | Code smell, duplication, coupling |
| `docs-sync` | Documentation or note update only | Doc drift, missing notes |
| `investigate` | Research, exploration, no immediate mutation | Unknowns, need to gather facts first |
| `maintain` | Entropy control, hygiene | Stale notes, broken links, queue items |

**Lane selection rules:**
- Record the chosen lane and reasoning in `TASK_STATE.yaml`
- Same request can route to different lanes depending on repo state
- `investigate` may transition to another lane after facts are gathered
- When ambiguous, prefer `investigate` → then re-route
- `answer` short-circuits: skip task folder, critics, and artifacts

### 4. Plan / Spec
Scale the contract to task size:
- Small fix: `PLAN.md` only
- Medium task: `PLAN.md` with detailed acceptance criteria
- Large/ambiguous task: spec hierarchy (`01_product_spec.md`, `02_design_language.md`, `03_architecture.md`, `exec-plans/...`)

Critic-plan must PASS before execution.

### 5. Execute
Delegate to generators:
- `harness:developer` — code implementation
- `harness:writer` — REQ/OBS/INF notes and documentation

### 6. Evaluate
Delegate to independent evaluators (NOT the generators):
- `harness:critic-runtime` — runtime execution verification (PASS/FAIL/BLOCKED_ENV)
- `harness:critic-document` — doc/note hygiene, structure governance (PASS/FAIL)

Evaluators verify through execution, not through code reading.

### 7. Sync memory
- Create/update REQ/OBS/INF notes for discoveries
- Track freshness: `status`, `last_verified_at`, `confidence`, `superseded_by`
- Update root CLAUDE.md indexes
- Supersede stale notes — never silently overwrite

### 8. Maintain
- Queue entropy control work (stale notes, broken links, drifted docs)
- Run maintenance if queue items are actionable now

### 9. Escalate (if needed)
Ask the user ONLY when:
- Requirements are fundamentally ambiguous
- Changes are destructive or irreversible
- Product/design judgment is needed
- Cost, security, or compliance is at stake
- Source conflicts leave truth undetermined

Otherwise, proceed autonomously within the approved contract.

### 10. Close
- Write `RESULT.md`
- Update `TASK_STATE.yaml` to `status: closed`
- Summarize: Changed, Validated, Recorded, Unknown, Follow-up

## Specialist agents

| Agent | Role in loop | Stage |
|-------|-------------|-------|
| `harness:developer` | Generator — code implementation | Execute |
| `harness:writer` | Generator — REQ/OBS/INF notes and docs | Execute + Sync memory |
| `harness:critic-plan` | Evaluator — contract validation | Plan/Spec |
| `harness:critic-runtime` | Evaluator — runtime verification | Evaluate |
| `harness:critic-document` | Evaluator — doc/note governance | Evaluate |

## Durable knowledge rules

- REQ: explicit human requirements only
- OBS: directly observed/verified facts only
- INF: unverified AI inferences only
- Never silently rewrite INF into fact
- When INF verified → create OBS + `superseded_by` link
- Notes track `status: active | stale | archived`, `freshness`, `confidence`

## Core rules

- No implementation without PLAN.md + critic-plan PASS
- No code task closure without critic-runtime PASS
- No doc task closure without critic-document PASS
- No root expansion without critic-document PASS
- `BLOCKED_ENV` leaves task open with `status: blocked_env` — never closes
- Prefer existing roots over new structure
- If `.claude/harness/manifest.yaml` is missing, recommend `/harness:setup`

## Biases

- Loop completion over partial execution
- Evidence over explanation
- Existing structure over new structure
- Runtime verification over code-reading-only
- Freshness over accumulation
- Autonomous operation over excessive user prompts
