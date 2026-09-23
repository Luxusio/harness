# plugin/scripts/

Minimal harness scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — core library (YAML helpers, scaffold, routing, context, path sync, receipt publication, and content-addressed review-detail storage)
- Receipt snapshots accept only the unified exact-field, string-valued schema defined by the consolidated-artifact ADR.
- `subagent_lifecycle.py` — direct Claude start/stop and stop-only receipt handling; active work is derived from unmatched current-run starts, with no background registry artifact
- `note_freshness.py` — flips `freshness: current -> suspect` on invalidated notes
- `prompt_memory.py` — zero-Git UserPromptSubmit context injection from stored task/receipt state, including active-task restore digest, Goal routing, and runbook reminders
- `hook_post_tool_use.py` — Codex PostToolUse routing for native `create_goal` and Bash hints
- `codex_hook_registration.py` — fail-open registration recovery used only by SessionStart and spawn-selective PreToolUse; preserves an existing current-version offset and limits late recovery to future subagent starts
- `codex_lifecycle_watcher.py` — SessionStart and spawn-selective PreToolUse restore a safe root-rollout registration; MCP-hosted daemon threads require direct `collaboration.spawn_agent`, exact `SubAgentActivity`, matching structured output, UUIDv7/runtime-local direct rollout lookup, a trusted depth-1 child rollout, and direct `FINAL_ANSWER` delivery before recording strictly correlated review/QA receipts. The watcher never scans session history to recover missing activity. The session marker and current `TASK.json` run identity are the sole task binding; protocol drift leaves close fail-closed until Harness and Codex are upgraded together.
- `setup_finalize.py` — applies canonical operational ignores, migrates legacy manifests to schema v5, verifies setup resources and routing, and stamps `.version` only after success
- `contract_lint.py` — CONTRACTS.md managed-block lint; `--check-weight` enforces C-13 SKILL.md budget
- `prewrite_gate.py` — PreToolUse hook (artifact ownership + plan-first enforcement)
- `golden_replay.py` — regression smoke tests for the scripts above (stdlib only)
- `review-log` — read one bounded formal-review final from stdin and append it to the active or explicitly selected task's `REVIEWS.jsonl`; prints only `DETAIL_SHA256:<hex>`
- `review-read` — stream and validate one task-local `REVIEWS.jsonl` entry selected by lowercase SHA-256; prints only that exact detail and never dumps the log
- `runbook_memory.py` — manages `doc/harness/runbooks.yaml` and `doc/harness/runbook_candidates.yaml`; approved runbooks are surfaced by `prompt_memory.py`, candidates are reviewed in the active/next harness task and recorded through close-time Self-Healing Candidates
# Runtime services

`runtime_services.py` starts, checks, logs, and stops background services from
`doc/harness/manifest.yaml` `runtime.services[]`.

```bash
python3 plugin/scripts/runtime_services.py start
python3 plugin/scripts/runtime_services.py status
python3 plugin/scripts/runtime_services.py logs api
python3 plugin/scripts/runtime_services.py stop
```

It stores state in `doc/harness/runtime/services.json`, logs in
`doc/harness/runtime/logs/`, waits for service healthchecks, and performs bounded
self-healing commands declared in the manifest.

# Formal review detail

`RECEIPTS.jsonl` remains the only review/QA lifecycle authority. Every current
formal-review completion stores its exact final in the same task's
non-authoritative `REVIEWS.jsonl` appendix before publishing the compact
receipt. Operators may retrieve that one body through its `DETAIL_SHA256`:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HARNESS_PLUGIN_ROOT/scripts/review-read" \
  [--task-dir doc/harness/tasks/TASK__slug] <64-lowercase-hex>
```

For diagnostics or adapter tests, append one exact final through the shared
writer instead of writing the protected file directly:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 "$HARNESS_PLUGIN_ROOT/scripts/review-log" \
  [--task-dir doc/harness/tasks/TASK__slug] < review-final.txt
```

The appendix may be absent for receipts written by older runtimes and does not
invalidate them. Current publication fails closed if detail storage fails.
There is no list/latest/all command and no migration or backfill requirement.
