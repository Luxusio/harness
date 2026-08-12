from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "install_verified", ROOT / "plugin/scripts/install_verified.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def _repo(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path
    (repo / ".git").mkdir()
    for rel, payload in (
        ("plugin-codex/.codex-plugin/plugin.json", {
            "name": "harness", "repository": "https://github.com/Luxusio/harness",
        }),
        ("plugin/.claude-plugin/plugin.json", {"name": "harness"}),
    ):
        path = repo / rel
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(payload), encoding="utf-8")
    (repo / "install.py").write_text("# fixture\n", encoding="utf-8")
    task = repo / "doc/harness/tasks/TASK__install"
    task.mkdir(parents=True)
    return repo, task


def test_trusted_repo_rejects_noncanonical_remote(tmp_path):
    repo, _ = _repo(tmp_path)
    with mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "https://evil.example/harness.git\n", "")):
        trusted, reason = mod._trusted_harness_repo(repo)
    assert not trusted
    assert "origin is not canonical" in reason


def test_install_requires_fresh_review_and_qa(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(False, "fresh QA PASS required", "fp")),
        mock.patch.object(mod.subprocess, "run") as run,
    ):
        assert mod.install_verified(task) == 3
    run.assert_not_called()


def test_verification_state_uses_one_receipt_snapshot(tmp_path):
    _, task = _repo(tmp_path)
    snapshot = object()
    with (
        mock.patch.object(mod, "read_task_control", return_value={}),
        mock.patch.object(mod, "receipt_snapshot", return_value=snapshot) as read_snapshot,
        mock.patch.object(mod, "receipt_stream_fingerprint", return_value="sha256:" + "a" * 64) as fingerprint,
        mock.patch.object(mod, "receipt_review_verdict", return_value="PASS") as review,
        mock.patch.object(mod, "receipt_runtime_verdict", return_value="PASS") as runtime,
    ):
        assert mod._verification_state(task) == (True, "", "sha256:" + "a" * 64)

    read_snapshot.assert_called_once_with(str(task))
    assert fingerprint.call_args.args[-1] is snapshot
    assert review.call_args.args[-1] is snapshot
    assert runtime.call_args.args[-1] is snapshot


def test_stateless_installer_reinstalls_every_verified_call_including_clean_source(tmp_path):
    repo, task = _repo(tmp_path)
    installer = subprocess.CompletedProcess([], 0)
    common = (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value=set()),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload-1"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
    )
    with common[0], common[1], common[2], common[3], common[4], common[5], common[6], common[7], common[8], common[9], mock.patch.object(
        mod, "_verification_state", return_value=(True, "", "fp-1")
    ), mock.patch.object(mod.subprocess, "run", return_value=installer) as run:
        assert mod.install_verified(task) == 0
        assert mod.install_verified(task) == 0
        assert run.call_count == 2
    assert not (task / "INSTALL_RECEIPT.json").exists()
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value=set()),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global-2.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp-2")),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload-2"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
        mock.patch.object(mod.subprocess, "run", return_value=installer) as run,
    ):
        assert mod.install_verified(task) == 0
        assert run.call_count == 1


def test_task_authority_mutation_waits_for_install_transaction(tmp_path):
    repo, task = _repo(tmp_path)
    task_lock = threading.Lock()
    installer_started = threading.Event()
    release_installer = threading.Event()
    installer_finished = threading.Event()
    mutation_attempted = threading.Event()
    mutation_finished = threading.Event()
    final_verification_finished = threading.Event()
    verification_calls = 0
    result = []

    @contextmanager
    def task_transaction(_task_dir):
        if threading.current_thread().name == "authority-mutator":
            mutation_attempted.set()
        with task_lock:
            yield

    def verification_state(_task_dir):
        nonlocal verification_calls
        assert task_lock.locked()
        verification_calls += 1
        if verification_calls == 3:
            final_verification_finished.set()
        return True, "", "fp"

    def run_installer(*_args, **_kwargs):
        assert task_lock.locked()
        installer_started.set()
        assert release_installer.wait(timeout=5)
        installer_finished.set()
        return subprocess.CompletedProcess([], 0)

    def mutate_authority():
        with mod.receipt_stream_transaction(str(task)):
            assert installer_finished.is_set()
            assert final_verification_finished.is_set()
            (task / "TASK.json").write_text('{"mutated": true}\n', encoding="utf-8")
        mutation_finished.set()

    install_thread = threading.Thread(
        target=lambda: result.append(mod.install_verified(task)),
        name="installer",
    )
    mutation_thread = threading.Thread(target=mutate_authority, name="authority-mutator")
    patches = (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", side_effect=verification_state),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
        mock.patch.object(mod, "receipt_stream_transaction", side_effect=task_transaction),
        mock.patch.object(mod.subprocess, "run", side_effect=run_installer),
    )
    for patcher in patches:
        patcher.start()
    try:
        install_thread.start()
        assert installer_started.wait(timeout=5)
        mutation_thread.start()
        assert mutation_attempted.wait(timeout=5)
        assert not mutation_finished.is_set()

        release_installer.set()
        install_thread.join(timeout=5)
        mutation_thread.join(timeout=5)
    finally:
        release_installer.set()
        for patcher in reversed(patches):
            patcher.stop()

    assert not install_thread.is_alive()
    assert not mutation_thread.is_alive()
    assert result == [0]
    assert verification_calls == 3
    assert mutation_finished.is_set()
    assert json.loads((task / "TASK.json").read_text(encoding="utf-8")) == {"mutated": True}


