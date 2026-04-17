---
freshness: current
---

# Regression Capture Pattern

QA agents (qa-cli, qa-api) emit `codifiable:` YAML blocks in their transcripts.
The `qa_codifier.py` script parses these and stages validated regression tests.

## Codifiable block schema

```yaml
codifiable:
  - behavior: short_snake_case_name
    command: "exact bash command"
    expected_exit: 0
    expected_stdout_contains: ["substring1", "substring2"]
    expected_stderr_contains: []
```

Fields:
- `behavior`: snake_case name, becomes the test function name
- `command`: exact shell command string (run with `shell=True`)
- `expected_exit`: expected process exit code (integer)
- `expected_stdout_contains`: list of substrings that must appear in stdout
- `expected_stderr_contains`: list of substrings that must appear in stderr

Multiple `codifiable:` blocks per transcript are allowed.

## When QA agents should emit codifiable blocks

Emit when ALL of these hold:
1. The AC verification reduces to a single shell command.
2. The expected output is deterministic (same command always produces same result on this repo).
3. The check is meaningful as a regression (not "binary exists on PATH" trivia).

Do NOT emit for: browser interactions, multi-step flows, outputs that vary by date/environment.

## Codifier behavior (qa_codifier.py)

Invoked from develop Phase 3.5 after Phase 7 PASS, before Phase 8 HANDOFF:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/qa_codifier.py --task-dir <task_dir> 2>/dev/null || true
```

Pipeline:
1. Read `<task_dir>/CRITIC__runtime.md` for codifiable blocks.
2. Infer test format from `manifest.yaml test_command` (pytest / bun:test / vitest / shell).
3. Stage rendered test at `<task_dir>/audit/regression-draft/<sanitized-task-id>/<behavior>.<ext>`.
4. Compile-check: `python3 -c compile(...)` or `node --check`.
5. On pass: move to `tests/regression/<sanitized-task-id>/<behavior>.<ext>`.
6. On fail: log `codifier-fail` to learnings, leave in staging, continue.
7. If no blocks: log `codifier-empty`, exit 0.

## Regression test quarantine rule

`tests/regression/<task-id>/` may be deleted when the owning task's HANDOFF.md
is quoted in the retiring commit message. Do not delete while the task is still
referenced in active post-mortems or CONTRACTS.

## Pattern entries

| Pattern | Discovered | Source |
|---------|------------|--------|
| codifiable-block-schema | 2026-04-17 | TASK__gstack-ideas-adoption |
| regression-test-quarantine | 2026-04-17 | TASK__gstack-ideas-adoption |
