"""Regression tests for P5: no top-level third-party imports in tests/.

Ensures that all test_*.py files in tests/ use only stdlib modules or
project-local harness scripts (plugin/scripts/) at the module level.

The real failure pattern this guards against:
  import yaml      ← PyYAML: not stdlib, not in plugin/scripts/ → FAIL
  import _lib      ← plugin/scripts/_lib.py: project-local → OK
  import unittest  ← stdlib → OK
"""

from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
TESTS_DIR = REPO_ROOT / "tests"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"

# Allowed top-level module names (stdlib only).
STDLIB_MODULES = {
    "os", "sys", "re", "json", "ast", "tempfile", "textwrap",
    "unittest", "pathlib", "subprocess", "io", "hashlib", "datetime",
    "collections", "itertools", "functools", "contextlib", "shutil",
    "copy", "types", "typing", "abc", "inspect", "time", "struct",
    "threading", "socket", "http", "urllib", "email", "html", "xml",
    "csv", "dataclasses", "enum", "warnings", "logging", "traceback",
    "gc", "weakref", "importlib", "uuid", "random", "math", "string",
    "operator", "numbers", "decimal", "fractions", "statistics",
    "array", "queue", "heapq", "bisect", "pprint", "reprlib",
    "difflib", "fnmatch", "glob", "stat", "zipfile",
    "tarfile", "gzip", "bz2", "lzma", "zlib", "base64", "binascii",
    "codecs", "unicodedata", "locale", "gettext", "argparse",
    "configparser", "tomllib", "netrc", "plistlib",
    "signal", "mmap", "ctypes", "platform", "builtins",
    "__future__",
}


def _local_script_modules() -> set[str]:
    """Return set of module names available as .py files in plugin/scripts/.

    These are project-local harness modules (_lib, task_completed_gate, etc.)
    and must not be flagged as third-party even though they are not stdlib.
    """
    local = set()
    for p in SCRIPT_DIR.glob("*.py"):
        local.add(p.stem)
    return local


def get_toplevel_imports(source: str) -> list[tuple[str, int]]:
    """Return list of (module_name, lineno) for top-level imports."""
    tree = ast.parse(source)
    results = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                results.append((top, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # not a relative import
                top = node.module.split(".")[0]
                results.append((top, node.lineno))
    return results


class NoThirdPartyToplevelImportsTests(unittest.TestCase):

    def test_no_third_party_toplevel_imports_in_test_files(self):
        """All test_*.py files in tests/ must only use stdlib or project-local
        harness scripts (plugin/scripts/) at the top level.

        This guards against accidental import yaml / import pyyaml / import requests
        at module level, which breaks in minimal environments that lack those packages.
        """
        this_file = Path(__file__).resolve()
        test_files = sorted(TESTS_DIR.glob("test_*.py"))
        # Exclude this file itself from its own scan
        test_files = [f for f in test_files if f.resolve() != this_file]

        local_modules = _local_script_modules()
        allowed = STDLIB_MODULES | local_modules

        violations: list[str] = []
        for test_file in test_files:
            try:
                source = test_file.read_text(encoding="utf-8")
            except OSError as exc:
                violations.append(f"{test_file.name}: cannot read file: {exc}")
                continue

            try:
                imports = get_toplevel_imports(source)
            except SyntaxError as exc:
                violations.append(f"{test_file.name}: syntax error: {exc}")
                continue

            for module_name, lineno in imports:
                if module_name not in allowed:
                    violations.append(
                        f"{test_file.name}:{lineno}: third-party import '{module_name}'"
                    )

        if violations:
            self.fail(
                "Found top-level third-party imports in test files:\n"
                + "\n".join(f"  {v}" for v in violations)
                + "\n\nFix: move the import inside the test method, "
                "or add the package to plugin/scripts/ if it is a project-local module."
            )


if __name__ == "__main__":
    unittest.main()
