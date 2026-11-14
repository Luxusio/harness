"""Runtime-home selection reaches real CLI calls and isolated installed trees."""
from __future__ import annotations

import importlib.util
import base64
import json
import os
import sys
import tomllib
from pathlib import Path


try:
    import pytest
except ModuleNotFoundError:
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")

pytestmark = pytest.mark.usefixtures("selected_installer_python")


def _load():
    spec = importlib.util.spec_from_file_location("install_runtime_locations", Path(__file__).resolve().parents[1] / "install.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_custom_codex_home_reaches_config_cache_trust_and_daemon(tmp_path, monkeypatch, selected_installer_python):
    home = tmp_path / "ordinary-home"
    home.mkdir()
    runtime = tmp_path / "runtime home"
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CODEX_HOME", str(runtime))
    log = tmp_path / "calls.jsonl"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake = fake_bin / "codex"
    fake.write_text(
        f"#!{sys.executable}\n"
        "import os,sys,json\nfrom pathlib import Path\n"
        f"log=Path({str(log)!r})\n"
        "home=Path(os.environ['CODEX_HOME']);cfg=home/'config.toml'\n"
        "manifest=home/'harness/plugins/harness/.codex-plugin/plugin.json'\n"
        "version=json.loads(manifest.read_text())['version'] if manifest.exists() else None\n"
        "with log.open('a') as f:f.write(json.dumps({'argv':sys.argv[1:],'home':str(home),'config_exists':cfg.exists(),'version':version})+'\\n')\n"
        "if sys.argv[1:]==['--version']:print('codex 0.130.0')\n"
        "if sys.argv[1:3]==['plugin','list']:\n"
        " versions=[p.name for p in (home/'plugins/cache/harness/harness').iterdir() if p.is_dir()]\n"
        " selected='local' if 'local' in versions else max(versions)\n"
        " print(json.dumps({'installed':[dict(pluginId='harness@harness',version=selected,installed=True,enabled=True)]}))\n"
        "if sys.argv[1:]==['app-server','daemon','version']:sys.exit(1)\n"
        "if sys.argv[1:]==['features','enable','plugin_hooks'] and '[features]' not in cfg.read_text():\n"
        " with cfg.open('a') as f:f.write('\\n[features]\\nplugin_hooks = true\\n')\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    install = _load()
    # This fixture models the CLI protocol, not platform executable discovery.
    monkeypatch.setattr(install, "host_command", lambda *a, **k: [selected_installer_python, "-B", str(fake)])
    result = install.install_codex(dry_run=False, force=True, config_path=None)
    assert result.ok, result.summary + '\n' + '\n'.join(result.steps)
    assert result.restart_required
    config = tomllib.loads((runtime / "config.toml").read_text())
    assert config["features"]["plugin_hooks"] is True
    assert config["hooks"]["state"]
    mirror = runtime / "harness" / "plugins" / "harness"
    cache = install._codex_cached_plugin_root(runtime, mirror)
    assert cache.is_dir()
    hooks = json.loads((cache / "hooks.json").read_text())["hooks"]
    commands = [item["command"] for rows in hooks.values() for row in rows for item in row["hooks"]]
    if os.name == "nt":
        commands = [base64.b64decode(command.rsplit(" ", 1)[1]).decode("utf-16-le") for command in commands]
    assert all(str(cache) in command for command in commands)
    assert config["marketplaces"]["harness"]["source"] == str(runtime / "harness")
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(row["home"] == str(runtime) for row in records)
    assert any(row["argv"] == ["app-server", "daemon", "version"] for row in records)
    assert next(row for row in records if row["argv"] == ["features", "enable", "plugin_hooks"])["config_exists"]
    assert not any(row["argv"] == ["plugin", "add", "harness@harness"] for row in records)
    assert any(row["argv"] == ["plugin", "list", "--json", "--marketplace", "harness"] for row in records)
    assert not (home / ".codex").exists()
    # A forced reinstall still goes through cache installation. Simulate the
    # host refusing to rename a loaded cache, while allowing mirror publication.
    rename, replace = install.os.rename, install.os.replace
    def guarded(operation):
        def call(src, dst, *args, **kwargs):
            if Path(src) == cache or Path(dst) == cache:
                raise PermissionError('loaded cache directory is locked')
            return operation(src, dst, *args, **kwargs)
        return call
    monkeypatch.setattr(install.os, 'rename', guarded(rename))
    monkeypatch.setattr(install.os, 'replace', guarded(replace))
    forced = install.install_codex(dry_run=False, force=True, config_path=None)
    assert forced.ok, forced.summary
    skipped = install.install_codex(dry_run=False, force=False, config_path=None)
    assert skipped.ok and 'SYNCHRONIZED' in skipped.summary
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum(row["argv"] == ["plugin", "list", "--json", "--marketplace", "harness"] for row in records) == 3
    assert not any(row["argv"] == ["plugin", "add", "harness@harness"] for row in records)


def test_explicit_config_selects_runtime_home_and_nonstandard_config_requires_session(tmp_path, monkeypatch):
    install = _load()
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "other-runtime"))
    config = tmp_path / "selected" / "custom.toml"
    assert install._codex_home_for_config(config) == config.parent
    calls = []
    monkeypatch.setattr(install, "_run", lambda *args: calls.append(args))
    ok, message, restart = install._reload_codex_runtime(str(config))
    assert ok and not restart and "Custom Codex config artifact" in message
    assert "Merge it into" in message
    assert calls == []
