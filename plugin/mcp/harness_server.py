#!/usr/bin/env python3
"""harness MCP server — self-contained, 7-field TASK_STATE.

No plugin-legacy dependency. All operations are direct file I/O.
7 MCP tools: task_start, task_context, task_verify, task_close,
             write_critic_qa,
             write_handoff, write_doc_sync.
"""

from __future__ import annotations
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
SERVER_INFO = {"name": "harness", "title": "harness Control Plane", "version": "2.0.0"}

sys.path.insert(0, str(SCRIPTS_DIR))
from _lib import (  # type: ignore
    now_iso, read_state, write_state, set_state_field,
    ensure_task_scaffold, emit_compact_context, sync_from_git_diff,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_repo_root,
)
try:
    from environment_snapshot import snapshot as _env_snapshot  # type: ignore
except Exception:
    _env_snapshot = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _ok(d: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(d, indent=2, ensure_ascii=False)}],
            "structuredContent": d}


def _err(m: str, data: dict | None = None) -> dict:
    p: dict[str, Any] = {"error": m}
    p.update(data or {})
    return {"content": [{"type": "text", "text": json.dumps(p, indent=2, ensure_ascii=False)}],
            "structuredContent": p, "isError": True}


def _req(args: dict, k: str) -> str:
    v = args.get(k)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{k} required")
    return v


def _opt(args: dict, k: str) -> str | None:
    v = args.get(k)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _task_artifact_rel(td: str, fn: str) -> str:
    return f"doc/harness/tasks/{os.path.basename(td)}/{fn}" if artifact_exists(td, fn) else ""


# ── PR2 close-gate helpers ──────────────────────────────────────────────


# Extensions / path fragments skipped during runtime-stale mtime scan.
# These churn without reflecting a real code change (Python caches, macOS
# metadata, editor swap files). Including them would produce false-positive
# stale verdicts.
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


def _runtime_is_stale(td: str) -> tuple[bool, str]:
    """Return (stale, offending_path).

    Stale when any file in ``touched_paths`` has ``mtime > mtime(CRITIC__qa.md)``.
    Skips Python caches / OS metadata per ``_STALE_CHECK_SKIP_*`` so generated
    churn doesn't invalidate a legitimate PASS. If ``CRITIC__qa.md`` is
    absent the caller should already be blocked by the ``runtime_verdict PASS``
    gate; return ``(False, "")`` here so we don't double-fire.
    """
    critic_path = os.path.join(td, "CRITIC__qa.md")
    if not os.path.isfile(critic_path):
        return False, ""
    try:
        critic_mtime = os.path.getmtime(critic_path)
    except OSError:
        return False, ""

    st = read_state(td)
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
            # File was deleted / renamed since last sync. Treat as stale;
            # the next task_verify will re-sync and prune the entry.
            return True, rel
        if m > critic_mtime:
            return True, rel
    return False, ""


