#!/usr/bin/env bash
set -euo pipefail

# Warn-only: remind agents about expected artifacts. Never blocks.
# Only uses explicit HARNESS_TASK_ID — no fallback to latest task.
TASK_DIR=".claude/harness/tasks"

if [[ -z "${HARNESS_TASK_ID:-}" ]]; then
  exit 0
fi

TARGET="${TASK_DIR}/${HARNESS_TASK_ID}"

if [[ ! -d "$TARGET" ]]; then
  exit 0
fi

TASK_ID="$HARNESS_TASK_ID"
AGENT_NAME="${CLAUDE_AGENT_NAME:-unknown}"

if [[ "$AGENT_NAME" == "developer" ]]; then
  [[ ! -f "${TARGET}/PLAN.md" ]] && echo "REMINDER: ${TASK_ID} — PLAN.md not found"
  [[ ! -f "${TARGET}/HANDOFF.md" ]] && echo "REMINDER: ${TASK_ID} — update HANDOFF.md with verification breadcrumbs"
fi

exit 0
