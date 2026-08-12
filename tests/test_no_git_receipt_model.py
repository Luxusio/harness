from __future__ import annotations

import importlib.util
import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "no_git_receipt_lib", ROOT / "plugin/scripts/_lib.py"
)
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path, lenses: dict | None = None) -> Path:
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
    task = tmp_path / "doc/harness/tasks/TASK__no-git"
    lib.ensure_task_scaffold(task, "TASK__no-git", repo_root=tmp_path)
    (task / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    if lenses is not None:
        control = lib.read_task_control(task)
        requested = set(lenses.get("required_lenses", [])) & lib.SUPPORTED_LENSES
        requested.add("review-code")
        if not requested & lib.QA_LENSES:
            requested.add("qa-cli")
        control["required_lenses"] = [
            lens for lens in lib.LENS_ORDER if lens in requested
        ]
        lib.write_task_control(task, control)
    return task


def _receipt(
    task: Path,
    lens: str,
    agent_id: str,
    event: str,
    verdict: str = "",
    **extra,
):
    summary = "started"
    if verdict:
        summary = f"VERDICT: {verdict}"
        if lens.startswith("review-"):
            summary += "\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
    return lib.record_subagent_receipt(
        task,
        {
            "agent_id": agent_id,
            "agent_type": lens,
            "lens": lens,
            "event": event,
            "verdict": verdict,
            "summary": summary,
            **extra,
        },
    )


def _pass_review(task: Path, agent_id: str = "review-1") -> None:
    _receipt(task, "review-code", agent_id, "started")
    _receipt(task, "review-code", agent_id, "completed", "PASS")


def test_scaffold_uses_only_exact_task_control_and_does_not_inspect_git(tmp_path):
    task = _task(tmp_path)
    assert not (task / "TASK_BASELINE.json").exists()
    assert set(lib.read_task_control(task)) == lib.TASK_CONTROL_FIELDS
    for legacy in (
        "TASK_STATE.yaml", "TASK_RUN.json", "PLAN.meta.json",
        "TASK_CLOSE_RECEIPT.json", "INSTALL_RECEIPT.json",
        "AUDIT_TRAIL.md", "ENVIRONMENT_SNAPSHOT.md", ".receipts.lock",
    ):
        assert not (task / legacy).exists()


def test_task_control_exact_four_field_schema_fails_closed(tmp_path):
    task = _task(tmp_path)
    valid = lib.read_task_control(task)
    assert set(valid) == lib.TASK_CONTROL_FIELDS

    for mutation in (
        {**valid, "legacy_status": "open"},
        {key: value for key, value in valid.items() if key != "execution_mode"},
        {**valid, "run_id": "a" * 32},
        {**valid, "required_lenses": ["review-security", "qa-cli"]},
        {**valid, "required_lenses": ["review-code"]},
        {**valid, "required_lenses": ["review-code", "qa-cli", "qa-cli"]},
        {**valid, "required_lenses": ["review-code", "qa-unknown"]},
    ):
        (task / "TASK.json").write_text(json.dumps(mutation) + "\n", encoding="utf-8")
        assert lib.read_task_control(task) == {}

    (task / "TASK.json").write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert lib.read_task_control(task) == valid


def test_uuid7_identity_is_canonical_timestamped_and_rotates(tmp_path):
    timestamp_ms = 1_786_424_400_900
    run_id = lib.new_uuid7(timestamp_ms)
    assert run_id == run_id.lower()
    assert run_id[14] == "7"
    assert run_id[19] in "89ab"
    assert lib.uuid7_timestamp_ms(run_id) == timestamp_ms
    assert lib.task_run_started_at({"run_id": run_id}) == "2026-08-11T05:00:00.900Z"

    task = _task(tmp_path)
    first = lib.read_task_control(task)["run_id"]
    rotated, _ = lib.begin_task_run(task)
    assert rotated["run_id"] != first
    assert lib.uuid7_timestamp_ms(rotated["run_id"]) >= lib.uuid7_timestamp_ms(first)


def test_task_control_routes_lenses_with_safe_defaults_and_explicit_security(tmp_path):
    default_task = _task(tmp_path / "default")
    assert lib.required_review_lenses(default_task) == ["review-code"]
    assert lib._required_qa_lenses(default_task) == ["qa-cli"]

    explicit_task = _task(
        tmp_path / "explicit",
        {
            "required_lenses": [
                "review-security", "unknown", "qa-browser", "qa-api", "qa-browser",
            ],
        },
    )
    assert lib.required_review_lenses(explicit_task) == [
        "review-code",
        "review-security",
    ]
    assert lib._required_qa_lenses(explicit_task) == ["qa-api", "qa-browser"]

    malformed_task = _task(tmp_path / "malformed")
    (malformed_task / "TASK.json").write_text("not json", encoding="utf-8")
    assert lib.required_review_lenses(malformed_task) == []
    assert lib._required_qa_lenses(malformed_task) == []


def test_receipts_require_matching_start(tmp_path):
    task = _task(tmp_path)
    _receipt(task, "review-code", "review-1", "started")
    completed = _receipt(
        task,
        "review-code",
        "review-1",
        "completed",
        "PASS",
    )
    assert set(completed) == lib.RECEIPT_FIELDS
    assert lib.receipt_review_verdict(task) == "PASS"

    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(
        task,
        "qa-cli",
        "qa-1",
        "completed",
        "PASS",
    )
    assert lib.receipt_runtime_verdict(task) == "PASS"

    no_start = _task(tmp_path / "no-start")
    _receipt(no_start, "review-code", "review-orphan", "completed", "PASS")
    assert lib.receipt_review_verdict(no_start) == "PENDING"


def test_qa_start_must_match_completion_and_follow_review(tmp_path):
    early = _task(tmp_path / "early")
    _receipt(early, "qa-cli", "qa-early", "started")
    _pass_review(early)
    _receipt(early, "qa-cli", "qa-early", "completed", "PASS")
    assert lib.receipt_runtime_verdict(early) == "PENDING"

    mismatch = _task(tmp_path / "mismatch")
    _pass_review(mismatch)
    _receipt(mismatch, "qa-cli", "qa-start", "started")
    _receipt(mismatch, "qa-cli", "qa-finish", "completed", "PASS")
    assert lib.receipt_runtime_verdict(mismatch) == "PENDING"

    replay = _task(tmp_path / "runtime-replay")
    _receipt(
        replay, "review-code", "review-reused", "started",
        runtime_event_id="event-a", runtime_session_id="session-1",
        runtime_thread_id="thread-a",
    )
    _receipt(
        replay, "review-code", "review-reused", "completed", "PASS",
        runtime_event_id="event-b", runtime_session_id="session-1",
        runtime_thread_id="thread-a",
    )
    assert lib.receipt_review_verdict(replay) == "PENDING"

    type_mismatch = _task(tmp_path / "type-mismatch")
    _receipt(type_mismatch, "review-code", "reviewer", "started", agent_type="type-a")
    _receipt(
        type_mismatch, "review-code", "reviewer", "completed", "PASS",
        agent_type="type-b",
    )
    assert lib.receipt_review_verdict(type_mismatch) == "PENDING"


def test_duplicate_terminals_and_contradictory_summaries_fail_closed(tmp_path):
    duplicate_review = _task(tmp_path / "duplicate-review")
    _receipt(duplicate_review, "review-code", "reviewer", "started")
    _receipt(duplicate_review, "review-code", "reviewer", "completed", "FAIL")
    _receipt(duplicate_review, "review-code", "reviewer", "completed", "PASS")
    assert lib.receipt_review_verdict(duplicate_review) == "PENDING"

    duplicate_qa = _task(tmp_path / "duplicate-qa")
    _pass_review(duplicate_qa)
    _receipt(duplicate_qa, "qa-cli", "qa", "started")
    _receipt(duplicate_qa, "qa-cli", "qa", "completed", "FAIL")
    _receipt(duplicate_qa, "qa-cli", "qa", "completed", "PASS")
    assert lib.receipt_runtime_verdict(duplicate_qa) == "PENDING"

    contradictory = _task(tmp_path / "contradictory")
    started = lib.record_subagent_receipt(contradictory, {
        "event": "started", "agent_id": "qa", "agent_type": "qa_cli",
        "lens": "qa-cli", "verdict": "PASS", "summary": "VERDICT: PASS",
    })
    completed = lib.record_subagent_receipt(contradictory, {
        "event": "completed", "agent_id": "qa", "agent_type": "qa_cli",
        "lens": "qa-cli", "verdict": "PASS", "summary": "VERDICT: FAIL",
    })
    missing = lib.record_subagent_receipt(contradictory, {
        "event": "completed", "agent_id": "qa-2", "agent_type": "qa_cli",
        "lens": "qa-cli", "verdict": "PASS", "summary": "done",
    })
    assert started["verdict"] == ""
    assert completed["verdict"] == "PENDING"
    assert missing["verdict"] == "PENDING"


def test_runtime_never_marks_pass_receipts_stale_after_source_edit(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(task, "qa-cli", "qa-1", "completed", "PASS")
    source = tmp_path / "nested/source.py"
    source.parent.mkdir()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert lib.receipt_runtime_verdict(task) == "PASS"


def test_close_is_the_current_receipt_fingerprint_in_task_control(tmp_path):
    task = _task(tmp_path)
    fingerprint = lib.receipt_stream_fingerprint(task)
    control = lib.publish_task_close(
        task, lib.read_task_control(task), receipt_fingerprint=fingerprint,
    )
    assert control["close_receipt_fingerprint"] == fingerprint
    assert lib.task_control_status(task, control) == "closed"
    assert not (task / "TASK_CLOSE_RECEIPT.json").exists()

    (task / lib.RECEIPTS_NAME).write_text("{}\n", encoding="utf-8")
    assert lib.task_control_status(task, control) == "invalid"


def test_ignored_nested_git_resolves_ancestor_harness_root(tmp_path):
    root = tmp_path / "workspace"
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\nsource_git_roots: []\n", encoding="utf-8")
    nested = root / "cleo-v4-web/src"
    nested.mkdir(parents=True)
    (root / "cleo-v4-web/.git").mkdir()
    task = _task(root)
    lib.write_active_marker(str(root), str(task))

    assert lib.harness_root_resolution(nested) == (str(root.resolve()), "")
    assert lib.find_harness_root(nested) == str(root.resolve())

    # The repository-wide legacy marker alone must not authorize a nested Git
    # boundary; only the session-specific binding may do that.
    Path(lib._session_active_path(str(root))).unlink()
    assert lib.harness_root_resolution(nested) == ("", "")


def test_terminal_receipt_reset_rejects_unsafe_stream_leaves(tmp_path):
    task = _task(tmp_path)
    outside = tmp_path / "outside.jsonl"
    outside.write_text("preserve\n", encoding="utf-8")
    stream = task / lib.RECEIPTS_NAME
    stream.symlink_to(outside)
    try:
        lib.reset_receipt_streams_for_new_run(task)
    except RuntimeError as exc:
        assert "receipt storage integrity" in str(exc)
    else:
        raise AssertionError("symlinked receipt stream must be rejected")
    assert outside.read_text(encoding="utf-8") == "preserve\n"


def test_receipt_stream_and_task_directory_reject_group_world_writable_modes(tmp_path):
    writable_stream = _task(tmp_path / "stream")
    _pass_review(writable_stream)
    stream = writable_stream / lib.RECEIPTS_NAME
    stream.chmod(0o666)
    try:
        lib.receipt_snapshot(writable_stream)
    except RuntimeError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("writable receipt stream must fail closed")

    writable_dir = _task(tmp_path / "directory")
    writable_dir.chmod(0o777)
    try:
        lib.receipt_snapshot(writable_dir)
    except RuntimeError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("writable task directory must fail closed")
    assert not (writable_dir / ".receipts.lock").exists()


def test_task_directory_transaction_is_nested_serialized_and_replacement_safe(tmp_path):
    task = _task(tmp_path / "serialized")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_worker():
        with lib.receipt_stream_transaction(task):
            with lib.receipt_stream_transaction(task):
                first_entered.set()
                assert release_first.wait(5)

    def second_worker():
        assert first_entered.wait(5)
        with lib.receipt_stream_transaction(task):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    second.start()
    assert first_entered.wait(5)
    assert not second_entered.wait(0.1)
    release_first.set()
    first.join(5)
    second.join(5)
    assert second_entered.is_set()
    assert not (task / ".receipts.lock").exists()

    original = _task(tmp_path / "replacement")
    displaced = original.with_name("TASK__displaced")
    try:
        with lib.receipt_stream_transaction(original):
            original.rename(displaced)
            original.mkdir()
    except RuntimeError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("task-directory replacement must fail closed")


def test_rotated_task_run_rejects_prior_run_receipts_without_source_state(tmp_path):
    task = _task(tmp_path)
    original_run = lib.read_task_control(task)["run_id"]
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(task, "qa-cli", "qa-1", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PASS"

    new_run, _ = lib.begin_task_run(task)
    assert new_run["run_id"] != original_run
    assert lib.receipt_runtime_verdict(task) == "PENDING"

    # A late completion explicitly bound to the old run cannot revive it.
    try:
        _receipt(
            task, "qa-cli", "qa-late", "completed", "PASS",
            task_run_id=original_run,
        )
    except RuntimeError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("old-run completion must be rejected")
    assert lib.receipt_runtime_verdict(task) == "PENDING"


def test_missing_or_mismatched_task_run_fails_receipts_closed(tmp_path):
    task = _task(tmp_path)
    run_id = lib.read_task_control(task)["run_id"]
    try:
        _receipt(
            task, "review-code", "review-wrong", "started",
            task_run_id=lib.new_uuid7(),
        )
    except RuntimeError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched task run must be rejected")

    _pass_review(task)
    (task / lib.TASK_CONTROL_NAME).write_text("{}\n", encoding="utf-8")
    assert lib.receipt_review_verdict(task) == "PENDING"
    try:
        _receipt(
            task, "qa-cli", "qa-missing-run", "started",
            task_run_id=run_id,
        )
    except RuntimeError as exc:
        assert "valid TASK.json" in str(exc)
    else:
        raise AssertionError("missing task run must reject receipt append")


def test_receipt_reset_removes_only_unified_stream(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    unified = task / lib.RECEIPTS_NAME
    unified_fingerprint = lib.receipt_stream_fingerprint(task)
    legacy_review = task / "REVIEW_RECEIPTS.jsonl"
    legacy_qa = task / "SUBAGENT_RECEIPTS.jsonl"
    legacy_review.write_text('{"kind":"review"}\n', encoding="utf-8")
    legacy_qa.write_text('{"kind":"subagent"}\n', encoding="utf-8")
    assert lib.receipt_stream_fingerprint(task) == unified_fingerprint

    snapshot = lib.reset_receipt_streams_for_new_run(task)

    assert not unified.exists()
    assert legacy_review.read_text(encoding="utf-8") == '{"kind":"review"}\n'
    assert legacy_qa.read_text(encoding="utf-8") == '{"kind":"subagent"}\n'
    assert set(snapshot) == {str(unified)}


def test_snapshot_fingerprint_stays_bound_to_the_same_bytes(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    snapshot = lib.receipt_snapshot(task)
    original = lib.receipt_stream_fingerprint(task, snapshot)

    _receipt(task, "qa-cli", "qa-1", "started")

    assert lib.receipt_stream_fingerprint(task, snapshot) == original
    assert lib.receipt_stream_fingerprint(task) != original


def test_snapshot_rejects_same_size_mutation_and_path_replacement(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    stream = task / lib.RECEIPTS_NAME

    with lib.receipt_stream_transaction(task):
        real_fstat = lib.os.fstat
        stream_inode = stream.stat().st_ino
        stream_calls = 0

        def changed_fstat(fd):
            nonlocal stream_calls
            info = real_fstat(fd)
            if info.st_ino == stream_inode:
                stream_calls += 1
            if info.st_ino == stream_inode and stream_calls == 2:
                values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                values["st_mtime_ns"] = info.st_mtime_ns + 1
                return SimpleNamespace(**values)
            return info

        with mock.patch.object(lib.os, "fstat", side_effect=changed_fstat):
            try:
                lib._receipt_snapshot_unlocked(task)
            except RuntimeError as exc:
                assert "integrity" in str(exc)
            else:
                raise AssertionError("same-size mutation must fail closed")

    with lib.receipt_stream_transaction(task):
        real_stat = lib.os.stat

        def replaced_stat(path, *args, **kwargs):
            info = real_stat(path, *args, **kwargs)
            if path == lib.RECEIPTS_NAME and kwargs.get("dir_fd") is not None:
                values = {name: getattr(info, name) for name in dir(info) if name.startswith("st_")}
                values["st_ino"] = info.st_ino + 1
                return SimpleNamespace(**values)
            return info

        with mock.patch.object(lib.os, "stat", side_effect=replaced_stat):
            try:
                lib._receipt_snapshot_unlocked(task)
            except RuntimeError as exc:
                assert "integrity" in str(exc)
            else:
                raise AssertionError("path replacement must fail closed")

def test_symlinked_manifest_remains_untrusted(tmp_path):
    root = tmp_path / "workspace"
    harness_dir = root / "doc/harness"
    harness_dir.mkdir(parents=True)
    external = tmp_path / "manifest.yaml"
    external.write_text("version: 5\n", encoding="utf-8")
    (harness_dir / "manifest.yaml").symlink_to(external)
    child = root / "child"
    child.mkdir()

    resolved, error = lib.harness_root_resolution(child)
    assert resolved == str(root.resolve())
    assert "non-symlink" in error
    assert lib.find_harness_root(child) == ""


def test_review_and_qa_share_one_receipt_stream(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(task, "qa-cli", "qa-1", "completed", "PASS")

    assert (task / lib.RECEIPTS_NAME).is_file()
    assert lib.receipt_review_verdict(task) == "PASS"
    assert lib.receipt_runtime_verdict(task) == "PASS"

    entries = lib.receipt_snapshot(task).entries
    assert entries
    assert all(set(entry) == lib.RECEIPT_FIELDS for entry in entries)
    assert {entry["event"] for entry in entries} == {"started", "completed"}


def test_old_unified_schema_requires_a_fresh_task_run(tmp_path):
    task = _task(tmp_path)
    (task / lib.RECEIPTS_NAME).write_text(
        json.dumps({"kind": "review", "status": "completed"}) + "\n",
        encoding="utf-8",
    )

    try:
        lib.receipt_snapshot(task)
    except RuntimeError as exc:
        assert "start a fresh task run" in str(exc)
    else:
        raise AssertionError("old receipt schema must fail closed")


def test_exact_schema_rejects_non_string_values(tmp_path):
    task = _task(tmp_path)
    entry = {field: "" for field in lib.RECEIPT_FIELDS}
    entry.update(
        event="started",
        task_run_id=lib.read_task_control(task)["run_id"],
        summary=[],
    )
    (task / lib.RECEIPTS_NAME).write_text(
        json.dumps(entry) + "\n", encoding="utf-8"
    )

    try:
        lib.receipt_snapshot(task)
    except RuntimeError as exc:
        assert "start a fresh task run" in str(exc)
    else:
        raise AssertionError("mutable receipt values must fail closed")
