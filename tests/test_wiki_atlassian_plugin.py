"""Exercise the built document plugin through its actual stdio boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wiki-atlassian"


def _jar():
    value = os.environ.get("WIKI_TEST_JAR")
    jar = Path(value) if value else PLUGIN / "build/libs/wiki-atlassian.jar"
    if not jar.is_file():
        pytest.skip("Build the standalone plugin first; packaged-runtime behavior is unverified")
    return jar.resolve()


def _calls(jar, cwd, env, command=None):
    java = env.get("WIKI_ATLASSIAN_JAVA", "java")
    rows = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "knowledge-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    command = command if command is not None else [java, "-jar", str(jar)]
    result = subprocess.run(command, cwd=cwd, env=env,
        input="".join(json.dumps(row) + "\n" for row in rows), text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return replies


def clean_environment(home, **settings):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JIRA_", "KNOWLEDGE_", "WIKI_", "ATLASSIAN_", "XDG_"))}
    env.update(HOME=str(home), **settings)
    if "WIKI_ATLASSIAN_JAVA" in os.environ:
        env["WIKI_ATLASSIAN_JAVA"] = os.environ["WIKI_ATLASSIAN_JAVA"]
    return env


def enabled_environment(home, **changes):
    env = clean_environment(home, WIKI_ATLASSIAN_ENABLED="true", WIKI_ATLASSIAN_SITE="https://127.0.0.1:9",
        WIKI_ATLASSIAN_SPACE="TEAM", WIKI_ATLASSIAN_SPACE_ID="123", WIKI_ATLASSIAN_ROOT_PAGE_ID="456",
        ATLASSIAN_EMAIL="fixture@example.invalid", ATLASSIAN_API_TOKEN="fixture-only-token")
    env.update(changes)
    return env


@pytest.mark.parametrize("settings", [{}, {"WIKI_ATLASSIAN_ENABLED": "false", "WIKI_ATLASSIAN_SITE": "bad"},
    {"ATLASSIAN_EMAIL": "shared", "ATLASSIAN_API_TOKEN": "shared"}])
def test_absent_or_disabled_connection_is_inert_from_multiple_projects(tmp_path, settings):
    jar = _jar()
    home = tmp_path / "home"
    # Old JSON and mise files must never be read as connection authority.
    old = home / ".config/knowledge-confluence/config.json"; old.parent.mkdir(parents=True)
    old.write_text('{"enabled":true,"secret":"invalid-sentinel"}')
    env = clean_environment(home, **settings)
    for name in ("project-a", "project-b"):
        cwd = tmp_path / name; cwd.mkdir()
        (cwd / "mise.local.toml").write_text("not-valid-TOML-secret-sentinel")
        replies = _calls(jar, cwd, env)
        initialized = next(row["result"] for row in replies if row.get("id") == 1)
        assert initialized["serverInfo"]["name"] == "atlassian"
        assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
        assert [p.name for p in cwd.iterdir()] == ["mise.local.toml"]
    assert not (home / ".local/share/wiki-atlassian").exists()


def test_actual_packaged_payload_starts_without_project_registration(tmp_path):
    jar = _jar()
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    market = module.prepare(tmp_path / "data", jar)
    packaged = market / "plugin"
    assert (packaged / "lib/wiki-atlassian.jar").read_bytes() == jar.read_bytes()
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert json.loads((packaged / name).read_text())["name"] == "wiki-atlassian"
    transport = json.loads((packaged / ".mcp.json").read_text())["mcpServers"]
    assert set(transport) == {"atlassian"}
    assert set(transport["atlassian"]["env_vars"]) >= {
        "WIKI_ATLASSIAN_ENABLED", "WIKI_ATLASSIAN_SITE", "WIKI_ATLASSIAN_SPACE", "WIKI_ATLASSIAN_SPACE_ID",
        "WIKI_ATLASSIAN_ROOT_PAGE_ID", "WIKI_ATLASSIAN_MEMORY_PAGE_ID", "WIKI_ATLASSIAN_FEEDBACK_PAGE_ID",
        "WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"}
    assert module.prepare(tmp_path / "data", jar) == market
    cwd = tmp_path / "unregistered-project"; cwd.mkdir()
    home = tmp_path / "empty-home"; home.mkdir()
    command = [transport["atlassian"]["command"], *transport["atlassian"]["args"]]
    replies = _calls(packaged / "lib/wiki-atlassian.jar", cwd, clean_environment(home), command=command)
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not list(cwd.iterdir())
    replies = _calls(packaged / "lib/wiki-atlassian.jar", cwd, enabled_environment(home), command=command)
    tools = next(row["result"]["tools"] for row in replies if row.get("id") == 2)
    assert {t["name"] for t in tools} == {"connection_status", "search_documents", "get_document", "create_document",
        "update_document", "list_comments", "create_comment", "update_comment", "list_document_versions", "document_relations"}
    assert not list(cwd.iterdir())


@pytest.mark.parametrize("changes", [{"WIKI_ATLASSIAN_ENABLED":"TRUE"}, {"WIKI_ATLASSIAN_ENABLED":None},
    {"ATLASSIAN_API_TOKEN":None}, {"WIKI_ATLASSIAN_SITE":"https://sensitive-secret@example.invalid"},
    {"WIKI_ATLASSIAN_SITE":"https://127.0.0.1:65536"}, {"WIKI_ATLASSIAN_MEMORY_PAGE_ID":""},
    {"WIKI_ATLASSIAN_POLL_INTERVAL_SECONDS":"+300"}])
def test_invalid_environment_fails_before_creating_corpus_without_leaking_values(tmp_path, changes):
    env = enabled_environment(tmp_path)
    for key,value in changes.items():
        if value is None:env.pop(key)
        else:env[key]=value
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout+result.stderr
    assert "sensitive-secret" not in result.stdout+result.stderr
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


def test_installer_rejects_writable_storage_and_substituted_publication(tmp_path, monkeypatch):
    jar = _jar()
    import importlib.util
    spec = importlib.util.spec_from_file_location("knowledge_install_security", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "unsafe-data"
    packages = data / "packages"
    packages.mkdir(parents=True)
    packages.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe|private"):
        module.prepare(data, jar)
    assert list(packages.iterdir()) == []

    data = tmp_path / "safe-data"
    attacker = tmp_path / "replacement"
    attacker.mkdir()
    real_rename = os.rename

    def replace_at_publication(source, destination, *, src_dir_fd, dst_dir_fd):
        displaced = str(source) + "-displaced"
        real_rename(source, displaced, src_dir_fd=src_dir_fd, dst_dir_fd=src_dir_fd)
        os.symlink(attacker, source, dir_fd=src_dir_fd)
        real_rename(source, destination, src_dir_fd=src_dir_fd, dst_dir_fd=dst_dir_fd)

    monkeypatch.setattr(module.os, "rename", replace_at_publication)
    with pytest.raises(ValueError, match="Published plugin directory changed"):
        module.prepare(data, jar)


def test_marketplace_source_stays_stable_across_versions_and_serializes_updates(tmp_path):
    import importlib.util
    import shutil
    import zipfile
    spec = importlib.util.spec_from_file_location("knowledge_install_upgrade", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    jar = _jar()
    first = module.prepare(tmp_path / "data", jar)
    second_jar = tmp_path / "next.jar"
    shutil.copyfile(jar, second_jar)
    with zipfile.ZipFile(second_jar, "a") as archive:
        archive.comment = b"next release fixture: unchanged executable entries"
    second = module.prepare(tmp_path / "data", second_jar)
    assert first != second
    with module.marketplace(first) as stable:
        assert (stable / "plugin").resolve() == first / "plugin"
        with pytest.raises(BlockingIOError):
            with module.marketplace(second):
                pytest.fail("A concurrent installer changed the selected payload")
        assert (stable / "plugin").resolve() == first / "plugin"
    with module.marketplace(second) as updated:
        assert updated == stable
        assert (updated / "plugin").resolve() == second / "plugin"
        assert (first / "plugin/lib/wiki-atlassian.jar").read_bytes() == jar.read_bytes()
    outside = tmp_path / "unowned-selection"
    outside.mkdir()
    (stable / "plugin").unlink()
    (stable / "plugin").symlink_to(outside)
    with pytest.raises(ValueError, match="outside managed packages"):
        with module.marketplace(first):
            pytest.fail("An unknown selection was overwritten")
    assert (stable / "plugin").resolve() == outside
