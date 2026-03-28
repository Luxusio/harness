#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "$0")/_lib.sh"

# PostCompact hook — context recovery after compaction.
# Non-blocking. Summarizes open tasks for re-injection.
# stdin: JSON | exit 0: always

[[ ! -f ".claude/harness/manifest.yaml" ]] && exit 0

if [[ ! -d "$TASK_DIR" ]]; then
  echo "harness: no active tasks after compaction."
  exit 0
fi

echo "=== HARNESS POST-COMPACT SUMMARY ==="

open_count=0
blocked_count=0
pending_verdicts=0

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
      blocked_count=$((blocked_count + 1))
      echo "- ${task_id} [BLOCKED_ENV, lane: ${lane:-unknown}]"
      blockers=$(grep "^blockers:" "$state_file" 2>/dev/null | head -1 | sed 's/blockers: *//')
      [[ -n "$blockers" && "$blockers" != "[]" ]] && echo "  blockers: ${blockers}"
      ;;
    *)
      open_count=$((open_count + 1))
      plan_v=$(grep "^plan_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/plan_verdict: *//')
      runtime_v=$(grep "^runtime_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/runtime_verdict: *//')
      doc_v=$(grep "^document_verdict:" "$state_file" 2>/dev/null | head -1 | sed 's/document_verdict: *//')
      echo "- ${task_id} [${status:-unknown}, lane: ${lane:-unknown}]"
      echo "  verdicts: plan=${plan_v:-?} runtime=${runtime_v:-?} document=${doc_v:-?}"
      [[ "$plan_v" == "pending" ]] && pending_verdicts=$((pending_verdicts + 1))
      [[ "$runtime_v" == "pending" ]] && pending_verdicts=$((pending_verdicts + 1))
      [[ "$doc_v" == "pending" ]] && pending_verdicts=$((pending_verdicts + 1))
      ;;
  esac
done

if [[ $open_count -eq 0 && $blocked_count -eq 0 ]]; then
  echo "(no active tasks)"
else
  echo ""
  echo "Summary: ${open_count} open, ${blocked_count} blocked_env, ${pending_verdicts} pending verdicts"
fi

# Flag missing DOC_SYNC on mutating tasks
for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue
  state_file="${task}TASK_STATE.yaml"
  [[ ! -f "$state_file" ]] && continue
  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
  case "$status" in closed|archived|stale) continue ;; esac
  mutates=$(grep "^mutates_repo:" "$state_file" 2>/dev/null | head -1 | sed 's/mutates_repo: *//')
  if [[ "$mutates" == "true" || "$mutates" == "unknown" ]]; then
    [[ ! -f "${task}DOC_SYNC.md" ]] && echo "NOTE: $(basename "$task") — repo-mutating task without DOC_SYNC.md"
  fi
done

exit 0
