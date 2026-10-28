"""Explicit migration preserves measured work and opaque historical bytes."""
import json

import pytest

from test_wbs_input_policy import public, policy_plan
from test_wbs import node


def migrate(repo, **args):
    from test_harness_mcp_server import HarnessMcpServerTests
    return HarnessMcpServerTests()._call_in_repo(str(repo), "wbs", dict(action="migrate", **args))


def legacy_control(td, *, closed=False):
    path = td / "TASK.json"
    control = json.loads(path.read_text())
    control.pop("closed")
    control["close_receipt_fingerprint"] = "sha256:" + "b" * 64 if closed else None
    path.write_text(json.dumps(control))
    return control


@pytest.mark.parametrize("closed", [False, True])
def test_public_preview_apply_preserves_run_checks_and_history(tmp_path, closed):
    repo, td, call, opened = public(tmp_path)
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assert not call("check", node_id="A").get("isError")
    history = td / "RECEIPTS.jsonl"
    history.write_bytes(b'corrupt historical receipt\x00\xff\n')
    legacy_control(td, closed=closed)
    prior = (td / "TASK.json").read_bytes()
    checks = (td / "WBS.json").read_bytes()
    assert call("context").get("isError")
    preview = migrate(repo)
    assert not preview.get("isError"), preview
    assert (td / "TASK.json").read_bytes() == prior
    applied = migrate(repo, apply=True)
    assert not applied.get("isError"), applied
    control = json.loads((td / "TASK.json").read_text())
    assert control == dict(run_id=opened["run_id"], execution_mode="standard",
                           required_lenses=["review-code", "qa-cli"], closed=closed)
    assert (td / "WBS.json").read_bytes() == checks
    assert history.read_bytes() == b'corrupt historical receipt\x00\xff\n'
    snapshot = (td / "TASK.json").read_bytes()
    assert not migrate(repo, apply=True).get("isError")
    assert (td / "TASK.json").read_bytes() == snapshot
    if not closed:
        assert call("verify")["structuredContent"]["runtime_verdict"] == "PASS"
        assert not call("close").get("isError")


def test_invalid_legacy_control_refuses_without_converting_other_tasks(tmp_path):
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    before = (td / "TASK.json").read_bytes()
    other = td.with_name("TASK__broken")
    other.mkdir()
    (other / "TASK.json").write_text('{"run_id":"bad","close_receipt_fingerprint":null}')
    result = migrate(repo, apply=True)
    assert result.get("isError"), result
    assert (td / "TASK.json").read_bytes() == before


def historical_control(td):
    td.mkdir(parents=True, exist_ok=True)
    value = dict(task_run_id="40fdc1c5e7511dd322b084d709f768fa",
                 started_at="2026-08-12T03:11:29.968778Z", execution_mode="standard",
                 review_lenses=["review-code", "review-security"], qa_lenses=["qa-cli"],
                 close_receipt_fingerprint=None)
    (td / "TASK.json").write_text(json.dumps(value, indent=3) + "\n")
    return value


@pytest.mark.parametrize("archived", [False, True])
def test_unreferenced_pre_uuid7_history_is_retained_and_reported(tmp_path, archived):
    from _lib import read_task_control
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    historical = (repo / "doc/harness/archive/batch/TASK__historical"
                  if archived else td.with_name("TASK__historical"))
    historical_control(historical)
    path = historical / "TASK.json"
    before = path.read_bytes()
    for apply in (False, True, True):
        result = migrate(repo, apply=apply)
        assert not result.get("isError"), result
        report = result["structuredContent"]["task_control_migration"]
        assert report["retained_historical_controls"] == [dict(
            path=str(path), reason="pre-UUIDv7 historical control retained; unavailable to current lifecycle")]
        assert str(path) not in report["destinations"]
        assert path.read_bytes() == before
        assert read_task_control(str(historical)) == {}


@pytest.mark.parametrize("mutation", ["json", "unknown", "run", "started", "lenses", "fingerprint"])
@pytest.mark.parametrize("archived", [False, True])
def test_malformed_pre_uuid7_history_refuses_before_writes(tmp_path, mutation, archived):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    historical = (repo / "doc/harness/archive/batch/TASK__historical"
                  if archived else td.with_name("TASK__historical"))
    value = historical_control(historical)
    if mutation == "unknown":
        value["extra"] = True
    elif mutation == "run":
        value["task_run_id"] = "invalid"
    elif mutation == "started":
        value["started_at"] = "2026-02-30T03:11:29Z"
    elif mutation == "lenses":
        value["qa_lenses"] = ["qa-unknown"]
    else:
        value["close_receipt_fingerprint"] = True
    (historical / "TASK.json").write_text("{" if mutation == "json" else json.dumps(value))
    before = (td / "TASK.json").read_bytes()
    for apply in (False, True):
        result = migrate(repo, apply=apply)
        assert result.get("isError"), result
        assert str(historical / "TASK.json") in str(result)
        assert (td / "TASK.json").read_bytes() == before
        assert not (repo / goal_migration.CONTROL_MANIFEST).exists()


