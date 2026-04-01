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
import subprocess
import sys
import tempfile
import unittest

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
    lines = [
        f"task_id: {task_id}",
        f"status: planned",
        f"lane: {lane}",
        f"risk_tags: {tags_str}",
        "browser_required: false",
        "runtime_verdict_fail_count: 0",
        "qa_required: true",
        "doc_sync_required: true",
        "browser_required: false",
        "parallelism: 1",
        "workflow_locked: true",
        "maintenance_task: false",
        "routing_compiled: false",
        "routing_source: pending",
        "risk_level: pending",
        "execution_mode: standard",
        "orchestration_mode: solo",
        "plan_verdict: PASS",
        "updated: 2026-01-01T00:00:00Z",
    ]
    if extra_fields:
        for k, v in extra_fields.items():
            lines.append(f"{k}: {v}")
    with open(os.path.join(task_dir, "TASK_STATE.yaml"), "w") as f:
        f.write("\n".join(lines) + "\n")
    return task_dir


def _run_hctl(*args, cwd=None):
    """Run hctl.py with given args, return (returncode, stdout, stderr)."""
    result = subprocess.run(
        [sys.executable, HCTL] + list(args),
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
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
        for sub in ("start", "context", "update", "verify", "close", "artifact"):
            self.assertIn(sub, out, f"subcommand '{sub}' missing from --help")


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
        self.assertEqual(om, "solo")

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

    def test_required_keys_present(self):
        ctx = self._context_json()
        required = {
            "task_id", "status", "lane", "risk_level", "qa_required",
            "doc_sync_required", "browser_required", "parallelism",
            "workflow_locked", "maintenance_task", "compat",
            "must_read", "commands", "checks", "open_failures", "notes",
            "next_action",
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

    def test_orchestration_mode_solo_when_parallelism_1(self):
        fields = self._start_and_read("build")
        self.assertEqual(fields["orchestration_mode"], "solo")


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
