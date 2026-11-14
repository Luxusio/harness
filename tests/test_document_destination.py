"""Project-selected document policy and actual prompt injection boundaries."""

from test_symlink_fixture import create_symlink
import builtins
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
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("document_destination_test", SCRIPTS / "document_destination.py")
destination = importlib.util.module_from_spec(spec)
spec.loader.exec_module(destination)


def connection(**changes):
    env = dict(ATLASSIAN_BASE_URL="https://example.invalid/", ATLASSIAN_EMAIL="fixture@example.invalid",
               ATLASSIAN_API_TOKEN="fixture-only-token")
    env.update(changes)
    return env


def config(**changes):
    result = dict(backend="atlassian", enabled=True, site="https://example.invalid", spaces=[{"key": "TEAM", "rootPageId": "456"}])
    result.update(changes)
    return result


def manifest_path(project):
    return project / "doc/harness/manifest.yaml"


def write_config(project, values=None, *, raw=None):
    import yaml
    project.mkdir(parents=True, exist_ok=True)
    path = manifest_path(project)
    path.parent.mkdir(parents=True, exist_ok=True)
    if raw is not None:
        path.write_text("type: library\nwiki:\n" + "\n".join("  " + line for line in raw.splitlines()) + "\n")
    else:
        manifest = yaml.safe_load(path.read_text()) if path.exists() else {"type": "library"}
        manifest["wiki"] = config() if values is None else values
        path.write_text(json.dumps(manifest))
    return project


def harness_project(project):
    manifest = project / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    if not manifest.exists():
        manifest.write_text("type: library\n")
    (project / ".git").mkdir(exist_ok=True)
    (manifest.parent / "runbooks.yaml").write_text(
        'runbooks:\n  probe:\n    description: local-recipe-sentinel\n    command: python -V\n')
    return project


def clean_env(home, settings=None):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("HARNESS_", "CLAUDE_", "CODEX_", "KNOWLEDGE_", "WIKI_", "ATLASSIAN_", "JIRA_"))}
    env.update(HOME=str(home), PYTHONDONTWRITEBYTECODE="1")
    env.update(settings or {})
    return env


def prompt(project, home, settings=None, *, process_cwd=None):
    harness_project(project)
    return subprocess.run([sys.executable, "-B", str(SCRIPTS / "prompt_memory.py")],
                          cwd=process_cwd or project, env=clean_env(home, settings),
                          input=json.dumps({"session_id": "destination-test", "cwd": str(project)}),
                          text=True, capture_output=True, timeout=10)


def test_absent_old_env_and_shared_credentials_are_local_without_parser(tmp_path, monkeypatch):
    original = builtins.__import__
    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise AssertionError("missing config must not import parser")
        return original(name, *args, **kwargs)
    manifest = manifest_path(tmp_path)
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: library\nproject_meta:\n  shape: library\n")
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    env = dict(connection(), WIKI_ATLASSIAN_ENABLED="true", WIKI_ATLASSIAN_SITE="https://stale.invalid")
    assert destination.select_destination(env, cwd=tmp_path)["mode"] == "local"
    assert list(tmp_path.iterdir()) == [tmp_path / "doc"]


def test_disabled_can_omit_connection_and_auth(tmp_path):
    write_config(tmp_path, {"enabled": False})
    assert destination.select_destination({}, cwd=tmp_path)["mode"] == "local"


def test_local_guidance_defers_document_review_to_effective_wbs_policy(tmp_path):
    selected = destination.policy({}, cwd=tmp_path)
    assert selected["mode"] == "local"
    assert "effective WBS policy for document review" in selected["policy"]
    assert "independent document review" not in selected["policy"]


def test_multiple_spaces_and_optional_root_are_public_choices_without_native_ids(tmp_path):
    spaces = [{"key": "TEAM"}, {"key": "DEV", "rootPageId": "456", "memoryPageId": "789",
                                "feedbackPageId": "790", "watchCommentPages": "456,789"}]
    project = write_config(tmp_path, config(spaces=spaces))
    before = manifest_path(project).read_bytes()
    selected = destination.select_destination(connection(), cwd=project)
    assert selected["mode"] == "external"
    assert selected["config"]["spaces"] == spaces
    assert "spaceId" not in json.dumps(selected) and "fixture-only-token" not in json.dumps(selected)
    assert manifest_path(project).read_bytes() == before


