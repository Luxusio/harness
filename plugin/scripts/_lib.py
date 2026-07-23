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
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"
TASK_BASELINE_NAME = "TASK_BASELINE.json"
TASK_BASELINE_REQUIRED_NAME = "TASK_BASELINE.required"
SUBAGENT_RECEIPTS_NAME = "SUBAGENT_RECEIPTS.jsonl"
REVIEW_RECEIPTS_NAME = "REVIEW_RECEIPTS.jsonl"
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


@contextmanager
def review_snapshot_scope():
    """Reuse source-derived review work only within one caller request."""
    current = _REVIEW_SNAPSHOT_CACHE.get()
    if current is not None:
        yield
        return
    token = _REVIEW_SNAPSHOT_CACHE.set({})
    roots_token = _REQUEST_GIT_ROOTS.set(set())
    try:
        yield
    finally:
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
    final_status = status if status in {"complete", "blocked"} else "complete"
    if final_status == "complete":
        tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
        blockers = []
        if not tasks:
            blockers.append("no child tasks")
        for task in tasks:
            if not isinstance(task, dict):
                blockers.append("invalid child task entry")
                continue
            task_id = str(task.get("task_id") or "")
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
                or state.get("status") != "closed"
                or receipt_runtime_verdict(task_dir, state) != "PASS"
                or runtime_is_stale(task_dir)[0]
            ):
                blockers.append(task_id or "<missing task_id>")
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


def is_harness_enabled_repo(repo_root=None):
    """Return True when a repo has completed harness setup.

    Claude hooks may be installed globally and can run from arbitrary project
    directories. A git root alone is not enough permission to create
    ``doc/harness`` runtime files; setup creates ``doc/harness/manifest.yaml``.
    """
    root = repo_root or find_repo_root()
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


