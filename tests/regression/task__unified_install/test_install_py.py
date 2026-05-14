"""AC-004 regression: install.py CLI surface + behavior.

Tests:
1. --dry-run prints non-empty plan and does NOT mutate any config
2. --codex-only flag suppresses claude steps
3. --claude-only flag suppresses codex steps
4. --codex-only + --claude-only is rejected (exit 2)
5. Missing CLI is handled cleanly (no crash, returncode reflects fail per runtime)
6. Parallel execution: both runtimes are attempted in one run when both CLIs exist
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_PY = REPO_ROOT / "install.py"


def _run(args: list[str], extra_path: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_path is not None:
        # Replace PATH so neither codex nor claude is discoverable
        env["PATH"] = extra_path
    return subprocess.run(
        [sys.executable, str(INSTALL_PY)] + args,
        capture_output=True, text=True, timeout=30, env=env,
    )


def test_install_py_exists():
    assert INSTALL_PY.is_file()
    # Shebang for direct invocation
    assert INSTALL_PY.read_text().startswith("#!/usr/bin/env python3")


def test_dry_run_prints_plan_without_mutation(tmp_path):
    # Sentinel config path that should remain untouched on dry-run
    cfg = tmp_path / "fake-codex-config.toml"
    cfg.write_text("# untouched\n")
    pre_size = cfg.stat().st_size
    r = _run(["--dry-run", "--config-path", str(cfg)])
    assert r.returncode == 0, f"dry-run exit {r.returncode}: {r.stderr}"
    # Output mentions both runtimes (whichever exist) and "dry-run"
    assert "dry-run" in r.stdout.lower()
    # Config file untouched
    assert cfg.stat().st_size == pre_size
    assert cfg.read_text() == "# untouched\n"


def test_codex_only_skips_claude(tmp_path):
    cfg = tmp_path / "fake-codex-config.toml"
    r = _run(["--dry-run", "--codex-only", "--config-path", str(cfg)])
    assert r.returncode == 0, r.stderr
    assert "[claude]" not in r.stdout
    # codex section present (or "codex CLI not found" if codex unavailable in env)
    assert "[codex]" in r.stdout or "codex CLI not found" in r.stdout


def test_claude_only_skips_codex(tmp_path):
    cfg = tmp_path / "fake-codex-config.toml"
    r = _run(["--dry-run", "--claude-only", "--config-path", str(cfg)])
    assert r.returncode == 0, r.stderr
    assert "[codex]" not in r.stdout
    assert "[claude]" in r.stdout or "claude CLI not found" in r.stdout


def test_mutual_exclusion_rejected():
    r = _run(["--codex-only", "--claude-only"])
    assert r.returncode == 2
    assert "mutually exclusive" in r.stderr


def test_missing_cli_handled_gracefully(tmp_path):
    """When BOTH CLIs missing, every runtime install fails cleanly with non-zero exit."""
    cfg = tmp_path / "fake-codex-config.toml"
    # Empty PATH — no codex, no claude discoverable
    r = _run(["--dry-run", "--config-path", str(cfg)], extra_path=str(tmp_path))
    # Both should report "CLI not found" but the harness itself shouldn't crash
    assert r.returncode == 1  # any runtime failed → exit 1
    assert "not found in PATH" in r.stdout
    # Both should report cleanly (no Python traceback)
    assert "Traceback" not in r.stderr
    assert "Traceback" not in r.stdout


def test_dry_run_parallel_attempts_both_when_available():
    """When both CLIs are present, dry-run output mentions both [codex] and [claude]."""
    if not (shutil.which("codex") and shutil.which("claude")):
        # Skip when env lacks one — test is environment-conditional
        return
    r = _run(["--dry-run"])
    assert r.returncode == 0, r.stderr
    assert "[codex]" in r.stdout
    assert "[claude]" in r.stdout


def test_help_lists_all_flags():
    r = _run(["--help"])
    assert r.returncode == 0
    for flag in ["--codex-only", "--claude-only", "--dry-run", "--force", "--config-path"]:
        assert flag in r.stdout, f"missing flag in --help: {flag}"
