"""Tests for receipt-backed Claude subagent lifecycle helpers."""
from __future__ import annotations

import json
import inspect
import os
import subprocess
import sys
import threading
from types import FunctionType
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from conftest import SCRIPTS_DIR

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402
import subagent_lifecycle  # noqa: E402


def _repo(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / ".git").mkdir()
    task_dir = tmp_path / "doc/harness/tasks/TASK__bg"
    task_dir.mkdir(parents=True)
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": _lib.new_uuid7(), "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (tmp_path / "doc/harness/manifest.yaml").write_text("type: test\n", encoding="utf-8")
    return str(tmp_path), str(task_dir)


def _bind(repo: str, task_dir: str, session_id: str) -> None:
    sessions = Path(repo) / "doc/harness/tasks/.active_sessions"
    sessions.mkdir(parents=True, exist_ok=True)
    control = _lib.read_task_control(task_dir)
    (sessions / f"{session_id}.json").write_text(json.dumps({
        "session_id": session_id, "task_dir": task_dir,
        "task_id": Path(task_dir).name, "run_id": control["run_id"],
        "updated": _lib.now_iso(),
    }) + "\n", encoding="utf-8")
    (Path(repo) / "doc/harness/tasks/.active").write_text(
        task_dir + "\n", encoding="utf-8",
    )


def _rotate(task_dir: str, timestamp_ms: int | None = None) -> None:
    control = _lib.read_task_control(task_dir)
    control["run_id"] = _lib.new_uuid7(timestamp_ms)
    Path(task_dir, "TASK.json").write_text(
        json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )


def _transcript(
    tmp_path: Path,
    monkeypatch,
    task_dir: str,
    session_id: str,
    agent_id: str,
    final_message: str,
    *,
    agent_type: str,
    qualified_hook_name: str = "",
    qualified_agent_id: str = "",
    qualified_content=None,
    canonical_starts: int = 1,
    transcript_final_message: str | None = None,
) -> str:
    """Build a subagent transcript.

    ``qualified_hook_name`` prepends a ``hookEvent: SubagentStart`` attachment
    carrying that ``hookName`` and no identity payload — the matcher-qualified
    hook-execution record. With ``canonical_starts=0`` it is the *only* start
    attachment, which is the shape 7 of 47 real transcripts had and the shape
    that used to be declined.
    """
    claude = tmp_path / ".claude"
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(claude))
    path = claude / "projects/project" / session_id / "subagents" / f"agent-{agent_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    run_started = datetime.fromisoformat(
        _lib.task_run_started_at(_lib.read_task_control(task_dir)).replace("Z", "+00:00")
    )
    timestamp = (run_started + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    items = []
    if qualified_hook_name:
        qualified_item = {
            "timestamp": timestamp,
            "sessionId": session_id,
            # Field set copied from a real Claude 2.1.220 transcript. Only the
            # fields the validator reads are load-bearing; `stdout` and
            # `command` are reproduced faithfully because the *absence* of an
            # identity string in stdout is the defect this shape represents —
            # and because the record comes from oh-my-claudecode's start hook,
            # not the harness's, which is the misattribution that sent an
            # earlier root-cause analysis to the wrong place.
            "attachment": {
                "type": "hook_success",
                "hookName": qualified_hook_name,
                "hookEvent": "SubagentStart",
                "content": "" if qualified_content is None else qualified_content,
                "stdout": '{"continue":true}',
                "stderr": "",
                "exitCode": 0,
                "command": (
                    'node "$CLAUDE_PLUGIN_ROOT"/scripts/run.cjs '
                    '"$CLAUDE_PLUGIN_ROOT"/scripts/subagent-tracker.mjs start'
                ),
                "durationMs": 12,
                "toolUseID": f"hook_{agent_id}",
            },
        }
        # qualified_agent_id=None omits the key entirely, proving the validator
        # does not depend on it.
        if qualified_agent_id is not None:
            qualified_item["agentId"] = qualified_agent_id or agent_id
        items.append(qualified_item)
    items += [
        {
            "timestamp": timestamp,
            "agentId": agent_id,
            "sessionId": session_id,
            "attachment": {
                "type": "hook_additional_context",
                "hookName": "SubagentStart",
                "hookEvent": "SubagentStart",
                "content": [f"Agent {agent_type} started ({agent_id})"],
            },
        },
    ] * canonical_starts + [
        {
            "timestamp": timestamp,
            "agentId": agent_id,
            "sessionId": session_id,
            "message": {
                "role": "assistant",
                "content": [{
                    "type": "text",
                    "text": (
                        final_message if transcript_final_message is None
                        else transcript_final_message
                    ),
                }],
            },
        },
    ]
    path.write_text("".join(json.dumps(item) + "\n" for item in items), encoding="utf-8")
    return str(path)


def _stop_payload(
    session_id: str, agent_id: str, agent_type: str, transcript: str, final_message: str,
) -> dict[str, object]:
    return {
        "hook_event_name": "SubagentStop",
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
        "agent_transcript_path": transcript,
        "last_assistant_message": final_message,
    }


def _receipts(task_dir: str) -> list[dict[str, str]]:
    path = Path(task_dir) / "RECEIPTS.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_start_and_real_stop_use_only_receipts(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-1", "agent-1", "harness:qa-cli"
    final_message = "VERDICT: PASS\nchecks passed"
    _bind(repo, task_dir, session_id)
    started = subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    assert started["status"] == "active"
    assert started["runtime_id"] == "claude:sess-1:agent-1"
    assert len(subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    )) == 1

    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload(session_id, agent_id, agent_type, transcript, final_message),
    )
    assert stopped["status"] == "done"
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    ) == []
    receipts = _receipts(task_dir)
    assert [(item["event"], item["verdict"]) for item in receipts] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert {item["source"] for item in receipts} == {"claude_hook"}
    assert {item["runtime_id"] for item in receipts} == {"claude:sess-1:agent-1"}
    assert not (Path(task_dir) / "CONVERSATION.md").exists()
    assert not (Path(repo) / "doc/harness/runtime/background.json").exists()
    assert not (Path(repo) / "doc/harness/runtime/background.json.lock").exists()


