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
    "write_critic_qa",
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
            assert "mcp__plugin_harness_harness__write_critic_qa" in body
