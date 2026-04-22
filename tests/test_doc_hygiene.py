"""CL-01..14, RV-01..03, AR-01..06, CL-COLD-START: doc_hygiene.py tests (AC-005..AC-010)."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")
sys.path.insert(0, SCRIPTS_DIR)

from doc_hygiene import (  # noqa: E402
    classify,
    _load_hygiene_config,
    _validate_pin_paths,
    _is_pinned,
    append_review_entry,
    KEEP, REMOVE, REVIEW,
)


def _make_repo(tmp_path):
    """Create a minimal temp dir with .git."""
    os.makedirs(os.path.join(tmp_path, ".git"), exist_ok=True)
    doc_harness = os.path.join(tmp_path, "doc", "harness")
    os.makedirs(doc_harness, exist_ok=True)
    with open(os.path.join(doc_harness, "hygiene.yaml"), "w") as f:
        f.write("enabled: true\nobserver_until_session: 14\npin_paths: []\n")
    return tmp_path


def _signals(**kwargs):
    defaults = {
        "reference_count": 0,
        "freshness": "current",
        "cited_paths_alive": 1.0,
        "superseded_by": None,
        "distilled_to": None,
        "tag_overlap": 0.0,
    }
    defaults.update(kwargs)
    return defaults


def _init_git(tmp_path):
    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=tmp_path, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=tmp_path, capture_output=True)


class TestClassificationRules(unittest.TestCase):
    """CL-01..08: classification rules."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_CL01_keep_when_reference_count_positive(self):
        sigs = _signals(reference_count=1, freshness="current")
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), KEEP)

    def test_CL02_remove_when_superseded_by_target_exists(self):
        target_dir = os.path.join(self.repo, "doc", "common")
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "target.md"), "w") as f:
            f.write("# target\n")
        sigs = _signals(superseded_by="doc/common/target.md", reference_count=0)
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REMOVE)

    def test_CL03_remove_when_distilled_to_target_exists(self):
        target_dir = os.path.join(self.repo, "doc", "harness", "patterns")
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "foo.md"), "w") as f:
            f.write("# pattern\n")
        sigs = _signals(distilled_to="doc/harness/patterns/foo.md", reference_count=0)
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REMOVE)

    def test_CL04_review_when_no_refs_and_suspect(self):
        sigs = _signals(reference_count=0, freshness="suspect")
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REVIEW)

    def test_CL05_absence_of_frontmatter_never_remove(self):
        """CL-05 hard rule: no superseded_by/distilled_to → never REMOVE."""
        sigs = _signals(reference_count=0, freshness="stale",
                        superseded_by=None, distilled_to=None)
        result = classify(sigs, self.repo, [], "doc/changes/foo.md")
        self.assertNotEqual(result, REMOVE,
            "Absence of new frontmatter must NEVER classify as REMOVE")

    def test_CL06_superseded_by_missing_target_review(self):
        sigs = _signals(superseded_by="doc/common/nonexistent.md", reference_count=0)
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REVIEW)

    def test_CL07_distilled_to_outside_doc_review(self):
        target_dir = os.path.join(self.repo, "plugin", "scripts")
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "foo.py"), "w") as f:
            f.write("# script\n")
        sigs = _signals(distilled_to="plugin/scripts/foo.py", reference_count=0)
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REVIEW)

    def test_CL08_tag_overlap_review(self):
        sigs = _signals(reference_count=0, freshness="suspect", tag_overlap=0.9)
        self.assertEqual(classify(sigs, self.repo, [], "doc/changes/foo.md"), REVIEW)


class TestPatternsExclusion(unittest.TestCase):
    """CL-09 (CI gate): patterns/ hard-exclude."""

    def test_CL09_patterns_dir_never_touched(self):
        """CL-09: doc_hygiene.py NEVER touches doc/harness/patterns/ files."""
        with tempfile.TemporaryDirectory() as tmp:
            _make_repo(tmp)
            patterns_dir = os.path.join(tmp, "doc", "harness", "patterns")
            os.makedirs(patterns_dir, exist_ok=True)
            patterns_file = os.path.join(patterns_dir, "general.md")
            with open(patterns_file, "w") as f:
                f.write("# pattern\nsuperseded_by: doc/common/x.md\n")
            target_dir = os.path.join(tmp, "doc", "common")
            os.makedirs(target_dir, exist_ok=True)
            with open(os.path.join(target_dir, "x.md"), "w") as f:
                f.write("# x\n")

            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS_DIR, "doc_hygiene.py"),
                 "--dry-run", "--repo-root", tmp],
                capture_output=True, text=True, timeout=15,
            )
            combined = result.stdout + result.stderr
            self.assertTrue(os.path.isfile(patterns_file),
                "doc/harness/patterns/ file must not be touched")
            # patterns/general.md must not appear as a REMOVE candidate
            self.assertNotIn("patterns/general.md\nDRY-RUN REMOVE", combined)


