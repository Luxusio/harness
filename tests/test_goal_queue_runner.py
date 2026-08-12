import json
import hashlib
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "goal_queue_runner.py"


def run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state", str(tmp_path / "goal-queue.json"), *args],
        text=True,
        capture_output=True,
        timeout=20,
    )


def load(tmp_path):
    return json.loads((tmp_path / "goal-queue.json").read_text(encoding="utf-8"))


def events(tmp_path):
    path = tmp_path / "goal-queue-events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def write_task_state(tmp_path, task_id, status="closed", verdict="PASS"):
    task_dir = tmp_path / "doc" / "harness" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    run_id = "0198c349-5800-7000-8000-000000000001"
    rows = []
    for lens, agent in (("review-code", "review"), ("qa-cli", "qa")):
        for event in ("started", "completed"):
            summary = "" if event == "started" else f"VERDICT: {verdict}"
            if lens == "review-code" and event == "completed":
                summary += "\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
            if event == "completed":
                summary += "\nDETAIL_SHA256:" + "0" * 64
            rows.append({
                "ts": "2026-08-12T00:00:00Z",
                "event": event,
                "source": "claude_hook",
                "task_run_id": run_id,
                "runtime_id": f"claude:test-session:{agent}",
                "agent_id": agent,
                "agent_type": lens,
                "lens": lens,
                "verdict": "" if event == "started" else verdict,
                "summary": summary,
            })
    receipt_bytes = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
    (task_dir / "RECEIPTS.jsonl").write_text(receipt_bytes, encoding="utf-8")
    digest = hashlib.sha256()
    digest.update(b"RECEIPTS.jsonl\0")
    digest.update(receipt_bytes.encode())
    digest.update(b"\0")
    fingerprint = "sha256:" + digest.hexdigest()
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": run_id,
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": fingerprint if status == "closed" else None,
    }) + "\n", encoding="utf-8")


def test_init_status_and_next_prompt(tmp_path):
    result = run(
        tmp_path,
        "init",
        "--product",
        "ops dashboard",
        "--stack",
        "Next.js + SQLite",
        "--slice",
        "auth:login and roles",
        "--slice",
        "dashboard:core metrics",
    )
    assert result.returncode == 0, result.stderr

    state = load(tmp_path)
    assert state["status"] == "active"
    assert [item["id"] for item in state["slices"]] == ["auth", "dashboard"]
    assert state["current_iteration"] == 1
    assert state["backlog"][0]["user_value"].startswith("User can experience")
    assert state["backlog"][0]["hypothesis"]
    assert state["backlog"][0]["acceptance"]
    assert state["backlog"][0]["priority"] > state["backlog"][1]["priority"]

    status = run(tmp_path, "status")
    assert status.returncode == 0
    assert "pending=2" in status.stdout
    assert "next: auth - login and roles" in status.stdout

    nxt = run(tmp_path, "next")
    assert nxt.returncode == 0
    assert "/goal child task auth" in nxt.stdout


