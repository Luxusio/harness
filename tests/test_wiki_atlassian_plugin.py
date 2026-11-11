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
    env = clean_environment(home, ATLASSIAN_BASE_URL="https://127.0.0.1:9",
        ATLASSIAN_EMAIL="fixture@example.invalid", ATLASSIAN_API_TOKEN="fixture-only-token")
    env.update(changes)
    return env


def write_connection(cwd, **changes):
    # JSON is a strict subset of YAML and keeps native IDs quoted in this fixture.
    value = dict(enabled=True, site="https://127.0.0.1:9", space="TEAM", spaceId="123", rootPageId="456")
    value.update(changes)
    value.setdefault("backend", "atlassian")
    manifest = cwd / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema_version": 3, "project": {"name": "fixture"}, "wiki": value}))


@pytest.mark.parametrize("settings", [{}, {"WIKI_ATLASSIAN_ENABLED": "false", "WIKI_ATLASSIAN_SITE": "bad"},
    {"WIKI_ATLASSIAN_ENABLED": "true", "WIKI_ATLASSIAN_SITE": "bad"},
    {"ATLASSIAN_EMAIL": "shared", "ATLASSIAN_API_TOKEN": "shared"}])
@pytest.mark.parametrize("disabled", [False, True])
def test_absent_or_disabled_connection_is_inert_from_multiple_projects(tmp_path, settings, disabled):
    jar = _jar()
    home = tmp_path / "home"
    old = home / ".config/knowledge-confluence/config.json"; old.parent.mkdir(parents=True)
    old.write_text('{"enabled":true,"secret":"invalid-sentinel"}')
    env = clean_environment(home, **settings)
    for name in ("project-a", "project-b"):
        cwd = tmp_path / name; cwd.mkdir()
        (cwd / "mise.local.toml").write_text("not-valid-TOML-secret-sentinel")
        if disabled:
            write_connection(cwd, enabled=False)
        before = {p.name for p in cwd.iterdir()}
        replies = _calls(jar, cwd, env)
        initialized = next(row["result"] for row in replies if row.get("id") == 1)
        assert initialized["serverInfo"]["name"] == "atlassian"
        assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
        assert {p.name for p in cwd.iterdir()} == before
    assert not (home / ".local/share/wiki-atlassian").exists()


def test_actual_packaged_payload_captures_consuming_project_before_plugin_chdir(tmp_path):
    jar = _jar()
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    market = module.prepare(tmp_path / "data", jar)
    packaged = market / "plugin"
    assert (packaged / "lib/wiki-atlassian.jar").read_bytes() == jar.read_bytes()
    for relative in ("skills/setup/SKILL.md", "skills/knowledge/SKILL.md", "skills/migrate-docs/SKILL.md", "agents/document-reviewer.md"):
        assert (packaged / relative).read_bytes() == (PLUGIN / relative).read_bytes()
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert json.loads((packaged / name).read_text())["name"] == "wiki-atlassian"
    transport = json.loads((packaged / ".mcp.json").read_text())["mcpServers"]
    assert set(transport) == {"atlassian"}
    assert set(transport["atlassian"]["env_vars"]) == {
        "HOME", "JAVA_HOME", "WIKI_ATLASSIAN_JAVA", "ATLASSIAN_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"}
    assert module.prepare(tmp_path / "data", jar) == market
    cwd = tmp_path / "unregistered-project"; cwd.mkdir()
    home = tmp_path / "empty-home"; home.mkdir()
    command = [transport["atlassian"]["command"], *transport["atlassian"]["args"]]
    replies = _calls(packaged / "lib/wiki-atlassian.jar", cwd, clean_environment(home), command=command)
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not list(cwd.iterdir())
    (cwd / ".git").write_text("gitdir: boundary-only")
    write_connection(cwd)
    nested = cwd / "nested"; nested.mkdir()
    replies = _calls(packaged / "lib/wiki-atlassian.jar", nested, enabled_environment(home,
        WIKI_ATLASSIAN_ENABLED="false", WIKI_ATLASSIAN_SITE="https://ignored.invalid"), command=command)
    tools = next(row["result"]["tools"] for row in replies if row.get("id") == 2)
    assert {t["name"] for t in tools} == {"connection_status", "search_documents", "get_document", "create_document",
        "update_document", "list_comments", "create_comment", "update_comment", "list_document_versions", "document_relations"}
    assert not list(nested.iterdir())


@pytest.mark.parametrize("raw", ["", "[]", "enabled: 'true'", "enabled: false\nenabled: true",
    "enabled: false\napiToken: sensitive-secret", "enabled: false\nsite: https://sensitive-secret@example.invalid",
    "enabled: false\nspaceId: 123", "enabled: false\npollIntervalSeconds: '300'", "enabled: false\nwatchCommentPages: ''",
    "enabled: &flag false", "enabled: false\nsite: *flag", "enabled: !!bool false", "!!java/object:sensitive-secret {}",
    "enabled: false\n---\nenabled: false", "enabled: false\nsite: [a]", "#" + "a" * 65536])
