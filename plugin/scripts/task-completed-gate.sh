#!/usr/bin/env bash
set -euo pipefail

# Reject task completion unless all required artifacts and verdicts are present.
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

# --- TASK_STATE.yaml required ---
if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  echo "BLOCKED: Task ${TASK_ID} cannot complete without TASK_STATE.yaml"
  exit 1
fi

# --- Verify task_id consistency ---
state_task_id=$(grep "^task_id:" "${TARGET}/TASK_STATE.yaml" 2>/dev/null | head -1 | sed 's/task_id: *//')
if [[ -n "$state_task_id" && "$state_task_id" != "$TASK_ID" ]]; then
  echo "WARNING: TASK_STATE.yaml task_id (${state_task_id}) does not match folder (${TASK_ID})"
fi

# --- Verify lane is recorded ---
lane=$(grep "^lane:" "${TARGET}/TASK_STATE.yaml" 2>/dev/null | head -1 | sed 's/lane: *//')
if [[ -z "$lane" || "$lane" == "pending" ]]; then
  echo "WARNING: Task ${TASK_ID} has no lane recorded in TASK_STATE.yaml"
fi

# --- Reject blocked_env ---
if grep -q "status: blocked_env" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
  echo "BLOCKED: Task ${TASK_ID} has status blocked_env — cannot close. Resolve the blocker first."
  exit 1
fi

# --- PLAN.md + plan critic PASS ---
if [[ ! -f "${TARGET}/PLAN.md" ]]; then
  echo "BLOCKED: Task ${TASK_ID} cannot complete without PLAN.md"
  exit 1
fi

if [[ ! -f "${TARGET}/CRITIC__plan.md" ]]; then
  echo "BLOCKED: Task ${TASK_ID} cannot complete without plan critic verdict"
  exit 1
fi

if ! grep -q "PASS" "${TARGET}/CRITIC__plan.md" 2>/dev/null; then
  echo "BLOCKED: Plan critic did not PASS for task ${TASK_ID}"
  exit 1
fi

# --- Check if repo-mutating ---
IS_MUTATING="true"
if grep -q "mutates_repo: false" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
  IS_MUTATING="false"
fi

if [[ "$IS_MUTATING" == "true" ]]; then
  # --- Runtime critic PASS for repo-mutating tasks ---
  if [[ ! -f "${TARGET}/CRITIC__runtime.md" ]]; then
    echo "BLOCKED: Repo-mutating task ${TASK_ID} cannot complete without runtime critic verdict"
    exit 1
  fi

  if ! grep -q "PASS" "${TARGET}/CRITIC__runtime.md" 2>/dev/null; then
    echo "BLOCKED: Runtime critic did not PASS for task ${TASK_ID}"
    exit 1
  fi

  # --- DOC_SYNC.md for repo-mutating tasks ---
  if [[ ! -f "${TARGET}/DOC_SYNC.md" ]]; then
    echo "BLOCKED: Repo-mutating task ${TASK_ID} cannot complete without DOC_SYNC.md"
    exit 1
  fi
fi

# --- Document critic PASS when docs/notes/indexes changed ---
if [[ -f "${TARGET}/DOC_SYNC.md" ]]; then
  if [[ ! -f "${TARGET}/CRITIC__document.md" ]]; then
    echo "BLOCKED: Task ${TASK_ID} with DOC_SYNC.md cannot complete without document critic verdict"
    exit 1
  fi

  if ! grep -q "PASS" "${TARGET}/CRITIC__document.md" 2>/dev/null; then
    echo "BLOCKED: Document critic did not PASS for task ${TASK_ID}"
    exit 1
  fi
fi

# --- RESULT.md required ---
if [[ ! -f "${TARGET}/RESULT.md" ]]; then
  echo "BLOCKED: Task ${TASK_ID} cannot complete without RESULT.md"
  exit 1
fi