@pytest.mark.parametrize("binding", ["legacy", "session", "conflict"])
@pytest.mark.parametrize("archived", [False, True])
def test_active_pre_uuid7_history_refuses_before_writes(tmp_path, binding, archived):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    historical = (repo / "doc/harness/archive/batch/TASK__historical"
                  if archived else td.with_name("TASK__historical"))
    historical_control(historical)
    if binding == "legacy":
        (td.parent / ".active").write_text(str(historical))
    else:
        sessions = td.parent / ".active_sessions"
        sessions.mkdir(exist_ok=True)
        value = dict(task_id=historical.name, task_dir=str(historical))
        if binding == "conflict":
            value = dict(conflicts=[value])
        (sessions / "old.json").write_text(json.dumps(value))
    before = (td / "TASK.json").read_bytes()
    for apply in (False, True):
        result = migrate(repo, apply=apply)
        assert result.get("isError"), result
        assert str(historical / "TASK.json") in str(result)
        assert (td / "TASK.json").read_bytes() == before
        assert not (repo / goal_migration.CONTROL_MANIFEST).exists()


@pytest.mark.parametrize("archived", [False, True])
def test_pool_referenced_pre_uuid7_history_refuses_before_writes(tmp_path, monkeypatch, archived):
    import goal_migration
    from test_batch_state import Pool
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    td = pool.repo / ("doc/harness/archive/batch/TASK__a" if archived else "doc/harness/tasks/TASK__a")
    historical_control(td)
    state = pool.state.read_bytes()
    control = (td / "TASK.json").read_bytes()
    for apply in (False, True):
        result = migrate(pool.repo, apply=apply)
        assert result.get("isError"), result
        assert str(td / "TASK.json") in str(result)
        assert (td / "TASK.json").read_bytes() == control
        assert pool.state.read_bytes() == state
        assert not (pool.repo / goal_migration.CONTROL_MANIFEST).exists()


@pytest.mark.parametrize("archived", [False, True])
def test_prepared_migration_rechecks_historical_bindings_before_retry(tmp_path, monkeypatch, archived):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    historical = (repo / "doc/harness/archive/batch/TASK__historical"
                  if archived else td.with_name("TASK__historical"))
    historical_control(historical)
    before = (td / "TASK.json").read_bytes()
    def interrupt(*args):
        raise OSError("interrupted before any task conversion")
    with monkeypatch.context() as patch:
        patch.setattr(goal_migration, "_control_write", interrupt)
        assert migrate(repo, apply=True).get("isError")
    journal = repo / goal_migration.CONTROL_MANIFEST
    journal_before = journal.read_bytes()
    (td.parent / ".active").write_text(str(historical))
    for apply in (False, True):
        result = migrate(repo, apply=apply)
        assert result.get("isError"), result
        assert str(historical / "TASK.json") in str(result)
        assert (td / "TASK.json").read_bytes() == before
        assert journal.read_bytes() == journal_before


def test_prepared_migration_retains_archived_history_on_successful_retry(tmp_path, monkeypatch):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    historical = repo / "doc/harness/archive/batch/TASK__historical"
    historical_control(historical)
    history = historical / "RECEIPTS.jsonl"
    history.write_bytes(b"opaque archived history\x00\xff\n")
    before = (historical / "TASK.json").read_bytes()
    def interrupt(*args):
        raise OSError("interrupted before any task conversion")
    with monkeypatch.context() as patch:
        patch.setattr(goal_migration, "_control_write", interrupt)
        assert migrate(repo, apply=True).get("isError")
    journal = repo / goal_migration.CONTROL_MANIFEST
    assert json.loads(journal.read_text())["status"] == "prepared"
    for apply in (False, True, True):
        result = migrate(repo, apply=apply)
        assert not result.get("isError"), result
        report = result["structuredContent"]["task_control_migration"]
        assert report["retained_historical_controls"][0]["path"] == str(historical / "TASK.json")
        assert (historical / "TASK.json").read_bytes() == before
        assert history.read_bytes() == b"opaque archived history\x00\xff\n"
    assert json.loads(journal.read_text())["status"] == "complete"


