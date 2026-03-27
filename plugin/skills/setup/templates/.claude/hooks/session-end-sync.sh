#!/usr/bin/env bash
set -euo pipefail

# At session end, save unresolved state for next session handoff.
QUEUE=".claude/harness/maintenance/QUEUE.md"
TASK_DIR=".claude/harness/tasks"

if [[ ! -f "$QUEUE" ]]; then
  exit 0
fi

DATE=$(date +%Y-%m-%d)
ITEMS=()

# Check for open tasks
if [[ -d "$TASK_DIR" ]]; then
  for task in "${TASK_DIR}"/TASK__*/; do
    if [[ -d "$task" && ! -f "${task}RESULT.md" ]]; then
      ITEMS+=("- [${DATE}] SessionEnd: open task $(basename "$task") — no RESULT.md")
    fi
  done
fi

# Check for unresolved INF notes
for inf in doc/*/INF__*.md; do
  if [[ -f "$inf" ]] && grep -q "status:active" "$inf" 2>/dev/null; then
    ITEMS+=("- [${DATE}] SessionEnd: unresolved INF $(basename "$inf")")
  fi
done

if [[ ${#ITEMS[@]} -gt 0 ]]; then
  printf '%s\n' "${ITEMS[@]}" >> "$QUEUE"
fi
