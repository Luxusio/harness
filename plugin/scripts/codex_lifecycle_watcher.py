#!/usr/bin/env python3
"""Observe Codex subagent lifecycle events that plugin PostToolUse omits.

Trusted Codex root hooks register or restore the current root rollout. The Harness
MCP server hosts the watcher as daemon threads and accepts a completion only when root delivery and
the child rollout agree.  It never reconstructs a PASS from historical finals
and never launches a detached operating-system process.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import math
try:
    import fcntl
except ImportError:  # pragma: no cover - Codex currently ships POSIX hooks
    fcntl = None
import json
import io
from itertools import islice
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone
from typing import Any

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from _lib import (  # type: ignore
    _REVIEW_DETAIL_MAX_BYTES,
    _read_regular_text_file,
    _has_binding_conflict_fence, read_active_session_marker,
    _infer_receipt_lens,
    active_session_transaction,
    extract_qa_verdict,
    find_harness_root,
    find_repo_root,
    _bind_runtime_receipt_adapter,
    parse_receipt_runtime_id,
    receipt_snapshot,
    record_subagent_receipt,
    read_task_control,
    task_control_status,
    uuid7_timestamp_ms,
    resolve_active_task_dir,
    resolve_registered_worktree,
)

THREAD_RE = re.compile(r"^[0-9a-fA-F-]{16,80}$")
CALL_RE = re.compile(r"^[A-Za-z0-9_.-]{6,160}$")
AGENT_PATH_RE = re.compile(r"^/root(?:/[A-Za-z0-9_.-]{1,120}){1,16}$")
TASK_NAME_RE = re.compile(r"^[A-Za-z0-9_.-]{1,120}$")
TASK_ID_RE = re.compile(r"^TASK__[A-Za-z0-9_.-]{1,180}$")
# A 2 MiB UTF-8 final can expand sixfold when control characters are escaped
# inside one JSONL string. Keep transport framing above the shared detail cap
# while the existing whole-rollout bound remains the outer memory limit.
MAX_LINE_BYTES = 8 * _REVIEW_DETAIL_MAX_BYTES
MAX_CHILD_BYTES = 64 * 1024 * 1024
MAX_PARENT_BYTES = 256 * 1024 * 1024  # Bounded long-lived coordinator history.
POLL_SECONDS = 0.20
IDLE_SECONDS = 8 * 60 * 60
REGISTRATION_TTL_SECONDS = IDLE_SECONDS
MAX_WATCHER_THREADS = 16
MAX_RECORD_OBSERVATION_ATTEMPTS = 3
RUNTIME_SUBDIR = os.path.join("harness", "codex-watchers")
REGISTRATION_VERSION = 13
REGISTRATION_OWNER = "codex_root_hook"


def _strict_json_loads(raw: str) -> Any:
    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate JSON key: {key}")
            result[key] = value
        return result

    try:
        return json.loads(raw, object_pairs_hook=unique_object)
    except RecursionError as exc:
        raise ValueError("JSON nesting exceeds the watcher parser limit") from exc


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


def _finite_timestamp(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and (not isinstance(value, float) or math.isfinite(value))
    )


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
    fd = None
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
            return None
        handle = os.fdopen(fd, "rb")
        fd = None  # The file object now owns cleanup, including interruption.
        return handle, info
    except OSError:
        return None
    finally:
        if fd is not None:
            os.close(fd)


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
    try:
        if _deadline_expired(deadline):
            return None
        created = datetime.fromtimestamp(uuid7_timestamp_ms(thread_id) / 1000).astimezone()
        direct_root = root / f"{created:%Y}" / f"{created:%m}" / f"{created:%d}"
        direct = list(direct_root.glob(f"rollout-*{thread_id}.jsonl"))
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    valid = [path for path in direct if _safe_regular_file(path, root)]
    return valid[0] if len(valid) == 1 else None


def _load_json_line(raw: bytes) -> dict[str, Any] | None:
    if not raw.endswith(b"\n") or len(raw) > MAX_LINE_BYTES:
        return None
    try:
        value = _strict_json_loads(raw)
    except (UnicodeDecodeError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def _native_meta(handle: Any) -> dict[str, Any]:
    handle.seek(0)
    event = _load_json_line(handle.readline(MAX_LINE_BYTES + 1))
    payload = event.get("payload") if event and event.get("type") == "session_meta" else None
    return payload if isinstance(payload, dict) else {}


class _AncestryPending(RuntimeError):
    """A native parent changed while its ancestry proof was being read."""


def _coordinator_identity(thread_id: str, session_cwd: str, seen=()) -> dict[str, Any]:
    """Authenticate every edge against native metadata and parent's spawn records."""
    if thread_id in seen or len(seen) > 16:
        return {}
    path = _find_rollout(thread_id)
    opened = _open_trusted_file(path, _sessions_root(), max_size=MAX_PARENT_BYTES) if path else None
    if opened is None:
        return {}
    handle, _ = opened
    try:
        meta = _native_meta(handle)
        if meta.get("id") != thread_id or os.path.realpath(str(meta.get("cwd") or "")) != session_cwd:
            return {}
        if meta.get("thread_source") != "subagent":
            if meta.get("session_id") != thread_id or meta.get("parent_thread_id"):
                return {}
            return {"session_id": thread_id, "agent_path": "/root", "depth": 0}
        source = meta.get("source")
        spawn = source.get("subagent", {}).get("thread_spawn", {}) if isinstance(source, dict) else {}
        parent_id = meta.get("parent_thread_id")
        if not isinstance(parent_id, str) or spawn.get("parent_thread_id") != parent_id:
            return {}
        parent = _coordinator_identity(parent_id, session_cwd, (*seen, thread_id))
        agent_path = str(meta.get("agent_path") or "")
        if not parent or not AGENT_PATH_RE.fullmatch(agent_path) or (
            meta.get("session_id") != parent["session_id"]
            or spawn.get("agent_path") != agent_path
            or type(spawn.get("depth")) is not int
            or spawn.get("depth") != parent["depth"] + 1
            or agent_path.rsplit("/", 1)[0] != parent["agent_path"]
        ):
            return {}
        if not _parent_spawn_proof(parent_id, thread_id, agent_path):
            return {}
        return {"session_id": parent["session_id"], "agent_path": agent_path, "depth": parent["depth"] + 1}
    finally:
        valid = _path_matches_handle(path, _sessions_root(), handle)
        handle.close()
        if not valid:
            return {}


# Cached proofs never outlive any metadata/content change to the trusted parent
# file. Descriptor identity and path safety are checked even on cache hits.
_PARENT_SPAWN_CACHE: dict[tuple[str, str, str], tuple[tuple[int, ...], bool]] = {}
_PARENT_SPAWN_LOCK = threading.Lock()


