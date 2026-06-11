"""Regression guards for the user-facing harness skill surface."""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

PUBLIC_SKILLS = {"setup"}
INTERNAL_SKILLS = {
    "goal-queue",
    "run",
    "plan",
    "develop",
    "plan-ceo-review",
    "plan-design-review",
    "plan-devex-review",
    "plan-eng-review",
}
CLAUDE_SKILL_ROOT = REPO / "plugin" / "skills"
CODEX_SKILL_ROOT = REPO / "plugin-codex" / "skills"
CODEX_INTERNAL_ROOT = REPO / "plugin-codex" / "internal-skills"


def _frontmatter(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines and lines[0] == "---", f"{path} must start with frontmatter"

    data: dict[str, str] = {}
    for line in lines[1:]:
        if line == "---":
            return data
        if ":" not in line or line.startswith((" ", "\t")):
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip()
    raise AssertionError(f"{path} frontmatter is not closed")


def test_public_and_internal_skill_visibility_is_explicit():
    for skill in sorted(PUBLIC_SKILLS | INTERNAL_SKILLS):
        meta = _frontmatter(CLAUDE_SKILL_ROOT / skill / "SKILL.md")
        expected = "true" if skill in PUBLIC_SKILLS else "false"
        assert meta.get("user-invocable") == expected, (
            f"plugin/skills/{skill} must set user-invocable: {expected}"
        )


def test_codex_user_visible_skill_tree_contains_only_public_skills():
    visible = {path.parent.name for path in CODEX_SKILL_ROOT.glob("*/SKILL.md")}
    internal = {path.parent.name for path in CODEX_INTERNAL_ROOT.glob("*/SKILL.md")}

    assert visible == PUBLIC_SKILLS
    assert internal == INTERNAL_SKILLS
    for skill in sorted(PUBLIC_SKILLS):
        assert _frontmatter(CODEX_SKILL_ROOT / skill / "SKILL.md").get("user-invocable") == "true"
    for skill in sorted(INTERNAL_SKILLS):
        assert _frontmatter(CODEX_INTERNAL_ROOT / skill / "SKILL.md").get("user-invocable") == "false"


def test_readme_user_skill_table_excludes_internal_skills():
    readme = (REPO / "README.md").read_text(encoding="utf-8")

    assert "| `/harness:setup` |" in readme
    assert "| `/harness:run` |" not in readme
    legacy_command = "| `/harness:" + "".join(map(chr, [97, 117, 116, 111, 112, 105, 108, 111, 116])) + "` |"
    assert legacy_command not in readme
    assert "native `/goal`" in readme
    assert "| `/harness:plan` |" not in readme
    assert "| `/harness:develop` |" not in readme
    assert "internal orchestration details" in readme


def test_claude_routing_does_not_expose_internal_skills_directly():
    routing_docs = [
        REPO / "plugin" / "CLAUDE.md",
        REPO / "plugin" / "skills" / "setup" / "bootstrap.md",
        REPO / "plugin" / "CHANGELOG.md",
    ]

    forbidden = [
        "Skill(harness:develop)",
        "Skill(plan-ceo-review)",
        "Skill(plan-design-review)",
        "Skill(plan-devex-review)",
        "Skill(plan-eng-review)",
    ]
    for path in routing_docs:
        body = path.read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in body, f"{path} exposes internal route {phrase}"

    claude = routing_docs[0].read_text(encoding="utf-8")
    assert "native `/goal`" in claude
    assert "review lenses are internal sub-skills" in claude
