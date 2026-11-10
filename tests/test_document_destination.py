"""Runtime-selected document policy and actual prompt injection boundaries."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin/scripts"
spec = importlib.util.spec_from_file_location("document_destination_test", SCRIPTS / "document_destination.py")
destination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(destination)


def config(home, **changes):
    path = home / ".config/knowledge-confluence/config.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    value = dict(schemaVersion=1, enabled=True, site="https://example.invalid",
                 space="TEAM", spaceId="123", rootPageId="456")
    value.update(changes)
    path.write_text(json.dumps(value))
    return path


def prompt(project, home):
    manifest = project / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: library\n")
    (project / ".git").mkdir()
    (manifest.parent / "runbooks.yaml").write_text(
        'runbooks:\n  probe:\n    description: local-recipe-sentinel\n    command: python -V\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "KNOWLEDGE_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / "prompt_memory.py")],
                          cwd=project, env=env, input='{"session_id":"destination-test"}',
                          text=True, capture_output=True, timeout=10)


def test_missing_and_disabled_use_local_without_creating_state(tmp_path):
    assert destination.select_destination({"HOME": str(tmp_path)})["mode"] == "local"
    assert list(tmp_path.iterdir()) == []
    config(tmp_path, enabled=False)
    assert destination.policy({"HOME": str(tmp_path)})["mode"] == "local"


@pytest.mark.parametrize("changes", [dict(enabled="true"), dict(schemaVersion=True),
    dict(schemaVersion=1.0), dict(site="http://example.invalid"), dict(site="https://secret@example.invalid"),
    dict(rootPageId="../1"), dict(spaceId=1), dict(space="../TEAM")])
def test_invalid_config_never_selects_local(tmp_path, changes):
    config(tmp_path, **changes)
    result = destination.policy({"HOME": str(tmp_path)})
    assert result["mode"] == "unavailable"
    assert "secret" not in json.dumps(result)


def test_malformed_and_symlinked_config_do_not_fall_back(tmp_path):
    path = config(tmp_path)
    path.write_text('{"enabled":true, "token":"secret-token"')
    result = destination.policy({"HOME": str(tmp_path)})
    assert result["mode"] == "unavailable"
    assert "secret-token" not in json.dumps(result)
    path.unlink()
    target = tmp_path / "other.json"
    target.write_text("{}")
    path.symlink_to(target)
    assert destination.select_destination({"HOME": str(tmp_path)})["mode"] == "unavailable"


def test_explicit_path_and_permissions(tmp_path):
    path = config(tmp_path)
    assert destination.policy({"KNOWLEDGE_CONFLUENCE_CONFIG": str(path)})["backend"] == "confluence"
    assert destination.policy({"KNOWLEDGE_CONFLUENCE_CONFIG": "relative.json"})["mode"] == "unavailable"
    assert destination.policy({"KNOWLEDGE_CONFLUENCE_CONFIG": str(tmp_path / "missing.json")})["mode"] == "unavailable"
    path.chmod(0o666)
    assert destination.policy({"HOME": str(tmp_path)})["mode"] == "unavailable"


def test_two_projects_get_remote_guidance_without_local_memory_injection(tmp_path):
    home = tmp_path / "home"
    config(home)
    for name in ("a", "b"):
        result = prompt(tmp_path / name, home)
        assert result.returncode == 0, result.stderr
        assert "search/read/update with confluence MCP" in result.stdout
        assert "Durable decisions require doc/" not in result.stdout
        assert "local-recipe-sentinel" not in result.stdout


def test_invalid_connection_exposes_pending_not_local_writing(tmp_path):
    home = tmp_path / "home"
    config(home, site="invalid")
    result = prompt(tmp_path / "project", home)
    assert result.returncode == 0
    assert "business-document writes remain pending" in result.stdout
    assert "Durable decisions require doc/" not in result.stdout
    assert "local-recipe-sentinel" not in result.stdout


@pytest.mark.parametrize("state", ["missing", "disabled", "external", "invalid"])
def test_actual_session_banner_uses_selected_global_destination(tmp_path, state):
    home = tmp_path / "home"
    if state != "missing":
        path = config(home, enabled=state != "disabled")
        if state == "invalid":
            path.write_text("broken")
    project = tmp_path / "project"
    manifest = project / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: library\n")
    (project / ".git").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "KNOWLEDGE_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "hook_session_start.py"), "--claude-banner"],
                            cwd=project, env=env, input=json.dumps({"cwd": str(project)}),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "harness — ready" in result.stdout
    if state in ("missing", "disabled"):
        assert "REQ: capture requirements as doc/" in result.stdout
        assert "confluence MCP" not in result.stdout
    else:
        assert "REQ: capture requirements as doc/" not in result.stdout
        assert ("confluence MCP" if state == "external" else "business-document writes remain pending") in result.stdout


def test_external_req_gate_requires_native_versioned_requirement_reference(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    from prewrite_gate import _wbs_has_req_reference
    home = tmp_path / "home"
    path = config(home)
    monkeypatch.setenv("KNOWLEDGE_CONFLUENCE_CONFIG", str(path))
    wbs = tmp_path / "repo/doc/harness/wbs/WBS__example"
    wbs.mkdir(parents=True)
    plan = wbs / "PLAN.md"
    # Local replicas and arbitrary URLs must not satisfy a remote requirement.
    for body in ("REQ: doc/ui/REQ__example.md", "REQ: confluence:123", "REQ: confluence:123@0",
                 "GUIDE: confluence:123@4", "An example is REQ: confluence:123@4"):
        plan.write_text(body)
        assert not _wbs_has_req_reference(str(wbs))
    plan.write_text("# Plan\nREQ: confluence:123@4\n")
    assert _wbs_has_req_reference(str(wbs))
    path.write_text("broken")
    assert not _wbs_has_req_reference(str(wbs))
