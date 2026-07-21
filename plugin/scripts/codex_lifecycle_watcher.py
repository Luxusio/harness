#!/usr/bin/env python3
"""Observe Codex subagent lifecycle events that plugin PostToolUse omits.

The watcher is started by the trusted SessionStart hook.  It tails only the
current root rollout from the launch offset, captures the source fingerprint
before a child finishes, and accepts a completion only when root delivery and
the child rollout agree.  It never reconstructs a PASS from historical finals.
"""
from __future__ import annotations

import argparse
import errno
try:
    import fcntl
except ImportError:  # pragma: no cover - Codex currently ships POSIX hooks
    fcntl = None
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Any

SCRIPTS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPTS_DIR)

from _lib import (  # type: ignore
    _infer_receipt_lens,
    extract_qa_verdict,
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
MAX_LINE_BYTES = 2 * 1024 * 1024
MAX_CHILD_BYTES = 64 * 1024 * 1024
POLL_SECONDS = 0.20
IDLE_SECONDS = 8 * 60 * 60
RUNTIME_SUBDIR = os.path.join("doc", "harness", "runtime", "codex-watchers")


def _codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex").resolve()


def _sessions_root() -> Path:
    return (_codex_home() / "sessions").resolve()


def _safe_regular_file(path: Path, root: Path, *, max_size: int | None = None) -> bool:
    try:
        root = root.resolve(strict=True)
        absolute = path.absolute()
        relative = absolute.relative_to(root)
        cursor = root
        for part in relative.parts:
            cursor = cursor / part
            mode = os.lstat(cursor).st_mode
            if stat.S_ISLNK(mode):
                return False
        candidate = absolute.resolve(strict=True)
        candidate.relative_to(root)
        info = os.stat(candidate, follow_symlinks=False)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            return False
        if max_size is not None and info.st_size > max_size:
            return False
        return True
    except (OSError, ValueError):
        return False


def _find_rollout(thread_id: str) -> Path | None:
    if not THREAD_RE.fullmatch(thread_id):
        return None
    root = _sessions_root()
    try:
        candidates = list(root.glob(f"**/rollout-*{thread_id}.jsonl"))[:3]
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


def _root_meta(path: Path, thread_id: str, repo_root: str) -> bool:
    try:
        with path.open("rb") as handle:
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
                    and os.path.realpath(str(payload.get("cwd") or "")) == repo_root
                    and payload.get("thread_source") != "subagent"
                ):
                    return True
    except OSError:
        return False
    return False


def _runtime_dir(repo_root: str) -> Path:
    return Path(repo_root) / RUNTIME_SUBDIR


def _state_path(repo_root: str, thread_id: str) -> Path:
    return _runtime_dir(repo_root) / f"{thread_id}.json"


def _lock_path(repo_root: str, thread_id: str) -> Path:
    return _runtime_dir(repo_root) / f"{thread_id}.lock"


def _pid_alive(pid: int) -> bool:
    if pid <= 1:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def _process_identity(pid: int) -> tuple[str, str] | None:
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode()
        fields = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()
        return cmdline, fields[21]
    except (OSError, IndexError, UnicodeDecodeError):
        return None


def _watcher_process_matches(state: dict[str, Any]) -> bool:
    try:
        pid = int(state.get("pid") or 0)
    except (TypeError, ValueError):
        return False
    identity = _process_identity(pid)
    if not _pid_alive(pid) or identity is None:
        return False
    cmdline, started = identity
    required = (
        os.path.abspath(__file__), "--watch", str(state.get("thread_id") or ""),
        str(state.get("repo_root") or ""),
    )
    return bool(state.get("process_start") == started and all(value in cmdline for value in required))


def _trusted_runtime_dir(repo_root: str) -> Path | None:
    root = Path(repo_root).resolve()
    cursor = root
    try:
        for part in Path(RUNTIME_SUBDIR).parts[:-1]:
            cursor = cursor / part
            if cursor.exists():
                info = os.lstat(cursor)
                if (
                    stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o022
                ):
                    return None
            else:
                cursor.mkdir(mode=0o755)
        runtime = cursor / Path(RUNTIME_SUBDIR).parts[-1]
        if runtime.exists():
            info = os.lstat(runtime)
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
                return None
            if info.st_mode & 0o022:
                return None
            os.chmod(runtime, 0o700)
        else:
            runtime.mkdir(mode=0o700)
        return runtime
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


