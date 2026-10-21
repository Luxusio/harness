"""One final verify must not freeze receipts before expensive WBS work."""
import json
import threading
import time
from pathlib import Path
from unittest import mock

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


@pytest.fixture
def pending_task(tmp_path, monkeypatch):
    from test_harness_mcp_server import HarnessMcpServerTests, harness_server as server, harness_lib as lib
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    (root / "doc/harness").mkdir(parents=True)
    (root / "doc/harness/manifest.yaml").write_text("version: 7\ntype: library\n")
    fixture = HarnessMcpServerTests()
    task = fixture._make_task(str(root), "TASK__settlement")
    fixture._write_subagent_receipt(task)
    path = Path(task, "RECEIPTS.jsonl")
    rows = path.read_text().splitlines(True)
    completion = json.loads(rows.pop())
    path.write_text("".join(rows))
    monkeypatch.setattr(server, "find_repo_root", lambda *a: str(root))
    monkeypatch.setattr(server, "RECEIPT_SETTLEMENT_SECONDS", 0.15)
    def append(entry=completion):
        with server.receipt_stream_transaction(task):
            with path.open("a") as handle:
                handle.write(json.dumps(entry) + "\n")
    return server, lib, task, path, append, completion


def verify(server):
    return server.call_tool("wbs", {"action": "verify", "wbs_id": "settlement"})


def payload(result):
    return result["structuredContent"]


def test_completion_during_measurement_uses_final_coherent_snapshot(pending_task, monkeypatch):
    server, _, task, _, append, _ = pending_task
    original = server.wbs.task_progress
    measured = []
    def measure(*args, **kwargs):
        measured.append(kwargs.get("measure", True))
        append()
        return original(*args, **kwargs)
    monkeypatch.setattr(server.wbs, "task_progress", measure)
    result = payload(verify(server))
    assert result["runtime_verdict"] == result["review_verdict"] == "PASS"
    assert not result["missing_for_close"]
    assert "Completed QA" in result["next_action"]
    assert measured == [True]
    assert result["receipt_recording"]["counts"] == {"completion_recorded": 2}


def test_delayed_completion_can_append_while_single_verify_waits(pending_task):
    server, _, _, _, append, _ = pending_task
    finished = threading.Event()
    def writer():
        time.sleep(0.05)
        append()
        finished.set()
    worker = threading.Thread(target=writer)
    worker.start()
    try:
        result = payload(verify(server))
        assert result["runtime_verdict"] == "PASS"
        assert not result["missing_for_close"]
        assert finished.is_set()  # A writer-blocking wait would deadlock here.
    finally:
        worker.join(timeout=2)
    assert not worker.is_alive()


