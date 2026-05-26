# GUIDE document taxonomy
tags: [guide, docs, taxonomy, durable-knowledge, status:active]
summary: Durable project knowledge uses typed documents under doc/<area>/.
updated: 2026-05-18

## Purpose

Durable project knowledge uses typed documents with this path shape:

```text
doc/<area>/<TYPE>__<name>.md
```

`area` is the domain area, bounded context, or shared scope. Use names such as
`ui`, `api`, `auth`, `billing`, `catalog`, `runtime`, `verification`, or
`common`.

## Types

`REQ__...` records product or system requirements that implementation and QA
must satisfy. Use it for observable behavior, externally consumed contracts,
and constraints where a mismatch is a defect.

`GUIDE__...` records coding, design, testing, or implementation guidance. Use
it for preferred patterns, examples, conventions, and exceptions.

`ADR__...` records an important technical decision. Include the decision,
alternatives considered, reason, consequences, and tradeoffs.

`POLICY__...` records external security, legal, data-handling, approval, or
organizational constraints that harness cannot fully enforce by itself.
Keep harness-internal execution rules in skills, agents, scripts, and tests.

`OBS__...` records observed facts.

`INF__...` records inference or hypotheses derived from observations.

## Examples

```text
doc/ui/REQ__filter-bar.md
doc/api/REQ__oauth-login.md
doc/auth/ADR__token-storage.md
doc/common/GUIDE__coding-style.md
doc/security/POLICY__pii-handling.md
doc/common/OBS__workspace-layout.md
doc/common/INF__initial-stack-assumptions.md
```

## Capture Rules

Start with a documentation impact decision, not a default-to-REQ reflex:

- `REQ needed` when the diff changes observable behavior, externally consumed
  contracts, process guarantees, or constraints where a mismatch is a defect.
- `Pattern/skill doc enough` when the durable knowledge is guidance for agents,
  contributors, implementation style, testing practice, or harness workflow.
- `No durable doc needed` when the change is internal-only, test-only,
  mechanical, or one-off maintenance and no durable knowledge surface changes.

Record the decision in PLAN/HANDOFF/DOC_SYNC with a concrete reason. The reason
should explain which durable knowledge surface is affected, or why none is.

Create or update `REQ` when a task changes observable behavior: visible screen
state, filters, search, sorting, loading, empty/error states, labels,
visibility, click/input interactions, externally consumed API request/response
shape, status codes, auth/session behavior, validation, compatibility, side
effects, or observable bugfixes.

New pages, admin/backoffice screens, routes, controllers, and endpoints require a `REQ` even when the change is additive. PLAN.md acceptance criteria do not replace durable requirements.

Create or update `GUIDE` when a task establishes a reusable coding, design,
testing, or implementation pattern.

Create or update `ADR` when a task makes a significant technical choice with
real alternatives or tradeoffs.

Create or update `POLICY` only for external operating constraints such as
security, privacy, legal, licensing, data retention, production access, or
approval rules.

For internal-only refactors, one-off tests, local tooling, or non-observable
maintenance, record `Durable docs: not needed — <reason>` when the reason says
which durable knowledge surfaces remain unchanged. "Additive change" and
"covered by PLAN.md" are not valid reasons to skip a `REQ` for observable
UI/API behavior.
