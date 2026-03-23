#!/usr/bin/env bash
set -euo pipefail

if [[ ! -f "harness/manifest.yaml" ]]; then
  echo "harness status: plugin installed but this repository is not initialized."
  echo "If the user wants repo-local workflows, memory, and routing, suggest /harness:setup."
  exit 0
fi

echo "harness status: initialized in this repository."
echo ""

MANIFEST="harness/manifest.yaml"

# ── helpers ──────────────────────────────────────────────────────────────────

# Extract a scalar field from under the top-level `project:` section.
# Usage: project_scalar_field <file> <field>
project_scalar_field() {
  local file="$1" field="$2"
  awk -v fld="$field" '
    /^project:/ { in_proj = 1; next }
    /^[a-zA-Z]/ { in_proj = 0 }
    in_proj && $0 ~ "^  "fld":" {
      val = $0; sub(/^[^:]*: */, "", val); gsub(/"/, "", val); sub(/ *#.*/, "", val);
      print val; exit
    }
  ' "$file"
}

# Extract list items from under the top-level `project:` section for a given
# sub-field (e.g. languages, frameworks).  Stops at the next 2-space key or
# top-level key.  Returns one item per line (leading "- " stripped).
# Usage: project_list_field <file> <field> [max]
project_list_field() {
  local file="$1" field="$2" max="${3:-999}"
  awk -v fld="$field" -v mx="$max" '
    /^project:/ { in_proj = 1; next }
    /^[a-zA-Z]/ { in_proj = 0; in_list = 0; next }
    in_proj && $0 ~ "^  "fld":" { in_list = 1; next }
    in_proj && in_list && /^  [a-zA-Z]/ { in_list = 0 }
    in_proj && in_list && /^[[:space:]]*-/ {
      if (n++ >= mx) exit
      val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/ *#.*/, "", val); sub(/"?[[:space:]]*$/, "", val)
      print val
    }
  ' "$file"
}

# Extract a scalar field nested one level under a named top-level section.
# Usage: section_field <file> <section> <field>
section_field() {
  local file="$1" section="$2" field="$3"
  awk -v sec="$section" -v fld="$field" '
    /^[a-zA-Z]/ { in_sec = ($0 ~ "^"sec":") }
    in_sec && $0 ~ "^  "fld":" {
      val = $0; sub(/^[^:]*: */, "", val); gsub(/"/, "", val); sub(/ *#.*/, "", val);
      print val; exit
    }
  ' "$file"
}

# Extract a top-level scalar list (items directly under a top-level key),
# stopping at the next top-level key.  Returns one item per line.
# Usage: top_level_scalar_list <file> <section> [max]
top_level_scalar_list() {
  local file="$1" section="$2" max="${3:-999}"
  awk -v sec="$section" -v mx="$max" '
    /^[a-zA-Z]/ { in_sec = ($0 ~ "^"sec":"); next }
    in_sec && /^[[:space:]]*-[[:space:]]/ {
      if (n++ >= mx) exit
      val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/ *#.*/, "", val); sub(/"?[[:space:]]*$/, "", val)
      # Only emit simple scalar items (no colon after the value)
      if (val !~ /:/) print val
    }
  ' "$file"
}

# Summarise objects in the always_ask_before list.
# For path-based rules: "kind — reason (paths: p1, p2)"
# For action-based rules: "kind — reason (actions: a, b; min_files: N)"
# Usage: always_ask_before_summary <file> [max]
always_ask_before_summary() {
  local file="$1" max="${2:-999}"
  awk -v mx="$max" '
    /^always_ask_before:/ { in_sec = 1; next }
    /^[a-zA-Z]/ && !/^always_ask_before:/ { in_sec = 0 }

    in_sec {
      # New object starts with "  - kind:" or "  - " followed by kind on same or next lines
      if (/^[[:space:]]*- kind:/) {
        if (kind != "") {
          if (n++ < mx) print_entry()
          kind = ""; reason = ""; paths = ""; actions = ""; min_files = ""
        }
        val = $0; sub(/.*kind:[[:space:]]*"?/, "", val); sub(/"?[[:space:]]*$/, "", val)
        kind = val
      } else if (/^[[:space:]]*- $/ || /^[[:space:]]*-$/) {
        if (kind != "") {
          if (n++ < mx) print_entry()
          kind = ""; reason = ""; paths = ""; actions = ""; min_files = ""
        }
      } else if (/^[[:space:]]+reason:/) {
        val = $0; sub(/.*reason:[[:space:]]*"?/, "", val); sub(/"?[[:space:]]*$/, "", val)
        reason = val
      } else if (/^[[:space:]]+min_files:/) {
        val = $0; sub(/.*min_files:[[:space:]]*/, "", val); sub(/[[:space:]]*$/, "", val)
        min_files = val
      } else if (/^[[:space:]]+-[[:space:]]/ && kind != "" && paths == "" && actions == "") {
        # Could be a path or action list item — look at context via paths/actions key seen above
        val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/"?[[:space:]]*$/, "", val)
        if (awaiting == "paths") {
          paths = (paths == "") ? val : paths ", " val
        } else if (awaiting == "actions") {
          actions = (actions == "") ? val : actions ", " val
        }
      } else if (/^[[:space:]]+paths:/) {
        awaiting = "paths"; paths = ""
      } else if (/^[[:space:]]+actions:/) {
        awaiting = "actions"; actions = ""
      } else if (/^[[:space:]]*- / && kind != "") {
        # list item under paths or actions
        val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/"?[[:space:]]*$/, "", val)
        if (val !~ /:/) {
          if (awaiting == "paths") {
            paths = (paths == "") ? val : paths ", " val
          } else if (awaiting == "actions") {
            actions = (actions == "") ? val : actions ", " val
          }
        }
      }
    }

    END {
      if (kind != "" && n < mx) print_entry()
    }

    function print_entry() {
      line = kind " — " reason
      if (actions != "") {
        detail = "actions: " actions
        if (min_files != "") detail = detail "; min_files: " min_files
        line = line " (" detail ")"
      } else if (paths != "") {
        line = line " (paths: " paths ")"
      }
      print line
    }
  ' "$file"
}

# ── MANIFEST SUMMARY ─────────────────────────────────────────────────────────

echo "=== MANIFEST SUMMARY ==="

MODE=$(project_scalar_field "$MANIFEST" "mode")
TYPE=$(project_scalar_field "$MANIFEST" "type")
echo "mode: ${MODE:-unknown}  |  type: ${TYPE:-unknown}"

# languages / frameworks (nested under project:)
for field in languages frameworks; do
  vals=$(project_list_field "$MANIFEST" "$field" 3 | paste -sd',' - | sed 's/,/, /g')
  [[ -n "$vals" ]] && echo "${field}: ${vals}"
done

# package_manager (scalar under project:)
pkg=$(project_scalar_field "$MANIFEST" "package_manager")
[[ -n "$pkg" ]] && echo "package_manager: ${pkg}"

# commands (nested under commands:)
for field in dev build test lint; do
  val=$(section_field "$MANIFEST" "commands" "$field")
  [[ -n "$val" ]] && echo "${field}: ${val}"
done

# key journeys — bounded: stops at next top-level key (risk_zones, etc.)
journeys=$(awk '
  /^key_journeys:/ { in_sec = 1; next }
  /^[a-zA-Z]/ { in_sec = 0 }
  in_sec && /^[[:space:]]*-[[:space:]]/ {
    if (n++ >= 3) exit
    val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/ *#.*/, "", val); sub(/"?[[:space:]]*$/, "", val)
    print val
  }
' "$MANIFEST")
if [[ -n "$journeys" ]]; then
  echo "key_journeys (top 3):"
  while IFS= read -r line; do echo "  - $line"; done <<< "$journeys"
fi

# risk zones (path + reason pairs) — bounded at next top-level key
risk_output=$(awk '
  /^[a-zA-Z]/ { in_sec = ($0 ~ /^risk_zones:/) ; next }
  in_sec && /^[[:space:]]*- path:/ {
    p = $0; sub(/.*path: *"?/, "", p); sub(/"?[[:space:]]*$/, "", p)
    getline
    if ($0 ~ /reason:/) { r = $0; sub(/.*reason: *"?/, "", r); sub(/"?[[:space:]]*$/, "", r) }
    else { r = "" }
    print p " — " r
  }
' "$MANIFEST" | head -3)
if [[ -n "$risk_output" ]]; then
  echo "risk_zones (top 3):"
  while IFS= read -r line; do echo "  - $line"; done <<< "$risk_output"
fi
echo ""

# ── APPROVALS SUMMARY ────────────────────────────────────────────────────────

echo "=== APPROVALS SUMMARY ==="
APPROVALS="harness/policies/approvals.yaml"
if [[ -f "$APPROVALS" ]]; then
  # always_ask_before — parse objects into summaries
  echo "always_ask_before:"
  ask_items=$(always_ask_before_summary "$APPROVALS" 5)
  if [[ -z "$ask_items" ]]; then
    echo "  (none)"
  else
    while IFS= read -r line; do echo "  - $line"; done <<< "$ask_items"
  fi

  # auto_ok_examples — simple scalar list, bounded at next top-level key
  ok_items=$(awk '
    /^auto_ok_examples:/ { in_sec = 1; next }
    /^[a-zA-Z]/ { in_sec = 0 }
    in_sec && /^[[:space:]]*-[[:space:]]/ {
      if (n++ >= 5) exit
      val = $0; sub(/^[[:space:]]*- *"?/, "", val); sub(/ *#.*/, "", val); sub(/"?[[:space:]]*$/, "", val)
      if (val !~ /:/) print val
    }
  ' "$APPROVALS")
  echo "auto_ok_examples:"
  if [[ -z "$ok_items" ]]; then
    echo "  (none)"
  else
    while IFS= read -r line; do echo "  - $line"; done <<< "$ok_items"
  fi

  # ask_when — show keys where value is true, bounded at next top-level key
  echo "ask_when:"
  ask_when=$(awk '
    /^ask_when:/ { in_sec = 1; next }
    /^[a-zA-Z]/ { in_sec = 0 }
    in_sec && /:[[:space:]]*true/ {
      k = $0; sub(/:.*/, "", k); gsub(/^[[:space:]]+/, "", k)
      print k
    }
  ' "$APPROVALS")
  if [[ -z "$ask_when" ]]; then
    echo "  (none)"
  else
    while IFS= read -r line; do echo "  - $line"; done <<< "$ask_when"
  fi
else
  echo "(no approvals policy found)"
fi
echo ""

# ── RECENT DECISIONS ─────────────────────────────────────────────────────────

echo "=== RECENT DECISIONS ==="
if [[ -f "harness/state/recent-decisions.md" ]]; then
  # Filter out HTML comments, blank lines, and heading-only lines
  content=$(grep -v '^\s*$' "harness/state/recent-decisions.md" | grep -v '^<!--' | grep -v -- '-->$' | grep -v '^# ' | tail -10 || true)
  if [[ -n "$content" ]]; then
    echo "$content"
  else
    echo "(no decisions recorded yet)"
  fi
else
  echo "(no recent decisions file)"
fi
echo ""

# ── MEMORY INDEX ────────────────────────────────────────────────────────────

echo "=== MEMORY INDEX ==="
if [[ -f "harness/memory-index/manifest.json" ]]; then
  # Extract record count and version from manifest
  RECORD_COUNT=$(python3 -c "import json; m=json.load(open('harness/memory-index/manifest.json')); print(m.get('record_count', 'unknown'))" 2>/dev/null || echo "unknown")
  INDEX_VERSION=$(cat "harness/memory-index/VERSION" 2>/dev/null || echo "unknown")
  echo "status: active"
  echo "version: ${INDEX_VERSION}"
  echo "records: ${RECORD_COUNT}"

  # Check overlay
  if [[ -f ".harness-cache/memory-overlay/manifest.json" ]]; then
    OVERLAY_COUNT=$(python3 -c "import json; m=json.load(open('.harness-cache/memory-overlay/manifest.json')); print(m.get('record_count', 0))" 2>/dev/null || wc -l < ".harness-cache/memory-overlay/records.jsonl" 2>/dev/null || echo "0")
    OVERLAY_BUILT=$(python3 -c "import json; m=json.load(open('.harness-cache/memory-overlay/manifest.json')); print(m.get('built_at', '')[:16])" 2>/dev/null || echo "")
    if [[ -n "$OVERLAY_BUILT" ]]; then
      echo "overlay: ${OVERLAY_COUNT} records (built ${OVERLAY_BUILT} UTC)"
    else
      echo "overlay: ${OVERLAY_COUNT} records"
    fi
  elif [[ -d ".harness-cache/memory-overlay" ]]; then
    OVERLAY_COUNT=$(wc -l < ".harness-cache/memory-overlay/records.jsonl" 2>/dev/null || echo "0")
    echo "overlay: ${OVERLAY_COUNT} records (no manifest)"
  else
    echo "overlay: none"
  fi
else
  echo "status: not built (run harness/scripts/build-memory-index.sh)"
fi
echo ""

# ── LAST SESSION ─────────────────────────────────────────────────────────────

echo "=== LAST SESSION ==="
if [[ -f "harness/state/last-session-summary.md" ]]; then
  content=$(grep -v '^\s*$' "harness/state/last-session-summary.md" | grep -v '^<!--' | grep -v -- '-->$' | grep -v '^# ' | head -12 || true)
  if [[ -n "$content" ]]; then
    echo "$content"
  else
    echo "(no session summary recorded)"
  fi
else
  echo "(no previous session summary)"
fi
echo ""

# ── INTERRUPTED TASK ─────────────────────────────────────────────────────────

if [[ -f "harness/state/current-task.yaml" ]]; then
  # Extract status, strip quotes and inline comments
  TASK_STATUS=$(awk '/^status:/ { val=$2; gsub(/"/, "", val); sub(/ *#.*/, "", val); print val; exit }' "harness/state/current-task.yaml")

  # Only active/validating/syncing count as interrupted
  if [[ "$TASK_STATUS" == "active" || "$TASK_STATUS" == "validating" || "$TASK_STATUS" == "syncing" ]]; then
    echo "=== INTERRUPTED TASK ==="
    # Show fields that actually exist in current-task.yaml schema
    awk '
      /^(intent|scope|risk_level|status|validated|memory_updates):/ { show=1 }
      /^[a-zA-Z]/ && !/^(intent|scope|risk_level|status|validated|memory_updates):/ { show=0 }
      show { print }
    ' "harness/state/current-task.yaml"
    echo ""
    echo "WARNING: Previous session ended with task status '$TASK_STATUS'. Review and resume or reset."
    echo ""
  fi
fi

# ── VALIDATION COMMANDS ──────────────────────────────────────────────────────

if [[ -f "CLAUDE.md" ]]; then
  echo "=== VALIDATION COMMANDS ==="
  sed 's/\r$//' "CLAUDE.md" | sed -n '/^## Validation commands/,/^## /{ /^## Validation commands/d; /^## /d; p; }' | head -8
  echo ""
  echo "CLAUDE.md is present -- follow its instructions for request handling."
fi

# ── ADDITIONAL REFERENCES ────────────────────────────────────────────────────

echo ""
echo "Additional repo-local sources when relevant:"
echo "- harness/router.yaml"
echo "- harness/policies/memory-policy.yaml"
echo "- harness/state/unknowns.md"
echo "- harness/docs/index.md"
echo "- harness/memory-index/manifest.json"
echo "- harness/scripts/query-memory.sh"
