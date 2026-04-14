# plugin/scripts/

Minimal harness2 scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — core library (YAML helpers, scaffold, routing, context, path sync)
- `write_plan_artifact.py` — plan artifact writer (PLAN.md, CHECKS.yaml, AUDIT_TRAIL.md)
- `prewrite_gate.py` — PreToolUse hook (artifact ownership + plan-first enforcement)
- `stop_gate.py` — Stop hook (open task reminder)
- `review-log` / `review-read` — standalone plan review tools
