#!/usr/bin/env python3
"""harness minimal library — stdlib only, 7-field TASK_STATE.

TASK_STATE schema:
  task_id, status, runtime_verdict,
  touched_paths, plan_session_state, closed_at, updated

Routing is computed on-the-fly from manifest + artifacts. Never stored.
Provenance is derived from artifact existence, not counters.
"""

import os
import re
import stat
import subprocess
import tempfile
import json
import hashlib
import time
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"
TASK_BASELINE_NAME = "TASK_BASELINE.json"
SUBAGENT_RECEIPTS_NAME = "SUBAGENT_RECEIPTS.jsonl"
REVIEW_RECEIPTS_NAME = "REVIEW_RECEIPTS.jsonl"
TASK_CLOSE_RECEIPT_NAME = "TASK_CLOSE_RECEIPT.json"
CONVERSATION_NAME = "CONVERSATION.md"
CONVERSATION_TEXT_CAP = 2000
CONVERSATION_READ_CAP = 256 * 1024

SCHEMA_FIELDS = (
    "task_id", "status", "runtime_verdict",
    "touched_paths", "plan_session_state",
    "closed_at", "updated",
)

_REVIEW_SNAPSHOT_CACHE = ContextVar("harness_review_snapshot_cache", default=None)
_REQUEST_GIT_ROOTS = ContextVar("harness_request_git_roots", default=None)
_REQUEST_SNAPSHOT_DEADLINE = ContextVar("harness_request_snapshot_deadline", default=None)
_GIT_ENUMERATION_TIMEOUT_SECONDS = 15.0


@contextmanager
def review_snapshot_scope(deadline_seconds=None):
    """Reuse source-derived review work only within one caller request."""
    current = _REVIEW_SNAPSHOT_CACHE.get()
    if current is not None:
        yield
        return
    token = _REVIEW_SNAPSHOT_CACHE.set({})
    roots_token = _REQUEST_GIT_ROOTS.set(set())
    deadline = (
        time.monotonic() + float(deadline_seconds)
        if deadline_seconds is not None
        else None
    )
    deadline_token = _REQUEST_SNAPSHOT_DEADLINE.set(deadline)
    try:
        yield
    finally:
        _REQUEST_SNAPSHOT_DEADLINE.reset(deadline_token)
        _REQUEST_GIT_ROOTS.reset(roots_token)
        _REVIEW_SNAPSHOT_CACHE.reset(token)


def refresh_review_snapshot() -> None:
    """Discard the current request snapshot before a final freshness gate."""
    cache = _REVIEW_SNAPSHOT_CACHE.get()
    if cache is not None:
        cache.clear()


def _review_snapshot_cache():
    return _REVIEW_SNAPSHOT_CACHE.get()


def _remember_git_root(repo_root):
    roots = _REQUEST_GIT_ROOTS.get()
    if roots is not None:
        roots.add(os.path.realpath(repo_root))


def _bounded_snapshot_timeout(
    default_seconds, operation, repo_root, *, deadline_allowance_seconds=None
):
    """Return the legacy timeout, or a larger allowance under a request deadline."""
    deadline = _REQUEST_SNAPSHOT_DEADLINE.get()
    if deadline is None:
        return float(default_seconds)
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RuntimeError(
            f"Git snapshot deadline exhausted before {operation} in {repo_root}"
        )
    allowance = (
        default_seconds
        if deadline_allowance_seconds is None
        else deadline_allowance_seconds
    )
    return min(float(allowance), remaining)


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


def _read_json_file(path: str, *, max_size: int = 1024 * 1024) -> dict:
    try:
        data = json.loads(_read_regular_text_file(path, max_size=max_size))
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_text_write(path: str, text: str) -> None:
    """Replace a text leaf without following a pre-existing leaf symlink."""
    parent = os.path.dirname(path) or "."
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
    for path in (_goal_path(repo_root, goal_id), _current_goal_path(repo_root)):
        _atomic_text_write(path, text)
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
                state = read_state(task_dir)
            except (OSError, ValueError):
                state = {}
            if (
                task.get("status") != "closed"
                or state.get("task_id") != task_id
                or state.get("status") != "closed"
                or str(state.get("runtime_verdict") or "").upper() != "PASS"
                or not task_close_attestation_valid(task_dir, state)
            ):
                blockers.append(task_id or "<missing task_id>")
            else:
                validated.append((task_id, task_dir))
        if not blockers:
            for task_id, task_dir in validated:
                final_state = read_state(task_dir)
                if (
                    final_state.get("task_id") != task_id
                    or final_state.get("status") != "closed"
                    or str(final_state.get("runtime_verdict") or "").upper() != "PASS"
                    or not task_close_attestation_valid(task_dir, final_state)
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
        or (
            os.environ.get("CODEX_THREAD_ID")
            if str(os.environ.get("HARNESS_RUNTIME") or "").lower() == "codex"
            else None
        )
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


# ── Task state read/write ────────────────────────────────────────────────


def state_file(task_dir):
    return os.path.join(task_dir, "TASK_STATE.yaml")


def read_state(task_dir):
    """Read all fields from TASK_STATE.yaml."""
    path = state_file(task_dir)
    result = {}
    text = _read_regular_text_file(path, max_size=256 * 1024)
    if not text:
        return result
    lines = text.splitlines()
    for field in SCHEMA_FIELDS:
        if field == "touched_paths":
            prefix = field + ":"
            result[field] = []
            for i, line in enumerate(lines):
                if not line.startswith(prefix):
                    continue
                rest = line[len(prefix):].strip()
                if rest == "[]":
                    break
                for item_line in lines[i + 1:]:
                    match = re.match(r"^\s+-\s+(.*)", item_line)
                    if not match:
                        break
                    result[field].append(match.group(1).strip().strip('"').strip("'"))
                break
        else:
            prefix = field + ":"
            result[field] = None
            for line in lines:
                if line.startswith(prefix):
                    value = line[len(prefix):].strip()
                    if value not in ("null", "~", "", "[]"):
                        result[field] = value.strip('"').strip("'")
                    break
    return result


def write_state(task_dir, fields):
    """Write TASK_STATE.yaml preserving field order. Atomic via tempfile."""
    path = state_file(task_dir)
    os.makedirs(task_dir, exist_ok=True)
    content = []
    for field in SCHEMA_FIELDS:
        content.append(f"{field}: {_yaml_fmt(fields.get(field))}")
    text = "\n".join(content) + "\n"
    fd, tmp = tempfile.mkstemp(dir=task_dir, prefix=".state.", suffix=".tmp")
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
    return True


def set_state_field(task_dir, field, value):
    """Set a single field, rewriting the file."""
    fields = read_state(task_dir)
    if not fields:
        return False
    fields[field] = value
    fields["updated"] = now_iso()
    return write_state(task_dir, fields)


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


def _nearest_git_root(start_dir):
    """Return the nearest containing Git root, or an empty string."""
    current = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    while True:
        git_path = os.path.join(current, ".git")
        if os.path.isdir(git_path) or os.path.isfile(git_path):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            return ""
        current = parent


def _manifest_array_field(repo_root, key):
    """Read one top-level scalar or block YAML string array, stdlib-only."""
    path = os.path.join(repo_root, MANIFEST_PATH)
    text = _read_regular_text_file(path, max_size=256 * 1024)
    if not text:
        return []
    lines = text.splitlines()
    prefix = key + ":"
    for index, line in enumerate(lines):
        if not line.startswith(prefix):
            continue
        rest = line[len(prefix):].strip()
        if rest.startswith("[") and rest.endswith("]"):
            inner = rest[1:-1].strip()
            if not inner:
                return []
            return [
                item.strip().strip('"').strip("'")
                for item in inner.split(",")
                if item.strip()
            ]
        values = []
        for child in lines[index + 1:]:
            match = re.match(r"^  -\s+(.+?)\s*$", child)
            if not match:
                break
            values.append(match.group(1).strip().strip('"').strip("'"))
        return values
    return []


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


def _direct_gitlink_index_entries(repo_root, *, git_dir=None):
    """Return direct stage-0 gitlinks without traversing their worktrees."""
    cache = _review_snapshot_cache()
    cache_key = (
        "direct_gitlink_index_entries",
        os.path.realpath(repo_root),
        os.path.realpath(git_dir) if git_dir else "",
    )
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])
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
            timeout=_bounded_snapshot_timeout(
                5,
                "direct gitlink index enumeration",
                repo_root,
                deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
            ),
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
    if cache is not None:
        cache[cache_key] = dict(found)
    return found


