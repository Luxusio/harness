# Goal Payload Capture

## Summary

Added an opt-in hook payload probe for discovering how Codex and Claude surface
native `/goal` commands, active goal context, and goal-related stop-loop state
to plugin hooks.

## Behavior

- `plugin/scripts/_lib.py` now owns shared payload-probe logic.
- `plugin/scripts/prompt_memory.py` records `UserPromptSubmit` payload shape.
- `plugin/scripts/prompt_memory.py` also syncs Claude `/goal` and `/골`
  prompts from `UserPromptSubmit` into durable harness Goal state.
- `plugin/scripts/stop_gate.py` records `Stop` payload shape before normal
  stop-gate routing, even when no active harness task exists.
- Enable capture with `HARNESS_CAPTURE_GOAL_PAYLOADS=1` or by creating
  `doc/harness/debug/CAPTURE_GOAL_PAYLOADS` in the repository.
- Records include top-level payload keys, prompt candidate fields, goal-command
  detection for `/goal` and `/골`, transcript candidate lines containing
  `/goal` or `Goal set`, runtime/session metadata, and a capped payload envelope
  for inspection.
- Default behavior is unchanged: no debug files are written unless capture is
  enabled.

## Rationale

Codex exposes active goal state to the agent through goal context/tooling, but
the hook payload only showed current user prompt/session metadata in live
testing. Live Claude testing showed `/goal` prompt text in `UserPromptSubmit`,
which is the automatic sync surface. Stop payloads can still reveal later
continuation state. The durable Goal model now lets harness map native goals to
child tasks while keeping existing task lifecycle artifacts intact.
