"""Real Native installer journeys against disposable host configuration roots.

Collection on POSIX is useful; skipped cases are not Windows acceptance.
"""
from __future__ import annotations

import importlib.util
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import shutil
import sys
from types import SimpleNamespace

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires actual native Windows installation")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin/scripts"))
if os.name == "nt":
    import _wiki_windows as win
    import wiki_platform as platform
    from mise_python import select_python


def _extended(path):
    # Keep the complete fixture name, even with LongPathsEnabled=0. This is
    # only the standard-library copy's spelling of that same native path.
    value = str(Path(path).absolute())
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _read_json(path):
    return json.loads(platform.safe_metadata_snapshot(path, 16 * 1024 * 1024)[0])


def _tree(path):
    return platform.metadata_tree_inventory(path, max_entries=100_000, max_bytes=256 * 1024 * 1024)


def _preserved_core_cache(path, original):
    current = _tree(path)
    for name, row in original.items():
        assert current.get(name) == row, f"previous cache path changed or disappeared: {name}"
    added = set(current) - set(original)
    assert added <= {".orphaned_at"}, added
    if added:
        marker = platform.safe_metadata_snapshot(path / ".orphaned_at", 64)[0]
        assert current[".orphaned_at"]["kind"] == "file"
        assert len(marker) == 13 and marker.isdigit(), marker


def _run(argv, *, cwd, env, input_bytes=None, timeout=180):
    command = [platform.native_executable(argv[0], cwd=cwd, env=env), *argv[1:]]
    result = platform.run_bounded(command, cwd=cwd, env=env, timeout=timeout,
                                  limit=4 * 1024 * 1024, input_bytes=input_bytes, capture_stderr=True)
    assert result.returncode == 0, (
        command, result.returncode,
        result.stdout.decode("utf-8", errors="replace"), result.stderr.decode("utf-8", errors="replace"))
    return result.stdout


@pytest.fixture
def native_install(tmp_path, monkeypatch):
    private = win.ensure_private_directory(tmp_path / "install fixture 한글 'quoted' $dollar & semicolon;")
    source = win.ensure_private_directory(private / "source checkout")
    # Copy actual product sources, without operational WBS state, user config,
    # or interpreter caches. Source files stay byte-identical and independently
    # writable so a later payload update never changes the reviewed checkout.
    for name in ("plugin", "plugin-codex", ".claude-plugin", "wiki-atlassian"):
        shutil.copytree(_extended(ROOT / name), _extended(source / name),
                        copy_function=shutil.copyfile,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    shutil.copyfile(_extended(ROOT / "install.py"), _extended(source / "install.py"))
    consumer = win.ensure_private_directory(private / "unrelated consumer 中文")
    codex = win.ensure_private_directory(private / "codex configuration")
    claude = win.ensure_private_directory(private / "claude configuration")
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in {
               "PYTHONPATH", "PYTHONHOME", "CODEX_THREAD_ID", "CODEX_SESSION_ID",
               "HARNESS_SKIP_PREWRITE", "HARNESS_DISABLE_SCOPE_LOCK"}}
    # Preserve the actual OS profile and mise store. Only supported explicit
    # host/install destinations point at this private disposable fixture.
    env.update(CODEX_HOME=str(codex), CLAUDE_CONFIG_DIR=str(claude),
               HARNESS_DEST=str(claude / "harness-dev"), PYTHONDONTWRITEBYTECODE="1",
               MISE_AUTO_INSTALL="0")
    for key in ("CODEX_HOME", "CLAUDE_CONFIG_DIR", "HARNESS_DEST"):
        monkeypatch.setenv(key, env[key])
    selected = select_python(require_yaml=True)
    identity = json.loads(_run([selected, "-I", "-B", "-c",
        "import json,sys;print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix,base=sys.base_prefix)))"],
        cwd=consumer, env=env))
    assert Path(identity["executable"]).samefile(selected)
    assert identity["prefix"] == identity["base"] and ".venv" not in selected.lower()
    return SimpleNamespace(private=private, source=source, consumer=consumer, codex=codex,
                           claude=claude, env=env, python=selected)


