---
tags: [harness, testing, xdist, isolation]
summary: 테스트는 프로세스 전역·실제 레포 상태를 형제 테스트에 누출하지 않는다. 단일 green 실행은 안정성의 증거가 아니다.
updated: 2026-09-28
freshness: suspect
invalidated_by_paths:
  - tests/conftest.py
  - tests/test_harness_mcp_server.py
  - plugin/mcp/harness_server.py
  - plugin/scripts/_lib.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/qa_codifier.py
  - pyproject.toml
freshness_updated: 2026-09-07T04:13:35Z
---

# REQ — the test suite is deterministic under pytest-xdist

## Expected behavior

`pyproject.toml` runs the suite with `-n auto --dist worksteal`. Worker count
and per-worker test composition therefore vary between runs, which makes two
properties mandatory:

1. **No test may leave process-global state changed for its siblings.** The MCP
   server owns a module global (`harness_server._SERVER`); production assigns it
   once per process, which is correct there. A test that constructs a second
   server must restore the previous value, or every later test in that worker
   reads another test's server.
2. **No test may depend on, or mutate, the developer's live repository state.**
   `doc/harness/tasks/.active` and `doc/harness/.watcher-diagnostics.json` are
   real files that the harness uses for session focus and watcher health. A test
   that reads them inherits whatever the developer's session is doing; a test
   that writes them corrupts it. `doc/harness/learnings.jsonl` is the same kind
   of file: a test that drives a ledger writer runs it against a tmp checkout
   (`make_tmp_harness_root` in `tests/conftest.py`) and asserts the row there.
3. **A single green run is not evidence of a green suite.** Under worksteal an
   isolation defect surfaces only when the offending pair shares a worker.
   Claiming "the suite passes" requires repeated runs — treat ≥10 consecutive
   green full-suite runs as the minimum, and state the count.

## Observed gaps (2026-09-03)

Four defects of this class were found while verifying an unrelated task. All
four are fixed. The last one below was the dominant contributor and was found
only after two rounds of wrong hypotheses; its post-mortem is worth reading
before diagnosing the next one.

### Fixed — `_SERVER` global leaked between tests

`McpServer.__init__` executes `global _SERVER; _SERVER = self` unconditionally.
`tests/test_harness_mcp_server.py` constructs about a dozen servers and restored
nothing. `_watcher_status` reads `_SERVER.runtime` and `_SERVER.watcher_manager`,
so a leaked codex-runtime server with no manager produced
`manager_running is False` → `receipts_recordable is False`, and
`_gate_next_action` then replaced `next_action` wholesale. The visible symptom
was the former test_micro_execution_mode_allows_no_plan_but_still_requires_verify (retired with receipt collection)
asserting on a `next_action` that described receipt unavailability.

Reproduction was 45% of runs for that file alone and 0/25 for the test in
isolation — the signature of a sibling-state defect. Fixed with an autouse
fixture in `tests/conftest.py` that snapshots and restores the global; 0/30
after. The fixture lives in `conftest.py` rather than the test module because
`test_no_toplevel_third_party_imports` forbids `import pytest` at the top level
of a `test_*.py` file.

### Fixed — `harness_server` re-executed instead of reused

`tests/test_harness_mcp_server.py` created a second module object for
`plugin/mcp/harness_server.py` and overwrote `sys.modules["harness_server"]`.
`_lib`'s control-writer authority binds the code objects it first observes, so
the second instance is not a recognised caller and every TASK.json mutation
raises `PermissionError: TASK.json mutation requires the task-control MCP`.
`tests/test_session_hint_marker_binding.py` already guarded this and its comment
claimed to match the other file; it did not. Both now reuse an existing
instance.

### Fixed — QA knowledge file shape

Unrelated to xdist but found in the same pass: a QA lens appended a top-level
`- topic:` sequence item to `doc/harness/qa/QA_KNOWLEDGE.yaml`, a mapping
document, making the whole file unparseable. Second occurrence in two days.
`tests/test_qa_knowledge_shape.py` catches it; the file now documents its own
append convention.

### Fixed — `importlib.reload(_lib)` emptied the control-writer bindings

`tests/test_promote_learnings_current_run.py` reloaded `_lib` in `setUp`.
Reload re-executes `plugin/scripts/_lib.py` **in the same module dict**, so
`_trusted_control_writer, _bind_control_writer = _make_control_writer_authority()`
runs again and the `bindings` closure is reset to empty. `harness_server` binds
itself into that closure once at import and keeps resolving through the shared
dict, so every later `write_task_control` / `write_goal_state` in that process
raised `PermissionError: TASK.json mutation requires the task-control MCP`.

Deterministic in three seconds, no xdist required:

```
pytest -n0 tests/test_promote_learnings_current_run.py tests/test_harness_mcp_server.py
→ 53 failed, 57 passed   (before)
→ 104 passed             (after)
```

