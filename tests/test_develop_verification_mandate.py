from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
VERIFY = REPO / "plugin" / "skills" / "develop" / "verification-gate.md"
QUALITY = REPO / "plugin" / "skills" / "develop" / "quality-audit-pipeline.md"
PARALLEL = REPO / "plugin" / "skills" / "develop" / "parallel-fanout.md"
CONTRACTS = REPO / "CONTRACTS.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_develop_forbids_verification_opt_in_prompt():
    body = _text(DEVELOP)

    assert "Highest-tier verification mandate" in body
    assert 'Do not ask "should I verify it?"' in body
    assert "Running the highest available verification tier is not scope expansion" in body
    assert "No verification opt-in prompt" in body


def test_verification_gate_runs_highest_available_tier_without_asking():
    body = _text(VERIFY)

    assert "Run the highest available verification tier without asking" in body
    assert "created or unblocked a live/API/browser/CLI verification path" in body
    assert "Local rebuilds, dev server restarts, local DB seeds" in body
    assert "Stop to ask only for destructive state changes" in body


def test_contract_records_highest_available_verification_rule():
    body = _text(CONTRACTS)

    assert "Highest available verification is part of the task" in body
    assert "asking the user whether to verify" in body
    assert "external/destructive blocker" in body


def test_develop_workflow_uses_unified_task_control_not_git_freshness():
    body = "\n".join(_text(path) for path in (DEVELOP, QUALITY, PARALLEL))

    assert "current TASK.json generation" in body
    assert "TASK.json.close_receipt_fingerprint" in body
    for obsolete in (
        "current `TASK_RUN`",
        "PLAN.meta.json.plan_meta.surfaces",
        "PASS verdict must be fresh after the last edit",
        "Source of truth: `TASK_STATE.yaml touched_paths`",
        "stale HEAD, or changed worktree fingerprint",
        "Any source edit invalidates all review receipts",
    ):
        assert obsolete not in body
