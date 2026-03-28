#!/usr/bin/env bash
set -euo pipefail

MANIFEST=".claude/harness/manifest.yaml"

if [[ -f "$MANIFEST" ]]; then
  echo "harness: initialized (v3)."
  echo ""

  TASK_DIR=".claude/harness/tasks"
  found_open=0
  found_blocked=0

  # === OPEN TASKS ===
  echo "=== OPEN TASKS ==="
  if [[ -d "$TASK_DIR" ]]; then
    for task in "$TASK_DIR"/TASK__*/; do
      if [[ -d "$task" ]]; then
        state_file="${task}TASK_STATE.yaml"
        task_id=$(basename "$task")
        if [[ -f "$state_file" ]]; then
          status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
          lane=$(grep "^lane:" "$state_file" 2>/dev/null | head -1 | sed 's/lane: *//')

          case "$status" in
            closed|archived|stale) continue ;;
            blocked_env)
              echo "- ${task_id} [lane: ${lane:-unknown}, BLOCKED_ENV]"
              found_blocked=1
              found_open=1
              ;;
            *)
              plan_v=$(grep "^plan_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/plan_verdict: *//')
              runtime_v=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
              echo "- ${task_id} [lane: ${lane:-unknown}, status: ${status:-unknown}, plan: ${plan_v:-?}, runtime: ${runtime_v:-?}]"
              found_open=1
              ;;
          esac
        fi
      fi
    done
  fi

  if [[ "$found_open" -eq 0 ]]; then
    echo "(no open tasks)"
  fi

  if [[ "$found_blocked" -eq 1 ]]; then
    echo ""
    echo "WARNING: blocked_env tasks need environment fixes before completion."
  fi

  echo ""
  echo "Follow CLAUDE.md instructions for request handling."
else
  echo "harness: plugin installed but repo not initialized."
  echo "Run /harness:setup to bootstrap."
fi
