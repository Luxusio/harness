# harness

Repo-local universal loop runtime for AI-assisted software work.

## How the runtime works

Every user request enters the same loop:

```
request → context gathering → lane selection → execution → independent evaluation → memory sync → escalation (if needed) → close
```

The plugin is not a collection of features. It is a single operating loop whose stages are fulfilled by specialized agents.

### Loop stages

| Stage | What happens | Who |
|-------|-------------|-----|
| **1. Receive** | Capture the user request as `REQUEST.md` | harness |
| **2. Gather context** | Load manifest, registered roots, relevant notes, repo state | harness |
| **3. Select lane** | Classify intent + inspect repo state → pick the right lane | harness |
| **4. Plan / Spec** | Form a contract (PLAN.md or spec hierarchy) scaled to task size | harness + critic-plan |
| **5. Execute** | Generate code, docs, or notes per the approved contract | developer, writer |
| **6. Evaluate** | Independent verification — runtime execution, not just code reading | critic-runtime, critic-document |
| **7. Sync memory** | Create/update/supersede REQ/OBS/INF notes, refresh indexes | writer |
| **8. Maintain** | Queue or run entropy control — stale notes, broken links, drift | harness |
| **9. Escalate** | Ask the user only when genuinely needed (ambiguity, destructive ops, policy) | harness |
| **10. Close** | Produce RESULT.md, update TASK_STATE.yaml | harness |

### Lanes

The runtime routes each request to one of these lanes based on intent and repo state:

| Lane | When | Loop depth |
|------|------|-----------|
| `answer` | Pure question, no repo mutation | Shallow — direct response |
| `spec` | Large/ambiguous request needing spec expansion | Deep — spec hierarchy before execution |
| `build` | Feature addition, new code | Full loop |
| `debug` | Bug investigation + fix | Full loop with repro focus |
| `verify` | Test/QA/validation request | Evaluation-heavy |
| `refactor` | Structural improvement, no behavior change | Full loop |
| `docs-sync` | Documentation update, note management | Writer + critic-document |
| `investigate` | Research, exploration, no immediate mutation | Context-heavy, may transition to another lane |
| `maintain` | Entropy control, hygiene, cleanup | Maintenance loop |

### Durable knowledge (REQ/OBS/INF)

The runtime manages a freshness-aware truth system, not a note accumulator:

- **REQ** — explicit human requirements
- **OBS** — directly observed/verified facts (with evidence)
- **INF** — unverified AI inferences (with verify_by)

Notes track `status`, `freshness`, `confidence`, and `superseded_by` to maintain a current picture of truth rather than accumulating stale documents.

### Independent evaluation

Generators (developer, writer) produce output. Evaluators (critic-runtime, critic-document) independently verify it through execution — running tests, probing endpoints, checking persistence. A critic never passes based on "looks good."

### Approval boundaries

The runtime asks the user only when:
- Requirements are fundamentally ambiguous
- Changes are destructive or irreversible
- Product/design judgment is needed
- Cost, security, or compliance is at stake
- Source conflicts leave truth undetermined

Everything else proceeds autonomously within the approved contract.

## Install

Add this plugin to your Claude Code configuration.

## Usage

After installing, run `/harness:setup` in your project to bootstrap the durable knowledge structure and executable QA scaffolding.

Then work in plain language — the harness routes requests through the appropriate lanes automatically.

## Plugin structure

```
plugin/
  .claude-plugin/plugin.json     # plugin manifest (v3.0.0)
  CLAUDE.md                      # plugin instructions (loop rules)
  settings.json                  # default agent config
  hooks/hooks.json               # plugin hooks
  agents/                        # 6 agent definitions
    harness.md                   # orchestrator — loop controller + lane router
    developer.md                 # generator — code implementation
    writer.md                    # generator — REQ/OBS/INF notes + documentation
    critic-plan.md               # evaluator — plan/spec contract validation
    critic-runtime.md            # evaluator — runtime execution verification
    critic-document.md           # evaluator — doc/note/structure governance
  skills/                        # 3 skills
    plan/SKILL.md                # create task contract (PLAN.md or spec hierarchy)
    maintain/SKILL.md            # entropy control and doc hygiene
    setup/SKILL.md               # bootstrap target project
  scripts/                       # hook scripts
    session-context.sh           # session start context loader
    task-created-gate.sh         # task artifact gate
    subagent-stop-gate.sh        # subagent artifact gate
    task-completed-gate.sh       # task completion gate
    post-compact-sync.sh         # post-compaction maintenance
    session-end-sync.sh          # session end state sync
```

## Setup outputs

When `/harness:setup` runs in a target project, it creates:

```
CLAUDE.md                        # root entrypoint + registry
doc/common/CLAUDE.md             # always-loaded root index
doc/common/REQ__|OBS__|INF__*    # initial durable notes
.claude/harness/manifest.yaml    # initialization marker + runtime config
.claude/harness/critics/         # plan.md, runtime.md, document.md
.claude/harness/constraints/     # architecture rules + check scripts (when applicable)
.claude/settings.json            # agent configuration
scripts/harness/                 # verify.sh, smoke.sh, healthcheck.sh, reset-db.sh
```

Hook scripts are built into the plugin — no need to copy them to target projects.

## Task artifacts

Every repo-mutating task produces artifacts identified by explicit `task_id`:

| Artifact | Purpose |
|----------|---------|
| `REQUEST.md` | Original user request |
| `PLAN.md` | Contract document (or spec hierarchy for large tasks) |
| `TASK_STATE.yaml` | Machine-readable state with task_id, run_id, lane |
| `HANDOFF.md` | Developer handoff notes and blockers |
| `QA__runtime.md` | Executable verification evidence |
| `DOC_SYNC.md` | Durable note/index update record |
| `CRITIC__plan.md` | Plan evaluator verdict |
| `CRITIC__runtime.md` | Runtime evaluator verdict |
| `CRITIC__document.md` | Document evaluator verdict |
| `RESULT.md` | Task outcome summary |

## Skills

| Skill | Description |
|-------|-------------|
| `/harness:setup` | Bootstrap harness structure and executable QA scaffolding |
| `/harness:plan` | Create or refresh a task contract (PLAN.md or spec hierarchy) |
| `/harness:maintain` | Run entropy control and structure maintenance |
