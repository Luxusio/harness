# REQ source anthropic-harness-design
tags: [req, root:common, source:explicit, status:active]
summary: Foundational requirements — Anthropic "Harness design for long-running application development" (2026-03-24)
source: https://www.anthropic.com/engineering/harness-design-long-running-apps
author: Prithvi Rajasekaran (Anthropic Labs)
updated: 2026-03-30

## Background — Why Naive Implementations Fail

Two core failure modes:

1. **Context anxiety** — As the context window fills, the model begins wrapping up work prematurely. Compaction alone is insufficient to resolve this.
2. **Self-evaluation bias** — Agents tend to over-praise their own output, even when quality is obviously mediocre.

## Core Architecture Requirements

### 1. Context Reset (over compaction)
- When context overflows, **replace with a fresh agent entirely** (do not compact and continue the same agent)
- A **structured handoff artifact** carrying previous agent state + next steps is mandatory
- From Opus 4.6 onward, context anxiety is reduced enough to run without resets — re-evaluate per model version

### 2. Generator / Evaluator Separation (GAN-inspired)
- **Generator** and **Evaluator** must always be separate agents
- The evaluator can be independently tuned to be skeptical (few-shot calibration + grading criteria)
- Having the generator evaluate its own work is fundamentally ineffective

### 3. Three-Agent Structure: Planner → Generator → Evaluator

**Planner**
- Expands a 1–4 sentence prompt into a full product spec
- Stays high-level — no premature technical implementation details (prevents error cascades)
- Weaves AI feature opportunities into the spec
- Output: a deliverables list for downstream agents to follow

**Generator**
- Works in sprints — one feature at a time
- Self-evaluates at the end of each sprint before handing off to QA
- Git version control required
- After evaluator feedback, makes a strategic decision: refine current direction or pivot entirely
- Example stack: React + Vite + FastAPI + SQLite / PostgreSQL

**Evaluator**
- Navigates the live browser like a real user using Playwright MCP
- Negotiates a **Sprint Contract** before each sprint (agrees on "done" criteria before any code is written)
- **Hard threshold per criterion** — any criterion below threshold = sprint FAIL, rework required
- Logs must be specific enough to act on (reproduction path, failure condition explicitly stated)

## Evaluation Criteria

### Frontend Design
| Criterion | Description | Priority |
|-----------|-------------|----------|
| Design quality | Coherent whole — colors, typography, layout, imagery combine into a distinct mood | High |
| Originality | Evidence of deliberate creative choices; no template defaults or library boilerplate | High |
| Craft | Typography hierarchy, spacing consistency, color contrast — technical execution | Medium |
| Functionality | Users can understand the interface, find primary actions, complete tasks | Medium |

> AI slop patterns (purple gradients over white cards, etc.) are explicitly penalized.

### Full-Stack Development
- Product depth, Functionality, Visual design, Code quality

## Inter-Agent Communication
- **File-based** — one agent writes a file; the other reads it and responds via a new file
- Files serve as shared state instead of direct message passing

## Harness Design Principles

1. **Simplicity first** — "Find the simplest solution possible, and only increase complexity when needed" (Anthropic Building Effective Agents)
2. **Each component encodes a model capability assumption** — stress-test those assumptions regularly; they go stale as models improve
3. **Re-examine harness with every new model** — strip out components that are no longer load-bearing, add new ones for newly achievable capability
4. **Task decomposition** — break complex work into tractable chunks
5. **Structured handoffs** — design artifacts that carry enough state for the next agent to continue cleanly
6. **Separate evaluator tuning** — iterate on evaluator prompts by reading logs until its judgment aligns with human standards

## Experimental Results

| Condition | Duration | Cost | Core Quality |
|-----------|----------|------|--------------|
| Solo agent (game maker) | 20 min | $9 | Core gameplay broken |
| Full harness (game maker) | 6 hr | $200 | Core gameplay works; 16-feature spec delivered |
| Full harness v2 — DAW (sprints removed) | 3 hr 50 min | $124 | Core features work; AI agent integration included |

- Cost is 20x+ higher, but quality difference is unambiguous
- Sprint construct removable on Opus 4.6 → harness simplification
- Evaluator adds most value when the task sits at the edge of the generator's solo capability

## Future Direction
- As models improve, less scaffolding is needed — enabling more complex tasks
- The space of interesting harness combinations does not shrink; it moves
- The AI engineer's job: find the next novel combination given the current model's capability frontier
