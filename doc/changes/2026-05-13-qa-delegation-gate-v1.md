# 2026-05-13 — main session no longer runs test runners inline (C-18 v1)

## What changed

The main orchestrator session is now redirected to qa-* subagents when it tries to inline-run a test runner. The new PreToolUse:Bash hook `plugin/scripts/qa_delegation_gate.py` watches for these commands:

- `pytest`, `py.test`, `python -m pytest`
- `npm test`, `yarn test`, `pnpm test`, `bun test`
- `cargo test`, `go test`
- `mvn ... test`, `gradle(w) ... test`
- `rspec`, `jest`, `vitest`, `mocha`, `phpunit`, `rake test`

When matched, the hook emits a `permissionDecision: "deny"` envelope whose reason surfaces in the system-reminder. The model reads it and spawns the appropriate qa-* agent (`harness:qa-cli` / `qa-api` / `qa-browser` / `qa-desktop`) instead of running the test inline.

Why: the previous pattern (main session running tests directly) bloated the orchestrator context with test output and background process state, which corrupted judgment and caused stalls. qa-* agents run verification in isolated contexts and write structured findings to `CRITIC__qa.md` — the main session only reads the verdict.

## What's NOT blocked

By user request (mid-plan): `curl`, `wget`, `httpie`, `psql -c`, `mysql -e`, `alembic`. These have too many legitimate inline uses — API exploration, schema inspection, ad-hoc debugging — to block at this level.

Also still allowed inline: lint / formatter / typecheck / build / compile / read-only inspection (grep, find, git status, git diff).

## Bypass

`HARNESS_SKIP_QA_DELEGATION=1` env var — one-shot. The bypass is logged to `learnings.jsonl` for retro audit.

## v1 vs v2

This is v1: WARN-level. Even though the hook returns "deny", the design assumption is that the model self-redirects to spawn qa-*. v2 will harden to a true block once reliable main-vs-subagent detection is in place. The current limitation is that the hook can't distinguish a qa-* subagent's legitimate `pytest` from the main session's — so every qa-* run will emit one redundant warn row. Cost: log noise. Benefit at v1: signal collection for tuning v2.

## Where to look

- New § 8c "Verification delegation" in `plugin/CLAUDE.md`
- New C-18 contract in `CONTRACTS.md` (managed block)
- New script `plugin/scripts/qa_delegation_gate.py`
- Cross-refs in `plugin/skills/develop/SKILL.md` Phase 3 / 3.9 / 7
