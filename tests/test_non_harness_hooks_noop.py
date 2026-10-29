"""Global hooks must no-op in repos without doc/harness/manifest.yaml."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"


def _run(script: str, repo: Path, payload: dict | None = None, *args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO / "plugin")
    env.pop("HARNESS_SKIP_PREWRITE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args],
        input=json.dumps(payload or {}),
        capture_output=True,
        text=True,
        cwd=repo,
        env=env,
        timeout=10,
    )


def _repo(tmp_path: Path) -> Path:
    (tmp_path / ".git").mkdir()
    return tmp_path


def _assert_no_harness_files(repo: Path) -> None:
    assert not (repo / "doc" / "harness").exists()


def test_prewrite_gate_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    target = repo / "src" / "app.py"
    payload = {
        "cwd": str(repo),
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "x = 1\n"},
    }

    result = _run("prewrite_gate.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_prewrite_gate_does_not_inherit_outer_manifest_for_gitfile_worktree(tmp_path: Path):
    outer = _repo(tmp_path)
    manifest = outer / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("type: cli\n", encoding="utf-8")
    inner = outer / "plain-worktree"
    inner.mkdir()
    (inner / ".git").write_text(
        "gitdir: ../.git/worktrees/plain-worktree\n", encoding="utf-8",
    )
    target = inner / "src/app.py"
    payload = {
        "cwd": str(inner),
        "tool_name": "Write",
        "tool_input": {"file_path": str(target), "content": "x = 1\n"},
    }

    result = _run("prewrite_gate.py", inner, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    assert not (inner / "doc/harness").exists()


def test_tool_routing_noops_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    payload = {
        "cwd": str(repo),
        "tool_name": "Bash",
        "tool_response": {"stderr": "bash: pytest: command not found"},
    }

    result = _run("tool_routing.py", repo, payload)

    assert result.returncode == 0
    assert result.stdout == ""
    _assert_no_harness_files(repo)


def test_note_freshness_does_not_mutate_without_manifest(tmp_path: Path):
    repo = _repo(tmp_path)
    doc = repo / "doc" / "note.md"
    doc.parent.mkdir()
    doc.write_text(
        "---\n"
        "freshness: current\n"
        "invalidated_by_paths:\n"
        "  - src\n"
        "---\n"
        "body\n",
        encoding="utf-8",
    )
    before = doc.read_text(encoding="utf-8")

    result = _run("note_freshness.py", repo, None, "--paths", "src/app.py", "--quiet")

    assert result.returncode == 0
    assert result.stdout == ""
    assert doc.read_text(encoding="utf-8") == before
    _assert_no_harness_files(repo)


def _isolated_env(home: Path) -> dict[str, str]:
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("HARNESS_", "CODEX_", "CLAUDE_"))
    }
    env.update(HOME=str(home), CODEX_HOME=str(home / ".codex"),
               CLAUDE_PLUGIN_ROOT=str(REPO / "plugin"),
               PYTHONDONTWRITEBYTECODE="1")
    return env


def _tree(root: Path) -> dict[str, tuple[str, bytes | str]]:
    """Include empty directories as well as bytes; a lock is an effect too."""
    result = {}
    for path in root.rglob("*"):
        relative = path.relative_to(root).as_posix()
        if path.is_symlink():
            result[relative] = ("symlink", os.readlink(path))
        elif path.is_file():
            result[relative] = ("file", path.read_bytes())
        else:
            result[relative] = ("directory", "")
    return result


def _enable(root: Path) -> None:
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: cli\nlanguages: [python]\n", encoding="utf-8")


_REGISTERED = json.loads((REPO / "plugin/hooks/hooks.json").read_text())["hooks"]
_HOOK_COMMANDS = [
    pytest.param(event, group.get("matcher", ""), hook["command"],
                 id=f"{event}-{index}-{hook_index}")
    for event, groups in _REGISTERED.items()
    for index, group in enumerate(groups)
    for hook_index, hook in enumerate(group["hooks"])
]


@pytest.mark.parametrize("event,matcher,command", _HOOK_COMMANDS)
@pytest.mark.parametrize("kind", ["plain", "git", "nested-git"])
def test_actual_registered_claude_hooks_are_inactive_outside_initialized_project(
    tmp_path: Path, event: str, matcher: str, command: str, kind: str,
):
    home = tmp_path / "home"
    home.mkdir()
    enabled = tmp_path / "enabled"
    _enable(enabled)
    repo = enabled / "nested" if kind == "nested-git" else tmp_path / "plain"
    repo.mkdir()
    if kind != "plain":
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True,
                       capture_output=True, env=_isolated_env(home))
    tool = "Write" if event == "PreToolUse" else (
        "mcp__harness__wbs" if "wbs" in matcher else "Bash")
    payload = {
        "cwd": str(repo), "tool_name": tool,
        "tool_input": {"file_path": str(repo / "src/app.py"), "content": "pass\n"},
        "tool_response": {"stderr": "bash: pytest: command not found"},
    }
    before = _tree(tmp_path)
    # The host cwd is deliberately enabled: the event payload owns context.
    result = subprocess.run(command, shell=True, input=json.dumps(payload),
                            text=True, capture_output=True, cwd=enabled,
                            env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("script,tool", [
    ("hook_session_start.py", ""),
    ("hook_user_prompt_submit.py", ""),
    ("hook_pre_tool_use.py", "Write"),
    ("hook_post_tool_use.py", "create_goal"),
])
@pytest.mark.parametrize("cwd_value", [None, "missing", 42, ""])
def test_codex_wrappers_do_not_use_enabled_process_cwd_for_invalid_payload_cwd(
    tmp_path: Path, script: str, tool: str, cwd_value,
):
    home = tmp_path / "home"
    home.mkdir()
    enabled = tmp_path / "enabled"
    _enable(enabled)
    cwd = str(enabled / "not-created") if cwd_value == "missing" else cwd_value
    payload = {"cwd": cwd, "tool_name": tool,
               "tool_input": {"file_path": str(tmp_path / "app.py"), "content": "pass"},
               "tool_response": {"status": "success"}}
    before = _tree(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / script)],
                            input=json.dumps(payload), text=True, capture_output=True,
                            cwd=enabled, env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("script,tool", [
    ("hook_session_start.py", ""),
    ("hook_user_prompt_submit.py", ""),
    ("hook_pre_tool_use.py", "Write"),
    ("hook_post_tool_use.py", "create_goal"),
])
@pytest.mark.parametrize("kind", ["plain", "git", "nested-git"])
def test_codex_wrappers_respect_uninitialized_payload_project(
    tmp_path: Path, script: str, tool: str, kind: str,
):
    home = tmp_path / "home"
    home.mkdir()
    enabled = tmp_path / "enabled"
    _enable(enabled)
    repo = enabled / "nested" if kind == "nested-git" else tmp_path / "project"
    repo.mkdir()
    if kind != "plain":
        subprocess.run(["git", "init", "--quiet", str(repo)], check=True,
                       capture_output=True, env=_isolated_env(home))
    payload = {"cwd": str(repo), "tool_name": tool,
               "tool_input": {"file_path": str(repo / "app.py"), "content": "pass"},
               "tool_response": {"status": "success"}}
    before = _tree(tmp_path)
    result = subprocess.run([sys.executable, "-B", str(SCRIPTS / script)],
                            input=json.dumps(payload), text=True, capture_output=True,
                            cwd=enabled, env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("alias", [False, True])
def test_registered_welcome_activates_in_initialized_non_git_project_subdirectory(
    tmp_path: Path, alias: bool,
):
    root = tmp_path / "enabled"
    _enable(root)
    cwd = root / "src"
    cwd.mkdir()
    if alias:
        link = tmp_path / "alias"
        link.symlink_to(root, target_is_directory=True)
        cwd = link / "src"
    home = tmp_path / "home"
    home.mkdir()
    command = _REGISTERED["SessionStart"][0]["hooks"][0]["command"]
    result = subprocess.run(command, shell=True, input=json.dumps({"cwd": str(cwd)}),
                            text=True, capture_output=True, cwd=tmp_path,
                            env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert "harness — ready." in result.stdout
    assert "Project: enabled" in result.stdout


@pytest.mark.parametrize("component", ["manifest", "control-directory"])
def test_manifest_alias_is_not_hook_activation_authority(tmp_path: Path, component: str):
    source = tmp_path / "source"
    _enable(source)
    repo = tmp_path / "project"
    (repo / "doc").mkdir(parents=True)
    if component == "manifest":
        (repo / "doc/harness").mkdir()
        (repo / "doc/harness/manifest.yaml").symlink_to(source / "doc/harness/manifest.yaml")
    else:
        (repo / "doc/harness").symlink_to(source / "doc/harness", target_is_directory=True)
    home = tmp_path / "home"
    home.mkdir()
    before = _tree(tmp_path)
    for args in (["hook_session_start.py", "--claude-banner"], ["hook_user_prompt_submit.py"]):
        result = subprocess.run([sys.executable, "-B", str(SCRIPTS / args[0]), *args[1:]],
                                input=json.dumps({"cwd": str(repo)}), text=True,
                                capture_output=True, cwd=source,
                                env=_isolated_env(home), timeout=12)
        assert result.returncode == 0, result.stderr
        assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("event,matcher,command", _HOOK_COMMANDS)
@pytest.mark.parametrize("cwd_value", [None, "missing", 42, ""])
def test_registered_claude_hooks_ignore_invalid_explicit_cwd(
    tmp_path: Path, event: str, matcher: str, command: str, cwd_value,
):
    home = tmp_path / "home"
    home.mkdir()
    enabled = tmp_path / "enabled"
    _enable(enabled)
    cwd = str(enabled / "not-created") if cwd_value == "missing" else cwd_value
    tool = "Write" if event == "PreToolUse" else (
        "mcp__harness__wbs" if "wbs" in matcher else "Bash")
    payload = {"cwd": cwd, "tool_name": tool,
               "tool_input": {"file_path": str(tmp_path / "app.py"), "content": "pass"},
               "tool_response": {"stderr": "bash: pytest: command not found"}}
    before = _tree(tmp_path)
    result = subprocess.run(command, shell=True, input=json.dumps(payload),
                            text=True, capture_output=True, cwd=enabled,
                            env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before


@pytest.mark.parametrize("event,matcher,command", _HOOK_COMMANDS)
@pytest.mark.parametrize("kind", ["oversized", "malformed", "non-object"])
def test_registered_hooks_never_fallback_after_rejected_input(
    tmp_path: Path, event: str, matcher: str, command: str, kind: str,
):
    home = tmp_path / "home"
    home.mkdir()
    enabled = tmp_path / "enabled"
    _enable(enabled)
    outside = tmp_path / "outside"
    outside.mkdir()
    if kind == "oversized":
        payload = json.dumps({"cwd": str(outside), "prompt": "x" * 70000})
    else:
        payload = "{" if kind == "malformed" else "null"
    before = _tree(tmp_path)
    result = subprocess.run(command, shell=True, input=payload, text=True,
                            capture_output=True, cwd=enabled,
                            env=_isolated_env(home), timeout=12)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "", result.stdout
    assert _tree(tmp_path) == before
