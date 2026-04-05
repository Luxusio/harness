#!/usr/bin/env python3
"""Write-tool PostToolUse hook — task-scoped verdict invalidation + plan-first violation recording.

Non-blocking. Resets stale PASS verdicts to pending only for tasks
whose touched_paths/roots_touched/verification_targets overlap with the changed file(s).

Precision rules:
  - doc path change  → invalidate document_verdict only
  - runtime path change → invalidate runtime_verdict only (via verification_targets)
  - both → invalidate both
Gitless fallback (no file list): invalidate only the indexed active task.

Plan-first enforcement (WS-5):
  - runtime path change on task with explicit verification_targets ownership
    AND plan_verdict != PASS → record workflow_violation source_mutation_before_plan_pass

Note freshness: if a changed file matches a note's invalidated_by_paths, set note freshness to suspect.
  Uses structural path comparison (exact / prefix), NOT substring matching.
stdin: JSON | exit 0: always
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_array, yaml_field, yaml_array,
                  is_doc_path, find_tasks_touching_path,
                  find_tasks_with_verification_targets, task_touches_path,
                  manifest_field, is_profile_enabled, TASK_DIR, MANIFEST, now_iso,
                  parse_note_metadata, set_note_freshness, parse_changed_files,
                  append_workflow_violation, merge_task_path_fields,
                  is_task_artifact_path, find_repo_root)

from task_index import resolve_active_task_dir

import re
import glob


def invalidate_runtime(state_file, task_id, reason):
    """If runtime_verdict is PASS → replace with pending, update timestamp."""
    rv = yaml_field("runtime_verdict", state_file)
    if rv == "PASS":
        with open(state_file, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("runtime_verdict: PASS", "runtime_verdict: pending")
        content = re.sub(r'^updated: .*', f'updated: {now_iso()}', content, flags=re.MULTILINE)
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"INVALIDATED: {task_id} — runtime_verdict reset to pending ({reason})")


def invalidate_document(state_file, task_id, reason):
    """If document_verdict is PASS → replace with pending, set doc_changes_detected: true."""
    dv = yaml_field("document_verdict", state_file)
    if dv == "PASS":
        with open(state_file, "r", encoding="utf-8") as f:
            content = f.read()
        content = content.replace("document_verdict: PASS", "document_verdict: pending")
        content = re.sub(r'^updated: .*', f'updated: {now_iso()}', content, flags=re.MULTILINE)
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"INVALIDATED: {task_id} — document_verdict reset to pending ({reason})")

    # Also set doc_changes_detected: true
    with open(state_file, "r", encoding="utf-8") as f:
        content = f.read()
    if re.search(r'^doc_changes_detected:', content, flags=re.MULTILINE):
        content = re.sub(r'^doc_changes_detected: .*', 'doc_changes_detected: true', content, flags=re.MULTILINE)
    else:
        content = content.rstrip('\n') + '\ndoc_changes_detected: true\n'
    with open(state_file, "w", encoding="utf-8") as f:
        f.write(content)


def _task_state_file(task_dir):
    return os.path.join(task_dir, "TASK_STATE.yaml")


def _is_open_task(task_dir):
    state_file = _task_state_file(task_dir)
    if not os.path.isfile(state_file):
        return False
    status = yaml_field("status", state_file)
    return status not in ("closed", "archived", "stale")


def _merge_active_task_paths(task_dir, changed_file):
    if not task_dir or not changed_file or is_task_artifact_path(changed_file):
        return
    merge_task_path_fields(
        task_dir,
        touched_paths=[changed_file],
        verification_targets=[] if is_doc_path(changed_file) else [changed_file],
    )


def _fallback_invalidate_active_task(changed_file=None, reason="files changed after PASS, no file list"):
    """Invalidate only the indexed active task when precision is unavailable."""
    active_task = resolve_active_task_dir(TASK_DIR)
    if not active_task or not _is_open_task(active_task):
        return False
    state_file = _task_state_file(active_task)
    task_id = os.path.basename(active_task.rstrip('/'))

    if changed_file:
        if is_task_artifact_path(changed_file):
            return True
        _merge_active_task_paths(active_task, changed_file)
        if is_doc_path(changed_file):
            invalidate_document(state_file, task_id, f"{changed_file} doc changed after PASS (active-task fallback)")
        else:
            _record_plan_first_violation(changed_file)
            invalidate_runtime(state_file, task_id, f"{changed_file} changed after PASS (active-task fallback)")
    else:
        invalidate_runtime(state_file, task_id, reason)
        invalidate_document(state_file, task_id, reason)
    return True


def _path_matches_inv(changed_file, inv_path):
    """Structural path match: exact equality or directory prefix.

    NOT a substring match. This prevents false positives like:
      changed: src/api-v2.py
      inv:     src/api.py
    → should NOT match (api.py != api-v2.py, no prefix relation)
    """
    if not inv_path or not changed_file:
        return False
    if changed_file == inv_path:
        return True
    if changed_file.startswith(inv_path + "/"):
        return True
    if inv_path.startswith(changed_file + "/"):
        return True
    return False


def _collect_note_files(doc_base="doc"):
    """Return sorted note files across doc roots."""
    note_files = []
    for pattern in (
        os.path.join(doc_base, "*", "*.md"),
        os.path.join(doc_base, "*", "*.yaml"),
    ):
        note_files.extend(glob.glob(pattern))
    return sorted(note_files)



def invalidate_note_freshness_for_changes(changed_files, doc_base="doc"):
    """Batch note freshness invalidation for one hook run.

    Scans notes exactly once per run, then checks all changed files against each
    note's invalidated_by_paths. This avoids rescanning/parsing the entire doc/*
    tree once per changed file during MultiEdit/PostToolUse bursts.
    """
    if not os.path.isdir(doc_base):
        return

    normalized_changes = []
    seen_changes = set()
    for changed_file in changed_files or []:
        if not changed_file or changed_file in seen_changes:
            continue
        normalized_changes.append(changed_file)
        seen_changes.add(changed_file)
    if not normalized_changes:
        return

    for note_file in _collect_note_files(doc_base):
        if not os.path.isfile(note_file):
            continue

        meta = parse_note_metadata(note_file)
        inv_paths = meta["invalidated_by_paths"]
        if not inv_paths:
            continue

        matched_change = None
        for changed_file in normalized_changes:
            if any(_path_matches_inv(changed_file, inv) for inv in inv_paths):
                matched_change = changed_file
                break
        if not matched_change:
            continue

        # Only transition current → suspect (already-suspect notes are left alone)
        if meta["freshness"] == "suspect":
            continue

        set_note_freshness(note_file, "suspect")
        print(
            f"NOTE SUSPECT: {note_file} — freshness set to suspect"
            f" ({matched_change} changed)"
        )



def invalidate_note_freshness(changed_file, doc_base="doc"):
    """Backward-compatible single-file wrapper around batch note invalidation."""
    invalidate_note_freshness_for_changes([changed_file], doc_base=doc_base)


def _record_plan_first_violation(changed_file):
    """WS-5: Check all open tasks that explicitly own changed_file via verification_targets.

    If such a task has plan_verdict != PASS, record source_mutation_before_plan_pass.
    Only fires when the task has explicit file ownership (non-empty verification_targets).
    """
    if not os.path.isdir(TASK_DIR):
        return

    for task in sorted(glob.glob(os.path.join(TASK_DIR, "TASK__*/"))):
        if not os.path.isdir(task):
            continue
        state_file = os.path.join(task, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue

        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue

        # Only check if plan has NOT passed yet
        plan_verdict = yaml_field("plan_verdict", state_file)
        if plan_verdict == "PASS":
            continue

        # Require explicit verification_targets ownership (not conservative fallback)
        vt = yaml_array("verification_targets", state_file)
        if not vt:
            continue  # no explicit ownership — skip to avoid false positives

        # Check if changed_file is in this task's verification_targets
        owned = any(
            changed_file == path or changed_file.startswith(path + "/")
            for path in vt
            if path
        )
        if not owned:
            continue

        task_id = os.path.basename(task.rstrip("/"))
        append_workflow_violation(task, "source_mutation_before_plan_pass")
        print(
            f"VIOLATION: {task_id} — source mutation before plan PASS"
            f" ({changed_file} changed while plan_verdict={plan_verdict})"
        )


def process_changed_file(changed_file):
    """Classify doc/runtime, call appropriate invalidation and violation recording."""
    if is_task_artifact_path(changed_file):
        print(f"IGNORE: task-local artifact change does not invalidate verdicts ({changed_file})")
        return True

    active_task = resolve_active_task_dir(TASK_DIR)
    if active_task and _is_open_task(active_task):
        _merge_active_task_paths(active_task, changed_file)

    changed_is_doc = is_doc_path(changed_file)
    changed_is_runtime = not changed_is_doc
    matched_any = False

    if changed_is_runtime:
        # WS-5: record plan-first violation for tasks that own this file
        _record_plan_first_violation(changed_file)

        # Runtime change: invalidate runtime_verdict on tasks whose verification_targets overlap
        for task in find_tasks_with_verification_targets(changed_file):
            if not task:
                continue
            state_file = os.path.join(task, "TASK_STATE.yaml")
            task_id = os.path.basename(task.rstrip('/'))
            if not os.path.isfile(state_file):
                continue
            invalidate_runtime(state_file, task_id, f"{changed_file} changed after PASS")
            matched_any = True

    if changed_is_doc:
        # Doc change: invalidate document_verdict on tasks whose touched_paths overlap
        for task in find_tasks_touching_path(changed_file):
            if not task:
                continue
            state_file = os.path.join(task, "TASK_STATE.yaml")
            task_id = os.path.basename(task.rstrip('/'))
            if not os.path.isfile(state_file):
                continue
            invalidate_document(state_file, task_id, f"{changed_file} doc changed after PASS")
            matched_any = True

    # Team status degradation: complete → degraded when related files change
    if os.path.isdir(TASK_DIR):
        for task in sorted(glob.glob(os.path.join(TASK_DIR, "TASK__*/"))):
            if not os.path.isdir(task):
                continue
            state_file_t = os.path.join(task, "TASK_STATE.yaml")
            if not os.path.isfile(state_file_t):
                continue
            status = yaml_field("status", state_file_t)
            if status in ("closed", "archived", "stale"):
                continue
            orch = yaml_field("orchestration_mode", state_file_t)
            ts = yaml_field("team_status", state_file_t)
            if orch == "team" and ts == "complete":
                if task_touches_path(task, changed_file):
                    with open(state_file_t, "r", encoding="utf-8") as f:
                        content = f.read()
                    content = content.replace("team_status: complete", "team_status: degraded")
                    content = re.sub(r'^updated: .*', f'updated: {now_iso()}', content, flags=re.MULTILINE)
                    with open(state_file_t, "w", encoding="utf-8") as f:
                        f.write(content)
                    task_id = os.path.basename(task.rstrip('/'))
                    print(f"TEAM DEGRADED: {task_id} — team_status set to degraded ({changed_file} changed)")
                    matched_any = True

    if not matched_any:
        _fallback_invalidate_active_task(changed_file, reason=f"{changed_file} changed after PASS (no path ownership)")
        matched_any = True

    return matched_any


def main():
    try:
        os.chdir(find_repo_root(os.getcwd()))
    except Exception:
        pass
    if not os.path.isfile(MANIFEST):
        sys.exit(0)
    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    hook_input = read_hook_input()

    # Parse changed files from stdin (handles files/paths/changed_files/file_path/file/path)
    changed_files = parse_changed_files(hook_input)

    if changed_files:
        # Note freshness invalidation is batched once per hook run.
        invalidate_note_freshness_for_changes(changed_files)

        # Process each changed file individually with precision
        for f in changed_files:
            if not f:
                continue
            process_changed_file(f)
    else:
        # No file list available — stale only the active task instead of all open tasks.
        if not _fallback_invalidate_active_task(reason="files changed after PASS, no file list"):
            print("INFO: no changed file list and no active task — skipping global invalidation")

    sys.exit(0)


if __name__ == "__main__":
    main()
