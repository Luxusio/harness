"""One-time import preserves data, refuses ambiguity, and resumes publication."""
import copy
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))
import _lib
import goal_migration as migration


def write(repo, relative, value):
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, sort_keys=True).encode())
    return path


def row(repo, *, goal="GOAL__old", objective="Finish requested work", tasks=None):
    value = dict(goal_id=goal, objective=objective, tasks=tasks or [], status="complete")
    path = write(repo, migration.GOALS + "/" + goal + ".json", value)
    write(repo, migration.GOALS + "/current.json", path.read_bytes())
    return path


@pytest.fixture
def owner(monkeypatch):
    monkeypatch.setattr(_lib, "_trusted_control_writer", lambda: True)


def spec(goal="GOAL__old", task="TASK__integration"):
    requests = [dict(slug=slug, request="Implement " + slug, scopes=[slug], depends_on=[]) for slug in ("a", "b")]
    identity = json.dumps([goal, task], separators=(",", ":")).encode()
    return dict(batch_id="goal-" + hashlib.sha256(identity).hexdigest(), requests=requests)


def test_preview_read_only_and_apply_archive_idempotent(tmp_path, owner):
    path = row(tmp_path, tasks=[dict(task_id="TASK__ordinary", title="Historical assertion", status="closed")])
    original = path.read_bytes()
    report = migration.preview(tmp_path)
    assert report["status"] == "preview" and len(report["destinations"]) == 1
    assert not (tmp_path / migration.ARCHIVE).exists()
    result = migration.apply(tmp_path)
    assert result["status"] == "complete" and not (tmp_path / migration.GOALS).exists()
    assert (tmp_path / migration.ARCHIVE / "original" / migration.GOALS / path.name).read_bytes() == original
    note = (tmp_path / report["destinations"][0]).read_text()
    assert "unverified historical intent" in note and "Historical assertion" in note
    assert not list(tmp_path.rglob("TASK.json")) and not list(tmp_path.rglob("WBS.json"))
    assert migration.apply(tmp_path) == result == migration.preview(tmp_path)


def test_apply_requires_owner(tmp_path):
    row(tmp_path)
    with pytest.raises(ValueError, match="MCP owner"):
        migration.apply(tmp_path)
    assert not (tmp_path / migration.ARCHIVE).exists()


@pytest.mark.parametrize("corrupt", ["duplicate", "current", "filename", "child_path", "duplicate_child", "oversized"])
def test_bad_inputs_refuse_before_any_publication(tmp_path, owner, corrupt):
    path = row(tmp_path)
    if corrupt == "duplicate":
        path.write_text('{"goal_id":"GOAL__old","goal_id":"GOAL__old"}')
    elif corrupt == "current":
        write(tmp_path, migration.GOALS + "/current.json", dict(goal_id="GOAL__other", objective="Other", tasks=[]))
    elif corrupt == "filename":
        path.rename(path.with_name("GOAL__wrong.json"))
    elif corrupt == "child_path":
        row(tmp_path, tasks=[dict(task_id="TASK__a", task_dir="../../outside")])
    elif corrupt == "duplicate_child":
        row(tmp_path, tasks=[dict(task_id="TASK__a"), dict(task_id="TASK__a")])
    else:
        path.write_bytes(b" " * (migration.LIMIT + 1))
    with pytest.raises(ValueError):
        migration.apply(tmp_path)
    assert not (tmp_path / migration.ARCHIVE).exists()


@pytest.mark.parametrize("location", ["file", "directory", "target", "hardlink"])
def test_unsafe_paths_refuse(tmp_path, owner, location):
    path = row(tmp_path)
    outside = tmp_path / "outside"
    outside.write_text("untouched")
    if location == "file":
        path.unlink()
        path.symlink_to(outside)
    elif location == "directory":
        goal_dir = path.parent
        moved = tmp_path / "old"
        goal_dir.rename(moved)
        goal_dir.symlink_to(moved, target_is_directory=True)
    elif location == "hardlink":
        (tmp_path / "link").hardlink_to(path)
    else:
        target = tmp_path / migration.preview(tmp_path)["destinations"][0]
        target.parent.mkdir(parents=True)
        target.symlink_to(outside)
    with pytest.raises(ValueError):
        migration.apply(tmp_path)
    assert outside.read_text() == "untouched"


def test_notes_aggregate_objective_without_claiming_user_source(tmp_path, owner):
    row(tmp_path, goal="GOAL__first")
    row(tmp_path, goal="GOAL__second")
    result = migration.apply(tmp_path)
    assert len(result["destinations"]) == 1
    note = (tmp_path / result["destinations"][0]).read_text()
    assert "GOAL__first" in note and "GOAL__second" in note
    assert "not confirmed user requirements or completion evidence" in note