def _read_binding_file(path, *, code, relpath, invariant, max_size=4096):
    """Read one small regular metadata file without following its leaf."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        before_path = os.lstat(path)
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        raw = os.read(fd, max_size + 1)
        after = os.fstat(fd)
        final_path = os.lstat(path)
    except OSError as exc:
        raise GitBindingError(
            code,
            f"registered source '{relpath}' has unreadable linked-worktree metadata",
            path=relpath,
            invariant=invariant,
            next_action="Repair the Git worktree binding or remove the manifest entry, then retry.",
        ) from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
    if (
        len(raw) > max_size
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(before_path.st_mode)
        or not stat.S_ISREG(final_path.st_mode)
        or (opened.st_dev, opened.st_ino) != (before_path.st_dev, before_path.st_ino)
        or (opened.st_dev, opened.st_ino) != (final_path.st_dev, final_path.st_ino)
        or after.st_size != opened.st_size
        or after.st_mtime_ns != opened.st_mtime_ns
        or after.st_ctime_ns != opened.st_ctime_ns
    ):
        raise GitBindingError(
            code,
            f"registered source '{relpath}' linked-worktree metadata changed during validation",
            path=relpath,
            invariant=invariant,
            next_action="Stop concurrent Git operations and retry.",
        )
    return raw, (
        opened.st_dev,
        opened.st_ino,
        opened.st_size,
        opened.st_mtime_ns,
        opened.st_ctime_ns,
        hashlib.sha256(raw).hexdigest(),
    )


def _require_real_directory_path(path, *, relpath, invariant):
    absolute = os.path.abspath(path)
    cursor = os.path.sep
    for component in [part for part in absolute.split(os.path.sep) if part]:
        cursor = os.path.join(cursor, component)
        try:
            info = os.lstat(cursor)
        except OSError as exc:
            raise GitBindingError(
                "REGISTERED_WORKTREE_BINDING_MISMATCH",
                f"registered source '{relpath}' has missing Git metadata",
                path=relpath,
                invariant=invariant,
                next_action="Repair the Git worktree binding or remove the manifest entry, then retry.",
            ) from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise GitBindingError(
                "REGISTERED_WORKTREE_BINDING_MISMATCH",
                f"registered source '{relpath}' uses a symlinked or non-directory Git metadata path",
                path=relpath,
                invariant=invariant,
                next_action="Use a real linked-worktree metadata directory and retry.",
            )
    return os.lstat(absolute)


def _registered_source_metadata_binding(control_root, source_root, relpath):
    """Validate a parent-confined submodule or reciprocal linked worktree."""
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
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' is an arbitrary nested repository, not a linked gitlink checkout",
            path=relpath,
            invariant="gitfile_checkout",
            next_action="Use the parent gitlink checkout or remove the manifest entry.",
        )
    if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISREG(git_info.st_mode):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has invalid .git metadata",
            path=relpath,
            invariant="gitfile_regular",
            next_action="Repair the checkout and retry.",
        )

    raw_git, git_binding = _read_binding_file(
        git_path,
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="worktree_gitfile",
    )
    line = os.fsdecode(raw_git).strip()
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
    target_info = _require_real_directory_path(target, relpath=relpath, invariant="gitdir_path")

    git_control = _nearest_git_root(control_root) == os.path.realpath(control_root)
    parent_confined = False
    if git_control:
        parent_common = _git_path_snapshot(control_root, "--git-common-dir", use_cache=False)
        try:
            parent_confined = os.path.commonpath(
                (os.path.realpath(target), os.path.realpath(parent_common))
            ) == os.path.realpath(parent_common)
        except ValueError:
            parent_confined = False
    if parent_confined:
        _binding, resolved_gitdir = _validate_submodule_git_metadata(
            control_root, source_root, git_info,
        )
        return resolved_gitdir

    reported_gitdir = _git_path_snapshot(source_root, "--absolute-git-dir", use_cache=False)
    reported_common = _git_path_snapshot(source_root, "--git-common-dir", use_cache=False)
    reported_top = _git_path_snapshot(source_root, "--show-toplevel", use_cache=False)
    common = os.path.abspath(reported_common)
    _require_real_directory_path(common, relpath=relpath, invariant="common_dir_path")
    if os.path.abspath(reported_gitdir) != target:
        invariant = "absolute_git_dir"
    elif os.path.realpath(reported_top) != os.path.realpath(source_root):
        invariant = "worktree_top_level"
    elif os.path.dirname(os.path.dirname(target)) != common or os.path.basename(os.path.dirname(target)) != "worktrees":
        invariant = "linked_worktree_admin_shape"
    else:
        invariant = ""
    if invariant:
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' failed linked-worktree validation",
            path=relpath,
            invariant=invariant,
            next_action="Repair the Git worktree binding or remove the manifest entry, then retry.",
        )

    raw_common, common_binding = _read_binding_file(
        os.path.join(target, "commondir"),
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="commondir",
    )
    raw_backref, backref_binding = _read_binding_file(
        os.path.join(target, "gitdir"),
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="admin_gitdir_backreference",
    )
    declared_common = os.path.abspath(os.path.join(target, os.fsdecode(raw_common).strip()))
    declared_backref = os.path.abspath(os.fsdecode(raw_backref).strip())
    if declared_common != common or os.path.realpath(declared_common) != common:
        invariant = "commondir"
    elif declared_backref != os.path.abspath(git_path) or os.path.realpath(declared_backref) != os.path.abspath(git_path):
        invariant = "admin_gitdir_backreference"
    else:
        invariant = ""
    if invariant:
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' failed linked-worktree validation",
            path=relpath,
            invariant=invariant,
            next_action="Run Git worktree repair for this checkout, then retry.",
        )

    _git_head_snapshot(source_root, git_dir=target, use_cache=False)
    raw_git_after, git_binding_after = _read_binding_file(
        git_path,
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="worktree_gitfile",
    )
    raw_common_after, common_binding_after = _read_binding_file(
        os.path.join(target, "commondir"),
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="commondir",
    )
    raw_backref_after, backref_binding_after = _read_binding_file(
        os.path.join(target, "gitdir"),
        code="REGISTERED_WORKTREE_BINDING_CHANGED",
        relpath=relpath,
        invariant="admin_gitdir_backreference",
    )
    target_after = os.lstat(target)
    if (
        raw_git_after != raw_git
        or raw_common_after != raw_common
        or raw_backref_after != raw_backref
        or git_binding_after != git_binding
        or common_binding_after != common_binding
        or backref_binding_after != backref_binding
        or (target_after.st_dev, target_after.st_ino) != (target_info.st_dev, target_info.st_ino)
    ):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_CHANGED",
            f"registered source '{relpath}' binding changed during validation",
            path=relpath,
            invariant="binding_stability",
            next_action="Stop concurrent Git operations and retry.",
        )
    return target


def configured_source_git_roots(control_root, *, strict=True):
    """Return deterministic ``(workspace prefix, Git root)`` source bindings.

    A normal Git-backed Harness repository needs no manifest field and maps to
    the empty prefix. A non-Git control workspace must explicitly declare
    ``source_git_roots`` in its manifest. Runtime discovery is intentionally
    forbidden so an outer manifest cannot capture an unrelated nested repo.
    """
    control = os.path.realpath(control_root)
    configured = _manifest_array_field(control, "source_git_roots")
    git_control = _nearest_git_root(control) == control
    if not configured and git_control:
        return [("", control)]
    if not configured:
        if strict:
            raise RuntimeError(
                "Harness workspace has no Git root; manifest source_git_roots is required"
            )
        return []

    bindings = [("", control)] if git_control else []
    seen_roots = set()
    direct_gitlinks = _direct_gitlink_index_entries(control) if git_control else {}
    for raw in configured:
        rel = _canonical_git_relpath(raw).rstrip("/")
        if (
            not rel
            or os.path.isabs(rel)
            or rel in {".", ".."}
            or rel.startswith("../")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", rel)
            or any(part in {"", ".", ".."} for part in rel.split("/"))
        ):
            raise RuntimeError(f"invalid source_git_roots entry: {raw}")
        candidate = os.path.join(control, *rel.split("/"))
        cursor = control
        for part in rel.split("/"):
            cursor = os.path.join(cursor, part)
            if os.path.islink(cursor):
                raise RuntimeError(f"source_git_roots entry contains symlink: {rel}")
        if git_control and rel not in direct_gitlinks:
            raise GitBindingError(
                "REGISTERED_SOURCE_NOT_DIRECT_GITLINK",
                f"source_git_roots entry '{rel}' is not an exact direct mode-160000 entry in the control repository index",
                path=rel,
                invariant="direct_parent_gitlink",
                next_action=f"Check: git ls-files --stage -- '{rel}'. Fix the parent gitlink or remove the manifest entry.",
            )
        if git_control and not os.path.isdir(candidate):
            _registered_source_metadata_binding(control, candidate, rel)
        root = os.path.realpath(candidate)
        try:
            contained = os.path.commonpath((control, root)) == control
        except ValueError:
            contained = False
        if not contained or not os.path.isdir(root):
            raise RuntimeError(f"source_git_roots entry is not a directory inside workspace: {rel}")
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=root, capture_output=True, text=True, timeout=2,
                env=_trusted_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(f"source_git_roots entry is not a readable Git root: {rel}") from exc
        if result.returncode != 0 or os.path.realpath(result.stdout.strip()) != root:
            raise RuntimeError(f"source_git_roots entry is not an exact Git root: {rel}")
        if git_control:
            _registered_source_metadata_binding(control, root, rel)
        if root in seen_roots:
            raise RuntimeError(f"duplicate source_git_roots entry: {rel}")
        if any(
            os.path.commonpath((root, prior)) in {root, prior}
            for prior in seen_roots
        ):
            raise RuntimeError(f"nested source_git_roots entries are not allowed: {rel}")
        seen_roots.add(root)
        bindings.append((rel + "/", root))
    return sorted(bindings)


def harness_root_resolution(start_dir=None):
    """Return ``(root, error)`` for valid/none/invalid Harness ancestry."""
    start = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    nearest_git = _nearest_git_root(start)
    current = start
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
            configured = _manifest_array_field(current, "source_git_roots")
            if configured:
                try:
                    bindings = configured_source_git_roots(current)
                except RuntimeError as exc:
                    return current, str(exc)
            elif _nearest_git_root(current) == current:
                bindings = [("", current)]
            else:
                raw_version = _read_top_manifest_field(current, "version")
                try:
                    legacy_manifest = 1 <= int(str(raw_version)) < 5
                except (TypeError, ValueError):
                    legacy_manifest = False
                if not legacy_manifest:
                    return current, (
                        "Harness workspace has no Git root; manifest "
                        "source_git_roots is required"
                    )
                # Versioned pre-v5 manifests remain readable for migration.
                try:
                    if os.path.commonpath((current, start)) == current:
                        return current, ""
                except ValueError:
                    return "", ""
                bindings = []
            if nearest_git:
                if any(os.path.realpath(root) == nearest_git for _prefix, root in bindings):
                    return current, ""
            else:
                try:
                    if os.path.commonpath((current, start)) == current:
                        return current, ""
                except ValueError:
                    return "", ""
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
    payload = {
        "session_id": sid,
        "task_dir": task_dir,
        "task_id": os.path.basename(os.path.normpath(task_dir)),
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
        # The legacy .active marker predates TASK_STATE and is intentionally
        # conservative: an explicit canonical marker keeps Stop/prewrite gates
        # engaged even for old or partially-created task packs.
        return td
    state = read_state(td)
    if str(state.get("status") or "").lower() not in {
        "created", "planning", "implementing", "verifying"
    }:
        return ""
    if state.get("task_id") != os.path.basename(td):
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


def clear_active_marker(repo_root, task_dir=None, session_id=None):
    """Clear this session's active marker and matching legacy marker."""
    try:
        os.unlink(_session_active_path(repo_root, session_id))
    except (OSError, ValueError):
        pass
    legacy = _legacy_active_path(repo_root)
    try:
        if os.path.isfile(legacy):
            current = _read_legacy_active(repo_root)
            if task_dir is None or os.path.normpath(current) == os.path.normpath(task_dir):
                os.unlink(legacy)
    except OSError:
        pass


# ── Scaffold ─────────────────────────────────────────────────────────────


def ensure_task_scaffold(task_dir, task_id, request_text="", repo_root=None):
    """Create task dir with minimal 7-field TASK_STATE.yaml. Preserves existing state on resume."""
    os.makedirs(task_dir, exist_ok=True)
    expected_tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    if os.path.lexists(state_file(task_dir)):
        existing = read_state(task_dir)
        if existing.get("task_id") != expected_tid:
            raise ValueError(
                "existing TASK_STATE.yaml must be a regular file whose task_id matches its canonical directory"
            )
        control_root = (
            os.path.realpath(repo_root)
            if repo_root
            else find_harness_root(task_dir) or find_repo_root(task_dir)
        )
        if os.path.lexists(_baseline_file(task_dir)) or _task_baseline_required(control_root):
            _read_task_baseline_snapshot(task_dir, repo_root=control_root)
        created = [state_file(task_dir)]
        return {"created": created, "task_dir": task_dir, "task_id": expected_tid}
    tid = expected_tid
    fields = {
        "task_id": tid,
        "status": "created",
        "runtime_verdict": "pending",
        "touched_paths": [],
        "plan_session_state": "closed",
        "closed_at": None,
        "updated": now_iso(),
    }
    repo_root = (
        os.path.realpath(repo_root)
        if repo_root
        else find_harness_root(task_dir) or find_repo_root(task_dir)
    )
    try:
        baseline_path = capture_task_baseline(task_dir, repo_root=repo_root)
    except Exception as exc:
        raise RuntimeError(f"task baseline capture unavailable: {exc}") from exc
    if _task_baseline_required(repo_root) and not baseline_path:
        raise RuntimeError(
            "task baseline capture unavailable; create or restore a valid Git HEAD and retry task_start"
        )
    write_state(task_dir, fields)

    created = [state_file(task_dir)]
    if request_text:
        req_path = os.path.join(task_dir, "REQUEST.md")
        if not os.path.isfile(req_path) or os.path.islink(req_path):
            _atomic_text_write(req_path, request_text)
            created.append(req_path)
    return {"created": created, "task_dir": task_dir, "task_id": tid}


# ── Manifest ─────────────────────────────────────────────────────────────


def read_manifest_field(field, repo_root=None):
    repo_root = repo_root or find_harness_root() or find_repo_root()
    return yaml_field(field, os.path.join(repo_root, MANIFEST_PATH))


# AC-002: browser-QA close gate helpers (2026-05-12 retro)
_FRONTEND_EXT = (".tsx", ".jsx", ".vue", ".svelte", ".html", ".css", ".scss")
_FRONTEND_PATH_FRAGMENTS = ("/components/", "/pages/", "/views/", "/routes/")
_API_EXT = (".py", ".rb", ".go", ".java", ".kt", ".ts", ".js", ".php", ".cs")
_API_PATH_FRAGMENTS = (
    "/api/", "/apis/", "/controllers/", "/controller/", "/routes/",
    "/handlers/", "/handler/", "/endpoints/", "/endpoint/",
)
_CLI_PATH_FRAGMENTS = (
    "/cli/", "/cmd/", "/commands/", "/bin/", "/scripts/",
)
_CLI_BASENAME_HINTS = ("cli", "command", "commands", "main")
_DESKTOP_PATH_FRAGMENTS = (
    "/desktop/", "/gui/", "/native/", "/electron/", "/tauri/", "/qt/", "/gtk/",
    "/windows/", "/window/", "/menus/", "/dialogs/",
)
_REQ_REF_RE = re.compile(r"doc/[^)\]\s`'\"]+/REQ__[A-Za-z0-9_.-]+\.md")
_DURABLE_DOC_RE = re.compile(r"^doc/[^/]+/(?:REQ|GUIDE|ADR|POLICY)__[^/]+\.md$")
_UX_VERDICT_RE = re.compile(
    r"^## ux-(cli|api|browser|desktop) verdict: (PASS|FAIL|BLOCKED_ENV|PENDING)\s*$",
    re.MULTILINE,
)


def _read_nested_manifest_field(repo_root, *keys):
    """Two-level YAML lookup for nested manifest blocks like ``qa.browser_qa_supported``.

    Stdlib only — scans the manifest line-by-line, tracking the current top-level
    block. Returns the raw value string for the second-level key under the
    given top-level key, or None if not found.
    """
    if not keys or len(keys) != 2:
        return None
    top, sub = keys
    path = os.path.join(repo_root, MANIFEST_PATH)
    if not os.path.isfile(path):
        return None
    in_block = False
    top_prefix = top + ":"
    sub_prefix = "  " + sub + ":"  # 2-space indent under block
    try:
        text = _read_regular_text_file(path, max_size=256 * 1024)
        for line in text.splitlines(keepends=True):
            stripped = line.rstrip("\n")
            if stripped.startswith(top_prefix):
                in_block = True
                continue
            if in_block:
                if stripped and not stripped.startswith(" ") and not stripped.startswith("#"):
                    in_block = False
                    continue
                if stripped.startswith(sub_prefix):
                    val = stripped[len(sub_prefix):].strip()
                    if val in ("null", "~", "", "[]"):
                        return None
                    return val.strip('"').strip("'")
    except Exception:
        return None
    return None


def _read_top_manifest_field(repo_root, key):
    path = os.path.join(repo_root, MANIFEST_PATH)
    if not os.path.isfile(path):
        return None
    prefix = key + ":"
    try:
        text = _read_regular_text_file(path, max_size=256 * 1024)
        for line in text.splitlines(keepends=True):
            # Only column-zero keys are top-level. Stripping leading
            # whitespace let nested metadata.type override QA routing.
            stripped = line.rstrip("\n")
            if stripped.startswith(prefix):
                val = stripped[len(prefix):].strip()
                if val in ("null", "~", "", "[]"):
                    return None
                return val.strip('"').strip("'")
    except Exception:
        return None
    return None


def _manifest_bool(repo_root, top, sub=None):
    try:
        if sub is None:
            val = _read_top_manifest_field(repo_root, top)
        else:
            val = _read_nested_manifest_field(repo_root, top, sub)
            if val is None:
                val = _read_top_manifest_field(repo_root, sub)
    except Exception:
        val = None
    return (val or "").strip().lower() == "true"


def _frontend_touched(touched_paths):
    """Return True if any touched path looks like a user-facing frontend file."""
    for p in touched_paths or []:
        if not isinstance(p, str):
            continue
        lp = p.lower()
        if any(lp.endswith(ext) for ext in _FRONTEND_EXT):
            return True
        if any(frag in lp for frag in _FRONTEND_PATH_FRAGMENTS):
            return True
    return False


def _cli_touched(touched_paths):
    """Return True if touched paths look like user-facing CLI surface."""
    for p in touched_paths or []:
        if not isinstance(p, str):
            continue
        lp = "/" + p.lower().lstrip("./")
        base = os.path.splitext(os.path.basename(lp))[0]
        if any(frag in lp for frag in _CLI_PATH_FRAGMENTS):
            return True
        if base in _CLI_BASENAME_HINTS or any(hint in base for hint in ("_cli", "-cli")):
            return True
    return False


def _desktop_touched(touched_paths):
    """Return True if touched paths look like native desktop GUI surface."""
    for p in touched_paths or []:
        if not isinstance(p, str):
            continue
        lp = "/" + p.lower().lstrip("./")
        if any(frag in lp for frag in _DESKTOP_PATH_FRAGMENTS):
            return True
    return False


