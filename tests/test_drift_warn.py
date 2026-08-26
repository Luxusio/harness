"""Tests for plugin/scripts/drift_warn.py

The check compares repo source against the scripts directory the hook is
actually executing from, so these tests run a *copy* of drift_warn.py placed in
a fake loaded tree. Before 2026-08-26 it compared against a hardcoded
~/.claude/harness-dev path — the very directory install.py keeps in sync — and
so stayed silent while the session loaded a three-month-old tree missing the
whole receipt subsystem.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"


def _setup(
    tmp_path: Path,
    *,
    with_manifest: bool = True,
    source_files: dict[str, str] | None = None,
    loaded_files: dict[str, str] | None = None,
    loaded_pyc: bool = False,
) -> tuple[Path, Path, dict]:
    """Build a repo plus a separate loaded tree holding a runnable copy.

    Returns (repo, loaded_script, env). `loaded_files` is None to model "no
    separate loaded tree at all", in which case the script is run from the repo
    checkout itself.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    if with_manifest:
        (repo / "doc" / "harness").mkdir(parents=True)
        (repo / "doc" / "harness" / "manifest.yaml").write_text("project_type: cli\n")
    if source_files is not None:
        src_dir = repo / "plugin" / "scripts"
        src_dir.mkdir(parents=True)
        for name, content in source_files.items():
            (src_dir / name).write_text(content)

    if loaded_files is None:
        loaded_script = SCRIPTS / "drift_warn.py"
    else:
        loaded_dir = tmp_path / "loaded" / "plugin" / "scripts"
        loaded_dir.mkdir(parents=True)
        for name, content in loaded_files.items():
            (loaded_dir / name).write_text(content)
        # The script needs its own module neighbours to import _lib. Files that
        # exist only in the loaded tree are never counted as drift, so these
        # copies do not perturb the comparison.
        for helper in ("drift_warn.py", "_lib.py"):
            shutil.copy2(SCRIPTS / helper, loaded_dir / helper)
        if loaded_pyc:
            pyc_dir = loaded_dir / "__pycache__"
            pyc_dir.mkdir()
            (pyc_dir / "a.cpython-311.pyc").write_bytes(b"\x42" * 64)
        loaded_script = loaded_dir / "drift_warn.py"

    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO / "plugin")
    return repo, loaded_script, env


def _run(repo: Path, script: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(script)],
        input="{}",
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
    )


def test_drift_warn_silent_in_sync(tmp_path: Path):
    """Identical *.py content in source and the loaded tree → no output."""
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        loaded_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_fires_when_loaded_tree_stale(tmp_path: Path):
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 2\n"},
        loaded_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert "[drift]" in result.stdout
    assert "1 files differ" in result.stdout


def test_drift_warn_names_the_loaded_root(tmp_path: Path):
    """The resolved path is the only way to see you are running the wrong tree.

    Regression cover for the 2026-08-26 diagnosis: source and the install target
    agreed, so a count alone would still have pointed at the innocent directory.
    """
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 2\n"},
        loaded_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, script, env)
    assert str(script.parent) in result.stdout


def test_drift_warn_silent_when_running_from_the_checkout(tmp_path: Path):
    """Running straight out of the repo: no separate copy can be behind."""
    repo = REPO
    env = os.environ.copy()
    env["HOME"] = str(tmp_path / "home")
    result = _run(repo, SCRIPTS / "drift_warn.py", env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_noop_outside_harness_enabled_repo(tmp_path: Path):
    """No manifest.yaml → is_harness_enabled_repo False → silent."""
    repo, script, env = _setup(
        tmp_path,
        with_manifest=False,
        source_files={"a.py": "x = 2\n"},
        loaded_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_ignores_pyc_and_pycache(tmp_path: Path):
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        loaded_files={"a.py": "x = 1\n"},
        loaded_pyc=True,
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_counts_missing_in_loaded_tree(tmp_path: Path):
    """Source has a.py + b.py; loaded has only a.py → 1 file differs."""
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n", "b.py": "y = 2\n"},
        loaded_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert "[drift]" in result.stdout
    assert "1 files differ" in result.stdout


def test_drift_warn_ignores_extra_files_in_loaded_tree(tmp_path: Path):
    """Leftovers present only in the loaded tree are harmless, not drift."""
    repo, script, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        loaded_files={"a.py": "x = 1\n", "legacy.py": "gone = True\n"},
    )
    result = _run(repo, script, env)
    assert result.returncode == 0
    assert result.stdout == ""
