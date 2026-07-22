"""Global hooks must no-op in repos without doc/harness/manifest.yaml."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"


def _run(script: str, repo: Path, payload: dict | None = None, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO / "plugin")
    env.pop("HARNESS_SKIP_PREWRITE", None)
    env.pop("HARNESS_SKIP_MCP_GUARD", None)
    env.pop("HARNESS_SKIP_QA_DELEGATION", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=json.dumps(payload or {}),
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
    )


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _assert_no_harness_files(repo: Path) -> None:
    assert not (repo / "doc" / "harness").exists()


def test_mcp_bash_guard_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi > src/app.py"},
    }

    result = _run("mcp_bash_guard.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_qa_delegation_gate_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "mcp__chrome-devtools__take_snapshot",
        "tool_input": {},
    }

    result = _run("qa_delegation_gate.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_prewrite_gate_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    target = repo / "src" / "app.py"
    payload = {
        "cwd": str(repo),
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "x = 1\n"},
    }

    result = _run("prewrite_gate.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_prewrite_gate_does_not_inherit_outer_manifest_for_gitfile_worktree(tmp_path: Path):
    outer = _repo(tmp_path)
    manifest = outer / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: cli\n", encoding="utf-8")
    inner = outer / "plain-worktree"
    inner.mkdir()
    (inner / ".git").write_text(
        "gitdir: ../.git/worktrees/plain-worktree\n", encoding="utf-8",
    )
    target = inner / "src/app.py"
    payload = {
        "cwd": str(inner),
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "x = 1\n"},
    }

    result = _run("prewrite_gate.py", inner, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    assert not (inner / "doc/harness").exists()


def test_tool_routing_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_response": {"stderr": "bash: pytest: command not found"},
    }

    result = _run("tool_routing.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_stop_gate_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)

    result = _run("stop_gate.py", repo, {"cwd": str(repo)})

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_note_freshness_does_not_mutate_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    doc = repo / "doc" / "note.md"
    doc.parent.mkdir()
    doc.write_text(
        "---\n"
        "freshness: current\n"
        "invalidated_by_paths:\n"
        "  - src\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    before = doc.read_text(encoding="utf-8")

    result = _run("note_freshness.py", repo, None, "--paths", "src/app.py", "--quiet")

    assert result.returncode == 0
    assert result.stdout == ""
    assert doc.read_text(encoding="utf-8") == before
    _assert_no_harness_files(repo)
