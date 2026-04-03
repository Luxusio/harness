#!/usr/bin/env python3
"""Small on-disk indexes for task-history hot paths."""

from __future__ import annotations

import json
import os
import tempfile
from typing import Any

from _lib import TASK_DIR, now_iso, yaml_field

ACTIVE_TASK_FILENAME = "ACTIVE_TASK.json"
FAILURE_INDEX_FILENAME = "FAILURE_INDEX.json"
_CLOSED_STATUSES = {"closed", "archived", "stale"}


def _resolve_tasks_dir(tasks_dir: str | None = None) -> str:
    resolved = tasks_dir or TASK_DIR
    return os.path.normpath(resolved)


def _read_json(path: str) -> dict[str, Any]:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _write_json_atomic(path: str, payload: dict[str, Any]) -> str:
    if not path:
        return ""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp-task-index-", dir=parent or None)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp_path, path)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return ""
    return path


def active_task_index_path(tasks_dir: str | None = None) -> str:
    return os.path.join(_resolve_tasks_dir(tasks_dir), ACTIVE_TASK_FILENAME)


def failure_index_path(tasks_dir: str | None = None) -> str:
    return os.path.join(_resolve_tasks_dir(tasks_dir), FAILURE_INDEX_FILENAME)


def _task_metadata(task_dir: str) -> dict[str, Any]:
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return {}
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip(os.sep))
    status = (yaml_field("status", state_file) or "").strip()
    updated = (yaml_field("updated", state_file) or "").strip()
    return {
        "task_id": task_id,
        "status": status,
        "updated": updated,
        "task_dir": os.path.normpath(task_dir),
    }


def update_active_task(task_dir: str, tasks_dir: str | None = None) -> str:
    """Persist the currently active task for prompt hooks.

    Closed / archived / stale tasks clear the pointer instead of becoming active.
    """
    if not task_dir or not os.path.isdir(task_dir):
        return ""
    meta = _task_metadata(task_dir)
    if not meta:
        return ""
    if str(meta.get("status") or "").lower() in _CLOSED_STATUSES:
        clear_active_task(task_dir=task_dir, tasks_dir=tasks_dir)
        return ""
    payload = {
        "task_id": meta["task_id"],
        "status": meta["status"],
        "updated": meta["updated"],
        "task_dir": meta["task_dir"],
        "indexed_at": now_iso(),
    }
    return _write_json_atomic(active_task_index_path(tasks_dir or os.path.dirname(meta["task_dir"])), payload)


def clear_active_task(task_dir: str | None = None, tasks_dir: str | None = None) -> str:
    """Clear ACTIVE_TASK.json entirely or only when it points at task_dir."""
    path = active_task_index_path(tasks_dir or (os.path.dirname(task_dir) if task_dir else None))
    if not os.path.isfile(path):
        return ""
    if task_dir:
        current = _read_json(path)
        current_dir = os.path.normpath(str(current.get("task_dir") or ""))
        if current_dir and current_dir != os.path.normpath(task_dir):
            return ""
    try:
        os.remove(path)
        return path
    except OSError:
        return ""


def resolve_active_task_dir(tasks_dir: str | None = None) -> str | None:
    """Return the indexed active task dir when the pointer is still valid."""
    path = active_task_index_path(tasks_dir)
    payload = _read_json(path)
    task_dir = os.path.normpath(str(payload.get("task_dir") or ""))
    if not task_dir or not os.path.isdir(task_dir):
        return None
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None
    status = (yaml_field("status", state_file) or "").strip().lower()
    if status in _CLOSED_STATUSES:
        return None
    indexed_task_id = str(payload.get("task_id") or "").strip()
    actual_task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip(os.sep))
    if indexed_task_id and indexed_task_id != actual_task_id:
        return None
    indexed_updated = str(payload.get("updated") or "").strip()
    actual_updated = (yaml_field("updated", state_file) or "").strip()
    if indexed_updated and actual_updated and indexed_updated != actual_updated:
        return None
    return task_dir


def load_failure_index(tasks_dir: str | None = None) -> list[dict[str, Any]]:
    payload = _read_json(failure_index_path(tasks_dir))
    cases = payload.get("cases")
    if not isinstance(cases, dict):
        return []
    loaded: list[dict[str, Any]] = []
    for _, case in sorted(cases.items()):
        if isinstance(case, dict):
            loaded.append(dict(case))
    return loaded


def upsert_failure_case(case_payload: dict[str, Any], tasks_dir: str | None = None) -> str:
    if not isinstance(case_payload, dict):
        return ""
    task_id = str(case_payload.get("task_id") or "").strip()
    if not task_id:
        return ""
    path = failure_index_path(tasks_dir)
    index_payload = _read_json(path)
    cases = index_payload.get("cases")
    if not isinstance(cases, dict):
        cases = {}
    cases[task_id] = dict(case_payload)
    new_payload = {
        "updated_at": now_iso(),
        "cases": cases,
    }
    return _write_json_atomic(path, new_payload)
