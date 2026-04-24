---
task: TASK__add-qa-desktop-agent
date: 2026-04-24
freshness: current
invalidated_by_paths:
  - plugin/agents/qa-desktop.md
  - plugin/skills/run/SKILL.md
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/_lib.py
  - plugin/CLAUDE.md
  - plugin/skills/plan/write-artifacts.md
  - plugin/skills/setup/bootstrap.md
  - plugin/skills/setup/SKILL.md
---

# Add qa-desktop agent (x11-mcp desktop QA lens)

The harness now has a fourth QA agent — `qa-desktop` — that verifies native desktop GUI
applications through a user-supplied x11-mcp MCP server, alongside the existing
qa-browser (web), qa-api (HTTP API), and qa-cli (CLI/library) lenses. Projects declare
`desktop_qa_supported: true` in `doc/harness/manifest.yaml` and register an x11-mcp
server in `.mcp.json`; `Skill(harness:run)` then routes desktop ACs to the new agent
automatically. v1 is Linux-only with explicit BLOCKED_ENV gates for non-Linux hosts,
missing DISPLAY/Xvfb, absent x11-mcp tools, and interactive sudo prompts. A latent
bug in `plugin/scripts/prewrite_gate.py` was fixed in-scope: the `PROTECTED_ARTIFACTS`
owner token for `CRITIC__qa.md` moved from the stale single-agent `"qa-cli"` to the
stable enum `"qa-agent"`, and the deny-reason human text now enumerates all four QA
agents. Regression coverage lives in `tests/test_qa_desktop_gate.py` (5 new tests).
