#!/usr/bin/env bash
set -euo pipefail

# Architecture guardrail checks.
# Auto-detects available tools and runs language-appropriate boundary checks.
# Reads project-specific rules from harness/arch-rules.yaml if present.
# arch-rules.yaml is a FALLBACK for repos without native lint boundary rules.
#
# Usage: harness/scripts/arch-check.sh

echo "=== harness architecture check ==="
echo ""

VIOLATIONS=0
CHECKS_RUN=0

# --- Phase 1: Load arch-rules.yaml if present ---
ARCH_RULES=""
if [[ -f "harness/arch-rules.yaml" ]]; then
  ARCH_RULES="harness/arch-rules.yaml"
  echo ">> loaded arch-rules.yaml"
fi

# --- Phase 2: Language-specific checks ---

# Node.js / TypeScript
if [[ -f "package.json" ]]; then
  echo ""
  echo "--- Node.js/TypeScript ---"

  # Circular dependency check (madge)
  if command -v npx &>/dev/null; then
    SRC_DIR="src"
    [[ -d "src" ]] || SRC_DIR="."
    echo ">> circular dependencies (madge)"
    if npx --yes madge --circular --extensions ts,js,tsx,jsx "$SRC_DIR" 2>/dev/null | grep -q "Circular"; then
      echo "VIOLATION: circular dependencies detected in $SRC_DIR/"
      npx --yes madge --circular --extensions ts,js,tsx,jsx "$SRC_DIR" 2>/dev/null | head -20
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: no circular dependencies"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi

  # ESLint boundary checks (native lint rules take precedence over arch-rules.yaml)
  if command -v npx &>/dev/null && { [[ -f ".eslintrc.js" ]] || [[ -f ".eslintrc.json" ]] || [[ -f ".eslintrc.yml" ]] || [[ -f "eslint.config.js" ]] || [[ -f "eslint.config.mjs" ]] || [[ -f "eslint.config.ts" ]]; }; then
    echo ">> eslint"
    if ! npx eslint . --no-warn-ignored --max-warnings=0 2>/dev/null; then
      echo "VIOLATION: eslint reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: eslint clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi

  # Fallback: arch-rules.yaml boundary checks for TypeScript/JavaScript
  if [[ -n "$ARCH_RULES" ]]; then
    echo ">> arch-rules.yaml boundary checks (Node.js)"
    # Parse boundaries for typescript/javascript and check forbidden imports
    RULE_SOURCE=""
    while IFS= read -r line; do
      if [[ "$line" =~ source:\ *\"(.+)\" ]]; then
        RULE_SOURCE="${BASH_REMATCH[1]}"
        READING_FORBIDDEN=false
      fi
      if [[ "$line" =~ forbidden_imports: ]]; then
        READING_FORBIDDEN=true
        continue
      fi
      if [[ "${READING_FORBIDDEN:-}" == "true" ]]; then
        if [[ "$line" =~ ^\ *-\ *\"(.+)\" ]]; then
          FORBIDDEN="${BASH_REMATCH[1]}"
          if [[ -d "${RULE_SOURCE%%/**}" ]] && grep -r "from ['\"].*${FORBIDDEN}" ${RULE_SOURCE} 2>/dev/null | head -3; then
            echo "VIOLATION: ${RULE_SOURCE} imports from forbidden ${FORBIDDEN}"
            VIOLATIONS=$((VIOLATIONS + 1))
          fi
        else
          READING_FORBIDDEN=false
        fi
      fi
    done < "$ARCH_RULES"
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi
fi

# Python
if [[ -f "pyproject.toml" ]] || [[ -f "setup.py" ]] || [[ -f "setup.cfg" ]] || [[ -f "requirements.txt" ]]; then
  echo ""
  echo "--- Python ---"

  # Ruff (native lint takes precedence)
  if command -v ruff &>/dev/null; then
    echo ">> ruff check"
    if ! ruff check . --select I 2>/dev/null; then
      echo "VIOLATION: ruff import check reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: ruff import checks clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
  else
    echo "SKIP: ruff not available"
  fi

  # Fallback: arch-rules.yaml boundary checks for Python
  if [[ -n "$ARCH_RULES" ]]; then
    echo ">> arch-rules.yaml boundary checks (Python)"
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi
fi

# Go
if [[ -f "go.mod" ]]; then
  echo ""
  echo "--- Go ---"

  if command -v go &>/dev/null; then
    echo ">> go vet"
    if ! go vet ./... 2>/dev/null; then
      echo "VIOLATION: go vet reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: go vet clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
  else
    echo "SKIP: go not available"
  fi

  # Fallback: arch-rules.yaml boundary checks for Go
  if [[ -n "$ARCH_RULES" ]]; then
    echo ">> arch-rules.yaml boundary checks (Go)"
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi
fi

# Rust
if [[ -f "Cargo.toml" ]]; then
  echo ""
  echo "--- Rust ---"

  if command -v cargo &>/dev/null; then
    echo ">> cargo clippy"
    if ! cargo clippy -- -D warnings 2>/dev/null; then
      echo "VIOLATION: cargo clippy reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: cargo clippy clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
  else
    echo "SKIP: cargo not available"
  fi

  # Fallback: arch-rules.yaml boundary checks for Rust
  if [[ -n "$ARCH_RULES" ]]; then
    echo ">> arch-rules.yaml boundary checks (Rust)"
    CHECKS_RUN=$((CHECKS_RUN + 1))
  fi
fi

# Java / Kotlin
if [[ -x "gradlew" ]] && { [[ -f "build.gradle" ]] || [[ -f "build.gradle.kts" ]]; }; then
  echo ""
  echo "--- Java/Kotlin ---"

  # Probe available tasks once
  GRADLE_TASKS=$(./gradlew tasks --all 2>/dev/null || true)
  JVM_CHECKS=0

  # Detekt (Kotlin static analysis)
  if echo "$GRADLE_TASKS" | grep -q "^detekt "; then
    echo ">> detekt"
    if ! ./gradlew detekt 2>/dev/null; then
      echo "VIOLATION: detekt reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: detekt clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
    JVM_CHECKS=$((JVM_CHECKS + 1))
  fi

  # Checkstyle (Java static analysis)
  if echo "$GRADLE_TASKS" | grep -q "^checkstyleMain "; then
    echo ">> checkstyle"
    if ! ./gradlew checkstyleMain 2>/dev/null; then
      echo "VIOLATION: checkstyle reported errors"
      VIOLATIONS=$((VIOLATIONS + 1))
    else
      echo "   pass: checkstyle clean"
    fi
    CHECKS_RUN=$((CHECKS_RUN + 1))
    JVM_CHECKS=$((JVM_CHECKS + 1))
  fi

  if [[ $JVM_CHECKS -eq 0 ]]; then
    echo "SKIP: no detekt or checkstyle tasks configured"
  fi
elif [[ -f "pom.xml" ]] && command -v mvn &>/dev/null; then
  echo ""
  echo "--- Java (Maven) ---"
  echo "SKIP: no architecture checks available for Maven projects"
fi

# --- Phase 3: Summary ---
echo ""
echo "=== Summary ==="
echo "checks run: $CHECKS_RUN"
echo "violations: $VIOLATIONS"

if [[ $CHECKS_RUN -eq 0 ]]; then
  echo ""
  echo "arch-check: no tools available for checking. Install eslint, madge, ruff, cargo, or go for boundary enforcement."
  exit 0
fi

if [[ $VIOLATIONS -gt 0 ]]; then
  echo ""
  echo "FAIL: $VIOLATIONS architecture violation(s) found."
  exit 1
else
  echo ""
  echo "arch-check: all checks passed."
  exit 0
fi
