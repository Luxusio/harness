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


def connection(**changes):
    env = dict(WIKI_ATLASSIAN_ENABLED="true", WIKI_ATLASSIAN_SITE="https://example.invalid",
               WIKI_ATLASSIAN_SPACE="TEAM", WIKI_ATLASSIAN_SPACE_ID="123", WIKI_ATLASSIAN_ROOT_PAGE_ID="456",
               ATLASSIAN_EMAIL="fixture@example.invalid", ATLASSIAN_API_TOKEN="fixture-only-token")
    env.update(changes)
    return env


def prompt(project, home, settings=None):
    manifest = project / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: library\n")
    (project / ".git").mkdir()
    (manifest.parent / "runbooks.yaml").write_text(
        'runbooks:\n  probe:\n    description: local-recipe-sentinel\n    command: python -V\n')
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "KNOWLEDGE_", "WIKI_", "ATLASSIAN_", "JIRA_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env.update(settings or {})
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / "prompt_memory.py")],
                          cwd=project, env=env, input='{"session_id":"destination-test"}',
                          text=True, capture_output=True, timeout=10)


def test_absent_disabled_and_shared_credentials_use_local_without_state(tmp_path):
    assert destination.select_destination({"HOME": str(tmp_path)})["mode"] == "local"
    assert destination.select_destination({"ATLASSIAN_EMAIL": "fixture", "ATLASSIAN_API_TOKEN": "fixture"})["mode"] == "local"
    assert destination.select_destination(connection(WIKI_ATLASSIAN_ENABLED="false", WIKI_ATLASSIAN_SITE="bad"))["mode"] == "local"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("changes", [dict(WIKI_ATLASSIAN_ENABLED="TRUE"), dict(WIKI_ATLASSIAN_ENABLED=True),
    dict(WIKI_ATLASSIAN_SITE="http://example.invalid"), dict(WIKI_ATLASSIAN_SITE="https://secret@example.invalid"),
    dict(WIKI_ATLASSIAN_SITE="https://example.invalid:0"), dict(WIKI_ATLASSIAN_SITE="https://example.invalid:65536"),
    dict(WIKI_ATLASSIAN_SITE="https://example.invalid:"), dict(WIKI_ATLASSIAN_SITE="https://example.invalid?"),
    dict(WIKI_ATLASSIAN_SITE="https://example.invalid/#"), dict(WIKI_ATLASSIAN_SITE="https://bad_host.invalid"),
    dict(WIKI_ATLASSIAN_SITE="https://example.invalid\\evil"), dict(WIKI_ATLASSIAN_SITE=" https://example.invalid"),
    dict(WIKI_ATLASSIAN_SITE="https://1.2.3"), dict(WIKI_ATLASSIAN_SITE="https://999.999.999.999"),
    dict(WIKI_ATLASSIAN_SITE="https://example.123"), dict(WIKI_ATLASSIAN_SITE="https://[v1.fe]"),
    dict(WIKI_ATLASSIAN_SITE="https://[v1.fe:abcd]"), dict(WIKI_ATLASSIAN_SITE="https://[::1%eth-0]"),
    dict(WIKI_ATLASSIAN_SITE="https://example.invalid.."), dict(WIKI_ATLASSIAN_SITE="https://example.1a"),
    dict(WIKI_ATLASSIAN_ROOT_PAGE_ID="../1"), dict(WIKI_ATLASSIAN_SPACE_ID="0"), dict(WIKI_ATLASSIAN_SPACE="../TEAM"),
    dict(WIKI_ATLASSIAN_MEMORY_PAGE_ID=""), dict(WIKI_ATLASSIAN_FEEDBACK_PAGE_ID="0"),
    dict(WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS="29"), dict(WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS="86401"),
    dict(WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS="+300"), dict(WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS=""),
    dict(ATLASSIAN_API_TOKEN=" "), dict(ATLASSIAN_EMAIL="")])
