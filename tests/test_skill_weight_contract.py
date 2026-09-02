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


def test_weight_lint_sees_the_codex_internal_skills_layout(tmp_path):
    """The Codex tree keeps skills under `internal-skills/`, not `skills/`.

    Scanning only `skills/` left every Codex twin unguarded — including the one
    that sits exactly at the cap, where a single added line would slip through.
    """
    import importlib.util
    import sys

    spec = importlib.util.spec_from_file_location(
        "_contract_lint_weight", ROOT / "plugin" / "scripts" / "contract_lint.py"
    )
    lint = importlib.util.module_from_spec(spec)
    # Register before exec: the module defines dataclasses, whose type
    # resolution reads back through sys.modules.
    sys.modules["_contract_lint_weight"] = lint
    spec.loader.exec_module(lint)

    over_budget = "x\n" * (lint.SKILL_WEIGHT_LIMIT + 1)
    for parent in ("skills", "internal-skills"):
        skill = tmp_path / parent / "develop" / "SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text(over_budget, encoding="utf-8")

    reported = {
        str(path).split("/")[-3] for path, _n in lint.check_skill_weights(str(tmp_path))
    }
    assert reported == {"skills", "internal-skills"}, reported

    within = tmp_path / "ok"
    (within / "internal-skills" / "develop").mkdir(parents=True)
    (within / "internal-skills" / "develop" / "SKILL.md").write_text(
        "x\n" * lint.SKILL_WEIGHT_LIMIT, encoding="utf-8"
    )
    assert lint.check_skill_weights(str(within)) == []


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
