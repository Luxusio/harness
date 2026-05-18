# REQ process product-requirement-capture
tags: [req, process, product-intent, ddd, status:active]
summary: Observable UI/API behavior requirements use the existing REQ format under doc/<area>/.
updated: 2026-05-18

## Requirement

Durable product intent uses the existing `REQ__...md` document format. Keep
screen behavior and API contracts in that same requirement document family.

Write observable product requirements under the area or bounded-context folder:

```text
doc/<area>/REQ__<name>.md
```

Examples:

```text
doc/ui/REQ__filter-bar.md
doc/api/REQ__oauth-login.md
doc/auth/REQ__session-policy.md
doc/common/REQ__project__primary-goals.md
```

Use `doc/common/` for cross-cutting or repository-wide requirements. Use a
domain area such as `doc/auth/`, `doc/billing/`, or `doc/catalog/` when that
better matches the product language.

## Capture Triggers

Create or update a REQ document when a task changes observable behavior:

- visible screen state, filters, search, sorting, loading, empty/error states,
  labels, visibility, or click/input interactions
- externally consumed API request or response shape, status codes,
  auth/session behavior, validation, compatibility, or side effects
- observable bugfixes, because visible behavior changes from wrong to intended

Internal-only refactors, tests, harness tooling, and non-observable maintenance
can record `Requirement docs: not needed — <reason>` when the reason states
which observable surfaces remain unchanged.

## Document Shape

Keep REQ documents readable. State the intended observable behavior first, then
include verification cues that QA or a future bug-fix agent can apply without
inferring intent from implementation details.
