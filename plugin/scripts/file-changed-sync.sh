#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# FileChanged hook — task-scoped verdict invalidation.
# Non-blocking. Resets stale PASS verdicts to pending only for tasks
# whose touched_paths/roots_touched overlap with the changed file(s).
# stdin: JSON | exit 0: always

[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0
[[ ! -d "$TASK_DIR" ]] && exit 0

# Parse changed files from stdin if available
CHANGED_FILES=$(json_array "files")
[[ -z "$CHANGED_FILES" ]] && CHANGED_FILES=$(json_array "paths")

# Process each changed file
process_changed_file() {
  local changed_file="$1"
  local is_doc=false

  # Detect doc file changes (.md files in doc/, CLAUDE.md, any .md)
  if echo "$changed_file" | grep -qE '(^doc/|CLAUDE\.md|\.md$)'; then
    is_doc=true
  fi

  # Find tasks that touch this file (task-scoped, not all tasks)
  while IFS= read -r task; do
    [[ -z "$task" ]] && continue
    local state_file="${task}TASK_STATE.yaml"
    local task_id
    task_id=$(basename "$task")

    [[ ! -f "$state_file" ]] && continue

    # Invalidate runtime verdict if PASS
    runtime_verdict=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
    if [[ "$runtime_verdict" == "PASS" ]]; then
      sed -i "s/^runtime_verdict: PASS/runtime_verdict: pending/" "$state_file"
      sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
      echo "INVALIDATED: ${task_id} — runtime_verdict reset to pending (${changed_file} changed after PASS)"
    fi

    # Invalidate document verdict if PASS and a doc file changed
    if [[ "$is_doc" == "true" ]]; then
      document_verdict=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
      if [[ "$document_verdict" == "PASS" ]]; then
        sed -i "s/^document_verdict: PASS/document_verdict: pending/" "$state_file"
        sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
        echo "INVALIDATED: ${task_id} — document_verdict reset to pending (${changed_file} changed after PASS)"
      fi

      # Set doc_changes_detected: true on affected tasks
      if grep -q "^doc_changes_detected:" "$state_file" 2>/dev/null; then
        sed -i "s/^doc_changes_detected: .*/doc_changes_detected: true/" "$state_file"
      else
        # Field not present — append it
        echo "doc_changes_detected: true" >> "$state_file"
      fi
    fi
  done < <(find_tasks_touching_path "$changed_file")
}

if [[ -n "$CHANGED_FILES" ]]; then
  # Process each changed file individually
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    process_changed_file "$file"
  done <<< "$CHANGED_FILES"
else
  # No file list available — fall back to conservative: invalidate all open tasks
  for task in "$TASK_DIR"/TASK__*/; do
    [[ ! -d "$task" ]] && continue

    state_file="${task}TASK_STATE.yaml"
    task_id=$(basename "$task")

    [[ ! -f "$state_file" ]] && continue

    status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')

    case "$status" in
      closed|archived|stale) continue ;;
    esac

    runtime_verdict=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
    if [[ "$runtime_verdict" == "PASS" ]]; then
      sed -i "s/^runtime_verdict: PASS/runtime_verdict: pending/" "$state_file"
      sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
      echo "INVALIDATED: ${task_id} — runtime_verdict reset to pending (files changed after PASS, no file list)"
    fi

    document_verdict=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
    if [[ "$document_verdict" == "PASS" ]]; then
      sed -i "s/^document_verdict: PASS/document_verdict: pending/" "$state_file"
      sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
      echo "INVALIDATED: ${task_id} — document_verdict reset to pending (files changed after PASS, no file list)"
    fi
  done
fi

exit 0
