#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# Project-specific smoke tests.
# Reads smoke_command from .claude/harness/manifest.yaml
# Falls back to project-local scripts/harness/smoke.sh if it exists.

echo "=== SMOKE TESTS ==="

# Priority 1: project-local override
if [[ -x "scripts/harness/smoke.sh" ]]; then
  exec scripts/harness/smoke.sh
fi

# Priority 2: manifest smoke_command
smoke_cmd=$(manifest_field "smoke_command")
if [[ -n "$smoke_cmd" ]]; then
  echo "Running: $smoke_cmd"
  OUTPUT=$(eval "$smoke_cmd" 2>&1)
  EXIT_CODE=$?
  TAIL=$(echo "$OUTPUT" | tail -20)
  echo "$OUTPUT"
  if [[ $EXIT_CODE -eq 0 ]]; then
    echo "[EVIDENCE] smoke: PASS — exit 0 — last output: $(echo "$TAIL" | tail -1)"
  else
    echo "[EVIDENCE] smoke: FAIL — exit ${EXIT_CODE} — last output: $(echo "$TAIL" | tail -3 | tr '\n' ' ')"
  fi
  exit $EXIT_CODE
fi

echo "SKIP: no smoke tests configured"
echo "Add smoke_command to .claude/harness/manifest.yaml or create scripts/harness/smoke.sh"
echo "[EVIDENCE] smoke: FAIL — no smoke tests configured"
exit 1
