#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# FileChanged hook — task-scoped verdict invalidation.
# Non-blocking. Resets stale PASS verdicts to pending only for tasks
# whose touched_paths/roots_touched/verification_targets overlap with the changed file(s).
#
# Precision rules:
#   - doc path change  → invalidate document_verdict only
#   - runtime path change → invalidate runtime_verdict only (via verification_targets)
#   - both → invalidate both
# Conservative fallback (no file list): invalidate ALL verdicts on ALL open tasks.
# Note freshness: if a changed file matches a note's invalidated_by_paths, set note freshness to suspect.
# stdin: JSON | exit 0: always

[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0
[[ ! -d "$TASK_DIR" ]] && exit 0

NOW=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# Parse changed files from stdin if available
CHANGED_FILES=$(json_array "files")
[[ -z "$CHANGED_FILES" ]] && CHANGED_FILES=$(json_array "paths")

# Invalidate runtime_verdict PASS → pending on a task state file.
invalidate_runtime() {
  local state_file="$1" task_id="$2" reason="$3"
  local rv
  rv=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
  if [[ "$rv" == "PASS" ]]; then
    sed -i "s/^runtime_verdict: PASS/runtime_verdict: pending/" "$state_file"
    sed -i "s/^updated: .*/updated: ${NOW}/" "$state_file"
    echo "INVALIDATED: ${task_id} — runtime_verdict reset to pending (${reason})"
  fi
}

# Invalidate document_verdict PASS → pending on a task state file.
invalidate_document() {
  local state_file="$1" task_id="$2" reason="$3"
  local dv
  dv=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
  if [[ "$dv" == "PASS" ]]; then
    sed -i "s/^document_verdict: PASS/document_verdict: pending/" "$state_file"
    sed -i "s/^updated: .*/updated: ${NOW}/" "$state_file"
    echo "INVALIDATED: ${task_id} — document_verdict reset to pending (${reason})"
  fi

  # Also set doc_changes_detected: true
  if grep -q "^doc_changes_detected:" "$state_file" 2>/dev/null; then
    sed -i "s/^doc_changes_detected: .*/doc_changes_detected: true/" "$state_file"
  else
    echo "doc_changes_detected: true" >> "$state_file"
  fi
}

# Check note files for freshness invalidation.
# A note whose invalidated_by_paths list contains the changed file gets freshness: suspect.
invalidate_note_freshness() {
  local changed_file="$1"
  local notes_dir="doc/common"
  [[ ! -d "$notes_dir" ]] && return

  for note_file in "$notes_dir"/*.md "$notes_dir"/*.yaml; do
    [[ ! -f "$note_file" ]] && continue
    # Check if note has invalidated_by_paths referencing this file
    if grep -q "invalidated_by_paths" "$note_file" 2>/dev/null; then
      if grep -qF "$changed_file" "$note_file" 2>/dev/null; then
        # Set freshness: suspect
        if grep -q "^freshness:" "$note_file" 2>/dev/null; then
          sed -i "s/^freshness: .*/freshness: suspect/" "$note_file"
        else
          # Insert freshness after the first line (front matter or header)
          sed -i "1a freshness: suspect" "$note_file"
        fi
        echo "NOTE SUSPECT: ${note_file} — freshness set to suspect (${changed_file} changed)"
      fi
    fi
  done
}

# Process a single changed file with precision invalidation.
process_changed_file() {
  local changed_file="$1"
  local changed_is_doc changed_is_runtime
  changed_is_doc=false
  changed_is_runtime=false

  if is_doc_path "$changed_file"; then
    changed_is_doc=true
  else
    changed_is_runtime=true
  fi

  # Note freshness check (applies to all changed files)
  invalidate_note_freshness "$changed_file"

  if [[ "$changed_is_runtime" == "true" ]]; then
    # Runtime change: invalidate runtime_verdict on tasks whose verification_targets overlap
    while IFS= read -r task; do
      [[ -z "$task" ]] && continue
      local state_file="${task}TASK_STATE.yaml"
      local task_id
      task_id=$(basename "$task")
      [[ ! -f "$state_file" ]] && continue
      invalidate_runtime "$state_file" "$task_id" "${changed_file} changed after PASS"
    done < <(find_tasks_with_verification_targets "$changed_file")
  fi

  if [[ "$changed_is_doc" == "true" ]]; then
    # Doc change: invalidate document_verdict on tasks whose touched_paths overlap
    while IFS= read -r task; do
      [[ -z "$task" ]] && continue
      local state_file="${task}TASK_STATE.yaml"
      local task_id
      task_id=$(basename "$task")
      [[ ! -f "$state_file" ]] && continue
      invalidate_document "$state_file" "$task_id" "${changed_file} doc changed after PASS"
    done < <(find_tasks_touching_path "$changed_file")
  fi
}

if [[ -n "$CHANGED_FILES" ]]; then
  # Process each changed file individually with precision
  while IFS= read -r file; do
    [[ -z "$file" ]] && continue
    process_changed_file "$file"
  done <<< "$CHANGED_FILES"
else
  # No file list available — conservative fallback: invalidate ALL verdicts on ALL open tasks
  for task in "$TASK_DIR"/TASK__*/; do
    [[ ! -d "$task" ]] && continue

    state_file="${task}TASK_STATE.yaml"
    task_id=$(basename "$task")

    [[ ! -f "$state_file" ]] && continue

    status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
    case "$status" in
      closed|archived|stale) continue ;;
    esac

    invalidate_runtime "$state_file" "$task_id" "files changed after PASS, no file list"
    invalidate_document "$state_file" "$task_id" "files changed after PASS, no file list"
  done
fi

exit 0
