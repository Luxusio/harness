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
  eval "$smoke_cmd"
  exit $?
fi

echo "SKIP: no smoke tests configured"
echo "Add smoke_command to .claude/harness/manifest.yaml or create scripts/harness/smoke.sh"
exit 1
