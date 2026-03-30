#!/usr/bin/env python3
"""FileChanged hook — task-scoped verdict invalidation + plan-first violation recording.

Non-blocking. Resets stale PASS verdicts to pending only for tasks
whose touched_paths/roots_touched/verification_targets overlap with the changed file(s).

Precision rules:
  - doc path change  → invalidate document_verdict only
  - runtime path change → invalidate runtime_verdict only (via verification_targets)
  - both → invalidate both
Conservative fallback (no file list): invalidate ALL verdicts on ALL open tasks.

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
                  append_workflow_violation)

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


def invalidate_note_freshness(changed_file):
    """Scan doc/*/*.md and *.yaml; if invalidated_by_paths structurally matches
    changed_file → set freshness: suspect.

    Uses parse_note_metadata for structured invalidated_by_paths extraction
    instead of substring matching on raw content.
    """
    doc_base = "doc"
    if not os.path.isdir(doc_base):
        return

    # Collect all note files across all doc/* subdirectories
    note_files = []
    for pattern in (
        os.path.join(doc_base, "*", "*.md"),
        os.path.join(doc_base, "*", "*.yaml"),
    ):
        note_files.extend(glob.glob(pattern))

    for note_file in sorted(note_files):
        if not os.path.isfile(note_file):
            continue

        meta = parse_note_metadata(note_file)
        inv_paths = meta["invalidated_by_paths"]

        if not inv_paths:
            continue

        # Structural comparison — no substring matching
        matched = any(_path_matches_inv(changed_file, inv) for inv in inv_paths)
        if not matched:
            continue

        # Only transition current → suspect (already-suspect notes are left alone)
        current_freshness = meta["freshness"]
        if current_freshness == "suspect":
            continue  # already suspect, no action needed

        set_note_freshness(note_file, "suspect")
        print(f"NOTE SUSPECT: {note_file} — freshness set to suspect ({changed_file} changed)")


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
    changed_is_doc = is_doc_path(changed_file)
    changed_is_runtime = not changed_is_doc

    # Note freshness check (applies to all changed files)
    invalidate_note_freshness(changed_file)

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


def main():
    if not os.path.isfile(MANIFEST):
        sys.exit(0)
    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    hook_input = read_hook_input()

    # Parse changed files from stdin (handles files/paths/changed_files/file_path/file/path)
    changed_files = parse_changed_files(hook_input)

    if changed_files:
        # Process each changed file individually with precision
        for f in changed_files:
            if not f:
                continue
            process_changed_file(f)
    else:
        # No file list available — conservative fallback: invalidate ALL verdicts on ALL open tasks
        for task in sorted(glob.glob(os.path.join(TASK_DIR, "TASK__*/"))):
            if not os.path.isdir(task):
                continue
            state_file = os.path.join(task, "TASK_STATE.yaml")
            task_id = os.path.basename(task.rstrip('/'))
            if not os.path.isfile(state_file):
                continue
            status = yaml_field("status", state_file)
            if status in ("closed", "archived", "stale"):
                continue
            invalidate_runtime(state_file, task_id, "files changed after PASS, no file list")
            invalidate_document(state_file, task_id, "files changed after PASS, no file list")

    sys.exit(0)


if __name__ == "__main__":
    main()
