"""A lens that complied with its agent definition must get its verdict bound.

Covers doc/harness/REQ__lens-verdicts-bind-when-the-lens-complied.md.

Two field sessions lost every lens verdict to this subsystem: 18 `completed`
receipts, verdict distribution `{'PENDING': 18}`. With nothing bound, neither
`task_close` (needs PASS) nor the attestation park path (needs a preceding
PASS) is reachable, so the task lifecycle has no exit that is not receipt
forgery. Three independent causes were measured, and each gets a test plus the
counter-case that keeps the fix from over-reaching.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from conftest import SCRIPTS_DIR  # type: ignore

import unittest


def _task_with_receipt():
    """Reuse the real task+receipt fixture rather than re-deriving it.

    Imported lazily: the top-level-import guard in
    tests/test_no_toplevel_third_party_imports.py cannot tell a sibling test
    module from a third-party package.
    """
    from test_receipt_watcher_fail_closed import (  # type: ignore
        _task_with_receipt as fixture,
    )

    return fixture()


def _write_plan(task_dir, lenses):
    """Give the fixture task a PLAN.md so context reaches the verdict branches.

    Without it `emit_compact_context` stops at the plan-first branch and any
    assertion about the verdict-state surfaces is vacuous.
    """
    import os
    import sys as _sys
    from conftest import REPO_ROOT  # type: ignore

    _sys.path.insert(0, os.path.join(REPO_ROOT, "plugin", "mcp"))
    import harness_server  # type: ignore

    return harness_server.handle_write_plan({
        "task_id": Path(task_dir).name,
        "plan": "# PLAN\n\n## Acceptance Criteria\n\n- AC-1: fixture task.\n",
        "required_lenses": list(lenses),
    })


def _lib_mod():
    sys.path.insert(0, SCRIPTS_DIR)
    import _lib  # type: ignore

    return _lib


def _append(task_dir, entry):
    _lib = _lib_mod()
    assert _lib._receipt_entry_semantics_valid(entry), entry
    with (Path(task_dir) / "RECEIPTS.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")


def _agent_type(lens):
    return "harness:code-reviewer" if lens.startswith("review-") else f"harness:{lens}"


def _start(task_dir, run_id, lens, agent):
    _lib = _lib_mod()
    _append(task_dir, {
        "ts": _lib._receipt_now_iso(),
        "event": "started",
        "source": "claude_hook",
        "task_run_id": run_id,
        "runtime_id": f"claude:session-disproof:agent-{agent}",
        "agent_id": f"agent-{agent}",
        "agent_type": _agent_type(lens),
        "lens": lens,
        "verdict": "",
        "summary": "",
    })


def _complete(task_dir, run_id, summary, lens="review-code", agent="disproof"):
    _lib = _lib_mod()
    verdict, compact = _lib.normalize_receipt_completion(lens, summary)
    _append(task_dir, {
        "ts": _lib._receipt_now_iso(),
        "event": "completed",
        "source": "claude_hook",
        "task_run_id": run_id,
        "runtime_id": f"claude:session-disproof:agent-{agent}",
        "agent_id": f"agent-{agent}",
        "agent_type": _agent_type(lens),
        "lens": lens,
        "verdict": verdict,
        "summary": compact,
    })
    return verdict


class PassCoexistsWithNonBlockingFindings(unittest.TestCase):
    """Only FIX_NOW contradicts PASS — the reviewer definition says nothing else.

    `code-reviewer.md` requires the counts to match the findings and stops
    there. The binder additionally downgraded PASS whenever INVESTIGATE was
    non-zero, so a reviewer that did exactly what it was told lost its verdict,
    and no surface anywhere told it why. Reruns reproduced it indefinitely.
    """

    def test_pass_binds_beside_investigate_and_optional_findings(self):
        _lib = _lib_mod()
        verdict, _ = _lib.normalize_receipt_completion(
            "review-code",
            "VERDICT: PASS\n"
            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=2 OPTIONAL=1\n"
            "Two things worth a look, nothing blocking.\n",
        )
        self.assertEqual(verdict, "PASS")

    def test_pass_still_does_not_bind_beside_a_fix_now_finding(self):
        """The one genuine contradiction stays rejected."""
        _lib = _lib_mod()
        verdict, _ = _lib.normalize_receipt_completion(
            "review-code",
            "VERDICT: PASS\n"
            "FINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n"
            "One thing must be fixed now.\n",
        )
        self.assertEqual(verdict, "PENDING")

    def test_a_compliant_pass_survives_the_whole_write_and_read_path(self):
        """The rule lived in two copies and only one was corrected.

        `normalize_receipt_completion` bound PASS while
        `_receipt_entry_semantics_valid` still rejected PASS beside
        INVESTIGATE>0, so the writer raised and appended nothing: a compliant
        review went from "bound as PENDING" to "no record at all", which reads
        downstream as a lens that never ran. Unit-testing the binder alone could
        not see it — only the round trip can.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            verdict = _complete(
                task_dir, run_id,
                "VERDICT: PASS\n"
                "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=2 OPTIONAL=1\n"
                "Two things worth a look, nothing blocking.\n",
            )
            self.assertEqual(verdict, "PASS")
            # It must come back out of the integrity-validated reader, not just
            # off the end of the writer.
            completed = _lib._completed_review_by_lens(task_dir)
            self.assertIn("review-code", completed)
            self.assertEqual(
                str(completed["review-code"].get("verdict")).upper(), "PASS"
            )
            self.assertEqual(_lib.receipt_review_verdict(task_dir), "PASS")

    def test_the_verdict_counts_rule_behaves_the_same_at_every_branch(self):
        """Pin the rule's truth table so a caller cannot quietly diverge from it.

        This does NOT detect a reintroduced second copy — measured: adding a
        duplicate branch inside `normalize_receipt_completion` leaves it green,
        because it exercises `_counts_contradict_verdict` in isolation and a
        copy elsewhere never calls it. The guard against cause 3-a is
        `test_a_compliant_pass_survives_the_whole_write_and_read_path`, which
        reddens when the validator-side copy is reverted. Naming this one for
        single-sourcing was the overclaim; it pins the rule, not its uniqueness.
        """
        _lib = _lib_mod()
        for verdict, fix_now, investigate, contradicts in (
            ("PASS", 0, 0, False),
            ("PASS", 0, 5, False),
            ("PASS", 1, 0, True),
            ("FAIL", 1, 0, False),
            ("FAIL", 0, 0, True),
            ("BLOCKED_ENV", 0, 1, False),
            ("BLOCKED_ENV", 0, 0, True),
        ):
            with self.subTest(verdict=verdict, fix_now=fix_now, investigate=investigate):
                self.assertEqual(
                    _lib._counts_contradict_verdict(verdict, fix_now, investigate),
                    contradicts,
                )

    def test_the_reviewer_definitions_state_the_rule_the_binder_enforces(self):
        """A downgrade the agent was never told about is unfixable by the agent.

        This is the whole reason AC-1 alone was not enough: the binder may
        reject a report only on a rule its author can read.
        """
        from conftest import REPO_ROOT  # type: ignore

        for tree in ("plugin", "plugin-codex"):
            for name in ("code-reviewer", "security-reviewer"):
                text = (Path(REPO_ROOT) / tree / "agents" / f"{name}.md").read_text(
                    encoding="utf-8"
                )
                # Whitespace-normalized: pinning the paragraph's line wrap would
                # redden this on a reflow that changes no rule.
                flat = " ".join(text.split())
                with self.subTest(tree=tree, agent=name):
                    if name == "code-reviewer":
                        self.assertIn("`FIX_NOW` equals the number of `findings`", flat)
                        self.assertIn("`OPTIONAL=0` always", flat)
                        self.assertIn(
                            "A non-null `blocker` means `BLOCKED_ENV`", flat,
                        )
                    else:
                        self.assertIn("`VERDICT: PASS` requires `FIX_NOW=0`", flat)
                        self.assertIn(
                            "`INVESTIGATE` and `OPTIONAL` counts are compatible with PASS",
                            flat,
                        )


