#!/usr/bin/env bash
set -euo pipefail

# Main verification entry point — runs smoke + healthcheck in sequence.
# Lives in plugin, referenced via ${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FAILURES=0

echo "=== HARNESS VERIFY ==="

echo "--- Running smoke tests ---"
if "${SCRIPT_DIR}/smoke.sh"; then
  echo "smoke: PASS"
else
  echo "smoke: FAIL"
  FAILURES=$((FAILURES + 1))
fi

echo "--- Running health checks ---"
if "${SCRIPT_DIR}/healthcheck.sh"; then
  echo "healthcheck: PASS"
else
  echo "healthcheck: FAIL"
  FAILURES=$((FAILURES + 1))
fi

echo ""
if [[ $FAILURES -gt 0 ]]; then
  echo "RESULT: ${FAILURES} check(s) failed"
  exit 1
else
  echo "RESULT: all checks passed"
  exit 0
fi
