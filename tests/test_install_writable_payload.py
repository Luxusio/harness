"""A world-writable payload must not survive an install, and must explain itself.

`shutil.copytree(..., copy2)` preserves source modes. On a checkout bind-mounted
from a Windows host every file reads `0o777`, so the installed runtime tree is
group/other-writable — and both canonical-import guards in `_lib` refuse exactly
that (`bind` on `before.st_mode & 0o022`, the receipt-adapter bind on the same
term inside `structural`). The install reports success, the MCP server cannot
open a task, and no hook records a receipt.

These tests pin both halves of the fix against a real payload copy:
the installer normalizes the modes, and when a tree is nonetheless writable the
smoke names that cause instead of blaming a stale `__pycache__`.

See `doc/harness/REQ__installed-tree-modes-are-installer-owned.md`.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SMOKE_SCRIPT = REPO_ROOT / "plugin" / "scripts" / "install_smoke.py"


def _load_install_module():
    name = "harness_install_for_writable_payload_test"
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `install.py` defines dataclasses, and
    # `dataclasses` resolves annotations through `sys.modules[cls.__module__]`.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _copy_payload(destination: Path) -> Path:
    """A real installed-shape plugin root, not a stub.

    The guards inspect the file they compile, so a fake tree would prove
    nothing about the condition under test.
    """
    plugin_root = destination / "plugin"
    shutil.copytree(
        REPO_ROOT / "plugin",
        plugin_root,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".omc"),
    )
    return plugin_root


def _modes(root: Path) -> list[int]:
    found = []
    for parent, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            path = Path(parent) / name
            info = os.lstat(path)
            if not stat.S_ISLNK(info.st_mode):
                found.append(stat.S_IMODE(info.st_mode))
    return found


def _run_smoke(plugin_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SMOKE_SCRIPT), "--plugin-root", str(plugin_root)],
        capture_output=True, text=True, timeout=300,
    )


def test_normalize_payload_modes_clears_group_and_other_write(tmp_path):
    install = _load_install_module()
    plugin_root = _copy_payload(tmp_path)
    for parent, dirnames, filenames in os.walk(plugin_root):
        for name in [*dirnames, *filenames]:
            os.chmod(Path(parent) / name, 0o777)
    os.chmod(plugin_root, 0o777)

    steps = install._normalize_payload_modes(plugin_root)

    assert any("cleared group/other-write bits" in step for step in steps), steps
    assert not any(mode & 0o022 for mode in _modes(plugin_root))
    assert not stat.S_IMODE(os.lstat(plugin_root).st_mode) & 0o022
    # Read and execute bits are untouched: the payload must stay runnable.
    assert all(mode & 0o400 for mode in _modes(plugin_root))


def test_normalize_payload_modes_is_a_noop_on_a_clean_tree(tmp_path):
    """AC2: it cannot turn a SYNCHRONIZED payload pair into a STALE one."""
    install = _load_install_module()
    plugin_root = _copy_payload(tmp_path)
    for parent, dirnames, filenames in os.walk(plugin_root):
        for name in [*dirnames, *filenames]:
            path = Path(parent) / name
            os.chmod(path, 0o755 if path.is_dir() else 0o644)
    before = _modes(plugin_root)

    steps = install._normalize_payload_modes(plugin_root)

    assert steps == []
    assert _modes(plugin_root) == before


def test_normalize_payload_modes_skips_symlinks(tmp_path):
    """chmod through a symlink would retarget a file outside the payload."""
    install = _load_install_module()
    root = tmp_path / "payload"
    root.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("keep me\n", encoding="utf-8")
    os.chmod(outside, 0o666)
    (root / "link").symlink_to(outside)

    install._normalize_payload_modes(root)

    assert stat.S_IMODE(os.lstat(outside).st_mode) == 0o666


def test_smoke_names_writable_modes_and_normalization_restores_the_tree(tmp_path):
    """AC3/AC4 end to end: the diagnosis is right, and the fix makes it pass."""
    install = _load_install_module()
    plugin_root = _copy_payload(tmp_path)

    healthy = _run_smoke(plugin_root)
    assert healthy.returncode == 0, healthy.stdout + healthy.stderr

    for parent, dirnames, filenames in os.walk(plugin_root):
        for name in [*dirnames, *filenames]:
            os.chmod(Path(parent) / name, 0o777)
    broken = _run_smoke(plugin_root)
    output = broken.stdout + broken.stderr
    assert broken.returncode != 0, output
    assert "group/other-writable" in output, output
    assert "chmod -R go-w" in output, output
    # The stale-cache claim is what sent the field report in circles; it must
    # not appear for a failure whose cause is demonstrably something else.
    assert "stale __pycache__ is the known cause" not in output, output

    install._normalize_payload_modes(plugin_root)
    repaired = _run_smoke(plugin_root)
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr


def test_claude_install_normalizes_modes_even_when_synchronized(tmp_path, monkeypatch):
    """AC1 at the call site, on the path the harness itself delivers through.

    Pinning `_normalize_payload_modes` alone would leave the load-bearing claim
    — that it runs on the `--if-stale` SYNCHRONIZED skip path too — asserted
    only by reading. Deleting the call from `install_claude` must fail a test,
    not just change a comment. Mirrors
    `tests/regression/task__unified_install/test_install_py.py::
    test_installer_clears_stale_bytecode_cache_even_when_synchronized`.
    """
    install = _load_install_module()
    install_root = tmp_path / "harness-dev"
    monkeypatch.setenv("HARNESS_DEST", str(install_root))

    plugin_root = install.sync_claude_payload(install_root)
    victim = plugin_root / "scripts" / "subagent_lifecycle.py"
    os.chmod(victim, 0o777)

    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", return_value=(0, "claude 2.1.0\n", "")),
    ):
        result = install.install_claude(dry_run=False, force=False, if_stale=True)

    assert result.ok, result.summary + "\n" + "\n".join(result.steps)
    assert any("cleared group/other-write bits" in step for step in result.steps)
    # Normalization runs before the staleness decision, so whichever branch the
    # comparison then takes, no world-writable module survives the run — and
    # the runtime smoke inside `install_claude` is what proves the resulting
    # tree can actually record a receipt.
    assert not stat.S_IMODE(os.lstat(victim).st_mode) & 0o022, (
        "the install left a world-writable module the import guards refuse"
    )
    assert not any(mode & 0o022 for mode in _modes(plugin_root))
    assert "runtime smoke: ok import" in " ".join(result.steps) or any(
        "ok import" in step for step in result.steps
    ), result.steps


def test_codex_install_normalizes_both_payload_roots_when_synchronized(tmp_path):
    """The Codex half: the mirror and the resolved plugin cache both count."""
    install = _load_install_module()
    codex_root = tmp_path / "codex-harness"
    config_path = tmp_path / "codex-home" / "config.toml"
    config_path.parent.mkdir(parents=True)
    codex_home = config_path.expanduser().resolve().parent

    mirror = codex_root / "plugins" / install.CODEX_PLUGIN_NAME / "scripts"
    cached = (
        codex_home / "plugins" / "cache" / install.CODEX_PLUGIN_MARKETPLACE
        / install.CODEX_PLUGIN_NAME / "0.1.0" / "scripts"
    )
    victims = []
    for scripts in (mirror, cached):
        scripts.mkdir(parents=True)
        module_file = scripts / "subagent_lifecycle.py"
        module_file.write_text("ok\n", encoding="utf-8")
        os.chmod(module_file, 0o777)
        victims.append(module_file)

    with (
        mock.patch.object(install, "CODEX_INSTALL_ROOT", codex_root),
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex 0.130.0\n", "")),
        mock.patch.object(
            install, "_codex_payload_state",
            return_value=(install.PAYLOAD_SYNCHRONIZED, ""),
        ),
        mock.patch.object(install, "sync_codex_payload") as sync,
    ):
        result = install.install_codex(
            dry_run=False, force=False, config_path=str(config_path), if_stale=True,
        )

    assert result.ok, result.summary + "\n" + "\n".join(result.steps)
    sync.assert_not_called()
    for module_file in victims:
        assert not stat.S_IMODE(os.lstat(module_file).st_mode) & 0o022, (
            f"the skip path left {module_file} world-writable"
        )


def test_smoke_keeps_the_stale_cache_wording_when_modes_are_clean(tmp_path):
    """The fallback stays put: this is the cause when nothing is writable."""
    sys.path.insert(0, str(SMOKE_SCRIPT.parent))
    try:
        import install_smoke
    finally:
        sys.path.pop(0)
    plugin_root = tmp_path / "plugin"
    (plugin_root / "scripts").mkdir(parents=True)
    module = plugin_root / "scripts" / "_lib.py"
    module.write_text("x = 1\n", encoding="utf-8")
    os.chmod(module, 0o644)

    assert install_smoke._world_writable_sources(str(plugin_root)) == []
    cause = install_smoke._refusal_cause(str(plugin_root))
    assert any("stale __pycache__" in line for line in cause), cause

    os.chmod(module, 0o666)
    cause = install_smoke._refusal_cause(str(plugin_root))
    assert any("group/other-writable" in line for line in cause), cause
    assert any(str(module) in line for line in cause), cause
