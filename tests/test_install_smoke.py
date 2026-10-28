"""Installed imports, actual WBS execution, source scope and cleanup."""

from __future__ import annotations

import atexit
import importlib.util
import marshal
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import conftest

REPO_ROOT = Path(conftest.REPO_ROOT)
SMOKE = REPO_ROOT / "plugin" / "scripts" / "install_smoke.py"
INSTALL_PY = REPO_ROOT / "install.py"


def _installed_tree(destination: Path) -> Path:
    """A tree shaped like `~/.claude/harness-dev/plugin`, built under tmp."""
    plugin_root = destination / "harness-dev" / "plugin"
    _load("install_smoke_fixture", INSTALL_PY)._copytree_clean(REPO_ROOT / "plugin", plugin_root)
    return plugin_root


_CLEAN_TREE: Path | None = None


def _clean_tree() -> Path:
    """One installed tree, built once, for the cases that must not alter it.

    A module-scoped `@pytest.fixture` would be the idiomatic form, but
    `tests/test_no_toplevel_third_party_imports.py` forbids importing pytest at
    module level in a test file, and a fixture decorator needs it there.
    """
    global _CLEAN_TREE
    if _CLEAN_TREE is None:
        holder = tempfile.mkdtemp(prefix="harness-clean-install-")
        atexit.register(shutil.rmtree, holder, True)
        _CLEAN_TREE = _installed_tree(Path(holder))
    return _CLEAN_TREE


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _plant_stale_pyc(source: Path) -> Path:
    """Write bytecode the loader accepts but that no longer matches `source`.

    The header carries the source's real mtime and size, so Python accepts the
    cache. Its body raises during import, proving the installed import probe
    catches executable bytecode corruption and pruning restores source loading.
    """
    text = source.read_text(encoding="utf-8") + "\nraise RuntimeError('stale cached module')\n"
    code = compile(text, str(source), "exec")
    info = source.stat()
    target = Path(importlib.util.cache_from_source(str(source)))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(
        importlib.util.MAGIC_NUMBER
        + struct.pack("<I", 0)
        + struct.pack("<I", int(info.st_mtime) & 0xFFFFFFFF)
        + struct.pack("<I", info.st_size & 0xFFFFFFFF)
        + marshal.dumps(code)
    )
    return target


def test_a_clean_installed_tree_passes_and_is_not_written_to():
    clean_tree = _clean_tree()
    # conftest exports PYTHONDONTWRITEBYTECODE suite-wide; the smoke must be
    # what keeps the tree clean, so it must not inherit that from the suite.
    env = {key: value for key, value in os.environ.items()
           if key != "PYTHONDONTWRITEBYTECODE"}
    result = subprocess.run(
        [sys.executable, str(SMOKE), "--plugin-root", str(clean_tree)],
        capture_output=True, text=True, timeout=300, env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "ok import:" in result.stdout
    assert "ok WBS: installed task/session ownership" in result.stdout

    # A probe that leaves bytecode behind can disable the runtime it just
    # certified: an entry written by another interpreter build fails the same
    # identity check a stale one does.
    assert not list(clean_tree.rglob("__pycache__")), "the smoke wrote into the tree"


def test_the_installer_runs_the_smoke_on_the_tree_it_installed():
    clean_tree = _clean_tree()
    install = _load("harness_install_for_smoke_test", INSTALL_PY)
    ok, steps = install._smoke_installed_runtime(clean_tree)
    assert ok, steps
    assert any("ok WBS" in step for step in steps), steps
    assert all(step.startswith("runtime smoke: ") for step in steps), steps


def test_a_stale_pyc_fails_the_install_and_the_prune_repairs_it(tmp_path):
    """An import failure fails delivery; pruning repairs stale bytecode."""
    install = _load("harness_install_for_smoke_test", INSTALL_PY)
    plugin_root = _installed_tree(tmp_path)
    pyc = _plant_stale_pyc(plugin_root / "scripts" / "prewrite_gate.py")
    assert pyc.is_file()

    ok, steps = install._smoke_installed_runtime(plugin_root)
    report = "\n".join(steps)
    assert not ok, report
    assert "FAIL import:" in report
    assert pyc.exists(), report

    steps = install._prune_bytecode_caches(plugin_root)
    # Pruning removes stale caches without touching their adjacent sources.
    assert all("could not remove" not in step for step in steps), steps

    ok, steps = install._smoke_installed_runtime(plugin_root)
    assert ok, steps


def test_each_installer_drives_the_smoke_on_the_tree_it_just_installed(tmp_path, monkeypatch):
    """The call site, not just the callable.

    A smoke test nobody calls is the failure this task exists to remove: the
    checks above would all stay green if the two `install.py` call sites were
    deleted. Both runtimes are covered because both write a tree that hooks
    later execute from.
    """
    install = _load("harness_install_for_smoke_test", INSTALL_PY)
    installed = tmp_path / "installed" / "plugin"
    installed.mkdir(parents=True)
    failure = (False, ["runtime smoke: FAIL WBS: task operation unavailable"])

    monkeypatch.setenv("HARNESS_DEST", str(tmp_path / "claude-dest"))
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", return_value=(0, "claude 2.1.0\n", "")),
        mock.patch.object(install, "sync_claude_payload", return_value=installed),
        mock.patch.object(
            install, "_smoke_installed_runtime", return_value=failure,
        ) as claude_smoke,
    ):
        result = install.install_claude(dry_run=False, force=True)
    claude_smoke.assert_called_once_with(installed)
    assert not result.ok
    assert "smoke test" in result.summary
    assert any("FAIL WBS" in step for step in result.steps), result.steps

    cached = tmp_path / "codex-cache" / "plugin"
    cached.mkdir(parents=True)
    # `install_codex` reads the synced payload's version before the cache step.
    (installed / ".codex-plugin").mkdir()
    (installed / ".codex-plugin" / "plugin.json").write_text(
        '{"name": "harness", "version": "9.9.9"}', encoding="utf-8",
    )
    with (
        mock.patch.object(install, "CODEX_INSTALL_ROOT", tmp_path / "codex-harness"),
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex 0.130.0\n", "")),
        mock.patch.object(install, "sync_codex_payload", return_value=installed),
        mock.patch.object(install, "install_codex_plugin_cache", return_value=cached),
        mock.patch.object(
            install, "_smoke_installed_runtime", return_value=failure,
        ) as codex_smoke,
    ):
        result = install.install_codex(
            dry_run=False, force=True, config_path=str(tmp_path / "config.toml"),
        )
    # Codex loads the cache entry, so that is the tree the smoke must drive.
    codex_smoke.assert_called_once_with(cached)
    assert not result.ok
    assert "smoke test" in result.summary