def _run_stop(
    tmp_path, monkeypatch, session_id, agent_id, agent_type,
    final_message="VERDICT: PASS\nchecks passed", **transcript_kwargs,
):
    """Register a start, then stop against a transcript built with the given shape."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type, **transcript_kwargs,
    )
    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload(session_id, agent_id, agent_type, transcript, final_message),
    )
    return stopped, task_dir


def test_matcher_qualified_start_attachment_still_completes(tmp_path, monkeypatch):
    """Claude 2.1.x writes 'SubagentStart:<matcher>' before the canonical entry.

    Regression: that duplicate carries no identity payload and used to abort
    provenance outright, so no subagent could ever record a completion and
    task_verify could never reach PASS.
    """
    agent_type = "harness:code-reviewer"
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    )
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-q", "agent-q", agent_type,
        final_message=final_message,
        qualified_hook_name=f"SubagentStart:{agent_type}",
    )
    assert stopped["status"] == "done"
    assert stopped["agent_type"] == agent_type
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]
    digest = _receipts(task_dir)[-1]["summary"].splitlines()[-1].removeprefix(
        "DETAIL_SHA256:"
    )
    assert _lib.read_review_detail(task_dir, digest) == final_message


def test_review_detail_failure_publishes_no_completion_receipt(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-detail-fail", "agent-detail-fail"
    agent_type = "harness:code-reviewer"
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    )
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    external = tmp_path / "external-reviews.jsonl"
    external.write_text("untouched\n", encoding="utf-8")
    Path(task_dir, _lib.REVIEW_DETAILS_NAME).symlink_to(external)

    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload(session_id, agent_id, agent_type, transcript, final_message),
    )

    assert stopped["status"] == "receipt_pending"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert external.read_text(encoding="utf-8") == "untouched\n"


def test_receipt_failure_leaves_idempotently_reusable_detail_orphan(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-receipt-fail", "agent-receipt-fail"
    agent_type = "harness:code-reviewer"
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    )
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    real_open = _lib.os.open

    def fail_receipt_append(path, flags, *args, **kwargs):
        if path == _lib.RECEIPTS_NAME and flags & (os.O_WRONLY | os.O_RDWR):
            raise OSError("simulated receipt append failure")
        return real_open(path, flags, *args, **kwargs)

    with mock.patch.object(_lib.os, "open", side_effect=fail_receipt_append):
        stopped = subagent_lifecycle.mark_subagent_stop(repo, payload)

    assert stopped["status"] == "receipt_pending"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    orphan = Path(task_dir, _lib.REVIEW_DETAILS_NAME).read_bytes()
    assert len(orphan.splitlines()) == 1

    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert Path(task_dir, _lib.REVIEW_DETAILS_NAME).read_bytes() == orphan
    assert [item["event"] for item in _receipts(task_dir)] == ["started", "completed"]


def test_partial_receipt_failure_rolls_back_to_detail_only_orphan(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-receipt-partial", "agent-receipt-partial"
    agent_type = "harness:code-reviewer"
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    )
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    receipt_path = Path(task_dir, _lib.RECEIPTS_NAME)
    receipt_before = receipt_path.read_bytes()
    receipt_inode = receipt_path.stat().st_ino
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    real_write = _lib.os.write
    receipt_writes = 0

    def fail_after_receipt_prefix(fd, data):
        nonlocal receipt_writes
        if os.fstat(fd).st_ino == receipt_inode:
            receipt_writes += 1
            if receipt_writes == 1:
                return real_write(fd, data[:9])
            raise OSError("simulated partial receipt append failure")
        return real_write(fd, data)

    with mock.patch.object(_lib.os, "write", side_effect=fail_after_receipt_prefix):
        stopped = subagent_lifecycle.mark_subagent_stop(repo, payload)

    assert stopped["status"] == "receipt_pending"
    assert receipt_path.read_bytes() == receipt_before
    detail_store = Path(task_dir, _lib.REVIEW_DETAILS_NAME)
    assert len(detail_store.read_text(encoding="utf-8").splitlines()) == 1

    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert [item["event"] for item in _receipts(task_dir)] == ["started", "completed"]
    assert len(detail_store.read_text(encoding="utf-8").splitlines()) == 1


def test_all_formal_review_completions_store_detail_including_pending(
    tmp_path, monkeypatch,
):
    cases = (
        (
            "security", "harness:security-reviewer",
            "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nsecure",
            "PASS",
        ),
        (
            "pending", "harness:code-reviewer",
            "VERDICT: PASS, malformed envelope\n"
            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\ndiagnostic",
            "PENDING",
        ),
    )
    for name, agent_type, final_message, expected_verdict in cases:
        stopped, task_dir = _run_stop(
            tmp_path / name,
            monkeypatch,
            f"sess-{name}",
            f"agent-{name}",
            agent_type,
            final_message=final_message,
        )
        assert stopped["status"] == "done"
        completion = _receipts(task_dir)[-1]
        assert completion["verdict"] == expected_verdict
        digest = completion["summary"].splitlines()[-1].removeprefix("DETAIL_SHA256:")
        assert _lib.read_review_detail(task_dir, digest) == final_message


def test_claude_formal_review_detail_preserves_trailing_whitespace(
    tmp_path, monkeypatch,
):
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
        "exact body with trailing whitespace\n  "
    )
    stopped, task_dir = _run_stop(
        tmp_path,
        monkeypatch,
        "sess-exact-whitespace",
        "agent-exact-whitespace",
        "harness:security-reviewer",
        final_message=final_message,
    )
    assert stopped["status"] == "done"
    completion = _receipts(task_dir)[-1]
    digest = completion["summary"].splitlines()[-1].removeprefix("DETAIL_SHA256:")
    assert _lib.read_review_detail(task_dir, digest) == final_message


def test_qualified_start_attachment_bound_to_another_agent_is_rejected(tmp_path, monkeypatch):
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-f", "agent-f", "harness:qa-cli",
        qualified_hook_name="SubagentStart:harness:qa-cli",
        qualified_agent_id="agent-other",
    )
    assert stopped == {}
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]


def test_stop_completes_when_the_final_text_has_not_been_flushed(tmp_path, monkeypatch):
    """The transcript need not yet carry the agent's final assistant message.

    The runtime appends it around the same instant SubagentStop fires. Requiring
    it to match made genuine stops fail roughly as often as they succeeded.
    """
    agent_type = "harness:qa-cli"
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-flush", "agent-flush", agent_type,
        final_message="VERDICT: PASS\nnot yet written to the transcript",
        transcript_final_message="",
    )
    assert stopped["status"] == "done"
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]


def test_qualified_start_attachment_cannot_spoof_the_agent_type(tmp_path, monkeypatch):
    """A qualified duplicate carries no identity, even with a valid-looking payload."""
    agent_type = "harness:code-reviewer"
    stopped, _task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-s", "agent-s", agent_type,
        final_message="VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean",
        qualified_hook_name=f"SubagentStart:{agent_type}",
        qualified_content=["Agent harness:attacker started (agent-s)"],
    )
    assert stopped["status"] == "done"
    assert stopped["agent_type"] == agent_type


def test_qualified_start_is_the_only_start_and_still_binds(tmp_path, monkeypatch):
    """Some builds emit ONLY the matcher-qualified attachment.

    The validator treated `SubagentStart:<type>` as a duplicate written
    *alongside* a canonical attachment and skipped it. On a build that emits no
    canonical companion it skipped the sole start line and rejected at
    `no-canonical-start-attachment`, declining roughly one in five completion
    receipts on transcripts that existed and were valid — and because
    `task_verify` derives PASS from ordered start/completion pairs, that made
    PASS unreachable for every task, not just the one being reviewed.

    Shape taken from real transcripts (agent-a97c6fcf98183fabf.jsonl and
    siblings): one attachment, hookName `SubagentStart:harness:code-reviewer`,
    content `''`, carrying agentId/sessionId/timestamp.
    """
    agent_type = "harness:code-reviewer"
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-only", "agent-only", agent_type,
        final_message="VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean",
        qualified_hook_name=f"SubagentStart:{agent_type}",
        canonical_starts=0,
    )
    assert stopped["status"] == "done"
    assert stopped["agent_type"] == agent_type
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]


def test_qualified_only_start_must_prove_identity(tmp_path, monkeypatch):
    """Falling back to the qualified line raises its identity bar.

    When it is the binding line it must carry this agentId; the tolerance that
    lets it omit one applies only while a canonical attachment supplies
    identity. Otherwise the new acceptance would bind a stop to a line that
    names no agent at all.
    """
    agent_type = "harness:qa-cli"
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-noid", "agent-noid", agent_type,
        qualified_hook_name=f"SubagentStart:{agent_type}",
        qualified_agent_id=None,
        canonical_starts=0,
    )
    assert stopped.get("status") != "done"
    # No completion receipt: the stop is declined, exactly as an unbindable
    # stop was before this shape was accepted at all.
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]


def test_every_rejection_reason_stays_reachable(tmp_path, monkeypatch):
    """Pin the reason codes, not just the fact that something was refused.

    Asserting only "no completion receipt" cannot tell one rejection from
    another, so widening acceptance can silently retire a check while the suite
    stays green — the `content` widening moved the empty-canonical case from
    `start-content-shape` to `start-identity-mismatch` and nothing noticed.
    """
    agent_type = "harness:qa-cli"

    def _mutate_canonical(transcript, mutate):
        lines = Path(transcript).read_text(encoding="utf-8").splitlines()
        for index, raw in enumerate(lines):
            item = json.loads(raw)
            attachment = item.get("attachment")
            if isinstance(attachment, dict) and attachment.get("hookName") == "SubagentStart":
                item["attachment"] = dict(attachment)
                mutate(item["attachment"])
                lines[index] = json.dumps(item)
                break
        Path(transcript).write_text("\n".join(lines) + "\n", encoding="utf-8")

    cases = [
        ("unrecognized-start-hook-name",
         {}, lambda a: a.__setitem__("hookName", "SubagentStarted")),
        # Suffix charset guard, on the qualified-only path where it is live.
        ("unrecognized-start-hook-name",
         dict(canonical_starts=0, qualified_hook_name="SubagentStart:bad suffix"),
         None),
        ("start-content-shape", {}, lambda a: a.__setitem__("content", 5)),
        ("start-identity-mismatch", {}, lambda a: a.__setitem__("content", [""])),
        ("canonical-start-agent-id-mismatch",
         dict(canonical_starts=0, qualified_hook_name=f"SubagentStart:{agent_type}",
              qualified_agent_id=None), None),
    ]
    for expected, kwargs, mutate in cases:
        root = tmp_path / (expected.replace("-", "_") + str(len(list(tmp_path.iterdir()))))
        root.mkdir(parents=True, exist_ok=True)
        repo, task_dir = _repo(root)
        _bind(repo, task_dir, "sess-r")
        subagent_lifecycle.register_subagent_start(repo, {
            "session_id": "sess-r", "agent_id": "agent-r", "agent_type": agent_type,
        })
        transcript = _transcript(
            root, monkeypatch, task_dir, "sess-r", "agent-r",
            "VERDICT: PASS", agent_type=agent_type, **kwargs,
        )
        if mutate is not None:
            _mutate_canonical(transcript, mutate)
        diagnostics: dict = {}
        stopped = subagent_lifecycle.mark_subagent_stop(
            repo,
            _stop_payload("sess-r", "agent-r", agent_type, transcript, "VERDICT: PASS"),
            diagnostics=diagnostics,
        )
        assert stopped == {}, expected
        assert diagnostics.get("provenance_reason") == expected


def test_repeated_identical_start_pair_still_completes(tmp_path, monkeypatch):
    """One start pair per registered SubagentStart hook, so repeats are normal.

    Rejecting on *count* declined honest stops: two transcripts in the observed
    session carried two identical start pairs for the same agentId and the same
    agent type, and both were refused as `duplicate-canonical-start`. Only two
    *different* types claiming one agentId is a real conflict.
    """
    agent_type = "harness:code-reviewer"
    stopped, task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-dup2", "agent-dup2", agent_type,
        final_message="VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean",
        qualified_hook_name=f"SubagentStart:{agent_type}",
        canonical_starts=2,
    )
    assert stopped["status"] == "done"
    assert stopped["agent_type"] == agent_type
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]


def test_qualified_only_start_from_a_prior_run_is_rejected(tmp_path, monkeypatch):
    """The run-window guard must hold on the path that actually binds.

    Prior-run rejection was only covered through a canonical start, but when the
    canonical banner is absent the qualified line is the binding one — so the
    freshness check on the live path was unpinned.
    """
    agent_type = "harness:code-reviewer"
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-stale")
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "sess-stale", "agent_id": "agent-stale", "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, "sess-stale", "agent-stale",
        "VERDICT: PASS", agent_type=agent_type,
        qualified_hook_name=f"SubagentStart:{agent_type}",
        canonical_starts=0,
    )
    # Move the run start past the transcript's start attachment.
    prior_ms = _lib.uuid7_timestamp_ms(_lib.read_task_control(task_dir)["run_id"])
    with _lib.receipt_stream_transaction(task_dir):
        _rotate(task_dir, prior_ms + 2_000)
        _bind(repo, task_dir, "sess-stale")
    assert subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload("sess-stale", "agent-stale", agent_type, transcript, "VERDICT: PASS"),
    ) == {}


def test_conflicting_qualified_suffixes_are_rejected(tmp_path, monkeypatch):
    """Two agent types claiming one agentId is a conflict, not a repeat."""
    agent_type = "harness:code-reviewer"
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-conflict")
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "sess-conflict", "agent_id": "agent-conflict",
        "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, "sess-conflict", "agent-conflict",
        "VERDICT: PASS", agent_type=agent_type,
        qualified_hook_name=f"SubagentStart:{agent_type}",
        canonical_starts=0,
    )
    # Splice a second qualified attachment naming a different agent type.
    lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    first = json.loads(lines[0])
    forged = json.loads(lines[0])
    forged["attachment"] = dict(first["attachment"])
    forged["attachment"]["hookName"] = "SubagentStart:harness:qa-cli"
    Path(transcript).write_text(
        "\n".join([lines[0], json.dumps(forged)] + lines[1:]) + "\n", encoding="utf-8",
    )
    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload("sess-conflict", "agent-conflict", agent_type,
                            transcript, "VERDICT: PASS"),
    )
    assert stopped.get("status") != "done"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]


def test_qualified_start_attachment_without_agent_id_still_completes(tmp_path, monkeypatch):
    """The duplicate's own agentId is not load-bearing.

    Requiring it would re-couple completion receipts to an undocumented field of
    a line the validator otherwise ignores — the failure shape this fix removes.
    """
    agent_type = "harness:qa-cli"
    stopped, _task_dir = _run_stop(
        tmp_path, monkeypatch, "sess-n", "agent-n", agent_type,
        qualified_hook_name=f"SubagentStart:{agent_type}",
        qualified_agent_id=None,
    )
    assert stopped["status"] == "done"
    assert stopped["agent_type"] == agent_type


def test_unrecognized_start_hook_name_is_still_rejected(tmp_path, monkeypatch):
    for hook_name in ("SubagentStarted", "Subagent:Start", "SubagentStar", "evil"):
        stopped, _task_dir = _run_stop(
            tmp_path / hook_name, monkeypatch, "sess-u", "agent-u", "harness:qa-cli",
            qualified_hook_name=hook_name,
        )
        assert stopped == {}, hook_name


def test_conflicting_canonical_start_attachments_are_still_rejected(tmp_path, monkeypatch):
    """Two canonical banners naming *different* agent types is the conflict.

    This test previously used two identical banners, which is not a forgery
    signal: the runtime writes one start pair per registered SubagentStart
    hook, so a second hook repeats the banner verbatim. Rejecting on count
    declined real completions — two transcripts in the observed session were
    refused for repeating themselves. The security property being pinned is
    that one agentId cannot claim two agent types, and that is unchanged.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-d")
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "sess-d", "agent_id": "agent-d", "agent_type": "harness:qa-cli",
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, "sess-d", "agent-d", "VERDICT: PASS",
        agent_type="harness:qa-cli", canonical_starts=1,
    )
    lines = Path(transcript).read_text(encoding="utf-8").splitlines()
    forged = json.loads(lines[0])
    forged["attachment"] = dict(forged["attachment"])
    forged["attachment"]["content"] = [
        "Agent harness:code-reviewer started (agent-d)"
    ]
    Path(transcript).write_text(
        "\n".join([lines[0], json.dumps(forged)] + lines[1:]) + "\n", encoding="utf-8",
    )
    stopped = subagent_lifecycle.mark_subagent_stop(
        repo, _stop_payload("sess-d", "agent-d", "harness:qa-cli", transcript, "VERDICT: PASS"),
    )
    assert stopped == {}
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]


