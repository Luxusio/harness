"""Exercise the built document plugin through its actual stdio boundary."""
from __future__ import annotations

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
PLUGIN = ROOT / "knowledge-confluence"


def _jar():
    value = os.environ.get("KNOWLEDGE_TEST_JAR")
    jar = Path(value) if value else PLUGIN / "build/libs/knowledge-confluence.jar"
    if not jar.is_file():
        pytest.skip("Build the standalone plugin first; packaged-runtime behavior is unverified")
    return jar.resolve()


def _calls(jar, cwd, env, launcher=None):
    java = env.get("KNOWLEDGE_CONFLUENCE_JAVA", "java")
    rows = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "knowledge-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    command = [sys.executable, "-I", str(launcher)] if launcher else [java, "-jar", str(jar)]
    result = subprocess.run(command, cwd=cwd, env=env,
        input="".join(json.dumps(row) + "\n" for row in rows), text=True,
        capture_output=True, timeout=30)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return replies


def test_disabled_connection_is_inert_from_multiple_projects(tmp_path):
    jar = _jar()
    home = tmp_path / "home"
    config = home / ".config/knowledge-confluence/config.json"
    config.parent.mkdir(parents=True)
    config.write_text(json.dumps({"schemaVersion": 1, "enabled": False}))
    config.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JIRA_", "KNOWLEDGE_", "XDG_"))}
    env.update(HOME=str(home), KNOWLEDGE_CONFLUENCE_CONFIG=str(config))
    if "KNOWLEDGE_CONFLUENCE_JAVA" in os.environ:
        env["KNOWLEDGE_CONFLUENCE_JAVA"] = os.environ["KNOWLEDGE_CONFLUENCE_JAVA"]
    for name in ("project-a", "project-b"):
        cwd = tmp_path / name
        cwd.mkdir()
        replies = _calls(jar, cwd, env)
        initialized = next(row["result"] for row in replies if row.get("id") == 1)
        assert initialized["serverInfo"]["name"] == "confluence"
        tools = next(row["result"]["tools"] for row in replies if row.get("id") == 2)
        assert tools == []
        assert list(cwd.iterdir()) == []
    assert not (home / ".local/share/knowledge-confluence").exists()


def test_actual_packaged_payload_starts_without_project_registration(tmp_path):
    jar = _jar()
    import importlib.util
    spec = importlib.util.spec_from_file_location("knowledge_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    market = module.prepare(tmp_path / "data", jar)
    packaged = market / "plugin"
    assert (packaged / "lib/knowledge-confluence.jar").read_bytes() == jar.read_bytes()
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert json.loads((packaged / name).read_text())["name"] == "knowledge-confluence"
    assert set(json.loads((packaged / ".mcp.json").read_text())["mcpServers"]) == {"confluence"}
    assert module.prepare(tmp_path / "data", jar) == market
    cwd = tmp_path / "unregistered-project"
    cwd.mkdir()
    home = tmp_path / "empty-home"
    home.mkdir()
    config = home / "config.json"
    config.write_text(json.dumps({"schemaVersion": 1, "enabled": False}))
    config.chmod(0o600)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JIRA_", "KNOWLEDGE_", "XDG_"))}
    env.update(HOME=str(home), KNOWLEDGE_CONFLUENCE_CONFIG=str(config))
    if "KNOWLEDGE_CONFLUENCE_JAVA" in os.environ:
        env["KNOWLEDGE_CONFLUENCE_JAVA"] = os.environ["KNOWLEDGE_CONFLUENCE_JAVA"]
    replies = _calls(packaged / "lib/knowledge-confluence.jar", cwd, env,
                     launcher=packaged / "scripts/launch.py")
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not list(cwd.iterdir())


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
