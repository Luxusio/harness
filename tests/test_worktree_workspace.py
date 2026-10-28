"""MCP ``workspace`` routing for tasks that live in a linked git worktree.

A ``harness:batch`` lead runs in ``<repo>/.claude/worktrees/<name>`` while the
MCP server's cwd stays the main checkout. These tests pin that task paths and
focus markers follow a validated ``workspace`` while session identity stays on
the control root, and that session source focus follows the worktree task.
See doc/harness/REQ__parallel-tasks-via-worktree-leads.md.
"""
from __future__ import annotations

def call_execution(*args, **kwargs):
    from test_harness_mcp_server import call_execution as execute
    return execute(*args, **kwargs)

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest import mock


REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "plugin" / "mcp" / "harness_server.py"
SCRIPTS = REPO_ROOT / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))

# Reuse an existing instance: the control-writer authority binds once per process.
if "harness_server" in sys.modules:
    harness_server = sys.modules["harness_server"]
else:
    spec = importlib.util.spec_from_file_location("harness_server", SERVER_PATH)
    assert spec and spec.loader
    harness_server = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = harness_server
    spec.loader.exec_module(harness_server)
import _lib as lib  # type: ignore  # noqa: E402

SID = "b1c2d3e4-5f60-7182-93a4-b5c6d7e8f901"
MANIFEST = "version: 5\nsource_git_roots: []\n"
TASKS = "doc/harness/tasks"

WORKTREE_PLAN = "# Plan\n\n```wbs\n" + json.dumps([{
    "id": "A", "title": "Worktree contains its own manifest", "weight": 1,
    "check": [sys.executable, "-c", "from pathlib import Path; assert Path('doc/harness/manifest.yaml').is_file()"],
}]) + "\n```\n"


def _git(cwd, *args):
    subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
        env={k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
    )


