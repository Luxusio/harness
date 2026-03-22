#!/usr/bin/env bash
set -euo pipefail

# Validate: run project-specific checks after code changes.
# Auto-detects available tools. Override by editing this file.
#
# Usage: harness/scripts/validate.sh [scope]
# Note: scope filtering is reserved for future use; currently runs all checks.

SCOPE="${1:-all}"
FAILURES=0

echo "=== harness validate ==="
echo "scope: $SCOPE"
echo ""

# 1. Lint / typecheck
if [[ -f "package.json" ]] && grep -q '"lint"' package.json 2>/dev/null; then
  echo ">> lint (npm)"
  npm run lint || { echo "FAIL: lint"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "pyproject.toml" ]] && command -v ruff &>/dev/null; then
  echo ">> lint (ruff)"
  ruff check . || { echo "FAIL: lint"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "Cargo.toml" ]] && command -v cargo &>/dev/null; then
  echo ">> lint (clippy)"
  cargo clippy -- -D warnings || { echo "FAIL: lint"; FAILURES=$((FAILURES + 1)); }
elif [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  GRADLE_TASKS=$(./gradlew tasks --all 2>/dev/null || true)
  if echo "$GRADLE_TASKS" | grep -q "^detekt "; then
    echo ">> lint (detekt)"
    ./gradlew detekt || { echo "FAIL: detekt"; FAILURES=$((FAILURES + 1)); }
  elif echo "$GRADLE_TASKS" | grep -q "^checkstyleMain "; then
    echo ">> lint (checkstyle)"
    ./gradlew checkstyleMain || { echo "FAIL: checkstyle"; FAILURES=$((FAILURES + 1)); }
  else
    echo "SKIP: no detekt or checkstyle tasks configured"
  fi
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo "SKIP: no lint runner detected for Maven"
else
  echo "SKIP: no lint runner detected"
fi

# 2. Build
if [[ -f "package.json" ]] && grep -q '"build"' package.json 2>/dev/null; then
  echo ">> build (npm)"
  npm run build || { echo "FAIL: build"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "Cargo.toml" ]]; then
  echo ">> build (cargo)"
  cargo build || { echo "FAIL: build"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "go.mod" ]]; then
  echo ">> build (go)"
  go build ./... || { echo "FAIL: build"; FAILURES=$((FAILURES + 1)); }
elif [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  echo ">> build (gradle)"
  ./gradlew build -x test || { echo "FAIL: build"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo ">> build (maven)"
  mvn compile -q || { echo "FAIL: build"; FAILURES=$((FAILURES + 1)); }
else
  echo "SKIP: no build tool detected"
fi

# 3. Tests
if [[ -f "package.json" ]] && grep -q '"test"' package.json 2>/dev/null; then
  echo ">> tests (npm)"
  npm test || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "pyproject.toml" ]] || [[ -f "pytest.ini" ]] || [[ -f "setup.cfg" ]]; then
  echo ">> tests (pytest)"
  pytest || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "Cargo.toml" ]]; then
  echo ">> tests (cargo)"
  cargo test || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "go.mod" ]]; then
  echo ">> tests (go)"
  go test ./... || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
elif [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  echo ">> tests (gradle)"
  ./gradlew test || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo ">> tests (maven)"
  mvn test -q || { echo "FAIL: tests"; FAILURES=$((FAILURES + 1)); }
else
  echo "SKIP: no test runner detected"
fi

echo ""
if [[ $FAILURES -gt 0 ]]; then
  echo "FAIL: $FAILURES validation step(s) failed."
  exit 1
else
  echo "validate: all detected checks passed."
fi