def test_the_synchronized_skip_path_still_drives_the_smoke(tmp_path, monkeypatch):
    """The default (conditional) run is the harness's own delivery path.

    `install_verified.py` calls `install.py` with no flags, which returns early on
    PAYLOAD_SYNCHRONIZED — the common case, since the payload is usually already
    current. With the probe only on the post-sync branch, the one check that
    inspects what actually runs almost never ran. Measured cost on the skip
    path: ~0.5s per runtime.
    """
    install = _load("harness_install_for_smoke_test", INSTALL_PY)
    failure = (False, ["runtime smoke: FAIL WBS: task operation unavailable"])

    claude_root = tmp_path / "claude-dest"
    (claude_root / "plugin").mkdir(parents=True)
    monkeypatch.setenv("HARNESS_DEST", str(claude_root))
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
        mock.patch.object(install, "_run", return_value=(0, "claude 2.1.0\n", "")),
        mock.patch.object(
            install, "_claude_payload_state",
            return_value=(install.PAYLOAD_SYNCHRONIZED, "identical"),
        ),
        mock.patch.object(install, "sync_claude_payload") as no_sync,
        mock.patch.object(
            install, "_smoke_installed_runtime", return_value=failure,
        ) as claude_smoke,
    ):
        result = install.install_claude(dry_run=False, force=False)
    no_sync.assert_not_called()
    claude_smoke.assert_called_once_with(claude_root / "plugin")
    assert not result.ok
    assert "smoke test" in result.summary

    # Codex loads the versioned cache entry, so that is the tree to probe.
    codex_install_root = tmp_path / "codex-harness"
    source = codex_install_root / "plugins" / "harness"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text(
        '{"name": "harness", "version": "9.9.9"}', encoding="utf-8",
    )
    cached = tmp_path / "plugins" / "cache" / "harness" / "harness" / "9.9.9"
    (cached / "scripts").mkdir(parents=True)
    with (
        mock.patch.object(install, "CODEX_INSTALL_ROOT", codex_install_root),
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex 0.130.0\n", "")),
        mock.patch.object(
            install, "_codex_payload_state",
            return_value=(install.PAYLOAD_SYNCHRONIZED, "identical"),
        ),
        mock.patch.object(install, "sync_codex_payload") as no_codex_sync,
        mock.patch.object(
            install, "_smoke_installed_runtime", return_value=failure,
        ) as codex_smoke,
    ):
        result = install.install_codex(
            dry_run=False, force=False,
            config_path=str(tmp_path / "config.toml"),
        )
    no_codex_sync.assert_not_called()
    codex_smoke.assert_called_once_with(cached)
    assert not result.ok
    assert "smoke test" in result.summary


def test_a_missing_codex_cache_entry_is_reported_not_assumed(tmp_path):
    """Nothing to probe is a stated step, never a silent pass."""
    install = _load("harness_install_for_smoke_test", INSTALL_PY)
    codex_install_root = tmp_path / "codex-harness"
    source = codex_install_root / "plugins" / "harness"
    (source / ".codex-plugin").mkdir(parents=True)
    (source / ".codex-plugin" / "plugin.json").write_text(
        '{"name": "harness", "version": "9.9.9"}', encoding="utf-8",
    )
    with (
        mock.patch.object(install, "CODEX_INSTALL_ROOT", codex_install_root),
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex 0.130.0\n", "")),
        mock.patch.object(
            install, "_codex_payload_state",
            return_value=(install.PAYLOAD_SYNCHRONIZED, "identical"),
        ),
    ):
        result = install.install_codex(
            dry_run=False, force=False,
            config_path=str(tmp_path / "config.toml"),
        )
    assert result.ok
    assert any("runtime smoke: skipped" in step for step in result.steps), result.steps


