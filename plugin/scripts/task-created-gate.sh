#!/usr/bin/env bash
set -euo pipefail

# Ensure task folder, REQUEST.md, TASK_STATE.yaml, and HANDOFF.md exist when a task is created.
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  echo "WARNING: No task folder found in ${TASK_DIR}/"
  exit 0
fi

if [[ ! -f "${LATEST}/REQUEST.md" ]]; then
  echo "WARNING: ${LATEST}/REQUEST.md missing — task should have a request record."
fi

if [[ ! -f "${LATEST}/TASK_STATE.yaml" ]]; then
  # Initialize default TASK_STATE.yaml
  cat > "${LATEST}/TASK_STATE.yaml" <<EOF
status: created
mutates_repo: true
qa_required: true
qa_mode: browser-first
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
needs_env: []
updated: $(date +%Y-%m-%d)
EOF
  echo "INFO: Initialized ${LATEST}/TASK_STATE.yaml with default status."
fi

if [[ ! -f "${LATEST}/HANDOFF.md" ]]; then
  echo "WARNING: ${LATEST}/HANDOFF.md missing — task should have a handoff document."
fi
