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
import subprocess
import tempfile
import json
import hashlib
from datetime import datetime, timezone

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"
TASK_BASELINE_NAME = "TASK_BASELINE.json"

SCHEMA_FIELDS = (
    "task_id", "status", "runtime_verdict",
    "touched_paths", "plan_session_state",
    "closed_at", "updated",
)


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
        out_dir = os.environ.get("HARNESS_GOAL_PAYLOAD_DIR") or os.path.join(repo_root, GOAL_PAYLOAD_DEBUG_DIR)
        os.makedirs(out_dir, exist_ok=True)
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
        with open(path, "w", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
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
        return goal_id if goal_id.startswith("GOAL__") else f"GOAL__{_goal_slug(goal_id)}"
    return f"GOAL__{_goal_slug(objective or 'goal')}"


def _goal_path(repo_root: str, goal_id: str) -> str:
    return os.path.join(repo_root, GOALS_DIR, f"{goal_id}.json")


def _current_goal_path(repo_root: str) -> str:
    return os.path.join(repo_root, GOAL_CURRENT_FILE)


def _read_json_file(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def read_current_goal(repo_root: str | None = None) -> dict:
    root = repo_root or find_repo_root()
    current = _read_json_file(_current_goal_path(root))
    if current.get("goal_id"):
        return current
    return {}


def write_goal_state(repo_root: str, state: dict) -> dict:
    goal_id = str(state.get("goal_id") or _goal_id(objective=str(state.get("objective") or "")))
    state = dict(state)
    state["goal_id"] = goal_id
    state["updated_at"] = now_iso()
    goals_dir = os.path.join(repo_root, GOALS_DIR)
    os.makedirs(goals_dir, exist_ok=True)
    text = json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    for path in (_goal_path(repo_root, goal_id), _current_goal_path(repo_root)):
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
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
        "status": existing.get("status") or "active",
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
    tid = task_id if task_id.startswith("TASK__") else f"TASK__{task_id}"
    tasks = current.get("tasks") if isinstance(current.get("tasks"), list) else []
    updated = False
    for task in tasks:
        if isinstance(task, dict) and task.get("task_id") == tid:
            if title:
                task["title"] = title
            if status:
                task["status"] = status
            if task_dir:
                task["task_dir"] = task_dir
            updated = True
            break
    if not updated:
        tasks.append({
            "task_id": tid,
            "title": title or tid,
            "status": status or "queued",
            "task_dir": task_dir,
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
    if not os.path.isfile(path):
        return result
    for field in SCHEMA_FIELDS:
        if field == "touched_paths":
            result[field] = yaml_array(field, path)
        else:
            result[field] = yaml_field(field, path)
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
    d = os.path.abspath(start_dir or _hook_payload_cwd() or os.getcwd())
    while d != "/":
        if os.path.isdir(os.path.join(d, ".git")):
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


def _normalize_task_id(task_id=None, slug=None, task_dir=None):
    """Derive canonical TASK__<id> from arguments."""
    if task_id:
        return task_id if task_id.startswith("TASK__") else f"TASK__{task_id}"
    if slug:
        return f"TASK__{slug}"
    if task_dir:
        name = os.path.basename(os.path.normpath(task_dir))
        return name if name.startswith("TASK__") else f"TASK__{name}"
    return None


def canonical_task_dir(task_id=None, slug=None, task_dir=None,
                       tasks_dir=TASK_DIR, repo_root=None):
    """Resolve canonical task directory path."""
    repo_root = repo_root or find_repo_root()
    tid = _normalize_task_id(task_id, slug, task_dir)
    if not tid:
        return ""
    return os.path.join(repo_root, tasks_dir, tid)


def canonical_task_id(task_id=None, slug=None, task_dir=None,
                      tasks_dir=TASK_DIR, repo_root=None):
    """Derive canonical task id string."""
    return _normalize_task_id(task_id, slug, task_dir) or ""


# ── Active task markers ─────────────────────────────────────────────────


ACTIVE_SESSIONS_DIRNAME = ".active_sessions"


def _legacy_active_path(repo_root):
    return os.path.join(repo_root, TASK_DIR, ".active")


def _active_sessions_dir(repo_root):
    return os.path.join(repo_root, TASK_DIR, ACTIVE_SESSIONS_DIRNAME)


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
    with open(_legacy_active_path(repo_root), "w", encoding="utf-8") as f:
        f.write(task_dir)


def _read_legacy_active(repo_root):
    path = _legacy_active_path(repo_root)
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            first = (f.read().strip().splitlines() or [""])[0]
    except OSError:
        return ""
    if not first:
        return ""
    if os.path.isabs(first):
        return first
    return os.path.join(repo_root, TASK_DIR, first.rstrip("/"))


def resolve_active_task_dir(repo_root=None, session_id=None):
    """Resolve active task for this session, falling back to legacy ``.active``."""
    repo_root = repo_root or find_repo_root()
    path = _session_active_path(repo_root, session_id)
    if os.path.isfile(path):
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            td = data.get("task_dir")
            if isinstance(td, str) and td:
                return td
        except Exception:
            pass
    return _read_legacy_active(repo_root)


def iter_active_task_dirs(repo_root=None):
    """Yield unique active task dirs from session markers and legacy fallback."""
    repo_root = repo_root or find_repo_root()
    seen = set()
    sessions = _active_sessions_dir(repo_root)
    if os.path.isdir(sessions):
        for name in os.listdir(sessions):
            if not name.endswith(".json"):
                continue
            try:
                with open(os.path.join(sessions, name), encoding="utf-8") as f:
                    td = json.load(f).get("task_dir")
            except Exception:
                continue
            if isinstance(td, str) and td and td not in seen:
                seen.add(td)
                yield td
    legacy = _read_legacy_active(repo_root)
    if legacy and legacy not in seen:
        yield legacy


def clear_active_marker(repo_root, task_dir=None, session_id=None):
    """Clear this session's active marker and matching legacy marker."""
    try:
        os.unlink(_session_active_path(repo_root, session_id))
    except OSError:
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
    if os.path.isfile(state_file(task_dir)):
        existing = read_state(task_dir)
        if existing:
            created = [state_file(task_dir)]
            tid = existing.get("task_id") or _normalize_task_id(task_id, task_dir=task_dir) or task_id
            return {"created": created, "task_dir": task_dir, "task_id": tid}
    tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    fields = {
        "task_id": tid,
        "status": "created",
        "runtime_verdict": "pending",
        "touched_paths": [],
        "plan_session_state": "closed",
        "closed_at": None,
        "updated": now_iso(),
    }
    write_state(task_dir, fields)
    capture_task_baseline(task_dir)

    created = [state_file(task_dir)]
    if request_text:
        req_path = os.path.join(task_dir, "REQUEST.md")
        if not os.path.isfile(req_path):
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(request_text)
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
_CRITIC_DOCUMENT_PASS_RE = re.compile(r"^\s*PASS\s*$", re.MULTILINE)
_UX_VERDICT_RE = re.compile(
    r"^## ux-(cli|api|browser|desktop) verdict: (PASS|FAIL|BLOCKED_ENV|PENDING)\s*$",
    re.MULTILINE,
)
_COMMIT_BACKED_LEARNING_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,3}\s*Commit-backed Learnings\b",
    re.MULTILINE | re.IGNORECASE,
)
_SELF_HEALING_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,3}\s*Self-Healing Candidates\b",
    re.MULTILINE | re.IGNORECASE,
)
_USER_FEEDBACK_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,3}\s*User Feedback Disposition\b",
    re.MULTILINE | re.IGNORECASE,
)
_COMMIT_BACKED_LEARNING_STATUS_RE = re.compile(
    r"^[ \t]{0,3}Status:\s*(none|captured|rejected)\b",
    re.MULTILINE | re.IGNORECASE,
)
_SELF_HEALING_STATUS_RE = re.compile(
    r"^[ \t]{0,3}Status:\s*(none|applied|deferred|rejected)\b",
    re.MULTILINE | re.IGNORECASE,
)
_FENCED_CODE_BLOCK_RE = re.compile(r"(^|\n)[ \t]{0,3}(```|~~~).*?(\n[ \t]{0,3}\2|$)", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)
_COMMIT_BACKED_LEARNING_NEXT_HEADING_RE = re.compile(
    r"^[ \t]{0,3}#{1,3}\s+\S+",
    re.MULTILINE,
)
_COMMIT_BACKED_CAPTURED_LINE_RE = re.compile(
    r"^[ \t]{0,3}[-*]\s*captured:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_SELF_HEALING_APPLIED_LINE_RE = re.compile(
    r"^[ \t]{0,3}[-*]\s*applied:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_SELF_HEALING_DEFERRED_LINE_RE = re.compile(
    r"^[ \t]{0,3}[-*]\s*deferred:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_SELF_HEALING_REJECTED_LINE_RE = re.compile(
    r"^[ \t]{0,3}[-*]\s*rejected:\s*(.+)$",
    re.MULTILINE | re.IGNORECASE,
)
_USER_FEEDBACK_TERMINAL_STATUSES = {"promoted", "handled-local", "deferred", "rejected"}
_USER_FEEDBACK_DISPOSITION_RE = re.compile(
    r"\bevent:\s*([A-Za-z0-9_.:-]+)\b.*?\bstatus:\s*([A-Za-z-]+)\b",
    re.IGNORECASE,
)
_COMMIT_BACKED_CAPTURED_CANDIDATE_LIMIT = 32


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
                stripped = line.strip()
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


