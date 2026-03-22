> Historical repo-os planning document. Retained only for reference. Not the current source of truth for harness.
# repo-os Project Plan

Status: Planning / Implementation Context
Purpose: This document is the **source context** used when continuing implementation of the `repo-os` Claude Code plugin.
Priority: The direction and constraints in this document take precedence over code. If the document and implementation conflict, update the document first, or record the reason for the change as an ADR.

---

## 1. One-Line Project Definition

`repo-os` is a **Claude Code plugin that transforms a repository into an operating system where AI can work — with just one setup command**.

After setup, users do not need to memorize any plugin commands. They simply make requests as they normally would.

Examples:
- Fix the login bug
- Clean up the payment domain docs
- Write more tests for this part
- Refactor the order module
- From now on, always ask me before changing auth

Claude Code then internally:
1. Classifies the intent
2. Loads the relevant context
3. Assesses risk
4. Asks the user for confirmation when necessary
5. Performs code / documentation / test work
6. Validates the result
7. Reflects the outcome in project knowledge

The key insight is **not that the model gets smarter, but that the repository accumulates an increasingly learned state**.

---

## 2. Problem Definition

Most problems in current AI development workflows fall into the following categories.

- Users must memorize commands and workflows every time.
- AI cannot persistently structure previous decisions and constraints.
- Bug fixes complete without a validation loop, causing regressions.
- In brownfield projects, insufficient context makes dangerous modifications easy.
- Documentation and code drift apart, so knowledge is lost between sessions.
- Knowledge that AI cannot see effectively does not exist.

`repo-os` solves these problems in the following ways.

- A single setup installs a repo-local control plane.
- Subsequent natural-language requests are automatically routed to internal workflows.
- User constraints and facts discovered during work accumulate as in-repo memory.
- All changes pass through a validation loop and a documentation sync loop.
- In brownfield projects, the system first understands the codebase, installs safeguards, then proceeds with work.

---

## 3. Product Vision

### North Star
Create an environment where Claude Code safely advances development based on repository context and prior decisions — the user only needs to say "what they want."

### Expected Benefits
- Users do not memorize workflows.
- Claude Code accumulates project-specific knowledge.
- Feature development, bug fixing, test reinforcement, refactoring, and documentation all operate within the same runtime system.
- Dangerous modifications are automatically halted and confirmation is requested.
- The same questions are repeated less frequently as sessions progress.

### Vision Statement
> One setup, plain-language development, repo-local memory, evidence-based validation.

---

## 4. Core Product Principles

### 4.1 Only One Public Command
The only public surface is:

```text
/repo-os:setup
```

All subsequent workflows must execute automatically within the internal skill / agent / hook layer.

### 4.2 Repo Memory, Not Session Memory
Conversations may disappear, but memory persisted in the repository must be usable in future sessions.

### 4.3 Only Evidence-Backed Memory
Memory does not mean storing anything and everything.

Criteria for good memory:
- The user stated it explicitly
- Verified by code / test / log / browser
- Will matter in future work
- Dangerous if remembered incorrectly

### 4.4 Push Constraints to Executable Form Before Documentation
Important rules are enforced in the following priority order whenever possible:
1. Tests
2. Architecture / static analysis rules
3. Configuration values / guards
4. Documentation

### 4.5 Every Task Must Close a Validation Loop
What matters more than the fact that code was written is **the fact that the change was validated**.

### 4.6 Brownfield: Understand First, Then Protect
In existing projects, do not immediately make many changes. First understand the structure, protect critical flows, make unknowns explicit, and then proceed with work.

### 4.7 Architecture as Constraint, Not Explanation
Layer boundaries and dependency direction are not maintained by documentation alone. They must be enforced by inspection rules.

---

## 5. User Experience

## 5.1 First-Time Experience
The user loads the plugin and runs setup exactly once.

```text
/repo-os:setup
```

Setup reads and reasons about the repo on its own as much as possible, then asks only the questions that are strictly necessary.

Example questions:
- What is the primary form of this project?
- What are the build / test / dev commands?
- What are the 1–3 most important user journeys?
- Are there any areas that must never be touched?
- Which types of changes must always be confirmed first?

### 5.2 Post-Setup Experience
After that, the user speaks naturally.

Examples:
- "Fix the order cancellation flow"
- "Clean up the flaky tests related to login"
- "Don't change the payment response format from now on"