def test_no_completion_is_bounded_and_never_writes_receipts(pending_task, monkeypatch):
    server, _, _, path, _, _ = pending_task
    before = path.read_bytes()
    clock = [0.0]
    waits = []
    def wait(seconds):
        waits.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(server.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(server.time, "sleep", wait)
    result = payload(verify(server))
    assert result["runtime_verdict"] == "PENDING"
    assert "completed QA verdict: qa-cli" in result["missing_for_close"]
    assert waits and sum(waits) == pytest.approx(server.RECEIPT_SETTLEMENT_SECONDS)
    assert path.read_bytes() == before
    assert result["receipt_recording"]["counts"] == {
        "completion_recorded": 1, "awaiting_completion_record": 1,
    }


@pytest.mark.parametrize("verdict", ["FAIL", "BLOCKED_ENV"])
def test_concurrent_negative_is_never_hidden_by_pending(pending_task, monkeypatch, verdict):
    server, lib, _, _, append, completion = pending_task
    bound, summary = lib.normalize_receipt_completion("qa-cli", "VERDICT: " + verdict, verdict)
    completion.update(verdict=bound, summary=summary)
    original = server.wbs.task_progress
    def measure(*args, **kwargs):
        append(completion)
        return original(*args, **kwargs)
    monkeypatch.setattr(server.wbs, "task_progress", measure)
    assert payload(verify(server))["runtime_verdict"] == verdict


def test_wrong_run_completion_never_passes(pending_task):
    server, lib, _, _, append, completion = pending_task
    completion["task_run_id"] = lib.new_uuid7()
    append(completion)
    assert payload(verify(server))["runtime_verdict"] == "PENDING"


def test_qa_started_before_review_stays_unordered(pending_task):
    server, _, _, path, append, _ = pending_task
    rows = path.read_text().splitlines(True)
    path.write_text(rows[2] + rows[0] + rows[1])
    append()
    assert payload(verify(server))["runtime_verdict"] == "PENDING"


def test_corrupt_receipt_is_integrity_error(pending_task):
    server, _, _, path, _, _ = pending_task
    with path.open("a") as handle:
        handle.write("not-json\n")
    result = verify(server)
    assert result.get("isError")
    assert "integrity" in str(result)


def test_task_generation_change_during_measurement_refuses_result(pending_task, monkeypatch):
    server, lib, task, _, append, _ = pending_task
    original = server.wbs.task_progress
    def measure(*args, **kwargs):
        progress = original(*args, **kwargs)
        control = server.read_task_control(task)
        control["run_id"] = lib.new_uuid7()
        Path(task, "TASK.json").write_text(json.dumps(control))
        return progress
    monkeypatch.setattr(server.wbs, "task_progress", measure)
    result = verify(server)
    assert result.get("isError") and "authority changed" in str(result)


def test_wbs_change_during_measurement_refuses_old_progress(pending_task, monkeypatch):
    server, _, _, _, append, _ = pending_task
    changed = []
    monkeypatch.setattr(server.wbs, "read_state", lambda *_: {"scope_revision": 2} if changed else None)
    def measure(*args, **kwargs):
        append()
        changed.append(True)
        return {"status": "unbaselined"}
    monkeypatch.setattr(server.wbs, "task_progress", measure)
    result = verify(server)
    assert result.get("isError") and "WBS changed" in str(result)


def test_no_started_required_lens_does_not_wait(pending_task):
    server, _, _, path, _, _ = pending_task
    path.write_text("")
    with mock.patch.object(server.time, "sleep", side_effect=AssertionError("unconditional wait")):
        assert payload(verify(server))["runtime_verdict"] == "PENDING"


def test_context_preserves_distinct_executions_and_old_run_history(pending_task, monkeypatch):
    server, lib, task, path, append, completion = pending_task
    # One completed QA and another independent invocation still awaiting its
    # completion must stay visible, even though both have the same lens.
    append()
    second = dict(completion, event="started", verdict="", summary="",
                  runtime_id="claude:session:second-qa", agent_id="second-qa")
    append(second)
    append(dict(second, task_run_id=lib.new_uuid7(), runtime_id="claude:session:old-qa"))
    before = path.read_bytes()
    with mock.patch.object(server.wbs, "fingerprint", side_effect=AssertionError("query must not hash")):
        result = payload(server.call_tool("wbs", {"action": "context", "wbs_id": "settlement"}))
    report = result["task_context"]["receipt_recording"]
    assert report["total_executions"] == 3
    assert report["counts"] == {"completion_recorded": 2, "awaiting_completion_record": 1}
    assert report["executions"][0]["runtime_id"] == second["runtime_id"]
    assert report["executions"][0]["verdict"] is None
    assert report["snapshot_fingerprint"] == lib.receipt_snapshot(task).fingerprint
    assert path.read_bytes() == before


def test_recorded_pass_then_invalidating_terminal_is_visible(pending_task):
    server, lib, task, _, append, completion = pending_task
    append()
    pending_verdict, summary = lib.normalize_receipt_completion("qa-cli", "unreadable report", "PENDING")
    append(dict(completion, verdict=pending_verdict, summary=summary))
    result = payload(server.call_tool("wbs", {"action": "context", "wbs_id": "settlement"}))
    report = result["task_context"]["receipt_recording"]
    assert report["counts"]["conflicting_records"] == 1
    row = report["executions"][0]
    assert row["completed_records"] == 2 and row["verdict"] is None
    assert "summary" not in row
    assert result["task_context"]["runtime_verdict"] != "PASS"


def test_recording_projection_bounds_history_without_hiding_counts(pending_task):
    _, lib, task, _, append, completion = pending_task
    for index in range(40):
        append(dict(completion, event="started", verdict="", summary="",
                    runtime_id=f"claude:session:qa-{index}", agent_id=f"qa-{index}"))
    before = Path(task, "RECEIPTS.jsonl").read_bytes()
    snapshot = lib.receipt_snapshot(task)
    report = lib.receipt_recording_status(snapshot, lib.read_task_control(task)["run_id"])
    assert report["total_executions"] == 42
    assert report["counts"] == {"completion_recorded": 1, "awaiting_completion_record": 41}
    assert report["truncated"] and len(report["executions"]) == 32
    assert report["executions"][0]["agent_id"] == "qa-39"
    assert Path(task, "RECEIPTS.jsonl").read_bytes() == before


def test_recording_projection_distinguishes_unbound_and_missing_start(pending_task):
    server, lib, task, path, append, completion = pending_task
    verdict, summary = lib.normalize_receipt_completion("qa-cli", "not a verdict", "PENDING")
    append(dict(completion, verdict=verdict, summary=summary))
    report = lib.receipt_recording_status(lib.receipt_snapshot(task), lib.read_task_control(task)["run_id"])
    assert report["counts"]["completion_unbound"] == 1
    # A stream with only a completion does not establish an authenticated pair.
    rows = path.read_text().splitlines(True)
    path.write_text("".join(row for row in rows if json.loads(row)["event"] != "started"))
    result = payload(server.call_tool("wbs", {"action": "context", "wbs_id": "settlement"}))
    assert result["task_context"]["receipt_recording"]["counts"] == {"completion_without_start": 2}
    assert result["task_context"]["runtime_verdict"] != "PASS"


def test_duplicate_started_records_are_conflicting_without_rewriting(pending_task):
    _, lib, task, path, append, completion = pending_task
    append(dict(completion, event="started", verdict="", summary=""))
    before = path.read_bytes()
    report = lib.receipt_recording_status(lib.receipt_snapshot(task), lib.read_task_control(task)["run_id"])
    row = report["executions"][0]
    assert row["recording_status"] == "conflicting_records"
    assert row["started_records"] == 2 and row["verdict"] is None
    assert path.read_bytes() == before


def test_timeout_error_explains_action_specific_support(pending_task):
    server, _, _, _, _, _ = pending_task
    result = server.call_tool("wbs", {"action": "verify", "wbs_id": "settlement", "timeout": 120})
    assert result.get("isError")
    message = str(result)
    assert "action verify" in message and "allowed fields:" in message
    assert "check (1..300 seconds)" in message
    schema = server.TOOLS["wbs"]["inputSchema"]["properties"]["timeout"]
    assert schema["maximum"] == 300 and "check only" in schema["description"]
    with mock.patch.object(server.wbs, "verify", return_value={"passed": True}) as command:
        accepted = server.call_tool("wbs", {"action": "check", "wbs_id": "settlement", "node_id": "A", "timeout": 120})
        assert not accepted.get("isError"), accepted
        assert command.call_args.kwargs["timeout"] == 120
        rejected = server.call_tool("wbs", {"action": "check", "wbs_id": "settlement", "node_id": "A", "timeout": 301})
        assert rejected.get("isError") and "1..300" in str(rejected)
        assert command.call_count == 1
