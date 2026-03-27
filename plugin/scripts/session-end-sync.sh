#!/usr/bin/env bash
set -euo pipefail

# At session end, save unresolved state for next session handoff.
# Scans by TASK_STATE.yaml, reports task_id for each open item.
QUEUE=".claude/harness/maintenance/QUEUE.md"
TASK_DIR=".claude/harness/tasks"

if [[ ! -f "$QUEUE" ]]; then
  exit 0
fi

DATE=$(date +%Y-%m-%d)
ITEMS=()

# Check for open tasks by TASK_STATE.yaml — report task_id explicitly
if [[ -d "$TASK_DIR" ]]; then
  for task in "${TASK_DIR}"/TASK__*/; do
    if [[ -d "$task" ]]; then
      state_file="${task}TASK_STATE.yaml"
      task_id=$(basename "$task")
      if [[ -f "$state_file" ]]; then
        status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
        lane=$(grep "^lane:" "$state_file" 2>/dev/null | head -1 | sed 's/lane: *//')
        case "$status" in
          closed)
            ;;
          blocked_env)
            ITEMS+=("- [${DATE}] SessionEnd: BLOCKED task ${task_id} [lane: ${lane:-unknown}] — status: blocked_env")
            ;;
          *)
            ITEMS+=("- [${DATE}] SessionEnd: open task ${task_id} [lane: ${lane:-unknown}] — status: ${status:-unknown}")
            ;;
        esac
      elif [[ ! -f "${task}RESULT.md" ]]; then
        ITEMS+=("- [${DATE}] SessionEnd: open task ${task_id} — no TASK_STATE.yaml, no RESULT.md")
      fi
    fi
  done
fi

# Check for unresolved INF notes
for inf in doc/*/INF__*.md; do
  if [[ -f "$inf" ]] && grep -q "status:active" "$inf" 2>/dev/null; then
    ITEMS+=("- [${DATE}] SessionEnd: unresolved INF $(basename "$inf")")
  fi
done

# Summarize open document sync work — report task_id
if [[ -d "$TASK_DIR" ]]; then
  for task in "${TASK_DIR}"/TASK__*/; do
    if [[ -d "$task" && -f "${task}TASK_STATE.yaml" ]]; then
      status=$(grep "^status:" "${task}TASK_STATE.yaml" 2>/dev/null | head -1 | sed 's/status: *//')
      task_id=$(basename "$task")
      if [[ "$status" != "closed" && ! -f "${task}DOC_SYNC.md" ]]; then
        if grep -q "mutates_repo: true" "${task}TASK_STATE.yaml" 2>/dev/null; then
          ITEMS+=("- [${DATE}] SessionEnd: missing DOC_SYNC.md for ${task_id}")
        fi
      fi
    fi
  done
fi

if [[ ${#ITEMS[@]} -gt 0 ]]; then
  printf '%s\n' "${ITEMS[@]}" >> "$QUEUE"
fi
