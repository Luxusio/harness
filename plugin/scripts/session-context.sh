#!/usr/bin/env bash
set -euo pipefail

if [[ -f "doc/CLAUDE.md" ]]; then
  echo "harness status: initialized in this repository."
  echo ""

  # === DOC REGISTRY ===
  echo "=== DOC REGISTRY ==="
  cat "doc/CLAUDE.md"
  echo ""

  # === RECENT NOTES ===
  echo "=== RECENT NOTES ==="
  # Show latest REQ/OBS/INF notes across all roots
  found_notes=0
  for root_dir in doc/*/; do
    if [[ -d "$root_dir" ]]; then
      for note in "${root_dir}"REQ__*.md "${root_dir}"OBS__*.md "${root_dir}"INF__*.md; do
        if [[ -f "$note" ]]; then
          echo "- $note"
          found_notes=1
        fi
      done
    fi
  done
  if [[ "$found_notes" -eq 0 ]]; then
    echo "(no durable notes found)"
  fi
  echo ""

  # === PENDING TASKS ===
  echo "=== PENDING TASKS ==="
  task_dir=".claude/harness/tasks"
  if [[ -d "$task_dir" ]]; then
    for task in "$task_dir"/TASK__*/; do
      if [[ -d "$task" ]]; then
        result_file="${task}RESULT.md"
        if [[ ! -f "$result_file" ]]; then
          echo "- OPEN: $(basename "$task")"
        fi
      fi
    done
  else
    echo "(no task history)"
  fi
  echo ""

  echo "CLAUDE.md is present -- follow its instructions for request handling."
else
  echo "harness status: plugin installed but this repository is not initialized."
  echo "If the user wants durable knowledge with REQ/OBS/INF notes and critic-gated workflows, suggest /harness:setup."
fi
