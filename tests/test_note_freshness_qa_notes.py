"""`qa_notes` entries declare the source their claims depend on.

`doc/harness/qa/QA_KNOWLEDGE.yaml` accumulates claims about this repo's source
and nothing re-checks them when that source moves. On 2026-09-18 a task added a
key to `task_verify`'s payload and thereby falsified a live note two entries
below the ones it was there to correct — in the file the task existed to clean.
QA caught it by reading. Nothing mechanical did.

Markdown notes already solve this: frontmatter declares `invalidated_by_paths`
and `note_freshness.py --paths` flips a matching note `current -> suspect`.
QA_KNOWLEDGE.yaml is a mapping document with no frontmatter, so the `doc/**/*.md`
walk went straight past it. These tests cover the reader that closes that gap.

The reader is line-oriented, not a YAML parse, because `note_freshness.py` is
stdlib-only and system python3 here has no PyYAML. That is a real risk — a
hand-rolled reader can disagree with the document it claims to read — so one
test cross-checks it against PyYAML on the real file whenever PyYAML is
importable, and the flip is checked for loadability rather than just for
plausible-looking text.

See doc/harness/REQ__qa-notes-carry-their-own-invalidation.md.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KNOWLEDGE = ROOT / "doc" / "harness" / "qa" / "QA_KNOWLEDGE.yaml"
SCRIPT = ROOT / "plugin" / "scripts" / "note_freshness.py"
DEVELOP_SKILLS = (
    ROOT / "plugin" / "skills" / "develop" / "SKILL.md",
    ROOT / "plugin-codex" / "internal-skills" / "develop" / "SKILL.md",
)
# Each tree reaches the installed payload through its own root variable.
INSTALL_ROOT_VARS = ("${CLAUDE_PLUGIN_ROOT}", "${HARNESS_PLUGIN_ROOT}")

sys.path.insert(0, str(ROOT / "plugin" / "scripts"))
import note_freshness as nf  # noqa: E402

STAMP = "2026-09-18T00:00:00Z"

FIXTURE = """\
# header comment
patterns:
  something: else

qa_notes:  # a trailing comment on the section key
  depends_on_source:
    discovered: 2026-09-02
    invalidated_by_paths:
      - plugin/scripts/stop_gate.py
    notes: >
      A claim about stop_gate.
  depends_on_a_directory:
    discovered: 2026-09-02
    invalidated_by_paths:
      - tests/
    notes: >
      A claim about the suite.
  declares_nothing:
    discovered: 2026-09-02
    notes: >
      A claim about host state that no file can falsify.
  already_superseded:
    discovered: 2026-09-02
    superseded: 2026-09-03
    invalidated_by_paths:
      - plugin/scripts/stop_gate.py
    notes: >
      SUPERSEDED - kept as historical record.
  already_suspect:
    discovered: 2026-09-02
    freshness: suspect
    freshness_updated: 2026-09-01T00:00:00Z
    invalidated_by_paths:
      - plugin/scripts/stop_gate.py
    notes: >
      Already flagged once.
  mentions_freshness_in_its_prose:
    discovered: 2026-09-02
    invalidated_by_paths:
      - plugin/scripts/_lib.py
    notes: >
      This body says freshness: current and invalidated_by_paths: on purpose,
      at block-scalar indentation, to catch a reader that matches on text
      rather than on the field position.
  appended_without_a_date:
    invalidated_by_paths:
      - plugin/scripts/stop_gate.py
    notes: >
      An entry appended without a discovered: line, which is the shape that
      exposes a writer applying its edits in document order.

trailing_section:
  after: qa_notes
