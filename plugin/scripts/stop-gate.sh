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
PENDING_DOC_SYNC=()

for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue

  state_file="${task}TASK_STATE.yaml"
  task_id=$(basename "$task")

  [[ ! -f "$state_file" ]] && continue

  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')

  case "$status" in
    closed|archived|stale) ;;
    blocked_env) BLOCKED_TASKS+=("$task_id") ;;
    *)
      OPEN_TASKS+=("$task_id [status: ${status:-unknown}]")
      # Warn about pending DOC_SYNC for repo-mutating open tasks
      mutates=$(grep "^mutates_repo:" "$state_file" 2>/dev/null | head -1 | sed 's/mutates_repo: *//')
      if [[ "$mutates" == "true" || "$mutates" == "unknown" ]]; then
        [[ ! -f "${task}DOC_SYNC.md" ]] && PENDING_DOC_SYNC+=("$task_id")
      fi
      ;;
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
  if [[ ${#PENDING_DOC_SYNC[@]} -gt 0 ]]; then
    echo "Note: ${#PENDING_DOC_SYNC[@]} repo-mutating task(s) still need DOC_SYNC.md:"
    for t in "${PENDING_DOC_SYNC[@]}"; do
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
