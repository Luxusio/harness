#!/usr/bin/env python3
"""Tests for the golden replay runner."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import shutil
import tempfile
import textwrap
import unittest
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"
os.environ["HARNESS_SKIP_PREREAD"] = "1"

import _lib  # type: ignore  # noqa: E402
from golden_replay import default_corpus_path, execute_replay  # type: ignore  # noqa: E402


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


@contextlib.contextmanager
def _pushd(path: Path):
    previous = Path.cwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(previous)


def _write_team_state(task_dir: Path, *, provider: str = "omc", extra: str = "") -> None:
    _write(
        task_dir / "TASK_STATE.yaml",
        textwrap.dedent(
            f"""
            task_id: {task_dir.name}
            status: plan_passed
            lane: build
            plan_verdict: PASS
            runtime_verdict: pending
            runtime_verdict_freshness: current
            document_verdict: pending
            document_verdict_freshness: current
            doc_changes_detected: true
            execution_mode: sprinted
            orchestration_mode: team
            routing_compiled: true
            routing_source: hctl
            team_provider: {provider}
            team_status: planned
            team_plan_required: true
            team_synthesis_required: true
            fallback_used: none
            mutates_repo: true
            roots_touched: [app, docs, tests]
            touched_paths: [app/main.ts, docs/architecture.md, tests/test_example.py]
            {extra}
            """
        ).strip()
        + "\n",
    )



def _write_team_state_fields(task_dir: Path, *, provider: str = "omc", **overrides: object) -> None:
    payload: dict[str, object] = {
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
    }
    payload.update(overrides)
    _write(task_dir / "TASK_STATE.yaml", yaml.safe_dump(payload, sort_keys=False))


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


def _touch_many(task_dir: Path, mapping: dict[str, int], *, base_ts: int = 1704067200) -> None:
    for relpath, offset in mapping.items():
        target = task_dir / relpath
        os.utime(target, (base_ts + int(offset), base_ts + int(offset)))


class TestGoldenReplayPackagedCorpus(unittest.TestCase):
    def test_packaged_corpus_passes(self):
        report = execute_replay(
            corpus_path=default_corpus_path(str(REPO_ROOT)),
            repo_root=str(REPO_ROOT),
        )
        summary = report.get("summary") or {}
        self.assertGreater(summary.get("total", 0), 0, report)
        self.assertEqual(summary.get("failed"), 0, report)


class TestGoldenReplayCaseKinds(unittest.TestCase):
    def _make_repo(self) -> Path:
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        _write(
            tmp / "doc" / "harness" / "manifest.yaml",
            textwrap.dedent(
                """
                name: temp-golden-replay
                type: app
                project_meta:
                  shape: app
                registered_roots:
                  - common
                qa:
                  default_mode: cli
                profiles:
                  observability_enabled: false
                teams:
                  provider: auto
                  fallback: subagents
                  default_size: 3
                """
            ).strip()
            + "\n",
        )
        return tmp

    def test_routing_case_respects_probe_override(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__multi-surface"
        _write(
            task_dir / "TASK_STATE.yaml",
            textwrap.dedent(
                """
                task_id: TASK__multi-surface
                status: planned
                lane: build
                risk_tags: [multi-surface]
                browser_required: false
                runtime_verdict_fail_count: 0
                """
            ).strip()
            + "\n",
        )
        _write(
            task_dir / "REQUEST.md",
            "Implement a frontend dashboard, backend API route, and test coverage for the new billing flow.\n",
        )

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "route-team-available",
                    "kind": "routing",
                    "task_dir": "doc/harness/tasks/TASK__multi-surface",
                    "provider_probe": {"native_ready": True, "omc_ready": False},
                    "expect": {
                        "risk_level": "medium",
                        "execution_mode": "standard",
                        "orchestration_mode": "team",
                        "team_provider": "native",
                        "team_status": "planned"
                    }
                }
            ]
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_close_gate_case_matches_required_substrings(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__blocked"
        _write(
            task_dir / "TASK_STATE.yaml",
            textwrap.dedent(
                """
                task_id: TASK__blocked
                status: implemented
                lane: build
                mutates_repo: true
                plan_verdict: PASS
                runtime_verdict: pending
                runtime_verdict_freshness: current
                document_verdict: skipped
                document_verdict_freshness: current
                doc_changes_detected: true
                execution_mode: standard
                orchestration_mode: solo
                workflow_violations: []
                artifact_provenance_required: false
                directive_capture_state: clean
                complaint_capture_state: clean
                updated: 2026-01-01T00:00:00Z
                touched_paths: ["src/app.py"]
                roots_touched: ["src"]
                verification_targets: ["src/app.py"]
                """
            ).strip()
            + "\n",
        )
        _write(task_dir / "PLAN.md", "# Plan\n")
        _write(task_dir / "CRITIC__plan.md", "verdict: PASS\n")
        _write(task_dir / "HANDOFF.md", "# Handoff\n## Current state\nblocked\n")
        _write(task_dir / "DOC_SYNC.md", "none\n")

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "close-blocked",
                    "kind": "close_gate",
                    "task_dir": "doc/harness/tasks/TASK__blocked",
                    "expect": {
                        "blocked": True,
                        "required_substrings": [
                            "runtime_verdict is 'pending'",
                            "document_verdict is 'skipped'",
                            "doc changes detected"
                        ]
                    }
                }
            ]
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_prompt_notes_case_checks_primary_note(self):
        repo = self._make_repo()
        _write(
            repo / "doc" / "common" / "REQ__policy.md",
            textwrap.dedent(
                """
                # REQ policy
                summary: requirement note
                freshness: current

                protected artifact writes should use CLI tool
                """
            ).strip()
            + "\n",
        )
        _write(
            repo / "doc" / "common" / "OBS__policy.md",
            textwrap.dedent(
                """
                # OBS policy
                summary: observation note
                freshness: current

                protected artifact writes should use CLI tool
                """
            ).strip()
            + "\n",
        )
        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "prompt-policy",
                    "kind": "prompt_notes",
                    "prompt": "protected artifact writes should use CLI tool",
                    "expect": {
                        "primary": "REQ__policy.md",
                        "primary_root": "common",
                        "count": 2
                    }
                }
            ]
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_next_step_case_contains_expected_text(self):
        repo = self._make_repo()
        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "next-implemented",
                    "kind": "next_step",
                    "status": "implemented",
                    "expect": {"contains": "critic-runtime"}
                }
            ]
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_handoff_case_surfaces_blocked_env_snapshot(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__blocked-env"
        _write(
            task_dir / "TASK_STATE.yaml",
            textwrap.dedent(
                """
                task_id: TASK__blocked-env
                status: blocked_env
                lane: build
                execution_mode: sprinted
                blockers: missing playwright browsers
                roots_touched: [app, tests]
                touched_paths: [app/main.tsx, tests/e2e/refund.spec.ts]
                runtime_verdict_fail_count: 0
                """
            ).strip()
            + "\n",
        )
        _write(task_dir / "PLAN.md", "# Plan\n")
        _write(task_dir / "ENVIRONMENT_SNAPSHOT.md", "# Environment Snapshot\n- browsers missing\n")

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "handoff-blocked-env",
                    "kind": "handoff",
                    "task_dir": "doc/harness/tasks/TASK__blocked-env",
                    "expect": {
                        "exists": True,
                        "trigger": "blocked_env_reentry",
                        "next_step_contains": "ENVIRONMENT_SNAPSHOT.md",
                        "files_to_read_first_contains": ["ENVIRONMENT_SNAPSHOT.md", "PLAN.md", "TASK_STATE.yaml"],
                    },
                }
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_handoff_case_checks_team_documentation_phase(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__team-docs"
        _write(
            task_dir / "TASK_STATE.yaml",
            textwrap.dedent(
                """
                task_id: TASK__team-docs
                status: in_progress
                lane: build
                plan_verdict: PASS
                execution_mode: standard
                orchestration_mode: team
                team_provider: omc
                team_status: planned
                team_plan_required: true
                team_synthesis_required: true
                fallback_used: none
                mutates_repo: true
                runtime_verdict: PASS
                document_verdict: skipped
                doc_changes_detected: true
                roots_touched: [app, docs]
                touched_paths: [app/main.py, docs/architecture.md]
                """
            ).strip()
            + "\n",
        )
        _write(task_dir / "PLAN.md", "# Plan\n")
        _write(task_dir / "HANDOFF.md", "# Handoff\n")
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
        _write(
            task_dir / "team" / "worker-a.md",
            "# Worker Summary\n## Completed Work\n- done\n\n## Owned Paths Handled\n- app/main.py\n\n## Verification\n- pytest tests/test_example.py\n\n## Residual Risks\n- none\n",
        )
        _write(
            task_dir / "team" / "worker-reviewer.md",
            "# Worker Summary\n## Completed Work\n- done\n\n## Owned Paths Handled\n- docs/architecture.md\n\n## Verification\n- docs lint clean\n\n## Residual Risks\n- none\n",
        )
        _write(
            task_dir / "TEAM_SYNTHESIS.md",
            "# Team Synthesis\n## Integrated Result\n- merged app and docs slices\n\n## Cross-Checks\n- ownership respected\n\n## Verification Summary\n- pytest tests/test_example.py\n\n## Residual Risks\n- none\n",
        )
        _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
        _write(
            task_dir / "DOC_SYNC.md",
            "# DOC_SYNC: task\nwritten_at: 2026-01-01T00:00:00Z\n\n## What changed\n- docs/architecture.md aligned with the verified implementation\n\n## New files\nnone\n\n## Updated files\n- docs/architecture.md\n\n## Deleted files\nnone\n\n## Notes\n- verified after final runtime QA\n",
        )

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "handoff-team-docs",
                    "kind": "handoff",
                    "task_dir": "doc/harness/tasks/TASK__team-docs",
                    "trigger": "runtime_fail_repeat",
                    "expect": {
                        "exists": True,
                        "trigger": "runtime_fail_repeat",
                        "next_step_contains": "CRITIC__document.md",
                        "files_to_read_first_contains": [
                            "TEAM_PLAN.md",
                            "team/worker-a.md",
                            "team/worker-reviewer.md",
                            "TEAM_SYNTHESIS.md",
                            "CRITIC__runtime.md",
                            "DOC_SYNC.md",
                        ],
                        "team_recovery": {
                            "phase": "documentation",
                            "pending_artifacts_contains": ["CRITIC__document.md"],
                            "doc_sync_owners": ["reviewer"],
                            "document_critic_owners": ["lead"],
                        },
                    },
                }
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)


    def test_context_case_surfaces_team_launch_and_relaunch_state(self):
        repo = self._make_repo()
        native_task = repo / "doc" / "harness" / "tasks" / "TASK__team-native"
        _write_team_state(native_task, provider="native")
        _write(native_task / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(native_task / "PLAN.md", "# Plan\n")
        _write_team_plan(native_task)

        synthesis_task = repo / "doc" / "harness" / "tasks" / "TASK__team-synthesis"
        _write_team_state(synthesis_task, provider="omc")
        _write(synthesis_task / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(synthesis_task / "PLAN.md", "# Plan\n")
        _write_team_plan(synthesis_task)
        _write_worker_summary(synthesis_task, "lead", "tests/test_example.py")
        _write_worker_summary(synthesis_task, "worker-a", "app/main.ts")
        _write_worker_summary(synthesis_task, "reviewer", "docs/architecture.md")

        with _pushd(repo):
            _lib.build_team_bootstrap(str(native_task), write_files=True)
            _lib.build_team_dispatch(str(native_task), write_files=True)
            _lib.build_team_bootstrap(str(synthesis_task), write_files=True)
            _lib.build_team_dispatch(str(synthesis_task), write_files=True)

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "context-team-native",
                    "kind": "context",
                    "task_dir": "doc/harness/tasks/TASK__team-native",
                    "provider_probe": {"native_ready": True, "claude_available": True},
                    "expect": {
                        "next_action_contains": "native lead prompt",
                        "team": {
                            "launch_available": True,
                            "launch_generated": False,
                            "launch_interactive_required": True,
                            "launch_execute_target": "implementers",
                            "launch_execute_fallback_available": True,
                        },
                    },
                },
                {
                    "id": "context-team-synthesis",
                    "kind": "context",
                    "task_dir": "doc/harness/tasks/TASK__team-synthesis",
                    "expect": {
                        "next_action_contains": "TEAM_SYNTHESIS.md",
                        "team": {
                            "relaunch_available": True,
                            "relaunch_worker": "lead",
                            "relaunch_phase": "synthesis",
                        },
                    },
                },
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_team_launch_case_detects_stale_manifest_and_native_fallback(self):
        repo = self._make_repo()
        stale_task = repo / "doc" / "harness" / "tasks" / "TASK__team-launch-stale"
        _write_team_state(stale_task, provider="omc")
        _write(stale_task / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(stale_task / "PLAN.md", "# Plan\n")
        _write_team_plan(stale_task)

        native_task = repo / "doc" / "harness" / "tasks" / "TASK__team-launch-native"
        _write_team_state(native_task, provider="native")
        _write(native_task / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(native_task / "PLAN.md", "# Plan\n")
        _write_team_plan(native_task)

        with _pushd(repo):
            _lib.build_team_bootstrap(str(stale_task), write_files=True)
            _lib.build_team_dispatch(str(stale_task), write_files=True)
            _lib.build_team_launch(str(stale_task), write_files=True, auto_refresh=False)
            launch_path = stale_task / "team" / "bootstrap" / "provider" / "launch.json"
            payload = json.loads(launch_path.read_text(encoding="utf-8"))
            payload["launch_signature"] = "stale-signature"
            launch_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

            _lib.build_team_bootstrap(str(native_task), write_files=True)
            _lib.build_team_dispatch(str(native_task), write_files=True)

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "team-launch-stale",
                    "kind": "team_launch",
                    "task_dir": "doc/harness/tasks/TASK__team-launch-stale",
                    "provider_probe": {"omc_ready": True},
                    "expect": {
                        "provider": "omc",
                        "target": "provider",
                        "generated": True,
                        "stale": True,
                        "refresh_needed": True,
                        "reason_contains": "out of date",
                    },
                },
                {
                    "id": "team-launch-native",
                    "kind": "team_launch",
                    "task_dir": "doc/harness/tasks/TASK__team-launch-native",
                    "provider_probe": {"native_ready": True, "claude_available": True},
                    "expect": {
                        "provider": "native",
                        "target": "provider",
                        "generated": False,
                        "interactive_required": True,
                        "execute_target": "implementers",
                        "execute_fallback_available": True,
                        "reason_contains": "not been generated yet",
                        "execute_resolution_reason_contains": "falling back",
                    },
                },
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_team_relaunch_case_selects_synthesis_phase(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__team-relaunch-synthesis"
        _write_team_state(task_dir, provider="omc")
        _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(task_dir / "PLAN.md", "# Plan\n")
        _write_team_plan(task_dir)
        _write_worker_summary(task_dir, "lead", "tests/test_example.py")
        _write_worker_summary(task_dir, "worker-a", "app/main.ts")
        _write_worker_summary(task_dir, "reviewer", "docs/architecture.md")

        with _pushd(repo):
            _lib.build_team_bootstrap(str(task_dir), write_files=True)
            _lib.build_team_dispatch(str(task_dir), write_files=True)

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "team-relaunch-synthesis",
                    "kind": "team_relaunch",
                    "task_dir": "doc/harness/tasks/TASK__team-relaunch-synthesis",
                    "expect": {
                        "available": True,
                        "ready": True,
                        "worker": "lead",
                        "phase": "synthesis",
                        "selection_reason_contains": "TEAM_SYNTHESIS",
                    },
                }
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)


    def test_context_case_routes_documentation_review_and_handoff_refresh(self):
        repo = self._make_repo()

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
            _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
            _write(
                task_dir / "DOC_SYNC.md",
                textwrap.dedent(
                    """
                    # DOC_SYNC: task
                    written_at: 2026-01-01T00:00:00Z

                    ## What changed
                    - docs aligned with the verified implementation

                    ## New files
                    none

                    ## Updated files
                    - docs/architecture.md

                    ## Deleted files
                    none

                    ## Notes
                    - verified after final runtime QA
                    """
                ).strip()
                + "\n",
            )
            if include_document_critic:
                _write(task_dir / "CRITIC__document.md", "verdict: PASS\nsummary: docs match the verified behavior\n")
            _write_non_stub_handoff(task_dir)

        doc_review_task = repo / "doc" / "harness" / "tasks" / "TASK__team-doc-review"
        _write_ready_team_task(
            doc_review_task,
            team_status="running",
            document_verdict="pending",
            include_document_critic=False,
        )

        handoff_task = repo / "doc" / "harness" / "tasks" / "TASK__team-handoff-refresh"
        _write_ready_team_task(
            handoff_task,
            team_status="complete",
            document_verdict="PASS",
            include_document_critic=True,
        )

        with _pushd(repo):
            _lib.build_team_bootstrap(str(doc_review_task), write_files=True)
            _lib.build_team_dispatch(str(doc_review_task), write_files=True)
            _lib.build_team_bootstrap(str(handoff_task), write_files=True)
            _lib.build_team_dispatch(str(handoff_task), write_files=True)

        _touch_many(
            doc_review_task,
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
        _touch_many(
            handoff_task,
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

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "context-doc-review",
                    "kind": "context",
                    "task_dir": "doc/harness/tasks/TASK__team-doc-review",
                    "expect": {
                        "next_action_contains": "CRITIC__document.md",
                        "team": {
                            "relaunch_available": True,
                            "relaunch_ready": True,
                            "relaunch_worker": "lead",
                            "relaunch_phase": "documentation_review",
                            "document_critic_pending": True,
                        },
                    },
                },
                {
                    "id": "context-handoff-refresh",
                    "kind": "context",
                    "task_dir": "doc/harness/tasks/TASK__team-handoff-refresh",
                    "expect": {
                        "next_action_contains": "after critic-document",
                        "team": {
                            "handoff_refresh_needed": True,
                            "handoff_refresh_reason": "refresh HANDOFF.md after critic-document",
                            "relaunch_available": True,
                            "relaunch_ready": True,
                            "relaunch_worker": "lead",
                            "relaunch_phase": "handoff_refresh",
                        },
                    },
                },
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_team_relaunch_case_selects_handoff_refresh_and_degraded_synthesis(self):
        repo = self._make_repo()

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
            _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
            _write(
                task_dir / "DOC_SYNC.md",
                textwrap.dedent(
                    """
                    # DOC_SYNC: task
                    written_at: 2026-01-01T00:00:00Z

                    ## What changed
                    - docs aligned with the verified implementation

                    ## New files
                    none

                    ## Updated files
                    - docs/architecture.md

                    ## Deleted files
                    none

                    ## Notes
                    - verified after final runtime QA
                    """
                ).strip()
                + "\n",
            )
            if include_document_critic:
                _write(task_dir / "CRITIC__document.md", "verdict: PASS\nsummary: docs match the verified behavior\n")
            _write_non_stub_handoff(task_dir)

        handoff_task = repo / "doc" / "harness" / "tasks" / "TASK__team-handoff-refresh"
        _write_ready_team_task(
            handoff_task,
            team_status="complete",
            document_verdict="PASS",
            include_document_critic=True,
        )

        degraded_task = repo / "doc" / "harness" / "tasks" / "TASK__team-degraded-refresh"
        _write_ready_team_task(
            degraded_task,
            team_status="degraded",
            document_verdict="PASS",
            include_document_critic=True,
        )
        _write_team_state_fields(
            degraded_task,
            provider="omc",
            team_status="degraded",
            runtime_verdict="PASS",
            runtime_verdict_freshness="current",
            document_verdict="PASS",
            document_verdict_freshness="current",
            doc_changes_detected=False,
        )

        _touch_many(
            degraded_task,
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

        with _pushd(repo):
            _lib.build_team_bootstrap(str(handoff_task), write_files=True)
            _lib.build_team_dispatch(str(handoff_task), write_files=True)
            _lib.build_team_bootstrap(str(degraded_task), write_files=True)
            _lib.build_team_dispatch(str(degraded_task), write_files=True)

        _touch_many(
            handoff_task,
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

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "relaunch-handoff-refresh",
                    "kind": "team_relaunch",
                    "task_dir": "doc/harness/tasks/TASK__team-handoff-refresh",
                    "expect": {
                        "available": True,
                        "ready": True,
                        "worker": "lead",
                        "phase": "handoff_refresh",
                        "selection_reason_contains": "critic-document",
                    },
                },
                {
                    "id": "relaunch-degraded-synthesis",
                    "kind": "team_relaunch",
                    "task_dir": "doc/harness/tasks/TASK__team-degraded-refresh",
                    "expect": {
                        "available": True,
                        "ready": True,
                        "worker": "lead",
                        "phase": "synthesis",
                        "selection_reason_contains": "degraded team round",
                    },
                },
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)

    def test_close_gate_case_blocks_degraded_team_synthesis_refresh(self):
        repo = self._make_repo()
        task_dir = repo / "doc" / "harness" / "tasks" / "TASK__team-degraded-refresh"
        _write_team_state_fields(
            task_dir,
            provider="omc",
            team_status="degraded",
            runtime_verdict="PASS",
            runtime_verdict_freshness="current",
            document_verdict="PASS",
            document_verdict_freshness="current",
            doc_changes_detected=False,
        )
        _write(task_dir / "REQUEST.md", "Implement app, docs, and tests.\n")
        _write(task_dir / "PLAN.md", "# Plan\n")
        _write(task_dir / "CRITIC__plan.md", "verdict: PASS\nsummary: plan approved\n")
        _write_team_plan(task_dir)
        _write_worker_summary(task_dir, "lead", "tests/test_example.py")
        _write_worker_summary(task_dir, "worker-a", "app/main.ts")
        _write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
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
        _write(task_dir / "CRITIC__runtime.md", "verdict: PASS\nsummary: final runtime verification passed\n")
        _write(
            task_dir / "DOC_SYNC.md",
            textwrap.dedent(
                """
                # DOC_SYNC: task
                written_at: 2026-01-01T00:00:00Z

                ## What changed
                - docs aligned with the verified implementation

                ## New files
                none

                ## Updated files
                - docs/architecture.md

                ## Deleted files
                none

                ## Notes
                - verified after final runtime QA
                """
            ).strip()
            + "\n",
        )
        _write(task_dir / "CRITIC__document.md", "verdict: PASS\nsummary: docs match the verified behavior\n")
        _write_non_stub_handoff(task_dir)

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

        with _pushd(repo):
            _lib.build_team_bootstrap(str(task_dir), write_files=True)
            _lib.build_team_dispatch(str(task_dir), write_files=True)

        corpus = {
            "version": 1,
            "cases": [
                {
                    "id": "close-gate-degraded-team-refresh",
                    "kind": "close_gate",
                    "task_dir": "doc/harness/tasks/TASK__team-degraded-refresh",
                    "expect": {
                        "blocked": True,
                        "required_substrings": [
                            "team_status must resolve to 'complete' or 'fallback' before close, got 'degraded'",
                            "TEAM_SYNTHESIS.md must be refreshed after the degraded team round before close",
                        ],
                    },
                }
            ],
        }
        corpus_path = repo / "corpus.json"
        corpus_path.write_text(json.dumps(corpus, ensure_ascii=False, indent=2), encoding="utf-8")

        report = execute_replay(corpus_path=str(corpus_path), repo_root=str(repo))
        self.assertEqual(report["summary"]["failed"], 0, report)


if __name__ == "__main__":
    unittest.main()