@pytest.mark.parametrize("spaces", [None, {}, "TEAM", [], ["TEAM"], [None], [{}],
    [{"key": "TEAM"}, {"key": "TEAM", "rootPageId": "456"}],
    [{"key": True}], [{"key": "../TEAM"}], [{"key": ""}], [{"key": "a" * 101}],
    [{"key": "TEAM", "spaceId": "123"}], [{"key": "TEAM", "site": "https://other.invalid"}],
    [{"key": "TEAM", "rootPageId": 456}], [{"key": "TEAM", "rootPageId": "0"}],
    [{"key": "TEAM", "memoryPageId": ""}], [{"key": "TEAM", "feedbackPageId": "../1"}],
    [{"key": "TEAM", "watchCommentPages": "101,101"}], [{"key": "TEAM", "watchCommentPages": 101}],
    [{"key": "TEAM", "watchCommentPages": "0"}],
    [{"key": "TEAM", "watchCommentPages": ",".join(str(i) for i in range(1, 12))}],
    [{"key": "TEAM", "apiToken": "private-yaml-secret"}],
    [{"key": "S" + str(i)} for i in range(33)]])
def test_invalid_space_selections_never_fall_back_or_echo_values(tmp_path, spaces):
    for enabled in (True, False):
        write_config(tmp_path, config(enabled=enabled, spaces=spaces))
        selected = destination.policy(connection(), cwd=tmp_path)
        assert selected["mode"] == "unavailable"
        assert "private-yaml-secret" not in json.dumps(selected)


def test_space_list_resource_bound_and_optional_fields_keep_strict_native_types(tmp_path):
    spaces = [{"key": "S" + str(i)} for i in range(32)]
    write_config(tmp_path, config(spaces=spaces))
    assert destination.select_destination(connection(), cwd=tmp_path)["config"]["spaces"] == spaces
    for key in ("rootPageId", "memoryPageId", "feedbackPageId"):
        for value in ("1", "123456789012345678901234567890"):
            write_config(tmp_path, config(spaces=[{"key": "TEAM", key: value}]))
            assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "external"
    write_config(tmp_path, config(enabled=False, spaces=[{"key": "TEAM"}]))
    assert destination.select_destination({}, cwd=tmp_path)["mode"] == "local"


def test_old_single_space_or_mixed_schema_is_rejected(tmp_path):
    for key, value in (("space", "TEAM"), ("spaceId", "123"), ("rootPageId", "456"),
                       ("memoryPageId", "789"), ("watchCommentPages", "456")):
        write_config(tmp_path, config(**{key: value}))
        assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"
    write_config(tmp_path, {"backend": "atlassian", "enabled": True, "site": "https://example.invalid",
                            "space": "TEAM", "spaceId": "123", "rootPageId": "456"})
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


@pytest.mark.parametrize("changes", [dict(enabled="true"), dict(enabled=1), dict(site="http://example.invalid"),
    dict(site="https://secret@example.invalid"), dict(site="https://example.invalid:0"), dict(site="https://example.invalid:65536"),
    dict(site="https://example.invalid:"), dict(site="https://example.invalid?"), dict(site="https://example.invalid/#"),
    dict(site="https://bad_host.invalid"), dict(site="https://example.invalid\\evil"), dict(site=" https://example.invalid"),
    dict(site="https://[::ffff:0127.0.0.1]:9"), dict(site="https://1.2.3"), dict(site="https://999.999.999.999"),
    dict(site="https://example.123"), dict(site="https://[v1.fe]"), dict(site="https://[v1.fe:abcd]"),
    dict(site="https://[::1%eth-0]"), dict(site="https://example.invalid.."), dict(site="https://example.1a"),
    dict(rootPageId="../1"), dict(spaceId="0"), dict(space="../TEAM"), dict(rootPageId=456), dict(memoryPageId=456),
    dict(memoryPageId=""), dict(feedbackPageId="0"), dict(pollIntervalSeconds=29), dict(pollIntervalSeconds=86401),
    dict(pollIntervalSeconds="300"), dict(pollIntervalSeconds=True), dict(watchCommentPages="101,101"),
    dict(watchCommentPages=101), dict(watchCommentPages="0"), dict(watchCommentPages=",".join(str(i) for i in range(1, 12))),
    dict(backend="other"), dict(backend=None), dict(ATLASSIAN_EMAIL="secret@example.invalid"), dict(auth={"token": "private-yaml-secret"})])
