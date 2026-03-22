#!/usr/bin/env bash
set -euo pipefail

# Smoke test: verify key behaviors still work after changes.
# Auto-detects available tools. Override by editing this file.
#
# Usage: scripts/agent/smoke.sh [scope]

SCOPE="${1:-all}"

echo "=== repo-os smoke test ==="
echo "scope: $SCOPE"
echo ""

# 1. Build check
if [[ -f "package.json" ]] && grep -q '"build"' package.json 2>/dev/null; then
  echo ">> build (npm)"
  npm run build || { echo "FAIL: build"; exit 1; }
elif [[ -f "Cargo.toml" ]]; then
  echo ">> build (cargo)"
  cargo build || { echo "FAIL: build"; exit 1; }
elif [[ -f "go.mod" ]]; then
  echo ">> build (go)"
  go build ./... || { echo "FAIL: build"; exit 1; }
else
  echo "SKIP: no build tool detected"
fi

# 2. Core test suite
if [[ -f "package.json" ]] && grep -q '"test"' package.json 2>/dev/null; then
  echo ">> tests (npm)"
  npm test || { echo "FAIL: tests"; exit 1; }
elif command -v pytest &>/dev/null && { [[ -f "pyproject.toml" ]] || [[ -f "pytest.ini" ]]; }; then
  echo ">> tests (pytest)"
  pytest --tb=short -q || { echo "FAIL: tests"; exit 1; }
elif [[ -f "Cargo.toml" ]]; then
  echo ">> tests (cargo)"
  cargo test || { echo "FAIL: tests"; exit 1; }
elif [[ -f "go.mod" ]]; then
  echo ">> tests (go)"
  go test ./... || { echo "FAIL: tests"; exit 1; }
else
  echo "SKIP: no test runner detected"
fi

echo ""
echo "smoke: all detected checks passed."
