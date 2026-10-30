"""AC-001: prewrite_gate._wbs_has_req_reference accepts REQ-doc back-link.

A REQ created by req_scaffold.py with a task source records a body line
`- source: wbs: <wbs_id>` in the REQ doc (see plugin/scripts/req_scaffold.py).
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

from prewrite_gate import _wbs_has_req_reference  # noqa: E402


def _make_wbs_dir(repo: Path, wbs_id: str) -> Path:
    wbs_dir = repo / "doc" / "harness" / "wbs" / wbs_id
    wbs_dir.mkdir(parents=True)
    return wbs_dir


def test_back_link_in_req_doc_counts_as_reference(tmp_path):
    repo = tmp_path / "repo"
    wbs_dir = _make_wbs_dir(repo, "WBS__example")
    req_dir = repo / "doc" / "ui"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__example-feature.md").write_text(
        "# REQ - example-feature\n\n"
        "status: accepted\n\n"
        "## Intent\nSomething observable.\n\n"
        "## Source\n"
        "- created: 2026-06-01\n"
        "- source: wbs: WBS__example\n",
        encoding="utf-8",
    )
    # No PLAN.md mirror.
    assert _wbs_has_req_reference(str(wbs_dir), str(repo)) is True


def test_no_req_no_body_grep_returns_false(tmp_path):
    repo = tmp_path / "repo"
    wbs_dir = _make_wbs_dir(repo, "WBS__solo")
    (repo / "doc").mkdir(parents=True, exist_ok=True)
    assert _wbs_has_req_reference(str(wbs_dir), str(repo)) is False


def test_original_body_grep_path_still_works(tmp_path):
    """Regression: PLAN.md mentioning a REQ path is still detected (original
    behavior preserved alongside the new back-link path)."""
    repo = tmp_path / "repo"
    wbs_dir = _make_wbs_dir(repo, "WBS__bodygrep")
    (wbs_dir / "PLAN.md").write_text(
        "# PLAN\n\nSee doc/ui/REQ__legacy.md for the contract.\n",
        encoding="utf-8",
    )
    # REQ file does not need to exist on disk — body grep is regex-only.
    assert _wbs_has_req_reference(str(wbs_dir), str(repo)) is True


def test_back_link_for_different_task_is_ignored(tmp_path):
    """A REQ doc linked to WBS__other must NOT satisfy the gate for
    WBS__mine — the back-link is identity-checked, not existence-checked."""
    repo = tmp_path / "repo"
    this_task = _make_wbs_dir(repo, "WBS__mine")
    req_dir = repo / "doc" / "api"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__other.md").write_text(
        "## Source\n- source: wbs: WBS__other\n",
        encoding="utf-8",
    )
    assert _wbs_has_req_reference(str(this_task), str(repo)) is False


def test_repo_root_inferred_from_wbs_dir_when_omitted(tmp_path):
    """Default repo_root falls back to wbs_dir/../../.. — pin that contract
    so callers that didn't migrate to the new signature still work."""
    repo = tmp_path / "repo"
    wbs_dir = _make_wbs_dir(repo, "WBS__inferred")
    req_dir = repo / "doc" / "common"
    req_dir.mkdir(parents=True)
    (req_dir / "REQ__inferred-link.md").write_text(
        "## Source\n- source: wbs: WBS__inferred\n",
        encoding="utf-8",
    )
    # Omit repo_root — function must compute it from wbs_dir.
    assert _wbs_has_req_reference(str(wbs_dir)) is True


def test_back_link_scan_skips_wbs_dir_tree(tmp_path):
    """The walker must skip doc/harness/wbs/** so a REQ-shaped file living
    inside another task's dir does not accidentally satisfy this task's gate."""
    repo = tmp_path / "repo"
    this_task = _make_wbs_dir(repo, "WBS__current")
    other_task = _make_wbs_dir(repo, "WBS__neighbor")
    (other_task / "REQ__decoy.md").write_text(
        "## Source\n- source: wbs: WBS__current\n",
        encoding="utf-8",
    )
    # No REQ outside the tasks tree — only the decoy inside.
    assert _wbs_has_req_reference(str(this_task), str(repo)) is False
