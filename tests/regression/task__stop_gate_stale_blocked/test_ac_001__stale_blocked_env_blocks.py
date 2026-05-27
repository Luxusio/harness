"""Regression test for stop_gate.py stale BLOCKED_ENV verdict handling.

Reproduces the 2026-05-14 bug where stop_gate.py:94 permitted stops on
stale BLOCKED_ENV verdicts indefinitely. Verifies the fix at the same
location now consults `ctx["stale"]` from `emit_compact_context` and
falls through to the block payload when the verdict is stale.

Test strategy: scaffold a minimal task_dir with:
  - TASK_STATE.yaml carrying runtime_verdict=BLOCKED_ENV and one touched_path
  - CRITIC__qa.md (the BLOCKED_ENV provenance file) with an OLD mtime
  - The touched_path file with a NEW mtime (post-dates CRITIC__qa.md)
Then invoke `stop_gate.main()` and assert the block payload reaches stdout.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "plugin" / "scripts"


def _scaffold_stale_task(tmp_path: Path) -> Path:
    """Build a fake repo tree with a stale BLOCKED_ENV task."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # Minimal .git so find_repo_root() succeeds
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    tasks = repo / "doc" / "harness" / "tasks"
    tasks.mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("type: test\n")
    task_dir = tasks / "TASK__test_stale"
    task_dir.mkdir()
    # The touched file lives under repo root, NOT inside task_dir, so the
    # mtime comparison exercises the path-resolution branch in runtime_is_stale.
    touched_file = repo / "src" / "some_source.py"
    touched_file.parent.mkdir(parents=True, exist_ok=True)
    touched_file.write_text("# initial content\n")

    # Step 1: write CRITIC__qa.md FIRST (the BLOCKED_ENV provenance).
    critic = task_dir / "CRITIC__qa.md"
    critic.write_text("# CRITIC__qa.md\nverdict: BLOCKED_ENV\n")
    # Force a clearly old mtime so the next write is unambiguously newer.
    old = time.time() - 60
    os.utime(critic, (old, old))

    # Step 2: TASK_STATE references the touched file.
    (task_dir / "TASK_STATE.yaml").write_text(
        "task_id: TASK__test_stale\n"
        "status: created\n"
        "runtime_verdict: BLOCKED_ENV\n"
        "touched_paths:\n"
        "  - src/some_source.py\n"
        "plan_session_state: closed\n"
        "closed_at: null\n"
        "updated: 2026-05-14T00:00:00Z\n"
    )

    # Step 3: touch the tracked file AFTER critic, so runtime_is_stale fires.
    touched_file.write_text("# updated content after BLOCKED_ENV\n")
    new = time.time()
    os.utime(touched_file, (new, new))

    # .active marker points at this task
    (tasks / ".active").write_text(str(task_dir))

    return repo


def _run_stop_gate(repo_root: Path) -> dict | None:
    """Invoke stop_gate.py as a subprocess in repo_root and parse stdout JSON."""
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "stop_gate.py")],
        input="",
        capture_output=True,
        text=True,
        cwd=str(repo_root),
        env={**os.environ, "HARNESS_PLUGIN_ROOT": str(REPO_ROOT / "plugin")},
        timeout=10,
    )
    if not result.stdout.strip():
        return None
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"_raw": result.stdout}


def test_stale_blocked_env_emits_block(tmp_path):
    """Stale BLOCKED_ENV verdict must NOT permit stop — gate emits block."""
    repo = _scaffold_stale_task(tmp_path)
    payload = _run_stop_gate(repo)
    assert payload is not None, "stop_gate produced no stdout payload"
    # Block payload schema: {"decision":"block","reason":...} from _gate_response.block
    decision = (
        payload.get("decision")
        or (payload.get("hookSpecificOutput") or {}).get("permissionDecision")
    )
    assert decision == "block", (
        f"expected block on stale BLOCKED_ENV, got decision={decision!r} "
        f"payload={payload}"
    )
    reason_blob = json.dumps(payload, ensure_ascii=False)
    assert "STALE" in reason_blob or "stale" in reason_blob, (
        f"block reason should call out staleness; got: {reason_blob[:400]}"
    )


def test_fresh_blocked_env_permits_stop(tmp_path):
    """Fresh BLOCKED_ENV (no post-verdict activity) must permit stop silently."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    (repo / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    tasks = repo / "doc" / "harness" / "tasks"
    tasks.mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("type: test\n")
    task_dir = tasks / "TASK__test_fresh"
    task_dir.mkdir()
    # Touched file FIRST (mtime baseline)
    touched_file = repo / "doc" / "harness" / "fresh_doc.md"
    touched_file.parent.mkdir(parents=True, exist_ok=True)
    touched_file.write_text("# stable content\n")
    old = time.time() - 60
    os.utime(touched_file, (old, old))
    # CRITIC AFTER (fresh BLOCKED_ENV provenance)
    critic = task_dir / "CRITIC__qa.md"
    critic.write_text("# CRITIC__qa.md\nverdict: BLOCKED_ENV\n")
    # leave critic mtime at default = now > old → fresh
    (task_dir / "TASK_STATE.yaml").write_text(
        "task_id: TASK__test_fresh\n"
        "status: created\n"
        "runtime_verdict: BLOCKED_ENV\n"
        "touched_paths:\n"
        "  - doc/harness/fresh_doc.md\n"
        "plan_session_state: closed\n"
        "closed_at: null\n"
        "updated: 2026-05-14T00:00:00Z\n"
    )
    (tasks / ".active").write_text(str(task_dir))

    payload = _run_stop_gate(repo)
    # Fresh BLOCKED_ENV → stop_gate returns silently (empty stdout)
    assert payload is None, (
        f"fresh BLOCKED_ENV should permit silent stop (empty stdout); got: {payload!r}"
    )
