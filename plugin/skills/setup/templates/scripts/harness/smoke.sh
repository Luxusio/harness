#!/usr/bin/env bash
set -euo pipefail

# Smoke test runner.
# Adapt to your project type:
#   web app:  curl key routes, check HTTP status codes
#   api:      curl API endpoints, verify response shapes
#   cli:      run example commands, check exit codes and output
#   library:  run test suite or example scripts
#
# {{SMOKE_COMMANDS}}

echo "=== Smoke Tests ==="

# Example: run project test suite if available
if command -v npm &>/dev/null && [[ -f "package.json" ]]; then
  if npm run --silent test 2>/dev/null; then
    echo "  ✓ npm test passed"
  else
    echo "  ✗ npm test failed"
    exit 1
  fi
elif command -v pytest &>/dev/null; then
  if pytest --tb=short -q 2>/dev/null; then
    echo "  ✓ pytest passed"
  else
    echo "  ✗ pytest failed"
    exit 1
  fi
else
  echo "  - No test runner detected. Add smoke checks for your project."
  exit 0
fi
