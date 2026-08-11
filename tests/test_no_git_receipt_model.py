from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "no_git_receipt_lib", ROOT / "plugin/scripts/_lib.py"
)
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path, meta: dict | None = None) -> Path:
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
    task = tmp_path / "doc/harness/tasks/TASK__no-git"
    lib.ensure_task_scaffold(task, "TASK__no-git", repo_root=tmp_path)
    (task / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    if meta is not None:
        (task / "PLAN.meta.json").write_text(
            json.dumps({"plan_meta": meta}) + "\n", encoding="utf-8"
        )
    return task


def _receipt(
    task: Path,
    lens: str,
    agent_id: str,
    status: str,
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
            "status": status,
            "verdict": verdict,
            "summary": summary,
            **extra,
        },
    )


def _pass_review(task: Path, agent_id: str = "review-1") -> None:
    _receipt(task, "review-code", agent_id, "started")
    _receipt(task, "review-code", agent_id, "completed", "PASS")


def test_scaffold_and_compatibility_helpers_do_not_inspect_git(tmp_path):
    task = _task(tmp_path)
    assert not (task / "TASK_BASELINE.json").exists()
    assert lib.review_diff_fingerprint(task) == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb924"
        "27ae41e4649b934ca495991b7852b855"
    )
    assert lib._effective_touched_paths(task, ["z.py", "a.py", "z.py"]) == [
        "a.py",
        "z.py",
    ]


def test_plan_meta_routes_lenses_with_safe_defaults_and_explicit_security(tmp_path):
    default_task = _task(tmp_path / "default")
    assert lib.required_review_lenses(default_task) == ["review-code"]
    assert lib._required_qa_lenses(default_task) == ["qa-cli"]

    explicit_task = _task(
        tmp_path / "explicit",
        {
            "review_lenses": ["review-security", "unknown"],
            "qa_lenses": ["qa-browser", "qa-api", "unknown", "qa-browser"],
            "security_review": "required",
        },
    )
    assert lib.required_review_lenses(explicit_task) == [
        "review-code",
        "review-security",
    ]
    assert lib._required_qa_lenses(explicit_task) == ["qa-browser", "qa-api"]

    malformed_task = _task(tmp_path / "malformed")
    (malformed_task / "PLAN.meta.json").write_text("not json", encoding="utf-8")
    assert lib.required_review_lenses(malformed_task) == ["review-code"]
    assert lib._required_qa_lenses(malformed_task) == ["qa-cli"]


def test_receipts_require_matching_start_but_ignore_head_and_diff(tmp_path):
    task = _task(tmp_path)
    _receipt(
        task,
        "review-code",
        "review-1",
        "started",
        head_sha="a" * 40,
        diff_fingerprint="sha256:" + "1" * 64,
    )
    completed = _receipt(
        task,
        "review-code",
        "review-1",
        "completed",
        "PASS",
        head_sha="b" * 40,
        diff_fingerprint="sha256:" + "2" * 64,
    )
    assert "head_sha" not in completed
    assert "diff_fingerprint" not in completed
    assert "base_sha" not in completed
    assert lib.receipt_review_verdict(task) == "PASS"

    _receipt(task, "qa-cli", "qa-1", "started", head_sha="c" * 40)
    _receipt(
        task,
        "qa-cli",
        "qa-1",
        "completed",
        "PASS",
        head_sha="d" * 40,
        diff_fingerprint="sha256:" + "3" * 64,
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
        runtime_thread_id="thread-a", runtime_agent_path="/root/review",
    )
    _receipt(
        replay, "review-code", "review-reused", "completed", "PASS",
        runtime_event_id="event-b", runtime_session_id="session-1",
        runtime_thread_id="thread-a", runtime_agent_path="/root/review",
    )
    assert lib.receipt_review_verdict(replay) == "PENDING"


def test_runtime_never_marks_pass_receipts_stale_after_source_edit(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(task, "qa-cli", "qa-1", "completed", "PASS")
    source = tmp_path / "nested/source.py"
    source.parent.mkdir()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    assert lib.receipt_runtime_verdict(task) == "PASS"
    assert lib.runtime_is_stale(task) == (False, "")


def test_close_attestation_v2_needs_receipt_hash_but_no_head(tmp_path):
    task = _task(tmp_path)
    state = lib.read_state(task)
    state.update(
        status="closed",
        runtime_verdict="PASS",
        closed_at="2026-08-11T00:00:00Z",
    )
    fingerprint = lib.receipt_stream_fingerprint(task)
    payload = lib.write_task_close_attestation(
        task, state, receipt_fingerprint=fingerprint
    )
    assert payload["version"] == 2
    assert "head_sha" not in payload
    assert lib.task_close_attestation_valid(task, state)

    legacy = {
        **payload,
        "version": 1,
        "head_sha": "a" * 40,
    }
    (task / lib.TASK_CLOSE_RECEIPT_NAME).write_text(
        json.dumps(legacy) + "\n", encoding="utf-8"
    )
    assert lib.task_close_attestation_valid(task, state)


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
    stream = task / lib.REVIEW_RECEIPTS_NAME
    stream.symlink_to(outside)
    try:
        lib.reset_receipt_streams_for_new_run(task)
    except RuntimeError as exc:
        assert "receipt storage integrity" in str(exc)
    else:
        raise AssertionError("symlinked receipt stream must be rejected")
    assert outside.read_text(encoding="utf-8") == "preserve\n"


def test_rotated_task_run_rejects_prior_run_receipts_without_source_state(tmp_path):
    task = _task(tmp_path)
    original_run = lib.read_task_run(task)["task_run_id"]
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    _receipt(task, "qa-cli", "qa-1", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PASS"

    new_run, _ = lib.begin_task_run(task)
    assert new_run["task_run_id"] != original_run
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
    run_id = lib.read_task_run(task)["task_run_id"]
    try:
        _receipt(
            task, "review-code", "review-wrong", "started",
            task_run_id="f" * 32,
        )
    except RuntimeError as exc:
        assert "does not match" in str(exc)
    else:
        raise AssertionError("mismatched task run must be rejected")

    _pass_review(task)
    (task / lib.TASK_RUN_NAME).write_text("{}\n", encoding="utf-8")
    assert lib.receipt_review_verdict(task) == "PENDING"
    try:
        _receipt(
            task, "qa-cli", "qa-missing-run", "started",
            task_run_id=run_id,
        )
    except RuntimeError as exc:
        assert "valid TASK_RUN" in str(exc)
    else:
        raise AssertionError("missing task run must reject receipt append")


def test_receipt_reset_restores_first_stream_if_second_unlink_fails(tmp_path):
    task = _task(tmp_path)
    _pass_review(task)
    _receipt(task, "qa-cli", "qa-1", "started")
    review_path = task / lib.REVIEW_RECEIPTS_NAME
    qa_path = task / lib.SUBAGENT_RECEIPTS_NAME
    before = {review_path: review_path.read_bytes(), qa_path: qa_path.read_bytes()}
    real_unlink = lib.os.unlink

    def fail_second(path, *args, **kwargs):
        if Path(path) == qa_path:
            raise OSError("forced second unlink failure")
        return real_unlink(path, *args, **kwargs)

    with mock.patch.object(lib.os, "unlink", side_effect=fail_second):
        try:
            lib.reset_receipt_streams_for_new_run(task)
        except OSError as exc:
            assert "forced second" in str(exc)
        else:
            raise AssertionError("forced unlink failure must propagate")

    assert review_path.read_bytes() == before[review_path]
    assert qa_path.read_bytes() == before[qa_path]

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
