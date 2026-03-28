#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# Database reset and seed.
# Reads reset_command from .claude/harness/manifest.yaml
# Falls back to project-local scripts/harness/reset-db.sh if it exists.

echo "=== DATABASE RESET ==="

# Priority 1: project-local override
if [[ -x "scripts/harness/reset-db.sh" ]]; then
  exec scripts/harness/reset-db.sh
fi

# Priority 2: manifest reset_command
reset_cmd=$(manifest_field "reset_command")
if [[ -n "$reset_cmd" ]]; then
  echo "Running: $reset_cmd"
  eval "$reset_cmd"
  exit $?
fi

echo "SKIP: no database configured"
exit 0
