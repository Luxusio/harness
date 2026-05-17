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
from datetime import datetime, timezone

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"

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
    d = os.path.abspath(start_dir or os.getcwd())
    while d != "/":
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.abspath(start_dir or os.getcwd())


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


def is_maintenance_task(task_dir, repo_root=None):
    if os.path.isfile(os.path.join(task_dir, "MAINTENANCE")):
        return True
    return str(read_manifest_field("maintenance_default", repo_root) or "").lower() == "true"


# ── Routing (on-the-fly, never stored) ───────────────────────────────────


def compile_routing(task_dir, repo_root=None):
    repo_root = repo_root or find_repo_root()
    maintenance = is_maintenance_task(task_dir, repo_root)
    return {
        "maintenance_task": maintenance,
        "workflow_locked": not maintenance,
        "risk_level": "high" if maintenance else "medium",
        "execution_mode": "standard",
        "orchestration_mode": "solo",
        "planning_mode": "standard",
    }


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
_STALE_CHECK_PATH_CAP = 1000  # bound mtime scan in pathological cases


def _stale_skip(relpath: str) -> bool:
    if not relpath:
        return True
    for suf in _STALE_CHECK_SKIP_SUFFIXES:
        if relpath.endswith(suf):
            return True
    for frag in _STALE_CHECK_SKIP_FRAGMENTS:
        if frag in relpath or relpath.endswith(frag.strip("/")):
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
    touched = st.get("touched_paths") or []
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
    touched = st.get("touched_paths") or []

    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_write_allowed = has_plan
    why_blocked = "" if source_write_allowed else "PLAN.md does not exist yet"

    has_handoff = artifact_exists(task_dir, "HANDOFF.md")

    missing_for_close = []
    if not has_plan:
        missing_for_close.append("PLAN.md")
    if not has_handoff:
        missing_for_close.append("HANDOFF.md")
    if runtime_verdict != "PASS":
        missing_for_close.append("runtime_verdict PASS")

    # AC-002: browser-QA close gate (2026-05-12 retro).
    # When manifest declares browser_qa_supported and touched paths include
    # frontend files, refuse to close until CRITIC__qa.md has a qa-browser
    # section. Prevents qa-api-only PASS verdicts on UI-bearing diffs.
    repo_root = find_repo_root()
    try:
        browser_supported = (_read_nested_manifest_field(
            repo_root, "qa", "browser_qa_supported") or "").lower() == "true"
    except Exception:
        browser_supported = False
    if browser_supported and _frontend_touched(touched):
        critic_path = os.path.join(task_dir, "CRITIC__qa.md")
        if os.path.isfile(critic_path) and not _has_qa_browser_section(task_dir):
            missing_for_close.append("qa-browser evidence in CRITIC__qa.md")

    if not has_plan:
        next_action = "Create PLAN.md via plan skill before source writes."
    elif runtime_verdict != "PASS":
        next_action = "Run task_verify to check runtime verification."
    elif "qa-browser evidence in CRITIC__qa.md" in missing_for_close:
        next_action = ("Spawn Agent(subagent_type='harness:qa-browser', ...) "
                       "and call write_critic_qa with lens='browser'.")
    else:
        next_action = "Runtime verdict PASS — run task_close."

    stale, stale_path = runtime_is_stale(task_dir)

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
        "missing_for_close": missing_for_close,
        "next_action": next_action,
        "effective_close_gate": "standard",
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
    changed = set()
    # 1. Unstaged modifications
    r1 = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if r1.returncode == 0:
        changed.update(f.strip() for f in r1.stdout.splitlines() if f.strip())
    # 2. Staged modifications (git add'd but not committed)
    r2 = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if r2.returncode == 0:
        changed.update(f.strip() for f in r2.stdout.splitlines() if f.strip())
    # 3. Untracked files (respects .gitignore via --exclude-standard)
    r3 = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if r3.returncode == 0:
        changed.update(f.strip() for f in r3.stdout.splitlines() if f.strip())
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
