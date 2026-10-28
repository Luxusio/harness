"""Completion is based on actual current WBS checks, without collection events."""
import json

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")

from test_wbs import node, plan
from test_wbs_input_policy import public
from test_harness_mcp_server import harness_lib, harness_server


def test_public_measured_lifecycle_closes_without_receipts_or_events(tmp_path):
    repo, td, call, opened = public(tmp_path)
    assert not (td / "RECEIPTS.jsonl").exists()
    assert call("verify")["structuredContent"]["runtime_verdict"] == "PENDING"
    assert call("close").get("isError")
    checked = call("check", node_id="A")
    assert not checked.get("isError"), checked
    verified = call("verify")["structuredContent"]
    assert verified["runtime_verdict"] == "PASS", verified
    assert verified["verification_basis"] == "current_wbs_checks"
    for key in ("watcher_status", "review_verdict", "receipt_recording", "report_path"):
        assert key not in verified
    closed = call("close")
    assert not closed.get("isError"), closed
    control = json.loads((td / "TASK.json").read_text())
    assert control == dict(run_id=opened["run_id"], execution_mode="standard",
                           required_lenses=["review-code", "qa-cli"], closed=True)
    assert harness_lib.task_control_status(str(td), control) == "closed"
    assert not (td / "RECEIPTS.jsonl").exists()
    assert call("open").get("isError")


@pytest.mark.parametrize("history", [b'not JSON\x00\xff\n', b'{"event":"completed","verdict":"FAIL"}\n'])
def test_historical_receipt_bytes_do_not_authorize_or_block_active_work(tmp_path, history):
    _, td, call, opened = public(tmp_path)
    path = td / "RECEIPTS.jsonl"
    path.write_bytes(history)
    assert call("verify")["structuredContent"]["runtime_verdict"] == "PENDING"
    resumed = call("open")
    assert not resumed.get("isError"), resumed
    assert resumed["structuredContent"]["run_id"] == opened["run_id"]
    assert not call("check", node_id="A").get("isError")
    assert call("verify")["structuredContent"]["runtime_verdict"] == "PASS"
    assert not call("close").get("isError")
    assert path.read_bytes() == history


@pytest.mark.parametrize("condition", ["missing", "failing", "stale"])
def test_missing_failing_and_stale_actual_checks_block_close(tmp_path, condition):
    repo, td, call, _ = public(tmp_path)
    if condition == "stale":
        assert not call("check", node_id="A").get("isError")
        (repo / "src.txt").write_text("changed after check")
    elif condition == "failing":
        (repo / "src.txt").write_text("assertion fails")
        call("check", node_id="A")
    result = call("verify")["structuredContent"]
    assert result["runtime_verdict"] != "PASS", result
    assert call("close").get("isError")
    assert json.loads((td / "TASK.json").read_text())["closed"] is False


def test_blocked_task_keeps_check_and_run_but_cannot_close_until_resumed(tmp_path):
    _, td, call, opened = public(tmp_path)
    assert not call("check", node_id="A").get("isError")
    checks = json.loads((td / "WBS.json").read_text())["checks"]
    blocked = call("block", blocked_reason="required external service unavailable",
                   unblock_condition="service responds to verification request")
    assert not blocked.get("isError"), blocked
    assert harness_lib.task_control_status(str(td)) == "blocked"
    assert call("close").get("isError")
    assert call("open")["structuredContent"]["run_id"] == opened["run_id"]
    assert json.loads((td / "WBS.json").read_text())["checks"] == checks
    assert not call("close").get("isError")


@pytest.mark.parametrize("failure", ["publish_task_close", "clear_active_marker"])
def test_close_restores_control_and_focus_when_publication_fails(tmp_path, monkeypatch, failure):
    repo, td, call, _ = public(tmp_path)
    assert not call("check", node_id="A").get("isError")
    before = (td / "TASK.json").read_bytes()
    marker = repo / "doc/harness/tasks/.active"
    marker_before = marker.read_bytes() if marker.exists() else None
    def unavailable(*args, **kwargs):
        raise OSError("publication unavailable")
    monkeypatch.setattr(harness_server, failure, unavailable)
    result = call("close")
    assert result.get("isError"), result
    assert (td / "TASK.json").read_bytes() == before
    if marker_before is not None:
        assert marker.read_bytes() == marker_before


@pytest.mark.parametrize("closed", [None, 0, 1, "false", [], {}])
def test_new_control_requires_a_real_closed_boolean(tmp_path, closed):
    _, td, _, _ = public(tmp_path)
    control = json.loads((td / "TASK.json").read_text())
    control["closed"] = closed
    (td / "TASK.json").write_text(json.dumps(control))
    assert not harness_lib.read_task_control(str(td))


def test_closed_control_and_blocker_conflict_is_invalid(tmp_path):
    _, td, call, _ = public(tmp_path)
    assert not call("check", node_id="A").get("isError")
    assert not call("close").get("isError")
    (td / "BLOCKED.md").write_text("# BLOCKED\n")
    assert harness_lib.task_control_status(str(td)) == "invalid"