def _parse_checks_yaml(td: str) -> list[dict] | None:
    """Parse CHECKS.yaml into [{id, status, title}, ...].

    Returns ``None`` when the file is missing (pre-PR2 task compatibility);
    caller warn-logs and proceeds. Returns ``[]`` when the file is present
    but empty or unparseable — treat as same as missing after logging. Uses
    block-scanning so we don't pull in PyYAML; matches the
    ``update_checks.py`` parser shape.
    """
    checks_path = os.path.join(td, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return None
    try:
        with open(checks_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    import re
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^-\s+id:\s*", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    items: list[dict] = []
    for block in blocks:
        m_id = re.match(r"^-\s+id:\s*(\S+)", block)
        m_status = re.search(r"^\s+status:\s*(\S+)", block, re.MULTILINE)
        m_title = re.search(r'^\s+title:\s*"?(.*?)"?\s*$', block, re.MULTILINE)
        if not m_id:
            continue
        title = (m_title.group(1) if m_title else "").strip().strip('"').strip("'")
        if len(title) > 120:
            title = title[:117] + "..."
        items.append({
            "id": m_id.group(1),
            "status": (m_status.group(1) if m_status else "open").strip(),
            "title": title,
        })
    return items


_CHECKS_GATE_TERMINAL = {"passed", "deferred"}


def _checks_gate_status(td: str) -> tuple[str, list[dict]]:
    """Return (``"ok"``|``"blocked"``|``"absent"``, blocking_acs).

    - ``ok``: CHECKS.yaml present, every AC in {passed, deferred}.
    - ``blocked``: CHECKS.yaml present, at least one AC not terminal.
      ``blocking_acs`` is the non-terminal subset (id, status, title).
    - ``absent``: CHECKS.yaml missing — caller warn-logs and proceeds.
    """
    items = _parse_checks_yaml(td)
    if items is None:
        return "absent", []
    if not items:
        return "absent", []
    blocking = [ac for ac in items if ac["status"] not in _CHECKS_GATE_TERMINAL]
    return ("blocked" if blocking else "ok"), blocking


def _log_gate_warn(task_id: str, key: str, insight: str) -> None:
    """Append a one-line gate-warn entry to doc/harness/learnings.jsonl."""
    try:
        import json as _json
        repo_root = find_repo_root()
        learn = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-warn",
            "source": "task_close",
            "key": key,
            "insight": insight,
            "task_id": task_id,
        })
        with open(learn, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _resolve_td(args: dict) -> str:
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id")
    if ti:
        return canonical_task_dir(task_id=ti)
    if td:
        return td
    raise ValueError("task_id or task_dir required")


# ── Tool handlers ────────────────────────────────────────────────────────


def handle_task_start(args: dict) -> dict:
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id")
    sl = _opt(args, "slug")
    rf = _opt(args, "request_file")
    if not td and not ti and not sl:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    repo_root = find_repo_root()
    task_dir = td or canonical_task_dir(task_id=ti, slug=sl, repo_root=repo_root)
    tid = canonical_task_id(task_id=ti, slug=sl, task_dir=task_dir)

    request_text = ""
    if rf:
        rp = rf if os.path.isabs(rf) else os.path.join(repo_root, rf)
        if os.path.isfile(rp):
            try:
                with open(rp, "r", encoding="utf-8") as f:
                    request_text = f.read()
            except OSError:
                pass

    ensure_task_scaffold(task_dir, tid, request_text=request_text)

    # Write .active marker so prewrite_gate can enforce plan-first
    active_file = os.path.join(repo_root, "doc", "harness", "tasks", ".active")
    os.makedirs(os.path.dirname(active_file), exist_ok=True)
    with open(active_file, "w", encoding="utf-8") as f:
        f.write(task_dir)

    # Best-effort environment snapshot: probe failure must never block task_start.
    snapshot_path = ""
    if _env_snapshot is not None:
        try:
            snapshot_path = _env_snapshot(task_dir, repo_root) or ""
        except Exception:
            snapshot_path = ""

    ctx = emit_compact_context(task_dir)
    if "error" in ctx:
        return _err("task_start failed", data={"task_dir": task_dir})
    return _ok({
        "task_dir": task_dir, "task_id": tid, "task_context": ctx,
        "environment_snapshot": snapshot_path,
    })


def handle_task_context(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    ctx = emit_compact_context(td)
    if "error" in ctx:
        return _err("task_context failed", data=ctx)
    return _ok({"task_dir": td, "task_context": ctx})


def handle_task_verify(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    sync_from_git_diff(td)

    # Stale check: if any touched path is newer than CRITIC__qa.md,
    # revert the stored verdict to pending so task_close won't accept a
    # frozen PASS. task_verify is the natural place to clear the bit —
    # re-running QA re-writes CRITIC__qa.md with a fresh mtime.
    stale, stale_path = _runtime_is_stale(td)
    if stale:
        st = read_state(td)
        if (st.get("runtime_verdict") or "").upper() == "PASS":
            set_state_field(td, "runtime_verdict", "pending")

    st = read_state(td)
    rv = (st.get("runtime_verdict") or "pending").upper()
    ctx = emit_compact_context(td)
    return _ok({
        "task_dir": td, "runtime_verdict": rv,
        "touched_paths": st.get("touched_paths") or [],
        "next_action": ctx.get("next_action", ""),
        "missing_for_close": ctx.get("missing_for_close", []),
        "report_path": _task_artifact_rel(td, "CRITIC__qa.md"),
        "stale": stale,
        "stale_path": stale_path,
    })


def handle_task_close(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    sync_from_git_diff(td)
    ctx = emit_compact_context(td)
    missing = ctx.get("missing_for_close") or []
    if missing:
        return _err("task_close blocked", data={
            "task_dir": td, "missing_for_close": missing, "task_context": ctx,
        })

    # PR2 runtime-stale gate: refuse close when a touched path is newer
    # than CRITIC__qa.md. Caller must re-run task_verify so QA can
    # re-issue a fresh PASS.
    stale, stale_path = _runtime_is_stale(td)
    if stale:
        return _err("task_close blocked: runtime_verdict stale — re-run task_verify", data={
            "task_dir": td, "stale_path": stale_path,
        })

    # PR2 CHECKS gate: refuse close when any AC is non-terminal.
    # Absent CHECKS.yaml → warn-log + proceed (pre-PR2 tasks).
    checks_status, blocking = _checks_gate_status(td)
    if checks_status == "blocked":
        return _err("task_close blocked: CHECKS gate", data={
            "task_dir": td, "blocking_acs": blocking,
        })
    if checks_status == "absent":
        # CHECKS.yaml absent → proceed silently for pre-PR2 task compatibility.
        # Do not pollute learnings.jsonl — runtime alerts are not learnings.
        pass

    st = read_state(td)
    st["status"] = "closed"
    st["closed_at"] = now_iso()
    st["updated"] = now_iso()
    write_state(td, st)

    # Clean up .active marker
    active_file = os.path.join(find_repo_root(), "doc", "harness", "tasks", ".active")
    try:
        if os.path.isfile(active_file):
            os.remove(active_file)
    except OSError:
        pass
    st = read_state(td)
    return _ok({
        "task_dir": td, "closed": True, "status": st.get("status"),
        "gate_artifact": _task_artifact_rel(td, "HANDOFF.md"),
    })


def _write_artifact(args: dict, filename: str, verdict_field: str | None = None,
                    verdict_value: str | None = None) -> dict:
    """Common artifact write: create file, optionally update verdict. Atomic.

    AC-006 (2026-05-12 retro): when called for CRITIC__qa.md, append a
    `## Manual UX verification` section after the standard content. The section
    content is taken from ``args["manual_ux_verification"]``; an empty value
    renders a `_NOT SUPPLIED_` placeholder (handled by handle_write_critic_qa
    so the placeholder text is lens-aware).
    """
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti:
        return _err("task_id or task_dir required")
    td = td or canonical_task_dir(task_id=ti)
    content_parts = [f"# {filename.replace('.md', '').replace('__', ' — ')}\n"]
    for key in ("verdict", "summary", "verification", "transcript"):
        val = _opt(args, key)
        if val:
            content_parts.append(f"\n## {key.title()}\n{val}\n")
    manual_ux = args.get("_rendered_manual_ux_section")
    if manual_ux and filename == "CRITIC__qa.md":
        content_parts.append(manual_ux)
    path = os.path.join(td, filename)
    os.makedirs(td, exist_ok=True)
    import tempfile
    text = "\n".join(content_parts)
    fd, tmp = tempfile.mkstemp(dir=td, prefix=f".{filename}.", suffix=".tmp")
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
    result = {"artifact": filename, "task_dir": td}
    if verdict_field:
        verdict = verdict_value or _opt(args, "verdict") or "PASS"
        set_state_field(td, verdict_field, verdict)
        result["verdict"] = verdict
    return _ok(result)


_QA_SEVERITY = {"PENDING": 0, "PASS": 1, "BLOCKED_ENV": 2, "FAIL": 3}


def _worst_verdict(current: str, new: str) -> str:
    """Severity ordering: PENDING < PASS < BLOCKED_ENV < FAIL. Returns the worst.

    Both inputs are normalized to uppercase canonical form before comparison;
    the returned value is also uppercase. The state file may previously have
    stored ``pending`` lowercase — we normalize on read to avoid same-severity
    case-only differences being treated as "no change".
    """
    cur_up = (current or "PENDING").upper()
    new_up = (new or "PENDING").upper()
    return new_up if _QA_SEVERITY.get(new_up, 0) >= _QA_SEVERITY.get(cur_up, 0) else cur_up


def _render_manual_ux_section(lens: str, manual_ux: str) -> tuple[str, str]:
    """Compute (section_markdown, effective_verdict_override).

    AC-006 (2026-05-12 retro): the CRITIC__qa.md template always includes a
    `## Manual UX verification` section. Behavior depends on lens + arg presence:

      - arg provided + non-empty → render content verbatim. No verdict override.
      - lens == 'browser' + arg empty → render placeholder + force PENDING.
      - lens != 'browser' + arg empty → render `_n/a — non-browser lens_`.
        No verdict override.

    Returns the section markdown (including the heading and trailing newline)
    and the effective verdict override ('' = no override; 'PENDING' = downgrade).
    """
    body = (manual_ux or "").strip()
    if body:
        return (f"\n## Manual UX verification\n{body}\n", "")
    if lens == "browser":
        return (
            "\n## Manual UX verification\n"
            "_NOT SUPPLIED — agent must supply manual_ux_verification arg. "
            "runtime_verdict downgraded to PENDING until provided._\n",
            "PENDING",
        )
    return ("\n## Manual UX verification\n_n/a — non-browser lens_\n", "")


def _lens_merge_critic_qa(td: str, lens: str, verdict: str,
                          summary: str, transcript: str,
                          manual_ux: str = "") -> dict:
    """Lens-aware merge for CRITIC__qa.md. Worst-wins runtime_verdict.

    First lens writer creates the file with a global header and one section.
    Subsequent lens writers append a new section (no truncation).
    runtime_verdict downgrades only when the new verdict is worse.

    AC-006: the Manual UX verification section is rendered per-lens; the
    browser lens with empty manual_ux forces PENDING (worst-wins still applies).
    """
    os.makedirs(td, exist_ok=True)
    path = os.path.join(td, "CRITIC__qa.md")
    manual_ux_md, verdict_override = _render_manual_ux_section(lens, manual_ux)
    if verdict_override:
        verdict = verdict_override
    section = (
        f"\n## qa-{lens} verdict: {verdict}\n\n"
        f"### summary\n{summary}\n\n"
        f"### transcript\n{transcript}\n"
        f"{manual_ux_md}"
    )
    if not os.path.exists(path):
        with open(path, "w", encoding="utf-8") as f:
            f.write(f"# CRITIC — qa\n{section}")
    else:
        with open(path, "a", encoding="utf-8") as f:
            f.write(section)
    current = read_state(td).get("runtime_verdict", "PENDING")
    final = _worst_verdict(current, verdict)
    set_state_field(td, "runtime_verdict", final)
    return _ok({"artifact": "CRITIC__qa.md", "task_dir": td,
                "lens": lens, "verdict": final, "merged": True})


def handle_write_critic_qa(args: dict) -> dict:
    verdict = _req(args, "verdict")
    if verdict not in ("PASS", "FAIL", "BLOCKED_ENV"):
        return _err(f"invalid verdict '{verdict}' — must be PASS, FAIL, or BLOCKED_ENV")
    lens = _opt(args, "lens")
    manual_ux = _opt(args, "manual_ux_verification") or ""
    if lens:
        td = _opt(args, "task_dir")
        ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
        if not ti:
            return _err("task_id or task_dir required")
        td = td or canonical_task_dir(task_id=ti)
        summary = _opt(args, "summary") or ""
        transcript = _opt(args, "transcript") or ""
        return _lens_merge_critic_qa(td, lens, verdict, summary, transcript, manual_ux)
    # Legacy single-lens (no lens arg). Treat as non-browser by default for the
    # Manual UX rendering — the agent's lens identity is unknown, so we err on
    # the side of not forcing PENDING.
    manual_ux_md, verdict_override = _render_manual_ux_section("", manual_ux)
    if verdict_override:
        verdict = verdict_override
    args = dict(args)  # don't mutate caller's dict
    args["_rendered_manual_ux_section"] = manual_ux_md
    return _write_artifact(args, "CRITIC__qa.md", "runtime_verdict", verdict_value=verdict)


def handle_write_handoff(args: dict) -> dict:
    return _write_artifact(args, "HANDOFF.md")


def handle_write_doc_sync(args: dict) -> dict:
    return _write_artifact(args, "DOC_SYNC.md")


# ── Tool definitions ─────────────────────────────────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "task_start", "title": "Create or resume a task",
     "description": "Create task scaffolding (7-field TASK_STATE) and return fresh context.",
     "inputSchema": {"type": "object", "properties": {
         "task_dir": {"type": "string"}, "task_id": {"type": "string"},
         "slug": {"type": "string"}, "request_file": {"type": "string"}},
         "additionalProperties": False},
     "handler": handle_task_start},
    {"name": "task_context", "title": "Read the task pack",
     "description": "Return compact task context with on-the-fly routing.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_context},
    {"name": "task_verify", "title": "Run task verification",
     "description": "Sync changed paths and check verification state.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_verify},
    {"name": "task_close", "title": "Run the completion gate",
     "description": "Check all verdicts PASS, then close the task.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_close},
    {"name": "write_critic_qa", "title": "Write runtime verdict — QA agents only",
     "description": "Write CRITIC__qa.md and set runtime_verdict. Called by qa-browser, qa-api, qa-cli, or qa-desktop. Pass `lens` (e.g. \"cli\", \"browser\") when multiple QA agents run in parallel — the handler appends a per-lens section and computes worst-wins runtime_verdict. Without `lens`, legacy full-overwrite behavior is preserved. Pass `manual_ux_verification` with a non-empty description of the manual UX verification performed; when lens='browser' and this field is empty, runtime_verdict is forced to PENDING (AC-006).",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]},
         "summary": {"type": "string"}, "transcript": {"type": "string"},
         "lens": {"type": "string", "description": "Optional QA lens identifier (cli, api, browser, desktop). When set, enables append-mode + worst-wins merge."},
         "manual_ux_verification": {"type": "string", "description": "Optional. Description of manual UX checks performed (browser lens). Empty + lens=browser → verdict forced to PENDING (AC-006)."}},
         "required": ["task_id", "verdict", "summary", "transcript"],
         "additionalProperties": False},
     "handler": handle_write_critic_qa},
    {"name": "write_handoff", "title": "Write developer handoff — developer only",
     "description": "Write HANDOFF.md.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "summary": {"type": "string"}, "verification": {"type": "string"}},
         "required": ["task_id", "summary", "verification"], "additionalProperties": False},
     "handler": handle_write_handoff},
    {"name": "write_doc_sync", "title": "Write DOC_SYNC — developer only",
     "description": "Write DOC_SYNC.md.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "summary": {"type": "string"}},
         "required": ["task_id", "summary"], "additionalProperties": False},
     "handler": handle_write_doc_sync},
]

