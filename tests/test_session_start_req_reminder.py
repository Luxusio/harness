"""AC-001 / AC-005: SessionStart banner has REQ reminder; drift_warn hook entry registered."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
HOOKS_JSON = REPO / "plugin" / "hooks" / "hooks.json"


def _load_session_start_entries() -> list[dict]:
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    sessions = data["hooks"]["SessionStart"]
    assert sessions, "no SessionStart entries"
    return sessions[0]["hooks"]


def test_session_start_banner_executes_cleanly():
    entries = _load_session_start_entries()
    banner_cmd = entries[0]["command"]
    result = subprocess.run(
        ["bash", "-c", banner_cmd],
        capture_output=True,
        text=True,
        cwd=REPO,
        input=json.dumps({"cwd": str(REPO)}),
        env={**os.environ, "CLAUDE_PLUGIN_ROOT": str(REPO / "plugin")},
        timeout=10,
    )
    assert result.returncode == 0, f"banner exited {result.returncode}: {result.stderr}"
    assert "REQ:" in result.stdout, f"banner stdout missing REQ line: {result.stdout!r}"
    assert "REQ__" in result.stdout
    assert "when behavior changes" in result.stdout
    assert "harness" in result.stdout


def test_drift_warn_session_start_entry_registered():
    entries = _load_session_start_entries()
    matches = [e for e in entries if "drift_warn" in e.get("command", "")]
    assert len(matches) == 1, f"expected exactly 1 drift_warn SessionStart entry, got {len(matches)}"
    entry = matches[0]
    assert entry["timeout"] == 5, f"expected timeout=5, got {entry['timeout']}"
    assert "|| true" in entry["command"], "fail-safe `|| true` wrapper missing on drift_warn entry"
    assert "${CLAUDE_PLUGIN_ROOT}" in entry["command"], "drift_warn entry should use CLAUDE_PLUGIN_ROOT path"
