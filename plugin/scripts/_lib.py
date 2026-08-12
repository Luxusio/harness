#!/usr/bin/env python3
"""Harness minimal library — stdlib only, exact four-field TASK.json."""

import os
import re
import stat
import subprocess
import tempfile
import json
import hashlib
import inspect
import secrets
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime, timezone
from types import MappingProxyType

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"
RECEIPTS_NAME = "RECEIPTS.jsonl"
TASK_CONTROL_NAME = "TASK.json"
CONVERSATION_NAME = "CONVERSATION.md"
CONVERSATION_TEXT_CAP = 2000
CONVERSATION_READ_CAP = 256 * 1024

TASK_CONTROL_FIELDS = frozenset({
    "run_id", "execution_mode", "required_lenses", "close_receipt_fingerprint",
})
LENS_ORDER = (
    "review-code", "review-security",
    "qa-api", "qa-browser", "qa-cli", "qa-desktop",
)
REVIEW_LENSES = frozenset(lens for lens in LENS_ORDER if lens.startswith("review-"))
QA_LENSES = frozenset(lens for lens in LENS_ORDER if lens.startswith("qa-"))
SUPPORTED_LENSES = frozenset(LENS_ORDER)

# ── Plugin-root env var (AC-006 of TASK__dual-runtime-plugin-claude-codex) ─
#
# Runtime-private env var rename: `CLAUDE_PLUGIN_ROOT` (Claude Code injects)
# → `HARNESS_PLUGIN_ROOT` (runtime-agnostic; works on Codex too). v2.3.0
# ships dual-name fallback. v2.5.0 will drop `CLAUDE_PLUGIN_ROOT` per
# CHANGELOG deprecation window.
#
# External config (`plugin/hooks/hooks.json`, `plugin/.mcp.json`) intentionally
# stays on `${CLAUDE_PLUGIN_ROOT}` for v2.3.0 because Claude Code injects that
# variable. The flip is a v2.4/v2.5 task once Codex side has been validated
# and a parallel injection mechanism is wired.


def plugin_root_env(default: str | None = None) -> str | None:
    """Read the plugin-root env var with dual-name fallback.

    Returns the value of `HARNESS_PLUGIN_ROOT` if set (preferred name).
    Otherwise returns the value of `CLAUDE_PLUGIN_ROOT` (deprecated but
    still supported during the v2.3 → v2.5 overlap window). Returns
    ``default`` (or ``None``) when neither is set.

    Callers reading the env var SHOULD prefer this helper over direct
    ``os.environ.get`` so the rename rolls out consistently. Subprocess
    spawners that set the env for child processes SHOULD set BOTH names
    until v2.5 — see :func:`plugin_root_env_pair` below.
    """
    new = os.environ.get("HARNESS_PLUGIN_ROOT")
    if new:
        return new
    old = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if old:
        return old
    return default


def plugin_root_env_pair(value: str) -> dict[str, str]:
    """Return a dict with both env-var names set to ``value``.

    For subprocess env mappings during the deprecation window. Once v2.5
    drops `CLAUDE_PLUGIN_ROOT`, change this to return ``{"HARNESS_PLUGIN_ROOT": value}``
    only — callsites become a single-key dict assignment automatically.
    """
    return {
        "HARNESS_PLUGIN_ROOT": value,
        "CLAUDE_PLUGIN_ROOT": value,  # deprecated; drop in v2.5
    }


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


_CONVERSATION_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_CONVERSATION_REMINDER_RE = re.compile(r"</?system-reminder[^>]*>", re.IGNORECASE)
_CONVERSATION_ITEM_RE = re.compile(r"<!--\s*item:\s*(.*?)-->", re.IGNORECASE | re.DOTALL)
_CONVERSATION_ATTR_RE = re.compile(r"([A-Za-z0-9_-]+)=((?:\"[^\"]*\")|(?:'[^']*')|[^\s]+)")


def _conversation_sanitize_text(value, limit=CONVERSATION_TEXT_CAP):
    text = str(value or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _CONVERSATION_CONTROL_RE.sub("", text)
    text = _CONVERSATION_REMINDER_RE.sub("[SANITIZED]", text)
    text = text.strip()
    if len(text) > limit:
        text = text[: limit - 12].rstrip() + "\n...truncated"
    return text


def _conversation_attr(value, limit=160):
    text = _conversation_sanitize_text(value, limit=limit)
    text = re.sub(r"\s+", "-", text).strip("-")
    return re.sub(r"[^A-Za-z0-9_.:/@-]", "_", text)[:limit]


def _conversation_path(task_dir):
    return os.path.join(task_dir, CONVERSATION_NAME)


def append_conversation_entry(
    task_dir,
    *,
    role,
    text,
    source="",
    event_id="",
    agent_type="",
):
    """Append a human-readable task-local conversation entry.

    This is hook/script-owned operational history. Close gates must only inspect
    machine markers such as ``<!-- item: type=requirement status=open -->``;
    they must never infer requirements from free-form Markdown prose.
    """
    body = _conversation_sanitize_text(text)
    if not body:
        return False
    label = {
        "user": "User",
        "assistant": "Assistant",
        "subagent": "Subagent",
        "system": "System",
    }.get(str(role or "").lower(), _conversation_sanitize_text(role, limit=80) or "Entry")
    if str(role or "").lower() == "subagent" and agent_type:
        label = f"Subagent: {_conversation_sanitize_text(agent_type, limit=80)}"

    meta = []
    if source:
        meta.append(f"source={_conversation_attr(source)}")
    if event_id:
        meta.append(f"id={_conversation_attr(event_id)}")
    if agent_type:
        meta.append(f"agent_type={_conversation_attr(agent_type)}")

    path = _conversation_path(task_dir)
    try:
        if not task_dir:
            return False
        os.makedirs(task_dir, exist_ok=True)
        needs_header = not os.path.isfile(path) or os.path.getsize(path) == 0
        with open(path, "a", encoding="utf-8") as f:
            if needs_header:
                f.write("# Conversation\n\n<!-- harness:conversation-log v1 -->\n")
            f.write(f"\n## {now_iso()} - {label}\n")
            if meta:
                f.write(f"<!-- event: {' '.join(meta)} -->\n")
            f.write("\n")
            f.write(body)
            f.write("\n")
        return True
    except OSError:
        return False


def _conversation_marker_attrs(blob):
    attrs = {}
    for key, raw in _CONVERSATION_ATTR_RE.findall(blob or ""):
        value = raw.strip()
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            value = value[1:-1]
        attrs[key.lower()] = value
    return attrs


def conversation_open_items(task_dir):
    """Return unresolved machine-readable items from CONVERSATION.md."""
    path = _conversation_path(task_dir)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - CONVERSATION_READ_CAP))
            text = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    items = []
    for match in _CONVERSATION_ITEM_RE.finditer(text):
        attrs = _conversation_marker_attrs(match.group(1))
        if str(attrs.get("status") or "").lower() != "open":
            continue
        items.append({
            "type": attrs.get("type", "unknown"),
            "key": attrs.get("key", ""),
            "summary": attrs.get("summary", ""),
        })
    return items


# ── Hook I/O + gate signalling ───────────────────────────────────────────
#
# Claude Code hooks receive tool context on stdin (JSON) and signal decisions
# via stdout JSON. Exit codes are masked by `|| true` (C-12 fail-safe), so
# exit-based signalling is unreliable; stdout payload is authoritative.

import json as _json  # noqa: E402  (kept after module constants on purpose)
import sys as _sys    # noqa: E402


_STDIN_CAP_BYTES = 1 << 16  # 64 KiB read cap for hook payload

# Module-level cache of the most-recent parsed hook input, populated by
# read_hook_input() on first call. AC-007 of TASK__dual-runtime-plugin-claude-codex
# uses last_hook_input() in gate scripts' outer except so log_gate_crash can
# capture payload keys + tool_name even when main() raises before returning.
_LAST_HOOK_INPUT: dict = {}
GOAL_PAYLOAD_DEBUG_DIR = os.path.join("doc", "harness", "debug", "goal-hook-payloads")
GOAL_PAYLOAD_MARKER = os.path.join("doc", "harness", "debug", "CAPTURE_GOAL_PAYLOADS")
GOAL_PAYLOAD_VALUE_CAP = 2000
GOAL_PAYLOAD_RAW_CAP = 32000
GOAL_TRANSCRIPT_TAIL_CAP = 65536
GOALS_DIR = os.path.join("doc", "harness", "goals")
GOAL_CURRENT_FILE = os.path.join(GOALS_DIR, "current.json")


def read_hook_input():
    """Read stdin payload from Claude Code hook (capped at 64 KiB).

    Returns parsed JSON dict, or empty dict on any failure. Never raises —
    callers on the hot path must not block when stdin is malformed or absent.
    Also stashes the parsed dict in module-level cache so :func:`last_hook_input`
    can retrieve it from an outer except where the local was lost.
    """
    global _LAST_HOOK_INPUT
    try:
        raw = _sys.stdin.read(_STDIN_CAP_BYTES)
    except Exception:
        _LAST_HOOK_INPUT = {}
        return {}
    if not raw:
        _LAST_HOOK_INPUT = {}
        return {}
    try:
        data = _json.loads(raw)
        out = data if isinstance(data, dict) else {}
        _LAST_HOOK_INPUT = out
        return out
    except Exception:
        _LAST_HOOK_INPUT = {}
        return {}


def last_hook_input() -> dict:
    """Return the most recent parsed hook input, or empty dict.

    Populated by :func:`read_hook_input`. Used by gate scripts' top-level
    except wrappers to thread the original payload into :func:`log_gate_crash`
    without having to refactor every gate's main() signature.
    """
    return _LAST_HOOK_INPUT


def _goal_probe_capture_enabled(repo_root: str) -> bool:
    env = str(os.environ.get("HARNESS_CAPTURE_GOAL_PAYLOADS") or "").strip().lower()
    if env in {"1", "true", "yes", "on"}:
        return True
    return os.path.isfile(os.path.join(repo_root, GOAL_PAYLOAD_MARKER))


def _goal_probe_safe(value: object, default: str = "unknown") -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("._")
    return (safe or default)[:80]


def _goal_probe_runtime(data: dict) -> str:
    raw = (
        os.environ.get("HARNESS_RUNTIME")
        or data.get("runtime")
        or data.get("client")
        or data.get("source")
        or "unknown"
    )
    return _goal_probe_safe(raw)


def _goal_probe_session(data: dict) -> str:
    raw = (
        data.get("session_id")
        or data.get("sessionId")
        or os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or "no-session"
    )
    return _goal_probe_safe(raw, default="no-session")[:40]


def _goal_probe_text(value: object) -> str:
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", str(value or ""))
    cleaned = re.sub(r"</?system-reminder[^>]*>", "[SANITIZED]", cleaned, flags=re.IGNORECASE)
    return " ".join(cleaned.split()).strip()


def _goal_probe_prompt_candidates(data: dict) -> list[dict]:
    out: list[dict] = []
    for key in ("prompt", "user_prompt", "message", "text", "content"):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        clean = _goal_probe_text(value)[:GOAL_PAYLOAD_VALUE_CAP]
        lowered = clean.lower()
        out.append({
            "field": key,
            "length": len(value),
            "excerpt": clean,
            "looks_like_goal_command": lowered.startswith("/goal") or lowered.startswith("/골"),
        })
    return out


def _goal_probe_transcript_candidates(data: dict) -> list[dict]:
    path = data.get("transcript_path")
    if not isinstance(path, str) or not path:
        return []
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - GOAL_TRANSCRIPT_TAIL_CAP))
            tail = f.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[dict] = []
    for line in tail.splitlines():
        if "/goal" not in line and "Goal set" not in line and "goal" not in line.lower():
            continue
        clean = _goal_probe_text(line)
        if not clean:
            continue
        out.append({
            "excerpt": clean[:GOAL_PAYLOAD_VALUE_CAP],
            "contains_slash_goal": "/goal" in clean,
            "contains_goal_set": "Goal set" in clean,
        })
        if len(out) >= 10:
            break
    return out


