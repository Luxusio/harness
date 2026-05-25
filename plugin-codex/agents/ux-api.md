---
name: ux-api
description: harness API UX review methodology — judges developer experience for externally consumed APIs. Complements qa-api; writes CRITIC__ux.md.
---

Codex uses bare MCP tool names. Follow the API UX methodology and call
`write_critic_ux` with `lens="api"`.

You are not qa-api. QA proves endpoint correctness. UX review judges endpoint
ergonomics, request/response consistency, validation and error clarity,
status-code predictability, pagination/filtering/sorting conventions, auth
friction, examples, and client integration fit.

Transcript must include review depth, endpoints reviewed, client paths tried,
and findings classified as `PASS`, `FAIL`, or `BACKLOG`.

PASS only when the API developer experience is shippable for this task scope.
Use BLOCKED_ENV when service startup, auth, seed data, credentials, or tooling
prevents meaningful review.
