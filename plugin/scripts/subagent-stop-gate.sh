#!/usr/bin/env bash
set -euo pipefail

# Check that subagents leave the artifacts they own before stopping.
# developer must leave: TASK_STATE.yaml + HANDOFF.md
# writer must leave: DOC_SYNC.md when durable docs changed
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  exit 0
fi

AGENT_NAME="${CLAUDE_AGENT_NAME:-unknown}"

if [[ "$AGENT_NAME" == "developer" ]]; then
  if [[ ! -f "${LATEST}/PLAN.md" ]]; then
    echo "BLOCKED: developer cannot stop without PLAN.md in ${LATEST}/"
    exit 1
  fi
  if [[ ! -f "${LATEST}/CRITIC__plan.md" ]]; then
    echo "BLOCKED: developer cannot stop without plan critic verdict in ${LATEST}/"
    exit 1
  fi
  if [[ ! -f "${LATEST}/TASK_STATE.yaml" ]]; then
    echo "BLOCKED: developer cannot stop without TASK_STATE.yaml in ${LATEST}/"
    exit 1
  fi
  if [[ ! -f "${LATEST}/HANDOFF.md" ]]; then
    echo "BLOCKED: developer cannot stop without HANDOFF.md in ${LATEST}/"
    exit 1
  fi
fi

if [[ "$AGENT_NAME" == "writer" ]]; then
  if [[ ! -f "${LATEST}/TASK_STATE.yaml" ]]; then
    echo "BLOCKED: writer cannot stop without TASK_STATE.yaml in ${LATEST}/"
    exit 1
  fi
  # Check if durable note files or root indexes were changed
  # If so, DOC_SYNC.md is required
  if git diff --name-only HEAD 2>/dev/null | grep -qE "^doc/.*\.(md)$"; then
    if [[ ! -f "${LATEST}/DOC_SYNC.md" ]]; then
      echo "BLOCKED: writer cannot stop without DOC_SYNC.md when durable docs changed in ${LATEST}/"
      exit 1
    fi
  fi
fi
