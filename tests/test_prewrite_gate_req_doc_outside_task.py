"""AC-003 (verification-only): prewrite_gate.py does not block doc/<area>/REQ__*.md outside an active task.

Phase 1 CEO consensus confirmed `SOURCE_EXTENSIONS` at prewrite_gate.py:82-87 contains only source code
extensions (.py/.ts/...). `.md` is not gated. This test verifies the property — if it fails, AC-003
escalates from verification-only to code change.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"


def _harness_enabled_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "doc" / "harness").mkdir(parents=True)
    (repo / "doc" / "harness" / "manifest.yaml").write_text("project_type: cli\n")
    return repo


def _gate(repo: Path, payload: dict) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO / "plugin")
    env.pop("HARNESS_SKIP_PREWRITE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "prewrite_gate.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
    )


def test_prewrite_gate_allows_req_doc_outside_active_task(tmp_path):
    repo = _harness_enabled_repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "doc" / "ui" / "REQ__example-feature.md"),
            "content": "# REQ - example-feature\n",
        },
    }
    result = _gate(repo, payload)
    assert result.returncode == 0, f"gate failed: rc={result.returncode}, stderr={result.stderr!r}"
    assert result.stdout == "", (
        "AC-003 verification FAILED: prewrite_gate now blocks doc/<area>/REQ__*.md outside an active task. "
        "The plan promise is broken; AC-003 must escalate from verification-only to a code change "
        f"in prewrite_gate.py. stdout={result.stdout!r}"
    )


def test_prewrite_gate_allows_req_doc_under_doc_harness(tmp_path):
    """Same property for REQ docs under doc/harness/ (the path this task uses for AC-006/007)."""
    repo = _harness_enabled_repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "Write",
        "tool_input": {
            "file_path": str(repo / "doc" / "harness" / "REQ__example.md"),
            "content": "# REQ - example\n",
        },
    }
    result = _gate(repo, payload)
    assert result.returncode == 0
    assert result.stdout == ""
