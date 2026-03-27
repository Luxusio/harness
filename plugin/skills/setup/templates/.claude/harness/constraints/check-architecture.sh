#!/usr/bin/env bash
set -euo pipefail

# Machine-enforced architecture constraint checker.
# Verifies that import/dependency boundaries defined in architecture.md are respected.
# Only useful when the repo has defined architecture constraints.
#
# This script is optional — only generated when repo shape benefits from machine constraints.

echo "=== Architecture Check ==="

FAIL=0

# Example: Check that domain layer does not import from infra
# Uncomment and adapt to your project:
#
# if grep -r "from.*infra" src/domain/ 2>/dev/null; then
#   echo "  ✗ domain/ imports from infra/ — violates layer boundary"
#   ((FAIL++))
# else
#   echo "  ✓ domain/ does not import from infra/"
# fi
#
# if grep -r "from.*api" src/domain/ 2>/dev/null; then
#   echo "  ✗ domain/ imports from api/ — violates layer boundary"
#   ((FAIL++))
# else
#   echo "  ✓ domain/ does not import from api/"
# fi

echo "  - No architecture constraints configured yet."

if [[ "$FAIL" -gt 0 ]]; then
  echo "VERDICT: FAIL ($FAIL violations)"
  exit 1
else
  echo "VERDICT: PASS"
  exit 0
fi
