"""The turn-end gate names an observed receipt outage instead of demanding it away.

A session got `receipts_recordable: false` from `task_start` — a
`StaleBytecodeCacheError` on the receipt adapter — and no `RECEIPTS.jsonl` was
ever written. The Stop hook then blocked every turn-end with
`missing: completed review verdict PASS ..., completed QA verdict: qa-cli`,
items satisfiable only by receipts the broken subsystem could not produce. The
coordinator spawned lenses and waited, ~15 turns running, until the user
interrupted.

The gate's reason already named the park route. That was not enough, and the
reason is worth stating exactly: the message frames the gap as *missing
verdicts*, which reads as work that remains. The one fact that turns "missing
verdicts" into "genuine external blocker" was reported by `task_start` several
turns earlier and never appeared again.

What this does NOT do is infer the outage from an empty receipt stream. That
design was built, measured, and rejected — zero receipts is the ordinary state
of a task that has not reached review, so releasing on it disabled the gate from
turn 4 of a normal task. See
doc/harness/REQ__gate-does-not-demand-impossible-evidence.md, whose rejected-
approach section is marked as the most important part of that document. The only
input here is the marker a failed hook wrote about itself.

No blocking decision changes. A turn that blocks today still blocks.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = str(ROOT / "plugin" / "scripts")
STOP_GATE = str(ROOT / "plugin" / "scripts" / "stop_gate.py")

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402
import stop_gate  # noqa: E402

MARKER = _lib.CAPABILITY_MARKER_RELPATH
TASK_ID = "TASK__deadlocked"


def run_gate(cwd: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["HARNESS_BACKGROUND_WAIT_SECS"] = "0"
    return subprocess.run(
        [sys.executable, STOP_GATE], input="{}", capture_output=True,
        text=True, cwd=cwd, env=env, timeout=10.0,
    )


def open_task(tmp: Path, *, marker: bool, complete: bool = False,
              planned: bool = True, stale_followup: bool = False) -> str:
    """A repo with one open task past PLAN.md and no receipts at all.

    This is exactly the state the field report was in, and — importantly — also
    the state of every ordinary task that has not yet reached review. The two
    are distinguished by the marker and by nothing else.
    """
    tmp.mkdir(parents=True, exist_ok=True)
    (tmp / ".git").mkdir()
    (tmp / "doc" / "harness").mkdir(parents=True)
    (tmp / "doc" / "harness" / "manifest.yaml").write_text("type: test\n", encoding="utf-8")
    task_dir = tmp / "doc" / "harness" / "tasks" / TASK_ID
    task_dir.mkdir(parents=True)
    (task_dir.parent / ".active").write_text(TASK_ID + "\n", encoding="utf-8")
    if planned:
        (task_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": _lib.new_uuid7(),
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "close_receipt_fingerprint": None,
    }), encoding="utf-8")
    if complete:
        sys.path.insert(0, str(ROOT / "tests"))
        import test_stop_gate

        test_stop_gate._write_completed_lenses(
            str(tmp), TASK_ID, [("review-code", "PASS"), ("qa-cli", "PASS")],
        )
    if stale_followup:
        _append_unreadable_followup(tmp, "qa-cli")
    if marker:
        (tmp / MARKER).write_text("StaleBytecodeCacheError\n", encoding="utf-8")
    return str(tmp)


def _append_unreadable_followup(tmp: Path, lens: str) -> None:
    """A second completion for an already-bound lens whose verdict cannot be read.

    Reaches `_lib`'s `stale_followup` branch: the bound verdict still stands, so
    nothing is missing, but the note appended to next_action tells the caller to
    spawn the lens fresh if the follow-up was a new finding.
    """
    task_dir = tmp / "doc" / "harness" / "tasks" / TASK_ID
    run_id = _lib.read_task_control(str(task_dir))["run_id"]
    agent_id = "agent-followup"
    common = {
        "source": "claude_hook",
        "task_run_id": run_id,
        "runtime_id": f"claude:test-session:{agent_id}",
        "agent_id": agent_id,
        "agent_type": f"harness:{lens}",
        "lens": lens,
    }
    # Normalized, not hand-written: the parser is what decides this is a
    # follow-up nobody can read, and faking the stored shape would test the
    # fixture rather than the branch.
    verdict, summary = _lib.normalize_receipt_completion(
        lens, "the lens reported, but not in the required shape", "PENDING",
    )
    rows = [
        dict(common, event="started", ts=_lib.now_iso(), verdict="", summary=""),
        dict(common, event="completed", ts=_lib.now_iso(),
             verdict=verdict, summary=summary),
    ]
    with (task_dir / "RECEIPTS.jsonl").open("a", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")


def reason_of(proc: subprocess.CompletedProcess) -> str:
    payload = json.loads(proc.stdout)
    # ensure_ascii=False so the em-dash in the inserted sentence compares as
    # itself rather than as — against a literal.
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


class MarkerReadTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_absent_marker_is_not_an_outage(self):
        self.assertFalse(stop_gate._receipt_capability_broken(str(self.tmp)))

    def test_present_marker_is_an_outage(self):
        (self.tmp / "doc" / "harness").mkdir(parents=True)
        (self.tmp / MARKER).write_text("x", encoding="utf-8")
        self.assertTrue(stop_gate._receipt_capability_broken(str(self.tmp)))

    def test_a_directory_at_the_marker_path_is_not_a_marker(self):
        (self.tmp / MARKER).mkdir(parents=True)
        self.assertFalse(stop_gate._receipt_capability_broken(str(self.tmp)))

    def test_an_empty_root_is_not_an_outage(self):
        # `repo_root` is "" on paths where the root could not be resolved.
        # os.path.join("", MARKER) is a RELATIVE path, so without the guard the
        # answer comes from whatever directory the process happens to be in —
        # the gate would report another repo's outage on this one's turn-end.
        # Hence the chdir: asserting against "" from a cwd with no marker
        # passes either way and pins nothing.
        (self.tmp / "doc" / "harness").mkdir(parents=True)
        (self.tmp / MARKER).write_text("x", encoding="utf-8")
        cwd = os.getcwd()
        try:
            os.chdir(self.tmp)
            self.assertFalse(stop_gate._receipt_capability_broken(""))
        finally:
            os.chdir(cwd)

    def test_a_fifo_at_the_marker_path_does_not_hang_the_gate(self):
        # A hook has a 10s budget (C-12). `isfile` on a fifo is False and does
        # not block; an implementation that opened the path to read it would
        # hang here until the test timed out.
        if not hasattr(os, "mkfifo"):
            self.skipTest("no mkfifo on this platform")
        (self.tmp / "doc" / "harness").mkdir(parents=True)
        os.mkfifo(self.tmp / MARKER)
        self.assertFalse(stop_gate._receipt_capability_broken(str(self.tmp)))

    def test_a_symlink_at_the_marker_path_is_not_a_marker(self):
        # `isfile` follows symlinks, so a link planted at this fixed name lets
        # any existing file masquerade as a marker. harness_server rejects that
        # (lstat + S_ISREG) and pins it; if the gate accepted it, the two
        # readers of one marker would disagree about whether recording works.
        (self.tmp / "doc" / "harness").mkdir(parents=True)
        decoy = self.tmp / "ordinary.txt"
        decoy.write_text("not a marker", encoding="utf-8")
        (self.tmp / MARKER).symlink_to(decoy)
        self.assertFalse(stop_gate._receipt_capability_broken(str(self.tmp)))

    def test_the_gate_and_the_mcp_agree_on_what_counts_as_a_marker(self):
        import stat as stat_mod

        (self.tmp / "doc" / "harness").mkdir(parents=True)
        path = self.tmp / MARKER

        def mcp_predicate() -> bool:
            try:
                return stat_mod.S_ISREG(os.lstat(path).st_mode)
            except OSError:
                return False

        decoy = self.tmp / "ordinary.txt"
        decoy.write_text("x", encoding="utf-8")
        cases = {"absent": None, "regular": "file", "symlink": "link", "dir": "dir"}
        for name, kind in cases.items():
            with self.subTest(shape=name):
                if path.is_symlink() or path.is_file():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
                if kind == "file":
                    path.write_text("x", encoding="utf-8")
                elif kind == "link":
                    path.symlink_to(decoy)
                elif kind == "dir":
                    path.mkdir()
                self.assertEqual(
                    stop_gate._receipt_capability_broken(str(self.tmp)), mcp_predicate(),
                )

    def test_an_unreadable_root_reports_no_outage_rather_than_raising(self):
        # Patched at `os.lstat`, the call the function actually makes. An
        # earlier version of this test still mocked `os.path.isfile` after the
        # implementation moved to `lstat` — the mock was dead, the branch ran
        # unexercised, and the docstring plus the REQ both went on claiming it
        # was pinned. A mock aimed at an API the code no longer calls asserts
        # nothing and looks like coverage.
        from unittest import mock

        with mock.patch.object(stop_gate.os, "lstat", side_effect=PermissionError):
            self.assertFalse(stop_gate._receipt_capability_broken(str(self.tmp)))


class GateReasonTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_the_outage_state_still_blocks(self):
        # The whole rejected design was "release the gate". This one does not.
        proc = run_gate(open_task(self.tmp, marker=True))
        payload = json.loads(proc.stdout)
        self.assertEqual(payload.get("decision"), "block")

    def test_the_outage_state_says_recording_is_unavailable(self):
        proc = run_gate(open_task(self.tmp, marker=True))
        body = reason_of(proc)
        self.assertIn(_lib.RECEIPT_RECORDING_UNAVAILABLE, body)

    def test_the_outage_state_says_the_missing_verdicts_are_not_remaining_work(self):
        # The reframing is the entire point. Without it the reader sees a list
        # of missing verdicts and goes to produce them.
        proc = run_gate(open_task(self.tmp, marker=True))
        body = reason_of(proc)
        self.assertIn("cannot be produced by continuing", body)
        self.assertIn("not work that remains", body)

    def test_the_outage_state_routes_to_task_blocked_not_to_another_lens(self):
        # Asserted on the structured fields, not on a dump of the payload.
        # `_gate_response` documents next_action_command as the exact call that
        # resolves the block; an earlier version of this test grepped the whole
        # JSON for a phrase and so never saw that those fields still said
        # "spawn and await a review subagent" while the prose said not to.
        #
        # "genuine external blocker" is deliberately not asserted: it appears in
        # the base reason in every state, so it would have passed with the
        # feature absent.
        payload = json.loads(run_gate(open_task(self.tmp, marker=True)).stdout)
        self.assertIn("task_blocked", payload["next_action_command"])
        self.assertNotIn("Spawn Agent", payload["next_action_command"])
        self.assertNotIn("review subagent", payload["next_action_command"])
        self.assertEqual(payload["owner_skill"], "harness-goal")
        self.assertIn("rather than spawning another lens", payload["reason"])

    def test_outage_park_does_not_end_with_normal_completion_advice(self):
        payload = json.loads(run_gate(open_task(self.tmp, marker=True)).stdout)
        self.assertNotIn("finish task start -> plan -> develop -> QA -> close", payload["reason"])
        self.assertIn(_lib.TRUST_BOUNDARY, payload["reason"])
        self.assertIn("Next: ", payload["reason"])
        self.assertIn(payload["next_action_command"], payload["reason"])

    def test_normal_completion_advice_remains_outside_outage_park(self):
        for name, marker, planned, complete in (
            ("healthy", False, True, False),
            ("needs-plan", True, False, False),
            ("close-ready", True, True, True),
        ):
            with self.subTest(name=name):
                payload = json.loads(run_gate(open_task(
                    self.tmp / name, marker=marker, planned=planned, complete=complete,
                )).stdout)
                self.assertIn("finish task start -> plan -> develop -> QA -> close", payload["reason"])

    def test_the_outage_routing_does_not_prescribe_a_fixed_attestation_pair(self):
        # Both pairs assert the lenses ran and returned results. C-17 scopes
        # verbatim copying to that branch, and this state is not it.
        payload = json.loads(run_gate(open_task(self.tmp, marker=True)).stdout)
        blob = payload["next_action_command"] + payload["reason"]
        self.assertNotIn(_lib.ATTESTATION_BLOCKED_REASON, blob)
        self.assertNotIn(_lib.NO_RECEIPTS_BLOCKED_REASON, blob)

    def test_an_ordinary_open_task_is_unchanged(self):
        # Same task, same empty receipt stream, no marker. If this ever starts
        # carrying the outage text, the gate has begun inferring from absence —
        # the design this task exists to keep rejected.
        proc = run_gate(open_task(self.tmp, marker=False))
        body = reason_of(proc)
        self.assertEqual(json.loads(proc.stdout).get("decision"), "block")
        self.assertNotIn(_lib.RECEIPT_RECORDING_UNAVAILABLE, body)
        self.assertNotIn("rather than spawning another lens", body)

    def test_an_unplanned_task_is_still_sent_to_the_plan_step(self):
        # missing_for_close leads with PLAN.md here, so a guard keyed only on
        # "something is missing" fires and replaces the plan routing with a
        # park — telling the coordinator to abandon a task for want of evidence
        # it has not started gathering. PLAN.md is producible work; an outage
        # does not change that. The MCP asks the same question via the same
        # predicate before replacing its own routing.
        payload = json.loads(
            run_gate(open_task(self.tmp, marker=True, planned=False)).stdout
        )
        self.assertIn("PLAN.md", payload["reason"])
        self.assertNotIn("task_blocked {", payload["next_action_command"])
        self.assertNotIn("rather than spawning another lens", payload["reason"])
        self.assertIn("plan", payload["next_action_command"].lower())

    def test_the_outage_applies_exactly_where_the_routing_was_a_spawn(self):
        # The predicate, not a proxy for it: whether the outage treatment
        # applies must track is_spawn_instruction. The two strings below are
        # stand-ins of the right shape, not the exact rendered routings — the
        # planned state actually routes through _lib's "Run and await the
        # required read-only review subagent(s)…". The rendered set is covered
        # by test_gate_detects_every_spawn_instruction_lib_can_render; what
        # this pins is that the gate's behaviour follows the predicate.
        for planned, expect in ((False, False), (True, True)):
            with self.subTest(planned=planned):
                payload = json.loads(
                    run_gate(open_task(
                        Path(self.tmp / f"r{int(planned)}"), marker=True, planned=planned,
                    )).stdout
                )
                applied = "rather than spawning another lens" in payload["reason"]
                self.assertEqual(applied, expect)
                self.assertEqual(
                    applied, _lib.is_spawn_instruction(
                        "Spawn Agent(subagent_type='harness:code-reviewer', ...)"
                        if planned else
                        "Create PLAN.md via plan skill before source writes."
                    ),
                )

    def test_a_spawn_shaped_routing_with_nothing_missing_is_not_told_to_park(self):
        """The `missing_summary` guard, in the only state that can observe it.

        QA measured that removing the guard left the whole suite green: every
        close-ready fixture routes to "run task_close", which is not a spawn
        instruction, so `is_spawn_instruction` already suppressed the sentence
        and the guard beside it did nothing observable.

        The guard is not dead code, though. `_lib.emit_compact_context` has a
        reachable state with an empty `missing_for_close` AND a spawn-shaped
        next_action: a bound PASS followed by a completion for the same lens
        that cannot be read prefixes the stale-followup note — which ends
        "spawn the lens fresh so it can report in the required shape" — onto
        "Completed QA verdicts present — run task_close." Nothing is missing,
        and the routing mentions spawning.

        Without the guard, that task is told to park instead of to close.
        """
        repo = open_task(self.tmp, marker=True, complete=True, stale_followup=True)
        ctx = _lib.emit_compact_context(
            os.path.join(repo, "doc", "harness", "tasks", TASK_ID)
        )
        # Guard the fixture itself: if either premise stops holding, this test
        # silently stops observing the guard and becomes the thing it pins.
        self.assertEqual(ctx["missing_for_close"], [])
        self.assertTrue(_lib.is_spawn_instruction(ctx["next_action"]))

        payload = json.loads(run_gate(repo).stdout)
        self.assertNotIn("rather than spawning another lens", payload["reason"])
        self.assertNotIn("task_blocked {", payload["next_action_command"])

    def test_a_close_ready_task_is_not_told_to_park(self):
        # The marker legitimately survives into close-ready — receipts earlier
        # in a run do not disprove a break after them — so the sentence must be
        # keyed on there being missing verdicts, not on the marker alone.
        # Otherwise the gate prescribes task_blocked in the one state whose
        # next_action is task_close, and points at a list it did not emit.
        repo = open_task(self.tmp, marker=True, complete=True)
        body = reason_of(run_gate(repo))
        self.assertIn("task_close", body)
        self.assertNotIn("rather than spawning another lens", body)
        self.assertNotIn("the missing verdicts above", body)

    def test_the_two_states_differ_only_where_the_design_says_they_may(self):
        # Three fields may differ and no others: the reason gains one sentence,
        # and the two routing fields switch from spawn-a-lens to park. `docs`,
        # `decision`, and anything else added later must stay identical, so a
        # future edit cannot quietly change unrelated behaviour in this branch.
        import tempfile

        with tempfile.TemporaryDirectory() as other:
            broken = json.loads(run_gate(open_task(self.tmp, marker=True)).stdout)
            healthy = json.loads(run_gate(open_task(Path(other), marker=False)).stdout)

        may_differ = {"reason", "next_action_command", "owner_skill"}
        self.assertEqual(set(broken), set(healthy))
        for key in set(broken) - may_differ:
            with self.subTest(field=key):
                self.assertEqual(broken[key], healthy[key])

        action, owner = _lib.receipt_outage_next_action()
        self.assertEqual(broken["next_action_command"], action)
        self.assertEqual(broken["owner_skill"], owner)
        # The reason differs by the inserted sentence and by the " Next: ..."
        # tail that carries the same routing change.
        self.assertIn(_lib.receipt_outage_block_instruction(), broken["reason"])
        self.assertNotIn(_lib.receipt_outage_block_instruction(), healthy["reason"])


class SingleSourceTests(unittest.TestCase):
    """No clause may get a second home (REQ__runtime-normative-text-has-one-source)."""

    def test_every_holder_of_the_marker_path_agrees(self):
        # Four holders, and one of them cannot be deduplicated: background_hook
        # must not import _lib, because it is the hook that still has to run
        # when _lib is the broken import. So the literals stay and this pins
        # them together instead.
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "background_hook_probe", str(ROOT / "plugin" / "scripts" / "background_hook.py"),
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sys.path.insert(0, str(ROOT / "plugin" / "mcp"))
        import harness_server

        setup_finalize_src = (
            ROOT / "plugin" / "scripts" / "setup_finalize.py"
        ).read_text(encoding="utf-8")

        with self.subTest(holder="background_hook"):
            self.assertEqual(module.CAPABILITY_MARKER_RELPATH, _lib.CAPABILITY_MARKER_RELPATH)
        with self.subTest(holder="harness_server"):
            self.assertEqual(harness_server._HOOK_CAPABILITY_MARKER, _lib.CAPABILITY_MARKER_RELPATH)
        with self.subTest(holder="setup_finalize"):
            self.assertIn(
                _lib.CAPABILITY_MARKER_RELPATH.replace(os.sep, "/"), setup_finalize_src,
            )

    def test_the_gate_does_not_respell_the_unavailable_sentence(self):
        source = (ROOT / "plugin" / "scripts" / "stop_gate.py").read_text(encoding="utf-8")
        self.assertNotIn(_lib.RECEIPT_RECORDING_UNAVAILABLE, source)

    def test_the_mcp_string_is_unchanged_by_the_extraction(self):
        # The constant moved to composition; the emitted advice must not shift.
        # This is the text a caller of task_verify reads, and it is normative.
        sys.path.insert(0, str(ROOT / "plugin" / "mcp"))
        import harness_server

        # The whole string, written out here independently, not a prefix. The
        # REQ claims this text is unchanged by the extraction; a prefix check
        # would leave that claim wider than what runs, and everything after the
        # extracted head is exactly the part a prefix check cannot see.
        expected = (
            "Receipt recording is unavailable. Continue and await the required "
            "review and QA: their results are substantive but NON-ATTESTING and "
            "cannot authorize task_close. Remediate an actual FAIL and publish "
            "an actual BLOCKED_ENV through task_blocked. Recording is "
            "unavailable in every state here, so do not repair, restart, "
            "resume, recollect, or rerun a lens solely to obtain a receipt at "
            "any point. "
            f"{_lib.TRUST_BOUNDARY} {_lib.attestation_endgame()}"
        )
        self.assertEqual(harness_server.RECEIPT_UNAVAILABLE_NEXT_ACTION, expected)

    def test_the_outage_text_does_not_carry_the_attestation_pairs(self):
        # Both pairs assert the lenses ran and returned results, which an
        # observed outage leaves unknown. Presenting an inapplicable pair first
        # is the failure the owning REQ was written to fix.
        text = _lib.receipt_outage_block_instruction()
        self.assertNotIn(_lib.ATTESTATION_BLOCKED_REASON, text)
        self.assertNotIn(_lib.NO_RECEIPTS_BLOCKED_REASON, text)


if __name__ == "__main__":
    unittest.main()
