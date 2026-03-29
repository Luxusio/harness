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

# --- Browser QA availability ---
browser_qa="disabled"
if awk '/^qa:/{found=1} found && /browser_qa_supported:/{print; exit}' ".claude/harness/manifest.yaml" 2>/dev/null | grep -qE "browser_qa_supported\s*:\s*true"; then
  browser_qa="enabled"
elif awk '/^browser:/{found=1} found && /enabled:/{print; exit}' ".claude/harness/manifest.yaml" 2>/dev/null | grep -qE "enabled\s*:\s*true"; then
  browser_qa="enabled"
fi
echo "browser_qa: ${browser_qa}"
echo ""

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
      qa_mode=$(grep "^qa_mode:" "$state_file" 2>/dev/null | head -1 | sed 's/qa_mode: *//')
      mutates=$(grep "^mutates_repo:" "$state_file" 2>/dev/null | head -1 | sed 's/mutates_repo: *//')
      doc_sync_status="n/a"
      if [[ "$mutates" == "true" || "$mutates" == "unknown" ]]; then
        if [[ -f "${task}DOC_SYNC.md" ]]; then
          doc_sync_status="present"
        else
          doc_sync_status="missing"
        fi
      fi
      echo "- ${task_id} [${status:-unknown}, lane: ${lane:-unknown}, qa_mode: ${qa_mode:-auto}]"
      echo "  verdicts: plan=${plan_v:-?} runtime=${runtime_v:-?} document=${doc_v:-?}"
      echo "  doc_sync: ${doc_sync_status}"
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

# --- Maintain-lite entropy indicators (quick scan, no fixes) ---
stale_count=0
orphan_count=0
broken_chain_count=0
now_epoch=$(date +%s)
stale_threshold=$((7 * 24 * 3600))

# Count stale tasks
for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue
  state_file="${task}TASK_STATE.yaml"
  [[ ! -f "$state_file" ]] && continue
  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
  case "$status" in
    closed|archived|stale) continue ;;
  esac
  updated_raw=$(grep "^updated:" "$state_file" 2>/dev/null | head -1 | sed 's/updated: *//')
  if [[ -n "$updated_raw" ]]; then
    updated_epoch=$(date -d "$updated_raw" +%s 2>/dev/null || echo 0)
    age=$(( now_epoch - updated_epoch ))
    [[ $age -gt $stale_threshold ]] && stale_count=$((stale_count + 1))
  fi
done

# Count orphan notes
if [[ -d "doc/common" ]]; then
  for note in doc/common/*.md; do
    [[ ! -f "$note" ]] && continue
    note_base=$(basename "$note")
    [[ "$note_base" == "CLAUDE.md" ]] && continue
    if ! grep -rl "$note_base" doc/ --include="CLAUDE.md" 2>/dev/null | grep -q .; then
      orphan_count=$((orphan_count + 1))
    fi
  done
fi

# Count broken supersede chains
if [[ -d "doc/common" ]]; then
  for note in doc/common/*.md; do
    [[ ! -f "$note" ]] && continue
    superseded_by=$(grep "^superseded_by:" "$note" 2>/dev/null | head -1 | sed 's/superseded_by: *//')
    if [[ -n "$superseded_by" && "$superseded_by" != "null" && "$superseded_by" != "~" ]]; then
      target="doc/common/${superseded_by}"
      [[ ! -f "$target" ]] && broken_chain_count=$((broken_chain_count + 1))
    fi
  done
fi

# Entropy health score
total_issues=$((stale_count + orphan_count))
if [[ $broken_chain_count -gt 0 || $((total_issues)) -ge 4 ]]; then
  entropy="HIGH"
elif [[ $total_issues -ge 1 ]]; then
  entropy="MEDIUM"
else
  entropy="LOW"
fi

echo ""
echo "harness health: entropy=${entropy}"
[[ $stale_count -gt 0 ]] && echo "  ${stale_count} task(s) may be stale (updated > 7 days ago)"
[[ $orphan_count -gt 0 ]] && echo "  ${orphan_count} note(s) are orphaned (not in any CLAUDE.md index)"
[[ $broken_chain_count -gt 0 ]] && echo "  ${broken_chain_count} broken supersede chain(s)"
[[ "$entropy" != "LOW" ]] && echo "  hint: run /harness:maintain to clean up"

exit 0
