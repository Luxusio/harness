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
        assert "hypotheses until checked against the repo" in body
        assert "partially-confirmed" in body
        assert "needs-runtime-check" in body
        assert "alternative evidence tier" in body
        assert "Status: none | applied | deferred | rejected" in body
        assert "AskUserQuestion" in body
        assert "request_user_input" in body
        assert "user_decision:" in body
        assert "proposed_artifact:" in body
        assert "User Feedback Event Review" in body
        assert "USER_FEEDBACK.jsonl" in body
        assert "durable source of truth" in body
        assert "by itself" in body
        assert "before the next action that depends on it" in body
        assert "User Feedback Disposition" in body
        assert "promoted|handled-local|deferred|rejected" in body
        assert "needs-user-decision` is not a closeable disposition" in body


def test_developer_prompts_reference_handoff_close_gate_guide():
    for rel in ("plugin/agents/developer.md", "plugin-codex/agents/developer.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "adjacent `HANDOFF_CLOSE_GATE.md`" in body
        assert "close-gate sections from that guide" in body


def test_handoff_close_gate_guide_names_strict_contract():
    for rel in ("plugin/agents/HANDOFF_CLOSE_GATE.md", "plugin-codex/agents/HANDOFF_CLOSE_GATE.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "User Feedback Disposition" in body
        assert "event: <id> status: <promoted|handled-local|deferred|rejected>" in body
        assert "Commit-backed Learnings" in body
        assert "Status: <none|captured|rejected>" in body
        assert "changed/touched" in body
        assert "commit-eligible repo artifact" in body
        assert "doc/harness/learnings.jsonl" in body
        assert "untouched existing files do not" in body
        assert "Self-Healing Candidates" in body
        assert "Status: <none|applied|deferred|rejected>" in body
        assert "user_decision:" in body
        assert "proposed_artifact:" in body
        assert "Durable docs: not needed" in body


def test_run_skills_check_feedback_events_before_dependent_actions():
    for rel in ("plugin/skills/run/SKILL.md", "plugin-codex/internal-skills/run/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "USER_FEEDBACK.jsonl" in body
        assert "automatic evidence from UserPromptSubmit" in body
        assert "before the next dependent action" in body
        assert "User Feedback Disposition" in body
        assert "Close-time checking only catches missed feedback" in body


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
        assert "final response" in body
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
        assert "final response" in body
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
    assert "Evidence-backed backlog shaping" in body
    assert "hypotheses until the current repository proves them" in body
    assert "confirmed" in body
    assert "partially-confirmed" in body
    assert "already-handled" in body
    assert "needs-runtime-check" in body
    assert "corrected_scope" in body
    assert "safe_fix_direction" in body


def test_harness_source_completion_requires_commit_and_force_install():
    root = (REPO / "CLAUDE.md").read_text(encoding="utf-8")
    assert "commit the completed diff" in root
    assert "python3 install.py --force" in root
    assert "before the final response" in root

    for rel in ("plugin/skills/run/SKILL.md", "plugin-codex/internal-skills/run/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "For this harness plugin source repo" in body
        assert "commit the completed diff" in body
        assert "python3 install.py --force" in body
        assert "final response" in body
        assert "force-install result" in body


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
