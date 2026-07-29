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

    def test_existing_config_preservation_documented(self):
        """A configured component list is preserved while [] is migrated."""
        content = self._read_setup_skill()
        self.assertIn("health_components already configured", content)
        self.assertIn("Treat `health_components: []` as an old disabled", content)

    def test_health_detection_never_prompts(self):
        """Health components are staged automatically for every setup."""
        content = self._read_setup_skill()
        section = content.split("## Phase 2.5: Health Stack Auto-Detection", 1)[1]
        section = section.split("## Phase 3:", 1)[0]
        self.assertIn("Never ask for confirmation", section)
        self.assertNotIn("HARNESS_SPAWNED", section)
        self.assertNotIn("[Y/n]", section)
        self.assertNotIn("AskUserQuestion", section)

    def test_phase_2_5_before_phase_3(self):
        """Phase 2.5 must appear before Phase 3 in the skill."""
        content = self._read_setup_skill()
        pos_2_5 = content.find("Phase 2.5")
        pos_3 = content.find("## Phase 3: Bootstrap")
        self.assertGreater(pos_3, pos_2_5, "Phase 2.5 must come before Phase 3")


class TestHealthComponentsIdempotency(unittest.TestCase):
    """AC-006: preserve configured components and migrate old empty defaults."""

    def test_skip_logic_in_skill(self):
        """Idempotent check uses grep for health_components key presence."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "SKILL.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("health_components:", content,
                      "health_components: key check must be in setup/SKILL.md")

    def test_empty_list_migration_documented(self):
        """An old empty list must no longer disable detected health checks."""
        path = os.path.join(REPO_ROOT, "plugin", "skills", "setup", "SKILL.md")
        with open(path) as f:
            content = f.read()
        self.assertIn("Treat `health_components: []` as an old disabled", content)
        self.assertIn("replace it with the detected/default components", content)


class TestMultiGitHealthCommandSafety(unittest.TestCase):
    def test_relative_prefix_precedes_shell_quoting(self):
        for relative in (
            "plugin/skills/setup/SKILL.md",
            "plugin-codex/skills/setup/SKILL.md",
            "plugin/skills/setup/bootstrap.md",
            "plugin/skills/setup/repo-census.md",
        ):
            path = os.path.join(REPO_ROOT, *relative.split("/"))
            with open(path, encoding="utf-8") as handle:
                content = handle.read()
            self.assertIn('shlex.quote("./" + root)', content)


if __name__ == "__main__":
    unittest.main()
