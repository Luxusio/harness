"""AC-001 + AC-002 — doc_hygiene.archive_file no longer calls git commit; all
git subprocess.run calls have a timeout kwarg.

Run: python3 -m unittest tests.regression.task_hygiene_defer_commit.test_ac_001__no_auto_commit
"""
from __future__ import annotations

import ast
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
DOC_HYGIENE = REPO / "plugin" / "scripts" / "doc_hygiene.py"
sys.path.insert(0, str(REPO / "plugin" / "scripts"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestNoAutoCommit(unittest.TestCase):
    """AC-001: archive_file must NOT invoke git commit.

    Uses a real temporary git repo to exercise archive_file end-to-end. After
    the call, the repo's HEAD must be unchanged (no new commit) but the
    archive move must be staged in the index.
    """

    def _init_repo(self, root: Path) -> str:
        subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=root, check=True, timeout=5)
        subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True, timeout=5)
        seed = root / "seed.md"
        seed.write_text("# seed\n", encoding="utf-8")
        subprocess.run(["git", "add", "seed.md"], cwd=root, check=True, timeout=5)
        subprocess.run(["git", "commit", "-q", "-m", "seed"], cwd=root, check=True, timeout=10)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5
        ).stdout.strip()
        return head

    def test_archive_leaves_history_unchanged_and_stages_rename(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            head_before = self._init_repo(root)

            doc_dir = root / "doc" / "common"
            doc_dir.mkdir(parents=True)
            target = doc_dir / "old.md"
            target.write_text("# old\n", encoding="utf-8")
            subprocess.run(["git", "add", str(target)], cwd=root, check=True, timeout=5)
            subprocess.run(
                ["git", "commit", "-q", "-m", "add old"], cwd=root, check=True, timeout=10
            )
            head_after_seed = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5
            ).stdout.strip()

            dh = _load("doc_hygiene", DOC_HYGIENE)
            ok = dh.archive_file(
                str(target), os.path.relpath(str(target), str(root)), str(root)
            )
            self.assertTrue(ok, "archive_file should report success")

            head_after_archive = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=root, capture_output=True, text=True, timeout=5
            ).stdout.strip()
            self.assertEqual(
                head_after_archive,
                head_after_seed,
                "archive_file must NOT create a new commit (was: %s, now: %s)"
                % (head_after_seed, head_after_archive),
            )

            porcelain = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=root, capture_output=True, text=True, timeout=5,
            ).stdout
            self.assertTrue(
                any(line.startswith("R") and "_archive/" in line for line in porcelain.splitlines()),
                "archive_file should leave a staged rename pointing into _archive/. Got: %r"
                % porcelain,
            )


class TestTimeoutKwargs(unittest.TestCase):
    """AC-002: every subprocess.run(["git", ...], ...) in doc_hygiene.py
    must pass a `timeout=` keyword argument.
    """

    def test_all_git_subprocess_calls_have_timeout(self):
        tree = ast.parse(DOC_HYGIENE.read_text(encoding="utf-8"))
        offenders: list[int] = []
        git_calls = 0
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if not (isinstance(func, ast.Attribute) and func.attr == "run"):
                continue
            if not (
                isinstance(func.value, ast.Name) and func.value.id == "subprocess"
            ):
                continue
            if not node.args:
                continue
            first = node.args[0]
            if isinstance(first, ast.List) and first.elts:
                head = first.elts[0]
                if isinstance(head, ast.Constant) and head.value == "git":
                    git_calls += 1
                    has_timeout = any(kw.arg == "timeout" for kw in node.keywords)
                    if not has_timeout:
                        offenders.append(node.lineno)

        self.assertGreater(git_calls, 0, "expected at least one git subprocess.run in doc_hygiene.py")
        self.assertEqual(
            offenders,
            [],
            "git subprocess.run without timeout kwarg at lines: %s" % offenders,
        )


if __name__ == "__main__":
    unittest.main()
