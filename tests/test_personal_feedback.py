"""Private feedback settings, identity separation and one-shot native delivery."""
import builtins
import copy
import importlib.util
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import sys
import threading
from urllib.error import HTTPError
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "plugin/scripts").is_dir())
SCRIPTS = ROOT / "plugin/scripts"
sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("personal_feedback_test", SCRIPTS / "personal_feedback.py")
feedback = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(feedback)

SITE = "https://feedback.example.invalid"
PAGE = SITE + "/wiki/spaces/TOOLS/pages/790/Harness+feedback"
CREDS = {"ATLASSIAN_BASE_URL": SITE, "ATLASSIAN_EMAIL": "feedback@example.invalid",
         "ATLASSIAN_API_TOKEN": "private-fixture-token"}


def private_settings(home, *, source=None, page=PAGE):
    folder = home / ".config/harness"
    folder.mkdir(parents=True, exist_ok=True)
    home.chmod(0o700)
    folder.parent.chmod(0o700)
    folder.chmod(0o700)
    path = folder / "feedback.yaml"
    path.write_text(source if source is not None else json.dumps({"backend": "atlassian", "page": page}))
    path.chmod(0o600)
    return folder, path


def private_auth(home):
    folder, _ = private_settings(home)
    path = folder / "mise.local.toml"
    path.write_text('[env]\nATLASSIAN_BASE_URL="https://feedback.example.invalid"\n'
                    'ATLASSIAN_EMAIL="feedback@example.invalid"\nATLASSIAN_API_TOKEN="synthetic-only"\n')
    path.chmod(0o600)
    return folder, path


def forbidden(*args, **kwargs):
    raise AssertionError("unexpected credential, network or mutation attempt")


