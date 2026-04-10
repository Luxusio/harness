#!/usr/bin/env python3
"""harness2 MCP server — 9-tool control plane."""

from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT.parent / "plugin" / "scripts"
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
MAX_TEXT_CHARS = 12000
sys.path.insert(0, str(SCRIPTS_DIR))
import harness_api  # type: ignore
from _lib import canonical_task_dir, canonical_task_id, find_repo_root, yaml_array, yaml_field  # type: ignore

SERVER_INFO = {"server_name": "harness2", "title": "harness2 Control Plane", "version": "2.0.0"}

def _cap(v, limit=MAX_TEXT_CHARS):
    t = v or ""; return t if len(t) <= limit else f"{t[:limit//2]}\n...[truncated]...\n{t[-(limit-limit//2):]}"
def _jt(d): return json.dumps(d, indent=2, ensure_ascii=False, sort_keys=True)
def _ok(d): return {"content": [{"type": "text", "text": _jt(d)}], "structuredContent": d}
def _err(m, data=None):
    p = {"error": m}; p.update(data or {}); return {"content": [{"type": "text", "text": _jt(p)}], "structuredContent": p, "isError": True}
def _run(script, args=None, env=None, cwd=None):
    argv = [sys.executable, str(SCRIPTS_DIR / script)] + list(args or [])
    e = os.environ.copy(); e.update(env or {})
    r = subprocess.run(argv, capture_output=True, text=True, cwd=cwd or os.getcwd(), env=e)
    return {"ok": r.returncode == 0, "argv": argv, "exit_code": r.returncode, "stdout": _cap(r.stdout), "stderr": _cap(r.stderr)}
def _req(args, k):
    v = args.get(k)
    if not isinstance(v, str) or not v.strip(): raise ValueError(f"{k} must be a non-empty string")
    return v
def _opt(args, k):
    v = args.get(k); return None if v is None else (v.strip() or None)
def _optb(args, k, d=False):
    v = args.get(k, d)
    if isinstance(v, bool): return v
    raise ValueError(f"{k} must be bool")
def _slim(r): return {"ok": r.get("ok"), "exit_code": r.get("exit_code"), "stderr": r.get("stderr", "")}
def _sf(td): return os.path.join(td, "TASK_STATE.yaml")
def _ctx(td):
    try:
        p = harness_api.get_task_context(td)
        return p, {"ok": True, "method": "direct", "exit_code": 0, "stdout": _cap(json.dumps(p)), "stderr": ""}
    except Exception as exc:
        de = {"ok": False, "method": "direct", "exit_code": 1, "stdout": "", "stderr": _cap(str(exc))}
    r = _run("hctl.py", ["context", "--task-dir", td, "--json"])
    if not r["ok"]: r.setdefault("fallback_from", de); return None, r
    try: p = json.loads(r["stdout"])
    except: p = None
    r.setdefault("fallback_from", de); return p, r
def _sumline(r):
    for src in (r.get("stdout") or "", r.get("stderr") or ""):
        for l in str(src).splitlines():
            if l.strip().startswith("RESULT:"): return l.strip()
    t = [l.strip() for s in (r.get("stdout") or "", r.get("stderr") or "") for l in str(s).splitlines() if l.strip()]
    return t[-1] if t else ""
def _art(td, fn):
    n = Path(td).name; return f"doc/harness/tasks/{n}/{fn}" if os.path.isfile(os.path.join(td, fn)) else ""
def _artr(sub, args, artifact, agent_name=None):
    env = {"HARNESS_SKIP_PREWRITE": "1"}
    if agent_name: env["CLAUDE_AGENT_NAME"] = agent_name
    r = _run("write_artifact.py", [sub] + args, env=env)
    td = args[args.index("--task-id")+1] if "--task-id" in args else None
    aw = None
    if r["ok"]:
        try: aw = json.loads(r["stdout"])
        except: pass
    p = {"artifact": artifact, "subcommand": sub, "task_dir": td, "artifact_write": aw, "write": r}
    return _err(f"{sub} failed", data=p) if not r["ok"] else _ok(p)

def handle_task_start(args):
    td = _opt(args, "task_dir"); ti = _opt(args, "task_id"); sl = _opt(args, "slug")
    rf = _opt(args, "request_file"); dbg = _optb(args, "debug")
    if not td and not ti and not sl: raise ValueError("task_start requires task_dir, task_id, or slug")
    rr = find_repo_root(os.getcwd())
    rtd = td or str(canonical_task_dir(task_id=ti, slug=sl, repo_root=rr))
    av = ["start"]
    if td: av.extend(["--task-dir", td])
    if ti: av.extend(["--task-id", ti])
    if sl: av.extend(["--slug", sl])
    if rf: av.extend(["--request-file", rf])
    r = _run("hctl.py", av); c, cr = _ctx(rtd)
    if not r["ok"] or c is None:
        return _err("task_start failed", data={"task_dir": rtd, "task_id": canonical_task_id(task_id=ti, slug=sl, task_dir=rtd), "start": _slim(r), "task_context": c})
    p = {"task_dir": rtd, "task_id": canonical_task_id(task_id=ti, slug=sl, task_dir=rtd), "task_context": c, "start": {"ok": True, "exit_code": int(r.get("exit_code") or 0)}}
    return _ok(p)

