#!/usr/bin/env python3
"""Observe Codex subagent lifecycle events that plugin PostToolUse omits.

Trusted Codex root hooks register or restore the current root rollout. The Harness
MCP server hosts the watcher as daemon threads, captures the source fingerprint
before a child finishes, and accepts a completion only when root delivery and
the child rollout agree.  It never reconstructs a PASS from historical finals
and never launches a detached operating-system process.
"""
from __future__ import annotations

import argparse
import hashlib
try:
    import fcntl
except ImportError:  # pragma: no cover - Codex currently ships POSIX hooks
    fcntl = None
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time
from typing import Any

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from _lib import (  # type: ignore
    _infer_receipt_lens,
    extract_qa_verdict,
    find_harness_root,
    find_repo_root,
    list_review_receipts,
    list_subagent_receipts,
    record_subagent_receipt,
    read_state,
    resolve_active_task_dir,
    review_diff_fingerprint,
)

THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")
CALL_RE = re.compile(r"^[A-Za-z0-9_.:-]{6,160}$")
AGENT_PATH_RE = re.compile(r"^/root/[A-Za-z0-9_.-]{1,120}$")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
TASK_NAME_LINE_RE = re.compile(r"^task_name: ([A-Za-z0-9_.-]{1,120})$")
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_CHILD_BYTES = 64 * 1024 * 1024
POLL_SECONDS = 0.20
IDLE_SECONDS = 8 * 60 * 60
REGISTRATION_TTL_SECONDS = IDLE_SECONDS
MAX_WATCHER_THREADS = 16
RUNTIME_SUBDIR = os.path.join("harness", "codex-watchers")
REGISTRATION_VERSION = 4
REGISTRATION_OWNER = "codex_root_hook"
LEGACY_REGISTRATION_VERSION = 3
LEGACY_REGISTRATION_OWNER = "codex_root_hook"
OLDER_REGISTRATION_VERSION = 2
OLDER_REGISTRATION_OWNER = "session_start_hook"


def _authorized_control_root(session_cwd: str) -> str:
    """Resolve an enabled workspace, preserving direct Git-root compatibility."""
    resolved = find_harness_root(session_cwd)
    if resolved:
        return resolved
    session = os.path.realpath(session_cwd)
    git_path = os.path.join(session, ".git")
    if os.path.isdir(git_path) or os.path.isfile(git_path):
        return session
    return ""


def _deadline_expired(deadline: float | None) -> bool:
    return deadline is not None and time.monotonic() >= deadline


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()


def _sessions_root() -> Path:
    return (_codex_home() / "sessions").resolve()


def _trusted_parent_tree(path: Path, root: Path) -> bool:
    """Require an owner-controlled, non-symlink directory chain below root."""
    try:
        root = root.resolve(strict=True)
        absolute = path.absolute()
        relative = absolute.relative_to(root)
        cursor = root
        root_info = os.lstat(cursor)
        if (
            stat.S_ISLNK(root_info.st_mode)
            or not stat.S_ISDIR(root_info.st_mode)
            or root_info.st_uid != os.getuid()
            or root_info.st_mode & 0o022
        ):
            return False
        for part in relative.parts[:-1]:
            if part:
                cursor = cursor / part
            info = os.lstat(cursor)
            if (
                stat.S_ISLNK(info.st_mode)
                or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o022
            ):
                return False
        return True
    except (OSError, ValueError):
        return False


def _open_trusted_file(
    path: Path,
    root: Path,
    *,
    max_size: int | None = None,
) -> tuple[Any, os.stat_result] | None:
    """Open one trusted file and bind validation to the returned descriptor."""
    if not _trusted_parent_tree(path, root):
        return None
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        info = os.fstat(fd)
        path_info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or stat.S_ISLNK(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)
            or (max_size is not None and info.st_size > max_size)
        ):
            os.close(fd)
            return None
        return os.fdopen(fd, "rb"), info
    except OSError:
        try:
            os.close(fd)
        except (OSError, UnboundLocalError):
            pass
        return None


def _path_matches_handle(
    path: Path,
    root: Path,
    handle: Any,
    *,
    max_size: int | None = None,
) -> bool:
    """Reject pathname replacement after a trusted descriptor was opened."""
    current = _open_trusted_file(path, root, max_size=max_size)
    if current is None:
        return False
    current_handle, current_info = current
    try:
        original = os.fstat(handle.fileno())
        return (current_info.st_dev, current_info.st_ino) == (original.st_dev, original.st_ino)
    finally:
        current_handle.close()


def _safe_regular_file(path: Path, root: Path, *, max_size: int | None = None) -> bool:
    opened = _open_trusted_file(path, root, max_size=max_size)
    if opened is None:
        return False
    handle, _ = opened
    handle.close()
    return True


def _find_rollout(thread_id: str, *, deadline: float | None = None) -> Path | None:
    if not THREAD_RE.fullmatch(thread_id):
        return None
    root = _sessions_root()
    candidates: list[Path] = []
    try:
        for directory, _subdirs, filenames in os.walk(root):
            if _deadline_expired(deadline):
                return None
            suffix = f"{thread_id}.jsonl"
            for name in filenames:
                if name.startswith("rollout-") and name.endswith(suffix):
                    candidates.append(Path(directory) / name)
                    if len(candidates) >= 3:
                        break
            if len(candidates) >= 3:
                break
    except OSError:
        return None
    valid = [path for path in candidates if _safe_regular_file(path, root)]
    return valid[0] if len(valid) == 1 else None


def _load_json_line(raw: bytes) -> dict[str, Any] | None:
    if not raw.endswith(b"\n") or len(raw) > MAX_LINE_BYTES:
        return None
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _root_meta_from_handle(handle: Any, thread_id: str, session_cwd: str) -> bool:
    handle.seek(0)
    for _ in range(8):
        raw = handle.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        event = _load_json_line(raw)
        if not event or event.get("type") != "session_meta":
            continue
        payload = event.get("payload") or {}
        if (
            str(payload.get("id") or "") == thread_id
            and str(payload.get("session_id") or "") == thread_id
            and os.path.realpath(str(payload.get("cwd") or "")) == session_cwd
            and payload.get("thread_source") != "subagent"
        ):
            return True
    return False


