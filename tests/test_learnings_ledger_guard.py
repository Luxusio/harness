"""The learnings-ledger guard must name rows the suite appends and nothing else.

`tests/conftest.py::learnings_ledger_gains_no_suite_rows` runs on every
invocation of this suite. `doc/harness/learnings.jsonl` is gitignored live
state, and `batch_harvest` copies a batch worktree's ledger into the main
checkout, so a row a test appends there becomes the developer's history. Three
tests did exactly that until they were bound to a tmp checkout
(`doc/harness/REQ__test-suite-determinism-under-xdist.md`, rule 2).

Two properties matter, and the second keeps the guard switched on:

  * a run that appends a row fails, naming the row, and
  * rows the developer's live session hooks append while the suite runs, and a
    ledger that shrinks or is replaced mid-run, do not fail it.

The end-to-end cases run a nested pytest whose conftest re-exports the real
fixture object with `_LEARNINGS_LEDGER` repointed at a stand-in ledger, as
`tests/test_install_tree_removal_guard.py` does for its guard: only a real
session shows that a session-scoped teardown assertion turns the run red, and
under xdist it fires inside a worker.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest import mock

import conftest

REPO_ROOT = Path(conftest.REPO_ROOT)
CONFTEST = REPO_ROOT / "tests" / "conftest.py"

# Rows a ledger already holds when the run starts. The guard must never name
# them, including after the ledger is replaced by a shorter copy.
HISTORY = [
    {"ts": "2026-09-01T00:00:00Z", "type": "gate-bypass", "source": "prewrite",
     "path": "historic-bypass"},
    {"ts": "2026-09-01T00:00:01Z", "type": "gate-parse-fail",
     "source": "prewrite_gate", "key": "gate-parse-fail", "insight": "historic-parse"},
]

# One row per writer the live session runs in the checkout; all must be ignored.
LIVE_ROWS = [
    {"type": "gate-error", "source": "prompt_memory", "error": "live"},
    {"type": "gate-error", "source": "tool_routing", "error": "live"},
]

SUITE_ROW = {"type": "gate-bypass", "source": "prewrite", "path": "suite-row"}


def _lines(*rows: dict) -> str:
    return "".join(json.dumps(row) + "\n" for row in rows)


def _nested_run(
    tmp_path: Path,
    tests: dict[str, str],
    *,
    xdist: bool,
    history: list[dict] | None = HISTORY,
) -> tuple[subprocess.CompletedProcess, Path]:
    """Run the real fixture in a nested pytest session over a stand-in ledger.

    `history=None` starts the run with no ledger at all. Each test body sees
    `LEDGER` and `append(row)`.
    """
    ledger = tmp_path / "checkout" / "doc" / "harness" / "learnings.jsonl"
    ledger.parent.mkdir(parents=True)
    if history is not None:
        ledger.write_text(_lines(*history), encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / "conftest.py").write_text(
        textwrap.dedent(
            f"""
            import importlib.util, sys

            spec = importlib.util.spec_from_file_location(
                "harness_real_conftest", {str(CONFTEST)!r},
            )
            real = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = real
            spec.loader.exec_module(real)
            real._LEARNINGS_LEDGER = {str(ledger)!r}

            # The fixture object under test, adopted by this nested session.
            learnings_ledger_gains_no_suite_rows = real.learnings_ledger_gains_no_suite_rows
            """
        ),
        encoding="utf-8",
    )
    source = textwrap.dedent(
        f"""
        import json, os
        from pathlib import Path

        LEDGER = Path({str(ledger)!r})

        def append(row):
            with LEDGER.open("a", encoding="utf-8") as f:
                f.write(json.dumps(row) + "\\n")
        """
    )
    for name, body in tests.items():
        source += f"\n\ndef {name}():\n" + textwrap.indent(
            textwrap.dedent(body).strip() + "\n", "    "
        )
    (project / "test_body.py").write_text(source, encoding="utf-8")
    args = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(project)]
    if xdist:
        args += ["-n", "2", "--dist", "worksteal"]
    # A nested probe skips the guard; this session must run it.
    env = {key: value for key, value in os.environ.items() if key != "HARNESS_NESTED_PROBE"}
    result = subprocess.run(
        args, capture_output=True, text=True, cwd=project, env=env, timeout=180,
    )
    return result, ledger


def _output(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


# --- End to end: the real fixture in a real session -------------------------


def test_a_run_that_appends_a_row_goes_red_under_xdist(tmp_path):
    """Detection, through the real fixture, in the arrangement it runs in.

    Two tests so both workers can hold a session over the shared ledger: the
    row is named by the worker that wrote it, and the historic rows by none.
    """
    result, _ = _nested_run(
        tmp_path,
        {
            "test_writes_a_row": f"append({SUITE_ROW!r})",
            "test_writes_nothing": "assert LEDGER.exists()",
        },
        xdist=True,
    )
    output = _output(result)
    assert result.returncode != 0, output
    assert "appended 1 row(s) to the learnings ledger" in output, output
    assert '"path": "suite-row"' in output, output
    assert "historic-bypass" not in output and "historic-parse" not in output, output


def test_rows_from_live_session_hooks_stay_green(tmp_path):
    """No false alarm on what the developer's own session writes mid-run."""
    result, ledger = _nested_run(
        tmp_path,
        {"test_live_rows": "\n".join(f"append({row!r})" for row in LIVE_ROWS)},
        xdist=False,
    )
    assert result.returncode == 0, _output(result)
    assert len(ledger.read_text(encoding="utf-8").splitlines()) == len(HISTORY) + len(LIVE_ROWS)


