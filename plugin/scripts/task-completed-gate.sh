#!/usr/bin/env bash
set -euo pipefail

# Completion firewall: reject task completion unless required verdicts are present.
# Only uses explicit HARNESS_TASK_ID — no fallback to latest task.
TASK_DIR=".claude/harness/tasks"

if [[ -z "${HARNESS_TASK_ID:-}" ]]; then
  exit 0
fi

TARGET="${TASK_DIR}/${HARNESS_TASK_ID}"

if [[ ! -d "$TARGET" ]]; then
  exit 0
fi

TASK_ID="$HARNESS_TASK_ID"

# --- TASK_STATE.yaml required ---
if [[ ! -f "${TARGET}/TASK_STATE.yaml" ]]; then
  echo "BLOCKED: ${TASK_ID} — missing TASK_STATE.yaml"
  exit 1
fi

# --- Reject blocked_env ---
if grep -q "^status: blocked_env" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
  echo "BLOCKED: ${TASK_ID} — status is blocked_env. Resolve the blocker first."
  exit 1
fi

# --- PLAN.md required ---
if [[ ! -f "${TARGET}/PLAN.md" ]]; then
  echo "BLOCKED: ${TASK_ID} — missing PLAN.md"
  exit 1
fi

# --- CRITIC__plan.md with exact verdict: PASS ---
if [[ ! -f "${TARGET}/CRITIC__plan.md" ]]; then
  echo "BLOCKED: ${TASK_ID} — missing plan critic verdict"
  exit 1
fi
if ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__plan.md" 2>/dev/null; then
  echo "BLOCKED: ${TASK_ID} — plan critic did not PASS"
  exit 1
fi

# --- HANDOFF.md required ---
if [[ ! -f "${TARGET}/HANDOFF.md" ]]; then
  echo "BLOCKED: ${TASK_ID} — missing HANDOFF.md"
  exit 1
fi

# --- Runtime critic for repo-mutating tasks ---
IS_MUTATING="true"
if grep -q "^mutates_repo: false" "${TARGET}/TASK_STATE.yaml" 2>/dev/null; then
  IS_MUTATING="false"
fi

if [[ "$IS_MUTATING" == "true" ]]; then
  if [[ ! -f "${TARGET}/CRITIC__runtime.md" ]]; then
    echo "BLOCKED: ${TASK_ID} — repo-mutating task needs runtime critic verdict"
    exit 1
  fi
  if ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__runtime.md" 2>/dev/null; then
    echo "BLOCKED: ${TASK_ID} — runtime critic did not PASS"
    exit 1
  fi
fi

# --- Document critic only when doc/ or CLAUDE.md actually changed ---
if git diff --cached --name-only 2>/dev/null | grep -qE '(^doc/|CLAUDE\.md)'; then
  DOCS_CHANGED="true"
elif git diff --name-only HEAD 2>/dev/null | grep -qE '(^doc/|CLAUDE\.md)'; then
  DOCS_CHANGED="true"
else
  DOCS_CHANGED="false"
fi

if [[ "$DOCS_CHANGED" == "true" ]]; then
  if [[ ! -f "${TARGET}/CRITIC__document.md" ]]; then
    echo "BLOCKED: ${TASK_ID} — docs changed, needs document critic verdict"
    exit 1
  fi
  if ! grep -qE '^verdict:\s*PASS\s*$' "${TARGET}/CRITIC__document.md" 2>/dev/null; then
    echo "BLOCKED: ${TASK_ID} — document critic did not PASS"
    exit 1
  fi
fi