def test_invalid_environment_never_selects_local_or_leaks_values(changes):
    env = connection(**changes)
    result = destination.policy(env)
    assert result["mode"] == "unavailable"
    if env["ATLASSIAN_API_TOKEN"].strip():
        assert env["ATLASSIAN_API_TOKEN"] not in json.dumps(result)
    assert "secret@" not in json.dumps(result)


def test_partial_environment_is_pending_and_valid_boundaries_are_accepted():
    env = connection(); env.pop("WIKI_ATLASSIAN_ENABLED")
    assert destination.policy(env)["mode"] == "unavailable"
    for key in ("WIKI_ATLASSIAN_SITE", "WIKI_ATLASSIAN_ROOT_PAGE_ID", "ATLASSIAN_API_TOKEN"):
        env = connection(); env.pop(key)
        assert destination.policy(env)["mode"] == "unavailable"
    for seconds in ("30", "86400"):
        assert destination.policy(connection(WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS=seconds))["mode"] == "external"
    for site in ("https://example.invalid:1", "https://example.invalid:65535/", "https://[::1]:443", "https://[::1%eth_0]"):
        assert destination.policy(connection(WIKI_ATLASSIAN_SITE=site))["mode"] == "external"


def test_legacy_json_and_mise_files_are_not_configuration_sources(tmp_path):
    for name in (".config/knowledge-confluence/config.json", ".config/wiki-atlassian/config.json", "mise.local.toml"):
        path = tmp_path / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("invalid-secret-config")
    assert destination.policy({"HOME": str(tmp_path)})["mode"] == "local"
    assert destination.policy(dict(connection(), HOME=str(tmp_path)))["mode"] == "external"


def test_two_projects_get_remote_guidance_without_local_memory_injection(tmp_path):
    home = tmp_path / "home"
    settings = connection()
    for name in ("a", "b"):
        result = prompt(tmp_path / name, home, settings)
        assert result.returncode == 0, result.stderr
        assert "search/read/update with atlassian MCP" in result.stdout
        assert "Durable decisions require doc/" not in result.stdout
        assert "local-recipe-sentinel" not in result.stdout


def test_invalid_connection_exposes_pending_not_local_writing(tmp_path):
    home = tmp_path / "home"
    result = prompt(tmp_path / "project", home, connection(WIKI_ATLASSIAN_SITE="invalid"))
    assert result.returncode == 0
    assert "business-document writes remain pending" in result.stdout
    assert "Durable decisions require doc/" not in result.stdout
    assert "local-recipe-sentinel" not in result.stdout


@pytest.mark.parametrize("state", ["missing", "disabled", "external", "invalid"])
def test_actual_session_banner_uses_selected_global_destination(tmp_path, state):
    home = tmp_path / "home"
    settings = {} if state == "missing" else connection(WIKI_ATLASSIAN_ENABLED="false" if state == "disabled" else "true")
    if state == "invalid":
        settings["WIKI_ATLASSIAN_SITE"] = "broken"
    project = tmp_path / "project"
    manifest = project / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: library\n")
    (project / ".git").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "KNOWLEDGE_", "WIKI_", "ATLASSIAN_", "JIRA_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1", **settings)
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "hook_session_start.py"), "--claude-banner"],
                            cwd=project, env=env, input=json.dumps({"cwd": str(project)}),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "harness — ready" in result.stdout
    if state in ("missing", "disabled"):
        assert "REQ: capture requirements as doc/" in result.stdout
        assert "atlassian MCP" not in result.stdout
    else:
        assert "REQ: capture requirements as doc/" not in result.stdout
        assert ("atlassian MCP" if state == "external" else "business-document writes remain pending") in result.stdout


def test_external_req_gate_requires_native_versioned_requirement_reference(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    from prewrite_gate import _wbs_has_req_reference
    home = tmp_path / "home"
    for key, value in connection().items():
        monkeypatch.setenv(key, value)
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
    monkeypatch.setenv("WIKI_ATLASSIAN_SITE", "broken")
    assert not _wbs_has_req_reference(str(wbs))
