#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# TaskCompleted hook — completion firewall.
# BLOCKING: exit 2 rejects completion when verdicts are missing.
# stdin: JSON | exit 0: allow | exit 2: BLOCK

TASK_ID=$(json_field "task_id")
TASK_ID="${TASK_ID:-${HARNESS_TASK_ID:-}}"

[[ -z "$TASK_ID" ]] && exit 0

TARGET="${TASK_DIR}/${TASK_ID}"
[[ ! -d "$TARGET" ]] && exit 0

FAILURES=()

# --- TASK_STATE.yaml required ---
IS_MUTATING="true"
if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  FAILURES+=("missing TASK_STATE.yaml")
else
  if grep -q "^status: blocked_env" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
    FAILURES+=("status is blocked_env — resolve the blocker first")
  fi
  if grep -q "^mutates_repo: false" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
    IS_MUTATING="false"
  fi
fi

# --- PLAN.md required ---
[[ ! -f "${TARGET}/PLAN.md" ]] && FAILURES+=("missing PLAN.md")

# --- CRITIC__plan.md with verdict: PASS ---
if [[ ! -f "${TARGET}/CRITIC__plan.md" ]]; then
  FAILURES+=("missing plan critic verdict (CRITIC__plan.md)")
elif ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__plan.md" 2>/dev/null; then
  FAILURES+=("plan critic did not PASS")
fi

# --- HANDOFF.md required ---
[[ ! -f "${TARGET}/HANDOFF.md" ]] && FAILURES+=("missing HANDOFF.md")

# --- Runtime critic for repo-mutating tasks ---
if [[ "$IS_MUTATING" != "false" ]]; then
  if [[ ! -f "${TARGET}/CRITIC__runtime.md" ]]; then
    FAILURES+=("repo-mutating task needs runtime critic verdict (CRITIC__runtime.md)")
  elif ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__runtime.md" 2>/dev/null; then
    FAILURES+=("runtime critic did not PASS")
  fi
fi

# --- Document critic when DOC_SYNC.md exists ---
# DOC_SYNC.md is the canonical signal that docs were changed by this task.
# git diff is too broad — it catches working-tree changes unrelated to the task.
if [[ -f "${TARGET}/DOC_SYNC.md" ]]; then
  if [[ ! -f "${TARGET}/CRITIC__document.md" ]]; then
    FAILURES+=("DOC_SYNC.md exists — needs document critic verdict (CRITIC__document.md)")
  elif ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__document.md" 2>/dev/null; then
    FAILURES+=("document critic did not PASS")
  fi
fi

# --- Report and block ---
if [[ ${#FAILURES[@]} -gt 0 ]]; then
  echo "BLOCKED: ${TASK_ID}"
  for f in "${FAILURES[@]}"; do
    echo "  - ${f}"
  done
  exit 2
fi

exit 0
