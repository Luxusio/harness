#!/usr/bin/env bash
set -euo pipefail

MANIFEST=".claude/harness/manifest.yaml"

if [[ -f "$MANIFEST" ]]; then
  echo "harness: initialized (v3)."
  echo ""

  # === OPEN TASKS ===
  echo "=== OPEN TASKS ==="
  TASK_DIR=".claude/harness/tasks"
  found_open=0
  if [[ -d "$TASK_DIR" ]]; then
    for task in "$TASK_DIR"/TASK__*/; do
      if [[ -d "$task" ]]; then
        state_file="${task}TASK_STATE.yaml"
        task_id=$(basename "$task")
        if [[ -f "$state_file" ]]; then
          status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
          lane=$(grep "^lane:" "$state_file" 2>/dev/null | head -1 | sed 's/lane: *//')
          if [[ "$status" != "closed" ]]; then
            echo "- ${task_id} [lane: ${lane:-unknown}, status: ${status:-unknown}]"
            found_open=1
          fi
        fi
      fi
    done
  fi
  if [[ "$found_open" -eq 0 ]]; then
    echo "(no open tasks)"
  fi

  echo ""
  echo "Follow CLAUDE.md instructions for request handling."
else
  echo "harness: plugin installed but repo not initialized."
  echo "Run /harness:setup to bootstrap."
fi