"""


def write_fixture(tmp: Path, text: str = FIXTURE) -> Path:
    path = tmp / "QA_KNOWLEDGE.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def entry_block(text: str, key: str) -> str:
    lines = text.splitlines(keepends=True)
    for name, start, end in nf._qa_note_spans(lines):
        if name == key:
            return "".join(lines[start:end])
    raise AssertionError(f"no entry {key!r}")


class QaNoteReaderTests(unittest.TestCase):
    def test_only_entries_declaring_paths_are_candidates(self):
        found = {c["key"]: c for c in nf.qa_note_candidates(FIXTURE)}
        self.assertNotIn("declares_nothing", found)
        self.assertEqual(
            found["depends_on_source"]["paths"], ["plugin/scripts/stop_gate.py"]
        )

    def test_field_shaped_prose_inside_a_note_body_is_not_read_as_a_field(self):
        # The body of this entry contains `freshness: current` and
        # `invalidated_by_paths:` as text. A reader that greps would take the
        # prose for a declaration and read the wrong paths off it.
        found = {c["key"]: c for c in nf.qa_note_candidates(FIXTURE)}
        self.assertEqual(
            found["mentions_freshness_in_its_prose"]["paths"],
            ["plugin/scripts/_lib.py"],
        )
        self.assertEqual(
            found["mentions_freshness_in_its_prose"]["freshness"], nf.FRESHNESS_CURRENT
        )

    def test_the_last_entry_stops_at_the_next_top_level_key(self):
        spans = {k: (s, e) for k, s, e in nf._qa_note_spans(FIXTURE.splitlines(True))}
        last = entry_block(FIXTURE, "appended_without_a_date")
        self.assertNotIn("trailing_section", last)
        self.assertEqual(len(spans), 7)


class QaNoteFlipTests(unittest.TestCase):
    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def test_a_matching_entry_is_flipped_and_reported(self):
        path = write_fixture(self.tmp)
        hits = nf.scan_qa_notes(str(path), {"plugin/scripts/_lib.py"}, stamp=STAMP)
        self.assertEqual(
            [h["path"].split("#")[1] for h in hits],
            ["mentions_freshness_in_its_prose"],
        )
        block = entry_block(path.read_text(), "mentions_freshness_in_its_prose")
        self.assertIn(f"    freshness: {nf.FRESHNESS_SUSPECT}\n", block)
        self.assertIn(f"    freshness_updated: {STAMP}\n", block)

    def test_the_flag_lands_under_discovered_not_inside_the_note_body(self):
        path = write_fixture(self.tmp)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        block = entry_block(path.read_text(), "depends_on_source").splitlines()
        self.assertEqual(block[1].strip(), "discovered: 2026-09-02")
        self.assertEqual(block[2].strip(), f"freshness: {nf.FRESHNESS_SUSPECT}")

    def test_each_of_several_entries_flipped_in_one_scan_gets_its_own_flag(self):
        # Inserting into one entry shifts every line below it, so the spans
        # computed before the first edit are stale for every later entry. An
        # entry with no `discovered:` line is where that goes visibly wrong:
        # the writer falls back to "directly under the key", and against a
        # shifted window that is the middle of the entry above.
        path = write_fixture(self.tmp)
        hits = nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"},
                                stamp=STAMP)
        self.assertEqual(
            sorted(h["path"].split("#")[1] for h in hits),
            ["appended_without_a_date", "depends_on_source"],
        )
        for key in ("appended_without_a_date", "depends_on_source"):
            with self.subTest(entry=key):
                block = entry_block(path.read_text(), key)
                self.assertIn(f"    freshness: {nf.FRESHNESS_SUSPECT}\n", block)
                self.assertIn(f"    freshness_updated: {STAMP}\n", block)

    def test_a_directory_prefix_matches_a_file_under_it(self):
        path = write_fixture(self.tmp)
        hits = nf.scan_qa_notes(str(path), {"tests/test_stop_gate.py"}, stamp=STAMP)
        self.assertEqual(
            [h["path"].split("#")[1] for h in hits], ["depends_on_a_directory"]
        )

    def test_a_superseded_entry_is_left_alone(self):
        path = write_fixture(self.tmp)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        block = entry_block(path.read_text(), "already_superseded")
        self.assertNotIn("freshness:", block)

    def test_an_entry_already_suspect_is_not_restamped(self):
        path = write_fixture(self.tmp)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        block = entry_block(path.read_text(), "already_suspect")
        self.assertIn("freshness_updated: 2026-09-01T00:00:00Z", block)
        self.assertNotIn(STAMP, block)

    def test_a_second_identical_scan_reports_nothing_new(self):
        path = write_fixture(self.tmp)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        settled = path.read_text()
        again = nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        self.assertEqual(again, [])
        self.assertEqual(path.read_text(), settled)

    def test_a_scan_that_matches_nothing_leaves_the_file_byte_identical(self):
        path = write_fixture(self.tmp)
        before = path.read_bytes()
        self.assertEqual(nf.scan_qa_notes(str(path), {"README.md"}), [])
        self.assertEqual(path.read_bytes(), before)

    def test_a_scan_that_matches_nothing_does_not_rewrite_the_file_at_all(self):
        # Byte-identity alone does not catch a rewrite: writing the unchanged
        # text back produces the same bytes. It still replaces the file, which
        # is a needless race with the qa-* agents that append to it.
        from unittest import mock

        path = write_fixture(self.tmp)
        with mock.patch.object(nf, "_atomic_write") as write:
            self.assertEqual(nf.scan_qa_notes(str(path), {"README.md"}), [])
        write.assert_not_called()

    def test_an_entry_declaring_nothing_is_never_rewritten(self):
        path = write_fixture(self.tmp)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        self.assertEqual(
            entry_block(path.read_text(), "declares_nothing"),
            entry_block(FIXTURE, "declares_nothing"),
        )

    def test_an_existing_freshness_field_is_rewritten_in_place(self):
        # `suspect` is not `current`, so the entry is skipped; but if a future
        # change makes such an entry eligible, the writer must replace the line
        # rather than add a second `freshness:` key to the same mapping.
        text = FIXTURE.replace(
            "    freshness: suspect\n", "    freshness: current\n", 1
        )
        path = write_fixture(self.tmp, text)
        nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py"}, stamp=STAMP)
        block = entry_block(path.read_text(), "already_suspect")
        # The value, not just the key count: dropping the assignment while
        # leaving `wrote_freshness = True` reports the entry as flipped and
        # ships it still reading `current`. Two entries in the real file carry
        # an explicit `freshness:` line, so this is live behaviour.
        self.assertIn(f"    freshness: {nf.FRESHNESS_SUSPECT}\n", block)
        self.assertEqual(block.count("freshness:"), 1)
        self.assertEqual(block.count("freshness_updated:"), 1)
        self.assertIn(f"freshness_updated: {STAMP}", block)

    def test_the_flipped_document_still_loads(self):
        # The failure this file has actually suffered is a malformed write
        # taking every accumulated section out at once, not a wrong value in
        # one entry. See tests/test_qa_knowledge_shape.py.
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not importable")
        path = write_fixture(self.tmp)
        hits = nf.scan_qa_notes(str(path), {"plugin/scripts/stop_gate.py", "tests/"},
                                stamp=STAMP)
        self.assertEqual(len(hits), 3)
        before = yaml.safe_load(FIXTURE)
        loaded = yaml.safe_load(path.read_text())
        self.assertEqual(loaded["trailing_section"], {"after": "qa_notes"})
        self.assertEqual(set(loaded["qa_notes"]), set(before["qa_notes"]))

        # Every flipped entry must carry the flag, and no entry may lose or
        # gain anything else. Inserting into one entry shifts the line numbers
        # of every entry below it, so a writer that applies its edits in
        # document order corrupts the second one — plausibly still loadable,
        # which is exactly why this checks each entry rather than the parse.
        flipped = {h["path"].split("#")[1] for h in hits}
        self.assertEqual(
            flipped,
            {"depends_on_source", "depends_on_a_directory", "appended_without_a_date"},
        )
        for key, entry in loaded["qa_notes"].items():
            with self.subTest(entry=key):
                expected = dict(before["qa_notes"][key])
                if key in flipped:
                    expected["freshness"] = nf.FRESHNESS_SUSPECT
                    # PyYAML resolves the timestamp to a datetime, so compare
                    # against the same resolution rather than the raw text.
                    expected["freshness_updated"] = yaml.safe_load(STAMP)
                self.assertEqual(entry, expected)


class MarkdownEvidencePreservationTests(unittest.TestCase):
    NOTE = "---\nfreshness: current\ninvalidated_by_paths:\n  - src/example.py\n---\nEvidence.\n"
    OPERATIONAL = ("archive", "tasks", "runtime", "checkpoints")

    def setUp(self):
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    def note(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(self.NOTE, encoding="utf-8")
        return path

    def scan_without_entering(self, doc_root, protected):
        import builtins
        from unittest import mock

        actual_open, actual_scandir = builtins.open, os.scandir
        opened, descended = [], []

        def protected_path(path):
            if isinstance(path, int):
                return False
            candidate = Path(path).resolve()
            return any(candidate == root.resolve() or root.resolve() in candidate.parents
                       for root in protected)

        def open_note(path, *args, **kwargs):
            self.assertFalse(protected_path(path), f"opened operational evidence: {path}")
            opened.append(Path(path))
            return actual_open(path, *args, **kwargs)

        def descend(path):
            self.assertFalse(protected_path(path), f"descended into operational evidence: {path}")
            descended.append(Path(path))
            return actual_scandir(path)

        with mock.patch("builtins.open", side_effect=open_note), mock.patch.object(
            nf.os, "scandir", side_effect=descend
        ):
            hits = nf.scan(str(doc_root), {"src/example.py"})
        return hits, opened, descended

    def test_operational_evidence_is_pruned_before_descent_and_open(self):
        doc = self.tmp / "doc"
        protected = [doc / "harness" / name for name in self.OPERATIONAL]
        evidence = [self.note(root / "nested" / "note.md") for root in protected]
        before = {path: path.read_bytes() for path in evidence}
        current = self.note(doc / "design" / "note.md")
        alias = doc / "evidence-alias.md"
        alias.symlink_to(evidence[0])
        hits, opened, descended = self.scan_without_entering(doc, protected)
        self.assertEqual([Path(hit["path"]) for hit in hits], [current])
        self.assertIn(current, opened)
        self.assertIn(doc, descended)
        self.assertIn("freshness: suspect", current.read_text())
        self.assertTrue(alias.is_symlink())
        self.assertEqual({path: path.read_bytes() for path in evidence}, before)

    def test_starting_inside_operational_evidence_never_enters_it(self):
        for name in self.OPERATIONAL:
            with self.subTest(name=name):
                root = self.tmp / "doc" / "harness" / name
                evidence = self.note(root / "nested" / "note.md")
                before = evidence.read_bytes()
                for start in (root, root / "nested"):
                    hits, opened, descended = self.scan_without_entering(start, [root])
                    self.assertEqual((hits, opened, descended), ([], [], []))
                self.assertEqual(evidence.read_bytes(), before)

    def test_explicit_doc_root_keeps_ordinary_same_basename_notes_live(self):
        doc = self.tmp / "manuals"
        durable = [self.note(doc / "guide" / name / "note.md") for name in self.OPERATIONAL]
        durable += [self.note(doc / "harness" / name / "note.md") for name in self.OPERATIONAL]
        durable.append(self.note(doc / "guide" / "harness" / "archive" / "note.md"))
        hits, _, _ = self.scan_without_entering(doc, [])
        self.assertEqual({Path(hit["path"]) for hit in hits}, set(durable))
        for path in durable:
            self.assertIn("freshness: suspect", path.read_text())

    def test_scan_from_repo_root_prunes_nested_conventional_evidence(self):
        root = self.tmp / "repo"
        protected = [root / "doc" / "harness" / name for name in self.OPERATIONAL]
        evidence = [self.note(path / "note.md") for path in protected]
        before = {path: path.read_bytes() for path in evidence}
        current = self.note(root / "doc" / "guide" / "note.md")
        hits, _, _ = self.scan_without_entering(root, protected)
        self.assertEqual([Path(hit["path"]) for hit in hits], [current])
        self.assertEqual({path: path.read_bytes() for path in evidence}, before)

    def test_scanning_ordinary_guide_does_not_redefine_operational_roots(self):
        guide = self.tmp / "doc" / "guide"
        current = self.note(guide / "harness" / "archive" / "note.md")
        hits, _, _ = self.scan_without_entering(guide, [])
        self.assertEqual([Path(hit["path"]) for hit in hits], [current])
        self.assertIn("freshness: suspect", current.read_text())

    def test_symlink_backed_operational_root_and_file_alias_are_never_opened(self):
        doc = self.tmp / "doc"
        storage = self.tmp / "stored-evidence"
        evidence = self.note(storage / "nested" / "note.md")
        before = evidence.read_bytes()
        archive = doc / "harness" / "archive"
        archive.parent.mkdir(parents=True)
        archive.symlink_to(storage, target_is_directory=True)
        alias = doc / "evidence-alias.md"
        alias.symlink_to(evidence)
        manuals = self.tmp / "manuals"
        manuals.symlink_to(storage, target_is_directory=True)
        for start in (archive, archive / "nested", doc, self.tmp, manuals):
            hits, opened, _ = self.scan_without_entering(start, [storage])
            self.assertEqual((hits, opened), ([], []))
        self.assertEqual(evidence.read_bytes(), before)
        self.assertTrue(alias.is_symlink())

    def test_archived_snapshot_qa_knowledge_is_not_opened_or_rewritten_by_cli(self):
        from unittest import mock

        repo = self.tmp / "repo"
        (repo / "doc" / "harness").mkdir(parents=True)
        (repo / "doc" / "harness" / "manifest.yaml").write_text("project: fixture\n")
        snapshot_doc = repo / "doc" / "harness" / "archive" / "snapshot" / "source" / "doc"
        qa = snapshot_doc / "harness" / "qa"
        qa.mkdir(parents=True)
        knowledge = write_fixture(qa)
        before = knowledge.read_bytes()
        with mock.patch("builtins.open", side_effect=AssertionError("opened archived QA knowledge")):
            self.assertEqual(nf.scan_qa_notes(str(knowledge), {"plugin/scripts/stop_gate.py"}), [])
        proc = subprocess.run(
            [sys.executable, str(SCRIPT), "--doc-root", str(snapshot_doc),
             "--paths", "plugin/scripts/stop_gate.py"],
            cwd=repo, capture_output=True, text=True, timeout=10, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("0 notes invalidated", proc.stdout)
        self.assertEqual(knowledge.read_bytes(), before)

    def test_cli_repo_context_protects_external_archive_storage_and_alias(self):
        import tempfile

        external = tempfile.TemporaryDirectory()
        self.addCleanup(external.cleanup)
        storage = Path(external.name)
        evidence = self.note(storage / "note.md")
        before = evidence.read_bytes()
        harness = self.tmp / "doc" / "harness"
        harness.mkdir(parents=True)
        (harness / "manifest.yaml").write_text("project: fixture\n")
        (harness / "archive").symlink_to(storage, target_is_directory=True)
        alias = self.tmp / "manuals"
        alias.symlink_to(storage, target_is_directory=True)
        for start in (alias, storage):
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--doc-root", str(start), "--paths", "src/example.py"],
                cwd=self.tmp, capture_output=True, text=True, timeout=10, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("0 notes invalidated", proc.stdout)
            self.assertEqual(evidence.read_bytes(), before)


class CliWiringTests(unittest.TestCase):
    """main() must reach the qa_notes scan, not merely export it."""

    def test_the_cli_reports_a_qa_note_hit(self):
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            (repo / "doc" / "harness" / "qa").mkdir(parents=True)
            (repo / "doc" / "harness" / "manifest.yaml").write_text(
                "project: fixture\n", encoding="utf-8"
            )
            write_fixture(repo / "doc" / "harness" / "qa")
            proc = subprocess.run(
                [sys.executable, str(SCRIPT), "--paths", "plugin/scripts/stop_gate.py"],
                cwd=str(repo), capture_output=True, text=True, check=False,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("QA_KNOWLEDGE.yaml#depends_on_source", proc.stdout)
            self.assertIn("2 note(s) flipped", proc.stdout)


class RealFileTests(unittest.TestCase):
    """The seeds in the shipped file must stay falsifiable."""

    def setUp(self):
        self.text = KNOWLEDGE.read_text(encoding="utf-8")
        self.candidates = nf.qa_note_candidates(self.text)

    def test_the_shipped_file_actually_declares_dependencies(self):
        # Without this, the path-existence test below passes vacuously the
        # moment someone deletes every declaration.
        self.assertGreaterEqual(sum(not c["superseded"] for c in self.candidates), 15)

    def test_every_declared_path_exists(self):
        # This is the whole falsifiability contract: a declaration naming a
        # deleted file silently stops protecting its note. Seeding found one on
        # the first run — mcp_bash_guard_false_positive named a script removed
        # in 5d29f55, and the note had been advising a workaround for a guard
        # that no longer existed.
        missing = sorted(
            f"{c['key']} -> {p}"
            for c in self.candidates
            if not c["superseded"]
            for p in c["paths"]
            if not (ROOT / p).exists()
        )
        self.assertEqual(missing, [], "declared path no longer exists")

    def test_the_line_reader_agrees_with_a_real_yaml_parser(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML not importable")
        parsed = yaml.safe_load(self.text)["qa_notes"]
        expected = {
            k: v["invalidated_by_paths"]
            for k, v in parsed.items()
            if v.get("invalidated_by_paths")
        }
        self.assertEqual({c["key"]: c["paths"] for c in self.candidates}, expected)


class DevelopSkillTests(unittest.TestCase):
    """A mechanism nothing runs is not a mechanism."""

    def test_both_develop_skills_name_the_command(self):
        # Pinned per tree, with the install-root variable spelled out. The
        # first draft wrote the repo-relative `plugin/scripts/...`, which
        # exists in no project the harness is installed into — and the codex
        # tree ships no scripts/ directory at all. The obvious relaxation
        # (drop the prefix, pin `/scripts/note_freshness.py --paths`) is a
        # substring of that broken form, so it would have readmitted exactly
        # the defect it was written to catch.
        for skill, root in zip(DEVELOP_SKILLS, INSTALL_ROOT_VARS, strict=True):
            with self.subTest(tree=skill.parent.parent.parent.name):
                body = skill.read_text(encoding="utf-8")
                self.assertIn(f"{root}/scripts/note_freshness.py --paths", body)
                self.assertIn("qa_notes", body)


if __name__ == "__main__":
    unittest.main()
