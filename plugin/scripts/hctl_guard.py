#!/usr/bin/env python3
"""PreToolUse write guard: require task + hctl start/context + approved plan.

This closes the broad Write/Edit/MultiEdit bypass where non-source files
(package.json, lockfiles, config, docs, assets) could be edited without a
tracked harness task. Protected artifacts (PLAN.md, HANDOFF.md, DOC_SYNC.md,
CRITIC__*.md, etc.) are excluded here and remain governed by prewrite_gate.py.

Blocking rules for guarded repo writes:
  1. There must be an active task.
  2. The active task must have routing_compiled=true (hctl start ran).
  3. The active task must have a recorded hctl context read
     (context_read_count > 0 and context_last_read_at set).
  4. PLAN.md must exist for the active task.
  5. plan_verdict must be PASS for the active task.

Escape hatches:
  - HARNESS_SKIP_PREWRITE=1      # shared emergency bypass
  - HARNESS_SKIP_HCTL_GUARD=1    # bypass this guard only
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_hook_input, yaml_field, TASK_DIR, MANIFEST
from prewrite_gate import (
    EXEMPT_FILENAMES,
    EXEMPT_PREFIXES,
    PROTECTED_ARTIFACT_OWNERS,
    _extract_file_path,
)

_DONE_STATUSES = {"closed", "archived", "stale"}


def _normalize_path(filepath: str | None) -> str:
    if not filepath:
        return ""
    fp = filepath
    if fp.startswith("./"):
        fp = fp[2:]
    cwd = os.getcwd()
    if os.path.isabs(fp):
        try:
            rel = os.path.relpath(fp, cwd)
            if not rel.startswith(".."):
                fp = rel
        except (OSError, ValueError):
            pass
    return fp.replace('\\', '/')


def _bool(value) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def _is_missing(value) -> bool:
    return str(value).strip().lower() in {"", "null", "none", "pending"}


def _task_display_path(task_dir: str) -> str:
    try:
        rel = os.path.relpath(task_dir, os.getcwd())
        if not rel.startswith(".."):
            return rel
    except (OSError, ValueError):
        pass
    return task_dir


def _is_guarded_write(filepath: str | None) -> bool:
    fp = _normalize_path(filepath)
    if not fp:
        return False

    basename = os.path.basename(fp)

    for prefix in EXEMPT_PREFIXES:
        if fp.startswith(prefix):
            return False

    if basename in EXEMPT_FILENAMES:
        return False

    if basename in PROTECTED_ARTIFACT_OWNERS:
        return False

    if basename.endswith('.meta.json'):
        return False

    return True


def _find_active_task_dir() -> str | None:
    if not os.path.isdir(TASK_DIR):
        return None

    best_dir = None
    best_updated = ""
    for entry in sorted(os.listdir(TASK_DIR)):
        if not entry.startswith('TASK__'):
            continue
        task_dir = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_dir):
            continue
        state_file = os.path.join(task_dir, 'TASK_STATE.yaml')
        if not os.path.isfile(state_file):
            continue
        status = yaml_field('status', state_file) or ''
        if status in _DONE_STATUSES:
            continue
        updated = yaml_field('updated', state_file) or ''
        if updated >= best_updated:
            best_updated = updated
            best_dir = task_dir
    return best_dir


def _task_failure(task_dir: str) -> str:
    state_file = os.path.join(task_dir, 'TASK_STATE.yaml')
    task_id = os.path.basename(task_dir)
    display_task_dir = _task_display_path(task_dir)
    start_cmd = f"python3 plugin/scripts/hctl.py start --task-dir {display_task_dir}"
    context_cmd = f"python3 plugin/scripts/hctl.py context --task-dir {display_task_dir} --json"

    routing_compiled = yaml_field('routing_compiled', state_file) or 'false'
    if not _bool(routing_compiled):
        return (
            f"BLOCKED: repo write requires compiled routing for {task_id}. "
            f"Run `{start_cmd}` then `{context_cmd}` before editing repository files."
        )

    context_count_raw = yaml_field('context_read_count', state_file) or '0'
    try:
        context_count = int(str(context_count_raw).strip() or '0')
    except ValueError:
        context_count = 0
    context_last = yaml_field('context_last_read_at', state_file) or ''
    if context_count < 1 or _is_missing(context_last):
        return (
            f"BLOCKED: repo write requires a recorded hctl context read for {task_id}. "
            f"Run `{context_cmd}` and use that JSON as the routing source before editing repository files."
        )

    plan_path = os.path.join(task_dir, 'PLAN.md')
    if not os.path.isfile(plan_path):
        return (
            f"BLOCKED: repo write requires PLAN.md for {task_id}. "
            f"Create the plan via /harness:plan before editing repository files."
        )

    plan_verdict = yaml_field('plan_verdict', state_file) or 'pending'
    if plan_verdict != 'PASS':
        return (
            f"BLOCKED: repo write requires critic-plan PASS for {task_id}. "
            f"Current plan_verdict={plan_verdict}. Get PLAN.md to PASS before editing repository files."
        )

    return ''


def main() -> None:
    if os.environ.get('HARNESS_SKIP_PREWRITE') or os.environ.get('HARNESS_SKIP_HCTL_GUARD'):
        raise SystemExit(0)

    hook_data = read_hook_input()
    if not hook_data:
        raise SystemExit(0)

    filepath = _extract_file_path(hook_data)
    if not filepath:
        raise SystemExit(0)

    if not os.path.isfile(MANIFEST):
        raise SystemExit(0)

    if not _is_guarded_write(filepath):
        raise SystemExit(0)

    task_dir = _find_active_task_dir()
    if not task_dir:
        print(
            f"BLOCKED: repo write to '{_normalize_path(filepath)}' requires an active harness task. "
            f"Create or resume a task, then run hctl start/context before editing repository files.",
            file=sys.stderr,
        )
        raise SystemExit(2)

    failure = _task_failure(task_dir)
    if failure:
        print(failure, file=sys.stderr)
        raise SystemExit(2)

    raise SystemExit(0)


if __name__ == '__main__':
    try:
        main()
    except SystemExit:
        raise
    except Exception as exc:
        if os.path.isfile(MANIFEST):
            print(
                f"BLOCKED: hctl_guard encountered an error: {exc}. "
                f"Fail-closed on managed repos. Set HARNESS_SKIP_HCTL_GUARD=1 to bypass.",
                file=sys.stderr,
            )
            raise SystemExit(2)
        raise SystemExit(0)