class TestPinPaths(unittest.TestCase):
    """CL-10, CL-11: pin_paths and malformed yaml."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_CL10_pin_paths_keeps_doc(self):
        target_dir = os.path.join(self.repo, "doc", "common")
        os.makedirs(target_dir, exist_ok=True)
        with open(os.path.join(target_dir, "target.md"), "w") as f:
            f.write("# target\n")
        sigs = _signals(superseded_by="doc/common/target.md", reference_count=0)
        result = classify(sigs, self.repo, ["doc/changes/pinned.md"], "doc/changes/pinned.md")
        self.assertEqual(result, KEEP)

    def test_CL11_malformed_hygiene_yaml_failsafe(self):
        with open(os.path.join(self.repo, "doc", "harness", "hygiene.yaml"), "w") as f:
            f.write("enabled: [invalid yaml {{{")
        cfg = _load_hygiene_config(self.repo)
        self.assertIn("enabled", cfg)
        self.assertIn("pin_paths", cfg)
        self.assertIsInstance(cfg["pin_paths"], list)


class TestReferenceScanning(unittest.TestCase):
    """CL-12, CL-13: reference scanning."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_CL12_active_task_plan_md_in_corpus(self):
        from doc_hygiene import _gather_search_corpus
        task_dir = os.path.join(self.repo, "doc", "harness", "tasks", "TASK__test")
        os.makedirs(task_dir)
        with open(os.path.join(task_dir, "PLAN.md"), "w") as f:
            f.write("# Plan\nSee doc/changes/foo.md for details.\n")
        corpus = _gather_search_corpus(self.repo)
        plan_paths = [rel for _abs, rel in corpus if "PLAN.md" in rel and "TASK__test" in rel]
        self.assertTrue(plan_paths, "Active task PLAN.md should be in corpus")

    def test_CL13_token_grep_inside_fenced_code(self):
        from doc_hygiene import _count_references
        ref_doc = os.path.join(self.repo, "CLAUDE.md")
        with open(ref_doc, "w") as f:
            f.write("# Claude\n```\nsee doc/changes/important.md\n```\n")
        corpus = [(ref_doc, "CLAUDE.md")]
        target_abs = os.path.join(self.repo, "doc", "changes", "important.md")
        count = _count_references("doc/changes/important.md", corpus, target_abs)
        self.assertGreaterEqual(count, 1, "Token-grep must find refs inside fenced code blocks")


class TestColdStart(unittest.TestCase):
    """CL-COLD-START: existing docs never classify as REMOVE without frontmatter signals."""

    def test_CL_COLD_START_existing_docs_no_remove(self):
        for subdir in ("changes", "common"):
            d = os.path.join(REPO_ROOT, "doc", subdir)
            if not os.path.isdir(d):
                continue
            for root, _dirs, files in os.walk(d):
                if "_archive" in root:
                    continue
                for fn in files:
                    if not fn.endswith(".md"):
                        continue
                    rel = os.path.relpath(os.path.join(root, fn), REPO_ROOT)
                    sigs = _signals(reference_count=0, freshness="current",
                                    superseded_by=None, distilled_to=None)
                    with self.subTest(path=rel):
                        result = classify(sigs, REPO_ROOT, [], rel)
                        self.assertNotEqual(result, REMOVE,
                            f"Cold-start doc {fn} must not classify as REMOVE")


