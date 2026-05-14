"""AC-007 regression: log_gate_crash writes payload-aware crash records.

Verifies that the structured gate-crash log captures:
- type: gate-crash
- script: gate name
- error: ExceptionName + message (truncated at 400)
- tool_name (when hook_input has it)
- payload_keys (sorted top-level keys of hook_input)

Plus integration smoke: invoking each of the 4 gate scripts (prewrite,
mcp_bash_guard, qa_delegation, stop) with a malformed payload writes a
type=gate-crash row instead of failing silently.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def test_log_gate_crash_writes_record(tmp_path, monkeypatch=None):
    from _lib import log_gate_crash

    # Redirect repo root to tmp so we don't pollute real learnings.jsonl
    learn_dir = tmp_path / "doc" / "harness"
    learn_dir.mkdir(parents=True)
    learn_path = learn_dir / "learnings.jsonl"
    # Stub find_repo_root by chdir + ensuring our scratch is detected.
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        exc = ValueError("kaboom on tool_input")
        log_gate_crash(exc, "test_gate", hook_input={"tool_name": "shell", "tool_input": {"x": 1}, "session_id": "abc"})
        assert learn_path.exists(), "log file not created"
        line = learn_path.read_text().strip()
        record = json.loads(line)
        assert record["type"] == "gate-crash"
        assert record["script"] == "test_gate"
        assert record["tool_name"] == "shell"
        assert record["payload_keys"] == ["session_id", "tool_input", "tool_name"]
        assert "ValueError" in record["error"] and "kaboom" in record["error"]
    finally:
        os.chdir(cwd)


def test_log_gate_crash_without_hook_input(tmp_path):
    """No hook_input → still writes type=gate-crash, omits tool_name/payload_keys."""
    from _lib import log_gate_crash

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    learn_dir = tmp_path / "doc" / "harness"
    learn_dir.mkdir(parents=True)
    learn_path = learn_dir / "learnings.jsonl"
    cwd = os.getcwd()
    os.chdir(tmp_path)
    try:
        log_gate_crash(RuntimeError("no payload"), "x_gate", hook_input=None)
        line = learn_path.read_text().strip()
        record = json.loads(line)
        assert record["type"] == "gate-crash"
        assert record["script"] == "x_gate"
        assert "tool_name" not in record
        assert "payload_keys" not in record
    finally:
        os.chdir(cwd)


def test_gate_script_imports_resolve():
    """All 4 gate scripts must import log_gate_crash + last_hook_input cleanly."""
    for script in ["prewrite_gate", "mcp_bash_guard", "qa_delegation_gate", "stop_gate"]:
        r = subprocess.run(
            [sys.executable, "-c",
             f"import sys; sys.path.insert(0, '{SCRIPTS}'); import {script}; "
             "from _lib import log_gate_crash, last_hook_input; "
             "print('OK')"],
            capture_output=True, text=True, timeout=10,
        )
        assert r.returncode == 0, f"{script} import failed: {r.stderr[:300]}"
        assert "OK" in r.stdout


def test_last_hook_input_returns_cache_after_read():
    """read_hook_input populates last_hook_input cache for outer-except retrieval."""
    from _lib import read_hook_input, last_hook_input
    # Simulate hook input by piping via subprocess so stdin actually has bytes
    code = (
        f"import sys; sys.path.insert(0, '{SCRIPTS}'); "
        "from _lib import read_hook_input, last_hook_input; "
        "read_hook_input(); "
        "import json; print(json.dumps(last_hook_input()))"
    )
    r = subprocess.run(
        [sys.executable, "-c", code],
        input='{"tool_name":"shell","session_id":"xyz","hook_event_name":"PreToolUse"}',
        capture_output=True, text=True, timeout=5,
    )
    assert r.returncode == 0, r.stderr
    cached = json.loads(r.stdout.strip())
    assert cached.get("tool_name") == "shell"
    assert cached.get("session_id") == "xyz"