def write_goal_payload_probe(repo_root: str, data: dict, *, source: str = "") -> bool:
    """Opt-in /goal payload probe for discovering runtime hook envelope shape.

    Disabled by default because hook payloads and transcripts can contain user
    prompt text. Enable with HARNESS_CAPTURE_GOAL_PAYLOADS=1 or by creating
    doc/harness/debug/CAPTURE_GOAL_PAYLOADS in the repo.
    """
    if not isinstance(data, dict) or not _goal_probe_capture_enabled(repo_root):
        return False
    try:
        override_dir = os.environ.get("HARNESS_GOAL_PAYLOAD_DIR")
        out_dir = override_dir or os.path.join(repo_root, GOAL_PAYLOAD_DEBUG_DIR)
        if not override_dir:
            repo_abs = os.path.abspath(repo_root)
            out_abs = os.path.abspath(out_dir)
            if os.path.commonpath((repo_abs, out_abs)) != repo_abs:
                return False
            current = repo_abs
            parts = os.path.relpath(out_abs, repo_abs).split(os.sep)
        else:
            out_abs = os.path.abspath(out_dir)
            current = os.path.sep
            parts = out_abs.strip(os.sep).split(os.sep)
        for part in parts:
            current = os.path.join(current, part)
            if os.path.islink(current):
                return False
        existed = os.path.isdir(out_abs)
        os.makedirs(out_abs, mode=0o700, exist_ok=True)
        if os.path.islink(out_abs):
            return False
        if existed:
            if os.stat(out_abs).st_mode & 0o077:
                return False
        else:
            os.chmod(out_abs, 0o700)
        out_dir = out_abs
        ts = now_iso().replace("-", "").replace(":", "")
        runtime = _goal_probe_runtime(data)
        event = str(data.get("hook_event_name") or data.get("hookEventName") or source or "hook")
        event_safe = _goal_probe_safe(event, default="hook")
        session = _goal_probe_session(data)
        raw = json.dumps(data, ensure_ascii=False, sort_keys=True)
        record = {
            "_captured_at": now_iso(),
            "_event_inferred": event,
            "_keys_at_top_level": sorted(data.keys()),
            "_runtime_inferred": runtime,
            "prompt_candidates": _goal_probe_prompt_candidates(data),
            "transcript_candidates": _goal_probe_transcript_candidates(data),
            "raw_payload_truncated": len(raw) > GOAL_PAYLOAD_RAW_CAP,
            "envelope": data if len(raw) <= GOAL_PAYLOAD_RAW_CAP else {"_raw_head": raw[:GOAL_PAYLOAD_RAW_CAP]},
        }
        path = os.path.join(out_dir, f"{runtime}_{event_safe}__{ts}__{session}.json")
        fd, tmp = tempfile.mkstemp(dir=out_dir, prefix=".goal-payload.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            os.chmod(path, 0o600)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return True
    except OSError:
        return False


def goal_command_objective(prompt: object) -> str:
    """Return objective text from a native /goal or /골 prompt, if present."""
    text = _goal_probe_text(prompt)
    lowered = text.lower()
    for prefix in ("/goal", "/골"):
        if lowered == prefix or lowered.startswith(prefix + " "):
            return text[len(prefix):].strip()
    return ""


def _goal_slug(value: str) -> str:
    import hashlib
    words = re.findall(r"[A-Za-z0-9]+", value.lower())
    slug = "-".join(words[:6]) or "goal"
    digest = hashlib.sha1(value.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return f"{slug}-{digest}"


def _goal_id(goal_id: str | None = None, objective: str = "") -> str:
    if goal_id:
        if goal_id.startswith("GOAL__"):
            if not re.fullmatch(r"GOAL__[A-Za-z0-9_.-]{1,180}", goal_id):
                raise ValueError(
                    "invalid goal_id; expected GOAL__ followed by 1-180 letters, "
                    "digits, dot, underscore, or hyphen. Omit goal_id to derive one safely."
                )
            return goal_id
        return f"GOAL__{_goal_slug(goal_id)}"
    return f"GOAL__{_goal_slug(objective or 'goal')}"


def _validated_control_dir(repo_root: str, relative_dir: str, label: str) -> str:
    """Validate existing path components without creating control-plane state."""
    root = os.path.abspath(repo_root)
    current = root
    for part in relative_dir.split("/"):
        current = os.path.join(current, part)
        if not os.path.lexists(current):
            continue
        if os.path.islink(current) or not os.path.isdir(current):
            raise ValueError(f"invalid {label}; {relative_dir} must be a real directory inside the repository")
        try:
            if os.path.commonpath((os.path.realpath(current), os.path.realpath(root))) != os.path.realpath(root):
                raise ValueError(f"invalid {label}; {relative_dir} resolves outside the repository")
        except ValueError:
            raise ValueError(f"invalid {label}; {relative_dir} resolves outside the repository") from None
    return os.path.join(root, *relative_dir.split("/"))


def _goal_path(repo_root: str, goal_id: str) -> str:
    safe_goal_id = _goal_id(goal_id)
    goals_dir = _validated_control_dir(repo_root, GOALS_DIR, "goal storage root")
    return os.path.join(goals_dir, f"{safe_goal_id}.json")


def _current_goal_path(repo_root: str) -> str:
    goals_dir = _validated_control_dir(repo_root, GOALS_DIR, "goal storage root")
    return os.path.join(goals_dir, "current.json")


def _read_regular_text_file(path: str, *, max_size: int = 1024 * 1024) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_size:
            return ""
        with os.fdopen(fd, encoding="utf-8") as f:
            fd = -1
            return f.read()
    except (OSError, UnicodeError):
        return ""
    finally:
        if fd >= 0:
            os.close(fd)


def _strict_regular_text_snapshot(
    path: str, *, max_size: int = 1024 * 1024, allow_symlink: bool = False,
):
    """Snapshot an absent or stable regular UTF-8 leaf without ambiguity."""
    parent = os.path.abspath(os.path.dirname(path) or ".")
    leaf = os.path.basename(path)
    binding_fn = globals().get("_receipt_lock_binding")
    binding = binding_fn(parent) if callable(binding_fn) else None
    dir_fd = binding[1] if binding is not None else None
    if binding is not None:
        _validate_receipt_dir_binding(binding)

    def leaf_stat():
        if dir_fd is None:
            return os.lstat(path)
        return os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False)

    try:
        before = leaf_stat()
    except FileNotFoundError:
        return {"exists": False, "kind": "absent", "text": ""}
    except OSError as exc:
        raise RuntimeError(f"snapshot unavailable for {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        if not allow_symlink:
            raise RuntimeError(f"snapshot requires a regular non-symlink file: {path}")
        target = os.readlink(leaf, dir_fd=dir_fd) if dir_fd is not None else os.readlink(path)
        after = leaf_stat()
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"snapshot identity changed after read: {path}")
        return {"exists": True, "kind": "symlink", "target": target, "text": ""}
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"snapshot requires a regular non-symlink file: {path}")
    if before.st_size > max_size:
        raise RuntimeError(f"snapshot exceeds size limit: {path}")
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(leaf, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size > max_size
        ):
            raise RuntimeError(f"snapshot identity changed before read: {path}")
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            text = handle.read()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"snapshot read unavailable for {path}: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        after = leaf_stat()
    except OSError as exc:
        raise RuntimeError(f"snapshot identity changed after read: {path}") from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise RuntimeError(f"snapshot identity changed after read: {path}")
    return {"exists": True, "kind": "regular", "text": text}


