from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CODEX_RUN = REPO / "plugin-codex" / "internal-skills" / "run" / "SKILL.md"
CLAUDE_RUN = REPO / "plugin" / "skills" / "run" / "SKILL.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"
GENERAL_PATTERNS = REPO / "doc" / "harness" / "patterns" / "general.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_codex_run_uses_capability_first_subagent_routing():
    body = _text(CODEX_RUN)

    assert "Codex Subagent Routing" in body
    assert "Route from the current session tools and the task shape" in body
    assert "not from whether the\nuser explicitly requested delegation" in body
    assert '"The user did not ask for parallel\nagents" is not a valid reason' in body
    assert "Do not wait for the user\nto request delegation" in body
    assert "User request is not a condition for parallel routing" in body
    assert "the user does not need to request delegation" in body
    assert "spawn_agent {" in body
    assert "SUBAGENT_RECEIPTS.jsonl" in body
    assert "Do not call a harness receipt tool" in body
    assert 'agent_type: "harness:qa-cli"' in body
    assert 'agent_type: "worker"' in body
    assert 'agent_type: "explorer"' in body
    assert "When `spawn_agent` is available and work is independent, use it" in body
    assert "Use inline execution as the fallback" in body
    assert "record `parallel-trigger-skipped` evidence" in body
    assert "vague reasons such as lack of user request are invalid" in body


def test_codex_run_documents_qa_subagent_call_shape():
    body = _text(CODEX_RUN)

    assert "QA subagent pattern on Codex" in body
    assert "Verify (QA — capability-routed on Codex)" in body
    assert "You are the qa-<lens> lens for <task_id>" in body
    assert "plugin-codex/agents/qa-<lens>.md" in body
    assert 'agent_type: "harness:qa-<lens>"' in body
    assert "concrete findings" in body
    assert "must not invent a PASS from its own context" in body
    assert "`subagent_receipts`" in body
    assert "Runtime Fallbacks" in body
    assert "Agent` fan-out routed through `spawn_agent` when available" in body


def test_run_skills_document_parallel_ux_lens_routing():
    for path in (CODEX_RUN, CLAUDE_RUN):
        body = _text(path)
        assert "ux-browser" in body
        assert "ux-cli" in body
        assert "ux-api" in body
        assert "ux-desktop" in body
        assert "final response" in body
        assert "SUBAGENT_RECEIPTS.jsonl" in body
        assert "runtime_verdict" in body


def test_run_skills_document_resume_detection_and_verify_reconciliation():
    for path in (CODEX_RUN, CLAUDE_RUN):
        body = _text(path)
        assert "Phase 0: Resume detection" in body
        assert "resume rather than creating a duplicate" in body or "resume instead of creating a duplicate" in body
        assert "PLAN.md missing → Phase 2 Plan" in body
        assert "runtime_verdict is not PASS → Phase 3 Develop/Verify" in body
        assert "reconcile_acs" in body
        assert "task_verify" in body
        assert "failed/deferred" in body


def test_run_skills_document_separate_hygiene_followup_policy():
    for path in (CODEX_RUN, CLAUDE_RUN):
        body = _text(path)
        assert "Schedule pending hygiene as a separate follow-up task" in body
        assert "do not mix unrelated" in body
        assert "primary task" in body


def test_run_skills_require_auto_followup_before_done():
    for path in (CODEX_RUN, CLAUDE_RUN):
        body = _text(path)
        assert "Mandatory Follow-up Continuation" in body
        assert 'returns `"action": "run_followup"`' in body
        assert "Do not send a final completion response yet" in body
        assert "continue the follow-up unless the user explicitly" in body
        assert "HARNESS_AUTO_FOLLOWUP_MAX" in body
        assert "Before writing DONE, assert:" in body
        assert "no auto-runnable follow-up task remains open" in body


def test_codex_develop_no_longer_says_agent_absence_is_absolute():
    body = _text(CODEX_DEVELOP)

    forbidden = [
        "No `Agent(subagent_type=...)` fan-out in v1.5",
        "Codex has no parallel `Agent(subagent_type=...)` primitive",
        "On Codex v1.5 there is no agent primitive",
        "Multi-lens concurrent spawning (qa-browser + qa-api combined in one batch) is deferred to v2",
        "absence of an `Agent` fan-out primitive",
        "sequential inline passes",
        "Phase 4 Agent() -> \"inline on Codex\"",
        "both nonexistent on Codex",
        "Sequential degradation",
        "codex has no fan-out in v1.5",
        "Codex just always picks \"sequential\"",
        "On Codex v1.5 it's an inline pass",
        "On Codex v1.5 it runs sequentially",
        "not parallel agents",
        "same-context pass",
        "no Voice A / Voice B Agent fan-out",
        "complex dual-voice review remains Claude-only",
        "Claude-only in v1.5",
        "Develop phase is Claude-only in v1.5",
        "no parallel agent fan-out to amortize",
        "Claude-only concept",
        "sequential is always the schedule",
        "AC Dependency Analysis (sequential on Codex)",
        "RUN_DOGFOOD runs the inline",
        "Codex runs it as a second adversarial pass in the same context",
    ]
    combined = _text(CODEX_RUN) + "\n" + body
    for phrase in forbidden:
        assert phrase not in combined

    assert "Agent fan-out is capability-gated" in body
    assert "spawn_agent {" in body
    assert "Runtime Fallbacks" in body


def test_runtime_fallback_notes_are_exception_only():
    combined = _text(CODEX_RUN) + "\n" + _text(CODEX_DEVELOP)

    assert "keep routine work free of runtime routing notes" in combined
    assert "Add a short `Runtime Fallbacks` section when" in combined
    noisy_phrases = [
        "always log routing",
        "runtime routing log",
        "capability-routing timeline event",
    ]
    for phrase in noisy_phrases:
        assert phrase not in combined


def test_feedback_pattern_records_writing_guidance_only():
    body = _text(GENERAL_PATTERNS)
    section = body.split("## Feedback-Derived Rule Writing", 1)[1]

    assert "When documenting user corrections" in section
    assert "readable prose" in section
    assert "Verify by using the form" in section
    assert "spawn_agent" not in section
    assert "subagent" not in section.lower()