def _ux_lens_verdicts(task_dir):
    path = os.path.join(task_dir, "CRITIC__ux.md")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return {}
    return {lens: verdict for lens, verdict in _UX_VERDICT_RE.findall(body)}


def _ux_review_stale(task_dir, touched_paths):
    path = os.path.join(task_dir, "CRITIC__ux.md")
    if not os.path.isfile(path):
        return False, ""
    try:
        critic_mtime = os.path.getmtime(path)
    except OSError:
        return False, ""
    repo_root = find_repo_root(task_dir)
    for rel in (touched_paths or [])[:_STALE_CHECK_PATH_CAP]:
        if _stale_skip(rel):
            continue
        abs_path = rel if os.path.isabs(rel) else os.path.join(repo_root, rel)
        try:
            m = os.path.getmtime(abs_path)
        except OSError:
            continue
        if m > critic_mtime:
            return True, rel
    return False, ""


def _has_req_doc_reference(task_dir, touched_paths):
    """Return True when task artifacts or touched docs reference a durable REQ."""
    repo_root = find_repo_root(task_dir)
    artifact_names = ("PLAN.md", "HANDOFF.md", "DOC_SYNC.md")
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
    """Merge task state paths with current git diff as a close-gate fallback."""
    out = set(touched_paths or [])
    try:
        repo_root = find_repo_root(task_dir)
        out.update(_git_changed_paths(repo_root))
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
    handoff = os.path.join(task_dir, "HANDOFF.md")
    if os.path.isfile(handoff):
        try:
            with open(handoff, encoding="utf-8") as f:
                body = f.read()
            match = re.search(
                r"^[ \t]{0,3}#{1,3}\s*Post-Close User Feedback\b(.*?)(?=^[ \t]{0,3}#{1,3}\s+\S+|\Z)",
                body,
                flags=re.IGNORECASE | re.MULTILINE | re.DOTALL,
            )
            if match:
                texts.append(match.group(1))
        except OSError:
            pass
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


