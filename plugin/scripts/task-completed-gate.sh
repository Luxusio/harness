#!/usr/bin/env bash
set -euo pipefail

# Reject task completion unless all required artifacts and verdicts are present.
TASK_DIR=".claude/harness/tasks"
LATEST=$(ls -dt "${TASK_DIR}"/TASK__* 2>/dev/null | head -1)

if [[ -z "$LATEST" ]]; then
  exit 0
fi

# --- TASK_STATE.yaml required ---
if [[ ! -f "${LATEST}/TASK_STATE.yaml" ]]; then
  echo "BLOCKED: Task cannot complete without TASK_STATE.yaml"
  exit 1
fi

# --- Reject blocked_env ---
if grep -q "status: blocked_env" "${LATEST}/TASK_STATE.yaml" 2>/dev/null; then
  echo "BLOCKED: Task has status blocked_env — cannot close. Resolve the blocker first."
  exit 1
fi

# --- PLAN.md + plan critic PASS ---
if [[ ! -f "${LATEST}/PLAN.md" ]]; then
  echo "BLOCKED: Task cannot complete without PLAN.md"
  exit 1
fi

if [[ ! -f "${LATEST}/CRITIC__plan.md" ]]; then
  echo "BLOCKED: Task cannot complete without plan critic verdict"
  exit 1
fi

if ! grep -q "PASS" "${LATEST}/CRITIC__plan.md" 2>/dev/null; then
  echo "BLOCKED: Plan critic did not PASS"
  exit 1
fi

# --- Check if repo-mutating ---
IS_MUTATING="true"
if grep -q "mutates_repo: false" "${LATEST}/TASK_STATE.yaml" 2>/dev/null; then
  IS_MUTATING="false"
fi

if [[ "$IS_MUTATING" == "true" ]]; then
  # --- Runtime critic PASS for repo-mutating tasks ---
  if [[ ! -f "${LATEST}/CRITIC__runtime.md" ]]; then
    echo "BLOCKED: Repo-mutating task cannot complete without runtime critic verdict"
    exit 1
  fi

  if ! grep -q "PASS" "${LATEST}/CRITIC__runtime.md" 2>/dev/null; then
    echo "BLOCKED: Runtime critic did not PASS"
    exit 1
  fi

  # --- DOC_SYNC.md for repo-mutating tasks ---
  if [[ ! -f "${LATEST}/DOC_SYNC.md" ]]; then
    echo "BLOCKED: Repo-mutating task cannot complete without DOC_SYNC.md"
    exit 1
  fi
fi

# --- Document critic PASS when docs/notes/indexes changed ---
if [[ -f "${LATEST}/DOC_SYNC.md" ]]; then
  if [[ ! -f "${LATEST}/CRITIC__document.md" ]]; then
    echo "BLOCKED: Task with DOC_SYNC.md cannot complete without document critic verdict"
    exit 1
  fi

  if ! grep -q "PASS" "${LATEST}/CRITIC__document.md" 2>/dev/null; then
    echo "BLOCKED: Document critic did not PASS"
    exit 1
  fi
fi

# --- RESULT.md required ---
if [[ ! -f "${LATEST}/RESULT.md" ]]; then
  echo "BLOCKED: Task cannot complete without RESULT.md"
  exit 1
fi