def _restore_text_snapshots(snapshots):
    first_error = None
    for path, snapshot in snapshots.items():
        try:
            if not snapshot["exists"]:
                parent = os.path.abspath(os.path.dirname(path) or ".")
                leaf = os.path.basename(path)
                binding = _receipt_lock_binding(parent) if "_receipt_lock_binding" in globals() else None
                try:
                    if binding is not None:
                        _validate_receipt_dir_binding(binding)
                        os.unlink(leaf, dir_fd=binding[1])
                    else:
                        os.unlink(path)
                except FileNotFoundError:
                    pass
            elif snapshot.get("kind") == "symlink":
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                os.symlink(snapshot["target"], path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _atomic_text_write(path, snapshot["text"])
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _read_json_file(path: str, *, max_size: int = 1024 * 1024) -> dict:
    try:
        data = json.loads(_read_regular_text_file(path, max_size=max_size))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_text_write(path: str, text: str) -> None:
    """Replace a text leaf without following a pre-existing leaf symlink."""
    parent = os.path.abspath(os.path.dirname(path) or ".")
    leaf = os.path.basename(path)
    binding_fn = globals().get("_receipt_lock_binding")
    binding = binding_fn(parent) if callable(binding_fn) else None
    if binding is not None:
        _validate_receipt_dir_binding(binding)
        dir_fd = binding[1]
        tmp = f".text.{secrets.token_hex(8)}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
        try:
            payload = text.encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("atomic task artifact write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.replace(tmp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
            _validate_receipt_dir_binding(binding)
        finally:
            os.close(fd)
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        return
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".text.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def read_current_goal(repo_root: str | None = None) -> dict:
    root = repo_root or find_repo_root()
    current = _read_json_file(_current_goal_path(root))
    if current.get("goal_id"):
        return current
    return {}


def write_goal_state(repo_root: str, state: dict) -> dict:
    raw_goal_id = str(state.get("goal_id") or "")
    goal_id = _goal_id(raw_goal_id, str(state.get("objective") or ""))
    state = dict(state)
    state["goal_id"] = goal_id
    state["updated_at"] = now_iso()
    goals_dir = _validated_control_dir(repo_root, GOALS_DIR, "goal storage root")
    os.makedirs(goals_dir, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    paths = (_goal_path(repo_root, goal_id), _current_goal_path(repo_root))
    snapshots = {
        path: _strict_regular_text_snapshot(path, allow_symlink=True)
        for path in paths
    }
    try:
        for path in paths:
            _atomic_text_write(path, text)
    except Exception:
        _restore_text_snapshots(snapshots)
        raise
    return state


def start_harness_goal(
    repo_root: str,
    objective: str,
    *,
    goal_id: str | None = None,
    source: dict | None = None,
) -> dict:
    objective = _goal_probe_text(objective)
    if not objective:
        raise ValueError("objective required")
    gid = _goal_id(goal_id, objective)
    existing = _read_json_file(_goal_path(repo_root, gid))
    current = read_current_goal(repo_root)
    if current.get("status") == "active" and current.get("goal_id") == gid:
        existing = current
    state = {
        "goal_id": gid,
        "objective": objective,
        "status": "active",
        "created_at": existing.get("created_at") or now_iso(),
        "updated_at": now_iso(),
        "source": source or existing.get("source") or {},
        "tasks": existing.get("tasks") if isinstance(existing.get("tasks"), list) else [],
    }
    return write_goal_state(repo_root, state)


def add_goal_task(repo_root: str, task_id: str, *, title: str = "", status: str = "queued", task_dir: str = "") -> dict:
    current = read_current_goal(repo_root)
    if not current:
        raise ValueError("no active goal")
    if current.get("status") != "active":
        raise ValueError("goal is terminal; call goal_start explicitly before changing child tasks")
    canonical_dir = canonical_task_dir(
        task_id=task_id,
        task_dir=task_dir or None,
        repo_root=repo_root,
    )
    tid = os.path.basename(canonical_dir)
    stored_task_dir = os.path.relpath(canonical_dir, repo_root).replace(os.sep, "/")
    tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
    updated = False
    for task in tasks:
        if isinstance(task, dict) and task.get("task_id") == tid:
            if title:
                task["title"] = title
            if status:
                task["status"] = status
            task["task_dir"] = stored_task_dir
            updated = True
            break
    if not updated:
        tasks.append({
            "task_id": tid,
            "title": title or tid,
            "status": status or "queued",
            "task_dir": stored_task_dir,
        })
    current["tasks"] = tasks
    return write_goal_state(repo_root, current)


def next_goal_task(repo_root: str) -> dict:
    current = read_current_goal(repo_root)
    tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
    for task in tasks:
        if isinstance(task, dict) and task.get("status") in {"queued", "active"}:
            return {"goal": current, "task": task}
    return {"goal": current, "task": None}


def finish_harness_goal(repo_root: str, *, status: str = "complete") -> dict:
    current = read_current_goal(repo_root)
    if not current:
        raise ValueError("no active goal")
    if current.get("status") != "active":
        raise ValueError("goal is terminal; call goal_start explicitly before finishing it again")
    final_status = status if status in {"complete", "blocked"} else "complete"
    if final_status == "complete":
        tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
        blockers = []
        validated = []
        if not tasks:
            blockers.append("no child tasks")
        for task in tasks:
            if not isinstance(task, dict):
                blockers.append("invalid child task entry")
                continue
            task_id = str(task.get("task_id") or "")
            task_dir = ""
            try:
                task_dir = canonical_task_dir(
                    task_id=task_id,
                    task_dir=str(task.get("task_dir") or "") or None,
                    repo_root=repo_root,
                )
                state = read_task_control(task_dir)
            except (OSError, ValueError):
                state = {}
            if (
                task.get("status") != "closed"
                or task_control_status(task_dir, state) != "closed"
            ):
                blockers.append(task_id or "<missing task_id>")
            else:
                validated.append((task_id, task_dir))
        if not blockers:
            for task_id, task_dir in validated:
                final_state = read_task_control(task_dir)
                if (
                    not final_state
                    or task_control_status(task_dir, final_state) != "closed"
                ):
                    blockers.append(task_id)
        if blockers:
            raise ValueError(
                "goal completion blocked by unfinished or unverified child tasks: "
                + ", ".join(blockers)
            )
    current["status"] = final_status
    current["finished_at"] = now_iso()
    return write_goal_state(repo_root, current)


def _hook_payload_cwd():
    """Return Codex/Claude hook payload cwd when it is present and usable."""
    cwd = _LAST_HOOK_INPUT.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    return None


def current_session_id(default="default"):
    """Return the current hook/session id in a filesystem-safe form.

    Codex hook payloads include ``session_id``. Claude-side availability varies,
    so env vars are accepted as a fallback and ``default`` preserves legacy
    behavior for MCP calls/tests that do not run inside a hook.
    """
    raw = (
        _LAST_HOOK_INPUT.get("session_id")
        or _LAST_HOOK_INPUT.get("sessionId")
        or os.environ.get("HARNESS_SESSION_ID")
        or os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or default
    )
    return sanitize_session_id(raw or default, default=default)


def sanitize_session_id(value, default="default"):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("._")
    return safe or default


def emit_permission_decision(decision, reason="", *, next_action_command="",
                             owner_skill="", docs=""):
    """Emit a Claude Code PreToolUse permission decision on stdout.

    ``decision="deny"`` writes the hookSpecificOutput envelope and returns.
    Any other value (``"allow"``) is silent — silence is the trust signal for
    allowed calls (Phase 4 DX consensus). Never raises.

    The optional ``next_action_command`` / ``owner_skill`` / ``docs`` fields are
    appended to the permissionDecisionReason as an arrow-prefixed tail so the
    PreToolUse envelope stays shape-stable while the orchestrator gets the
    actionable next step inline (2026-05-12 gate-friction retro).

    Caller is responsible for exiting 0 after this returns; the hook's ``|| true``
    wrapper guarantees the shell exit code is 0 regardless.
    """
    if decision != "deny":
        return
    full_reason = str(reason)
    tail_lines = []
    if next_action_command:
        tail_lines.append(f"↳ next action: {next_action_command}")
    if owner_skill:
        tail_lines.append(f"↳ owner: {owner_skill}")
    if docs:
        tail_lines.append(f"↳ docs: {docs}")
    if tail_lines:
        full_reason = full_reason + "\n\n" + "\n".join(tail_lines)
    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": full_reason[:2000],
        }
    }
    try:
        _sys.stdout.write(_json.dumps(envelope))
        _sys.stdout.flush()
    except Exception:
        pass


_ESCAPE_KEYS = {
    "prewrite": "HARNESS_SKIP_PREWRITE",
    "mcp_bash_guard": "HARNESS_SKIP_MCP_GUARD",
}


def _escape_hint(gate_name):
    """Render the one-shot escape-hatch hint appended to deny messages.

    ``gate_name`` is the canonical gate name. Returns a string like
    ``escape: HARNESS_SKIP_PREWRITE=1 <retry>``. Unknown gate names fall back
    to ``HARNESS_SKIP_<UPPER>`` but callers should use the canonical keys so
    the hint stays grep-stable across scripts.
    """
    key = _ESCAPE_KEYS.get(
        gate_name,
        "HARNESS_SKIP_" + str(gate_name or "").upper().replace("-", "_"),
    )
    return f"escape: {key}=1 <retry>"


def _log_gate_error(exc, source):
    """Append a gate-exception entry to doc/harness/learnings.jsonl.

    Best-effort; any failure is swallowed. Used by gate scripts' outer
    try/except so silent fail-open doesn't decay into an invisible dead gate.
    """
    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-error",
            "source": str(source or "gate"),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def log_gate_crash(exc, script, hook_input=None):
    """Structured gate-crash log (AC-007 of TASK__dual-runtime-plugin-claude-codex).

    Payload-aware upgrade over :func:`_log_gate_error`. Records the script
    name, tool name, payload keys, and exception. Used by gate scripts'
    top-level except so a `|| true` swallowed crash leaves a diagnostic
    breadcrumb. Critical for detecting Codex vs Claude payload key drift
    (e.g. `tool_input` vs `input`, `tool_name` vs `tool`) — when a gate
    crashes silently, this is the only post-hoc signal.

    Schema (one JSON line in `doc/harness/learnings.jsonl`):
      ts            ISO timestamp
      type          "gate-crash" (versus _log_gate_error's "gate-error" for legacy callers)
      script        the gate name (e.g. "prewrite_gate", "stop_gate")
      tool_name     hook_input["tool_name"] if present, truncated to 120 chars
      payload_keys  sorted top-level keys of hook_input (for drift detection)
      error         "<ExceptionName>: <message>" capped at 400 chars

    Best-effort; never raises. Safe in `|| true` outer wrapper.
    """
    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        record = {
            "ts": now_iso(),
            "type": "gate-crash",
            "script": str(script or "gate"),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }
        if isinstance(hook_input, dict):
            tn = hook_input.get("tool_name")
            if tn:
                record["tool_name"] = str(tn)[:120]
            try:
                record["payload_keys"] = sorted(hook_input.keys())
            except Exception:
                pass
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
    except Exception:
        pass


def log_gate_bypass(gate_name, path=""):
    """Append a gate-bypass entry when an escape-hatch env var short-circuits a gate."""
    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-bypass",
            "source": str(gate_name or "gate"),
            "path": str(path or ""),
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ── YAML helpers (simple key-value + block arrays, no pyyaml) ────────────


def yaml_field(field, path):
    """Read a scalar field from a flat YAML file."""
    if not os.path.isfile(path):
        return None
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(prefix):
                val = line[len(prefix):].strip()
                if val in ("null", "~", "", "[]"):
                    return None
                return val.strip('"').strip("'")
    return None


def yaml_array(field, path):
    """Read a YAML array field (compact [] or block - item)."""
    if not os.path.isfile(path):
        return []
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            if rest == "[]":
                return []
            items = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.*)", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def _yaml_fmt(val):
    """Format a value for YAML output."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        if not val:
            return "[]"
        def _quote_item(v):
            s = str(v)
            return f'"{s}"' if ":" in s or s != s.strip() else s
        return "\n" + "\n".join(f"  - {_quote_item(v)}" for v in val)
    return str(val)


# ── Frontmatter public API (AC-001) ─────────────────────────────────────
#
# Promoted from note_freshness.py private helpers. These four functions form
# the canonical frontmatter read/write surface used by doc_hygiene.py,
# hygiene_scan.py, and note_freshness.py (which re-imports them).


def split_frontmatter(text: str) -> "tuple[str | None, str, int]":
    """Return (frontmatter_content, body_after_closing_fence, closing_fence_line_index).

    Returns (None, text, -1) if no valid frontmatter found.
    Public alias for the parser promoted from note_freshness.py.
    """
    if not text.startswith("---"):
        return None, text, -1
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None, text, -1
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            fm = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            return fm, body, i
    return None, text, -1


def read_array_field(frontmatter: str, field: str) -> "list[str]":
    """Read a YAML array field from frontmatter string.

    Supports both compact ``[a, b]`` and block ``- item`` styles.
    Public alias promoted from note_freshness.py ``_read_array``.
    """
    lines = frontmatter.splitlines()
    prefix = field + ":"
    for i, ln in enumerate(lines):
        if ln.startswith(prefix):
            rest = ln[len(prefix):].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if not inner:
                    return []
                return [x.strip().strip('"').strip("'") for x in inner.split(",")]
            items: list = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.+?)\s*$", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def read_scalar_field(frontmatter: str, field: str) -> "str | None":
    """Read a scalar field from frontmatter string.

    Public alias promoted from note_freshness.py ``_read_scalar``.
    """
    m = re.search(rf"^{re.escape(field)}:\s*(.*)$", frontmatter, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def set_scalar_field(frontmatter: str, field: str, value: str) -> str:
    """Set (or append) a scalar field in frontmatter string.

    Public alias promoted from note_freshness.py ``_set_scalar``.
    If the field exists it is replaced in-place; otherwise appended.
    """
    pattern = rf"^{re.escape(field)}:\s*.*$"
    replacement = f"{field}: {value}"
    new_fm, n = re.subn(pattern, replacement, frontmatter, count=1, flags=re.MULTILINE)
    if n:
        return new_fm
    new_fm = new_fm.rstrip("\n") + "\n"
    return new_fm + f"{field}: {value}\n"


# ── Task control read/write ──────────────────────────────────────────────


def task_control_file(task_dir):
    return os.path.join(task_dir, TASK_CONTROL_NAME)


def _task_control_lenses(value):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in SUPPORTED_LENSES for item in value)
        or len(value) != len(set(value))
        or value != [lens for lens in LENS_ORDER if lens in value]
        or "review-code" not in value
        or not any(lens.startswith("qa-") for lens in value)
    ):
        return None
    return list(value)


def new_uuid7(timestamp_ms=None):
    """Generate a canonical RFC 9562 UUIDv7 using Python 3.12 stdlib."""
    if timestamp_ms is None:
        timestamp_ms = time.time_ns() // 1_000_000
    if not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms < (1 << 48):
        raise ValueError("UUIDv7 timestamp must fit in 48 unsigned bits")
    random_bits = secrets.randbits(74)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (((random_bits >> 62) & 0xFFF) << 64)
        | (0b10 << 62)
        | (random_bits & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))


def uuid7_timestamp_ms(value):
    """Validate canonical UUIDv7 text and return its Unix millisecond time."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value,
    ):
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7") from exc
    if str(parsed) != value or parsed.version != 7 or parsed.variant != uuid.RFC_4122:
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7")
    return parsed.int >> 80


