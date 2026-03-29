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
  raw=$(grep "^[[:space:]]*${field}:" "$file" 2>/dev/null | head -1 | sed "s/.*${field}: *//")
  # Strip surrounding single or double quotes
  raw="${raw#\"}" ; raw="${raw%\"}"
  raw="${raw#\'}" ; raw="${raw%\'}"
  echo "$raw"
}

# Parse a field from any YAML file, stripping surrounding quotes.
# Usage: value=$(yaml_field "field_name" "/path/to/file.yaml")
yaml_field() {
  local field="$1"
  local file="$2"
  [[ -z "$file" || ! -f "$file" ]] && return
  local raw
  raw=$(grep "^[[:space:]]*${field}:" "$file" 2>/dev/null | head -1 | sed "s/.*${field}: *//")
  raw="${raw#\"}" ; raw="${raw%\"}"
  raw="${raw#\'}" ; raw="${raw%\'}"
  echo "$raw"
}

# Parse YAML array values into newline-separated output.
# Handles both inline arrays (field: [a, b]) and block sequences (- item).
# Returns empty string for empty arrays [].
# Usage: items=$(yaml_array "touched_paths" "/path/to/file.yaml")
yaml_array() {
  local field="$1"
  local file="$2"
  [[ -z "$file" || ! -f "$file" ]] && return
  # Try inline array first: field: [a, b, c]
  local line
  line=$(grep "^[[:space:]]*${field}:" "$file" 2>/dev/null | head -1 || true)
  if [[ -n "$line" ]]; then
    local inline
    inline=$(echo "$line" | grep -oE '\[[^]]*\]' | sed 's/^\[//;s/\]$//' | tr ',' '\n' | sed 's/^[[:space:]]*"//;s/"[[:space:]]*$//;s/^[[:space:]]*//;s/[[:space:]]*$//' | grep -v '^$' 2>/dev/null || true)
    # If we found the field line (even if array is empty []), return whatever we parsed
    echo "$inline"
    return
  fi
  # Fall back to block sequence: lines starting with "  - " after the field
  awk "/^[[:space:]]*${field}:/{found=1; next} found && /^[[:space:]]*-[[:space:]]/{gsub(/^[[:space:]]*-[[:space:]]*/,\"\"); print; next} found && /^[^[:space:]-]/{exit}" "$file" 2>/dev/null | sed 's/^"//;s/"$//' | grep -v '^$' || true
}

# Read a field from a task's TASK_STATE.yaml.
# Usage: value=$(task_state_field "status" ".claude/harness/tasks/TASK__foo")
task_state_field() {
  local field="$1"
  local task_dir="$2"
  yaml_field "$field" "${task_dir}/TASK_STATE.yaml"
}