def _api_touched(touched_paths):
    """Return True if any touched path looks like an externally consumed API file."""
    for p in touched_paths or []:
        if not isinstance(p, str):
            continue
        lp = p.lower()
        if any(frag in lp for frag in _API_PATH_FRAGMENTS) and any(
            lp.endswith(ext) for ext in _API_EXT
        ):
            return True
    return False


def _required_ux_lenses(repo_root, touched_paths):
    """Return UX lenses required by manifest opt-in and touched surface."""
    ux_supported = _manifest_bool(repo_root, "qa", "ux_review_supported")
    browser_supported = _manifest_bool(repo_root, "qa", "browser_qa_supported")
    desktop_supported = _manifest_bool(repo_root, "qa", "desktop_qa_supported")

    lenses = []
    if (ux_supported or browser_supported) and _frontend_touched(touched_paths):
        lenses.append("browser")
    if ux_supported and _api_touched(touched_paths):
        lenses.append("api")
    if ux_supported and _cli_touched(touched_paths):
        lenses.append("cli")
    if (ux_supported or desktop_supported) and _desktop_touched(touched_paths):
        lenses.append("desktop")
    return lenses


def _has_req_doc_reference(task_dir, touched_paths):
    """Return True when task artifacts or touched docs reference a durable REQ."""
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    artifact_names = ("PLAN.md",)
    for name in artifact_names:
        path = os.path.join(task_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                if _REQ_REF_RE.search(f.read()):
                    return True
        except OSError:
            continue

    for rel in touched_paths or []:
        if not isinstance(rel, str):
            continue
        if _REQ_REF_RE.search(rel):
            abs_path = rel if os.path.isabs(rel) else os.path.join(repo_root, rel)
            if os.path.isfile(abs_path):
                return True
    return False


def _durable_docs_touched(touched_paths):
    """Return durable doc paths changed by this task."""
    paths = []
    for p in touched_paths or []:
        if not isinstance(p, str):
            continue
        rel = p.strip().lstrip("./")
        if _DURABLE_DOC_RE.match(rel):
            paths.append(rel)
    return paths


def _effective_touched_paths(task_dir, touched_paths):
    """Merge task paths with committed and current changes since task start."""
    out = set(touched_paths or [])
    try:
        control_root = find_harness_root(task_dir) or find_repo_root(task_dir)
        changed = set()
        for prefix, source_root in _workspace_source_bindings(control_root):
            changed.update(
                prefix + path
                for path in _committed_paths_since_baseline(
                    task_dir, source_root, workspace_prefix=prefix
                )
            )
        changed.update(_workspace_git_changed_paths(control_root))
        changed.update(_control_root_changed_paths(task_dir, control_root))
        out.update(_filter_baseline_unchanged(task_dir, control_root, changed))
    except RuntimeError:
        raise
    except Exception:
        pass
    return sorted(p for p in out if isinstance(p, str))


def _task_req_detector_texts(task_dir):
    texts = []
    for name in ("USER_FEEDBACK.md",):
        path = os.path.join(task_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as f:
                texts.append(f.read())
        except OSError:
            continue
    return texts


def _req_detector_result(task_dir, touched_paths):
    try:
        from req_detector import detect_req_need  # type: ignore
        return detect_req_need(texts=_task_req_detector_texts(task_dir), paths=touched_paths)
    except Exception:
        return {"requires_req": False, "confidence": "low", "surfaces": [], "reasons": []}


def _feedback_event_ids(task_dir):
    """Return captured user-feedback event ids from task-local JSONL."""
    path = os.path.join(task_dir, "USER_FEEDBACK.jsonl")
    if not os.path.isfile(path):
        return []
    ids = []
    seen = set()
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                event_id = str(event.get("id") or "").strip()
                if not event_id or event_id in seen:
                    continue
                seen.add(event_id)
                ids.append(event_id)
    except OSError:
        return []
    return ids


def _unresolved_feedback_event_ids(task_dir):
    return _feedback_event_ids(task_dir)


def is_maintenance_task(task_dir, repo_root=None):
    if os.path.isfile(os.path.join(task_dir, "MAINTENANCE")):
        return True
    return str(read_manifest_field("maintenance_default", repo_root) or "").lower() == "true"


# ── Routing (on-the-fly, never stored) ───────────────────────────────────


def compile_routing(task_dir, repo_root=None):
    repo_root = repo_root or find_repo_root()
    maintenance = is_maintenance_task(task_dir, repo_root)
    st = read_state(task_dir)
    micro_loop = _is_micro_loop_state(st)
    return {
        "maintenance_task": maintenance,
        "workflow_locked": not maintenance,
        "risk_level": "high" if maintenance else "medium",
        "execution_mode": "micro" if micro_loop else "standard",
        "orchestration_mode": "solo",
        "planning_mode": "skipped" if micro_loop else "standard",
    }


def _is_micro_loop_state(st):
    """Return True when TASK_STATE explicitly selects no-plan micro-loop mode.

    The harness keeps TASK_STATE to its historical 7 fields. To avoid a schema
    migration, the opt-in is encoded in the existing ``plan_session_state``
    field. Standard tasks keep ``closed`` and retain the plan-first gate.
    """
    mode = str((st or {}).get("plan_session_state") or "").strip().lower()
    return mode in {"micro", "micro_loop", "no_plan_micro", "develop_verify_close"}


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


def _subagent_receipts_path(task_dir):
    return os.path.join(task_dir, SUBAGENT_RECEIPTS_NAME)


def _review_receipts_path(task_dir):
    return os.path.join(task_dir, REVIEW_RECEIPTS_NAME)


_RECEIPT_STREAM_MAX_BYTES = 16 * 1024 * 1024


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
    if task_info.st_uid != os.getuid():
        raise RuntimeError("receipt storage integrity unavailable")
    return task_dir


@contextmanager
def _receipt_stream_lock(task_dir):
    task_dir = _validated_receipt_task_dir(task_dir)
    lock_path = os.path.join(task_dir, ".receipts.lock")
    flags = os.O_CREAT | os.O_RDWR
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(lock_path, flags, 0o600)
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_nlink != 1
        ):
            raise RuntimeError("receipt storage integrity unavailable")
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        yield
    finally:
        os.close(fd)


def _receipt_stream_info(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    if (
        stat.S_ISLNK(info.st_mode)
        or not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_nlink != 1
        or info.st_size > _RECEIPT_STREAM_MAX_BYTES
    ):
        raise RuntimeError("receipt storage integrity unavailable")
    return info


def _append_receipt_stream_unlocked(path, payload):
    prior = _receipt_stream_info(path)
    flags = os.O_APPEND | os.O_CREAT | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags, 0o644)
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or opened.st_size + len(payload) > _RECEIPT_STREAM_MAX_BYTES
            or (
                prior is not None
                and (opened.st_dev, opened.st_ino) != (prior.st_dev, prior.st_ino)
            )
        ):
            raise RuntimeError("receipt storage integrity unavailable")
        view = memoryview(payload)
        while view:
            written = os.write(fd, view)
            if written <= 0:
                raise RuntimeError("receipt storage integrity unavailable")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)


def _append_receipt_stream(path, payload):
    with _receipt_stream_lock(os.path.dirname(path)):
        _append_receipt_stream_unlocked(path, payload)


def _read_receipt_stream_unlocked(path, kind):
    prior = _receipt_stream_info(path)
    if prior is None:
        return []
    flags = os.O_RDONLY
    flags |= (
        getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or opened.st_size > _RECEIPT_STREAM_MAX_BYTES
            or (opened.st_dev, opened.st_ino) != (prior.st_dev, prior.st_ino)
        ):
            raise RuntimeError("receipt storage integrity unavailable")
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            text = handle.read(_RECEIPT_STREAM_MAX_BYTES + 1)
            final = os.fstat(handle.fileno())
        if (
            len(text.encode("utf-8")) > _RECEIPT_STREAM_MAX_BYTES
            or (final.st_dev, final.st_ino) != (opened.st_dev, opened.st_ino)
            or final.st_size != opened.st_size
        ):
            raise RuntimeError("receipt storage integrity unavailable")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError("receipt storage integrity unavailable") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    receipts = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except Exception as exc:
            raise RuntimeError("receipt storage integrity unavailable") from exc
        if not isinstance(item, dict) or item.get("kind") != kind:
            raise RuntimeError("receipt storage integrity unavailable")
        receipts.append(item)
    return receipts


def _read_receipt_stream(path, kind):
    with _receipt_stream_lock(os.path.dirname(path)):
        return _read_receipt_stream_unlocked(path, kind)


def _hash_file(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


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


def _git_head_for_receipt(task_dir):
    try:
        control_root = find_harness_root(task_dir) or find_repo_root(task_dir)
        return _workspace_head_snapshot(control_root)
    except Exception:
        return ""


def _workspace_source_bindings(control_root):
    control_root = os.path.realpath(control_root)
    cache = _review_snapshot_cache()
    cache_key = ("workspace_source_bindings", control_root)
    manifest_path = os.path.join(control_root, MANIFEST_PATH)
    try:
        manifest_info = os.lstat(manifest_path)
        manifest_token = (
            manifest_info.st_dev,
            manifest_info.st_ino,
            manifest_info.st_size,
            manifest_info.st_mtime_ns,
            manifest_info.st_ctime_ns,
        )
    except FileNotFoundError:
        manifest_token = None
    except OSError as exc:
        raise RuntimeError("Harness manifest binding snapshot unavailable") from exc
    if cache is not None and cache_key in cache:
        prior_token, prior_bindings = cache[cache_key]
        if prior_token != manifest_token:
            raise GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED",
                "source_git_roots authorization changed during the Git snapshot",
                path=MANIFEST_PATH,
                invariant="manifest_binding_set",
                next_action="Stop concurrent manifest edits and retry.",
            )
        return list(prior_bindings)
    nearest_git = _nearest_git_root(control_root)
    has_manifest = os.path.isfile(os.path.join(control_root, MANIFEST_PATH))
    configured = _manifest_array_field(control_root, "source_git_roots") if has_manifest else []
    if not nearest_git and not configured:
        if not has_manifest:
            return []
        raw_version = _read_top_manifest_field(control_root, "version")
        try:
            if 1 <= int(str(raw_version)) < 5:
                return []
        except (TypeError, ValueError):
            pass
        bindings = configured_source_git_roots(control_root, strict=True)
    else:
        bindings = configured_source_git_roots(control_root, strict=True)
    if cache is not None:
        cache[cache_key] = (manifest_token, tuple(bindings))
    return bindings