TOOLS = {t["name"]: t for t in TOOL_DEFS}


def list_tools() -> list[dict]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOL_DEFS]


def call_tool(name: str, args: dict | None) -> dict:
    if name not in TOOLS:
        return _err(f"Unknown tool: {name}")
    try:
        return TOOLS[name]["handler"](args or {})
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{name} failed: {e}")


# ── MCP protocol ─────────────────────────────────────────────────────────


class McpServer:
    def __init__(self) -> None:
        self.initialized = False
        self.protocol_version = SUPPORTED_PROTOCOLS[0]

    def _read(self) -> dict | None:
        line = sys.stdin.buffer.readline()
        if not line or not line.strip():
            return None
        return json.loads(line.strip().decode())

    def _write(self, payload: dict) -> None:
        data = (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode()
        sys.stdout.buffer.write(data)
        sys.stdout.buffer.flush()

    def _reply(self, msg_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    def handle_request(self, req: dict) -> None:
        method = req.get("method")
        msg_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            pv = params.get("protocolVersion")
            self.protocol_version = pv if isinstance(pv, str) and pv in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            self._reply(msg_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": "harness MCP — 7 tools, 7-field TASK_STATE. write_* tools are subagent-only.",
            })
        elif method == "notifications/initialized":
            self.initialized = True
        elif method == "ping":
            self._reply(msg_id, {})
        elif method == "tools/list":
            self._reply(msg_id, {"tools": list_tools()})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                self._error(msg_id, -32602, "Tool name must be a string")
                return
            self._reply(msg_id, call_tool(name, arguments))
        else:
            self._error(msg_id, -32601, f"Method not found: {method}")

    def serve_forever(self) -> None:
        while True:
            req = self._read()
            if req is None:
                return
            self.handle_request(req)


def main() -> int:
    McpServer().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
