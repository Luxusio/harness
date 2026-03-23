#!/usr/bin/env bash
set -euo pipefail

# Smoke test: verify key behaviors still work after changes.
# Uses harness/manifest.yaml commands first. Falls back to tool auto-detection only when a manifest command is empty.
#
# Usage: harness/scripts/smoke.sh [scope]
# Note: scope filtering is reserved for future use; currently runs all checks.

SCOPE="${1:-all}"
MANIFEST="harness/manifest.yaml"

echo "=== harness smoke test ==="
echo "scope: $SCOPE"
echo ""

# ── helper: extract a command from manifest commands section ──────────────
manifest_command() {
  local key="$1"
  if [[ ! -f "$MANIFEST" ]]; then return; fi
  awk -v k="$key" '
    /^[a-zA-Z]/ { in_cmd = ($0 ~ /^commands:/) }
    in_cmd && $0 ~ "^  "k":" {
      val = $0; sub(/^[^:]*: */, "", val); gsub(/"/, "", val); sub(/ *#.*/, "", val)
      gsub(/^[ \t]+|[ \t]+$/, "", val)
      if (val != "") print val
      exit
    }
  ' "$MANIFEST"
}

# 1. Build check
BUILD_CMD=$(manifest_command "build")
if [[ -n "$BUILD_CMD" ]]; then
  echo ">> build (manifest)"
  eval "$BUILD_CMD" || { echo "FAIL: build"; exit 1; }
elif [[ -f "package.json" ]] && grep -q '"build"' package.json 2>/dev/null; then
  echo ">> build (npm)"
  npm run build || { echo "FAIL: build"; exit 1; }
elif [[ -f "Cargo.toml" ]]; then
  echo ">> build (cargo)"
  cargo build || { echo "FAIL: build"; exit 1; }
elif [[ -f "go.mod" ]]; then
  echo ">> build (go)"
  go build ./... || { echo "FAIL: build"; exit 1; }
elif [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  echo ">> build (gradle)"
  ./gradlew build -x test || { echo "FAIL: build"; exit 1; }
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo ">> build (maven)"
  mvn compile -q || { echo "FAIL: build"; exit 1; }
else
  echo "SKIP: no build tool detected"
fi

# 2. Core test suite
TEST_CMD=$(manifest_command "test")
if [[ -n "$TEST_CMD" ]]; then
  echo ">> tests (manifest)"
  eval "$TEST_CMD" || { echo "FAIL: tests"; exit 1; }
elif [[ -f "package.json" ]] && grep -q '"test"' package.json 2>/dev/null; then
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
elif [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  echo ">> tests (gradle)"
  ./gradlew test || { echo "FAIL: tests"; exit 1; }
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo ">> tests (maven)"
  mvn test -q || { echo "FAIL: tests"; exit 1; }
else
  echo "SKIP: no test runner detected"
fi

echo ""
echo "smoke: all detected checks passed."