def test_invalid_yaml_fields_are_pending_without_value_echo(tmp_path, changes):
    write_config(tmp_path, config(**changes))
    result = destination.policy(connection(), cwd=tmp_path)
    assert result["mode"] == "unavailable"
    assert "secret@" not in json.dumps(result) and "private-yaml-secret" not in json.dumps(result)
    assert "fixture-only-token" not in json.dumps(result)


@pytest.mark.parametrize("changes", [dict(site="broken"), dict(rootPageId=123), dict(pollIntervalSeconds="300"), dict(unknown="value")])
def test_disabled_still_rejects_invalid_provided_fields(tmp_path, changes):
    write_config(tmp_path, config(enabled=False, **changes))
    assert destination.select_destination({}, cwd=tmp_path)["mode"] == "unavailable"


@pytest.mark.parametrize("raw", ["", "[]", "enabled: true\nenabled: false\n", "enabled: &flag true\nsite: *flag\n",
    "enabled: !!python/object:builtins.str {}\n", "enabled: &flag false\n", "enabled: false\nsite: !!str https://example.invalid\n", "enabled: false\nunknown: " + "[" * 22 + "0" + "]" * 22,
    "enabled: false\nsite: [", "enabled: false\nsite: !!binary aGVsbG8=", "? [a,b]\n: value", "enabled: false\n---\nenabled: true\n"])
def test_unsafe_or_malformed_yaml_is_unavailable(tmp_path, raw):
    write_config(tmp_path, raw=raw)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


