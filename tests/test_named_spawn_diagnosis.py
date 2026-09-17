"""An unbound verdict from a named spawn must be diagnosed as a named spawn.

A spawn that passes `name=` has its display name put in the agent-type position
by the CLI, so the resolved type never reaches the hook. When the name happens
to encode a lens (`review-code-3` -> `review-code`) a receipt row is still
written, the verdict binds nothing, and the row is indistinguishable from a
badly formatted report — which is exactly what the generic `shape` wording then
asserts. Following that wording reproduces the failure, because the fault is the
spawn argument and not the report.

Measured 2026-09-17 on TASK__install-strips-host-write-bits (run 01a0acca):
five named spawns returned substantive verdicts and bound none; one unnamed
spawn bound immediately. See
`doc/harness/REQ__unbound-verdict-names-the-spawn-shape.md`.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"


def _lib():
    name = "harness_lib_for_named_spawn_test"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / "_lib.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# The real ids, copied from the receipt rows of the measured incident.
NAMED_ID = "areview-code-3-32d77a6e778ff60c"
NAMED_QA_ID = "aqa-cli-da62c2add4e4dd0c"
UNNAMED_ID = "a30853ac485f19ace"


def test_discriminator_matches_the_observed_agent_ids():
    lib = _lib()
    assert lib.UNNAMED_AGENT_ID_RE.match(UNNAMED_ID)
    assert not lib.UNNAMED_AGENT_ID_RE.match(NAMED_ID)
    assert not lib.UNNAMED_AGENT_ID_RE.match(NAMED_QA_ID)
    # Empty is the absent-id case; it must not read as unnamed, because the
    # wording it selects would then assert a spawn shape nothing observed.
    assert not lib.UNNAMED_AGENT_ID_RE.match("")


def test_subagent_lifecycle_reads_the_discriminator_from_lib():
    """AC1: one source. Two copies of a CLI encoding drift apart silently."""
    body = (SCRIPTS / "subagent_lifecycle.py").read_text(encoding="utf-8")
    assert "UNNAMED_AGENT_ID_RE as _UNNAMED_AGENT_ID" in body
    # The fallback copy inside `except Exception:` is deliberate and is the one
    # permitted restatement; nothing else may define it at module scope.
    assert body.count("_UNNAMED_AGENT_ID = re.compile") == 1
    definition = body.index("_UNNAMED_AGENT_ID = re.compile")
    assert body.index("except Exception:") < definition


def test_named_spawn_gets_its_own_diagnosis():
    """AC2: name the cause, state the fix, do not route to the park."""
    note = _lib().nonparsing_completion_note({"review-code": "shape_named"})
    assert "display name" in note
    assert "`name=`" in note
    assert "parallel-fanout.md" in note
    # The three instructions that were wrong for this cause must not appear.
    assert "missing-attestation case until an unnamed spawn" in note
    assert "was not in the position and shape" not in note


def test_unnamed_spawn_keeps_the_existing_wording():
    """AC3: the named branch must not swallow the generic one."""
    note = _lib().nonparsing_completion_note({"qa-cli": "shape"})
    assert "was not in the position and shape the agent definition requires" in note
    assert "display name" not in note


def test_both_kinds_in_one_snapshot_produce_both_sentences():
    note = _lib().nonparsing_completion_note(
        {"review-code": "shape_named", "qa-cli": "shape"},
    )
    assert "display name" in note
    assert "was not in the position and shape the agent definition requires" in note
    # Each lens is named exactly once, under the branch that fits it.
    assert note.count("review-code") == 1
    assert note.count("qa-cli") == 1


def test_the_report_text_alone_does_not_decide_the_kind():
    """`_pending_completion_kind` cannot see the spawn shape, by construction.

    Both rows carry the same report, so this function returns `shape` for
    both. It is the classifier above it that splits them, which is what
    `test_classifier_*` drives end to end. Pinned so a future change that moves
    the decision back into report-reading fails here.
    """
    lib = _lib()
    summary = (
        "VERDICT: PENDING\nFINDING_COUNTS: INVALID\n"
        "FIRST_LINE: ## Verdict: PASS\nDETAIL_SHA256:" + "0" * 64
    )
    for agent_id in (NAMED_ID, UNNAMED_ID):
        row = {"agent_id": agent_id, "summary": summary}
        assert lib._pending_completion_kind("review-code", row) == "shape"


def _task_with_unbound_completion(agent_id, source="claude_hook", runtime_id=None):
    """Open a real task and record a started/completed PENDING pair for it.

    Hand-built dicts would not do: the classifier reads a validated snapshot,
    so the rows have to be ones `receipt_snapshot` accepts and
    `_valid_completion` pairs. Returns the kinds mapping.
    """
    sys.path.insert(0, str(REPO / "plugin" / "mcp"))
    sys.path.insert(0, str(SCRIPTS))
    import harness_server  # type: ignore

    lib = _lib()
    import json
    import os
    import tempfile

    summary_in = "## Verdict: PASS\n\nfindings follow\n"
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        manifest = root / "doc" / "harness" / "manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
        prior = os.getcwd()
        os.chdir(tmp)
        try:
            started = json.loads(
                harness_server.handle_task_start({"task_id": "TASK__named_spawn"})
                ["content"][0]["text"]
            )
            task_dir, run_id = started["task_dir"], started["run_id"]
            verdict, compact = lib.normalize_receipt_completion(
                "review-code", summary_in,
            )
            assert verdict == "PENDING", verdict
            rows = []
            for event, stored in (("started", ""), ("completed", compact)):
                rows.append({
                    "ts": lib._receipt_now_iso(),
                    "event": event,
                    "source": source,
                    "task_run_id": run_id,
                    "runtime_id": runtime_id or f"claude:session-named:{agent_id}",
                    "agent_id": agent_id,
                    "agent_type": "harness:code-reviewer",
                    "lens": "review-code",
                    "verdict": "PENDING" if event == "completed" else "",
                    "summary": stored,
                })
            with (Path(task_dir) / "RECEIPTS.jsonl").open(
                "a", encoding="utf-8",
            ) as handle:
                for row in rows:
                    assert lib._receipt_entry_semantics_valid(row), row
                    handle.write(
                        json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n"
                    )
            return lib.nonparsing_completion_lenses(task_dir)
        finally:
            os.chdir(prior)


def test_classifier_reads_a_named_spawn_off_a_real_receipt_pair():
    """AC4: the branch itself, from rows the real reader accepts.

    Deleting the `shape_named` block left the whole suite green before this
    test existed — every other assertion either hands the kind string to the
    note function or exercises the regex. This is the one that fails.
    """
    assert _task_with_unbound_completion(NAMED_ID) == {"review-code": "shape_named"}


def test_classifier_leaves_an_unnamed_spawn_on_the_generic_kind():
    assert _task_with_unbound_completion(UNNAMED_ID) == {"review-code": "shape"}


def test_classifier_does_not_apply_the_claude_id_shape_to_codex_rows():
    """The source gate. The id encoding belongs to one CLI.

    Without it, every Codex completion that binds no verdict would be told to
    drop a `name=` argument its coordinator never passed.
    """
    # A real Codex row: its agent id is a path and never matches the Claude
    # CLI's unnamed pattern, so without the source gate every one of them would
    # be classified `shape_named`.
    codex_agent_id = "/root/code_review_gitignore_venv_01a09eae"
    assert not _lib().UNNAMED_AGENT_ID_RE.match(codex_agent_id)
    kinds = _task_with_unbound_completion(
        codex_agent_id,
        source="codex_session_watcher:collaboration",
        runtime_id=(
            "codex:01a09d71-39f6-7491-8f83-75c5b4521933:"
            "call_P0o0ddED5srgYURqC5QnHWaq:01a09eaf-479e-7de3-bd0e-c6e5c926c261"
        ),
    )
    assert kinds == {"review-code": "shape"}
