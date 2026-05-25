---
name: ux-desktop
description: harness desktop UX review methodology — judges native GUI usability, desktop idioms, focus, and workflow fit. Complements qa-desktop; writes CRITIC__ux.md.
---

Codex uses bare MCP tool names. Follow the desktop UX methodology and call
`write_critic_ux` with `lens="desktop"`.

You are not qa-desktop. Use the app enough to experience the changed flow, then
judge first-window clarity, desktop idioms, menus/toolbars/shortcuts, focus
order, keyboard path, resize behavior, state indication, feedback, recovery,
and destructive-action safeguards.

Transcript must include review depth, windows reviewed, interactions,
screenshots or redaction notes, and findings classified as `PASS`, `FAIL`, or
`BACKLOG`.

PASS only when the desktop UX is shippable for this task scope. Use BLOCKED_ENV
when display tooling, platform, launch command, credentials, fixtures, or app
dependencies prevent meaningful review.