def test_missing_personal_settings_are_readonly_even_with_project_auth(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    project = tmp_path / "project"
    (project / "doc/harness").mkdir(parents=True)
    (project / ".git").mkdir()
    manifest = project / "doc/harness/manifest.yaml"
    manifest.write_text('wiki:\n  backend: atlassian\n  enabled: true\n  site: https://project.invalid\n'
                        '  spaces: [{key: TEAM, feedbackPageId: "123"}]\n')
    monkeypatch.chdir(project)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    original = builtins.__import__
    def no_parser(name, *args, **kwargs):
        if name == "yaml":
            raise AssertionError("absent personal config must not import YAML")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_parser)
    before = {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    result = feedback.perform("status", home=home, environment={
        "ATLASSIAN_BASE_URL": "https://project.invalid", "ATLASSIAN_API_TOKEN": "project-only",
        "HARNESS_FEEDBACK_PAGE": "https://project.invalid/wiki/spaces/A/pages/123"})
    assert result["status"] == "unconfigured"
    assert before == {str(p.relative_to(tmp_path)): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("page", [PAGE, SITE + "/spaces/TOOLS/pages/790",
    SITE + "/wiki/pages/viewpage.action?pageId=790",
    SITE + "/wiki/spaces/~AAID/pages/790/Personal+inbox",
    SITE + "/wiki/spaces/~712020:123e4567-e89b-12d3-a456-426614174000/pages/790"])
def test_private_destination_accepts_documented_native_page_urls(tmp_path, monkeypatch, page):
    private_settings(tmp_path, page=page)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    result = feedback.perform("status", home=tmp_path)
    assert result["status"] == "configured"
    assert "790" in json.dumps(result) and SITE in json.dumps(result)
    assert "private-fixture-token" not in json.dumps(result)


@pytest.mark.parametrize("source", [
    "backend: atlassian\nbackend: atlassian\npage: " + PAGE,
    "backend: atlassian\npage: &page " + PAGE + "\nother: *page",
    "backend: !!str atlassian\npage: " + PAGE,
    json.dumps({"backend": "atlassian", "page": PAGE, "ATLASSIAN_API_TOKEN": "must-not-echo"}),
    json.dumps({"backend": "unknown", "page": PAGE}),
    json.dumps({"backend": "atlassian"}),
    json.dumps({"page": PAGE}),
    "[]", "null", "backend: atlassian\npage: [unterminated", "x" * (64 * 1024 + 1),
])
def test_invalid_private_configuration_fails_closed_and_redacts(tmp_path, monkeypatch, source):
    private_settings(tmp_path, source=source)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    result = feedback.perform("status", home=tmp_path)
    assert result["status"] == "unavailable"
    assert "must-not-echo" not in json.dumps(result)


@pytest.mark.parametrize("page", [
    "http://feedback.example.invalid/wiki/spaces/A/pages/790",
    "https://token:secret@feedback.example.invalid/wiki/spaces/A/pages/790",
    SITE + "/wiki/spaces/A/pages/0", SITE + "/wiki/spaces/A/pages/not-an-id",
    SITE + "/wiki/spaces/A", SITE + "/wiki/spaces/A/pages/790?token=must-not-echo",
    SITE + "/wiki/pages/viewpage.action?pageId=790&pageId=791",
    SITE + "/wiki/spaces/A/pages/790#must-not-echo", " " + PAGE,
    "https://feedback.example.invalid\\evil/wiki/spaces/A/pages/790",
])
def test_unsupported_or_credential_bearing_page_urls_never_escape(tmp_path, monkeypatch, page):
    private_settings(tmp_path, page=page)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    result = feedback.perform("status", home=tmp_path)
    assert result["status"] == "unavailable"
    assert "must-not-echo" not in json.dumps(result) and "secret@" not in json.dumps(result)


@pytest.mark.parametrize("component", ["file", "harness", "config"])
def test_symlinked_private_configuration_components_are_not_followed(tmp_path, monkeypatch, component):
    home = tmp_path / "home"
    folder, path = private_settings(home)
    if component == "file":
        target = tmp_path / "target.yaml"
        path.rename(target)
        path.symlink_to(target)
    elif component == "harness":
        target = tmp_path / "target-harness"
        folder.rename(target)
        folder.symlink_to(target, target_is_directory=True)
    else:
        target = tmp_path / "target-config"
        folder.parent.rename(target)
        folder.parent.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("status", home=home)["status"] == "unavailable"


@pytest.mark.parametrize("mode", [0o644, 0o620, 0o606, 0o666])
def test_nonprivate_destination_permissions_are_rejected(tmp_path, mode):
    _, path = private_settings(tmp_path)
    path.chmod(mode)
    assert feedback.load_destination(home=tmp_path)["status"] == "unavailable"


def mise_result(path, **changes):
    result = {key: {"source": str(path), "value": value} for key, value in CREDS.items()}
    result.update(changes)
    return subprocess.CompletedProcess(["mise"], 0, stdout=json.dumps(result), stderr="")


def test_private_auth_evaluation_scrubs_project_identity_and_uses_exact_provenance(tmp_path, monkeypatch):
    folder, path = private_auth(tmp_path)
    seen = {}
    def process(argv, **kwargs):
        seen.update(argv=argv, **kwargs)
        return mise_result(path)
    monkeypatch.setattr(feedback, "_private_mise", process)
    monkeypatch.setattr(feedback.shutil, "which", lambda name, **kwargs: "/usr/local/bin/mise")
    original = {"PATH": "/usr/local/bin:/usr/bin", "HOME": str(tmp_path),
                "ATLASSIAN_API_TOKEN": "project-token", "ATLASSIAN_EMAIL": "project@example.invalid",
                "ATLASSIAN_BASE_URL": "https://project.invalid", "MISE_ENV": "project",
                "MISE_CONFIG_FILE": "/project/mise.toml", "__MISE_ORIG_PATH": "/project/bin"}
    before = original.copy()
    assert feedback._credentials(folder, original) == CREDS
    assert original == before
    assert Path(seen["cwd"]) == folder
    assert "--json-extended" in seen["argv"]
    assert not any(key.startswith(("ATLASSIAN_", "__MISE_")) for key in seen["env"])
    assert "MISE_ENV" not in seen["env"] and "MISE_CONFIG_FILE" not in seen["env"]
    assert "project-token" not in json.dumps(seen["argv"])


@pytest.mark.parametrize("key", sorted(CREDS))
@pytest.mark.parametrize("fault", ["global-source", "missing", "empty"])
def test_private_auth_never_falls_back_to_global_or_project_values(tmp_path, monkeypatch, key, fault):
    folder, path = private_auth(tmp_path)
    result = json.loads(mise_result(path).stdout)
    if fault == "global-source":
        result[key]["source"] = str(tmp_path / ".config/mise/config.toml")
    elif fault == "missing":
        del result[key]
    else:
        result[key]["value"] = ""
    monkeypatch.setattr(feedback, "_private_mise", lambda *a, **k:
                        subprocess.CompletedProcess(a, 0, json.dumps(result), ""))
    monkeypatch.setattr(feedback.shutil, "which", lambda name, **kwargs: "/usr/local/bin/mise")
    monkeypatch.setattr(feedback, "_request", forbidden)
    output = feedback.perform("check", home=tmp_path, environment=dict(CREDS))
    assert output["status"] == "unavailable"
    assert CREDS["ATLASSIAN_API_TOKEN"] not in json.dumps(output)


def test_untrusted_mise_is_not_auto_trusted_and_error_is_redacted(tmp_path, monkeypatch):
    private_auth(tmp_path)
    calls = []
    def process(argv, **kwargs):
        calls.append(argv)
        return subprocess.CompletedProcess(argv, 1, "", "untrusted private-fixture-token")
    monkeypatch.setattr(feedback, "_private_mise", process)
    monkeypatch.setattr(feedback.shutil, "which", lambda name, **kwargs: "/usr/local/bin/mise")
    monkeypatch.setattr(feedback, "_request", forbidden)
    output = feedback.perform("check", home=tmp_path, environment={})
    assert output["status"] == "unavailable"
    assert len(calls) == 1 and "trust" not in calls[0]
    assert "private-fixture-token" not in json.dumps(output)


def test_real_mise_private_account_is_independent_of_project_environment(tmp_path, monkeypatch):
    mise = shutil.which("mise")
    if not mise:
        pytest.skip("mise is not installed")
    home = tmp_path / "isolated-home"
    folder, auth = private_auth(home)
    environment = {k: v for k, v in os.environ.items()
                   if not k.startswith(("MISE_", "__MISE_", "ATLASSIAN_"))}
    environment.update(HOME=str(home), USERPROFILE=str(home),
                       XDG_CONFIG_HOME=str(home / ".config"), XDG_DATA_HOME=str(home / ".local/share"),
                       XDG_CACHE_HOME=str(home / ".cache"), XDG_STATE_HOME=str(home / ".local/state"),
                       APPDATA=str(home / "AppData/Roaming"), LOCALAPPDATA=str(home / "AppData/Local"),
                       ATLASSIAN_BASE_URL="https://project.invalid",
                       ATLASSIAN_EMAIL="project@example.invalid", ATLASSIAN_API_TOKEN="project-token")
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("check", home=home, environment=environment)["status"] == "unavailable"
    subprocess.run([mise, "trust", str(auth)], cwd=folder, env=environment,
                   capture_output=True, text=True, timeout=20, check=True)
    try:
        selected = feedback._credentials(folder, environment)
        assert selected["ATLASSIAN_EMAIL"] == "feedback@example.invalid"
        assert selected["ATLASSIAN_API_TOKEN"] == "synthetic-only"
        assert environment["ATLASSIAN_EMAIL"] == "project@example.invalid"
    finally:
        subprocess.run([mise, "trust", "--untrust", str(auth)], cwd=folder, env=environment,
                       capture_output=True, text=True, timeout=20, check=True)


class NativeComments:
    def __init__(self):
        self.calls = []
        self.created = None
        self.change = None
        self.failure = None

    def __call__(self, site, credentials, method, path, body=None):
        assert site == SITE
        assert credentials == CREDS
        self.calls.append((method, path, copy.deepcopy(body)))
        route = urlsplit(path).path
        if route == "/wiki/rest/api/user/current":
            return {"accountId": "private-user"}
        if route == "/wiki/api/v2/pages/790":
            return {"id": "790", "status": "current"}
        if method == "POST":
            assert route == "/wiki/api/v2/footer-comments"
            self.created = body
            if self.failure == "post":
                raise OSError("lost response with private-fixture-token")
            if self.failure == "post-id":
                return {"id": 900}
            return {"id": "900"}
        assert route == "/wiki/api/v2/footer-comments/900"
        if self.failure == "readback":
            raise OSError("failed readback with private-fixture-token")
        result = {"id": "900", "pageId": "790", "status": "current",
                  "version": {"number": 1, "authorId": "private-user"},
                  "body": {"storage": {"representation": "storage", "value": self.created["body"]["value"]}}}
        if self.change:
            self.change(result)
        return result


def transport(home, monkeypatch):
    private_auth(home)
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k: CREDS.copy())
    server = NativeComments()
    monkeypatch.setattr(feedback, "_request", server)
    return server


def test_auth_origin_mismatch_prevents_all_network_calls(tmp_path, monkeypatch):
    private_auth(tmp_path)
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k:
                        dict(CREDS, ATLASSIAN_BASE_URL="https://project.invalid"))
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("send", report="Do not leak", home=tmp_path)["status"] == "unavailable"


