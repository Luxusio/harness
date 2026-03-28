#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# TaskCreated hook — initializes minimal task artifacts.
# Non-blocking (exit 0 always).
# stdin: JSON | exit 0: success | exit 2: block (unused)

TASK_ID=$(json_field "task_id")
TASK_ID="${TASK_ID:-${HARNESS_TASK_ID:-}}"

[[ -z "$TASK_ID" ]] && exit 0

TARGET="${TASK_DIR}/${TASK_ID}"
mkdir -p "$TARGET"

# Initialize TASK_STATE.yaml if missing
if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  cat > "${TARGET}/TASK_STATE.yaml" <<EOF
task_id: ${TASK_ID}
status: created
lane: pending
mutates_repo: unknown
qa_required: pending
qa_mode: auto
plan_verdict: pending
runtime_verdict: pending
document_verdict: pending
blockers: []
updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  echo "INFO: Initialized ${TARGET}/TASK_STATE.yaml"
fi

# Create HANDOFF.md stub if missing
if [[ ! -f "${TARGET}/HANDOFF.md" ]]; then
  cat > "${TARGET}/HANDOFF.md" <<EOF
# Handoff: ${TASK_ID}
status: pending
updated: $(date -u +%Y-%m-%dT%H:%M:%SZ)
EOF
  echo "INFO: Created ${TARGET}/HANDOFF.md stub"
fi

# Create REQUEST.md stub if missing
if [[ ! -f "${TARGET}/REQUEST.md" ]]; then
  request_text=$(json_field "description")
  request_text="${request_text:-$(json_field "request")}"
  cat > "${TARGET}/REQUEST.md" <<EOF
# Request: ${TASK_ID}
created: $(date -u +%Y-%m-%dT%H:%M:%SZ)

${request_text:-<!-- Request details pending -->}
EOF
  echo "INFO: Created ${TARGET}/REQUEST.md"
fi

exit 0
