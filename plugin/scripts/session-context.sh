#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "harness/manifest.yaml" ]]; then
  echo "harness status: plugin installed but this repository is not initialized."
  echo "If the user wants repo-local workflows, memory, and routing, suggest /harness:setup."
  exit 0
fi

echo "harness status: initialized in this repository."
echo ""

# === MANIFEST SUMMARY ===
echo "=== MANIFEST SUMMARY ==="
_yaml_field() {
  local file="$1" field="$2"
  grep -m1 "^${field}:" "$file" 2>/dev/null | sed 's/^[^:]*: *//' | tr -d '"' || true
}
MANIFEST="harness/manifest.yaml"
MODE=$(_yaml_field "$MANIFEST" "mode")
TYPE=$(_yaml_field "$MANIFEST" "type")
echo "mode: ${MODE:-unknown}  |  type: ${TYPE:-unknown}"

# languages / frameworks (first 3 values from list fields)
for field in languages frameworks; do
  vals=$(grep -A5 "^${field}:" "$MANIFEST" 2>/dev/null | grep '^\s*-' | head -3 | sed 's/^\s*- *//' | tr '\n' ',' | sed 's/,$//' || true)
  [[ -n "$vals" ]] && echo "${field}: ${vals}"
done

# build/test/lint/dev commands
for field in build test lint dev; do
  val=$(grep -m1 "^\s*${field}:" "$MANIFEST" 2>/dev/null | sed 's/^[^:]*: *//' | tr -d '"' || true)
  [[ -n "$val" ]] && echo "${field}: ${val}"
done

# top 3 key journeys
journeys=$(grep -A20 "^key_journeys:" "$MANIFEST" 2>/dev/null | grep '^\s*-' | head -3 | sed 's/^\s*- */  - /' || true)
[[ -n "$journeys" ]] && { echo "key_journeys (top 3):"; echo "$journeys"; }

# top 3 risk zones
risks=$(grep -A20 "^risk_zones:" "$MANIFEST" 2>/dev/null | grep '^\s*-' | head -3 | sed 's/^\s*- */  - /' || true)
[[ -n "$risks" ]] && { echo "risk_zones (top 3):"; echo "$risks"; }
echo ""

# === APPROVALS SUMMARY ===
echo "=== APPROVALS SUMMARY ==="
APPROVALS="harness/policies/approvals.yaml"
if [[ -f "$APPROVALS" ]]; then
  # Print rule names and first 1-2 paths per rule (bounded)
  awk '
    /^[a-zA-Z_][a-zA-Z0-9_]*:/ { rule=$0; gsub(/:.*/, "", rule); paths=0; print "rule: " rule }
    /paths:/ { in_paths=1; next }
    in_paths && /^\s*-/ {
      if (paths < 2) { gsub(/^\s*- */, "    "); print; paths++ }
      else if (paths == 2) { print "    ..."; paths++ }
    }
    /^[a-zA-Z]/ && !/paths:/ { in_paths=0 }
  ' "$APPROVALS" | head -30
else
  echo "(no approvals policy found)"
fi
echo ""

# === RECENT DECISIONS ===
echo "=== RECENT DECISIONS ==="
if [[ -f "harness/state/recent-decisions.md" ]]; then
  if grep -q '^[^#]' "harness/state/recent-decisions.md" 2>/dev/null; then
    grep '^[^#]' "harness/state/recent-decisions.md" | tail -10
  else
    echo "(no decisions recorded yet)"
  fi
else
  echo "(no recent decisions file)"
fi
echo ""

# === LAST SESSION ===
echo "=== LAST SESSION ==="
if [[ -f "harness/state/last-session-summary.md" ]]; then
  if grep -q '^[^#]' "harness/state/last-session-summary.md" 2>/dev/null; then
    { grep -v '^<!-- ' "harness/state/last-session-summary.md" | grep -v '^\s*$' | head -12; } || true
  else
    echo "(no session summary recorded)"
  fi
else
  echo "(no previous session summary)"
fi
echo ""

# === INTERRUPTED TASK ===
if [[ -f "harness/state/current-task.yaml" ]]; then
  TASK_STATUS=$(grep '^status:' "harness/state/current-task.yaml" 2>/dev/null | head -1 | sed 's/status: *"\{0,1\}\([^"]*\)"\{0,1\}/\1/')
  if [[ -n "$TASK_STATUS" && "$TASK_STATUS" != "idle" && "$TASK_STATUS" != "complete" ]]; then
    echo "=== INTERRUPTED TASK ==="
    # Show key fields only, not the full file
    grep -E '^(id|title|status|goal|validated|started_at):' "harness/state/current-task.yaml" 2>/dev/null || true
    echo ""
    echo "WARNING: Previous session ended with task status '$TASK_STATUS'. Review and resume or reset."
    echo ""
  fi
fi

# === VALIDATION COMMANDS ===
if [[ -f "CLAUDE.md" ]]; then
  echo "=== VALIDATION COMMANDS ==="
  sed 's/\r$//' "CLAUDE.md" | sed -n '/^## Validation commands/,/^## /{ /^## Validation commands/d; /^## /d; p; }' | head -8
  echo ""
  echo "CLAUDE.md is present -- follow its instructions for request handling."
fi

# === ADDITIONAL REFERENCES ===
echo ""
echo "Additional repo-local sources when relevant:"
echo "- harness/router.yaml"
echo "- harness/policies/memory-policy.yaml"
echo "- harness/state/unknowns.md"
echo "- harness/docs/index.md"
