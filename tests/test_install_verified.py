from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


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
    repo.mkdir(parents=True, exist_ok=True)
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
    import wbs
    control = {"run_id": "0198c349-5800-7000-8000-000000000001", "closed": False,
               "execution_mode": "standard", "required_lenses": ["review-code", "qa-cli"]}
    (task / "TASK.json").write_text(json.dumps(control))
    plan = '# Plan\n```wbs\n[{"id":"A","title":"Installed","weight":1}]\n```\n'
    (task / "PLAN.md").write_text(plan)
    (task / "WBS.json").write_text(json.dumps(wbs.prepare(str(task), plan, control["run_id"])))
    return repo, task


def test_trusted_repo_rejects_noncanonical_remote(tmp_path):
    repo, _ = _repo(tmp_path)
    with mock.patch.object(mod.subprocess, "run", return_value=subprocess.CompletedProcess([], 0, "https://evil.example/harness.git\n", "")):
        trusted, reason = mod._trusted_harness_repo(repo)
    assert not trusted
    assert "origin is not canonical" in reason


def test_install_requires_current_wbs_checks(tmp_path):
    repo, task = _repo(tmp_path)
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", return_value=(True, "")),
        mock.patch.object(mod, "_snapshot_paths", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_dirty_install_payload", return_value={"plugin/file.py"}),
        mock.patch.object(mod, "_global_lock_path", return_value=tmp_path / "global.lock"),
        mock.patch.object(mod, "_verification_state", return_value=(False, "current WBS checks required", "fp")),
        mock.patch.object(mod, "_payload_modes_match_index", return_value=True),
        mock.patch.object(mod, "_payload_fingerprint", return_value="payload"),
        mock.patch.object(mod, "_copy_payload_snapshot"),
        mock.patch.object(mod.subprocess, "run") as run,
    ):
        assert mod.install_verified(task) == 5
    run.assert_not_called()


def test_verification_state_tracks_actual_task_wbs_and_target_applicability(tmp_path):
    import wbs
    _, task = _repo(tmp_path)
    control = {"run_id": "run", "closed": False}
    state = {"checks": {"A": "current"}}
    context = {"runtime_verdict": "PASS", "missing_for_close": [],
               "verification_targets": {"status": "applicable"}}
    with (
        mock.patch.object(mod, "read_task_control", return_value=control),
        mock.patch.object(mod, "emit_compact_context", return_value=context) as measured,
        mock.patch.object(wbs, "read_state", return_value=state),
    ):
        ready, reason, fingerprint = mod._verification_state(task)
        assert ready and not reason
        assert mod._verification_state(task)[2] == fingerprint
        state["checks"]["A"] = "changed"
        assert mod._verification_state(task)[2] != fingerprint
        state["checks"]["A"] = "current"
        control["run_id"] = "new-run"
        assert mod._verification_state(task)[2] != fingerprint
        control["run_id"] = "run"
        context["verification_targets"]["status"] = "stale"
        assert mod._verification_state(task)[2] != fingerprint
        context["runtime_verdict"] = "PENDING"
        context["missing_for_close"] = ["current WBS checks"]
        ready, reason, _ = mod._verification_state(task)
        assert not ready and "current WBS checks" in reason
    measured.assert_called_with(str(task), measure_wbs=True)


def test_stateless_installer_rechecks_every_call_with_conditional_mode(tmp_path):
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
        assert all(
            call.args[0][-1].endswith("install.py")
            for call in run.call_args_list
        )
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
        # The default run is the conditional refresh; `--force` would reinstall
        # synchronized runtimes on every close.
        assert run.call_args.args[0][-1].endswith("install.py")


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
        if verification_calls == 2:
            final_verification_finished.set()
        return True, "", "fp"

    def run_installer(*_args, **_kwargs):
        assert task_lock.locked()
        installer_started.set()
        assert release_installer.wait(timeout=5)
        installer_finished.set()
        return subprocess.CompletedProcess([], 0)

    def mutate_authority():
        with mod.task_control_transaction(str(task)):
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
        mock.patch.object(mod, "task_control_transaction", side_effect=task_transaction),
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
    assert verification_calls == 2
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
            side_effect=["payload-1", "payload-1", "payload-1", "payload-2"],
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
    valid, reason = mod._validate_task_dir(repo, rogue.resolve(), "sid-9")
    assert not valid
    assert "not canonical" in reason


