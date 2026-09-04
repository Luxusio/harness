"""Regression tests for plugin/scripts/stop_gate.py.

Covers the four ACs in TASK__stop-hook-when-task-active:
  AC-001  block JSON emitted on stdout when .active exists
  AC-002  silent stdout when .active absent
  AC-003  reason names the task_id and the two legitimate exits
  AC-004  never raises on malformed / missing inputs
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from datetime import datetime, timedelta, timezone

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402

STOP_GATE = os.path.join(SCRIPTS_DIR, "stop_gate.py")


def _fake_repo(tmp_path, active_contents: str | None = None) -> str:
    """Create a tmp fake repo with a .git dir. Optionally write .active."""
    (tmp_path / ".git").mkdir()
    tasks_dir = tmp_path / "doc" / "harness" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tmp_path / "doc" / "harness" / "manifest.yaml").write_text("type: test\n", encoding="utf-8")
    if active_contents is not None:
        (tasks_dir / ".active").write_text(active_contents, encoding="utf-8")
    return str(tmp_path)


def _run(cwd: str, stdin: str = "{}", env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
    proc_env = os.environ.copy()
    proc_env.update(env or {})
    return subprocess.run(
        [sys.executable, STOP_GATE],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=cwd,
        env=proc_env,
        timeout=5.0,
    )


def _write_claude_start(
    repo: str, task_id: str, session_id: str, *, ts: str = "",
    agent_id: str = "agent-bg", append: bool = False,
) -> None:
    """Write one `started` row with no completion — an agent the gate sees as live.

    `agent_id` / `append` exist for the consecutive-yield tests, which need a
    *second, different* record to appear without erasing the first. Defaults
    reproduce the original single-row, truncating behaviour exactly.
    """
    task_dir = os.path.join(repo, "doc", "harness", "tasks", task_id)
    os.makedirs(task_dir, exist_ok=True)
    control_path = Path(task_dir) / "TASK.json"
    if not control_path.exists():
        control_path.write_text(json.dumps({
            "run_id": _lib.new_uuid7(), "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "close_receipt_fingerprint": None,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    run_id = _lib.read_task_control(task_dir)["run_id"]
    sessions = Path(repo) / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "task_dir": task_dir, "task_id": task_id,
        "run_id": run_id, "updated": _lib.now_iso(),
    }) + "\n", encoding="utf-8")
    (Path(repo) / "doc/harness/tasks/.active").write_text(
        task_dir + "\n", encoding="utf-8",
    )
    row = {
        "ts": ts or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "event": "started",
        "source": "claude_hook",
        "task_run_id": run_id,
        "runtime_id": f"claude:{session_id}:{agent_id}",
        "agent_id": agent_id,
        "agent_type": "harness:qa-cli",
        "lens": "qa-cli",
        "verdict": "",
        "summary": "",
    }
    mode = "a" if append else "w"
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), mode, encoding="utf-8") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _write_completed_lenses(repo: str, task_id: str, lenses: list[tuple[str, str]]) -> None:
    """Write paired lifecycle rows for isolated Stop-hook policy tests."""
    task_dir = Path(repo) / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    control_path = task_dir / "TASK.json"
    if not control_path.exists():
        control_path.write_text(json.dumps({
            "run_id": _lib.new_uuid7(),
            "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "close_receipt_fingerprint": None,
        }), encoding="utf-8")
    (task_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    run_id = _lib.read_task_control(str(task_dir))["run_id"]
    rows = []
    for index, (lens, verdict) in enumerate(lenses):
        agent_id = f"agent-{index}"
        common = {
            "source": "claude_hook",
            "task_run_id": run_id,
            "runtime_id": f"claude:test-session:{agent_id}",
            "agent_id": agent_id,
            "agent_type": f"harness:{lens}",
            "lens": lens,
        }
        rows.append({
            **common, "ts": _lib.now_iso(), "event": "started",
            "verdict": "", "summary": "",
        })
        summary = f"VERDICT: {verdict}"
        if lens.startswith("review-"):
            summary += (
                "\nFINDING_COUNTS: FIX_NOW=" + ("1" if verdict == "FAIL" else "0")
                + " INVESTIGATE=" + ("1" if verdict == "BLOCKED_ENV" else "0")
                + " OPTIONAL=0"
            )
        normalized_verdict, normalized_summary = _lib.normalize_receipt_completion(
            lens, summary, verdict,
        )
        rows.append({
            **common, "ts": _lib.now_iso(), "event": "completed",
            "verdict": normalized_verdict, "summary": normalized_summary,
        })
    (task_dir / "RECEIPTS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_blocks_when_active(tmp_path):
    """AC-001: .active present → stdout is JSON with decision=block."""
    repo = _fake_repo(tmp_path, active_contents="TASK__example-active-task\n")
    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip(), "expected JSON on stdout"
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert isinstance(payload.get("reason"), str) and payload["reason"]


def test_silent_when_no_active(tmp_path):
    """AC-002: .active absent → empty stdout, exit 0."""
    repo = _fake_repo(tmp_path, active_contents=None)
    result = _run(repo)

    assert result.returncode == 0, result.stderr
    assert result.stdout == "", f"expected empty stdout, got {result.stdout!r}"


def test_removed_goal_payload_capture_does_not_write_on_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents=None)
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(
        '{"type":"system","content":"Goal set: GOAL TASK내용 감지 테스트"}\n',
        encoding="utf-8",
    )
    out_dir = tmp_path / "goal-probe"
    payload = {
        "hook_event_name": "Stop",
        "session_id": "claude-goal-test",
        "transcript_path": str(transcript),
    }

    result = _run(
        repo,
        stdin=json.dumps(payload),
        env={
            "HARNESS_CAPTURE_GOAL_PAYLOADS": "1",
            "HARNESS_GOAL_PAYLOAD_DIR": str(out_dir),
            "HARNESS_RUNTIME": "claude",
        },
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert not out_dir.exists()


def test_reason_contains_task_id_and_exits(tmp_path):
    """AC-003: reason names the active task_id and legitimate stop exits."""
    repo = _fake_repo(tmp_path, active_contents="TASK__alpha-beta-gamma\n")
    result = _run(repo)

    payload = json.loads(result.stdout)
    reason = payload["reason"]
    assert "TASK__alpha-beta-gamma" in reason, reason
    assert "task_close" in reason, reason
    assert "runtime_verdict=PASS" in reason, reason
    assert "task_verify once" in reason, reason
    assert "task_verify until" not in reason, reason
    assert "task_blocked directly" in reason, reason
    assert "stop-judge" not in reason, reason
    assert "BLOCKED_ENV" in reason, reason
    assert "cancel the task" not in reason, reason
    assert "AskUserQuestion" not in reason, reason


def test_reason_handles_full_path_active(tmp_path):
    """AC-003: .active written as a full task_dir path (the MCP's format) still
    surfaces just the TASK__ basename in the reason, not the whole path."""
    full_path = str(tmp_path / "doc" / "harness" / "tasks" / "TASK__from-path")
    repo = _fake_repo(tmp_path, active_contents=full_path + "\n")
    result = _run(repo)

    payload = json.loads(result.stdout)
    reason = payload["reason"]
    assert "TASK__from-path" in reason, reason
    # Full path should not appear verbatim — only the basename.
    assert full_path not in reason, reason


def test_safe_on_error(tmp_path):
    """AC-004: malformed input never raises — empty stdin + corrupt .active."""
    # .active is a directory, not a regular file → triggers read error path.
    (tmp_path / ".git").mkdir()
    tasks_dir = tmp_path / "doc" / "harness" / "tasks"
    tasks_dir.mkdir(parents=True)
    (tmp_path / "doc" / "harness" / "manifest.yaml").write_text("type: test\n", encoding="utf-8")
    (tasks_dir / ".active").mkdir()  # directory in place of the expected file

    result = _run(str(tmp_path), stdin="")  # empty stdin, unreadable marker
    assert result.returncode == 0, result.stderr
    # .active exists but is a dir → os.path.isfile() is False → silent pass.
    assert result.stdout == "", f"expected silent exit, got {result.stdout!r}"

    # Second branch: .active is a file containing bytes that are not decodable.
    import shutil as _shutil
    _shutil.rmtree(str(tasks_dir / ".active"))
    (tasks_dir / ".active").write_bytes(b"\xff\xfe\xfd invalid utf-8\n")

    result2 = _run(str(tmp_path), stdin="{not valid json}")
    assert result2.returncode == 0, result2.stderr
    # Either a clean block JSON with fallback task_id, or empty stdout — both are acceptable;
    # the invariant is "no crash, exit 0, nothing on stderr from a raise".
    if result2.stdout.strip():
        payload = json.loads(result2.stdout)
        assert payload["decision"] == "block"


def test_yields_the_turn_to_an_active_background_subagent(tmp_path):
    """A fresh Stop while a lens runs auto-waits, then allows — and says so.

    This blocked until 2026-09-04. Blocking here cannot produce the missing
    evidence: only the subagent can, and its completion notification re-invokes
    the coordinator. What blocking did produce was a turn whose entire content
    was "the review is still running" — measured ~20 times in one session.

    Allowing is not silence. The payload names the task and the agents being
    waited on, so an operator seeing the run pause can tell why.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__with-bg\n")
    _write_claude_start(repo, "TASK__with-bg", "sess-bg")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-bg", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload.get("decision") != "block"
    assert payload["continue"] is True
    message = payload["systemMessage"]
    assert "background subagent work still running" in message
    assert "agent-bg" in message
    assert "wait_background.py" not in message
    # The report must not carry the directive that used to accompany a block;
    # ordering the model not to stop while allowing the stop is incoherent.
    assert "do not stop" not in message.lower()


def test_yielding_to_a_lens_does_not_survive_the_record_clearing(tmp_path):
    """AC-1b — the yield lasts as long as the *receipt record* does.

    Not as long as the agent does. There is no heartbeat: `subagent_lifecycle`
    stamps `updated_ts` from the `started` receipt and never refreshes it, so
    the gate cannot see an agent die. A killed agent — or one whose
    SubagentStop was rejected — leaves an orphan `started` row that reads as
    active until HARNESS_BACKGROUND_STALE_SECS. That gap is bounded separately,
    by the consecutive-yield counter; see
    test_repeated_yields_on_an_unchanged_record_set_block.

    What this pins is the scoping half: the allowance is conditioned on a
    record for this task and session, and disappears with it.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__yield-ends\n")
    stdin = json.dumps({"session_id": "sess-ends", "hook_event_name": "Stop"})
    env = {"HARNESS_BACKGROUND_WAIT_SECS": "0"}

    _write_claude_start(repo, "TASK__yield-ends", "sess-ends")
    while_running = json.loads(_run(repo, stdin=stdin, env=env).stdout)
    assert while_running.get("decision") != "block"

    # Clear the live record the way a SubagentStop would.
    receipts = Path(repo) / "doc/harness/tasks/TASK__yield-ends/RECEIPTS.jsonl"
    receipts.write_text("", encoding="utf-8")

    after_finishing = json.loads(_run(repo, stdin=stdin, env=env).stdout)
    assert after_finishing["decision"] == "block"
    assert "TASK__yield-ends" in after_finishing["reason"]


def test_repeated_yields_on_an_unchanged_record_set_block(tmp_path):
    """An orphan `started` row must not silence C-17 for half an hour.

    Killing a lens, or having its SubagentStop rejected, leaves a `started` row
    with no completion (REQ__subagent-lifecycle-receipt-boundaries). It reads as
    active until HARNESS_BACKGROUND_STALE_SECS — 1800s by default. Yielding on
    that alone would mean: nothing running, no completion possible, PASS
    unreachable, and the gate quiet for 30 minutes. That is precisely the
    abandonment C-17 exists to prevent, and review reproduced it against the
    first version of this change.

    Age cannot separate the two cases — there is no heartbeat, and real review
    lenses here routinely run for many minutes against the 1800s window, so no
    age threshold separates them. Repetition can: a live
    agent yields once and its completion notification resumes the run.
    """
    # Aged 25 minutes: inside the 1800s stale window, so `active_records` still
    # reports it, and old enough to be the killed-agent case rather than a lens
    # that just started. Using a fresh row here would exercise the counter but
    # never reach the scenario the counter exists for.
    aged = (
        datetime.now(timezone.utc) - timedelta(minutes=25)
    ).isoformat().replace("+00:00", "Z")
    repo = _fake_repo(tmp_path, active_contents="TASK__orphan\n")
    _write_claude_start(repo, "TASK__orphan", "sess-orphan", ts=aged)
    stdin = json.dumps({"session_id": "sess-orphan", "hook_event_name": "Stop"})
    # The stale window is pinned, not inherited: the 25-minute row is only the
    # aged-but-active case while it stays under this bound, and `_run` passes
    # the ambient environment through. A shell exporting a smaller value would
    # otherwise make the row stale and fail this test pointing at the yield
    # counter instead of at the window. 1500s against 1800s is the margin.
    env = {"HARNESS_BACKGROUND_WAIT_SECS": "0", "HARNESS_BACKGROUND_STALE_SECS": "1800"}

    decisions = []
    for _ in range(5):
        decisions.append(json.loads(_run(repo, stdin=stdin, env=env).stdout))

    yielded = [d for d in decisions if d.get("decision") != "block"]
    blocked = [d for d in decisions if d.get("decision") == "block"]
    assert len(yielded) == 3, [d.get("decision") for d in decisions]
    assert blocked, "an unchanged record set must eventually stop being yielded to"
    # The block has to name the case a coordinator cannot otherwise diagnose,
    # and the remedy — a resumed agent writes no receipt, so waiting is wrong.
    reason = blocked[0]["reason"]
    assert "no completion will ever arrive" in reason
    assert "spawn a fresh lens" in reason
    assert "HARNESS_BACKGROUND_STALE_SECS" in reason
    # A refusal to yield must not also announce that it is yielding.
    assert "yielding the turn" not in reason
    # It still names what is being waited on.
    assert "agent-bg" in reason


def test_an_unparseable_receipt_timestamp_still_exhausts_the_budget(tmp_path):
    """The counter must not be defeated by a record that churns its own clock.

    `subagent_lifecycle` keeps a row with an unparseable `ts` active forever —
    the stale window only applies to a timestamp it could read — and reports
    `updated_ts` as *now*. Fingerprinting that derived value made the record
    look different on every Stop, so the budget never advanced and the silence
    became unbounded, which is worse than the 1800s window this replaced.

    Only an out-of-band write to RECEIPTS.jsonl produces such a row, so this is
    defence in depth, not a live path. The fingerprint keys on agent identity.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__badts\n")
    _write_claude_start(repo, "TASK__badts", "sess-badts", ts="not-a-timestamp")
    stdin = json.dumps({"session_id": "sess-badts", "hook_event_name": "Stop"})
    env = {"HARNESS_BACKGROUND_WAIT_SECS": "0"}

    decisions = [
        json.loads(_run(repo, stdin=stdin, env=env).stdout).get("decision")
        for _ in range(5)
    ]
    assert decisions.count("block") >= 1, decisions


def test_a_failed_ledger_write_leaves_no_temp_files(tmp_path):
    """One leaked file per turn-end is a slow leak, not a harmless one."""
    repo = _fake_repo(tmp_path, active_contents="TASK__tmpleak\n")
    _write_claude_start(repo, "TASK__tmpleak", "sess-tmpleak")
    task_dir = Path(repo) / "doc/harness/tasks/TASK__tmpleak"
    (task_dir / ".stop_yield.sess-tmpleak.json").mkdir(parents=True, exist_ok=True)

    for _ in range(3):
        _run(
            repo,
            stdin=json.dumps({"session_id": "sess-tmpleak", "hook_event_name": "Stop"}),
            env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
        )

    assert not list(task_dir.glob(".stop_yield.*.tmp"))


def test_one_sessions_progress_cannot_reset_anothers_yield_budget(tmp_path):
    """The ledger is per-session because the record set it counts is per-session.

    `active_records` filters on the `claude:<sid>:` runtime prefix, so two
    sessions bound to one task dir observe disjoint records. With a single
    shared ledger, session A's healthy churn would reset session B's counter on
    every Stop and B's orphan would never exhaust its budget — reinstating,
    for B, exactly the unbounded silence this counter exists to bound.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__twosess\n")
    _write_claude_start(repo, "TASK__twosess", "sess-b", agent_id="agent-b")
    env = {"HARNESS_BACKGROUND_WAIT_SECS": "0"}
    b_stdin = json.dumps({"session_id": "sess-b", "hook_event_name": "Stop"})

    # B yields twice, then A churns a different record set in between.
    for _ in range(2):
        assert json.loads(_run(repo, stdin=b_stdin, env=env).stdout).get("decision") != "block"
    _write_claude_start(repo, "TASK__twosess", "sess-a", agent_id="agent-a", append=True)
    _run(
        repo,
        stdin=json.dumps({"session_id": "sess-a", "hook_event_name": "Stop"}),
        env=env,
    )

    # B's third yield is still its third: the next one must block.
    assert json.loads(_run(repo, stdin=b_stdin, env=env).stdout).get("decision") != "block"
    assert json.loads(_run(repo, stdin=b_stdin, env=env).stdout)["decision"] == "block"


def test_an_unmaintainable_yield_ledger_blocks(tmp_path):
    """Fail toward the gate, not away from it.

    The counter is what bounds the orphan window. If it cannot be persisted the
    gate cannot tell a first yield from a hundredth, so allowing would restore
    the unbounded silence. Degrading to the pre-2026-09-04 block is noisy and
    safe; degrading to a permanent yield is quiet and wrong.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__noledger\n")
    _write_claude_start(repo, "TASK__noledger", "sess-noledger")
    # A directory where the ledger file must go: os.replace onto it fails.
    ledger = Path(repo) / "doc/harness/tasks/TASK__noledger/.stop_yield.sess-noledger.json"
    ledger.mkdir(parents=True, exist_ok=True)

    payload = json.loads(_run(
        repo,
        stdin=json.dumps({"session_id": "sess-noledger", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    ).stdout)

    assert payload["decision"] == "block"


def test_a_changed_record_set_restarts_the_yield_budget(tmp_path):
    """Progress resets the counter, so a real second lens is not penalised."""
    repo = _fake_repo(tmp_path, active_contents="TASK__progress\n")
    _write_claude_start(repo, "TASK__progress", "sess-progress")
    stdin = json.dumps({"session_id": "sess-progress", "hook_event_name": "Stop"})
    env = {"HARNESS_BACKGROUND_WAIT_SECS": "0"}

    for _ in range(3):
        _run(repo, stdin=stdin, env=env)
    assert json.loads(_run(repo, stdin=stdin, env=env).stdout)["decision"] == "block"

    # A different agent starts: new record set, new budget.
    _write_claude_start(
        repo, "TASK__progress", "sess-progress",
        agent_id="agent-second", append=True,
    )
    assert json.loads(_run(repo, stdin=stdin, env=env).stdout).get("decision") != "block"


def test_stale_background_record_does_not_mask_normal_stop_gate(tmp_path):
    """Stale starts are ignored, so the existing open-task reason is emitted."""
    repo = _fake_repo(tmp_path, active_contents="TASK__stale-bg\n")
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    _write_claude_start(repo, "TASK__stale-bg", "sess-stale", ts=old)

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-stale", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0", "HARNESS_BACKGROUND_STALE_SECS": "1"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task start -> plan -> develop -> QA -> close" in payload["reason"]
    assert "review and task_verify are internal close gates" in payload["reason"]
    assert "background subagent work still running" not in payload["reason"]


def test_malformed_receipt_stream_blocks_normal_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents="TASK__bad-receipts\n")
    task_dir = os.path.join(repo, "doc", "harness", "tasks", "TASK__bad-receipts")
    os.makedirs(task_dir, exist_ok=True)
    _write_claude_start(repo, "TASK__bad-receipts", "sess-bad")
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"legacy":true}\n')

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-bad", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "malformed or unsafe" in payload["reason"]
    assert "fresh task run" in payload["reason"]


def test_malformed_receipt_stream_blocks_recursive_stop(tmp_path):
    repo = _fake_repo(tmp_path, active_contents="TASK__bad-recursive\n")
    task_dir = os.path.join(repo, "doc", "harness", "tasks", "TASK__bad-recursive")
    os.makedirs(task_dir, exist_ok=True)
    _write_claude_start(repo, "TASK__bad-recursive", "sess-bad-recursive")
    with open(os.path.join(task_dir, "RECEIPTS.jsonl"), "w", encoding="utf-8") as handle:
        handle.write('{"legacy":true}\n')

    result = _run(repo, stdin=json.dumps({
        "session_id": "sess-bad-recursive", "hook_event_name": "Stop",
        "stop_hook_active": True,
    }))

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "malformed or unsafe" in payload["reason"]


def test_stop_hook_active_with_active_background_allows_and_reports(tmp_path):
    """Recursive Stop hook continuation should not re-block while background work runs.

    Allowing is unchanged. What changed on 2026-09-04 is that this path used to
    emit nothing at all, leaving an operator with an unexplained stop; the
    fresh-Stop path now takes the same decision, so both report it the same way.
    """
    repo = _fake_repo(tmp_path, active_contents="TASK__recursive-bg\n")
    _write_claude_start(repo, "TASK__recursive-bg", "sess-1")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-1", "hook_event_name": "Stop", "stop_hook_active": True}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload.get("decision") != "block"
    assert payload["continue"] is True
    assert "TASK__recursive-bg" in payload["systemMessage"]


def test_stop_hook_active_without_active_background_still_blocks_open_task(tmp_path):
    """Recursive Stop without active background records still protects the open task."""
    repo = _fake_repo(tmp_path, active_contents="TASK__recursive-no-bg\n")

    result = _run(
        repo,
        stdin=json.dumps({"session_id": "sess-1", "hook_event_name": "Stop", "stop_hook_active": True}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task start -> plan -> develop -> QA -> close" in payload["reason"]
    assert "review and task_verify are internal close gates" in payload["reason"]
    assert "background subagent work still running" not in payload["reason"]


def test_missing_receipts_do_not_prescribe_receipt_only_reruns(tmp_path):
    """Stop guidance keeps unrun work possible but blocks receipt-only retries."""
    task_id = "TASK__missing-receipts"
    repo = _fake_repo(tmp_path, active_contents=task_id + "\n")
    task_dir = Path(repo) / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": _lib.new_uuid7(),
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }), encoding="utf-8")
    (task_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")

    result = _run(repo)

    payload = json.loads(result.stdout)
    action = payload["next_action_command"]
    assert "if a required review has not actually completed" in action
    # State-scoped: the endgame's ban is anchored to "after an actual QA PASS",
    # so in the review-pending state — review final arrived, no receipt, QA not
    # yet run — it does not reach the caller. This head keeps its own.
    assert "do not rerun review solely for a receipt" in action
    # The park call itself lives in `_lib.attestation_endgame()`, which every
    # pending next_action composes.
    assert _lib.attestation_endgame() in action
    assert "call task_blocked directly with" in action
    assert "stop-judge" not in action
    assert _lib.ATTESTATION_BLOCKED_REASON in action
    assert _lib.ATTESTATION_UNBLOCK_CONDITION in action
    assert action.index("task_verify once") < action.index("call task_blocked directly with")


def test_review_blocked_receipt_does_not_bypass_task_blocked(tmp_path):
    task_id = "TASK__review-blocked-receipt"
    repo = _fake_repo(tmp_path, active_contents=task_id + "\n")
    _write_completed_lenses(repo, task_id, [("review-code", "BLOCKED_ENV")])

    result = _run(repo, env={"HARNESS_BACKGROUND_WAIT_SECS": "0"})

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task_blocked" in payload["reason"]
    assert "Call task_blocked directly" in payload["next_action_command"]
    assert "actionable unblock condition" in payload["next_action_command"]


def test_qa_blocked_receipt_does_not_bypass_task_blocked(tmp_path):
    task_id = "TASK__qa-blocked-receipt"
    repo = _fake_repo(tmp_path, active_contents=task_id + "\n")
    _write_completed_lenses(repo, task_id, [
        ("review-code", "PASS"),
        ("qa-cli", "BLOCKED_ENV"),
    ])

    result = _run(repo, env={"HARNESS_BACKGROUND_WAIT_SECS": "0"})

    payload = json.loads(result.stdout)
    assert payload["decision"] == "block"
    assert "task_blocked" in payload["reason"]
    assert "Call task_blocked directly" in payload["next_action_command"]
    assert "actionable unblock condition" in payload["next_action_command"]


# ── State-aware reason (TASK__state-aware-stop-gate-message) ─────────────


def _task_with(tmp_path, task_id: str, *, plan: bool):
    """Fake repo whose .active points at a task dir, optionally with a PLAN."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo = Path(_fake_repo(tmp_path, active_contents=task_id + "\n"))
    task_dir = repo / "doc" / "harness" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": _lib.new_uuid7(),
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }, indent=2) + "\n", encoding="utf-8")
    if plan:
        (task_dir / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    return repo


def _bare_repo(tmp_path, task_id: str) -> Path:
    """Active marker pointing at a task dir that does not exist."""
    tmp_path = Path(tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    return Path(_fake_repo(tmp_path, active_contents=task_id + "\n"))


def _reason(repo) -> str:
    result = _run(
        str(repo),
        stdin=json.dumps({"session_id": "sess-state", "hook_event_name": "Stop"}),
        env={"HARNESS_BACKGROUND_WAIT_SECS": "0"},
    )
    payload = json.loads(result.stdout)
    assert payload["decision"] == "block", payload
    return payload["reason"]


def test_reason_names_the_actual_missing_items(tmp_path):
    """The module docstring has promised this since the 2026-05-12 retro.

    Until 2026-09-03 the reason was a fixed paragraph and the derived state
    reached the caller only through `next_action_command`, so the promise was
    prose. Pin the behaviour, not the promise.
    """
    reason = _reason(_task_with(tmp_path, "TASK__needs-plan", plan=False))
    assert "missing:" in reason, reason
    assert "PLAN.md" in reason, reason


def test_reason_reflects_a_different_gap_differently(tmp_path):
    """Guards the guard: a constant string would satisfy the test above."""
    without_plan = _reason(_task_with(tmp_path / "a", "TASK__needs-plan", plan=False))
    with_plan = _reason(_task_with(tmp_path / "b", "TASK__needs-lenses", plan=True))

    assert "PLAN.md" in without_plan, without_plan
    assert "PLAN.md" not in with_plan, with_plan
    assert "review-code" in with_plan, with_plan


def test_attestation_pair_is_absent_when_that_branch_does_not_apply(tmp_path):
    """C-17 scopes verbatim delivery of the fixed pair to the missing-attestation
    branch. It used to be pinned onto every block, including ones where no lens
    had run yet and no attestation could be missing.
    """
    pair = "Required hook-owned review/QA attestation remains missing"
    assert pair not in _reason(_task_with(tmp_path / "np", "TASK__needs-plan", plan=False))
    # No task dir at all: no derived state, so nothing attestation-related.
    assert pair not in _reason(_bare_repo(tmp_path / "bare", "TASK__bare"))

    # ...and positively delivered where it does apply. Without this half, the
    # reason could stop carrying the pair entirely and the suite would stay
    # green — which is how the docstring claim this task repaired survived from
    # 2026-05-12 to 2026-09-03.
    pending = _reason(_task_with(tmp_path / "lp", "TASK__needs-lenses", plan=True))
    assert pair in pending, pending


def test_reason_stays_far_below_the_old_fixed_paragraph(tmp_path):
    """The cost this change exists to remove.

    The previous reason was ~250 words on every turn-end, and the payload
    reaches the model twice (hook feedback and blocking error). Bound the
    no-derived-state case, which carries no next_action to justify length.
    """
    reason = _reason(_bare_repo(tmp_path, "TASK__bare"))
    assert len(reason.split()) < 100, (len(reason.split()), reason)


def test_trust_boundary_is_stated_once_per_block(tmp_path):
    """It must be present — and only once.

    In the lenses-pending state `emit_compact_context`'s next_action already
    carries the clause, so emitting the gate's own copy as well duplicated ~40
    words in the state that fires most often, on a payload the client surfaces
    twice.
    """
    for name, plan in (("TASK__needs-plan", False), ("TASK__needs-lenses", True)):
        reason = _reason(_task_with(tmp_path / name, name, plan=plan))
        # Count the precedence sentence: it marks a *complete* restatement of
        # the boundary, which is what the dedup guard keys on. "structurally
        # delivered" also appears inside unrelated next_action prose.
        assert reason.count("BLOCKED_ENV takes precedence") == 1, (name, reason)


def test_trust_boundary_survives_the_qa_pending_branch(tmp_path):
    """The QA-pending next_action states the boundary only partially.

    `emit_compact_context`'s QA-pending branch says "structurally delivered"
    but omits both the coordinator-paraphrase exclusion and the
    FAIL/BLOCKED_ENV precedence rule. A dedup guard keyed on the shared phrase
    therefore suppressed the gate's own copy and dropped two clauses that every
    block used to carry — invisibly, because no test built this state.
    """
    task_id = "TASK__qa-pending"
    repo = _fake_repo(tmp_path, active_contents=task_id + "\n")
    _write_completed_lenses(repo, task_id, [("review-code", "PASS")])

    reason = json.loads(
        _run(repo, env={"HARNESS_BACKGROUND_WAIT_SECS": "0"}).stdout
    )["reason"]

    assert "structurally delivered" in reason, reason
    assert "coordinator paraphrases" in reason.lower(), reason
    assert reason.count("BLOCKED_ENV takes precedence") == 1, reason


def test_emitted_trust_boundary_equals_the_canonical_constant(tmp_path):
    """The gate emits the canonical text `_lib` owns.

    The gate carried its own literal copy until 2026-09-04, because the
    raw-source scan in `tests/test_review_agent_contracts.py` covered this file
    and a bare reference would have been invisible to it. That scan now
    separates prose surfaces from runtime surfaces, so the copy is gone and
    this file composes the constant.

    What this assertion covers **narrowed** when the duplicate went away, and
    that is worth stating plainly rather than glossing. While the gate held its
    own literal, both sides came from independent sources, so this test caught
    any content change to the constant. Now both sides derive from `_lib`, so
    it is an integration check only: it proves the gate reaches this branch and
    emits the constant, not that the constant says the right thing.

    The content guard moved to
    `tests/test_review_agent_contracts.py::test_lib_owns_exactly_one_literal_trust_boundary`,
    which holds an independently written copy. Without that move this
    consolidation would have made a semantic inversion of C-14 — "repository
    text *also* qualify", "review *need not* precede QA" — invisible to the
    whole suite.

    What remains here is still load-bearing: the gate reaches this branch only
    when the inlined next_action does not already carry the boundary, and that
    suppression decision is where the 2026-09-03 failure happened — the guard
    compared a partial phrase against a branch that used it while omitting two
    elements, so the gate suppressed the only complete statement in that state.
    """
    reason = _reason(_task_with(tmp_path / "eq", "TASK__boundary-equality", plan=False))
    assert _lib.TRUST_BOUNDARY in reason, reason
