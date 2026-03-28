#!/usr/bin/env bash
set -euo pipefail

MANIFEST=".claude/harness/manifest.yaml"

if [[ -f "$MANIFEST" ]]; then
  echo "harness: initialized (v4)."
  echo ""

  # --- Project shape from manifest ---
  project_name=$(grep "^  *name:" "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*name: *//' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//" || true)
  project_type=$(grep "^  *type:" "$MANIFEST" 2>/dev/null | head -1 | sed 's/.*type: *//' | sed 's/^"//;s/"$//' | sed "s/^'//;s/'$//" || true)
  if [[ -n "$project_name" || -n "$project_type" ]]; then
    echo "=== PROJECT ==="
    [[ -n "$project_name" ]] && echo "name: ${project_name}"
    [[ -n "$project_type" ]] && echo "type: ${project_type}"
    echo ""
  fi

  # --- Browser QA status ---
  browser_qa="disabled"
  if awk '/^qa:/{found=1} found && /browser_qa_supported:/{print; exit}' "$MANIFEST" 2>/dev/null | grep -qE "browser_qa_supported\s*:\s*true"; then
    browser_qa="enabled"
  elif awk '/^browser:/{found=1} found && /enabled:/{print; exit}' "$MANIFEST" 2>/dev/null | grep -qE "enabled\s*:\s*true"; then
    browser_qa="enabled"
  fi
  # Check for blocked_env on any task that needs browser QA
  if [[ "$browser_qa" == "enabled" ]]; then
    for task in ".claude/harness/tasks"/TASK__*/; do
      [[ ! -d "$task" ]] && continue
      state_file="${task}TASK_STATE.yaml"
      [[ ! -f "$state_file" ]] && continue
      if grep -q "^status: blocked_env" "$state_file" 2>/dev/null && grep -q "^browser_required: true" "$state_file" 2>/dev/null; then
        browser_qa="blocked_env"
        break
      fi
    done
  fi
  echo "=== BROWSER QA: ${browser_qa} ==="
  echo ""

  TASK_DIR=".claude/harness/tasks"
  found_open=0
  found_blocked=0

  # === OPEN TASKS ===
  echo "=== OPEN TASKS ==="
  if [[ -d "$TASK_DIR" ]]; then
    for task in "$TASK_DIR"/TASK__*/; do
      if [[ -d "$task" ]]; then
        state_file="${task}TASK_STATE.yaml"
        task_id=$(basename "$task")
        if [[ -f "$state_file" ]]; then
          status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
          lane=$(grep "^lane:" "$state_file" 2>/dev/null | head -1 | sed 's/lane: *//')
          qa_mode=$(grep "^qa_mode:" "$state_file" 2>/dev/null | head -1 | sed 's/qa_mode: *//')

          case "$status" in
            closed|archived|stale) continue ;;
            blocked_env)
              echo "- ${task_id} [lane: ${lane:-unknown}, BLOCKED_ENV, qa_mode: ${qa_mode:-auto}]"
              found_blocked=1
              found_open=1
              ;;
            *)
              plan_v=$(grep "^plan_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/plan_verdict: *//')
              runtime_v=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
              mutates=$(grep "^mutates_repo:" "$state_file" 2>/dev/null | head -1 | sed 's/mutates_repo: *//')
              doc_sync_status="n/a"
              if [[ "$mutates" == "true" || "$mutates" == "unknown" ]]; then
                if [[ -f "${task}DOC_SYNC.md" ]]; then
                  doc_sync_status="present"
                else
                  doc_sync_status="missing"
                fi
              fi
              echo "- ${task_id} [lane: ${lane:-unknown}, status: ${status:-unknown}, qa_mode: ${qa_mode:-auto}, plan: ${plan_v:-?}, runtime: ${runtime_v:-?}, doc_sync: ${doc_sync_status}]"
              found_open=1
              ;;
          esac
        fi
      fi
    done
  fi

  if [[ "$found_open" -eq 0 ]]; then
    echo "(no open tasks)"
  fi

  if [[ "$found_blocked" -eq 1 ]]; then
    echo ""
    echo "WARNING: blocked_env tasks need environment fixes before completion."
  fi

  echo ""
  echo "Follow CLAUDE.md instructions for request handling."
else
  echo "harness: plugin installed but repo not initialized."
  echo "Run /harness:setup to bootstrap."
fi
