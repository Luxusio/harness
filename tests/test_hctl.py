"""Tests for hctl.py — harness CLI control plane.

Covers (PLAN.md §10.1):
  1. hctl start — routing compile, canonical + compat fields written
  2. hctl context --json — required keys present, brief caps enforced
  3. hctl update --from-git-diff — touched_paths written (mocked git)
  4. Routing compile logic — maintenance_task / workflow_locked / risk_level
  5. Compatibility preservation — execution_mode / orchestration_mode derived correctly

Run with: python -m unittest discover -s tests -p 'test_*.py'
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts"))
os.environ["HARNESS_SKIP_STDIN"] = "1"

HCTL = os.path.join(os.path.dirname(__file__), "..", "plugin", "scripts", "hctl.py")
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_task(base_dir, task_id, lane="refactor", risk_tags=None,
               extra_fields=None):
    """Create a minimal TASK_STATE.yaml in base_dir/task_id/."""
    task_dir = os.path.join(base_dir, task_id)
    os.makedirs(task_dir, exist_ok=True)
    tags_str = str(risk_tags or [])
    fields = {
        "task_id": task_id,
        "status": "planned",
        "lane": lane,
        "risk_tags": tags_str,
        "browser_required": "false",
        "runtime_verdict_fail_count": "0",
        "qa_required": "true",
        "doc_sync_required": "true",
        "parallelism": "1",
        "workflow_locked": "true",
        "maintenance_task": "false",
        "routing_compiled": "false",
        "routing_source": "pending",
        "risk_level": "pending",
        "execution_mode": "standard",
        "orchestration_mode": "solo",
        "plan_verdict": "PASS",
        "updated": "2026-01-01T00:00:00Z",
    }
    if extra_fields:
        for k, v in extra_fields.items():
            fields[k] = v
    lines = [f"{k}: {v}" for k, v in fields.items()]
    with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return task_dir


def _write_request(task_dir, body):
    with open(os.path.join(task_dir, "REQUEST.md"), "w", encoding="utf-8") as f:
        f.write(
            "# Request: TEST\n"
            "created: 2026-01-01T00:00:00Z\n\n"
            f"{body}\n"
        )


def _run_hctl(*args, cwd=None, env=None):
    """Run hctl.py with given args, return (returncode, stdout, stderr)."""
    run_env = os.environ.copy()
    if env:
        run_env.update(env)
    result = subprocess.run(
        [sys.executable, HCTL] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
        env=run_env,
    )
    return result.returncode, result.stdout, result.stderr


def _yaml_field(path, field):
    """Read a single flat field from YAML file."""
    with open(path) as f:
        for line in f:
            if line.startswith(field + ":"):
                return line.split(":", 1)[1].strip()
    return None


# ---------------------------------------------------------------------------
# Test: hctl --help
# ---------------------------------------------------------------------------

class TestHctlHelp(unittest.TestCase):

    def test_help_exits_zero(self):
        code, out, _ = _run_hctl("--help")
        self.assertEqual(code, 0)
        self.assertIn("hctl", out)

    def test_subcommands_listed(self):
        code, out, _ = _run_hctl("--help")
        for sub in ("start", "context", "team-bootstrap", "team-dispatch", "team-launch", "team-relaunch", "history", "top-failures", "diff-case", "update", "record-agent-run", "verify", "close", "artifact"):
            self.assertIn(sub, out, f"subcommand '{sub}' missing from --help")


class TestHctlRecordAgentRun(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_agent_run_updates_state_and_returns_json(self):
        task_dir = _make_task(self.tmp.name, "TASK__record_agent_run", extra_fields={
            "agent_run_developer_count": "0",
            "agent_run_developer_last": "null",
        })
        code, out, err = _run_hctl(
            "record-agent-run",
            "--task-dir", task_dir,
            "--agent-name", "developer",
            "--observed-at", "2026-02-03T04:05:06Z",
            "--json",
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["count_before"], 0)
        self.assertEqual(payload["count_after"], 1)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "agent_run_developer_count"), "1")
        self.assertEqual(_yaml_field(state, "agent_run_developer_last").strip('"'), "2026-02-03T04:05:06Z")



# ---------------------------------------------------------------------------
# Test: hctl start — routing compile
# ---------------------------------------------------------------------------

class TestHctlStart(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_start_writes_routing_compiled(self):
        task_dir = _make_task(self.tmp.name, "TASK__test")
        code, out, err = _run_hctl("start", "--task-dir", task_dir)
        self.assertEqual(code, 0, err)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        val = _yaml_field(state, "routing_compiled") or ""
        self.assertIn(val.lower(), ("true", "1"), f"routing_compiled expected true, got {val!r}")

    def test_start_writes_routing_source_hctl(self):
        task_dir = _make_task(self.tmp.name, "TASK__test")
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "routing_source"), "hctl")

    def test_start_writes_risk_level(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="refactor",
                              risk_tags=["multi-root"])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        risk = _yaml_field(state, "risk_level")
        self.assertIn(risk, ("low", "medium", "high"))

    def test_start_maintenance_task_false_for_normal_lane(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="build",
                              risk_tags=[])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        mt = (_yaml_field(state, "maintenance_task") or "").lower()
        self.assertIn(mt, ("false", "0"))

    def test_start_maintenance_task_true_for_maintenance_tag(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="refactor",
                              risk_tags=["maintenance-task", "harness-source"])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        mt = (_yaml_field(state, "maintenance_task") or "").lower()
        self.assertIn(mt, ("true", "1"))

    def test_start_workflow_locked_false_when_maintenance(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="refactor",
                              risk_tags=["maintenance-task"])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        wl = (_yaml_field(state, "workflow_locked") or "").lower()
        self.assertIn(wl, ("false", "0"))

    def test_start_workflow_locked_true_when_not_maintenance(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="build",
                              risk_tags=[])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        wl = (_yaml_field(state, "workflow_locked") or "").lower()
        self.assertIn(wl, ("true", "1"))

    def test_start_writes_compat_execution_mode(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="answer")
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        em = _yaml_field(state, "execution_mode")
        self.assertEqual(em, "light")

    def test_start_writes_compat_orchestration_mode(self):
        task_dir = _make_task(self.tmp.name, "TASK__test")
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        om = _yaml_field(state, "orchestration_mode")
        self.assertEqual(om, "subagents")

    def test_start_small_task_keeps_solo(self):
        task_dir = _make_task(self.tmp.name, "TASK__small", lane="debug")
        _write_request(task_dir, "Fix a small single-file typo in README.md.")
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "orchestration_mode"), "solo")
        self.assertEqual(_yaml_field(state, "team_status"), "skipped")

    def test_start_team_preferred_falls_back_to_subagents_when_provider_missing(self):
        task_dir = _make_task(self.tmp.name, "TASK__team", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        _run_hctl("start", "--task-dir", task_dir, env={"PATH": ""})
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "orchestration_mode"), "subagents")
        self.assertEqual(_yaml_field(state, "team_status"), "fallback")
        self.assertEqual(_yaml_field(state, "team_provider"), "fallback-subagents")
        self.assertEqual(_yaml_field(state, "fallback_used"), "subagents")

    def test_start_team_selected_when_provider_ready(self):
        task_dir = _make_task(self.tmp.name, "TASK__team_ready", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        fake_bin = Path(self.tmp.name) / "bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        omc_path = fake_bin / "omc"
        omc_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        omc_path.chmod(0o755)
        env = {"PATH": f"{fake_bin}{os.pathsep}" + os.environ.get("PATH", "")}
        _run_hctl("start", "--task-dir", task_dir, env=env)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "orchestration_mode"), "team")
        self.assertEqual(_yaml_field(state, "team_status"), "planned")
        self.assertEqual(_yaml_field(state, "team_provider"), "omc")
        self.assertEqual(_yaml_field(state, "team_plan_required"), "true")
        self.assertEqual(_yaml_field(state, "team_synthesis_required"), "true")
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "TEAM_PLAN.md")))
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "TEAM_SYNTHESIS.md")))

    def test_start_team_selects_native_when_supported_version_and_env_present(self):
        task_dir = _make_task(self.tmp.name, "TASK__team_native_ready", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        fake_bin = Path(self.tmp.name) / "bin-native-ready"
        fake_bin.mkdir(parents=True, exist_ok=True)
        claude_path = fake_bin / "claude"
        claude_path.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  echo 'Claude Code 2.1.32'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        claude_path.chmod(0o755)
        env = {
            "PATH": f"{fake_bin}{os.pathsep}" + os.environ.get("PATH", ""),
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        }
        _run_hctl("start", "--task-dir", task_dir, env=env)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "orchestration_mode"), "team")
        self.assertEqual(_yaml_field(state, "team_provider"), "native")
        self.assertEqual(_yaml_field(state, "team_status"), "planned")

    def test_start_team_falls_back_when_native_version_too_old(self):
        task_dir = _make_task(self.tmp.name, "TASK__team_native_old", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        fake_bin = Path(self.tmp.name) / "bin-native-old"
        fake_bin.mkdir(parents=True, exist_ok=True)
        claude_path = fake_bin / "claude"
        claude_path.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--version\" ]; then\n"
            "  echo 'Claude Code 2.1.31'\n"
            "  exit 0\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        claude_path.chmod(0o755)
        env = {
            "PATH": f"{fake_bin}{os.pathsep}" + os.environ.get("PATH", ""),
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        }
        _run_hctl("start", "--task-dir", task_dir, env=env)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "orchestration_mode"), "subagents")
        self.assertEqual(_yaml_field(state, "team_provider"), "fallback-subagents")
        self.assertEqual(_yaml_field(state, "fallback_used"), "subagents")

    def test_start_writes_failure_case_sidecar(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="debug")
        code, out, err = _run_hctl("start", "--task-dir", task_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("failure_case:", out)
        self.assertTrue(os.path.isfile(os.path.join(task_dir, "FAILURE_CASE.json")))

    def test_start_auto_promotes_planning_mode_broad_build(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__test",
            lane="build",
            extra_fields={"browser_required": "true", "planning_mode": "standard"},
        )
        _write_request(
            task_dir,
            "Create a new admin dashboard web app for customer operations. "
            "Show key metrics and a detail workflow.",
        )

        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        self.assertEqual(_yaml_field(state, "planning_mode"), "broad-build")

    def test_start_missing_task_dir_exits_nonzero(self):
        code, _, err = _run_hctl("start", "--task-dir", "/nonexistent/path")
        self.assertNotEqual(code, 0)

    def test_start_high_risk_for_maintenance_tags(self):
        task_dir = _make_task(self.tmp.name, "TASK__test", lane="refactor",
                              risk_tags=["maintenance-task", "harness-source",
                                         "template-sync-required"])
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        risk = _yaml_field(state, "risk_level")
        self.assertEqual(risk, "high")


# ---------------------------------------------------------------------------
# Test: hctl context --json — required keys, brief caps
# ---------------------------------------------------------------------------

class TestHctlContextJson(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _context_json(self, lane="refactor", risk_tags=None):
        task_dir = _make_task(self.tmp.name, "TASK__ctx", lane=lane,
                              risk_tags=risk_tags)
        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        return json.loads(out)

    def _team_env(self):
        fake_bin = Path(self.tmp.name) / "bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        omc_path = fake_bin / "omc"
        omc_path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        omc_path.chmod(0o755)
        return {"PATH": f"{fake_bin}{os.pathsep}" + os.environ.get("PATH", "")}

    def _native_team_env(self):
        fake_bin = Path(self.tmp.name) / "native-bin"
        fake_bin.mkdir(parents=True, exist_ok=True)
        claude_path = fake_bin / "claude"
        claude_path.write_text("#!/bin/sh\necho '{\"ok\": true}'\n", encoding="utf-8")
        claude_path.chmod(0o755)
        return {
            "PATH": f"{fake_bin}{os.pathsep}" + os.environ.get("PATH", ""),
            "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "1",
        }

    def _write_completed_team_plan(self, task_dir):
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n"
            "- worker-1: frontend\n"
            "- worker-2: backend\n\n"
            "## Owned Writable Paths\n"
            "- worker-1: app/**\n"
            "- worker-2: api/**\n\n"
            "## Shared Read-Only Paths\n"
            "- docs/**\n\n"
            "## Forbidden Writes\n"
            "- worker-1: api/**\n"
            "- worker-2: app/**\n\n"
            "## Synthesis Strategy\n"
            "- lead merges diffs and runs final verification\n",
            encoding="utf-8",
        )

    def _write_worker_summary(self, task_dir, worker_name, handled_path):
        team_dir = Path(task_dir) / "team"
        team_dir.mkdir(parents=True, exist_ok=True)
        rel_name = worker_name if worker_name.startswith("worker-") else f"worker-{worker_name}"
        (team_dir / f"{rel_name}.md").write_text(
            "# Worker Summary\n"
            "## Completed Work\n- finished slice\n\n"
            f"## Owned Paths Handled\n- {handled_path}\n\n"
            "## Verification\n- pytest\n\n"
            "## Residual Risks\n- none\n",
            encoding="utf-8",
        )

    def _write_completed_worker_summaries(self, task_dir):
        self._write_worker_summary(task_dir, "worker-1", "app/main.ts")
        self._write_worker_summary(task_dir, "worker-2", "api/server.ts")

    def _write_doc_sync(self, task_dir, *, meaningful=False):
        if meaningful:
            what_changed = "- docs/architecture.md aligned with the verified implementation"
            updated = "- docs/architecture.md"
            notes = "- verified after final runtime QA"
        else:
            what_changed = "none"
            updated = "none"
            notes = "none"
        Path(task_dir, "DOC_SYNC.md").write_text(
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
            encoding="utf-8",
        )

    def _write_documentation_owner_plan(self, task_dir):
        Path(task_dir, "TEAM_PLAN.md").write_text(
            "# Team Plan\n"
            "## Worker Roster\n"
            "- lead: integrator\n"
            "- worker-a: app\n"
            "- reviewer: doc-reviewer\n\n"
            "## Owned Writable Paths\n"
            "- lead: tests/**\n"
            "- worker-a: app/**\n"
            "- reviewer: docs/**\n\n"
            "## Shared Read-Only Paths\n"
            "- api/**\n\n"
            "## Forbidden Writes\n"
            "- lead: app/**, docs/**\n"
            "- worker-a: tests/**, docs/**\n"
            "- reviewer: tests/**, app/**\n\n"
            "## Synthesis Strategy\n"
            "- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md\n\n"
            "## Documentation Ownership\n"
            "- writer: reviewer\n"
            "- critic-document: lead\n",
            encoding="utf-8",
        )

    def _set_state_field(self, task_dir, field, value):
        state_path = Path(task_dir, "TASK_STATE.yaml")
        content = state_path.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(field)}:\s*.+$", content, re.MULTILINE):
            content = re.sub(rf"^{re.escape(field)}:\s*.+$", f"{field}: {value}", content, flags=re.MULTILINE)
        else:
            content += f"{field}: {value}\n"
        state_path.write_text(content, encoding="utf-8")

    def _write_document_pass(self, task_dir):
        self._set_state_field(task_dir, "document_verdict", "PASS")
        self._set_state_field(task_dir, "doc_changes_detected", "true")
        Path(task_dir, "CRITIC__document.md").write_text(
            "verdict: PASS\n"
            "summary: docs match the verified behavior\n\n"
            "## Findings\n- docs/architecture.md is current\n",
            encoding="utf-8",
        )

    def test_required_keys_present(self):
        ctx = self._context_json()
        required = {
            "task_id", "status", "lane", "risk_level", "qa_required",
            "doc_sync_required", "browser_required", "parallelism",
            "workflow_locked", "maintenance_task", "planning_mode", "compat", "team",
            "must_read", "commands", "checks", "open_failures", "notes",
            "review_focus", "next_action",
        }
        for key in required:
            self.assertIn(key, ctx, f"required key '{key}' missing from context JSON")

    def test_compat_has_execution_and_orchestration_mode(self):
        ctx = self._context_json()
        self.assertIn("execution_mode", ctx["compat"])
        self.assertIn("orchestration_mode", ctx["compat"])

    def test_must_read_cap_at_four(self):
        ctx = self._context_json()
        self.assertLessEqual(len(ctx["must_read"]), 4,
                             "must_read must not exceed 4 items")

    def test_notes_cap_at_three(self):
        ctx = self._context_json()
        self.assertLessEqual(len(ctx["notes"]), 3,
                             "notes must not exceed 3 items")

    def test_task_id_matches(self):
        ctx = self._context_json()
        self.assertEqual(ctx["task_id"], "TASK__ctx")

    def test_lane_matches(self):
        ctx = self._context_json(lane="investigate")
        self.assertEqual(ctx["lane"], "investigate")

    def test_risk_level_valid_value(self):
        ctx = self._context_json()
        self.assertIn(ctx["risk_level"], ("low", "medium", "high"))

    def test_checks_is_summary_object(self):
        ctx = self._context_json()
        self.assertIsInstance(ctx["checks"], dict)
        for key in ("total", "open_ids", "failed_ids", "blocked_ids", "candidate_ids", "top_open_titles"):
            self.assertIn(key, ctx["checks"])

    def test_next_action_present(self):
        ctx = self._context_json()
        self.assertTrue(ctx["next_action"])

    def test_no_verbose_artifact_dump(self):
        """Context JSON must not contain raw artifact file content."""
        ctx = self._context_json()
        dumped = json.dumps(ctx)
        # Should not contain multi-line prose blocks
        self.assertLess(len(dumped), 2200,
                        "context JSON appears too verbose for a compact task pack")

    def test_low_risk_for_answer_lane(self):
        ctx = self._context_json(lane="answer")
        self.assertEqual(ctx["risk_level"], "low")

    def test_maintenance_task_false_for_normal(self):
        ctx = self._context_json(lane="build")
        self.assertFalse(ctx["maintenance_task"])

    def test_maintenance_task_true_for_maintenance_tags(self):
        ctx = self._context_json(lane="refactor",
                                 risk_tags=["maintenance-task"])
        self.assertTrue(ctx["maintenance_task"])

    def test_review_focus_defaults_to_disabled(self):
        ctx = self._context_json()
        self.assertIn("review_focus", ctx)
        self.assertFalse(ctx["review_focus"].get("evidence_first"))

    def test_context_surfaces_broad_build_planning_mode(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx",
            lane="build",
            extra_fields={
                "browser_required": "true",
                "planning_mode": "standard",
                "plan_verdict": "pending",
            },
        )
        _write_request(
            task_dir,
            "Create a new admin dashboard web app for customer operations. "
            "Show key metrics and a detail workflow.",
        )

        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["planning_mode"], "broad-build")
        self.assertIn("01_product_spec.md", ctx["next_action"])

    def test_runtime_fail_surfaces_failing_critic_in_must_read(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx")
        task_path = Path(task_dir)
        (task_path / "PLAN.md").write_text("# Plan\n\nFix runtime bug.\n", encoding="utf-8")
        (task_path / "CHECKS.yaml").write_text(
            "checks:\n  - id: AC-001\n    status: failed\n    title: runtime failure\n",
            encoding="utf-8",
        )
        (task_path / "HANDOFF.md").write_text(
            "# Handoff\n\n## Result\n- repro: pytest tests/test_fail.py\n",
            encoding="utf-8",
        )
        with open(task_path / "TASK_STATE.yaml", "a", encoding="utf-8") as fh:
            fh.write("runtime_verdict: FAIL\n")
        (task_path / "CRITIC__runtime.md").write_text(
            "verdict: FAIL\n"
            "summary: pytest still fails on AC-001\n\n"
            "## Transcript\n"
            "pytest tests/test_fail.py\n"
            "AssertionError: expected 200 got 500\n",
            encoding="utf-8",
        )

        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["review_focus"].get("evidence_first"))
        self.assertEqual(ctx["review_focus"].get("trigger"), "runtime_fail")
        self.assertTrue(ctx["must_read"][0].endswith("CRITIC__runtime.md"))
        self.assertIn("CRITIC__runtime.md", ctx["review_focus"].get("critic_artifact", ""))
        self.assertIn("pytest", ctx["review_focus"].get("evidence_excerpt", ""))
        self.assertIn("runtime evidence first", ctx["next_action"].lower())

    def test_session_handoff_is_prioritized_for_fix_round(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx")
        task_path = Path(task_dir)
        (task_path / "PLAN.md").write_text("# Plan\n\nResume task.\n", encoding="utf-8")
        (task_path / "CRITIC__runtime.md").write_text(
            "verdict: FAIL\n"
            "summary: failing verification\n\n"
            "## Transcript\n"
            "pytest tests/test_fail.py\n",
            encoding="utf-8",
        )
        (task_path / "SESSION_HANDOFF.json").write_text(
            json.dumps(
                {
                    "trigger": "runtime_fail_repeat",
                    "next_step": "Reproduce the pytest failure before broad repo exploration.",
                    "open_check_ids": ["AC-009"],
                    "paths_in_focus": ["plugin/scripts/_lib.py"],
                    "do_not_regress": ["existing CLI path remains stable"],
                    "files_to_read_first": ["PLAN.md", "CRITIC__runtime.md"],
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["review_focus"].get("evidence_first"))
        self.assertEqual(
            ctx["review_focus"].get("supporting_artifact"),
            "doc/harness/tasks/TASK__ctx/SESSION_HANDOFF.json",
        )
        self.assertEqual(ctx["review_focus"].get("focus_check_ids"), ["AC-009"])
        self.assertEqual(ctx["must_read"][0], "doc/harness/tasks/TASK__ctx/SESSION_HANDOFF.json")
        self.assertIn("pytest", ctx["review_focus"].get("evidence_excerpt", ""))

    def test_session_handoff_surfaces_team_recovery_phase(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx_team_handoff", lane="build", risk_tags=["multi-root"])
        task_path = Path(task_dir)
        self._write_completed_team_plan(task_dir)
        (task_path / "team").mkdir(parents=True, exist_ok=True)
        self._write_worker_summary(task_dir, "worker-1", "app/main.ts")
        (task_path / "SESSION_HANDOFF.json").write_text(
            json.dumps(
                {
                    "trigger": "runtime_fail_repeat",
                    "next_step": "Collect missing worker summaries before refresh.",
                    "files_to_read_first": ["TEAM_PLAN.md", "team/worker-1.md"],
                    "team_recovery": {
                        "phase": "worker_summaries",
                        "pending_workers": ["worker-2"],
                        "pending_artifacts": ["team/worker-2.md", "TEAM_SYNTHESIS.md"],
                    },
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )

        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["review_focus"].get("team_recovery_phase"), "worker_summaries")
        self.assertEqual(ctx["review_focus"].get("team_pending_workers"), ["worker-2"])
        self.assertIn("TEAM_SYNTHESIS.md", " ".join(ctx["review_focus"].get("team_pending_artifacts") or []))
        self.assertTrue(any(item.endswith("TEAM_PLAN.md") for item in ctx["must_read"]))

    def test_document_fail_surfaces_document_critic(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx")
        task_path = Path(task_dir)
        (task_path / "PLAN.md").write_text("# Plan\n\nFix docs.\n", encoding="utf-8")
        (task_path / "DOC_SYNC.md").write_text("# Doc Sync\n\nDocs drifted.\n", encoding="utf-8")
        (task_path / "CRITIC__document.md").write_text(
            "verdict: FAIL\n"
            "summary: DOC_SYNC misses updated file list\n"
            "issues: missing DOC_SYNC evidence\n",
            encoding="utf-8",
        )

        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["review_focus"].get("evidence_first"))
        self.assertEqual(ctx["review_focus"].get("trigger"), "document_fail")
        self.assertIn("CRITIC__document.md", ctx["review_focus"].get("critic_artifact", ""))
        self.assertIn("DOC_SYNC.md", ctx["review_focus"].get("supporting_artifact", ""))
        self.assertIn("document evidence first", ctx["next_action"].lower())

    def test_team_context_prioritizes_team_plan_when_scaffold_is_incomplete(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx_team", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"]["status"], "planned")
        self.assertTrue(ctx["team"]["plan_exists"])
        self.assertFalse(ctx["team"]["plan_ready"])
        self.assertTrue(any(item.endswith("TEAM_PLAN.md") for item in ctx["must_read"]))
        self.assertIn("TEAM_PLAN.md", ctx["next_action"])

    def test_team_context_promotes_status_to_running_when_team_plan_is_ready(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx_team", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        self._write_completed_worker_summaries(task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"]["status"], "running")
        self.assertTrue(ctx["team"]["plan_ready"])
        self.assertTrue(ctx["team"]["worker_summary_ready"])
        self.assertFalse(ctx["team"]["synthesis_ready"])
        self.assertIn("TEAM_SYNTHESIS.md", ctx["next_action"])

    def test_team_context_requests_worker_summaries_before_synthesis(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx_team_workers", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"]["status"], "running")
        self.assertTrue(ctx["team"]["worker_summary_required"])
        self.assertFalse(ctx["team"]["worker_summary_ready"])
        self.assertIn("worker summaries", ctx["next_action"].lower())
        self.assertIn("worker-1", " ".join(ctx["team"].get("worker_summary_missing_workers") or []))

    def test_team_context_personalizes_pending_worker_resume(self):
        task_dir = _make_task(self.tmp.name, "TASK__ctx_team_worker_resume", lane="build", risk_tags=["multi-root"])
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "worker-2"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-1", "app/main.ts")
        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"]["current_worker"], "worker-2")
        self.assertTrue(ctx["team"]["current_worker_pending"])
        self.assertIn("api/**", " ".join(ctx["team"].get("current_worker_owned_paths") or []))
        self.assertIn("worker-2", ctx["next_action"])
        self.assertIn("team/worker-2.md", ctx["next_action"])
        self.assertEqual(ctx["review_focus"].get("team_current_worker"), "worker-2")


    def test_team_context_routes_synthesis_owner_to_final_runtime_verification(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_verify",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "lead"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        Path(task_dir, "TEAM_PLAN.md").write_text(
            """# Team Plan