def handle_task_context(args):
    ti = _req(args, "task_id"); td = str(canonical_task_dir(task_id=ti))
    an = _opt(args, "agent_name"); dbg = _optb(args, "debug")
    try:
        c = harness_api.get_task_context(td, agent_name=an)
        r = {"ok": True, "method": "direct", "exit_code": 0, "stdout": _cap(json.dumps(c)), "stderr": ""}
    except Exception as exc:
        r = _run("hctl.py", ["context", "--task-dir", td, "--json"])
        c = json.loads(r["stdout"]) if r["ok"] else None
    if c is None: return _err("task_context failed", data={"task_dir": td})
    return _ok({"task_dir": td, "task_context": c, "context_revision": c.get("context_revision"), "fetch": {"ok": r.get("ok"), "method": r.get("method", "direct")}})

def handle_task_verify(args):
    ti = _req(args, "task_id"); td = str(canonical_task_dir(task_id=ti)); dbg = _optb(args, "debug")
    r = _run("hctl.py", ["verify", "--task-dir", td]); c, cf = _ctx(td)
    if not r["ok"]: return _err("task_verify failed", data={"task_dir": td, "verify": _slim(r), "task_context": c})
    sf = _sf(td)
    return _ok({"task_dir": td, "ok": True, "summary": _sumline(r), "next_action": (c or {}).get("next_action", ""),
                "missing_for_close": list((c or {}).get("missing_for_close") or [])[:4],
                "runtime_verdict": yaml_field("runtime_verdict", sf) or "pending", "report_path": _art(td, "CRITIC__runtime.md")})

def handle_task_close(args):
    ti = _req(args, "task_id"); td = str(canonical_task_dir(task_id=ti)); dbg = _optb(args, "debug")
    r = _run("hctl.py", ["close", "--task-dir", td]); c, cf = _ctx(td); sf = _sf(td)
    closed = bool(r.get("ok")) or (yaml_field("status", sf) or "").strip().lower() in {"closed", "archived"}
    if not r["ok"]: return _err("task_close failed", data={"task_dir": td, "close": _slim(r), "stdout": r.get("stdout"), "task_context": c})
    return _ok({"task_dir": td, "closed": closed, "status": yaml_field("status", sf) or "unknown",
                "summary": _sumline(r) or ("close gate PASSED" if closed else "close gate finished"),
                "missing_for_close": list((c or {}).get("missing_for_close") or [])[:4],
                "next_action": (c or {}).get("next_action", ""), "gate_artifact": _art(td, "HANDOFF.md")})

def handle_write_critic_plan(args):
    td = _opt(args, "task_dir"); ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti: return _err("task_id or task_dir is required")
    v = _req(args, "verdict"); s = _req(args, "summary"); an = _opt(args, "agent_name")
    return _artr("critic-plan", ["--task-id", ti, "--verdict", v, "--summary", s], "CRITIC__plan.md", an)

def handle_write_critic_runtime(args):
    ti = _req(args, "task_id"); v = _req(args, "verdict"); s = _req(args, "summary"); t = _req(args, "transcript"); an = _opt(args, "agent_name")
    return _artr("critic-runtime", ["--task-id", ti, "--verdict", v, "--summary", s, "--transcript", t], "CRITIC__runtime.md", an)

def handle_write_critic_document(args):
    td = _opt(args, "task_dir"); ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti: return _err("task_id or task_dir is required")
    v = _req(args, "verdict"); s = _req(args, "summary"); an = _opt(args, "agent_name")
    return _artr("critic-document", ["--task-id", ti, "--verdict", v, "--summary", s], "CRITIC__document.md", an)

def handle_write_handoff(args):
    td = _opt(args, "task_dir"); ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti: return _err("task_id or task_dir is required")
    s = _req(args, "summary"); ver = _req(args, "verification"); an = _opt(args, "agent_name")
    return _artr("handoff", ["--task-id", ti, "--summary", s, "--verification", ver], "HANDOFF.md", an)

def handle_write_doc_sync(args):
    td = _opt(args, "task_dir"); ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti: return _err("task_id or task_dir is required")
    s = _req(args, "summary"); an = _opt(args, "agent_name")
    return _artr("doc-sync", ["--task-id", ti, "--summary", s], "DOC_SYNC.md", an)

