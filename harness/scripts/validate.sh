#!/usr/bin/env bash
set -euo pipefail

# Validate: run project-specific checks after code changes.
# Auto-detects available tools. Override by editing this file.
#
# Usage: harness/scripts/validate.sh [scope]

SCOPE="${1:-all}"
FAILURES=0

echo "=== harness validate ==="
echo "scope: $SCOPE"
echo ""

# 1. Lint / typecheck
echo "SKIP: no lint runner detected"

# 2. Tests
echo "SKIP: no test runner detected"

# 3. Build
echo "SKIP: no build tool detected"

echo ""
if [[ $FAILURES -gt 0 ]]; then
  echo "FAIL: $FAILURES validation step(s) failed."
  exit 1
else
  echo "validate: all detected checks passed."
fi
