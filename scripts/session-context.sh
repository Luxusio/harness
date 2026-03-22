#!/usr/bin/env bash
set -euo pipefail

if [[ -f ".claude-harness/manifest.yaml" ]]; then
  echo "repo-os status: initialized in this repository."
  echo ""

  # === MANIFEST ===
  echo "=== MANIFEST ==="
  if [[ -f ".claude-harness/manifest.yaml" ]]; then
    head -100 ".claude-harness/manifest.yaml"
    MANIFEST_LINES=$(wc -l < ".claude-harness/manifest.yaml")
    if [[ "$MANIFEST_LINES" -gt 100 ]]; then
      echo "... (truncated at 100 lines, read full file for details)"
    fi
  fi
  echo ""

  # === APPROVALS ===
  echo "=== APPROVALS ==="
  if [[ -f ".claude-harness/policies/approvals.yaml" ]]; then
    head -100 ".claude-harness/policies/approvals.yaml"
    APPROVALS_LINES=$(wc -l < ".claude-harness/policies/approvals.yaml")
    if [[ "$APPROVALS_LINES" -gt 100 ]]; then
      echo "... (truncated at 100 lines, read full file for details)"
    fi
  else
    echo "(no approvals policy found)"
  fi
  echo ""

  # === RECENT DECISIONS ===
  echo "=== RECENT DECISIONS ==="
  if [[ -f ".claude-harness/state/recent-decisions.md" ]]; then
    # Show only non-comment content, last 20 entries
    if grep -q '^[^#]' ".claude-harness/state/recent-decisions.md" 2>/dev/null; then
      grep '^[^#]' ".claude-harness/state/recent-decisions.md" | tail -20
    else
      echo "(no decisions recorded yet)"
    fi
  else
    echo "(no recent decisions file)"
  fi
  echo ""

  # === LAST SESSION ===
  echo "=== LAST SESSION ==="
  if [[ -f ".claude-harness/state/last-session-summary.md" ]]; then
    if grep -q '^[^#]' ".claude-harness/state/last-session-summary.md" 2>/dev/null; then
      grep -v '^#' ".claude-harness/state/last-session-summary.md" | grep -v '^\s*$' | grep -v '^<!--' | head -20
    else
      echo "(no session summary recorded)"
    fi
  else
    echo "(no previous session summary)"
  fi
  echo ""

  # === CLAUDE.MD REMINDER ===
  if [[ -f "CLAUDE.md" ]]; then
    echo "CLAUDE.md is present -- follow its instructions for request handling."
  fi

  # === KEY REFERENCES ===
  echo ""
  echo "Additional repo-local sources when relevant:"
  echo "- .claude-harness/router.yaml"
  echo "- .claude-harness/policies/memory-policy.yaml"
  echo "- .claude-harness/state/unknowns.md"
  echo "- docs/index.md"
else
  echo "repo-os status: plugin installed but this repository is not initialized."
  echo "If the user wants repo-local workflows, memory, and routing, suggest /repo-os:setup."
fi
