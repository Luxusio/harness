#!/usr/bin/env bash
set -euo pipefail

# Main verification entry point for harness runtime QA.
# Runs smoke tests, health checks, and persistence checks in sequence.
# Adapt this script to your project's verification needs.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0
FAIL=0
SKIP=0

run_check() {
  local name="$1"
  local script="$2"
  if [[ -x "$script" ]]; then
    echo "--- Running: $name ---"
    if "$script"; then
      echo "  ✓ $name PASSED"
      ((PASS++))
    else
      echo "  ✗ $name FAILED"
      ((FAIL++))
    fi
  else
    echo "  - $name SKIPPED (not found or not executable)"
    ((SKIP++))
  fi
}

echo "=== Harness Verification ==="
echo ""

run_check "Smoke tests" "${SCRIPT_DIR}/smoke.sh"
run_check "Health checks" "${SCRIPT_DIR}/healthcheck.sh"

echo ""
echo "=== Results ==="
echo "PASSED: $PASS | FAILED: $FAIL | SKIPPED: $SKIP"

if [[ "$FAIL" -gt 0 ]]; then
  echo "VERDICT: FAIL"
  exit 1
else
  echo "VERDICT: PASS"
  exit 0
fi
