"""Exercise the built document plugin through its actual stdio boundary."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path
import subprocess
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ssl
import sys
import threading
import time
import signal

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "wiki-atlassian"
sys.path.insert(0, str(ROOT / 'plugin/scripts'))


pytestmark = pytest.mark.usefixtures("selected_installer_python")


@contextmanager
def private_inbox(tmp_path):
    home = tmp_path / "private-home"; home.mkdir(mode=0o700)
    folder = home / ".config/harness"; folder.mkdir(parents=True, mode=0o700)
    folder.parent.chmod(0o700)
    cert, key = tmp_path / "cert.pem", tmp_path / "key.pem"
    # Known public localhost fixture: no OpenSSL/Git Bash prerequisite on Windows.
    cert.write_bytes((ROOT / "tests/fixtures/wiki-localhost/cert.pem").read_bytes())
    key.write_bytes((ROOT / "tests/fixtures/wiki-localhost/key.pem").read_bytes())
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
    if os.name == 'nt':
        if not shutil.which('mise.exe'):
            server.shutdown(); server.server_close(); thread.join(timeout=5)
            pytest.skip('Native mise.exe is required for actual private authentication; no POSIX fixture substitutes for it')
    else:
        mise = binary / "mise"
        mise.write_text("#!" + sys.executable + ' -I\nimport json,tomllib\nfrom pathlib import Path\np=Path.cwd()/"mise.local.toml"\nd=tomllib.loads(p.read_text())["env"]\nprint(json.dumps({k:{"value":v,"source":str(p)} for k,v in d.items()}))\n'); mise.chmod(0o700)
    try:
        yield home, auth, dict(SSL_CERT_FILE=str(cert), PATH=str(binary) + os.pathsep + os.environ["PATH"]), calls
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=5)


def _runtime():
    path = PLUGIN / "scripts/launch.py"
    assert path.is_file(), "Python MCP launcher must exist; no build prerequisite"
    return path


def test_selected_native_collection_without_posix_marker_primitives(tmp_path):
    # Absence/collection proof only; this subprocess does not emulate Windows.
    script = """
import os, runpy, sys
from pathlib import Path
import pytest
sys.modules['fcntl'] = None
scope = runpy.run_path('tests/conftest.py')
scratch = Path(sys.argv[1]) / 'unused-marker-root'
with pytest.MonkeyPatch.context() as patch:
    def unexpected(*args, **kwargs):
        raise AssertionError('unsupported marker helper performed a POSIX action')
    patch.setattr(os, 'getuid', unexpected, raising=False)
    with pytest.raises(pytest.skip.Exception):
        with scope['active_marker_lock'](str(scratch)):
            raise AssertionError('unsupported marker lock yielded')
    patch.delattr(os, 'O_NOFOLLOW', raising=False)
    with pytest.raises(pytest.skip.Exception):
        with scope['active_marker_lock'](str(scratch)):
            raise AssertionError('unsupported marker lock yielded')
assert not scratch.exists()
raise SystemExit(pytest.main(['--collect-only', '-q', '-o', 'addopts=', '-p', 'no:cacheprovider',
    'tests/test_wiki_windows_native.py']))