def test_present_file_without_parser_is_unavailable(tmp_path, monkeypatch):
    write_config(tmp_path)
    original = builtins.__import__
    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("yaml unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


@pytest.mark.parametrize("kind", ["oversized", "invalid_utf8", "symlink", "directory"])
def test_unsafe_config_file_is_unavailable(tmp_path, kind):
    target = manifest_path(tmp_path)
    target.parent.mkdir(parents=True)
    if kind == "oversized":
        target.write_bytes(b"#" * (64 * 1024 + 1))
    elif kind == "invalid_utf8":
        target.write_bytes(b"\xff")
    elif kind == "directory":
        target.mkdir()
    else:
        source = tmp_path / "other"
        source.write_text("enabled: false")
        create_symlink(target, source)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


@pytest.mark.parametrize("changes", [dict(ATLASSIAN_EMAIL=""), dict(ATLASSIAN_API_TOKEN=" "), dict(ATLASSIAN_BASE_URL=""),
    dict(ATLASSIAN_BASE_URL="https://other.invalid"), dict(ATLASSIAN_BASE_URL="http://example.invalid"),
    dict(ATLASSIAN_BASE_URL="https://example.invalid/path"), dict(ATLASSIAN_BASE_URL="https://private@example.invalid")])
def test_auth_origin_pin_and_missing_credentials_are_pending(tmp_path, changes):
    write_config(tmp_path)
    selected = destination.policy(connection(**changes), cwd=tmp_path)
    assert selected["mode"] == "unavailable"
    assert "sync_pending" in selected["policy"]
    assert "does not by itself invalidate applicable product results or block PR readiness" in selected["policy"]
    assert "explicit current acceptance dependency on publication remains unfinished" in selected["policy"]
    assert "local fallback" in selected["policy"]


def test_required_fields_and_valid_boundaries(tmp_path):
    for key in ("backend", "enabled", "site", "spaces"):
        values = config(); values.pop(key)
        write_config(tmp_path, values)
        assert destination.policy(connection(), cwd=tmp_path)["mode"] == "unavailable"
    for seconds in (30, 86400):
        write_config(tmp_path, config(pollIntervalSeconds=seconds))
        assert destination.policy(connection(), cwd=tmp_path)["config"]["pollIntervalSeconds"] == seconds
    for site in ("https://example.invalid:1", "https://example.invalid:65535/", "https://[::1]:443", "https://[::1%eth_0]", "https://123.", "https://[::ffff:127.0.0.1]:9"):
        write_config(tmp_path, config(site=site))
        assert destination.policy(connection(ATLASSIAN_BASE_URL=site.rstrip("/")), cwd=tmp_path)["mode"] == "external"
    write_config(tmp_path)
    assert destination.select_destination(connection(), cwd=tmp_path)["config"]["pollIntervalSeconds"] == 300


@pytest.mark.parametrize("gitfile", [False, True])
def test_nearest_git_root_and_nested_boundary(tmp_path, gitfile):
    parent = write_config(tmp_path / "parent", config(site="https://parent.invalid"))
    (parent / ".git").mkdir()
    repo = write_config(parent / "repo")
    if gitfile:
        (repo / ".git").write_text("gitdir: /unused/native-only\n")
    else:
        (repo / ".git").mkdir()
    nested = repo / "nested"; nested.mkdir()
    write_config(nested, {"enabled": False})  # ignored below a Git root
    assert destination.select_destination(connection(), cwd=nested)["mode"] == "external"
    manifest_path(repo).unlink()
    assert destination.select_destination(connection(), cwd=nested)["mode"] == "local"


def test_no_git_uses_only_explicit_cwd_and_no_private_config_fallback(tmp_path):
    parent = write_config(tmp_path / "parent")
    child = parent / "child"; child.mkdir()
    for name in (".config/knowledge-confluence/config.json", ".config/wiki-atlassian/config.json", "mise.local.toml"):
        path = child / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("invalid-secret-config")
    assert destination.policy(connection(), cwd=child)["mode"] == "local"
    write_config(child)
    stale = dict(connection(), WIKI_ATLASSIAN_ENABLED="false", WIKI_ATLASSIAN_SITE="https://stale.invalid")
    assert destination.policy(stale, cwd=child)["mode"] == "external"


def test_two_projects_remote_guidance_uses_payload_cwd(tmp_path):
    home = tmp_path / "home"
    unrelated = harness_project(tmp_path / "process")
    for name in ("a", "b"):
        project = write_config(tmp_path / name)
        result = prompt(project, home, connection(), process_cwd=unrelated)
        assert result.returncode == 0, result.stderr
        assert "search/read/update with atlassian MCP" in result.stdout
        assert "Durable decisions require doc/" not in result.stdout
        assert "local-recipe-sentinel" not in result.stdout


def test_invalid_connection_exposes_pending_not_local_writing(tmp_path):
    project = write_config(tmp_path / "project", config(site="invalid"))
    result = prompt(project, tmp_path / "home", connection())
    assert result.returncode == 0
    assert "business-document writes remain pending" in result.stdout
    assert "Durable decisions require doc/" not in result.stdout
    assert "local-recipe-sentinel" not in result.stdout


@pytest.mark.parametrize("state", ["missing", "disabled", "external", "invalid"])
def test_actual_session_banner_uses_payload_destination(tmp_path, state):
    project = harness_project(tmp_path / "project")
    if state != "missing":
        write_config(project, config(enabled=state != "disabled", site="broken" if state == "invalid" else "https://example.invalid"))
    unrelated = harness_project(tmp_path / "process")
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / "hook_session_start.py"), "--claude-banner"],
                            cwd=unrelated, env=clean_env(tmp_path / "home", connection()), input=json.dumps({"cwd": str(project)}),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "harness — ready" in result.stdout
    if state in ("missing", "disabled"):
        assert "REQ: capture requirements as doc/" in result.stdout
        assert "atlassian MCP" not in result.stdout
    else:
        assert "REQ: capture requirements as doc/" not in result.stdout
        assert ("atlassian MCP" if state == "external" else "business-document writes remain pending") in result.stdout


