from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
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
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/file.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(False, "fresh QA PASS required", "fp")),
        mock.patch.object(mod.subprocess, "run") as run,
    ):
        assert mod.install_verified(task) == 3
    run.assert_not_called()


def test_success_marker_skips_same_fingerprint_and_reinstalls_changed_diff(tmp_path):
    repo, task = _repo(tmp_path)
    installer = subprocess.CompletedProcess([], 0)
    common = (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/file.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_git_head_for_receipt", return_value="head"),
        mock.patch.object(mod, "_unreviewed_dirty_payload", return_value=[]),
        mock.patch.object(mod, "_unreviewed_tracked_payload", return_value=[]),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload-1"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
    )
    with common[0], common[1], common[2], common[3], common[4], common[5], common[6], common[7], common[8], common[9], common[10], common[11], mock.patch.object(
        mod, "_verification_state", return_value=(True, "", "fp-1")
    ), mock.patch.object(mod.subprocess, "run", return_value=installer) as run:
        assert mod.install_verified(task) == 0
        assert mod.install_verified(task) == 0
        assert run.call_count == 1

    receipt = json.loads((task / mod.RECEIPT_NAME).read_text())
    assert receipt["diff_fingerprint"] == "fp-1"
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/file.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global-2.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp-2")),
        mock.patch.object(mod, "_git_head_for_receipt", return_value="head"),
        mock.patch.object(mod, "_unreviewed_dirty_payload", return_value=[]),
        mock.patch.object(mod, "_unreviewed_tracked_payload", return_value=[]),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload-2"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod.subprocess, "run", return_value=installer) as run,
    ):
        assert mod.install_verified(task) == 0
        assert run.call_count == 1


def test_unreviewed_dirty_payload_blocks_install(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/old.py"}),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/old.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp")),
        mock.patch.object(mod, "_unreviewed_dirty_payload", return_value=["plugin/old.py"]),
        mock.patch.object(mod, "_unreviewed_tracked_payload", return_value=[]),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/old.py"}),
        mock.patch.object(mod.subprocess, "run") as run,
    ):
        assert mod.install_verified(task) == 4
    run.assert_not_called()


def test_source_change_during_install_withholds_success_marker(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/file.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", side_effect=[
            (True, "", "fp-1"), (True, "", "fp-2"),
        ]),
        mock.patch.object(mod, "_unreviewed_dirty_payload", return_value=[]),
        mock.patch.object(mod, "_unreviewed_tracked_payload", return_value=[]),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(
            mod, "_payload_fingerprint",
            side_effect=["payload-1", "payload-1", "payload-2"],
        ),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
    ):
        assert mod.install_verified(task) == 5
    assert not (task / mod.RECEIPT_NAME).exists()


def test_task_dir_must_be_canonical_and_active(tmp_path):
    repo, _ = _repo(tmp_path)
    rogue = repo / "rogue/TASK__fake"
    rogue.mkdir(parents=True)
    valid, reason = mod._validate_task_dir(repo, rogue.resolve())
    assert not valid
    assert "not canonical" in reason


def test_snapshot_paths_use_tracked_and_task_reviewed_files_only(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/tracked.py"}),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/new.py"]),
    ):
        paths = mod._snapshot_paths(repo, task)
    assert paths == {"plugin/tracked.py", "plugin/new.py"}
    assert "plugin/.omc/ignored-secret" not in paths


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
        mock.patch.object(mod, "_reviewable_source_paths", return_value=["plugin/file.py"]),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(True, "", "fp")),
        mock.patch.object(mod, "_unreviewed_dirty_payload", return_value=[]),
        mock.patch.object(mod, "_unreviewed_tracked_payload", return_value=[]),
        mock.patch.object(mod, "_tracked_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)),
    ):
        assert mod.install_verified(task) == 5
    assert not (task / mod.RECEIPT_NAME).exists()


def test_tracked_worktree_drift_hidden_from_status_requires_review(tmp_path):
    repo, task = _repo(tmp_path)
    hidden = repo / "plugin/hidden.py"
    hidden.write_text("changed\n", encoding="utf-8")
    stage = subprocess.CompletedProcess(
        [], 0, b"100644 indexhash 0\tplugin/hidden.py\0", b""
    )
    work = subprocess.CompletedProcess([], 0, b"workhash\n", b"")
    with (
        mock.patch.object(mod.subprocess, "run", side_effect=[stage, work]),
        mock.patch.object(mod, "_reviewable_source_paths", return_value=[]),
    ):
        uncovered = mod._unreviewed_tracked_payload(
            repo, task, {"plugin/hidden.py"}
        )
    assert uncovered == ["plugin/hidden.py"]


def test_reviewed_deletion_does_not_shift_hashes_for_later_files(tmp_path):
    repo, task = _repo(tmp_path)
    normal = repo / "plugin/z-normal.py"
    normal.write_text("normal\n", encoding="utf-8")
    stage = subprocess.CompletedProcess(
        [], 0,
        b"100644 deletedhash 0\tplugin/a-deleted.py\0"
        b"100644 normalhash 0\tplugin/z-normal.py\0",
        b"",
    )
    work = subprocess.CompletedProcess([], 0, b"normalhash\n", b"")
    with (
        mock.patch.object(mod.subprocess, "run", side_effect=[stage, work]),
        mock.patch.object(
            mod, "_reviewable_source_paths", return_value=["plugin/a-deleted.py"]
        ),
    ):
        uncovered = mod._unreviewed_tracked_payload(
            repo, task, {"plugin/a-deleted.py", "plugin/z-normal.py"}
        )
    assert uncovered == []


def test_tracked_hash_uses_git_path_filters(tmp_path):
    repo, task = _repo(tmp_path)
    filtered = repo / "plugin/filtered.txt"
    filtered.write_bytes(b"line\r\n")
    stage = subprocess.CompletedProcess(
        [], 0, b"100644 samehash 0\tplugin/filtered.txt\0", b""
    )
    work = subprocess.CompletedProcess([], 0, b"samehash\n", b"")
    with (
        mock.patch.object(mod.subprocess, "run", side_effect=[stage, work]) as run,
        mock.patch.object(mod, "_reviewable_source_paths", return_value=[]),
    ):
        assert mod._unreviewed_tracked_payload(
            repo, task, {"plugin/filtered.txt"}
        ) == []
    assert "--no-filters" not in run.call_args_list[1].args[0]
