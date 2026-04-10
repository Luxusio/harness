#!/usr/bin/env python3
"""note_reverify.py — Bounded auto-reverification of suspect notes at task completion.

Called from task_completed_gate.py after the gate checks.
Finds suspect notes whose invalidated_by_paths overlap with the task's
touched_paths/verification_targets, then runs their verification_command.
On success: restores freshness to current. On failure: leaves suspect.

Constraints:
  - Max MAX_NOTES notes per completion (default 5)
  - Per-command timeout CMD_TIMEOUT seconds (default 10)
  - doc/ absent → no-op
  - No verification_command on note → skip (stays suspect)
  - Never blocks task completion — purely advisory recovery

No pip packages — stdlib only.
"""

import glob as _glob
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import parse_note_metadata, set_note_freshness, yaml_array, now_iso

MAX_NOTES = 5
CMD_TIMEOUT = 10


def collect_suspect_notes(doc_base="doc"):
    """Return list of (note_path, metadata_dict) for suspect notes with a verification_command."""
    if not os.path.isdir(doc_base):
        return []

    candidates = []
    for pattern in (
        os.path.join(doc_base, "*", "*.md"),
        os.path.join(doc_base, "*", "*.yaml"),
    ):
        for note_path in sorted(_glob.glob(pattern)):
            if not os.path.isfile(note_path):
                continue
            meta = parse_note_metadata(note_path)
            if meta["freshness"] == "suspect" and meta["verification_command"]:
                candidates.append((note_path, meta))

    return candidates


def paths_overlap(inv_paths, task_paths):
    """Return True if any inv_path matches any task_path (exact or prefix — not substring).

    Uses structural path comparison:
      - exact match: inv == task
      - directory prefix: task starts with inv + "/"
      - reverse prefix: inv starts with task + "/"

    Deliberately NOT a substring match to avoid false positives like
    matching 'src/api.py' against changed file 'src/api-v2.py'.
    """
    if not inv_paths or not task_paths:
        return False
    for inv in inv_paths:
        if not inv:
            continue
        for tp in task_paths:
            if not tp:
                continue
            if tp == inv:
                return True
            if tp.startswith(inv + "/"):
                return True
            if inv.startswith(tp + "/"):
                return True
    return False


def run_verification_command(cmd, cwd=None, timeout=CMD_TIMEOUT):
    """Run a shell verification command.

    Returns (success: bool, output: str).
    Captures both stdout and stderr.
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
        output = (result.stdout + result.stderr).strip()
        return result.returncode == 0, output
    except subprocess.TimeoutExpired:
        return False, f"Command timed out after {timeout}s"
    except Exception as e:
        return False, str(e)


def reverify_suspect_notes(
    task_dir,
    doc_base="doc",
    max_notes=MAX_NOTES,
    timeout=CMD_TIMEOUT,
):
    """Reverify suspect notes whose invalidated_by_paths overlap with the task's paths.

    Args:
        task_dir: path to task directory (reads touched_paths + verification_targets)
        doc_base: root doc directory (default: "doc")
        max_notes: max notes to reverify per call (default: MAX_NOTES)
        timeout: per-command timeout in seconds (default: CMD_TIMEOUT)

    Returns:
        list of (note_path, status) where status is one of:
          'recovered'  — verification_command succeeded, freshness → current
          'failed'     — verification_command failed, freshness stays suspect
          'skipped'    — no path overlap, not attempted
    """
    # Read task's touched_paths + verification_targets for overlap check
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_paths = []
    if os.path.isfile(state_file):
        task_paths += yaml_array("touched_paths", state_file)
        task_paths += yaml_array("verification_targets", state_file)
    task_paths = list(dict.fromkeys(p for p in task_paths if p))  # dedupe, preserve order

    results = []
    candidates = collect_suspect_notes(doc_base)
    reverified = 0

    for note_path, meta in candidates:
        if reverified >= max_notes:
            break

        inv_paths = meta["invalidated_by_paths"]
        cmd = meta["verification_command"]

        # Only attempt notes whose invalidated_by_paths overlap with task paths
        # If task_paths is empty, attempt all suspect notes (conservative)
        if task_paths and not paths_overlap(inv_paths, task_paths):
            results.append((note_path, "skipped"))
            continue

        reverified += 1
        success, output = run_verification_command(cmd, timeout=timeout)

        if success:
            set_note_freshness(note_path, "current", verified_at=now_iso())
            print(
                f"NOTE RECOVERED: {note_path} — freshness restored to current"
                f" (cmd: {cmd!r})"
            )
            results.append((note_path, "recovered"))
        else:
            truncated = output[:200] if output else "(no output)"
            print(
                f"NOTE REVERIFY FAILED: {note_path} — suspect retained"
                f" (cmd: {cmd!r}, output: {truncated})"
            )
            results.append((note_path, "failed"))

    return results


if __name__ == "__main__":
    # Standalone invocation: reverify for a given task_dir
    import argparse

    os.environ.setdefault("HARNESS_SKIP_STDIN", "1")
    parser = argparse.ArgumentParser(description="Reverify suspect notes for a task")
    parser.add_argument("task_dir", help="Task directory path")
    parser.add_argument("--doc-base", default="doc", help="Doc root (default: doc)")
    parser.add_argument("--max-notes", type=int, default=MAX_NOTES)
    parser.add_argument("--timeout", type=int, default=CMD_TIMEOUT)
    args = parser.parse_args()

    results = reverify_suspect_notes(
        args.task_dir,
        doc_base=args.doc_base,
        max_notes=args.max_notes,
        timeout=args.timeout,
    )
    recovered = sum(1 for _, s in results if s == "recovered")
    failed = sum(1 for _, s in results if s == "failed")
    skipped = sum(1 for _, s in results if s == "skipped")
    print(f"Reverify complete: {recovered} recovered, {failed} failed, {skipped} skipped")