def test_missing_pool_preview_has_exact_recovery_and_apply_refuses(tmp_path, owner):
    value = spec()
    row(tmp_path, tasks=[dict(task_id="TASK__integration", batch=value)])
    report = migration.preview(tmp_path)
    recovery = report["blockers"][0]
    assert recovery["batch_id"] == value["batch_id"] and recovery["requests"] == value["requests"]
    assert recovery["task_id"] == "TASK__integration" and recovery["wbs_id"] == "WBS__integration"
    with pytest.raises(ValueError, match="missing legacy pool"):
        migration.apply(tmp_path)
    assert not (tmp_path / migration.ARCHIVE).exists()


def test_initialized_pool_import_is_unbaselined_not_new_admission(tmp_path, monkeypatch, owner):
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    value = spec()
    pool.batch = value["batch_id"]
    pool.init(value["requests"])
    row(pool.repo, tasks=[dict(task_id="TASK__integration", batch=value)])
    import work_pack
    monkeypatch.setattr(work_pack, "make_spec", lambda *a: pytest.fail("must not re-admit an existing pool"))
    result = migration.apply(pool.repo)
    assert not result["blockers"]
    state_path = pool.repo / "doc/harness/tasks/TASK__integration/WBS.json"
    state = json.loads(state_path.read_text())
    assert state == dict(version=1, nodes=[], checks={}, run_id="", started_at=0, last_progress_at=0,
                         pack_only=True, batch=dict(task_id="TASK__integration", **value))
    assert not state_path.with_name("TASK.json").exists()
    assert json.loads(pool.state.read_text())["batch_id"] == value["batch_id"]


def test_wbs_metadata_only_preserves_evidence_and_opaque_pool(tmp_path, owner):
    value = spec("GOAL__wbs-old")
    state = dict(version=1, nodes=[dict(id="one", title="Observed result", weight=1, parent=None, depends_on=[])],
                 checks={}, run_id="existing-run", started_at=19, last_progress_at=23,
                 batch_goal_id="GOAL__wbs-old", batch=value,
                 batch_history=[dict(batch_goal_id="GOAL__wbs-old", batch=value)])
    path = write(tmp_path, "doc/harness/tasks/TASK__integration/WBS.json", state)
    control = write(tmp_path, "doc/harness/tasks/TASK__integration/TASK.json", b'old exact control bytes')
    receipt = write(tmp_path, "doc/harness/tasks/TASK__integration/RECEIPTS.json", b'old exact receipt bytes')
    originals = (path.read_bytes(), control.read_bytes(), receipt.read_bytes())
    migration.apply(tmp_path)
    expected = copy.deepcopy(state)
    del expected["batch_goal_id"]
    del expected["batch_history"][0]["batch_goal_id"]
    expected["batch"]["task_id"] = "TASK__integration"
    expected["batch_history"][0]["batch"]["task_id"] = "TASK__integration"
    assert json.loads(path.read_text()) == expected
    assert (control.read_bytes(), receipt.read_bytes()) == originals[1:]
    assert (tmp_path / migration.ARCHIVE / "original/doc/harness/tasks/TASK__integration/WBS.json").read_bytes() == originals[0]


@pytest.mark.parametrize("failure_stage", ["publish", "retire"])
def test_interrupted_publication_resumes_exactly(tmp_path, monkeypatch, owner, failure_stage):
    row(tmp_path)
    original_write = migration._write
    original_unlink = Path.unlink
    fired = []
    def fail_write(repo, relative, data):
        if relative.endswith("MIGRATION.md") and not fired:
            fired.append(True)
            raise OSError("interrupted publication")
        return original_write(repo, relative, data)
    def fail_unlink(path, *args, **kwargs):
        if path.parent.name == "goals" and not fired:
            fired.append(True)
            raise OSError("interrupted retirement")
        return original_unlink(path, *args, **kwargs)
    if failure_stage == "publish":
        monkeypatch.setattr(migration, "_write", fail_write)
    else:
        monkeypatch.setattr(Path, "unlink", fail_unlink)
    with pytest.raises(OSError):
        migration.apply(tmp_path)
    journal = json.loads((tmp_path / migration.MANIFEST).read_text())
    assert journal["status"] == "prepared"
    assert migration.preview(tmp_path)["status"] == "prepared"
    assert migration.apply(tmp_path)["status"] == "complete"


@pytest.mark.parametrize("changed", ["source", "destination", "archive", "new_source"])
def test_interrupted_retry_refuses_external_changes(tmp_path, monkeypatch, owner, changed):
    source = row(tmp_path)
    original = migration._write
    def interrupt(repo, relative, data):
        if relative.endswith("MIGRATION.md"):
            raise OSError("stop")
        return original(repo, relative, data)
    monkeypatch.setattr(migration, "_write", interrupt)
    with pytest.raises(OSError):
        migration.apply(tmp_path)
    monkeypatch.setattr(migration, "_write", original)
    if changed == "source":
        source.write_text("changed")
    elif changed == "archive":
        (tmp_path / migration.ARCHIVE / "original" / migration.GOALS / source.name).write_text("changed")
    elif changed == "new_source":
        write(tmp_path, migration.GOALS + "/GOAL__new.json", dict(goal_id="GOAL__new", objective="new", tasks=[]))
    else:
        report = migration.preview(tmp_path)
        write(tmp_path, report["destinations"][0], b"user changes")
    with pytest.raises(ValueError):
        migration.apply(tmp_path)
    assert source.exists()


