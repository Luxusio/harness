#!/usr/bin/env bash
set -euo pipefail

# Ensure task folder, REQUEST.md, TASK_STATE.yaml, and HANDOFF.md exist when a task is created.
# Uses explicit HARNESS_TASK_ID when available; falls back to latest task folder.
TASK_DIR=".claude/harness/tasks"

resolve_task_dir() {
  if [[ -n "${HARNESS_TASK_ID:-}" && -d "${TASK_DIR}/${HARNESS_TASK_ID}" ]]; then
    echo "${TASK_DIR}/${HARNESS_TASK_ID}"
  else
    ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1
  fi
}

TARGET=$(resolve_task_dir)

if [[ -z "$TARGET" ]]; then
  echo "WARNING: No task folder found in ${TASK_DIR}/"
  exit 0
fi

TASK_ID=$(basename "$TARGET")

if [[ ! -f "${TARGET}/REQUEST.md" ]]; then
  echo "WARNING: ${TARGET}/REQUEST.md missing — task should have a request record."
fi

if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  cat > "${TARGET}/TASK_STATE.yaml" <<EOF
task_id: ${TASK_ID}
run_id: $(date +%s)
lane: pending
lane_rationale: pending
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
  echo "INFO: Initialized ${TARGET}/TASK_STATE.yaml with task_id=${TASK_ID}."
fi

if [[ ! -f "${TARGET}/HANDOFF.md" ]]; then
  echo "WARNING: ${TARGET}/HANDOFF.md missing — task should have a handoff document."
fi