class TrailingCommentaryDoesNotDiscardTheVerdict(unittest.TestCase):
    """`VERDICT: PASS — report complete.` was measured in the field and bound nothing."""

    def test_a_verdict_line_with_trailing_commentary_binds(self):
        _lib = _lib_mod()
        self.assertEqual(
            _lib.extract_qa_verdict("VERDICT: PASS — report complete."), "PASS"
        )

    def test_a_longer_token_is_not_the_verdict(self):
        """`PASSING` and `PASS_LATER` name nothing this contract defines."""
        _lib = _lib_mod()
        for line in (
            "VERDICT: PASSING",
            "VERDICT: PASSable now",
            "VERDICT: PASS_LATER",
            "VERDICT: FAILURE",
        ):
            with self.subTest(line=line):
                self.assertEqual(_lib.extract_qa_verdict(line), "")

    def test_a_hedge_that_continues_the_sentence_does_not_bind(self):
        """The tail must read as commentary, not as a qualification.

        A `qa-*` lens stores no counts line to contradict a wrongly bound PASS,
        and its verdict is the last gate before close, so a hedged line binding
        PASS is the worst reachable outcome of relaxing line 1 at all.
        """
        _lib = _lib_mod()
        for line in (
            "VERDICT: PASS if you accept the two failing tests below; otherwise FAIL",
            "VERDICT: PASS, actually FAIL",
            "VERDICT: PASS; see the blockers below",
        ):
            with self.subTest(line=line):
                self.assertEqual(_lib.extract_qa_verdict(line), "")

    def test_the_field_measured_restatements_still_bind(self):
        """The counter-case to the tightening: what it must not break."""
        _lib = _lib_mod()
        for line, want in (
            ("VERDICT: PASS", "PASS"),
            ("VERDICT: PASS.", "PASS"),
            ("VERDICT: PASS — report complete.", "PASS"),
            ("VERDICT: PASS (all clear)", "PASS"),
            ("VERDICT: BLOCKED_ENV - docker unavailable", "BLOCKED_ENV"),
        ):
            with self.subTest(line=line):
                self.assertEqual(_lib.extract_qa_verdict(line), want)

    def test_a_restatement_after_a_bound_pass_leaves_the_verdict_standing(self):
        """The end-to-end property the extractor test only appeared to cover.

        `extract_qa_verdict` is not where a restatement is lost, so asserting it
        there stayed green through a regression that made these exact strings
        evict the verdict they were restating. A review lens files them without
        a counts line, and classifying on the *presence* of a readable verdict
        rather than its value read them as substantive negatives.

        This is the field-observed path: bound PASS, re-invoked, restated.
        """
        _lib = _lib_mod()
        for restatement in (
            "VERDICT: PASS — report complete.",
            "VERDICT: PASS.",
            "VERDICT: PASS",
        ):
            with self.subTest(restatement=restatement):
                with _task_with_receipt() as (task_dir, run_id):
                    _complete(
                        task_dir, run_id,
                        "VERDICT: PASS\n"
                        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                    )
                    _start(task_dir, run_id, "review-code", "rerun")
                    _complete(task_dir, run_id, restatement, agent="rerun")
                    self.assertEqual(_lib.receipt_review_verdict(task_dir), "PASS")
                    self.assertEqual(
                        _lib.nonparsing_completion_lenses(task_dir),
                        {"review-code": "stale_followup"},
                    )

    def test_a_report_with_no_verdict_line_cannot_evict_on_its_counts_alone(self):
        """A surviving counts line must mean the pair was read and disagreed.

        Otherwise a report that named no verdict at all evicts a bound PASS and
        is described to its reader as "reported a readable line-1 verdict … not
        a restatement" — every clause false. The writer drops counts it cannot
        attach to a verdict, so the classifier reads an invariant instead of
        inferring one.

        Deciding this from the counts instead (fix_now>0 means substantive)
        would reinstate the round-2 hazard: `VERDICT: FAIL` beside FIX_NOW=0 is
        a reviewer saying "not done" and must still evict.
        """
        _lib = _lib_mod()
        for report, kind in (
            # No verdict, nothing blocking: a shape failure, and it must not
            # displace a verdict that was readable.
            ("Still good.\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n", "shape"),
            # No verdict, but FIX_NOW says the findings block. Dropping these
            # counts masked a reviewer that listed three blockers — the same
            # masked negative as a swallowed FAIL, not a lesser routing loss.
            ("Still broken.\nFINDING_COUNTS: FIX_NOW=2 INVESTIGATE=0 OPTIONAL=0\n",
             "inconsistent"),
            # INVESTIGATE alone is non-blocking by the rule both reviewer
            # definitions state, so it stays a shape failure.
            ("No verdict here.\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=3 OPTIONAL=0\n",
             "shape"),
            ("VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
             "inconsistent"),
            ("VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=3 INVESTIGATE=0 OPTIONAL=0\n",
             "inconsistent"),
        ):
            with self.subTest(report=report.splitlines()[0]):
                verdict, compact = _lib.normalize_receipt_completion(
                    "review-code", report
                )
                self.assertEqual(verdict, "PENDING")
                self.assertEqual(
                    _lib._pending_completion_kind("review-code", {"summary": compact}),
                    kind,
                )

    def test_the_watchers_invalidation_can_displace_a_stale_pass(self):
        """An invalidated run is void, so it must outlive the PASS it invalidates.

        `codex_lifecycle_watcher._invalidate` writes `VERDICT: PENDING` — which
        binds nothing by design — plus a counts line. Coded as INVESTIGATE-only
        it read as non-blocking and could not displace the very verdict it was
        invalidating; `FIX_NOW` is what says the finding blocks.
        """
        _lib = _lib_mod()
        watcher_summary = (
            "VERDICT: PENDING\n"
            "FINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n"
            "Runtime watcher invalidated: session ended\n"
        )
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            _start(task_dir, run_id, "review-code", "rerun")
            _complete(task_dir, run_id, watcher_summary, agent="rerun")
            self.assertEqual(_lib.receipt_review_verdict(task_dir), "PENDING")

    def test_the_watcher_emits_a_blocking_invalidation(self):
        """Pin the producer too — the shape above is only correct if it is sent."""
        from conftest import REPO_ROOT  # type: ignore

        text = (
            Path(REPO_ROOT) / "plugin" / "scripts" / "codex_lifecycle_watcher.py"
        ).read_text(encoding="utf-8")
        self.assertIn("FINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0", text)

    def test_a_verdictless_report_does_not_evict_a_bound_pass(self):
        """The end-to-end form of the case above."""
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            _start(task_dir, run_id, "review-code", "rerun")
            _complete(
                task_dir, run_id,
                "Still good.\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                agent="rerun",
            )
            self.assertEqual(_lib.receipt_review_verdict(task_dir), "PASS")

    def test_the_retained_token_decides_eviction_not_its_presence(self):
        """A readable PASS restates; a readable FAIL objects. Both are readable."""
        _lib = _lib_mod()
        for report, kind in (
            ("VERDICT: PASS — report complete.", "shape"),
            ("VERDICT: PASS", "shape"),
            ("VERDICT: FAIL — three blockers found.", "inconsistent"),
            ("VERDICT: BLOCKED_ENV - the sandbox is gone.", "inconsistent"),
            ("Still good.", "shape"),
        ):
            with self.subTest(report=report):
                verdict, compact = _lib.normalize_receipt_completion(
                    "review-code", report
                )
                self.assertEqual(verdict, "PENDING")
                self.assertEqual(
                    _lib._pending_completion_kind("review-code", {"summary": compact}),
                    kind,
                )

    def test_a_later_bare_line_naming_a_different_verdict_still_voids(self):
        """Relaxing line 1 must not open a hole in the conflict rule."""
        _lib = _lib_mod()
        self.assertEqual(
            _lib.extract_qa_verdict("VERDICT: PASS — done.\nVERDICT: FAIL"), ""
        )

    def test_prose_discussing_a_different_verdict_is_still_safe(self):
        """The relaxation is line-1 only.

        Applying it to the conflict scan would let a sentence that merely starts
        with the token destroy a report — the self-destroying-review failure the
        bare-only rule exists to prevent, and the exact thing a review *of this
        subsystem* must be free to write.
        """
        _lib = _lib_mod()
        for later in (
            # Unpunctuated: rejected by the line-1 head pattern too, so on its
            # own this case stopped discriminating once the tail was tightened.
            "VERDICT: FAIL was last round's result, now resolved.",
            # Punctuated: this one *would* match the relaxed head pattern, so it
            # is the case that actually proves the conflict scan stayed strict.
            "VERDICT: FAIL — last round's result, now resolved.",
            "VERDICT: FAIL. That was round one; it is fixed.",
        ):
            with self.subTest(later=later):
                self.assertEqual(
                    _lib.extract_qa_verdict(f"VERDICT: PASS\n{later}"), "PASS"
                )


