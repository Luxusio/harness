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
        assert "gitignored staging" in body
        assert "committed artifact" in body
        assert "changed a committed" in body
        assert "Self-Healing Candidates" in body
        assert "development friction, QA-discovered" in body
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
        assert "do not" in body and "USER_FEEDBACK.jsonl" in body
        assert "durable source of truth" in body
        assert "by itself" in body
        assert "before the next action that depends on it" in body


def test_developer_prompts_do_not_reference_handoff_close_gate_guide():
    for rel in ("plugin/agents/developer.md", "plugin-codex/agents/developer.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "HANDOFF_CLOSE_GATE.md" not in body
        assert "close-gate sections from that guide" not in body


def test_handoff_close_gate_guide_removed():
    for rel in ("plugin/agents/HANDOFF_CLOSE_GATE.md", "plugin-codex/agents/HANDOFF_CLOSE_GATE.md"):
        assert not (REPO / rel).exists()


def test_run_skills_check_feedback_events_before_dependent_actions():
    for rel in ("plugin/skills/run/SKILL.md", "plugin-codex/internal-skills/run/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "USER_FEEDBACK.jsonl" in body
        assert "does not" in body
        assert "separate feedback sidecar" in body
        assert "explicit user corrections" in body


def test_codex_subagent_waiting_avoids_rapid_polling_noise():
    run = (REPO / "plugin-codex/internal-skills/run/SKILL.md").read_text(encoding="utf-8")
    develop = (REPO / "plugin-codex/internal-skills/develop/SKILL.md").read_text(encoding="utf-8")
    assert "Subagent wait UX" in run
    assert "rapid 10/20/30-second wait loops" in run
    assert "one compact status update" in run
    assert "Use `wait_agent` only to coordinate" in run
    assert "`wait_agent` and `list_agents` output do not author receipts" in run
    assert "never use rapid short polling" in develop
    assert "and `list_agents` do not author receipts" in develop


def test_qa_agents_surface_self_healing_candidates():
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
        assert "Self-Healing Candidates" in body
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
    assert "before `task_close`" in root
    assert "must not introduce a second install phase" in root

    for rel in ("plugin/skills/run/SKILL.md", "plugin-codex/internal-skills/run/SKILL.md"):
        body = (REPO / rel).read_text(encoding="utf-8")
        assert "For this harness plugin source repo" in body
        assert "commit the completed diff" in body
        assert "python3 install.py --force" in body
        assert "final response" in body
        assert "force-install result" in body
        assert "stateless root installer" in body


def test_develop_installs_harness_after_fresh_qa_before_close():
    paths = (
        REPO / "plugin/skills/develop/SKILL.md",
        REPO / "plugin-codex/internal-skills/develop/SKILL.md",
    )
    for path in paths:
        body = path.read_text(encoding="utf-8")
        install_at = body.index("### Phase 7.8: Harness source auto-install")
        close_heading = (
            "### Phase 9: Final verification, install, close, and response"
            if "plugin-codex" in str(path)
            else "### Phase 8: Close and final response"
        )
        close_at = body.index(close_heading)
        assert install_at < close_at
        section = body[install_at:close_at]
        assert "plugin/scripts/install_verified.py" in section
        assert "python3 install.py --force" in section
        assert "terminal ordered" in section
        assert "failed install blocks completion" in section.lower()


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
