#!/usr/bin/env bash
set -euo pipefail

# Main verification entry point — runs smoke + healthcheck in sequence.
# Lives in plugin, referenced via ${CLAUDE_PLUGIN_ROOT}/scripts/verify.sh

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
FAILURES=0
TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"

echo "=== HARNESS VERIFY ==="
echo "[EVIDENCE] verify: started at ${TIMESTAMP}"

echo "--- Running smoke tests ---"
SMOKE_OUTPUT=$("${SCRIPT_DIR}/smoke.sh" 2>&1) || SMOKE_EXIT=$?
SMOKE_EXIT="${SMOKE_EXIT:-0}"
if [[ $SMOKE_EXIT -eq 0 ]]; then
  echo "smoke: PASS"
  echo "[EVIDENCE] smoke: PASS — $(echo "$SMOKE_OUTPUT" | tail -1)"
else
  echo "smoke: FAIL"
  echo "[EVIDENCE] smoke: FAIL — exit ${SMOKE_EXIT} — $(echo "$SMOKE_OUTPUT" | tail -3 | tr '\n' ' ')"
  FAILURES=$((FAILURES + 1))
fi

echo "--- Running health checks ---"
HC_OUTPUT=$("${SCRIPT_DIR}/healthcheck.sh" 2>&1) || HC_EXIT=$?
HC_EXIT="${HC_EXIT:-0}"
if [[ $HC_EXIT -eq 0 ]]; then
  echo "healthcheck: PASS"
  echo "[EVIDENCE] healthcheck: PASS — $(echo "$HC_OUTPUT" | tail -1)"
else
  echo "healthcheck: FAIL"
  echo "[EVIDENCE] healthcheck: FAIL — exit ${HC_EXIT} — $(echo "$HC_OUTPUT" | tail -3 | tr '\n' ' ')"
  FAILURES=$((FAILURES + 1))
fi

echo ""
END_TIMESTAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
if [[ $FAILURES -gt 0 ]]; then
  echo "RESULT: ${FAILURES} check(s) failed"
  echo "[EVIDENCE] verify: FAIL — ${FAILURES} check(s) failed at ${END_TIMESTAMP}"
  exit 1
else
  echo "RESULT: all checks passed"
  echo "[EVIDENCE] verify: PASS — all checks passed at ${END_TIMESTAMP}"
  exit 0
fi