def test_check_reads_private_account_and_page_without_creating_a_comment(tmp_path, monkeypatch):
    server = transport(tmp_path, monkeypatch)
    result = feedback.perform("check", home=tmp_path)
    assert result["status"] == "checked"
    assert [call[0] for call in server.calls] == ["GET", "GET"]
    assert "private-fixture-token" not in json.dumps(result)


def test_send_adds_one_escaped_comment_and_verifies_it_without_echoing_report(tmp_path, monkeypatch):
    server = transport(tmp_path, monkeypatch)
    report = "Sensitive report context <script>alert(1)</script> & text\n두 번째 줄"
    output = feedback.perform("send", report=report, home=tmp_path)
    assert output["status"] == "sent"
    assert sum(method == "POST" for method, _, _ in server.calls) == 1
    assert server.created["pageId"] == "790" and "parentCommentId" not in server.created
    value = server.created["body"]["value"]
    parsed = ET.fromstring(value)
    assert not list(parsed.iter("script"))
    assert "<script>alert(1)</script> & text" in "".join(parsed.itertext())
    assert "두 번째 줄" in "".join(parsed.itertext())
    assert report not in json.dumps(output) and "Sensitive report context" not in json.dumps(output)
    assert CREDS["ATLASSIAN_API_TOKEN"] not in json.dumps(output)
    assert "900" in json.dumps(output)