def _core_install(fixture, runtime, *, force=False):
    argv = [fixture.python, "-E", "-s", "-B", str(fixture.source / "install.py")]
    if runtime:
        argv.append(f"--{runtime}-only")
    if runtime in (None, "codex"):
        argv.extend(["--config-path", str(fixture.codex / "config.toml")])
    if force:
        argv.append("--force")
    output = _run(argv, cwd=fixture.consumer, env=fixture.env)
    for name in ([runtime] if runtime else ["codex", "claude"]):
        assert (name.title() + ": SUCCESS").encode() in output, output
    return output


def _core_selection(fixture, runtime):
    if runtime == "codex":
        mirror = fixture.codex / "harness/plugins/harness"
        version = _read_json(mirror / ".codex-plugin/plugin.json")["version"]
        cache = fixture.codex / "plugins/cache/harness/harness" / version
    else:
        registry = _read_json(fixture.claude / "plugins/installed_plugins.json")
        rows = [row for row in registry["plugins"]["harness@harness"] if row["scope"] == "user"]
        assert len(rows) == 1, rows
        version, cache = rows[0]["version"], Path(rows[0]["installPath"])
        assert cache.is_relative_to(fixture.claude / "plugins/cache/harness/harness")
    return version, cache


def _registered_claude_transport(fixture):
    # Read the actual CLI-owned private registry, including its project scope;
    # the plugin's generated .mcp.json is a different transport surface.
    config = _read_json(fixture.claude / ".claude.json")
    candidates = [config.get("mcpServers", {}).get("harness")]
    candidates.extend(project.get("mcpServers", {}).get("harness")
                      for path, project in config.get("projects", {}).items()
                      if Path(path) == fixture.consumer)
    candidates = [record for record in candidates if isinstance(record, dict)]
    assert len(candidates) == 1, "actual CLI registration must identify one harness transport"
    transport = candidates[0]
    assert transport["args"][:3] == ["-I", "-B", "-c"]
    entry = Path(fixture.env["HARNESS_DEST"]) / "plugin/mcp/harness_server.py"
    assert len(str(entry).encode("utf-16-le")) // 2 > 260
    assert str(entry) in transport["args"]
    return transport


def _mcp_roundtrip(transport, fixture, *, harness, plugin_root=None):
    assert Path(transport["command"]).samefile(fixture.python)
    projected = json.dumps(transport, ensure_ascii=False)
    assert str(fixture.source) not in projected and ".venv" not in projected.lower()
    def expand(value):
        return value.replace("${CLAUDE_PLUGIN_ROOT}", str(plugin_root)) if plugin_root else value

    env = dict(fixture.env, **{key: expand(value) for key, value in transport.get("env", {}).items()})
    requests = [dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
        protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="native-install-fixture", version="1"))),
        dict(jsonrpc="2.0", id=2, method="tools/list", params={})]
    raw = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in requests).encode("utf-8")
    output = _run([transport["command"], *map(expand, transport["args"])],
                  cwd=fixture.consumer, env=env, input_bytes=raw, timeout=30)
    replies = [json.loads(line) for line in output.splitlines() if line.startswith(b"{")]
    initialized = next(row for row in replies if row.get("id") == 1)
    listed = next(row for row in replies if row.get("id") == 2)
    assert "result" in initialized and "error" not in initialized, initialized
    assert "result" in listed and "error" not in listed, listed
    if harness:
        assert [tool["name"] for tool in listed["result"]["tools"]] == ["wbs"]
    else:
        assert listed["result"]["tools"] == [], listed


def _codex_hook(hook, fixture, payload):
    # This is Codex's Windows command selection and actual native shell
    # boundary. Execute generated bytes without translating POSIX syntax.
    command = hook.get("commandWindows", hook["command"])
    assert 0 < hook["timeout"] <= 10
    return _run([fixture.env["COMSPEC"], "/d", "/s", "/c", command],
                cwd=fixture.consumer, env=fixture.env,
                input_bytes=payload, timeout=hook["timeout"] + 5)