def test_the_smoke_covers_every_registered_hook_module():
    """Discovery, not a hand-written list: a new hook is covered on landing."""
    clean_tree = _clean_tree()
    install_smoke = _load("harness_install_smoke", SMOKE)
    modules = install_smoke._hook_modules(str(clean_tree))

    hooks_json = (clean_tree / "hooks" / "hooks.json").read_text(encoding="utf-8")
    registered = set(install_smoke.HOOK_SCRIPT_REFERENCE.findall(hooks_json))
    assert registered, "no hook scripts found in hooks.json"
    assert registered <= set(modules), sorted(registered - set(modules))
    # Include shared task control and the session-binding owner.
    assert {"_lib", "codex_hook_registration"} <= set(modules)


def test_an_absent_or_partial_tree_reports_a_failure_not_a_crash(tmp_path):
    install_smoke = _load("harness_install_smoke", SMOKE)
    ok, lines = install_smoke.smoke_installed_runtime(str(tmp_path / "nothing-here"))
    assert not ok
    assert "is not an installed plugin root" in lines[0]

    partial = tmp_path / "partial" / "plugin"
    (partial / "scripts").mkdir(parents=True)
    (partial / "scripts" / "hook_stop.py").write_text("x = 1\n", encoding="utf-8")
    ok, lines = install_smoke.smoke_installed_runtime(str(partial))
    assert not ok
    assert any("has no" in line and "harness_server.py" in line for line in lines), lines


def test_the_probe_environment_is_pinned_to_the_runtime_interpreter(monkeypatch):
    """The smoke inherits the same pinned interpreter as generated launchers."""
    # conftest exports PYTHONDONTWRITEBYTECODE suite-wide; without this the
    # assertion below would pass even if `_probe_env` stopped setting it.
    monkeypatch.delenv("PYTHONDONTWRITEBYTECODE", raising=False)
    install_smoke = _load("harness_install_smoke", SMOKE)
    env = install_smoke._probe_env("/nonexistent/plugin", "sid")
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "PYTEST_CURRENT_TEST" not in env
    assert install_smoke._runtime_python() == os.path.realpath(sys.executable)


def _codex_tree(destination):
    root = _installed_tree(destination)
    (root / ".codex-plugin").mkdir()
    (root / ".codex-plugin/plugin.json").write_text('{"name":"harness","version":"test"}')
    return root


def test_codex_smoke_drives_task_source_checks_without_payload_writes(tmp_path):
    import hashlib
    root = _codex_tree(tmp_path)
    smoke = _load("codex_native_smoke", SMOKE)
    def inventory():
        return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in root.rglob("*") if p.is_file()}
    before = inventory()
    ok, lines = smoke.smoke_installed_runtime(str(root), timeout=10)
    assert ok, lines
    assert any("ok WBS:" in line for line in lines)
    assert not any("ok WBS: bound subagent" in line for line in lines)
    assert inventory() == before
    assert not list(root.rglob("__pycache__"))


def test_smoke_rejects_broken_installed_source_assignment(tmp_path):
    smoke = _load("broken_source_smoke", SMOKE)
    root = _codex_tree(tmp_path)
    path = root / "scripts/wbs.py"
    text = path.read_text()
    old = "def require_source_assignment(repo, task_id, path=None):"
    assert old in text
    path.write_text(text.replace(old, old + "\n    return {}", 1))
    ok, lines = smoke.smoke_installed_runtime(str(root), timeout=20)
    assert not ok, lines
    assert any("FAIL WBS" in line for line in lines), lines


def test_codex_probe_timeout_cleans_worker_owned_scratch_too(tmp_path, monkeypatch):
    smoke = _load("codex_timeout_cleanup", SMOKE)
    root = _codex_tree(tmp_path / "installed")
    server = root / "mcp/harness_server.py"
    child_pid = tmp_path / "helper.pid"
    injected = ("from __future__ import annotations\nimport time, subprocess, sys\n"
                "_helper = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], "
                "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
                f"open({str(child_pid)!r}, 'w').write(str(_helper.pid))\n"
                "time.sleep(2)")
    server.write_text(server.read_text().replace("from __future__ import annotations", injected, 1))
    monkeypatch.setattr(smoke.tempfile, "tempdir", str(tmp_path))
    ok, lines = smoke._wbs_probe(str(root), timeout=0.5)
    assert not ok and any("did not complete" in line for line in lines)
    assert not list(tmp_path.glob("harness-wbs-*")), "killed worker leaked fixture directories"
    if child_pid.exists():
        pid = int(child_pid.read_text())
        stat = Path(f"/proc/{pid}/stat")
        assert not stat.exists() or stat.read_text().split()[2] == "Z", "worker helper survived timeout"