def _root_meta(path: Path, thread_id: str, session_cwd: str) -> bool:
    trust_root = _sessions_root()
    opened = _open_trusted_file(path, trust_root)
    if opened is None:
        return False
    handle, _ = opened
    try:
        return _root_meta_from_handle(handle, thread_id, session_cwd) and _path_matches_handle(
            path, trust_root, handle
        )
    finally:
        handle.close()


def _runtime_dir(repo_root: str) -> Path:
    repo_key = hashlib.sha256(os.path.realpath(repo_root).encode("utf-8")).hexdigest()
    if os.environ.get("CODEX_HOME"):
        base = _codex_home() / "harness-runtime" / "codex-watchers"
    else:
        state_home = Path(
            os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state"
        ).resolve()
        base = state_home / RUNTIME_SUBDIR
    return base / repo_key


def _state_path(repo_root: str, thread_id: str) -> Path:
    return _runtime_dir(repo_root) / f"{thread_id}.json"


def _lock_path(repo_root: str, thread_id: str) -> Path:
    return _runtime_dir(repo_root) / f"{thread_id}.lock"


def _lease_path(repo_root: str, thread_id: str) -> Path:
    return _runtime_dir(repo_root) / f"{thread_id}.lease"


def _acquire_registration_lease(repo_root: str, thread_id: str) -> Any | None:
    """Hold one cross-process watcher lease for a root registration."""
    if fcntl is None:
        return None
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return None
    path = _lease_path(repo_root, thread_id)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o600)
        info = os.fstat(fd)
        path_info = os.lstat(path)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
            or info.st_mode & 0o022
            or stat.S_ISLNK(path_info.st_mode)
            or (path_info.st_dev, path_info.st_ino) != (info.st_dev, info.st_ino)
        ):
            os.close(fd)
            return None
        os.fchmod(fd, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return os.fdopen(fd, "a+", encoding="utf-8")
    except OSError:
        try:
            os.close(fd)
        except (OSError, UnboundLocalError):
            pass
        return None


def _trusted_runtime_dir(repo_root: str) -> Path | None:
    runtime = _runtime_dir(repo_root)
    anchor = (_codex_home().parent if os.environ.get("CODEX_HOME") else Path.home()).resolve()
    try:
        relative = runtime.relative_to(anchor)
    except ValueError:
        return None
    cursor = anchor
    try:
        anchor_info = os.lstat(anchor)
        if (
            stat.S_ISLNK(anchor_info.st_mode) or not stat.S_ISDIR(anchor_info.st_mode)
            or anchor_info.st_uid != os.getuid() or anchor_info.st_mode & 0o022
        ):
            return None
        for part in relative.parts:
            cursor = cursor / part
            if cursor.exists():
                info = os.lstat(cursor)
                if (
                    stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o022
                ):
                    return None
            else:
                cursor.mkdir(mode=0o700)
        os.chmod(cursor, 0o700)
        return cursor
    except OSError:
        return None


def _read_owned_json(path: Path, root: Path) -> dict[str, Any]:
    if not _safe_regular_file(path, root, max_size=64 * 1024):
        return {}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
        with os.fdopen(fd, encoding="utf-8") as handle:
            value = json.load(handle)
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(value, handle, sort_keys=True)
            handle.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _valid_current_registration(repo_root: str, thread_id: str) -> bool:
    """Validate the exact v3 state without discovery, locking, or rewriting."""
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return False
    state = _read_owned_json(_state_path(repo_root, thread_id), runtime)
    offset = state.get("offset")
    registered_at = state.get("registered_at")
    session_cwd = state.get("session_cwd")
    if not (
        state.get("version") == REGISTRATION_VERSION
        and state.get("owner") == REGISTRATION_OWNER
        and state.get("thread_id") == thread_id
        and state.get("repo_root") == repo_root
        and isinstance(session_cwd, str)
        and _authorized_control_root(session_cwd) == repo_root
        and isinstance(offset, int)
        and not isinstance(offset, bool)
        and offset >= 0
        and isinstance(registered_at, (int, float))
        and not isinstance(registered_at, bool)
    ):
        return False
    rollout_value = state.get("rollout")
    if not isinstance(rollout_value, str):
        return False
    rollout = Path(rollout_value)
    trust_root = _sessions_root()
    opened = _open_trusted_file(rollout, trust_root)
    if opened is None:
        return False
    handle, info = opened
    try:
        return (
            offset <= info.st_size
            and _root_meta_from_handle(handle, thread_id, os.path.realpath(session_cwd))
            and _path_matches_handle(rollout, trust_root, handle)
        )
    finally:
        handle.close()


def ensure(
    repo_root: str,
    thread_id: str,
    *,
    session_cwd: str | None = None,
    deadline: float | None = None,
) -> bool:
    """Register a root rollout for the MCP-hosted watcher manager.

    This entry point is owned by trusted Codex root hooks. It performs only
    validation and an atomic registry write; it never forks or writes a review
    or QA receipt. Existing valid registration offsets are immutable.
    """
    if not THREAD_RE.fullmatch(thread_id):
        return False
    repo_root = os.path.realpath(repo_root)
    session_cwd = os.path.realpath(session_cwd or repo_root)
    if _authorized_control_root(session_cwd) != repo_root:
        return False
    if _valid_current_registration(repo_root, thread_id):
        return True
    rollout = _find_rollout(thread_id, deadline=deadline)
    if rollout is None or _deadline_expired(deadline):
        return False
    trust_root = _sessions_root()
    opened = _open_trusted_file(rollout, trust_root)
    if opened is None:
        return False
    rollout_handle, rollout_info = opened
    try:
        if not _root_meta_from_handle(rollout_handle, thread_id, session_cwd):
            return False
        if not _path_matches_handle(rollout, trust_root, rollout_handle):
            return False
        offset = rollout_info.st_size
    finally:
        rollout_handle.close()
    if offset < 0 or _deadline_expired(deadline):
        return False
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return False
    lock_path = _lock_path(repo_root, thread_id)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        lock_info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
            or lock_info.st_nlink != 1 or lock_info.st_mode & 0o022
        ):
            os.close(lock_fd)
            return False
        os.fchmod(lock_fd, 0o600)
    except OSError:
        return False
    with os.fdopen(lock_fd, "a+", encoding="utf-8") as lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
        state_path = _state_path(repo_root, thread_id)
        state = _read_owned_json(state_path, runtime)
        if _deadline_expired(deadline):
            return False
        state_offset = state.get("offset")
        registered_at = state.get("registered_at")
        tuple_valid = (
            state.get("thread_id") == thread_id
            and state.get("repo_root") == repo_root
            and state.get("session_cwd") in {None, "", session_cwd}
            and state.get("rollout") == str(rollout)
            and isinstance(state_offset, int)
            and not isinstance(state_offset, bool)
            and 0 <= state_offset <= rollout_info.st_size
            and isinstance(registered_at, (int, float))
            and not isinstance(registered_at, bool)
        )
        if tuple_valid and (
            state.get("version") == REGISTRATION_VERSION
            and state.get("owner") == REGISTRATION_OWNER
            and state.get("session_cwd") == session_cwd
        ):
            return True
        if tuple_valid and (
            (state.get("version"), state.get("owner")) in {
                (LEGACY_REGISTRATION_VERSION, LEGACY_REGISTRATION_OWNER),
                (OLDER_REGISTRATION_VERSION, OLDER_REGISTRATION_OWNER),
            }
        ):
            offset = state_offset
        else:
            registered_at = time.time()
        _atomic_json(state_path, {
            "version": REGISTRATION_VERSION,
            "repo_root": repo_root,
            "session_cwd": session_cwd,
            "thread_id": thread_id,
            "rollout": str(rollout),
            "offset": offset,
            "registered_at": registered_at,
            "owner": REGISTRATION_OWNER,
        })
        return True