@pytest.mark.parametrize("path_kind", ["unregistered-live", "unregistered-archive", "noncanonical"])
def test_retained_report_rejects_unregistered_or_noncanonical_paths(tmp_path, path_kind):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    assert not migrate(repo, apply=True).get("isError")
    journal = repo / goal_migration.CONTROL_MANIFEST
    value = json.loads(journal.read_text())
    if path_kind == "noncanonical":
        path = str(repo / "doc/harness/archive/other/TASK__historical/TASK.json")
    else:
        suffix = "tasks" if path_kind == "unregistered-live" else "archive/batch"
        path = str(tmp_path / "unregistered" / "doc/harness" / suffix / "TASK__historical/TASK.json")
    value["retained_historical_controls"] = [dict(path=path,
        reason="pre-UUIDv7 historical control retained; unavailable to current lifecycle")]
    journal.write_text(json.dumps(value))
    before = journal.read_bytes()
    for apply in (False, True):
        assert migrate(repo, apply=apply).get("isError")
        assert journal.read_bytes() == before


def test_public_migration_rejects_nonboolean_apply_without_changes(tmp_path):
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    before = (td / "TASK.json").read_bytes()
    result = migrate(repo, apply="true")
    assert result.get("isError"), result
    assert (td / "TASK.json").read_bytes() == before


def archived_legacy_pool(tmp_path, monkeypatch):
    """Produce genuine integration/archive proof, then encode its retired format."""
    from test_batch_state import Pool, complete
    import batch_state
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    complete(pool)
    pool.cli("finish", "--slug", "a")
    archive = pool.repo / "doc/harness/archive/batch/TASK__a"
    history = archive / "RECEIPTS.jsonl"
    history.write_bytes(b'opaque historical archive bytes\x00\xff\n')
    legacy_control(archive, closed=True)
    state = json.loads(pool.state.read_text())
    for row in state["requests"]:
        if "closed" in row:
            row["close_fingerprint"] = "sha256:" + "c" * 64 if row.pop("closed") else None
        checkpoint = row.get("checkpoint") or {}
        if "closed" in checkpoint:
            assert checkpoint.pop("closed") is True
            checkpoint["close_fingerprint"] = "sha256:" + "c" * 64
        if checkpoint.get("archive_fingerprint"):
            checkpoint["archive_fingerprint"] = batch_state.fingerprint(str(archive))
    pool.state.write_text(json.dumps(state))
    return pool, archive, history.read_bytes()


def test_archive_task_conversion_updates_existing_pool_hash_without_rewriting_history(tmp_path, monkeypatch):
    import batch_state
    pool, archive, history = archived_legacy_pool(tmp_path, monkeypatch)
    before = json.loads(pool.state.read_text())
    old_hash = before["requests"][0]["checkpoint"]["archive_fingerprint"]
    result = migrate(pool.repo, apply=True)
    assert not result.get("isError"), result
    after = json.loads(pool.state.read_text())
    row = after["requests"][0]
    assert "close_fingerprint" not in row
    assert "close_fingerprint" not in row["checkpoint"]
    assert row["checkpoint"]["closed"] is True
    assert row["checkpoint"]["archive_fingerprint"] != old_hash
    assert row["checkpoint"]["archive_fingerprint"] == batch_state.fingerprint(str(archive))
    assert json.loads((archive / "TASK.json").read_text())["closed"] is True
    assert (archive / "RECEIPTS.jsonl").read_bytes() == history
    pool.cli("status")


def test_preexisting_archive_drift_is_rejected_before_any_conversion(tmp_path, monkeypatch):
    pool, archive, _ = archived_legacy_pool(tmp_path, monkeypatch)
    control = (archive / "TASK.json").read_bytes()
    state = pool.state.read_bytes()
    (archive / "RECEIPTS.jsonl").write_bytes(b'changed after old archive custody hash')
    for apply in (False, True):
        result = migrate(pool.repo, apply=apply)
        assert result.get("isError"), result
        assert "archive" in str(result).lower() and "drift" in str(result).lower()
        assert (archive / "TASK.json").read_bytes() == control
        assert pool.state.read_bytes() == state


