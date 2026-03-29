#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# Service health probes.
# Reads healthcheck_command from .claude/harness/manifest.yaml
# Falls back to project-local scripts/harness/healthcheck.sh if it exists.

echo "=== HEALTH CHECKS ==="

# Priority 1: project-local override
if [[ -x "scripts/harness/healthcheck.sh" ]]; then
  exec scripts/harness/healthcheck.sh
fi

# Priority 2: manifest healthcheck_command
hc_cmd=$(manifest_field "healthcheck_command")
if [[ -n "$hc_cmd" ]]; then
  echo "Running: $hc_cmd"
  # Capture HTTP status and response time if command is a curl health probe
  START_TIME=$(date +%s%3N)
  OUTPUT=$(eval "$hc_cmd" 2>&1)
  EXIT_CODE=$?
  END_TIME=$(date +%s%3N)
  ELAPSED=$(( END_TIME - START_TIME ))ms
  echo "$OUTPUT"
  ENDPOINT=$(echo "$hc_cmd" | grep -oE 'https?://[^ ]+' | head -1 || echo "custom")
  if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[EVIDENCE] healthcheck: PASS ${ENDPOINT} exit=0 time=${ELAPSED}"
  else
    echo "[EVIDENCE] healthcheck: FAIL ${ENDPOINT} exit=${EXIT_CODE} time=${ELAPSED} — $(echo "$OUTPUT" | tail -1)"
  fi
  exit $EXIT_CODE
fi

echo "SKIP: no health checks configured"
echo "[EVIDENCE] healthcheck: PASS — skipped (none configured)"
exit 0
