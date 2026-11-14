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

import json
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")


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


def _wiki_manifest(repo, mode):
    manifest = {"version": 7, "type": "library", "workflow": {"document_review": "self"},
                "capabilities": {"strict_compliance_requires_delegation": True}}
    if mode != "local":
        manifest["wiki"] = {"backend": "atlassian", "enabled": True,
                            "site": "https://fixture.invalid" if mode == "external" else "invalid",
                            "spaces": [{"key": "TEAM"}]}
    (repo / "doc/harness/manifest.yaml").write_text(json.dumps(manifest))


@pytest.mark.parametrize("mode", ["external", "unavailable"])
@pytest.mark.parametrize("metadata,expected", [
    ("REQ: sync_pending\n", True), ("REQ: confluence:123@4\n", True),
    ("REQ: sync_pending_extra\n", False), ("REQ: sync_pending extra\n", False),
    ("Example REQ: sync_pending\n", False), ("> REQ: sync_pending\n", False),
    ("    REQ: sync_pending\n", False), ("REQ: confluence:123@0\n", False),
    ("```text\nREQ: sync_pending\n```\n", False),
    ("~~~~example\nREQ: sync_pending\n~~~~\n", False),
    ("```text\nREQ: confluence:123@4\n```\n", False), ("", False),
])
def test_external_reference_intent_is_explicit_bounded_metadata(tmp_path, monkeypatch, mode, metadata, expected):
    repo = tmp_path / "repo"
    td = _make_wbs_dir(repo, "WBS__example")
    _wiki_manifest(repo, mode)
    monkeypatch.setenv("ATLASSIAN_BASE_URL", "https://fixture.invalid")
    monkeypatch.setenv("ATLASSIAN_EMAIL", "fixture@example.invalid")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "fixture-token")
    (td / "PLAN.md").write_text("# Plan\n" + metadata)
    assert _wbs_has_req_reference(str(td), str(repo)) is expected


@pytest.mark.parametrize("kind", ["missing", "oversized", "invalid_utf8", "symlink"])
def test_external_reference_does_not_accept_unsafe_plan(tmp_path, kind):
    repo = tmp_path / "repo"
    td = _make_wbs_dir(repo, "WBS__example")
    _wiki_manifest(repo, "unavailable")
    path = td / "PLAN.md"
    if kind == "oversized":
        path.write_text("REQ: sync_pending\n" + "x" * (1024 * 1024))
    elif kind == "invalid_utf8":
        path.write_bytes(b"REQ: sync_pending\n\xff")
    elif kind == "symlink":
        target = repo / "other.md"
        target.write_text("REQ: sync_pending\n")
        path.symlink_to(target)
    assert not _wbs_has_req_reference(str(td), str(repo))


@pytest.mark.parametrize("mode,metadata,assignment,allowed", [
    ("external", "REQ: sync_pending", "current", True),
    ("unavailable", "REQ: sync_pending", "current", True),
    ("unavailable", "REQ: confluence:123@4", "current", True),
    ("local", "REQ: sync_pending", "current", False),
    ("external", "REQ: sync_pending extra", "current", False),
    ("unavailable", "```text\nREQ: sync_pending\n```", "current", False),
    ("unavailable", "<!-- Example only\nREQ: sync_pending\n-->", "current", False),
    ("unavailable", "<!-- REQ: sync_pending -->", "current", False),
    ("unavailable", "<!-- Example only\nREQ: sync_pending", "current", False),
    ("unavailable", "<!-- Example only\nREQ: sync_pending\n-->\nREQ: sync_pending", "current", True),
    ("unavailable", "<!-- Example only -->REQ: sync_pending", "current", True),
    ("unavailable", "```text\n<!-- literal\nREQ: sync_pending\n```\nREQ: sync_pending", "current", True),
    ("unavailable", "```text <!-- literal\nREQ: sync_pending\n```\nREQ: sync_pending", "current", True),
    ("unavailable", "", "current", False),
    ("unavailable", "REQ: sync_pending", "missing", False),
    ("unavailable", "REQ: sync_pending", "outside", False),
    ("unavailable", "REQ: sync_pending", "stale", False),
])
def test_actual_prewrite_pending_reference_preserves_source_assignment(
        tmp_path, mode, metadata, assignment, allowed):
    from conftest import invoke_hook, parse_decision
    from test_harness_mcp_server import _write_marker_fixture
    from test_managed_wbs import host_snapshot
    from test_wbs import node
    from test_wbs_input_policy import policy_plan, public
    repo, td, call, _ = public(tmp_path)
    _wiki_manifest(repo, mode)
    text = ("# Approved feature\nSource: current user request to implement this feature.\n"
            "Acceptance: feature has the requested observable behavior.\n"
            "Document next action: publish the selected REQ when the service returns.\n"
            + metadata + "\n" + policy_plan([node()], {"source": ["src.txt"]}))
    assert not call("plan", plan=text).get("isError")
    path = "web/pages/feature.tsx"
    if assignment != "missing":
        ownership = "other.py" if assignment == "outside" else path
        prepared = call("dispatch", operation="prepare", ownership={"A": [ownership]}, host=host_snapshot())
        assert not prepared.get("isError"), prepared
    sid = "req-pending-hook"
    _write_marker_fixture(str(repo), str(td), sid)
    if assignment == "stale":
        marker = repo / "doc/harness/wbs/.active_sessions" / (sid + ".json")
        value = json.loads(marker.read_text())
        from _lib import new_uuid7
        value["run_id"] = new_uuid7()
        marker.write_text(json.dumps(value))
    before = (td / "WBS.control.json").read_bytes()
    result = invoke_hook(str(SCRIPTS / "prewrite_gate.py"), "Write", {"file_path": str(repo / path)},
        cwd=str(repo), env_extra={"HARNESS_SESSION_ID": sid, "HARNESS_SKIP_PREWRITE": "0",
            "ATLASSIAN_BASE_URL": "https://fixture.invalid", "ATLASSIAN_EMAIL": "fixture@example.invalid",
            "ATLASSIAN_API_TOKEN": "fixture-token"})
    assert result.returncode == 0, result.stderr
    decision, reason = parse_decision(result.stdout)
    assert (decision != "deny") is allowed, (result.stdout, result.stderr)
    if not allowed and assignment in {"missing", "outside"}:
        assert "wbs-execution-assignment" in reason
    assert (td / "WBS.control.json").read_bytes() == before