def _composite_source_heads(source_heads):
    digest = hashlib.sha1()
    for prefix, head in sorted(source_heads.items()):
        digest.update(prefix.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(head).lower().encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _registered_source_operation(control_root, prefix, source_root, bindings, operation):
    """Run one service snapshot through its validated explicit Git authority."""
    control = os.path.realpath(control_root)
    if (
        not prefix
        or _nearest_git_root(control) != control
        or os.path.realpath(source_root) == control
    ):
        return operation(None)
    relpath = prefix.rstrip("/")
    before = os.lstat(source_root)
    git_dir = _registered_source_metadata_binding(control, source_root, relpath)
    result = operation(git_dir)
    git_dir_after = _registered_source_metadata_binding(control, source_root, relpath)
    after = os.lstat(source_root)
    if (
        os.path.realpath(git_dir_after) != os.path.realpath(git_dir)
        or not stat.S_ISDIR(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_CHANGED",
            f"registered source '{relpath}' changed during the Git snapshot",
            path=relpath,
            invariant="source_snapshot_binding",
            next_action="Stop concurrent Git/worktree operations and retry.",
        )
    return result


def _workspace_source_heads(control_root):
    bindings = _workspace_source_bindings(control_root)
    if not bindings:
        return {}
    heads = [
        (
            prefix,
            _registered_source_operation(
                control_root,
                prefix,
                root,
                bindings,
                lambda git_dir, root=root: _git_head_snapshot(
                    root, git_dir=git_dir, use_cache=False,
                ),
            ),
        )
        for prefix, root in bindings
    ]
    return dict(heads)


def _workspace_head_snapshot(control_root):
    """Return one stable 40-hex identity for all configured source HEADs."""
    heads = _workspace_source_heads(control_root)
    if not heads:
        return ""
    if set(heads) == {""}:
        return heads[""]
    return _composite_source_heads(heads)


def _workspace_changed_path_fingerprints(control_root):
    changed = {}
    bindings = _workspace_source_bindings(control_root)
    for prefix, root in bindings:
        leaves = _registered_leaves_for_binding(control_root, prefix, root, bindings)
        fingerprints = _registered_source_operation(
            control_root,
            prefix,
            root,
            bindings,
            lambda git_dir, root=root, leaves=leaves: _changed_path_fingerprints(
                root, registered_leaves=leaves, git_dir=git_dir,
            ),
        )
        for relpath, fingerprint in fingerprints.items():
            changed[prefix + relpath] = fingerprint
    return changed


_CONTROL_ROOT_BEHAVIOR_PATHS = (
    "AGENTS.md",
    "CLAUDE.md",
    "CONTRACTS.md",
    "CONTRACTS.local.md",
    "CONTRACTS.user.md",
    MANIFEST_PATH,
)


def _control_root_behavior_fingerprint(control_root, relpath):
    current = os.path.realpath(control_root)
    for component in relpath.split("/"):
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            return "missing"
        except OSError as exc:
            raise RuntimeError(
                "control-root behavior fingerprint unavailable"
            ) from exc
        if stat.S_ISLNK(info.st_mode):
            raise RuntimeError("control-root behavior path must not be a symlink")
    return _fingerprint_path(control_root, relpath)


def _control_root_behavior_fingerprints(control_root):
    """Fingerprint the bounded behavioral surface of every Harness root."""
    control = os.path.realpath(control_root)
    if (
        not os.path.lexists(os.path.join(control, MANIFEST_PATH))
        and not _nearest_git_root(control)
    ):
        return {}
    return {
        relpath: _control_root_behavior_fingerprint(control, relpath)
        for relpath in _CONTROL_ROOT_BEHAVIOR_PATHS
    }


def _control_root_changed_paths(task_dir, control_root):
    """Return bounded parent-workspace behavior paths changed since baseline."""
    baseline = _read_task_baseline_snapshot(task_dir, repo_root=control_root)
    if not baseline or baseline.get("version") != 2:
        return set()
    before = baseline.get("control_paths")
    if not isinstance(before, dict):
        return set()
    current = _control_root_behavior_fingerprints(control_root)
    return {
        relpath
        for relpath in set(before) | set(current)
        if before.get(relpath, "missing") != current.get(relpath, "missing")
    }


def _control_root_touched_path_fingerprints(task_dir, control_root):
    """Fingerprint bounded behavioral paths outside child Git roots.

    This is intentionally independent of TASK_STATE.touched_paths so a parent
    file changed concurrently before synchronization still invalidates close.
    """
    del task_dir  # Signature stays task-close friendly; discovery is state-independent.
    return _control_root_behavior_fingerprints(control_root)


def _workspace_git_changed_paths(control_root):
    changed = set()
    bindings = _workspace_source_bindings(control_root)
    for prefix, root in bindings:
        leaves = _registered_leaves_for_binding(control_root, prefix, root, bindings)
        def source_changed(git_dir, *, root=root, prefix=prefix, leaves=leaves):
            source_paths = set(_git_changed_paths(root, prefix=prefix, git_dir=git_dir))
            for sub_path in _initialized_submodule_paths(
                root, registered_leaves=leaves, git_dir=git_dir,
            ):
                sub_root, _ = _validated_submodule_root(root, sub_path)
                source_paths.update(_git_changed_paths(
                    sub_root,
                    prefix=prefix + sub_path.rstrip("/") + "/",
                ))
                _validated_submodule_root(root, sub_path)
            return source_paths
        changed.update(_registered_source_operation(
            control_root, prefix, root, bindings, source_changed,
        ))
    return changed


def _workspace_gitlink_paths(control_root):
    paths = {}
    bindings = _workspace_source_bindings(control_root)
    for prefix, root in bindings:
        leaves = _registered_leaves_for_binding(control_root, prefix, root, bindings)
        snapshot = _registered_source_operation(
            control_root,
            prefix,
            root,
            bindings,
            lambda git_dir, root=root, leaves=leaves: _gitlink_index_snapshot(
                root, registered_leaves=leaves, git_dir=git_dir,
            ),
        )
        for relpath, entry in snapshot.items():
            paths[prefix + relpath] = (root, relpath, entry, relpath in leaves)
    return paths


def _registered_leaves_for_binding(control_root, prefix, root, bindings):
    """Return direct gitlink leaves owned by sibling source bindings."""
    control = os.path.realpath(control_root)
    if prefix or os.path.realpath(root) != control or _nearest_git_root(control) != control:
        return ()
    return tuple(sorted(
        child_prefix.rstrip("/")
        for child_prefix, _child_root in bindings
        if child_prefix
    ))


def _workspace_path_binding_with_prefix(control_root, relpath):
    rel = _canonical_git_relpath(relpath)
    bindings = _workspace_source_bindings(control_root)
    for prefix, root in sorted(bindings, key=lambda item: len(item[0]), reverse=True):
        if rel.startswith(prefix):
            inner = rel[len(prefix):]
            if inner:
                return prefix, root, inner
    for prefix, root in bindings:
        if not prefix:
            return prefix, root, rel
    control = os.path.realpath(control_root)
    candidate = os.path.realpath(os.path.join(control, rel))
    try:
        if os.path.commonpath((control, candidate)) != control:
            raise RuntimeError(f"path is outside Harness control root: {rel}")
    except ValueError as exc:
        raise RuntimeError(f"path is outside Harness control root: {rel}") from exc
    nearest_git = _nearest_git_root(
        candidate if os.path.isdir(candidate) else os.path.dirname(candidate)
    )
    if nearest_git and all(nearest_git != root for _prefix, root in bindings):
        raise RuntimeError(f"path is inside an unregistered Git root: {rel}")
    return "", control, rel


def _workspace_path_binding(control_root, relpath):
    _prefix, root, inner = _workspace_path_binding_with_prefix(control_root, relpath)
    return root, inner


_DEPENDENCY_REVIEW_FILES = {
    "composer.json", "composer.lock", "gemfile", "gemfile.lock", "go.mod", "go.sum",
    "package-lock.json", "package.json", "pipfile", "pipfile.lock", "poetry.lock",
    "pyproject.toml", "cargo.lock", "cargo.toml",
}

_AGENT_INSTRUCTION_FILES = {"agents.md", "claude.md"}


def _canonical_git_relpath(value):
    """Preserve Git path identity while accepting an explicit ./ prefix."""
    rel = str(value or "")
    if os.sep == "\\":
        rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _is_dependency_manifest(path):
    basename = os.path.basename(str(path or "").lower())
    return basename in _DEPENDENCY_REVIEW_FILES or bool(
        re.fullmatch(r"(?:requirements|constraints)(?:[-_.][a-z0-9_-]+)?\.txt", basename)
    )


def _reviewable_source_paths(task_dir, state=None):
    """Return task paths whose behavior merits independent static review."""
    st = state or read_state(task_dir)
    cache = _review_snapshot_cache()
    cache_key = (
        "reviewable_paths",
        os.path.realpath(task_dir),
        tuple(sorted(str(path) for path in (st.get("touched_paths") or []))),
    )
    if cache is not None and cache_key in cache:
        return list(cache[cache_key])
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    gitlink_paths = set(_workspace_gitlink_paths(repo_root))
    candidates = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    candidates = _filter_baseline_unchanged(task_dir, repo_root, candidates)
    paths = []
    executable_suffixes = {
        ".c", ".cc", ".conf", ".config", ".cpp", ".cs", ".css", ".go", ".h",
        ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx", ".kt", ".lock",
        ".php", ".pl", ".properties", ".py", ".rb", ".rs", ".sh", ".sql", ".swift",
        ".toml", ".ts", ".tsx", ".vue", ".xml", ".yaml", ".yml",
    }
    for raw in candidates:
        rel = _canonical_git_relpath(raw)
        if not rel:
            continue
        lower = rel.lower()
        basename = os.path.basename(lower)
        suffix = os.path.splitext(lower)[1]
        is_agent_instruction = basename in _AGENT_INSTRUCTION_FILES
        is_reviewable_artifact = (
            suffix in executable_suffixes
            or _is_dependency_manifest(lower)
            or is_agent_instruction
            or rel.rstrip("/") in gitlink_paths
        )
        if lower.startswith("doc/") and not is_reviewable_artifact:
            continue
        if lower.endswith((".pyc", ".pyo", ".pyd")) or "__pycache__/" in lower:
            continue
        if lower.endswith((".md", ".rst", ".txt")) and not (
            lower.startswith(("plugin/", "plugin-codex/"))
            or _is_dependency_manifest(lower)
            or is_agent_instruction
        ):
            continue
        if basename in {"readme", "readme.md", "changelog", "changelog.md", "license"}:
            continue
        paths.append(rel)
    result = sorted(set(paths))
    if cache is not None:
        cache[cache_key] = tuple(result)
    return result


_SECURITY_REVIEW_SIGNAL_RE = re.compile(
    r"(?i)(?:auth(?:entication|orization)?|session|token|secret|password|permission|"
    r"credential|oauth|jwt|csrf|cors|xss|injection|encrypt|crypto|payment|pii|"
    r"upload|file.?path|subprocess|shell|command|sql|database|migration|transaction|"
    r"concurren|race|lock|serialize|deserializ|external.?url|ssrf|dependency|"
    r"tls|ssl|certificate|cookie|rbac|acl|sanitize|verify[_-]?ssl|verify\s*=\s*false|"
    r"requirements|package(?:-lock)?\.json|pyproject|poetry\.lock|pipfile|cargo\.(?:toml|lock)|"
    r"gemfile|go\.(?:mod|sum)|composer\.(?:json|lock)|admin|privilege|"
    r"access[_-]?(?:control|policy)|user[_-]?role|role[_-]?(?:id|name|check))"
)


def _task_baseline_head_sha(task_dir):
    baseline = _read_task_baseline_snapshot(task_dir)
    if not baseline:
        return ""
    if baseline.get("version") == 2:
        return str((baseline.get("source_heads") or {}).get("") or "")
    return str(baseline.get("head_sha") or "")


def _task_baseline_source_head(task_dir, workspace_prefix=""):
    if not workspace_prefix:
        return _task_baseline_head_sha(task_dir)
    baseline = _read_task_baseline_snapshot(task_dir)
    if not baseline:
        return ""
    if baseline.get("version") == 1:
        return ""
    source_heads = baseline.get("source_heads") or {}
    return str(source_heads.get(workspace_prefix) or "")


def _committed_paths_since_baseline(task_dir, repo_root=None, *, workspace_prefix=""):
    """Return repository paths committed after the task baseline."""
    repo_root = repo_root or find_harness_root(task_dir) or find_repo_root(task_dir)
    baseline_head = _task_baseline_source_head(task_dir, workspace_prefix)
    if not baseline_head:
        return set()
    cache = _review_snapshot_cache()
    cache_key = (
        "committed_paths_since_baseline",
        os.path.realpath(task_dir),
        os.path.realpath(repo_root),
        workspace_prefix,
        baseline_head,
    )
    if cache is not None and cache_key in cache:
        return set(cache[cache_key])
    operation = "committed path diff"
    timeout = _bounded_snapshot_timeout(
        5, operation, repo_root,
        deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
    )
    control_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    source_before = os.lstat(repo_root)
    source_git_dir = None
    if workspace_prefix and _nearest_git_root(control_root) == os.path.realpath(control_root):
        source_git_dir = _registered_source_metadata_binding(
            control_root, repo_root, workspace_prefix.rstrip("/"),
        )
    command = ["git"]
    if source_git_dir:
        command.extend([
            f"--git-dir={source_git_dir}", f"--work-tree={repo_root}",
        ])
    command.extend([
        "diff", "--name-only", "-z", "--no-renames",
        "--end-of-options", baseline_head, "HEAD", "--",
    ])
    try:
        result = subprocess.run(
            command,
            cwd=repo_root, capture_output=True, timeout=timeout,
            env=_trusted_git_env(),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"task baseline Git diff unavailable: {operation} timed out "
            f"after {timeout:.1f}s in {repo_root}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"task baseline Git diff unavailable: {operation} could not run "
            f"in {repo_root}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"task baseline Git diff unavailable: {operation} exited "
            f"{result.returncode} in {repo_root}"
        )
    if source_git_dir:
        source_git_dir_after = _registered_source_metadata_binding(
            control_root, repo_root, workspace_prefix.rstrip("/"),
        )
        source_after = os.lstat(repo_root)
        if (
            os.path.realpath(source_git_dir_after) != os.path.realpath(source_git_dir)
            or not stat.S_ISDIR(source_after.st_mode)
            or stat.S_ISLNK(source_after.st_mode)
            or (source_after.st_dev, source_after.st_ino)
            != (source_before.st_dev, source_before.st_ino)
        ):
            raise GitBindingError(
                "REGISTERED_WORKTREE_BINDING_CHANGED",
                f"registered source '{workspace_prefix.rstrip('/')}' changed during committed-path diff",
                path=workspace_prefix.rstrip("/"),
                invariant="committed_path_source_binding",
                next_action="Stop concurrent Git/worktree operations and retry.",
            )
    raw = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout or "").encode()
    paths = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = _canonical_git_relpath(os.fsdecode(item))
        if rel and not os.path.isabs(rel) and rel != ".." and not rel.startswith("../"):
            paths.add(rel)
    if cache is not None:
        cache[cache_key] = frozenset(paths)
    return paths


def _path_has_security_signal(task_dir, repo_root, relpath):
    if (
        os.path.basename(str(relpath or "").lower()) in _AGENT_INSTRUCTION_FILES
        or _is_dependency_manifest(relpath)
        or _SECURITY_REVIEW_SIGNAL_RE.search(relpath)
    ):
        return True
    prefix, source_root, source_relpath = _workspace_path_binding_with_prefix(
        repo_root, relpath
    )
    baseline_head = _task_baseline_source_head(task_dir, prefix)
    if not baseline_head:
        # Legacy/corrupt baselines cannot prove that committed deleted lines
        # were inspected. Route the security reviewer rather than granting an
        # unsafe exemption.
        return True
    try:
        bindings = _workspace_source_bindings(repo_root)
        def security_diff(git_dir):
            command = ["git"]
            if git_dir:
                command.extend([
                    f"--git-dir={git_dir}", f"--work-tree={source_root}",
                ])
            command.extend([
                "diff", "--no-ext-diff", "--no-textconv", "--unified=0",
                "--end-of-options", baseline_head, "--", source_relpath,
            ])
            return subprocess.run(
                command,
                cwd=source_root, capture_output=True, text=True, timeout=3,
                env=_trusted_git_env(),
            )
        result = _registered_source_operation(
            repo_root, prefix, source_root, bindings, security_diff,
        )
        if result.returncode == 0 and result.stdout and _SECURITY_REVIEW_SIGNAL_RE.search(result.stdout):
            return True
        if result.returncode != 0:
            return True
    except Exception:
        return True
    path = os.path.join(source_root, source_relpath)
    try:
        if os.path.isfile(path):
            overlap = ""
            with open(path, encoding="utf-8", errors="replace") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    text = overlap + chunk
                    if _SECURITY_REVIEW_SIGNAL_RE.search(text):
                        return True
                    overlap = text[-512:]
    except OSError:
        pass
    return False


def required_review_lenses(task_dir, state=None):
    """Route always-on code review plus conditional deep security review."""
    st = state or read_state(task_dir)
    paths = _reviewable_source_paths(task_dir, st)
    if not paths:
        return []
    lenses = ["review-code"]
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    for relpath in paths:
        if _path_has_security_signal(task_dir, repo_root, relpath):
            lenses.append("review-security")
            break
    return lenses


def review_diff_fingerprint(task_dir, state=None):
    """Hash every task-owned path for review/QA completion freshness."""
    st = state or read_state(task_dir)
    cache = _review_snapshot_cache()
    cache_key = (
        "review_fingerprint",
        os.path.realpath(task_dir),
        tuple(sorted(str(path) for path in (st.get("touched_paths") or []))),
    )
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    gitlink_paths = _workspace_gitlink_paths(repo_root)
    h = hashlib.sha256()
    candidates = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    candidates = _filter_baseline_unchanged(task_dir, repo_root, candidates)
    for relpath in sorted(set(_canonical_git_relpath(path) for path in candidates if path)):
        h.update(os.fsencode(relpath))
        h.update(b"\0")
        if relpath.rstrip("/") in gitlink_paths:
            source_root, source_relpath, entry, registered = gitlink_paths[relpath.rstrip("/")]
            fingerprint = (
                _registered_source_gitlink_fingerprint(
                    source_root, source_relpath, entry[0],
                )
                if registered
                else _submodule_gitlink_fingerprint(source_root, source_relpath)
            )
        else:
            source_root, source_relpath = _workspace_path_binding(repo_root, relpath)
            fingerprint = _fingerprint_path(source_root, source_relpath)
        h.update(fingerprint.encode("ascii"))
        h.update(b"\0")
    for relpath, fingerprint in sorted(
        _control_root_behavior_fingerprints(repo_root).items()
    ):
        h.update(b"@control\0")
        h.update(os.fsencode(relpath))
        h.update(b"\0")
        h.update(fingerprint.encode("ascii"))
        h.update(b"\0")
    result = "sha256:" + h.hexdigest()
    if cache is not None:
        cache[cache_key] = result
    return result


def _receipt_stream_fingerprint_unlocked(task_dir):
    """Hash live review/QA receipt streams without request caching."""
    h = hashlib.sha256()
    for name in (REVIEW_RECEIPTS_NAME, SUBAGENT_RECEIPTS_NAME):
        path = os.path.join(task_dir, name)
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            h.update(b"<missing>\0")
            continue
        except OSError as exc:
            raise RuntimeError("receipt stream snapshot unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise RuntimeError("receipt stream snapshot unavailable")
        flags = (
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0)
        )
        fd = None
        try:
            fd = os.open(path, flags)
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
            ):
                raise RuntimeError("receipt stream snapshot unavailable")
            handle = os.fdopen(fd, "rb")
            fd = None
            with handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    h.update(chunk)
                after = os.fstat(handle.fileno())
            final_path = os.lstat(path)
            if (
                after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
                or not stat.S_ISREG(final_path.st_mode)
                or (final_path.st_dev, final_path.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise RuntimeError("receipt stream snapshot unavailable")
        except OSError as exc:
            raise RuntimeError("receipt stream snapshot unavailable") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        h.update(b"\0")
    return "sha256:" + h.hexdigest()


def receipt_stream_fingerprint(task_dir):
    with _receipt_stream_lock(task_dir):
        return _receipt_stream_fingerprint_unlocked(task_dir)


def write_task_close_attestation(task_dir, state, *, head_sha, receipt_fingerprint):
    """Persist the task_close evidence needed after later Goal work changes Git."""
    payload = {
        "version": 1,
        "task_id": str(state.get("task_id") or ""),
        "closed_at": str(state.get("closed_at") or ""),
        "runtime_verdict": str(state.get("runtime_verdict") or "").upper(),
        "head_sha": str(head_sha or ""),
        "receipt_stream_fingerprint": str(receipt_fingerprint or ""),
    }
    if (
        not re.fullmatch(r"TASK__[A-Za-z0-9._-]+", payload["task_id"])
        or not payload["closed_at"]
        or payload["runtime_verdict"] != "PASS"
        or not re.fullmatch(r"[0-9a-f]{40}", payload["head_sha"])
        or not re.fullmatch(r"sha256:[0-9a-f]{64}", payload["receipt_stream_fingerprint"])
    ):
        raise ValueError("invalid task close attestation inputs")
    _atomic_json_write(os.path.join(task_dir, TASK_CLOSE_RECEIPT_NAME), payload)
    return payload


def clear_task_close_attestation(task_dir):
    try:
        os.unlink(os.path.join(task_dir, TASK_CLOSE_RECEIPT_NAME))
    except FileNotFoundError:
        pass


def task_close_attestation_valid(task_dir, state):
    if not task_dir:
        return False
    payload = _read_json_file(
        os.path.join(task_dir, TASK_CLOSE_RECEIPT_NAME),
        max_size=16 * 1024,
    )
    if (
        payload.get("version") != 1
        or payload.get("task_id") != state.get("task_id")
        or payload.get("closed_at") != state.get("closed_at")
        or payload.get("runtime_verdict") != "PASS"
        or not re.fullmatch(r"[0-9a-f]{40}", str(payload.get("head_sha") or ""))
        or not re.fullmatch(
            r"sha256:[0-9a-f]{64}",
            str(payload.get("receipt_stream_fingerprint") or ""),
        )
    ):
        return False
    try:
        return receipt_stream_fingerprint(task_dir) == payload["receipt_stream_fingerprint"]
    except RuntimeError:
        return False


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


def record_subagent_receipt(task_dir, receipt):
    """Append a structured subagent invocation receipt to the task directory.

    This is intentionally hook-owned. Start entries prove delegation;
    completed QA entries with explicit verdicts drive task verification.
    """
    if not isinstance(receipt, dict):
        raise ValueError("receipt must be an object")
    agent_id = _receipt_short(receipt.get("agent_id") or receipt.get("id"), 300)
    if not agent_id:
        raise ValueError("agent_id required")
    source = _receipt_short(receipt.get("source") or "spawn_agent", 100)
    agent_type = _receipt_short(receipt.get("agent_type"), 300)
    verdict = _receipt_short(receipt.get("verdict") or "", 40).upper()
    if verdict and verdict not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING", "UNKNOWN"}:
        verdict = "UNKNOWN"
    transcript_path = _receipt_short(receipt.get("transcript_path"), 1000)
    lens = _infer_receipt_lens(agent_type, receipt.get("lens"))
    is_review = lens.startswith("review-")
    status = _receipt_short(receipt.get("status") or "done", 80)
    is_completed = status.lower() in {"completed", "done"}
    now = _receipt_now_iso()
    finding_counts = {"fix_now": 0, "investigate": 0, "optional": 0}
    raw_summary = str(receipt.get("summary") or "")
    summary = _receipt_short(raw_summary, 1000)
    summary_lines = raw_summary.splitlines()
    counts_match = _FINDING_COUNTS_RE.fullmatch(summary_lines[1]) if len(summary_lines) > 1 else None
    counts_reported = bool(counts_match) and sum(
        "FINDING_COUNTS:" in line for line in summary_lines
    ) == 1
    if counts_match:
        finding_counts = {
            "fix_now": int(counts_match.group(1)),
            "investigate": int(counts_match.group(2)),
            "optional": int(counts_match.group(3)),
        }
    if is_review and is_completed and not counts_reported:
        verdict = "PENDING"
    if is_review and is_completed and counts_reported:
        if verdict == "PASS" and (finding_counts["fix_now"] or finding_counts["investigate"]):
            verdict = "PENDING"
        if verdict == "FAIL" and not finding_counts["fix_now"]:
            verdict = "PENDING"
        if verdict == "BLOCKED_ENV" and not finding_counts["investigate"]:
            verdict = "PENDING"
    entry = {
        "receipt_id": "",
        "ts": now,
        "kind": "review" if is_review else "subagent",
        "event": ("review_completed" if is_completed else "review_started") if is_review else (
            "subagent_completed" if is_completed else "subagent_started"
        ),
        "source": source,
        "status": status,
        "task_id": os.path.basename(os.path.normpath(task_dir)),
        "agent_id": agent_id,
        "agent_type": agent_type,
        "lens": lens,
        "verdict": verdict,
        "summary": summary,
        "transcript_path": transcript_path,
        "transcript_sha256": _hash_file(transcript_path) if transcript_path else "",
        "prompt_hash": hashlib.sha256(
            _receipt_short(receipt.get("prompt"), 10000).encode("utf-8")
        ).hexdigest() if receipt.get("prompt") else "",
        "head_sha": _receipt_short(
            receipt.get("head_sha") or receipt.get("commit_sha") or _git_head_for_receipt(task_dir),
            80,
        ),
        "diff_fingerprint": _receipt_short(
            receipt.get("diff_fingerprint") or review_diff_fingerprint(task_dir), 100
        ),
        "base_sha": _receipt_short(receipt.get("base_sha") or _git_head_for_receipt(task_dir), 80),
        "started_at": "" if is_completed else now,
        "finished_at": now if is_completed else "",
        "finding_counts": finding_counts,
        "finding_counts_reported": counts_reported,
        "runtime_event_id": _receipt_short(receipt.get("runtime_event_id"), 500),
        "runtime_session_id": _receipt_short(receipt.get("runtime_session_id"), 160),
        "runtime_thread_id": _receipt_short(receipt.get("runtime_thread_id"), 160),
        "runtime_agent_path": _receipt_short(receipt.get("runtime_agent_path"), 300),
    }
    seed = "|".join([entry["ts"], entry["source"], entry["agent_id"], entry["agent_type"], entry["lens"]])
    entry["receipt_id"] = "subagent-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    path = _review_receipts_path(task_dir) if is_review else _subagent_receipts_path(task_dir)
    _validated_receipt_task_dir(task_dir)
    payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    _append_receipt_stream(path, payload)
    return entry


def list_subagent_receipts(task_dir):
    return _read_receipt_stream(_subagent_receipts_path(task_dir), "subagent")


def list_review_receipts(task_dir):
    return _read_receipt_stream(_review_receipts_path(task_dir), "review")


def subagent_receipt_summary(task_dir):
    receipts = list_subagent_receipts(task_dir)
    by_lens = {}
    by_agent_type = {}
    by_source = {}
    by_verdict = {}
    for item in receipts:
        lens = item.get("lens") or "unknown"
        agent_type = item.get("agent_type") or "unknown"
        source = item.get("source") or "unknown"
        verdict = item.get("verdict") or "UNKNOWN"
        by_lens[lens] = by_lens.get(lens, 0) + 1
        by_agent_type[agent_type] = by_agent_type.get(agent_type, 0) + 1
        by_source[source] = by_source.get(source, 0) + 1
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    completed = [
        item for item in receipts
        if str(item.get("status") or "").lower() in {"completed", "done"}
    ]
    latest = receipts[-1] if receipts else {}
    if latest:
        latest = {
            "receipt_id": latest.get("receipt_id", ""),
            "ts": latest.get("ts", ""),
            "source": latest.get("source", ""),
            "agent_id": latest.get("agent_id", ""),
            "agent_type": latest.get("agent_type", ""),
            "lens": latest.get("lens", ""),
            "verdict": latest.get("verdict", ""),
        }
    return {
        "count": len(receipts),
        "completed_count": len(completed),
        "by_lens": by_lens,
        "by_agent_type": by_agent_type,
        "by_source": by_source,
        "by_verdict": by_verdict,
        "latest": latest,
    }


def review_receipt_summary(task_dir):
    receipts = list_review_receipts(task_dir)
    by_lens = {}
    by_verdict = {}
    for item in receipts:
        lens = item.get("lens") or "unknown"
        verdict = item.get("verdict") or "UNKNOWN"
        by_lens[lens] = by_lens.get(lens, 0) + 1
        by_verdict[verdict] = by_verdict.get(verdict, 0) + 1
    return {
        "count": len(receipts),
        "completed_count": sum(
            str(item.get("status") or "").lower() in {"completed", "done"}
            for item in receipts
        ),
        "by_lens": by_lens,
        "by_verdict": by_verdict,
        "latest": receipts[-1] if receipts else {},
    }


def _completed_review_by_lens(task_dir):
    receipts = list_review_receipts(task_dir)
    latest_events = {}
    for item in receipts:
        lens = str(item.get("lens") or "").lower()
        if lens.startswith("review-"):
            latest_events[lens] = item
    completed = {}
    for lens, item in latest_events.items():
        if str(item.get("status") or "").lower() not in {"completed", "done"}:
            continue
        if str(item.get("verdict") or "").upper() not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING"}:
            continue
        matching_starts = [
            prior for prior in receipts
            if prior is not item
            and prior.get("lens") == lens
            and prior.get("agent_id") == item.get("agent_id")
            and str(prior.get("status") or "").lower() == "started"
        ]
        if not matching_starts:
            continue
        start = matching_starts[-1]
        if start.get("diff_fingerprint") != item.get("diff_fingerprint"):
            continue
        if start.get("head_sha") != item.get("head_sha"):
            continue
        completed[lens] = item
    return completed


def _receipt_timestamp(item):
    try:
        return datetime.fromisoformat(str(item.get("ts") or "").replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _latest_review_pass_timestamp(task_dir, state=None):
    st = state or read_state(task_dir)
    if receipt_review_verdict(task_dir, st) != "PASS":
        return 0.0
    completed = _completed_review_by_lens(task_dir)
    return max((_receipt_timestamp(completed[lens]) for lens in required_review_lenses(task_dir, st)), default=0.0)


def _qa_started_after_review(task_dir, lens, completion, review_ts):
    if review_ts <= 0:
        return True
    agent_id = completion.get("agent_id")
    return any(
        item.get("lens") == lens
        and item.get("agent_id") == agent_id
        and str(item.get("status") or "").lower() == "started"
        and _receipt_timestamp(item) >= review_ts
        for item in list_subagent_receipts(task_dir)
    )


def receipt_review_verdict(task_dir, state=None):
    st = state or read_state(task_dir)
    required = required_review_lenses(task_dir, st)
    if not required:
        return "NOT_APPLICABLE"
    completed = _completed_review_by_lens(task_dir)
    current_fingerprint = review_diff_fingerprint(task_dir, st)
    current_head = _git_head_for_receipt(task_dir)
    verdicts = []
    for lens in required:
        item = completed.get(lens)
        if (
            not item
            or item.get("diff_fingerprint") != current_fingerprint
            or item.get("head_sha") != current_head
        ):
            return "PENDING"
        verdicts.append(str(item.get("verdict") or "").upper())
    if any(verdict == "FAIL" for verdict in verdicts):
        return "FAIL"
    if any(verdict == "BLOCKED_ENV" for verdict in verdicts):
        return "BLOCKED_ENV"
    return "PASS" if all(verdict == "PASS" for verdict in verdicts) else "PENDING"


def _required_qa_lenses(task_dir, state=None):
    st = state or read_state(task_dir)
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    project_type = (_read_top_manifest_field(repo_root, "type") or "").lower()
    lenses = []
    if _manifest_bool(repo_root, "qa", "desktop_qa_supported") or _desktop_touched(touched):
        lenses.append("qa-desktop")
    if _manifest_bool(repo_root, "qa", "browser_qa_supported") and _frontend_touched(touched):
        lenses.append("qa-browser")
    if project_type == "api" or _api_touched(touched):
        lenses.append("qa-api")
    if project_type in {"cli", "library"}:
        lenses.append("qa-cli")
    return list(dict.fromkeys(lenses or ["qa-cli"]))


def _completed_qa_by_lens(task_dir):
    latest_events = {}
    for item in list_subagent_receipts(task_dir):
        lens = str(item.get("lens") or "").lower()
        if not lens.startswith("qa-"):
            continue
        latest_events[lens] = item
    latest = {}
    for lens, item in latest_events.items():
        status = str(item.get("status") or "").lower()
        verdict = str(item.get("verdict") or "").upper()
        if status not in {"completed", "done"}:
            continue
        if verdict not in {"PASS", "FAIL", "BLOCKED_ENV", "PENDING"}:
            continue
        latest[lens] = item
    return latest


def receipt_runtime_verdict(task_dir, state=None):
    """Compute runtime verdict from completed, explicit QA receipts only."""
    st = state or read_state(task_dir)
    current = (st.get("runtime_verdict") or "pending").upper() if isinstance(st, dict) else "PENDING"
    if current == "BLOCKED_ENV":
        return "BLOCKED_ENV"
    review_verdict = receipt_review_verdict(task_dir, st)
    if review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        return "PENDING"
    required = _required_qa_lenses(task_dir, st)
    completed = _completed_qa_by_lens(task_dir)
    review_ts = _latest_review_pass_timestamp(task_dir, st)
    current_head = _git_head_for_receipt(task_dir)
    current_fingerprint = review_diff_fingerprint(task_dir)
    valid = {
        lens: completed[lens] for lens in required
        if (
            lens in completed
            and _qa_started_after_review(task_dir, lens, completed[lens], review_ts)
            and str(completed[lens].get("head_sha") or "") == current_head
            and str(completed[lens].get("diff_fingerprint") or "") == current_fingerprint
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


# ── Runtime-verdict staleness check ─────────────────────────────────────
#
# Runtime verification is receipt-backed. Completion timestamps are compared
# with touched paths so edits made after QA invalidate the close signal.

_STALE_CHECK_SKIP_SUFFIXES = (
    ".pyc", ".pyo", ".pyd",
)
_STALE_CHECK_SKIP_FRAGMENTS = (
    "__pycache__/", "/.DS_Store", ".swp", ".swo",
)
_STALE_CHECK_SKIP_PREFIXES = (
    "doc/harness/",
    "doc/changes/",
)
_STALE_CHECK_SKIP_BASENAMES = {
    SUBAGENT_RECEIPTS_NAME,
    REVIEW_RECEIPTS_NAME,
    "TASK_STATE.yaml",
    "PLAN.meta.json",
    "PLAN_SESSION.json",
    "PROGRESS.md",
    "DOGFOOD.md",
    "ENVIRONMENT_SNAPSHOT.md",
}
_STALE_CHECK_PATH_CAP = 1000  # bound mtime scan in pathological cases


def _stale_skip(relpath: str) -> bool:
    if not relpath:
        return True
    norm = _canonical_git_relpath(relpath)
    base = os.path.basename(norm)
    if base in _STALE_CHECK_SKIP_BASENAMES:
        return True
    for prefix in _STALE_CHECK_SKIP_PREFIXES:
        if norm.startswith(prefix):
            return True
    for suf in _STALE_CHECK_SKIP_SUFFIXES:
        if norm.endswith(suf):
            return True
    for frag in _STALE_CHECK_SKIP_FRAGMENTS:
        if frag in norm or norm.endswith(frag.strip("/")):
            return True
    return False


def runtime_is_stale(task_dir: str) -> tuple[bool, str]:
    st = read_state(task_dir)
    required = _required_qa_lenses(task_dir, st)
    completed = _completed_qa_by_lens(task_dir)
    passing = [completed.get(lens) for lens in required]
    if not passing or any(not item or item.get("verdict") != "PASS" for item in passing):
        return False, ""
    try:
        completed_at = min(
            datetime.fromisoformat(str(item.get("ts") or "").replace("Z", "+00:00")).timestamp()
            for item in passing if item
        )
    except (TypeError, ValueError):
        return True, SUBAGENT_RECEIPTS_NAME
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    for relpath in touched[:_STALE_CHECK_PATH_CAP]:
        if _stale_skip(relpath):
            continue
        try:
            source_root, source_relpath = _workspace_path_binding(repo_root, relpath)
        except RuntimeError:
            continue
        path = os.path.join(source_root, source_relpath)
        try:
            if os.path.getmtime(path) > completed_at:
                return True, relpath
        except OSError:
            continue
    return False, ""


def emit_compact_context(task_dir):
    """Build the canonical task pack with on-the-fly routing.

    Always populates ``stale`` and ``stale_path`` keys via :func:`runtime_is_stale`
    so callers (stop_gate, task_close gate, MCP task_verify) can refuse to
    permit transitions on stale frozen verdicts without re-computing.
    """
    st = read_state(task_dir)
    if not st:
        return {"error": "no TASK_STATE.yaml", "task_dir": task_dir}

    routing = compile_routing(task_dir)
    runtime_verdict = receipt_runtime_verdict(task_dir, st)
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])

    micro_loop = _is_micro_loop_state(st)
    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_write_allowed = has_plan or micro_loop
    why_blocked = "" if source_write_allowed else "PLAN.md does not exist yet"

    missing_for_close = []
    if not has_plan and not micro_loop:
        missing_for_close.append("PLAN.md")
    receipt_summary = subagent_receipt_summary(task_dir)
    review_summary = review_receipt_summary(task_dir)
    required_reviews = required_review_lenses(task_dir, st)
    review_verdict = receipt_review_verdict(task_dir, st)
    completed_reviews = _completed_review_by_lens(task_dir)
    missing_reviews = [lens for lens in required_reviews if lens not in completed_reviews]
    if review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        if missing_reviews:
            missing_for_close.append("completed review verdict: " + ", ".join(missing_reviews))
        else:
            missing_for_close.append("completed review verdict PASS for current diff")
    required_qa_lenses = _required_qa_lenses(task_dir, st)
    completed_qa = _completed_qa_by_lens(task_dir)
    missing_qa_lenses = [lens for lens in required_qa_lenses if lens not in completed_qa]
    if runtime_verdict != "PASS":
        if missing_qa_lenses:
            missing_for_close.append("completed QA verdict: " + ", ".join(missing_qa_lenses))
        else:
            missing_for_close.append("completed QA verdict PASS")

    # Browser QA uses the same lifecycle receipt stream as other QA lenses.
    # There is no separate browser critic artifact gate.
    repo_root = find_repo_root()
    try:
        browser_supported = _manifest_bool(repo_root, "qa", "browser_qa_supported")
    except Exception:
        browser_supported = False
    frontend_touched = _frontend_touched(touched)
    api_touched = _api_touched(touched)
    durable_doc_paths = _durable_docs_touched(touched)
    req_detection = _req_detector_result(task_dir, touched)
    unresolved_feedback = _unresolved_feedback_event_ids(task_dir)
    open_conversation_items = conversation_open_items(task_dir)
    if open_conversation_items:
        missing_for_close.append("CONVERSATION.md open items")
    # Durable-doc close gate. PLAN's Durable Docs Decision is still required,
    # but implementation can grow new visible/API surfaces after planning. The
    # close gate rechecks the actual diff so `REQ: n/a` cannot silently pass for
    # a new page, route, controller, endpoint, or comparable observable surface.
    req_needed_by_detector = (
        bool(req_detection.get("requires_req"))
        and str(req_detection.get("confidence") or "").lower() in {"high", "medium"}
    )
    if (frontend_touched or api_touched or req_needed_by_detector) and not _has_req_doc_reference(task_dir, touched):
        if frontend_touched and api_touched:
            missing_for_close.append("REQ durable doc for UI/API observable behavior")
        elif frontend_touched:
            missing_for_close.append("REQ durable doc for UI observable behavior")
        elif api_touched:
            missing_for_close.append("REQ durable doc for API observable behavior")
        else:
            missing_for_close.append("REQ durable doc for observable behavior or user feedback")

    if not has_plan and not micro_loop:
        next_action = "Create PLAN.md via plan skill before source writes."
    elif review_verdict not in {"PASS", "NOT_APPLICABLE"}:
        next_action = (
            "Run and await the required read-only review subagent(s); completion hooks "
            "must record an explicit PASS for the current diff before QA."
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
    elif any(m.startswith("REQ durable doc") for m in missing_for_close):
        next_action = (
            "Create or update a doc/<area>/REQ__*.md for the observable "
            "behavior before source work, then link it from PLAN.md."
        )
    else:
        next_action = "Completed QA verdicts present — run task_close."

    stale, stale_path = runtime_is_stale(task_dir)

    attempts = list_attempts(task_dir)
    return {
        "task_id": st.get("task_id") or os.path.basename(task_dir),
        "status": st.get("status") or "unknown",
        "task_dir": task_dir,
        "routing": routing,
        "runtime_verdict": runtime_verdict,
        "source_write_allowed": source_write_allowed,
        "why_source_write_blocked": why_blocked,
        "touched_paths": touched,
        "path_count": len(touched),
        "attempt_count": len(attempts),
        "latest_attempt": attempts[-1] if attempts else {},
        "subagent_receipts": receipt_summary,
        "review_receipts": review_summary,
        "review_verdict": review_verdict,
        "required_review_lenses": required_reviews,
        "required_qa_lenses": required_qa_lenses,
        "missing_for_close": missing_for_close,
        "next_action": next_action,
        "conversation_open_items": open_conversation_items[:10],
        "unresolved_feedback_count": len(unresolved_feedback),
        "unresolved_feedback_ids": unresolved_feedback[:5],
        "effective_close_gate": "micro" if micro_loop else "standard",
        "stale": stale,
        "stale_path": stale_path,
    }


# ── Path sync ────────────────────────────────────────────────────────────


def sync_touched_paths(task_dir, new_paths=None):
    """Merge new paths into touched_paths."""
    st = read_state(task_dir)
    existing = st.get("touched_paths") or []
    incoming = [p for p in (new_paths or []) if p]
    merged = list(dict.fromkeys(existing + incoming))
    set_state_field(task_dir, "touched_paths", merged)
    return merged


def _fingerprint_path(repo_root, relpath):
    """Return a stable fingerprint for current path contents.

    Missing paths use a sentinel so deleted-at-baseline files do not keep
    reappearing as task-owned changes. Symlinks hash their link target without
    following it. Unreadable or unsupported path types fail closed.
    """
    path = os.path.join(repo_root, relpath)
    try:
        path_info = os.lstat(path)
    except FileNotFoundError:
        return "missing"
    except OSError as exc:
        raise RuntimeError("changed path fingerprint unavailable") from exc
    if stat.S_ISDIR(path_info.st_mode):
        return "dir"
    if stat.S_ISLNK(path_info.st_mode):
        try:
            target = os.readlink(path)
            after = os.lstat(path)
        except OSError as exc:
            raise RuntimeError("changed path fingerprint unavailable") from exc
        if (after.st_dev, after.st_ino) != (path_info.st_dev, path_info.st_ino):
            raise RuntimeError("changed path fingerprint unavailable")
        return "symlink-sha256:" + hashlib.sha256(os.fsencode(target)).hexdigest()
    if not stat.S_ISREG(path_info.st_mode):
        raise RuntimeError("changed path fingerprint unavailable")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(path, flags)
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (path_info.st_dev, path_info.st_ino)
        ):
            raise RuntimeError("changed path fingerprint unavailable")
        h = hashlib.sha256()
        handle = os.fdopen(fd, "rb")
        fd = None
        with handle as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
            after = os.fstat(f.fileno())
        final_path = os.lstat(path)
        if (
            after.st_size != opened.st_size
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or not stat.S_ISREG(final_path.st_mode)
            or (final_path.st_dev, final_path.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise RuntimeError("changed path fingerprint unavailable")
        return "sha256:" + h.hexdigest()
    except OSError as exc:
        raise RuntimeError("changed path fingerprint unavailable") from exc
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def _has_git_metadata(repo_root):
    git_path = os.path.join(repo_root, ".git")
    roots = _REQUEST_GIT_ROOTS.get()
    direct = (
        os.path.isfile(git_path)
        or os.path.isfile(os.path.join(git_path, "HEAD"))
        or roots is not None and os.path.realpath(repo_root) in roots
    )
    if direct:
        return True
    if (
        os.path.isfile(os.path.join(repo_root, MANIFEST_PATH))
        and _manifest_array_field(repo_root, "source_git_roots")
    ):
        try:
            return bool(configured_source_git_roots(repo_root, strict=False))
        except RuntimeError:
            return False
    return False


def _task_baseline_required(repo_root):
    """Require baseline evidence for real Git or explicit multi-Git controls."""
    if _has_git_metadata(repo_root):
        return True
    if _manifest_array_field(repo_root, "source_git_roots"):
        # Preserve the strict configuration error (missing/moved/symlinked root)
        # instead of degrading an existing multi-Git task to a non-Git fixture.
        configured_source_git_roots(repo_root, strict=True)
        return True
    return False


def _git_path_snapshot(repo_root, argument, *, use_cache=True):
    cache = _review_snapshot_cache()
    cache_key = ("git_path_snapshot", os.path.realpath(repo_root), argument)
    if use_cache and cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", argument],
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=_trusted_git_env(),
            timeout=_bounded_snapshot_timeout(2, f"git rev-parse {argument}", repo_root),
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: git rev-parse {argument} "
            f"timed out in {repo_root}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: git rev-parse {argument} "
            f"could not run in {repo_root}"
        ) from exc
    value = str(result.stdout or "").strip()
    if result.returncode != 0 or not value or not os.path.isabs(value):
        raise RuntimeError("Git submodule snapshot unavailable")
    value = os.path.abspath(value)
    if use_cache and cache is not None:
        cache[cache_key] = value
    return value


def _validate_submodule_git_metadata(repo_root, sub_root, git_info):
    git_path = os.path.join(sub_root, ".git")
    binding_material = []
    if stat.S_ISDIR(git_info.st_mode):
        if os.path.realpath(git_path) != os.path.abspath(git_path):
            raise RuntimeError("Git submodule snapshot unavailable")
        resolved_gitdir = os.path.abspath(git_path)
        binding_material.append(f"dir:{git_info.st_dev}:{git_info.st_ino}")
    else:
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        fd = None
        try:
            fd = os.open(git_path, flags)
            opened = os.fstat(fd)
            if (
                not stat.S_ISREG(opened.st_mode)
                or (opened.st_dev, opened.st_ino) != (git_info.st_dev, git_info.st_ino)
            ):
                raise RuntimeError("Git submodule snapshot unavailable")
            raw = os.read(fd, 4097)
            after = os.fstat(fd)
            final_path = os.lstat(git_path)
            if (
                len(raw) > 4096
                or after.st_size != opened.st_size
                or after.st_mtime_ns != opened.st_mtime_ns
                or after.st_ctime_ns != opened.st_ctime_ns
                or not stat.S_ISREG(final_path.st_mode)
                or (final_path.st_dev, final_path.st_ino) != (opened.st_dev, opened.st_ino)
            ):
                raise RuntimeError("Git submodule snapshot unavailable")
        except OSError as exc:
            raise RuntimeError("Git submodule snapshot unavailable") from exc
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        line = os.fsdecode(raw).strip()
        if not line.startswith("gitdir: "):
            raise RuntimeError("Git submodule snapshot unavailable")
        target = line[len("gitdir: "):].strip()
        if not target:
            raise RuntimeError("Git submodule snapshot unavailable")
        target = os.path.abspath(
            target if os.path.isabs(target) else os.path.join(sub_root, target)
        )
        target_real = os.path.realpath(target)
        parent_common = _git_path_snapshot(repo_root, "--git-common-dir")
        parent_common_real = os.path.realpath(parent_common)
        try:
            confined = os.path.commonpath([target_real, parent_common_real]) == parent_common_real
        except ValueError:
            confined = False
        if not confined:
            relpath = _canonical_git_relpath(
                os.path.relpath(sub_root, repo_root)
            ).rstrip("/")
            raise GitBindingError(
                "UNREGISTERED_EXTERNAL_GITDIR",
                "Git submodule snapshot unavailable: an unregistered gitlink "
                f"'{relpath}' points outside the parent common directory",
                path=relpath,
                invariant="parent_common_confinement",
                next_action=(
                    "Register the exact direct gitlink in source_git_roots if it is "
                    "an intentional linked worktree, otherwise repair the submodule checkout."
                ),
            )
        if (
            target != target_real
            or parent_common != parent_common_real
            or not os.path.isdir(target)
        ):
            raise RuntimeError("Git submodule snapshot unavailable")
        resolved_gitdir = target_real
        binding_material.append(
            "file:"
            + str(git_info.st_dev)
            + ":"
            + str(git_info.st_ino)
            + ":"
            + hashlib.sha256(raw).hexdigest()
        )

    reported_worktree = _git_path_snapshot(
        sub_root, "--show-toplevel", use_cache=False,
    )
    if os.path.realpath(reported_worktree) != os.path.realpath(sub_root):
        raise RuntimeError("Git submodule snapshot unavailable")
    try:
        final_git = os.lstat(git_path)
    except OSError as exc:
        raise RuntimeError("Git submodule snapshot unavailable") from exc
    if (
        final_git.st_mode != git_info.st_mode
        or (final_git.st_dev, final_git.st_ino) != (git_info.st_dev, git_info.st_ino)
    ):
        raise RuntimeError("Git submodule snapshot unavailable")
    binding_material.append(resolved_gitdir)
    return "|".join(binding_material), resolved_gitdir


def _submodule_metadata_binding(repo_root, sub_root):
    try:
        git_info = os.lstat(os.path.join(sub_root, ".git"))
    except OSError as exc:
        raise RuntimeError("Git submodule snapshot unavailable") from exc
    if stat.S_ISLNK(git_info.st_mode) or not (
        stat.S_ISREG(git_info.st_mode) or stat.S_ISDIR(git_info.st_mode)
    ):
        raise RuntimeError("Git submodule snapshot unavailable")
    return _validate_submodule_git_metadata(repo_root, sub_root, git_info)


def _validated_submodule_root(repo_root, relpath, *, allow_missing=False):
    """Return an initialized submodule root without following worktree symlinks."""
    canonical = _canonical_git_relpath(relpath).rstrip("/")
    if (
        not canonical
        or os.path.isabs(canonical)
        or canonical == ".."
        or canonical.startswith("../")
    ):
        raise RuntimeError("Git submodule snapshot unavailable")
    current = repo_root
    for component in canonical.split("/"):
        if component in ("", ".", ".."):
            raise RuntimeError("Git submodule snapshot unavailable")
        current = os.path.join(current, component)
        try:
            info = os.lstat(current)
        except FileNotFoundError:
            if allow_missing:
                return None, None
            raise RuntimeError("Git submodule snapshot unavailable")
        except OSError as exc:
            raise RuntimeError("Git submodule snapshot unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("Git submodule snapshot unavailable")
    try:
        git_info = os.lstat(os.path.join(current, ".git"))
    except FileNotFoundError:
        if allow_missing:
            return None, None
        raise RuntimeError("Git submodule snapshot unavailable")
    except OSError as exc:
        raise RuntimeError("Git submodule snapshot unavailable") from exc
    if stat.S_ISLNK(git_info.st_mode) or not (
        stat.S_ISREG(git_info.st_mode) or stat.S_ISDIR(git_info.st_mode)
    ):
        raise RuntimeError("Git submodule snapshot unavailable")
    _validate_submodule_git_metadata(repo_root, current, git_info)
    return current, info


def _submodule_gitlink_fingerprint(repo_root, relpath):
    entry = _gitlink_index_snapshot(repo_root).get(relpath)
    if not entry:
        raise RuntimeError("Git submodule snapshot unavailable")
    index_oid, initialized = entry
    if not initialized:
        return f"gitlink:index:{index_oid}:uninitialized"
    sub_root, before = _validated_submodule_root(repo_root, relpath)
    binding_before, git_dir = _submodule_metadata_binding(repo_root, sub_root)
    head = _git_head_snapshot(
        sub_root, git_dir=git_dir, use_cache=False,
    )
    binding_after, _ = _submodule_metadata_binding(repo_root, sub_root)
    if binding_after != binding_before:
        raise RuntimeError("Git submodule snapshot unavailable")
    _validated_submodule_root(repo_root, relpath)
    try:
        after = os.lstat(sub_root)
    except OSError as exc:
        raise RuntimeError("Git submodule snapshot unavailable") from exc
    if (
        not stat.S_ISDIR(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise RuntimeError("Git submodule snapshot unavailable")
    return (
        f"gitlink:index:{index_oid}:checkout:{head}:"
        f"worktree:{after.st_dev}:{after.st_ino}"
    )


def _registered_source_gitlink_fingerprint(repo_root, relpath, index_oid=None):
    """Fingerprint a registered leaf without applying parent confinement."""
    entries = _direct_gitlink_index_entries(repo_root)
    oid = str(index_oid or entries.get(relpath) or "").lower()
    if not oid:
        raise RuntimeError("Git submodule snapshot unavailable")
    source_root = os.path.join(repo_root, *relpath.split("/"))
    before = os.lstat(source_root)
    git_dir = _registered_source_metadata_binding(repo_root, source_root, relpath)
    head = _git_head_snapshot(source_root, git_dir=git_dir, use_cache=False)
    git_dir_after = _registered_source_metadata_binding(repo_root, source_root, relpath)
    after = os.lstat(source_root)
    if (
        os.path.realpath(git_dir_after) != os.path.realpath(git_dir)
        or not stat.S_ISDIR(after.st_mode)
        or stat.S_ISLNK(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_CHANGED",
            f"registered source '{relpath}' changed during gitlink fingerprinting",
            path=relpath,
            invariant="registered_gitlink_identity",
            next_action="Stop concurrent Git/worktree operations and retry.",
        )
    return (
        f"gitlink:index:{oid}:registered-source:checkout:{head}:"
        f"worktree:{after.st_dev}:{after.st_ino}"
    )


def _uncached_git_changed_paths(repo_root, *, git_dir=None):
    """Read changed repository-relative path names from Git once."""
    if _has_git_metadata(repo_root):
        _remember_git_root(repo_root)
    changed = set()
    base = ["git", "-c", f"safe.directory={repo_root}"]
    if git_dir:
        base.extend([f"--git-dir={git_dir}", f"--work-tree={repo_root}"])
    commands = (
        ("working tree diff", base + ["diff", "--name-only", "-z", "HEAD"]),
        ("staged diff", base + ["diff", "--cached", "--name-only", "-z", "HEAD"]),
        ("untracked files", base + ["ls-files", "--others", "--exclude-standard", "-z"]),
    )
    for operation, cmd in commands:
        try:
            timeout = _bounded_snapshot_timeout(
                5, operation, repo_root,
                deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
            )
            r = subprocess.run(
                cmd, capture_output=True, cwd=repo_root, timeout=timeout,
                env=_trusted_git_env(),
            )
        except subprocess.TimeoutExpired as exc:
            if not _has_git_metadata(repo_root):
                return set()
            raise RuntimeError(
                f"Git changed-path snapshot unavailable: {operation} timed out "
                f"after {timeout:.1f}s in {repo_root}"
            ) from exc
        except OSError as exc:
            if not _has_git_metadata(repo_root):
                return set()
            raise RuntimeError(
                f"Git changed-path snapshot unavailable: {operation} could not run "
                f"in {repo_root}"
            ) from exc
        if r.returncode != 0:
            if not _has_git_metadata(repo_root):
                return set()
            raise RuntimeError(
                f"Git changed-path snapshot unavailable: {operation} failed "
                f"in {repo_root}"
            )
        raw_output = r.stdout
        if isinstance(raw_output, bytes):
            paths = (os.fsdecode(item) for item in raw_output.split(b"\0"))
        else:
            paths = str(raw_output or "").split("\0")
        changed.update(path for path in paths if path)
    return changed


def _git_changed_paths(repo_root, prefix="", with_fingerprints=False, *, git_dir=None):
    cache = _review_snapshot_cache()
    binding_key = os.path.realpath(git_dir) if git_dir else ""
    root_key = ("git_changed_path_names", os.path.realpath(repo_root), binding_key)
    if cache is not None and root_key in cache:
        raw_paths = set(cache[root_key])
    else:
        raw_paths = (
            _uncached_git_changed_paths(repo_root, git_dir=git_dir)
            if git_dir
            else _uncached_git_changed_paths(repo_root)
        )
        if cache is not None:
            cache[root_key] = frozenset(raw_paths)

    if not with_fingerprints:
        return {prefix + path for path in raw_paths}

    cache_key = (
        "git_changed_path_fingerprints", os.path.realpath(repo_root), prefix,
        binding_key,
    )
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])
    changed = {
        prefix + path: _fingerprint_path(repo_root, path)
        for path in raw_paths
    }
    if cache is not None:
        cache[cache_key] = dict(changed)
    return changed


def _baseline_file(task_dir):
    return os.path.join(task_dir, TASK_BASELINE_NAME)


def _changed_path_fingerprints(repo_root, *, registered_leaves=(), git_dir=None):
    changed = _git_changed_paths(
        repo_root, with_fingerprints=True, git_dir=git_dir,
    )
    leaves = frozenset(registered_leaves)
    for sub_path, (index_oid, initialized) in _gitlink_index_snapshot(
        repo_root, registered_leaves=leaves, git_dir=git_dir,
    ).items():
        if sub_path in leaves:
            changed[sub_path] = _registered_source_gitlink_fingerprint(
                repo_root, sub_path, index_oid,
            )
            continue
        changed[sub_path] = _submodule_gitlink_fingerprint(repo_root, sub_path)
        if not initialized:
            continue
        sub_root, _ = _validated_submodule_root(repo_root, sub_path)
        changed.update(_git_changed_paths(
            sub_root,
            prefix=sub_path.rstrip("/") + "/",
            with_fingerprints=True,
        ))
        _validated_submodule_root(repo_root, sub_path)
    return changed


def _git_head_snapshot(repo_root, *, git_dir=None, use_cache=True):
    """Return an exact repository HEAD for source snapshot comparison."""
    cache = _review_snapshot_cache()
    cache_key = (
        "git_head_snapshot",
        os.path.realpath(repo_root),
        os.path.realpath(git_dir) if git_dir else "",
    )
    if use_cache and cache is not None and cache_key in cache:
        return cache[cache_key]
    command = ["git"]
    if git_dir:
        command.extend([f"--git-dir={git_dir}", f"--work-tree={repo_root}"])
    command.extend(["rev-parse", "--verify", "HEAD"])
    operation = "git HEAD read"
    timeout = _bounded_snapshot_timeout(
        2, operation, repo_root,
        deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
    )
    try:
        result = subprocess.run(
            command,
            cwd=repo_root,
            capture_output=True,
            text=True,
            env=_trusted_git_env(),
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Git HEAD snapshot unavailable: {operation} timed out after "
            f"{timeout:.1f}s in {repo_root}"
        ) from exc
    except OSError as exc:
        raise RuntimeError(
            f"Git HEAD snapshot unavailable: {operation} could not run "
            f"in {repo_root}: {exc}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Git HEAD snapshot unavailable: {operation} exited "
            f"{result.returncode} in {repo_root}"
        )
    head = result.stdout.strip()
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head):
        raise RuntimeError(
            f"Git HEAD snapshot unavailable: {operation} returned an invalid "
            f"object id in {repo_root}"
        )
    if use_cache and cache is not None:
        cache[cache_key] = head
    return head


def capture_task_baseline(task_dir, repo_root=None):
    """Write task-start dirty-path fingerprints.

    Existing valid baselines are preserved on resume. Git-backed tasks require
    a valid baseline; absence or invalid contents are integrity failures.
    """
    path = _baseline_file(task_dir)
    if os.path.lexists(path):
        _read_task_baseline_snapshot(task_dir, repo_root=repo_root)
        return path
    repo_root = repo_root or find_harness_root(task_dir) or find_repo_root(task_dir)
    if (
        not _has_git_metadata(repo_root)
        and not os.path.isfile(os.path.join(repo_root, MANIFEST_PATH))
    ):
        return ""
    bindings = _workspace_source_bindings(repo_root)
    if not bindings:
        return ""
    source_heads = _workspace_source_heads(repo_root)
    if len(bindings) == 1 and bindings[0][0] == "":
        data = {
            "version": 1,
            "captured_at": now_iso(),
            "repo_root": repo_root,
            "head_sha": source_heads[""],
            "dirty_paths": _changed_path_fingerprints(repo_root),
        }
    else:
        data = {
            "version": 2,
            "captured_at": now_iso(),
            "control_root": repo_root,
            "head_sha": _workspace_head_snapshot(repo_root),
            "source_heads": source_heads,
            "dirty_paths": _workspace_changed_path_fingerprints(repo_root),
            "control_paths": _control_root_behavior_fingerprints(repo_root),
        }
    os.makedirs(task_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=task_dir, prefix=".baseline.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
        try:
            _read_task_baseline_snapshot(task_dir, repo_root=repo_root)
        except Exception:
            try:
                os.unlink(path)
            except OSError:
                pass
            raise
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return path


def _read_task_baseline_snapshot(task_dir, repo_root=None):
    """Read and validate one task baseline without following its leaf."""
    path = _baseline_file(task_dir)
    repo_root = os.path.abspath(
        repo_root or find_harness_root(task_dir) or find_repo_root(task_dir)
    )
    cache = _review_snapshot_cache()
    cache_key = (
        "validated_task_baseline",
        os.path.realpath(task_dir),
        os.path.realpath(repo_root),
    )
    if not os.path.lexists(path):
        if _task_baseline_required(repo_root):
            raise RuntimeError("required task baseline missing")
        return None
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    data = _read_json_file(path, max_size=2 * 1024 * 1024)
    head_sha = str(data.get("head_sha") or "").strip()
    dirty = data.get("dirty_paths")
    control_paths = data.get("control_paths", {})
    version = data.get("version")
    stored_root = str(
        data.get("repo_root") if version == 1 else data.get("control_root") or ""
    )
    valid_paths = isinstance(dirty, dict) and len(dirty) <= 10000 and all(
        isinstance(key, str)
        and key == _canonical_git_relpath(key)
        and key
        and not os.path.isabs(key)
        and key != ".."
        and not key.startswith("../")
        and all(part not in {"", ".", ".."} for part in key.split("/"))
        and isinstance(value, str)
        and bool(re.fullmatch(
            r"(?:missing|dir|(?:sha256|symlink-sha256):[0-9a-f]{64}|gitlink:[A-Za-z0-9:._/-]{1,500})",
            value,
        ))
        for key, value in (dirty.items() if isinstance(dirty, dict) else [])
    )
    valid_control_paths = (
        isinstance(control_paths, dict)
        and set(control_paths).issubset(_CONTROL_ROOT_BEHAVIOR_PATHS)
        and all(
            isinstance(value, str)
            and bool(re.fullmatch(
                r"(?:missing|dir|(?:sha256|symlink-sha256):[0-9a-f]{64})",
                value,
            ))
            for value in control_paths.values()
        )
    )
    if (
        version not in {1, 2}
        or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head_sha)
        or not stored_root
        or not os.path.isabs(stored_root)
        or os.path.realpath(stored_root) != os.path.realpath(repo_root)
        or not valid_paths
        or not valid_control_paths
    ):
        raise RuntimeError("task baseline integrity unavailable")
    if version == 1:
        current_bindings = _workspace_source_bindings(repo_root)
        if current_bindings != [("", os.path.realpath(repo_root))]:
            raise GitBindingError(
                "SOURCE_BINDINGS_CHANGED_RESTART_REQUIRED",
                "This task was started before the current additive source binding set",
                invariant="task_baseline_source_bindings",
                next_action=(
                    "Do not edit TASK_BASELINE.json. Validate the manifest, then start "
                    "a new Harness task ID."
                ),
            )
        source_snapshots = [("", repo_root, head_sha)]
    else:
        source_heads = data.get("source_heads")
        bindings = _workspace_source_bindings(repo_root)
        expected_prefixes = {prefix for prefix, _root in bindings}
        actual_prefixes = set(source_heads) if isinstance(source_heads, dict) else set()
        if isinstance(source_heads, dict) and actual_prefixes != expected_prefixes:
            added = sorted(expected_prefixes - actual_prefixes)
            removed = sorted(actual_prefixes - expected_prefixes)
            raise GitBindingError(
                "SOURCE_BINDINGS_CHANGED_RESTART_REQUIRED",
                "This task was started with a different source binding set "
                f"(added: {added}; removed: {removed})",
                invariant="task_baseline_source_bindings",
                next_action=(
                    "Do not edit TASK_BASELINE.json. Validate the manifest, then start "
                    "a new Harness task ID."
                ),
            )
        if (
            not isinstance(source_heads, dict)
            or any(
                not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", str(value))
                for value in source_heads.values()
            )
            or _composite_source_heads(source_heads) != head_sha.lower()
        ):
            raise RuntimeError("task baseline integrity unavailable")
        source_snapshots = [
            (prefix, source_root, str(source_heads[prefix]))
            for prefix, source_root in bindings
        ]

    for prefix, source_root, source_head in source_snapshots:
        source_before = os.lstat(source_root)
        source_git_dir = None
        if prefix and _nearest_git_root(repo_root) == os.path.realpath(repo_root):
            source_git_dir = _registered_source_metadata_binding(
                repo_root, source_root, prefix.rstrip("/"),
            )
        git_command = ["git"]
        if source_git_dir:
            git_command.extend([
                f"--git-dir={source_git_dir}", f"--work-tree={source_root}",
            ])
        commit_operation = "baseline commit validation"
        if prefix:
            commit_operation += f" ({prefix})"
        commit_timeout = _bounded_snapshot_timeout(
            2, commit_operation, source_root,
            deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
        )
        try:
                commit = subprocess.run(
                git_command + [
                    "rev-parse", "--verify", "--end-of-options",
                    f"{source_head}^{{commit}}",
                ],
                cwd=source_root,
                    capture_output=True,
                    text=True,
                    env=_trusted_git_env(),
                timeout=commit_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {commit_operation} timed "
                f"out after {commit_timeout:.1f}s in {source_root}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {commit_operation} could "
                f"not run in {source_root}: {exc}"
            ) from exc
        if commit.returncode != 0:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {commit_operation} exited "
                f"{commit.returncode} in {source_root}"
            )
        if commit.stdout.strip().lower() != source_head.lower():
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {commit_operation} "
                f"returned a mismatched object id "
                f"in {source_root}"
            )

        ancestor_operation = "baseline ancestry validation"
        if prefix:
            ancestor_operation += f" ({prefix})"
        ancestor_timeout = _bounded_snapshot_timeout(
            2, ancestor_operation, source_root,
            deadline_allowance_seconds=_GIT_ENUMERATION_TIMEOUT_SECONDS,
        )
        try:
                ancestor = subprocess.run(
                git_command + [
                    "merge-base", "--is-ancestor", source_head, "HEAD",
                ],
                cwd=source_root,
                    capture_output=True,
                    text=True,
                    env=_trusted_git_env(),
                timeout=ancestor_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {ancestor_operation} timed "
                f"out after {ancestor_timeout:.1f}s in {source_root}"
            ) from exc
        except OSError as exc:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {ancestor_operation} could "
                f"not run in {source_root}: {exc}"
            ) from exc
        if ancestor.returncode != 0:
            raise RuntimeError(
                f"task baseline Git snapshot unavailable: {ancestor_operation} exited "
                f"{ancestor.returncode} in {source_root}"
            )
        if source_git_dir:
            source_git_dir_after = _registered_source_metadata_binding(
                repo_root, source_root, prefix.rstrip("/"),
            )
            source_after = os.lstat(source_root)
            if (
                os.path.realpath(source_git_dir_after) != os.path.realpath(source_git_dir)
                or not stat.S_ISDIR(source_after.st_mode)
                or stat.S_ISLNK(source_after.st_mode)
                or (source_after.st_dev, source_after.st_ino)
                != (source_before.st_dev, source_before.st_ino)
            ):
                raise GitBindingError(
                    "REGISTERED_WORKTREE_BINDING_CHANGED",
                    f"registered source '{prefix.rstrip('/')}' changed during baseline validation",
                    path=prefix.rstrip("/"),
                    invariant="baseline_source_binding",
                    next_action="Stop concurrent Git/worktree operations and retry.",
                )
    if cache is not None:
        cache[cache_key] = data
    return data


def _read_task_baseline(task_dir):
    baseline = _read_task_baseline_snapshot(task_dir)
    return baseline.get("dirty_paths") if baseline else None


def _filter_baseline_unchanged(task_dir, repo_root, changed):
    baseline = _read_task_baseline(task_dir)
    if baseline is None:
        return changed
    current = _workspace_changed_path_fingerprints(repo_root)
    gitlink_paths = _workspace_gitlink_paths(repo_root)
    out = set()
    for rel in changed:
        if rel not in baseline:
            out.add(rel)
            continue
        current_fp = current.get(rel)
        if current_fp is None:
            if rel.rstrip("/") in gitlink_paths:
                source_root, source_relpath, entry, registered = gitlink_paths[rel.rstrip("/")]
                current_fp = (
                    _registered_source_gitlink_fingerprint(
                        source_root, source_relpath, entry[0],
                    )
                    if registered
                    else _submodule_gitlink_fingerprint(source_root, source_relpath)
                )
            else:
                source_root, source_relpath = _workspace_path_binding(repo_root, rel)
                current_fp = _fingerprint_path(source_root, source_relpath)
        if current_fp != baseline.get(rel):
            out.add(rel)
    return out


def _gitlink_index_snapshot(repo_root, *, registered_leaves=(), git_dir=None):
    registered_leaves = tuple(sorted(set(registered_leaves)))
    cache = _review_snapshot_cache()
    cache_key = (
        "gitlink_index_snapshot",
        os.path.realpath(repo_root),
        registered_leaves,
        os.path.realpath(git_dir) if git_dir else "",
    )
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])

    def walk(worktree, prefix, seen):
        real_worktree = os.path.realpath(worktree)
        if real_worktree in seen:
            raise RuntimeError("Git submodule snapshot unavailable")
        seen.add(real_worktree)
        if _has_git_metadata(worktree):
            _remember_git_root(worktree)
        found = {}
        try:
            direct_entries = _direct_gitlink_index_entries(
                worktree, git_dir=git_dir if not prefix else None,
            )
        except RuntimeError:
            if not _has_git_metadata(worktree):
                return {}
            raise
        for path, oid in direct_entries.items():
            full_path = prefix + path
            if not prefix and path in registered_leaves:
                initialized = os.path.isdir(os.path.join(worktree, path)) and os.path.lexists(
                    os.path.join(worktree, path, ".git")
                )
                found[full_path] = (oid, initialized)
                continue
            sub_root, _ = _validated_submodule_root(
                worktree, path, allow_missing=True,
            )
            initialized = sub_root is not None
            found[full_path] = (oid.lower(), initialized)
            if initialized:
                found.update(walk(sub_root, full_path + "/", seen))
        return found

    out = walk(repo_root, "", set())
    if cache is not None:
        cache[cache_key] = dict(out)
    return out