def _file_generation(info: Any) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _parent_spawn_proof(parent_id: str, thread_id: str, agent_path: str) -> bool:
    path = _find_rollout(parent_id)
    opened = _open_trusted_file(path, _sessions_root(), max_size=MAX_PARENT_BYTES) if path else None
    if opened is None:
        return False
    handle, info = opened
    handle = io.BufferedReader(handle, buffer_size=1024 * 1024)
    key = (str(path), thread_id, agent_path)
    generation = _file_generation(info)
    proof = None
    try:
        with _PARENT_SPAWN_LOCK:
            cached = _PARENT_SPAWN_CACHE.get(key)
        if cached and cached[0] == generation:
            if not _path_matches_handle(path, _sessions_root(), handle):
                return False
            if _file_generation(os.fstat(handle.fileno())) != generation:
                raise _AncestryPending()
            return cached[1]
        # The live coordinator legitimately appends while a child authenticates.
        # Parse one copied complete prefix, not two different moving EOFs.
        captured_size = info.st_size
        proof = tempfile.SpooledTemporaryFile(max_size=8 * 1024 * 1024, mode="w+b")
        copied, complete, prefix_hash = 0, 0, hashlib.sha256()
        while copied < captured_size:
            data = handle.read(min(1024 * 1024, captured_size - copied))
            if not data:
                raise _AncestryPending()
            proof.write(data)
            prefix_hash.update(data)
            newline = data.rfind(b"\n")
            if newline >= 0:
                complete = copied + newline + 1
            copied += len(data)
        proof.truncate(complete)
        proof.seek(0)

        def prefix_unchanged():
            if not _path_matches_handle(path, _sessions_root(), handle, max_size=MAX_PARENT_BYTES):
                return False
            if os.fstat(handle.fileno()).st_size < captured_size:
                return False
            handle.seek(0)
            observed, remaining = hashlib.sha256(), captured_size
            while remaining:
                data = handle.read(min(1024 * 1024, remaining))
                if not data:
                    return False
                observed.update(data)
                remaining -= len(data)
            return observed.digest() == prefix_hash.digest()

        task_name = agent_path.rsplit("/", 1)[1]
        child_bytes = thread_id.encode("ascii")
        call_ids = set()
        for raw in proof:
            if len(raw) > MAX_LINE_BYTES:
                return False
            if not raw.endswith(b"\n"):
                raise _AncestryPending()
            if b'"SubAgentActivity"' not in raw or child_bytes not in raw:
                continue
            event = _load_json_line(raw)
            if not event:
                return False
            activity = _spawn_activity(event)
            if activity and activity[1:] == (thread_id, agent_path):
                call_ids.add(activity[0])
        if len(call_ids) != 1:
            if not prefix_unchanged():
                raise _AncestryPending()
            return False
        call_bytes = next(iter(call_ids)).encode("ascii")
        proof.seek(0)
        calls, outputs, activities, order = {}, {}, {}, {}
        for raw in proof:
            if len(raw) > MAX_LINE_BYTES:
                return False
            if not raw.endswith(b"\n"):
                raise _AncestryPending()
            # Decode only exact candidate call IDs. Both passes stay bounded;
            # strict structured parsing remains the authority, never this filter.
            if call_bytes not in raw:
                continue
            event = _load_json_line(raw)
            if not event:
                return False
            for value, target, kind in (
                (_spawn_call(event, lens_only=False), calls, "call"),
                (_spawn_output(event), outputs, "output"),
                (_spawn_activity(event), activities, "activity"),
            ):
                if value:
                    target.setdefault(value[0], []).append(value[1:])
                    order.setdefault(value[0], []).append(kind)
        if not prefix_unchanged():
            raise _AncestryPending()
        matches = [key for key, value in calls.items()
                   if value == [(task_name,)]
                   and outputs.get(key) == [(agent_path,)]
                   and activities.get(key) == [(thread_id, agent_path)]
                   and order.get(key) == ["call", "activity", "output"]]
        valid = len(matches) == 1
        with _PARENT_SPAWN_LOCK:
            if len(_PARENT_SPAWN_CACHE) >= 256:
                _PARENT_SPAWN_CACHE.clear()
            _PARENT_SPAWN_CACHE[key] = (generation, valid)
        return valid
    finally:
        if proof is not None:
            proof.close()
        handle.close()


def _root_meta_from_handle(handle: Any, thread_id: str, session_cwd: str) -> bool:
    meta = _native_meta(handle)
    if meta.get("thread_source") == "subagent":
        try:
            return bool(_coordinator_identity(thread_id, session_cwd))
        except _AncestryPending:
            return False
    return bool(meta.get("id") == thread_id and meta.get("session_id") == thread_id
                and not meta.get("parent_thread_id")
                and os.path.realpath(str(meta.get("cwd") or "")) == session_cwd)


def workspace_roots(control_root: str) -> list[str]:
    """Enumerate only revalidated linked worktrees, with a bounded registry scan."""
    root = os.path.realpath(control_root)
    gitfile = Path(root) / ".git"
    if gitfile.is_file():
        try:
            target = _read_regular_text_file(str(gitfile), max_size=4096).strip().removeprefix("gitdir: ")
            common = (Path(root) / target).resolve().parent.parent
            main = str(common.parent)
            if resolve_registered_worktree(main, root) == root:
                root = main
        except (OSError, ValueError, RuntimeError):
            return [root]
    found = [root]
    registry = Path(root) / ".git" / "worktrees"
    if registry.is_dir() and not registry.is_symlink():
        entries = list(islice(registry.iterdir(), 257))
        if len(entries) > 256:
            return []
        for entry in entries:
            try:
                if entry.is_symlink():
                    continue
                target = str(Path(_read_regular_text_file(str(entry / "gitdir"), max_size=4096).strip()).parent)
                if target not in found and resolve_registered_worktree(root, target) == target:
                    found.append(target)
            except (OSError, ValueError, RuntimeError):
                continue
    return found


def _workspace_matches(session_cwd: str, repo_root: str) -> bool:
    native_root = _authorized_control_root(session_cwd)
    return bool(native_root and repo_root in workspace_roots(native_root))


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


def registration_host_live(repo_root: str, thread_id: str) -> bool:
    """Return true only while another process holds the watcher lease."""
    if fcntl is None:
        return False
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return False
    path = _lease_path(repo_root, thread_id)
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return False
    try:
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
            return False
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    except OSError:
        return False
    finally:
        os.close(fd)


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


def _valid_current_registration(
    repo_root: str, thread_id: str, task_id: str = "", run_id: str = "",
) -> bool:
    """Validate the exact current state without discovery, locking, or rewriting."""
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
        and (not task_id or state.get("task_id") == task_id)
        and (not run_id or state.get("run_id") == run_id)
        and isinstance(session_cwd, str)
        and _workspace_matches(session_cwd, repo_root)
        and isinstance(offset, int)
        and not isinstance(offset, bool)
        and offset >= 0
        and _finite_timestamp(registered_at)
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