def ensure(repo_root: str, thread_id: str) -> bool:
    repo_root = os.path.realpath(find_repo_root(repo_root))
    rollout = _find_rollout(thread_id)
    if rollout is None or not _root_meta(rollout, thread_id, repo_root):
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
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state_path = _state_path(repo_root, thread_id)
        state = _read_owned_json(state_path, runtime)
        if (
            state.get("thread_id") == thread_id
            and state.get("repo_root") == repo_root
            and _watcher_process_matches(state)
        ):
            return True
        offset = rollout.stat().st_size
        process = subprocess.Popen(
            [
                sys.executable,
                os.path.abspath(__file__),
                "--watch",
                "--repo-root", repo_root,
                "--thread-id", thread_id,
                "--rollout", str(rollout),
                "--offset", str(offset),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        identity = None
        for _ in range(10):
            identity = _process_identity(process.pid)
            if identity is not None:
                break
            time.sleep(0.01)
        _atomic_json(state_path, {
            "version": 1,
            "pid": process.pid,
            "repo_root": repo_root,
            "thread_id": thread_id,
            "rollout": str(rollout),
            "offset": offset,
            "started_at": time.time(),
            "process_start": identity[1] if identity else "",
        })
        return True


def _active_task_for_session(repo_root: str, root_id: str) -> str:
    tasks_root = (Path(repo_root) / "doc/harness/tasks").resolve()
    marker = tasks_root / ".active_sessions" / f"{root_id}.json"
    marker_data = _read_owned_json(marker, tasks_root)
    if marker_data.get("session_id") != root_id:
        return ""
    resolved = resolve_active_task_dir(repo_root, session_id=root_id)
    if not resolved or marker_data.get("task_dir") != resolved:
        return ""
    try:
        raw_task_dir = Path(resolved).absolute()
        raw_info = os.lstat(raw_task_dir)
        if stat.S_ISLNK(raw_info.st_mode):
            return ""
        task_dir = raw_task_dir.resolve(strict=True)
        if task_dir.parent != tasks_root or not task_dir.name.startswith("TASK__"):
            return ""
        info = os.lstat(task_dir)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
            return ""
    except (OSError, ValueError):
        return ""
    if not _safe_regular_file(task_dir / "TASK_STATE.yaml", task_dir, max_size=256 * 1024):
        return ""
    state = read_state(str(task_dir))
    if state.get("task_id") != task_dir.name or str(state.get("status") or "").lower() in {"closed", "blocked"}:
        return ""
    if marker_data.get("task_id") != task_dir.name:
        return ""
    return str(task_dir)


def _event_payload(event: dict[str, Any], expected_type: str) -> dict[str, Any] | None:
    payload = event.get("payload")
    if event.get("type") != expected_type or not isinstance(payload, dict):
        return None
    return payload


def _spawn_call(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") != "function_call":
        return None
    if payload.get("namespace") != "collaboration" or payload.get("name") != "spawn_agent":
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    try:
        arguments = json.loads(payload.get("arguments") or "{}")
    except (TypeError, json.JSONDecodeError):
        return None
    task_name = str(arguments.get("task_name") or "") if isinstance(arguments, dict) else ""
    lens = _infer_receipt_lens(task_name)
    if not lens.startswith(("review-", "qa-", "ux-")):
        return None
    return call_id, task_name


def _spawn_output(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") != "function_call_output":
        return None
    call_id = str(payload.get("call_id") or "")
    if not CALL_RE.fullmatch(call_id):
        return None
    output = payload.get("output")
    if isinstance(output, str):
        try:
            output = json.loads(output)
        except json.JSONDecodeError:
            return None
    if not isinstance(output, dict):
        return None
    agent_path = str(output.get("task_name") or "")
    return (call_id, agent_path) if AGENT_PATH_RE.fullmatch(agent_path) else None


def _started_activity(event: dict[str, Any]) -> tuple[str, str, str] | None:
    payload = _event_payload(event, "event_msg")
    if not payload or payload.get("type") != "sub_agent_activity" or payload.get("kind") != "started":
        return None
    call_id = str(payload.get("event_id") or "")
    child_id = str(payload.get("agent_thread_id") or "")
    agent_path = str(payload.get("agent_path") or "")
    if not CALL_RE.fullmatch(call_id) or not THREAD_RE.fullmatch(child_id):
        return None
    if not AGENT_PATH_RE.fullmatch(agent_path):
        return None
    return call_id, child_id, agent_path


def _root_delivery(event: dict[str, Any]) -> tuple[str, str] | None:
    payload = _event_payload(event, "response_item")
    if not payload or payload.get("type") != "agent_message":
        return None
    author = str(payload.get("author") or "")
    if not AGENT_PATH_RE.fullmatch(author) or payload.get("recipient") != "/root":
        return None
    text_parts = []
    for item in payload.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "input_text":
            text_parts.append(str(item.get("text") or ""))
    text = "\n".join(text_parts)
    marker = "Payload:\n"
    final = text.split(marker, 1)[1] if marker in text else text
    return author, final.strip()


def _child_status(
    child_id: str,
    root_id: str,
    agent_path: str,
    repo_root: str,
) -> tuple[str, Path | None, str]:
    path = _find_rollout(child_id)
    sessions = _sessions_root()
    if path is None or not _safe_regular_file(path, sessions, max_size=MAX_CHILD_BYTES):
        return "pending", None, ""
    matching_meta = 0
    child_boundaries = 0
    child_turn = False
    finals: list[str] = []
    completes: list[str] = []
    try:
        with path.open("rb") as handle:
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
                        and os.path.realpath(str(payload.get("cwd") or "")) == repo_root
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
                if child_turn and event.get("type") == "event_msg" and payload.get("type") == "agent_message" and payload.get("phase") == "final_answer":
                    finals.append(str(payload.get("message") or "").strip())
                if child_turn and event.get("type") == "event_msg" and payload.get("type") == "task_complete":
                    completes.append(str(payload.get("last_agent_message") or "").strip())
    except OSError:
        return "pending", path, ""
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


class Watcher:
    def __init__(self, repo_root: str, root_id: str):
        self.repo_root = repo_root
        self.root_id = root_id
        self.calls: dict[str, dict[str, Any]] = {}
        self.by_path: dict[str, dict[str, Any]] = {}

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
            "agent_id": item.get("activity_path", ""),
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
            "runtime_agent_path": item.get("activity_path", ""),
        })

    def _set_once(self, item: dict[str, Any], key: str, value: Any) -> bool:
        if key in item:
            self._invalidate(item, f"duplicate or conflicting {key} event")
            return False
        item[key] = value
        return True

    def _maybe_start(self, call_id: str) -> None:
        item = self.calls.get(call_id) or {}
        if not all(item.get(key) for key in ("task_name", "output_path", "child_id", "activity_path")):
            return
        if item["output_path"] != item["activity_path"]:
            self._invalidate(item, "spawn output path did not match started activity")
            return
        if item.get("invalid") or item.get("started"):
            return
        child_status, _, _ = _child_status(
            item["child_id"], self.root_id, item["activity_path"], self.repo_root
        )
        if child_status == "pending":
            return
        # A child that is invalid or already complete cannot establish a
        # trustworthy start-time snapshot.
        if child_status != "running":
            self._invalidate(item, "child evidence was invalid or already complete at start capture")
            return
        task_dir = _active_task_for_session(self.repo_root, self.root_id)
        lens = _infer_receipt_lens(item["task_name"])
        if not task_dir or not lens.startswith(("review-", "qa-", "ux-")):
            item["invalid"] = True
            return
        event_id = f"{self.root_id}:{call_id}:{item['child_id']}"
        existing = _matching_receipt(
            task_dir, event_id, "started", agent_path=item["activity_path"], lens=lens
        )
        if existing is None:
            receipt = record_subagent_receipt(task_dir, {
                "source": "codex_session_watcher",
                "status": "started",
                "agent_id": item["activity_path"],
                "agent_type": item["task_name"],
                "lens": lens,
                "summary": "Codex runtime spawn observed before child completion",
                "runtime_event_id": event_id,
                "runtime_session_id": self.root_id,
                "runtime_thread_id": item["child_id"],
                "runtime_agent_path": item["activity_path"],
            })
        else:
            receipt = existing
        item.update({
            "started": True,
            "task_dir": task_dir,
            "event_id": event_id,
            "head_sha": receipt.get("head_sha") or "",
            "base_sha": receipt.get("base_sha") or "",
            "diff_fingerprint": receipt.get("diff_fingerprint") or "",
        })
        self.by_path[item["activity_path"]] = item

    def _maybe_complete(self, item: dict[str, Any]) -> None:
        root_final = str(item.get("root_final") or "")
        if not root_final or item.get("completed") or item.get("invalid"):
            return
        status, transcript, child_final = _child_status(
            item["child_id"], self.root_id, item["activity_path"], self.repo_root
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
            agent_path=item["activity_path"], lens=lens, summary=child_final,
        ) is None:
            record_subagent_receipt(item["task_dir"], {
                "source": "codex_session_watcher",
                "status": "completed",
                "agent_id": item["activity_path"],
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
                "runtime_agent_path": item["activity_path"],
            })
        item["completed"] = True

    def retry(self) -> None:
        for call_id in list(self.calls):
            self._maybe_start(call_id)
        for item in list(self.by_path.values()):
            self._maybe_complete(item)

    def feed(self, event: dict[str, Any]) -> None:
        spawn = _spawn_call(event)
        if spawn:
            call_id, task_name = spawn
            item = self.calls.setdefault(call_id, {})
            self._set_once(item, "task_name", task_name)
            self._maybe_start(call_id)
            return
        activity = _started_activity(event)
        if activity:
            call_id, child_id, agent_path = activity
            item = self.calls.setdefault(call_id, {})
            self._set_once(item, "child_id", child_id)
            self._set_once(item, "activity_path", agent_path)
            self._maybe_start(call_id)
            return
        output = _spawn_output(event)
        if output:
            call_id, agent_path = output
            item = self.calls.setdefault(call_id, {})
            self._set_once(item, "output_path", agent_path)
            self._maybe_start(call_id)
            return
        delivery = _root_delivery(event)
        if not delivery:
            return
        agent_path, root_final = delivery
        item = self.by_path.get(agent_path)
        if not item:
            return
        if item.get("root_final") is not None:
            self._invalidate(item, "duplicate or ambiguous root completion delivery")
            return
        item["root_final"] = root_final
        self._maybe_complete(item)


def watch(repo_root: str, thread_id: str, rollout: str, offset: int) -> int:
    repo_root = os.path.realpath(find_repo_root(repo_root))
    path = Path(rollout)
    if _find_rollout(thread_id) != path or not _root_meta(path, thread_id, repo_root):
        return 2
    watcher = Watcher(repo_root, thread_id)
    running = True

    def stop(*_args: Any) -> None:
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    last_data = time.monotonic()
    with path.open("rb") as handle:
        handle.seek(max(0, offset))
        while running and time.monotonic() - last_data < IDLE_SECONDS:
            position = handle.tell()
            raw = handle.readline(MAX_LINE_BYTES + 1)
            if not raw:
                try:
                    if path.stat().st_size < position:
                        return 3
                except OSError:
                    return 3
                handle.seek(position)
                watcher.retry()
                time.sleep(POLL_SECONDS)
                continue
            if len(raw) > MAX_LINE_BYTES or not raw.endswith(b"\n"):
                # Partial tails are retried. Oversized complete records stop the
                # watcher rather than skipping evidence in the candidate chain.
                if not raw.endswith(b"\n"):
                    handle.seek(position)
                    time.sleep(POLL_SECONDS)
                    continue
                return 3
            event = _load_json_line(raw)
            if event is None:
                return 3
            last_data = time.monotonic()
            watcher.feed(event)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--ensure", action="store_true")
    mode.add_argument("--watch", action="store_true")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--thread-id", default=os.environ.get("CODEX_THREAD_ID", ""))
    parser.add_argument("--rollout")
    parser.add_argument("--offset", type=int, default=0)
    args = parser.parse_args(argv)
    if not THREAD_RE.fullmatch(args.thread_id or ""):
        return 0 if args.ensure else 2
    if args.ensure:
        return 0 if ensure(args.repo_root, args.thread_id) else 0
    if not args.rollout:
        return 2
    return watch(args.repo_root, args.thread_id, args.rollout, args.offset)


if __name__ == "__main__":
    raise SystemExit(main())
