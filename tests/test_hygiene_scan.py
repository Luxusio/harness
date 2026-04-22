"""HK-01..09, TM-01..05: hygiene_scan.py tests (AC-003, AC-004, AC-013, AC-021)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from hygiene_scan import classify_lint_line, TIER_A, TIER_B, TIER_C, TIER_SKIP  # noqa: E402


def _make_minimal_repo(tmp_path, has_c16=True):
    """Create a minimal temp dir with .git and CONTRACTS.md."""
    git_dir = os.path.join(tmp_path, ".git")
    os.makedirs(git_dir)
    contracts_content = "<!-- harness:managed-begin v1 -->\n"
    if has_c16:
        contracts_content += "### C-16\n"
    else:
        contracts_content += "### C-15\n"
    contracts_content += "<!-- harness:managed-end -->\n"
    with open(os.path.join(tmp_path, "CONTRACTS.md"), "w") as f:
        f.write(contracts_content)
    doc_harness = os.path.join(tmp_path, "doc", "harness")
    os.makedirs(doc_harness, exist_ok=True)
    return tmp_path


class TestHooksJsonWiring(unittest.TestCase):
    """HK-01, HK-04, HK-05: hooks.json wiring tests."""

    def test_HK01_has_hygiene_entry(self):
        """HK-01: hooks.json SessionStart has hygiene_scan.py entry with timeout 10 + || true."""
        hooks_path = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")
        with open(hooks_path, encoding="utf-8") as f:
            data = json.load(f)
        session_hooks = data["hooks"]["SessionStart"][0]["hooks"]
        hygiene_entries = [h for h in session_hooks if "hygiene_scan.py" in h.get("command", "")]
        self.assertTrue(hygiene_entries, "hygiene_scan.py entry missing from SessionStart hooks")
        entry = hygiene_entries[0]
        self.assertEqual(entry["timeout"], 10)
        self.assertIn("|| true", entry["command"])

    def test_HK01_hygiene_after_contract_lint(self):
        """HK-01: hygiene_scan.py entry comes AFTER contract_lint --quick."""
        hooks_path = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")
        with open(hooks_path, encoding="utf-8") as f:
            data = json.load(f)
        commands = [h.get("command", "") for h in data["hooks"]["SessionStart"][0]["hooks"]]
        lint_idx = next((i for i, c in enumerate(commands) if "contract_lint" in c), -1)
        hygiene_idx = next((i for i, c in enumerate(commands) if "hygiene_scan.py" in c), -1)
        self.assertGreaterEqual(lint_idx, 0)
        self.assertGreaterEqual(hygiene_idx, 0)
        self.assertGreater(hygiene_idx, lint_idx)

    def test_HK04_exit_zero_on_real_repo(self):
        """HK-04: hygiene_scan.py exits 0 on real repo."""
        result = subprocess.run(
            [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
             "--apply-safe", "--repo-root", REPO_ROOT],
            capture_output=True, text=True, timeout=15,
        )
        self.assertEqual(result.returncode, 0)

    def test_HK05_single_user_prompt_submit_slot(self):
        """HK-05: exactly ONE UserPromptSubmit slot."""
        hooks_path = os.path.join(REPO_ROOT, "plugin", "hooks", "hooks.json")
        with open(hooks_path, encoding="utf-8") as f:
            data = json.load(f)
        ups_slots = data["hooks"].get("UserPromptSubmit", [])
        self.assertEqual(len(ups_slots), 1)


class TestObserverAndBootstrap(unittest.TestCase):
    """HK-08, HK-09: observer mode and bootstrap detection."""

    def test_HK08_observe_only_no_writes(self):
        """HK-08: --observe-only performs ZERO writes to pending json."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_minimal_repo(tmp)
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--observe-only", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            pending = os.path.join(tmp, "doc", "harness", ".maintain-pending.json")
            self.assertFalse(os.path.exists(pending),
                ".maintain-pending.json must not be written in observe-only mode")

    def test_HK09_missing_c16_bootstrap_needed(self):
        """HK-09: missing C-16 → [hygiene-bootstrap-needed], exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_minimal_repo(tmp, has_c16=False)
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                 "--apply-safe", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            self.assertEqual(result.returncode, 0)
            combined = result.stdout + result.stderr
            self.assertIn("[hygiene-bootstrap-needed]", combined)


class TestConcurrency(unittest.TestCase):
    """HK-03: concurrent invocations are idempotent."""

    def test_HK03_concurrent_flock(self):
        """HK-03: Two concurrent invocations both exit 0."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_minimal_repo(tmp)
            results = []

            def run_scan():
                r = subprocess.run(
                    [sys.executable, os.path.join(SCRIPTS_DIR, "hygiene_scan.py"),
                     "--apply-safe", "--repo-root", tmp],
                    capture_output=True, text=True, timeout=15,
                )
                results.append(r.returncode)

            t1 = threading.Thread(target=run_scan)
            t2 = threading.Thread(target=run_scan)
            t1.start(); t2.start()
            t1.join(timeout=20); t2.join(timeout=20)
            self.assertEqual(len(results), 2)
            self.assertTrue(all(rc == 0 for rc in results))


class TestMalformedPending(unittest.TestCase):
    """HK-07: malformed pending JSON skipped."""

    def test_HK07_malformed_pending_json_skipped(self):
        """HK-07: _build_hygiene_block handles corrupt JSON gracefully."""
        import prompt_memory as pm
        with tempfile.TemporaryDirectory() as tmp:
            pending_file = os.path.join(tmp, ".maintain-pending.json")
            with open(pending_file, "w") as f:
                f.write("NOT VALID JSON {{{")
            orig = pm.PENDING_JSON
            pm.PENDING_JSON = os.path.relpath(pending_file, tmp)
            try:
                result = pm._build_hygiene_block(tmp)
                self.assertIsInstance(result, str)
            finally:
                pm.PENDING_JSON = orig


class TestTierMapping(unittest.TestCase):
    """TM-01..05: Tier mapping."""

    def test_TM01_info_tier_a(self):
        self.assertEqual(classify_lint_line("[INFO] contract matrix row missing"), TIER_A)

    def test_TM01_soft_additive_tier_b(self):
        self.assertEqual(classify_lint_line("[SOFT] missing contract heading C-16"), TIER_B)

    def test_TM01_soft_non_additive_tier_c(self):
        self.assertEqual(classify_lint_line("[SOFT] modified contract body for C-11"), TIER_C)

    def test_TM01_hard_tier_c(self):
        self.assertEqual(classify_lint_line("[HARD] marker tampering detected"), TIER_C)

    def test_TM02_soft_destructive_tier_c(self):
        self.assertEqual(classify_lint_line("[SOFT] delete stale contract row"), TIER_C)

    def test_TM03_unknown_prefix_tier_c(self):
        self.assertEqual(classify_lint_line("[DEBUG] some debug message"), TIER_C)
        self.assertEqual(classify_lint_line("[WARN] something"), TIER_C)

    def test_TM04_empty_line_skip(self):
        self.assertEqual(classify_lint_line(""), TIER_SKIP)
        self.assertEqual(classify_lint_line("   "), TIER_SKIP)

    def test_TM05_non_prefixed_skip(self):
        self.assertEqual(classify_lint_line("Some plain text"), TIER_SKIP)


if __name__ == "__main__":
    unittest.main()
