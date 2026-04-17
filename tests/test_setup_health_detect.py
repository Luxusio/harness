"""Tests for setup skill health auto-detection (AC-005..AC-006).

Tests the logic described in setup/SKILL.md Phase 2.5.
Since the skill is prose, we test the structural requirements:
- Phase 2.5 exists in the skill
- Idempotent skip logic is present
- 9-signal scan documented
"""
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class TestSetupSkillHealthDetect(unittest.TestCase):
    """AC-005..AC-006: setup skill health auto-detection documentation."""

    def _read_setup_skill(self):
        path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "SKILL.md")
        with open(path) as f:
            return f.read()

    def test_phase_2_5_exists(self):
        """setup/SKILL.md should contain Phase 2.5 health auto-detect step."""
        content = self._read_setup_skill()
        self.assertIn("Phase 2.5", content, "Phase 2.5 must be present in setup/SKILL.md")
        self.assertIn("Health Stack Auto-Detection", content)

    def test_nine_signals_documented(self):
        """All 9 health signals should be mentioned in setup/SKILL.md."""
        content = self._read_setup_skill()
        signals = [
            "tsconfig.json",
            "biome.json",
            "eslint.config",
            "pyproject.toml",
            "pytest",
            "ruff",
            "package.json",
            "Cargo.toml",
            "go.mod",
        ]
        for sig in signals:
            self.assertIn(sig, content, f"Signal {sig!r} must be documented in setup/SKILL.md")

    def test_idempotent_skip_documented(self):
        """health_components already set -> skip logic must be present."""
        content = self._read_setup_skill()
        self.assertIn("health_components already set", content,
                      "Idempotent skip message must be in setup/SKILL.md")

    def test_spawned_auto_accept_documented(self):
        """HARNESS_SPAWNED=1 auto-accept should be present."""
        content = self._read_setup_skill()
        self.assertIn("HARNESS_SPAWNED", content,
                      "HARNESS_SPAWNED auto-accept must be documented in setup/SKILL.md")

    def test_yn_confirm_documented(self):
        """[Y/n] confirmation must be documented."""
        content = self._read_setup_skill()
        self.assertIn("[Y/n]", content, "[Y/n] confirmation must be in setup/SKILL.md")

    def test_phase_2_5_before_phase_3(self):
        """Phase 2.5 must appear before Phase 3 in the skill."""
        content = self._read_setup_skill()
        pos_2_5 = content.find("Phase 2.5")
        pos_3 = content.find("## Phase 3: Bootstrap")
        self.assertGreater(pos_3, pos_2_5, "Phase 2.5 must come before Phase 3")


class TestHealthComponentsIdempotency(unittest.TestCase):
    """AC-006: idempotent skip when health_components key already present."""

    def test_skip_logic_in_skill(self):
        """Idempotent check uses grep for health_components key presence."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "SKILL.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("health_components:", content,
                      "health_components: key check must be in setup/SKILL.md")

    def test_empty_list_opt_out_documented(self):
        """Empty list as intentional opt-out must be documented."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "SKILL.md")
        with open(path) as f:
            content = f.read()
        # Should mention writing health_components: [] as opt-out
        self.assertIn("health_components: []", content,
                      "health_components: [] opt-out must be documented")


if __name__ == "__main__":
    unittest.main()