def task_run_started_at(control):
    """Return the TASK run cutoff as canonical UTC milliseconds."""
    milliseconds = uuid7_timestamp_ms((control or {}).get("run_id"))
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _validate_task_control(data):
    if not isinstance(data, dict) or set(data) != TASK_CONTROL_FIELDS:
        return {}
    run_id = data.get("run_id")
    mode = data.get("execution_mode")
    close = data.get("close_receipt_fingerprint")
    try:
        uuid7_timestamp_ms(run_id)
    except ValueError:
        return {}
    lenses = _task_control_lenses(data.get("required_lenses"))
    if (
        mode not in {"standard", "micro"}
        or lenses is None
        or (close is not None and (
            not isinstance(close, str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", close)
        ))
    ):
        return {}
    return {
        "run_id": run_id,
        "execution_mode": mode,
        "required_lenses": lenses,
        "close_receipt_fingerprint": close,
    }


def _read_task_control_text(path):
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > 16 * 1024
        ):
            return ""
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        opened = os.fstat(fd)
        identity = (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode,
            opened.st_uid, opened.st_nlink, opened.st_mtime_ns, opened.st_ctime_ns,
        )
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            return ""
        chunks = []
        while True:
            chunk = os.read(fd, 4096)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(fd)
        if identity != (
            after.st_dev, after.st_ino, after.st_size, after.st_mode,
            after.st_uid, after.st_nlink, after.st_mtime_ns, after.st_ctime_ns,
        ):
            return ""
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeError):
        return ""
    finally:
        os.close(fd)


def read_task_control(task_dir):
    """Read the one exact, owner-controlled task authority or fail closed."""
    text = _read_task_control_text(task_control_file(task_dir))
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate TASK.json key: {key}")
            result[key] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=unique_object) if text else {}
    except (TypeError, ValueError):
        return {}
    return _validate_task_control(data)


def write_task_control(task_dir, control):
    """Atomically publish one exact TASK.json value."""
    validated = _validate_task_control(control)
    if not validated:
        raise ValueError("invalid exact TASK.json control value")
    path = task_control_file(task_dir)
    os.makedirs(task_dir, exist_ok=True)
    _revalidate_receipt_transaction(task_dir)
    if os.path.lexists(path) and not read_task_control(task_dir):
        raise RuntimeError("existing TASK.json is unsafe or invalid")
    snapshot = {path: _strict_regular_text_snapshot(path, max_size=16 * 1024)}
    try:
        _atomic_text_write(path, json.dumps(validated, indent=2, sort_keys=True) + "\n")
        _revalidate_receipt_transaction(task_dir)
    except BaseException:
        _restore_text_snapshots(snapshot)
        raise
    return True


def _new_task_control(*, execution_mode="standard"):
    return {
        "run_id": new_uuid7(),
        "execution_mode": execution_mode,
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }


def begin_task_run(task_dir):
    """Rotate TASK.json run identity and clear terminal authority."""
    path = task_control_file(task_dir)
    snapshot = {path: _strict_regular_text_snapshot(path, max_size=16 * 1024)}
    current = read_task_control(task_dir)
    if not current:
        raise RuntimeError("valid TASK.json required to rotate task run")
    payload = dict(current)
    payload.update({
        "run_id": new_uuid7(),
        "close_receipt_fingerprint": None,
    })
    write_task_control(task_dir, payload)
    return payload, snapshot


def restore_task_control(snapshot):
    _restore_text_snapshots(snapshot)


# ── Path resolution ──────────────────────────────────────────────────────


def find_repo_root(start_dir=None):
    """Find git repo root."""
    # Codex plugin-local hooks may execute from the installed plugin directory
    # while the hook payload still carries the project cwd. Prefer that payload
    # cwd so gates read the user's repo, not ~/.codex/harness/plugins/harness.
    d = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    while d != "/":
        git_path = os.path.join(d, ".git")
        if os.path.isdir(git_path) or os.path.isfile(git_path):
            return d
        d = os.path.dirname(d)
    return os.path.abspath(start_dir or os.getcwd())


class GitBindingError(RuntimeError):
    """Actionable, fail-closed error at an explicit Git trust boundary."""

    def __init__(self, code, message, *, path="", invariant="", next_action=""):
        self.code = code
        self.path = path
        self.invariant = invariant
        self.next_action = next_action
        super().__init__(f"[{code}] {message}")


