"""AC-003 (MCP half): write_req_doc MCP handler accepts status field; schema includes it.

P5 convention: no top-level third-party imports. pytest auto-injects tmp_path / monkeypatch.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "plugin" / "mcp" / "harness_server.py"


def _load_harness_server(tmp_path, monkeypatch):
    """Load harness_server module with cwd pinned to a tmp harness-enabled repo."""
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "doc" / "harness").mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("project_type: cli\n")
    monkeypatch.chdir(repo)

    scripts_dir = str(REPO / "plugin" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    spec = importlib.util.spec_from_file_location("harness_server_status_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, repo


def _body(result: dict) -> dict:
    return result.get("structuredContent") or {}


def test_write_req_doc_accepts_status_candidate(tmp_path, monkeypatch):
    mod, repo = _load_harness_server(tmp_path, monkeypatch)
    result = mod.handle_write_req_doc({
        "area": "harness",
        "slug": "status-candidate-mcp",
        "intent": "MCP-level status candidate.",
        "observable_behaviors": "- frontmatter has status: candidate",
        "verification_cues": "- read REQ file",
        "status": "candidate",
    })
    body = _body(result)
    assert "error" not in body, body
    assert body.get("status") == "candidate"
    written = (repo / body["req_path"]).read_text(encoding="utf-8")
    assert "status: candidate" in written


def test_write_req_doc_defaults_status_accepted(tmp_path, monkeypatch):
    mod, repo = _load_harness_server(tmp_path, monkeypatch)
    result = mod.handle_write_req_doc({
        "area": "harness",
        "slug": "status-default-mcp",
        "intent": "MCP-level default.",
        "observable_behaviors": "- frontmatter has status: accepted",
        "verification_cues": "- read REQ file",
    })
    body = _body(result)
    assert "error" not in body, body
    assert body.get("status") == "accepted"
    written = (repo / body["req_path"]).read_text(encoding="utf-8")
    assert "status: accepted" in written


def test_write_req_doc_schema_lists_status_optional_enum(tmp_path, monkeypatch):
    mod, _repo = _load_harness_server(tmp_path, monkeypatch)
    write_req = mod.TOOLS["write_req_doc"]
    props = write_req["inputSchema"]["properties"]
    assert "status" in props, f"status property missing from schema: {sorted(props.keys())}"
    enum = props["status"].get("enum")
    assert enum is not None and "accepted" in enum and "candidate" in enum, (
        f"status enum must include both 'accepted' and 'candidate', got {enum!r}"
    )
    required = write_req["inputSchema"]["required"]
    assert "status" not in required, "status must remain optional"