Claude Code routes the request internally, selects the appropriate workflow, loads the necessary context, and updates knowledge after the work is done.

---

## 6. System Conceptual Model

The system is divided into three broad layers.

### 6.1 Plugin Layer
The operational components that live inside the Claude Code plugin itself.

Contents:
- default main agent: `repo-os-orchestrator`
- hidden skills
- specialized subagents
- session start / stop hooks
- setup skill

### 6.2 Repo Control Plane
The operational layer created inside the repository after setup.

Contents:
- `CLAUDE.md`
- `.claude-harness/manifest.yaml`
- `.claude-harness/router.yaml`
- `.claude-harness/policies/*.yaml`
- `.claude-harness/state/*`
- `.claude-harness/workflows/*`
- `docs/*`
- `scripts/agent/*`

This layer is what allows AI to operate consistently without guessing from scratch each time.

### 6.3 Project Evidence Layer
Evidence and execution results of actual work.

Contents:
- Code
- Tests
- Browser results
- Logs
- Metrics / traces
- Documentation
- Diffs

---

## 7. Request Processing Runtime Loop

All requests are processed by the following state machine.

```text
User Request
-> Intent Router
-> Scope / Context Loader
-> Risk Gate
-> Execute
-> Validate
-> Knowledge Sync
-> Summary / Ask for Approval if needed
```

### 7.1 Intent Router
Classifies the request into one or more of the following categories:
- Explanation / question answering
- Requirements gathering
- Feature development
- Bug fixing
- Test reinforcement
- Refactoring
- Documentation
- Policy / decision recording
- Brownfield understanding / inventory
- Cleanup

### 7.2 Scope / Context Loader
Estimates the relevant domain and file scope, and loads only the necessary memory.

Always load first:
- Global constraints
- Approval rules
- Recent relevant decisions

Load based on scope:
- Relevant domain documentation
- Observed facts linked to the relevant paths
- Related bug history
- Runbooks

### 7.3 Risk Gate
User confirmation takes priority over automatic progression in the following cases:
- Requirements interpretation is ambiguous
- External behavior is changing
- Impact on DB / API contract / auth / billing / infra
- Large deletions or large-scale moves
- Dependency upgrades
- Impact scope is unclear in brownfield

### 7.4 Execute
Runs the internal workflow appropriate for the request.

### 7.5 Validate
Performs validation appropriate for the project type.

### 7.6 Knowledge Sync
Reflects rules, facts, decisions, and runbook notes confirmed during this work into repo memory.

### 7.7 Summary
At the end, report the following briefly and clearly:
- What was changed
- What was validated
- What was confirmed
- What remains unknown
- Whether additional confirmation is needed

---

## 8. Core Workflow Structure

This plugin does not add visible commands after setup. Instead, it hides multiple workflows internally and uses them automatically.

### 8.1 feature-workflow
Implements user feature requests.

Responsibilities:
- Requirements gathering
- Loading relevant context
- Writing an implementation plan
- Code modifications
- Adding tests
- Updating documentation
- Validation

### 8.2 bugfix-workflow
Focuses on bug fixes and regression prevention.

Responsibilities:
- Identifying the reproduction path
- Collecting before-change evidence
- Applying the fix
- Confirming after-change behavior
- Adding regression tests
- Recording root cause and lessons learned

### 8.3 test-expansion
Reinforces missing tests.

Responsibilities:
- Identifying boundary conditions
- Adding regression tests
- Reviewing flakiness potential
- Cleaning up existing test structure

### 8.4 refactor-workflow
Improves structure while preserving external behavior.

Responsibilities:
- Understanding dependencies
- Confirming scope of impact
- Improving structure
- Eliminating rule violations
- Validation

### 8.5 docs-sync
Synchronizes code changes with documentation changes.

Responsibilities:
- Updating relevant docs index
- Updating domain docs
- Determining whether an ADR is needed
- Updating runbooks

### 8.6 decision-capture
Structures persistent rules or decisions arising from user conversations.

Examples:
- "No more than 2 retries"
- "Always confirm auth changes with me"
- "Order cancellation is only allowed before payment is complete"

### 8.7 brownfield-adoption
Safely installs the AI operating layer into an existing project.

Responsibilities:
- Writing an inventory
- Protecting critical flows
- Clarifying unknowns
- Marking risk zones
- Recommending minimal protective smoke / contract tests

### 8.8 validation-loop
Closes the validation loop after a change.

