"""Runtime-home selection reaches real CLI calls and isolated installed trees."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tomllib
from pathlib import Path


def _load():
    spec = importlib.util.spec_from_file_location("install_runtime_locations", Path(__file__).resolve().parents[1] / "install.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_custom_codex_home_reaches_config_cache_trust_and_daemon(tmp_path, monkeypatch):
    home = tmp_path / "ordinary-home"
    home.mkdir()
    runtime = tmp_path / "runtime home"
    monkeypatch.setenv("HOME", str(home))
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
        "with log.open('a') as f:f.write(json.dumps({'argv':sys.argv[1:],'home':str(home),'config_exists':cfg.exists()})+'\\n')\n"
        "if sys.argv[1:]==['--version']:print('codex 0.130.0')\n"
        "if sys.argv[1:]==['app-server','daemon','version']:sys.exit(1)\n"
        "if sys.argv[1:]==['features','enable','plugin_hooks'] and '[features]' not in cfg.read_text():\n"
        " with cfg.open('a') as f:f.write('\\n[features]\\nplugin_hooks = true\\n')\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", str(fake_bin) + os.pathsep + os.environ["PATH"])
    install = _load()
    result = install.install_codex(dry_run=False, force=True, config_path=None)
    assert result.ok, result.summary + '\n' + '\n'.join(result.steps)
    assert result.restart_required
    config = tomllib.loads((runtime / "config.toml").read_text())
    assert config["features"]["plugin_hooks"] is True
    assert config["hooks"]["state"]
    mirror = runtime / "harness" / "plugins" / "harness"
    cache = install._codex_cached_plugin_root(runtime, mirror)
    assert cache.is_dir()
    assert str(cache) in (cache / "hooks.json").read_text()
    assert config["marketplaces"]["harness"]["source"] == str(runtime / "harness")
    records = [json.loads(line) for line in log.read_text().splitlines()]
    assert all(row["home"] == str(runtime) for row in records)
    assert any(row["argv"] == ["app-server", "daemon", "version"] for row in records)
    assert next(row for row in records if row["argv"] == ["features", "enable", "plugin_hooks"])["config_exists"]
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