"""
    result = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path)], cwd=ROOT,
        env={**os.environ, "PYTEST_DISABLE_PLUGIN_AUTOLOAD": "1", "PYTHONDONTWRITEBYTECODE": "1"},
        text=True, capture_output=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'tests/test_wiki_windows_native.py::' in result.stdout


def _calls(runtime, cwd, env, command=None, requests=None):
    rows = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-06-18", "capabilities": {},
            "clientInfo": {"name": "knowledge-test", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    rows.extend(requests or [])
    command = command if command is not None else [sys.executable, "-I", "-B", str(runtime)]
    result = subprocess.run(command, cwd=cwd, env=env,
        input="".join(json.dumps(row) + "\n" for row in rows), text=True,
        capture_output=True, encoding='utf-8', timeout=30)
    assert result.returncode == 0, result.stderr
    replies = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    return replies


def clean_environment(home, **settings):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("JIRA_", "KNOWLEDGE_", "WIKI_", "ATLASSIAN_", "XDG_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), APPDATA=str(home / 'AppData/Roaming'),
               LOCALAPPDATA=str(home / 'AppData/Local'), **settings)
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
    package = module.prepare(tmp_path / "payload-data") / "plugin"
    transport = json.loads((package / ".mcp.json").read_text())["mcpServers"]["atlassian"]
    assert transport["command"] == str(Path(sys.executable).absolute())
    for name in ("personal_feedback.py", "document_destination.py", "wiki_platform.py", "_wiki_windows.py", "mise_python.py"):
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
    for name in (".mcp.json", "PROVENANCE.md", "README.md", "pyproject.toml"):
        shutil.copyfile(PLUGIN / name, wiki / name)
    helpers = source / "plugin/scripts"; helpers.mkdir(parents=True)
    for name in module.SHARED_HELPERS:
        shutil.copyfile(ROOT / "plugin/scripts" / name, helpers / name)
    paths = [source, *source.rglob("*")]
    for path in paths:
        path.chmod(0o500 if path.is_dir() else 0o400)
    before = {str(path.relative_to(source)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
              for path in paths}
    try:
        monkeypatch.setattr(module, "ROOT", wiki)
        monkeypatch.setattr(module, "HELPERS", helpers)
        package = module.prepare(tmp_path / "data") / "plugin"
        assert (package / "scripts/personal_feedback.py").read_bytes() == (helpers / "personal_feedback.py").read_bytes()
        assert before == {str(path.relative_to(source)): (path.stat().st_mode, path.read_bytes() if path.is_file() else None)
                          for path in paths}
    finally:
        for path in paths:
            if path.is_dir():
                path.chmod(0o700)


def feedback_call(package, command, cwd, env, **arguments):
    replies = _calls(package / "scripts/launch.py", cwd, env, command=command, requests=[
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


@pytest.mark.skipif(os.name == 'nt', reason='POSIX nested process-group cleanup; Windows native Job lifetime has its own suite')
@pytest.mark.parametrize('cancel', [False, True])
def test_private_read_timeout_and_server_termination_reap_nested_mise(tmp_path, cancel):
    package, command = personal_payload(tmp_path)
    project = tmp_path / 'consumer'; project.mkdir()
    pidfile = tmp_path / 'nested-mise.pid'
    child = None
    pid = None
    with private_inbox(tmp_path) as (home, _, settings, _):
        mise = Path(settings['PATH'].split(os.pathsep)[0]) / 'mise'
        mise.write_text('#!' + sys.executable + ' -I\nimport os,time\nfrom pathlib import Path\n'
                        + 'Path(' + repr(str(pidfile)) + ').write_text(str(os.getpid()))\ntime.sleep(60)\n')
        mise.chmod(0o700)
        env = clean_environment(home, **settings)
        argv = command if cancel else [sys.executable, '-I', '-B', '-c',
            "import sys,json;sys.path[:0]=sys.argv[1:3];from wiki_atlassian.main import read_personal;print(json.dumps(read_personal({},timeout=3)))",
            str(package), str(package / 'scripts')]
        try:
            child = subprocess.Popen(argv, cwd=project, env=env, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            if cancel:
                child.stdin.write(json.dumps(dict(jsonrpc='2.0', id=1, method='tools/call',
                    params=dict(name='read_personal_feedback', arguments={}))) + '\n')
                child.stdin.flush()
            deadline = time.monotonic() + 5
            while not pidfile.exists() and time.monotonic() < deadline:
                time.sleep(.02)
            assert pidfile.exists(), 'The actual nested authentication helper did not start'
            pid = int(pidfile.read_text())
            if cancel:
                child.terminate()
            output, error = child.communicate(timeout=6)
            if not cancel:
                assert child.returncode == 0 and json.loads(output)['status'] == 'unavailable', error
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    break
                time.sleep(.02)
            else:
                raise AssertionError('Nested mise survived private execution cleanup')
        finally:
            if child is not None and child.poll() is None:
                child.kill(); child.communicate(timeout=5)
            if pid is not None:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.parametrize("fault", ["missing-auth", "unsafe-settings", "mismatched-origin"])
def test_packaged_personal_mcp_never_borrows_project_authentication(tmp_path, fault):
    if os.name == 'nt' and fault == 'unsafe-settings':
        pytest.skip('POSIX mode-bit case; native ACL denial is covered by test_wiki_windows_native.py')
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
    runtime = _runtime()
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
        replies = _calls(runtime, cwd, env)
        initialized = next(row["result"] for row in replies if row.get("id") == 1)
        assert initialized["serverInfo"]["name"] == "atlassian"
        assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
        assert {p.name for p in cwd.iterdir()} == before
    assert not (home / ".local/share/wiki-atlassian").exists()


def test_actual_packaged_payload_captures_consuming_project_before_plugin_chdir(tmp_path):
    runtime = _runtime()
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_install", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    market = module.prepare(tmp_path / "data")
    packaged = market / "plugin"
    assert (packaged / "scripts/launch.py").read_bytes() == _runtime().read_bytes()
    assert not (packaged / "lib").exists()
    for relative in ("skills/setup/SKILL.md", "skills/knowledge/SKILL.md", "skills/migrate-docs/SKILL.md", "agents/document-reviewer.md"):
        assert (packaged / relative).read_bytes() == (PLUGIN / relative).read_bytes()
    for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
        assert json.loads((packaged / name).read_text())["name"] == "wiki-atlassian"
    transport = json.loads((packaged / ".mcp.json").read_text())["mcpServers"]
    assert set(transport) == {"atlassian"}
    assert set(transport["atlassian"]["env_vars"]) == {
        "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot", "PATH", "TEMP", "TMP", "ATLASSIAN_BASE_URL", "ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN"}
    assert module.prepare(tmp_path / "data") == market
    cwd = tmp_path / "unregistered-project"; cwd.mkdir()
    home = tmp_path / "empty-home"; home.mkdir()
    command = [transport["atlassian"]["command"], *transport["atlassian"]["args"]]
    replies = _calls(packaged / "scripts/launch.py", cwd, clean_environment(home), command=command)
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not list(cwd.iterdir())
    (cwd / ".git").write_text("gitdir: boundary-only")
    write_connection(cwd)
    nested = cwd / "nested"; nested.mkdir()
    replies = _calls(packaged / "scripts/launch.py", nested, enabled_environment(home,
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
    result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout + result.stderr
    assert "sensitive-secret" not in result.stdout + result.stderr
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("key", ["ATLASSIAN_EMAIL", "ATLASSIAN_API_TOKEN", "ATLASSIAN_BASE_URL"])
def test_missing_private_authentication_blocks_startup(tmp_path, key):
    write_connection(tmp_path)
    env = enabled_environment(tmp_path); env.pop(key)
    result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
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
        result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
            cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
        assert result.returncode != 0
        assert select.select([listener], [], [], 0)[0] == []
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()
    assert "fixture-only-token" not in result.stdout + result.stderr


def test_direct_python_uses_nearest_git_boundary_and_nogit_startup_only(tmp_path):
    runtime = _runtime(); env = enabled_environment(tmp_path / "home")
    root = tmp_path / "repo"; root.mkdir(); (root / ".git").mkdir(); write_connection(root)
    nested = root / "nested"; nested.mkdir()
    tools = next(row["result"]["tools"] for row in _calls(runtime, nested, env) if row.get("id") == 2)
    assert tools
    (nested / ".git").write_text("gitdir: boundary-only")
    assert next(row["result"]["tools"] for row in _calls(runtime, nested, env) if row.get("id") == 2) == []
    isolated = tmp_path / "no-git"; isolated.mkdir(); write_connection(isolated)
    child = isolated / "child"; child.mkdir()
    assert next(row["result"]["tools"] for row in _calls(runtime, child, env) if row.get("id") == 2) == []


@pytest.mark.parametrize("manifest_text", [None, "schema_version: 3\nproject:\n  name: fixture\n  arbitrary: [a, b]\n",
    '{"project": {"name": "fixture"}}'])
def test_common_manifest_without_wiki_and_legacy_separate_file_are_inert(tmp_path, manifest_text):
    (tmp_path / "wiki-atlassian.yaml").write_text('{"enabled":true,"site":"https://invalid.invalid"}')
    if manifest_text is not None:
        manifest = tmp_path / "doc/harness/manifest.yaml"; manifest.parent.mkdir(parents=True)
        manifest.write_text(manifest_text)
    replies = _calls(_runtime(), tmp_path, enabled_environment(tmp_path))
    assert next(row["result"]["tools"] for row in replies if row.get("id") == 2) == []
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("manifest_text", ["wiki: null", "wiki: []", "wiki: {enabled: true}",
    "wiki: {backend: other, enabled: false}", "wiki: {backend: false, enabled: false}",
    "'wiki': {enabled: true}", "{wiki: {enabled: true}}", "wiki: {enabled: false}\nwiki: {enabled: false}"])
def test_common_manifest_rejects_invalid_backend_mapping_and_ambiguous_root(tmp_path, manifest_text):
    manifest = tmp_path / "doc/harness/manifest.yaml"; manifest.parent.mkdir(parents=True)
    manifest.write_text(manifest_text)
    env = enabled_environment(tmp_path)
    result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert not (tmp_path / ".local/share/wiki-atlassian").exists()


@pytest.mark.parametrize("component", ["doc", "harness", "manifest"])
@pytest.mark.skipif(os.name == 'nt', reason='POSIX symlink fixture; native junction/reparse rejection is exercised in test_wiki_windows_native.py')
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
    result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
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
        tools = next(row["result"]["tools"] for row in _calls(_runtime(), tmp_path, env) if row.get("id") == 2)
        assert len(tools) == 10
    else:
        result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
            cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
        assert result.returncode != 0
        assert not (tmp_path / "home/.local/share/wiki-atlassian").exists()
    assert path.read_text() == source


def test_multiple_spaces_and_optional_roots_expose_one_toolset_before_native_resolution(tmp_path):
    write_connection(tmp_path, spaces=[{"key": "TEAM"}, {"key": "DEV", "rootPageId": "456"}])
    replies = _calls(_runtime(), tmp_path, enabled_environment(tmp_path / "home"), requests=[
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
    result = subprocess.run([sys.executable, "-I", "-B", str(_runtime())],
        cwd=tmp_path, env=env, input="", text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "fixture-only-token" not in result.stdout + result.stderr
    assert not (tmp_path / "home/.local/share/wiki-atlassian").exists()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX mode and symlink fixture; native DACL/reparse publication checks have separate coverage')
def test_installer_rejects_writable_storage_and_substituted_publication(tmp_path, monkeypatch):
    runtime = _runtime()
    import importlib.util
    spec = importlib.util.spec_from_file_location("knowledge_install_security", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    data = tmp_path / "unsafe-data"
    packages = data / "marketplace/versions"
    packages.mkdir(parents=True)
    packages.chmod(0o777)
    with pytest.raises(ValueError, match="Unsafe|private"):
        module.prepare(data)
    assert list(packages.iterdir()) == []

    data = tmp_path / "safe-data"
    attacker = tmp_path / "replacement"
    attacker.mkdir()
    real_rename = os.rename

    def replace_at_publication(source, destination):
        displaced = str(source) + "-displaced"
        real_rename(source, displaced)
        os.symlink(attacker, source)
        real_rename(source, destination)

    monkeypatch.setattr(module.os, "rename", replace_at_publication)
    with pytest.raises((ValueError, OSError), match="private|symlink|link|directory|Directory"):
        module.prepare(data)


def test_marketplace_source_stays_stable_without_symlinks_and_preserves_failed_selection(tmp_path, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location("knowledge_install_upgrade", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    first = module.prepare(tmp_path / "data")
    sources = module._source_files()
    sources['README.md'] += b"\nNext version fixture\n"
    monkeypatch.setattr(module, '_source_files', lambda: dict(sources))
    second = module.prepare(tmp_path / "data")
    assert first != second
    def selected(root):
        data = json.loads((root / '.claude-plugin/marketplace.json').read_text(encoding='utf-8'))
        return root / data['plugins'][0]['source']
    with module.marketplace(first) as stable:
        assert selected(stable) == first / 'plugin'
        with pytest.raises(BlockingIOError):
            with module.marketplace(second):
                pytest.fail('A concurrent installer replaced the selection')
    before = (stable / '.claude-plugin/marketplace.json').read_bytes()
    with pytest.raises(RuntimeError):
        with module.marketplace(second):
            raise RuntimeError('registration failed')
    assert (stable / '.claude-plugin/marketplace.json').read_bytes() == before
    assert (first / 'plugin/scripts/launch.py').is_file()
    with module.marketplace(second) as updated:
        assert updated == stable and selected(updated) == second / 'plugin'
    assert not any(path.is_symlink() for path in stable.rglob('*'))
    metadata = stable / '.claude-plugin/marketplace.json'
    data = json.loads(metadata.read_text(encoding='utf-8'))
    data['plugins'][0]['source'] = './unowned-selection'
    metadata.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(ValueError, match='managed payload'):
        with module.marketplace(first):
            pytest.fail('An unknown selection was overwritten')
    assert json.loads(metadata.read_text(encoding='utf-8'))['plugins'][0]['source'] == './unowned-selection'


def test_installer_uses_user_home_when_xdg_location_is_supplied(tmp_path, monkeypatch, capsys):
    import importlib.util
    spec = importlib.util.spec_from_file_location("wiki_install_fixed_home", PLUGIN / "install.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    home = tmp_path / "home"
    override = tmp_path / "unused-override"
    monkeypatch.setenv("XDG_DATA_HOME", str(override))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setattr(module.sys, "argv", ["install.py", "--prepare-only"])
    assert module.main() == 0
    market = Path(capsys.readouterr().out.strip())
    assert market == home / ".local/share/wiki-atlassian/marketplace"
    item = json.loads((market / ".claude-plugin/marketplace.json").read_text(encoding="utf-8"))["plugins"][0]
    assert (market / item["source"] / "skills/setup/SKILL.md").is_file()
    assert not override.exists()