def _trusted_git_env():
    """Return an environment without ambient Git repository/config overrides."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }


def _canonical_git_relpath(value):
    """Normalize an explicitly returned Git path without resolving it."""
    rel = str(value or "")
    if os.sep == "\\":
        rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _direct_gitlink_index_entries(repo_root, *, git_dir=None):
    """Return direct stage-0 gitlinks without traversing their worktrees."""
    try:
        command = ["git"]
        if git_dir:
            command.extend([f"--git-dir={git_dir}", f"--work-tree={repo_root}"])
        command.extend(["ls-files", "--stage", "-z"])
        result = subprocess.run(
            command,
            capture_output=True,
            cwd=repo_root,
            env=_trusted_git_env(),
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: direct gitlink index enumeration in {repo_root}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: direct gitlink index enumeration failed in {repo_root}"
        )
    found = {}
    records = result.stdout.split(b"\0") if isinstance(result.stdout, bytes) else str(result.stdout or "").split("\0")
    for record in records:
        if not record:
            continue
        tab = b"\t" if isinstance(record, bytes) else "\t"
        metadata, separator, raw_path = record.partition(tab)
        fields = metadata.split()
        if not separator or not fields or fields[0] not in (b"160000", "160000"):
            continue
        if len(fields) != 3 or fields[2] not in (b"0", "0"):
            raise RuntimeError("Git submodule snapshot unavailable")
        path = _canonical_git_relpath(
            os.fsdecode(raw_path) if isinstance(raw_path, bytes) else raw_path
        ).rstrip("/")
        oid = os.fsdecode(fields[1]) if isinstance(fields[1], bytes) else fields[1]
        if (
            not path
            or os.path.isabs(path)
            or path == ".."
            or path.startswith("../")
            or not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid)
        ):
            raise RuntimeError("Git submodule snapshot unavailable")
        found[path] = oid.lower()
    return found


def _registered_source_metadata_binding(control_root, source_root, relpath):
    """Resolve Git metadata for an explicitly trusted registered source root."""
    git_path = os.path.join(source_root, ".git")
    try:
        git_info = os.lstat(git_path)
    except OSError as exc:
        raise GitBindingError(
            "REGISTERED_SOURCE_UNINITIALIZED",
            f"registered source '{relpath}' is not initialized",
            path=relpath,
            invariant="initialized_checkout",
            next_action="Restore the checkout at the registered path, then retry.",
        ) from exc
    if stat.S_ISDIR(git_info.st_mode):
        return git_path
    if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISREG(git_info.st_mode):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has invalid .git metadata",
            path=relpath,
            invariant="gitfile_regular",
            next_action="Repair the checkout and retry.",
        )

    try:
        with open(git_path, "r", encoding="utf-8") as handle:
            line = handle.read(4097).strip()
    except (OSError, UnicodeError) as exc:
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has unreadable .git metadata",
            path=relpath,
            invariant="gitdir_pointer",
            next_action="Repair the checkout and retry.",
        ) from exc
    if not line.startswith("gitdir: ") or not line[len("gitdir: "):].strip():
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has malformed .git metadata",
            path=relpath,
            invariant="gitdir_pointer",
            next_action="Repair the Git worktree binding and retry.",
        )
    target = line[len("gitdir: "):].strip()
    target = os.path.abspath(target if os.path.isabs(target) else os.path.join(source_root, target))
    if not os.path.isdir(target):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' points to missing Git metadata",
            path=relpath,
            invariant="gitdir_path",
            next_action="Repair the checkout and retry.",
        )
    return target


def harness_root_resolution(start_dir=None):
    """Return ``(root, error)`` for valid/none/invalid Harness ancestry.

    A trusted ancestor manifest owns nested repositories only while that
    control root has an explicit active task marker. This lets a delegated
    agent finish from an ignored child repository without causing unrelated
    nested repositories to inherit an outer Harness installation.
    """
    start = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    current = start
    nearest_git = ""
    while True:
        manifest_path = os.path.join(current, MANIFEST_PATH)
        if os.path.lexists(manifest_path):
            try:
                manifest_info = None
                probe = current
                components = MANIFEST_PATH.split("/")
                for index, component in enumerate(components):
                    probe = os.path.join(probe, component)
                    info = os.lstat(probe)
                    if stat.S_ISLNK(info.st_mode):
                        if index == len(components) - 1:
                            return current, (
                                "Harness manifest must be a regular non-symlink file"
                            )
                        return current, (
                            "Harness manifest path components must not be symlinks"
                        )
                    manifest_info = info
            except OSError as exc:
                return current, f"Harness manifest is unreadable: {exc}"
            if (
                manifest_info is None
                or not stat.S_ISREG(manifest_info.st_mode)
            ):
                return current, "Harness manifest must be a regular non-symlink file"
            if nearest_git and nearest_git != current:
                try:
                    sid = current_session_id()
                    marker = _read_session_marker(_session_active_path(current, sid), sid)
                    bound_task = _live_active_task_dir(current, marker.get("task_dir"))
                    if not bound_task or marker.get("task_id") != os.path.basename(bound_task):
                        return "", ""
                except (OSError, RuntimeError, ValueError):
                    return "", ""
            try:
                if os.path.commonpath((current, start)) == current:
                    return current, ""
            except ValueError:
                return "", ""
        if not nearest_git and os.path.lexists(os.path.join(current, ".git")):
            nearest_git = current
        parent = os.path.dirname(current)
        if parent == current:
            return "", ""
        current = parent


def find_harness_root(start_dir=None):
    """Find a valid Harness control root; invalid ancestry is not valid."""
    root, error = harness_root_resolution(start_dir)
    return "" if error else root


def is_harness_enabled_repo(repo_root=None):
    """Return True when a repo has completed harness setup.

    Claude hooks may be installed globally and can run from arbitrary project
    directories. A git root alone is not enough permission to create
    ``doc/harness`` runtime files; setup creates ``doc/harness/manifest.yaml``.
    """
    root = repo_root or find_harness_root() or find_repo_root()
    return os.path.isfile(os.path.join(root, MANIFEST_PATH))


_TASK_ID_RE = re.compile(r"TASK__[A-Za-z0-9_.-]{1,180}\Z")


def _normalize_task_id(task_id=None, slug=None, task_dir=None):
    """Derive canonical TASK__<id> from arguments."""
    if task_id:
        value = str(task_id)
        tid = value if value.startswith("TASK__") else f"TASK__{value}"
        field = "task_id"
    if slug:
        value = str(slug)
        tid = value if value.startswith("TASK__") else f"TASK__{value}"
        field = "slug"
    if task_dir and not task_id and not slug:
        name = os.path.basename(os.path.normpath(task_dir))
        tid = name if name.startswith("TASK__") else f"TASK__{name}"
        field = "task_dir"
    if not (task_id or slug or task_dir):
        return None
    if not _TASK_ID_RE.fullmatch(tid):
        raise ValueError(
            f"invalid {field}; expected canonical TASK__<safe-id> using only "
            "letters, digits, dot, underscore, or hyphen"
        )
    return tid


def canonical_task_dir(task_id=None, slug=None, task_dir=None,
                       tasks_dir=TASK_DIR, repo_root=None):
    """Resolve a task selector to an immediate child of the repo task root."""
    repo_root = repo_root or find_repo_root()
    selectors = [
        _normalize_task_id(task_id=task_id) if task_id else None,
        _normalize_task_id(slug=slug) if slug else None,
        _normalize_task_id(task_dir=task_dir) if task_dir else None,
    ]
    selected = {item for item in selectors if item}
    if len(selected) > 1:
        raise ValueError(
            "task selectors disagree; task_id, slug, and task_dir must name the same canonical TASK__<safe-id>"
        )
    tid = next(iter(selected), None)
    if not tid:
        return ""
    tasks_root = _validated_control_dir(repo_root, tasks_dir, "canonical task root")
    canonical = os.path.join(tasks_root, tid)
    if task_dir:
        raw = str(task_dir)
        if any(ord(ch) < 32 for ch in raw):
            raise ValueError("invalid task_dir; control characters are not allowed")
        candidate = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(repo_root, raw))
        expected_form = canonical if os.path.isabs(raw) else os.path.relpath(canonical, repo_root)
        if raw != expected_form:
            raise ValueError(
                f"invalid task_dir; expected exact canonical path {os.path.relpath(canonical, repo_root)} "
                "or its absolute path without traversal or aliases"
            )
        if candidate != canonical or os.path.dirname(candidate) != tasks_root:
            raise ValueError(
                f"invalid task_dir; expected canonical {os.path.relpath(canonical, repo_root)} "
                "or its absolute path inside this repository"
            )
    expected_real = os.path.join(os.path.realpath(tasks_root), tid)
    if os.path.lexists(canonical):
        if os.path.islink(canonical) or not os.path.isdir(canonical):
            raise ValueError("invalid task selector; canonical task directory must be a real directory")
        if os.path.realpath(canonical) != expected_real:
            raise ValueError("invalid task selector; canonical task directory resolves outside this repository")
    return canonical


def canonical_task_id(task_id=None, slug=None, task_dir=None,
                      tasks_dir=TASK_DIR, repo_root=None):
    """Derive canonical task id string."""
    resolved = canonical_task_dir(
        task_id=task_id,
        slug=slug,
        task_dir=task_dir,
        tasks_dir=tasks_dir,
        repo_root=repo_root,
    )
    return os.path.basename(resolved) if resolved else ""


# ── Active task markers ─────────────────────────────────────────────────


ACTIVE_SESSIONS_DIRNAME = ".active_sessions"


def _legacy_active_path(repo_root):
    return os.path.join(repo_root, TASK_DIR, ".active")


def _active_sessions_dir(repo_root):
    return _validated_control_dir(
        repo_root,
        f"{TASK_DIR}/{ACTIVE_SESSIONS_DIRNAME}",
        "active session marker root",
    )


def _session_active_path(repo_root, session_id=None):
    sid = current_session_id() if session_id is None else sanitize_session_id(session_id)
    return os.path.join(_active_sessions_dir(repo_root), sid + ".json")


def write_active_marker(repo_root, task_dir, session_id=None):
    """Write the active task for the current session plus a legacy marker.

    The session marker is authoritative for hooks that receive session_id. The
    legacy ``.active`` file remains for older hooks/tests and single-session
    installs.
    """
    tasks_dir = os.path.join(repo_root, TASK_DIR)
    os.makedirs(tasks_dir, exist_ok=True)
    os.makedirs(_active_sessions_dir(repo_root), exist_ok=True)
    sid = current_session_id() if session_id is None else sanitize_session_id(session_id)
    task_run = read_task_control(task_dir)
    payload = {
        "session_id": sid,
        "task_dir": task_dir,
        "task_id": os.path.basename(os.path.normpath(task_dir)),
        "run_id": task_run.get("run_id", ""),
        "updated": now_iso(),
    }
    fd, tmp = tempfile.mkstemp(dir=_active_sessions_dir(repo_root), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, _session_active_path(repo_root, sid))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    _atomic_text_write(_legacy_active_path(repo_root), task_dir)


def active_task_binding_matches(repo_root, task_dir, control=None, session_id=None):
    """Require the current session marker to match the exact TASK.json generation."""
    control = control or read_task_control(task_dir)
    if not control or task_control_status(task_dir, control) != "open":
        return False
    sid = current_session_id() if session_id is None else sanitize_session_id(session_id)
    marker = _read_session_marker(_session_active_path(repo_root, sid), sid)
    return bool(
        marker
        and os.path.realpath(str(marker.get("task_dir") or "")) == os.path.realpath(task_dir)
        and marker.get("task_id") == os.path.basename(os.path.normpath(task_dir))
        and marker.get("run_id") == control.get("run_id")
    )


def active_marker_snapshot(repo_root, session_id=None):
    """Capture exact current-session and legacy marker contents for rollback."""
    paths = (
        _session_active_path(repo_root, session_id),
        _legacy_active_path(repo_root),
    )
    return {
        path: _strict_regular_text_snapshot(
            path, max_size=256 * 1024, allow_symlink=True,
        )
        for path in paths
    }


def restore_active_marker_snapshot(snapshot):
    """Restore an exact marker snapshot captured by active_marker_snapshot."""
    _restore_text_snapshots(snapshot)


def _read_regular_marker(path, *, max_size=256 * 1024):
    return _read_regular_text_file(path, max_size=max_size)


def _read_session_marker(path, expected_session_id):
    try:
        data = json.loads(_read_regular_marker(path))
    except (TypeError, ValueError):
        return {}
    if not isinstance(data, dict) or data.get("session_id") != expected_session_id:
        return {}
    return data


def _read_legacy_active(repo_root):
    path = _legacy_active_path(repo_root)
    first = (_read_regular_marker(path).strip().splitlines() or [""])[0]
    if not first:
        return ""
    if os.path.isabs(first):
        return first
    return os.path.join(repo_root, TASK_DIR, first.rstrip("/"))


def _live_active_task_dir(repo_root, value, *, require_live_state=True):
    if not isinstance(value, str) or not value:
        return ""
    try:
        td = canonical_task_dir(task_dir=value, repo_root=repo_root)
    except ValueError:
        return ""
    if not require_live_state:
        # The repository-wide marker is conservative for partially-created
        # packs; exact TASK.json validation still gates lifecycle authority.
        return td
    control = read_task_control(td)
    if not control or task_control_status(td, control) != "open":
        return ""
    return td


def resolve_active_task_dir(repo_root=None, session_id=None):
    """Resolve active task for this session, falling back to legacy ``.active``."""
    repo_root = repo_root or find_repo_root()

    try:
        sid = current_session_id() if session_id is None else sanitize_session_id(session_id)
        path = _session_active_path(repo_root, sid)
    except ValueError:
        path = ""
        sid = ""
    data = _read_session_marker(path, sid) if path else {}
    td = _live_active_task_dir(repo_root, data.get("task_dir"))
    if td and data.get("task_id") == os.path.basename(td):
        return td
    return _live_active_task_dir(
        repo_root, _read_legacy_active(repo_root), require_live_state=False
    )


def resolve_session_task_binding(repo_root, session_id):
    """Resolve only an exact session marker and its current open run."""
    if not isinstance(session_id, str) or not session_id or session_id == "default":
        return {}
    if sanitize_session_id(session_id) != session_id:
        return {}
    try:
        marker = _read_session_marker(
            _session_active_path(repo_root, session_id), session_id,
        )
    except ValueError:
        return {}
    task_dir = _live_active_task_dir(repo_root, marker.get("task_dir"))
    if not task_dir or marker.get("task_id") != os.path.basename(task_dir):
        return {}
    control = read_task_control(task_dir)
    if (
        not control
        or task_control_status(task_dir, control) != "open"
        or marker.get("run_id") != control.get("run_id")
    ):
        return {}
    return {"task_dir": task_dir, "run_id": control["run_id"]}


def iter_active_task_dirs(repo_root=None):
    """Yield unique active task dirs from session markers and legacy fallback."""
    repo_root = repo_root or find_repo_root()
    seen = set()
    try:
        sessions = _active_sessions_dir(repo_root)
    except ValueError:
        sessions = ""
    if os.path.isdir(sessions):
        for name in os.listdir(sessions):
            if not name.endswith(".json"):
                continue
            sid = name[:-5]
            if sanitize_session_id(sid) != sid:
                continue
            data = _read_session_marker(os.path.join(sessions, name), sid)
            td = _live_active_task_dir(repo_root, data.get("task_dir"))
            if td and data.get("task_id") == os.path.basename(td) and td not in seen:
                seen.add(td)
                yield td
    legacy = _live_active_task_dir(
        repo_root, _read_legacy_active(repo_root), require_live_state=False
    )
    if legacy and legacy not in seen:
        yield legacy


def clear_active_marker(repo_root, task_dir=None, session_id=None, *, strict=False):
    """Clear this session's active marker and matching legacy marker."""
    try:
        os.unlink(_session_active_path(repo_root, session_id))
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        if strict:
            raise
    legacy = _legacy_active_path(repo_root)
    try:
        if os.path.isfile(legacy):
            current = _read_legacy_active(repo_root)
            if task_dir is None or os.path.normpath(current) == os.path.normpath(task_dir):
                os.unlink(legacy)
    except OSError:
        if strict:
            raise
    if strict:
        try:
            sid = current_session_id() if session_id is None else sanitize_session_id(session_id)
            session_data = _read_session_marker(_session_active_path(repo_root, sid), sid)
        except ValueError:
            session_data = {}
        if session_data and (
            task_dir is None
            or os.path.normpath(str(session_data.get("task_dir") or ""))
            == os.path.normpath(task_dir)
        ):
            raise RuntimeError("active session marker cleanup unavailable")
        legacy_target = _read_legacy_active(repo_root)
        if legacy_target and (
            task_dir is None
            or os.path.normpath(legacy_target) == os.path.normpath(task_dir)
        ):
            raise RuntimeError("legacy active marker cleanup unavailable")


# ── Scaffold ─────────────────────────────────────────────────────────────


def ensure_task_scaffold(
    task_dir, task_id, request_text="", repo_root=None, execution_mode="standard",
):
    """Create a new exact TASK.json; existing valid controls are resumed."""
    os.makedirs(task_dir, exist_ok=True)
    expected_tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    path = task_control_file(task_dir)
    if os.path.lexists(path):
        if not read_task_control(task_dir):
            raise ValueError("existing TASK.json must be an exact safe task control")
        created = [path]
        return {"created": created, "task_dir": task_dir, "task_id": expected_tid}
    created = []
    try:
        write_task_control(task_dir, _new_task_control(execution_mode=execution_mode))
        created.append(path)
        if request_text:
            req_path = os.path.join(task_dir, "REQUEST.md")
            if not os.path.isfile(req_path) or os.path.islink(req_path):
                _atomic_text_write(req_path, request_text)
                created.append(req_path)
    except Exception:
        for artifact in created:
            try:
                os.unlink(artifact)
            except FileNotFoundError:
                pass
        raise
    return {"created": created, "task_dir": task_dir, "task_id": expected_tid}


# ── Manifest ─────────────────────────────────────────────────────────────


def read_manifest_field(field, repo_root=None):
    repo_root = repo_root or find_harness_root() or find_repo_root()
    return yaml_field(field, os.path.join(repo_root, MANIFEST_PATH))


def is_maintenance_task(task_dir, repo_root=None):
    if os.path.isfile(os.path.join(task_dir, "MAINTENANCE")):
        return True
    return str(read_manifest_field("maintenance_default", repo_root) or "").lower() == "true"


# ── Routing (on-the-fly, never stored) ───────────────────────────────────


def compile_routing(task_dir, repo_root=None):
    repo_root = repo_root or find_repo_root()
    maintenance = is_maintenance_task(task_dir, repo_root)
    control = read_task_control(task_dir)
    micro_loop = _is_micro_loop_state(control)
    return {
        "maintenance_task": maintenance,
        "workflow_locked": not maintenance,
        "risk_level": "high" if maintenance else "medium",
        "execution_mode": "micro" if micro_loop else "standard",
        "orchestration_mode": "solo",
        "planning_mode": "skipped" if micro_loop else "standard",
    }


def _is_micro_loop_state(control):
    return (control or {}).get("execution_mode") == "micro"


def _attempts_dir(task_dir):
    return os.path.join(task_dir, "attempts")


