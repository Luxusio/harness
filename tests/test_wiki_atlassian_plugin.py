"""Exercise the built document plugin through its actual stdio boundary."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import sys
import threading

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wiki-atlassian"


@contextmanager
def private_inbox(tmp_path):
    home = tmp_path / "private-home"; home.mkdir(mode=0o700)
    folder = home / ".config/harness"; folder.mkdir(parents=True, mode=0o700)
    folder.parent.chmod(0o700)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
        "-keyout", str(key), "-out", str(cert), "-subj", "/CN=127.0.0.1", "-addext", "subjectAltName=IP:127.0.0.1"],
        check=True, capture_output=True)
    calls = []
    expected = "Basic " + base64.b64encode(b"private@example.invalid:private-mcp-fixture").decode()
    class Native(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            if self.headers.get("Authorization") != expected:
                self.send_error(401); return
            if self.path == "/wiki/rest/api/user/current":
                body = {"accountId": "private-account"}
            elif self.path.startswith("/wiki/api/v2/pages/790/footer-comments?"):
                subsequent = "cursor=" in self.path
                body = {"results": [{"id": "901" if subsequent else "900", "status": "current", "pageId": "790",
                    "version": {"number": 1}, "body": {"storage": {"representation": "storage",
                    "value": "<p>Second opinion</p>" if subsequent else "<p>Reuse compatible tokens</p>"}}}]}
            elif self.path.startswith("/wiki/api/v2/pages/790"):
                body = {"id": "790", "title": "Harness feedback", "status": "current", "version": {"number": 2},
                    "body": {"storage": {"representation": "storage", "value": "<h2>Improvement inbox</h2>"}}}
            else:
                self.send_error(404); return
            self.send_response(200)
            if "/footer-comments?" in self.path and "cursor=" not in self.path:
                self.send_header("Link", '</wiki/api/v2/pages/790/footer-comments?cursor=next%2Bcursor&limit=1>; rel="next"')
            self.send_header("Content-Type", "application/json"); self.end_headers()
            self.wfile.write(json.dumps(body).encode())
        def do_POST(self):
            calls.append((self.command, self.path, self.headers.get("Authorization")))
            self.send_error(405)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), Native)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    site = "https://127.0.0.1:" + str(server.server_port)
    settings = folder / "feedback.yaml"
    settings.write_text(json.dumps({"backend": "atlassian", "page": site + "/wiki/pages/viewpage.action?pageId=790"})); settings.chmod(0o600)
    auth = folder / "mise.local.toml"
    auth.write_text('[env]\nATLASSIAN_BASE_URL=' + json.dumps(site) + '\nATLASSIAN_EMAIL="private@example.invalid"\nATLASSIAN_API_TOKEN="private-mcp-fixture"\n'); auth.chmod(0o600)
    binary = tmp_path / "fixture-bin"; binary.mkdir()
    mise = binary / "mise"
    mise.write_text("#!" + sys.executable + ' -I\nimport json,tomllib\nfrom pathlib import Path\np=Path.cwd()/"mise.local.toml"\nd=tomllib.loads(p.read_text())["env"]\nprint(json.dumps({k:{"value":v,"source":str(p)} for k,v in d.items()}))\n'); mise.chmod(0o700)
    try:
        yield home, auth, dict(SSL_CERT_FILE=str(cert), PATH=str(binary) + os.pathsep + os.environ["PATH"]), calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def _jar():
    value = os.environ.get("WIKI_TEST_JAR")
    jar = Path(value) if value else PLUGIN / "build/libs/wiki-atlassian.jar"
    if not jar.is_file():
        pytest.skip("Build the standalone plugin first; packaged-runtime behavior is unverified")
    return jar.resolve()


def _calls(jar, cwd, env, command=None, requests=None):
    java = env.get("WIKI_ATLASSIAN_JAVA", "java")
    rows = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "knowledge-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    rows.extend(requests or [])
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
    value = dict(enabled=True, site="https://127.0.0.1:9", spaces=[dict(key="TEAM", rootPageId="456")])
    value.update(changes)
    value.setdefault("backend", "atlassian")
    manifest = cwd / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps({"schema_version": 3, "project": {"name": "fixture"}, "wiki": value}))


def personal_payload(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_personal_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    package = module.prepare(tmp_path / "payload-data", _jar()) / "plugin"
    transport = json.loads((package / ".mcp.json").read_text())["mcpServers"]["atlassian"]
    assert transport["command"] == str(Path(sys.executable).absolute())
    for name in ("personal_feedback.py", "document_destination.py"):
        assert (package / "scripts" / name).read_bytes() == (ROOT / "plugin/scripts" / name).read_bytes()
    return package, [transport["command"], *transport["args"]]


def test_private_payload_accepts_frozen_readonly_sources_without_modifying_them(tmp_path, monkeypatch):
    import importlib.util
    import shutil
    spec = importlib.util.spec_from_file_location("wiki_frozen_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    source = tmp_path / "frozen"; wiki = source / "wiki-atlassian"; wiki.mkdir(parents=True)
    for name in module.PAYLOAD_DIRS:
        shutil.copytree(PLUGIN / name, wiki / name)
    for name in (".mcp.json", "PROVENANCE.md", "README.md"):
        shutil.copyfile(PLUGIN / name, wiki / name)
    helpers = source / "plugin/scripts"; helpers.mkdir(parents=True)
    for name in ("personal_feedback.py", "document_destination.py"):
        shutil.copyfile(ROOT / "plugin/scripts" / name, helpers / name)
    paths = [source, *source.rglob("*")]
    for path in paths:
        path.chmod(0o500 if path.is_dir() else 0o400)
    before = {str(path.relative_to(source)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
              for path in paths}
    try:
        monkeypatch.setattr(module, "ROOT", wiki)
        package = module.prepare(tmp_path / "data", _jar()) / "plugin"
        assert (package / "scripts/personal_feedback.py").read_bytes() == (helpers / "personal_feedback.py").read_bytes()
        assert before == {str(path.relative_to(source)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                          for path in paths}
    finally:
        for path in paths:
            if path.is_dir():
                path.chmod(0o700)


def feedback_call(package, command, cwd, env, **arguments):
    replies = _calls(package / "lib/wiki-atlassian.jar", cwd, env, command=command, requests=[
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
            "name": "read_personal_feedback", "arguments": arguments}}])
    tools = next(row["result"]["tools"] for row in replies if row.get("id") == 2)
    reply = next(row["result"] for row in replies if row.get("id") == 3)
    assert not reply.get("isError"), reply
    return tools, json.loads(reply["content"][0]["text"])


def test_personal_feedback_uses_packaged_mcp_and_private_identity_without_project_wiki(tmp_path):
    package, command = personal_payload(tmp_path)
    project = tmp_path / "consumer"; project.mkdir()
    with private_inbox(tmp_path) as (home, _, settings, calls):
        env = clean_environment(home, **settings, ATLASSIAN_BASE_URL="https://project.invalid",
            ATLASSIAN_EMAIL="project@example.invalid", ATLASSIAN_API_TOKEN="project-token-sentinel",
            HTTP_PROXY="http://127.0.0.1:9", HTTPS_PROXY="http://127.0.0.1:9",
            PYTHONPATH=str(project), PYTHONHOME=str(project))
        (project / "personal_feedback.py").write_text("raise RuntimeError('project-import-poison')")
        tools, first = feedback_call(package, command, project, env, limit=1)
        assert [t["name"] for t in tools] == ["read_personal_feedback"]
        assert tools[0]["annotations"]["readOnlyHint"] is True
        assert tools[0]["annotations"]["destructiveHint"] is False
        assert set(tools[0]["inputSchema"]["properties"]) == {"limit", "cursor"}
        assert first["status"] == "read" and first["page"]["id"] == "790"
        assert "Reuse compatible tokens" in first["comments"][0]["body"]["value"]
        assert not first["complete"]
        other = tmp_path / "other-consumer"; other.mkdir()
        _, second = feedback_call(package, command, other, env, limit=1, **first["next"])
        assert second["status"] == "read" and second["comments"][0]["id"] == "901" and second["complete"]
        assert all(method == "GET" for method, _, _ in calls)
        assert all(authorization == calls[0][2] for _, _, authorization in calls)
        assert not (project / "doc").exists() and not list(other.iterdir())
        assert all(secret not in json.dumps(first) + json.dumps(second) for secret in (
            "private-mcp-fixture", "project-token-sentinel", "project@example.invalid", "project-import-poison"))


@pytest.mark.parametrize("fault", ["missing-auth", "unsafe-settings", "mismatched-origin"])
def test_packaged_personal_mcp_never_borrows_project_authentication(tmp_path, fault):
    package, command = personal_payload(tmp_path)
    project = tmp_path / "consumer"; project.mkdir()
    with private_inbox(tmp_path) as (home, auth, settings, calls):
        if fault == "missing-auth":
            auth.unlink()
        elif fault == "unsafe-settings":
            (auth.parent / "feedback.yaml").chmod(0o644)
        else:
            auth.write_text(auth.read_text().replace("127.0.0.1", "other.invalid"))
        env = clean_environment(home, **settings, ATLASSIAN_BASE_URL="https://project.invalid",
            ATLASSIAN_EMAIL="project@example.invalid", ATLASSIAN_API_TOKEN="project-token-sentinel")
        tools, result = feedback_call(package, command, project, env)
        assert tools and result["status"] == "unavailable" and calls == []
        assert "project-token-sentinel" not in json.dumps(result)


def test_private_inbox_remains_readable_when_unrelated_project_wiki_is_invalid(tmp_path):
    package, command = personal_payload(tmp_path)
    project = tmp_path / "consumer"; project.mkdir()
    write_connection(project)
    with private_inbox(tmp_path) as (home, _, settings, calls):
        env = clean_environment(home, **settings, ATLASSIAN_BASE_URL="https://different-project.invalid",
            ATLASSIAN_EMAIL="project@example.invalid", ATLASSIAN_API_TOKEN="project-token-sentinel")
        tools, result = feedback_call(package, command, project, env, limit=1)
        assert {t["name"] for t in tools} == {"read_personal_feedback", "connection_status"}
        assert result["status"] == "read" and result["page"]["id"] == "790"
        assert all(method == "GET" for method, _, _ in calls)


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
    "enabled: false\nspaceId: 123", "enabled: false\nspaces: [{key: TEAM, rootPageId: 123}]", "enabled: false\nspaces: [{key: TEAM}, {key: TEAM}]", "enabled: false\npollIntervalSeconds: '300'", "enabled: false\nwatchCommentPages: ''",
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


def test_multiple_spaces_and_optional_roots_expose_one_toolset_before_native_resolution(tmp_path):
    write_connection(tmp_path, spaces=[{"key": "TEAM"}, {"key": "DEV", "rootPageId": "456"}])
    replies = _calls(_jar(), tmp_path, enabled_environment(tmp_path / "home"), requests=[
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "connection_status", "arguments": {}}},
    ])
    tools = next(row["result"]["tools"] for row in replies if row.get("id") == 2)
    assert len(tools) == 10
    assert len({tool["name"] for tool in tools}) == 10
    for name in ("search_documents", "get_document", "create_document", "update_document"):
        tool = next(tool for tool in tools if tool["name"] == name)
        assert tool["inputSchema"]["properties"]["space"]["type"] == "string"
    create = next(tool for tool in tools if tool["name"] == "create_document")
    assert "parent" not in create["inputSchema"].get("required", [])
    content = next(row["result"]["content"][0]["text"] for row in replies if row.get("id") == 3)
    status = json.loads(content)
    assert status["enabled"] is True and status["ready"] is False
    assert [(space["key"], space["wholeSpace"]) for space in status["spaces"]] == [("TEAM", True), ("DEV", False)]
    assert status["spaces"][1]["rootPageId"] == "456"
    assert not (tmp_path / "home/.local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("spaces", [[], [{"key": "TEAM", "spaceId": "123"}], [{"key": "TEAM"}, {"key": "TEAM"}],
    [{"key": "TEAM", "rootPageId": 456}], [{"key": "TEAM", "site": "https://other.invalid"}],
    [{"key": "S" + str(index)} for index in range(33)]])
def test_public_space_selections_reject_native_ids_duplicates_and_unbounded_lists(tmp_path, spaces):
    write_connection(tmp_path, spaces=spaces)
    env = enabled_environment(tmp_path / "home")
    result = subprocess.run([env.get("WIKI_ATLASSIAN_JAVA", "java"), "-jar", str(_jar())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout + result.stderr
    assert not (tmp_path / "home/.local/share/wiki-atlassian").exists()


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
