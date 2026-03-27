#!/usr/bin/env bash
set -euo pipefail

# Check that subagents leave the artifacts they own before stopping.
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
  exit 0
fi

TASK_ID=$(basename "$TARGET")
AGENT_NAME="${CLAUDE_AGENT_NAME:-unknown}"

if [[ "$AGENT_NAME" == "developer" ]]; then
  if [[ ! -f "${TARGET}/PLAN.md" ]]; then
    echo "BLOCKED: developer cannot stop without PLAN.md in ${TARGET}/ (task_id=${TASK_ID})"
    exit 1
  fi
  if [[ ! -f "${TARGET}/CRITIC__plan.md" ]]; then
    echo "BLOCKED: developer cannot stop without plan critic verdict in ${TARGET}/ (task_id=${TASK_ID})"
    exit 1
  fi
  if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
    echo "BLOCKED: developer cannot stop without TASK_STATE.yaml in ${TARGET}/ (task_id=${TASK_ID})"
    exit 1
  fi
  if [[ ! -f "${TARGET}/HANDOFF.md" ]]; then
    echo "BLOCKED: developer cannot stop without HANDOFF.md in ${TARGET}/ (task_id=${TASK_ID})"
    exit 1
  fi
  # Verify task_id consistency
  if [[ -f "${TARGET}/TASK_STATE.yaml" ]]; then
    state_task_id=$(grep "^task_id:" "${TARGET}/TASK_STATE.yaml" 2>/dev/null | head -1 | sed 's/task_id: *//')
    if [[ -n "$state_task_id" && "$state_task_id" != "$TASK_ID" ]]; then
      echo "WARNING: TASK_STATE.yaml task_id (${state_task_id}) does not match folder (${TASK_ID})"
    fi
  fi
fi

if [[ "$AGENT_NAME" == "writer" ]]; then
  if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
    echo "BLOCKED: writer cannot stop without TASK_STATE.yaml in ${TARGET}/ (task_id=${TASK_ID})"
    exit 1
  fi
  # Check if durable note files or root indexes were changed
  if git diff --name-only HEAD 2>/dev/null | grep -qE "^doc/.*\.(md)$"; then
    if [[ ! -f "${TARGET}/DOC_SYNC.md" ]]; then
      echo "BLOCKED: writer cannot stop without DOC_SYNC.md when durable docs changed in ${TARGET}/ (task_id=${TASK_ID})"
      exit 1
    fi
  fi
fi