## Worker Roster
- lead: integrator
- worker-a: app
- worker-b: api

## Owned Writable Paths
- lead: tests/**
- worker-a: app/**
- worker-b: api/**

## Shared Read-Only Paths
- docs/**

## Forbidden Writes
- lead: app/**, api/**
- worker-a: tests/**, api/**
- worker-b: tests/**, app/**

## Synthesis Strategy
- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md
""",
            encoding="utf-8",
        )
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "worker-b", "api/server.ts")
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: pre-synthesis runtime pass

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )
        time.sleep(0.02)
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and api slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("runtime_verification_needed"))
        self.assertEqual(ctx["team"].get("current_worker"), "lead")
        self.assertTrue(ctx["review_focus"].get("team_final_verification_needed"))
        self.assertIn("final runtime verification", ctx["next_action"].lower())
        self.assertIn("lead", ctx["next_action"])

    def test_team_context_routes_after_final_verification_to_documentation(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_docs",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, API, tests, and refresh docs.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "lead"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        Path(task_dir, "TEAM_PLAN.md").write_text(
            """# Team Plan
## Worker Roster
- lead: integrator
- worker-a: app
- worker-b: api

## Owned Writable Paths
- lead: tests/**
- worker-a: app/**
- worker-b: api/**

## Shared Read-Only Paths
- docs/**

## Forbidden Writes
- lead: app/**, api/**
- worker-a: tests/**, api/**
- worker-b: tests/**, app/**

## Synthesis Strategy
- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md
""",
            encoding="utf-8",
        )
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "worker-b", "api/server.ts")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and api slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=False)
        time.sleep(0.02)
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: final runtime verification passed

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("documentation_needed"))
        self.assertTrue(ctx["team"].get("doc_sync_needed"))
        self.assertTrue(ctx["review_focus"].get("team_documentation_needed"))
        self.assertIn("documentation pass", ctx["next_action"].lower())
        self.assertIn("DOC_SYNC.md", ctx["next_action"])

    def test_team_context_routes_after_doc_sync_to_document_critic(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_doc_critic",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, API, tests, and refresh docs.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "lead"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        Path(task_dir, "TEAM_PLAN.md").write_text(
            """# Team Plan
## Worker Roster
- lead: integrator
- worker-a: app
- worker-b: api

## Owned Writable Paths
- lead: tests/**
- worker-a: app/**
- worker-b: api/**

## Shared Read-Only Paths
- docs/**

## Forbidden Writes
- lead: app/**, api/**
- worker-a: tests/**, api/**
- worker-b: tests/**, app/**

## Synthesis Strategy
- lead merges worker summaries and writes TEAM_SYNTHESIS.md then refreshes HANDOFF.md
""",
            encoding="utf-8",
        )
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "worker-b", "api/server.ts")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and api slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: final runtime verification passed

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=True)

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("documentation_needed"))
        self.assertTrue(ctx["team"].get("document_critic_needed"))
        self.assertTrue(ctx["review_focus"].get("team_document_critic_needed"))
        self.assertIn("critic-document", ctx["next_action"])

    def test_team_context_personalizes_doc_sync_to_documentation_owner(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_doc_owner",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, tests, and refresh docs.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "reviewer"
        env["CLAUDE_AGENT_NAME"] = "harness:writer:reviewer"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and docs slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=False)
        time.sleep(0.02)
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: final runtime verification passed

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"].get("current_worker"), "reviewer")
        self.assertEqual(ctx["team"].get("current_agent_role"), "writer")
        self.assertTrue(ctx["team"].get("current_worker_is_doc_sync_owner"))
        self.assertEqual(ctx["team"].get("doc_sync_owners"), ["reviewer"])
        self.assertIn("As reviewer", ctx["next_action"])
        self.assertIn("DOC_SYNC.md", ctx["next_action"])

    def test_team_context_personalizes_document_critic_to_owner(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_doc_critic_owner",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, tests, and refresh docs.")
        env = self._team_env()
        env["HARNESS_TEAM_WORKER"] = "lead"
        env["CLAUDE_AGENT_NAME"] = "harness:critic-document:lead"
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and docs slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: final runtime verification passed

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=True)

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"].get("current_worker"), "lead")
        self.assertEqual(ctx["team"].get("current_agent_role"), "critic-document")
        self.assertTrue(ctx["team"].get("current_worker_is_document_critic_owner"))
        self.assertEqual(ctx["team"].get("document_critic_owners"), ["lead"])
        self.assertIn("As lead", ctx["next_action"])
        self.assertIn("CRITIC__document.md", ctx["next_action"])

    def test_context_accepts_explicit_worker_and_agent_without_env(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_explicit_worker",
            lane="build",
            risk_tags=["multi-root"],
            extra_fields={"mutates_repo": "true", "runtime_verdict": "PASS"},
        )
        _write_request(task_dir, "Implement app, tests, and refresh docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "reviewer", "docs/architecture.md")
        Path(task_dir, "TEAM_SYNTHESIS.md").write_text(
            """# Team Synthesis
## Integrated Result
- merged app and docs slices

## Cross-Checks
- ownership respected

## Verification Summary
- pytest tests/test_example.py

## Residual Risks
- none
""",
            encoding="utf-8",
        )
        self._write_doc_sync(task_dir, meaningful=False)
        time.sleep(0.02)
        Path(task_dir, "CRITIC__runtime.md").write_text(
            """verdict: PASS
summary: final runtime verification passed

## Transcript
pytest tests/test_example.py
""",
            encoding="utf-8",
        )

        code, out, err = _run_hctl(
            "context",
            "--task-dir",
            task_dir,
            "--json",
            "--team-worker",
            "reviewer",
            "--agent-name",
            "harness:writer:reviewer",
        )
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"].get("current_worker"), "reviewer")
        self.assertEqual(ctx["team"].get("current_agent_role"), "writer")
        self.assertTrue(ctx["team"].get("current_worker_is_doc_sync_owner"))
        self.assertIn("DOC_SYNC.md", ctx["next_action"])

    def test_team_context_recommends_team_bootstrap_before_fanout(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_bootstrap_hint",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, and tests for the feature.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("bootstrap_available"))
        self.assertFalse(ctx["team"].get("bootstrap_generated"))
        self.assertIn("team_bootstrap", ctx["next_action"])

    def test_team_bootstrap_generates_worker_briefs_and_env_files(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_bootstrap_write",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        code, out, err = _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "index.json").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "worker-a.md").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "worker-a.developer.env").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "reviewer.writer.env").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "lead.critic-document.env").is_file())

        brief = (Path(task_dir) / "team" / "bootstrap" / "reviewer.md").read_text(encoding="utf-8")
        self.assertIn("Team Worker Bootstrap — reviewer", brief)
        self.assertIn("DOC_SYNC.md", brief)

        env_file = (Path(task_dir) / "team" / "bootstrap" / "reviewer.writer.env").read_text(encoding="utf-8")
        self.assertIn("export HARNESS_TEAM_WORKER='reviewer'", env_file)
        self.assertIn("export CLAUDE_AGENT_NAME='harness:writer:reviewer'", env_file)

    def test_team_dispatch_generates_provider_prompts_and_run_scripts(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_dispatch_write",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        code, out, err = _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)

        code, out, err = _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("provider"), "omc")
        self.assertIn("bootstrap_signature", payload)
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "dispatch.json").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "omc-team.prompt.md").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "launch-omc-team.sh").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "run-worker-a-implement.sh").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "reviewer.documentation_sync.writer.prompt.md").is_file())

        provider_prompt = (Path(task_dir) / "team" / "bootstrap" / "provider" / "omc-team.prompt.md").read_text(encoding="utf-8")
        self.assertIn("TEAM_PLAN.md", provider_prompt)
        self.assertIn("Planned workers", provider_prompt)

        launcher = (Path(task_dir) / "team" / "bootstrap" / "provider" / "launch-omc-team.sh").read_text(encoding="utf-8")
        self.assertIn("omc team 3:executor", launcher)

    def test_team_launch_autorefreshes_bootstrap_and_dispatch(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_launch_write",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        code, out, err = _run_hctl("team-launch", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("provider"), "omc")
        self.assertEqual(payload.get("target"), "provider")
        self.assertTrue(payload.get("bootstrap_refreshed"))
        self.assertTrue(payload.get("dispatch_refreshed"))
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "index.json").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "dispatch.json").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "launch.json").is_file())
        self.assertIn("launch-omc-team.sh", payload.get("launch_script", ""))

    def test_team_launch_native_provider_surfaces_prompt_and_execute_fallback(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_launch_native",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._native_team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        code, out, err = _run_hctl("team-launch", "--task-dir", task_dir, "--json", "--write-files", env=env)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("provider"), "native")
        self.assertEqual(payload.get("target"), "provider")
        self.assertTrue(payload.get("interactive_required"))
        self.assertTrue(payload.get("execute_supported"))
        self.assertTrue(payload.get("execute_fallback_available"))
        self.assertEqual(payload.get("execute_target"), "implementers")
        self.assertIn("falling back", payload.get("execute_resolution_reason", ""))
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "native-team.prompt.md").is_file())
        self.assertIn("native-team.prompt.md", payload.get("provider_prompt", ""))
        self.assertIn("dispatch-implementers.sh", payload.get("execute_launch_script", ""))

    def test_team_launch_execute_auto_uses_implementer_fallback_for_native_provider(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_launch_native_exec",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._native_team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        code, out, err = _run_hctl("team-launch", "--task-dir", task_dir, "--json", "--write-files", "--execute", env=env)
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("target"), "provider")
        self.assertEqual(payload.get("execute_target"), "implementers")
        self.assertTrue(payload.get("execution", {}).get("spawned"))
        self.assertEqual(payload.get("execution", {}).get("resolved_target"), "implementers")
        self.assertIn("dispatch-implementers.sh", payload.get("execution", {}).get("launch_script", ""))

    def test_team_dispatch_generates_synthesis_and_handoff_run_scripts(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_dispatch_synthesis",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)

        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        code, out, err = _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "run-lead-synthesis.sh").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "run-lead-handoff_refresh.sh").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "lead.synthesis.developer.prompt.md").is_file())
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "lead.handoff_refresh.developer.prompt.md").is_file())

    def test_team_relaunch_defaults_to_pending_worker_implement_phase(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_relaunch_worker",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")

        code, out, err = _run_hctl("team-relaunch", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("worker"), "worker-a")
        self.assertEqual(payload.get("phase"), "implement")
        self.assertIn("worker summary", payload.get("selection_reason", ""))
        self.assertTrue((Path(task_dir) / "team" / "bootstrap" / "provider" / "relaunch.json").is_file())

    def test_team_relaunch_routes_synthesis_owner_after_worker_summaries(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_relaunch_synthesis",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_documentation_owner_plan(task_dir)
        self._write_worker_summary(task_dir, "lead", "tests/test_example.py")
        self._write_worker_summary(task_dir, "worker-a", "app/main.ts")
        self._write_worker_summary(task_dir, "reviewer", "docs/guide.md")
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")

        code, out, err = _run_hctl("team-relaunch", "--task-dir", task_dir, "--json", "--write-files")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)

        self.assertTrue(payload.get("ready"))
        self.assertEqual(payload.get("worker"), "lead")
        self.assertEqual(payload.get("phase"), "synthesis")
        self.assertIn("TEAM_SYNTHESIS", payload.get("selection_reason", ""))
        self.assertIn("run-lead-synthesis.sh", payload.get("run_script", ""))

    def test_team_context_requests_team_dispatch_after_bootstrap(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_dispatch_hint",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("dispatch_available"))
        self.assertFalse(ctx["team"].get("dispatch_generated"))
        self.assertIn("team_dispatch", ctx["next_action"])

    def test_team_context_requests_team_launch_after_dispatch(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_launch_hint",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("launch_available"))
        self.assertFalse(ctx["team"].get("launch_generated"))
        self.assertIn("team_launch", ctx["next_action"])

    def test_team_context_surfaces_native_launch_prompt_and_execute_fallback(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_launch_native_hint",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._native_team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files", env=env)
        _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files", env=env)

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json", env=env)
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertEqual(ctx["team"].get("launch_target"), "provider")
        self.assertTrue(ctx["team"].get("launch_interactive_required"))
        self.assertTrue(ctx["team"].get("launch_execute_supported"))
        self.assertEqual(ctx["team"].get("launch_execute_target"), "implementers")
        self.assertIn("native-team.prompt.md", ctx["team"].get("launch_provider_prompt", ""))
        self.assertIn("native lead prompt", ctx.get("next_action", ""))

    def test_team_context_marks_dispatch_stale_when_dispatch_files_go_missing(self):
        task_dir = _make_task(
            self.tmp.name,
            "TASK__ctx_team_dispatch_stale",
            lane="build",
            risk_tags=["multi-root"],
        )
        _write_request(task_dir, "Implement app, API, tests, and docs.")
        env = self._team_env()
        _run_hctl("start", "--task-dir", task_dir, env=env)
        self._write_completed_team_plan(task_dir)
        _run_hctl("team-bootstrap", "--task-dir", task_dir, "--json", "--write-files")
        _run_hctl("team-dispatch", "--task-dir", task_dir, "--json", "--write-files")

        provider_prompt = Path(task_dir) / "team" / "bootstrap" / "provider" / "omc-team.prompt.md"
        provider_prompt.unlink()

        code, out, err = _run_hctl("context", "--task-dir", task_dir, "--json")
        self.assertEqual(code, 0, err)
        ctx = json.loads(out)

        self.assertTrue(ctx["team"].get("dispatch_generated"))
        self.assertTrue(ctx["team"].get("dispatch_stale"))
        self.assertTrue(ctx["team"].get("dispatch_refresh_needed"))
        self.assertIn("dispatch files missing", ctx["team"].get("dispatch_reason", ""))
        self.assertIn("team_dispatch", ctx["next_action"])


# ---------------------------------------------------------------------------
# Test: hctl context human-readable (no --json)
# ---------------------------------------------------------------------------

class TestHctlContextHuman(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_human_output_contains_task_id(self):
        task_dir = _make_task(self.tmp.name, "TASK__human")
        _run_hctl("start", "--task-dir", task_dir)
        code, out, err = _run_hctl("context", "--task-dir", task_dir)
        self.assertEqual(code, 0, err)
        self.assertIn("TASK__human", out)

    def test_human_output_contains_lane(self):
        task_dir = _make_task(self.tmp.name, "TASK__human2", lane="debug")
        _run_hctl("start", "--task-dir", task_dir)
        code, out, _ = _run_hctl("context", "--task-dir", task_dir)
        self.assertEqual(code, 0)
        self.assertIn("debug", out)


# ---------------------------------------------------------------------------
# Test: compatibility field derivation
# ---------------------------------------------------------------------------

class TestCompatibilityFields(unittest.TestCase):
    """Ensure execution_mode / orchestration_mode are derived correctly."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _start_and_read(self, lane, risk_tags=None):
        task_dir = _make_task(self.tmp.name, f"TASK__{lane}", lane=lane,
                              risk_tags=risk_tags)
        _run_hctl("start", "--task-dir", task_dir)
        state = os.path.join(task_dir, "TASK_STATE.yaml")
        return {
            "execution_mode": _yaml_field(state, "execution_mode"),
            "orchestration_mode": _yaml_field(state, "orchestration_mode"),
            "risk_level": _yaml_field(state, "risk_level"),
        }

    def test_low_risk_maps_to_light(self):
        fields = self._start_and_read("answer")
        self.assertEqual(fields["execution_mode"], "light")
        self.assertEqual(fields["risk_level"], "low")

    def test_medium_risk_maps_to_standard(self):
        fields = self._start_and_read("build")
        self.assertEqual(fields["execution_mode"], "standard")
        self.assertEqual(fields["risk_level"], "medium")

    def test_high_risk_maps_to_sprinted(self):
        fields = self._start_and_read("refactor",
                                      risk_tags=["maintenance-task"])
        self.assertEqual(fields["execution_mode"], "sprinted")
        self.assertEqual(fields["risk_level"], "high")

    def test_non_trivial_build_prefers_subagents_over_solo(self):
        fields = self._start_and_read("build")
        self.assertEqual(fields["orchestration_mode"], "subagents")


# ---------------------------------------------------------------------------
# Test: failure history CLI surfaces indexed cases
# ---------------------------------------------------------------------------

class TestHctlFailureHistoryCli(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def _make_failure_task(self, task_id, *, verification_target="src/api/users.py", check_id="AC-001", critic_summary="users persistence failed", runtime_verdict="FAIL", fail_count="1"):
        task_dir = _make_task(
            self.tmp.name,
            task_id,
            lane="debug",
            extra_fields={
                "runtime_verdict": runtime_verdict,
                "runtime_verdict_fail_count": fail_count,
                "verification_targets": f"[{verification_target}]",
                "plan_verdict": "PASS",
            },
        )
        task_path = Path(task_dir)
        (task_path / "REQUEST.md").write_text("Fix users persistence.\n", encoding="utf-8")
        (task_path / "CHECKS.yaml").write_text(
            f"checks:\n  - id: {check_id}\n    status: failed\n    title: persistence\n",
            encoding="utf-8",
        )
        (task_path / "CRITIC__runtime.md").write_text(
            f"verdict: FAIL\nsummary: {critic_summary}\n",
            encoding="utf-8",
        )
        _run_hctl("start", "--task-dir", task_dir)
        return task_dir

    def test_history_lists_failure_cases(self):
        self._make_failure_task("TASK__a", fail_count="2")
        self._make_failure_task("TASK__b", verification_target="src/api/profile.py", check_id="AC-002")

        code, out, err = _run_hctl("history", "--tasks-dir", self.tmp.name, "--json")
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertGreaterEqual(len(payload), 2)
        self.assertEqual(payload[0]["task_id"], "TASK__a")

    def test_top_failures_returns_similar_cases(self):
        current = self._make_failure_task("TASK__current", fail_count="1")
        self._make_failure_task("TASK__match", fail_count="2")
        self._make_failure_task("TASK__other", verification_target="docs/README.md", check_id="DOC-001", critic_summary="docs drift")

        code, out, err = _run_hctl(
            "top-failures",
            "--task-dir", current,
            "--tasks-dir", self.tmp.name,
            "--json",
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertTrue(payload)
        self.assertEqual(payload[0]["task_id"], "TASK__match")

    def test_diff_case_compares_overlap(self):
        self._make_failure_task("TASK__a", fail_count="2")
        self._make_failure_task("TASK__b", fail_count="1")

        code, out, err = _run_hctl(
            "diff-case",
            "--tasks-dir", self.tmp.name,
            "--case-a", "TASK__a",
            "--case-b", "TASK__b",
            "--json",
        )
        self.assertEqual(code, 0, err)
        payload = json.loads(out)
        self.assertIn("AC-001", payload.get("shared_check_ids", []))
        self.assertIn("src", payload.get("shared_paths", []))


# ---------------------------------------------------------------------------
# Test: hctl update --from-git-diff (mocked via subprocess patching)
# ---------------------------------------------------------------------------

class TestHctlUpdate(unittest.TestCase):
    """hctl update writes touched_paths/roots_touched/verification_targets."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_update_no_flag_prints_hint(self):
        task_dir = _make_task(self.tmp.name, "TASK__upd")
        code, out, _ = _run_hctl("update", "--task-dir", task_dir)
        self.assertEqual(code, 0)
        self.assertIn("Nothing to update", out)

    def test_update_from_git_diff_exits_zero_in_repo(self):
        """Running --from-git-diff in the real repo exits 0."""
        task_dir = _make_task(self.tmp.name, "TASK__upd2")
        code, out, err = _run_hctl("update", "--task-dir", task_dir,
                                   "--from-git-diff", cwd=REPO_ROOT)
        self.assertEqual(code, 0, err)


if __name__ == "__main__":
    unittest.main()
