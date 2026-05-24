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
    REPO / "plugin-codex" / "skills" / "run" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md",
    REPO / "plugin-codex" / "skills" / "plan" / "SKILL.md",
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
        "Preserve browser-required tasks with browser-lens evidence",
        "Keep browser-required close evidence on the browser lens",
        "close with browser-lens PASS evidence or browser-lens `BLOCKED_ENV`",
        "Reserve this protocol for high-stakes ambiguity",
        "Surface the discovery through the EUREKA path",
        "Log only concrete, reusable facts at discovery time",
        "Convert corrective feedback into a reusable conditional behavior rule",
        "Stop on phase failures, report the failure, check task state, and ask how to proceed",
        "Auto-decide scope-partition choices",
        "Complete every loaded sub-skill methodology section",
    ]
    for phrase in expected:
        assert phrase in combined