def test_invalid_yaml_fails_before_creating_corpus_without_leaking_values(tmp_path, raw):
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("wiki:\n  backend: atlassian\n" + "\n".join("  " + line for line in raw.split("\n")) + "\n")
    env = enabled_environment(tmp_path)
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout + result.stderr
    assert "sensitive-secret" not in result.stdout + result.stderr
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("key", ["ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "ATLASSIAN_BASE_URL"])
def test_missing_private_authentication_blocks_startup(tmp_path, key):
    write_connection(tmp_path)
    env = enabled_environment(tmp_path); env.pop(key)
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout + result.stderr
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


def test_origin_pin_mismatch_performs_zero_connections_or_corpus_activation(tmp_path):
    import select
    import socket
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0)); listener.listen()
        site = "https://127.0.0.1:" + str(listener.getsockname()[1])
        write_connection(tmp_path, site=site)
        env = enabled_environment(tmp_path)  # Pin remains the other origin, port9.
        result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
            cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
        assert result.returncode != 0
        assert select.select([listener], [], [], 0)[0] == []
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()
    assert "fixture-only-token" not in result.stdout + result.stderr


def test_direct_jar_uses_nearest_git_boundary_and_nogit_startup_only(tmp_path):
    jar = _jar(); env = enabled_environment(tmp_path / "home")
    root = tmp_path / "repo"; root.mkdir(); (root / ".git").mkdir(); write_connection(root)
    nested = root / "nested"; nested.mkdir()
    tools = next(row["result"]["tools"] for row in _calls(jar, nested, env) if row.get("id") == 2)
    assert tools
    (nested / ".git").write_text("gitdir: boundary-only")
    assert next(row["result"]["tools"] for row in _calls(jar, nested, env) if row.get("id") == 2) == []
    isolated = tmp_path / "no-git"; isolated.mkdir(); write_connection(isolated)
    child = isolated / "child"; child.mkdir()
    assert next(row["result"]["tools"] for row in _calls(jar, child, env) if row.get("id") == 2) == []


@pytest.mark.parametrize("manifest_text", [None, "schema_version: 3\nproject:\n  name: fixture\n  arbitrary: [a, b]\n",
    '{"project": {"name": "fixture"}}'])
def test_common_manifest_without_wiki_and_legacy_separate_file_are_inert(tmp_path, manifest_text):
    (tmp_path / "wiki-atlassian.yaml").write_text('{"enabled":true,"site":"https://invalid.invalid"}')
    if manifest_text is not None:
        manifest = tmp_path / "doc/harness/manifest.yaml"; manifest.parent.mkdir(parents=True)
        manifest.write_text(manifest_text)
    replies = _calls(_jar(), tmp_path, enabled_environment(tmp_path))
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("manifest_text", ["wiki: null", "wiki: []", "wiki: {enabled: true}",
    "wiki: {backend: other, enabled: false}", "wiki: {backend: false, enabled: false}",
    "'wiki': {enabled: true}", "{wiki: {enabled: true}}", "wiki: {enabled: false}\nwiki: {enabled: false}"])
def test_common_manifest_rejects_invalid_backend_mapping_and_ambiguous_root(tmp_path, manifest_text):
    manifest = tmp_path / "doc/harness/manifest.yaml"; manifest.parent.mkdir(parents=True)
    manifest.write_text(manifest_text)
    env = enabled_environment(tmp_path)
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("component", ["doc", "harness", "manifest"])
def test_common_manifest_rejects_symlink_path_components(tmp_path, component):
    project = tmp_path / "project"; project.mkdir()
    outside = tmp_path / "outside"; outside.mkdir(); write_connection(outside)
    if component == "doc":
        (project / "doc").symlink_to(outside / "doc", target_is_directory=True)
    elif component == "harness":
        (project / "doc").mkdir()
        (project / "doc/harness").symlink_to(outside / "doc/harness", target_is_directory=True)
    else:
        (project / "doc/harness").mkdir(parents=True)
        (project / "doc/harness/manifest.yaml").symlink_to(outside / "doc/harness/manifest.yaml")
    env = enabled_environment(tmp_path)
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=project, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("nesting,accepted", [(18, True), (19, True), (20, False)])
def test_common_manifest_nesting_boundary_matches_harness(tmp_path, nesting, accepted):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_depth_destination", ROOT / "plugin/scripts/document_destination.py")
    destination = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(destination)
    write_connection(tmp_path)
    path = tmp_path / "doc/harness/manifest.yaml"
    wiki = json.loads(path.read_text())["wiki"]
    source = "other: " + "[" * nesting + "x" + "]" * nesting + "\nwiki: " + json.dumps(wiki) + "\n"
    path.write_text(source)
    env = enabled_environment(tmp_path / "home")
    assert destination.select_destination(env, cwd=tmp_path)["mode"] == ("external" if accepted else "unavailable")
    if accepted:
        tools = next(row["result"]["tools"] for row in _calls(_jar(), tmp_path, env) if row.get("id") == 2)
        assert len(tools) == 10
    else:
        result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
            cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
        assert result.returncode != 0
        assert not (tmp_path / "home/.local/share/wiki-atlassian").exists()
    assert path.read_text() == source


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


def test_installer_uses_user_home_when_xdg_location_is_supplied(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_install_fixed_home", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = tmp_path / "home"
    override = tmp_path / "unused-override"
    monkeypatch.setenv("XDG_DATA_HOME", str(override))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    real_prepare = module.prepare
    jar = _jar()
    monkeypatch.setattr(module, "prepare", lambda data, ignored_jar: real_prepare(data, jar))
    monkeypatch.setattr(module.sys, "argv", ["install.py", "--skip-build", "--prepare-only"])
    assert module.main() == 0
    market = Path(capsys.readouterr().out.strip())
    assert market == home / ".local/share/wiki-atlassian/marketplace"
    assert (market / "plugin/skills/setup/SKILL.md").is_file()
    assert not override.exists()
