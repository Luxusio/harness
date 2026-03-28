#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# Stop hook — catches premature completion attempts.
# BLOCKING: exit 2 prevents stop when tasks are still open.
# stdin: JSON | exit 0: allow stop | exit 2: BLOCK stop

# No harness initialized — allow stop
[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0
[[ ! -d "$TASK_DIR" ]] && exit 0

OPEN_TASKS=()
BLOCKED_TASKS=()

for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue

  state_file="${task}TASK_STATE.yaml"
  task_id=$(basename "$task")

  [[ ! -f "$state_file" ]] && continue

  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')

  case "$status" in
    closed|archived|stale) ;;
    blocked_env) BLOCKED_TASKS+=("$task_id") ;;
    *) OPEN_TASKS+=("$task_id [status: ${status:-unknown}]") ;;
  esac
done

if [[ ${#OPEN_TASKS[@]} -gt 0 ]]; then
  echo "BLOCKED: Cannot stop — open tasks remain:"
  for t in "${OPEN_TASKS[@]}"; do
    echo "  - ${t}"
  done
  if [[ ${#BLOCKED_TASKS[@]} -gt 0 ]]; then
    echo "Note: ${#BLOCKED_TASKS[@]} task(s) are blocked_env (need env fix):"
    for t in "${BLOCKED_TASKS[@]}"; do
      echo "  - ${t}"
    done
  fi
  exit 2
fi

if [[ ${#BLOCKED_TASKS[@]} -gt 0 ]]; then
  echo "WARNING: Stopping with ${#BLOCKED_TASKS[@]} blocked_env task(s):"
  for t in "${BLOCKED_TASKS[@]}"; do
    echo "  - ${t}"
  done
fi

exit 0
