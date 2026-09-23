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
    (root / "doc/harness/manifest.yaml").write_text("version: 5\nname: demo\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def migrate(root: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(root), "--migrate-harness-version"],
        capture_output=True, text=True, timeout=10,
    )


def test_legacy_project_is_prompted_and_migrated_once(tmp_path):
    root = repo(tmp_path)
    (root / ".gitignore").write_text("# user\ncustom.log\n", encoding="utf-8")
    check = load_check()
    message = check.reminder(root)
    assert "manifest version 5 -> 6" in message
    assert "--migrate-harness-version" in message

    first = migrate(root)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "updated=true" in first.stdout
    assert "version: 6\n" in (root / "doc/harness/manifest.yaml").read_text()
    assert "harness_version" not in (root / "doc/harness/manifest.yaml").read_text()
    ignores = (root / ".gitignore").read_text()
    assert "custom.log" in ignores
    assert "doc/harness/.watcher-diagnostics.json" in ignores
    assert check.reminder(root) == ""

    second = migrate(root)
    assert second.returncode == 0, second.stdout + second.stderr
    assert "updated=false" in second.stdout
    assert (root / ".gitignore").read_text() == ignores


def test_current_field_with_missing_ignore_is_repaired(tmp_path):
    root = repo(tmp_path)
    (root / "doc/harness/manifest.yaml").write_text("version: 6\nname: demo\n")
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
    assert not (nested / "doc/harness/manifest.yaml").exists()

    outside = tmp_path / "outside"
    outside.mkdir()
    assert migrate(outside).returncode == 1
    assert not (outside / ".gitignore").exists()


def test_reminder_names_absolute_git_root_for_nested_session(tmp_path):
    root = repo(tmp_path)
    message = load_check().reminder(root)
    assert f"--repo {root}" in message
    assert "--repo ." not in message


def test_invalid_and_future_fields_do_not_mutate(tmp_path):
    root = repo(tmp_path)
    manifest = root / "doc/harness/manifest.yaml"
    for raw in ("garbage", "7", "1.5", "true", "-1", ""):
        manifest.write_text(f"version: {raw}\nname: demo\n")
        before = manifest.read_text()
        message = load_check().reminder(root)
        assert "Cannot check Harness version" in message
        assert "--migrate-harness-version" not in message
        result = migrate(root)
        assert result.returncode == 1
        assert manifest.read_text() == before
        assert not (root / ".gitignore").exists()

    manifest.write_text("version: 5\nversion: 5\n")
    assert "duplicate top-level key" in migrate(root).stdout
    assert not (root / ".gitignore").exists()


def test_tracked_operational_file_does_not_advance_marker(tmp_path):
    root = repo(tmp_path)
    target = root / "doc/harness/.watcher-diagnostics.json"
    target.write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(target)], check=True)
    result = migrate(root)
    assert result.returncode == 1
    assert "already tracked" in result.stdout
    assert "harness_version" not in (root / "doc/harness/manifest.yaml").read_text()
    assert not (root / ".gitignore").exists()


def test_alternate_git_index_cannot_hide_tracked_operational_file(tmp_path):
    root = repo(tmp_path)
    target = root / "doc/harness/.watcher-diagnostics.json"
    target.write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(target)], check=True)
    env = os.environ.copy()
    env["GIT_INDEX_FILE"] = str(tmp_path / "empty-index")

    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(root), "--migrate-harness-version"],
        capture_output=True, text=True, timeout=10, env=env,
    )

    assert result.returncode == 1
    assert "already tracked" in result.stdout
    assert "harness_version" not in (root / "doc/harness/manifest.yaml").read_text()


def test_legacy_standalone_marker_removed_after_success(tmp_path):
    root = repo(tmp_path)
    legacy = root / "doc/harness/.format-version"
    legacy.write_text("1\n")
    assert "manifest version 5 -> 6" in load_check().reminder(root)
    assert migrate(root).returncode == 0
    assert not legacy.exists()
    assert "harness_version" not in (root / "doc/harness/manifest.yaml").read_text()


