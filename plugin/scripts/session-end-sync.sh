#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# SessionEnd hook — final session state summary.
# Non-blocking. Records open tasks, blockers, unresolved items.
# stdin: JSON | exit 0: always

[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0
[[ ! -d "$TASK_DIR" ]] && exit 0

echo "=== HARNESS SESSION END SUMMARY ==="

# --- Browser QA status ---
browser_qa="disabled"
if awk '/^qa:/{found=1} found && /browser_qa_supported:/{print; exit}' ".claude/harness/manifest.yaml" 2>/dev/null | grep -qE "browser_qa_supported\s*:\s*true"; then
  browser_qa="enabled"
elif awk '/^browser:/{found=1} found && /enabled:/{print; exit}' ".claude/harness/manifest.yaml" 2>/dev/null | grep -qE "enabled\s*:\s*true"; then
  browser_qa="enabled"
fi
echo "browser_qa: ${browser_qa}"
echo ""

open_tasks=()
blocked_tasks=()
missing_doc_sync=()
incomplete_verdicts=()

for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue

  state_file="${task}TASK_STATE.yaml"
  task_id=$(basename "$task")
  [[ ! -f "$state_file" ]] && continue

  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
  lane=$(grep "^lane:" "$state_file" 2>/dev/null | head -1 | sed 's/lane: *//')

  case "$status" in
    closed|archived|stale) continue ;;
    blocked_env)
      blocked_tasks+=("${task_id} [lane: ${lane:-unknown}]")
      ;;
    *)
      qa_mode=$(grep "^qa_mode:" "$state_file" 2>/dev/null | head -1 | sed 's/qa_mode: *//')
      open_tasks+=("${task_id} [status: ${status:-unknown}, lane: ${lane:-unknown}, qa_mode: ${qa_mode:-auto}]")

      plan_v=$(grep "^plan_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/plan_verdict: *//')
      runtime_v=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
      doc_v=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
      if [[ "$plan_v" == "pending" || "$runtime_v" == "pending" || "$doc_v" == "pending" ]]; then
        incomplete_verdicts+=("${task_id}: plan=${plan_v:-?} runtime=${runtime_v:-?} document=${doc_v:-?}")
      fi

      mutates=$(grep "^mutates_repo:" "$state_file" 2>/dev/null | head -1 | sed 's/mutates_repo: *//')
      if [[ "$mutates" == "true" || "$mutates" == "unknown" ]]; then
        [[ ! -f "${task}DOC_SYNC.md" ]] && missing_doc_sync+=("$task_id")
      fi
      ;;
  esac
done

if [[ ${#open_tasks[@]} -gt 0 ]]; then
  echo "OPEN TASKS (${#open_tasks[@]}):"
  for t in "${open_tasks[@]}"; do echo "  - $t"; done
fi

if [[ ${#blocked_tasks[@]} -gt 0 ]]; then
  echo "BLOCKED_ENV TASKS (${#blocked_tasks[@]}):"
  for t in "${blocked_tasks[@]}"; do echo "  - $t"; done
fi

if [[ ${#incomplete_verdicts[@]} -gt 0 ]]; then
  echo "PENDING VERDICTS:"
  for v in "${incomplete_verdicts[@]}"; do echo "  - $v"; done
fi

if [[ ${#missing_doc_sync[@]} -gt 0 ]]; then
  echo "MISSING DOC_SYNC (repo-mutating tasks):"
  for d in "${missing_doc_sync[@]}"; do echo "  - $d"; done
fi

if [[ ${#open_tasks[@]} -eq 0 && ${#blocked_tasks[@]} -eq 0 ]]; then
  echo "All tasks closed. Clean session end."
fi

exit 0