def test_task_dir_requires_open_exact_session_generation(tmp_path):
    repo, task = _repo(tmp_path)
    control = {
        "run_id": "0198c349-5800-7000-8000-000000000001",
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "closed": False,
    }
    with (
        mock.patch.object(mod, "read_task_control", return_value=control),
        mock.patch.object(mod, "active_task_binding_matches", return_value=False) as binding,
    ):
        valid, reason = mod._validate_task_dir(repo, task.resolve(), "sid-9")
    assert not valid
    assert "open active TASK.json generation" in reason
    # The resolved id must reach the binding check; without it the lookup falls
    # back to "default" and misses the marker this session wrote.
    binding.assert_called_once_with(
        str(repo), str(task.resolve()), control, session_id="sid-9",
    )


def test_resolved_session_id_uses_only_actual_caller(tmp_path):
    repo, task = _repo(tmp_path)
    sessions = repo / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / ".session-hint").write_text("another-actor\n", encoding="utf-8")
    for sid in ("real-sid", "default", "unknown"):
        with mock.patch.object(mod, "current_session_id", return_value=sid):
            assert mod._resolved_session_id(repo) == sid
    with mock.patch.object(mod, "active_task_binding_matches", return_value=True) as binding:
        valid, reason = mod._validate_task_dir(repo, task.resolve(), "default")
    assert not valid and "session identity unavailable" in reason
    binding.assert_not_called()


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


def _measured_repo(tmp_path, monkeypatch):
    """Real Git, WBS checks, source measurement, payload copy and installer CLI."""
    import wbs
    repo, task = _repo(tmp_path / "repo")
    (repo / ".git").rmdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "remote.origin.url", mod.CANONICAL_REMOTE], cwd=repo, check=True)
    (repo / ".gitignore").write_text("doc/harness/tasks/\n")
    marker = tmp_path / "installed"
    (repo / "install.py").write_text(
        "from pathlib import Path\n"
        "assert Path('plugin/source.bin').read_bytes().startswith(b'good')\n"
        f"Path({str(marker)!r}).write_text('installed')\n"
    )
    (repo / "plugin/source.bin").write_bytes(b"good" + b"x" * (2 * 1024 * 1024 - 4))
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                    "commit", "-qm", "fixture"], cwd=repo, check=True)
    control = mod.read_task_control(str(task))
    nodes = [{"id": "A", "title": "Current payload", "weight": 1,
              "check": [sys.executable, "-c", "from pathlib import Path; assert Path('plugin/source.bin').read_bytes().startswith(b'good')"]}]
    plan = "# Plan\n```wbs\n" + json.dumps(nodes) + "\n```\n```wbs-inputs\n" + json.dumps(
        {"source": ["install.py", "plugin", "plugin-codex"]}) + "\n```\n"
    (task / "WBS.json").unlink()
    state = wbs.prepare(str(task), plan, control["run_id"])
    (task / "PLAN.md").write_text(plan)
    (task / "WBS.json").write_text(json.dumps(state))
    assert wbs.verify(str(task), str(repo), control["run_id"], "A")["passed"]
    sessions = task.parent / ".active_sessions"
    sessions.mkdir()
    (sessions / "installer-test.json").write_text(json.dumps({
        "session_id": "installer-test", "task_dir": str(task), "task_id": task.name,
        "run_id": control["run_id"], "updated": "2026-10-29T00:00:00Z",
    }))
    monkeypatch.setattr(mod, "_resolved_session_id", lambda _: "installer-test")
    monkeypatch.setattr(mod, "_global_lock_path", lambda: tmp_path / "install.lock")
    assert mod._validate_task_dir(repo, task, "installer-test")[0]
    assert mod._verification_state(task)[0]
    return repo, task, marker