def registrations(repo_root: str) -> list[dict[str, Any]]:
    """Return safe, exact registrations for one repository."""
    repo_root = os.path.realpath(repo_root)
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return []
    found: list[dict[str, Any]] = []
    now = time.time()
    try:
        candidates = list(runtime.glob("*.json"))
    except OSError:
        return []
    for path in candidates:
        state = _read_owned_json(path, runtime)
        thread_id = str(state.get("thread_id") or "")
        rollout = _find_rollout(thread_id)
        offset = state.get("offset")
        registered_at = state.get("registered_at")
        session_cwd = state.get("session_cwd")
        if (
            state.get("version") != REGISTRATION_VERSION
            or state.get("owner") != REGISTRATION_OWNER
            or state.get("repo_root") != repo_root
            or not isinstance(session_cwd, str)
            or _authorized_control_root(session_cwd) != repo_root
            or path.name != f"{thread_id}.json"
            or rollout is None
            or state.get("rollout") != str(rollout)
            or not _root_meta(rollout, thread_id, os.path.realpath(session_cwd))
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not isinstance(registered_at, (int, float))
            or isinstance(registered_at, bool)
        ):
            continue
        trust_root = _sessions_root()
        opened = _open_trusted_file(rollout, trust_root)
        if opened is None:
            continue
        rollout_handle, rollout_info = opened
        try:
            if offset > rollout_info.st_size or not _path_matches_handle(
                rollout, trust_root, rollout_handle
            ):
                continue
        finally:
            rollout_handle.close()
        activity = max(float(registered_at), rollout_info.st_mtime)
        if now - activity > REGISTRATION_TTL_SECONDS:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        item = dict(state)
        item["activity_at"] = activity
        found.append(item)
    found.sort(key=lambda item: float(item["activity_at"]), reverse=True)
    return found[:MAX_WATCHER_THREADS]


def _validated_task_dir(repo_root: str, task_id: str) -> str:
    if not re.fullmatch(r"TASK__[A-Za-z0-9_.-]{1,180}", task_id):
        return ""
    try:
        canonical_repo = Path(repo_root).resolve(strict=True)
        tasks_root = canonical_repo
        for part in ("doc", "harness", "tasks"):
            tasks_root = tasks_root / part
            component = os.lstat(tasks_root)
            trusted_owner = component.st_uid == os.getuid() or (
                component.st_uid == 0 and not component.st_mode & 0o022
            )
            if (
                stat.S_ISLNK(component.st_mode)
                or not stat.S_ISDIR(component.st_mode)
                or not trusted_owner
            ):
                return ""
        if tasks_root.resolve(strict=True) != tasks_root.absolute():
            return ""
        raw_task_dir = (tasks_root / task_id).absolute()
        raw_info = os.lstat(raw_task_dir)
        if stat.S_ISLNK(raw_info.st_mode):
            return ""
        task_dir = raw_task_dir.resolve(strict=True)
        if (
            task_dir.parent != tasks_root
            or task_dir.name != task_id
            or canonical_repo not in task_dir.parents
        ):
            return ""
        info = os.lstat(task_dir)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return ""
    except (OSError, ValueError):
        return ""
    if not _safe_regular_file(task_dir / "TASK_STATE.yaml", task_dir, max_size=256 * 1024):
        return ""
    state = read_state(str(task_dir))
    if state.get("task_id") != task_id or str(state.get("status") or "").lower() in {"closed", "blocked"}:
        return ""
    return str(task_dir)


def _active_task_for_session(repo_root: str, root_id: str) -> str:
    tasks_root = (Path(repo_root) / "doc/harness/tasks").resolve()
    marker = tasks_root / ".active_sessions" / f"{root_id}.json"
    marker_data = _read_owned_json(marker, tasks_root)
    if marker_data.get("session_id") != root_id:
        return ""
    resolved = resolve_active_task_dir(repo_root, session_id=root_id)
    if not resolved or marker_data.get("task_dir") != resolved:
        return ""
    task_id = str(marker_data.get("task_id") or "")
    task_dir = _validated_task_dir(repo_root, task_id)
    if not task_dir or os.path.realpath(resolved) != task_dir:
        return ""
    return task_dir


def _event_payload(event: dict[str, Any], expected_type: str) -> dict[str, Any] | None:
    payload = event.get("payload")
    if event.get("type") != expected_type or not isinstance(payload, dict):
        return None
    return payload


def _valid_agent_identity(value: str) -> bool:
    return bool(AGENT_PATH_RE.fullmatch(value) or THREAD_RE.fullmatch(value))


def _json_arguments(payload: dict[str, Any]) -> dict[str, Any] | None:
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        return arguments
    if not isinstance(arguments, str):
        return None
    try:
        decoded = json.loads(arguments or "{}")
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _prompt_texts(arguments: dict[str, Any]) -> list[str]:
    texts: list[str] = []
    message = arguments.get("message")
    if isinstance(message, str):
        texts.append(message)
    items = arguments.get("items")
    if not isinstance(items, list):
        return texts
    for item in items:
        if isinstance(item, str):
            texts.append(item)
        elif isinstance(item, dict):
            for key in ("message", "text"):
                value = item.get(key)
                if isinstance(value, str):
                    texts.append(value)
    return texts


