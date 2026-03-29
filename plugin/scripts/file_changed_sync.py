#!/usr/bin/env python3
"""FileChanged hook — task-scoped verdict invalidation.

Non-blocking. Resets stale PASS verdicts to pending only for tasks
whose touched_paths/roots_touched/verification_targets overlap with the changed file(s).

Precision rules:
  - doc path change  → invalidate document_verdict only
  - runtime path change → invalidate runtime_verdict only (via verification_targets)
  - both → invalidate both
Conservative fallback (no file list): invalidate ALL verdicts on ALL open tasks.
Note freshness: if a changed file matches a note's invalidated_by_paths, set note freshness to suspect.
stdin: JSON | exit 0: always
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_array, yaml_field, yaml_array,
                  is_doc_path, find_tasks_touching_path,
                  find_tasks_with_verification_targets, manifest_field,
                  is_profile_enabled, TASK_DIR, MANIFEST, now_iso)

import re
import glob


def invalidate_runtime(state_file, task_id, reason):
    """If runtime_verdict is PASS → replace with pending, update timestamp."""
    rv = yaml_field("runtime_verdict", state_file)
    if rv == "PASS":
        content = open(state_file, encoding="utf-8").read()
        content = content.replace("runtime_verdict: PASS", "runtime_verdict: pending")
        content = re.sub(r'^updated: .*', f'updated: {now_iso()}', content, flags=re.MULTILINE)
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"INVALIDATED: {task_id} — runtime_verdict reset to pending ({reason})")


def invalidate_document(state_file, task_id, reason):
    """If document_verdict is PASS → replace with pending, set doc_changes_detected: true."""
    dv = yaml_field("document_verdict", state_file)
    if dv == "PASS":
        content = open(state_file, encoding="utf-8").read()
        content = content.replace("document_verdict: PASS", "document_verdict: pending")
        content = re.sub(r'^updated: .*', f'updated: {now_iso()}', content, flags=re.MULTILINE)
        with open(state_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"INVALIDATED: {task_id} — document_verdict reset to pending ({reason})")

    # Also set doc_changes_detected: true
    content = open(state_file, encoding="utf-8").read()
    if re.search(r'^doc_changes_detected:', content, flags=re.MULTILINE):
        content = re.sub(r'^doc_changes_detected: .*', 'doc_changes_detected: true', content, flags=re.MULTILINE)
    else:
        content = content.rstrip('\n') + '\ndoc_changes_detected: true\n'
    with open(state_file, "w", encoding="utf-8") as f:
        f.write(content)


def invalidate_note_freshness(changed_file):
    """Scan doc/*/*.md and *.yaml across all roots; if invalidated_by_paths contains the file → set freshness: suspect."""
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

    for note_file in note_files:
        if not os.path.isfile(note_file):
            continue
        try:
            content = open(note_file, encoding="utf-8").read()
        except OSError:
            continue
        if "invalidated_by_paths" not in content:
            continue
        if changed_file not in content:
            continue
        # Set freshness: suspect
        if re.search(r'^freshness:', content, flags=re.MULTILINE):
            content = re.sub(r'^freshness: .*', 'freshness: suspect', content, flags=re.MULTILINE)
        else:
            # Insert freshness after the first line
            lines = content.split('\n')
            lines.insert(1, 'freshness: suspect')
            content = '\n'.join(lines)
        with open(note_file, "w", encoding="utf-8") as f:
            f.write(content)
        print(f"NOTE SUSPECT: {note_file} — freshness set to suspect ({changed_file} changed)")


def process_changed_file(changed_file):
    """Classify doc/runtime, call appropriate invalidation."""
    changed_is_doc = is_doc_path(changed_file)
    changed_is_runtime = not changed_is_doc

    # Note freshness check (applies to all changed files)
    invalidate_note_freshness(changed_file)

    if changed_is_runtime:
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


def main():
    if not os.path.isfile(MANIFEST):
        sys.exit(0)
    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    hook_input = read_hook_input()

    # Parse changed files from stdin
    changed_files = json_array("files", hook_input)
    if not changed_files:
        changed_files = json_array("paths", hook_input)

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