@pytest.mark.parametrize("failure", ["post", "post-id", "readback"])
def test_uncertain_native_write_is_not_retried_or_reported_as_success(tmp_path, monkeypatch, failure):
    server = transport(tmp_path, monkeypatch)
    server.failure = failure
    output = feedback.perform("send", report="A report", home=tmp_path)
    assert output["status"] == "unknown"
    assert sum(method == "POST" for method, _, _ in server.calls) == 1
    assert "private-fixture-token" not in json.dumps(output)
    if failure == "readback":
        assert "900" in json.dumps(output)


@pytest.mark.parametrize("change", [
    lambda d: d.update(id="901"), lambda d: d.update(pageId="791"),
    lambda d: d.update(status="deleted"), lambda d: d.update(parentCommentId="850"),
    lambda d: d.update(blogPostId="790"), lambda d: d["version"].update(number=2),
    lambda d: d["version"].update(authorId="project-user"),
    lambda d: d["version"].update(number=True), lambda d: d["version"].update(number="1"),
    lambda d: d.update(version=None), lambda d: d.update(body=None),
    lambda d: d["body"]["storage"].update(value="<p>different</p>"),
])
def test_readback_cannot_promote_another_comment_or_identity(tmp_path, monkeypatch, change):
    server = transport(tmp_path, monkeypatch)
    server.change = change
    output = feedback.perform("send", report="A report", home=tmp_path)
    assert output["status"] == "unknown"
    assert sum(method == "POST" for method, _, _ in server.calls) == 1
    assert "900" in json.dumps(output)


@pytest.mark.parametrize("report", ["", "  \n", "a" * (128 * 1024 + 1)])
def test_invalid_report_never_reaches_native_transport(tmp_path, monkeypatch, report):
    private_settings(tmp_path)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("send", report=report, home=tmp_path)["status"] == "unavailable"


def test_invalid_utf8_and_hardlinked_private_settings_fail_closed(tmp_path):
    _, path = private_settings(tmp_path)
    path.write_bytes(b"backend: atlassian\npage: \xff")
    assert feedback.load_destination(home=tmp_path)["status"] == "unavailable"
    private_settings(tmp_path)
    os.link(path, tmp_path / "also-feedback.yaml")
    assert feedback.load_destination(home=tmp_path)["status"] == "unavailable"


