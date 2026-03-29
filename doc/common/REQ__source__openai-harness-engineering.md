# REQ source openai-harness-engineering
tags: [req, root:common, source:explicit, status:active]
summary: Foundational requirements — OpenAI "Harness engineering: leveraging Codex in an agent-first world" (2026-02-11)
source: https://openai.com/index/harness-engineering/
author: Ryan Lopopolo (OpenAI)
updated: 2026-03-30

## Core Message

Improving agent productivity is far more about **designing the working environment clearly** (repo structure, documentation, rules, feedback loops) than asking the model to try harder.

## Role Separation Requirements

| Role | Responsibility |
|------|---------------|
| Human | Prioritization, goal decomposition, environment/rule/feedback-loop design, exception handling |
| Agent | Implementation, testing, fixes, review responses, build failure recovery |

## Repository Design Requirements

### Documentation Structure
- **Short entry document** — provides the full knowledge map (no single giant instruction file)
- **Detailed docs under `docs/`** — design rationale, rules, domain knowledge organized systematically
- Information that lives only in external docs, chat, or tacit knowledge must be moved into the repo
  - Agents can only use information that is visible inside the repository

### Documentation Quality
- CI + linters automatically verify document freshness, cross-links, and structural consistency
- Reference hierarchy of small files instead of one long CLAUDE.md

### Codebase Readability (Agent Perspective)
- Not just "clean code" for humans — the structure must let an **agent infer the business domain by reading the repo alone**
- Markdown, schemas, plans, and tests are the medium for domain knowledge

## Architecture Rule Requirements

- Dependency direction and layer structure must be **mechanically enforced** (documentation alone cannot maintain consistency)
- Automate format constraints: logging format, type/schema naming conventions, file size limits
- Define **boundaries and invariants** strictly rather than over-specifying implementation details
  - Agents move fast without breaking the underlying structure

## Observability Requirements

- Agents need an environment where they can **directly verify, reproduce, and validate UI behavior**
  - Per-worktree app instances + browser control (snapshots, screenshots, navigation)
- **Logs, metrics, and traces connected to a local observability stack**
  - Agents can directly query, correlate, and act on performance conditions and error signals

## Merge Philosophy Requirements

- In an environment where agents generate PRs at high throughput:
  - Gating all changes behind human review becomes a bottleneck
  - **Fast iteration + follow-up fixes** is the realistic strategy
- Low fix cost, high wait cost → merge on shorter cycles

## Entropy Management Requirements

- As full autonomy increases, mediocre existing patterns get replicated too
- Manual "clean up AI output" does not scale
- Requirements:
  1. **Promote good principles into repo rules** (documentation alone is not enough)
  2. **Regular deviation detection + refactoring PRs** as a background job
  3. Continuous rule codification cycle

## Autonomy Pipeline (Target State)

A single prompt should drive the following flow automatically:

```
Check current state
→ Reproduce bug + record failure
→ Apply fix
→ Re-validate
→ Open PR
→ Respond to review feedback
→ Recover from build failures
→ Escalate to human (only when necessary)
→ Merge
```

> This level of autonomy is only achievable on top of repo structure, rules, and observability investment.

## Open Questions

- Can structural consistency be maintained over a multi-year timescale?
- Where exactly does human judgment provide the most leverage?
- How does this system evolve as models become more capable?

## Core Premise

> The key competitive advantage going forward will be closer to
> **environment design, feedback loops, and control systems**
> than to writing code itself.

---

## Image Diagram Descriptions

### Image 1 — Codex validates the app via Chrome DevTools MCP

Sequence diagram: **CODEX → APP → CHROME DEVTOOLS**

```
1. Select target + clear console      (CODEX → CHROME DEVTOOLS)
2. Snapshot BEFORE                    (CODEX → CHROME DEVTOOLS)
3. Trigger UI path                    (CODEX → CHROME DEVTOOLS)
4. Runtime events during interaction  (APP   → CHROME DEVTOOLS)
5. Snapshot AFTER                     (CODEX → CHROME DEVTOOLS)
6. Apply fix + restart                (CODEX → CHROME DEVTOOLS)
─── LOOP UNTIL CLEAN ─────────────────────────────────────────
7. Re-run validation                  (CODEX → CHROME DEVTOOLS)
```

- Agent controls the browser directly, taking before/after snapshots of UI state
- After applying a fix and restarting, repeats until CLEAN

---

### Image 2 — Full observability stack for Codex

```
APP
 ├─ LOGS (HTTP)
 ├─ OTLP METRICS   →  VECTOR  ──fan out (local)──▶ Observability stack services
 └─ OTLP TRACES                                       ├─ Victoria Logs    → LogQL API    ─┐
                                                      ├─ Victoria Metrics → PromQL API   ├─▶ CODEX
                                                      └─ Victoria Traces  → TraceQL API  ─┘
                                                                            (Query / Correlate / Reason)
                                                                                    │
                                                                        Implement change (PR)
                                                                                    │
                                                                               CODEBASE
                                                                                    │
                                                          Re-run workload ◀── Test ◀── UI Journey ◀─┘
```

- App logs, metrics, and traces collected by Vector → Victoria stack (Logs, Metrics, Traces)
- Codex queries via LogQL, PromQL, TraceQL APIs to query, correlate, and reason
- Open PR → restart app → re-run workload → UI Journey test → feedback loop

---

### Image 3 — The limits of agent knowledge: what Codex can't see doesn't exist

```
                  ┌──────────────────────────┐
                  │     CODEX'S KNOWLEDGE     │◀── Encode into codebase as markdown
                  └──────────────────────────┘
                                ▲
                   (these are unseen knowledge to Codex)
         ┌──────────────────────┼──────────────────────┐
         │                      │                      │
    Google doc             Slack message          Tacit knowledge
 "This document outlines   "We will follow        "Ryan is responsible
 our approach to feature   @PaulM's guidance      for the overall
 prioritization and        on security posture    architectural direction
 planning."                from now on."          of the system."
```

- Google docs, Slack messages, and tacit knowledge are **invisible to Codex = effectively non-existent**
- Must be encoded into the repo as markdown to be usable by the agent

---

### Image 4 — Layered domain architecture with explicit cross-cutting boundaries

```
        Utils
          │
          ▼
┌───────────────────────────────────────┐
│         Business logic domain         │
│                                       │
│  Providers ────────▶ App Wiring + UI  │
│     │                      ▲          │
│     ▼                      │          │
│  Service ──▶ Runtime ──────┘          │
│     ▲              └──▶ UI            │
│     │                                 │
│  Types ──▶ Config ──▶ Repo            │
└───────────────────────────────────────┘
```

- **Dependency direction**: Utils → Providers → Service → Types (strictly downward, one-way)
- **Internal layers of the Business logic domain**:
  - `Types` / `Config` / `Repo` — bottom layer (data and configuration)
  - `Service` — business logic
  - `Runtime` — execution layer
  - `App Wiring + UI` / `UI` — top layer (presentation)
- `Providers` initializes `App Wiring + UI`; `Runtime` results surface up to `App Wiring + UI`
- Cross-cutting entry points are explicitly restricted → prevents agents from violating dependency direction