class ABoundVerdictSurvivesALaterRestatement(unittest.TestCase):
    """Selection ran before validation, so the newest record won even if unusable.

    A lens that reported properly and was then invoked again answered with a
    short restatement. That restatement bound nothing — and evicted the good
    receipt, because the selector took the last event and only then asked
    whether it was usable.
    """

    def test_an_unbindable_rerun_does_not_evict_the_bound_verdict(self):
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            self.assertEqual(
                _complete(
                    task_dir, run_id,
                    "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                ),
                "PASS",
            )
            # The re-invocation: same lens, fresh identity, unreadable answer.
            _start(task_dir, run_id, "review-code", "rerun")
            self.assertEqual(
                _complete(task_dir, run_id, "Still good.", agent="rerun"), "PENDING"
            )

            completed = _lib._completed_review_by_lens(task_dir)
            self.assertEqual(
                str(completed["review-code"].get("verdict")).upper(), "PASS"
            )
            # The bound verdict stands, so nothing is blocked and no rerun is
            # required — but the unreadable follow-up is still named rather than
            # silently dropped.
            self.assertEqual(
                _lib.nonparsing_completion_lenses(task_dir),
                {"review-code": "stale_followup"},
            )

    def test_a_binding_rerun_still_supersedes(self):
        """Remediation genuinely produces a newer result. Last *binding* wins."""
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            _start(task_dir, run_id, "review-code", "rerun")
            _complete(
                task_dir, run_id,
                "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n",
                agent="rerun",
            )
            completed = _lib._completed_review_by_lens(task_dir)
            self.assertEqual(
                str(completed["review-code"].get("verdict")).upper(), "FAIL"
            )

    def test_a_rerun_that_reports_something_negative_always_displaces_the_pass(self):
        """A read report that says "not done" must never be masked by an old PASS.

        These four bind no verdict, so an earlier "last binding wins" rule
        returned the previous round's PASS and the diagnostic stayed silent —
        the runtime would close a task over a live objection with no surface
        mentioning it. That is strictly worse than the deadlock this module
        exists to fix. The distinguishing datum is the stored counts line:
        `FINDING_COUNTS: INVALID` means nothing could be read, anything else
        means the report was read and disagreed.
        """
        _lib = _lib_mod()
        negatives = (
            "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=3 INVESTIGATE=0 OPTIONAL=0\n",
            "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            "VERDICT: BLOCKED_ENV\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
        )
        for summary in negatives:
            with self.subTest(round_two=summary.splitlines()[0]):
                with _task_with_receipt() as (task_dir, run_id):
                    _complete(
                        task_dir, run_id,
                        "VERDICT: PASS\n"
                        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                    )
                    _start(task_dir, run_id, "review-code", "rerun")
                    self.assertEqual(
                        _complete(task_dir, run_id, summary, agent="rerun"), "PENDING"
                    )
                    completed = _lib._completed_review_by_lens(task_dir)
                    self.assertEqual(
                        str(completed["review-code"].get("verdict")).upper(), "PENDING"
                    )
                    # And it must be named, not silently swallowed.
                    self.assertEqual(
                        _lib.nonparsing_completion_lenses(task_dir),
                        {"review-code": "inconsistent"},
                    )

    def test_a_readable_negative_evicts_even_when_its_counts_line_is_unreadable(self):
        """The likeliest degraded rerun: a real FAIL with no usable counts line.

        Classifying on the counts line alone called this a restatement, so the
        previous round's PASS survived a reviewer's `FAIL` and nothing said so.
        The verdict token was readable; the receipt now records that.
        """
        _lib = _lib_mod()
        for second_round in (
            "VERDICT: FAIL — three blockers found.\n",
            "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n"
            "FINDING_COUNTS: FIX_NOW=9 INVESTIGATE=0 OPTIONAL=0\n",
        ):
            with self.subTest(second_round=second_round.splitlines()[0]):
                with _task_with_receipt() as (task_dir, run_id):
                    _complete(
                        task_dir, run_id,
                        "VERDICT: PASS\n"
                        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                    )
                    _start(task_dir, run_id, "review-code", "rerun")
                    _complete(task_dir, run_id, second_round, agent="rerun")
                    self.assertEqual(_lib.receipt_review_verdict(task_dir), "PENDING")
                    self.assertEqual(
                        _lib.nonparsing_completion_lenses(task_dir),
                        {"review-code": "inconsistent"},
                    )

    def test_an_unreadable_followup_to_a_bound_verdict_is_named_not_silent(self):
        """A QA lens has no counts line, so eviction is not available.

        Evicting on an unreadable QA follow-up would reinstate the deadlock, so
        the bound verdict stands — but staying silent would mean a report nobody
        could read is also a report nobody is told about. This kind is advisory:
        it asks for a confirmation and never requires a rerun.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _write_plan(task_dir, ["review-code", "qa-cli"])
            _start(task_dir, run_id, "review-code", "rev1")
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                agent="rev1",
            )
            _start(task_dir, run_id, "qa-cli", "qa1")
            _complete(task_dir, run_id, "VERDICT: PASS\n", lens="qa-cli", agent="qa1")
            _start(task_dir, run_id, "qa-cli", "qa2")
            _complete(
                task_dir, run_id,
                "Re-ran the suite: the new --json flag crashes on empty input.",
                lens="qa-cli", agent="qa2",
            )
            kinds = _lib.nonparsing_completion_lenses(task_dir)
            self.assertEqual(kinds, {"qa-cli": "stale_followup"})
            note = _lib.nonparsing_completion_note(kinds)
            # And it must reach the surface a coordinator actually reads.
            # `stale_followup` only occurs when a lens bound a non-PENDING
            # verdict, so the all-PASS branch of emit_compact_context is the one
            # state it can happen in — and that branch used to discard the note,
            # making the kind computed and thrown away in exactly the case it
            # was written for. Asserting the helpers alone could not see that.
            surfaced = _lib.emit_compact_context(task_dir)["next_action"]
        self.assertIn("Bound verdict stands", note)
        self.assertIn("no rerun is needed to restore it", note)
        self.assertNotIn("not in the position and shape", note)
        self.assertIn("Bound verdict stands", surfaced)
        self.assertIn("run task_close", surfaced)
        # The branch also fires for a bound FAIL or BLOCKED_ENV, so the sentence
        # must not assert the state of the gate — it once said "nothing is
        # blocked" beside a runtime verdict of FAIL.
        self.assertNotIn("Nothing is blocked", note)

    def test_only_the_advisory_kind_rides_the_closable_branch(self):
        """A work order must not print immediately before "run task_close".

        An undeclared lens reaches the all-PASS branch — the kind scan covers
        every supported lens, not only declared ones, and `review-security`
        routinely runs undeclared. Prefixing the whole note there emitted
        "route the findings and respawn" beside "run task_close" with nothing
        marking which governs.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _write_plan(task_dir, ["review-code", "qa-cli"])
            _start(task_dir, run_id, "review-code", "rev1")
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                agent="rev1",
            )
            _start(task_dir, run_id, "qa-cli", "qa1")
            _complete(task_dir, run_id, "VERDICT: PASS\n", lens="qa-cli", agent="qa1")
            # Undeclared lens, self-contradictory report: kind `inconsistent`.
            _start(task_dir, run_id, "review-security", "sec1")
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=2 INVESTIGATE=0 OPTIONAL=0\n",
                lens="review-security", agent="sec1",
            )
            ctx = _lib.emit_compact_context(task_dir)
        self.assertEqual(ctx["runtime_verdict"], "PASS")
        self.assertIn("run task_close", ctx["next_action"])
        self.assertNotIn("Route the reported findings first", ctx["next_action"])
        self.assertNotIn("Recorded but unusable", ctx["next_action"])

    def test_an_unpaired_followup_names_the_pairing_failure_not_the_followup(self):
        """An identity-invalid follow-up is a receipt problem first.

        It reaches the same branch as a readable restatement, where the advice
        happens to be harmless — but the pairing failure would never be named,
        which is the wrong-diagnosis-costs-a-loop failure one level down.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            # No `started` receipt for this identity.
            _complete(
                task_dir, run_id,
                "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n",
                agent="orphan",
            )
            self.assertEqual(
                _lib.nonparsing_completion_lenses(task_dir),
                {"review-code": "unpaired"},
            )
            # The bound verdict is untouched: an untrusted record cannot evict.
            self.assertEqual(_lib.receipt_review_verdict(task_dir), "PASS")

    def test_an_offposition_negative_still_displaces_a_bound_pass(self):
        """A verdict block pushed off line 1 loses its token AND its counts.

        Positional authority decides what binds and is not relaxed. But using
        position to also decide whether an unbound report was *negative* made a
        review reporting `VERDICT: FAIL` with `FIX_NOW=3` — one line lower than
        the contract requires, an observed field failure — read as an unreadable
        restatement, so the previous round's PASS survived and the task closed
        over a live objection. That is the outcome `_effective_completion`'s own
        docstring commits against.
        """
        _lib = _lib_mod()
        for second_round in (
            "I found three blockers.\n"
            "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=3 INVESTIGATE=0 OPTIONAL=0\n",
            "Environment notes first.\n"
            "VERDICT: BLOCKED_ENV\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=1 OPTIONAL=0\n",
        ):
            with self.subTest(second_round=second_round.splitlines()[1]):
                with _task_with_receipt() as (task_dir, run_id):
                    _complete(
                        task_dir, run_id,
                        "VERDICT: PASS\n"
                        "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                    )
                    _start(task_dir, run_id, "review-code", "rerun")
                    _complete(task_dir, run_id, second_round, agent="rerun")
                    self.assertEqual(_lib.receipt_review_verdict(task_dir), "PENDING")
                    self.assertEqual(
                        _lib.nonparsing_completion_lenses(task_dir),
                        {"review-code": "inconsistent"},
                    )

    def test_an_offposition_pass_is_recorded_as_unreadable_not_as_a_pass(self):
        """The scan only looks for negatives, and the slot must not overclaim.

        Eviction is unaffected either way — `UNREADABLE PASS` and `INVALID` both
        classify `shape` — so this pins the *record*, not the gate. A report
        whose line 1 was prose did not report PASS in any position that binds,
        and writing `UNREADABLE PASS` into its receipt would assert the lens
        said something it did not say in an authoritative place.
        """
        _lib = _lib_mod()
        verdict, compact = _lib.normalize_receipt_completion(
            "review-code",
            "Summary first.\n"
            "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
        )
        self.assertEqual(verdict, "PENDING")
        self.assertIn("FINDING_COUNTS: INVALID", compact)
        self.assertNotIn("UNREADABLE", compact)

    def test_an_offposition_pass_cannot_displace_anything(self):
        """The scan may refuse to mask a negative; it may never grant one."""
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            _start(task_dir, run_id, "review-code", "rerun")
            _complete(
                task_dir, run_id,
                "Summary first.\n"
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
                agent="rerun",
            )
            self.assertEqual(_lib.receipt_review_verdict(task_dir), "PASS")

    def test_disagreeing_offposition_verdicts_are_declined_not_guessed(self):
        """Ambiguity is what positional authority exists to settle."""
        _lib = _lib_mod()
        verdict, compact = _lib.normalize_receipt_completion(
            "review-code", "prose\nVERDICT: FAIL\nVERDICT: PASS\n"
        )
        self.assertEqual(verdict, "PENDING")
        self.assertEqual(
            _lib._pending_completion_kind("review-code", {"summary": compact}), "shape"
        )

    def test_a_selfdescribed_pass_stops_the_offposition_scan(self):
        """A report that already said PASS on line 1 must not be re-read.

        `VERDICT: PASS, no blockers found` misses the binding tail class by a
        comma. Scanning on regardless let a `VERDICT: FAIL` quoted as an example
        revoke a genuine PASS — and every review of this subsystem quotes
        exactly that. Fail-closed, but the coordinator is then told to route
        findings that do not exist.
        """
        _lib = _lib_mod()
        verdict, compact = _lib.normalize_receipt_completion(
            "review-code",
            "VERDICT: PASS, no blockers found\n"
            "FINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
            "The binder rejects a report shaped like:\n"
            "```\nVERDICT: FAIL\n```\n",
        )
        self.assertEqual(verdict, "PENDING")
        self.assertIn("FINDING_COUNTS: INVALID", compact)
        self.assertEqual(
            _lib._pending_completion_kind("review-code", {"summary": compact}), "shape"
        )

    def test_authoritative_counts_never_depend_on_the_offposition_scan(self):
        """Line 2 is where the contract puts the counts, so it keeps its own path.

        Routing it through the scan gave it the scan's self-describe veto and
        inverted the discriminator: a report naming PASS beside `FIX_NOW=3` was
        masked while one naming nothing was not, and the surface then asserted
        "that follow-up changes nothing" about the record listing three
        blockers. A near-miss on line 1 (`VERDICT: PASS,` — any character
        outside the binding tail class) is the whole trigger.
        """
        _lib = _lib_mod()
        for head in (
            "VERDICT: PASS, no blockers found",   # near-miss, self-describes PASS
            "No blockers found.",                 # control: names nothing
        ):
            with self.subTest(head=head):
                verdict, compact = _lib.normalize_receipt_completion(
                    "review-code",
                    f"{head}\n"
                    "FINDING_COUNTS: FIX_NOW=3 INVESTIGATE=0 OPTIONAL=0\n"
                    "1. blocker 2. blocker 3. blocker\n",
                )
                self.assertEqual(verdict, "PENDING")
                self.assertIn("FIX_NOW=3", compact)
                self.assertEqual(
                    _lib._pending_completion_kind("review-code", {"summary": compact}),
                    "inconsistent",
                )

    def test_the_selfdescribe_guard_matches_whole_tokens_only(self):
        """`PASSable` is not a self-described PASS.

        A case-sensitive lookahead let it veto the scan, masking an
        off-position FAIL beneath it, while `PASSING` correctly still scanned.
        """
        _lib = _lib_mod()
        for head in ("VERDICT: PASSable now", "VERDICT: PASSING"):
            with self.subTest(head=head):
                _verdict, compact = _lib.normalize_receipt_completion(
                    "review-code", f"{head}\nprose\nVERDICT: FAIL\n"
                )
                self.assertIn("UNREADABLE FAIL", compact)

    def test_a_selfdescribed_negative_still_scans(self):
        """Declining is only for non-negatives; a FAIL that misses the tail
        class must still be recoverable from a bare line elsewhere."""
        _lib = _lib_mod()
        verdict, compact = _lib.normalize_receipt_completion(
            "review-code", "VERDICT: FAIL, three blockers\nVERDICT: FAIL\n"
        )
        self.assertEqual(verdict, "PENDING")
        self.assertIn("UNREADABLE FAIL", compact)

    def test_the_pending_bound_branch_also_names_a_pairing_failure(self):
        """3-b was fixed on one branch and left on its sibling.

        Reverting the pending-bound check left the whole suite green, because
        the existing case exercised only the bound-verdict branch.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            # A valid but shape-PENDING completion: nothing binds.
            _complete(task_dir, run_id, "Still good.")
            # Then an orphan completion with no `started` for its identity.
            _complete(
                task_dir, run_id,
                "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n",
                agent="orphan",
            )
            self.assertEqual(
                _lib.nonparsing_completion_lenses(task_dir),
                {"review-code": "unpaired"},
            )

    def test_an_unpaired_completion_is_not_described_as_a_format_failure(self):
        """A completion with no matching start is a receipt-pairing failure.

        Telling its reader the verdict block was malformed is false in every
        clause and sends them to re-read a report whose text was never the
        problem. It has its own kind and its own sentence.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            # A well-formed report, but no `started` receipt for this identity.
            _complete(
                task_dir, run_id,
                "VERDICT: FAIL\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0\n",
                lens="review-security", agent="orphan",
            )
            self.assertEqual(
                _lib.nonparsing_completion_lenses(task_dir),
                {"review-security": "unpaired"},
            )
            note = _lib.nonparsing_completion_note(
                _lib.nonparsing_completion_lenses(task_dir)
            )
        self.assertIn("could not be bound to exactly one start/stop pair", note)
        self.assertIn("SubagentStart", note)
        # `bound is None` also catches a replayed completion, so the sentence
        # must name that cause too rather than sending the reader to audit hook
        # registration for a tree that registers both events correctly.
        self.assertIn("replayed", note)
        # The two wrong diagnoses must not appear for this kind.
        self.assertNotIn("verdict and finding counts contradict", note)
        self.assertNotIn("not in the position and shape", note)

    def test_a_rerun_still_in_flight_suppresses_the_earlier_completion(self):
        """A lens currently re-reviewing has not reported yet.

        Keeping its previous PASS current would let a task close mid-review.
        Only completion-versus-completion selection changed.
        """
        _lib = _lib_mod()
        with _task_with_receipt() as (task_dir, run_id):
            _complete(
                task_dir, run_id,
                "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n",
            )
            _start(task_dir, run_id, "review-code", "rerun")
            self.assertEqual(_lib._completed_review_by_lens(task_dir), {})


if __name__ == "__main__":
    unittest.main()