### 8.9 architecture-guardrails
Defines and enforces layer boundary and dependency direction rules.

---

## 9. Greenfield / Brownfield Strategy

## 9.1 Greenfield
For new projects, quickly set up the following:
- Draft product / domain documentation
- Initial architecture structure
- Core user journeys
- Basic validation / smoke scripts
- Memory policy and approval policy

### 9.2 Brownfield
For existing projects, follow this sequence:

1. Inventory
   - Estimate language, framework, structure, key packages, build / test commands

2. Protect
   - Protect critical flows and high-risk zones first

3. Encode
   - Pull tacit knowledge and findings into repo documentation

4. Constrain
   - Install minimal boundary rules and approval rules

5. Operate
   - Then enter the normal feature / bugfix / refactor workflow

The core principle of brownfield is: **things that are not known are not hidden — they are left as unknowns**.

---

## 10. Memory System Design

An important differentiator of this project is repo-local memory.

## 10.1 Memory Types

### A. Working Memory
Temporary state needed only during the current task.

Examples:
- Current scope
- Next experiment plan
- Pending questions

Example location:
- `.claude-harness/state/current-task.yaml`

### B. Confirmed Memory
Long-term rules that have been explicitly stated by the user or confirmed with user approval.

Examples:
- Always confirm auth changes
- Maximum 2 retries
- No changes to payment contract

Example locations:
- `docs/constraints/`
- `docs/decisions/`
- `.claude-harness/policies/approvals.yaml`

### C. Observed Memory
Facts confirmed by code / test / log / browser / runtime.

Examples:
- Bug root cause
- Actual dependency direction
- Flakiness root cause
- Runtime characteristics

Example locations:
- `docs/runbooks/`
- `docs/architecture/`
- `docs/brownfield/findings/`

### D. Hypothesis Memory
Things that have been estimated but not yet confirmed.

Examples:
- Estimated billing domain behavior
- Estimated cache consistency risk

Example location:
- `.claude-harness/state/unknowns.md`

## 10.2 Memory Storage Policy

### Can Be Stored Automatically
- Explicit user constraint
- Explicit approval rule
- Verified bug root cause
- Verified architecture fact
- Verified runtime fact
- Repeated project pattern

### Store After Confirmation
- Business rule interpretation
- Architecture principle
- Ownership assignment
- External contract meaning
- Breaking behavior policy

### Must Not Be Stored
- Transient chat
- Emotional comment
- Unverified guess stored as fact
- Duplicate memory
- Repo-irrelevant preference

## 10.3 Memory Promotion Stages

```text
hypothesis -> observed_fact -> confirmed -> enforced
```

Descriptions:
- hypothesis: estimated
- observed_fact: confirmed by code / test / log
- confirmed: finalized by user or explicit rule
- enforced: forced by test / rule / configuration

## 10.4 Ultimate Goal of Memory
The goal is not to accumulate memory. The goal is to make the next task proceed with fewer questions, higher consistency, and greater safety.

---

## 11. Approval Policy Basic Principles

Can proceed automatically:
- Documentation cleanup
- Adding tests
- Internal refactoring with a clearly defined scope
- Behavior-preserving structural cleanup
- Verifiable bug fixes

Always requires confirmation:
- External API contract changes
- DB migration / schema changes
- Authentication / authorization logic changes
- Billing / payment policy changes
- Dependency upgrades
- Large-scale deletions / moves
- CI/CD / infra / deployment changes
- Modifications with large risk scope in brownfield

---

## 12. Validation Strategy

Validation is expanded progressively, starting from the lowest-cost checks.

1. Fast static checks
   - Format
   - Lint
   - Typecheck

2. Scope-based checks
   - Relevant unit tests
   - Relevant integration tests

3. Smoke / critical flow checks
   - Core user journeys
   - Contract-level replay

4. Runtime evidence
   - Browser interaction
   - Logs
   - Metrics / traces

5. Documentation / constraint sync checks
   - Whether public behavior changes are documented
   - Whether decisions / constraints / runbooks are updated

### Strategy by Project Type

#### Web app
- Before / after snapshot
- UI journey smoke
- Console error check

#### API service
- Request / response replay
- Contract test
- Log / metric / trace check

#### Worker / batch
- Fixture replay
- Retry / idempotency path check

#### Library / SDK
- Public API example validation
- Snapshot / examples execution

---

## 13. Setup Command Design

Public command:

```text
/repo-os:setup
```

### Setup Goals
- Create the repo-local operating layer
- Determine greenfield vs. brownfield
- Capture project characteristics with minimal questions
- Install memory policy / approval policy / workflow stubs
- Generate documentation structure and validation scripts

### Setup Detailed Procedure

1. Check for existing setup
   - If `.claude-harness/manifest.yaml` already exists, confirm whether to repair / upgrade / re-run

2. Detect project form
   - Greenfield vs. brownfield
   - Estimate web / api / worker / library / monorepo
   - Estimate language / framework / package manager
   - Estimate build / test / dev commands

3. Minimal questions
   - Ask only about information the repo cannot provide on its own

4. Create control plane
   - `CLAUDE.md`
   - `.claude-harness/*`
   - `docs/*`
   - `scripts/agent/*`

5. Additional brownfield processing
   - `docs/brownfield/inventory.md`
   - `docs/brownfield/findings.md`
   - Initial unknowns

6. Memory bootstrap
   - Explicit constraints
   - Approval rules
   - Initial key journeys
   - Inferred commands
   - Initial risk zones

7. Completion summary
   - Created / updated files
   - Distinction between inferred vs. confirmed
   - Remaining unknowns

---

## 14. Repository Structure to Be Created

```text
CLAUDE.md

.claude-harness/
  manifest.yaml
  router.yaml
  policies/
    approvals.yaml
    memory-policy.yaml
  state/
    current-task.yaml
    recent-decisions.md
    unknowns.md
  workflows/
    feature.md
    bugfix.md
    tests.md
    refactor.md
    brownfield-adoption.md

.docs/  # optional internal structured memory index if needed

docs/
  index.md
  constraints/
    project-constraints.md
  decisions/
    ADR-0001-repo-os-bootstrap.md
  domains/
    README.md
  architecture/
  runbooks/
    development.md
  brownfield/
    inventory.md
    findings.md

scripts/
  agent/
    validate.sh
    smoke.sh
    arch-check.sh
```

Note: The actual directory structure may be adjusted based on the Claude Code plugin spec and the current repo structure. However, the role separation represented by the structure above is maintained.

---

## 15. Plugin Internal Architecture Goals

The plugin itself requires the following.

### 15.1 Main Agent
`repo-os-orchestrator`

Responsibilities:
- First-pass interpretation of natural-language requests
- Scope / risk assessment
- Selection of appropriate subagent / skill
- Integration of final results

### 15.2 Specialized Subagents
Recommended candidates:
- requirements agent
- brownfield mapper
- implementation agent
- test agent
- refactor agent
- docs agent
- browser validation agent

### 15.3 Hidden Skills
- feature-workflow
- bugfix-workflow
- test-expansion
- refactor-workflow
- brownfield-adoption
- repo-memory-policy
- decision-capture
- docs-sync
- validation-loop
- architecture-guardrails

### 15.4 Hooks
- SessionStart: Load repo-local memory and recent decisions
- Stop: Check whether validation is missing for this task and whether memory reflection is needed

---

## 16. Honest Notes on Current Implementation State

The scaffold currently created should be viewed as an **initial starting point**. The following items are likely not yet complete.

Things that may be lacking:
- Official Claude Code plugin structure files
- Plugin manifest / settings
- Actual implementation of hidden skills
- Actual agent / hook files
- Template files referenced by setup
- Actual code or documentation for routing / memory / validation stubs
- Verification against plugin validate criteria

In other words, the current state is "direction and basic scaffold." The next tasks should prioritize **completing the official plugin structure** and **implementing templates + agents + hooks + hidden workflows**.

---

## 17. Implementation Roadmap

## Phase 0. Planning / Alignment
Goal:
- Confirm product direction, single setup command, memory policy, and workflow structure

Completion criteria:
- This document is usable as source context

## Phase 1. Complete Official Plugin Skeleton
Goal:
- Complete file structure conforming to Claude Code plugin spec
- `/repo-os:setup` is operational

Required work:
- Finalize plugin manifest / settings
- Add `repo-os-orchestrator` agent
- Add template directory connected to setup skill
- Create basic hidden skills files
- Add hook stubs

Completion criteria:
- Claude Code can load the plugin normally
- `/repo-os:setup` can be executed

## Phase 2. Complete Setup Outputs
Goal:
- Automate repo-local control plane creation

