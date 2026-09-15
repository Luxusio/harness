from __future__ import annotations

import json
import re
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CANONICAL_PROGRESS_KEYS = {
    "phase",
    "current_ac",
    "partial_ac",
    "completed_acs",
    "allowed_paths",
    "test_paths",
    "forbidden_paths",
}
CANONICAL_PROGRESS_ORDER = [
    "phase",
    "current_ac",
    "partial_ac",
    "completed_acs",
    "allowed_paths",
    "test_paths",
    "forbidden_paths",
]


def _top_level_yaml_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-z_]+):(?:\s|$)", line)
        if match:
            keys.add(match.group(1))
    return keys


def _top_level_yaml_fields(path: Path) -> list[tuple[str, str]]:
    fields: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-z_]+):(.*)$", line)
        if match:
            fields.append((match.group(1), match.group(2).strip()))
    return fields


def test_compact_planning_is_procedural_and_fail_closed():
    claude = (REPO / "plugin/skills/plan/intake.md").read_text(encoding="utf-8")
    codex = (REPO / "plugin-codex/internal-skills/plan/SKILL.md").read_text(
        encoding="utf-8"
    )

    for body in (claude, codex):
        normalized = " ".join(body.split())
        assert "compact" in normalized and "full" in normalized
        assert "Unknown means full" in normalized
        for trigger in (
            "security/auth/permissions/secrets",
            "data/schema",
            "public API or observable UI behavior",
            "destructive operations",
            "dependency",
            "platform",
            "configuration",
            "workflow-control",
            "material user choice",
            "cross-component scope",
            "high-risk maintenance",
        ):
            assert trigger in normalized
        assert "TASK.json" in normalized
        assert "standard" in normalized and "micro" in normalized
    assert "user asks for a full plan" in claude
    assert "explicit request" in codex
    for body in (claude, codex):
        assert "inspection" in body and "escalation" in body
        assert "restart" in body and "full Phase 1" in body


def test_optional_plan_session_is_removed_after_publication():
    plan = (REPO / "plugin/skills/plan/SKILL.md").read_text(encoding="utf-8")
    intake = (REPO / "plugin/skills/plan/intake.md").read_text(encoding="utf-8")
    writer = (REPO / "plugin/skills/plan/write-artifacts.md").read_text(
        encoding="utf-8"
    )
    codex = (REPO / "plugin-codex/internal-skills/plan/SKILL.md").read_text(
        encoding="utf-8"
    )

    assert "Do not create\nPLAN_SESSION.json by default" in plan
    assert "Its absence is normal" in intake
    assert "malformed legacy scratch is equivalent to absent scratch" in intake
    assert "MUST re-probe current availability" in intake
    assert "HARNESS_DISABLE_CROSS_MODEL" in intake
    assert "remove task-local PLAN_SESSION.json" in writer
    assert "Remove it after successful `write_plan`" in codex


def test_compact_review_report_never_claims_full_review():
    writer = (REPO / "plugin/skills/plan/write-artifacts.md").read_text(
        encoding="utf-8"
    )
    compact = writer.split("Compact procedure:", 2)[-1]
    assert "ASSESSED_COMPACT" in compact
    assert "no full dual-voice review is claimed" in compact
    assert "REVIEWED — plan has passed the full dual-voice pipeline" not in compact

    for path in (
        REPO / "plugin/skills/plan/SKILL.md",
        REPO / "plugin-codex/internal-skills/plan/SKILL.md",
    ):
        body = path.read_text(encoding="utf-8")
        assert "Missing compact evidence" in body
        assert "full Phase 1" in body


def test_both_planning_procedures_ask_only_unresolved_material_decisions():
    for path in (
        REPO / "plugin/skills/plan/SKILL.md",
        REPO / "plugin-codex/internal-skills/plan/SKILL.md",
    ):
        body = path.read_text(encoding="utf-8")
        normalized = " ".join(body.split())
        assert "One decision interaction" in normalized
        assert "up to three" in normalized
        assert "explicitly requested pre-code plan approval" in normalized
        assert "specific delta to §5.3" in normalized
        assert "Compact/full selection alone never runs it" in normalized
        assert "review depth" in normalized and "approval requirement" in normalized
        assert "one at a time" not in body
        assert "Do not batch" not in body
        assert "Full planning asks for premise confirmation" not in body
        assert "Sequential execution.** 0 → 1 → 2 → 3 → 4 → 5 → 6" not in body
    codex = (REPO / "plugin-codex/internal-skills/plan/SKILL.md").read_text(
        encoding="utf-8"
    )
    assert "Three mandatory user-gates remain" not in codex


def test_thin_request_does_not_force_a_compact_prompt():
    intake = (REPO / "plugin/skills/plan/intake.md").read_text(encoding="utf-8")
    prerequisite = intake.split("## Phase 0.4.5: Prerequisite offer", 1)[1].split(
        "## Phase 0.5:", 1
    )[0]
    assert "use it as the request summary" in prerequisite
    assert "skip this offer even when Phase 0.7 must select full" in prerequisite
    assert "Never couple this offer to compact/full selection" in prerequisite
    assert "Skip →" not in prerequisite
    assert "Point to context" in prerequisite


