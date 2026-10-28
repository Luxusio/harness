"""install.py refreshes the Claude plugin cache, not only the harness-dev mirror.

Claude Code runs harness hooks from its plugin cache
(`<config>/plugins/cache/harness/harness/<version>`). `claude plugin update`
only refreshes that cache when the version string changes, so a same-version
install used to sync `~/.claude/harness-dev` while the runtime kept executing
the old copy. On 2026-09-25 a receipt fix reached the hooks only after a manual
`claude plugin uninstall` + `install`. The mirror's manifest version now carries
a payload hash, and every Claude install path ends with `claude plugin update`.
"""
from __future__ import annotations

import importlib.util
import json
import re
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
UPDATE = ["claude", "plugin", "update", "harness@harness"]
MARKETPLACE_UPDATE = ["claude", "plugin", "marketplace", "update", "harness"]
REFRESHED = '✔ Plugin "harness" updated from 2.3.0 to 2.3.0+h0badc0de for scope user. Restart to apply changes.'
CURRENT = "✔ harness is already at the latest version (2.3.0+h0badc0de)."


def _load_install_module():
    spec = importlib.util.spec_from_file_location("harness_install_cache", REPO_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _fake_run(install_root: Path, *, update=(0, REFRESHED, ""), marketplace_update=(0, "", ""),
              calls: list | None = None, listed: bool = True):
    def run(cmd, dry):
        if calls is not None:
            calls.append(list(cmd))
        if cmd == UPDATE:
            if not isinstance(update, BaseException) and update[0] == 0:
                config = Path(os.environ["CLAUDE_CONFIG_DIR"])
                version = _manifest_version(install_root / "plugin")
                cache = config / "plugins" / "cache" / "harness" / "harness" / version
                if not cache.exists():
                    shutil.copytree(install_root / "plugin", cache)
                registry = config / "plugins" / "installed_plugins.json"
                registry.write_text(json.dumps({"version": 2, "plugins": {"harness@harness": [
                    {"scope": "user", "installPath": str(cache), "version": version}
                ]}}))
            if isinstance(update, BaseException):
                raise update
            return update
        if cmd == MARKETPLACE_UPDATE:
            return marketplace_update
        if cmd[:3] == ["claude", "plugin", "marketplace"] and cmd[3:4] == ["list"]:
            if not listed:
                return 0, "No marketplaces configured\n", ""
            return 0, f"harness Source: Directory ({install_root})\n", ""
        return 0, "claude 2.1.0\n", ""
    return run


def _manifest_version(plugin_root: Path) -> str:
    return json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())["version"]


def test_mirror_version_is_a_deterministic_payload_hash(tmp_path):
    install = _load_install_module()
    source_manifest = REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json"
    source_before = source_manifest.read_bytes()
    first = install.sync_claude_payload(tmp_path / "a" / "harness-dev")
    second = install.sync_claude_payload(tmp_path / "b" / "harness-dev")
    base = json.loads(source_before)["version"]
    assert re.fullmatch(re.escape(base) + r"\+h[0-9a-f]{8}", _manifest_version(first))
    assert _manifest_version(first) == _manifest_version(second)
    assert source_manifest.read_bytes() == source_before, "the source manifest must stay unstamped"

    # Any payload change is a new version, and re-stamping keeps one suffix.
    (second / "scripts" / "_lib.py").write_text("# changed\n", encoding="utf-8")
    restamped = install._stamp_claude_plugin_version(second)
    assert restamped != _manifest_version(first)
    assert restamped.count("+") == 1


def test_synchronized_default_run_refreshes_a_stale_plugin_cache(tmp_path, monkeypatch):
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    install.sync_claude_payload(install_root)
    calls: list = []
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", side_effect=_fake_run(install_root, calls=calls)),
    ):
        result = install.install_claude(dry_run=False, force=False)
    assert result.ok, result.summary + "\n" + "\n".join(result.steps)
    assert "payload comparison: SYNCHRONIZED" in result.steps
    assert calls.index(MARKETPLACE_UPDATE) < calls.index(UPDATE)
    assert any(step.startswith("claude plugin cache refreshed") for step in result.steps)
    assert "plugin cache refreshed" in result.summary
    assert result.restart_required
    # main() classifies "install skipped" as SKIPPED; a refresh changed things.
    assert "install skipped" not in result.summary


def test_synchronized_default_run_with_a_current_cache_stays_skipped(tmp_path, monkeypatch):
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    install.sync_claude_payload(install_root)
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", side_effect=_fake_run(install_root, update=(0, CURRENT, ""))),
    ):
        result = install.install_claude(dry_run=False, force=False)
    assert result.ok, result.summary
    assert "claude plugin cache current" in result.steps
    assert "install skipped" in result.summary
    assert not result.restart_required


def test_synchronized_mirror_without_a_marketplace_falls_back_to_a_full_install(
    tmp_path, monkeypatch,
):
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    install.sync_claude_payload(install_root)
    calls: list = []
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", side_effect=_fake_run(
            install_root, marketplace_update=(1, "", "marketplace not found"), calls=calls,
            listed=False,
        )),
    ):
        result = install.install_claude(dry_run=False, force=False)
    assert result.ok, result.summary + "\n" + "\n".join(result.steps)
    assert any(
        step.startswith("payload comparison: STALE (mirror synchronized but claude plugin marketplace update failed")
        for step in result.steps
    ), result.steps
    assert any("synced plugin payload" in step for step in result.steps)
    assert ["claude", "plugin", "marketplace", "add", str(install_root)] in calls
    assert calls.index(UPDATE) > calls.index(["claude", "plugin", "install", "harness@harness"])


