#!/usr/bin/env bash
set -euo pipefail

# Block developer/writer from stopping without a critic verdict.
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  exit 0
fi

AGENT_NAME="${CLAUDE_AGENT_NAME:-unknown}"

if [[ "$AGENT_NAME" == "developer" && ! -f "${LATEST}/CRITIC__runtime.md" ]]; then
  echo "BLOCKED: developer cannot stop without runtime critic verdict in ${LATEST}/"
  exit 1
fi

if [[ "$AGENT_NAME" == "writer" && ! -f "${LATEST}/CRITIC__write.md" ]]; then
  echo "BLOCKED: writer cannot stop without write critic verdict in ${LATEST}/"
  exit 1
fi
