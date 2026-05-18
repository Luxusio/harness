from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
CLAUDE_DEVELOP = REPO / "plugin" / "skills" / "develop" / "SKILL.md"
PARALLEL_FANOUT = REPO / "plugin" / "skills" / "develop" / "parallel-fanout.md"
CODEX_DEVELOP = REPO / "plugin-codex" / "skills" / "develop" / "SKILL.md"


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
    assert "Use one Agent per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "Do not edit PROGRESS.md or CHECKS.yaml" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body


def test_parallel_fanout_small_task_skip_requires_evidence_and_coordinator_merge():
    body = _text(PARALLEL_FANOUT)

    assert "do not reduce worker count for independent ACs" in body
    assert "Executors write per-AC result files under `<task_dir>/audit/`" in body
    assert "coordinator" in body and "only writer to PROGRESS.md and CHECKS.yaml" in body
    assert "Merge cost controls batch size only" in body
    assert "does not justify collapsing two or more" in body
    assert "AC ids, estimated lines, estimated runtime" in body
    assert '`reason:"small-task"`' in body


def test_codex_develop_uses_spawn_agent_lane_analysis_not_sequential_default():
    body = _text(CODEX_DEVELOP)

    assert "Phase 3.0: AC Dependency Analysis" in body
    assert "AC Dependency Analysis (sequential on Codex)" not in body
    assert "| AC | Files | Depends on | Lane | Route | Reason |" in body
    assert "`Route` is `spawn_agent(worker)`" in body
    assert "spawn one worker per" in body
    assert "Use one worker per independent AC" in body
    assert "Do not assign multiple independent ACs to one" in body
    assert "Runtime Fallbacks" in body
    assert "For sequential batches, work **one AC at a time**" in body
    assert "1. **One AC at a time**, in order." not in body