def _exercise_installed_codex_hooks(cache, fixture):
    config = _read_json(cache / "hooks.json")["hooks"]
    installer = _load(fixture.source / "install.py", "native_installed_hook_identity_fixture")
    for groups in config.values():
        for group in groups:
            for hook in group["hooks"]:
                assert 0 < hook["timeout"] <= 10
    manifest = fixture.consumer / "doc/harness/manifest.yaml"
    win.ensure_private_directory(manifest.parent)
    platform.atomic_write(manifest, b"version: 5\ntype: library\nstrict_compliance_requires_delegation: true\n")
    protected = fixture.consumer / "doc/harness/wbs/WBS__native/PLAN.md"
    payload = dict(cwd=str(fixture.consumer), session_id="native-install-fixture",
        tool_name="Write", tool_input=dict(file_path=str(protected), content="한글 소식 😀\n"))
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    prewrite = next(hook for group in config["PreToolUse"] for hook in group["hooks"]
                    if "hook_pre_tool_use.py" in hook["command"])
    group = next(group for group in config["PreToolUse"] if prewrite in group["hooks"])
    trusted = installer._codex_hook_trust_hash("PreToolUse", group, prewrite)
    assert installer._codex_hook_trust_hash("PreToolUse", group,
        dict(prewrite, command="unused POSIX command")) == trusted
    assert installer._codex_hook_trust_hash("PreToolUse", group,
        dict(prewrite, commandWindows=prewrite["commandWindows"] + " changed")) != trusted
    output = _codex_hook(prewrite, fixture, raw)
    decision = json.loads(output)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", decision
    assert "PLAN.md" in decision["permissionDecisionReason"], decision
    assert not protected.exists()
    # The exact generated launcher must retain fail-safety when its real
    # entrypoint rejects malformed input, with no alternate interpreter.
    _codex_hook(prewrite, fixture, b"{invalid-json")


