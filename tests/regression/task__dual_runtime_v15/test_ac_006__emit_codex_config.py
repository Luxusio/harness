"""AC-006 regression: emit_codex_config.py produces valid Codex TOML.

Tests:
1. emit() returns valid TOML parseable by tomllib
2. Required top-level keys present (mcp_servers, hooks)
3. All 11 hook scripts get a [hooks.state.<key>] entry with placeholder hash
4. --install with existing mcp_servers.harness refuses without --force-merge
5. --install with --force-merge writes backup file
6. Fresh install (no existing config) writes the file directly
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "plugin-codex" / "install" / "emit_codex_config.py"
PLUGIN_ROOT = REPO_ROOT / "plugin"


def _run(args: list[str], stdin: str = "") -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        input=stdin,
        capture_output=True, text=True, timeout=10,
    )


def test_dry_run_emits_valid_toml():
    r = _run(["--plugin-root", str(PLUGIN_ROOT)])
    assert r.returncode == 0, f"emit failed: {r.stderr}"
    parsed = tomllib.loads(r.stdout)
    assert "mcp_servers" in parsed
    assert "harness" in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["harness"]["command"] == "python3"
    assert "hooks" in parsed


def test_hook_events_match_claude():
    r = _run(["--plugin-root", str(PLUGIN_ROOT)])
    parsed = tomllib.loads(r.stdout)
    hooks = parsed["hooks"]
    events = {k for k in hooks if k != "state"}
    # All 4 events from HOOKS_TO_EMIT must be present.
    assert events == {"SessionStart", "Stop", "PreToolUse", "UserPromptSubmit"}, events


def test_trust_state_entries_match_hook_count():
    r = _run(["--plugin-root", str(PLUGIN_ROOT)])
    parsed = tomllib.loads(r.stdout)
    trust = parsed["hooks"]["state"]
    # 5 SessionStart + 1 Stop + 3 PreToolUse + 2 UserPromptSubmit = 11
    assert len(trust) == 11, f"expected 11 trust entries, got {len(trust)}: {sorted(trust)}"
    # Every entry has placeholder hash
    for key, val in trust.items():
        assert val["trusted_hash"].startswith("UNTRUSTED"), (key, val)


def test_install_refuses_existing_without_force(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.harness]\ncommand = "preexisting"\n')
    r = _run(["--plugin-root", str(PLUGIN_ROOT), "--install", "--config-path", str(cfg)])
    assert r.returncode == 2, f"expected exit 2 (refuse), got {r.returncode}: {r.stdout} {r.stderr}"
    assert "already present" in r.stderr


def test_install_force_merge_writes_backup(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.harness]\ncommand = "preexisting"\n')
    r = _run([
        "--plugin-root", str(PLUGIN_ROOT),
        "--install", "--force-merge",
        "--config-path", str(cfg),
    ])
    assert r.returncode == 0, f"force-merge failed: {r.stderr}"
    # Backup file exists
    backups = list(tmp_path.glob("config.toml.bak.*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    # New content has the harness snippet appended
    new_content = cfg.read_text()
    assert "harness v2.3.0" in new_content
    assert "[mcp_servers.harness]" in new_content


def test_fresh_install_writes_file(tmp_path):
    cfg = tmp_path / "config.toml"
    assert not cfg.exists()
    r = _run([
        "--plugin-root", str(PLUGIN_ROOT),
        "--install",
        "--config-path", str(cfg),
    ])
    assert r.returncode == 0, r.stderr
    assert cfg.exists()
    # Parses as valid TOML
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["mcp_servers"]["harness"]["enabled"] is True


def test_missing_plugin_root_errors():
    r = _run(["--plugin-root", "/nonexistent/path"])
    assert r.returncode == 1
    assert "not a directory" in r.stderr
