#!/usr/bin/env bash
set -euo pipefail

# Health check probe.
# Verifies that key services are reachable and responding.
# Adapt endpoints and ports to your project.
#
# {{HEALTHCHECK_ENDPOINTS}}

echo "=== Health Checks ==="

check_url() {
  local name="$1"
  local url="$2"
  local timeout="${3:-5}"
  if curl -sf --max-time "$timeout" "$url" >/dev/null 2>&1; then
    echo "  ✓ $name ($url) is healthy"
    return 0
  else
    echo "  ✗ $name ($url) is not reachable"
    return 1
  fi
}

# Example health checks — uncomment and adapt:
# check_url "web server" "http://localhost:3000/health"
# check_url "api server" "http://localhost:3001/health"
# check_url "database"   "http://localhost:5432" 2

echo "  - No health checks configured yet. Add endpoints for your project."
exit 0
