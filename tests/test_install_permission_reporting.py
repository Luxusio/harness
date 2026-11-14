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


pytestmark = pytest.mark.usefixtures("selected_installer_python")


def _assert_mode(path, mode):
    """Check meaningful native read-only state or the exact POSIX permission bits."""
    info = path.stat()
    if os.name == "nt":
        if path.is_file():
            assert bool(info.st_file_attributes & stat.FILE_ATTRIBUTE_READONLY) == (not bool(mode & stat.S_IWUSR))
    else:
        assert stat.S_IMODE(info.st_mode) == mode


def test_claude_read_only_sources_are_writable_only_in_private_projection(tmp_path, monkeypatch):
    install = _load_install_module()
    source = tmp_path / "source"
    contents = {
        ".claude-plugin/marketplace.json": "{}",
        "plugin/.claude-plugin/plugin.json": '{"version":"1.0.0"}',
        "plugin/tool": "#!/bin/sh\nexit 0\n",
        "plugin/.git/config": "excluded",
        "plugin/__pycache__/ignored.pyc": "excluded",
    }
    for name, content in contents.items():
        path = source / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o555 if name == "plugin/tool" else 0o444)
    directories = [source, *(p for p in source.rglob("*") if p.is_dir())]
    for path in directories:
        path.chmod(0o555)
    monkeypatch.setattr(install, "REPO_ROOT", source)
    monkeypatch.setattr(install, "PLUGIN_ROOT", source / "plugin")
    target = tmp_path / "private"
    try:
        install._build_claude_payload(target)
        assert json.loads((target / "plugin/.claude-plugin/plugin.json").read_text())["version"].startswith("1.0.0+h")
        _assert_mode(target / ".claude-plugin/marketplace.json", 0o644)
        _assert_mode(target / "plugin/tool", 0o755)
        for path in (p for p in target.rglob("*") if p.is_dir()):
            _assert_mode(path, 0o755)
        assert not (target / "plugin/.git").exists()
        assert not (target / "plugin/__pycache__").exists()
        for name, content in contents.items():
            path = source / name
            assert path.read_text() == content
            _assert_mode(path, 0o555 if name == "plugin/tool" else 0o444)
        for path in directories:
            _assert_mode(path, 0o555)
    finally:
        for path in directories:
            path.chmod(0o755)


def test_read_only_source_directory_projects_without_source_mode_change(tmp_path, monkeypatch):
    install = _load_install_module()
    source = tmp_path / "source"
    shutil.copytree(install.PLUGIN_CODEX_ROOT, source)
    canonical = tmp_path / "canonical-plugin"
    install._copytree_clean(install.PLUGIN_ROOT, canonical)
    shared = {}
    for name in ("repo-census.md", "project-interview.md", "bootstrap.md", "verify-report.md"):
        path = canonical / "skills/setup" / name
        shared[name] = path.read_bytes()
        path.chmod(0o444)
    source.chmod(0o555)
    monkeypatch.setattr(install, "PLUGIN_CODEX_ROOT", source)
    monkeypatch.setattr(install, "PLUGIN_ROOT", canonical)
    target = tmp_path / "projection"
    previous_umask = os.umask(0o002)
    try:
        install._build_codex_payload(target, tmp_path / "installed")
        assert (target / "scripts/_lib.py").is_file()
        assert (target / "hooks.json").is_file()
        _assert_mode(source, 0o555)
        _assert_mode(target, 0o755)
        for name, content in shared.items():
            original = canonical / "skills/setup" / name
            projected = target / "skills/setup" / name
            assert original.read_bytes() == projected.read_bytes() == content
            _assert_mode(original, 0o444)
            _assert_mode(projected, 0o644)
        state, _, reason = install._tree_inventory(target)
        assert state == install.PAYLOAD_SYNCHRONIZED, reason
        for name in ("hooks.json", ".mcp.json"):
            _assert_mode(target / name, 0o644)
        cache_source = tmp_path / "cache-source"
        install._copytree_clean(target, cache_source)
        for name in ("hooks.json", ".mcp.json"):
            (cache_source / name).unlink()
        cache = tmp_path / "cache"
        install._build_codex_cache_payload(cache_source, cache, tmp_path / "installed-cache")
        state, _, reason = install._tree_inventory(cache)
        assert state == install.PAYLOAD_SYNCHRONIZED, reason
        for name in ("hooks.json", ".mcp.json"):
            _assert_mode(cache / name, 0o644)
        marketplace = tmp_path / "marketplace"
        marketplace.mkdir(mode=0o700)
        install._write_codex_marketplace(marketplace, version=install._codex_plugin_version(target))
        state, _, reason = install._tree_inventory(marketplace)
        assert state == install.PAYLOAD_SYNCHRONIZED, reason
        _assert_mode(marketplace, 0o700)
        monkeypatch.setattr(install, "CODEX_INSTALL_ROOT", tmp_path / "absent-install")
        state, reason = install._codex_payload_state(tmp_path / "config/config.toml")
        assert state == install.PAYLOAD_STALE, reason
    finally:
        os.umask(previous_umask)
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
    if os.name != "nt":  # POSIX copier dereferences; Native custody refuses reparse sources.
        (source / "linked").symlink_to(outside)
    target = tmp_path / "cache"
    install._build_codex_cache_payload(source, target, tmp_path / "installed")
    assert json.loads((target / "hooks.json").read_text()) != {}
    _assert_mode(target / "tool", 0o755)
    _assert_mode(source / "hooks.json", 0o444)
    assert (source / "hooks.json").read_text() == "{}"
    _assert_mode(outside, 0o666)


