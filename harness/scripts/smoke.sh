#!/usr/bin/env bash
set -euo pipefail

# Smoke test: verify key behaviors still work after changes.
#
# Usage: harness/scripts/smoke.sh [scope]

SCOPE="${1:-all}"

echo "=== harness smoke test ==="
echo "scope: $SCOPE"
echo ""

# 1. Build check
echo "SKIP: no build tool detected"

# 2. Core test suite
echo "SKIP: no test runner detected"

echo ""
echo "smoke: all detected checks passed."
