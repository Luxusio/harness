from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


TARGETED_OLD_NEGATIVE_PHRASES = [
    "Do not silently replace required browser QA with CLI-only checks",
    "Do not downgrade the task to qa-cli only",
    "do not close on CLI/API evidence alone",
    "Do NOT use this protocol for routine routing or obvious resolutions",
    "Do NOT silently override the plan",
    "Do NOT save reflections for the end",
    "Do NOT save up \"reflections\" for the end",
    "Do NOT log entries to fill a quota",
    "Do not document the mistake itself",
    "Never write \"the agent forgot...\"",
    "Never silently continue past a failure",
    "Do not compress or skip sections",
    "Not a trigger.",
    "No TBD, no placeholders",
]


OPERATIONAL_DOCS = [
    REPO / "plugin-codex" / "internal-skills" / "run" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan" / "SKILL.md",
    REPO / "plugin" / "skills" / "run" / "SKILL.md",
    REPO / "plugin" / "skills" / "develop" / "SKILL.md",
    REPO / "plugin" / "skills" / "plan" / "SKILL.md",
    REPO / "plugin" / "skills" / "run" / "self-improvement.md",
    REPO / "plugin" / "skills" / "plan" / "write-artifacts.md",
]


def test_targeted_negative_guidance_phrases_stay_rewritten():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in OPERATIONAL_DOCS)

    for phrase in TARGETED_OLD_NEGATIVE_PHRASES:
        assert phrase not in combined


def test_positive_guidance_replacements_are_present():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in OPERATIONAL_DOCS)

    expected = [
        "Await actual independent code review and conditional security review PASS before actual QA",
        "Reserve this protocol for high-stakes planning ambiguity",
        "EUREKA — re-scope AC-004 to a smaller addition. Surface before writing PLAN.md.",
        "Log only concrete, reusable facts at discovery time",
        "When a user correction points at an agent mistake, extract only a reusable conditional behavior rule",
        "Repair authorized failures autonomously and restore affected independent review",
        "Auto-decide scope-partition choices",
        "Use wbs context/next for measured progress and dependency-ready outcomes",
        "Complete every loaded sub-skill methodology section",
    ]
    for phrase in expected:
        assert phrase in combined
