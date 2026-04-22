# plugin/scripts/

Minimal harness scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — core library (YAML helpers, scaffold, routing, context, path sync, frontmatter public API)
- `write_plan_artifact.py` — plan artifact writer (PLAN.md, CHECKS.yaml, AUDIT_TRAIL.md)
- `update_checks.py` — post-plan AC status updater (develop/qa use this, not Edit)
- `note_freshness.py` — flips `freshness: current -> suspect` on invalidated notes
- `contract_lint.py` — CONTRACTS.md managed-block lint; `--check-weight` enforces C-13 SKILL.md budget
- `prewrite_gate.py` — PreToolUse hook (artifact ownership + plan-first enforcement)
- `stop_gate.py` — Stop hook (open task reminder)
- `golden_replay.py` — regression smoke tests for the scripts above (stdlib only)
- `review-log` / `review-read` — standalone plan review tools
- `hygiene_scan.py` — SessionStart auto-hygiene: contract drift Tier A/B auto-apply + doc_hygiene invocation. State: `doc/harness/.maintain-last-run`, `doc/harness/.maintain-pending.json`, `doc/harness/.maintain-observe.log`
- `doc_hygiene.py` — content-signal KEEP/REMOVE/REVIEW classifier for `doc/changes/` and `doc/common/`. Archives REMOVE files via `git mv` to `_archive/`. State: `doc/harness/.maintain-pending.json`
- `maintain_restore.py` — restore a file previously archived by doc_hygiene.py via `git mv`. Usage: `python3 plugin/scripts/maintain_restore.py <archive-path>`