def test_a_replaced_shorter_ledger_is_not_a_suite_row_under_xdist(tmp_path):
    """A ledger replaced by a shorter copy mid-run leaves only its new rows.

    The recorded offset now points past the end of a different file, so the
    guard falls back to comparing rows; the historic row that survived is not
    new, and the live row the replacement carried is ignored.
    """
    result, _ = _nested_run(
        tmp_path,
        {
            "test_replaces_the_ledger": f"""
                replacement = LEDGER.with_name("learnings.jsonl.new")
                replacement.write_text(
                    json.dumps({HISTORY[1]!r}) + "\\n" + json.dumps({LIVE_ROWS[0]!r}) + "\\n",
                    encoding="utf-8",
                )
                os.replace(replacement, LEDGER)
            """,
            "test_writes_nothing": "assert LEDGER.exists()",
        },
        xdist=True,
    )
    assert result.returncode == 0, _output(result)


def test_a_suite_row_in_a_replaced_ledger_is_still_named(tmp_path):
    result, _ = _nested_run(
        tmp_path,
        {
            "test_truncates_then_appends": f"""
                LEDGER.write_text(json.dumps({HISTORY[1]!r}) + "\\n", encoding="utf-8")
                append({SUITE_ROW!r})
            """,
        },
        xdist=True,
    )
    output = _output(result)
    assert result.returncode != 0, output
    assert "appended 1 row(s)" in output and '"path": "suite-row"' in output, output
    assert "historic-parse" not in output, output


def test_a_ledger_created_by_a_suite_row_goes_red(tmp_path):
    """Fresh clone or CI: no ledger at the start is not a reason to look away."""
    result, _ = _nested_run(
        tmp_path, {"test_creates_it": f"append({SUITE_ROW!r})"}, xdist=False, history=None,
    )
    output = _output(result)
    assert result.returncode != 0, output
    assert '"path": "suite-row"' in output, output


def test_a_nested_probe_reads_no_ledger():
    """This guard's `is_nested_probe` branch, driven like the install guard's.

    A probe runs inside the real repo while the outer session is watching the
    same ledger, and a teardown error there would make its exit 1 ambiguous
    (`tests/test_qa_knowledge_shape.py::red_claim_violation`).
    """
    with mock.patch.dict(os.environ, {"HARNESS_NESTED_PROBE": "1"}), \
            mock.patch.object(conftest, "_ledger_snapshot") as snapshot:
        generator = conftest.learnings_ledger_gains_no_suite_rows.__wrapped__()
        next(generator)
        try:
            next(generator)
        except StopIteration:
            pass
        else:
            raise AssertionError("the fixture yielded twice")
        snapshot.assert_not_called()