def ensure_task_scaffold(task_dir, task_id, request_text=""):
    """Create task dir with minimal 7-field TASK_STATE.yaml. Preserves existing state on resume."""
    os.makedirs(task_dir, exist_ok=True)
    expected_tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    if os.path.lexists(state_file(task_dir)):
        existing = read_state(task_dir)
        if existing.get("task_id") != expected_tid:
            raise ValueError(
                "existing TASK_STATE.yaml must be a regular file whose task_id matches its canonical directory"
            )
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
    repo_root = find_repo_root(task_dir)
    baseline_path = capture_task_baseline(task_dir, repo_root=repo_root)
    if _has_git_metadata(repo_root) and not baseline_path:
        raise RuntimeError(
            "task baseline capture unavailable; create or restore a valid Git HEAD and retry task_start"
        )
    if baseline_path:
        _atomic_text_write(
            os.path.join(task_dir, TASK_BASELINE_REQUIRED_NAME),
            "version: 1\n",
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
    repo_root = repo_root or find_repo_root()
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
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
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
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
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
    repo_root = find_repo_root(task_dir)
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
        repo_root = find_repo_root(task_dir)
        changed = _committed_paths_since_baseline(task_dir, repo_root)
        changed.update(_git_changed_paths(repo_root))
        out.update(_filter_baseline_unchanged(task_dir, repo_root, changed))
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
        repo_root = find_repo_root(task_dir)
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo_root,
            capture_output=True, text=True, timeout=2,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except Exception:
        return ""


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
    repo_root = find_repo_root(task_dir)
    gitlink_paths = set(_gitlink_index_snapshot(repo_root))
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
    return str(baseline.get("head_sha") or "") if baseline else ""


def _committed_paths_since_baseline(task_dir, repo_root=None):
    """Return repository paths committed after the task baseline."""
    repo_root = repo_root or find_repo_root(task_dir)
    baseline_head = _task_baseline_head_sha(task_dir)
    if not baseline_head:
        return set()
    try:
        result = subprocess.run(
            [
                "git", "diff", "--name-only", "-z", "--no-renames",
                "--end-of-options", baseline_head, "HEAD", "--",
            ],
            cwd=repo_root, capture_output=True, timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("task baseline Git diff unavailable") from exc
    if result.returncode != 0:
        raise RuntimeError("task baseline Git diff unavailable")
    raw = result.stdout if isinstance(result.stdout, bytes) else str(result.stdout or "").encode()
    paths = set()
    for item in raw.split(b"\0"):
        if not item:
            continue
        rel = _canonical_git_relpath(os.fsdecode(item))
        if rel and not os.path.isabs(rel) and rel != ".." and not rel.startswith("../"):
            paths.add(rel)
    return paths


def _path_has_security_signal(task_dir, repo_root, relpath):
    if (
        os.path.basename(str(relpath or "").lower()) in _AGENT_INSTRUCTION_FILES
        or _is_dependency_manifest(relpath)
        or _SECURITY_REVIEW_SIGNAL_RE.search(relpath)
    ):
        return True
    baseline_head = _task_baseline_head_sha(task_dir)
    if not baseline_head:
        # Legacy/corrupt baselines cannot prove that committed deleted lines
        # were inspected. Route the security reviewer rather than granting an
        # unsafe exemption.
        return True
    try:
        result = subprocess.run(
            ["git", "diff", "--unified=0", "--end-of-options", baseline_head, "--", relpath],
            cwd=repo_root, capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout and _SECURITY_REVIEW_SIGNAL_RE.search(result.stdout):
            return True
        if result.returncode != 0:
            return True
    except Exception:
        return True
    path = os.path.join(repo_root, relpath)
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
    repo_root = find_repo_root(task_dir)
    for relpath in paths:
        if _path_has_security_signal(task_dir, repo_root, relpath):
            lenses.append("review-security")
            break
    return lenses


def review_diff_fingerprint(task_dir, state=None):
    """Hash the current task source snapshot, including uncommitted files."""
    st = state or read_state(task_dir)
    cache = _review_snapshot_cache()
    cache_key = (
        "review_fingerprint",
        os.path.realpath(task_dir),
        tuple(sorted(str(path) for path in (st.get("touched_paths") or []))),
    )
    if cache is not None and cache_key in cache:
        return cache[cache_key]
    repo_root = find_repo_root(task_dir)
    gitlink_paths = set(_gitlink_index_snapshot(repo_root))
    h = hashlib.sha256()
    for relpath in _reviewable_source_paths(task_dir, st):
        h.update(os.fsencode(relpath))
        h.update(b"\0")
        if relpath.rstrip("/") in gitlink_paths:
            fingerprint = _submodule_gitlink_fingerprint(repo_root, relpath.rstrip("/"))
        else:
            fingerprint = _fingerprint_path(repo_root, relpath)
        h.update(fingerprint.encode("ascii"))
        h.update(b"\0")
    result = "sha256:" + h.hexdigest()
    if cache is not None:
        cache[cache_key] = result
    return result


def receipt_stream_fingerprint(task_dir):
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
    os.makedirs(task_dir, exist_ok=True)
    payload = (json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, payload)
    finally:
        os.close(fd)
    return entry


def list_subagent_receipts(task_dir):
    path = _subagent_receipts_path(task_dir)
    if not os.path.isfile(path):
        return []
    receipts = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict) and item.get("kind") == "subagent":
                    receipts.append(item)
    except Exception:
        return []
    return receipts


def list_review_receipts(task_dir):
    path = _review_receipts_path(task_dir)
    if not os.path.isfile(path):
        return []
    receipts = []
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                try:
                    item = json.loads(line)
                except Exception:
                    continue
                if isinstance(item, dict) and item.get("kind") == "review":
                    receipts.append(item)
    except Exception:
        return []
    return receipts


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
    repo_root = find_repo_root(task_dir)
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
    valid = {
        lens: completed[lens] for lens in required
        if lens in completed and _qa_started_after_review(task_dir, lens, completed[lens], review_ts)
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
    repo_root = find_repo_root(task_dir)
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    for relpath in touched[:_STALE_CHECK_PATH_CAP]:
        if _stale_skip(relpath):
            continue
        path = os.path.join(repo_root, relpath)
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
    return (
        os.path.isfile(git_path)
        or os.path.isfile(os.path.join(git_path, "HEAD"))
        or roots is not None and os.path.realpath(repo_root) in roots
    )


def _git_path_snapshot(repo_root, argument, *, use_cache=True):
    cache = _review_snapshot_cache()
    cache_key = ("git_path_snapshot", os.path.realpath(repo_root), argument)
    if use_cache and cache is not None and cache_key in cache:
        return cache[cache_key]
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--path-format=absolute", argument],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Git submodule snapshot unavailable") from exc
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
        if (
            target != target_real
            or parent_common != parent_common_real
            or not confined
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


def _uncached_git_changed_paths(repo_root):
    """Read changed repository-relative path names from Git once."""
    if _has_git_metadata(repo_root):
        _remember_git_root(repo_root)
    changed = set()
    commands = (
        ["git", "-c", f"safe.directory={repo_root}", "diff", "--name-only", "-z", "HEAD"],
        ["git", "-c", f"safe.directory={repo_root}", "diff", "--cached", "--name-only", "-z", "HEAD"],
        ["git", "-c", f"safe.directory={repo_root}", "ls-files", "--others", "--exclude-standard", "-z"],
    )
    for cmd in commands:
        try:
            r = subprocess.run(
                cmd, capture_output=True, cwd=repo_root, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if not _has_git_metadata(repo_root):
                return set()
            raise RuntimeError("Git changed-path snapshot unavailable") from exc
        if r.returncode != 0:
            if not _has_git_metadata(repo_root):
                return set()
            raise RuntimeError("Git changed-path snapshot unavailable")
        raw_output = r.stdout
        if isinstance(raw_output, bytes):
            paths = (os.fsdecode(item) for item in raw_output.split(b"\0"))
        else:
            paths = str(raw_output or "").split("\0")
        changed.update(path for path in paths if path)
    return changed


def _git_changed_paths(repo_root, prefix="", with_fingerprints=False):
    cache = _review_snapshot_cache()
    root_key = ("git_changed_path_names", os.path.realpath(repo_root))
    if cache is not None and root_key in cache:
        raw_paths = set(cache[root_key])
    else:
        raw_paths = _uncached_git_changed_paths(repo_root)
        if cache is not None:
            cache[root_key] = frozenset(raw_paths)

    if not with_fingerprints:
        return {prefix + path for path in raw_paths}

    cache_key = ("git_changed_path_fingerprints", os.path.realpath(repo_root), prefix)
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


def _changed_path_fingerprints(repo_root):
    changed = _git_changed_paths(repo_root, with_fingerprints=True)
    for sub_path, (_, initialized) in _gitlink_index_snapshot(repo_root).items():
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
    try:
        result = subprocess.run(
            command,
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Git HEAD snapshot unavailable") from exc
    head = result.stdout.strip() if result.returncode == 0 else ""
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head):
        raise RuntimeError("Git HEAD snapshot unavailable")
    if use_cache and cache is not None:
        cache[cache_key] = head
    return head


def capture_task_baseline(task_dir, repo_root=None):
    """Write task-start dirty-path fingerprints.

    Existing valid baselines are preserved on resume. A missing baseline keeps
    legacy behavior; a present-invalid baseline is an integrity failure.
    """
    path = _baseline_file(task_dir)
    if os.path.lexists(path):
        _read_task_baseline_snapshot(task_dir, repo_root=repo_root)
        return path
    try:
        repo_root = repo_root or find_repo_root(task_dir)
        head_sha = _git_head_for_receipt(task_dir)
        if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head_sha):
            return ""
        data = {
            "version": 1,
            "captured_at": now_iso(),
            "repo_root": repo_root,
            "head_sha": head_sha,
            "dirty_paths": _changed_path_fingerprints(repo_root),
        }
        os.makedirs(task_dir, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=task_dir, prefix=".baseline.", suffix=".tmp")
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
        return path
    except Exception:
        return ""


def _read_task_baseline_snapshot(task_dir, repo_root=None):
    """Read and validate one task baseline without following its leaf."""
    path = _baseline_file(task_dir)
    if not os.path.lexists(path):
        marker = os.path.join(task_dir, TASK_BASELINE_REQUIRED_NAME)
        if os.path.lexists(marker):
            if _read_regular_text_file(marker, max_size=64) != "version: 1\n":
                raise RuntimeError("task baseline requirement marker integrity unavailable")
            raise RuntimeError("required task baseline missing")
        return None
    data = _read_json_file(path, max_size=2 * 1024 * 1024)
    repo_root = os.path.abspath(repo_root or find_repo_root(task_dir))
    head_sha = str(data.get("head_sha") or "").strip()
    dirty = data.get("dirty_paths")
    stored_root = str(data.get("repo_root") or "")
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
    if (
        data.get("version") != 1
        or not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", head_sha)
        or not stored_root
        or not os.path.isabs(stored_root)
        or os.path.realpath(stored_root) != os.path.realpath(repo_root)
        or not valid_paths
    ):
        raise RuntimeError("task baseline integrity unavailable")
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "--verify", "--end-of-options", f"{head_sha}^{{commit}}"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
        ancestor = subprocess.run(
            ["git", "merge-base", "--is-ancestor", head_sha, "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("task baseline Git snapshot unavailable") from exc
    if (
        commit.returncode != 0
        or commit.stdout.strip().lower() != head_sha.lower()
        or ancestor.returncode != 0
    ):
        raise RuntimeError("task baseline Git snapshot unavailable")
    return data


def _read_task_baseline(task_dir):
    baseline = _read_task_baseline_snapshot(task_dir)
    return baseline.get("dirty_paths") if baseline else None


def _filter_baseline_unchanged(task_dir, repo_root, changed):
    baseline = _read_task_baseline(task_dir)
    if baseline is None:
        return changed
    current = _changed_path_fingerprints(repo_root)
    gitlink_paths = set(_gitlink_index_snapshot(repo_root))
    out = set()
    for rel in changed:
        if rel not in baseline:
            out.add(rel)
            continue
        current_fp = current.get(rel)
        if current_fp is None:
            current_fp = (
                _submodule_gitlink_fingerprint(repo_root, rel.rstrip("/"))
                if rel.rstrip("/") in gitlink_paths
                else _fingerprint_path(repo_root, rel)
            )
        if current_fp != baseline.get(rel):
            out.add(rel)
    return out


def _gitlink_index_snapshot(repo_root):
    cache = _review_snapshot_cache()
    cache_key = ("gitlink_index_snapshot", os.path.realpath(repo_root))
    if cache is not None and cache_key in cache:
        return dict(cache[cache_key])

    def walk(worktree, prefix, seen):
        real_worktree = os.path.realpath(worktree)
        if real_worktree in seen:
            raise RuntimeError("Git submodule snapshot unavailable")
        seen.add(real_worktree)
        if _has_git_metadata(worktree):
            _remember_git_root(worktree)
        try:
            result = subprocess.run(
                ["git", "ls-files", "--stage", "-z"],
                capture_output=True, cwd=worktree, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            if not _has_git_metadata(worktree):
                return {}
            raise RuntimeError("Git submodule snapshot unavailable") from exc
        if result.returncode != 0:
            if not _has_git_metadata(worktree):
                return {}
            raise RuntimeError("Git submodule snapshot unavailable")

        found = {}
        raw_output = result.stdout
        records = (
            raw_output.split(b"\0")
            if isinstance(raw_output, bytes)
            else str(raw_output or "").split("\0")
        )
        for record in records:
            if not record:
                continue
            tab = b"\t" if isinstance(record, bytes) else "\t"
            metadata, separator, raw_path = record.partition(tab)
            mode = (
                metadata.split(b" ", 1)[0]
                if isinstance(metadata, bytes)
                else metadata.split(" ", 1)[0]
            )
            if not separator or mode not in (b"160000", "160000"):
                continue
            path = os.fsdecode(raw_path) if isinstance(raw_path, bytes) else raw_path
            path = _canonical_git_relpath(path).rstrip("/")
            if not path or os.path.isabs(path) or path == ".." or path.startswith("../"):
                raise RuntimeError("Git submodule snapshot unavailable")
            full_path = prefix + path
            fields = metadata.split()
            if len(fields) != 3 or fields[2] not in (b"0", "0"):
                raise RuntimeError("Git submodule snapshot unavailable")
            oid = os.fsdecode(fields[1]) if isinstance(fields[1], bytes) else fields[1]
            if not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid):
                raise RuntimeError("Git submodule snapshot unavailable")
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


def _initialized_submodule_paths(repo_root):
    return [
        path for path, (_, initialized) in _gitlink_index_snapshot(repo_root).items()
        if initialized
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
    repo_root = find_repo_root(task_dir)
    changed = _committed_paths_since_baseline(task_dir, repo_root)
    current_changes = _git_changed_paths(repo_root)
    for sub_path in _initialized_submodule_paths(repo_root):
        sub_root, _ = _validated_submodule_root(repo_root, sub_path)
        current_changes.update(_git_changed_paths(
            sub_root, prefix=sub_path.rstrip("/") + "/",
        ))
        _validated_submodule_root(repo_root, sub_path)
    changed.update(current_changes)
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