def _initialized_submodule_paths(repo_root, *, registered_leaves=(), git_dir=None):
    return [
        path for path, (_, initialized) in _gitlink_index_snapshot(
            repo_root, registered_leaves=registered_leaves, git_dir=git_dir,
        ).items()
        if initialized and path not in set(registered_leaves)
    ]


def sync_from_git_diff(task_dir):
    """Sync touched paths from git state.

    Four sources:
      1. Paths committed after the task-start HEAD baseline.
      2. Unstaged modifications (``git diff --name-only HEAD``).
      3. Staged modifications (``git diff --cached --name-only HEAD``).
      4. Untracked-but-not-ignored files (``git ls-files --others --exclude-standard``).

    Untracked inclusion matters for the PR2 stale-verdict check: a new file
    created after ``runtime_verdict: PASS`` must show up in ``touched_paths``
    so mtime comparison can refuse ``task_close``. ``.gitignore`` entries
    stay excluded via ``--exclude-standard``.
    """
    repo_root = find_harness_root(task_dir) or find_repo_root(task_dir)
    changed = set()
    for prefix, source_root in _workspace_source_bindings(repo_root):
        changed.update(
            prefix + path
            for path in _committed_paths_since_baseline(
                task_dir, source_root, workspace_prefix=prefix
            )
        )
    changed.update(_workspace_git_changed_paths(repo_root))
    changed.update(_control_root_changed_paths(task_dir, repo_root))
    changed = _filter_baseline_unchanged(task_dir, repo_root, changed)
    if not changed:
        return []
    return sync_touched_paths(task_dir, changed)


# ── Artifact helpers ─────────────────────────────────────────────────────


def artifact_exists(task_dir, filename):
    return os.path.isfile(os.path.join(task_dir, filename))


def provenance_from_artifacts(task_dir):
    """Derive provenance from artifact existence."""
    has_subagent = artifact_exists(task_dir, SUBAGENT_RECEIPTS_NAME)
    completed = _completed_qa_by_lens(task_dir)
    reviews = _completed_review_by_lens(task_dir)
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
