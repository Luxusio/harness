"""Effective permissions, surviving payloads and concise installer results."""
import errno
import json
import os
from pathlib import Path
import shutil
import stat
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

from test_install_writable_source_comparison import _load_install_module


def test_read_only_source_directory_projects_without_source_mode_change(tmp_path, monkeypatch):
    install = _load_install_module()
    source = tmp_path / "source"
    shutil.copytree(install.PLUGIN_CODEX_ROOT, source)
    source.chmod(0o555)
    monkeypatch.setattr(install, "PLUGIN_CODEX_ROOT", source)
    target = tmp_path / "projection"
    try:
        install._build_codex_payload(target, tmp_path / "installed")
        assert (target / "scripts/_lib.py").is_file()
        assert (target / "hooks.json").is_file()
        assert stat.S_IMODE(source.stat().st_mode) == 0o555
        assert stat.S_IMODE(target.stat().st_mode) == 0o755
    finally:
        source.chmod(0o755)


def test_read_only_generated_configs_are_writable_only_in_private_copy(tmp_path):
    install = _load_install_module()
    source = tmp_path / "source"
    source.mkdir()
    for name in ("hooks.json", ".mcp.json"):
        (source / name).write_text("{}")
        (source / name).chmod(0o444)
    (source / "tool").write_text("#!/bin/sh\nexit 0\n")
    (source / "tool").chmod(0o555)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    outside.chmod(0o666)
    (source / "linked").symlink_to(outside)
    target = tmp_path / "cache"
    install._build_codex_cache_payload(source, target, tmp_path / "installed")
    assert json.loads((target / "hooks.json").read_text()) != {}
    assert stat.S_IMODE((target / "tool").stat().st_mode) == 0o755
    assert stat.S_IMODE((source / "hooks.json").stat().st_mode) == 0o444
    assert (source / "hooks.json").read_text() == "{}"
    assert stat.S_IMODE(outside.stat().st_mode) == 0o666


def test_post_activation_cleanup_failure_reports_active_and_retained_trees(tmp_path):
    if os.geteuid() == 0:
        pytest.skip("requires effective filesystem permission enforcement")
    install = _load_install_module()
    target, staged = tmp_path / "payload", tmp_path / "staged"
    target.mkdir(); staged.mkdir()
    (target / "old").write_text("old")
    (staged / "new").write_text("new")
    target.chmod(0o555)
    backup = tmp_path / ".payload.previous"
    try:
        with pytest.raises(RuntimeError, match="new payload active") as failure:
            install._activate_staged_tree(staged, target)
        assert str(backup) in str(failure.value)
        assert (target / "new").read_text() == "new"
        assert (backup / "old").read_text() == "old"
    finally:
        if backup.exists():
            backup.chmod(0o755)


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_activation_failure_preserves_original_and_rollback_state(tmp_path, monkeypatch, rollback_fails):
    install = _load_install_module()
    target, staged = tmp_path / "payload", tmp_path / "staged"
    target.mkdir(); staged.mkdir()
    (target / "old").write_text("old")
    (staged / "new").write_text("new")
    real_replace = install.os.replace
    calls = []
    def replace(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError(errno.EXDEV, "cross-device activation", str(staged))
        if len(calls) == 3 and rollback_fails:
            raise PermissionError(errno.EACCES, "rollback denied", str(target))
        return real_replace(src, dst)
    monkeypatch.setattr(install.os, "replace", replace)
    with pytest.raises((OSError, RuntimeError)) as failure:
        install._activate_staged_tree(staged, target)
    if rollback_fails:
        assert "cross-device activation" in str(failure.value)
        assert "rollback denied" in str(failure.value)
        assert str(tmp_path / ".payload.previous") in str(failure.value)
        assert (tmp_path / ".payload.previous/old").is_file()
        assert not target.exists()
    else:
        assert (target / "old").read_text() == "old"
    assert (staged / "new").read_text() == "new"


@pytest.mark.parametrize("stage", ["payload sync", "cache install", "config merge"])
def test_permission_failure_names_stage_path_and_preserves_progress(tmp_path, monkeypatch, capsys, stage):
    install = _load_install_module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--codex-only", "--force", "--verbose", "--config-path", str(tmp_path / "config.toml")])
    monkeypatch.setattr(install, "CODEX_INSTALL_ROOT", tmp_path / "harness")
    monkeypatch.setattr(install.shutil, "which", lambda _: "/bin/codex")
    monkeypatch.setattr(install, "_run", lambda *a, **k: (0, "codex 1.0.0", ""))
    monkeypatch.setattr(install, "sync_codex_payload", lambda: install.PLUGIN_CODEX_ROOT)
    monkeypatch.setattr(install, "install_codex_plugin_cache", lambda *a: tmp_path / "cache")
    monkeypatch.setattr(install, "_smoke_installed_runtime", lambda *a: (True, ["runtime smoke: ok"]))
    path = tmp_path / "permission-denied"
    def denied(*a, **k):
        raise PermissionError(errno.EACCES, "Permission denied", str(path))
    monkeypatch.setattr(install, {"payload sync": "sync_codex_payload", "cache install": "install_codex_plugin_cache", "config merge": "emit_and_install_codex_config"}[stage], denied)
    assert install.main() == 1
    out = capsys.readouterr().out
    assert "Codex: FAILED" in out
    assert "Codex " + stage in out and str(path) in out
    assert "codex 1.0.0 >= pin" in out  # Progress survives an unexpected exception.


@pytest.mark.parametrize("failed", [False, True])
def test_compact_results_have_only_runtime_lines_and_failure_error(monkeypatch, capsys, failed):
    install = _load_install_module()
    monkeypatch.setattr(sys, "argv", ["install.py"])
    monkeypatch.setattr(install.shutil, "which", lambda _: "/bin/runtime")
    monkeypatch.setattr(install, "install_claude", lambda **k: install.InstallResult("claude", True, "done", ["private step"]))
    monkeypatch.setattr(install, "install_codex", lambda **k: install.InstallResult("codex", not failed, "cache install: denied /test/path" if failed else "synchronized", ["private step"]))
    assert install.main() == int(failed)
    expected = "Claude: SUCCESS\nCodex: " + ("FAILED\n  Error: cache install: denied /test/path\n" if failed else "SUCCESS\n")
    captured = capsys.readouterr()
    assert captured.out == expected
    assert not captured.err


def test_preview_is_not_reported_as_applied_success(monkeypatch, capsys):
    install = _load_install_module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--codex-only", "--dry-run"])
    monkeypatch.setattr(install.shutil, "which", lambda _: "/bin/runtime")
    monkeypatch.setattr(install, "install_codex", lambda **k: install.InstallResult("codex", True, "would install"))
    assert install.main() == 0
    assert capsys.readouterr().out == "Codex: DRY RUN\n"


def test_verbose_explicitly_shows_diagnostic_details(monkeypatch, capsys):
    install = _load_install_module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--codex-only", "--verbose"])
    monkeypatch.setattr(install.shutil, "which", lambda _: "/bin/runtime")
    monkeypatch.setattr(install, "install_codex", lambda **k: install.InstallResult("codex", False, "denied", ["completed phase"], repair="fix exact path"))
    assert install.main() == 1
    out = capsys.readouterr().out
    assert "completed phase" in out and "fix exact path" in out