def test_completed_retry_does_not_restore_deleted_destination(tmp_path, owner):
    row(tmp_path)
    result = migration.apply(tmp_path)
    (tmp_path / result["destinations"][0]).unlink()
    with pytest.raises(ValueError, match="destination changed"):
        migration.apply(tmp_path)


@pytest.mark.parametrize("failure_stage", ["archive", "prepare_manifest", "complete_manifest"])
def test_durable_commit_stages_retry_without_loss(tmp_path, monkeypatch, owner, failure_stage):
    source = row(tmp_path)
    original = source.read_bytes()
    original_write = migration._write
    fired = []
    def interrupt(repo, relative, data):
        matches = (failure_stage == "archive" and "/original/" in relative
                   or failure_stage == "prepare_manifest" and relative == migration.MANIFEST
                   and json.loads(data)["status"] == "prepared"
                   or failure_stage == "complete_manifest" and relative == migration.MANIFEST
                   and json.loads(data)["status"] == "complete")
        if matches and not fired:
            fired.append(True)
            raise OSError("interrupted durable stage")
        return original_write(repo, relative, data)
    monkeypatch.setattr(migration, "_write", interrupt)
    with pytest.raises(OSError):
        migration.apply(tmp_path)
    if failure_stage != "complete_manifest":
        assert source.read_bytes() == original
        assert not list(tmp_path.rglob("MIGRATION.md"))
    assert migration.apply(tmp_path)["status"] == "complete"
    archived = tmp_path / migration.ARCHIVE / "original" / migration.GOALS / source.name
    assert archived.read_bytes() == original


def test_unrelated_symlink_not_traversed_but_referenced_child_refused(tmp_path, owner):
    row(tmp_path)
    tasks = tmp_path / "doc/harness/tasks"
    tasks.mkdir(parents=True)
    (tasks / "TASK__unrelated").symlink_to(tmp_path / "outside", target_is_directory=True)
    assert migration.preview(tmp_path)["status"] == "preview"
    row(tmp_path, tasks=[dict(task_id="TASK__unrelated")])
    with pytest.raises(ValueError, match="symlink"):
        migration.apply(tmp_path)


def test_noncanonical_absolute_child_refused(tmp_path, owner):
    row(tmp_path, tasks=[dict(task_id="TASK__a", task_dir="/elsewhere/doc/harness/tasks/TASK__a")])
    with pytest.raises(ValueError, match="noncanonical"):
        migration.preview(tmp_path)


def test_existing_absolute_canonical_child_accepted(tmp_path, owner):
    row(tmp_path, tasks=[dict(task_id="TASK__a", task_dir=str(tmp_path / "doc/harness/tasks/TASK__a"))])
    assert migration.apply(tmp_path)["status"] == "complete"


def test_copied_legacy_binding_cannot_change_owner(tmp_path, owner):
    value = spec()
    state = dict(version=1, nodes=[dict(id="one", title="Observed result", weight=1, parent=None, depends_on=[])],
                 checks={}, run_id="existing-run", started_at=0, last_progress_at=0,
                 batch_goal_id="GOAL__old", batch=value)
    write(tmp_path, "doc/harness/tasks/TASK__different/WBS.json", state)
    with pytest.raises(ValueError, match="identity or intake"):
        migration.apply(tmp_path)
    assert not (tmp_path / migration.ARCHIVE).exists()


def test_unstarted_binding_preserves_existing_control_identity(tmp_path, monkeypatch, owner):
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    value = spec()
    pool.batch = value["batch_id"]
    pool.init(value["requests"])
    row(pool.repo, tasks=[dict(task_id="TASK__integration", batch=value)])
    control = dict(run_id=_lib.new_uuid7(), execution_mode="standard", required_lenses=["review-code", "qa-cli"],
                   close_receipt_fingerprint=None)
    path = write(pool.repo, "doc/harness/tasks/TASK__integration/TASK.json", control)
    original = path.read_bytes()
    migration.apply(pool.repo)
    state = json.loads(path.with_name("WBS.json").read_text())
    assert state["run_id"] == control["run_id"] and path.read_bytes() == original
    assert state["batch"]["task_id"] == "TASK__integration"


def test_active_legacy_writer_refuses_without_retiring_sources(tmp_path, owner):
    import fcntl
    import os
    path = row(tmp_path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(ValueError, match="legacy writer active"):
            migration.apply(tmp_path)
        assert path.exists() and not (tmp_path / migration.MANIFEST).exists()
    finally:
        os.close(fd)


def test_migration_manifest_and_originals_are_hook_protected():
    import prewrite_gate
    for relative in (migration.MANIFEST, migration.ARCHIVE + '/original/doc/harness/goals/GOAL__old.json',
                     migration.ARCHIVE + '/.lock'):
        assert prewrite_gate._is_protected_artifact(relative)
        assert prewrite_gate._protected_denial(relative)[0] == 'legacy-data-migration'
