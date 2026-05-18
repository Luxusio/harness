# Codex Browser QA Availability Gate Fix

Codex run/develop guidance now treats browser QA as availability-gated instead of categorically unavailable. When a Codex session exposes browser tools, the orchestrator must run the qa-browser methodology inline and write browser-lens evidence with `manual_ux_verification`; when required browser verification cannot run, it must write a browser-lens `BLOCKED_ENV` rather than falling back to CLI-only QA.

Claude-side policy remains unchanged: main Claude sessions delegate Chrome DevTools MCP use to `harness:qa-browser`. The new regression test protects both sides by checking Codex does not reintroduce stale skip wording and Claude still requires qa-browser delegation.