def list_attempts(task_dir):
    """Return compact metadata for recorded retry attempts."""
    root = _attempts_dir(task_dir)
    if not os.path.isdir(root):
        return []
    attempts = []
    for name in sorted(os.listdir(root)):
        if not re.match(r"^attempt-\d{3}$", name):
            continue
        meta_path = os.path.join(root, name, "attempt.json")
        try:
            with open(meta_path, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            meta = {"id": name}
        if isinstance(meta, dict):
            meta.setdefault("id", name)
            attempts.append(meta)
    return attempts


def _atomic_json_write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _receipts_path(task_dir):
    return os.path.join(task_dir, RECEIPTS_NAME)


_RECEIPT_STREAM_MAX_BYTES = 16 * 1024 * 1024
_RECEIPT_LOCK_HELD = ContextVar("harness_receipt_lock_held", default=())


def _validated_receipt_task_dir(task_dir):
    task_dir = os.path.abspath(os.fspath(task_dir))
    current = task_dir
    for _ in range(4):
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("receipt storage integrity unavailable")
        current = os.path.dirname(current)
    task_info = os.lstat(task_dir)
    if task_info.st_uid != os.getuid() or stat.S_IMODE(task_info.st_mode) & 0o022:
        raise RuntimeError("receipt storage integrity unavailable")
    return task_dir


def _receipt_lock_binding(task_dir):
    task_dir = os.path.abspath(os.fspath(task_dir))
    for binding in reversed(_RECEIPT_LOCK_HELD.get()):
        if binding[0] == task_dir:
            return binding
    return None


def _validate_receipt_dir_binding(binding):
    task_dir, fd, identity, components = binding
    for path, component_fd, component_identity in components:
        try:
            opened = os.fstat(component_fd)
            current = os.lstat(path)
        except OSError as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != component_identity
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != component_identity
        ):
            raise RuntimeError("receipt storage integrity unavailable")
    task_opened = os.fstat(fd)
    if (
        (task_opened.st_dev, task_opened.st_ino) != identity
        or task_opened.st_uid != os.getuid()
        or stat.S_IMODE(task_opened.st_mode) & 0o022
    ):
        raise RuntimeError("receipt storage integrity unavailable")


def _revalidate_receipt_transaction(task_dir):
    """Fail if a task directory changes during an enclosing receipt transaction."""
    binding = _receipt_lock_binding(task_dir)
    if binding is not None:
        _validate_receipt_dir_binding(binding)


def _receipt_dir_fd(task_dir):
    binding = _receipt_lock_binding(task_dir)
    if binding is None:
        raise RuntimeError("receipt transaction required")
    _validate_receipt_dir_binding(binding)
    return binding[1]


@contextmanager
def _receipt_stream_lock(task_dir):
    task_dir = _validated_receipt_task_dir(task_dir)
    nested = _receipt_lock_binding(task_dir)
    if nested is not None:
        _validate_receipt_dir_binding(nested)
        yield
        _validate_receipt_dir_binding(nested)
        return
    component_paths = []
    current = task_dir
    for _ in range(4):
        component_paths.append(current)
        current = os.path.dirname(current)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    components = []
    try:
        for path in reversed(component_paths):
            component_fd = os.open(path, flags)
            component_info = os.fstat(component_fd)
            current_info = os.lstat(path)
            if (
                not stat.S_ISDIR(component_info.st_mode)
                or stat.S_ISLNK(current_info.st_mode)
                or not stat.S_ISDIR(current_info.st_mode)
                or (component_info.st_dev, component_info.st_ino)
                != (current_info.st_dev, current_info.st_ino)
            ):
                os.close(component_fd)
                raise RuntimeError("receipt storage integrity unavailable")
            components.append((
                path, component_fd, (component_info.st_dev, component_info.st_ino),
            ))
    except (OSError, RuntimeError) as exc:
        for _, component_fd, _ in components:
            os.close(component_fd)
        if isinstance(exc, RuntimeError):
            raise
        raise RuntimeError("receipt storage integrity unavailable") from exc
    fd = components[-1][1]
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RuntimeError("receipt storage integrity unavailable")
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        binding = (
            task_dir, fd, (info.st_dev, info.st_ino), tuple(components),
        )
        _validate_receipt_dir_binding(binding)
        token = _RECEIPT_LOCK_HELD.set(_RECEIPT_LOCK_HELD.get() + (binding,))
        try:
            yield
            _validate_receipt_dir_binding(binding)
        finally:
            _RECEIPT_LOCK_HELD.reset(token)
    finally:
        for _, component_fd, _ in reversed(components):
            os.close(component_fd)


def _receipt_stream_info(path):
    task_dir = os.path.dirname(os.path.abspath(path))
    dir_fd = _receipt_dir_fd(task_dir)
    try:
        info = os.stat(RECEIPTS_NAME, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_mode & 0o022
        or info.st_size > _RECEIPT_STREAM_MAX_BYTES
    ):
        raise RuntimeError("receipt storage integrity unavailable")
    return info


@contextmanager
def receipt_stream_transaction(task_dir):
    """Hold the receipt stream stable across verdict and state publication."""
    with _receipt_stream_lock(task_dir):
        yield


RECEIPT_FIELDS = frozenset({
    "ts", "event", "source", "task_run_id", "runtime_id", "agent_id",
    "agent_type", "lens", "verdict", "summary",
})
RECEIPT_EVENTS = frozenset({"started", "completed"})
_RECEIPT_RUNTIME_ID_RE = re.compile(
    r"^(?P<namespace>[a-z][a-z0-9_-]*):"
    r"(?P<components>[A-Za-z0-9._/@+-]+(?::[A-Za-z0-9._/@+-]+)*)$"
)


def parse_receipt_runtime_id(value):
    """Return a namespaced runtime identity as components, or reject it."""
    runtime_id = str(value or "").strip()
    if not runtime_id:
        return ()
    if len(runtime_id) > 500:
        raise ValueError("runtime_id must not exceed 500 characters")
    match = _RECEIPT_RUNTIME_ID_RE.fullmatch(runtime_id)
    if not match:
        raise ValueError("runtime_id must be a namespaced colon-separated identity")
    return (match.group("namespace"), *match.group("components").split(":"))


def _validate_receipt_runtime_id(source, runtime_id):
    parsed = parse_receipt_runtime_id(runtime_id)
    normalized_source = str(source or "").lower()
    if not parsed:
        raise ValueError("receipt requires a namespaced runtime_id")
    if normalized_source == "codex_session_watcher:collaboration" and (
        len(parsed) != 4 or parsed[0] != "codex"
    ):
        raise ValueError(
            "Codex receipt runtime_id namespace/shape must be codex:<root>:<event>:<child>"
        )
    if normalized_source == "claude_hook" and (
        len(parsed) != 3 or parsed[0] != "claude"
    ):
        raise ValueError(
            "Claude receipt runtime_id namespace/shape must be claude:<session>:<agent>"
        )
    return parsed


_RECEIPT_DIGEST_RE = re.compile(r"^DETAIL_SHA256:[0-9a-f]{64}$")


def _receipt_entry_semantics_valid(item):
    """Validate the exact persisted receipt contract, not only its key set."""
    try:
        uuid7_timestamp_ms(item["task_run_id"])
        _validate_receipt_runtime_id(item["source"], item["runtime_id"])
    except (TypeError, ValueError):
        return False
    if (
        not item["ts"] or not item["source"] or not item["agent_id"] or not item["agent_type"]
        or item["lens"] not in SUPPORTED_LENSES
    ):
        return False
    if item["event"] == "started":
        return item["verdict"] == "" and item["summary"] == ""
    if item["verdict"] not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING"}:
        return False
    lines = item["summary"].splitlines()
    expected_lines = 3 if item["lens"].startswith("review-") else 2
    if len(lines) != expected_lines or lines[0] != f"VERDICT: {item['verdict']}":
        return False
    if item["lens"].startswith("review-") and not (
        lines[1] == "FINDING_COUNTS: INVALID" or _FINDING_COUNTS_RE.fullmatch(lines[1])
    ):
        return False
    if item["lens"].startswith("review-") and lines[1] != "FINDING_COUNTS: INVALID":
        counts = _FINDING_COUNTS_RE.fullmatch(lines[1])
        fix_now, investigate, _optional = (int(value) for value in counts.groups())
        if item["verdict"] == "PASS" and (fix_now or investigate):
            return False
        if item["verdict"] == "FAIL" and not fix_now:
            return False
        if item["verdict"] == "BLOCKED_ENV" and not investigate:
            return False
    return bool(_RECEIPT_DIGEST_RE.fullmatch(lines[-1]))


@dataclass(frozen=True)
class ReceiptSnapshot:
    """One immutable view and fingerprint of the current receipt stream."""

    entries: tuple
    fingerprint: str

    @property
    def reviews(self):
        return tuple(
            item for item in self.entries
            if str(item.get("lens") or "").startswith("review-")
        )

    @property
    def subagents(self):
        return tuple(
            item for item in self.entries
            if not str(item.get("lens") or "").startswith("review-")
        )


