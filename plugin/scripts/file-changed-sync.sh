#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# FileChanged hook — verdict invalidation.
# Non-blocking. Resets stale PASS verdicts to pending when files change.
# stdin: JSON | exit 0: always

[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0
[[ ! -d "$TASK_DIR" ]] && exit 0

# Parse changed files from stdin if available
CHANGED_FILES=$(json_array "files")
[[ -z "$CHANGED_FILES" ]] && CHANGED_FILES=$(json_array "paths")

for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue

  state_file="${task}TASK_STATE.yaml"
  task_id=$(basename "$task")

  [[ ! -f "$state_file" ]] && continue

  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')

  case "$status" in
    closed|archived|stale) continue ;;
  esac

  # Invalidate runtime verdict if PASS
  runtime_verdict=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
  if [[ "$runtime_verdict" == "PASS" ]]; then
    sed -i "s/^runtime_verdict: PASS/runtime_verdict: pending/" "$state_file"
    sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
    echo "INVALIDATED: ${task_id} — runtime_verdict reset to pending (files changed after PASS)"
  fi

  # Invalidate document verdict if PASS and docs changed
  document_verdict=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
  if [[ "$document_verdict" == "PASS" ]]; then
    doc_changed=false
    if [[ -n "$CHANGED_FILES" ]]; then
      echo "$CHANGED_FILES" | grep -qE '(^doc/|CLAUDE\.md|\.md$)' && doc_changed=true
    else
      # No file list available — conservatively invalidate
      doc_changed=true
    fi
    if [[ "$doc_changed" == "true" ]]; then
      sed -i "s/^document_verdict: PASS/document_verdict: pending/" "$state_file"
      sed -i "s/^updated: .*/updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)/" "$state_file"
      echo "INVALIDATED: ${task_id} — document_verdict reset to pending (docs changed after PASS)"
    fi
  fi
done

exit 0