TOOL_DEFS = [
    {"name": "task_start", "title": "Create or resume a task", "description": "Run the harness task start step and return the fresh task pack.", "inputSchema": {"type": "object", "properties": {"task_dir": {"type": "string"}, "task_id": {"type": "string"}, "slug": {"type": "string"}, "request_file": {"type": "string"}, "debug": {"type": "boolean"}}, "additionalProperties": False}, "handler": handle_task_start},
    {"name": "task_context", "title": "Refresh the task pack", "description": "Return the compact machine-readable task context.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "agent_name": {"type": "string"}, "debug": {"type": "boolean"}}, "required": ["task_id"], "additionalProperties": False}, "handler": handle_task_context},
    {"name": "task_verify", "title": "Run task verification", "description": "Auto-sync changed paths, then run the harness verification suite.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "debug": {"type": "boolean"}}, "required": ["task_id"], "additionalProperties": False}, "handler": handle_task_verify},
    {"name": "task_close", "title": "Run the completion gate", "description": "Auto-sync changed paths, then attempt to close the task.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "debug": {"type": "boolean"}}, "required": ["task_id"], "additionalProperties": False}, "handler": handle_task_close},
    {"name": "write_critic_plan", "title": "Write plan critic verdict — critic-plan only", "description": "Write CRITIC__plan.md. Only critic-plan subagent should call this.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_dir": {"type": "string"}, "verdict": {"type": "string", "enum": ["PASS", "FAIL"]}, "summary": {"type": "string"}, "agent_name": {"type": "string"}}, "required": ["verdict", "summary"], "additionalProperties": False}, "handler": handle_write_critic_plan},
    {"name": "write_critic_runtime", "title": "Write runtime critic verdict — critic-runtime only", "description": "Write CRITIC__runtime.md. Only critic-runtime subagent should call this.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]}, "summary": {"type": "string"}, "transcript": {"type": "string"}, "agent_name": {"type": "string"}}, "required": ["task_id", "verdict", "summary", "transcript"], "additionalProperties": False}, "handler": handle_write_critic_runtime},
    {"name": "write_critic_document", "title": "Write document critic verdict — critic-document only", "description": "Write CRITIC__document.md. Only critic-document subagent should call this.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_dir": {"type": "string"}, "verdict": {"type": "string", "enum": ["PASS", "FAIL"]}, "summary": {"type": "string"}, "agent_name": {"type": "string"}}, "required": ["verdict", "summary"], "additionalProperties": False}, "handler": handle_write_critic_document},
    {"name": "write_handoff", "title": "Write developer handoff — developer only", "description": "Write HANDOFF.md. Only developer subagent should call this.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_dir": {"type": "string"}, "summary": {"type": "string"}, "verification": {"type": "string"}, "agent_name": {"type": "string"}}, "required": ["summary", "verification"], "additionalProperties": False}, "handler": handle_write_handoff},
    {"name": "write_doc_sync", "title": "Write DOC_SYNC artifact — writer only", "description": "Write DOC_SYNC.md. Only writer subagent should call this.", "inputSchema": {"type": "object", "properties": {"task_id": {"type": "string"}, "task_dir": {"type": "string"}, "summary": {"type": "string"}, "agent_name": {"type": "string"}}, "required": ["summary"], "additionalProperties": False}, "handler": handle_write_doc_sync},
]
TOOLS = {t["name"]: t for t in TOOL_DEFS}

def list_tools(): return [{k: v for k, v in t.items() if k != "handler"} for t in TOOL_DEFS]
def call_tool(name, args):
    if name not in TOOLS: return _err(f"Unknown tool: {name}")
    try: return TOOLS[name]["handler"](args or {})
    except ValueError as e: return _err(str(e))
    except Exception as e: return _err(f"{name} failed: {e}")

class McpServer:
    def __init__(self): self.initialized = False; self.protocol_version = SUPPORTED_PROTOCOLS[0]
    def _read(self):
        l = sys.stdin.buffer.readline()
        return None if not l else (json.loads(l.strip().decode()) if l.strip() else None)
    def _write(self, p): sys.stdout.buffer.write((json.dumps(p, separators=(",",":"), ensure_ascii=False)+"\n").encode()); sys.stdout.buffer.flush()
    def _reply(self, i, r): self._write({"jsonrpc":"2.0","id":i,"result":r})
    def _error(self, i, c, m): self._write({"jsonrpc":"2.0","id":i,"error":{"code":c,"message":m}})
    def handle_request(self, req):
        m = req.get("method"); i = req.get("id"); p = req.get("params") or {}
        if m == "initialize":
            rv = p.get("protocolVersion")
            self.protocol_version = rv if isinstance(rv, str) and rv in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            self._reply(i, {"protocolVersion": self.protocol_version, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": SERVER_INFO, "instructions": "harness2 MCP — 9 tools. write_* tools are subagent-only."})
        elif m == "notifications/initialized": self.initialized = True
        elif m == "ping": self._reply(i, {})
        elif m == "tools/list": self._reply(i, {"tools": list_tools()})
        elif m == "tools/call":
            n = p.get("name"); a = p.get("arguments") or {}
            if not isinstance(n, str): self._error(i, -32602, "Tool name must be a string"); return
            self._reply(i, call_tool(n, a))
        else: self._error(i, -32601, f"Method not found: {m}")
    def serve_forever(self):
        while True:
            r = self._read()
            if r is None: return
            self.handle_request(r)

def main(): McpServer().serve_forever(); return 0
if __name__ == "__main__": sys.exit(main())
