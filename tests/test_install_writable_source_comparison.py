"""Comparison must survive a world-writable checkout, and name what it refuses.

`TASK__install-strips-host-write-bits` stopped the installer from *producing* a
guard-rejected tree. Two states it left open, both found after that task closed:

1. `--if-stale` still died on a world-writable *source*, because
   `_compare_payload_trees` builds the expected side with
   `copytree(..., copy2)` into a temp dir and then refuses its own scratch copy
   for carrying the source's `0o777`. That takes out `install_verified.py`, the
   harness's own delivery path, while `--force` keeps working.
2. A world-writable installer-created *ancestor* of a payload root made
   `--if-stale` fail permanently, with a message naming the payload root rather
   than the ancestor — and the `--force` the tool prints does not clear
   ancestors, so the next run failed identically.

See `doc/harness/REQ__installed-tree-modes-are-installer-owned.md`.
"""
from __future__ import annotations

import importlib.util
import os
import shutil
import stat
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_install_module():
    name = "harness_install_for_writable_source_test"
    spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _tree(root: Path, body: str = "payload\n") -> Path:
    (root / "scripts").mkdir(parents=True)
    (root / "scripts" / "tool.py").write_text(body, encoding="utf-8")
    (root / "mcp").mkdir()
    (root / "mcp" / "server.py").write_text("ok\n", encoding="utf-8")
    return root


def _chmod_tree(root: Path, mode: int) -> None:
    for parent, dirnames, filenames in os.walk(root):
        for name in [*dirnames, *filenames]:
            os.chmod(Path(parent) / name, mode)
    os.chmod(root, mode)


def test_a_world_writable_expected_tree_still_reaches_a_verdict(tmp_path):
    """AC1: the installer must not refuse the scratch copy it just made."""
    install = _load_install_module()
    expected = _tree(tmp_path / "expected")
    actual = _tree(tmp_path / "actual")
    _chmod_tree(expected, 0o777)

    state, reason = install._compare_payload_trees(expected, actual)

    # The verdict is a real one either way; what must never happen again is the
    # installer refusing its own scratch copy before comparing anything.
    assert state in {install.PAYLOAD_SYNCHRONIZED, install.PAYLOAD_STALE}, (state, reason)
    assert "expected payload unavailable" not in reason
    # Normalization touched the installer's own copy only.
    assert not any(
        stat.S_IMODE(os.lstat(Path(p) / n).st_mode) & 0o022
        for p, dirs, files in os.walk(expected)
        for n in [*dirs, *files]
    )


def test_the_real_flow_converges_on_synchronized_from_a_777_source(tmp_path):
    """AC1, modelling what actually happens rather than a mixed-mode pair.

    Both sides come from the same checkout, so both normalize the same way:
    `0o777 & ~0o022` is `0o755` on the expected side and on the installed side
    that `_normalize_payload_modes` already cleaned. A pair built from a 0o644
    source lands on 0o644 on both sides. Mode still participates in the
    verdict — a checkout whose modes change between installs reports STALE and
    gets re-synced, which is correct — but it can no longer refuse outright.
    """
    install = _load_install_module()
    expected = _tree(tmp_path / "expected")
    actual = _tree(tmp_path / "actual")
    _chmod_tree(expected, 0o777)
    _chmod_tree(actual, 0o777)
    install._normalize_payload_modes(actual)  # what an install leaves behind

    assert install._compare_payload_trees(expected, actual) == (
        install.PAYLOAD_SYNCHRONIZED, "",
    )


def test_normalizing_the_expected_tree_cannot_mask_a_real_difference(tmp_path):
    """AC2: content still decides the verdict, modes never do."""
    install = _load_install_module()
    expected = _tree(tmp_path / "expected")
    actual = _tree(tmp_path / "actual", body="different\n")
    _chmod_tree(expected, 0o777)

    state, reason = install._compare_payload_trees(expected, actual)

    assert state == install.PAYLOAD_STALE, (state, reason)
    assert "differs from canonical projection" in reason


def test_no_writable_bit_from_the_source_reaches_an_inventory(tmp_path):
    """AC2: `0o022` never enters a recorded mode, digest, or verdict.

    Stated exactly: the residual mode does participate (`0o755` is not
    `0o644`), which is why a checkout whose modes change reports STALE. What
    cannot happen is a writable bit being recorded as the canonical projection
    of an installed tree.
    """
    install = _load_install_module()
    expected = _tree(tmp_path / "expected")
    actual = _tree(tmp_path / "actual")
    _chmod_tree(expected, 0o777)
    _chmod_tree(actual, 0o777)
    install._normalize_payload_modes(actual)

    install._compare_payload_trees(expected, actual)
    state, inventory, reason = install._tree_inventory(expected)

    assert state == install.PAYLOAD_SYNCHRONIZED, (state, reason)
    assert inventory, "an empty inventory would pass this vacuously"
    recorded_modes = {mode for kind, mode, _digest in inventory.values() if kind == "file"}
    assert recorded_modes and not any(mode & 0o022 for mode in recorded_modes)


def test_a_rejected_path_component_is_named_with_its_mode(tmp_path):
    """AC4: name the component that was refused, not the path asked for."""
    install = _load_install_module()
    ancestor = tmp_path / "owned"
    payload = _tree(ancestor / "plugins" / "harness")
    os.chmod(ancestor, 0o777)

    state, _inventory, reason = install._tree_inventory(payload)

    assert state == install.PAYLOAD_ERROR, (state, reason)
    assert str(ancestor) in reason, reason
    assert "0777" in reason, reason
    # The payload root is still reported, as context rather than as the cause.
    assert str(payload) in reason, reason