def test_current_field_with_legacy_file_prompts_cleanup(tmp_path):
    root = repo(tmp_path)
    assert migrate(root).returncode == 0
    legacy = root / "doc/harness/.format-version"
    legacy.write_text("1\n")
    assert "obsolete standalone version file" in load_check().reminder(root)
    assert migrate(root).returncode == 0
    assert not legacy.exists()


def test_leftover_harness_version_key_at_current_version_is_reminded_and_stripped(tmp_path):
    root = repo(tmp_path)
    assert migrate(root).returncode == 0
    manifest = root / "doc/harness/manifest.yaml"
    manifest.write_text(manifest.read_text() + "harness_version: 1\n")
    assert "obsolete harness_version manifest key" in load_check().reminder(root)
    assert migrate(root).returncode == 0
    assert "harness_version" not in manifest.read_text()
    assert load_check().reminder(root) == ""


def test_pre_v5_manifest_migrates_schema_before_stamping(tmp_path):
    root = repo(tmp_path)
    manifest = root / "doc/harness/manifest.yaml"
    manifest.write_text("project: demo\nproject_type: api\nharness_version: 2\n")
    result = migrate(root)
    assert result.returncode == 0, result.stdout + result.stderr
    body = manifest.read_text()
    assert "version: 6\n" in body
    assert "name: demo\n" in body
    assert "harness_version:" not in body
    assert load_check().reminder(root) == ""


def test_future_manifest_schema_is_not_modified(tmp_path):
    root = repo(tmp_path)
    manifest = root / "doc/harness/manifest.yaml"
    manifest.write_text("version: 7\nname: future\n")
    result = migrate(root)
    assert result.returncode == 1
    assert "newer than supported schema" in result.stdout
    assert manifest.read_text() == "version: 7\nname: future\n"
    assert not (root / ".gitignore").exists()


def test_terminal_manifest_version_without_newline_remains_valid(tmp_path):
    root = repo(tmp_path)
    manifest = root / "doc/harness/manifest.yaml"
    manifest.write_text("name: demo\nversion: 5")
    result = migrate(root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert manifest.read_text() == "name: demo\nversion: 6"
    assert load_check().reminder(root) == ""


def test_legacy_marker_preserved_when_validation_fails(tmp_path):
    root = repo(tmp_path)
    legacy = root / "doc/harness/.format-version"
    legacy.write_text("1\n")
    target = root / "doc/harness/.watcher-diagnostics.json"
    target.write_text("{}\n")
    subprocess.run(["git", "-C", str(root), "add", "-f", str(target)], check=True)
    assert migrate(root).returncode == 1
    assert legacy.read_text() == "1\n"
    assert "harness_version" not in (root / "doc/harness/manifest.yaml").read_text()


def test_symlinked_version_marker_fails_closed_without_writes(tmp_path):
    root = repo(tmp_path)
    outside = tmp_path / "outside-version"
    outside.write_text("2.3.0\n")
    (root / "doc/harness/.version").symlink_to(outside)
    result = migrate(root)
    assert result.returncode == 1
    assert "symlink" in result.stdout
    assert not (root / ".gitignore").exists()
    assert "version: 6" not in (root / "doc/harness/manifest.yaml").read_text()
    assert outside.read_text() == "2.3.0\n"


def test_directory_format_version_marker_fails_closed_without_writes(tmp_path):
    root = repo(tmp_path)
    (root / "doc/harness/.format-version").mkdir()
    result = migrate(root)
    assert result.returncode == 1
    assert "must be a regular file" in result.stdout
    assert not (root / ".gitignore").exists()
    assert "version: 6" not in (root / "doc/harness/manifest.yaml").read_text()


def test_both_session_start_hooks_run_check():
    claude = json.loads((ROOT / "plugin/hooks/hooks.json").read_text())
    commands = [item["command"] for item in claude["hooks"]["SessionStart"][0]["hooks"]]
    assert sum("project_format_check.py" in item for item in commands) == 1
    codex = (ROOT / "plugin/scripts/hook_session_start.py").read_text()
    assert '["project_format_check.py"]' in codex
