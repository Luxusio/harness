"""Versioned project-file repair is prompted on both runtime surfaces."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path



ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugin/scripts/setup_finalize.py"
CHECK = ROOT / "plugin/scripts/project_format_check.py"


def load_check():
    spec = importlib.util.spec_from_file_location("project_format_check_test", CHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / "doc/harness").mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def migrate(root: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(root), "--migrate-file-format"],
        capture_output=True, text=True, timeout=10,
    )


def test_legacy_project_is_prompted_and_migrated_once(tmp_path):
    root = repo(tmp_path)
    (root / ".gitignore").write_text("# user\ncustom.log\n", encoding="utf-8")
    check = load_check()
    message = check.reminder(root)
    assert "project format 0 -> 1" in message
    assert "--migrate-file-format" in message

    first = migrate(root)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "updated=true" in first.stdout
    assert (root / "doc/harness/.format-version").read_text() == "1\n"
    ignores = (root / ".gitignore").read_text()
    assert "custom.log" in ignores
    assert "doc/harness/.watcher-diagnostics.json" in ignores
    assert check.reminder(root) == ""

    second = migrate(root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "updated=false" in second.stdout
    assert (root / ".gitignore").read_text() == ignores


def test_current_marker_with_missing_ignore_is_repaired(tmp_path):
    root = repo(tmp_path)
    (root / "doc/harness/.format-version").write_text("1\n")
    (root / ".gitignore").write_text("# user\n")
    assert "operational .gitignore drift" in load_check().reminder(root)
    assert migrate(root).returncode == 0
    assert load_check().reminder(root) == ""


def test_existing_ignore_line_overridden_by_negation_is_reported(tmp_path):
    root = repo(tmp_path)
    assert migrate(root).returncode == 0
    with (root / ".gitignore").open("a") as stream:
        stream.write("!doc/harness/.watcher-diagnostics.json\n")
    assert "effective-ignore errors=" in load_check().reminder(root)
    assert migrate(root).returncode == 0
    assert load_check().reminder(root) == ""


def test_migration_rejects_subdirectory_and_non_git_root(tmp_path):
    root = repo(tmp_path)
    nested = root / "nested"
    nested.mkdir()
    wrong = migrate(nested)
    assert wrong.returncode == 1
    assert "exact Git root" in wrong.stdout
    assert not (nested / ".gitignore").exists()
    assert not (nested / "doc/harness/.format-version").exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    assert migrate(outside).returncode == 1
    assert not (outside / ".gitignore").exists()


def test_reminder_names_absolute_git_root_for_nested_session(tmp_path):
    root = repo(tmp_path)
    message = load_check().reminder(root)
    assert f"--repo {root}" in message
    assert "--repo ." not in message


def test_missing_future_migration_fails_closed(tmp_path, monkeypatch):
    import pytest

    load_check()
    from setup_finalize import pending_project_format_migrations
    import setup_finalize
    monkeypatch.setattr(setup_finalize, "PROJECT_FORMAT_VERSION", 2)
    with pytest.raises(ValueError, match="migration 2 is not implemented"):
        pending_project_format_migrations(1)


def test_invalid_and_future_markers_do_not_mutate(tmp_path):
    root = repo(tmp_path)
    marker = root / "doc/harness/.format-version"
    for raw in ("garbage\n", "2\n"):
        marker.write_text(raw)
        message = load_check().reminder(root)
        assert "Cannot check project format" in message
        assert "--migrate-file-format" not in message
        result = migrate(root)
        assert result.returncode == 1
        assert marker.read_text() == raw
        assert not (root / ".gitignore").exists()


def test_tracked_operational_file_does_not_advance_marker(tmp_path):
    root = repo(tmp_path)
    target = root / "doc/harness/.watcher-diagnostics.json"
    target.write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(target)], check=True)
    result = migrate(root)
    assert result.returncode == 1
    assert "already tracked" in result.stdout
    assert not (root / "doc/harness/.format-version").exists()
    assert not (root / ".gitignore").exists()


def test_alternate_git_index_cannot_hide_tracked_operational_file(tmp_path):
    root = repo(tmp_path)
    target = root / "doc/harness/.watcher-diagnostics.json"
    target.write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(target)], check=True)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(tmp_path / "empty-index")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(root), "--migrate-file-format"],
        capture_output=True, text=True, timeout=10, env=env,
    )

    assert result.returncode == 1
    assert "already tracked" in result.stdout
    assert not (root / "doc/harness/.format-version").exists()


def test_both_session_start_hooks_run_check():
    claude = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())
    commands = [item["command"] for item in claude["hooks"]["SessionStart"][0]["hooks"]]
    assert sum("project_format_check.py" in item for item in commands) == 1
    codex = (ROOT / "plugin/scripts/hook_session_start.py").read_text()
    assert '["project_format_check.py"]' in codex