def test_payload_fingerprint_includes_mode_and_rejects_symlink(tmp_path):
    repo = tmp_path / "repo"
    payload = repo / "plugin/file.py"
    payload.parent.mkdir(parents=True)
    payload.write_text("print('ok')\n", encoding="utf-8")
    payload.chmod(0o644)
    regular = mod._payload_fingerprint(repo, {"plugin/file.py"})

    payload.chmod(0o755)
    assert mod._payload_fingerprint(repo, {"plugin/file.py"}) != regular

    payload.unlink()
    outside = tmp_path / "outside.py"
    outside.write_text("print('outside')\n", encoding="utf-8")
    payload.symlink_to(outside)
    assert mod._payload_fingerprint(repo, {"plugin/file.py"}) == ""


def test_payload_path_normalization_preserves_dot_root_and_rejects_escape():
    assert mod._is_install_payload_path(".claude-plugin/marketplace.json")
    assert mod._is_install_payload_path("./plugin/scripts/tool.py")
    assert not mod._is_install_payload_path("../plugin/CHANGELOG.md")
    assert not mod._is_install_payload_path("/plugin/CHANGELOG.md")
    assert not mod._is_install_payload_path("plugin//scripts/tool.py")
    assert not mod._is_install_payload_path("C:/plugin/scripts/tool.py")


def test_payload_modes_must_match_git_and_reject_hardlinks(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    payload = repo / "plugin/file.py"
    payload.parent.mkdir(parents=True)
    payload.write_text("print('ok')\n", encoding="utf-8")
    payload.chmod(0o644)
    subprocess.run(["git", "add", "plugin/file.py"], cwd=repo, check=True)
    paths = {"plugin/file.py"}
    assert mod._payload_modes_match_index(repo, paths)

    payload.chmod(0o755)
    assert not mod._payload_modes_match_index(repo, paths)
    payload.chmod(0o644)
    hardlink = repo / "plugin/file-hardlink.py"
    hardlink.hardlink_to(payload)
    assert not mod._payload_modes_match_index(repo, paths)


def test_payload_read_rejects_writable_directory_chain(tmp_path):
    repo = tmp_path / "repo"
    payload = repo / "plugin/file.py"
    payload.parent.mkdir(parents=True)
    payload.write_text("print('ok')\n", encoding="utf-8")
    payload.chmod(0o644)

    repo.chmod(0o777)
    assert mod._read_payload_file(repo, "plugin/file.py") is None
    repo.chmod(0o700)
    payload.parent.chmod(0o777)
    assert mod._read_payload_file(repo, "plugin/file.py") is None


def test_payload_mode_check_fails_closed_on_git_index_error(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    with mock.patch.object(mod, "_git_index_modes", return_value=None):
        assert not mod._payload_modes_match_index(repo, {"plugin/file.py"})


def test_payload_change_during_install_withholds_success_marker(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp-1")),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(
            mod, "_payload_fingerprint",
            side_effect=["payload-1", "payload-1", "payload-2"],
        ),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
        mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
    ):
        assert mod.install_verified(task) == 5
    assert not (task / "INSTALL_RECEIPT.json").exists()


def test_task_dir_must_be_canonical_and_active(tmp_path):
    repo, _ = _repo(tmp_path)
    rogue = repo / "rogue/TASK__fake"
    rogue.mkdir(parents=True)
    valid, reason = mod._validate_task_dir(repo, rogue.resolve())
    assert not valid
    assert "not canonical" in reason


def test_task_dir_requires_open_exact_session_generation(tmp_path):
    repo, task = _repo(tmp_path)
    control = {
        "run_id": "0198c349-5800-7000-8000-000000000001",
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }
    with (
        mock.patch.object(mod, "read_task_control", return_value=control),
        mock.patch.object(mod, "active_task_binding_matches", return_value=False) as binding,
    ):
        valid, reason = mod._validate_task_dir(repo, task.resolve())
    assert not valid
    assert "open active TASK.json generation" in reason
    binding.assert_called_once_with(str(repo), str(task.resolve()), control)


def test_snapshot_paths_use_tracked_and_dirty_payload_files(tmp_path):
    repo, task = _repo(tmp_path)
    for rel in ("plugin/tracked.py", "plugin/new.py"):
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("ok\n", encoding="utf-8")
    with (
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/tracked.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/new.py"}),
    ):
        paths = mod._snapshot_paths(repo, task)
    assert paths == {"plugin/tracked.py", "plugin/new.py"}
    assert "plugin/.omc/ignored-secret" not in paths


def test_snapshot_paths_exclude_deleted_payload(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/deleted.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/deleted.py"}),
    ):
        paths = mod._snapshot_paths(repo, task)
    assert paths == set()


def test_global_lock_ignores_xdg_cache_and_follows_installer_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    assert mod._global_lock_path() == tmp_path / "home/.cache/harness/install.lock"


def test_active_task_switch_during_install_withholds_marker(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", side_effect=[
            (True, ""), (False, "task is not active"),
        ]),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp")),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
        mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
    ):
        assert mod.install_verified(task) == 5
    assert not (task / "INSTALL_RECEIPT.json").exists()