def _repo(path: Path) -> Path:
    path.mkdir(parents=True)
    root = Path(os.path.realpath(path))
    _git(root, "init", "-q", "-b", "master")
    _git(root, "config", "user.email", "t@example.com")
    _git(root, "config", "user.name", "t")
    (root / "doc/harness").mkdir(parents=True)
    (root / "doc/harness/manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    (root / ".gitignore").write_text("doc/harness/tasks/\n.claude/worktrees/\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


def _worktree(main: Path, name: str) -> Path:
    wt = main / ".claude/worktrees" / name
    _git(main, "worktree", "add", "-q", "-b", f"worktree-{name}", str(wt))
    return Path(os.path.realpath(wt))


def _setup(tmp_path):
    main = _repo(tmp_path / "main")
    wt = _worktree(main, "lead")
    lib.write_session_hint(str(main), SID)
    return main, wt


def _call(main: Path, name: str, args: dict) -> dict:
    with mock.patch.object(harness_server, "find_repo_root", return_value=str(main)):
        return call_execution(harness_server, name, args)


def _payload(result: dict) -> dict:
    return json.loads(result["content"][0]["text"])


def _markers(root: Path) -> list[str]:
    d = root / TASKS / ".active_sessions"
    return sorted(p.name for p in d.iterdir() if p.suffix == ".json") if d.is_dir() else []


def _snapshot(root: Path) -> dict:
    base = root / TASKS
    return {
        str(p.relative_to(base)): p.read_bytes()
        for p in sorted(base.rglob("*")) if p.is_file() and ".lock" not in p.name
    } if base.is_dir() else {}


def test_task_start_with_workspace_creates_task_and_marker_in_worktree(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    result = _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not result.get("isError"), result
    data = _payload(result)
    assert data["task_dir"] == str(wt / TASKS / "TASK__lead")
    # Session identity comes from the control root's hint, never "default".
    assert _markers(wt) == [f"{SID}.json"]
    assert not (main / TASKS / "TASK__lead").exists()
    bound = lib.resolve_session_task_binding(str(wt), SID)
    assert bound["task_dir"] == data["task_dir"]


def test_followup_tools_route_by_workspace_and_miss_without_it(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)})
    plan = _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": WORKTREE_PLAN, "required_lenses": ["review-code", "qa-cli"],
    })
    assert not plan.get("isError"), plan
    assert (wt / TASKS / "TASK__lead/PLAN.md").is_file()
    ctx = _call(main, "task_context", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not ctx.get("isError"), ctx
    verify = _call(main, "task_verify", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not verify.get("isError"), verify
    # Forgetting workspace looks in the main checkout, where the task does not exist.
    missing = _call(main, "task_context", {"task_id": "TASK__lead"})
    assert missing.get("isError")


def test_workspace_equal_to_control_root_is_omitted(tmp_path):
    repo = _setup(tmp_path)
    main, _wt = repo
    result = _call(main, "task_start", {"task_id": "TASK__main", "workspace": str(main)})
    assert not result.get("isError"), result
    assert (main / TASKS / "TASK__main/TASK.json").is_file()


def _refused(main: Path, workspace: str, target: Path | None = None) -> None:
    before = _snapshot(target) if target else None
    result = _call(main, "task_start", {"task_id": "TASK__x", "workspace": workspace})
    assert result.get("isError"), result
    data = _payload(result)
    assert data.get("error_code") == "WORKSPACE_NOT_REGISTERED_WORKTREE", data
    assert not (main / TASKS / "TASK__x").exists()
    if target is not None:
        assert _snapshot(target) == before
        assert not (target / TASKS / "TASK__x").exists()


def test_refuses_plain_directory(tmp_path):
    repo = _setup(tmp_path)
    main, _ = repo
    plain = tmp_path / "plain"
    (plain / "doc/harness").mkdir(parents=True)
    (plain / "doc/harness/manifest.yaml").write_text(MANIFEST, encoding="utf-8")
    _refused(main, os.path.realpath(plain), Path(os.path.realpath(plain)))


def test_refuses_other_repository_and_its_worktree(tmp_path):
    repo = _setup(tmp_path)
    main, _ = repo
    other = _repo(tmp_path / "other")
    other_wt = _worktree(other, "x")
    _refused(main, str(other), other)
    _refused(main, str(other_wt), other_wt)


def test_refuses_relative_and_symlinked_paths(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    _refused(main, os.path.relpath(wt))
    link = tmp_path / "link"
    link.symlink_to(wt)
    _refused(main, str(link))


def test_refuses_forged_back_pointer(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    forged = _worktree(main, "forged")
    # Point the registration's back-pointer at another checkout.
    (main / ".git/worktrees/forged/gitdir").write_text(str(wt / ".git") + "\n", encoding="utf-8")
    _refused(main, str(forged), forged)


def test_refuses_worktree_without_manifest(tmp_path):
    repo = _setup(tmp_path)
    main, _ = repo
    bare = _worktree(main, "bare")
    (bare / "doc/harness/manifest.yaml").unlink()
    _refused(main, str(bare))


def test_task_blocked_on_worktree_task_leaves_main_markers_untouched(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    before = {p: (main / TASKS / ".active_sessions" / p).read_bytes() for p in _markers(main)}
    legacy = (main / TASKS / ".active").read_bytes()
    result = _call(main, "task_blocked", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "blocked_reason": "probe", "unblock_condition": "probe",
    })
    assert not result.get("isError"), result
    assert (wt / TASKS / "TASK__lead/BLOCKED.md").is_file()
    assert {p: (main / TASKS / ".active_sessions" / p).read_bytes() for p in _markers(main)} == before
    assert (main / TASKS / ".active").read_bytes() == legacy
    assert lib.resolve_session_task_binding(str(wt), SID) == {}


def test_routing_reads_the_worktree_manifest(tmp_path, monkeypatch):
    repo = _setup(tmp_path)
    main, wt = repo
    _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)})
    (wt / "doc/harness/manifest.yaml").write_text(MANIFEST + "maintenance_default: true\n", encoding="utf-8")
    monkeypatch.chdir(main)
    routing = lib.compile_routing(str(wt / TASKS / "TASK__lead"))
    assert routing["maintenance_task"] is True
    assert lib.compile_routing(str(wt / TASKS / "TASK__lead"), str(main))["maintenance_task"] is False


def _hook(script: str, payload: dict, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env["CLAUDE_PLUGIN_ROOT"] = str(REPO_ROOT / "plugin")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("HARNESS_SKIP_PREWRITE", None)
    return subprocess.run(
        [sys.executable, str(SCRIPTS / script), *args], input=json.dumps(payload),
        capture_output=True, text=True, cwd=str(cwd), env=env, timeout=30,
    )


def _gate(wt: Path, file_path: Path) -> str | None:
    result = _hook("prewrite_gate.py", {
        "hook_event_name": "PreToolUse", "session_id": SID, "cwd": str(wt),
        "tool_name": "Write", "tool_input": {"file_path": str(file_path), "content": "x = 1\n"},
    }, wt)
    if not result.stdout.strip():
        return None
    return (json.loads(result.stdout).get("hookSpecificOutput") or {}).get("permissionDecision")


def test_worktree_plan_alone_does_not_admit_source_writes(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    source = wt / "src/feature.py"
    assert _gate(wt, source) == "deny"
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": WORKTREE_PLAN, "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    assert _gate(wt, source) == "deny"


def test_full_lifecycle_closes_in_worktree_and_leaves_main_untouched(tmp_path):
    repo = _setup(tmp_path)

    main, wt = repo
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    main_before = _snapshot(main)
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": WORKTREE_PLAN, "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    lead_task = str(wt / TASKS / "TASK__lead")
    assert not _call(main, "wbs", {"action": "check", "wbs_id": "TASK__lead", "workspace": str(wt), "node_id": "A"}).get("isError")
    verify = _payload(_call(main, "task_verify", {"task_id": "TASK__lead", "workspace": str(wt)}))
    assert verify["runtime_verdict"] == "PASS", verify
    closed = _call(main, "task_close", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not closed.get("isError"), closed
    assert lib.task_control_status(lead_task, lib.read_task_control(lead_task)) == "closed"
    assert lib.resolve_session_task_binding(str(wt), SID) == {}
    # The main checkout's task, markers, and goal state are untouched.
    assert _snapshot(main) == main_before
    assert not (main / "doc/harness/goals").exists()


def test_codex_runtime_routes_workspace_without_authenticated_native_binding(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    with mock.patch.dict(os.environ, {"HARNESS_RUNTIME": "codex", "CODEX_THREAD_ID": SID}):
        result = _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not result.get("isError"), result
    data = _payload(result)
    assert data["workspace"] == str(wt)
    assert data["task_dir"] == str(wt / TASKS / "TASK__lead")
    assert not lib.resolve_session_task_binding(str(wt), SID)
    assert not (main / TASKS / "TASK__lead").exists()
    with mock.patch.dict(os.environ, {"HARNESS_RUNTIME": "codex", "CODEX_THREAD_ID": SID}):
        context = _payload(_call(main, "task_context", {"task_id": "TASK__lead", "workspace": str(wt)}))
    assert context["workspace"] == str(wt)
    assert not lib.resolve_session_task_binding(str(wt), SID)


def test_codex_workspace_without_process_identity_does_not_promote_repo_hint(tmp_path, monkeypatch):
    main, wt = _setup(tmp_path)
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.delenv("CODEX_SESSION_ID", raising=False)
    for tool in ("task_start", "task_context"):
        result = _call(main, tool, {"task_id": "TASK__lead", "workspace": str(wt)})
        assert not result.get("isError"), result
        assert _payload(result)["workspace"] == str(wt)
        assert not lib.resolve_session_task_binding(str(wt), SID)
        assert f"{SID}.json" not in _markers(wt)


def test_non_string_workspace_is_refused_before_resolution(tmp_path):
    repo = _setup(tmp_path)
    main, _wt = repo
    result = _call(main, "task_start", {"task_id": "TASK__lead", "workspace": ["/tmp"]})
    assert result.get("isError"), result
    data = _payload(result)
    assert data.get("field") == "workspace" and data.get("reason") == "wrong_type", data
    assert not (main / TASKS / "TASK__lead").exists()


def test_lead_close_never_touches_the_coordinator_goal(tmp_path):
    repo = _setup(tmp_path)
    """A lead's worktree has no Goal store; closing it leaves the main Goal alone."""

    main, wt = repo
    goal_dir = main / "doc/harness/goals"
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "GOAL__old.json").write_text('{"historical":true}\n')
    goal_before = (main / "doc/harness/goals").glob("*.json")
    goal_before = {p.name: p.read_bytes() for p in goal_before}
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": WORKTREE_PLAN, "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    lead_task = str(wt / TASKS / "TASK__lead")
    assert not _call(main, "wbs", {"action": "check", "wbs_id": "TASK__lead", "workspace": str(wt), "node_id": "A"}).get("isError")
    assert not _call(main, "task_close", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    goal_after = {p.name: p.read_bytes() for p in (main / "doc/harness/goals").glob("*.json")}
    assert goal_after == goal_before




def test_codex_task_results_fence_conflicts_across_registered_workspaces(tmp_path, monkeypatch):
    import codex_hook_registration as binding
    main, wt = _setup(tmp_path)
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    sid = "019f825b-f25f-70c3-8ee8-071f79fa1c42"
    def result(root, name, workspace=None):
        task = root / TASKS / name
        control = lib.read_task_control(str(task))
        args = {"action": "context", "wbs_id": name}
        response = {"task_dir": str(task), "task_id": name, "run_id": control["run_id"]}
        if workspace:
            args["workspace"] = str(workspace)
            response["workspace"] = str(workspace)
        return json.dumps({"cwd": str(main), "session_id": sid, "tool_name": "wbs",
                           "tool_input": args, "tool_response": {"structuredContent": response}}).encode()
    assert binding.register_task_result(result(main, "TASK__main"))
    assert not binding.register_task_result(result(wt, "TASK__lead", wt))
    assert not lib.resolve_session_task_binding(str(main), sid)
    assert not lib.resolve_session_task_binding(str(wt), sid)
    for root in (main, wt):
        assert len(lib.read_active_session_marker(str(root), sid)["conflicts"]) == 2
    assert not _call(main, "task_blocked", {"task_id": "TASK__main",
                    "blocked_reason": "separate work parked", "unblock_condition": "resume explicitly"}).get("isError")
    assert binding.register_task_result(result(wt, "TASK__lead", wt))
    assert lib.resolve_session_task_binding(str(wt), sid)["task_dir"] == str(wt / TASKS / "TASK__lead")
    assert not lib.resolve_session_task_binding(str(main), sid)
