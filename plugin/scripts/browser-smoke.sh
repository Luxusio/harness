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
for i in $(seq 1 $RETRIES); do
  if curl -sf --max-time 3 "$FRONTEND_URL" > /dev/null 2>&1; then
    echo "  ✓ Dev server is running"
    break
  fi
  if [[ $i -eq $RETRIES ]]; then
    echo "  ✗ Dev server not responding after $RETRIES attempts"
    echo "  Start with: npm run dev (or check manifest for dev_command)"
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
echo "=== Browser Smoke complete ==="
