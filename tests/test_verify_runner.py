from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
RUNNER_PATH = REPO_ROOT / "plugin" / "scripts" / "verify_runner.py"

spec = importlib.util.spec_from_file_location("verify_runner", RUNNER_PATH)
assert spec and spec.loader
verify_runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(verify_runner)


def test_parallel_runner_preserves_manifest_order_and_passes():
    commands = ["python3 -c 'print(\"first\")'", "python3 -c 'print(\"second\")'"]
    if os.name == "nt":
        commands = [subprocess.list2cmdline([sys.executable, "-c", f"print({value!r})"])
                    for value in ("first", "second")]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        payload = verify_runner.run(
            commands,
            root,
            parallel=True,
            max_workers=2,
            timeout=5,
        )
    assert payload["returncode"] == 0
    assert payload["status"] == "PASS"
    assert [r["index"] for r in payload["commands"]] == [0, 1]
    assert "first" in payload["commands"][0]["stdout"]
    assert "second" in payload["commands"][1]["stdout"]


def _cwd_check_command():
    return (subprocess.list2cmdline([sys.executable, "-c", "from pathlib import Path;print(Path.cwd())"])
            if os.name == "nt" else "pwd -P")


def _main_with_in_tree_worktree(tmp: str) -> tuple[Path, Path]:
    """A main checkout (``.git`` dir) holding a linked worktree (``.git`` gitfile)
    under ``.claude/worktrees``, each with its own manifest verify_commands."""
    main = Path(os.path.realpath(tmp)) / "main"
    wt = main / ".claude" / "worktrees" / "lead"
    (main / ".git" / "worktrees" / "lead").mkdir(parents=True)
    wt.mkdir(parents=True)
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'lead'}\n", encoding="utf-8")
    for root, command in ((main, "echo main-checkout"), (wt, _cwd_check_command())):
        manifest = root / "doc" / "harness" / "manifest.yaml"
        manifest.parent.mkdir(parents=True)
        rendered = command
        manifest.write_text(f"verify_commands:\n  - {rendered}\n", encoding="utf-8")
    return main, wt


def test_find_repo_root_stops_at_a_linked_worktree_gitfile():
    with tempfile.TemporaryDirectory() as tmp:
        main, wt = _main_with_in_tree_worktree(tmp)
        (wt / "src").mkdir()
        assert verify_runner._find_repo_root(str(wt / "src")) == wt
        assert verify_runner._find_repo_root(str(wt)) == wt
        assert verify_runner._find_repo_root(str(main / "doc")) == main


def test_runner_from_worktree_runs_the_worktree_manifest_in_the_worktree():
    with tempfile.TemporaryDirectory() as tmp:
        _main, wt = _main_with_in_tree_worktree(tmp)
        proc = subprocess.run(
            [sys.executable, str(RUNNER_PATH), "--json"],
            cwd=str(wt), capture_output=True, text=True, timeout=30,
        )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert [r["command"] for r in payload["commands"]] == [_cwd_check_command()]
    assert payload["commands"][0]["stdout"].strip() == str(wt)


def test_plain_manifest_command_preserves_quoted_argument_in_actual_cli(tmp_path):
    command = ("call " if os.name == "nt" else "exec ") + subprocess.list2cmdline(
        [sys.executable, "-c", "print('plain scalar ok')"])
    assert command.endswith('"')
    manifest = tmp_path / "doc" / "harness" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"verify_commands:\n  - {command}\n", encoding="utf-8")
    proc = subprocess.run([sys.executable, str(RUNNER_PATH), "--root", str(tmp_path), "--json"],
                          cwd=tmp_path, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    record = json.loads(proc.stdout)["commands"][0]
    assert record["command"] == command
    assert record["stdout"].strip() == "plain scalar ok"


def test_manifest_command_outer_quote_pairs_remain_supported(tmp_path):
    manifest = tmp_path / "doc" / "harness" / "manifest.yaml"
    manifest.parent.mkdir(parents=True)
    for body, expected in (
        ("verify_commands:\n  - 'echo single'\n  - \"echo double\"\n", ["echo single", "echo double"]),
        ("verify_commands: 'echo scalar'\n", ["echo scalar"]),
        ("verify_commands: ['echo inline', \"echo double\"]\n", ["echo inline", "echo double"]),
    ):
        manifest.write_text(body, encoding="utf-8")
        assert verify_runner._read_verify_commands(tmp_path) == expected


def test_parallel_runner_aggregates_failure():
    commands = ["python3 -c 'print(\"ok\")'", "python3 -c 'raise SystemExit(7)'"]
    if os.name == "nt":
        commands = [subprocess.list2cmdline([sys.executable, "-c", code])
                    for code in ("print('ok')", "raise SystemExit(7)")]
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / ".git").mkdir()
        payload = verify_runner.run(
            commands,
            root,
            parallel=True,
            max_workers=2,
            timeout=5,
        )
    assert payload["returncode"] == 1
    assert payload["status"] == "FAIL"
    assert payload["commands"][0]["returncode"] == 0
    assert payload["commands"][1]["returncode"] == 7