def _feedback_disposition_ids(task_dir):
    """Return event ids terminally disposed in HANDOFF.md."""
    path = os.path.join(task_dir, "HANDOFF.md")
    section = _handoff_section_body(path, _USER_FEEDBACK_HEADING_RE)
    if section is None:
        return set()
    resolved = set()
    for match in _USER_FEEDBACK_DISPOSITION_RE.finditer(section):
        event_id = match.group(1).strip()
        status = match.group(2).strip().lower()
        if status in _USER_FEEDBACK_TERMINAL_STATUSES:
            resolved.add(event_id)
    return resolved


def _unresolved_feedback_event_ids(task_dir):
    events = _feedback_event_ids(task_dir)
    if not events:
        return []
    resolved = _feedback_disposition_ids(task_dir)
    return [event_id for event_id in events if event_id not in resolved]


def _document_critic_status(task_dir, durable_doc_paths):
    """Return (ok, reason) for CRITIC__document.md freshness and PASS verdict."""
    if not durable_doc_paths:
        return True, ""
    path = os.path.join(task_dir, "CRITIC__document.md")
    if not os.path.isfile(path):
        return False, "CRITIC__document.md PASS for durable docs"
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return False, "CRITIC__document.md PASS for durable docs"
    if not _CRITIC_DOCUMENT_PASS_RE.search(body):
        return False, "CRITIC__document.md PASS for durable docs"

    repo_root = find_repo_root(task_dir)
    try:
        critic_mtime = os.path.getmtime(path)
    except OSError:
        return False, "fresh CRITIC__document.md PASS for durable docs"
    for rel in durable_doc_paths:
        abs_path = os.path.join(repo_root, rel)
        try:
            if os.path.getmtime(abs_path) > critic_mtime:
                return False, "fresh CRITIC__document.md PASS for durable docs"
        except OSError:
            continue
    return True, ""