def _prompt_task_name(arguments: dict[str, Any]) -> str:
    """Accept exactly one task marker, and only as a prompt's first line."""
    markers: list[tuple[int, str]] = []
    for text in _prompt_texts(arguments):
        for line_number, line in enumerate(text.splitlines()):
            match = TASK_NAME_LINE_RE.fullmatch(line)
            if match:
                markers.append((line_number, match.group(1)))
    if len(markers) != 1 or markers[0][0] != 0:
        return ""
    return markers[0][1]


def _exec_spawn_arguments(source: str) -> dict[str, Any] | None:
    if len(re.findall(r"multi_agent_v1__spawn_agent\s*\(", source)) != 1:
        return None
    structured = re.search(
        r"""(?:["']?task_name["']?)\s*:\s*["']([A-Za-z0-9_.-]{1,120})["']""",
        source,
    )
    if structured:
        return {"task_name": structured.group(1)}
    encoded_message = re.search(
        r'''(?:["']?message["']?)\s*:\s*("(?:\\.|[^"\\])*")''',
        source,
    )
    if not encoded_message:
        return None
    try:
        message = json.loads(encoded_message.group(1))
    except json.JSONDecodeError:
        return None
    return {"message": message} if isinstance(message, str) else None


def _tool_identity(payload: dict[str, Any]) -> tuple[str, str]:
    namespace = str(payload.get("namespace") or "")
    name = str(payload.get("name") or "")
    if name.startswith("multi_agent_v1__"):
        return "multi_agent_v1", name.removeprefix("multi_agent_v1__")
    return namespace, name


def _spawn_call(event: dict[str, Any]) -> tuple[str, str, bool] | None:
    payload = _event_payload(event, "response_item")
    if not payload:
        return None
    namespace, name = _tool_identity(payload)
    current_protocol = namespace == "multi_agent_v1" and name == "spawn_agent"
    if payload.get("type") == "function_call" and (
        (namespace == "collaboration" and name == "spawn_agent") or current_protocol
    ):
        arguments = _json_arguments(payload)
        if arguments is None:
            return None
    elif payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
        source = str(payload.get("input") or "")
        arguments = _exec_spawn_arguments(source)
        if arguments is None:
            return None
        current_protocol = True
    else:
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    task_name = str(arguments.get("task_name") or "")
    if task_name and not TASK_NAME_RE.fullmatch(task_name):
        return None
    if not task_name:
        task_name = _prompt_task_name(arguments)
    lens = _infer_receipt_lens(task_name)
    if not lens.startswith(("review-", "qa-", "ux-")):
        return None
    return call_id, task_name, current_protocol


def _unsupported_current_spawn(event: dict[str, Any]) -> tuple[str, str] | None:
    """Identify a recognizable current spawn that failed strict normalization."""
    payload = _event_payload(event, "response_item")
    if not payload:
        return None
    namespace, name = _tool_identity(payload)
    arguments: dict[str, Any] | None = None
    if payload.get("type") == "function_call" and (
        namespace == "multi_agent_v1" and name == "spawn_agent"
    ):
        arguments = _json_arguments(payload)
    elif payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
        source = str(payload.get("input") or "")
        if len(re.findall(r"multi_agent_v1__spawn_agent\s*\(", source)) != 1:
            return None
        arguments = _exec_spawn_arguments(source)
    else:
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    if arguments is None:
        return None
    raw_name = str(arguments.get("task_name") or "")
    marker_lines = [
        line for text in _prompt_texts(arguments) for line in text.splitlines()
        if "task_name" in line.lower()
    ]
    candidates = [raw_name, *marker_lines]
    receipt_intended = any(
        _infer_receipt_lens(candidate).startswith(("review-", "qa-", "ux-"))
        for candidate in candidates if candidate
    )
    if receipt_intended:
        return call_id, "strict task_name field or first-line marker was invalid"
    return None


def _spawn_output(
    event: dict[str, Any], *, require_agent_id: bool = False,
) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") not in {
        "function_call_output", "custom_tool_call_output",
    }:
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    output = payload.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            identity_field = "agent_id" if require_agent_id else "(?:agent_id|agent_name)"
            identity_key = (
                rf"(?:[\"']{identity_field}[\"']|{identity_field})"
            )
            match = re.search(
                rf"""(?:^|[{{,]\s*){identity_key}\s*[:=]\s*["']?([A-Za-z0-9_./:-]{{6,160}})""",
                output,
            )
            output = {"agent_id": match.group(1)} if match else {}
    if not isinstance(output, dict):
        return None
    if require_agent_id:
        identity = str(output.get("agent_id") or "")
    else:
        identity = str(
            output.get("agent_id")
            or output.get("agent_name")
            or output.get("agentName")
            or output.get("task_name")
            or ""
        )
    return (call_id, identity) if _valid_agent_identity(identity) else None


def _completion_call(event: dict[str, Any]) -> tuple[str, str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload:
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    if payload.get("type") == "function_call":
        namespace, name = _tool_identity(payload)
        if namespace not in {"collaboration", "multi_agent_v1"} or name not in {
            "wait_agent", "close_agent",
        }:
            return None
        arguments = _json_arguments(payload)
        if arguments is None:
            return None
    elif payload.get("type") == "custom_tool_call" and payload.get("name") == "exec":
        source = str(payload.get("input") or "")
        matches = re.findall(r"multi_agent_v1__(wait_agent|close_agent)\s*\(", source)
        if len(matches) != 1:
            return None
        name = matches[0]
        arguments = {}
        if name == "close_agent":
            target_match = re.search(
                r'''(?:["']?(?:agent_id|target)["']?)\s*:\s*["']([A-Za-z0-9_./:-]{6,160})["']''',
                source,
            )
            if not target_match:
                return None
            arguments = {"agent_id": target_match.group(1)}
    else:
        return None
    target = ""
    if name == "close_agent":
        target = str(arguments.get("agent_id") or arguments.get("target") or "")
        if not _valid_agent_identity(target):
            return None
    return call_id, name, target


def _completion_output(
    event: dict[str, Any], completion_call: tuple[str, str] | None,
) -> list[tuple[str, str]]:
    if completion_call is None:
        return []
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") not in {
        "function_call_output", "custom_tool_call_output",
    }:
        return []
    output = payload.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return []
    if not isinstance(output, dict):
        return []
    kind, target = completion_call
    deliveries: list[tuple[str, str]] = []
    if kind == "wait_agent":
        statuses = output.get("status")
        if not isinstance(statuses, dict):
            return []
        for identity, status in statuses.items():
            final = status.get("completed") if isinstance(status, dict) else None
            identity = str(identity)
            if _valid_agent_identity(identity) and isinstance(final, str) and final:
                deliveries.append((identity, final.strip()))
    elif kind == "close_agent":
        previous = output.get("previous_status")
        final = previous.get("completed") if isinstance(previous, dict) else None
        if _valid_agent_identity(target) and isinstance(final, str) and final:
            deliveries.append((target, final.strip()))
    return deliveries


