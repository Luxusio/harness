from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = tuple(
    ROOT / runtime / name / "SKILL.md"
    for runtime in ("plugin/skills", "plugin-codex/internal-skills")
    for name in (
        "plan-ceo-review", "plan-devex-review", "plan-design-review",
        "plan-eng-review", "develop",
    )
)


def test_heavy_review_and_develop_skills_stay_under_weight_budget():
    counts = {path: len(path.read_text(encoding="utf-8").splitlines()) for path in TARGETS}
    assert all(count <= 500 for count in counts.values()), counts
    assert sum(counts.values()) <= int(9379 * 0.60), counts


def test_compaction_does_not_add_prompt_reference_indirection():
    allowed = {
        "plugin/skills/plan-devex-review/dx-hall-of-fame.md",
        "plugin/skills/plan-eng-review/rubrics-threat-rollback.md",
    }
    actual = {
        str(path.relative_to(ROOT))
        for base in (
            ROOT / "plugin/skills/plan-ceo-review",
            ROOT / "plugin/skills/plan-devex-review",
            ROOT / "plugin/skills/plan-design-review",
            ROOT / "plugin/skills/plan-eng-review",
            ROOT / "plugin-codex/internal-skills/plan-ceo-review",
            ROOT / "plugin-codex/internal-skills/plan-devex-review",
            ROOT / "plugin-codex/internal-skills/plan-design-review",
            ROOT / "plugin-codex/internal-skills/plan-eng-review",
        )
        for path in base.rglob("*")
        if path.is_file() and path.name != "SKILL.md"
    }
    assert actual == allowed


def test_compressed_skills_preserve_unique_role_and_close_contracts():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in TARGETS)
    for term in (
        "Premise", "Scope", "Architecture", "Security", "Performance",
        "Information architecture", "accessibility", "Developer Journey",
        "TTHW", "task_verify", "install_verified.py", "task_close",
        "RECEIPTS.jsonl", "BLOCKED_ENV",
    ):
        assert term in combined
