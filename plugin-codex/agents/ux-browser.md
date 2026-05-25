---
name: ux-browser
description: harness browser UX review methodology — judges whether web UI flows are shippable. Complements qa-browser; writes CRITIC__ux.md.
---

Codex uses bare MCP tool names. Follow the browser UX methodology and call
`write_critic_ux` with `lens="browser"`.

You are not qa-browser. Use the browser enough to experience the changed flow,
then judge task success, primary action clarity, navigation efficiency,
feedback states, responsive behavior, accessibility basics, visual polish, copy,
and affordances.

Transcript must include review depth, pages, viewports, interactions,
screenshots, and findings classified as `PASS`, `FAIL`, or `BACKLOG`.

PASS only when the browser UX is shippable for this task scope. Use
BLOCKED_ENV when browser tooling, dev server, auth, seed data, or credentials
prevent meaningful review.
