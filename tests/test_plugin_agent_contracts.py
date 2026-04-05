import json
import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent


def split_frontmatter(path: str):
    text = (REPO_ROOT / path).read_text(encoding="utf-8")
    assert text.startswith("---\n"), f"missing frontmatter in {path}"
    parts = text.split("---\n", 2)
    assert len(parts) >= 3, f"unterminated frontmatter in {path}"
    return parts[1], parts[2]


def extract_frontmatter(path: str) -> str:
    frontmatter, _body = split_frontmatter(path)
    return frontmatter


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
        frontmatter, body = split_frontmatter("plugin/agents/harness.md")
        tools = parse_csv_field(frontmatter, "tools")
        for required in ("Agent", "Skill", "AskUserQuestion"):
            self.assertIn(required, tools)
        referenced = sorted(set(re.findall(r"`(mcp__plugin_harness_harness__[A-Za-z0-9_]+)`", body)))
        for required in referenced:
            self.assertIn(required, tools, msg=f"harness.md references {required} without allowlisting it")
        self.assertNotRegex(frontmatter, r"^skills:")

    def test_runtime_critic_inherits_session_tools_but_cannot_edit(self):
        frontmatter = extract_frontmatter("plugin/agents/critic-runtime.md")
        self.assertNotRegex(frontmatter, r"^tools:")
        disallowed = parse_csv_field(frontmatter, "disallowedTools")
        for forbidden in ("Edit", "Write", "MultiEdit", "Agent", "Skill"):
            self.assertIn(forbidden, disallowed)

    def test_plugin_mcp_server_is_declared(self):
        config = json.loads((REPO_ROOT / "plugin/.mcp.json").read_text(encoding="utf-8"))
        self.assertIn("harness", config.get("mcpServers", {}))

    def test_agents_and_skills_expose_required_harness_mcp_tools(self):
        harness_tools = parse_csv_field(extract_frontmatter("plugin/agents/harness.md"), "tools")
        for required in (
            "mcp__plugin_harness_harness__task_start",
            "mcp__plugin_harness_harness__task_context",
            "mcp__plugin_harness_harness__task_update_from_git_diff",
            "mcp__plugin_harness_harness__task_verify",
            "mcp__plugin_harness_harness__task_close",
            "mcp__plugin_harness_harness__team_bootstrap",
            "mcp__plugin_harness_harness__team_dispatch",
        ):
            self.assertIn(required, harness_tools)

        plan_tools = parse_csv_field((REPO_ROOT / "plugin/skills/plan/SKILL.md").read_text(encoding="utf-8").split("---\n", 2)[1], "allowed-tools")
        for required in ("mcp__plugin_harness_harness__task_start", "mcp__plugin_harness_harness__task_context"):
            self.assertIn(required, plan_tools)

        critic_plan_tools = parse_csv_field(extract_frontmatter("plugin/agents/critic-plan.md"), "tools")
        self.assertIn("mcp__plugin_harness_harness__write_critic_plan", critic_plan_tools)

        critic_document_tools = parse_csv_field(extract_frontmatter("plugin/agents/critic-document.md"), "tools")
        self.assertIn("mcp__plugin_harness_harness__write_critic_document", critic_document_tools)

    def test_project_settings_preapprove_harness_skills(self):
        settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
        allow = settings["permissions"]["allow"]
        expected = {
            "Skill(harness:plan)",
            "Skill(harness:plan *)",
            "Skill(harness:maintain)",
            "Skill(harness:maintain *)",
            "mcp__plugin_harness_harness__*",
        }
        self.assertTrue(expected.issubset(set(allow)))

    def test_plugin_settings_only_use_supported_agent_key(self):
        settings = json.loads((REPO_ROOT / "plugin/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(set(settings), {"agent"})

    def test_docs_do_not_claim_native_team_approval_bypass(self):
        forbidden_phrases = (
            "The harness never asks the user for team permission",
            "No user confirmation is required before spawning workers",
            "approval_mode: preapproved",
        )
        for rel in (
            "README.md",
            "plugin/docs/orchestration-modes.md",
            "plugin/skills/setup/SKILL.md",
            "plugin/skills/setup/templates/doc/harness/manifest.yaml",
            "doc/harness/manifest.yaml",
        ):
            text = (REPO_ROOT / rel).read_text(encoding="utf-8")
            for phrase in forbidden_phrases:
                self.assertNotIn(phrase, text, msg=rel)

    def test_blueprint_examples_do_not_show_unsupported_plugin_frontmatter(self):
        text = (REPO_ROOT / "CLAUDE_CODE_HARNESS_BLUEPRINT.md").read_text(encoding="utf-8")
        self.assertNotRegex(text, r"(?m)^permissionMode:")
        self.assertNotRegex(text, r"(?m)^mcpServers:")


if __name__ == "__main__":
    unittest.main()