def test_external_req_gate_uses_explicit_repo_root(tmp_path, monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    from prewrite_gate import _wbs_has_req_reference
    for key, value in connection().items():
        monkeypatch.setenv(key, value)
    repo = write_config(tmp_path / "repo")
    (repo / ".git").mkdir()
    wbs = repo / "doc/harness/wbs/WBS__example"; wbs.mkdir(parents=True)
    plan = wbs / "PLAN.md"
    monkeypatch.chdir(tmp_path)
    for body in ("REQ: doc/ui/REQ__example.md", "REQ: confluence:123", "REQ: confluence:123@0",
                 "GUIDE: confluence:123@4", "An example is REQ: confluence:123@4"):
        plan.write_text(body)
        assert not _wbs_has_req_reference(str(wbs), str(repo))
    plan.write_text("# Plan\nREQ: confluence:123@4\n")
    assert _wbs_has_req_reference(str(wbs), str(repo))
    assert _wbs_has_req_reference(str(wbs))  # infer repo from canonical WBS location
    write_config(repo, config(site="broken"))
    assert _wbs_has_req_reference(str(wbs), str(repo))  # reference presence survives an unavailable connection


@pytest.mark.parametrize("present", [False, True])
def test_actual_selector_without_site_packages_distinguishes_absent_and_present(tmp_path, present):
    if present:
        write_config(tmp_path)
    result = subprocess.run([sys.executable, "-S", "-B", str(SCRIPTS / "document_destination.py")],
                            cwd=tmp_path, env=clean_env(tmp_path / "home", connection()),
                            text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["mode"] == ("unavailable" if present else "local")


@pytest.mark.parametrize("source", [
    "type: library\n",
    "---\n# fixture\ntype: library\nlanguages: [python, markdown]\nqa:\n  browser_qa_supported: false\n",
    "type: library\ncustom:\n  wiki:\n    enabled: true\n",
])
def test_ordinary_manifest_without_wiki_needs_no_parser(tmp_path, monkeypatch, source):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    path.write_text(source)
    original = builtins.__import__
    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise AssertionError("ordinary no-wiki manifest must stay stdlib-only")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "local"


@pytest.mark.parametrize("source", [
    '"wiki": {enabled: false}', '"w\\u0069ki": {enabled: false}',
    '{type: library}', '? wiki\n: {enabled: false}',
    '---\ntype: library\n---\ntype: other', '', '# only a comment\n',
    '<<: *other', 'wiki: null',
])
def test_ambiguous_manifest_requires_parser_never_guesses_local(tmp_path, monkeypatch, source):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    path.write_text(source)
    original = builtins.__import__
    def no_yaml(name, *args, **kwargs):
        if name == "yaml":
            raise ModuleNotFoundError("yaml unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", no_yaml)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


@pytest.mark.parametrize("source", ['', '# only a comment', 'wiki: null', 'wiki: []', 'wiki: {enabled: false}\nwiki: {enabled: false}'])
def test_invalid_manifest_or_wiki_mapping_is_unavailable(tmp_path, source):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    path.write_text(source)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"


def test_other_harness_settings_preserved_and_legacy_file_is_inert(tmp_path):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    manifest = {"type": "library", "qa": {"custom": True}, "otherPlugin": {"custom": [1, 2]}, "wiki": config()}
    path.write_text(json.dumps(manifest))
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "external"
    assert json.loads(path.read_text()) == manifest
    del manifest["wiki"]
    path.write_text(json.dumps(manifest))
    (tmp_path / "wiki-atlassian.yaml").write_text(json.dumps(config()))
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "local"


@pytest.mark.parametrize("component", ["doc", "harness", "manifest.yaml"])
def test_manifest_components_cannot_be_symlinks(tmp_path, component):
    project = write_config(tmp_path / "project")
    path = project / "doc"
    if component != "doc":
        path /= "harness"
    if component == "manifest.yaml":
        path /= "manifest.yaml"
    moved = tmp_path / "moved"
    path.rename(moved)
    create_symlink(path, moved, target_is_directory=component != "manifest.yaml")
    assert destination.select_destination(connection(), cwd=project)["mode"] == "unavailable"


def test_symlink_and_nonregular_git_markers_are_unavailable(tmp_path):
    project = write_config(tmp_path / "project")
    target = tmp_path / "gitdir"; target.mkdir()
    marker = project / ".git"; create_symlink(marker, target, target_is_directory=True)
    assert destination.select_destination(connection(), cwd=project)["mode"] == "unavailable"
    marker.unlink(); os.mkfifo(marker)
    assert destination.select_destination(connection(), cwd=project)["mode"] == "unavailable"



def test_unrelated_manifest_mapping_accepts_numeric_keys_without_mutation(tmp_path):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    source = "type: library\nstatus_codes: {200: ok, 404: missing}\nwiki: " + json.dumps(config()) + "\n"
    path.write_text(source)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "external"
    assert path.read_text() == source


@pytest.mark.parametrize("source", [
    "type: library\nstatus_codes: {200: ok, 200: duplicate}\nwiki: {enabled: false}\n",
    "type: library\nwiki: {enabled: false, 200: invalid}\n",
])
def test_nonstring_mapping_keys_still_require_unique_keys_and_strict_wiki_fields(tmp_path, source):
    path = manifest_path(tmp_path); path.parent.mkdir(parents=True)
    path.write_text(source)
    assert destination.select_destination(connection(), cwd=tmp_path)["mode"] == "unavailable"