def invalidate_registration(repo_root: str, thread_id: str) -> bool:
    """Remove one root registration so a later bind starts at a fresh offset."""
    if not THREAD_RE.fullmatch(thread_id):
        return False
    repo_root = os.path.realpath(repo_root)
    runtime_path = _runtime_dir(repo_root)
    if not os.path.lexists(runtime_path):
        return True
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return False
    state_path = _state_path(repo_root, thread_id)
    if not os.path.lexists(state_path):
        return True
    lock_path = _lock_path(repo_root, thread_id)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = None
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        lock_info = os.fstat(lock_fd)
        lock_path_info = os.lstat(lock_path)
        if (
            not stat.S_ISREG(lock_info.st_mode)
            or lock_info.st_uid != os.getuid()
            or lock_info.st_nlink != 1
            or lock_info.st_mode & 0o022
            or stat.S_ISLNK(lock_path_info.st_mode)
            or (lock_path_info.st_dev, lock_path_info.st_ino)
            != (lock_info.st_dev, lock_info.st_ino)
        ):
            return False
        os.fchmod(lock_fd, 0o600)
        lock = os.fdopen(lock_fd, "a+", encoding="utf-8")
        lock_fd = None
    except OSError:
        return False
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    with lock:
        if fcntl is None:
            return False
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        if not os.path.lexists(state_path):
            return True
        try:
            state_info = os.lstat(state_path)
            if (
                stat.S_ISLNK(state_info.st_mode)
                or not stat.S_ISREG(state_info.st_mode)
                or state_info.st_uid != os.getuid()
                or state_info.st_nlink != 1
                or state_info.st_mode & 0o022
            ):
                return False
            os.unlink(state_path)
        except OSError:
            return False
        return not os.path.lexists(state_path)


def _registration_binding_matches(
    repo_root: str, thread_id: str, task_id: str, run_id: str,
) -> bool:
    if any(_has_binding_conflict_fence(read_active_session_marker(root, thread_id))
           for root in workspace_roots(repo_root)):
        return False
    if not task_id or not run_id:
        return True
    binding = _active_task_binding_for_session(repo_root, thread_id)
    return bool(
        os.path.basename(str(binding.get("task_dir") or "")) == task_id
        and binding.get("run_id") == run_id
    )


