"""Instruction regressions for grounded planning and recovery authority."""
import importlib.util
from pathlib import Path
import re
import sys

REPO = Path(__file__).resolve().parents[1]


def test_planning_completeness_has_no_sentence_quota():
    paths = ("plugin/skills/plan/SKILL.md", "plugin/skills/plan/review-phases.md",
             "plugin-codex/internal-skills/plan/SKILL.md")
    for path in paths:
        body = (REPO / path).read_text()
        assert "fewer than 3 sentences" not in body
        assert "compression signal" not in body
        assert "what was examined" in body


def test_discovery_amends_current_outcomes_without_promoting_user_intent():
    for runtime in ("plugin/skills", "plugin-codex/internal-skills"):
        body = (REPO / runtime / "develop/SKILL.md").read_text()
        gate = body.split("## Premise Gate / User Challenge", 1)[1].split("## Error Philosophy", 1)[0]
        assert "necessary helpers and implementation alternatives" in gate
        assert "current PLAN/WBS" in gate and "autonomously" in gate
        assert "material user decision" in gate and "lacks authorization" in gate
        assert "fire the Premise Gate conversational ask" not in body
        assert "user-confirmed direction before changing course" not in body
        assert "fresh-task / continue-anyway" not in body
        assert "run plan skill / check task_id / abort" not in body
        assert "Required planning, independent review and QA cannot be skipped" in body


def test_rolling_wbs_keeps_dispatch_ownership_separate():
    body = (REPO / "plugin/skills/plan/write-artifacts.md").read_text()
    assert "do not freeze test commands or" in body
    assert "implementation layout at intake" in body
    assert "Add ownership only when assigning writers" in body
    assert "Supported implementation work may enter WBS" in body
    assert "cannot introduce a" in body


def test_loaded_recovery_chain_preserves_authority_without_attempt_quotas(tmp_path):
    """Inspect the subfiles actually delivered to Codex, not just SKILL.md."""
    spec = importlib.util.spec_from_file_location("harness_install_recovery", REPO / "install.py")
    install = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = install
    spec.loader.exec_module(install)
    payload = tmp_path / "harness"
    install._build_codex_payload(payload, payload)

    shared = ("fix-first-pattern.md", "verification-gate.md",
              "hypothesis-driven-debugging.md", "test-failure-triage.md")
    for name in shared:
        source = REPO / "plugin/skills/develop" / name
        delivered = payload / "internal-skills/develop" / name
        expected = source.read_text().replace(
            "${CLAUDE_PLUGIN_ROOT}/skills/", "${HARNESS_PLUGIN_ROOT}/internal-skills/"
        ).replace("${CLAUDE_PLUGIN_ROOT}", "${HARNESS_PLUGIN_ROOT}")
        assert delivered.read_text() == expected, name

    public = (REPO / "plugin/skills/develop/SKILL.md").read_text()
    codex = (payload / "internal-skills/develop/SKILL.md").read_text()
    for name in shared[:2]:
        assert name in public and name in codex
    gate = (payload / "internal-skills/develop/verification-gate.md").read_text()
    for name in shared[2:]:
        assert name in gate

    # parallel-fanout is a Claude-loaded file; Codex delivers ready-lanes
    # instead. Exercise its actual payload builder rather than inventing a
    # Codex dependency just to make a source scan look transitive.
    claude_payload = tmp_path / "claude"
    install._build_claude_payload(claude_payload)
    claude_develop = claude_payload / "plugin/skills/develop"
    delivered_public = (claude_develop / "SKILL.md").read_text()
    fanout = (claude_develop / "parallel-fanout.md").read_text()
    assert "parallel-fanout.md" in delivered_public
    assert fanout == (REPO / "plugin/skills/develop/parallel-fanout.md").read_text()

    for name, body in [("public develop", public), ("Codex develop", codex),
                       ("Claude parallel fanout", fanout), *(
        (name, (payload / "internal-skills/develop" / name).read_text()) for name in shared
    )]:
        assert not re.search(r"3[- ](?:cycle|attempt|strike)|(?:maximum|only get) 3|>5 files|WTF score",
                             body, re.IGNORECASE), name
        assert "DONE_WITH_CONCERNS" not in body, name
    assert "unknown effects" in (payload / "internal-skills/develop/fix-first-pattern.md").read_text()
    assert "Required FAIL or BLOCKED_ENV" in gate
    assert "affected independent review PASS before actual QA" in gate
    assert "retry PASS alone does not clear" in gate
    assert "missing authorization" in gate
    for body in (public, codex):
        assert "do not lower thresholds to clear a failure" in body
        assert "Required failures remain unfinished" in body

    helper = next(line for line in fanout.splitlines() if "| Helper-extract-first |" in line)
    assert "necessary helper discovered during implementation" in helper
    assert "current PLAN/WBS autonomously" in helper
    assert "actual execution ownership before assigning writers" in helper
    assert "dependent consumers" in helper
    assert "Do not invent helper progress nodes" in helper
    assert "material unresolved decision or missing authorization" in helper
    reconciliation = fanout.split("No fix crosses between", 1)[1].split("Fallback:", 1)[0]
    assert "without an\nattempt quota" in reconciliation
    assert "never clears it or changes\nexecution ownership" in reconciliation
    assert "updated assignment before writing" in reconciliation
    assert "Required failures remain unfinished" in reconciliation
    assert "independent review PASS before actual QA" in reconciliation
