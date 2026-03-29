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

# --- Maintain-lite entropy summary ---
echo ""
echo "=== MAINTAIN-LITE ==="

stale_count=0
orphan_count=0
broken_chain_count=0
dead_artifact_count=0
now_epoch=$(date +%s)
stale_threshold=$((7 * 24 * 3600))

# Count stale tasks (updated > 7 days ago, not closed/archived/stale)
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
    if [[ $age -gt $stale_threshold ]]; then
      stale_count=$((stale_count + 1))
    fi
  fi
done

# Count dead artifacts: CRITIC__*.md in closed task folders
for task in "$TASK_DIR"/TASK__*/; do
  [[ ! -d "$task" ]] && continue
  state_file="${task}TASK_STATE.yaml"
  [[ ! -f "$state_file" ]] && continue
  status=$(grep "^status:" "$state_file" 2>/dev/null | head -1 | sed 's/status: *//')
  if [[ "$status" == "closed" ]]; then
    artifact_count=$(find "$task" -maxdepth 1 -name 'CRITIC__*.md' 2>/dev/null | wc -l)
    dead_artifact_count=$((dead_artifact_count + artifact_count))
  fi
done

# Count orphan notes: files in doc/common/ not referenced in any CLAUDE.md index
if [[ -d "doc/common" ]]; then
  for note in doc/common/*.md; do
    [[ ! -f "$note" ]] && continue
    note_base=$(basename "$note")
    [[ "$note_base" == "CLAUDE.md" ]] && continue
    # Check if note appears in any CLAUDE.md index under doc/
    if ! grep -rl "$note_base" doc/ --include="CLAUDE.md" 2>/dev/null | grep -q .; then
      orphan_count=$((orphan_count + 1))
    fi
  done
fi

# Count broken supersede chains: superseded_by: pointing to non-existent file
if [[ -d "doc/common" ]]; then
  for note in doc/common/*.md; do
    [[ ! -f "$note" ]] && continue
    superseded_by=$(grep "^superseded_by:" "$note" 2>/dev/null | head -1 | sed 's/superseded_by: *//')
    if [[ -n "$superseded_by" && "$superseded_by" != "null" && "$superseded_by" != "~" ]]; then
      # Resolve relative to doc/common/
      target="doc/common/${superseded_by}"
      [[ ! -f "$target" ]] && broken_chain_count=$((broken_chain_count + 1))
    fi
  done
fi

echo "stale_tasks: ${stale_count}"
echo "orphan_notes: ${orphan_count}"
echo "broken_supersede_chains: ${broken_chain_count}"
echo "dead_artifacts: ${dead_artifact_count}"

# Entropy health score
total_issues=$((stale_count + orphan_count + dead_artifact_count))
if [[ $broken_chain_count -gt 0 || $total_issues -ge 4 ]]; then
  entropy="HIGH"
elif [[ $total_issues -ge 1 ]]; then
  entropy="MEDIUM"
else
  entropy="LOW"
fi
echo "entropy: ${entropy}"

if [[ "$entropy" != "LOW" ]]; then
  echo "hint: run /harness:maintain to clean up"
fi

exit 0
