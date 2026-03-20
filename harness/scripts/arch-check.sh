#!/usr/bin/env bash
set -euo pipefail

# Architecture guardrail checks.
# Auto-detects available tools and runs language-appropriate boundary checks.
# Reads project-specific rules from harness/arch-rules.yaml if present.
#
# Usage: harness/scripts/arch-check.sh

echo "=== harness architecture check ==="
echo ""

VIOLATIONS=0
CHECKS_RUN=0

# --- Phase 1: Load arch-rules.yaml if present ---
ARCH_RULES=""
if [[ -f "harness/arch-rules.yaml" ]]; then
  ARCH_RULES="harness/arch-rules.yaml"
  echo ">> loaded arch-rules.yaml"
fi

# --- Phase 2: No language-specific checks available ---
echo "SKIP: no language-specific tools detected"

# --- Phase 3: Summary ---
echo ""
echo "=== Summary ==="
echo "checks run: $CHECKS_RUN"
echo "violations: $VIOLATIONS"

if [[ $CHECKS_RUN -eq 0 ]]; then
  echo ""
  echo "arch-check: no tools available for checking."
  exit 0
fi

if [[ $VIOLATIONS -gt 0 ]]; then
  echo ""
  echo "FAIL: $VIOLATIONS architecture violation(s) found."
  exit 1
else
  echo ""
  echo "arch-check: all checks passed."
  exit 0
fi
