"""AC-002: write_req_doc MCP handler accepts omitted task_id; schema marks task_id optional.

P5 convention: no top-level third-party imports. pytest auto-injects `tmp_path` /
`monkeypatch` fixtures by parameter name without requiring `import pytest`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "plugin" / "mcp" / "harness_server.py"


def _load_harness_server(tmp_path, monkeypatch):
    """Load the harness MCP server module with cwd pinned to a tmp repo.

    handle_write_req_doc resolves repo_root via find_repo_root() which walks
    upward looking for .git — keep tests hermetic by giving each test its own
    tmp repo so REQ files land in tmp, not the real repo.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "doc" / "harness").mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("project_type: cli\n")
    monkeypatch.chdir(repo)

    scripts_dir = str(REPO / "plugin" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)

    spec = importlib.util.spec_from_file_location("harness_server_test", SERVER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod, repo


def _structured(result: dict) -> dict:
    return result.get("structuredContent") or {}


def test_write_req_doc_accepts_missing_task_id(tmp_path, monkeypatch):
    mod, repo = _load_harness_server(tmp_path, monkeypatch)
    result = mod.handle_write_req_doc({
        "area": "harness",
        "slug": "no-task-fixture",
        "intent": "Verify standalone write_req_doc.",
        "observable_behaviors": "- REQ file exists at expected path.",
        "verification_cues": "- Test reads the written file.",
    })
    body = _structured(result)
    assert "error" not in body, f"expected ok, got error: {body}"
    assert body.get("task_id", "") == "", f"task_id should be empty, got {body.get('task_id')!r}"
    assert body.get("task_dir", "") == "", f"task_dir should be empty, got {body.get('task_dir')!r}"
    assert body["source"].startswith("adhoc:"), f"source should start with adhoc:, got {body['source']!r}"
    req_path = repo / body["req_path"]
    assert req_path.exists(), f"REQ file not written: {req_path}"
    content = req_path.read_text()
    # req_scaffold titlecases the slug (no-task-fixture -> "No Task Fixture")
    assert "REQ - No Task Fixture" in content
    assert body["source"] in content


def test_write_req_doc_with_task_id_preserves_existing_behavior(tmp_path, monkeypatch):
    mod, repo = _load_harness_server(tmp_path, monkeypatch)
    task_id = "TASK__with-task-fixture"
    result = mod.handle_write_req_doc({
        "task_id": task_id,
        "area": "harness",
        "slug": "with-task-fixture",
        "intent": "Verify task-scoped write_req_doc unchanged.",
        "observable_behaviors": "- REQ file exists.",
        "verification_cues": "- Test reads source line.",
    })
    body = _structured(result)
    assert "error" not in body, f"expected ok, got error: {body}"
    assert body["task_id"] == task_id
    assert body["task_dir"].endswith(task_id), f"task_dir should point at {task_id}, got {body['task_dir']!r}"
    assert body["source"] == f"task: {task_id}"
    req_path = repo / body["req_path"]
    assert req_path.exists()
    assert f"task: {task_id}" in req_path.read_text()


def test_write_req_doc_with_empty_task_id_string_treated_as_omitted(tmp_path, monkeypatch):
    mod, repo = _load_harness_server(tmp_path, monkeypatch)
    result = mod.handle_write_req_doc({
        "task_id": "",
        "area": "harness",
        "slug": "empty-task-id",
        "intent": "Empty string task_id is equivalent to omitted.",
        "observable_behaviors": "- adhoc source used.",
        "verification_cues": "- task_dir is empty.",
    })
    body = _structured(result)
    assert "error" not in body
    assert body.get("task_id", "") == ""
    assert body.get("task_dir", "") == ""
    assert body["source"].startswith("adhoc:")


def test_write_req_doc_schema_marks_task_id_optional(tmp_path, monkeypatch):
    mod, _repo = _load_harness_server(tmp_path, monkeypatch)
    assert "write_req_doc" in mod.TOOLS, "write_req_doc tool not registered"
    write_req = mod.TOOLS["write_req_doc"]
    required = write_req["inputSchema"]["required"]
    assert "task_id" not in required, f"task_id should not be in required, got {required}"
    for field in ("slug", "intent", "observable_behaviors", "verification_cues"):
        assert field in required, f"{field} should still be required, got {required}"
