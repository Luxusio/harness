#!/usr/bin/env python3
"""Packaged golden replay task fixtures.

This module keeps the golden replay corpus self-contained without depending on
live ``doc/harness/tasks`` runtime state. Fixtures are materialized into a
hidden overlay inside the repo so helper code still resolves the real repo
root, but path outputs can be normalized back to the logical task paths used by
historical replay cases.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import textwrap
from pathlib import Path
from typing import Any, Callable, Dict, Optional

import _lib  # type: ignore

OVERLAY_DIRNAME = ".harness-replay-fixtures"
TASK_STATE_SCHEMA_VERSION = 2
TASK_STATE_REVISION = 1
_BASE_TS = 1704067200  # 2024-01-01T00:00:00Z


@contextlib.contextmanager
def _pushd(path: str):
    previous = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _touch_many(task_dir: Path, mapping: Dict[str, int], *, base_ts: int = _BASE_TS) -> None:
    for relpath, offset in mapping.items():
        target = task_dir / relpath
        ts = base_ts + int(offset)
        os.utime(target, (ts, ts))


def _yaml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return str(value)


def _yaml_list(items: list[Any]) -> str:
    if not items:
        return "[]"
    rendered = ", ".join(str(item) for item in items)
    return f"[{rendered}]"


def _write_task_state(task_dir: Path, payload: Dict[str, Any]) -> None:
    ordered = {
        "schema_version": payload.pop("schema_version", TASK_STATE_SCHEMA_VERSION),
        "state_revision": payload.pop("state_revision", TASK_STATE_REVISION),
        "parent_revision": payload.pop("parent_revision", 0),
        **payload,
    }
    lines = []
    for key, value in ordered.items():
        if isinstance(value, list):
            lines.append(f"{key}: {_yaml_list(value)}")
        else:
            lines.append(f"{key}: {_yaml_scalar(value)}")
    _write(task_dir / "TASK_STATE.yaml", "\n".join(lines).rstrip() + "\n")


def _write_team_state_fields(task_dir: Path, *, provider: str = "omc", **overrides: Any) -> None:
    payload: Dict[str, Any] = {
        "task_id": task_dir.name,
        "status": "plan_passed",
        "lane": "build",
        "plan_verdict": "PASS",
        "runtime_verdict": "pending",
        "runtime_verdict_freshness": "current",
        "document_verdict": "pending",
        "document_verdict_freshness": "current",
        "doc_changes_detected": True,
        "execution_mode": "sprinted",
        "orchestration_mode": "team",
        "routing_compiled": True,
        "routing_source": "hctl",
        "team_provider": provider,
        "team_status": "planned",
        "team_plan_required": True,
        "team_synthesis_required": True,
        "fallback_used": "none",
        "mutates_repo": True,
        "roots_touched": ["app", "docs", "tests"],
        "touched_paths": ["app/main.ts", "docs/architecture.md", "tests/test_example.py"],
        "runtime_verdict_fail_count": 0,
    }
    payload.update(overrides)
    _write_task_state(task_dir, payload)


def _write_team_plan(task_dir: Path) -> None:
    _write(
        task_dir / "TEAM_PLAN.md",
        textwrap.dedent(
            """
            # Team Plan
            ## Worker Roster
            - lead: integrator
            - worker-a: app
            - reviewer: doc-reviewer

            ## Owned Writable Paths
            - lead: tests/**
            - worker-a: app/**
            - reviewer: docs/**

            ## Shared Read-Only Paths
            - api/**

            ## Forbidden Writes
            - lead: app/**, docs/**
            - worker-a: tests/**, docs/**
            - reviewer: tests/**, app/**

            ## Synthesis Strategy
            - lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md

            ## Documentation Ownership
            - writer: reviewer
            - critic-document: lead
            """
        ).strip()
        + "\n",
    )


def _write_worker_summary(task_dir: Path, worker_name: str, handled_path: str) -> None:
    rel_name = worker_name if worker_name.startswith("worker-") else f"worker-{worker_name}"
    _write(
        task_dir / "team" / f"{rel_name}.md",
        textwrap.dedent(
            f"""
            # Worker Summary
            ## Completed Work
            - finished slice

            ## Owned Paths Handled
            - {handled_path}

            ## Verification
            - pytest tests/test_example.py

            ## Residual Risks
            - none
            """
        ).strip()
        + "\n",
    )


def _write_non_stub_handoff(task_dir: Path) -> None:
    _write(
        task_dir / "HANDOFF.md",
        textwrap.dedent(
            """
            # Handoff

            ## Current state
            - integrated team result is ready for the next recovery phase

            ## Verification
            - pytest tests/test_example.py

            ## Next steps
            - follow the surfaced recovery command
            """
        ).strip()
        + "\n",
    )


def _write_doc_sync(task_dir: Path, *, meaningful: bool) -> None:
    if meaningful:
        what_changed = "- docs/architecture.md aligned with the verified implementation"
        updated = "- docs/architecture.md"
        notes = "- verified after final runtime QA"
    else:
        what_changed = "none"
        updated = "none"
        notes = "none"
    _write(
        task_dir / "DOC_SYNC.md",
        "# DOC_SYNC: task\n"
        "written_at: 2026-01-01T00:00:00Z\n\n"
        "## What changed\n"
        f"{what_changed}\n\n"
        "## New files\nnone\n\n"
        "## Updated files\n"
        f"{updated}\n\n"
        "## Deleted files\nnone\n\n"
        "## Notes\n"
        f"{notes}\n",
    )


def _write_team_synthesis(task_dir: Path) -> None:
    _write(
        task_dir / "TEAM_SYNTHESIS.md",
        textwrap.dedent(
            """
            # Team Synthesis
            ## Integrated Result
            - merged slices

            ## Cross-Checks
            - ownership respected

            ## Verification Summary
            - pytest tests/test_example.py

            ## Residual Risks
            - none
            """
        ).strip()
        + "\n",
    )


def _build_bootstrap_and_dispatch(repo_root: str, task_dir: Path) -> None:
    with _pushd(repo_root):
        _lib.build_team_bootstrap(str(task_dir), write_files=True)
        _lib.build_team_dispatch(str(task_dir), write_files=True)


def _write_ready_team_task(task_dir: Path, *, team_status: str, document_verdict: str, include_document_critic: bool) -> None:
    _write_team_state_fields(
        task_dir,
        provider="omc",
        team_status=team_status,
        document_verdict=document_verdict,
        runtime_verdict="PASS",
        runtime_verdict_freshness="current",
        document_verdict_freshness="current",
    )
    _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\nsummary: plan approved\n")
    _write_team_plan(task_dir)
    _write_worker_summary(task_dir, "lead", "tests/test_example.py")
    _write_worker_summary(task_dir, "worker-a", "app/main.ts")
    _write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
    _write_team_synthesis(task_dir)
    _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
    _write_doc_sync(task_dir, meaningful=True)
    if include_document_critic:
        _write(task_dir / "CRITIC__document.md", "verdict: PASS\nsummary: docs match the verified behavior\n")
    _write_non_stub_handoff(task_dir)


def _build_harness_schema_sync(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "created",
            "lane": "docs-sync",
            "browser_required": False,
            "runtime_verdict_fail_count": 0,
        },
    )
    _write(task_dir / "REQUEST.md", "Synchronize the harness schema documentation with the current implementation.\n")


def _build_fix_crlf_script(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "implemented",
            "lane": "build",
            "mutates_repo": True,
            "plan_verdict": "PASS",
            "runtime_verdict": "PASS",
            "runtime_verdict_freshness": "current",
            "document_verdict": "skipped",
            "document_verdict_freshness": "current",
            "doc_changes_detected": False,
            "execution_mode": "standard",
            "orchestration_mode": "subagents",
            "workflow_violations": [],
            "artifact_provenance_required": False,
            "directive_capture_state": "clean",
            "complaint_capture_state": "clean",
            "updated": "2026-01-01T00:00:00Z",
            "touched_paths": ["scripts/fix-diff.sh"],
            "roots_touched": ["scripts"],
            "verification_targets": ["scripts/fix-diff.sh"],
        },
    )
    _write(task_dir / "REQUEST.md", "Fix the CRLF normalization script so generated diffs stop breaking on Windows line endings.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
    _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: runtime verification passed\n")
    _write_doc_sync(task_dir, meaningful=False)
    _write_non_stub_handoff(task_dir)


def _build_cli_first_workflow(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "implemented",
            "lane": "build",
            "mutates_repo": True,
            "plan_verdict": "PASS",
            "runtime_verdict": "PASS",
            "runtime_verdict_freshness": "current",
            "document_verdict": "skipped",
            "document_verdict_freshness": "current",
            "doc_changes_detected": False,
            "execution_mode": "sprinted",
            "orchestration_mode": "subagents",
            "workflow_violations": [],
            "artifact_provenance_required": False,
            "directive_capture_state": "clean",
            "complaint_capture_state": "clean",
            "updated": "2026-01-01T00:00:00Z",
            "touched_paths": ["plugin/scripts/hctl.py", "plugin/docs/orchestration-modes.md"],
            "roots_touched": ["plugin"],
            "verification_targets": ["plugin/scripts/hctl.py"],
        },
    )
    _write(
        task_dir / "REQUEST.md",
        "Update CLAUDE.md, execution-modes guidance, and hctl workflow templates to keep the CLI-first control surface consistent.\n",
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
    _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: runtime verification passed\n")
    _write_doc_sync(task_dir, meaningful=False)
    _write_non_stub_handoff(task_dir)


def _build_launch_json_relocate(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "planned",
            "lane": "build",
            "risk_tags": ["multi-root"],
            "browser_required": False,
            "runtime_verdict_fail_count": 0,
        },
    )
    _write(
        task_dir / "REQUEST.md",
        "Implement a frontend launch flow, a backend API route to relocate launch JSON, and the corresponding tests.\n",
    )


def _build_task_created_gate(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "implemented",
            "lane": "build",
            "mutates_repo": True,
            "plan_verdict": "PASS",
            "runtime_verdict": "pending",
            "runtime_verdict_freshness": "current",
            "document_verdict": "skipped",
            "document_verdict_freshness": "current",
            "doc_changes_detected": True,
            "execution_mode": "standard",
            "orchestration_mode": "solo",
            "workflow_violations": [],
            "artifact_provenance_required": False,
            "directive_capture_state": "clean",
            "complaint_capture_state": "clean",
            "updated": "2026-01-01T00:00:00Z",
            "touched_paths": ["plugin/scripts/task_created_gate.py", "plugin/docs/task-created-gate.md"],
            "roots_touched": ["plugin", "docs"],
            "verification_targets": ["plugin/scripts/task_created_gate.py"],
            "agent_run_critic_document_count": 0,
        },
    )
    _write(task_dir / "REQUEST.md", "Tighten the task-created gate prefix filter and refresh the related docs.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
    _write_doc_sync(task_dir, meaningful=False)
    _write_non_stub_handoff(task_dir)


def _build_broad_build_support_portal(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "created",
            "lane": "build",
            "browser_required": False,
            "runtime_verdict_fail_count": 0,
        },
    )
    _write(
        task_dir / "REQUEST.md",
        "Build a customer support portal from scratch with a shared inbox, knowledge base, and responsive admin workspace.\n",
    )


def _build_criterion_reopen_recovery(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "in_progress",
            "lane": "build",
            "execution_mode": "standard",
            "runtime_verdict_fail_count": 0,
            "roots_touched": ["app", "tests"],
            "touched_paths": ["app/main.ts", "tests/test_example.py"],
        },
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(
        task_dir / "CHECKS.yaml",
        textwrap.dedent(
            """
            close_gate: standard
            checks:
              - id: AC-001
                title: user can save the edited draft
                status: failed
                kind: functional
                evidence_refs: []
                reopen_count: 2
                last_updated: "2026-01-01T00:00:00Z"
              - id: AC-002
                title: docs mention the keyboard shortcut
                status: passed
                kind: doc
                evidence_refs: []
                reopen_count: 0
                last_updated: "2026-01-01T00:00:00Z"
            """
        ).strip()
        + "\n",
    )


def _build_blocked_env_recovery(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "blocked_env",
            "lane": "build",
            "execution_mode": "sprinted",
            "blockers": "missing playwright browsers",
            "roots_touched": ["app", "tests"],
            "touched_paths": ["app/main.tsx", "tests/e2e/refund.spec.ts"],
            "runtime_verdict_fail_count": 0,
        },
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "ENVIRONMENT_SNAPSHOT.md", "# Environment Snapshot\n- browsers missing\n")


def _build_team_doc_recovery(task_dir: Path, repo_root: str) -> None:
    _write_task_state(
        task_dir,
        {
            "task_id": task_dir.name,
            "status": "in_progress",
            "lane": "build",
            "plan_verdict": "PASS",
            "execution_mode": "standard",
            "orchestration_mode": "team",
            "team_provider": "omc",
            "team_status": "planned",
            "team_plan_required": True,
            "team_synthesis_required": True,
            "fallback_used": "none",
            "mutates_repo": True,
            "runtime_verdict": "PASS",
            "document_verdict": "skipped",
            "doc_changes_detected": True,
            "roots_touched": ["app", "docs"],
            "touched_paths": ["app/main.py", "docs/architecture.md"],
            "runtime_verdict_fail_count": 2,
        },
    )
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write(task_dir / "HANDOFF.md", "# Handoff\n\n## Verification\n- pytest tests/test_example.py\n")
    _write_team_plan(task_dir)
    _write_worker_summary(task_dir, "worker-a", "app/main.py")
    _write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
    _write(
        task_dir / "TEAM_SYNTHESIS.md",
        "# Team Synthesis\n## Integrated Result\n- merged app and docs slices\n\n## Cross-Checks\n- ownership respected\n\n## Verification Summary\n- pytest tests/test_example.py\n\n## Residual Risks\n- none\n",
    )
    _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
    _write_doc_sync(task_dir, meaningful=True)


def _build_team_launch_native_replay(task_dir: Path, repo_root: str) -> None:
    _write_team_state_fields(task_dir, provider="native")
    _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write_team_plan(task_dir)
    _build_bootstrap_and_dispatch(repo_root, task_dir)


def _build_team_launch_stale_replay(task_dir: Path, repo_root: str) -> None:
    _write_team_state_fields(task_dir, provider="omc")
    _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write_team_plan(task_dir)
    with _pushd(repo_root):
        _lib.build_team_bootstrap(str(task_dir), write_files=True)
        _lib.build_team_dispatch(str(task_dir), write_files=True)
        _lib.build_team_launch(str(task_dir), write_files=True, auto_refresh=False)
    launch_path = task_dir / "team" / "bootstrap" / "provider" / "launch.json"
    payload = json.loads(launch_path.read_text(encoding="utf-8"))
    payload["launch_signature"] = "stale-signature"
    launch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _build_team_synthesis_recovery(task_dir: Path, repo_root: str) -> None:
    _write_team_state_fields(task_dir, provider="omc")
    _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
    _write(task_dir / "PLAN.md", "# Plan\n")
    _write_team_plan(task_dir)
    _write_worker_summary(task_dir, "lead", "tests/test_example.py")
    _write_worker_summary(task_dir, "worker-a", "app/main.ts")
    _write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
    _build_bootstrap_and_dispatch(repo_root, task_dir)


def _build_team_documentation_review_replay(task_dir: Path, repo_root: str) -> None:
    _write_ready_team_task(
        task_dir,
        team_status="running",
        document_verdict="pending",
        include_document_critic=False,
    )
    _write_team_state_fields(
        task_dir,
        provider="omc",
        team_status="running",
        document_verdict="pending",
        runtime_verdict="PASS",
        runtime_verdict_freshness="current",
        document_verdict_freshness="current",
        runtime_verdict_fail_count=2,
    )
    _build_bootstrap_and_dispatch(repo_root, task_dir)
    _touch_many(
        task_dir,
        {
            "CRITIC__plan.md": 5,
            "TEAM_PLAN.md": 10,
            "team/worker-lead.md": 20,
            "team/worker-a.md": 30,
            "team/worker-reviewer.md": 40,
            "TEAM_SYNTHESIS.md": 50,
            "CRITIC__runtime.md": 60,
            "DOC_SYNC.md": 70,
            "HANDOFF.md": 71,
        },
    )


def _build_team_handoff_refresh_replay(task_dir: Path, repo_root: str) -> None:
    _write_ready_team_task(
        task_dir,
        team_status="complete",
        document_verdict="PASS",
        include_document_critic=True,
    )
    _write_team_state_fields(
        task_dir,
        provider="omc",
        team_status="complete",
        document_verdict="PASS",
        runtime_verdict="PASS",
        runtime_verdict_freshness="current",
        document_verdict_freshness="current",
        runtime_verdict_fail_count=2,
    )
    _build_bootstrap_and_dispatch(repo_root, task_dir)
    _touch_many(
        task_dir,
        {
            "CRITIC__plan.md": 5,
            "TEAM_PLAN.md": 10,
            "team/worker-lead.md": 20,
            "team/worker-a.md": 30,
            "team/worker-reviewer.md": 40,
            "TEAM_SYNTHESIS.md": 50,
            "CRITIC__runtime.md": 60,
            "DOC_SYNC.md": 70,
            "HANDOFF.md": 75,
            "CRITIC__document.md": 80,
        },
    )


def _build_team_degraded_refresh_replay(task_dir: Path, repo_root: str) -> None:
    _write_ready_team_task(
        task_dir,
        team_status="degraded",
        document_verdict="PASS",
        include_document_critic=True,
    )
    _write_team_state_fields(
        task_dir,
        provider="omc",
        team_status="degraded",
        runtime_verdict="PASS",
        runtime_verdict_freshness="current",
        document_verdict="PASS",
        document_verdict_freshness="current",
        doc_changes_detected=False,
        runtime_verdict_fail_count=2,
    )
    _touch_many(
        task_dir,
        {
            "CRITIC__plan.md": 5,
            "TEAM_PLAN.md": 10,
            "team/worker-lead.md": 20,
            "team/worker-a.md": 30,
            "team/worker-reviewer.md": 40,
            "TEAM_SYNTHESIS.md": 50,
            "CRITIC__runtime.md": 60,
            "DOC_SYNC.md": 70,
            "CRITIC__document.md": 80,
            "HANDOFF.md": 90,
            "TASK_STATE.yaml": 100,
        },
    )
    _build_bootstrap_and_dispatch(repo_root, task_dir)


FIXTURE_BUILDERS: Dict[str, Callable[[Path, str], None]] = {
    "doc/harness/tasks/TASK__harness-schema-sync": _build_harness_schema_sync,
    "doc/harness/tasks/TASK__fix-crlf-script": _build_fix_crlf_script,
    "doc/harness/tasks/TASK__cli-first-workflow-v1": _build_cli_first_workflow,
    "doc/harness/tasks/TASK__launch-json-relocate": _build_launch_json_relocate,
    "doc/harness/tasks/TASK__task-created-gate-prefix-filter": _build_task_created_gate,
    "doc/harness/tasks/TASK__broad-build-support-portal": _build_broad_build_support_portal,
    "doc/harness/tasks/TASK__criterion-reopen-recovery": _build_criterion_reopen_recovery,
    "doc/harness/tasks/TASK__blocked-env-recovery": _build_blocked_env_recovery,
    "doc/harness/tasks/TASK__team-doc-recovery": _build_team_doc_recovery,
    "doc/harness/tasks/TASK__team-launch-native-replay": _build_team_launch_native_replay,
    "doc/harness/tasks/TASK__team-launch-stale-replay": _build_team_launch_stale_replay,
    "doc/harness/tasks/TASK__team-synthesis-recovery": _build_team_synthesis_recovery,
    "doc/harness/tasks/TASK__team-documentation-review-replay": _build_team_documentation_review_replay,
    "doc/harness/tasks/TASK__team-handoff-refresh-replay": _build_team_handoff_refresh_replay,
    "doc/harness/tasks/TASK__team-degraded-refresh-replay": _build_team_degraded_refresh_replay,
}


class ReplayFixtureManager:
    """Materialize packaged golden replay fixtures into a hidden repo overlay."""

    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        self.overlay_root = os.path.join(self.repo_root, OVERLAY_DIRNAME)
        self._materialized: Dict[str, Dict[str, Any]] = {}

    def cleanup(self) -> None:
        shutil.rmtree(self.overlay_root, ignore_errors=True)

    def materialize(self, logical_task_dir: str) -> Optional[Dict[str, Any]]:
        logical = str(logical_task_dir or "").strip()
        if not logical:
            return None
        if logical in self._materialized:
            return self._materialized[logical]
        builder = FIXTURE_BUILDERS.get(logical)
        if builder is None:
            return None
        actual = Path(self.overlay_root) / Path(logical)
        if actual.exists():
            shutil.rmtree(actual, ignore_errors=True)
        actual.parent.mkdir(parents=True, exist_ok=True)
        builder(actual, self.repo_root)
        actual_abs = str(actual.resolve())
        actual_rel = os.path.relpath(actual_abs, self.repo_root)
        info = {
            "logical_task_dir": logical,
            "actual_task_dir": actual_abs,
            "path_aliases": {
                actual_abs: logical,
                actual_rel: logical,
            },
            "source": "packaged-fixture-overlay",
        }
        self._materialized[logical] = info
        return info
