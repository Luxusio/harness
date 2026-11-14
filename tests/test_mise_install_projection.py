"""Every owned installed command pins the selected interpreter and identity."""
import importlib.util
import json
import os
from pathlib import Path
import shlex
import sys
import tomllib

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

ROOT = Path(__file__).resolve().parents[1]


def load(relative, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer(monkeypatch, tmp_path):
    monkeypatch.setenv('CODEX_HOME', str(tmp_path / 'codex'))
    module = load("install.py", "mise_projection_installer")
    monkeypatch.setattr(module, "select_python", lambda **kwargs: "/managed mise/python/3.12.15/bin/python")
    return module


def hook_commands(config):
    return [hook["command"] for groups in config["hooks"].values()
            for group in groups for hook in group["hooks"] if hook.get("type") == "command"]


def test_codex_commands_and_toml_escape_selected_python(installer, monkeypatch):
    selected = 'C:\\Users\\Name With Spaces 🐍\\mise\\installs\\python\\3.12.15\\python.exe'
    monkeypatch.setattr(installer, "select_python", lambda **kwargs: selected)
    root = r'C:\Users\Name With Spaces\harness'
    block = tomllib.loads(installer._mcp_block(root))["mcp_servers"]["harness"]
    assert block["command"] == selected
    if os.name == "nt":
        assert block["args"][:3] == ["-I", "-B", "-c"]
        assert str(Path(root) / "mcp/harness_server.py") in block["args"]
    else:
        assert block["args"] == ["-E", "-s", "-B", root + "/mcp/harness_server.py"]
    assert block["env"]["HARNESS_PLUGIN_ROOT"] == root
    mcp = installer._codex_mcp_config(Path("/plugin"))["mcpServers"]["harness"]
    assert mcp["command"] == selected
    assert mcp["args"][:3] == (["-I", "-B", "-c"] if os.name == "nt" else ["-E", "-s", "-B"])
    for command in hook_commands(installer._codex_hooks_config(Path("/plugin"))):
        assert shlex.split(command)[1:5] == [selected, "-E", "-s", "-B"]


def test_claude_projection_preserves_metadata_source_and_effective_identity(installer, tmp_path, monkeypatch):
    before = {name: (ROOT / "plugin" / name).read_bytes()
              for name in (".mcp.json", "hooks/hooks.json", ".claude-plugin/plugin.json")}
    first = installer.sync_claude_payload(tmp_path / "first")
    second = installer.sync_claude_payload(tmp_path / "second")
    mcp = json.loads((first / ".mcp.json").read_text())["mcpServers"]["harness"]
    assert mcp["command"] == installer._python_cmd()
    if os.name == "nt":
        assert mcp["args"][:3] == ["-I", "-B", "-c"]
        assert "${CLAUDE_PLUGIN_ROOT}/mcp/harness_server.py" in mcp["args"]
    else:
        assert mcp["args"] == ["-E", "-s", "-B", "${CLAUDE_PLUGIN_ROOT}/mcp/harness_server.py"]
    old_hooks = json.loads(before["hooks/hooks.json"])
    hooks = json.loads((first / "hooks/hooks.json").read_text())
    for name, groups in hooks["hooks"].items():
        for old_group, group in zip(old_hooks["hooks"][name], groups):
            assert {k: v for k, v in group.items() if k != "hooks"} == {k: v for k, v in old_group.items() if k != "hooks"}
            for old, hook in zip(old_group["hooks"], group["hooks"]):
                owned = {"command", "args", "onFailure"} if os.name == "nt" else {"command"}
                assert {k: v for k, v in hook.items() if k not in owned} == {k: v for k, v in old.items() if k not in owned}
                if os.name == "nt":
                    assert hook["command"] == installer._python_cmd()
                    assert hook["args"][:3] == ["-I", "-B", "-c"] and hook["onFailure"] == "continue"
                else:
                    assert hook["command"].endswith("|| true")
                    assert shlex.split(hook["command"])[1:5] == [installer._python_cmd(), "-E", "-s", "-B"]
                assert "mise_python.py" not in hook["command"]
    version = lambda root: json.loads((root / ".claude-plugin/plugin.json").read_text())["version"]
    assert version(first) == version(second)
    monkeypatch.setattr(installer, "select_python", lambda **k: "/another/mise/python")
    third = installer.sync_claude_payload(tmp_path / "third")
    assert version(third) != version(first)
    assert all((ROOT / "plugin" / name).read_bytes() == data for name, data in before.items())


def test_codex_cache_identity_changes_with_python_and_is_stable(installer, tmp_path, monkeypatch):
    first = installer.sync_codex_payload(tmp_path / "first")
    second = installer.sync_codex_payload(tmp_path / "second")
    assert installer._codex_plugin_version(first) == installer._codex_plugin_version(second)
    cache = installer.install_codex_plugin_cache(first, tmp_path / "codex")
    assert installer.install_codex_plugin_cache(first, tmp_path / "codex") == cache
    assert json.loads((cache / ".mcp.json").read_text())["mcpServers"]["harness"]["command"] == installer._python_cmd()
    monkeypatch.setattr(installer, "select_python", lambda **k: "/another/mise/python")
    third = installer.sync_codex_payload(tmp_path / "third")
    assert installer._codex_plugin_version(first) != installer._codex_plugin_version(third)


@pytest.mark.parametrize("operation", ["sync_claude", "sync_codex", "install_claude", "install_codex", "cache"])
def test_preflight_failure_precedes_any_installation_mutation(installer, monkeypatch, tmp_path, operation):
    def refuse(**kwargs):
        raise installer.MisePythonError("A valid installed mise base Python is required.") from None
    monkeypatch.setattr(installer, "select_python", refuse)
    monkeypatch.setattr(installer.shutil, "which", lambda *a, **k: "/fake/host")
    for name in ("_prune_bytecode_caches", "_normalize_payload_modes", "_copytree_clean"):
        monkeypatch.setattr(installer, name, lambda *a, **k: pytest.fail("mutation before preflight"))
    target = tmp_path / "absent"
    with pytest.raises(installer.MisePythonError):
        if operation == "sync_claude":
            installer.sync_claude_payload(target)
        elif operation == "sync_codex":
            installer.sync_codex_payload(target)
        elif operation == "cache":
            installer.install_codex_plugin_cache(ROOT / "plugin-codex", target)
        elif operation == "install_claude":
            installer.install_claude(dry_run=False, force=True)
        else:
            installer.install_codex(dry_run=False, force=True, config_path=str(target / "config.toml"))
    assert not target.exists()


@pytest.mark.parametrize("arguments,code", [([], 2), (["--codex-only", "--claude-only"], 2), (["--help"], 0), (["--unknown"], 2)])
def test_help_invalid_flags_and_no_host_need_no_mise(installer, monkeypatch, capsys, arguments, code):
    monkeypatch.setattr(installer, "select_python", lambda **k: pytest.fail("usage/no-host required mise"))
    monkeypatch.setattr(installer.shutil, "which", lambda *a, **k: None)
    monkeypatch.setattr(sys, "argv", ["install.py", *arguments])
    if arguments in (["--help"], ["--unknown"]):
        with pytest.raises(SystemExit) as captured:
            installer.main()
        assert captured.value.code == code
    else:
        assert installer.main() == code


def test_main_resolves_before_dispatching_any_runtime(installer, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["install.py"])
    monkeypatch.setattr(installer.shutil, "which", lambda *a, **k: "/fake/host")
    def refuse(**kwargs):
        raise installer.MisePythonError("A valid installed mise base Python is required.") from None
    monkeypatch.setattr(installer, "select_python", refuse)
    monkeypatch.setattr(installer, "install_codex", lambda **k: pytest.fail("worker before preflight"))
    monkeypatch.setattr(installer, "install_claude", lambda **k: pytest.fail("worker before preflight"))
    assert installer.main() == 1
    assert "mise base Python" in capsys.readouterr().err


def test_smoke_and_claude_cli_registration_use_selected_python(installer, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda argv, **k: calls.append(argv) or installer.subprocess.CompletedProcess(argv, 0, "done", ""))
    assert installer._smoke_installed_runtime(tmp_path)[0]
    flags = ["-I", "-B", "-c"] if os.name == "nt" else ["-E", "-s", "-B"]
    assert calls[0][:4] == [installer._python_cmd(), *flags]
    monkeypatch.setattr(installer.shutil, "which", lambda *a: "/fake/claude")
    monkeypatch.setattr(installer, "_run", lambda argv, dry: calls.append(argv) or (0, "claude 1.0", ""))
    monkeypatch.setattr(installer, "sync_claude_payload", lambda *a: tmp_path / "plugin")
    monkeypatch.setattr(installer, "_verify_claude_selected_cache", lambda *a: (True, "verified"))
    result = installer.install_claude(dry_run=False, force=True)
    assert result.ok
    command = next(argv for argv in calls if argv[:4] == ["claude", "mcp", "add", "harness"])
    index = command.index("--")
    assert command[index + 1:index + 5] == [installer._python_cmd(), *flags]


def test_wiki_yaml_selection_precedes_writes_and_changes_identity(monkeypatch, tmp_path):
    module = load("wiki-atlassian/install.py", "mise_wiki_installer")
    target = tmp_path / "absent"
    def refuse(**kwargs):
        assert kwargs == {"require_yaml": True}
        raise ValueError("Mise Python with PyYAML is required")
    monkeypatch.setattr(module, "select_python", refuse)
    with pytest.raises(ValueError, match="PyYAML"):
        module.prepare(target)
    assert not target.exists()
    monkeypatch.setattr(module, "select_python", lambda **k: "/managed/mise/python-one")
    first = module.prepare(target)
    assert module.prepare(target) == first
    transport = json.loads((first / "plugin/.mcp.json").read_text())["mcpServers"]["atlassian"]
    assert transport["command"] == "/managed/mise/python-one"
    if os.name == "nt":
        assert transport["args"][:3] == ["-I", "-B", "-c"]
        assert str(first / "plugin/scripts/launch.py") in transport["args"]
    else:
        assert transport["args"] == ["-I", "-B", str(first / "plugin/scripts/launch.py")]
    monkeypatch.setattr(module, "select_python", lambda **k: "/managed/mise/python-two")
    assert module.prepare(target) != first


def test_raw_manifests_share_guarded_bootstrap():
    for relative, server in (("plugin/.mcp.json", "harness"), ("wiki-atlassian/.mcp.json", "atlassian")):
        transport = json.loads((ROOT / relative).read_text())["mcpServers"][server]
        assert transport["command"] == "mise"
        assert transport["args"][:6] == ["exec", "python@3.12", "--", "python", "-I", "-B"]
        assert transport["args"][6].endswith("/scripts/mise_python.py" if server == "harness" else "/scripts/launch.py")
        if server == "atlassian":
            assert transport["args"][7:] == ["--mise"]
        assert transport["env"]["MISE_AUTO_INSTALL"] == "0"