def test_run_once_marks_slice_passed_and_advances(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")

    result = run(tmp_path, "run-once", "--command-template", f"{sys.executable} -c \"print('PASS')\"")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "result: contacts passed rc=0" in result.stdout

    state = load(tmp_path)
    assert state["status"] == "done"
    assert state["slices"][0]["status"] == "passed"
    assert state["slices"][0]["attempts"] == 1
    assert (tmp_path / "runtime" / "goal-queue-heartbeat.json").exists()
    assert any(event["type"] == "slice_passed" for event in events(tmp_path))


def test_require_harness_close_blocks_false_pass_until_task_closed(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")
    command = f"{sys.executable} -c \"print('command pass')\""

    first = run(tmp_path, "run-once", "--require-harness-close", "--command-template", command)
    assert first.returncode == 1
    state = load(tmp_path)
    assert state["slices"][0]["status"] == "failed"
    assert state["slices"][0]["failure_class"] == "harness_close_missing"
    assert state["slices"][0]["retryable"] is True
    assert "HARNESS_CLOSE_REQUIRED" in state["slices"][0]["last_result"]

    write_task_state(tmp_path, "TASK__goal-queue-contacts")
    second = run(tmp_path, "run-once", "--require-harness-close", "--command-template", command)
    assert second.returncode == 0, second.stdout + second.stderr
    state = load(tmp_path)
    assert state["status"] == "done"
    assert state["slices"][0]["status"] == "passed"


def test_require_harness_close_resolves_default_doc_harness_state_shape(tmp_path):
    state_path = tmp_path / "doc" / "harness" / "goal-queue.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--state",
            str(state_path),
            "init",
            "--product",
            "crm",
            "--stack",
            "Django",
            "--slice",
            "contacts:manage contacts",
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0
    write_task_state(tmp_path, "TASK__goal-queue-contacts")

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--state",
            str(state_path),
            "run-once",
            "--require-harness-close",
            "--command-template",
            f"{sys.executable} -c \"print('command pass')\"",
        ],
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["status"] == "done"


def test_run_once_retries_then_blocks_at_max_attempts(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--max-attempts",
        "2",
        "--slice",
        "contacts:manage contacts",
    )
    command = f"{sys.executable} -c \"import sys; print('QA FAIL'); sys.exit(7)\""

    first = run(tmp_path, "run-once", "--command-template", command)
    assert first.returncode == 1
    state = load(tmp_path)
    assert state["slices"][0]["status"] == "failed"
    assert state["slices"][0]["failure_class"] == "test_failure"
    assert state["slices"][0]["retryable"] is True

    second = run(tmp_path, "run-once", "--command-template", command)
    assert second.returncode == 1
    state = load(tmp_path)
    assert state["status"] == "blocked"
    assert state["slices"][0]["status"] == "blocked"
    assert state["slices"][0]["attempts"] == 2


def test_user_decision_marker_blocks_immediately(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "billing:billing rules")
    command = f"{sys.executable} -c \"import sys; print('USER_DECISION_REQUIRED choose billing provider'); sys.exit(3)\""

    result = run(tmp_path, "run-once", "--command-template", command)
    assert result.returncode == 1
    state = load(tmp_path)
    assert state["status"] == "blocked"
    assert state["slices"][0]["attempts"] == 1
    assert state["slices"][0]["failure_class"] == "user_decision_required"
    assert state["slices"][0]["retryable"] is False
    assert "USER_DECISION_REQUIRED" in state["slices"][0]["last_result"]
    assert any(event["type"] == "slice_blocked" for event in events(tmp_path))


def test_auth_failure_blocks_immediately_with_action(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "auth:auth setup")
    command = f"{sys.executable} -c \"import sys; print('auth expired, please login'); sys.exit(2)\""

    result = run(tmp_path, "run-once", "--command-template", command)
    assert result.returncode == 1
    state = load(tmp_path)
    item = state["slices"][0]
    assert state["status"] == "blocked"
    assert item["failure_class"] == "auth_required"
    assert item["retryable"] is False
    assert "Re-authenticate" in item["recommended_action"]

    status = run(tmp_path, "status")
    assert "class=auth_required" in status.stdout
    assert "action=Re-authenticate" in status.stdout


def test_dependency_failure_is_retryable(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "deps:install deps")
    command = f"{sys.executable} -c \"import sys; print('Cannot find module express'); sys.exit(9)\""

    result = run(tmp_path, "run-once", "--command-template", command)
    assert result.returncode == 1
    item = load(tmp_path)["slices"][0]
    assert item["status"] == "failed"
    assert item["failure_class"] == "dependency_missing"
    assert item["retryable"] is True
    assert "Install or restore" in item["recommended_action"]


def test_review_records_iteration_and_event(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "contacts:manage contacts",
        "--slice",
        "pipeline:track pipeline",
    )

    result = run(
        tmp_path,
        "review",
        "--slice-id",
        "contacts",
        "--demo-result",
        "partial",
        "--user-workflow-status",
        "partial",
        "--qa-result",
        "PASS",
        "--ux-result",
        "FAIL",
        "--learning",
        "Users need a faster create-contact path",
        "--backlog-change",
        "Raise pipeline slice after contact UX fix",
        "--next-slice-id",
        "pipeline",
        "--next-slice-reason",
        "Pipeline is the next highest-value workflow after contact creation.",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = load(tmp_path)
    assert state["current_iteration"] == 2
    review = state["iteration_reviews"][0]
    assert review["slice_id"] == "contacts"
    assert review["demo_result"] == "partial"
    assert review["review_quality"] == "warning"
    assert review["quality_warnings"]
    assert review["quality_blockers"] == []
    assert review["learnings"] == ["Users need a faster create-contact path"]
    assert state["next_slice_id"] == "pipeline"
    assert any(event["type"] == "iteration_reviewed" for event in events(tmp_path))


def test_review_quality_records_blockers_for_failed_evidence(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")

    result = run(
        tmp_path,
        "review",
        "--slice-id",
        "contacts",
        "--demo-result",
        "fail",
        "--user-workflow-status",
        "blocked",
        "--qa-result",
        "FAIL",
        "--ux-result",
        "FAIL",
        "--learning",
        "Contact creation is not usable yet",
        "--backlog-change",
        "Keep contact workflow as the next fix slice",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "review_quality: blocked" in result.stdout
    state = load(tmp_path)
    review = state["iteration_reviews"][0]
    assert review["review_quality"] == "blocked"
    assert any("QA failed" in blocker for blocker in review["quality_blockers"])


def test_preflight_warns_about_missing_review_by_default(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")
    result = run(tmp_path, "run-once", "--command-template", f"{sys.executable} -c \"print('PASS')\"")
    assert result.returncode == 0

    result = run(tmp_path, "preflight")
    assert result.returncode == 0
    assert "preflight: WARN" in result.stdout
    assert "passed slice has no iteration review" in result.stdout


def test_require_review_before_next_blocks_unreviewed_completed_slice(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "contacts:manage contacts",
        "--slice",
        "pipeline:track pipeline",
    )
    first = run(tmp_path, "run-once", "--command-template", f"{sys.executable} -c \"print('PASS')\"")
    assert first.returncode == 0

    second = run(
        tmp_path,
        "run-once",
        "--require-review-before-next",
        "--command-template",
        f"{sys.executable} -c \"print('PASS')\"",
    )
    assert second.returncode == 2
    assert "preflight: BLOCK" in second.stdout
    assert "contacts: passed slice has no iteration review" in second.stdout
    state = load(tmp_path)
    assert state["slices"][1]["status"] == "pending"


def test_require_review_before_next_allows_warning_only_review(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "contacts:manage contacts",
        "--slice",
        "pipeline:track pipeline",
    )
    first = run(tmp_path, "run-once", "--command-template", f"{sys.executable} -c \"print('PASS')\"")
    assert first.returncode == 0
    review = run(
        tmp_path,
        "review",
        "--slice-id",
        "contacts",
        "--demo-result",
        "partial",
        "--user-workflow-status",
        "partial",
        "--qa-result",
        "PASS",
        "--ux-result",
        "FAIL",
        "--learning",
        "The contact path needs a faster empty-state action",
        "--backlog-change",
        "Address the UX gap in the next slice ordering",
        "--next-slice-id",
        "pipeline",
        "--next-slice-reason",
        "Pipeline remains the next highest-value workflow while contact UX is tracked.",
    )
    assert review.returncode == 0
    assert "review_quality: warning" in review.stdout

    second = run(
        tmp_path,
        "run-once",
        "--require-review-before-next",
        "--command-template",
        f"{sys.executable} -c \"print('PASS')\"",
    )
    assert second.returncode == 0, second.stdout + second.stderr
    assert "preflight: WARN" in second.stdout
    state = load(tmp_path)
    assert state["slices"][1]["status"] == "passed"


def test_preflight_blocks_recorded_quality_blockers(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")
    passed = run(tmp_path, "run-once", "--command-template", f"{sys.executable} -c \"print('PASS')\"")
    assert passed.returncode == 0
    review = run(
        tmp_path,
        "review",
        "--slice-id",
        "contacts",
        "--demo-result",
        "fail",
        "--user-workflow-status",
        "blocked",
        "--qa-result",
        "FAIL",
        "--ux-result",
        "FAIL",
        "--learning",
        "Contact workflow failed in review",
        "--backlog-change",
        "Reopen contact workflow as the next fix",
    )
    assert review.returncode == 0

    result = run(tmp_path, "preflight", "--require-review-before-next")
    assert result.returncode == 2
    assert "preflight: BLOCK" in result.stdout
    assert "QA failed" in result.stdout


def test_replan_updates_priority_status_and_status_output(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "contacts:manage contacts",
        "--slice",
        "pipeline:track pipeline",
    )

    result = run(
        tmp_path,
        "replan",
        "--set-priority",
        "pipeline:2000",
        "--set-status",
        "contacts:passed",
        "--next-slice-id",
        "pipeline",
        "--next-slice-reason",
        "Pipeline now unlocks the most important user workflow.",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = load(tmp_path)
    assert state["slices"][0]["status"] == "passed"
    assert state["backlog"][0]["status"] == "passed"
    assert state["backlog"][1]["priority"] == 2000
    assert state["next_slice_id"] == "pipeline"
    assert any(event["type"] == "backlog_replanned" for event in events(tmp_path))

    status = run(tmp_path, "status")
    assert "next: pipeline - track pipeline" in status.stdout
    assert "next_reason: Pipeline now unlocks the most important user workflow." in status.stdout


def test_recover_stale_running_slice_to_failed(tmp_path):
    run(tmp_path, "init", "--product", "crm", "--stack", "Django", "--slice", "contacts:manage contacts")
    state = load(tmp_path)
    state["slices"][0]["status"] = "running"
    state["slices"][0]["attempts"] = 1
    (tmp_path / "goal-queue.json").write_text(json.dumps(state), encoding="utf-8")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "goal-queue-heartbeat.json").write_text(
        json.dumps({"ts": "2000-01-01T00:00:00Z", "pid": 999999, "slice_id": "contacts"}),
        encoding="utf-8",
    )

    result = run(tmp_path, "recover", "--stale-sec", "1")
    assert result.returncode == 0
    assert "recovered: 1" in result.stdout
    state = load(tmp_path)
    assert state["status"] == "active"
    assert state["slices"][0]["status"] == "failed"
    assert any(event["type"] == "slice_recovered" for event in events(tmp_path))


def test_loop_stops_when_done(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "one:first slice",
        "--slice",
        "two:second slice",
    )

    result = run(
        tmp_path,
        "loop",
        "--command-template",
        f"{sys.executable} -c \"print('slice pass')\"",
        "--max-iterations",
        "5",
        "--sleep-sec",
        "0",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    state = load(tmp_path)
    assert state["status"] == "done"
    assert [item["status"] for item in state["slices"]] == ["passed", "passed"]


def test_loop_stops_at_iteration_budget(tmp_path):
    run(
        tmp_path,
        "init",
        "--product",
        "crm",
        "--stack",
        "Django",
        "--slice",
        "one:first slice",
        "--slice",
        "two:second slice",
    )

    result = run(
        tmp_path,
        "loop",
        "--command-template",
        f"{sys.executable} -c \"print('slice pass')\"",
        "--max-iterations",
        "1",
        "--sleep-sec",
        "0",
    )
    assert result.returncode == 0
    assert "max iterations reached" in result.stdout
    state = load(tmp_path)
    assert state["status"] == "active"
    assert [item["status"] for item in state["slices"]] == ["passed", "pending"]
