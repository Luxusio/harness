import json
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "task_pack_runner.py"


def run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--state", str(tmp_path / "task-pack.json"), *args],
        text=True,
        capture_output=True,
        timeout=20,
    )


def load(tmp_path):
    return json.loads((tmp_path / "task-pack.json").read_text(encoding="utf-8"))


def events(tmp_path):
    path = tmp_path / "task-pack-events.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_init_persists_ordered_task_records_and_next(tmp_path):
    result = run(
        tmp_path,
        "init",
        "--goal",
        "Toss redesign stages",
        "--task",
        "stage-4:Admin density spec and application",
        "--task",
        "stage-5:Admin follow-up verification",
    )
    assert result.returncode == 0, result.stderr

    state = load(tmp_path)
    assert state["status"] == "active"
    assert state["pack_id"] == "toss-redesign-stages"
    assert [item["task_id"] for item in state["tasks"]] == [
        "TASK__stage-4",
        "TASK__stage-5",
    ]
    assert [item["status"] for item in state["tasks"]] == ["queued", "queued"]

    nxt = run(tmp_path, "next")
    assert nxt.returncode == 0
    assert "next: TASK__stage-4 - Admin density spec and application" in nxt.stdout
    assert "prompt: /harness:run task-pack toss-redesign-stages task stage-4" in nxt.stdout
    assert any(event["type"] == "initialized" for event in events(tmp_path))


def test_claim_and_close_advances_without_sequence_question(tmp_path):
    run(
        tmp_path,
        "init",
        "--goal",
        "Roadmap",
        "--task",
        "stage-1:Foundation",
        "--task",
        "stage-2:Browse funnel",
    )

    claimed = run(tmp_path, "claim-next")
    assert claimed.returncode == 0
    assert "claimed: TASK__stage-1 - Foundation" in claimed.stdout
    assert load(tmp_path)["tasks"][0]["status"] == "active"

    closed = run(tmp_path, "close", "--task", "stage-1")
    assert closed.returncode == 0
    assert "closed: TASK__stage-1 closed" in closed.stdout
    assert "next: TASK__stage-2 - Browse funnel" in closed.stdout

    state = load(tmp_path)
    assert [item["status"] for item in state["tasks"]] == ["closed", "queued"]
    assert state["status"] == "active"
    assert "which" not in closed.stdout.lower()
    assert "?" not in closed.stdout


def test_all_closed_marks_pack_done(tmp_path):
    run(tmp_path, "init", "--goal", "Roadmap", "--task", "one:One", "--task", "two:Two")

    assert run(tmp_path, "close", "--task", "one").returncode == 0
    assert run(tmp_path, "close", "--task", "two").returncode == 0

    status = run(tmp_path, "status")
    assert status.returncode == 0
    assert "status: done" in status.stdout
    assert "next: none" in status.stdout


def test_blocked_task_blocks_pack_with_reason(tmp_path):
    run(tmp_path, "init", "--goal", "Roadmap", "--task", "auth:Auth")

    result = run(tmp_path, "close", "--task", "auth", "--result", "blocked", "--reason", "credential missing")
    assert result.returncode == 2
    state = load(tmp_path)
    assert state["status"] == "blocked"
    assert state["tasks"][0]["reason"] == "credential missing"
