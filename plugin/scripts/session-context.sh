#!/usr/bin/env bash
set -euo pipefail

MANIFEST=".claude/harness/manifest.yaml"

if [[ -f "$MANIFEST" ]]; then
  echo "harness status: initialized (v3)."
  echo ""

  # === REGISTERED ROOTS ===
  echo "=== REGISTERED ROOTS ==="
  if [[ -f "CLAUDE.md" ]]; then
    grep -E "^registered_roots:" CLAUDE.md 2>/dev/null || echo "(none found in CLAUDE.md)"
  fi
  echo ""

  # === OPEN TASKS ===
  echo "=== OPEN TASKS ==="
  TASK_DIR=".claude/harness/tasks"
  found_open=0
  if [[ -d "$TASK_DIR" ]]; then
    for task in "$TASK_DIR"/TASK__*/; do
      if [[ -d "$task" ]]; then
        state_file="${task}TASK_STATE.yaml"
        if [[ -f "$state_file" ]]; then
          status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
          if [[ "$status" != "closed" ]]; then
            echo "- $(basename "$task") [status: ${status:-unknown}]"
            found_open=1
            # Show blockers from HANDOFF.md if present
            handoff="${task}HANDOFF.md"
            if [[ -f "$handoff" ]]; then
              blocker=$(grep -i "blocker" "$handoff" 2>/dev/null | head -2)
              if [[ -n "$blocker" ]]; then
                echo "  $blocker"
              fi
            fi
          fi
        elif [[ ! -f "${task}RESULT.md" ]]; then
          echo "- OPEN: $(basename "$task") (no TASK_STATE.yaml)"
          found_open=1
        fi
      fi
    done
  fi
  if [[ "$found_open" -eq 0 ]]; then
    echo "(no open tasks)"
  fi
  echo ""

  # === BLOCKED TASKS ===
  echo "=== BLOCKED TASKS ==="
  found_blocked=0
  if [[ -d "$TASK_DIR" ]]; then
    for task in "$TASK_DIR"/TASK__*/; do
      if [[ -d "$task" ]]; then
        state_file="${task}TASK_STATE.yaml"
        if [[ -f "$state_file" ]] && grep -q "status: blocked_env" "$state_file" 2>/dev/null; then
          echo "- BLOCKED: $(basename "$task")"
          found_blocked=1
        fi
      fi
    done
  fi
  if [[ "$found_blocked" -eq 0 ]]; then
    echo "(none)"
  fi
  echo ""

  # === MAINTENANCE QUEUE ===
  QUEUE=".claude/harness/maintenance/QUEUE.md"
  if [[ -f "$QUEUE" ]] && [[ -s "$QUEUE" ]]; then
    echo "=== MAINTENANCE QUEUE ==="
    tail -5 "$QUEUE"
    echo ""
  fi

  echo "CLAUDE.md is present -- follow its instructions for request handling."
else
  echo "harness status: plugin installed but this repository is not initialized."
  echo "If the user wants durable knowledge with REQ/OBS/INF notes and critic-gated workflows, suggest /harness:setup."
fi
