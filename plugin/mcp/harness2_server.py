#!/usr/bin/env python3
"""harness2 MCP server — self-contained, 7-field TASK_STATE.

No plugin-legacy dependency. All operations are direct file I/O.
7 MCP tools: task_start, task_context, task_verify, task_close,
             write_critic_runtime,
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
SERVER_INFO = {"name": "harness2", "title": "harness2 Control Plane", "version": "2.0.0"}

sys.path.insert(0, str(SCRIPTS_DIR))
from _lib import (  # type: ignore
    now_iso, read_state, write_state, set_state_field,
    ensure_task_scaffold, emit_compact_context, sync_from_git_diff,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_repo_root,
)


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

    ctx = emit_compact_context(task_dir)
    if "error" in ctx:
        return _err("task_start failed", data={"task_dir": task_dir})
    return _ok({"task_dir": task_dir, "task_id": tid, "task_context": ctx})


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
    st = read_state(td)
    rv = (st.get("runtime_verdict") or "pending").upper()
    ctx = emit_compact_context(td)
    return _ok({
        "task_dir": td, "runtime_verdict": rv,
        "touched_paths": st.get("touched_paths") or [],
        "next_action": ctx.get("next_action", ""),
        "missing_for_close": ctx.get("missing_for_close", []),
        "report_path": _task_artifact_rel(td, "CRITIC__runtime.md"),
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


def _write_artifact(args: dict, filename: str, verdict_field: str | None = None) -> dict:
    """Common artifact write: create file, optionally update verdict."""
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
    path = os.path.join(td, filename)
    os.makedirs(td, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(content_parts))
    result = {"artifact": filename, "task_dir": td}
    if verdict_field:
        verdict = _opt(args, "verdict") or "PASS"
        set_state_field(td, verdict_field, verdict)
        result["verdict"] = verdict
    return _ok(result)


def handle_write_critic_runtime(args: dict) -> dict:
    return _write_artifact(args, "CRITIC__runtime.md", "runtime_verdict")


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
    {"name": "write_critic_runtime", "title": "Write runtime verdict — QA agents only",
     "description": "Write CRITIC__runtime.md and set runtime_verdict. Called by qa-browser, qa-api, or qa-cli.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]},
         "summary": {"type": "string"}, "transcript": {"type": "string"}},
         "required": ["task_id", "verdict", "summary", "transcript"],
         "additionalProperties": False},
     "handler": handle_write_critic_runtime},
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
                "instructions": "harness2 MCP — 7 tools, 7-field TASK_STATE. write_* tools are subagent-only.",
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
