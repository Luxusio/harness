"""An unsafe ancestor of the payload path names the command that fixes it.

2026-09-26: on a host where `~/.claude` and `~/.codex` are root:root 0777,
The conditional `install.py` run (and so `install_verified.py`) refused every run with
"unsafe payload path component", then printed `--force` as the repair. `--force`
never changes a directory above the payload, so the printed repair looped. The
trust rule is unchanged; the refusal now carries a remedy that satisfies it.
Inside the home directory that remedy is chown + go-w, not `+t`: sticky only
stops renaming other users' entries, and any local user could still create new
startup files there.
"""
from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
HOME = Path("/home/someone")
CHOWN = 'sudo chown "$(id -u):$(id -g)" {c} && chmod go-w {c}'


def _load_install_module():
    spec = importlib.util.spec_from_file_location("harness_install_remedy", REPO_ROOT / "install.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


install = _load_install_module()


def _remedy(component, uid, *, final=False, current_uid=1001, mode=0o777):
    return install._unsafe_component_remedy(
        Path(component), uid, mode, final=final, current_uid=current_uid, home=HOME,
    )


def test_a_directory_you_own_gets_go_w():
    assert _remedy("/home/someone/.claude", 1001) == "chmod go-w /home/someone/.claude"
    assert _remedy("/data/mine", 1001) == "chmod go-w /data/mine"


def test_running_as_root_does_not_make_a_system_dir_yours():
    """Location is judged before ownership: never `chmod go-w /opt`."""
    for final in (False, True):
        remedy = _remedy("/opt", 0, current_uid=0, final=final)
        assert "chmod" not in remedy and "chown" not in remedy, remedy
        assert "HARNESS_DEST" in remedy


def test_a_root_owned_config_dir_in_home_gets_chown_not_sticky():
    assert _remedy("/home/someone/.claude", 0) == CHOWN.format(c="/home/someone/.claude")


def test_a_shared_dir_outside_home_is_never_altered():
    """Outside home the remedy points at another install root instead."""
    for component, uid, mode in (("/opt/shared", 0, 0o777), ("/tmp", 0, 0o1777),
                                 ("/srv/other", 1234, 0o1777)):
        for final in (False, True):
            remedy = _remedy(component, uid, mode=mode, final=final)
            assert "chmod" not in remedy and "chown" not in remedy, (component, remedy)
            assert "HARNESS_DEST" in remedy and "--config-path" in remedy
            # Only knobs install.py actually reads.
            assert "CODEX_HOME" not in remedy


def test_another_users_directory_gets_no_command():
    remedy = _remedy("/home/someone/shared", 1234)
    assert "owned by uid 1234" in remedy and "chmod" not in remedy


def test_paths_are_shell_quoted():
    assert _remedy("/home/someone/my dir", 1001) == "chmod go-w '/home/someone/my dir'"


def test_an_existing_payload_under_a_writable_ancestor_names_the_fix(tmp_path):
    shared = tmp_path / "shared"
    target = shared / "harness-dev"
    target.mkdir(parents=True)
    os.chmod(shared, 0o777)
    try:
        state, _inventory, reason = install._tree_inventory(target)
    finally:
        os.chmod(shared, 0o755)
    assert state == install.PAYLOAD_ERROR
    assert f"unsafe payload path component: {shared}" in reason
    assert install._repair_from_reason(reason) == f"chmod go-w {shared}"


def test_a_missing_payload_under_a_writable_intermediate_names_the_fix(tmp_path):
    """The writable existing component is refused before the missing part."""
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    try:
        state, _inventory, reason = install._tree_inventory(shared / "harness-dev")
    finally:
        os.chmod(shared, 0o755)
    assert state == install.PAYLOAD_ERROR
    assert install._repair_from_reason(reason) == f"chmod go-w {shared}"


def _missing(ancestor, uid, mode, next_component, *, current_uid=1001):
    return install._missing_target_remedy(
        Path(ancestor), uid, mode, Path(next_component),
        current_uid=current_uid, home=HOME,
    )


def test_a_missing_target_under_a_shared_sticky_dir_creates_the_next_dir():
    """Never chown or chmod a shared directory such as /tmp."""
    remedy = _missing("/tmp", 0, 0o1777, "/tmp/harness-root")
    assert remedy == "mkdir -m 0755 /tmp/harness-root"
    # Running as root does not turn it into `chmod go-w /tmp` either.
    assert _missing("/tmp", 0, 0o1777, "/tmp/harness-root", current_uid=0) == remedy


def test_a_missing_target_under_a_root_system_dir_creates_it_as_you():
    expected = 'sudo install -d -o "$(id -u)" -g "$(id -g)" -m 0755 /opt/harness'
    assert _missing("/opt", 0, 0o755, "/opt/harness") == expected
    assert _missing("/opt", 0, 0o755, "/opt/harness", current_uid=0) == expected


def test_a_sticky_dir_owned_by_someone_else_gets_no_mkdir():
    """Only a root-owned sticky ancestor is trusted by the rule."""
    remedy = _missing("/srv/shared", 1234, 0o1777, "/srv/shared/next")
    assert "mkdir" not in remedy and "chmod" not in remedy


def test_a_missing_target_under_a_root_config_dir_in_home_takes_it_over():
    assert _missing("/home/someone/.codex", 0, 0o755, "/home/someone/.codex/plugins") == (
        CHOWN.format(c="/home/someone/.codex")
    )


def test_a_missing_target_under_another_users_dir_gets_no_command():
    remedy = _missing("/home/someone/other", 1234, 0o755, "/home/someone/other/x")
    assert "owned by uid 1234" in remedy and "mkdir" not in remedy


def test_a_missing_target_under_tmp_names_the_next_dir_end_to_end():
    """The real branch: nearest existing ancestor /tmp, two levels missing."""
    info = os.stat("/tmp")
    if info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
        import pytest
        pytest.skip("/tmp is not a root-owned sticky directory on this host")
    missing = Path("/tmp") / f"harness-remedy-{os.getpid()}-{id(info)}"
    assert not missing.exists()
    state, _inventory, reason = install._tree_inventory(missing / "leaf")
    assert state == install.PAYLOAD_ERROR
    assert reason.startswith("unsafe nearest existing ancestor for missing target: /tmp ")
    repair = install._repair_from_reason(reason)
    assert repair == f"mkdir -m 0755 {missing}"
    assert "chown" not in repair and "chmod" not in repair


def test_install_claude_carries_the_repair(tmp_path, monkeypatch):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o777)
    monkeypatch.setenv("HARNESS_DEST", str(shared / "harness-dev"))
    try:
        with (
            mock.patch.object(install.shutil, "which", return_value="/bin/claude"),
            mock.patch.object(install, "_run", return_value=(0, "claude 2.1.0\n", "")),
        ):
            result = install.install_claude(dry_run=False, force=False)
    finally:
        os.chmod(shared, 0o755)
    assert not result.ok
    assert result.repair == f"chmod go-w {shared}"