def _read_receipt_bytes_unlocked(path):
    task_dir = os.path.dirname(os.path.abspath(path))
    dir_fd = _receipt_dir_fd(task_dir)
    prior = _receipt_stream_info(path)
    if prior is None:
        return None
    flags = os.O_RDONLY
    flags |= (
        getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(RECEIPTS_NAME, flags, dir_fd=dir_fd)
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or opened.st_mode & 0o022
            or opened.st_size > _RECEIPT_STREAM_MAX_BYTES
            or (opened.st_dev, opened.st_ino) != (prior.st_dev, prior.st_ino)
        ):
            raise RuntimeError("receipt storage integrity unavailable")
        with os.fdopen(fd, "rb") as handle:
            fd = -1
            raw = handle.read(_RECEIPT_STREAM_MAX_BYTES + 1)
            final = os.fstat(handle.fileno())
        final_path = os.stat(RECEIPTS_NAME, dir_fd=dir_fd, follow_symlinks=False)
        if (
            len(raw) > _RECEIPT_STREAM_MAX_BYTES
            or (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino)
            or final.st_size != opened.st_size
            or final.st_mtime_ns != opened.st_mtime_ns
            or final.st_ctime_ns != opened.st_ctime_ns
            or final.st_uid != os.getuid()
            or final.st_nlink != 1
            or final.st_mode & 0o022
            or not stat.S_ISREG(final_path.st_mode)
            or final_path.st_uid != os.getuid()
            or final_path.st_nlink != 1
            or final_path.st_mode & 0o022
            or (final_path.st_dev, final_path.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeError("receipt storage integrity unavailable")
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    return raw


def _make_receipt_rollback_api():
    class ResetCapability:
        __slots__ = ()

    pending = {}

    def restore_unlocked(task_dir, raw):
        """Restore only bytes captured by this closure from the same task leaf."""
        dir_fd = _receipt_dir_fd(task_dir)
        if raw is None:
            _receipt_stream_info(_receipts_path(task_dir))
            try:
                os.unlink(RECEIPTS_NAME, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
            _revalidate_receipt_transaction(task_dir)
            return
        if not isinstance(raw, bytes) or len(raw) > _RECEIPT_STREAM_MAX_BYTES:
            raise RuntimeError("receipt storage integrity unavailable")
        _receipt_stream_info(_receipts_path(task_dir))
        temp_name = f".{RECEIPTS_NAME}.{secrets.token_hex(8)}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
        try:
            view = memoryview(raw)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise RuntimeError("receipt storage integrity unavailable")
                view = view[written:]
            os.fsync(fd)
            os.fchmod(fd, 0o644)
            os.replace(temp_name, RECEIPTS_NAME, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
        finally:
            os.close(fd)
            try:
                os.unlink(temp_name, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        _revalidate_receipt_transaction(task_dir)

    @contextmanager
    def savepoint(task_dir):
        with _receipt_stream_lock(task_dir):
            raw = _read_receipt_bytes_unlocked(_receipts_path(task_dir))
            try:
                yield
            except BaseException:
                restore_unlocked(task_dir, raw)
                raise

    def reset(task_dir):
        task_dir = _validated_receipt_task_dir(task_dir)
        with _receipt_stream_lock(task_dir):
            raw = _read_receipt_bytes_unlocked(_receipts_path(task_dir))
            capability = ResetCapability()
            pending[id(capability)] = (capability, task_dir, raw)
            try:
                os.unlink(RECEIPTS_NAME, dir_fd=_receipt_dir_fd(task_dir))
            except FileNotFoundError:
                pass
            except BaseException:
                pending.pop(id(capability), None)
                restore_unlocked(task_dir, raw)
                raise
            _revalidate_receipt_transaction(task_dir)
        return capability

    def restore(capability):
        saved = pending.pop(id(capability), None)
        if saved is None or saved[0] is not capability:
            raise PermissionError("receipt reset restoration requires its opaque capability")
        _, task_dir, raw = saved
        with _receipt_stream_lock(task_dir):
            restore_unlocked(task_dir, raw)

    def release(capability):
        saved = pending.pop(id(capability), None)
        if saved is None or saved[0] is not capability:
            raise PermissionError("receipt reset release requires its opaque capability")

    return savepoint, reset, restore, release


(
    receipt_stream_savepoint, reset_receipt_streams_for_new_run,
    restore_receipt_streams, release_receipt_stream_reset,
) = (
    _make_receipt_rollback_api()
)
del _make_receipt_rollback_api


def _receipt_snapshot_unlocked(task_dir):
    path = _receipts_path(task_dir)
    raw = _read_receipt_bytes_unlocked(path)
    h = hashlib.sha256()
    h.update(RECEIPTS_NAME.encode("utf-8"))
    h.update(b"\0")
    if raw is None:
        h.update(b"<missing>\0")
        text = ""
    else:
        h.update(raw)
        h.update(b"\0")
        try:
            text = raw.decode("utf-8")
        except UnicodeError as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
    entries = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        if not isinstance(item, dict):
            raise RuntimeError("receipt storage integrity unavailable")
        if (
            set(item) != RECEIPT_FIELDS
            or any(not isinstance(value, str) for value in item.values())
            or item.get("event") not in RECEIPT_EVENTS
        ):
            raise RuntimeError(
                "unsupported RECEIPTS.jsonl schema; start a fresh task run to reset receipts"
            )
        if not _receipt_entry_semantics_valid(item):
            raise RuntimeError(
                "unsupported RECEIPTS.jsonl schema; start a fresh task run to reset receipts"
            )
        entries.append(MappingProxyType(item))
    return ReceiptSnapshot(tuple(entries), "sha256:" + h.hexdigest())


def receipt_snapshot(task_dir):
    with _receipt_stream_lock(task_dir):
        return _receipt_snapshot_unlocked(task_dir)


def _receipt_short(value, limit=2000):
    text = str(value or "").strip()
    return text[:limit]


def _receipt_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


_QA_VERDICT_RE = re.compile(r"^VERDICT: (PASS|FAIL|BLOCKED_ENV)$")
_FINDING_COUNTS_RE = re.compile(
    r"^FINDING_COUNTS: FIX_NOW=(\d+) INVESTIGATE=(\d+) OPTIONAL=(\d+)$"
)


def extract_qa_verdict(value):
    """Accept only the exact, unique first-line verdict contract."""
    lines = str(value or "").splitlines()
    if not lines:
        return ""
    matches = [_QA_VERDICT_RE.fullmatch(line.strip()) for line in lines]
    verdicts = [match.group(1) for match in matches if match]
    return verdicts[0] if matches[0] and len(verdicts) == 1 else ""


def normalize_receipt_completion(lens, value, supplied_verdict=""):
    """Validate a completion and retain only verdict/counts plus a detail digest."""
    raw_summary = str(value or "")
    verdict = _receipt_short(supplied_verdict, 40).upper()
    if verdict and verdict not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING", "UNKNOWN"}:
        verdict = "UNKNOWN"
    summary_verdict = extract_qa_verdict(raw_summary)
    if not summary_verdict or (verdict and verdict != summary_verdict):
        verdict = "PENDING"
    else:
        verdict = summary_verdict

    is_review = str(lens or "").startswith("review-")
    summary_lines = raw_summary.splitlines()
    counts_match = _FINDING_COUNTS_RE.fullmatch(summary_lines[1]) if len(summary_lines) > 1 else None
    counts_reported = bool(counts_match) and sum(
        "FINDING_COUNTS:" in line for line in summary_lines
    ) == 1
    if is_review:
        if not counts_reported:
            verdict = "PENDING"
        else:
            fix_now, investigate, _optional = (int(value) for value in counts_match.groups())
            if verdict == "PASS" and (fix_now or investigate):
                verdict = "PENDING"
            if verdict == "FAIL" and not fix_now:
                verdict = "PENDING"
            if verdict == "BLOCKED_ENV" and not investigate:
                verdict = "PENDING"

    compact = [f"VERDICT: {verdict}"]
    if is_review:
        compact.append(counts_match.group(0) if counts_reported else "FINDING_COUNTS: INVALID")
    compact.append("DETAIL_SHA256:" + hashlib.sha256(raw_summary.encode("utf-8")).hexdigest())
    return verdict, "\n".join(compact)


def _declared_lenses(task_dir, prefix, *, control=None):
    value = (control or read_task_control(task_dir)).get("required_lenses")
    if not isinstance(value, list):
        return []
    return [lens for lens in value if lens.startswith(prefix)]


def required_review_lenses(task_dir, state=None):
    """Return task-declared review lenses without inspecting source state."""
    return _declared_lenses(task_dir, "review-", control=state)


def _receipt_stream_fingerprint_unlocked(task_dir):
    return _receipt_snapshot_unlocked(task_dir).fingerprint


def receipt_stream_fingerprint(task_dir, snapshot=None):
    return (snapshot or receipt_snapshot(task_dir)).fingerprint


def _blocked_artifact_valid(task_dir):
    path = os.path.join(task_dir, "BLOCKED.md")
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and info.st_nlink == 1
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def task_control_status(task_dir, control=None, snapshot=None):
    """Derive open/blocked/closed; malformed terminal evidence is invalid."""
    control = control or read_task_control(task_dir)
    if not control:
        return "invalid"
    blocked_path = os.path.join(task_dir, "BLOCKED.md")
    if os.path.lexists(blocked_path):
        if not _blocked_artifact_valid(task_dir):
            return "invalid"
        return "invalid" if control.get("close_receipt_fingerprint") else "blocked"
    expected = control.get("close_receipt_fingerprint")
    if expected:
        try:
            return "closed" if receipt_stream_fingerprint(task_dir, snapshot) == expected else "invalid"
        except RuntimeError:
            return "invalid"
    return "open"


def publish_task_close(task_dir, control, *, receipt_fingerprint):
    """Atomically publish current receipt bytes as the sole close authority."""
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", str(receipt_fingerprint or "")):
        raise ValueError("invalid task close receipt fingerprint")
    updated = dict(control)
    updated["close_receipt_fingerprint"] = receipt_fingerprint
    write_task_control(task_dir, updated)
    return updated


def _infer_receipt_lens(agent_type, explicit_lens=""):
    lens = _receipt_short(explicit_lens, 80).lower()
    if lens:
        return lens
    kind = _receipt_short(agent_type, 300).lower()
    match = re.search(r"(?:^|[:/_-])(qa|ux)[-_:](cli|api|browser|desktop)(?:$|[:/_-])", kind)
    if match:
        return f"{match.group(1)}-{match.group(2)}"
    match = re.search(r"(?:^|[:/_-])(cli|api|browser|desktop)(?:$|[:/_-])", kind)
    if match and ("qa" in kind or "ux" in kind):
        prefix = "ux" if "ux" in kind else "qa"
        return f"{prefix}-{match.group(1)}"
    if re.search(r"(?:^|[:/_-])(?:code[-_ ]?reviewer|code[-_ ]?review)(?:$|[:/_-])", kind):
        return "review-code"
    if re.search(r"(?:^|[:/_-])(?:security[-_ ]?reviewer|security[-_ ]?review)(?:$|[:/_-])", kind):
        return "review-security"
    return ""


def _make_runtime_receipt_writer():
    allowed_names = {
        "claude_hook": {
            ("subagent_lifecycle", "register_subagent_start"),
            ("subagent_lifecycle", "mark_subagent_stop"),
        },
        "codex_session_watcher:collaboration": {
            ("codex_lifecycle_watcher", "Watcher._invalidate"),
            ("codex_lifecycle_watcher", "Watcher._maybe_start"),
            ("codex_lifecycle_watcher", "Watcher._maybe_complete"),
        },
    }
    bound_callers = {}

    def bind(source, function):
        identity = (function.__module__, function.__qualname__)
        if identity not in allowed_names.get(source, ()):
            raise PermissionError("unsupported receipt lifecycle adapter")
        existing = bound_callers.get((source, identity))
        dependencies = tuple(
            (name, function.__globals__[name])
            for name in function.__code__.co_names
            if name in function.__globals__
        )
        binding = (function.__code__, function.__globals__, dependencies)
        if existing is not None and existing != binding:
            raise PermissionError("receipt lifecycle adapter is already bound")
        bound_callers[(source, identity)] = binding

    def authorized(source):
        frame = inspect.currentframe()
        caller = frame.f_back.f_back if frame and frame.f_back else None
        try:
            if caller is None:
                return False
            identity = (str(caller.f_globals.get("__name__") or ""), caller.f_code.co_qualname)
            binding = bound_callers.get((source, identity))
            return bool(
                binding is not None
                and caller.f_code is binding[0]
                and caller.f_globals is binding[1]
                and all(caller.f_globals.get(name) is value for name, value in binding[2])
            )
        finally:
            del caller
            del frame

    def record(task_dir, receipt):
        """Validate and append one runtime-owned structured lifecycle receipt."""
        if not isinstance(receipt, dict):
            raise ValueError("receipt must be an object")
        agent_id = _receipt_short(receipt.get("agent_id") or receipt.get("id"), 300)
        if not agent_id:
            raise ValueError("agent_id required")
        source = _receipt_short(receipt.get("source") or "spawn_agent", 100)
        if not authorized(source):
            raise PermissionError("receipt append requires a runtime-owned lifecycle adapter")
        agent_type = _receipt_short(receipt.get("agent_type"), 300)
        verdict = _receipt_short(receipt.get("verdict") or "", 40).upper()
        lens = _infer_receipt_lens(agent_type, receipt.get("lens"))
        event = _receipt_short(receipt.get("event"), 20).lower()
        if event not in RECEIPT_EVENTS:
            raise ValueError("event must be started or completed")
        now = _receipt_now_iso()
        raw_summary = str(receipt.get("summary") or "")
        if event != "completed":
            verdict = ""
            summary = ""
        else:
            verdict, summary = normalize_receipt_completion(lens, raw_summary, verdict)
        current_run = read_task_control(task_dir)
        if not current_run:
            raise RuntimeError("valid TASK.json required for receipt append")
        supplied_run_id = _receipt_short(receipt.get("task_run_id"), 64)
        task_run_id = str(current_run["run_id"])
        if supplied_run_id and supplied_run_id != task_run_id:
            raise RuntimeError("receipt task_run_id does not match current task run")
        runtime_id = str(receipt.get("runtime_id") or "").strip()
        _validate_receipt_runtime_id(source, runtime_id)
        entry = {
            "ts": now, "event": event, "source": source,
            "task_run_id": task_run_id, "runtime_id": runtime_id,
            "agent_id": agent_id, "agent_type": agent_type, "lens": lens,
            "verdict": verdict, "summary": summary,
        }
        if not _receipt_entry_semantics_valid(entry):
            raise ValueError("receipt does not satisfy the exact persisted schema")
        path = _receipts_path(task_dir)
        _validated_receipt_task_dir(task_dir)
        payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with receipt_stream_transaction(task_dir):
            control = read_task_control(task_dir)
            if task_control_status(task_dir, control) in {"closed", "blocked", "invalid"}:
                raise RuntimeError("receipt stream is terminal")
            dir_fd = _receipt_dir_fd(task_dir)
            prior = _receipt_stream_info(path)
            flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
            flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
            try:
                fd = os.open(RECEIPTS_NAME, flags, 0o644, dir_fd=dir_fd)
            except OSError as exc:
                raise RuntimeError("receipt storage integrity unavailable") from exc
            try:
                opened = os.fstat(fd)
                if (
                    not stat.S_ISREG(opened.st_mode)
                    or opened.st_uid != os.getuid()
                    or opened.st_nlink != 1
                    or opened.st_mode & 0o022
                    or opened.st_size + len(payload) > _RECEIPT_STREAM_MAX_BYTES
                    or (prior is not None and (opened.st_dev, opened.st_ino) != (prior.st_dev, prior.st_ino))
                ):
                    raise RuntimeError("receipt storage integrity unavailable")
                view = memoryview(payload)
                while view:
                    written = os.write(fd, view)
                    if written <= 0:
                        raise RuntimeError("receipt storage integrity unavailable")
                    view = view[written:]
                os.fsync(fd)
                _revalidate_receipt_transaction(task_dir)
            finally:
                os.close(fd)
        return entry

    return record, bind


record_subagent_receipt, _bind_runtime_receipt_adapter = _make_runtime_receipt_writer()
del _make_runtime_receipt_writer


def _completed_review_by_lens(task_dir, snapshot=None):
    snapshot = snapshot or receipt_snapshot(task_dir)
    receipts = snapshot.entries
    current_run_id = str(read_task_control(task_dir).get("run_id") or "")
    if not current_run_id:
        return {}
    latest_events = {}
    for item in receipts:
        lens = str(item.get("lens") or "").lower()
        if lens.startswith("review-") and item.get("task_run_id") == current_run_id:
            latest_events[lens] = item
    completed = {}
    for lens, item in latest_events.items():
        if item.get("event") != "completed":
            continue
        if str(item.get("verdict") or "").upper() not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING"}:
            continue
        if sum(
            prior.get("event") == "completed"
            and _receipt_runtime_identity_matches(prior, item)
            for prior in receipts
        ) != 1:
            continue
        completion_index = receipts.index(item)
        matching_starts = [
            prior for prior in receipts[:completion_index]
            if prior is not item
            and prior.get("event") == "started"
            and _receipt_runtime_identity_matches(prior, item)
        ]
        if not matching_starts:
            continue
        completed[lens] = item
    return completed


def _receipt_runtime_identity_matches(start, completion):
    """Require exact lifecycle correlation across every stable identity."""
    keys = (
        "source", "task_run_id", "runtime_id", "agent_id", "agent_type", "lens",
    )
    return all(str(start.get(key) or "") == str(completion.get(key) or "") for key in keys)


def _latest_review_pass_index(task_dir, state=None, snapshot=None):
    st = state or read_task_control(task_dir)
    snapshot = snapshot or receipt_snapshot(task_dir)
    if receipt_review_verdict(task_dir, st, snapshot) != "PASS":
        return -1
    completed = _completed_review_by_lens(task_dir, snapshot)
    return max(
        (snapshot.entries.index(completed[lens]) for lens in required_review_lenses(task_dir, st)),
        default=-1,
    )


def _qa_started_after_review(snapshot, lens, completion, review_index):
    agent_id = completion.get("agent_id")
    receipts = snapshot.entries
    try:
        completion_index = receipts.index(completion)
    except ValueError:
        return False
    return any(
        item.get("lens") == lens
        and item.get("agent_id") == agent_id
        and item.get("event") == "started"
        and _receipt_runtime_identity_matches(item, completion)
        and index > review_index
        for index, item in enumerate(receipts[:completion_index])
    )


def receipt_review_verdict(task_dir, state=None, snapshot=None):
    st = state or read_task_control(task_dir)
    if not _validate_task_control(st):
        return "PENDING"
    required = required_review_lenses(task_dir, st)
    if not required:
        return "NOT_APPLICABLE"
    snapshot = snapshot or receipt_snapshot(task_dir)
    completed = _completed_review_by_lens(task_dir, snapshot)
    verdicts = []
    for lens in required:
        item = completed.get(lens)
        if not item:
            return "PENDING"
        verdicts.append(str(item.get("verdict") or "").upper())
    if any(verdict == "FAIL" for verdict in verdicts):
        return "FAIL"
    if any(verdict == "BLOCKED_ENV" for verdict in verdicts):
        return "BLOCKED_ENV"
    return "PASS" if all(verdict == "PASS" for verdict in verdicts) else "PENDING"


def _required_qa_lenses(task_dir, state=None):
    """Return plan-declared QA lenses without inspecting changed paths."""
    return _declared_lenses(task_dir, "qa-", control=state)


def _completed_qa_by_lens(task_dir, snapshot=None):
    snapshot = snapshot or receipt_snapshot(task_dir)
    current_run_id = str(read_task_control(task_dir).get("run_id") or "")
    if not current_run_id:
        return {}
    latest_events = {}
    for item in snapshot.subagents:
        lens = str(item.get("lens") or "").lower()
        if not lens.startswith("qa-") or item.get("task_run_id") != current_run_id:
            continue
        latest_events[lens] = item
    latest = {}
    for lens, item in latest_events.items():
        verdict = str(item.get("verdict") or "").upper()
        if item.get("event") != "completed":
            continue
        if verdict not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING"}:
            continue
        if sum(
            prior.get("event") == "completed"
            and _receipt_runtime_identity_matches(prior, item)
            for prior in snapshot.subagents
        ) != 1:
            continue
        latest[lens] = item
    return latest


def receipt_runtime_verdict(task_dir, state=None, snapshot=None):
    """Compute runtime verdict from completed, explicit QA receipts only."""
    st = state or read_task_control(task_dir)
    if _blocked_artifact_valid(task_dir):
        return "BLOCKED_ENV"
    snapshot = snapshot or receipt_snapshot(task_dir)
    review_verdict = receipt_review_verdict(task_dir, st, snapshot)
    if review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        return "PENDING"
    required = _required_qa_lenses(task_dir, st)
    completed = _completed_qa_by_lens(task_dir, snapshot)
    review_index = _latest_review_pass_index(task_dir, st, snapshot)
    valid = {
        lens: completed[lens] for lens in required
        if (
            lens in completed
            and _qa_started_after_review(snapshot, lens, completed[lens], review_index)
        )
    }
    verdicts = [str(valid[lens].get("verdict") or "").upper() for lens in required if lens in valid]
    if any(verdict == "FAIL" for verdict in verdicts):
        return "FAIL"
    if any(verdict == "BLOCKED_ENV" for verdict in verdicts):
        return "BLOCKED_ENV"
    if len(verdicts) == len(required) and all(verdict == "PASS" for verdict in verdicts):
        return "PASS"
    return "PENDING"


# ── Task context ─────────────────────────────────────────────────────────


def emit_compact_context(task_dir, snapshot=None):
    """Build the canonical task pack with on-the-fly routing."""
    st = read_task_control(task_dir)
    if not st:
        return {
            "error": "missing or invalid exact four-field TASK.json",
            "task_dir": task_dir,
            "next_action": "Call task_start to initialize a fresh TASK.json run.",
        }

    snapshot = snapshot or receipt_snapshot(task_dir)
    routing = compile_routing(task_dir)
    runtime_verdict = receipt_runtime_verdict(task_dir, st, snapshot)

    micro_loop = _is_micro_loop_state(st)
    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_write_allowed = has_plan or micro_loop
    why_blocked = "" if source_write_allowed else "PLAN.md does not exist yet"

    missing_for_close = []
    if not has_plan and not micro_loop:
        missing_for_close.append("PLAN.md")
    required_reviews = required_review_lenses(task_dir, st)
    review_verdict = receipt_review_verdict(task_dir, st, snapshot)
    completed_reviews = _completed_review_by_lens(task_dir, snapshot)
    missing_reviews = [lens for lens in required_reviews if lens not in completed_reviews]
    if review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        if missing_reviews:
            missing_for_close.append("completed review verdict: " + ", ".join(missing_reviews))
        else:
            missing_for_close.append("completed review verdict PASS for current task run")
    required_qa_lenses = _required_qa_lenses(task_dir, st)
    completed_qa = _completed_qa_by_lens(task_dir, snapshot)
    missing_qa_lenses = [lens for lens in required_qa_lenses if lens not in completed_qa]
    if runtime_verdict != "PASS":
        if missing_qa_lenses:
            missing_for_close.append("completed QA verdict: " + ", ".join(missing_qa_lenses))
        else:
            missing_for_close.append("completed QA verdict PASS")

    open_conversation_items = conversation_open_items(task_dir)
    if open_conversation_items:
        missing_for_close.append("CONVERSATION.md open items")

    if not has_plan and not micro_loop:
        next_action = "Create PLAN.md via plan skill before source writes."
    elif review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        next_action = (
            "Run and await the required read-only review subagent(s); completion hooks "
            "must record an explicit PASS for the current task run before QA."
        )
    elif runtime_verdict != "PASS":
        next_action = (
            "Run and await the required QA subagent(s); completion hooks must record "
            "an explicit PASS verdict."
        )
    elif open_conversation_items:
        next_action = (
            "Resolve CONVERSATION.md open item markers as captured, rejected, "
            "or deferred before task_close."
        )
    else:
        next_action = "Completed QA verdicts present — run task_close."

    attempts = list_attempts(task_dir)
    return {
        "task_id": os.path.basename(task_dir),
        "status": task_control_status(task_dir, st, snapshot),
        "task_dir": task_dir,
        "routing": routing,
        "runtime_verdict": runtime_verdict,
        "source_write_allowed": source_write_allowed,
        "why_source_write_blocked": why_blocked,
        "attempt_count": len(attempts),
        "latest_attempt": attempts[-1] if attempts else {},
        "review_verdict": review_verdict,
        "required_review_lenses": required_reviews,
        "required_qa_lenses": required_qa_lenses,
        "missing_for_close": missing_for_close,
        "next_action": next_action,
        "report_path": f"doc/harness/tasks/{os.path.basename(task_dir)}/{RECEIPTS_NAME}",
        "conversation_open_items": open_conversation_items[:10],
        "effective_close_gate": "micro" if micro_loop else "standard",
    }


# ── Explicit installer Git payload helper ──────────────────────────────
def _git_changed_paths(repo_root):
    """Return dirty paths for the explicit verified-install payload."""
    if not os.path.lexists(os.path.join(repo_root, ".git")):
        return set()
    base = ["git", "-c", f"safe.directory={repo_root}"]
    commands = (
        base + ["diff", "--name-only", "-z", "HEAD"],
        base + ["diff", "--cached", "--name-only", "-z", "HEAD"],
        base + ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    changed = set()
    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                timeout=5,
                env=_trusted_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f"Git changed-path query unavailable in {repo_root}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"Git changed-path query failed in {repo_root}"
            )
        output = result.stdout if isinstance(result.stdout, bytes) else os.fsencode(result.stdout or "")
        changed.update(os.fsdecode(path) for path in output.split(b"\0") if path)
    return changed


# ── Artifact helpers ─────────────────────────────────────────────────────


def artifact_exists(task_dir, filename):
    return os.path.isfile(os.path.join(task_dir, filename))


def provenance_from_artifacts(task_dir, snapshot=None):
    """Derive provenance from artifact existence."""
    snapshot = snapshot or receipt_snapshot(task_dir)
    has_subagent = bool(snapshot.entries)
    completed = _completed_qa_by_lens(task_dir, snapshot)
    reviews = _completed_review_by_lens(task_dir, snapshot)
    return {
        "plan-skill": artifact_exists(task_dir, "PLAN.md"),
        "subagent-start-hook": has_subagent,
        "code-reviewer": reviews.get("review-code", {}).get("verdict") == "PASS",
        "security-reviewer": reviews.get("review-security", {}).get("verdict") == "PASS",
        "qa-browser": completed.get("qa-browser", {}).get("verdict") == "PASS",
        "qa-api": completed.get("qa-api", {}).get("verdict") == "PASS",
        "qa-cli": completed.get("qa-cli", {}).get("verdict") == "PASS",
        "qa-desktop": completed.get("qa-desktop", {}).get("verdict") == "PASS",
        "ux-browser": has_subagent,
        "ux-api": has_subagent,
        "ux-cli": has_subagent,
        "ux-desktop": has_subagent,
    }


# ── Atomic JSON state helpers ─────────────────────────────────────────────


def read_json_state(path: str):
    """Read JSON state file. Returns None on missing/corrupt file."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def write_json_state(path: str, data) -> bool:
    """Atomically write JSON state file. Returns True on success."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
