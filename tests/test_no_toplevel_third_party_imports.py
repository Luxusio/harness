"""Regression tests for P5: no top-level third-party imports in tests/.

Ensures that test_*.py files use only stdlib or project-local runtime/test
helpers in unguarded module-level imports. Optional test runners must skip
cleanly when unavailable; other optional packages are imported where needed.

The real failure pattern this guards against:
  import yaml      ← PyYAML: not stdlib, not in plugin/scripts/ → FAIL
  import _lib      ← plugin/scripts/_lib.py: project-local → OK
  import unittest  ← stdlib → OK
"""

from __future__ import annotations

import ast
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_DIR = REPO_ROOT / "plugin" / "scripts"
TESTS_DIR = REPO_ROOT / "tests"
sys.path.insert(0, str(SCRIPT_DIR))
os.environ["HARNESS_SKIP_STDIN"] = "1"

def _local_script_modules() -> set[str]:
    """Return set of module names available as .py files in plugin/scripts/.

    These are project-local harness modules (_lib, task_completed_gate, etc.)
    and must not be flagged as third-party even though they are not stdlib.
    """
    local = set()
    for p in SCRIPT_DIR.glob("*.py"):
        local.add(p.stem)
    return local


def _allowed_toplevel_modules() -> set[str]:
    """Recognize test helpers without permitting external test dependencies."""
    test_modules = {path.stem for path in TESTS_DIR.glob("test_*.py")}
    return set(sys.stdlib_module_names) | {"conftest"} | _local_script_modules() | test_modules


def _allowed_runtime_modules() -> set[str]:
    """Recognize concrete modules from both shipped Python directories."""
    mcp_modules = {path.stem for path in (REPO_ROOT / "plugin" / "mcp").glob("*.py")}
    return set(sys.stdlib_module_names) | _local_script_modules() | mcp_modules


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


def get_disallowed_toplevel_imports(source: str) -> list[tuple[str, int]]:
    """Return top-level imports outside stdlib and project-local modules."""
    allowed = _allowed_toplevel_modules()
    return [item for item in get_toplevel_imports(source) if item[0] not in allowed]


class NoThirdPartyToplevelImportsTests(unittest.TestCase):

    def test_stdlib_conftest_and_local_script_imports_are_allowed(self):
        source = "import tomllib\nimport conftest\nimport _lib\nimport test_batch_state\n"
        self.assertEqual([], get_disallowed_toplevel_imports(source))

    def test_synthetic_third_party_import_is_rejected(self):
        for name in ("synthetic_third_party", "pytest", "yaml"):
            with self.subTest(name=name):
                self.assertEqual(
                    [(name, 1)], get_disallowed_toplevel_imports(f"import {name}\n"),
                )

    def test_pytest_modules_skip_cleanly_without_site_packages(self):
        modules = [
            "test_batch_pending.py", "test_batch_worktrees.py",
            "test_codex_registration_liveness.py", "test_device_lab_qa_contract.py",
            "test_install_permission_reporting.py", "test_installer_output.py",
            "test_verify_receipt_settlement.py", "test_wbs_input_policy.py",
            "test_wbs_measurement.py",
            "test_batch_scope_extension.py", "test_verify_runner_jobs.py",
        ]
        code = f"""
import importlib.util
import unittest
assert importlib.util.find_spec('pytest') is None
for name in {modules!r}:
    suite = unittest.TestLoader().discover('tests', pattern=name)
    result = unittest.TestResult()
    suite.run(result)
    assert result.wasSuccessful(), (name, result.errors, result.failures)
    assert len(result.skipped) == 1, (name, result.skipped)
"""
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [sys.executable, "-S", "-c", code], cwd=REPO_ROOT, env=env,
            capture_output=True, text=True, timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_no_third_party_toplevel_imports_in_test_files(self):
        """Unguarded top-level imports must be stdlib or project-local helpers.

        This guards against accidental import yaml / import pyyaml / import requests
        at module level, which breaks in minimal environments that lack those packages.
        """
        this_file = Path(__file__).resolve()
        test_files = sorted(TESTS_DIR.glob("test_*.py"))
        # Exclude this file itself from its own scan
        test_files = [f for f in test_files if f.resolve() != this_file]

        violations: list[str] = []
        for test_file in test_files:
            try:
                source = test_file.read_text(encoding="utf-8")
            except OSError as exc:
                violations.append(f"{test_file.name}: cannot read file: {exc}")
                continue

            try:
                imports = get_disallowed_toplevel_imports(source)
            except SyntaxError as exc:
                violations.append(f"{test_file.name}: syntax error: {exc}")
                continue

            for module_name, lineno in imports:
                violations.append(
                    f"{test_file.name}:{lineno}: third-party import '{module_name}'"
                )

        if violations:
            self.fail(
                "Found top-level third-party imports in test files:\n"
                + "\n".join(f"  {v}" for v in violations)
                + "\n\nFix: import optional packages inside the test method, "
                "or explicitly skip a runner-dependent module when its runner is unavailable."
            )


def _literal_dynamic_import(node: ast.AST) -> str | None:
    """The module named by `import_module("x")` / `__import__("x")`, if literal."""
    if not isinstance(node, ast.Call) or not node.args:
        return None
    func = node.func
    called = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
    first = node.args[0]
    if called in {"import_module", "__import__"} and isinstance(first, ast.Constant) \
            and isinstance(first.value, str) and not first.value.startswith("."):
        return first.value
    return None


class ShippedRuntimeIsStdlibOnlyTests(unittest.TestCase):
    """The installed payload runs on the user's bare python3.

    Since 2026-09-26 PyYAML is a dev dependency, so a stray `import yaml` in the
    runtime would import cleanly in every synced venv and only fail for users.
    This scans every import, not just top-level ones, since a lazy import in a
    hook fails the same way. `importlib.import_module("x")` and
    `__import__("x")` count too when the name is a positional string literal; a computed
    name cannot be resolved statically and is not checked.
    """

    def test_runtime_imports_only_stdlib_or_project_modules(self):
        runtime_files = [
            *sorted(SCRIPT_DIR.glob("*.py")),
            *sorted((REPO_ROOT / "plugin" / "mcp").glob("*.py")),
            REPO_ROOT / "install.py",
        ]
        allowed = _allowed_runtime_modules()
        violations = []
        for path in runtime_files:
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    names = [node.module]
                elif (name := _literal_dynamic_import(node)) is not None:
                    names = [name]
                else:
                    continue
                for name in names:
                    if name.split(".")[0] not in allowed:
                        violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}: {name}")
        self.assertEqual(violations, [], "runtime imports a non-stdlib module")

    def test_runtime_allowlist_includes_shipped_mcp_but_rejects_external_modules(self):
        allowed = _allowed_runtime_modules()
        self.assertIn("harness_server", allowed)
        self.assertIn("_lib", allowed)
        self.assertNotIn("synthetic_third_party", allowed)
        self.assertNotIn("yaml", allowed)
        self.assertNotIn("test_batch_state", allowed)

    def test_literal_dynamic_imports_are_resolved(self):
        cases = {
            'importlib.import_module("yaml")': "yaml",
            '__import__("requests.adapters")': "requests.adapters",
            'import_module("json")': "json",
            'importlib.import_module(name)': None,
            'importlib.import_module(".sibling", "pkg")': None,
            'len("yaml")': None,
        }
        for source, expected in cases.items():
            call = ast.parse(source, mode="eval").body
            self.assertEqual(_literal_dynamic_import(call), expected, source)


if __name__ == "__main__":
    unittest.main()
