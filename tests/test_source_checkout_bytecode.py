"""A `.pyc` written by another CPython build cannot break the source checkout's suite.

Two builds sharing a cache tag (measured 2026-09-26: /usr/bin/python3 3.12.3 and
the mise/.venv 3.12.14; 2026-09-09: two 3.12.13 builds) write `cpython-312.pyc`
files with identical headers and unequal code. After a script ran under the
other build, `pytest tests/test__lib.py` failed at collection with
`StaleBytecodeCacheError` (16 errors) until `plugin/scripts/__pycache__` was
deleted by hand. `tests/conftest.py` now prunes checkout bytecode before
collection and writes none itself.

The poisoned cache is built in a tmp copy, never in the real `plugin/scripts`
(see tests/test_bytecode_prevention.py for why).
"""
from __future__ import annotations

import importlib.util
import marshal
import os
import shutil
import struct
import subprocess
import sys
from pathlib import Path

from conftest import REPO_ROOT

IMPORT_TEST = (
    "import os, sys\n"
    "sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'plugin', 'scripts'))\n"
    "def test_import_lifecycle():\n"
    "    import _lib  # noqa: F401\n"
)
PRUNE_CALL = '    _prune_checkout_bytecode(os.path.join(REPO_ROOT, "plugin"))\n'


def _stamp_foreign_build_cache(py_path: Path) -> Path:
    """Write the on-disk shape another build leaves: header-valid, unequal code."""
    cache_dir = py_path.parent / "__pycache__"
    cache_dir.mkdir(exist_ok=True)
    pyc = cache_dir / f"{py_path.stem}.{sys.implementation.cache_tag}.pyc"
    divergent = compile(
        py_path.read_text(encoding="utf-8") + "\n_OTHER_BUILD = True\n", str(py_path), "exec",
    )
    stat = py_path.stat()
    pyc.write_bytes(
        importlib.util.MAGIC_NUMBER
        + struct.pack("<I", 0)
        + struct.pack("<II", int(stat.st_mtime) & 0xFFFFFFFF, stat.st_size & 0xFFFFFFFF)
        + marshal.dumps(divergent)
    )
    return pyc


def _checkout_copy(root: Path, *, prune: bool) -> Path:
    from test_install_writable_source_comparison import _load_install_module
    _load_install_module()._copytree_clean(
        Path(REPO_ROOT) / "plugin" / "scripts", root / "plugin" / "scripts",
    )
    conftest = (Path(REPO_ROOT) / "tests" / "conftest.py").read_text(encoding="utf-8")
    assert PRUNE_CALL in conftest
    if not prune:
        conftest = conftest.replace(PRUNE_CALL, "    pass\n")
    (root / "tests").mkdir()
    (root / "tests" / "conftest.py").write_text(conftest, encoding="utf-8")
    (root / "tests" / "test_import.py").write_text(IMPORT_TEST, encoding="utf-8")
    _stamp_foreign_build_cache(root / "plugin" / "scripts" / "_lib.py")
    return root


def _run_suite(root: Path) -> subprocess.CompletedProcess:
    # PYTHONDONTWRITEBYTECODE is dropped too, so the copied conftest has to
    # set it itself for the post-run "no __pycache__" check to hold.
    env = {key: value for key, value in os.environ.items()
           if key not in {"HARNESS_NESTED_PROBE", "PYTEST_ADDOPTS", "PYTEST_CURRENT_TEST",
                          "PYTHONDONTWRITEBYTECODE"}}
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-o", "addopts=", "-p", "no:cacheprovider",
         "tests/test_import.py"],
        cwd=root, env=env, capture_output=True, text=True, timeout=300,
    )


def test_a_foreign_build_cache_in_the_checkout_is_pruned_before_collection(tmp_path):
    root = _checkout_copy(tmp_path / "checkout", prune=True)
    result = _run_suite(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list((root / "plugin").rglob("__pycache__")) == []




def test_the_suite_writes_no_bytecode_into_the_checkout():
    assert sys.dont_write_bytecode is True
    assert os.environ.get("PYTHONDONTWRITEBYTECODE") == "1"