def test_missing_yaml_parser_does_not_trigger_auth_or_network(tmp_path, monkeypatch):
    private_settings(tmp_path)
    original = builtins.__import__
    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ImportError("synthetic optional parser unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("status", home=tmp_path)["status"] == "unavailable"


@pytest.mark.parametrize("fault", ["unsafe-mode", "symlink"])
def test_private_auth_file_must_be_safe_before_mise_executes(tmp_path, monkeypatch, fault):
    _, path = private_auth(tmp_path)
    if fault == "unsafe-mode":
        path.chmod(0o644)
    else:
        target = tmp_path / "target.toml"
        path.rename(target)
        path.symlink_to(target)
    monkeypatch.setattr(feedback.subprocess, "run", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    assert feedback.perform("check", home=tmp_path)["status"] == "unavailable"


def test_report_containing_the_private_token_is_not_sent(tmp_path, monkeypatch):
    private_auth(tmp_path)
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k: CREDS.copy())
    monkeypatch.setattr(feedback, "_request", forbidden)
    result = feedback.perform("send", report="accidental " + CREDS["ATLASSIAN_API_TOKEN"], home=tmp_path)
    assert result["status"] == "unavailable"
    assert CREDS["ATLASSIAN_API_TOKEN"] not in json.dumps(result)


def test_http_transport_refuses_redirect_instead_of_forwarding_credentials(monkeypatch):
    received = []
    class Sink(BaseHTTPRequestHandler):
        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        def log_message(self, *args):
            pass
    sink = ThreadingHTTPServer(("127.0.0.1", 0), Sink)
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", f"http://127.0.0.1:{sink.server_port}/captured")
            self.end_headers()
        def log_message(self, *args):
            pass
    source = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (source, sink)]
    for thread in threads:
        thread.start()
    try:
        with pytest.raises(HTTPError) as caught:
            feedback._request(f"http://127.0.0.1:{source.server_port}", CREDS, "GET", "/redirect")
        assert caught.value.code == 302
        assert received == []
    finally:
        for server in (source, sink):
            server.shutdown()
            server.server_close()
        for thread in threads:
            thread.join(timeout=2)


@pytest.mark.parametrize("fault", ["status-line", "chunked-truncation"])
@pytest.mark.parametrize("stage", ["preflight", "post", "readback"])
def test_actual_http_protocol_failure_retains_effect_uncertainty(tmp_path, monkeypatch, capsys, fault, stage):
    """Exercise http.client's real exceptions through urllib, not a mocked raise."""
    private_auth(tmp_path)
    monkeypatch.setattr(feedback, "_credentials", lambda *a, **k: CREDS.copy())
    requests = []
    class NativeServer(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def broken(self):
            if fault == "status-line":
                # A server/proxy error may contain sensitive text. It must not
                # reach an exception traceback or user-visible result.
                self.connection.sendall(b"private-fixture-token\r\n\r\n")
            else:
                status = b"201 Created" if self.command == "POST" else b"200 OK"
                self.connection.sendall(b"HTTP/1.1 " + status + b"\r\nTransfer-Encoding: chunked\r\n\r\n20\r\nshort")
            self.close_connection = True

        def respond(self, status, payload):
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            requests.append(("GET", self.path))
            if self.path == "/wiki/rest/api/user/current":
                if stage == "preflight":
                    self.broken()
                else:
                    self.respond(200, {"accountId": "private-user"})
            elif self.path == "/wiki/api/v2/pages/790":
                self.respond(200, {"id": "790", "status": "current"})
            else:
                assert self.path.startswith("/wiki/api/v2/footer-comments/900?")
                assert stage == "readback"
                self.broken()

        def do_POST(self):
            requests.append(("POST", self.path))
            size = int(self.headers["Content-Length"])
            payload = json.loads(self.rfile.read(size))
            assert payload["pageId"] == "790"
            assert self.path == "/wiki/api/v2/footer-comments"
            # Reaching this handler represents an accepted native mutation;
            # a broken acknowledgement cannot safely be treated as no write.
            if stage == "post":
                self.broken()
            else:
                self.respond(201, {"id": "900"})

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), NativeServer)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    real_request = feedback._request
    def loopback(site, credentials, method, path, body=None):
        assert site == SITE
        return real_request(f"http://127.0.0.1:{server.server_port}", credentials, method, path, body)
    monkeypatch.setattr(feedback, "_request", loopback)
    try:
        result = feedback.perform("send", report="Private feedback body", home=tmp_path)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
    assert result["status"] == ("unavailable" if stage == "preflight" else "unknown")
    assert sum(method == "POST" for method, _ in requests) == (0 if stage == "preflight" else 1)
    if stage == "readback":
        assert "900" in json.dumps(result)
    captured = capsys.readouterr()
    rendered = json.dumps(result) + captured.out + captured.err
    assert "private-fixture-token" not in rendered and "Traceback" not in rendered
    assert "Private feedback body" not in rendered


