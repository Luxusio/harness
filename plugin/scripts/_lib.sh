#!/usr/bin/env bash
# Shared helper for harness hook scripts.
# Source this at the top of each script: source "$(dirname "$0")/_lib.sh"

# Read stdin JSON into HOOK_INPUT (only for hook scripts, not QA scripts).
# Set HARNESS_SKIP_STDIN=1 before sourcing to skip stdin reading.
HOOK_INPUT=""
if [[ -z "${HARNESS_SKIP_STDIN:-}" ]] && [[ ! -t 0 ]]; then
  # Read with timeout to avoid blocking QA scripts
  HOOK_INPUT=$(timeout 1 cat 2>/dev/null || true)
fi

# Parse a string field from JSON without jq dependency.
# Usage: value=$(json_field "task_id")
json_field() {
  local field="$1"
  local input="${2:-$HOOK_INPUT}"
  if [[ -z "$input" ]]; then
    return
  fi
  if command -v jq &>/dev/null; then
    jq -r ".${field} // empty" <<<"$input" 2>/dev/null || true
  else
    # Fallback: grep for "field": "value" or "field":"value"
    echo "$input" | grep -oE "\"${field}\"[[:space:]]*:[[:space:]]*\"[^\"]*\"" | head -1 | sed "s/.*\"${field}\"[[:space:]]*:[[:space:]]*\"\([^\"]*\)\".*/\1/" 2>/dev/null || true
  fi
}

# Parse a JSON array of strings into newline-separated output.
# Usage: files=$(json_array "files")
json_array() {
  local field="$1"
  local input="${2:-$HOOK_INPUT}"
  if [[ -z "$input" ]]; then
    return
  fi
  if command -v jq &>/dev/null; then
    jq -r ".${field} // [] | .[]" <<<"$input" 2>/dev/null || true
  else
    # Fallback: extract array contents, split by comma, strip quotes
    echo "$input" | grep -oE "\"${field}\"[[:space:]]*:[[:space:]]*\[[^]]*\]" | head -1 | sed 's/.*\[//;s/\].*//;s/,/\n/g' | sed 's/^[[:space:]]*"//;s/"[[:space:]]*$//' | grep -v '^$' 2>/dev/null || true
  fi
}

TASK_DIR=".claude/harness/tasks"
MANIFEST=".claude/harness/manifest.yaml"

# Parse a YAML value from manifest, stripping surrounding quotes.
# Usage: value=$(manifest_field "smoke_command")
manifest_field() {
  local field="$1"
  local file="${2:-$MANIFEST}"
  [[ ! -f "$file" ]] && return
  local raw
  raw=$(grep "^  *${field}:" "$file" 2>/dev/null | head -1 | sed "s/.*${field}: *//")
  # Strip surrounding single or double quotes
  raw="${raw#\"}" ; raw="${raw%\"}"
  raw="${raw#\'}" ; raw="${raw%\'}"
  echo "$raw"
}