def ensure(
    repo_root: str,
    thread_id: str,
    *,
    session_cwd: str | None = None,
    task_id: str = "",
    run_id: str = "",
    deadline: float | None = None,
) -> bool:
    """Register a root rollout for the MCP-hosted watcher manager.

    This entry point is owned by trusted Codex root hooks. It performs only
    validation and an atomic registry write; it never forks or writes a review
    or QA receipt. Existing valid registration offsets are immutable.
    """
    if not THREAD_RE.fullmatch(thread_id):
        return False
    if bool(task_id) != bool(run_id) or (task_id and not TASK_ID_RE.fullmatch(task_id)):
        return False
    repo_root = os.path.realpath(repo_root)
    session_cwd = os.path.realpath(session_cwd or repo_root)
    if not _workspace_matches(session_cwd, repo_root):
        return False
    if not _registration_binding_matches(repo_root, thread_id, task_id, run_id):
        return False
    if _valid_current_registration(repo_root, thread_id, task_id, run_id):
        return True
    rollout = _find_rollout(thread_id, deadline=deadline)
    if rollout is None or _deadline_expired(deadline):
        return False
    trust_root = _sessions_root()
    runtime = _trusted_runtime_dir(repo_root)
    if runtime is None:
        return False
    lock_path = _lock_path(repo_root, thread_id)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    lock_fd = None
    try:
        lock_fd = os.open(lock_path, flags, 0o600)
        lock_info = os.fstat(lock_fd)
        if (
            not stat.S_ISREG(lock_info.st_mode) or lock_info.st_uid != os.getuid()
            or lock_info.st_nlink != 1 or lock_info.st_mode & 0o022
        ):
            return False
        os.fchmod(lock_fd, 0o600)
        lock = os.fdopen(lock_fd, "a+", encoding="utf-8")
        lock_fd = None
    except OSError:
        return False
    finally:
        if lock_fd is not None:
            os.close(lock_fd)
    with lock:
        if fcntl is not None:
            try:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                return False
        if (
            _deadline_expired(deadline)
            or not _registration_binding_matches(
                repo_root, thread_id, task_id, run_id,
            )
        ):
            return False
        opened = _open_trusted_file(rollout, trust_root)
        if opened is None:
            return False
        rollout_handle, _ = opened
        try:
            if not _root_meta_from_handle(rollout_handle, thread_id, session_cwd):
                return False
            if not _path_matches_handle(rollout, trust_root, rollout_handle):
                return False
            rollout_info = os.fstat(rollout_handle.fileno())
            offset = rollout_info.st_size
            if offset < 0 or _deadline_expired(deadline):
                return False
            state_path = _state_path(repo_root, thread_id)
            state = _read_owned_json(state_path, runtime)
            state_offset = state.get("offset")
            registered_at = state.get("registered_at")
            tuple_valid = (
                state.get("thread_id") == thread_id
                and state.get("repo_root") == repo_root
                and state.get("task_id") == task_id
                and state.get("run_id") == run_id
                and state.get("session_cwd") in {None, "", session_cwd}
                and state.get("rollout") == str(rollout)
                and isinstance(state_offset, int)
                and not isinstance(state_offset, bool)
                and 0 <= state_offset <= rollout_info.st_size
                and _finite_timestamp(registered_at)
            )
            if tuple_valid and (
                state.get("version") == REGISTRATION_VERSION
                and state.get("owner") == REGISTRATION_OWNER
                and state.get("session_cwd") == session_cwd
            ):
                return True
            registered_at = time.time()
            _atomic_json(state_path, {
                "version": REGISTRATION_VERSION,
                "repo_root": repo_root,
                "session_cwd": session_cwd,
                "thread_id": thread_id,
                "task_id": task_id,
                "run_id": run_id,
                "rollout": str(rollout),
                "offset": offset,
                "registered_at": registered_at,
                "owner": REGISTRATION_OWNER,
            })
            return True
        finally:
            rollout_handle.close()


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
        task_id = str(state.get("task_id") or "")
        run_id = str(state.get("run_id") or "")
        binding = _active_task_binding_for_session(repo_root, thread_id)
        rollout = _find_rollout(thread_id)
        offset = state.get("offset")
        registered_at = state.get("registered_at")
        session_cwd = state.get("session_cwd")
        if (
            state.get("version") != REGISTRATION_VERSION
            or state.get("owner") != REGISTRATION_OWNER
            or state.get("repo_root") != repo_root
            or not TASK_ID_RE.fullmatch(task_id)
            or not run_id
            or os.path.basename(str(binding.get("task_dir") or "")) != task_id
            or binding.get("run_id") != run_id
            or not isinstance(session_cwd, str)
            or not _workspace_matches(session_cwd, repo_root)
            or path.name != f"{thread_id}.json"
            or rollout is None
            or state.get("rollout") != str(rollout)
            or not _root_meta(rollout, thread_id, os.path.realpath(session_cwd))
            or not isinstance(offset, int)
            or isinstance(offset, bool)
            or offset < 0
            or not _finite_timestamp(registered_at)
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
    control = read_task_control(str(task_dir))
    if not control or task_control_status(str(task_dir), control) != "open":
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


def _active_task_binding_for_session(repo_root: str, root_id: str) -> dict[str, str]:
    task_dir = _active_task_for_session(repo_root, root_id)
    if not task_dir:
        return {}
    tasks_root = (Path(repo_root) / "doc/harness/tasks").resolve()
    marker = tasks_root / ".active_sessions" / f"{root_id}.json"
    marker_data = _read_owned_json(marker, tasks_root)
    task_run = read_task_control(task_dir)
    marker_run_id = str(marker_data.get("run_id") or "")
    if (
        not task_run
        or marker_run_id != task_run.get("run_id")
    ):
        return {}
    return {
        "task_dir": task_dir,
        "run_id": marker_run_id,
    }


class _BindingUnavailable(RuntimeError):
    """An unknown binding must retry the worker, never discard a record."""


class _RegistrationChanged(RuntimeError):
    """A fresh registration owns the tail; the old worker must retire."""


def _current_registration_generation(
    repo_root: str, root_id: str, session_cwd: str,
) -> RegistrationGeneration:
    runtime = _trusted_runtime_dir(repo_root)
    state = _read_owned_json(_state_path(repo_root, root_id), runtime) if runtime else {}
    if not (
        state.get("version") == REGISTRATION_VERSION
        and state.get("owner") == REGISTRATION_OWNER
        and state.get("repo_root") == repo_root
        and state.get("thread_id") == root_id
        and state.get("session_cwd") == session_cwd
        and isinstance(state.get("rollout"), str)
        and isinstance(state.get("offset"), int)
        and not isinstance(state.get("offset"), bool)
        and state["offset"] >= 0
        and _finite_timestamp(state.get("registered_at"))
        and isinstance(state.get("task_id"), str)
        and TASK_ID_RE.fullmatch(state["task_id"])
        and isinstance(state.get("run_id"), str)
        and state["run_id"]
    ):
        raise _BindingUnavailable("binding unavailable")
    return _registration_generation(state)


def _require_task_binding(
    repo_root: str,
    root_id: str,
    expected_generation: RegistrationGeneration | None = None,
    session_cwd: str | None = None,
) -> dict[str, str]:
    # Never bridge an unknown interval: a conflict can revoke the old offset
    # and rebind the same task/run. Only the manager may restart after that gap.
    binding = _active_task_binding_for_session(repo_root, root_id)
    if not binding.get("task_dir") or not binding.get("run_id"):
        raise _BindingUnavailable("binding unavailable")
    if expected_generation is not None:
        roots = workspace_roots(repo_root)
        if any(_has_binding_conflict_fence(read_active_session_marker(root, root_id))
               for root in roots):
            raise _BindingUnavailable("unacknowledged coordinator conflict fence")
        if any(root != repo_root and _active_task_binding_for_session(root, root_id)
               for root in roots):
            raise _BindingUnavailable("conflicting workspace bindings")
        if not _workspace_matches(session_cwd or repo_root, repo_root):
            raise _BindingUnavailable("workspace binding unavailable")
        current = _current_registration_generation(
            repo_root, root_id, session_cwd or repo_root,
        )
        if current != expected_generation:
            raise _RegistrationChanged("registration changed")
    return binding


def _event_precedes_run(event: dict[str, Any], run_id: str) -> bool:
    if not run_id:
        return False
    try:
        event_time = datetime.fromisoformat(
            str(event.get("timestamp") or "").replace("Z", "+00:00")
        )
        if event_time.tzinfo is None:
            return True
        event_ms = int(event_time.astimezone(timezone.utc).timestamp() * 1000)
        run_ms = uuid7_timestamp_ms(run_id)
    except (OverflowError, TypeError, ValueError):
        return True
    return event_ms < run_ms


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
        decoded = _strict_json_loads(arguments or "{}")
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _spawn_call(event: dict[str, Any], *, lens_only: bool = True) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if (
        not payload
        or payload.get("type") != "function_call"
        or payload.get("namespace") != "collaboration"
        or payload.get("name") != "spawn_agent"
    ):
        return None
    arguments = _json_arguments(payload)
    if arguments is None:
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    task_name = str(arguments.get("task_name") or "")
    if not TASK_NAME_RE.fullmatch(task_name):
        return None
    lens = _infer_receipt_lens(task_name)
    if lens_only and not lens.startswith(("review-", "qa-", "ux-")):
        return None
    return call_id, task_name


def _structured_tool_output(output: Any) -> dict[str, Any] | None:
    """Decode one direct structured tool result."""
    if isinstance(output, dict):
        return output
    if not isinstance(output, str):
        return None
    try:
        decoded = _strict_json_loads(output)
    except ValueError:
        return None
    return decoded if isinstance(decoded, dict) else None


def _followup_call(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if (not payload or payload.get("type") != "function_call"
            or payload.get("namespace") != "collaboration"
            or payload.get("name") != "followup_task"):
        return None
    arguments = _json_arguments(payload)
    call_id = str(payload.get("call_id") or "")
    target = str((arguments or {}).get("target") or "")
    if not CALL_RE.fullmatch(call_id) or not (AGENT_PATH_RE.fullmatch(target) or TASK_NAME_RE.fullmatch(target)):
        return None
    return call_id, target


def _spawn_output(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") != "function_call_output":
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    output = _structured_tool_output(payload.get("output"))
    if not isinstance(output, dict) or output.get("isError") is True or output.get("success") is False:
        return None
    identity = str(
        output.get("agent_id")
        or output.get("agent_name")
        or output.get("task_name")
        or ""
    )
    return (call_id, identity) if _valid_agent_identity(identity) else None


def _spawn_activity(event: dict[str, Any], *, kind: str = "started") -> tuple[str, str, str] | None:
    """Decode the runtime's structured child-start correlation event."""
    payload = _event_payload(event, "event_msg")
    if not payload or payload.get("type") != "item_completed":
        return None
    item = payload.get("item")
    if not isinstance(item, dict):
        return None
    if item.get("type") != "SubAgentActivity":
        return None
    call_id = str(item.get("id") or "")
    child_id = str(item.get("agent_thread_id") or "")
    agent_path = str(item.get("agent_path") or "")
    if (
        item.get("kind") != kind
        or not CALL_RE.fullmatch(call_id)
        or not THREAD_RE.fullmatch(child_id)
        or not AGENT_PATH_RE.fullmatch(agent_path)
    ):
        return None
    return call_id, child_id, agent_path


def _root_delivery(event: dict[str, Any], coordinator_path: str = "/root") -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") != "agent_message":
        return None
    author = str(payload.get("author") or "")
    if not _valid_agent_identity(author) or payload.get("recipient") != coordinator_path:
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
    return author, final


def _child_status(
    child_id: str,
    root_id: str,
    agent_path: str,
    session_cwd: str,
    *, generation: int = 1,
) -> tuple[str, Path | None, str]:
    try:
        coordinator = ({"session_id": root_id, "agent_path": "/root", "depth": 0}
                       if agent_path.count("/") == 2 else _coordinator_identity(root_id, session_cwd))
    except _AncestryPending:
        return "pending", None, ""
    if not coordinator:
        return "invalid", None, ""
    if agent_path.rsplit("/", 1)[0] != coordinator["agent_path"]:
        return "invalid", None, ""
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
                    payload.get("thread_source") == "subagent"
                    and payload.get("session_id") == coordinator["session_id"]
                    and payload.get("parent_thread_id") == root_id
                    and os.path.realpath(str(payload.get("cwd") or "")) == session_cwd
                    and payload.get("agent_path") == agent_path
                    and spawn.get("parent_thread_id") == root_id
                    and spawn.get("agent_path") == agent_path
                    and type(spawn.get("depth")) is int
                    and spawn.get("depth") == coordinator["depth"] + 1
                ):
                    matching_meta += 1
            if event.get("type") == "response_item" and payload.get("type") == "agent_message":
                if payload.get("author") == coordinator["agent_path"] and payload.get("recipient") == agent_path:
                    content = payload.get("content") or []
                    texts = [
                        str(item.get("text") or "") for item in content
                        if isinstance(item, dict) and item.get("type") == "input_text"
                    ]
                    if any("Message Type: NEW_TASK" in text for text in texts):
                        if child_turn and (len(completes) != 1 or len(finals) > 1):
                            return "invalid", path, ""
                        child_boundaries += 1
                        child_turn = child_boundaries == generation
                        continue
            if child_turn and event.get("type") == "event_msg" and payload.get("type") == "agent_message" and payload.get("phase") == "final_answer":
                finals.append(str(payload.get("message") or ""))
            if child_turn and event.get("type") == "event_msg" and payload.get("type") == "task_complete":
                completes.append(str(payload.get("last_agent_message") or ""))
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
    if child_boundaries < generation:
        return "pending", path, ""
    if not finals and not completes:
        return "running", path, ""
    if len(finals) > 1 or len(completes) != 1:
        return "invalid", path, ""
    child_final = completes[0]
    if not child_final or (finals and finals[0] != child_final):
        return "invalid", path, ""
    return "complete", path, child_final


def _exact_receipt(
    task_dir: str,
    runtime_id: str,
    event: str,
    *,
    source: str,
    agent_path: str,
    lens: str,
    task_run_id: str,
    agent_type: str,
) -> dict[str, Any] | None:
    for item in receipt_snapshot(task_dir).entries:
        if (
            item.get("runtime_id") == runtime_id
            and item.get("event") == event
            and item.get("source") == source
            and item.get("agent_id") == agent_path
            and item.get("lens") == lens
            and item.get("task_run_id") == task_run_id
            and item.get("agent_type") == agent_type
        ):
            return item
    return None


def _codex_runtime_id(root_id: str, call_id: str, child_id: str) -> str:
    runtime_id = f"codex:{root_id}:{call_id}:{child_id}"
    parse_receipt_runtime_id(runtime_id)
    return runtime_id


class Watcher:
    def __init__(
        self,
        repo_root: str,
        root_id: str,
        *,
        session_cwd: str | None = None,
    ):
        self.repo_root = repo_root
        self.root_id = root_id
        self.session_cwd = os.path.realpath(session_cwd or repo_root)
        self.calls: dict[str, dict[str, Any]] = {}
        self.by_agent: dict[str, dict[str, Any]] = {}
        self.receipt_progress = 0
        self.expected_generation: RegistrationGeneration | None = None
        # State reconstructed from exact, already-persisted receipts during a
        # restart. This proves replay recovery without pretending a new write
        # occurred or weakening duplicate-terminal checks.
        self.replay_recovery_progress = 0

    @staticmethod
    def _receipt_agent_id(item: dict[str, Any]) -> str:
        return str(item.get("agent_path") or "")

    @staticmethod
    def _receipt_source(_item: dict[str, Any]) -> str:
        return "codex_session_watcher:collaboration"

    def _invalidate(
        self,
        item: dict[str, Any],
        reason: str,
        *,
        completion_summary: str | None = None,
        completion_confirmed: bool = False,
    ) -> None:
        if item.get("invalid"):
            return
        if (
            not item.get("completed")
            and not completion_confirmed
        ) or not item.get("task_dir"):
            item["invalid"] = True
            return
        lens = _infer_receipt_lens(item.get("task_name", ""))
        summary = completion_summary
        if summary is None:
            summary = "VERDICT: PENDING"
        if completion_summary is None and lens.startswith("review-"):
            # FIX_NOW, not INVESTIGATE. An invalidated run is void and must be
            # redone, which is blocking; INVESTIGATE is non-blocking by the rule
            # `_lib._counts_contradict_verdict` enforces and both reviewer
            # definitions state. Coded as non-blocking, this invalidation could
            # not displace the stale PASS it exists to invalidate.
            summary += "\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0"
        if completion_summary is None:
            summary += f"\nRuntime watcher invalidated: {reason}"
        with active_session_transaction(self.repo_root):
            _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            pending_exists = any(
                receipt.get("runtime_id") == item.get("runtime_id")
                and receipt.get("event") == "completed"
                and receipt.get("source") == self._receipt_source(item)
                and receipt.get("agent_id") == self._receipt_agent_id(item)
                and receipt.get("lens") == lens
                and receipt.get("task_run_id") == item.get("task_run_id")
                and receipt.get("agent_type") == item.get("task_name")
                and receipt.get("verdict") == "PENDING"
                for receipt in receipt_snapshot(item["task_dir"]).entries
            )
            if not pending_exists:
                record_subagent_receipt(item["task_dir"], {
                    "source": self._receipt_source(item),
                    "event": "completed",
                    "agent_id": self._receipt_agent_id(item),
                    "agent_type": item.get("task_name", ""),
                    "lens": lens,
                    "task_run_id": item.get("task_run_id", ""),
                    "verdict": "PENDING",
                    "summary": summary,
                    # A second terminal for the exact identity invalidates the
                    # lifecycle under the normal duplicate-terminal fail-closed rule.
                    "runtime_id": item.get("runtime_id", ""),
                })
                self.receipt_progress += 1
        # Publish the fail-closed terminal before suppressing later attempts.
        # The watch loop rolls in-memory state back and replays this record if
        # either the snapshot or append fails transiently.
        item["invalid"] = True

    def _set_once(self, item: dict[str, Any], key: str, value: Any) -> bool:
        if key in item:
            self._invalidate(item, f"duplicate or conflicting {key} event")
            return False
        item[key] = value
        return True

    def _maybe_start(self, call_id: str) -> None:
        item = self.calls.get(call_id) or {}
        if not all(item.get(key) for key in (
            "task_name", "output_path", "agent_path", "child_id",
        )):
            return
        if item.get("agent_path") and item["agent_path"] != item["output_path"]:
            self._invalidate(item, "structured activity and spawn output identities differed")
            return
        if item.get("invalid") or item.get("started"):
            return
        try:
            identity = _coordinator_identity(self.root_id, self.session_cwd)
        except _AncestryPending:
            return
        if not identity and item["agent_path"].count("/") > 2:
            self._invalidate(item, "coordinator ancestry is unavailable")
            return
        coordinator_path = identity.get("agent_path", "/root")
        if item["agent_path"] != coordinator_path + "/" + item["task_name"]:
            self._invalidate(item, "spawn path was not the exact coordinator child")
            return
        task_dir = str(item.get("task_dir") or "")
        binding = _require_task_binding(
            self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
        )
        active_task = binding.get("task_dir")
        lens = _infer_receipt_lens(item["task_name"])
        if (
            not task_dir
            or active_task != task_dir
            or not item.get("task_run_id")
            or binding.get("run_id") != item.get("task_run_id")
            or not lens.startswith(("review-", "qa-", "ux-"))
        ):
            item["invalid"] = True
            return
        runtime_id = _codex_runtime_id(self.root_id, call_id, item["child_id"])
        source = self._receipt_source(item)
        with active_session_transaction(self.repo_root):
            binding = _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            if (
                binding.get("task_dir") != task_dir
                or binding.get("run_id") != item.get("task_run_id")
            ):
                item["invalid"] = True
                return
            exact_existing = _exact_receipt(
                task_dir,
                runtime_id,
                "started",
                source=source,
                agent_path=self._receipt_agent_id(item),
                lens=lens,
                task_run_id=item["task_run_id"],
                agent_type=item["task_name"],
            )
        if exact_existing is not None:
            self.replay_recovery_progress += 1
            item.update({
                "started": True,
                "task_dir": task_dir,
                "runtime_id": runtime_id,
            })
            self.by_agent[item["agent_path"]] = item
            return
        child_status, _, _ = self._item_child_status(item)
        if child_status == "pending":
            return
        # The registration offset proves this spawn was observed in order.
        # A delayed manager may reach it after the trusted depth-1 child
        # already completed; that remains real replay, not history scan.
        if child_status not in {"running", "complete"}:
            self._invalidate(item, "child evidence was invalid at start capture")
            return
        with active_session_transaction(self.repo_root):
            binding = _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            if (
                binding.get("task_dir") != task_dir
                or binding.get("run_id") != item.get("task_run_id")
            ):
                item["invalid"] = True
                return
            exact_existing = _exact_receipt(
                task_dir,
                runtime_id,
                "started",
                source=source,
                agent_path=self._receipt_agent_id(item),
                lens=lens,
                task_run_id=item["task_run_id"],
                agent_type=item["task_name"],
            )
            if exact_existing is None:
                record_subagent_receipt(task_dir, {
                    "source": source,
                    "event": "started",
                    "agent_id": self._receipt_agent_id(item),
                    "agent_type": item["task_name"],
                    "lens": lens,
                    "task_run_id": item.get("task_run_id", ""),
                    "summary": "Codex runtime spawn observed from the registered rollout checkpoint",
                    "runtime_id": runtime_id,
                })
                self.receipt_progress += 1
            else:
                self.replay_recovery_progress += 1
        item.update({
            "started": True,
            "task_dir": task_dir,
            "runtime_id": runtime_id,
        })
        self.by_agent[item["agent_path"]] = item

    def _item_child_status(self, item):
        kwargs = {"generation": item["generation"]} if item.get("generation", 1) > 1 else {}
        return _child_status(item["child_id"], self.root_id, item["agent_path"], self.session_cwd, **kwargs)

    def _maybe_complete(self, item: dict[str, Any]) -> None:
        root_final = str(item.get("root_final") or "")
        if not item.get("started") or not root_final or item.get("completed") or item.get("invalid"):
            return
        binding = _require_task_binding(
            self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
        )
        current_task = binding.get("task_dir")
        if (
            current_task != item.get("task_dir")
            or not item.get("task_run_id")
            or binding.get("run_id") != item.get("task_run_id")
        ):
            self._invalidate(item, "active task changed while agent was running")
            return
        status, transcript, child_final = self._item_child_status(item)
        if status in {"pending", "running"}:
            return
        if status != "complete" or transcript is None or root_final != child_final:
            self._invalidate(item, "root and child completion evidence did not match")
            return
        verdict = extract_qa_verdict(child_final)
        if not verdict:
            # The child final is complete and independently matched here. Keep
            # that exact malformed/PENDING evidence addressable instead of
            # replacing it with the watcher's diagnostic text.
            self._invalidate(
                item,
                "child final did not contain one exact verdict",
                completion_summary=child_final,
                completion_confirmed=True,
            )
            return
        lens = _infer_receipt_lens(item["task_name"])
        with active_session_transaction(self.repo_root):
            binding = _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            if (
                binding.get("task_dir") != item.get("task_dir")
                or binding.get("run_id") != item.get("task_run_id")
            ):
                # No terminal was published for this item, so invalidation
                # only marks memory; do not acquire a nested transaction.
                item["invalid"] = True
                return
            exact_existing = _exact_receipt(
                item["task_dir"],
                item["runtime_id"],
                "completed",
                source=self._receipt_source(item),
                agent_path=self._receipt_agent_id(item),
                lens=lens,
                task_run_id=item["task_run_id"],
                agent_type=item["task_name"],
            )
            if exact_existing is None:
                record_subagent_receipt(item["task_dir"], {
                    "source": self._receipt_source(item),
                    "event": "completed",
                    "agent_id": self._receipt_agent_id(item),
                    "agent_type": item["task_name"],
                    "lens": lens,
                    "task_run_id": item.get("task_run_id", ""),
                    "verdict": verdict,
                    "summary": child_final,
                    "runtime_id": item["runtime_id"],
                })
                self.receipt_progress += 1
            else:
                self.replay_recovery_progress += 1
        item["completed"] = True

    def retry(self) -> None:
        for call_id in list(self.calls):
            self._maybe_start(call_id)
        for item in list(self.calls.values()):
            self._maybe_complete(item)

    def feed(self, event: dict[str, Any]) -> None:
        followup = _followup_call(event)
        if followup:
            call_id, target = followup
            prior = next((item for item in reversed(list(self.calls.values()))
                          if (item.get("started") or item.get("followup_child")) and not item.get("invalid")
                          and target in {item.get("agent_path"), item.get("followup_path"), item.get("task_name")}), None)
            if prior is None:
                return  # No authenticated original invocation in this stream.
            binding = _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            if (_event_precedes_run(event, binding.get("run_id", ""))
                    or binding.get("task_dir") != prior.get("task_dir")
                    or binding.get("run_id") != prior.get("task_run_id")):
                return
            if call_id in self.calls:
                self._invalidate(self.calls[call_id], "duplicate follow-up invocation")
                return
            self.calls[call_id] = dict(
                task_name=prior["task_name"], task_dir=prior["task_dir"],
                task_run_id=prior["task_run_id"],
                followup_child=prior.get("child_id") or prior["followup_child"],
                followup_path=prior.get("agent_path") or prior["followup_path"],
                generation=prior.get("generation", 1) + 1,
            )
            return
        spawn = _spawn_call(event)
        if spawn:
            call_id, task_name = spawn
            item = self.calls.setdefault(call_id, {})
            binding = _require_task_binding(
                self.repo_root, self.root_id, self.expected_generation, self.session_cwd,
            )
            active_task = binding.get("task_dir")
            if _event_precedes_run(event, binding.get("run_id", "")):
                item["invalid"] = True
                return
            self._set_once(item, "task_name", task_name)
            self._set_once(item, "task_dir", active_task)
            self._set_once(item, "task_run_id", binding.get("run_id", ""))
            self._maybe_start(call_id)
            return
        interacted = _spawn_activity(event, kind="interacted")
        activity = _spawn_activity(event) or interacted
        if activity:
            call_id, child_id, agent_path = activity
            item = self.calls.get(call_id)
            if not item or not item.get("task_name"):
                return
            if item.get("followup_child"):
                if (not interacted or child_id != item["followup_child"]
                        or agent_path != item["followup_path"]):
                    self._invalidate(item, "follow-up activity changed child identity")
                    return
                self._set_once(item, "output_path", agent_path)
            elif interacted:
                return  # Activity alone cannot create an invocation.
            self._set_once(item, "child_id", child_id)
            self._set_once(item, "agent_path", agent_path)
            self._maybe_start(call_id)
            return
        payload = _event_payload(event, "response_item")
        output_call_id = str(payload.get("call_id") or "") if payload else ""
        if (
            output_call_id
            and payload
            and payload.get("type") == "function_call_output"
            and not (self.calls.get(output_call_id) or {}).get("task_name")
        ):
            return
        output = _spawn_output(event)
        if output:
            call_id, agent_path = output
            item = self.calls.setdefault(call_id, {})
            if item.get("followup_child"):
                if agent_path != item["followup_path"]:
                    self._invalidate(item, "follow-up output changed child identity")
                return
            self._set_once(item, "output_path", agent_path)
            self._maybe_start(call_id)
            return
        if not payload or payload.get("type") != "agent_message":
            return
        recipient = str(payload.get("recipient") or "")
        delivery = _root_delivery(event, recipient)
        if not delivery:
            return
        agent_path, root_final = delivery
        # Preserve this original observed delivery across an ancestry retry.
        # The native coordinator/child proof and final equality remain mandatory
        # before _maybe_complete can publish any receipt.
        if recipient != agent_path.rsplit("/", 1)[0]:
            return
        self._deliver(agent_path, root_final)

    def _deliver(self, agent_path: str, root_final: str) -> dict[str, Any] | None:
        # Native deliveries are ordered per child. Complete the oldest observed
        # invocation still awaiting a delivery, including a queued follow-up.
        item = next((entry for entry in self.calls.values()
                     if (entry.get("agent_path") or entry.get("followup_path")) == agent_path
                     and not entry.get("invalid") and entry.get("root_final") is None), None)
        item = item or self.by_agent.get(agent_path) or next(
            (entry for entry in self.calls.values() if entry.get("agent_path") == agent_path), None,
        )
        if not item:
            return None
        if item.get("root_final") is not None:
            if item["root_final"] == root_final:
                return item
            self._invalidate(item, "duplicate or ambiguous root completion delivery")
            return item
        item["root_final"] = root_final
        self._maybe_complete(item)
        return item


def _drain_oversized_line(
    handle: Any,
    path: Path,
    trust_root: Path,
    stop_event: threading.Event,
    initial_size: int,
    deadline: float,
) -> bool:
    """Skip one oversized rollout record without accepting it as evidence."""
    total = initial_size
    while (
        total <= MAX_CHILD_BYTES
        and not stop_event.is_set()
        and time.monotonic() < deadline
    ):
        remaining = MAX_CHILD_BYTES - total
        raw = handle.readline(min(MAX_LINE_BYTES + 1, remaining + 1))
        if raw:
            total += len(raw)
            if total > MAX_CHILD_BYTES:
                return False
            if raw.endswith(b"\n"):
                return True
            continue
        try:
            current = os.fstat(handle.fileno())
            if current.st_size < handle.tell() or not _path_matches_handle(
                path, trust_root, handle
            ):
                return False
        except OSError:
            return False
        stop_event.wait(min(POLL_SECONDS, max(0.0, deadline - time.monotonic())))
    return False


def watch(
    repo_root: str,
    thread_id: str,
    rollout: str,
    offset: int,
    *,
    session_cwd: str | None = None,
    task_id: str = "",
    run_id: str = "",
    stop_event: threading.Event | None = None,
    idle_seconds: float = IDLE_SECONDS,
    on_error: Any | None = None,
    recovering: bool = False,
    expected_generation: RegistrationGeneration | None = None,
) -> int:
    """Tail one registered root rollout inside an MCP-owned thread."""
    repo_root = os.path.realpath(repo_root)
    session_cwd = os.path.realpath(session_cwd or repo_root)
    if not _workspace_matches(session_cwd, repo_root):
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
    watcher = Watcher(
        repo_root,
        thread_id,
        session_cwd=session_cwd,
    )
    stop_event = stop_event or threading.Event()
    rollout_age = max(0.0, time.time() - rollout_info.st_mtime)
    last_data = time.monotonic() - min(rollout_age, idle_seconds)
    observation_failed = bool(recovering)
    discarded_lifecycle_failure = False
    failed_position: int | None = None
    failed_attempts = 0

    def notify(message: str) -> None:
        if on_error is None:
            return
        try:
            on_error(message)
        except Exception:
            pass

    def observe(action: Any) -> bool:
        """Retry one record from a rolled-back in-memory lifecycle state."""
        nonlocal observation_failed
        state = copy.deepcopy((
            watcher.calls,
            watcher.by_agent,
            watcher.receipt_progress,
            getattr(watcher, "replay_recovery_progress", 0),
        ))
        progress_before = watcher.receipt_progress
        replay_before = getattr(watcher, "replay_recovery_progress", 0)
        try:
            action()
        except Exception as exc:
            (
                watcher.calls,
                watcher.by_agent,
                watcher.receipt_progress,
                watcher.replay_recovery_progress,
            ) = state
            if isinstance(exc, (_BindingUnavailable, _RegistrationChanged)):
                raise
            observation_failed = True
            detail = " ".join(str(exc).split())[:240]
            notify(f"{type(exc).__name__}: {detail}".rstrip())
            return False
        if (
            observation_failed
            and not discarded_lifecycle_failure
            and (
                watcher.receipt_progress > progress_before
                or getattr(watcher, "replay_recovery_progress", 0) > replay_before
            )
        ):
            observation_failed = False
            notify("")
        return True
    try:
        if task_id or run_id:
            if expected_generation is None:
                expected_generation = _current_registration_generation(
                    repo_root, thread_id, session_cwd,
                )
            if (
                expected_generation[0] != rollout
                or expected_generation[1] != offset
                or expected_generation[3:] != (task_id, run_id)
            ):
                raise _BindingUnavailable("binding unavailable")
        watcher.expected_generation = expected_generation
        handle.seek(max(0, offset))
        while not stop_event.is_set() and time.monotonic() - last_data < idle_seconds:
            if task_id or run_id:
                binding = _require_task_binding(
                    repo_root, thread_id, expected_generation, session_cwd,
                )
                if (
                    os.path.basename(str(binding.get("task_dir") or "")) != task_id
                    or binding.get("run_id") != run_id
                ):
                    return 0
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
                observe(watcher.retry)
                stop_event.wait(POLL_SECONDS)
                continue
            if len(raw) > MAX_LINE_BYTES:
                # Oversized records are never lifecycle evidence, but a bounded
                # drain lets later independent records continue through the tail.
                if not raw.endswith(b"\n") and not _drain_oversized_line(
                    handle,
                    path,
                    trust_root,
                    stop_event,
                    len(raw),
                    last_data + idle_seconds,
                ):
                    return 3
                last_data = time.monotonic()
                continue
            if not raw.endswith(b"\n"):
                handle.seek(position)
                stop_event.wait(POLL_SECONDS)
                continue
            event = _load_json_line(raw)
            if event is None:
                # A malformed record is not lifecycle evidence. Reject only
                # that record so a corrupt diagnostic/tool-output line cannot
                # permanently blind the watcher to later independent agents.
                # It is quarantined locally rather than published through the
                # worker-error channel: that channel gates the first review
                # spawn, so a harmless non-evidence line there would create a
                # self-deadlock with no receipt progress available to clear it.
                last_data = time.monotonic()
                continue
            last_data = time.monotonic()
            if not observe(lambda: watcher.feed(event)):
                # Retry a transient failure from the rolled-back lifecycle
                # state, but never let one permanently rejected record blind
                # the tail to later independent agents. Externally appended
                # receipts remain idempotently detectable during replay.
                if failed_position == position:
                    failed_attempts += 1
                else:
                    failed_position = position
                    failed_attempts = 1
                if failed_attempts < MAX_RECORD_OBSERVATION_ATTEMPTS:
                    handle.seek(position)
                    stop_event.wait(POLL_SECONDS)
                    continue
                # Advancing past a valid lifecycle record after exhausting
                # retries loses information that may have been required to
                # invalidate earlier evidence. Keep the live failure sticky;
                # unrelated later receipt progress cannot prove that skipped
                # record was safely applied.
                discarded_lifecycle_failure = True
                failed_position = None
                failed_attempts = 0
                continue
            failed_position = None
            failed_attempts = 0
    except _RegistrationChanged:
        return 0
    except _BindingUnavailable:
        notify("binding unavailable")
        return 5
    finally:
        handle.close()
    return 0


RegistrationGeneration = tuple[str, int, int | float, str, str]


def _registration_generation(registration: dict[str, Any]) -> RegistrationGeneration:
    """Return the immutable manager-local identity for a validated registration."""
    return (
        str(registration["rollout"]),
        int(registration["offset"]),
        registration["registered_at"],
        str(registration.get("task_id") or ""),
        str(registration.get("run_id") or ""),
    )


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
        self.seen: dict[str, RegistrationGeneration] = {}
        self.worker_generations: dict[str, RegistrationGeneration] = {}
        self.worker_results: dict[str, int] = {}
        self.worker_errors: dict[str, str] = {}
        self._lock = threading.Lock()

    def _worker(self, registration: dict[str, Any], lease: Any) -> None:
        thread_id = str(registration["thread_id"])
        def note_error(message: str) -> None:
            with self._lock:
                if message:
                    self.worker_errors[thread_id] = message[:320]
                else:
                    self.worker_errors.pop(thread_id, None)
        try:
            with self._lock:
                recovering = bool(self.worker_errors.get(thread_id))
            result = watch(
                str(registration["repo_root"]),
                thread_id,
                str(registration["rollout"]),
                int(registration["offset"]),
                session_cwd=str(registration.get("session_cwd") or self.repo_root),
                task_id=str(registration.get("task_id") or ""),
                run_id=str(registration.get("run_id") or ""),
                stop_event=self.stop_event,
                on_error=note_error,
                recovering=recovering,
                expected_generation=_registration_generation(registration),
            )
            if result != 0:
                note_error(
                    "binding unavailable" if result == 5
                    else f"watcher exited with status {result}"
                )
        except Exception as exc:
            result = 4
            note_error(f"{type(exc).__name__}: {' '.join(str(exc).split())[:240]}".rstrip())
        finally:
            lease.close()
        with self._lock:
            self.worker_results[thread_id] = result

    def worker_error(self, thread_id: str) -> str:
        """Return a bounded advisory error for one registered root watcher."""
        with self._lock:
            return self.worker_errors.get(thread_id, "")

    def is_running(self) -> bool:
        """Report the manager thread itself, not merely its container object."""
        return bool(
            self.thread is not None
            and self.thread.is_alive()
            and not self.stop_event.is_set()
        )

    def scan_once(self) -> int:
        """Start one daemon worker for every newly registered root thread."""
        started = 0
        try:
            items = [dict(item, repo_root=root) for root in workspace_roots(self.repo_root)
                     for item in registrations(root)]
        except Exception:
            return 0
        with self._lock:
            # Failed workers must be restartable from the registration's
            # immutable offset. Successful workers retain their exact
            # generation so the same registration is not replayed
            # continuously, while a refreshed registration remains eligible.
            for thread_id, worker in list(self.workers.items()):
                if worker.is_alive():
                    continue
                self.workers.pop(thread_id, None)
                generation = self.worker_generations.pop(thread_id, None)
                if (
                    self.worker_results.get(thread_id, 0) != 0
                    and generation is not None
                    and self.seen.get(thread_id) == generation
                ):
                    self.seen.pop(thread_id, None)
            for item in items:
                active = sum(1 for worker in self.workers.values() if worker.is_alive())
                if active >= self.max_workers:
                    break
                thread_id = str(item.get("thread_id") or "")
                if thread_id in self.workers:
                    continue
                generation = _registration_generation(item)
                if self.seen.get(thread_id) == generation:
                    continue
                lease = _acquire_registration_lease(str(item["repo_root"]), thread_id)
                if lease is None:
                    continue
                self.seen[thread_id] = generation
                self.worker_generations[thread_id] = generation
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
        binding = _active_task_binding_for_session(args.repo_root, args.thread_id)
        task_dir = str(binding.get("task_dir") or "")
        task_id = os.path.basename(os.path.normpath(task_dir)) if task_dir else ""
        run_id = str(binding.get("run_id") or "")
        if task_id and run_id and ensure(
            args.repo_root, args.thread_id, task_id=task_id, run_id=run_id,
        ):
            return 0
        if time.monotonic() >= deadline:
            return 1
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


if __name__ == "codex_lifecycle_watcher":
    _bind_runtime_receipt_adapter("codex_session_watcher:collaboration", watch)
del _bind_runtime_receipt_adapter


if __name__ == "__main__":
    raise SystemExit(main())
