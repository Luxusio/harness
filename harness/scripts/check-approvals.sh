#!/usr/bin/env bash
# check-approvals.sh <action> <path1> [path2 ...]
#
# Reads harness/policies/approvals.yaml and evaluates whether the given
# action + paths require explicit approval.
#
# Exit codes:
#   0  — normal (decision is either "ask" or "auto")
#   1  — usage error or parse error
#
# Output format (stdout):
#   decision: ask
#   matched_rules:
#     - kind_name — reason text
#
#   or:
#
#   decision: auto
#   matched_rules:
#     (none)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Resolve repo root: this script lives at <repo>/harness/scripts/
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
APPROVALS_FILE="${REPO_ROOT}/harness/policies/approvals.yaml"

# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------
if [[ $# -lt 2 ]]; then
  echo "Usage: $(basename "$0") <action> <path1> [path2 ...]" >&2
  exit 1
fi

ACTION="$1"
shift
INPUT_PATHS=("$@")
INPUT_COUNT="${#INPUT_PATHS[@]}"

if [[ ! -f "${APPROVALS_FILE}" ]]; then
  echo "Error: approvals file not found: ${APPROVALS_FILE}" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Glob matching (pure shell, no extglob dependency)
# Converts a yaml glob pattern to a shell case pattern and tests the path.
# Supports: * ** ? and literal characters.
# ---------------------------------------------------------------------------
glob_match() {
  local pattern="$1"
  local subject="$2"

  # Normalise: strip leading ./
  subject="${subject#./}"
  pattern="${pattern#./}"

  # Use bash case for matching; ** needs special handling.
  # We convert ** to a placeholder that case can handle via *
  # Strategy: replace ** with the multi-segment wildcard understood by bash
  # extglob would be cleaner but we keep it dependency-free.
  # Simple approach: test with case after converting ** -> *
  local shell_pattern
  shell_pattern="${pattern//\*\*/*}"

  # shellcheck disable=SC2254
  case "${subject}" in
    ${shell_pattern}) return 0 ;;
  esac
  return 1
}

# ---------------------------------------------------------------------------
# Check whether ACTION is in a whitespace/comma-separated list string.
# The YAML list items are parsed one per line by the awk extractor below.
# ---------------------------------------------------------------------------
action_in_list() {
  local needle="$1"
  local haystack="$2"   # newline-separated items
  while IFS= read -r item; do
    item="$(echo "${item}" | sed 's/^[[:space:]]*//' | sed 's/[[:space:]]*$//')"
    [[ "${item}" == "${needle}" ]] && return 0
  done <<< "${haystack}"
  return 1
}

# ---------------------------------------------------------------------------
# Parse approvals.yaml with awk.
#
# We parse the always_ask_before list.  Each rule block looks like:
#
#   - kind: <name>
#     paths:           (optional)
#       - "glob1"
#       - "glob2"
#     actions:         (optional)
#       - delete
#       - move
#     min_files: N     (optional)
#     reason: "text"
#
# We emit structured records separated by sentinel lines so the shell
# can process them without jq/yq/python.
#
# Output format per rule (one rule per block between ---RULE--- sentinels):
#   kind:<name>
#   reason:<text>
#   path:<glob>          (0 or more)
#   action:<value>       (0 or more)
#   min_files:<N>        (0 or 1)
# ---------------------------------------------------------------------------
parse_rules() {
  awk '
  BEGIN { in_ask=0; in_rule=0; in_paths=0; in_actions=0 }

  # Detect start of always_ask_before block
  /^always_ask_before:/ { in_ask=1; next }

  # Detect end of top-level block (another top-level key)
  in_ask && /^[a-zA-Z_][a-zA-Z_0-9]*:/ && !/^  / && !/^-/ {
    if (in_rule) { print "---RULE---" }
    in_ask=0; in_rule=0; in_paths=0; in_actions=0; next
  }

  !in_ask { next }

  # New rule entry
  /^  - kind:/ {
    if (in_rule) { print "---RULE---" }
    in_rule=1; in_paths=0; in_actions=0
    val=$0; sub(/^  - kind:[[:space:]]*/, "", val); gsub(/"/, "", val)
    print "kind:" val
    next
  }

  !in_rule { next }

  # reason
  /^    reason:/ {
    val=$0; sub(/^    reason:[[:space:]]*/, "", val); gsub(/"/, "", val)
    print "reason:" val
    in_paths=0; in_actions=0
    next
  }

  # min_files
  /^    min_files:/ {
    val=$0; sub(/^    min_files:[[:space:]]*/, "", val); gsub(/"/, "", val)
    print "min_files:" val
    in_paths=0; in_actions=0
    next
  }

  # paths key
  /^    paths:/ { in_paths=1; in_actions=0; next }

  # actions key
  /^    actions:/ { in_actions=1; in_paths=0; next }

  # list items under paths
  in_paths && /^      - / {
    val=$0; sub(/^      - /, "", val); gsub(/"/, "", val)
    print "path:" val
    next
  }

  # list items under actions
  in_actions && /^      - / {
    val=$0; sub(/^      - /, "", val); gsub(/"/, "", val)
    print "action:" val
    next
  }

  # Any non-list, non-blank line at rule indent ends sub-list
  /^    [^ ]/ { in_paths=0; in_actions=0 }

  END { if (in_rule) print "---RULE---" }
  ' "${APPROVALS_FILE}"
}

# ---------------------------------------------------------------------------
# Evaluate rules
# ---------------------------------------------------------------------------
MATCHED_KINDS=()
MATCHED_REASONS=()

# We accumulate each rule's fields then evaluate when we hit ---RULE---
current_kind=""
current_reason=""
current_min_files=""
current_paths=()
current_actions=()

evaluate_rule() {
  local kind="$1"
  local reason="$2"
  local min_files="$3"
  local -n _paths=$4
  local -n _actions=$5

  local has_paths=0
  local has_actions=0
  [[ ${#_paths[@]} -gt 0 ]] && has_paths=1
  [[ ${#_actions[@]} -gt 0 ]] && has_actions=1

  # Check paths condition
  local paths_match=0
  if [[ ${has_paths} -eq 1 ]]; then
    for glob in "${_paths[@]}"; do
      for ipath in "${INPUT_PATHS[@]}"; do
        if glob_match "${glob}" "${ipath}"; then
          paths_match=1
          break 2
        fi
      done
    done
  fi

  # Check actions condition
  local actions_match=0
  if [[ ${has_actions} -eq 1 ]]; then
    for act in "${_actions[@]}"; do
      if [[ "${act}" == "${ACTION}" ]]; then
        actions_match=1
        break
      fi
    done
  fi

  # Check min_files condition
  local min_ok=1
  if [[ -n "${min_files}" && "${min_files}" =~ ^[0-9]+$ ]]; then
    if [[ ${INPUT_COUNT} -lt ${min_files} ]]; then
      min_ok=0
    fi
  fi

  # Determine if rule matches based on which conditions are present
  local rule_matches=0

  if [[ ${has_paths} -eq 1 && ${has_actions} -eq 1 ]]; then
    # Both must match
    [[ ${paths_match} -eq 1 && ${actions_match} -eq 1 && ${min_ok} -eq 1 ]] && rule_matches=1
  elif [[ ${has_paths} -eq 1 ]]; then
    [[ ${paths_match} -eq 1 && ${min_ok} -eq 1 ]] && rule_matches=1
  elif [[ ${has_actions} -eq 1 ]]; then
    [[ ${actions_match} -eq 1 && ${min_ok} -eq 1 ]] && rule_matches=1
  fi
  # Rule with neither paths nor actions: never matches (defensive)

  if [[ ${rule_matches} -eq 1 ]]; then
    MATCHED_KINDS+=("${kind}")
    MATCHED_REASONS+=("${reason}")
  fi
}

# Process the parsed output line by line
while IFS= read -r line; do
  if [[ "${line}" == "---RULE---" ]]; then
    if [[ -n "${current_kind}" ]]; then
      evaluate_rule \
        "${current_kind}" \
        "${current_reason}" \
        "${current_min_files}" \
        current_paths \
        current_actions
    fi
    current_kind=""
    current_reason=""
    current_min_files=""
    current_paths=()
    current_actions=()
    continue
  fi

  if [[ "${line}" == kind:* ]]; then
    current_kind="${line#kind:}"
  elif [[ "${line}" == reason:* ]]; then
    current_reason="${line#reason:}"
  elif [[ "${line}" == min_files:* ]]; then
    current_min_files="${line#min_files:}"
  elif [[ "${line}" == path:* ]]; then
    current_paths+=("${line#path:}")
  elif [[ "${line}" == action:* ]]; then
    current_actions+=("${line#action:}")
  fi
done < <(parse_rules)

# ---------------------------------------------------------------------------
# Emit output
# ---------------------------------------------------------------------------
if [[ ${#MATCHED_KINDS[@]} -gt 0 ]]; then
  echo "decision: ask"
  echo "matched_rules:"
  for i in "${!MATCHED_KINDS[@]}"; do
    echo "  - ${MATCHED_KINDS[$i]} — ${MATCHED_REASONS[$i]}"
  done
else
  echo "decision: auto"
  echo "matched_rules:"
  echo "  (none)"
fi

exit 0
