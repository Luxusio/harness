import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def extract_frontmatter(path: str) -> str:
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter in {path}"
    parts = text.split("---\n", 2)
    assert len(parts) >= 3, f"unterminated frontmatter in {path}"
    return parts[1]


def parse_csv_field(frontmatter: str, field: str):
    pattern = re.compile(rf"^{re.escape(field)}:\s*(.+)$", re.MULTILINE)
    match = pattern.search(frontmatter)
    if not match:
        return []
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


class PluginAgentContractTests(unittest.TestCase):
    def test_plugin_agents_do_not_use_unsupported_plugin_frontmatter(self):
        for path in sorted((REPO_ROOT / "plugin/agents").glob("*.md")):
            frontmatter = extract_frontmatter(path.relative_to(REPO_ROOT).as_posix())
            self.assertNotRegex(frontmatter, r"^mcpServers:", msg=path.name)
            self.assertNotRegex(frontmatter, r"^permissionMode:", msg=path.name)
            self.assertNotRegex(frontmatter, r"^hooks:", msg=path.name)

    def test_harness_agent_exposes_real_orchestration_tools(self):
        frontmatter = extract_frontmatter("plugin/agents/harness.md")
        tools = parse_csv_field(frontmatter, "tools")
        for required in ("Agent", "Skill", "AskUserQuestion"):
            self.assertIn(required, tools)
        self.assertNotRegex(frontmatter, r"^skills:")

    def test_runtime_critic_inherits_session_tools_but_cannot_edit(self):
        frontmatter = extract_frontmatter("plugin/agents/critic-runtime.md")
        self.assertNotRegex(frontmatter, r"^tools:")
        disallowed = parse_csv_field(frontmatter, "disallowedTools")
        for forbidden in ("Edit", "Write", "MultiEdit", "Agent", "Skill"):
            self.assertIn(forbidden, disallowed)

    def test_project_settings_preapprove_harness_skills(self):
        settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        allow = settings["permissions"]["allow"]
        expected = {
            "Skill(harness:plan)",
            "Skill(harness:plan *)",
            "Skill(harness:maintain)",
            "Skill(harness:maintain *)",
        }
        self.assertTrue(expected.issubset(set(allow)))


if __name__ == "__main__":
    unittest.main()
