"""One final verify must not freeze receipts before expensive WBS work."""
import json
import threading
import time
from pathlib import Path
from unittest import mock

import pytest


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