class TestReviewQueue(unittest.TestCase):
    """RV-01..03: REVIEW queue."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.repo = _make_repo(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_RV01_entry_appended(self):
        sigs = _signals(reference_count=0, freshness="suspect")
        append_review_entry("doc/changes/foo.md", sigs, self.repo)
        pending_path = os.path.join(self.repo, "doc", "harness", ".maintain-pending.json")
        self.assertTrue(os.path.isfile(pending_path))
        data = json.loads(open(pending_path).read())
        self.assertIsInstance(data, list)
        self.assertEqual(len(data), 1)
        entry = data[0]
        self.assertEqual(entry["path"], "doc/changes/foo.md")
        self.assertEqual(entry["kind"], "review")
        self.assertIn("signals", entry)
        self.assertIn("added_at", entry)

    def test_RV02_deduplicates(self):
        sigs = _signals(reference_count=0, freshness="suspect")
        append_review_entry("doc/changes/foo.md", sigs, self.repo)
        append_review_entry("doc/changes/foo.md", sigs, self.repo)
        pending_path = os.path.join(self.repo, "doc", "harness", ".maintain-pending.json")
        data = json.loads(open(pending_path).read())
        paths = [e["path"] for e in data]
        self.assertEqual(paths.count("doc/changes/foo.md"), 1)

    def test_RV03_atomic_write(self):
        sigs = _signals()
        for i in range(5):
            append_review_entry(f"doc/changes/foo{i}.md", sigs, self.repo)
        pending_path = os.path.join(self.repo, "doc", "harness", ".maintain-pending.json")
        data = json.loads(open(pending_path).read())
        self.assertIsInstance(data, list)


class TestArchive(unittest.TestCase):
    """AR-01..06: archive tests."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        _make_repo(self.tmp)
        _init_git(self.tmp)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_AR01_remove_moves_to_archive(self):
        from doc_hygiene import archive_file
        doc_changes = os.path.join(self.tmp, "doc", "changes")
        os.makedirs(doc_changes, exist_ok=True)
        foo = os.path.join(doc_changes, "foo.md")
        with open(foo, "w") as f:
            f.write("# foo\n")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmp, capture_output=True)
        ok = archive_file(foo, "doc/changes/foo.md", self.tmp)
        self.assertTrue(ok)
        self.assertFalse(os.path.isfile(foo))
        self.assertTrue(os.path.isfile(os.path.join(doc_changes, "_archive", "foo.md")))

    def test_AR02_idempotent_last_run(self):
        from hygiene_scan import _should_skip_today, _touch_last_run
        doc_harness = os.path.join(self.tmp, "doc", "harness")
        os.makedirs(doc_harness, exist_ok=True)
        _touch_last_run(self.tmp)
        self.assertTrue(_should_skip_today(self.tmp))

    def test_AR03_collision_gets_suffix(self):
        from doc_hygiene import archive_file
        doc_changes = os.path.join(self.tmp, "doc", "changes")
        archive_dir = os.path.join(doc_changes, "_archive")
        os.makedirs(archive_dir, exist_ok=True)
        with open(os.path.join(archive_dir, "foo.md"), "w") as f:
            f.write("# already archived\n")
        foo = os.path.join(doc_changes, "foo.md")
        with open(foo, "w") as f:
            f.write("# foo v2\n")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmp, capture_output=True)
        ok = archive_file(foo, "doc/changes/foo.md", self.tmp)
        self.assertTrue(ok)
        self.assertFalse(os.path.isfile(foo))
        archived = [f for f in os.listdir(archive_dir) if f.startswith("foo.archived-")]
        self.assertTrue(archived, "Collision should produce timestamp-suffixed file")

    def test_AR04_path_traversal_refused(self):
        from doc_hygiene import archive_file
        outside = os.path.join(self.tmp, "outside.md")
        with open(outside, "w") as f:
            f.write("# outside\n")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmp, capture_output=True)
        result = archive_file(outside, "_archive/outside.md", self.tmp)
        self.assertFalse(result)

    def test_AR05_dirty_file_skipped(self):
        from doc_hygiene import archive_file
        doc_changes = os.path.join(self.tmp, "doc", "changes")
        os.makedirs(doc_changes, exist_ok=True)
        foo = os.path.join(doc_changes, "foo.md")
        with open(foo, "w") as f:
            f.write("# foo\n")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmp, capture_output=True)
        with open(foo, "w") as f:
            f.write("# foo MODIFIED\n")
        ok = archive_file(foo, "doc/changes/foo.md", self.tmp)
        self.assertFalse(ok)
        self.assertTrue(os.path.isfile(foo))

    def test_AR06_archive_dir_recursion_guard(self):
        from doc_hygiene import archive_file
        doc_changes = os.path.join(self.tmp, "doc", "changes")
        archive_dir = os.path.join(doc_changes, "_archive")
        os.makedirs(archive_dir, exist_ok=True)
        fake = os.path.join(archive_dir, "foo.md")
        with open(fake, "w") as f:
            f.write("# already archived\n")
        subprocess.run(["git", "add", "."], cwd=self.tmp, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.tmp, capture_output=True)
        result = archive_file(fake, "doc/changes/_archive/foo.md", self.tmp)
        self.assertFalse(result)


class TestPinPathsValidation(unittest.TestCase):
    """AC-010: pin_paths validation."""

    def test_rejects_absolute(self):
        self.assertEqual(_validate_pin_paths(["/absolute/path.md"], REPO_ROOT), [])

    def test_rejects_traversal(self):
        self.assertEqual(_validate_pin_paths(["doc/../etc/passwd"], REPO_ROOT), [])

    def test_rejects_non_doc(self):
        self.assertEqual(_validate_pin_paths(["plugin/scripts/foo.py"], REPO_ROOT), [])

    def test_accepts_valid(self):
        valid = _validate_pin_paths(["doc/common/CLAUDE.md", "doc/changes/2026-*.md"], REPO_ROOT)
        self.assertIn("doc/common/CLAUDE.md", valid)
        self.assertIn("doc/changes/2026-*.md", valid)


if __name__ == "__main__":
    unittest.main()
