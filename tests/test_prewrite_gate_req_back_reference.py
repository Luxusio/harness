"""AC-001: prewrite_gate._task_has_req_reference accepts REQ-doc back-link.

A REQ created by req_scaffold.py with a task source records a body line
`- source: task: <task_id>` in the REQ doc (see plugin/scripts/req_scaffold.py).
Before this change, prewrite_gate's REQ-presence check only grepped
PLAN.md bodies for a REQ path string, so a task that correctly registered its
REQ by source back-link still had to mirror the REQ path into PLAN.md before
the first source Edit was allowed.

This test pins the new behavior: the gate must follow the back-link.
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))

from prewrite_gate import _task_has_req_reference  # noqa: E402


def _make_task_dir(repo: Path, task_id: str) -> Path:
    task_dir = repo / "doc" / "harness" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    return task_dir


def test_back_link_in_req_doc_counts_as_reference(tmp_path):
    repo = tmp_path / "repo"
    task_dir = _make_task_dir(repo, "TASK__example")
    req_dir = repo / "doc" / "ui"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__example-feature.md").write_text(
        "# REQ - example-feature\n\n"
        "status: accepted\n\n"
        "## Intent\nSomething observable.\n\n"
        "## Source\n"
        "- created: 2026-06-01\n"
        "- source: task: TASK__example\n",
        encoding="utf-8",
    )
    # No PLAN.md mirror.
    assert _task_has_req_reference(str(task_dir), str(repo)) is True


def test_no_req_no_body_grep_returns_false(tmp_path):
    repo = tmp_path / "repo"
    task_dir = _make_task_dir(repo, "TASK__solo")
    (repo / "doc").mkdir(parents=True, exist_ok=True)
    assert _task_has_req_reference(str(task_dir), str(repo)) is False


def test_original_body_grep_path_still_works(tmp_path):
    """Regression: PLAN.md mentioning a REQ path is still detected (original
    behavior preserved alongside the new back-link path)."""
    repo = tmp_path / "repo"
    task_dir = _make_task_dir(repo, "TASK__bodygrep")
    (task_dir / "PLAN.md").write_text(
        "# PLAN\n\nSee doc/ui/REQ__legacy.md for the contract.\n",
        encoding="utf-8",
    )
    # REQ file does not need to exist on disk — body grep is regex-only.
    assert _task_has_req_reference(str(task_dir), str(repo)) is True


def test_back_link_for_different_task_is_ignored(tmp_path):
    """A REQ doc linked to TASK__other must NOT satisfy the gate for
    TASK__mine — the back-link is identity-checked, not existence-checked."""
    repo = tmp_path / "repo"
    this_task = _make_task_dir(repo, "TASK__mine")
    req_dir = repo / "doc" / "api"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__other.md").write_text(
        "## Source\n- source: task: TASK__other\n",
        encoding="utf-8",
    )
    assert _task_has_req_reference(str(this_task), str(repo)) is False


def test_repo_root_inferred_from_task_dir_when_omitted(tmp_path):
    """Default repo_root falls back to task_dir/../../.. — pin that contract
    so callers that didn't migrate to the new signature still work."""
    repo = tmp_path / "repo"
    task_dir = _make_task_dir(repo, "TASK__inferred")
    req_dir = repo / "doc" / "common"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__inferred-link.md").write_text(
        "## Source\n- source: task: TASK__inferred\n",
        encoding="utf-8",
    )
    # Omit repo_root — function must compute it from task_dir.
    assert _task_has_req_reference(str(task_dir)) is True


def test_back_link_scan_skips_task_dir_tree(tmp_path):
    """The walker must skip doc/harness/tasks/** so a REQ-shaped file living
    inside another task's dir does not accidentally satisfy this task's gate."""
    repo = tmp_path / "repo"
    this_task = _make_task_dir(repo, "TASK__current")
    other_task = _make_task_dir(repo, "TASK__neighbor")
    (other_task / "REQ__decoy.md").write_text(
        "## Source\n- source: task: TASK__current\n",
        encoding="utf-8",
    )
    # No REQ outside the tasks tree — only the decoy inside.
    assert _task_has_req_reference(str(this_task), str(repo)) is False