Required work:
- Enrich templates
- Improve build / test / dev command inference logic
- Implement greenfield / brownfield branching
- Generate approvals / memory-policy / docs index

Completion criteria:
- Valid basic structure is created in both empty repos and existing repos

## Phase 3. Automatic Routing and Memory Operation
Goal:
- Automatically route natural-language requests to workflows after setup
- Activate the memory reflection loop

Required work:
- Refine orchestrator prompt
- Write intent routing rules
- Finalize memory extraction / storage rules
- Add docs sync rules

Completion criteria:
- A general natural-language request executes the appropriate workflow among feature / bugfix / docs / tests / refactor

## Phase 4. Strengthen Validation Loop and Brownfield Support
Goal:
- Safe modifications and regression prevention

Required work:
- Strengthen validation-loop
- Finalize browser / runtime evidence strategy
- Improve brownfield inventory / findings templates
- Strengthen architecture guardrails

Completion criteria:
- Even in brownfield, unknowns / risks / evidence are explicit and work proceeds safely

## Phase 5. Polish / Validation / Release Preparation
Goal:
- Stabilize plugin quality

Required work:
- Document test scenarios
- Apply to sample repo
- Plugin validate
- Clean up README / examples / install docs

Completion criteria:
- Another developer can install and use it immediately

---

## 18. Definition of Done

This project has achieved its core objectives when the following are satisfied:

1. Users use only one public command: `/repo-os:setup`.
2. After setup, development work is possible using only natural-language requests.
3. The plugin accumulates user constraints and verified findings as repo-local memory.
4. Dangerous changes automatically require confirmation.
5. Feature development / bug fixing / test reinforcement / refactoring / documentation all exist within a common operating loop.
6. In brownfield, unknowns and risks are made explicit.
7. Important rules are enforced not only through documentation but also through tests / rules / configuration.
8. Validation and documentation sync are not omitted after work.

---

## 19. Risks and Mitigations

### Risk 1. Too much documentation causes degraded retrieval quality
Mitigations:
- Scope-first retrieval
- Deduplication
- Separate unknowns from confirmed facts
- Maintain concise docs

### Risk 2. AI stores estimates as facts
Mitigations:
- Separate hypothesis / observed / confirmed / enforced stages
- High-risk policies must be stored only after confirmation

### Risk 3. Dangerous automatic modifications in brownfield
Mitigations:
- Strictly follow inventory -> protect -> encode -> constrain -> operate sequence
- Set conservative defaults for risk zone approval policies

### Risk 4. Plugin accumulates too many public commands
Mitigations:
- Keep everything except setup as hidden workflows
- Users use only natural language

### Risk 5. Validation cost becomes too high
Mitigations:
- Expand progressively: fast checks -> scope-based checks -> core smoke -> runtime evidence

---

## 20. Immediate Priorities for the Next Claude Code Work Session

Work to prioritize in the next implementation session, using this document as context:

1. **Extend current scaffold to an official Claude Code plugin structure**
   - Add plugin manifest / settings / agents / hooks / hidden skills structure
   - Verify against the latest official spec at the time of implementation

2. **Create all templates referenced by the setup skill**
   - `CLAUDE.md`
   - `.claude-harness/*`
   - `docs/*`
   - `scripts/agent/*`

3. **Write the `repo-os-orchestrator` main agent**
   - Write instructions that branch plain-language requests into internal workflows

4. **Write actual files for hidden workflows**
   - feature
   - bugfix
   - tests
   - refactor
   - brownfield-adoption
   - decision-capture
   - docs-sync
   - validation-loop
   - architecture-guardrails

5. **Push memory policy and approvals to working default values**
   - Make setup outputs and orchestrator use them together

6. **Validate end-to-end on example repos**
   - Greenfield sample
   - Brownfield sample

7. **Finally, finalize packaging / validate / documentation**

---

## 21. Final Summary

The essence of this project is not "a plugin for Claude Code" — it is an **AI operating system installed inside a repository**.

- There is only one public command: `/repo-os:setup`
- After setup, users use only natural language
- Workflows are automatically routed internally
- User decisions and verified findings accumulate as repo-local memory
- Important rules are enforced through tests / rules / configuration / documentation
- In brownfield, the system understands and protects before it acts
- Every task passes through a validation and documentation sync loop

Maintaining this direction allows `repo-os` to become not just a prompt pack, but a **repo-aware development system** that progressively develops deeper understanding of the project over time.
