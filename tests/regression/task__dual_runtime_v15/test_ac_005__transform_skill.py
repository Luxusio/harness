"""AC-005 regression: transform_skill.py round-trip for hello canonical.

Tests:
1. Canonical → Claude SKILL.md → token map applied (Read, Bash, mcp__harness__task_start)
2. Canonical → Codex SKILL.md → token map flipped (read_file, shell, task_start)
3. Codex output contains runtime_notes header; Claude output does not
4. Sub-skill renders as Skill() on Claude, "read inline" prose on Codex
5. Interaction renders as AskUserQuestion list on Claude, conversational ask on Codex
6. Dry-run emits both targets to stdout
7. Emit-to-file writes atomically with content-hash header
8. Validation rejects canonical missing required fields
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "plugin" / "runtime-sync" / "transform_skill.py"
HELLO = REPO_ROOT / "tests" / "runtime-sync" / "corpus" / "hello.skill.yaml"

sys.path.insert(0, str(REPO_ROOT / "plugin" / "runtime-sync"))


def _run(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, text=True, timeout=10)


def test_claude_token_substitution():
    r = _run(["--canonical", str(HELLO), "--dry-run"])
    assert r.returncode == 0, r.stderr
    claude_section = r.stdout.split("===== CODEX TARGET =====")[0]
    assert "Use Read to inspect" in claude_section
    assert "shell command" not in claude_section  # codex-only token wins
    assert "quick Bash command" in claude_section
    assert "mcp__harness__task_start" in claude_section


def test_codex_token_substitution():
    r = _run(["--canonical", str(HELLO), "--dry-run"])
    assert r.returncode == 0, r.stderr
    codex_section = r.stdout.split("===== CODEX TARGET =====")[1]
    assert "Use read_file" in codex_section
    assert "quick shell command" in codex_section
    # Bare task_start, not the mcp_ prefix
    assert "Then call task_start" in codex_section
    assert "mcp__harness__task_start" not in codex_section


def test_runtime_notes_only_on_codex():
    r = _run(["--canonical", str(HELLO), "--dry-run"])
    claude_section, codex_section = r.stdout.split("===== CODEX TARGET =====")
    assert "Codex runtime notes" not in claude_section
    assert "Codex runtime notes" in codex_section


def test_sub_skill_rendering():
    r = _run(["--canonical", str(HELLO), "--dry-run"])
    claude_section, codex_section = r.stdout.split("===== CODEX TARGET =====")
    assert 'Skill("harness:plan"' in claude_section
    assert "read the SKILL.md and execute the methodology inline" in codex_section


def test_interaction_rendering():
    r = _run(["--canonical", str(HELLO), "--dry-run"])
    claude_section, codex_section = r.stdout.split("===== CODEX TARGET =====")
    # Claude shows AskUserQuestion call sites
    assert "AskUserQuestion call sites" in claude_section
    # Codex shows conversational ask
    assert "conversational asks" in codex_section
    assert "Ask: Confirm project name?" in codex_section


def test_emit_to_files(tmp_path):
    claude_out = tmp_path / "claude" / "SKILL.md"
    codex_out = tmp_path / "codex" / "SKILL.md"
    r = _run([
        "--canonical", str(HELLO),
        "--emit-claude", str(claude_out),
        "--emit-codex", str(codex_out),
    ])
    assert r.returncode == 0, r.stderr
    assert claude_out.exists() and codex_out.exists()
    claude_content = claude_out.read_text()
    codex_content = codex_out.read_text()
    # Both have content hash header
    assert claude_content.startswith("<!-- canonical-hash: ")
    assert codex_content.startswith("<!-- canonical-hash: ")
    # Different content (different hashes)
    claude_hash = claude_content.split("\n")[0]
    codex_hash = codex_content.split("\n")[0]
    assert claude_hash != codex_hash


def test_validation_rejects_missing_required(tmp_path):
    bad = tmp_path / "bad.skill.yaml"
    bad.write_text(json.dumps({"name": "x"}))  # missing description + body
    r = _run(["--canonical", str(bad), "--dry-run"])
    assert r.returncode != 0
    # Validation error mentions the missing fields
    err = r.stderr + r.stdout
    assert "description" in err or "body" in err


def test_missing_canonical_file_errors():
    r = _run(["--canonical", "/nonexistent.yaml", "--dry-run"])
    assert r.returncode == 1
    assert "not found" in r.stderr


def test_emit_without_target_errors(tmp_path):
    """No --dry-run AND no --emit-* should fail with explicit error."""
    r = _run(["--canonical", str(HELLO)])
    assert r.returncode == 2
    assert "no --emit-claude or --emit-codex" in r.stderr