def test_packaged_help_explains_private_setup_without_source_readme(tmp_path):
    package = tmp_path / "installed/scripts"
    package.mkdir(parents=True)
    for name in ("personal_feedback.py", "document_destination.py", "wiki_platform.py", "_wiki_windows.py"):
        shutil.copyfile(SCRIPTS / name, package / name)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    cwd = tmp_path / "consumer"
    cwd.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MISE_", "__MISE_", "ATLASSIAN_", "HARNESS_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-S", "-B", str(package / "personal_feedback.py"), "--help"],
                            cwd=cwd, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and not result.stderr
    assert "~/.config/harness/feedback.yaml" in result.stdout
    assert "~/.config/harness/mise.local.toml" in result.stdout
    for name in CREDS:
        assert name in result.stdout
    assert "backend:" in result.stdout and "page:" in result.stdout
    assert "0600" in result.stdout and "mise trust" in result.stdout
    assert not list(home.iterdir()) and not list(cwd.iterdir())


def test_cli_native_unicode_result_survives_restrictive_redirected_encoding(monkeypatch):
    import io

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1252")
    monkeypatch.setattr(feedback.sys, "stdout", stream)
    monkeypatch.setattr(feedback.sys, "stdin", io.TextIOWrapper(io.BytesIO(b"{}"), encoding="utf-8"))
    monkeypatch.setattr(feedback.sys, "argv", ["personal_feedback.py", "read"])
    result = {"status": "read", "title": "한국어 문서", "body": "<p>中文</p>"}
    monkeypatch.setattr(feedback, "perform", lambda *args, **kwargs: result)
    assert feedback.main() == 0
    stream.flush()
    assert json.loads(raw.getvalue().decode("utf-8")) == result


@pytest.mark.parametrize("payload", [None, [], True, 10, {"limit": 0}, {"limit": "bad"},
                                     {"limit": True}, {"cursor": ""}, {"cursor": []},
                                     {"cursor": "x" * 8193}, {"extra": "ignored"}])
def test_actual_read_cli_rejects_invalid_options_without_traceback(tmp_path, payload):
    home = tmp_path / "isolated-home"
    home.mkdir(mode=0o700)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.upper().startswith(("MISE_", "__MISE_", "ATLASSIAN_", "HARNESS_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
    result = subprocess.run([sys.executable, "-I", "-B", str(SCRIPTS / "personal_feedback.py"), "read"],
                            input=json.dumps(payload).encode("utf-8"), cwd=consumer, env=env,
                            capture_output=True, timeout=10)
    assert result.returncode == 1 and result.stderr == b""
    assert json.loads(result.stdout)["status"] == "unavailable"
    assert not list(home.iterdir()) and not list(consumer.iterdir())


def test_public_cli_default_help_and_invalid_action_from_unrelated_project(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".git").mkdir()
    env = {key: value for key, value in os.environ.items()
           if not key.startswith(("MISE_", "__MISE_", "ATLASSIAN_", "HARNESS_"))}
    env.update(HOME=str(home), USERPROFILE=str(home), PYTHONDONTWRITEBYTECODE="1")
    command = [sys.executable, "-B", str(SCRIPTS / "personal_feedback.py")]
    result = subprocess.run(command, cwd=project, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and json.loads(result.stdout)["status"] == "unconfigured"
    result = subprocess.run(command + ["--help"], cwd=project, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 0 and "send" in result.stdout
    result = subprocess.run(command + ["bogus"], cwd=project, env=env, capture_output=True, text=True, timeout=10)
    assert result.returncode == 2
    assert sorted(p.name for p in project.iterdir()) == [".git"] and not list(home.iterdir())