Under `worksteal` it struck whichever tests shared that worker, which is why it
presented as an intermittent failure of four `HarnessMcpServerPR2CloseGate`
tests. The count varied — sometimes three, sometimes four — because only the
tests scheduled *after* the reload on that worker were affected.

Fixed by reloading only `promote_learnings`, which is what the test needs. The
`_lib` reload was incidental.

**How this stayed open longer than it should have.** An earlier revision of this
REQ recorded "pairwise co-location of ten candidate files in both orders did not
reproduce it, so it is not a simple two-file interaction." The exclusion was
false: the interaction *is* pairwise, and the offending file simply was not
among the ten candidates, which had been chosen by grepping for tests that touch
`harness_server` — `test_promote_learnings_current_run.py` touches `_lib`. A
negative result from a hand-picked candidate list is evidence about the list,
not about the space.

## Diagnostic debt this exposed

The refusal is a single boolean. `_make_control_writer_authority` evaluates
roughly eighteen conditions and
`PermissionError("TASK.json mutation requires the task-control MCP")` names none
of them, so the cause had to be bisected rather than read. Naming the failing
predicate would have turned a multi-hour hunt into one traceback. Tracked as
follow-up work.

## Observed gap (2026-09-28) — rule 2: the suite wrote the learnings ledger

`doc/harness/learnings.jsonl` is gitignored and per checkout, and in a batch
worktree `plugin/scripts/batch_harvest.py` appends that checkout's rows to the
main checkout's ledger. A full xdist run in an isolated clone (1655 tests)
appended exactly 4 rows to it. Running each test alone attributed them to three
tests, each of which handed a ledger writer the real checkout as its root:

| Test | Writer | Rows |
|------|--------|------|
| `test_prewrite_gate_json_decision.py::TestEnvEscape::test_skip_env_allows_and_logs_bypass` | `prewrite_gate` with `HARNESS_SKIP_PREWRITE=1` and `cwd=REPO_ROOT`; `log_gate_bypass` resolves the root from cwd | 1 `gate-bypass` |
| `test_prewrite_gate_scope.py::TestScopeLockCanonicalization::test_absolute_path_in_progress_skipped` | `_handle_scope_lock(..., REPO_ROOT, ...)` on a scratch task under the real `doc/harness/tasks` | 1 `gate-parse-fail` |
| `test_qa_codifier.py::TestCodifierPipeline::test_codifier_never_crashes_on_garbage` | `codify()` without `target_root`, so the root came from cwd | 1 `codifier-rejected`, 1 `codifier-fail` |

Fixed on the test side; the writers are unchanged. Each test now runs its
writer against `make_tmp_harness_root(tmp)` (a `.git` directory plus
`doc/harness/manifest.yaml`, so `find_repo_root` and `is_harness_enabled_repo`
both stop there) and asserts the row lands in that tmp ledger, so
`..._logs_bypass` is a true name. `test_qa_codifier.py::test_learnings_no_leak`
asserted nothing about the real ledger; it now uses a transcript that logs
rows and fails if any real-ledger line names its tmp task id.

The regression guard is the session-scoped autouse fixture
`learnings_ledger_gains_no_suite_rows` in `tests/conftest.py`, proven red and
green by nested pytest sessions in `tests/test_learnings_ledger_guard.py`. It
records the ledger's complete lines when each session starts and fails the run
naming every row appended since. When the ledger shrank or was replaced, the
recorded offset no longer marks the run's start, so rows are compared as a
multiset instead. Under xdist every worker runs it, so a row is reported by the
worker whose test wrote it and possibly by a sibling too.

**Exclusion rule.** The developer's live session keeps writing this ledger
while the suite runs. A row is ignored when its writer (`source`, or `script`
for a `gate-crash` row without `source`, up to the first `:`) is a hook fired by
a session event rather than by the suite: `background_hook`,
`subagent_lifecycle` and `receipts` (SubagentStart/SubagentStop),
`prompt_memory` (UserPromptSubmit) and `tool_routing` (PostToolUse on Bash). The
first four were observed during suite runs. The fixture's
`_LIVE_SESSION_LEDGER_WRITERS` comment is the authoritative list. The write
gate, `qa_codifier` and agent-authored learnings are not excluded, because the
suite drives them. Known ceiling: a suite-driven row from an excluded writer is
invisible to the guard, and a row the live session appends from any other
writer mid-run, such as a write-gate bypass or a hand-added learning, fails the
run under that row's name. A nested probe session skips the guard, as it skips
the install-tree guard.

## Consequence for verification claims

Report the number of runs observed, and the machine, when claiming a green
suite. Failure rates in this class are worker-partition-dependent: the same
defect measured 45% for one file, 20–35% for the full suite, and 0% in
isolation. A percentage without its sample size and host says very little.
