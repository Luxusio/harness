#!/usr/bin/env bash
set -euo pipefail

# Validate: run project-specific checks after code changes.
# Uses harness/manifest.yaml commands first. Falls back to tool auto-detection only when a manifest command is empty.
#
# Usage: harness/scripts/validate.sh [scope]
# Note: scope filtering is reserved for future use; currently runs all checks.

SCOPE="${1:-all}"
FAILURES=0
MANIFEST="harness/manifest.yaml"

echo "=== harness validate ==="
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
      # trim whitespace
      gsub(/^[ \t]+|[ \t]+$/, "", val)
      if (val != "") print val
      exit
    }
  ' "$MANIFEST"
}

# ── helper: run manifest command or fallback ──────────────────────────────
# Usage: run_step <label> <manifest_key> <fallback_function>
run_step() {
  local label="$1" key="$2" fallback="$3"
  local cmd
  cmd=$(manifest_command "$key")
  if [[ -n "$cmd" ]]; then
    echo ">> $label (manifest)"
    eval "$cmd" || { echo "FAIL: $label"; FAILURES=$((FAILURES + 1)); }
  else
    "$fallback"
  fi
}

# ── fallback detectors ───────────────────────────────────────────────────

fallback_lint() {
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
}

fallback_build() {
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
}

fallback_test() {
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
}

# ── run steps ─────────────────────────────────────────────────────────────

run_step "lint"   "lint"  fallback_lint
run_step "build"  "build" fallback_build
run_step "tests"  "test"  fallback_test

echo ""
if [[ $FAILURES -gt 0 ]]; then
  echo "FAIL: $FAILURES validation step(s) failed."
  exit 1
else
  echo "validate: all detected checks passed."
fi