@pytest.mark.skipif(os.name == "nt", reason="POSIX UID/xattr backend; actual Native owner/DACL refusal is covered in test_windows_install")
def test_private_projection_does_not_copy_virtual_filesystem_ownership_xattrs(tmp_path):
    install = _load_install_module()
    source = tmp_path / "source"
    (source / "mcp").mkdir(parents=True)
    (source / "mcp/server.py").write_bytes(b"# exact payload bytes\n")
    uid = (os.getuid() + 1).to_bytes(4, "little")
    paths = [source, source / "mcp", source / "mcp/server.py"]
    source.chmod(0o700)
    (source / "mcp").chmod(0o700)
    (source / "mcp/server.py").chmod(0o600)
    try:
        for path in paths:
            os.setxattr(path, "user.virtfs.uid", uid)
    except (AttributeError, OSError) as error:
        pytest.skip(f"fixture filesystem does not support user xattrs: {error}")
    # Reproduce the prior copier's real metadata propagation on this filesystem.
    prior = tmp_path / "prior-copytree"
    shutil.copytree(source, prior)
    for path in paths:
        assert os.getxattr(prior / path.relative_to(source), "user.virtfs.uid") == uid
    projected = tmp_path / "projected"
    install._copytree_clean(source, projected)
    for path in paths:
        copied = projected / path.relative_to(source)
        assert "user.virtfs.uid" not in os.listxattr(copied)
        assert copied.stat().st_uid == os.getuid()
        assert stat.S_IMODE(copied.stat().st_mode) == stat.S_IMODE(path.stat().st_mode)
        assert os.getxattr(path, "user.virtfs.uid") == uid
    assert (projected / "mcp/server.py").read_bytes() == (source / "mcp/server.py").read_bytes()


@pytest.mark.skipif(os.name == "nt", reason="POSIX UID/xattr backend; actual Native owner/DACL refusal is covered in test_windows_install")
def test_private_projection_foreign_owner_refusal_names_actual_and_installer_uid(tmp_path, monkeypatch):
    install = _load_install_module()
    source = tmp_path / "source"
    source.mkdir()
    (source / "payload").write_bytes(b"source unchanged")
    owner = os.getuid()
    chmod_calls = []
    monkeypatch.setattr(install.os, "getuid", lambda: owner + 1)
    monkeypatch.setattr(install.os, "chmod", lambda *args, **kwargs: chmod_calls.append(args))
    with pytest.raises(PermissionError) as failure:
        install._copytree_clean(source, tmp_path / "projection")
    assert f"uid {owner}, installer uid {owner + 1}" in str(failure.value)
    assert not chmod_calls
    assert (source / "payload").read_bytes() == b"source unchanged"


def test_post_activation_cleanup_failure_reports_active_and_retained_trees(tmp_path, monkeypatch):
    install = _load_install_module()
    target, staged = tmp_path / "payload", tmp_path / "staged"
    target.mkdir(); staged.mkdir()
    (target / "old").write_text("old")
    (staged / "new").write_text("new")
    backup = tmp_path / ".payload.previous"
    removed = []
    def denied(path):
        removed.append(Path(path))
        assert Path(path) == backup
        raise PermissionError(errno.EACCES, "backup cleanup denied", str(path))
    monkeypatch.setattr(install, "_remove_owned_tree", denied)
    with pytest.raises(RuntimeError, match="new payload active") as failure:
        install._activate_staged_tree(staged, target)
    assert removed == [backup]
    assert str(backup) in str(failure.value)
    assert (target / "new").read_text() == "new"
    assert (backup / "old").read_text() == "old"


@pytest.mark.parametrize("rollback_fails", [False, True])
def test_activation_failure_preserves_original_and_rollback_state(tmp_path, monkeypatch, rollback_fails):
    install = _load_install_module()
    target, staged = tmp_path / "payload", tmp_path / "staged"
    target.mkdir(); staged.mkdir()
    (target / "old").write_text("old")
    (staged / "new").write_text("new")
    if os.name == "nt":
        import wiki_platform
        real_replace = wiki_platform.metadata_rename_directory
    else:
        real_replace = install.os.replace
    calls = []
    def replace(src, dst):
        calls.append((src, dst))
        if len(calls) == 2:
            raise OSError(errno.EXDEV, "cross-device activation", str(staged))
        if len(calls) == 3 and rollback_fails:
            raise PermissionError(errno.EACCES, "rollback denied", str(target))
        return real_replace(src, dst)
    monkeypatch.setattr(wiki_platform if os.name == "nt" else install.os,
                        "metadata_rename_directory" if os.name == "nt" else "replace", replace)
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
    monkeypatch.setattr(install, "host_command", lambda *a, **k: ["fixture-codex"])
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
    monkeypatch.setattr(install, "host_command", lambda *a, **k: ["fixture-runtime"])
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
    monkeypatch.setattr(install, "host_command", lambda *a, **k: ["fixture-runtime"])
    monkeypatch.setattr(install, "install_codex", lambda **k: install.InstallResult("codex", True, "would install"))
    assert install.main() == 0
    assert capsys.readouterr().out == "Codex: DRY RUN\n"


def test_verbose_explicitly_shows_diagnostic_details(monkeypatch, capsys):
    install = _load_install_module()
    monkeypatch.setattr(sys, "argv", ["install.py", "--codex-only", "--verbose"])
    monkeypatch.setattr(install, "host_command", lambda *a, **k: ["fixture-runtime"])
    monkeypatch.setattr(install, "install_codex", lambda **k: install.InstallResult("codex", False, "denied", ["completed phase"], repair="fix exact path"))
    assert install.main() == 1
    out = capsys.readouterr().out
    assert "completed phase" in out and "fix exact path" in out