# --- The pieces the fixture is built from -----------------------------------


def test_the_guard_watches_this_checkouts_ledger():
    assert conftest._LEARNINGS_LEDGER == str(
        REPO_ROOT / "doc" / "harness" / "learnings.jsonl"
    )


def test_the_writers_that_polluted_the_ledger_are_never_excused():
    """Silencing the guard by excusing a suite-driven writer must fail here.

    These are the writers the three polluting tests drove (`prewrite` for
    `gate-bypass`, `prewrite_gate` for `gate-parse-fail`, `qa_codifier`) plus
    the historic `mcp_bash_guard` bypass rows.
    """
    for writer in ("prewrite", "prewrite_gate", "qa_codifier", "mcp_bash_guard"):
        assert writer not in conftest._LIVE_SESSION_LEDGER_WRITERS, writer
    kept = conftest._suite_ledger_rows([json.dumps(SUITE_ROW)])
    assert kept == [json.dumps(SUITE_ROW)]


def test_live_rows_are_dropped_and_everything_else_is_kept():
    live = [json.dumps(row) for row in LIVE_ROWS]
    others = [
        json.dumps({"type": "gate-crash", "script": "prewrite_gate", "error": "x"}),
        json.dumps({"type": "pitfall", "source": "review-code", "key": "k"}),
        json.dumps({"type": "gate-error", "source": "background_hookish"}),
        "not json at all",
        json.dumps(["a", "list"]),
    ]
    assert conftest._suite_ledger_rows(live + others) == others


def test_appended_rows_after_growth(tmp_path):
    ledger = tmp_path / "learnings.jsonl"
    ledger.write_text(_lines(*HISTORY), encoding="utf-8")
    before = conftest._ledger_snapshot(str(ledger))
    assert before == _lines(*HISTORY).encode()
    with ledger.open("a", encoding="utf-8") as f:
        f.write(_lines(SUITE_ROW, SUITE_ROW))
    # Two identical rows are two rows.
    assert conftest._ledger_rows_appended(str(ledger), before) == [json.dumps(SUITE_ROW)] * 2


def test_appended_rows_after_shrink_count_duplicates(tmp_path):
    ledger = tmp_path / "learnings.jsonl"
    ledger.write_text(_lines(SUITE_ROW, *HISTORY), encoding="utf-8")
    before = conftest._ledger_snapshot(str(ledger))
    # Rewritten without the first history row, then the same suite row again:
    # one copy is old, the second is new.
    ledger.write_text(_lines(SUITE_ROW, HISTORY[1], SUITE_ROW), encoding="utf-8")
    assert conftest._ledger_rows_appended(str(ledger), before) == [json.dumps(SUITE_ROW)]


def test_a_partial_line_is_counted_once_it_is_whole(tmp_path):
    """A writer mid-append at either end must not produce a fragment row."""
    ledger = tmp_path / "learnings.jsonl"
    whole = json.dumps(SUITE_ROW)
    ledger.write_text(_lines(*HISTORY) + whole[:10], encoding="utf-8")
    before = conftest._ledger_snapshot(str(ledger))
    assert before == _lines(*HISTORY).encode()
    assert conftest._ledger_rows_appended(str(ledger), before) == []
    with ledger.open("a", encoding="utf-8") as f:
        f.write(whole[10:] + "\n")
    assert conftest._ledger_rows_appended(str(ledger), before) == [whole]


def test_an_absent_or_deleted_ledger_reports_nothing(tmp_path):
    ledger = tmp_path / "learnings.jsonl"
    assert conftest._ledger_snapshot(str(ledger)) == b""
    assert conftest._ledger_rows_appended(str(ledger), b"") == []
    ledger.write_text(_lines(*HISTORY), encoding="utf-8")
    before = conftest._ledger_snapshot(str(ledger))
    ledger.unlink()
    assert conftest._ledger_rows_appended(str(ledger), before) == []
