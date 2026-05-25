from pathlib import Path


REPO = Path(__file__).resolve().parents[1]


def test_develop_skills_require_feedback_rule_judgment():
    for rel in ("plugin/skills/develop/SKILL.md", "plugin-codex/internal-skills/develop/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "Feedback-Derived Rules" in body
        assert "none" in body
        assert "captured" in body
        assert "rejected" in body
        assert "When X, do Y. Verify by Z." in body
        assert "Write behavior rules for Tier 2 docs" in body
        assert "incident-shaped lessons" in body
        assert "Commit-backed Learnings" in body
        assert "learnings.jsonl` is gitignored staging" in body
        assert "committed artifact" in body
        assert "changed a committed" in body
        assert "Self-Healing Candidates" in body
        assert "development, QA, dogfood, and close-gate discoveries" in body
        assert "Status: none | applied | deferred | rejected" in body
        assert "AskUserQuestion" in body
        assert "request_user_input" in body
        assert "user_decision:" in body
        assert "proposed_artifact:" in body


def test_qa_agents_surface_self_healing_candidates_for_handoff():
    for rel in (
        "plugin/agents/qa-cli.md",
        "plugin/agents/qa-api.md",
        "plugin/agents/qa-browser.md",
        "plugin/agents/qa-desktop.md",
        "plugin-codex/agents/qa-cli.md",
        "plugin-codex/agents/qa-api.md",
        "plugin-codex/agents/qa-browser.md",
        "plugin-codex/agents/qa-desktop.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "Self-Healing Candidates for HANDOFF" in body
        assert "write_critic_qa" in body
        assert "applied" in body
        assert "deferred" in body
        assert "rejected" in body


def test_browser_and_api_qa_fail_missing_req_for_observable_behavior():
    for rel in (
        "plugin/agents/qa-browser.md",
        "plugin/agents/qa-api.md",
        "plugin-codex/agents/qa-browser.md",
        "plugin-codex/agents/qa-api.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "missing REQ" in body
        assert "FAIL" in body
        assert "Durable Docs: linked REQ | missing | not-applicable" in body


def test_ux_agents_use_critic_ux_and_do_not_claim_qa_role():
    for rel in (
        "plugin/agents/ux-cli.md",
        "plugin/agents/ux-api.md",
        "plugin/agents/ux-browser.md",
        "plugin/agents/ux-desktop.md",
        "plugin-codex/agents/ux-cli.md",
        "plugin-codex/agents/ux-api.md",
        "plugin-codex/agents/ux-browser.md",
        "plugin-codex/agents/ux-desktop.md",
    ):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "write_critic_ux" in body
        assert "CRITIC__ux.md" in body
        assert "not qa-" in body.lower()
        assert "shippable" in body


def test_self_improvement_documents_readable_tier2_format():
    body = (REPO / "plugin/skills/run/self-improvement.md").read_text(encoding="utf-8")

    assert "Feedback-derived rules" in body
    assert "When <trigger>, <action>." in body
    assert "Verify by <observable check>." in body
    assert "judgment, not forced documentation" in body
    assert "Commit-backed promotion checkpoint" in body
    assert "learnings.jsonl` by itself is never enough for `captured`" in body
    assert "committed artifact" in body


def test_continuous_maintenance_doc_maps_former_maintain_work():
    body = (REPO / "doc/harness/patterns/continuous-maintenance-flow.md").read_text(
        encoding="utf-8"
    )

    assert "Maintain Responsibility Map" in body
    assert "REVIEW queue" in body
    assert "Tier C contract drift" in body
    assert "Runbook candidate" in body
    assert "Staged hygiene archive" in body
    assert "Self-Healing Candidates" in body
    assert "user_decision" in body
    assert "proposed_artifact" in body
    assert "AskUserQuestion" in body
    assert "request_user_input" in body


def test_maintenance_state_naming_doc_records_compatibility_plan():
    body = (REPO / "doc/harness/patterns/maintenance-state-naming.md").read_text(
        encoding="utf-8"
    )

    assert "compatibility surfaces" in body
    assert ".maintain-pending.json" in body
    assert ".maintain-last-run" in body
    assert ".maintain-observe.log" in body
    assert "maintain_restore.py" in body
    assert "hygiene_restore.py" in body
    assert ".hygiene-pending.json" in body
    assert ".hygiene-last-run" in body
    assert ".hygiene-observe.log" in body
    assert "Read both old and new locations" in body
    assert "no-loss migration" in body
    assert "The first compatibility slice is implemented" in body
