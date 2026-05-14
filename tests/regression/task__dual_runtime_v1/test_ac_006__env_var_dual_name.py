"""AC-006 regression: plugin_root_env reads HARNESS_PLUGIN_ROOT or CLAUDE_PLUGIN_ROOT.

Behavior under v2.3 -> v2.5 deprecation window:
- HARNESS_PLUGIN_ROOT set, CLAUDE_PLUGIN_ROOT unset  -> returns HARNESS value
- HARNESS_PLUGIN_ROOT unset, CLAUDE_PLUGIN_ROOT set  -> returns CLAUDE value (legacy)
- Both set                                            -> HARNESS wins (deprecation direction)
- Neither set                                         -> returns default arg or None

Also exercises plugin_root_env_pair for subprocess env setup.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "plugin" / "scripts"))


def _clear_env():
    os.environ.pop("HARNESS_PLUGIN_ROOT", None)
    os.environ.pop("CLAUDE_PLUGIN_ROOT", None)


def test_harness_var_wins():
    _clear_env()
    from _lib import plugin_root_env
    os.environ["HARNESS_PLUGIN_ROOT"] = "/expected/harness"
    assert plugin_root_env() == "/expected/harness"
    _clear_env()


def test_claude_fallback_when_harness_unset():
    _clear_env()
    from _lib import plugin_root_env
    os.environ["CLAUDE_PLUGIN_ROOT"] = "/expected/legacy"
    assert plugin_root_env() == "/expected/legacy"
    _clear_env()


def test_harness_preferred_when_both_set():
    _clear_env()
    from _lib import plugin_root_env
    os.environ["HARNESS_PLUGIN_ROOT"] = "/new/preferred"
    os.environ["CLAUDE_PLUGIN_ROOT"] = "/old/deprecated"
    assert plugin_root_env() == "/new/preferred"
    _clear_env()


def test_returns_default_when_neither_set():
    _clear_env()
    from _lib import plugin_root_env
    assert plugin_root_env() is None
    assert plugin_root_env(default="./plugin") == "./plugin"


def test_pair_helper_sets_both():
    from _lib import plugin_root_env_pair
    pair = plugin_root_env_pair("/some/path")
    assert pair == {
        "HARNESS_PLUGIN_ROOT": "/some/path",
        "CLAUDE_PLUGIN_ROOT": "/some/path",
    }


def test_contract_lint_reads_dual_name():
    """contract_lint --quick should run cleanly when env is set via HARNESS_PLUGIN_ROOT."""
    import subprocess
    _clear_env()
    os.environ["HARNESS_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    r = subprocess.run(
        [sys.executable, str(REPO_ROOT / "plugin" / "scripts" / "contract_lint.py"), "--quick", "--quiet"],
        capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=10,
    )
    assert r.returncode in (0, 1), f"unexpected exit {r.returncode}: stderr={r.stderr[:300]}"
    _clear_env()
