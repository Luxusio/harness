"""Tests for plugin/scripts/drift_warn.py"""
from __future__ import annotations

import os
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
    installed_files: dict[str, str] | None = None,
    installed_pyc: bool = False,
) -> tuple[Path, dict]:
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
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    if installed_files is not None:
        inst_dir = fake_home / ".claude" / "harness-dev" / "plugin" / "scripts"
        inst_dir.mkdir(parents=True)
        for name, content in installed_files.items():
            (inst_dir / name).write_text(content)
        if installed_pyc:
            pyc_dir = inst_dir / "__pycache__"
            pyc_dir.mkdir()
            (pyc_dir / "a.cpython-311.pyc").write_bytes(b"\x42" * 64)
    env = os.environ.copy()
    env["HOME"] = str(fake_home)
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO / "plugin")
    return repo, env


def _run(repo: Path, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "drift_warn.py")],
        input="{}",
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
    )


def test_drift_warn_silent_in_sync(tmp_path: Path):
    """When source and installed have identical *.py content, no output."""
    repo, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        installed_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_fires_when_installed_stale(tmp_path: Path):
    """When installed a.py differs from source a.py, warn with count=1."""
    repo, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 2\n"},
        installed_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert "[drift]" in result.stdout
    assert "1 files differ" in result.stdout


def test_drift_warn_noop_in_non_dev_repo(tmp_path: Path):
    """When manifest present but no installed dir, silent (non-dev mode)."""
    repo, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        installed_files=None,  # installed dir not created
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_noop_outside_harness_enabled_repo(tmp_path: Path):
    """When no manifest.yaml, is_harness_enabled_repo returns False → silent."""
    repo, env = _setup(
        tmp_path,
        with_manifest=False,
        source_files={"a.py": "x = 1\n"},
        installed_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_ignores_pyc_and_pycache(tmp_path: Path):
    """pyc files in installed __pycache__ must not cause a false positive."""
    repo, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n"},
        installed_files={"a.py": "x = 1\n"},
        installed_pyc=True,
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert result.stdout == ""


def test_drift_warn_counts_missing_in_installed(tmp_path: Path):
    """Source has a.py + b.py; installed has only a.py → 1 file differs."""
    repo, env = _setup(
        tmp_path,
        source_files={"a.py": "x = 1\n", "b.py": "y = 2\n"},
        installed_files={"a.py": "x = 1\n"},
    )
    result = _run(repo, env)
    assert result.returncode == 0
    assert "[drift]" in result.stdout
    assert "1 files differ" in result.stdout
