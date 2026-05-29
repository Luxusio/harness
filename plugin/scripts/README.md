# plugin/scripts/

Minimal harness scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — core library (YAML helpers, scaffold, routing, context, path sync, frontmatter public API)
- `write_plan_artifact.py` — legacy compatibility shim; canonical plan artifact writes use MCP `write_plan_artifact`
- `update_checks.py` — post-plan AC status updater (develop/qa use this, not Edit)
- `note_freshness.py` — flips `freshness: current -> suspect` on invalidated notes
- `environment_snapshot.py` — task_start snapshot with manifest, tool manager, and tool version probes
- `prompt_memory.py` — UserPromptSubmit context injection, including active-task restore digest and runbook reminders
- `task_pack_runner.py` — ordered multi-task queue state for roadmap/stage requests that should continue without asking users to choose internal task sequence
- `contract_lint.py` — CONTRACTS.md managed-block lint; `--check-weight` enforces C-13 SKILL.md budget
- `prewrite_gate.py` — PreToolUse hook (artifact ownership + plan-first enforcement)
- `stop_gate.py` — Stop hook (open task reminder)
- `golden_replay.py` — regression smoke tests for the scripts above (stdlib only)
- `review-log` / `review-read` — standalone plan review tools
- `runbook_memory.py` — manages `doc/harness/runbooks.yaml` and `doc/harness/runbook_candidates.yaml`; approved runbooks are surfaced by `prompt_memory.py`, candidates are reviewed in the active/next harness task and recorded through close-time Self-Healing Candidates
- `hygiene_scan.py` — close-time hygiene scan: contract drift Tier A/B auto-apply + doc_hygiene invocation. State: `doc/harness/.hygiene-last-run`, `doc/harness/.hygiene-pending.json`, `doc/harness/.hygiene-observe.log` (legacy read fallback for old `.maintain-*` names; see `doc/harness/patterns/maintenance-state-naming.md`)
- `doc_hygiene.py` — content-signal KEEP/REMOVE/REVIEW classifier for `doc/changes/` and `doc/common/`. Archives REMOVE files via `git mv` to `_archive/`. State: `doc/harness/.hygiene-pending.json` with legacy `.maintain-pending.json` read fallback
- `hygiene_followup.py` — post-close scheduler that turns pending hygiene review items into one standalone follow-up task so cleanup does not dilute the primary task
- `hygiene_restore.py` — restore a file previously archived by doc_hygiene.py via `git mv`. Usage: `python3 plugin/scripts/hygiene_restore.py <archive-path>`
- `maintain_restore.py` — legacy-compatible wrapper for old restore commands; delegates to `hygiene_restore.py`
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
