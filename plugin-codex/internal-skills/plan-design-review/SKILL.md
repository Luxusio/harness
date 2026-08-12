---
name: plan-design-review
user-invocable: false
description: Review and improve a UI/UX plan before implementation.
---

> **Codex runtime delta:** Ask questions as plain conversational prose with
> lettered options and wait for the next turn; Codex has no structured
> `AskUserQuestion` tool. Use `apply_patch` for approved plan edits. No outside
> voice or subagent workflow is part of this compressed skill.

# Design plan review

Review and edit the plan only. Do not implement product code. Apply the shared
plan rules in `${HARNESS_PLUGIN_ROOT}/internal-skills/plan/SKILL.md`, including
search-before-building, repo ownership, context recovery, and conversational asks.

## UI scope detection and setup

1. Read the plan, `DESIGN.md` when present, relevant UI source, recent commits
   touching those surfaces, and up to five relevant entries from
   `doc/harness/learnings.jsonl`.
2. Inventory reusable design tokens, components, layout patterns, and flows.
3. Treat the review as not applicable only when the plan has no user-visible
   screen, page, component, content, or interaction change. Report
   `skipped, no UI scope` and stop.
4. Otherwise emit this `Review Readiness Dashboard`:

```text
| Item | Status |
| PLAN.md | present/missing |
| UI scope | yes/no |
| DESIGN.md | present/missing |
| Reusable component inventory | present/missing |
| Prior learnings | N loaded |
```

## Step 0: Design scope assessment

Rate initial design completeness 0-10. State what a 10 means for this plan,
which existing patterns it should reuse, and the three biggest gaps. Ask whether
the user wants a narrower focus; stop for the answer.

### Step 0.5: Visual mockups

Unless the user requests text-only review, draw an ASCII/Markdown wireframe for
every key screen or component, including navigation, hierarchy, primary actions,
interaction flow, and responsive changes. Store any approved persistent mockup
under `docs/designs/`. Show the wireframes and ask for corrections before scoring.

## Design rubric

Use these principles throughout:

- Hierarchy must make the first, second, and third priorities obvious.
- Reuse conventions and existing components; every element must earn its pixels.
- Specify concrete tokens, type, spacing, copy, and behavior instead of
  "clean/modern" language.
- Cover loading, empty, error, success, partial, boundary, and recovery states.
- Design for scanning, clear affordances, and an uninterrupted user journey.
- Responsive behavior is intentional per viewport, not merely stacked.
- Require keyboard access, landmarks/labels, visible focus, 44px touch targets,
  body contrast of at least 4.5:1, and readable text.
- Empty states need context and a useful next action.
- Preserve user trust around destructive actions, identity, privacy, and errors.

### Design Hard Rules

Classify each surface before applying visual rules:

- Marketing: first viewport is one brand-first composition with one visual
  anchor, concise copy, clear CTA, and purposeful motion.
- App UI: calm surface hierarchy, dense but readable workspace, minimal chrome,
  utility language, and clear primary/secondary context.
- Hybrid: apply each rule set to its corresponding surface.

Flag these generic-design failures: decorative card grids, card-based heroes,
weak brand or action, busy imagery behind text, repeated section purpose,
purposeless carousels, centered-everything layouts, uniform oversized radii,
decorative blobs/emojis, purple-gradient defaults, generic hero copy, default
font stacks, ornamental icons, or motion without hierarchy value. Cards are
valid only when the card is the interaction.

## Fix-to-10 Loop

Score all seven dimensions with an integer 0-10 and one concrete finding and
smallest fix for every score below 10:

```text
| # | Dimension | Score | Finding | Smallest fix |
| 1 | Information hierarchy | /10 | | |
| 2 | Interaction states | /10 | | |
| 3 | User journey | /10 | | |
| 4 | Responsive strategy | /10 | | |
| 5 | Accessibility | /10 | | |
| 6 | Visual specificity | /10 | | |
| 7 | Design-system alignment | /10 | | |
```

For each sub-10 dimension:

1. Identify the violated principle and smallest useful plan edit.
2. Classify it as `structural` when omission would cause a broken or confusing
   implementation, otherwise `taste`.
3. Add unambiguous structural requirements to the plan. For any meaningful
   alternative or product decision, ask first.
4. Queue taste choices for the user gate; never choose them silently.
5. Re-score until the dimension reaches 8+, or identify the exact deferred taste
   decision that prevents it.

## Seven review passes

Evaluate every pass; say `No issues found` when clear. For each non-trivial
decision, ask separately with 2-3 lettered options in prose, effort/risk,
and an opinionated recommendation. Do not batch issues or continue before the
answer.

1. **Information architecture:** content priority, page/screen structure,
   navigation and wayfinding. Add an ASCII hierarchy/flow diagram.
2. **Interaction states:** add a table covering loading, empty, error, success,
   partial, boundary, and recovery states as visible user behavior.
3. **User journey:** map `step | action | user expectation/emotion | UI support`,
   including first-use and repeated-use paths.
4. **Visual specificity:** apply the surface classifier and generic-design
   failure list; replace vague descriptions with implementable constraints.
5. **Design-system alignment:** reuse named tokens/components; justify every new
   primitive. If no design system exists, record the constraint without inventing one.
6. **Responsive and accessibility:** specify viewport changes, keyboard/screen
   reader behavior, focus order, contrast, labels, and touch targets.
7. **Unresolved decisions:** list each ambiguity and the implementation failure
   caused by deferral; resolve or explicitly defer each one.

If approved mockups and later decisions diverge, offer one regeneration pass.

## Required plan/output contracts

The reviewed plan must contain:

- `NOT in scope`, with rationale for each deferral.
- `What already exists`, naming reused patterns/components.
- Information architecture and navigation diagrams where non-trivial.
- Interaction-state and user-journey tables for each changed UI flow.
- Responsive and accessibility requirements.
- `Approved Mockups` table when mockups were approved:

```text
| Screen/section | Mockup path | Direction | Constraints |
```

Present each possible design-debt TODO individually with What, Why, Pros, Cons,
Context, dependencies, then ask: add to `TODOS.md`, skip, or include now. Never
write a vague or unapproved TODO.

Finish with:

```text
DESIGN PLAN REVIEW
System audit: ...
Initial -> final score: N/10 -> N/10
Passes 1-7: score changes and finding counts
Decisions: N resolved, N deferred
NOT in scope: written
What already exists: written
TODOs proposed: N
Approved mockups: N
Unresolved decisions: ...
```

Also emit a compact `Design Review Report` with dimensions scored, average, severity counts,
and fix paths. If every pass is 8+, declare the plan design-complete and recommend
post-implementation visual QA. Otherwise name what remains and why. Recommend
`/plan-eng-review` next; recommend `/plan-ceo-review` only for a sub-4 initial
score, structural information-architecture failure, or an unresolved product premise.

Log only genuine 5+ minute operational discoveries to
`doc/harness/learnings.jsonl`; never fabricate a learning.
