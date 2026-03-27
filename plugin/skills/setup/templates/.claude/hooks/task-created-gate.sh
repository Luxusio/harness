#!/usr/bin/env bash
set -euo pipefail

# Ensure task folder and REQUEST.md exist when a task is created.
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  echo "WARNING: No task folder found in ${TASK_DIR}/"
  exit 0
fi

if [[ ! -f "${LATEST}/REQUEST.md" ]]; then
  echo "WARNING: ${LATEST}/REQUEST.md missing — task should have a request record."
fi
