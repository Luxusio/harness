#!/usr/bin/env bash
set -euo pipefail

if [[ -f "harness/manifest.yaml" ]]; then
  echo "harness status: initialized in this repository."
  echo ""

  # === MANIFEST ===
  echo "=== MANIFEST ==="
  cat "harness/manifest.yaml"
  echo ""

  # === APPROVALS ===
  echo "=== APPROVALS ==="
  if [[ -f "harness/policies/approvals.yaml" ]]; then
    cat "harness/policies/approvals.yaml"
  else
    echo "(no approvals policy found)"
  fi
  echo ""

  # === RECENT DECISIONS ===
  echo "=== RECENT DECISIONS ==="
  if [[ -f "harness/state/recent-decisions.md" ]]; then
    # Show only non-comment content, last 20 entries
    if grep -q '^[^#]' "harness/state/recent-decisions.md" 2>/dev/null; then
      grep '^[^#]' "harness/state/recent-decisions.md" | tail -20
    else
      echo "(no decisions recorded yet)"
    fi
  else
    echo "(no recent decisions file)"
  fi
  echo ""

  # === LAST SESSION ===
  echo "=== LAST SESSION ==="
  if [[ -f "harness/state/last-session-summary.md" ]]; then
    if grep -q '^[^#]' "harness/state/last-session-summary.md" 2>/dev/null; then
      { grep -v '^<!-- ' "harness/state/last-session-summary.md" | grep -v '^\s*$' | head -20; } || true
    else
      echo "(no session summary recorded)"
    fi
  else
    echo "(no previous session summary)"
  fi
  echo ""

  # === CURRENT TASK ===
  if [[ -f "harness/state/current-task.yaml" ]]; then
    TASK_STATUS=$(grep '^status:' "harness/state/current-task.yaml" 2>/dev/null | head -1 | sed 's/status: *"\{0,1\}\([^"]*\)"\{0,1\}/\1/')
    if [[ -n "$TASK_STATUS" && "$TASK_STATUS" != "idle" && "$TASK_STATUS" != "complete" ]]; then
      echo "=== INTERRUPTED TASK ==="
      cat "harness/state/current-task.yaml"
      echo ""
      echo "WARNING: Previous session ended with task status '$TASK_STATUS'. Review and resume or reset."
      echo ""
    fi
  fi

  # === VALIDATION COMMANDS ===
  if [[ -f "CLAUDE.md" ]]; then
    echo "=== VALIDATION COMMANDS ==="
    # Extract the validation commands block from CLAUDE.md (handles both LF and CRLF)
    sed 's/\r$//' "CLAUDE.md" | sed -n '/^## Validation commands/,/^## /{ /^## Validation commands/d; /^## /d; p; }' | head -10
    echo ""
    echo "CLAUDE.md is present -- follow its instructions for request handling."
  fi

  # === KEY REFERENCES ===
  echo ""
  echo "Additional repo-local sources when relevant:"
  echo "- harness/router.yaml"
  echo "- harness/policies/memory-policy.yaml"
  echo "- harness/state/unknowns.md"
  echo "- harness/docs/index.md"
else
  echo "harness status: plugin installed but this repository is not initialized."
  echo "If the user wants repo-local workflows, memory, and routing, suggest /harness:setup."
fi