def _has_qa_browser_section(task_dir):
    """Return True if CRITIC__qa.md has a qa-browser header.

    Anchors on ``## qa-browser`` or ``### qa-browser`` at the start of a line
    so prose mentions of ``qa-browser`` in transcripts do not match.
    """
    path = os.path.join(task_dir, "CRITIC__qa.md")
    if not os.path.isfile(path):
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                s = line.lstrip()
                if s.startswith("## qa-browser") or s.startswith("### qa-browser"):
                    return True
    except Exception:
        return False
    return False


def _has_commit_backed_learning_section(task_dir):
    """Return True when HANDOFF records shared-learning disposition.

    Runtime-local `learnings.jsonl` is gitignored staging. Every task handoff must
    say whether reusable learning was absent, captured into committed artifacts,
    or explicitly rejected so future contributors are not asked to rely on a
    private session memory file.
    """
    path = os.path.join(task_dir, "HANDOFF.md")
    section = _handoff_section_body(path, _COMMIT_BACKED_LEARNING_HEADING_RE)
    if section is None:
        return False
    status_match = _COMMIT_BACKED_LEARNING_STATUS_RE.search(section)
    if not status_match:
        return False
    status = status_match.group(1).lower()
    if status != "captured":
        return True
    return _section_names_changed_artifact(
        task_dir, section, _COMMIT_BACKED_CAPTURED_LINE_RE
    )


def _commit_backed_learning_missing_reasons(task_dir):
    """Return precise close-gate reasons for the HANDOFF learning section."""
    path = os.path.join(task_dir, "HANDOFF.md")
    section = _handoff_section_body(path, _COMMIT_BACKED_LEARNING_HEADING_RE)
    if section is None:
        return ["Commit-backed Learnings section in HANDOFF.md"]
    status_match = _COMMIT_BACKED_LEARNING_STATUS_RE.search(section)
    if not status_match:
        return [
            "Commit-backed Learnings Status line in HANDOFF.md",
            "Commit-backed Learnings section in HANDOFF.md",
        ]
    status = status_match.group(1).lower()
    if status != "captured":
        return []
    if _section_names_changed_artifact(task_dir, section, _COMMIT_BACKED_CAPTURED_LINE_RE):
        return []
    return [
        "Commit-backed Learnings captured artifact path in HANDOFF.md",
        "Commit-backed Learnings section in HANDOFF.md",
    ]


def _has_self_healing_candidates_section(task_dir):
    """Return True when HANDOFF records close-time self-healing disposition."""
    path = os.path.join(task_dir, "HANDOFF.md")
    section = _handoff_section_body(path, _SELF_HEALING_HEADING_RE)
    if section is None:
        return False
    status_match = _SELF_HEALING_STATUS_RE.search(section)
    if not status_match:
        return False
    status = status_match.group(1).lower()
    if status == "none":
        return True
    if status == "applied":
        return _section_names_changed_artifact(
            task_dir, section, _SELF_HEALING_APPLIED_LINE_RE
        )
    if status == "deferred":
        if not _SELF_HEALING_DEFERRED_LINE_RE.search(section):
            return False
        lowered = section.lower()
        return (
            "user_decision:" in lowered
            and ("proposed_artifact:" in lowered or "proposed_task:" in lowered)
            and "reason:" in lowered
        )
    if status == "rejected":
        return bool(_SELF_HEALING_REJECTED_LINE_RE.search(section))
    return False


def _self_healing_candidates_missing_reasons(task_dir):
    """Return precise close-gate reasons for the HANDOFF self-healing section."""
    path = os.path.join(task_dir, "HANDOFF.md")
    section = _handoff_section_body(path, _SELF_HEALING_HEADING_RE)
    if section is None:
        return ["Self-Healing Candidates section in HANDOFF.md"]
    status_match = _SELF_HEALING_STATUS_RE.search(section)
    if not status_match:
        return [
            "Self-Healing Candidates Status line in HANDOFF.md",
            "Self-Healing Candidates section in HANDOFF.md",
        ]
    status = status_match.group(1).lower()
    if status == "none":
        return []
    if status == "applied":
        if _section_names_changed_artifact(task_dir, section, _SELF_HEALING_APPLIED_LINE_RE):
            return []
        return [
            "Self-Healing Candidates applied artifact path in HANDOFF.md",
            "Self-Healing Candidates section in HANDOFF.md",
        ]
    if status == "deferred":
        lowered = section.lower()
        if (
            _SELF_HEALING_DEFERRED_LINE_RE.search(section)
            and "user_decision:" in lowered
            and ("proposed_artifact:" in lowered or "proposed_task:" in lowered)
            and "reason:" in lowered
        ):
            return []
        return [
            "Self-Healing Candidates deferred decision fields in HANDOFF.md",
            "Self-Healing Candidates section in HANDOFF.md",
        ]
    if status == "rejected" and _SELF_HEALING_REJECTED_LINE_RE.search(section):
        return []
    return [
        "Self-Healing Candidates valid status body in HANDOFF.md",
        "Self-Healing Candidates section in HANDOFF.md",
    ]