# Check if a task's touched_paths, roots_touched, or verification_targets overlaps with a given file path.
# Returns 0 (true) if there is overlap or if touched_paths is empty (conservative).
# Usage: task_touches_path ".claude/harness/tasks/TASK__foo" "src/foo.ts"
task_touches_path() {
  local task_dir="$1"
  local changed_file="$2"
  local state_file="${task_dir}/TASK_STATE.yaml"
  [[ ! -f "$state_file" ]] && return 0

  local touched roots vt
  touched=$(yaml_array "touched_paths" "$state_file")
  roots=$(yaml_array "roots_touched" "$state_file")
  vt=$(yaml_array "verification_targets" "$state_file")

  # If all lists are empty, conservatively treat as touching everything
  if [[ -z "$touched" && -z "$roots" && -z "$vt" ]]; then
    return 0
  fi

  # Check verification_targets for exact or prefix match
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if [[ "$changed_file" == "$path" || "$changed_file" == "$path"/* ]]; then
      return 0
    fi
  done <<< "$vt"

  # Check touched_paths for exact or prefix match
  while IFS= read -r path; do
    [[ -z "$path" ]] && continue
    if [[ "$changed_file" == "$path" || "$changed_file" == "$path"/* ]]; then
      return 0
    fi
  done <<< "$touched"

  # Check roots_touched for prefix match
  while IFS= read -r root; do
    [[ -z "$root" ]] && continue
    if [[ "$changed_file" == "$root" || "$changed_file" == "$root"/* ]]; then
      return 0
    fi
  done <<< "$roots"

  return 1
}

# Find all open tasks whose touched_paths/roots_touched overlap with a given file.
# Prints task directory paths (one per line).
# Usage: find_tasks_touching_path "src/foo.ts"
find_tasks_touching_path() {
  local changed_file="$1"
  [[ ! -d "$TASK_DIR" ]] && return

  for task in "$TASK_DIR"/TASK__*/; do
    [[ ! -d "$task" ]] && continue
    local state_file="${task}TASK_STATE.yaml"
    [[ ! -f "$state_file" ]] && continue

    local status
    status=$(yaml_field "status" "$state_file")
    case "$status" in
      closed|archived|stale) continue ;;
    esac

    if task_touches_path "$task" "$changed_file"; then
      echo "$task"
    fi
  done
}

# Derive the first path segment (root) from a file path.
# Usage: root=$(path_root "src/foo/bar.ts")  → "src"
path_root() {
  echo "${1%%/*}"
}

# Derive unique directory roots (first path segment) from a newline-separated list of paths.
# Usage: roots=$(extract_roots "$touched_paths_newline_separated")
extract_roots() {
  local paths="$1"
  while IFS= read -r p; do
    [[ -z "$p" ]] && continue
    path_root "$p"
  done <<< "$paths" | sort -u
}

# Classify a path as a doc path vs a runtime path.
# Doc paths: doc/*, docs/*, *.md, README*, CHANGELOG*, LICENSE*,
#             .claude/harness/critics/*, DOC_SYNC.md
# Returns 0 (true) if the path is a doc path, 1 otherwise.
# Usage: if is_doc_path "docs/guide.md"; then ...
is_doc_path() {
  local p="$1"
  case "$p" in
    doc/*|docs/*|*.md|README*|CHANGELOG*|LICENSE*|.claude/harness/critics/*|DOC_SYNC.md)
      return 0 ;;
    *)
      return 1 ;;
  esac
}

# Find all open tasks whose verification_targets overlap with a given file.
# Falls back to touched_paths/roots_touched if verification_targets is empty.
# Prints task directory paths (one per line).
# Usage: find_tasks_with_verification_targets "src/foo.ts"
find_tasks_with_verification_targets() {
  local changed_file="$1"
  [[ ! -d "$TASK_DIR" ]] && return

  for task in "$TASK_DIR"/TASK__*/; do
    [[ ! -d "$task" ]] && continue
    local state_file="${task}TASK_STATE.yaml"
    [[ ! -f "$state_file" ]] && continue

    local status
    status=$(yaml_field "status" "$state_file")
    case "$status" in
      closed|archived|stale) continue ;;
    esac

    local vt
    vt=$(yaml_array "verification_targets" "$state_file")

    if [[ -n "$vt" ]]; then
      # Check verification_targets for exact or prefix match
      local matched=false
      while IFS= read -r path; do
        [[ -z "$path" ]] && continue
        if [[ "$changed_file" == "$path" || "$changed_file" == "$path"/* ]]; then
          matched=true
          break
        fi
      done <<< "$vt"
      if [[ "$matched" == "true" ]]; then
        echo "$task"
      fi
    else
      # Fall back to touched_paths/roots_touched check
      if task_touches_path "$task" "$changed_file"; then
        echo "$task"
      fi
    fi
  done
}

# Check if the current project is browser-first (manifest has browser.enabled: true).
# Returns 0 if browser-first, 1 otherwise.
is_browser_first_project() {
  [[ ! -f "$MANIFEST" ]] && return 1
  # Check browser.enabled: true
  if grep -qE "^\s*enabled\s*:\s*true" "$MANIFEST" 2>/dev/null; then
    # Verify it's under the browser section
    if awk '/^browser:/{found=1} found && /enabled:/{print; exit}' "$MANIFEST" 2>/dev/null | grep -qE "enabled\s*:\s*true"; then
      return 0
    fi
  fi
  # Also check qa.browser_qa_supported: true
  if awk '/^qa:/{found=1} found && /browser_qa_supported:/{print; exit}' "$MANIFEST" 2>/dev/null | grep -qE "browser_qa_supported\s*:\s*true"; then
    return 0
  fi
  return 1
}