def test_registered_install_ends_with_a_plugin_update(tmp_path, monkeypatch):
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    calls: list = []
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", side_effect=_fake_run(install_root, calls=calls)),
    ):
        result = install.install_claude(dry_run=False, force=False)
    assert result.ok, result.summary + "\n" + "\n".join(result.steps)
    assert calls.index(MARKETPLACE_UPDATE) < calls.index(UPDATE)
    mcp_add = next(i for i, cmd in enumerate(calls) if cmd[:3] == ["claude", "mcp", "add"])
    assert calls.index(UPDATE) < mcp_add


def test_plugin_update_failure_fails_the_install(tmp_path, monkeypatch):
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    for failure, expected in (
        ((1, "", "network down"), "claude plugin update harness@harness failed: network down"),
        (subprocess.TimeoutExpired(UPDATE, 120), "claude plugin update harness@harness timed out"),
    ):
        with (
            mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
            mock.patch.object(install, "_run", side_effect=_fake_run(install_root, update=failure)),
        ):
            result = install.install_claude(dry_run=False, force=False)
        assert not result.ok
        assert result.summary == expected


def test_dry_run_lists_the_plugin_update_without_running_it(tmp_path, monkeypatch):
    install = _load_install_module()
    monkeypatch.setenv("HARNESS_DEST", str(tmp_path / "harness-dev"))
    calls: list = []

    def run(cmd, dry):
        calls.append(list(cmd))
        assert dry
        return 0, "", ""

    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", side_effect=run),
    ):
        result = install.install_claude(dry_run=True, force=False)
    assert result.ok
    assert "would run: claude plugin update harness@harness" in result.steps
    assert UPDATE not in calls


def test_healthy_mirror_does_not_certify_corrupt_selected_cache(tmp_path, monkeypatch):
    install = _load_install_module()
    root = tmp_path / "mirror"
    monkeypatch.setenv("HARNESS_DEST", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    install.sync_claude_payload(root)
    run = _fake_run(root, update=(0, CURRENT, ""))
    run(UPDATE, False)
    version = _manifest_version(root / "plugin")
    cache = tmp_path / "claude-config" / "plugins" / "cache" / "harness" / "harness" / version
    (cache / "scripts" / "_lib.py").write_text("raise RuntimeError('corrupt cache')\n")
    with mock.patch.object(install.shutil, "which", return_value="/bin/claude"), mock.patch.object(install, "_run", side_effect=run):
        result = install.install_claude(dry_run=False, force=False)
    assert not result.ok
    assert "selected cache" in result.summary and str(cache) in result.summary
    assert install._claude_payload_state()[0] == install.PAYLOAD_SYNCHRONIZED


def test_selected_cache_rejects_missing_stale_ambiguous_and_unsafe_entries(tmp_path, monkeypatch):
    install = _load_install_module()
    root = tmp_path / "mirror"
    mirror = install.sync_claude_payload(root)
    config = tmp_path / "claude-config"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(config))
    ok, reason = install._verify_claude_selected_cache(mirror, [])
    assert not ok and "installed_plugins.json" in reason
    monkeypatch.setenv("HARNESS_DEST", str(root))
    _fake_run(root)(UPDATE, False)
    registry = config / "plugins" / "installed_plugins.json"
    original = json.loads(registry.read_text())
    for change, expected in (("version", "selected version"), ("path", "selected cache path"), ("duplicate", "exactly one"), ("mode", "unsafe regular file")):
        data = json.loads(json.dumps(original))
        entry = data["plugins"]["harness@harness"][0]
        if change == "version":
            entry["version"] = "old"
        elif change == "path":
            entry["installPath"] = str(mirror)
        elif change == "duplicate":
            data["plugins"]["harness@harness"].append(dict(entry))
        else:
            (Path(entry["installPath"]) / "scripts" / "_lib.py").chmod(0o666)
        registry.write_text(json.dumps(data))
        ok, reason = install._verify_claude_selected_cache(mirror, [])
        assert not ok and expected in reason, reason


def test_forced_current_cache_needs_no_restart_but_first_install_does(tmp_path, monkeypatch):
    install = _load_install_module()
    root = tmp_path / "mirror"
    monkeypatch.setenv("HARNESS_DEST", str(root))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    run = _fake_run(root, update=(0, CURRENT, ""))
    with mock.patch.object(install.shutil, "which", return_value="/bin/claude"), mock.patch.object(install, "_run", side_effect=run):
        first = install.install_claude(dry_run=False, force=True)
        current = install.install_claude(dry_run=False, force=True)
    assert first.ok and first.restart_required, first.summary
    assert current.ok and not current.restart_required, current.summary


def test_unavailable_cli_cannot_claim_cache_verified(tmp_path, monkeypatch):
    install = _load_install_module()
    monkeypatch.setenv("HARNESS_DEST", str(tmp_path / "mirror"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude-config"))
    with mock.patch.object(install.shutil, "which", return_value=None), mock.patch.object(install, "_verify_claude_selected_cache") as probe:
        result = install.install_claude(dry_run=False, force=False)
    assert not result.ok and result.summary == "claude CLI not found in PATH"
    probe.assert_not_called()