def _handoff_section_body(path, heading_re):
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read()
    except OSError:
        return None
    body = _HTML_COMMENT_RE.sub("\n", _FENCED_CODE_BLOCK_RE.sub("\n", body))
    heading = heading_re.search(body)
    if not heading:
        return None
    remainder = body[heading.end():]
    next_heading = _COMMIT_BACKED_LEARNING_NEXT_HEADING_RE.search(remainder)
    return remainder[:next_heading.start()] if next_heading else remainder


def _section_names_changed_artifact(task_dir, section, item_re):
    repo_root = find_repo_root(task_dir)
    touched = _commit_backed_learning_touched_paths(task_dir)
    touched_candidates = sorted(touched, key=len, reverse=True)
    candidates = []
    for line_match in item_re.finditer(section):
        line = line_match.group(1)
        for rel_path in touched_candidates:
            if rel_path not in line:
                continue
            normalized = _normalize_commit_backed_learning_path(repo_root, rel_path)
            if not normalized:
                continue
            candidates.append(normalized)
            if len(candidates) >= _COMMIT_BACKED_CAPTURED_CANDIDATE_LIMIT:
                break
        if len(candidates) >= _COMMIT_BACKED_CAPTURED_CANDIDATE_LIMIT:
            break
    if not candidates:
        return False
    ignored = _git_ignored_paths(repo_root, candidates)
    return any(path not in ignored for path in candidates)


def _commit_backed_learning_touched_paths(task_dir):
    paths = set()
    st = read_state(task_dir)
    for rel in st.get("touched_paths") or []:
        norm = os.path.normpath(str(rel))
        if norm and norm == str(rel) and not norm.startswith(".."):
            paths.add(norm)
    return paths


def _normalize_commit_backed_learning_path(repo_root, rel_path):
    """Return normalized rel_path when it names a real repo artifact."""
    if not rel_path or os.path.isabs(rel_path):
        return None
    norm = os.path.normpath(rel_path)
    if norm != rel_path or norm in (".", "") or norm.startswith(".."):
        return None
    if norm == "doc/harness/learnings.jsonl" or norm.startswith("doc/harness/tasks/"):
        return None
    abs_path = os.path.realpath(os.path.join(repo_root, norm))
    real_root = os.path.realpath(repo_root)
    if not (abs_path == real_root or abs_path.startswith(real_root + os.sep)):
        return None
    if not os.path.isfile(abs_path):
        return None
    return norm


def _git_ignored_paths(repo_root, rel_paths):
    """Return the subset of rel_paths ignored by git, using one bounded subprocess."""
    paths = [p for p in rel_paths if p]
    if not paths:
        return set()
    try:
        ignored = subprocess.run(
            ["git", "-c", f"safe.directory={repo_root}", "check-ignore", "--stdin"],
            cwd=repo_root,
            input="\n".join(paths) + "\n",
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        )
        if ignored.returncode == 0:
            return {line.strip() for line in ignored.stdout.splitlines() if line.strip()}
    except Exception:
        pass
    return set()


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


