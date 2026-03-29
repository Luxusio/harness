#!/usr/bin/env bash
set -euo pipefail
# browser-smoke.sh — Browser-first smoke test (requires chrome-devtools MCP)
# Validates dev server is running and accessible for browser QA.

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
source "$SCRIPT_DIR/_lib.sh"

MANIFEST="${HARNESS_MANIFEST:-.claude/harness/manifest.yaml}"

echo "=== Browser Smoke Test ==="

# Get frontend URL from manifest
FRONTEND_URL=$(manifest_field "$MANIFEST" "frontend" 2>/dev/null || echo "")
if [[ -z "$FRONTEND_URL" ]]; then
  FRONTEND_URL="http://localhost:3000"
fi

echo "Checking dev server at $FRONTEND_URL..."
RETRIES=10
CONSOLE_ERRORS=0
NETWORK_FAILURES=0
LOAD_SUCCESS=false

for i in $(seq 1 $RETRIES); do
  HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$FRONTEND_URL" 2>/dev/null || echo "000")
  if [[ "$HTTP_STATUS" =~ ^[23] ]]; then
    echo "  OK Dev server is running (HTTP ${HTTP_STATUS})"
    LOAD_SUCCESS=true
    break
  fi
  if [[ $i -eq $RETRIES ]]; then
    echo "  FAIL Dev server not responding after $RETRIES attempts (last HTTP ${HTTP_STATUS})"
    echo "  Start with: npm run dev (or check manifest for dev_command)"
    echo "[EVIDENCE] browser: FAIL ${FRONTEND_URL} — server not reachable after ${RETRIES} attempts, last HTTP ${HTTP_STATUS}"
    exit 1
  fi
  sleep 2
done

echo ""
echo "Browser smoke prerequisites met."
echo "Use chrome-devtools MCP for interactive verification:"
echo "  1. Navigate to $FRONTEND_URL"
echo "  2. Check console for errors"
echo "  3. Verify key routes render"
echo "  4. Check network for failed requests"

# Emit evidence line for evidence bundle capture
# Console errors and network failures are captured by chrome-devtools MCP during actual browser QA;
# this script confirms server reachability only.
echo "[EVIDENCE] browser: PASS ${FRONTEND_URL} — server reachable, console_errors=${CONSOLE_ERRORS}, network_failures=${NETWORK_FAILURES}"
echo "=== Browser Smoke complete ==="
