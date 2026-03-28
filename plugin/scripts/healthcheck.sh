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
  eval "$hc_cmd"
  exit $?
fi

echo "SKIP: no health checks configured"
exit 0