def record_attempt(task_dir, kind, verdict, summary, transcript=""):
    """Create attempts/attempt-NNN with JSON metadata and optional transcript."""
    root = _attempts_dir(task_dir)
    os.makedirs(root, exist_ok=True)
    existing = [
        int(m.group(1))
        for name in os.listdir(root)
        for m in [re.match(r"^attempt-(\d{3})$", name)]
        if m
    ]
    idx = (max(existing) + 1) if existing else 1
    attempt_id = f"attempt-{idx:03d}"
    adir = os.path.join(root, attempt_id)
    os.makedirs(adir, exist_ok=False)
    meta = {
        "id": attempt_id,
        "ts": now_iso(),
        "kind": str(kind or "retry"),
        "verdict": str(verdict or "unknown").upper(),
        "summary": str(summary or "")[:1000],
    }
    _atomic_json_write(os.path.join(adir, "attempt.json"), meta)
    if transcript:
        with open(os.path.join(adir, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write(str(transcript))
            if not str(transcript).endswith("\n"):
                f.write("\n")
    with open(os.path.join(adir, "SUMMARY.md"), "w", encoding="utf-8") as f:
        f.write(
            f"# {attempt_id}\n\n"
            f"kind: {meta['kind']}\n\n"
            f"verdict: {meta['verdict']}\n\n"
            f"summary: {meta['summary']}\n"
        )
    return meta


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


# ── Task context ─────────────────────────────────────────────────────────


# ── Runtime-verdict staleness check ─────────────────────────────────────
#
# A frozen `runtime_verdict` (PASS / BLOCKED_ENV) must NOT permit close or
# stop if any tracked file has been modified after the verdict was written
# to `CRITIC__qa.md`. PR2 introduced this gate for `task_close`; AC-001
# of TASK__stop-gate-stale-blocked-env-fix extends it to the Stop hook.
#
# Skip lists below cover churn that doesn't reflect a real code change
# (Python caches, OS metadata, editor swap files). Without the skip,
# `__pycache__/*.pyc` touches would falsely stale every verdict.

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
    "HANDOFF.md",
    "DOC_SYNC.md",
    "CRITIC__qa.md",
    "CRITIC__document.md",
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
    norm = relpath.replace("\\", "/").lstrip("./")
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
    """Return ``(stale, offending_path)``.

    Stale when any file in ``touched_paths`` has ``mtime > mtime(CRITIC__qa.md)``.
    Skips Python caches / OS metadata per ``_STALE_CHECK_SKIP_*``. If
    ``CRITIC__qa.md`` is absent the caller is expected to be blocked by the
    ``runtime_verdict PASS`` / ``BLOCKED_ENV`` precondition; return
    ``(False, "")`` so this helper does not double-fire.
    """
    critic_path = os.path.join(task_dir, "CRITIC__qa.md")
    if not os.path.isfile(critic_path):
        return False, ""
    try:
        critic_mtime = os.path.getmtime(critic_path)
    except OSError:
        return False, ""

    st = read_state(task_dir)
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])
    if not touched:
        return False, ""

    repo_root = find_repo_root()
    for rel in touched[:_STALE_CHECK_PATH_CAP]:
        if _stale_skip(rel):
            continue
        abs_path = rel if os.path.isabs(rel) else os.path.join(repo_root, rel)
        try:
            m = os.path.getmtime(abs_path)
        except OSError:
            # Deleted paths can be part of a verified diff. There is no mtime
            # to compare, so they must not make a fresh QA verdict stale forever.
            continue
        if m > critic_mtime:
            return True, rel
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
    runtime_verdict = (st.get("runtime_verdict") or "pending").upper()
    touched = _effective_touched_paths(task_dir, st.get("touched_paths") or [])

    micro_loop = _is_micro_loop_state(st)
    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_write_allowed = has_plan or micro_loop
    why_blocked = "" if source_write_allowed else "PLAN.md does not exist yet"

    has_handoff = artifact_exists(task_dir, "HANDOFF.md")

    missing_for_close = []
    if not has_plan and not micro_loop:
        missing_for_close.append("PLAN.md")
    if not has_handoff:
        missing_for_close.append("HANDOFF.md")
    else:
        missing_for_close.extend(_commit_backed_learning_missing_reasons(task_dir))
        missing_for_close.extend(_self_healing_candidates_missing_reasons(task_dir))
    if runtime_verdict != "PASS":
        missing_for_close.append("runtime_verdict PASS")

    # AC-002: browser-QA close gate (2026-05-12 retro).
    # When manifest declares browser_qa_supported and touched paths include
    # frontend files, refuse to close until CRITIC__qa.md has a qa-browser
    # section. Prevents qa-api-only PASS verdicts on UI-bearing diffs.
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
    if browser_supported and frontend_touched:
        critic_path = os.path.join(task_dir, "CRITIC__qa.md")
        if os.path.isfile(critic_path) and not _has_qa_browser_section(task_dir):
            missing_for_close.append("qa-browser evidence in CRITIC__qa.md")

    required_ux_lenses = _required_ux_lenses(repo_root, touched)
    if required_ux_lenses:
        ux_verdicts = _ux_lens_verdicts(task_dir)
        ux_stale, _ux_stale_path = _ux_review_stale(task_dir, touched)
        for lens in required_ux_lenses:
            if ux_verdicts.get(lens) != "PASS" or ux_stale:
                missing_for_close.append(f"ux-{lens} PASS in CRITIC__ux.md")

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

    doc_critic_ok, doc_critic_reason = _document_critic_status(task_dir, durable_doc_paths)
    if not doc_critic_ok:
        missing_for_close.append(doc_critic_reason)
    if unresolved_feedback:
        missing_for_close.append("User feedback disposition in HANDOFF.md")

    if not has_plan and not micro_loop:
        next_action = "Create PLAN.md via plan skill before source writes."
    elif micro_loop and not has_handoff:
        next_action = (
            "Micro-loop active — PLAN.md is exempt under execution_mode='micro' "
            "(REQ durable-doc gate still applies). Develop, write HANDOFF.md, "
            "then verify. Verification is still required."
        )
    elif runtime_verdict != "PASS":
        next_action = "Run task_verify to check runtime verification."
    elif any(m.startswith("REQ durable doc") for m in missing_for_close):
        next_action = (
            "Create or update a doc/<area>/REQ__*.md for the observable "
            "behavior before source work. Use write_req_doc or req_scaffold.py, "
            "then link it from PLAN.md or HANDOFF.md."
        )
    elif any(m.startswith("CRITIC__document.md") or m.startswith("fresh CRITIC__document.md")
             for m in missing_for_close):
        next_action = (
            "Run the critic-document agent on changed durable docs and write "
            "CRITIC__document.md with write_critic_document."
        )
    elif "User feedback disposition in HANDOFF.md" in missing_for_close:
        feedback_ids = ", ".join(unresolved_feedback)
        next_action = (
            "Review USER_FEEDBACK.jsonl before the next dependent action. Reflect "
            "each event in code/docs/tests, defer it, or reject it, then add "
            "`## User Feedback Disposition` lines to HANDOFF.md using "
            "`event: <id> status: promoted|handled-local|deferred|rejected`. "
            f"Unresolved feedback ids: {feedback_ids}."
        )
    elif "qa-browser evidence in CRITIC__qa.md" in missing_for_close:
        next_action = ("Spawn Agent(subagent_type='harness:qa-browser', ...) "
                       "and call write_critic_qa with lens='browser'.")
    elif any(m.startswith("ux-") and m.endswith("PASS in CRITIC__ux.md")
             for m in missing_for_close):
        next_action = (
            "Run the required ux-* review lens and call write_critic_ux with "
            "lens='cli', 'api', 'browser', or 'desktop'."
        )
    elif any(m.startswith("Commit-backed Learnings") for m in missing_for_close):
        next_action = (
            "Rewrite HANDOFF.md via write_handoff, preserving existing content, "
            "and fix Commit-backed Learnings. Required shape: "
            "`## Commit-backed Learnings`, `Status: none|captured|rejected`. "
            "When captured, include `- captured: <changed commit-eligible path> "
            "- <rule/fact>`; doc/harness/learnings.jsonl, task-local files, "
            "ignored files, nonexistent files, and untouched existing files do "
            f"not count. Missing: {', '.join(m for m in missing_for_close if m.startswith('Commit-backed Learnings'))}."
        )
    elif any(m.startswith("Self-Healing Candidates") for m in missing_for_close):
        next_action = (
            "Rewrite HANDOFF.md via write_handoff, preserving existing content, "
            "and fix Self-Healing Candidates. Required shape: "
            "`## Self-Healing Candidates`, `Status: none|applied|deferred|rejected`. "
            "When applied, include `- applied: <failure mode> - <changed "
            "commit-eligible path>`; when deferred, include a `- deferred:` item "
            "plus `user_decision:`, `reason:`, and `proposed_artifact:` or "
            "`proposed_task:`; rejected items need `- rejected: <candidate> - "
            f"<reason>`. Missing: {', '.join(m for m in missing_for_close if m.startswith('Self-Healing Candidates'))}."
        )
    else:
        next_action = "Runtime verdict PASS — run task_close."

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
        "missing_for_close": missing_for_close,
        "next_action": next_action,
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
    reappearing as task-owned changes. Hash failures return ``None`` and are
    treated as changed by the baseline filter.
    """
    path = os.path.join(repo_root, relpath)
    if not os.path.exists(path):
        return "missing"
    if os.path.isdir(path):
        return "dir"
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return "sha256:" + h.hexdigest()
    except OSError:
        return None


def _git_changed_paths(repo_root, prefix="", with_fingerprints=False):
    changed = {} if with_fingerprints else set()
    commands = (
        ["git", "-c", f"safe.directory={repo_root}", "diff", "--name-only", "HEAD"],
        ["git", "-c", f"safe.directory={repo_root}", "diff", "--cached", "--name-only", "HEAD"],
        ["git", "-c", f"safe.directory={repo_root}", "ls-files", "--others", "--exclude-standard"],
    )
    for cmd in commands:
        r = subprocess.run(cmd, capture_output=True, text=True, cwd=repo_root)
        if r.returncode == 0:
            for line in r.stdout.splitlines():
                path = line.strip()
                if path:
                    rel = (prefix + path).replace("\\", "/")
                    if with_fingerprints:
                        changed[rel] = _fingerprint_path(repo_root, path)
                    else:
                        changed.add(rel)
    return changed


def _baseline_file(task_dir):
    return os.path.join(task_dir, TASK_BASELINE_NAME)


def _changed_path_fingerprints(repo_root):
    changed = _git_changed_paths(repo_root, with_fingerprints=True)
    for sub_path in _initialized_submodule_paths(repo_root):
        sub_root = os.path.join(repo_root, sub_path)
        if os.path.isdir(sub_root):
            changed.update(_git_changed_paths(
                sub_root,
                prefix=sub_path.rstrip("/") + "/",
                with_fingerprints=True,
            ))
    return changed


def capture_task_baseline(task_dir, repo_root=None):
    """Write task-start dirty-path fingerprints.

    Existing baselines are preserved on resume. Failure is non-blocking:
    tasks without a readable baseline fall back to historical touched-path
    behavior in ``sync_from_git_diff``.
    """
    path = _baseline_file(task_dir)
    if os.path.isfile(path):
        return path
    try:
        repo_root = repo_root or find_repo_root(task_dir)
        data = {
            "version": 1,
            "captured_at": now_iso(),
            "repo_root": repo_root,
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


def _read_task_baseline(task_dir):
    try:
        with open(_baseline_file(task_dir), encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    dirty = data.get("dirty_paths")
    return dirty if isinstance(dirty, dict) else None


def _filter_baseline_unchanged(task_dir, repo_root, changed):
    baseline = _read_task_baseline(task_dir)
    if baseline is None:
        return changed
    current = _changed_path_fingerprints(repo_root)
    out = set()
    for rel in changed:
        if rel not in baseline:
            out.add(rel)
            continue
        current_fp = current.get(rel)
        if current_fp is None or current_fp != baseline.get(rel):
            out.add(rel)
    return out


def _initialized_submodule_paths(repo_root):
    r = subprocess.run(
        ["git", "submodule", "status", "--recursive"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if r.returncode != 0:
        return []
    out = []
    for line in r.stdout.splitlines():
        # Leading '-' means registered but not initialized.
        if not line or line[0] == "-":
            continue
        parts = line.strip().split()
        if len(parts) >= 2:
            path = parts[1].strip()
            if path and not path.startswith("-"):
                out.append(path.rstrip("/"))
    return out


def sync_from_git_diff(task_dir):
    """Sync touched paths from git state.

    Three sources:
      1. Unstaged modifications (``git diff --name-only HEAD``).
      2. Staged modifications (``git diff --cached --name-only HEAD``).
      3. Untracked-but-not-ignored files (``git ls-files --others --exclude-standard``).

    Untracked inclusion matters for the PR2 stale-verdict check: a new file
    created after ``runtime_verdict: PASS`` must show up in ``touched_paths``
    so mtime comparison can refuse ``task_close``. ``.gitignore`` entries
    stay excluded via ``--exclude-standard``.
    """
    repo_root = find_repo_root(task_dir)
    changed = _git_changed_paths(repo_root)
    for sub_path in _initialized_submodule_paths(repo_root):
        sub_root = os.path.join(repo_root, sub_path)
        if os.path.isdir(sub_root):
            changed.update(_git_changed_paths(sub_root, prefix=sub_path.rstrip("/") + "/"))
    changed = _filter_baseline_unchanged(task_dir, repo_root, changed)
    if not changed:
        return []
    return sync_touched_paths(task_dir, changed)


# ── Artifact helpers ─────────────────────────────────────────────────────


def artifact_exists(task_dir, filename):
    return os.path.isfile(os.path.join(task_dir, filename))


def provenance_from_artifacts(task_dir):
    """Derive provenance from artifact existence."""
    return {
        agent: artifact_exists(task_dir, fn)
        for agent, fn in {
            "plan-skill": "PLAN.md",
            "developer": "HANDOFF.md",
            "qa-browser": "CRITIC__qa.md",
            "qa-api": "CRITIC__qa.md",
            "qa-cli": "CRITIC__qa.md",
            "qa-desktop": "CRITIC__qa.md",
            "ux-browser": "CRITIC__ux.md",
            "ux-api": "CRITIC__ux.md",
            "ux-cli": "CRITIC__ux.md",
            "ux-desktop": "CRITIC__ux.md",
        }.items()
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
