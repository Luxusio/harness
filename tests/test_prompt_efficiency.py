from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

QA_PROMPTS = [
    REPO / "plugin" / "agents" / "qa-api.md",
    REPO / "plugin" / "agents" / "qa-browser.md",
    REPO / "plugin" / "agents" / "qa-cli.md",
    REPO / "plugin" / "agents" / "qa-desktop.md",
    REPO / "plugin-codex" / "agents" / "qa-api.md",
    REPO / "plugin-codex" / "agents" / "qa-browser.md",
    REPO / "plugin-codex" / "agents" / "qa-cli.md",
    REPO / "plugin-codex" / "agents" / "qa-desktop.md",
]

CODEX_SKILL_PROMPTS = [
    REPO / "plugin-codex" / "skills" / "setup" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "run" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-ceo-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-eng-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-design-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-devex-review" / "SKILL.md",
]

REVIEW_SKILL_PROMPTS = [
    REPO / "plugin" / "skills" / "plan-ceo-review" / "SKILL.md",
    REPO / "plugin" / "skills" / "plan-eng-review" / "SKILL.md",
    REPO / "plugin" / "skills" / "plan-design-review" / "SKILL.md",
    REPO / "plugin" / "skills" / "plan-devex-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-ceo-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-eng-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-design-review" / "SKILL.md",
    REPO / "plugin-codex" / "internal-skills" / "plan-devex-review" / "SKILL.md",
]

QA_WORD_BUDGETS = {
    "qa-api.md": 1450,
    "qa-browser.md": 1450,
    "qa-cli.md": 1250,
    "qa-desktop.md": 1900,
}

RHETORICAL_PHRASES = [
    "Your reputation",
    "Trust nothing",
    "A developer saying",
    "starting point, not a ceiling",
    "A QA engineer who",
]

QA_REQUIRED_CONTRACTS = [
    "AC-to-evidence 1:1",
    "PASS requires",
    "BLOCKED_ENV",
    "final response",
]


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _word_count(text: str) -> int:
    return len(text.split())


def test_qa_prompts_omit_rhetorical_boilerplate():
    for path in QA_PROMPTS:
        body = _text(path)
        for phrase in RHETORICAL_PHRASES:
            assert phrase not in body, f"{path} still contains boilerplate: {phrase}"


def test_qa_prompts_stay_within_runtime_word_budget():
    for path in QA_PROMPTS:
        budget = QA_WORD_BUDGETS[path.name]
        count = _word_count(_text(path))
        assert count <= budget, f"{path} has {count} words, budget is {budget}"


def test_qa_prompts_keep_core_verdict_contracts():
    for path in QA_PROMPTS:
        body = _text(path)
        for phrase in QA_REQUIRED_CONTRACTS:
            assert phrase in body, f"{path} missing contract phrase: {phrase}"


def test_codex_qa_prompts_keep_runtime_delta():
    for path in QA_PROMPTS:
        body = _text(path)
        if "plugin-codex" in str(path):
            assert "Codex runtime notes" in body
            assert "HARNESS_PLUGIN_ROOT" in body
        else:
            assert "tools:" in body
            assert "final response" in body


def test_codex_skill_prompts_omit_port_measurement_history():
    banned = [
        "Codex port measurement",
        "v1.5 spike measurements",
        "Empirical port measurement",
        "Dominant deltas:",
        "HAND-PORTED",
        "spike-report.md §3.6",
    ]
    for path in CODEX_SKILL_PROMPTS:
        body = _text(path)
        for phrase in banned:
            assert phrase not in body, f"{path} still carries port history: {phrase}"


def test_review_skill_prompts_omit_default_status_boilerplate():
    banned = [
        "NO REVIEWS YET",
        "This sub-skill shares common sections",
        "Voice/Tone",
    ]
    for path in REVIEW_SKILL_PROMPTS:
        body = _text(path)
        for phrase in banned:
            assert phrase not in body, f"{path} still carries repeated review boilerplate: {phrase}"
