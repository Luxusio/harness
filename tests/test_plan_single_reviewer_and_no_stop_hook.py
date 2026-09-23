"""Plan review runs one independent reviewer; Claude registers no Stop hook.

User request 2026-09-23 (TASK__plan-single-voice-review):
- Historical full reviews showed the second plan voice almost never changed a
  decision, and Phase 1 Voice B reran the identical prompt on same-model
  transport. Each full review phase now spawns one independent reviewer
  subagent; the cross-model transport, consensus table and dual-voice
  degradation matrix are gone from both runtimes.
- The Claude Stop hook turned every wait on a background reviewer into repeated
  "Do not stop" turns. Continuation is native /goal; completion is still gated
  by task_close.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CLAUDE_PLAN = [
    REPO / "plugin/skills/plan" / name
    for name in (
        "SKILL.md",
        "review-phases.md",
        "intake.md",
        "decision-principles.md",
        "write-artifacts.md",
    )
]
CODEX_PLAN = REPO / "plugin-codex/internal-skills/plan/SKILL.md"
CODEX_RUN = REPO / "plugin-codex/internal-skills/run/SKILL.md"
DX_HALL = REPO / "plugin/skills/plan-devex-review/dx-hall-of-fame.md"

DUAL_VOICE = re.compile(
    r"voice a\b|voice b\b|dual[- ]voice|consensus|cross[-_ ]model"
    r"|single-voice|Phase 0\.3",
    re.IGNORECASE,
)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_no_plan_skill_file_keeps_dual_voice_machinery():
    for path in [*CLAUDE_PLAN, CODEX_PLAN, CODEX_RUN, DX_HALL]:
        hits = [
            f"{i}: {line.strip()}"
            for i, line in enumerate(_text(path).splitlines(), 1)
            if DUAL_VOICE.search(line)
        ]
        assert not hits, f"{path.relative_to(REPO)} still describes dual voices: {hits}"


def test_single_reviewer_protocol_terms_are_shared_across_runtimes():
    review_phases = _text(REPO / "plugin/skills/plan/review-phases.md")
    writer = _text(REPO / "plugin/skills/plan/write-artifacts.md")
    codex = _text(CODEX_PLAN)

    assert "independent reviewer subagent" in review_phases
    assert "| dimension | risk | finding | decision |" in review_phases
    assert "## Prior phase findings" in review_phases
    for body in (review_phases, codex):
        assert "coordinator-only" in body
    assert "REVIEWED_DEGRADED" in writer
    assert "REVIEWED_DEGRADED" in codex
    assert "| Phase | Ran | Reviewer | Findings | User Challenges |" in writer


def test_reviewer_failure_never_opens_a_user_interaction():
    review_phases = _text(REPO / "plugin/skills/plan/review-phases.md")
    row = next(
        line
        for line in review_phases.splitlines()
        if "coordinator-only" in line and line.lstrip().startswith("|")
    )
    assert "do not create a separate user interaction" in row
    assert "AskUserQuestion" not in row


def test_reviewer_brief_keeps_the_skill_definition_boundary():
    review_phases = _text(REPO / "plugin/skills/plan/review-phases.md")
    assert re.search(r"Do NOT\*\* read SKILL\.md files", review_phases)


def test_cross_model_probe_is_gone_but_scratch_tolerance_survives():
    intake = _text(REPO / "plugin/skills/plan/intake.md")
    assert "HARNESS_DISABLE_CROSS_MODEL" not in intake
    assert "omc ask" not in intake
    assert "malformed legacy scratch is equivalent to absent scratch" in intake


def test_claude_runtime_registers_no_stop_hook():
    hooks = json.loads(_text(REPO / "plugin/hooks/hooks.json"))["hooks"]
    assert "Stop" not in hooks
    # The receipt lifecycle and the other gates are untouched.
    for event in ("SessionStart", "SubagentStart", "SubagentStop", "PreToolUse",
                  "UserPromptSubmit", "PostToolUse"):
        assert event in hooks, f"{event} hook disappeared"
    assert "stop_gate.py" not in _text(REPO / "plugin/hooks/hooks.json")


def test_turn_end_contract_no_longer_claims_hook_enforcement():
    for path in (REPO / "CONTRACTS.md",
                 REPO / "plugin/skills/setup/templates/CONTRACTS.md"):
        body = _text(path)
        c17 = body[body.index("### C-17"):body.index("### C-18")]
        enforced = next(line for line in c17.splitlines()
                        if line.startswith("**Enforced by:**"))
        assert "stop_gate" not in enforced and "Stop gate" not in enforced
        assert "task_close" in c17
        assert "/goal" in c17
        assert "Bounded-yield" not in c17
        row = next(line for line in body.splitlines() if "[C-17](#c-17)" in line)
        assert row.rstrip().endswith("| soft |"), row


def test_continuation_docs_point_at_goal_not_stop_gate():
    auto_loop = _text(REPO / "doc/harness/patterns/auto-loop.md")
    assert "/goal" in auto_loop
    assert "task_close" in auto_loop
    readme = _text(REPO / "README.md")
    assert not re.search(r"^\| Stop \| `stop_gate\.py`", readme, re.MULTILINE)
    assert "Stop hook auto-wait" not in readme


def test_phase_priorities_have_one_source():
    """review-phases.md once pointed at a deleted matrix and restated
    priorities that disagreed with decision-principles.md (QA finding)."""
    review_phases = _text(REPO / "plugin/skills/plan/review-phases.md")
    assert "see matrix below" not in review_phases
    assert "Conflict priority:" not in review_phases
    assert "Per-phase priority" in review_phases
    assert "**Per-phase priority:**" in _text(REPO / "plugin/skills/plan/decision-principles.md")


def test_dormant_turn_end_gate_is_deleted():
    """TASK__remove-dormant-stop-gate-and-taste-contradiction: nothing registered
    stop_gate.py / hook_stop.py after the Stop hook removal, so they are gone."""
    for rel in ("plugin/scripts/stop_gate.py", "plugin/scripts/hook_stop.py"):
        assert not (REPO / rel).exists(), rel


def test_taste_decisions_are_recorded_not_surfaced_at_the_gate():
    files = [*CLAUDE_PLAN, CODEX_PLAN,
             REPO / "doc/common/REQ__process__plan-skill-review-pipeline.md"]
    banned = re.compile(
        r"surface at phase 5\.2|surface all auto-decided|surface every taste"
        r"|surface every decision|taste surfaced",
        re.IGNORECASE,
    )
    for path in files:
        hits = [line.strip() for line in _text(path).splitlines() if banned.search(line)]
        assert not hits, f"{path.relative_to(REPO)} still surfaces Taste: {hits}"