def _exercise_installed_claude_hooks(cache, fixture):
    config = _read_json(cache / "hooks/hooks.json")["hooks"]
    for groups in config.values():
        for group in groups:
            for hook in group["hooks"]:
                assert Path(hook["command"]).samefile(fixture.python)
                assert hook["args"][:3] == ["-I", "-B", "-c"]
                assert hook["onFailure"] == "continue" and 0 < hook["timeout"] <= 10
    manifest = fixture.consumer / "doc/harness/manifest.yaml"
    win.ensure_private_directory(manifest.parent)
    platform.atomic_write(manifest, b"version: 5\ntype: library\nstrict_compliance_requires_delegation: true\n")
    protected = fixture.consumer / "doc/harness/wbs/WBS__native/PLAN.md"
    raw = json.dumps(dict(cwd=str(fixture.consumer), session_id="native-install-fixture", tool_name="Write",
        tool_input=dict(file_path=str(protected), content="한글 소식 😀\n")), ensure_ascii=False).encode("utf-8")
    hook = config["PreToolUse"][0]["hooks"][0]
    argv = [hook["command"], *(arg.replace("${CLAUDE_PLUGIN_ROOT}", str(cache)) for arg in hook["args"])]
    output = _run(argv, cwd=fixture.consumer, env=fixture.env, input_bytes=raw, timeout=hook["timeout"] + 5)
    decision = json.loads(output)["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny", decision
    assert "PLAN.md" in decision["permissionDecisionReason"] and not protected.exists()


def _selected_claude_startup(fixture, marker):
    cli = _load(fixture.source / "plugin/scripts/native_cli.py", "native_selected_startup_cli")
    env = {key: value for key, value in fixture.env.items()
           if not key.upper().endswith("_PROXY")
           and not any(term in key.upper() for term in
                       ("API_KEY", "AUTH_TOKEN", "ACCESS_TOKEN", "AWS_", "AZURE_", "GOOGLE_", "GCLOUD_"))
           and key.upper() not in {"CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                                   "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_GIT_BASH_PATH"}}
    home = win.ensure_private_directory(fixture.private / "local-only startup profile")
    for name in ("APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
        env[name] = str(win.ensure_private_directory(home / name))
    env.update(HOME=str(home), USERPROFILE=str(home), ANTHROPIC_BASE_URL="http://127.0.0.1:9",
               NO_PROXY="127.0.0.1,localhost,::1", no_proxy="127.0.0.1,localhost,::1",
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1", DISABLE_TELEMETRY="1",
               DISABLE_ERROR_REPORTING="1", DISABLE_AUTOUPDATER="1")
    settings = fixture.private / "local-only startup settings.json"
    platform.atomic_write(settings, json.dumps(dict(env=dict(
        ANTHROPIC_BASE_URL="http://127.0.0.1:9", CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1"))).encode())
    debug = fixture.private / "selected startup debug.log"
    # Read the legitimately installed private user settings/registry. No
    # --plugin-dir, prompt, provider call, forged trust or enabledPlugins file.
    command = cli.host_command("claude", cwd=fixture.consumer, env=env)
    _run([*command, "--init-only", "--settings", str(settings), "--setting-sources", "user",
          "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}', "--debug-file", str(debug)],
         cwd=fixture.consumer, env=env, timeout=30)
    detail = platform.safe_metadata_snapshot(debug, 4 * 1024 * 1024)[0] if debug.exists() else b"no debug log"
    assert marker.exists(), detail.decode("utf-8", errors="replace")[-4000:]
    assert marker.read_bytes() == b"native selected SessionStart\n"


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_native_core_clean_default_force_and_update_preserve_prior_selected_cache(native_install, runtime):
    fixture = native_install
    if runtime == "claude":
        units = len(str(fixture.claude).encode("utf-16-le")) // 2
        parent = win.ensure_private_directory(fixture.claude / ("deep registered mirror-" + "界" * max(80, 270 - units)))
        fixture.env["HARNESS_DEST"] = str(parent / "harness-dev")
    _core_install(fixture, runtime)
    first_version, first_cache = _core_selection(fixture, runtime)
    first = _tree(first_cache)
    assert first and not any("__pycache__" in Path(name).parts for name in first)
    transport = _read_json(first_cache / ".mcp.json")["mcpServers"]["harness"]
    _mcp_roundtrip(transport, fixture, harness=True, plugin_root=first_cache)
    if runtime == "claude":
        _mcp_roundtrip(_registered_claude_transport(fixture), fixture, harness=True)
    if runtime == "codex":
        _exercise_installed_codex_hooks(first_cache, fixture)
    else:
        _exercise_installed_claude_hooks(first_cache, fixture)
    for force in (False, True):
        _core_install(fixture, None if runtime == "codex" and not force else runtime, force=force)
        version, cache = _core_selection(fixture, runtime)
        assert version == first_version and cache == first_cache
        _preserved_core_cache(first_cache, first)
    changed = fixture.source / "plugin/scripts/project_format_check.py"
    with open(_extended(changed), "ab") as stream:
        stream.write(b"\n# Disposable installer update fixture.\n")
    marker = fixture.private / "selected SessionStart marker.txt"
    if runtime == "claude":
        script = fixture.source / "plugin/scripts/hook_session_start.py"
        with open(_extended(script), "r", encoding="utf-8") as stream:
            text = stream.read()
        anchor = "def main() -> int:\n"
        assert text.count(anchor) == 1
        addition = f"    with open({str(marker)!r}, 'ab') as fixture_marker:\n        fixture_marker.write(b'native selected SessionStart\\n')\n"
        with open(_extended(script), "w", encoding="utf-8", newline="") as stream:
            stream.write(text.replace(anchor, anchor + addition, 1))
    _core_install(fixture, runtime)
    updated_version, updated_cache = _core_selection(fixture, runtime)
    assert updated_version != first_version and updated_cache != first_cache
    _preserved_core_cache(first_cache, first)
    updated = _tree(updated_cache)
    _mcp_roundtrip(_read_json(updated_cache / ".mcp.json")["mcpServers"]["harness"],
                   fixture, harness=True, plugin_root=updated_cache)
    if runtime == "claude":
        _mcp_roundtrip(_registered_claude_transport(fixture), fixture, harness=True)
        _selected_claude_startup(fixture, marker)
    _preserved_core_cache(updated_cache, updated)
    _core_install(fixture, runtime)
    assert _core_selection(fixture, runtime) == (updated_version, updated_cache)
    _preserved_core_cache(first_cache, first)
    _preserved_core_cache(updated_cache, updated)


def test_native_wiki_prepare_update_and_rerun_preserve_immutable_launcher_and_selected_python(native_install):
    fixture = native_install
    installer = _load(fixture.source / "wiki-atlassian/install.py", "native_wiki_installer_fixture")
    data = fixture.private / "wiki data"
    first = installer.prepare(data)
    before = _tree(first)
    transport = _read_json(first / "plugin/.mcp.json")["mcpServers"]["atlassian"]
    assert transport["args"][:3] == ["-I", "-B", "-c"]
    assert str(first / "plugin/scripts/launch.py") in transport["args"]
    _mcp_roundtrip(transport, fixture, harness=False)
    assert installer.prepare(data) == first
    assert _tree(first) == before
    with open(_extended(fixture.source / "wiki-atlassian/README.md"), "ab") as stream:
        stream.write("\nDisposable native update 소식.\n".encode("utf-8"))
    updated = installer.prepare(data)
    assert updated != first
    assert _tree(first) == before
    _mcp_roundtrip(_read_json(updated / "plugin/.mcp.json")["mcpServers"]["atlassian"], fixture, harness=False)
    assert installer.prepare(data) == updated
    assert _tree(first) == before


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_native_wiki_public_install_update_and_rerun_preserve_immutable_launcher(native_install, runtime):
    import mise_python

    fixture = native_install
    # Path.home supplies the supported wiki data destination. Keep discovery
    # on the genuine, independently validated installed mise store while all
    # test data and host registries remain in this disposable profile.
    actual_data = mise_python._context()[2][0]
    home = win.ensure_private_directory(fixture.private / "wiki data profile")
    env = dict(fixture.env, HOME=str(home), USERPROFILE=str(home), MISE_DATA_DIR=str(actual_data))
    marketplace = home / ".local/share/wiki-atlassian/marketplace"

    def install():
        output = _run([fixture.python, "-E", "-s", "-B", str(fixture.source / "wiki-atlassian/install.py"),
                       "--runtime", runtime], cwd=fixture.consumer, env=env)
        assert (runtime.title() + ": SUCCESS").encode() in output, output
        selection = _read_json(marketplace / ".claude-plugin/marketplace.json")["plugins"][0]["source"]
        payload = (marketplace / selection).parent
        if runtime == "codex":
            cache = fixture.codex / "plugins/cache/knowledge-documents/wiki-atlassian" / payload.name
        else:
            registry = _read_json(fixture.claude / "plugins/installed_plugins.json")
            rows = [row for row in registry["plugins"]["wiki-atlassian@knowledge-documents"] if row["scope"] == "user"]
            assert len(rows) == 1
            cache = Path(rows[0]["installPath"])
            assert cache.is_relative_to(fixture.claude / "plugins/cache/knowledge-documents/wiki-atlassian")
        return payload, cache

    first, first_cache = install()
    original, original_cache = _tree(first), _tree(first_cache)
    assert install() == (first, first_cache)
    assert _tree(first) == original and _tree(first_cache) == original_cache
    with open(_extended(fixture.source / "wiki-atlassian/README.md"), "ab") as stream:
        stream.write("\nDisposable native installed update 소식.\n".encode("utf-8"))
    updated, updated_cache = install()
    assert updated != first and updated_cache != first_cache
    assert _tree(first) == original
    _mcp_roundtrip(_read_json(first / "plugin/.mcp.json")["mcpServers"]["atlassian"], fixture, harness=False)
    _mcp_roundtrip(_read_json(updated_cache / ".mcp.json")["mcpServers"]["atlassian"], fixture, harness=False)
    assert install() == (updated, updated_cache)
    assert _tree(first) == original


@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_native_host_discovery_uses_real_executable_and_preserves_literal_arguments(native_install, monkeypatch, runtime):
    fixture = native_install
    cli = _load(fixture.source / "plugin/scripts/native_cli.py", "native_host_cli_fixture")
    shadow = fixture.consumer / (runtime + ".exe")
    shutil.copyfile(sys.executable, shadow)
    monkeypatch.chdir(fixture.consumer)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    command = cli.host_command(runtime, cwd=fixture.consumer, env=fixture.env)
    assert command and Path(command[0]).suffix.lower() in {".exe", ".com"}
    assert not Path(command[0]).samefile(shadow)
    assert not any(Path(argument).suffix.lower() in {".cmd", ".bat"} for argument in command)
    version = _run([*command, "--version"], cwd=fixture.consumer, env=fixture.env, timeout=30)
    assert runtime.encode() in version.lower(), version
    marker = fixture.consumer / "shell interpolation must not run.txt"
    literal = f'& echo accidental-shell > "{marker}" & rem'
    result = platform.execute_bounded([*command, "--definitely-invalid-native-argv-fixture", literal],
        cwd=fixture.consumer, env=fixture.env, timeout=30, limit=64 * 1024)
    assert result["returncode"] not in (None, 0) and not result["error"], result
    assert not marker.exists(), "a host CLI argument was interpreted by a shell"


def test_native_real_profile_guard_survives_home_environment_spoofing(native_install, monkeypatch):
    fixture = native_install
    installer = _load(fixture.source / "install.py", "native_profile_guard_fixture")
    token = win.HANDLE()
    win._check(win._OpenProcessToken(win._GetCurrentProcess(), 8, ctypes.byref(token)))
    try:
        profile = ctypes.WinDLL("userenv", use_last_error=True).GetUserProfileDirectoryW
        profile.argtypes = [win.HANDLE, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        profile.restype = wintypes.BOOL
        size = wintypes.DWORD()
        profile(token, None, ctypes.byref(size))
        assert size.value > 0
        buffer = ctypes.create_unicode_buffer(size.value)
        win._check(profile(token, buffer, ctypes.byref(size)))
        actual_home = Path(buffer.value)
    finally:
        win._close(token)
    fake_home = win.ensure_private_directory(fixture.private / "environment-only profile")
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setenv("USERPROFILE", str(fake_home))
    assert installer._real_user_home() == actual_home
    # These calls inspect refusal only; no operation targets real user data.
    for target in (actual_home / ".codex", actual_home / ".claude/plugins/cache"):
        with pytest.raises(RuntimeError, match="real install tree"):
            installer._reject_real_install_root_under_test(target)
    installer._reject_real_install_root_under_test(fixture.private / "allowed explicit install")


def test_native_generated_codex_launcher_fails_safe_when_entrypoint_is_unavailable(native_install):
    fixture = native_install
    installer = _load(fixture.source / "install.py", "native_hook_failure_fixture")
    absent = fixture.private / "missing plugin 한글 'quoted' & no fallback"
    hooks = installer._codex_hooks_config(absent)["hooks"]
    for event in ("SessionStart", "PreToolUse"):
        hook = hooks[event][0]["hooks"][0]
        _codex_hook(hook, fixture, json.dumps(dict(cwd=str(fixture.consumer))).encode("utf-8"))
    assert not absent.exists(), "failed launcher created an alternate runtime"


def test_native_deep_owned_entry_retains_canonical_public_wbs_writer(native_install):
    fixture = native_install
    cli = _load(fixture.source / "plugin/scripts/native_cli.py", "native_deep_owned_entry_fixture")
    units = lambda path: len(str(path).encode("utf-16-le")) // 2
    padding = max(80, 270 - units(fixture.private))
    deep = fixture.private / ("deep owned source-" + "界" * padding) / "plugin"
    shutil.copytree(_extended(fixture.source / "plugin"), _extended(deep), copy_function=shutil.copyfile)
    entry = deep / "mcp/harness_server.py"
    assert units(entry) > 260
    _run(["git", "init", "-q", str(fixture.consumer)], cwd=fixture.private, env=fixture.env, timeout=10)
    manifest = fixture.consumer / "doc/harness/manifest.yaml"
    win.ensure_private_directory(manifest.parent)
    platform.atomic_write(manifest, b"version: 5\ntype: library\nstrict_compliance_requires_delegation: true\n")
    platform.atomic_write(fixture.consumer / "source.py", b"VALUE = 1\n")
    plan = ("# Genuine deep Native writer\n```wbs\n" + json.dumps([
        dict(id="A", title="Canonical deep source executes its check", weight=1,
             check=[fixture.python, "-I", "-B", "-c", "print('native deep checked')"])
    ]) + "\n```\n```wbs-inputs\n" + json.dumps(dict(source=["source.py"])) + "\n```\n")
    requests = [dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
        protocolVersion="2025-06-18", capabilities={}, clientInfo=dict(name="native-deep-fixture", version="1")))]
    for identifier, action, extra in ((2, "open", {}), (3, "plan", dict(plan=plan)),
                                      (4, "check", dict(node_id="A")), (5, "verify", {})):
        requests.append(dict(jsonrpc="2.0", id=identifier, method="tools/call", params=dict(name="wbs",
            arguments=dict(action=action, wbs_id="native-deep", **extra))))
    env = dict(fixture.env, HARNESS_RUNTIME="claude", HARNESS_SESSION_ID="native-deep-owned-fixture", CLAUDE_SESSION_ID="")
    argv = cli.owned_python_argv(fixture.python, entry, import_roots=[deep / "scripts"])
    assert argv[:4] == [fixture.python, "-I", "-B", "-c"]
    output = _run(argv, cwd=fixture.consumer, env=env, timeout=40,
                  input_bytes="".join(json.dumps(row) + "\n" for row in requests).encode("utf-8"))
    replies = {row["id"]: row for row in map(json.loads, output.splitlines()) if "id" in row}
    assert "result" in replies[1] and "error" not in replies[1], replies
    for identifier in (2, 3, 4, 5):
        assert "result" in replies[identifier] and "error" not in replies[identifier], replies[identifier]
        assert not replies[identifier]["result"].get("isError"), replies[identifier]
    opened = replies[2]["result"]["structuredContent"]
    checked = replies[4]["result"]["structuredContent"]
    assert checked["passed"] and "native deep checked" in checked["output"], checked
    assert replies[5]["result"]["structuredContent"]["runtime_verdict"] == "PASS", replies[5]
    control = _read_json(fixture.consumer / "doc/harness/wbs/WBS__native-deep/WBS.control.json")
    assert control["run_id"] == opened["run_id"] and control["closed"] is False
    jobs = [dict(job_id="deep-prepare", outcome_id="A", kind="producer",
                 argv=[fixture.python, "-I", "-B", "-c",
                       "from pathlib import Path;Path('prepared.txt').write_bytes(Path('source.py').read_bytes());print('produced')"],
                 inputs=dict(source=["source.py"], artifacts=[]), outputs=["prepared.txt"]),
            dict(job_id="deep-read", outcome_id="A", kind="consumer",
                 argv=[fixture.python, "-I", "-B", "-c",
                       "from pathlib import Path;assert Path('prepared.txt').read_bytes()==Path('source.py').read_bytes();print('consumed')"],
                 inputs=dict(source=["source.py"], artifacts=["prepared.txt"]), outputs=[])]
    jobs_path = fixture.consumer / "deep jobs.json"
    results_dir = win.ensure_private_directory(fixture.consumer / "verification metadata")
    results_path = results_dir / "deep results.json"
    platform.atomic_write(jobs_path, json.dumps(jobs).encode("utf-8"))
    generation = "deep-source-native-1"
    runner_argv = cli.owned_python_argv(fixture.python, deep / "scripts/verify_runner.py", [
        "--jobs", str(jobs_path), "--results", str(results_path), "--root", str(fixture.consumer),
        "--wbs-id", "WBS__native-deep", "--run-id", opened["run_id"], "--generation", generation,
        "--max-workers", "2", "--timeout", "15"])
    import mise_python
    actual_mise_data = mise_python._context()[2][0]
    job_home = win.ensure_private_directory(fixture.private / "verification job state home")
    # Only disposable verification leases/results use this supported home
    # context. Core installation and the OS token profile remain unchanged.
    job_env = dict(env, HOME=str(job_home), USERPROFILE=str(job_home), MISE_DATA_DIR=str(actual_mise_data))
    returned = json.loads(_run(runner_argv, cwd=fixture.consumer, env=job_env, timeout=60))
    persisted = _read_json(results_path)
    for result in (returned, persisted):
        assert result["schema"] == "harness.verification-execution" and result["version"] == 2, result
        assert result["wbs_id"] == "WBS__native-deep" and result["run_id"] == opened["run_id"]
        assert result["execution_generation"] == generation
        assert result["status"] == "PASS" and result["returncode"] == 0, result
        assert [record["job_id"] for record in result["commands"]] == ["deep-prepare", "deep-read"]
        for record in result["commands"]:
            assert record["state"] == "completed" and record["status"] == "PASS" and record["returncode"] == 0
            assert record["reused"] is False and record["input_fingerprint"] and record["context_fingerprint"]
        assert [row[0] for row in result["commands"][0]["artifact_manifest"]] == ["prepared.txt"]
        assert "consumed" in result["commands"][1]["output"]
    assert (fixture.consumer / "prepared.txt").read_bytes() == (fixture.consumer / "source.py").read_bytes()
    assert not any("__pycache__" in Path(name).parts for name in _tree(deep))