def test_a_reason_without_a_remedy_has_no_repair():
    assert install._repair_from_reason("payload root changed during comparison") is None


def test_the_trust_rule_still_accepts_a_root_owned_sticky_ancestor():
    """Remedies mirror the rule; the rule itself is unchanged."""
    source = (REPO_ROOT / "install.py").read_text(encoding="utf-8")
    assert "sticky_shared = info.st_uid == 0 and bool(mode & stat.S_ISVTX)" in source


def test_main_prints_the_remedy_instead_of_force(tmp_path, monkeypatch, capsys):
    failing = install.InstallResult(
        "claude", False,
        "Claude payload comparison failed: unsafe payload path component: /x",
        repair="chmod go-w /x",
    )
    monkeypatch.setattr(sys, "argv", ["install.py", "--claude-only", "--verbose"])
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(install, "install_claude", lambda **_kw: failing)
    install.main()
    out = capsys.readouterr().out
    assert "repair: chmod go-w /x, then re-run: python3 install.py --claude-only" in out
    assert "--force" not in out


def test_main_keeps_the_force_repair_for_other_failures(monkeypatch, capsys):
    failing = install.InstallResult("claude", False, "Claude payload comparison failed: boom")
    monkeypatch.setattr(sys, "argv", ["install.py", "--claude-only", "--verbose"])
    monkeypatch.setattr(install.shutil, "which", lambda name: f"/bin/{name}")
    monkeypatch.setattr(install, "install_claude", lambda **_kw: failing)
    install.main()
    assert "python3 install.py --claude-only --force" in capsys.readouterr().out


def test_install_codex_carries_the_repair(monkeypatch):
    reason = "Codex mirror: unsafe payload path component: /x (mode 0777, uid 0) while inspecting /x/h; fix: chmod go-w /x"
    monkeypatch.setattr(install, "_codex_payload_state", lambda _config: (install.PAYLOAD_ERROR, reason))
    with (
        mock.patch.object(install.shutil, "which", return_value="/bin/codex"),
        mock.patch.object(install, "_run", return_value=(0, "codex-cli 9.9.9\n", "")),
        mock.patch.object(install, "_prune_bytecode_caches", return_value=[]),
        mock.patch.object(install, "_normalize_payload_modes", return_value=[]),
    ):
        result = install.install_codex(dry_run=False, force=False, config_path=None)
    assert not result.ok
    assert result.repair == "chmod go-w /x"


def test_a_home_of_root_makes_nothing_yours():
    """A container uid without a passwd entry gets HOME=/; never `chown /`."""
    for component in ("/", "/opt", "/tmp"):
        remedy = install._unsafe_component_remedy(
            Path(component), 0, 0o777, final=True, current_uid=1001, home=Path("/"),
        )
        assert "chown" not in remedy and "chmod" not in remedy, (component, remedy)
    missing = install._missing_target_remedy(
        Path("/"), 0, 0o755, Path("/nonexistent-harness-x"), current_uid=1001, home=Path("/"),
    )
    assert "chown" not in missing and "chmod" not in missing, missing


def test_a_writable_ancestor_of_home_asks_for_an_administrator():
    """No install root under home can avoid a writable /home."""
    remedy = _remedy("/home", 0, final=False)
    assert "above your home" in remedy and "administrator" in remedy
    assert "chown" not in remedy and "HARNESS_DEST" not in remedy