def test_cloned_bound_adapter_with_foreign_globals_cannot_append(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-clone")
    original = subagent_lifecycle.register_subagent_start
    clone = FunctionType(
        original.__code__, dict(original.__globals__), original.__name__,
        original.__defaults__, original.__closure__,
    )
    clone.__kwdefaults__ = original.__kwdefaults__

    try:
        clone(repo, {
            "session_id": "sess-clone", "agent_id": "agent-clone",
            "agent_type": "harness:qa-cli",
        })
    except PermissionError as exc:
        assert "runtime-owned" in str(exc)
    else:
        raise AssertionError("cloned adapter code authorized a receipt append")
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_start_rechecks_run_after_receipt_lock_acquisition(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-race")
    real_read = _lib.read_task_control
    writer_calls = 0

    def rotate_on_writer_recheck(path):
        nonlocal writer_calls
        control = real_read(path)
        in_writer = any(frame.function == "record" for frame in inspect.stack())
        if in_writer:
            writer_calls += 1
            if writer_calls == 2:
                rotated = dict(control)
                rotated["run_id"] = _lib.new_uuid7()
                Path(path, "TASK.json").write_text(
                    json.dumps(rotated, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                return rotated
        return control

    with mock.patch.object(_lib, "read_task_control", side_effect=rotate_on_writer_recheck):
        try:
            subagent_lifecycle.register_subagent_start(repo, {
                "session_id": "sess-race", "agent_id": "agent-race",
                "agent_type": "harness:qa-cli",
            })
        except RuntimeError as exc:
            assert "task run changed" in str(exc)
        else:
            raise AssertionError("old-run receipt crossed a run rotation")
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_review_completion_rechecks_run_before_detail_and_receipt_append(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-review-race", "agent-review-race"
    agent_type = "harness:code-reviewer"
    final_message = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\nclean"
    )
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    real_read = _lib.read_task_control
    writer_calls = 0

    def rotate_on_locked_writer_recheck(path):
        nonlocal writer_calls
        control = real_read(path)
        if any(frame.function == "record" for frame in inspect.stack()):
            writer_calls += 1
            if writer_calls == 2:
                rotated = dict(control)
                rotated["run_id"] = _lib.new_uuid7()
                Path(path, "TASK.json").write_text(
                    json.dumps(rotated, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                return rotated
        return control

    with mock.patch.object(
        _lib, "read_task_control", side_effect=rotate_on_locked_writer_recheck,
    ):
        stopped = subagent_lifecycle.mark_subagent_stop(repo, payload)

    assert stopped["status"] == "receipt_pending"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert not Path(task_dir, _lib.REVIEW_DETAILS_NAME).exists()


def test_invalid_events_create_no_diagnostic_authority(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-1")
    assert subagent_lifecycle.register_subagent_start(
        repo, {"session_id": "sess-1", "agent_type": "harness:qa-cli"},
    ) == {}
    assert subagent_lifecycle.mark_subagent_stop(
        repo, {"session_id": "sess-1", "agent_id": "agent-1"},
    ) == {}
    assert subagent_lifecycle.handle_subagent_hook(repo, {"event": "unknown"}) == {}
    assert subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "sess-1", "agent_id": "bad:agent",
        "agent_type": "harness:qa-cli",
    }) == {}
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert not (Path(repo) / "doc/harness/runtime").exists()


def test_stop_only_provenance_rejects_foreign_missing_start_symlink_and_prior_run(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-proof", "agent-proof", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = Path(_transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    ))
    base = _stop_payload(session_id, agent_id, "spoofed-type", str(transcript), final_message)

    foreign = dict(base, session_id="foreign")
    assert subagent_lifecycle.mark_subagent_stop(repo, foreign) == {}
    missing = dict(base)
    missing.pop("session_id")
    assert subagent_lifecycle.mark_subagent_stop(repo, missing) == {}

    original = transcript.read_text(encoding="utf-8")
    transcript.write_text(original.splitlines()[1] + "\n", encoding="utf-8")
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    transcript.write_text(original, encoding="utf-8")

    real = transcript.with_name("runtime-copy.jsonl")
    transcript.rename(real)
    transcript.symlink_to(real)
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    transcript.unlink()
    real.rename(transcript)

    prior_ms = _lib.uuid7_timestamp_ms(_lib.read_task_control(task_dir)["run_id"])
    with _lib.receipt_stream_transaction(task_dir):
        _rotate(task_dir, prior_ms + 2_000)
        _bind(repo, task_dir, session_id)
    assert subagent_lifecycle.mark_subagent_stop(repo, base) == {}
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_stop_only_uses_transcript_type_and_conflicting_replay_does_not_append(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-type", "agent-type"
    _bind(repo, task_dir, session_id)
    first = "VERDICT: PASS\nfirst"
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, first,
        agent_type="harness:qa-cli",
    )
    payload = _stop_payload(session_id, agent_id, "harness:review-security", transcript, first)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert {row["agent_type"] for row in _receipts(task_dir)} == {"harness:qa-cli"}

    second = "VERDICT: FAIL\nchanged"
    _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, second,
        agent_type="harness:qa-cli",
    )
    changed = _stop_payload(session_id, agent_id, "harness:qa-cli", transcript, second)
    assert subagent_lifecycle.mark_subagent_stop(repo, changed)["status"] == "receipt_pending"
    assert len(_receipts(task_dir)) == 2


def test_concurrent_starts_publish_each_identity_once(tmp_path):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-start-race"
    _bind(repo, task_dir, session_id)
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(
        subagent_lifecycle.register_subagent_start(repo, {
            "session_id": session_id, "agent_id": f"agent-{i}",
            "agent_type": "harness:qa-cli",
        })
    )) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 12
    assert {row["agent_id"] for row in _receipts(task_dir)} == {
        f"agent-{i}" for i in range(12)
    }


def test_start_replay_is_idempotent(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-replay")
    payload = {
        "session_id": "sess-replay", "agent_id": "agent-replay",
        "agent_type": "harness:qa-cli",
    }
    assert subagent_lifecycle.register_subagent_start(repo, payload)["status"] == "active"
    assert subagent_lifecycle.register_subagent_start(repo, payload)["status"] == "duplicate_start"
    assert len(_receipts(task_dir)) == 1


def test_stop_only_pair_is_atomic_and_replay_is_idempotent(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-stop", "agent-stop", "harness:qa-cli"
    final_message = "VERDICT: PASS\nfocused checks passed"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)

    first = subagent_lifecycle.mark_subagent_stop(repo, payload)
    second = subagent_lifecycle.mark_subagent_stop(repo, payload)

    assert first["status"] == "done" and first["started_from_stop"] is True
    assert second["status"] == "duplicate_stop"
    assert len(_receipts(task_dir)) == 2


def test_stop_only_append_failure_rolls_back_and_retries(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-retry", "agent-retry", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    real_write = _lib.os.write
    calls = 0

    def fail_second_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected completion failure")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(_lib.os, "write", fail_second_once)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "receipt_pending"
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert len(_receipts(task_dir)) == 2


def test_real_stop_append_failure_leaves_start_retryable(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-real-retry", "agent-real-retry", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": agent_type,
    })
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    real_write = _lib.os.write
    failed = False

    def fail_once(*args, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise OSError("injected completion failure")
        return real_write(*args, **kwargs)

    monkeypatch.setattr(_lib.os, "write", fail_once)
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "receipt_pending"
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert subagent_lifecycle.mark_subagent_stop(repo, payload)["status"] == "done"
    assert [item["event"] for item in _receipts(task_dir)] == ["started", "completed"]


def test_concurrent_stop_only_events_publish_one_pair(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-race", "agent-race", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    payload = _stop_payload(session_id, agent_id, agent_type, transcript, final_message)
    results: list[dict[str, object]] = []
    threads = [threading.Thread(
        target=lambda: results.append(subagent_lifecycle.mark_subagent_stop(repo, payload)),
    ) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(_receipts(task_dir)) == 2
    assert {result["status"] for result in results} == {"done", "duplicate_stop"}


def test_real_start_type_conflict_fails_closed_and_stays_active(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-conflict", "agent-conflict"
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id,
        "agent_type": "harness:qa-cli",
    })
    final_message = "VERDICT: PASS"
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type="harness:review-security",
    )
    result = subagent_lifecycle.mark_subagent_stop(repo, _stop_payload(
        session_id, agent_id, "harness:qa-cli", transcript, final_message,
    ))
    assert result["status"] == "receipt_pending"
    assert len(_receipts(task_dir)) == 1
    assert len(subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id,
    )) == 1


def test_active_records_are_current_run_and_session_scoped(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "owner-session")
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": "owner-session", "agent_id": "owner-agent",
        "agent_type": "harness:qa-cli",
    })
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="foreign-session",
    ) == []

    prior = _lib.read_task_control(task_dir)["run_id"]
    with _lib.receipt_stream_transaction(task_dir):
        _rotate(task_dir)
        _bind(repo, task_dir, "owner-session")
    assert _lib.read_task_control(task_dir)["run_id"] != prior
    assert subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="owner-session",
    ) == []


def test_active_records_ignore_other_session_receipts_in_same_run(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "owner-session")
    run_id = _lib.read_task_control(task_dir)["run_id"]
    rows = []
    for session_id, agent_id in (
        ("owner-session", "owner-agent"), ("foreign-session", "foreign-agent"),
    ):
        rows.append({
            "ts": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "event": "started", "source": "claude_hook", "task_run_id": run_id,
            "runtime_id": f"claude:{session_id}:{agent_id}", "agent_id": agent_id,
            "agent_type": "harness:qa-cli", "lens": "qa-cli", "verdict": "",
            "summary": "",
        })
    (Path(task_dir) / "RECEIPTS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )

    active = subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id="owner-session",
    )
    assert [item["id"] for item in active] == ["owner-agent"]


def test_stale_valid_start_expires_but_invalid_and_future_stay_active(tmp_path):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-time"
    _bind(repo, task_dir, session_id)
    run_id = _lib.read_task_control(task_dir)["run_id"]
    base = {
        "event": "started", "source": "claude_hook", "task_run_id": run_id,
        "agent_type": "harness:qa-cli", "lens": "qa-cli", "verdict": "",
        "summary": "",
    }
    old = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    future = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat().replace("+00:00", "Z")
    rows = [
        {**base, "ts": old, "runtime_id": f"claude:{session_id}:old", "agent_id": "old"},
        {**base, "ts": "not-a-time", "runtime_id": f"claude:{session_id}:invalid", "agent_id": "invalid"},
        {**base, "ts": future, "runtime_id": f"claude:{session_id}:future", "agent_id": "future"},
    ]
    (Path(task_dir) / "RECEIPTS.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8",
    )
    active = subagent_lifecycle.active_records(
        repo, task_id="TASK__bg", session_id=session_id, stale_secs=60,
    )
    assert {item["id"] for item in active} == {"invalid", "future"}


def test_background_hook_ignores_non_harness_repo(tmp_path):
    repo = tmp_path / "plain"
    repo.mkdir()
    (repo / ".git").mkdir()
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo,
        input=json.dumps({"session_id": "sess-1", "agent_id": "agent-1"}),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert not (repo / "doc/harness").exists()


def test_background_hook_publishes_start_without_registry(tmp_path):
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-hook")
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "start"],
        cwd=repo,
        input=json.dumps({
            "cwd": repo,
            "session_id": "sess-hook",
            "agent_id": "agent-hook",
            "agent_type": "harness:qa-cli",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert [item["event"] for item in _receipts(task_dir)] == ["started"]
    assert not (Path(repo) / "doc/harness/runtime").exists()


def _run_background_hook(repo: str, event: str, payload: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", event],
        cwd=repo, input=json.dumps({"cwd": repo, **payload}),
        text=True, capture_output=True, timeout=10,
    )


def _learnings(repo: str) -> list[dict]:
    path = Path(repo) / "doc/harness/learnings.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_binding_miss_breadcrumb_is_written_when_a_receipt_was_owed(tmp_path):
    """A lens agent that produces no receipt must stay visible.

    This breadcrumb is the documented diagnostic entry point; the 2026-08-25
    outage was only findable because of it.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-owed")
    result = _run_background_hook(repo, "stop", {
        "session_id": "sess-owed", "agent_id": "agent-owed",
        "agent_type": "harness:qa-cli",
        "agent_transcript_path": "/nonexistent/subagents/agent-owed.jsonl",
        "last_assistant_message": "VERDICT: PASS",
    })
    assert result.returncode == 0, result.stderr
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists(), "no receipt may be written"
    misses = [
        item for item in _learnings(repo)
        if item.get("source") == "background_hook:binding-miss"
    ]
    assert len(misses) == 1, misses
    # Assert the specific code, not merely that the field is present: the
    # breadcrumb formats `provenance_reason={reason or 'n/a'}` unconditionally,
    # so a weaker assertion would still pass if the diagnostics plumbing were
    # removed entirely.
    assert "provenance_reason=path-outside-projects-root" in misses[0]["error"]


def test_binding_miss_breadcrumb_is_silent_when_no_receipt_was_owed(tmp_path):
    """Agent classes with no transcript and no started receipt owe no completion."""
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-unowed")
    result = _run_background_hook(repo, "stop", {
        "session_id": "sess-unowed", "agent_id": "agent-unowed",
        "agent_transcript_path": "/nonexistent/subagents/agent-unowed.jsonl",
        "last_assistant_message": "some text",
    })
    assert result.returncode == 0, result.stderr
    assert [
        item for item in _learnings(repo)
        if item.get("source") == "background_hook:binding-miss"
    ] == []


def test_background_hook_publishes_stop_only_pair_without_registry(tmp_path, monkeypatch):
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id, agent_type = "sess-stop-hook", "agent-stop-hook", "harness:qa-cli"
    final_message = "VERDICT: PASS"
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        agent_type=agent_type,
    )
    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "stop"],
        cwd=repo,
        input=json.dumps(_stop_payload(
            session_id, agent_id, agent_type, transcript, final_message,
        )),
        text=True,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr
    assert [(item["event"], item["verdict"]) for item in _receipts(task_dir)] == [
        ("started", ""), ("completed", "PASS"),
    ]
    assert not (Path(task_dir) / "CONVERSATION.md").exists()
    assert not (Path(repo) / "doc/harness/runtime").exists()


def test_background_hook_accepts_formal_review_at_the_detail_size_limit(
    tmp_path, monkeypatch,
):
    repo, task_dir = _repo(tmp_path)
    session_id = "sess-stop-size-limit"
    agent_id = "agent-stop-size-limit"
    agent_type = "harness:security-reviewer"
    prefix = (
        "VERDICT: PASS\n"
        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
    )
    final_message = prefix + "x" * (
        _lib._REVIEW_DETAIL_MAX_BYTES - len(prefix)
    )
    _bind(repo, task_dir, session_id)
    transcript = _transcript(
        tmp_path,
        monkeypatch,
        task_dir,
        session_id,
        agent_id,
        final_message,
        agent_type=agent_type,
    )

    result = _run_background_hook(repo, "stop", _stop_payload(
        session_id, agent_id, agent_type, transcript, final_message,
    ))

    assert result.returncode == 0, result.stderr
    completion = _receipts(task_dir)[-1]
    assert completion["event"] == "completed"
    assert completion["verdict"] == "PASS"
    digest = completion["summary"].splitlines()[-1].removeprefix("DETAIL_SHA256:")
    assert _lib.read_review_detail(task_dir, digest) == final_message


def test_a_lens_less_agent_writes_no_receipt_and_no_crash(tmp_path):
    """`harness:developer` and friends owe nothing. Saying so must be silent.

    Until 2026-09-11 their receipt was refused deep inside
    `_receipt_entry_semantics_valid` on the `lens not in SUPPORTED_LENSES`
    clause; the ValueError escaped `register_subagent_start` into
    `background_hook`'s `except`, and every spawn wrote a `gate-crash` row.
    37 rows since 2026-08-25, 19 on 2026-09-10 alone. Logging an expected
    absence as a crash is a false signal, and at that volume it buries the real
    failures that commit 6689dd7 was landed to surface.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-lensless")
    diagnostics: dict = {}

    for agent_type in (
        "harness:developer", "oh-my-claudecode:critic", "harness:documentation-review",
    ):
        result = subagent_lifecycle.register_subagent_start(
            repo,
            {
                "session_id": "sess-lensless",
                # The unnamed id shape: `a` + 16 hex, as this CLI emits it.
                "agent_id": "a0123456789abcdef",
                "agent_type": agent_type,
            },
            diagnostics=diagnostics,
        )
        assert result == {}, agent_type
        assert diagnostics["provenance_reason"] == "no-lens-for-agent-type"
        assert diagnostics["receipt_not_owed"] is True

    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_a_name_shadowed_lens_spawn_stays_observable(tmp_path):
    """Passing `name=` loses the agent type, so the lens cannot be recovered.

    The CLI puts the display name in the `agentType` position. Its own subagent
    metadata shows the loss directly — a named spawn records
    `{"agentType": "qa-cli-1", "name": "qa-cli-1", ...}` where an unnamed one
    records `{"agentType": "harness:code-reviewer", "toolUseId": ...}`. The
    resolved type is simply absent.

    Measured 2026-09-10, same session and same agent type: three named spawns
    wrote 0 receipts and 3 gate-crashes; one unnamed spawn wrote its receipt
    immediately. A coordinator that names its lanes for legibility cannot
    produce a PASS no matter how correctly the review runs.

    Making this silent alongside the genuinely lens-less case would be strictly
    worse than the crash it replaces, so it keeps a breadcrumb.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-named")
    diagnostics: dict = {}

    result = subagent_lifecycle.register_subagent_start(
        repo,
        {
            "session_id": "sess-named",
            # A named spawn's id embeds the name, as in the observed
            # `aqa-cli-1-ee8c583a936c4ed3` / `adiscover-adversarial-...`.
            "agent_id": "areview-scoring-b96cb3d1a3b38065",
            "agent_type": "review-scoring",
        },
        diagnostics=diagnostics,
    )

    assert result == {}
    assert diagnostics["provenance_reason"] == "named-spawn-shadows-agent-type"
    # Not owed-nothing: this one must reach the binding-miss breadcrumb.
    assert diagnostics["receipt_not_owed"] is False
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_a_named_spawn_whose_name_still_encodes_a_lens_is_unaffected(tmp_path):
    """Not every named spawn is broken — only ones whose name loses the lens.

    `qa-cli-1` happens to tokenise to `qa-cli`, which is why the defect was
    intermittent rather than total and took a month to isolate. The guard must
    not widen into "named spawns never get receipts".
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-named-ok")
    diagnostics: dict = {}

    result = subagent_lifecycle.register_subagent_start(
        repo,
        {
            "session_id": "sess-named-ok",
            "agent_id": "aqa-cli-1-ee8c583a936c4ed3",
            "agent_type": "qa-cli-1",
        },
        diagnostics=diagnostics,
    )

    assert result["status"] == "active"
    assert [item["lens"] for item in _receipts(task_dir)] == ["qa-cli"]
    assert "receipt_not_owed" not in diagnostics


def test_lens_less_spawn_through_the_hook_logs_neither_crash_nor_miss(tmp_path):
    """End to end: the two noise channels both stay quiet for an owed-nothing spawn.

    `_receipt_was_expected` falls back to "the payload names an agent type", and
    a lens-less agent does name one — so without `receipt_not_owed` this change
    would simply trade `gate-crash` rows for binding-miss rows.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-hook-lensless")

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "start"],
        cwd=repo,
        input=json.dumps({
            "hook_event_name": "SubagentStart",
            "session_id": "sess-hook-lensless",
            "agent_id": "a0123456789abcdef",
            "agent_type": "harness:developer",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert _learnings(repo) == []


def test_named_lens_spawn_through_the_hook_leaves_a_breadcrumb(tmp_path):
    """The shadowed case is the one an operator has to be able to find."""
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-hook-named")

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "start"],
        cwd=repo,
        input=json.dumps({
            "hook_event_name": "SubagentStart",
            "session_id": "sess-hook-named",
            "agent_id": "areview-scoring-b96cb3d1a3b38065",
            "agent_type": "review-scoring",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    misses = [
        item for item in _learnings(repo)
        if item.get("source") == "background_hook:binding-miss"
    ]
    assert len(misses) == 1
    assert "named-spawn-shadows-agent-type" in json.dumps(misses[0])
    # Reason codes only — never the display name's surrounding payload values.
    assert "gate-crash" not in json.dumps(_learnings(repo))


def test_a_stop_that_is_owed_a_completion_is_never_silenced(tmp_path, monkeypatch):
    """`receipt_not_owed` must not fire when a `started` receipt already exists.

    The stop path computes `expected_receipt` before consulting `_lens_absent`.
    If the start payload's type carried a lens but the transcript-derived type
    loses it, the spawn is genuinely owed a completion and its PASS is
    unreachable without one — silencing that is strictly worse than the
    `gate-crash` row it used to leave.
    """
    repo, task_dir = _repo(tmp_path)
    session_id, agent_id = "sess-owed", "a0123456789abcdef"
    _bind(repo, task_dir, session_id)
    subagent_lifecycle.register_subagent_start(repo, {
        "session_id": session_id, "agent_id": agent_id, "agent_type": "harness:qa-cli",
    })
    assert [item["lens"] for item in _receipts(task_dir)] == ["qa-cli"]

    diagnostics: dict = {}
    final_message = "VERDICT: PASS"
    transcript = _transcript(
        tmp_path, monkeypatch, task_dir, session_id, agent_id, final_message,
        # The transcript reports a type with no lens: the shape that reaches
        # `_lens_absent` with `expected_receipt` already True.
        agent_type="harness:developer",
    )
    subagent_lifecycle.mark_subagent_stop(
        repo,
        _stop_payload(session_id, agent_id, "harness:developer", transcript, final_message),
        diagnostics,
    )

    assert diagnostics.get("expected_receipt") is True
    assert diagnostics.get("receipt_not_owed") is False


def test_a_ux_lens_agent_owes_nothing_and_says_so_silently(tmp_path):
    """`harness:ux-*` infers a lens string that the schema does not accept.

    `_infer_receipt_lens` has an explicit `ux` branch, so `harness:ux-cli`
    yields `"ux-cli"` — but `SUPPORTED_LENSES` is the six `review-*`/`qa-*`
    values only, so `_receipt_entry_semantics_valid` rejects it. A guard that
    tested truthiness rather than membership let these spawns through to that
    exact clause, and the develop and run skills both prescribe UX lenses for
    user-facing changes, so every one of them kept writing the `gate-crash` row
    AC-2 exists to remove.

    A `ux-*` lens can never be required — `required_lenses` is validated against
    the same set — so it owes nothing and belongs on the silent path.
    """
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-ux")
    diagnostics: dict = {}

    for agent_type in ("harness:ux-cli", "harness:ux-api", "harness:ux-browser"):
        result = subagent_lifecycle.register_subagent_start(
            repo,
            {
                "session_id": "sess-ux",
                "agent_id": "a0123456789abcdef",
                "agent_type": agent_type,
            },
            diagnostics=diagnostics,
        )
        assert result == {}, agent_type
        assert diagnostics["provenance_reason"] == "no-lens-for-agent-type"
        assert diagnostics["receipt_not_owed"] is True

    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_a_ux_lens_spawn_through_the_hook_logs_neither_crash_nor_miss(tmp_path):
    """End to end: the UX lens must be quiet on both noise channels."""
    repo, task_dir = _repo(tmp_path)
    _bind(repo, task_dir, "sess-ux-hook")

    result = subprocess.run(
        [sys.executable, os.path.join(SCRIPTS_DIR, "background_hook.py"), "--event", "start"],
        cwd=repo,
        input=json.dumps({
            "hook_event_name": "SubagentStart",
            "session_id": "sess-ux-hook",
            "agent_id": "a0123456789abcdef",
            "agent_type": "harness:ux-cli",
        }),
        text=True,
        capture_output=True,
        timeout=5,
    )

    assert result.returncode == 0, result.stderr
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()
    assert _learnings(repo) == []
