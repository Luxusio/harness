"""`doc/harness/qa/QA_KNOWLEDGE.yaml` must stay loadable and mapping-shaped.

Every `qa-*` agent is told to read this file first and to append discoveries to
it, so an append in the wrong shape is a normal, expected event — and its blast
radius is the whole file, not the new entry. On 2026-09-02 a QA lens appended a
top-level sequence item to this mapping document; parsing it then raised and
every accumulated section became unreadable at once, including the note that
existed to stop the next session re-diagnosing a known-expected warning.

Four consecutive review rounds and a 1010-test suite missed it for one reason:
nothing parsed the file. This test is that parse.

PyYAML is not a dependency of this repo (see
`test_no_toplevel_third_party_imports`), so the parser is imported inside the
tests and the checks degrade to a structural scan when it is absent.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "doc" / "harness" / "qa" / "QA_KNOWLEDGE.yaml"

# --- known-red claims -------------------------------------------------------
#
# A note saying "this test is a known/baseline red" is the most dangerous thing
# this file can hold, because its whole function is to stop the next reader
# investigating. Twice now it has done exactly that against a real regression:
# the same test shipped red in the commit that introduced it, and again in
# a01bf3f, and several sessions called it "pre-existing" and moved on. See
# doc/harness/REQ__recorded-claims-must-stay-falsifiable.md.
#
# So a live claim of that kind is held to two things a machine can check: it
# names a pytest node id, and that node id still actually fails. A claim whose
# red has been fixed is not neutral — it is an excuse waiting for the next
# genuine failure — so it fails here until someone marks it SUPERSEDED.

# Deliberately narrow, and narrowed twice.
#
# The harmful class is not "a note that mentions a failure" — it is a note that
# licenses the reader to *expect* a specific failure and move on. Matching on
# "failing" caught a note describing an intermittent PermissionError and
# demanded the tests it named be red, which they are not; that note asks for
# vigilance, not for permission.
#
# Matching a bare marker word was still too wide: "baseline" and "pre-existing"
# carry their ordinary meanings all over this file — a baseline recipe, a
# baseline lint output, suite baseline totals, a pre-existing behaviour of
# handle_task_verify. Six live notes matched that way. Requiring each of them
# to name a pytest node id would have been six false alarms; exempting them for
# having no node id would have made the node-id requirement unenforced while
# this comment claimed otherwise. So the marker must sit next to the failure
# word, which is how the real known-red notes are written ("Known pre-existing
# RED at master 90a7afe", "Baseline RED confirmed again").
#
# The failure word is "red" alone, not "failure"/"failing". `known failure`
# and `known test failure` are ordinary English — "a known failure mode when
# run twice" describes a hazard to watch for, and flagging it as an
# unfalsifiable claim would make the guard noisy enough to get weakened. `red`
# in this sense is jargon; it is what all three real claims use, and prose
# rarely reaches for it by accident. The cost is recorded in the REQ: a future
# note written "pre-existing failure in tests/x.py::y" is not caught.
KNOWN_RED_RE = re.compile(
    r"\b(?:pre-?existing|baseline|known|expected|standing)[ -](?:test[ -])?red\b",
    re.IGNORECASE,
)
# The parametrize suffix is part of the node id. Dropping it would probe the
# bare node, running every variant, so an aggregate exit 1 from some unrelated
# variant could vouch for a stale claim about one bracketed case.
NODE_ID_RE = re.compile(r"tests/[\w./-]+\.py(?:::\w+)+(?:\[[^\]\s]*\])?")
COMMIT_RE = re.compile(r"\b[0-9a-f]{7,40}\b")
SUPERSEDED = "SUPERSEDED"
PROBE_TIMEOUT_SECONDS = 120


def known_red_claims(texts):
    """Return `(text, node_id)` for every live known-red claim in `texts`.

    A claim is live when it is not marked SUPERSEDED. Exemption is checked on
    the whole entry rather than per line: the existing markers sit on a
    different line of the same folded block than the node id they retire.

    `node_id` is None when the note uses known-red language but names nothing
    runnable. That is a claim too, and the worst-behaved kind — unfalsifiable
    by construction — so it is reported rather than skipped. Yielding nothing
    there is what let the requirement read as enforced while it was not.
    """
    claims = []
    for text in texts:
        if SUPERSEDED in text or not KNOWN_RED_RE.search(text):
            continue
        nodes = NODE_ID_RE.findall(text)
        if nodes:
            claims.extend((text, node) for node in nodes)
        else:
            claims.append((text, None))
    return claims


def red_claim_violation(node_id, cwd=ROOT, timeout=PROBE_TIMEOUT_SECONDS):
    """Return a complaint string if `node_id` does not currently fail, else "".

    pytest exit codes carry the distinction that matters: 1 is "tests failed",
    0 means the claim is stale, 4/5 mean it names a node that cannot be
    collected — just as unusable to a reader and never a pass. Everything else
    is the probe's own failure and says so, rather than blaming the note for an
    interrupted or crashed run.

    Exit 1 alone is NOT accepted as evidence that the named node failed. The
    nested session is a whole pytest run, so anything session-scoped that
    errors — a teardown guard, a fixture — also yields rc 1 while the selected
    test passes (`1 passed, 1 error`). Reading that as "still red" would vouch
    for a stale claim, which is precisely the false green this guard exists to
    prevent. The summary line has to show a failure and no error.
    """
    # `-o addopts=` rather than `-p no:xdist`: this repo's addopts carry `-n`
    # and `--dist`, so disabling the plugin that defines them turns every probe
    # into pytest exit 4 — which this function would then report as an
    # uncollectable node, blaming the note for the checker's own bad argv.
    #
    # HARNESS_NESTED_PROBE tells tests/conftest.py that this is a nested
    # session: its `.active` snapshot/restore and its install-tree guard must
    # not run. Without it the probe renames the developer's live task marker
    # away for its duration and can unlink one a concurrent sibling test just
    # created.
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", node_id, "-q", "--no-header",
             "-o", "addopts=", "-p", "no:cacheprovider"],
            cwd=str(cwd), capture_output=True, text=True, check=False,
            env={**os.environ, "HARNESS_NESTED_PROBE": "1"},
            # A claim may name a hung test. Without a bound, the guard against
            # unverifiable claims becomes an unverifiable hang itself.
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return (
            f"{node_id} did not finish within {timeout}s, so the claim cannot "
            f"be checked"
        )
    if proc.returncode == 1:
        summary = f"{proc.stdout or ''}\n{proc.stderr or ''}"
        if re.search(r"\b\d+ error", summary):
            return (
                f"probing {node_id} hit a session-level error, so exit 1 is not "
                f"evidence that the node itself failed"
            )
        if not re.search(r"\b\d+ failed", summary):
            return (
                f"probing {node_id} exited 1 without reporting a failed test, "
                f"so the claim cannot be confirmed"
            )
        return ""
    if proc.returncode == 0:
        return f"{node_id} passes now; the note is stale — mark it {SUPERSEDED}"
    if proc.returncode in (4, 5):
        return (
            f"{node_id} could not be collected (pytest exit {proc.returncode}) "
            f"— the test was most likely renamed or removed; update the note to "
            f"the current node id, or mark it {SUPERSEDED}"
        )
    return (
        f"probing {node_id} failed (pytest exit {proc.returncode}); this is the "
        f"probe's own failure, not evidence about the claim"
    )

TOP_LEVEL_SECTIONS = (
    "services",
    "selectors",
    "test_data",
    "known_issues",
    "patterns",
    "qa_notes",
)


def _yaml():
    try:
        import yaml  # type: ignore
    except ImportError:
        return None
    return yaml


class QaKnowledgeShapeTests(unittest.TestCase):
    def setUp(self):
        self.text = KNOWLEDGE.read_text(encoding="utf-8")

    def test_no_top_level_sequence_item(self):
        """The break was a `- ` at column 0 in a mapping document.

        Checked without a parser so the guard holds on a bare interpreter.
        """
        offenders = [
            (n, line)
            for n, line in enumerate(self.text.splitlines(), 1)
            if re.match(r"^- ", line)
        ]
        self.assertEqual(offenders, [], offenders)

    def test_parses_and_keeps_its_sections(self):
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML absent")
        data = yaml.safe_load(self.text)
        self.assertIsInstance(data, dict)
        for section in TOP_LEVEL_SECTIONS:
            self.assertIn(section, data)

    def test_every_qa_note_is_a_named_mapping(self):
        """Appends belong under `qa_notes` as `<slug>: {discovered, notes}`."""
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML absent")
        notes = yaml.safe_load(self.text)["qa_notes"]
        self.assertIsInstance(notes, dict)
        self.assertTrue(notes)
        for name, entry in notes.items():
            self.assertIsInstance(name, str, name)
            self.assertIsInstance(entry, dict, name)
            self.assertIn("notes", entry, name)


class KnownRedClaimsTests(unittest.TestCase):
    """The live file, plus the checker itself.

    The live-file case is expected to find nothing today — both existing
    known-red notes are SUPERSEDED — so on its own it would pass vacuously and
    keep passing if the extractor silently stopped matching anything. The
    synthetic cases below are what give it teeth, and they need no parser, so
    the logic stays covered even where the live scan skips.
    """

    def _entry_texts(self):
        yaml = _yaml()
        if yaml is None:
            self.skipTest("PyYAML absent")
        data = yaml.safe_load(KNOWLEDGE.read_text(encoding="utf-8"))
        texts = []
        def walk(node):
            if isinstance(node, str):
                texts.append(node)
            elif isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
        walk(data)
        return texts

    def test_live_known_red_claims_name_a_node_that_is_still_red(self):
        for text, node in known_red_claims(self._entry_texts()):
            with self.subTest(node=node):
                self.assertIsNotNone(
                    node,
                    f"known-red claim names no pytest node id, so nothing can "
                    f"check it — name the test it refers to, or drop the "
                    f"known-red wording if it is not claiming one: {text}",
                )
                self.assertEqual(red_claim_violation(node), "", text)

    def test_live_known_red_claims_name_their_introducing_commit(self):
        """Without it the claim cannot be bisected, only believed.

        Both historical incidents were resolved by `git bisect`, and both cost
        several sessions first because the note recorded the symptom and not
        where it came from.
        """
        for text, node in known_red_claims(self._entry_texts()):
            # A claim naming nothing runnable already fails the test above with
            # the actionable complaint. Repeating "and it names no commit" here
            # is noise on a note whose real problem is that it is unfalsifiable.
            if node is None:
                continue
            with self.subTest(node=node):
                self.assertTrue(
                    COMMIT_RE.search(text.replace(node, "")),
                    f"known-red claim names no introducing commit — add the "
                    f"commit that introduced the red so the next reader can "
                    f"bisect instead of believing it: {text}",
                )

    def test_the_retired_claims_in_the_real_file_are_recognisable_claims(self):
        """Non-vacuity against real wording, not just the synthetic string.

        The live scan finds nothing today because every known-red note here is
        SUPERSEDED, so it would keep passing if the pattern quietly stopped
        matching anything. Strip the markers and the known notes must surface.

        Deliberately a subset check, and deliberately silent about how many
        live claims exist. An earlier version asserted `known_red_claims(texts)
        == []` and that *every* revived claim named the receipt-watcher node,
        which meant the suite went red the moment anyone used the mechanism as
        the REQ prescribes — one correctly-formed live claim, or one correctly
        retired claim about some other test, and this failed. A guard that
        punishes correct use gets deleted, and it would have taken the real
        checks with it. Whether live claims are legitimate is owned by
        `test_live_known_red_claims_name_a_node_that_is_still_red`; this test
        owns only "the pattern still recognises real wording".
        """
        texts = self._entry_texts()
        revived = {node for _text, node in
                   known_red_claims([t.replace(SUPERSEDED, "") for t in texts])}
        self.assertTrue(revived, "the known-red pattern matches nothing in the real file")
        self.assertTrue(
            any("test_receipt_watcher_fail_closed" in (node or "") for node in revived),
            revived,
        )

    def test_extractor_recognises_a_claim_and_honours_supersession(self):
        live = "Known pre-existing RED at master 90a7afe: tests/test_x.py::C::test_y."
        retired = live + " SUPERSEDED 2026-09-17 — fixed, do not deselect."
        unrelated = "The suite runs via `uv run pytest tests/ -q`."
        no_node = "Known pre-existing RED on this host, see the run above."
        # Names failing tests, but as a symptom to watch for — not as a red the
        # reader may skip. This is a live note in the real file.
        intermittent = (
            "Observed intermittent signature: PermissionError raised from "
            "_trusted_control_writer(), failing tests in tests/test_z.py::C."
        )
        # "baseline" and "pre-existing" in their ordinary senses. Six notes of
        # this kind are live in the real file; none is a known-red claim.
        ordinary_senses = [
            "`contract_lint.py --check-weight` baseline output is exit 0.",
            "Suite baseline after TASK__x: 1418 passed, exit 0.",
            "Template-vs-root managed-block divergence baseline recipe: ...",
            "handle_task_verify overwrites next_action. Pre-existing at 90a7afe.",
        ]

        self.assertEqual(
            known_red_claims(
                [live, retired, unrelated, intermittent, *ordinary_senses],
            ),
            [(live, "tests/test_x.py::C::test_y")],
        )
        # Language that licenses skipping but names nothing runnable is a
        # claim, reported with no node rather than silently exempted.
        self.assertEqual(known_red_claims([no_node]), [(no_node, None)])

    def test_ordinary_failure_prose_is_not_a_claim(self):
        """The boundary the no-node arm made expensive to get wrong.

        Before the node-id requirement was enforced, a false positive here was
        harmless — the note named nothing, so nothing ran. Now it fails the
        suite, so `known failure` and `known test failure`, which are ordinary
        English, must not read as known-red jargon.
        """
        for benign in (
            "This is a known test failure that QA should watch for, not skip.",
            "The suite has a known failure mode when run twice in one sandbox.",
            "Pre-existing behaviour of handle_task_verify, not a regression.",
        ):
            with self.subTest(benign=benign):
                self.assertEqual(known_red_claims([benign]), [])

    def test_a_parametrized_case_is_probed_as_the_case_it_named(self):
        """The bracketed suffix is part of the node id.

        Dropping it probes the bare node, which runs every variant — so an
        unrelated failing variant would vouch for a stale claim about one
        specific case, and a still-red case could be lost among passing ones.
        """
        text = "Known pre-existing RED at 90a7afe: tests/test_x.py::C::test_y[param-1]."
        self.assertEqual(
            known_red_claims([text]), [(text, "tests/test_x.py::C::test_y[param-1]")],
        )

    def test_a_missing_introducing_commit_is_detectable(self):
        """The live scan above has nothing to check today, so check the rule.

        Without this, `test_live_known_red_claims_name_their_introducing_commit`
        is an empty loop that would keep passing even if COMMIT_RE matched
        nothing — and the first real claim to arrive would sail through.
        """
        with_commit = "Known pre-existing RED at master 90a7afe: tests/t.py::C::t."
        without = "Known pre-existing RED, seen today: tests/t.py::C::t."
        node = "tests/t.py::C::t"

        self.assertTrue(COMMIT_RE.search(with_commit.replace(node, "")))
        self.assertIsNone(COMMIT_RE.search(without.replace(node, "")))

    def test_the_probe_survives_this_repo_s_own_addopts(self):
        """Runs against the real tree, which is the only place addopts apply.

        The synthetic cases below build a suite in a tmpdir with no inifile, so
        they cannot catch a probe argv that conflicts with this repo's
        `-n`/`--dist`. That exact argv bug made every probe report pytest exit 4
        — an uncollectable node — blaming the note for the checker's mistake.
        """
        passing = (
            "tests/test_qa_knowledge_shape.py::QaKnowledgeShapeTests"
            "::test_no_top_level_sequence_item"
        )
        self.assertIn("passes now", red_claim_violation(passing))

    def test_a_session_level_error_does_not_vouch_for_a_stale_claim(self):
        """Exit 1 is not by itself evidence about the named node.

        The probe runs a whole pytest session, so a session-scoped teardown
        that errors yields `1 passed, 1 error` and rc 1 while the selected test
        passed. Crediting that to the node would vouch for a stale claim — the
        exact false green this guard exists to prevent.
        """
        cases = {
            "1 passed, 1 error in 0.5s": "session-level error",
            "no summary at all": "without reporting a failed test",
        }
        for stdout, expected in cases.items():
            with self.subTest(stdout=stdout), mock.patch.object(
                subprocess, "run",
                return_value=subprocess.CompletedProcess([], 1, stdout, ""),
            ):
                self.assertIn(
                    expected, red_claim_violation("tests/test_x.py::C::test_y"),
                )

        # The genuine case still reads as red.
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess([], 1, "1 failed in 0.4s", ""),
        ):
            self.assertEqual(red_claim_violation("tests/test_x.py::C::test_y"), "")

    def test_conftest_honours_the_nested_probe_flag(self):
        """The other half: the flag only helps if conftest acts on it.

        `test_the_probe_marks_itself_as_a_nested_session` proves the probe
        sends it. This proves the session hooks read it, so neither side can
        be reverted silently — and it reads the env at call time rather than
        import time precisely so this is checkable.
        """
        import conftest

        # Asserted on the lock, not on `_SESSION_ACTIVE_BACKUP`. The first
        # version of this test checked that the backup global stayed None, and
        # it could not fail: by the time it runs, the outer session has already
        # renamed `.active` away, so the unguarded path also finds no marker
        # and also sets None. Deleting the guard left the suite green —
        # measured. Taking the lock is the first thing the unguarded path does
        # and is observable whatever the marker's state.
        #
        # The outer session's own backup path lives in that global, so it is
        # saved and restored: clobbering it would make the real
        # `pytest_sessionfinish` skip restoring the developer's `.active`,
        # causing the very loss this test exists to prevent.
        saved = conftest._SESSION_ACTIVE_BACKUP
        try:
            with mock.patch.dict(os.environ, {"HARNESS_NESTED_PROBE": "1"}), \
                    mock.patch.object(conftest, "active_marker_lock") as lock:
                self.assertTrue(conftest.is_nested_probe())
                conftest.pytest_sessionstart(None)
                lock.assert_not_called()

                # sessionfinish only reaches the lock when a backup path is
                # set, so exercise it in that state: otherwise its guard is an
                # unreachable branch that could be deleted unnoticed. A nested
                # session inheriting a populated global must not restore over
                # the parent's marker.
                sidecar = Path(
                    self.enterContext(__import__("tempfile").TemporaryDirectory()),
                ) / "active.backup"
                sidecar.write_text("parent", encoding="utf-8")
                conftest._SESSION_ACTIVE_BACKUP = str(sidecar)
                conftest.pytest_sessionfinish(None, 0)
                lock.assert_not_called()
                self.assertTrue(sidecar.is_file(), "the probe consumed the backup")
        finally:
            conftest._SESSION_ACTIVE_BACKUP = saved

        # Third guard site: the install-tree fixture. Driven through its
        # generator because the decorated object is a fixture, not a callable.
        # Without this the branch was revertible in silence — the two session
        # hooks are reached by this test, the fixture is not.
        with mock.patch.dict(os.environ, {"HARNESS_NESTED_PROBE": "1"}), \
                mock.patch.object(conftest, "_install_tree_inventory") as inventory:
            generator = conftest.install_trees_lose_no_files.__wrapped__()
            next(generator)
            with self.assertRaises(StopIteration):
                next(generator)
            inventory.assert_not_called()

        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(conftest.is_nested_probe())

    def test_the_probe_marks_itself_as_a_nested_session(self):
        """Without the flag the probe mutates the live repo's task marker.

        `tests/conftest.py`'s session hooks rename `doc/harness/tasks/.active`
        away for the session's duration and unlink whatever `.active` they find
        before restoring, so a nested run in the real tree can destroy a marker
        an unlocked sibling test just created.
        """
        with mock.patch.object(
            subprocess, "run",
            return_value=subprocess.CompletedProcess([], 0, "", ""),
        ) as run:
            red_claim_violation("tests/test_x.py::C::test_y")

        self.assertEqual(
            run.call_args.kwargs["env"].get("HARNESS_NESTED_PROBE"), "1",
        )

    def test_a_probe_side_failure_is_not_blamed_on_the_note(self):
        """Exit codes 2 and 3 are the probe's problem, not the claim's.

        No real probe reaches them — instrumenting the suite shows only
        {0, 1, 4} occur — so this arm is unreachable from the outside and has
        to be driven directly. Without it, folding 2/3 back into the
        uncollectable message leaves the suite green while the REQ documents
        the distinction, which is this task's own recurring defect.
        """
        for code in (2, 3):
            with self.subTest(exit_code=code):
                with mock.patch.object(
                    subprocess, "run",
                    return_value=subprocess.CompletedProcess([], code, "", ""),
                ):
                    complaint = red_claim_violation("tests/test_x.py::C::test_y")
                self.assertIn("probe's own failure", complaint)
                self.assertNotIn("could not be collected", complaint)

    def test_a_hanging_test_is_reported_not_waited_on(self):
        """The bound is real, not just declared.

        A claim naming a hung test would otherwise hang the suite — the guard
        against unverifiable claims becoming an unverifiable hang itself. The
        timeout is injectable so this costs a second rather than two minutes.
        """
        suite = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        (suite / "tests").mkdir()
        (suite / "tests" / "test_hang.py").write_text(
            "import time\n\n\ndef test_hangs():\n    time.sleep(60)\n",
            encoding="utf-8",
        )

        complaint = red_claim_violation(
            "tests/test_hang.py::test_hangs", suite, timeout=2,
        )

        self.assertIn("did not finish within 2s", complaint)

    def test_a_claim_whose_test_passes_is_a_violation(self):
        """The whole point: a fixed red must not keep excusing itself."""
        suite = Path(self.enterContext(__import__("tempfile").TemporaryDirectory()))
        (suite / "tests").mkdir()
        (suite / "tests" / "test_probe.py").write_text(
            "def test_green():\n    assert True\n\n"
            "def test_red():\n    assert False\n",
            encoding="utf-8",
        )

        self.assertEqual(red_claim_violation("tests/test_probe.py::test_red", suite), "")
        self.assertIn(
            "passes now",
            red_claim_violation("tests/test_probe.py::test_green", suite),
        )
        self.assertIn(
            "could not be collected",
            red_claim_violation("tests/test_probe.py::test_absent", suite),
        )


if __name__ == "__main__":
    unittest.main()
