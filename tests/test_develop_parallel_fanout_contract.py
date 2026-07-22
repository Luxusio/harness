from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
PARALLEL_FANOUT = REPO / "plugin" / "skills" / "develop" / "parallel-fanout.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md"
CONTRACTS = REPO / "CONTRACTS.md"
AC_WORKER = REPO / "plugin" / "agents" / "ac-worker.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_claude_develop_requires_lane_table_before_implementation():
    body = _text(CLAUDE_DEVELOP)

    assert "| AC | Files | Depends on | Lane | Route | Reason |" in body
    assert "`Route` must be one of: `Agent(...)`, `sequential-prelude`, `sequential-dependent`" in body
    assert "Fill the table before editing files" in body
    assert "two or more independent `Agent(...)` rows" in body


def test_claude_develop_forbids_collapsing_independent_acs_into_one_executor():
    body = _text(CLAUDE_DEVELOP)

    assert 'Agent(name="<task_id>:AC-001"' in body
    assert 'Agent(name="<task_id>:AC-002"' in body
    assert 'subagent_type="harness:ac-worker"' in body
    assert "Use one Agent per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "Do not edit PROGRESS.md or CHECKS.yaml" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body


def test_parallel_fanout_small_task_skip_requires_evidence_and_coordinator_merge():
    body = _text(PARALLEL_FANOUT)

    assert "Parallel is the default posture" in body
    assert "Mandatory parallel delegation is" in body
    assert "User request is not a condition\nfor parallel routing" in body
    assert "do not reduce worker count for independent ACs" in body
    assert "Executors return status, changed paths, and blockers in their final response" in body
    assert "coordinator" in body and "only writer to PROGRESS.md and CHECKS.yaml" in body
    assert "Merge cost controls batch size only" in body
    assert "does not justify collapsing two or more" in body
    assert "For N>4, spawn batches of up to 4" in body
    assert "AC ids, estimated lines, estimated runtime" in body
    assert '`reason:"small-task"`' in body
    assert "this opt-out is disabled" in body


def test_claude_develop_parallelizes_full_suite_verification():
    body = _text(CLAUDE_DEVELOP)
    fanout = _text(PARALLEL_FANOUT)

    assert "full-suite verification MUST be delegated to qa-* agents" in body
    assert "Spawn every applicable lens" in body
    assert "Phase 7 multi-lens QA" in fanout
    assert "Phase 7.7 dogfooder" in fanout
    assert "Spawn every applicable lens in one message" in fanout


def test_contracts_do_not_reintroduce_single_agent_default():
    body = _text(CONTRACTS)

    assert "parallel agents = 1 by default" not in body
    assert "fewer parallel agents" not in body
    assert "Develop fanout is" in body
    assert "plugin/skills/develop/parallel-fanout.md" in body


def test_harness_ac_worker_is_scoped_to_one_ac_and_no_shared_artifacts():
    body = _text(AC_WORKER)

    assert "Implement only the assigned AC or lane" in body
    assert "Do not write `PLAN.md`, `SUBAGENT_RECEIPTS.jsonl`" in body
    assert "`PROGRESS.md`, or `CHECKS.yaml`" in body
    assert "Do not collapse multiple independent ACs into your lane" in body


def test_codex_develop_uses_spawn_agent_lane_analysis_not_sequential_default():
    body = _text(CODEX_DEVELOP)

    assert "Phase 3.0: AC Dependency Analysis" in body
    assert "AC Dependency Analysis (sequential on Codex)" not in body
    assert "| AC | Files | Depends on | Lane | Route | Reason |" in body
    assert "`Route` is `spawn_agent(worker)`" in body
    assert "spawn one worker per" in body
    assert "Use one worker per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "state the fallback in task state or final response" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body


def test_codex_develop_rejects_user_request_based_parallel_skip():
    body = _text(CODEX_DEVELOP)

    assert "capability-gated, not user-request-gated" in body
    assert "The user does not need to ask for delegation" in body
    assert "`user did not ask for delegation` is an invalid" in body
    assert "`delegation was not requested`" in body
    assert "Do not wait for the user to request delegation" in body
    assert "User request is\nnot a condition for parallel routing" in body
    assert "mandatory capability/task-shape routing" in body


def test_codex_develop_sequential_fallback_requires_skip_evidence_payload():
    body = _text(CODEX_DEVELOP)

    assert "Sequential fallback must" in body
    assert "state `ac_count`" in body
    assert "`conflict` (specific" in body
    assert "`estimated_lines`, `estimated_seconds`" in body
    assert "Valid reasons are only `spawn_agent-unavailable`" in body
    assert "`dependency-conflict`, or `small-task`" in body


def test_coordinator_review_precedes_generic_parallel_failure_retry():
    required = (
        "needs-coordinator-review",
        "before generic rollback",
        "never retry",
        "same ownership",
        "reassign ownership",
        "amend",
        "escalate",
    )
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = " ".join(_text(path).lower().split())
        for fragment in required:
            assert fragment in body, f"{path}: missing {fragment!r}"


def test_codex_worker_prompt_produces_coordinator_review_status():
    body = " ".join(_text(CODEX_DEVELOP).lower().split())

    assert "read `plugin-codex/agents/developer.md`" in body
    assert "return the exact status `needs-coordinator-review`" in body
    assert "ownership, lane, or approved scope" in body


def test_coordinator_review_keeps_successful_independent_siblings_promoted():
    for path in (CLAUDE_DEVELOP, CODEX_DEVELOP):
        body = " ".join(_text(path).lower().split())
        assert "keep successful independent siblings promoted" in body