def test_real_install_uses_two_full_measurements_and_repeats_fresh(tmp_path, monkeypatch):
    import wbs
    repo, task, marker = _measured_repo(tmp_path, monkeypatch)
    original_fingerprint, original_fdopen = wbs.fingerprint, wbs.os.fdopen
    active = False
    totals = {"measurements": 0, "bytes": 0, "reads": 0, "opens": 0}

    class Counted:
        def __init__(self, stream):
            self.stream = stream
        def __enter__(self):
            self.stream.__enter__()
            return self
        def __exit__(self, *args):
            return self.stream.__exit__(*args)
        def fileno(self):
            return self.stream.fileno()
        def read(self, size):
            data = self.stream.read(size)
            totals["bytes"] += len(data)
            totals["reads"] += 1
            return data

    def fdopen(fd, *args, **kwargs):
        stream = original_fdopen(fd, *args, **kwargs)
        if active and args and args[0] == "rb":
            totals["opens"] += 1
            return Counted(stream)
        return stream

    def fingerprint(*args, **kwargs):
        nonlocal active
        totals["measurements"] += 1
        active = True
        try:
            return original_fingerprint(*args, **kwargs)
        finally:
            active = False

    monkeypatch.setattr(wbs, "fingerprint", fingerprint)
    monkeypatch.setattr(wbs.os, "fdopen", fdopen)
    # Same real fixture, old measurement lane versus the actual new install path.
    for _ in range(3):
        assert mod._verification_state(task)[0]
    three = dict(totals)
    for attempt in range(2):
        totals.update(dict.fromkeys(totals, 0))
        marker.unlink(missing_ok=True)
        assert mod.install_verified(task) == 0
        assert marker.read_text() == "installed"
        assert totals["measurements"] == 2
        assert totals["bytes"] * 3 == three["bytes"] * 2
        assert totals["reads"] * 3 == three["reads"] * 2
        assert totals["opens"] * 3 == three["opens"] * 2
        print(f"real WBS measurement lane attempt {attempt + 1}: three={three}, installed={totals}")
    assert not (task / "INSTALL_RECEIPT.json").exists()


@pytest.mark.parametrize("drift", ["copy", "source", "mode", "path", "definition", "control", "binding", "unverified-swap"])
def test_real_preinstall_drift_never_executes_snapshot(tmp_path, monkeypatch, drift):
    import wbs
    repo, task, marker = _measured_repo(tmp_path, monkeypatch)
    original_copy = mod._copy_payload_snapshot
    source = repo / "plugin/source.bin"
    good = source.read_bytes()
    if drift == "unverified-swap":
        source.write_bytes(b"bad!" + good[4:])
        assert not mod._verification_state(task)[0]

    def copy(*args):
        original_copy(*args)
        if drift == "copy":
            (args[1] / "plugin/source.bin").write_bytes(b"tampered")
        elif drift == "source":
            source.write_bytes(b"changed")
        elif drift == "mode":
            source.chmod(0o755)
        elif drift == "path":
            (repo / "plugin/new.py").write_text("new")
        elif drift == "definition":
            state = wbs.read_state(str(task))
            state["checks"]["A"]["checked_at"] += 1
            (task / "WBS.json").write_text(json.dumps(state))
        elif drift == "control":
            control = mod.read_task_control(str(task))
            control["required_lenses"].append("review-security")
            (task / "TASK.json").write_text(json.dumps(control))
        elif drift == "binding":
            (task.parent / ".active_sessions/installer-test.json").unlink()
        elif drift == "unverified-swap":
            # Full current WBS verification now passes, but the copied old bytes
            # were never verified and must not become executable authority.
            source.write_bytes(good)
            assert mod._verification_state(task)[0]

    monkeypatch.setattr(mod, "_copy_payload_snapshot", copy)
    assert mod.install_verified(task) == 5
    assert not marker.exists()


