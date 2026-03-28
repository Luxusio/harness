#!/usr/bin/env bash
set -euo pipefail

# Init-only: ensure minimal task artifacts exist. Never blocks.
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

# Initialize TASK_STATE.yaml if missing (minimal schema)
if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  cat > "${TARGET}/TASK_STATE.yaml" <<EOF
task_id: ${TASK_ID}
lane: pending
status: created
mutates_repo: true
updated: $(date +%Y-%m-%d)
EOF
  echo "INFO: Initialized ${TARGET}/TASK_STATE.yaml"
fi

# Create HANDOFF.md stub if missing
if [[ ! -f "${TARGET}/HANDOFF.md" ]]; then
  cat > "${TARGET}/HANDOFF.md" <<EOF
# Handoff: ${TASK_ID}
status: pending
updated: $(date +%Y-%m-%d)
EOF
  echo "INFO: Created ${TARGET}/HANDOFF.md stub"
fi
