#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# SubagentStop hook — checks subagent left expected artifacts.
# Warn-only (exit 0 always).
# stdin: JSON | exit 0: allow | exit 2: block (unused)

AGENT_NAME=$(json_field "agent_name")
AGENT_NAME="${AGENT_NAME:-$(json_field "agent")}"
AGENT_NAME="${AGENT_NAME:-${CLAUDE_AGENT_NAME:-unknown}}"

TASK_ID=$(json_field "task_id")
TASK_ID="${TASK_ID:-${HARNESS_TASK_ID:-}}"

[[ -z "$TASK_ID" ]] && exit 0

TARGET="${TASK_DIR}/${TASK_ID}"
[[ ! -d "$TARGET" ]] && exit 0

case "$AGENT_NAME" in
  developer|harness:developer)
    [[ ! -f "${TARGET}/TASK_STATE.yaml" ]] && echo "REMINDER: ${TASK_ID} — developer should update TASK_STATE.yaml"
    [[ ! -f "${TARGET}/HANDOFF.md" ]] && echo "REMINDER: ${TASK_ID} — developer should update HANDOFF.md with verification breadcrumbs"
    if [[ -f "${TARGET}/TASK_STATE.yaml" ]]; then
      status=$(grep "^status:" "${TARGET}/TASK_STATE.yaml" 2>/dev/null | head -1 | sed 's/status: *//')
      if [[ "$status" != "implemented" && "$status" != "blocked_env" ]]; then
        echo "REMINDER: ${TASK_ID} — developer finished but status is '${status}', expected 'implemented'"
      fi
    fi
    ;;
  writer|harness:writer)
    if git diff --name-only HEAD 2>/dev/null | grep -qE '(^doc/|\.md$)'; then
      [[ ! -f "${TARGET}/DOC_SYNC.md" ]] && echo "REMINDER: ${TASK_ID} — writer changed docs but DOC_SYNC.md not found"
    fi
    ;;
  critic-runtime|harness:critic-runtime)
    [[ ! -f "${TARGET}/CRITIC__runtime.md" ]] && echo "REMINDER: ${TASK_ID} — runtime critic should write CRITIC__runtime.md"
    ;;
  critic-plan|harness:critic-plan)
    [[ ! -f "${TARGET}/CRITIC__plan.md" ]] && echo "REMINDER: ${TASK_ID} — plan critic should write CRITIC__plan.md"
    ;;
  critic-document|harness:critic-document)
    [[ ! -f "${TARGET}/CRITIC__document.md" ]] && echo "REMINDER: ${TASK_ID} — document critic should write CRITIC__document.md"
    ;;
esac

exit 0
