#!/usr/bin/env bash
set -euo pipefail

# Reject task completion without fresh critic PASS.
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  exit 0
fi

if [[ ! -f "${LATEST}/PLAN.md" ]]; then
  echo "BLOCKED: Task cannot complete without PLAN.md"
  exit 1
fi

if [[ ! -f "${LATEST}/CRITIC__plan.md" ]]; then
  echo "BLOCKED: Task cannot complete without plan critic verdict"
  exit 1
fi

if ! grep -q "PASS" "${LATEST}/CRITIC__plan.md" 2>/dev/null; then
  echo "BLOCKED: Plan critic did not PASS"
  exit 1
fi

if [[ ! -f "${LATEST}/RESULT.md" ]]; then
  echo "BLOCKED: Task cannot complete without RESULT.md"
  exit 1
fi