@pytest.mark.parametrize("drift", ["source", "mode", "path", "definition", "control", "binding"])
def test_real_postinstall_drift_withholds_success(tmp_path, monkeypatch, capsys, drift):
    repo, task, marker = _measured_repo(tmp_path, monkeypatch)
    original_run = mod.subprocess.run

    def run(command, *args, **kwargs):
        result = original_run(command, *args, **kwargs)
        if len(command) == 2 and str(command[1]).endswith("/install.py"):
            if drift == "source":
                (repo / "plugin/source.bin").write_bytes(b"changed")
            elif drift == "mode":
                (repo / "plugin/source.bin").chmod(0o755)
            elif drift == "path":
                (repo / "plugin/new.py").write_text("new")
            elif drift == "definition":
                state = json.loads((task / "WBS.json").read_text())
                state["checks"]["A"]["checked_at"] += 1
                (task / "WBS.json").write_text(json.dumps(state))
            elif drift == "control":
                control = mod.read_task_control(str(task))
                control["required_lenses"].append("review-security")
                (task / "TASK.json").write_text(json.dumps(control))
            elif drift == "binding":
                (task.parent / ".active_sessions/installer-test.json").unlink()
        return result

    monkeypatch.setattr(mod.subprocess, "run", run)
    assert mod.install_verified(task) == 5
    assert marker.exists()
    assert "verified delivery PASS" not in capsys.readouterr().out


@pytest.mark.parametrize("name", ["TASK.json", "WBS.json"])
def test_definition_baseline_rejects_unsafe_metadata(tmp_path, name):
    _, task = _repo(tmp_path)
    path = task / name
    path.chmod(0o666)
    with pytest.raises(ValueError, match="unsafe or unreadable"):
        mod._verification_definition(task)


@pytest.mark.parametrize("denied,leaf_override", [
    ("plugin", False), ("plugin/source.bin", False), ("plugin/source.bin", True),
])
def test_denied_payload_is_refused_before_any_payload_read(tmp_path, monkeypatch, denied, leaf_override):
    import wbs
    repo, task, marker = _measured_repo(tmp_path, monkeypatch)
    state = wbs.read_state(str(task))
    state["inputs"]["deny"] = [denied]
    if leaf_override:
        # A specialized leaf cannot erase the task-wide no-read boundary.
        state["nodes"][0]["inputs"] = wbs.input_policy({"source": ["install.py"]})
    (task / "WBS.json").write_text(json.dumps(state))
    (task / "PLAN.md").write_text(
        "# Plan\n```wbs\n" + json.dumps(state["nodes"]) + "\n```\n```wbs-inputs\n"
        + json.dumps(state["inputs"]) + "\n```\n"
    )
    reads = []
    original_read = mod._read_payload_file

    def read(root, path, **kwargs):
        if root == repo:
            reads.append(path)
        return original_read(root, path, **kwargs)

    monkeypatch.setattr(mod, "_read_payload_file", read)
    assert mod.install_verified(task) == 3
    assert reads == []
    assert not marker.exists()


def test_active_task_switch_during_install_withholds_marker(tmp_path):
    repo, task = _repo(tmp_path)
    validate = mock.Mock(side_effect=[(True, ""), (True, ""), (False, "task is not active")])
    with (
        mock.patch.object(mod, "find_repo_root", return_value=str(repo)),
        mock.patch.object(mod, "_trusted_harness_repo", return_value=(True, "")),
        mock.patch.object(mod, "_validate_task_dir", validate),
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
    # Both binding checks must use one id resolved once. Re-inlining the
    # resolution inside _validate_task_dir would let a prompt hook rewriting
    # .session-hint mid-install make the post-check consult a different
    # session than the pre-check.
    assert validate.call_count == 3
    assert len({call.args[2] for call in validate.call_args_list}) == 1