def _started_activity(event: dict[str, Any]) -> tuple[str, str, str] | None:
    payload = _event_payload(event, "event_msg")
    if not payload or payload.get("type") != "sub_agent_activity" or payload.get("kind") != "started":
        return None
    call_id = str(payload.get("event_id") or "")
    child_id = str(payload.get("agent_thread_id") or "")
    agent_path = str(payload.get("agent_path") or "")
    if not CALL_RE.fullmatch(call_id) or not THREAD_RE.fullmatch(child_id):
        return None
    if not _valid_agent_identity(agent_path):
        return None
    return call_id, child_id, agent_path


def _root_delivery(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload:
        payload = _event_payload(event, "event_msg")
        if not payload or payload.get("type") != "subagent_notification":
            return None
        identity = str(payload.get("agent_id") or payload.get("agent_path") or "")
        status = payload.get("status")
        final = status.get("completed") if isinstance(status, dict) else None
        if _valid_agent_identity(identity) and isinstance(final, str) and final:
            return identity, final.strip()
        return None
    if payload.get("type") == "agent_message":
        author = str(payload.get("author") or "")
        if not _valid_agent_identity(author) or payload.get("recipient") != "/root":
            return None
        text_parts = []
        for item in payload.get("content") or []:
            if isinstance(item, dict) and item.get("type") == "input_text":
                text_parts.append(str(item.get("text") or ""))
        text = "\n".join(text_parts)
        if not text.startswith("Message Type: FINAL_ANSWER\n"):
            return None
        marker = "Payload:\n"
        final = text.split(marker, 1)[1] if marker in text else text
        return author, final.strip()
    if payload.get("type") != "message" or payload.get("role") != "user":
        return None
    for item in payload.get("content") or []:
        if not isinstance(item, dict) or item.get("type") != "input_text":
            continue
        text = str(item.get("text") or "").strip()
        if not text.startswith("<subagent_notification>") or not text.endswith(
            "</subagent_notification>"
        ):
            continue
        encoded = text[len("<subagent_notification>"):-len("</subagent_notification>")].strip()
        try:
            notification = json.loads(encoded)
        except json.JSONDecodeError:
            continue
        identity = str(notification.get("agent_id") or notification.get("agent_path") or "")
        status = notification.get("status")
        final = status.get("completed") if isinstance(status, dict) else None
        if _valid_agent_identity(identity) and isinstance(final, str) and final:
            return identity, final.strip()
    return None


def _task_binding(event: dict[str, Any], repo_root: str) -> str:
    """Return a task named by this root's successful Harness MCP event."""
    payload = _event_payload(event, "event_msg")
    if not payload or payload.get("type") != "mcp_tool_call_end":
        return ""
    invocation = payload.get("invocation")
    result = payload.get("result")
    if not isinstance(invocation, dict) or not isinstance(result, dict):
        return ""
    ok = result.get("Ok")
    if not isinstance(ok, dict):
        return ""
    if invocation.get("server") != "harness" or invocation.get("tool") not in {"task_start", "task_context"}:
        return ""
    arguments = invocation.get("arguments")
    if not isinstance(arguments, dict):
        return ""
    structured = ok.get("structuredContent") or ok.get("structured_content")
    if not isinstance(structured, dict):
        return ""
    context = structured.get("task_context")
    context_task_id = context.get("task_id") if isinstance(context, dict) else ""
    result_ids = {
        str(value) for value in (structured.get("task_id"), context_task_id) if value
    }
    if len(result_ids) != 1:
        return ""
    task_id = next(iter(result_ids))
    argument_task_id = str(arguments.get("task_id") or "")
    if argument_task_id and argument_task_id != task_id:
        return ""
    task_dir = _validated_task_dir(repo_root, task_id)
    result_task_dir = structured.get("task_dir")
    if not task_dir or not isinstance(result_task_dir, str):
        return ""
    return task_dir if os.path.realpath(result_task_dir) == task_dir else ""


def _child_status(
    child_id: str,
    root_id: str,
    agent_path: str,
    session_cwd: str,
    task_name: str = "",
) -> tuple[str, Path | None, str]:
    path = _find_rollout(child_id)
    trust_root = _sessions_root()
    if path is None:
        return "pending", None, ""
    opened = _open_trusted_file(path, trust_root, max_size=MAX_CHILD_BYTES)
    if opened is None:
        return "pending", None, ""
    handle, _ = opened
    matching_meta = 0
    child_boundaries = 0
    child_turn = False
    finals: list[str] = []
    completes: list[str] = []
    try:
        while True:
            raw = handle.readline(MAX_LINE_BYTES + 1)
            if not raw:
                break
            if len(raw) > MAX_LINE_BYTES:
                return "invalid", path, ""
            if not raw.endswith(b"\n"):
                return "pending", path, ""
            event = _load_json_line(raw)
            if event is None:
                return "invalid", path, ""
            payload = event.get("payload") or {}
            if event.get("type") == "session_meta" and payload.get("id") == child_id:
                source = payload.get("source") or {}
                spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
                if (
                    payload.get("session_id") == root_id
                    and payload.get("parent_thread_id") == root_id
                    and os.path.realpath(str(payload.get("cwd") or "")) == session_cwd
                    and payload.get("agent_path") == agent_path
                    and spawn.get("parent_thread_id") == root_id
                    and spawn.get("agent_path") == agent_path
                    and spawn.get("depth") == 1
                ):
                    matching_meta += 1
            if event.get("type") == "response_item" and payload.get("type") == "agent_message":
                if payload.get("author") == "/root" and payload.get("recipient") == agent_path:
                    content = payload.get("content") or []
                    texts = [
                        str(item.get("text") or "") for item in content
                        if isinstance(item, dict) and item.get("type") == "input_text"
                    ]
                    if any("Message Type: NEW_TASK" in text for text in texts):
                        child_boundaries += 1
                        child_turn = True
                        finals.clear()
                        completes.clear()
                        continue
            if (
                event.get("type") == "response_item"
                and payload.get("type") == "message"
                and payload.get("role") == "user"
                and task_name
            ):
                texts = [
                    str(item.get("text") or "") for item in payload.get("content") or []
                    if isinstance(item, dict) and item.get("type") == "input_text"
                ]
                if any(
                    (text.strip().splitlines() or [""])[0].strip()
                    == f"task_name: {task_name}"
                    for text in texts
                ):
                    child_boundaries += 1
                    child_turn = True
                    finals.clear()
                    completes.clear()
                    continue
            if child_turn and event.get("type") == "event_msg" and payload.get("type") == "agent_message" and payload.get("phase") == "final_answer":
                finals.append(str(payload.get("message") or "").strip())
            if child_turn and event.get("type") == "event_msg" and payload.get("type") == "task_complete":
                completes.append(str(payload.get("last_agent_message") or "").strip())
    except OSError:
        return "pending", path, ""
    finally:
        identity_matches = _path_matches_handle(path, trust_root, handle, max_size=MAX_CHILD_BYTES)
        handle.close()
    if not identity_matches:
        return "invalid", path, ""
    if matching_meta == 0 and child_boundaries == 0:
        return "pending", path, ""
    if matching_meta != 1:
        return "invalid", path, ""
    if child_boundaries == 0:
        return "pending", path, ""
    if child_boundaries != 1:
        return "invalid", path, ""
    if not finals and not completes:
        return "running", path, ""
    if len(finals) != 1 or len(completes) != 1:
        return "invalid", path, ""
    if not finals[0] or finals[0] != completes[0]:
        return "invalid", path, ""
    return "complete", path, finals[0]


def _matching_receipt(
    task_dir: str,
    event_id: str,
    status: str,
    *,
    agent_path: str = "",
    lens: str = "",
    summary: str = "",
) -> dict[str, Any] | None:
    for item in list_review_receipts(task_dir) + list_subagent_receipts(task_dir):
        if item.get("runtime_event_id") == event_id and item.get("status") == status:
            return item
        if (
            agent_path
            and lens
            and item.get("agent_id") == agent_path
            and item.get("lens") == lens
            and item.get("status") == status
            and (not summary or item.get("summary") == summary[:1000])
        ):
            return item
    return None


def _exact_receipt(
    task_dir: str,
    event_id: str,
    status: str,
    *,
    root_id: str,
    child_id: str,
    agent_path: str,
    lens: str,
) -> dict[str, Any] | None:
    for item in list_review_receipts(task_dir) + list_subagent_receipts(task_dir):
        if (
            item.get("runtime_event_id") == event_id
            and item.get("status") == status
            and item.get("runtime_session_id") == root_id
            and item.get("runtime_thread_id") == child_id
            and item.get("runtime_agent_path") == agent_path
            and item.get("lens") == lens
        ):
            return item
    return None


class Watcher:
    def __init__(self, repo_root: str, root_id: str, *, session_cwd: str | None = None):
        self.repo_root = repo_root
        self.root_id = root_id
        self.session_cwd = os.path.realpath(session_cwd or repo_root)
        self.task_dir = ""
        self.calls: dict[str, dict[str, Any]] = {}
        self.completion_calls: dict[str, tuple[str, str]] = {}
        self.by_path: dict[str, dict[str, Any]] = {}

    @staticmethod
    def _receipt_agent_id(item: dict[str, Any]) -> str:
        if item.get("current_protocol"):
            return str(item.get("output_path") or "")
        return str(item.get("activity_path") or "")

    def _record_adapter_diagnostic(self, call_id: str, reason: str) -> None:
        task_dir = self.task_dir or _active_task_for_session(
            self.repo_root, self.root_id,
        )
        if not task_dir:
            return
        event_id = call_id + ":adapter"
        if _matching_receipt(task_dir, event_id, "adapter_unsupported"):
            return
        summary = (
            "Receipt adapter unsupported: observed=multi_agent_v1__spawn_agent "
            "supported=collaboration.spawn_agent,multi_agent_v1.spawn_agent "
            f"reason={reason}"
        )
        record_subagent_receipt(task_dir, {
            "source": "codex_session_watcher",
            "status": "adapter_unsupported",
            "agent_id": call_id,
            "agent_type": "multi_agent_v1__spawn_agent",
            "summary": summary,
            "runtime_event_id": event_id,
            "runtime_session_id": self.root_id,
        })

    def _invalidate(self, item: dict[str, Any], reason: str) -> None:
        if item.get("invalid"):
            return
        item["invalid"] = True
        if not item.get("completed") or not item.get("task_dir"):
            return
        lens = _infer_receipt_lens(item.get("task_name", ""))
        summary = "VERDICT: PENDING"
        if lens.startswith("review-"):
            summary += "\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=1 OPTIONAL=0"
        summary += f"\nRuntime watcher invalidated: {reason}"
        record_subagent_receipt(item["task_dir"], {
            "source": "codex_session_watcher",
            "status": "completed",
            "agent_id": self._receipt_agent_id(item),
            "agent_type": item.get("task_name", ""),
            "lens": lens,
            "verdict": "PENDING",
            "summary": summary,
            "head_sha": item.get("head_sha", ""),
            "base_sha": item.get("base_sha", ""),
            "diff_fingerprint": item.get("diff_fingerprint", ""),
            "runtime_event_id": item.get("event_id", "") + ":conflict",
            "runtime_session_id": self.root_id,
            "runtime_thread_id": item.get("child_id", ""),
            "runtime_agent_path": self._receipt_agent_id(item),
        })

    def _set_once(self, item: dict[str, Any], key: str, value: Any) -> bool:
        if key in item:
            self._invalidate(item, f"duplicate or conflicting {key} event")
            return False
        item[key] = value
        return True

    def _maybe_start(self, call_id: str) -> None:
        item = self.calls.get(call_id) or {}
        if (
            item.get("current_protocol")
            and item.get("output_path")
            and not item.get("child_id")
            and not item.get("activity_path")
        ):
            identity = item["output_path"]
            status, _, _ = _child_status(
                identity, self.root_id, identity, self.session_cwd,
                str(item.get("task_name") or ""),
            )
            if status != "running":
                return
            item.setdefault("child_id", identity)
            item.setdefault("activity_path", identity)
            item["synthetic_activity"] = True
        if not all(item.get(key) for key in ("task_name", "output_path", "child_id", "activity_path")):
            return
        if item.get("current_protocol") and item["output_path"] != item["child_id"]:
            self._invalidate(item, "spawn output identity did not match child thread")
            return
        if not item.get("current_protocol") and item["output_path"] != item["activity_path"]:
            if item["output_path"] != item["child_id"]:
                self._invalidate(item, "spawn output identity did not match child activity")
                return
        if item.get("invalid") or item.get("started"):
            return
        task_dir = self.task_dir or _active_task_for_session(self.repo_root, self.root_id)
        lens = _infer_receipt_lens(item["task_name"])
        if not task_dir or not lens.startswith(("review-", "qa-", "ux-")):
            item["invalid"] = True
            return
        event_id = f"{self.root_id}:{call_id}:{item['child_id']}"
        exact_existing = _exact_receipt(
            task_dir,
            event_id,
            "started",
            root_id=self.root_id,
            child_id=item["child_id"],
            agent_path=self._receipt_agent_id(item),
            lens=lens,
        )
        existing = _matching_receipt(
            task_dir, event_id, "started", agent_path=self._receipt_agent_id(item), lens=lens
        )
        if (
            existing is not None
            and existing.get("runtime_event_id") == event_id
            and exact_existing is None
        ):
            existing = None
        if exact_existing is not None:
            receipt = exact_existing
        else:
            child_status, _, _ = _child_status(
                item["child_id"], self.root_id, item["activity_path"], self.session_cwd,
                item["task_name"],
            )
            if child_status == "pending":
                return
            # Without an earlier trusted start receipt, a child that is invalid
            # or already complete cannot establish a start-time snapshot.
            if child_status != "running":
                self._invalidate(item, "child evidence was invalid or already complete at start capture")
                return
            receipt = existing or record_subagent_receipt(task_dir, {
                    "source": "codex_session_watcher",
                    "status": "started",
                    "agent_id": self._receipt_agent_id(item),
                    "agent_type": item["task_name"],
                    "lens": lens,
                    "summary": "Codex runtime spawn observed before child completion",
                    "runtime_event_id": event_id,
                    "runtime_session_id": self.root_id,
                    "runtime_thread_id": item["child_id"],
                    "runtime_agent_path": self._receipt_agent_id(item),
                })
        item.update({
            "started": True,
            "task_dir": task_dir,
            "event_id": event_id,
            "head_sha": receipt.get("head_sha") or "",
            "base_sha": receipt.get("base_sha") or "",
            "diff_fingerprint": receipt.get("diff_fingerprint") or "",
        })
        self.by_path[item["activity_path"]] = item
        self.by_path[item["output_path"]] = item

    def _maybe_complete(self, item: dict[str, Any]) -> None:
        root_final = str(item.get("root_final") or "")
        if not root_final or item.get("completed") or item.get("invalid"):
            return
        status, transcript, child_final = _child_status(
            item["child_id"], self.root_id, item["activity_path"], self.session_cwd,
            item["task_name"],
        )
        if status == "pending":
            return
        if status != "complete" or transcript is None or root_final != child_final:
            self._invalidate(item, "root and child completion evidence did not match")
            return
        verdict = extract_qa_verdict(child_final)
        if not verdict:
            self._invalidate(item, "child final did not contain one exact verdict")
            return
        if item["diff_fingerprint"] != review_diff_fingerprint(item["task_dir"]):
            verdict = "PENDING"
            child_final += "\nRuntime watcher invalidated: source changed while agent was running."
        lens = _infer_receipt_lens(item["task_name"])
        if _matching_receipt(
            item["task_dir"], item["event_id"], "completed",
            agent_path=self._receipt_agent_id(item), lens=lens, summary=child_final,
        ) is None:
            record_subagent_receipt(item["task_dir"], {
                "source": "codex_session_watcher",
                "status": "completed",
                "agent_id": self._receipt_agent_id(item),
                "agent_type": item["task_name"],
                "lens": lens,
                "verdict": verdict,
                "summary": child_final,
                "transcript_path": str(transcript),
                "head_sha": item["head_sha"],
                "base_sha": item["base_sha"],
                "diff_fingerprint": item["diff_fingerprint"],
                "runtime_event_id": item["event_id"],
                "runtime_session_id": self.root_id,
                "runtime_thread_id": item["child_id"],
                "runtime_agent_path": self._receipt_agent_id(item),
            })
        item["completed"] = True

    def retry(self) -> None:
        for call_id in list(self.calls):
            self._maybe_start(call_id)
        for item in list(self.by_path.values()):
            self._maybe_complete(item)

    def feed(self, event: dict[str, Any]) -> None:
        task_dir = _task_binding(event, self.repo_root)
        if task_dir:
            self.task_dir = task_dir
            self.retry()
            return
        spawn = _spawn_call(event)
        if spawn:
            call_id, task_name, current_protocol = spawn
            item = self.calls.setdefault(call_id, {})
            self._set_once(item, "task_name", task_name)
            self._set_once(item, "current_protocol", current_protocol)
            self._maybe_start(call_id)
            return
        unsupported_spawn = _unsupported_current_spawn(event)
        if unsupported_spawn:
            self._record_adapter_diagnostic(*unsupported_spawn)
            return
        completion = _completion_call(event)
        if completion:
            call_id, kind, target = completion
            if call_id not in self.completion_calls:
                self.completion_calls[call_id] = (kind, target)
            return
        activity = _started_activity(event)
        if activity:
            call_id, child_id, agent_path = activity
            item = self.calls.setdefault(call_id, {})
            if item.get("synthetic_activity"):
                if child_id != item.get("child_id"):
                    self._invalidate(item, "spawn output identity did not match child activity")
                    return
                item["activity_path"] = agent_path
                item["synthetic_activity"] = False
            else:
                self._set_once(item, "child_id", child_id)
                self._set_once(item, "activity_path", agent_path)
            self._maybe_start(call_id)
            return
        payload = _event_payload(event, "response_item")
        output_call_id = str(payload.get("call_id") or "") if payload else ""
        completion_deliveries = _completion_output(
            event, self.completion_calls.get(output_call_id),
        )
        if completion_deliveries:
            for identity, root_final in completion_deliveries:
                self._deliver(identity, root_final)
            return
        output_item = self.calls.get(output_call_id) or {}
        output = _spawn_output(
            event, require_agent_id=bool(output_item.get("current_protocol")),
        )
        if output:
            call_id, agent_path = output
            item = self.calls.setdefault(call_id, {})
            self._set_once(item, "output_path", agent_path)
            self._maybe_start(call_id)
            return
        if output_call_id:
            item = self.calls.get(output_call_id) or {}
            if item.get("current_protocol") and not item.get("output_path"):
                self._record_adapter_diagnostic(
                    output_call_id, "spawn output did not contain a valid agent_id",
                )
                return
        delivery = _root_delivery(event)
        if not delivery:
            return
        agent_path, root_final = delivery
        self._deliver(agent_path, root_final)

    def _deliver(self, agent_path: str, root_final: str) -> None:
        item = self.by_path.get(agent_path)
        if not item:
            return
        if item.get("root_final") is not None:
            if item["root_final"] == root_final:
                return
            self._invalidate(item, "duplicate or ambiguous root completion delivery")
            return
        item["root_final"] = root_final
        self._maybe_complete(item)


def watch(
    repo_root: str,
    thread_id: str,
    rollout: str,
    offset: int,
    *,
    session_cwd: str | None = None,
    stop_event: threading.Event | None = None,
    idle_seconds: float = IDLE_SECONDS,
) -> int:
    """Tail one registered root rollout inside an MCP-owned thread."""
    repo_root = os.path.realpath(repo_root)
    session_cwd = os.path.realpath(session_cwd or repo_root)
    if _authorized_control_root(session_cwd) != repo_root:
        return 2
    path = Path(rollout)
    trust_root = _sessions_root()
    if _find_rollout(thread_id) != path:
        return 2
    opened = _open_trusted_file(path, trust_root)
    if opened is None:
        return 2
    handle, rollout_info = opened
    if not _root_meta_from_handle(handle, thread_id, session_cwd) or not _path_matches_handle(
        path, trust_root, handle
    ):
        handle.close()
        return 2
    watcher = Watcher(repo_root, thread_id, session_cwd=session_cwd)
    stop_event = stop_event or threading.Event()
    rollout_age = max(0.0, time.time() - rollout_info.st_mtime)
    last_data = time.monotonic() - min(rollout_age, idle_seconds)
    try:
        handle.seek(max(0, offset))
        while not stop_event.is_set() and time.monotonic() - last_data < idle_seconds:
            position = handle.tell()
            raw = handle.readline(MAX_LINE_BYTES + 1)
            if not raw:
                try:
                    current = os.fstat(handle.fileno())
                    if current.st_size < position or not _path_matches_handle(path, trust_root, handle):
                        return 3
                except OSError:
                    return 3
                handle.seek(position)
                watcher.retry()
                stop_event.wait(POLL_SECONDS)
                continue
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                # Partial tails are retried. Oversized complete records stop the
                # watcher rather than skipping evidence in the candidate chain.
                if not raw.endswith(b"\n"):
                    handle.seek(position)
                    stop_event.wait(POLL_SECONDS)
                    continue
                return 3
            event = _load_json_line(raw)
            if event is None:
                return 3
            last_data = time.monotonic()
            watcher.feed(event)
    finally:
        handle.close()
    return 0


class WatcherManager:
    """Host registered Codex rollout watchers inside the MCP server process."""

    def __init__(
        self,
        repo_root: str,
        *,
        scan_seconds: float = 0.5,
        max_workers: int = MAX_WATCHER_THREADS,
    ):
        self.repo_root = os.path.realpath(
            find_harness_root(repo_root) or find_repo_root(repo_root)
        )
        self.scan_seconds = max(0.05, float(scan_seconds))
        self.max_workers = max(1, min(int(max_workers), MAX_WATCHER_THREADS))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self.workers: dict[str, threading.Thread] = {}
        self.seen: set[str] = set()
        self.worker_results: dict[str, int] = {}
        self._lock = threading.Lock()

    def _worker(self, registration: dict[str, Any], lease: Any) -> None:
        thread_id = str(registration["thread_id"])
        try:
            result = watch(
                self.repo_root,
                thread_id,
                str(registration["rollout"]),
                int(registration["offset"]),
                session_cwd=str(registration.get("session_cwd") or self.repo_root),
                stop_event=self.stop_event,
            )
        except Exception:
            result = 4
        finally:
            lease.close()
        with self._lock:
            self.worker_results[thread_id] = result

    def scan_once(self) -> int:
        """Start one daemon worker for every newly registered root thread."""
        started = 0
        try:
            items = registrations(self.repo_root)
        except Exception:
            return 0
        with self._lock:
            for item in items:
                active = sum(1 for worker in self.workers.values() if worker.is_alive())
                if active >= self.max_workers:
                    break
                thread_id = str(item.get("thread_id") or "")
                if thread_id in self.seen:
                    continue
                lease = _acquire_registration_lease(self.repo_root, thread_id)
                if lease is None:
                    continue
                self.seen.add(thread_id)
                worker = threading.Thread(
                    target=self._worker,
                    args=(item, lease),
                    name=f"harness-codex-watcher-{thread_id[-8:]}",
                    daemon=True,
                )
                self.workers[thread_id] = worker
                worker.start()
                started += 1
        return started

    def _run(self) -> None:
        while not self.stop_event.is_set():
            self.scan_once()
            self.stop_event.wait(self.scan_seconds)

    def start(self) -> "WatcherManager":
        if self.thread is not None and self.thread.is_alive():
            return self
        self.thread = threading.Thread(
            target=self._run,
            name="harness-codex-watcher-manager",
            daemon=True,
        )
        self.thread.start()
        return self

    def stop(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=max(0.0, timeout))
        deadline = time.monotonic() + max(0.0, timeout)
        with self._lock:
            workers = list(self.workers.values())
        for worker in workers:
            worker.join(timeout=max(0.0, deadline - time.monotonic()))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ensure", action="store_true", required=True)
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID", ""))
    parser.add_argument("--retry-seconds", type=float, default=0.0)
    args = parser.parse_args(argv)
    if not THREAD_RE.fullmatch(args.thread_id or ""):
        return 0
    deadline = time.monotonic() + max(0.0, min(args.retry_seconds, 2.0))
    while True:
        if ensure(args.repo_root, args.thread_id):
            return 0
        if time.monotonic() >= deadline:
            return 1
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


if __name__ == "__main__":
    raise SystemExit(main())