def test_premise_authorization_and_scope_expansion_fail_closed():
    review = (REPO / "plugin/skills/plan/review-phases.md").read_text(
        encoding="utf-8"
    )
    principles = (REPO / "plugin/skills/plan/decision-principles.md").read_text(
        encoding="utf-8"
    )
    writer = (REPO / "plugin/skills/plan/write-artifacts.md").read_text(
        encoding="utf-8"
    )

    assert "Premise extraction and authorization (MANDATORY ANALYSIS)" in review
    assert "Authorized and evidence-backed premises do not produce a question" in review
    assert "separate premise-confirmation interaction is not" in review
    assert "P1 never grants authority" in principles
    assert "relay the unresolved decision bundle" in principles
    assert "approval field, ledger, or sidecar" in writer


def test_plan_review_lenses_defer_user_interaction_to_parent():
    forbidden_direct_interaction = (
        "Ask one decision per",
        "ask separately",
        "wait for the next turn",
        "stop for the answer",
        "Do not continue without a confirmed",
        "obtain explicit selection",
        "ask the user to choose",
        "ask the user to select",
        "ask the user to confirm",
        "obtain user approval",
        "obtain the user's decision",
        "individual question",
        "then ask:",
        "Confirm the mode and chosen implementation approach before proceeding",
        "ask before removing",
        "ask before every cut",
        "For each non-trivial decision, return a candidate",
        "For every non-trivial choice, return a decision candidate",
        "Queue taste choices for the user gate",
    )
    for runtime in ("plugin/skills", "plugin-codex/internal-skills"):
        for name in (
            "plan-ceo-review", "plan-eng-review", "plan-design-review",
            "plan-devex-review",
        ):
            body = (REPO / runtime / name / "SKILL.md").read_text(encoding="utf-8")
            ownership = body.split("## Parent interaction ownership", 1)[1].split("##", 1)[0]
            normalized_ownership = " ".join(ownership.split())
            assert "do not" in normalized_ownership
            assert "wait for the user" in normalized_ownership
            assert "unresolved material-decision candidate" in normalized_ownership
            assert "single post-review decision interaction" in normalized_ownership
            assert "material" in body
            assert all(
                phrase not in body for phrase in forbidden_direct_interaction
            ), f"{runtime}/{name} still owns a direct user interaction"

    review_phases = (REPO / "plugin/skills/plan/review-phases.md").read_text(
        encoding="utf-8"
    )
    both_fail = next(
        line for line in review_phases.splitlines() if "Both voices fail" in line
    )
    assert "do not create a separate user interaction" in both_fail
    assert "AskUserQuestion" not in both_fail


def test_new_progress_fixtures_have_exact_seven_key_shape():
    for name in ("PROGRESS_valid.md", "PROGRESS_forbidden.md"):
        path = REPO / "tests/fixtures/gstack_adoption" / name
        assert _top_level_yaml_keys(path) == CANONICAL_PROGRESS_KEYS
        fields = _top_level_yaml_fields(path)
        assert [key for key, _ in fields] == CANONICAL_PROGRESS_ORDER
        values = dict(fields)
        for key in ("phase", "current_ac", "partial_ac"):
            assert values[key]
        for key in ("completed_acs", "allowed_paths", "test_paths", "forbidden_paths"):
            assert values[key] in {"", "[]"}


def test_develop_skills_publish_same_progress_contract():
    for path in (
        REPO / "plugin/skills/develop/SKILL.md",
        REPO / "plugin-codex/internal-skills/develop/SKILL.md",
    ):
        body = path.read_text(encoding="utf-8")
        for key in CANONICAL_PROGRESS_KEYS:
            assert f"{key}:" in body
        positions = [body.index(f"{key}:") for key in CANONICAL_PROGRESS_ORDER]
        assert positions == sorted(positions)
        assert "exactly these\nseven top-level keys" in body
        assert "Do not add an `attempts` key" not in body

    fix_first = (REPO / "plugin/skills/develop/fix-first-pattern.md").read_text(
        encoding="utf-8"
    )
    assert "Do not add an `attempts` key" in fix_first


def test_replay_corpus_uses_only_supported_task_modes():
    corpus = json.loads(
        (REPO / "doc/harness/replays/golden-corpus.json").read_text(encoding="utf-8")
    )
    modes = {
        case.get("expect", {}).get("execution_mode")
        for case in corpus["cases"]
        if "execution_mode" in case.get("expect", {})
    }
    assert modes <= {"standard", "micro"}


def test_post_close_guidance_uses_current_run_and_verified_close_apis():
    body = (REPO / "plugin/skills/run/self-improvement.md").read_text(
        encoding="utf-8"
    )
    assert '--task "<task_id>" --task-run-id "<task_run_id>"' in body
    assert 'retro.py --count-closed-since "$_LAST_RETRO_TS"' in body
    assert "os.path.getmtime(path)" not in body

    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "append-only raw signal ledger" in readme
    assert "raw signals, session-transient" not in readme