def test_migration_retries_forward_after_archive_conversion_before_pool_publication(tmp_path, monkeypatch):
    import goal_migration
    import batch_state
    pool, archive, history = archived_legacy_pool(tmp_path, monkeypatch)
    state_before = pool.state.read_bytes()
    actual = goal_migration._control_write
    interrupted = []
    def interrupt(repo, entry, data, expected):
        if "/runtime/batches/" in entry["path"] and not interrupted:
            interrupted.append(entry["path"])
            raise OSError("interrupted before pool checkpoint publication")
        return actual(repo, entry, data, expected)
    with monkeypatch.context() as patch:
        patch.setattr(goal_migration, "_control_write", interrupt)
        result = migrate(pool.repo, apply=True)
    assert result.get("isError"), result
    assert interrupted
    assert json.loads((archive / "TASK.json").read_text())["closed"] is True
    assert pool.state.read_bytes() == state_before
    journal = pool.repo / goal_migration.CONTROL_MANIFEST
    assert json.loads(journal.read_text())["status"] == "prepared"
    retried = migrate(pool.repo, apply=True)
    assert not retried.get("isError"), retried
    assert json.loads(journal.read_text())["status"] == "complete"
    state = json.loads(pool.state.read_text())
    assert state["requests"][0]["checkpoint"]["archive_fingerprint"] == batch_state.fingerprint(str(archive))
    assert (archive / "RECEIPTS.jsonl").read_bytes() == history
    assert not migrate(pool.repo, apply=True).get("isError")
    pool.cli("status")


def test_prepared_migration_rejects_unrelated_destination_changes(tmp_path, monkeypatch):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    other = td.with_name("TASK__second")
    other.mkdir()
    (other / "TASK.json").write_bytes((td / "TASK.json").read_bytes())
    other_before = (other / "TASK.json").read_bytes()
    actual = goal_migration._control_write
    writes = []
    def interrupt(repo, entry, data, expected):
        if writes:
            raise OSError("interrupted after one task publication")
        actual(repo, entry, data, expected)
        writes.append(entry)
    with monkeypatch.context() as patch:
        patch.setattr(goal_migration, "_control_write", interrupt)
        result = migrate(repo, apply=True)
    assert result.get("isError"), result
    changed = json.loads((td / "TASK.json").read_text())
    changed["required_lenses"].append("qa-api")
    (td / "TASK.json").write_text(json.dumps(changed))
    result = migrate(repo, apply=True)
    assert result.get("isError"), result
    assert "drift" in str(result).lower()
    assert (other / "TASK.json").read_bytes() == other_before


@pytest.mark.parametrize("retry", [False, True])
def test_migration_hashes_each_archive_only_before_and_after_publication(tmp_path, monkeypatch, retry):
    import batch_state
    import goal_migration
    pool, archive, history = archived_legacy_pool(tmp_path, monkeypatch)
    if retry:
        def interrupt(*args):
            raise OSError("interrupted before task publication")
        with monkeypatch.context() as patch:
            patch.setattr(goal_migration, "_control_write", interrupt)
            assert migrate(pool.repo, apply=True).get("isError")
        journal = pool.repo / goal_migration.CONTROL_MANIFEST
        assert json.loads(journal.read_text())["status"] == "prepared"
    actual = batch_state._fingerprint_entries
    walks = []
    def counting(path):
        walks.append(str(path))
        return actual(path)
    monkeypatch.setattr(batch_state, "_fingerprint_entries", counting)
    result = migrate(pool.repo, apply=True)
    assert not result.get("isError"), result
    assert walks == [str(archive), str(archive)]
    assert (archive / "RECEIPTS.jsonl").read_bytes() == history
    assert json.loads((archive / "TASK.json").read_text())["closed"] is True


def test_changed_migration_lock_scope_refuses_before_planning_or_publication(tmp_path, monkeypatch):
    import goal_migration
    repo, td, _, _ = public(tmp_path)
    legacy_control(td)
    before = (td / "TASK.json").read_bytes()
    actual = goal_migration._control_scope
    scopes = []
    def changing(root):
        if scopes:
            new = td.with_name("TASK__arrived")
            new.mkdir()
            (new / "TASK.json").write_bytes(before)
        scopes.append(root)
        return actual(root)
    def forbidden(*args):
        pytest.fail("archive planning started before lock scope was stable")
    monkeypatch.setattr(goal_migration, "_control_scope", changing)
    monkeypatch.setattr(goal_migration, "_control_changes", forbidden)
    result = migrate(repo, apply=True)
    assert result.get("isError"), result
    assert "lock scope changed" in str(result)
    assert (td / "TASK.json").read_bytes() == before
    assert not (repo / goal_migration.CONTROL_MANIFEST).exists()
