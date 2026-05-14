---
date: 2026-05-14
task: TASK__codex-agents-and-gates-finalize
type: feature
---

# Codex plugin development complete

The Codex tree under plugin-codex/ now mirrors the harness Claude tree at functional completeness: 9 user-facing skills (setup, run, plan, develop, maintain, plan-{ceo,eng,design,devex}-review) and 7 role methodology references (stop-judge, qa-cli, qa-api, qa-browser, qa-desktop, dogfooder, developer) hand-authored under the MCP-only-sharing architecture (spike-report §3.6). Codex users can now run the full canonical loop end-to-end with the same MCP server, hook payload schemas, gate scripts, and contract artifacts as Claude side. The only runtime gaps that remain are deferred-to-v2 items with concrete root cause: parallel sub-agent fanout (needs Codex multi_agent ergonomics), browser MCP verification (needs Codex Playwright MCP), dual-voice plan-* reviews (needs Agent primitive), AskUserQuestion structured tool (currently prose-rendered), prewrite gate role-detection (currently bypassed with HARNESS_SKIP_PREWRITE), and gate-script runtime-aware prose (stop_gate.py + qa_delegation_gate.py emit Claude-specific Agent spawn syntax). All deferrals are documented in plugin-codex/README.md.