def test_a_rejected_component_is_named_when_the_target_does_not_exist_yet(tmp_path):
    """AC4's other rejection site: the absent-target ancestor walk.

    This is the branch a *first* install hits — payload root not created yet,
    writable ancestor above it — so it is exactly the F2 scenario on a fresh
    machine. It is also a hand-written copy of the message in
    `_open_inventory_root`, in a different control path (return-tuple, not
    raise), so nothing but a test stops the two from drifting apart.
    """
    install = _load_install_module()
    ancestor = tmp_path / "owned"
    ancestor.mkdir()
    os.chmod(ancestor, 0o777)
    missing = ancestor / "plugins" / "harness"
    assert not missing.exists()

    state, _inventory, reason = install._tree_inventory(missing)

    assert state == install.PAYLOAD_ERROR, (state, reason)
    assert str(ancestor) in reason, reason
    assert "0777" in reason, reason
    assert str(missing) in reason, reason


def test_install_clears_a_writable_installer_created_ancestor(tmp_path, monkeypatch):
    """AC3: `--if-stale` must not stay stuck on a state `--force` cannot clear.

    Drives the real Codex entry point so the fix is pinned at the call site.
    """
    install = _load_install_module()
    codex_root = tmp_path / "codex-harness"
    config_path = tmp_path / "codex-home" / "config.toml"
    config_path.parent.mkdir(parents=True)
    codex_home = config_path.expanduser().resolve().parent

    mirror = codex_root / "plugins" / install.CODEX_PLUGIN_NAME
    cached = (
        codex_home / "plugins" / "cache" / install.CODEX_PLUGIN_MARKETPLACE
        / install.CODEX_PLUGIN_NAME / "0.1.0"
    )
    for root in (mirror, cached):
        _tree(root)
    # The ancestors, not the payload: exactly the state that used to stick.
    writable = [
        codex_root,
        codex_root / "plugins",
        codex_home / "plugins" / "cache" / install.CODEX_PLUGIN_MARKETPLACE,
    ]
    for path in writable:
        os.chmod(path, 0o777)

    from unittest import mock

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
    for path in writable:
        assert not stat.S_IMODE(os.lstat(path).st_mode) & 0o022, (
            f"{path} stayed writable, so --if-stale stays stuck"
        )
    # And the tree is now inspectable, which is the point of clearing them.
    state, _inv, reason = install._tree_inventory(mirror)
    assert state == install.PAYLOAD_SYNCHRONIZED, (state, reason)


def test_the_installer_does_not_chmod_what_it_does_not_exclusively_own(
    tmp_path, monkeypatch,
):
    """Non-goal pinned: `cache/` and `plugins/` under the Codex home are shared.

    The criterion is exclusive ownership, not authorship. `install_codex_plugin_cache`
    mkdirs the cache entry's parents, so under a permissive umask this installer
    may well have created `plugins/` and `cache/` — and they still hold every
    other Codex plugin's tree, so widening the walk to reach them would chmod
    those. The walk stops at the marketplace directory; a writable `cache/`
    gets a named diagnosis from `_tree_inventory`, never a mutation.
    """
    install = _load_install_module()
    codex_root = tmp_path / "codex-harness"
    config_path = tmp_path / "codex-home" / "config.toml"
    config_path.parent.mkdir(parents=True)
    codex_home = config_path.expanduser().resolve().parent
    cache = codex_home / "plugins" / "cache"
    _tree(cache / install.CODEX_PLUGIN_MARKETPLACE / install.CODEX_PLUGIN_NAME / "0.1.0")
    _tree(codex_root / "plugins" / install.CODEX_PLUGIN_NAME)
    os.chmod(cache, 0o777)

    from unittest import mock

    with (
        mock.patch.object(install, "CODEX_INSTALL_ROOT", codex_root),
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex 0.130.0\n", "")),
        mock.patch.object(
            install, "_codex_payload_state",
            return_value=(install.PAYLOAD_SYNCHRONIZED, ""),
        ),
        mock.patch.object(install, "sync_codex_payload"),
    ):
        install.install_codex(
            dry_run=False, force=False, config_path=str(config_path), if_stale=True,
        )

    assert stat.S_IMODE(os.lstat(cache).st_mode) & 0o022, (
        "the installer chmodded a directory shared with other Codex plugins"
    )
    # The other half of the boundary: refused, but named. Without this the
    # docstring's "gets a named diagnosis" would be unasserted prose.
    state, _inv, reason = install._tree_inventory(
        cache / install.CODEX_PLUGIN_MARKETPLACE / install.CODEX_PLUGIN_NAME,
    )
    assert state == install.PAYLOAD_ERROR, (state, reason)
    assert str(cache) in reason and "0777" in reason, reason


def test_copytree_still_reproduces_the_original_hazard(tmp_path):
    """The premise, kept honest: `copy2` really does carry modes across.

    If this ever stops being true the normalization above becomes dead code,
    and a dead guard that still reads as protection is worse than none.
    """
    source = _tree(tmp_path / "source")
    _chmod_tree(source, 0o777)
    destination = tmp_path / "copy"

    shutil.copytree(source, destination)

    assert stat.S_IMODE(os.lstat(destination / "scripts" / "tool.py").st_mode) & 0o022
