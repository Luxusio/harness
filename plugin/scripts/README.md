# plugin/scripts/

Minimal harness2 scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — core library (YAML helpers, scaffold, routing, context, path sync)
- `write_plan_artifact.py` — plan artifact writer (PLAN.md, CHECKS.yaml, AUDIT_TRAIL.md)
- `update_checks.py` — post-plan AC status updater (develop/qa use this, not Edit)
- `note_freshness.py` — flips `freshness: current -> suspect` on invalidated notes
- `contract_lint.py` — CONTRACTS.md managed-block lint; `--check-weight` enforces C-13 SKILL.md budget
- `prewrite_gate.py` — PreToolUse hook (artifact ownership + plan-first enforcement)
- `stop_gate.py` — Stop hook (open task reminder)
- `golden_replay.py` — regression smoke tests for the scripts above (stdlib only)
- `review-log` / `review-read` — standalone plan review tools
