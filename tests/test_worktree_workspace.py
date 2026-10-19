"""MCP ``workspace`` routing for tasks that live in a linked git worktree.

A ``harness:batch`` lead runs in ``<repo>/.claude/worktrees/<name>`` while the
MCP server's cwd stays the main checkout. These tests pin that task paths and
focus markers follow a validated ``workspace`` while session identity stays on
the control root, and that hooks bind receipts to the worktree task.
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
        "plan": "# Plan\n\nSmall.\n", "required_lenses": ["review-code", "qa-cli"],
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


def _receipts(task_dir: Path) -> list[dict]:
    path = task_dir / lib.RECEIPTS_NAME
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_nested_reviewer_start_in_worktree_binds_to_worktree_task(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    # The main checkout holds a live marker for the same session id.
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    main_task = main / TASKS / "TASK__main"
    lead_task = wt / TASKS / "TASK__lead"
    before_main = _receipts(main_task)
    result = _hook("background_hook.py", {
        "hook_event_name": "SubagentStart", "session_id": SID,
        "agent_id": "a0123456789abcdef", "agent_type": "harness:code-reviewer",
        "cwd": str(wt),
    }, main, "--event", "start")
    assert result.returncode == 0, result.stderr
    started = [r for r in _receipts(lead_task) if r.get("event") == "started"]
    assert [r.get("agent_id") for r in started] == ["a0123456789abcdef"]
    assert started[0]["task_run_id"] == lib.read_task_control(str(lead_task))["run_id"]
    assert _receipts(main_task) == before_main


def _gate(wt: Path, file_path: Path) -> str | None:
    result = _hook("prewrite_gate.py", {
        "hook_event_name": "PreToolUse", "session_id": SID, "cwd": str(wt),
        "tool_name": "Write", "tool_input": {"file_path": str(file_path), "content": "x = 1\n"},
    }, wt)
    if not result.stdout.strip():
        return None
    return (json.loads(result.stdout).get("hookSpecificOutput") or {}).get("permissionDecision")


def test_prewrite_gate_in_worktree_follows_the_worktree_plan(tmp_path):
    repo = _setup(tmp_path)
    main, wt = repo
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    source = wt / "src/feature.py"
    assert _gate(wt, source) == "deny"
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": "# Plan\n\nSmall.\n", "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    assert _gate(wt, source) in (None, "allow")


def test_full_lifecycle_closes_in_worktree_and_leaves_main_untouched(tmp_path):
    repo = _setup(tmp_path)
    from test_harness_mcp_server import _record_receipt_fixture

    main, wt = repo
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    main_before = _snapshot(main)
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": "# Plan\n\nSmall.\n", "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    lead_task = str(wt / TASKS / "TASK__lead")
    for payload in (
        {"agent_id": "review-1", "agent_type": "harness:code-reviewer", "event": "started"},
        {"agent_id": "review-1", "agent_type": "harness:code-reviewer", "event": "completed",
         "verdict": "PASS",
         "summary": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"},
        {"agent_id": "qa-1", "agent_type": "harness:qa-cli", "event": "started"},
        {"agent_id": "qa-1", "agent_type": "harness:qa-cli", "event": "completed",
         "verdict": "PASS", "summary": "VERDICT: PASS"},
    ):
        _record_receipt_fixture(lead_task, {
            **payload, "source": "claude_hook",
            "runtime_id": f"claude:{SID}:{payload['agent_id']}",
        })
    verify = _payload(_call(main, "task_verify", {"task_id": "TASK__lead", "workspace": str(wt)}))
    assert verify["runtime_verdict"] == "PASS", verify
    closed = _call(main, "task_close", {"task_id": "TASK__lead", "workspace": str(wt)})
    assert not closed.get("isError"), closed
    assert lib.task_control_status(lead_task, lib.read_task_control(lead_task)) == "closed"
    assert lib.resolve_session_task_binding(str(wt), SID) == {}
    # The main checkout's task, markers, and goal state are untouched.
    assert _snapshot(main) == main_before
    assert not (main / "doc/harness/goals").exists()


def test_codex_runtime_routes_workspace_without_eager_native_binding(tmp_path):
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
    from test_harness_mcp_server import _record_receipt_fixture

    main, wt = repo
    goal_dir = main / "doc/harness/goals"
    goal_dir.mkdir(parents=True, exist_ok=True)
    (goal_dir / "GOAL__old.json").write_text('{"historical":true}\n')
    goal_before = (main / "doc/harness/goals").glob("*.json")
    goal_before = {p.name: p.read_bytes() for p in goal_before}
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    assert not _call(main, "write_plan", {
        "task_id": "TASK__lead", "workspace": str(wt),
        "plan": "# Plan\n\nSmall.\n", "required_lenses": ["review-code", "qa-cli"],
    }).get("isError")
    lead_task = str(wt / TASKS / "TASK__lead")
    for agent, kind, summary in (
        ("review-1", "harness:code-reviewer",
         "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"),
        ("qa-1", "harness:qa-cli", "VERDICT: PASS"),
    ):
        for event in ("started", "completed"):
            _record_receipt_fixture(lead_task, {
                "agent_id": agent, "agent_type": kind, "event": event,
                "verdict": "PASS" if event == "completed" else "",
                "summary": summary if event == "completed" else "",
                "source": "claude_hook", "runtime_id": f"claude:{SID}:{agent}",
            })
    assert not _call(main, "task_close", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    goal_after = {p.name: p.read_bytes() for p in (main / "doc/harness/goals").glob("*.json")}
    assert goal_after == goal_before


def test_nested_lens_start_and_stop_in_worktree_bind_to_worktree_task(tmp_path, monkeypatch):
    """AC-003: the completion receipt (what grants PASS) binds to the worktree task."""
    from test_subagent_lifecycle import _stop_payload, _transcript

    main, wt = _setup(tmp_path)
    assert not _call(main, "task_start", {"task_id": "TASK__main"}).get("isError")
    assert not _call(main, "task_start", {"task_id": "TASK__lead", "workspace": str(wt)}).get("isError")
    main_task = main / TASKS / "TASK__main"
    lead_task = wt / TASKS / "TASK__lead"
    before_main = _receipts(main_task)
    agent_id, agent_type = "a00112233445566ff", "harness:qa-cli"
    final = "VERDICT: PASS\nchecks passed"
    start = _hook("background_hook.py", {
        "hook_event_name": "SubagentStart", "session_id": SID,
        "agent_id": agent_id, "agent_type": agent_type, "cwd": str(wt),
    }, main, "--event", "start")
    assert start.returncode == 0, start.stderr
    # _transcript sets CLAUDE_CONFIG_DIR in os.environ; _hook passes it through.
    transcript = _transcript(
        tmp_path, monkeypatch, str(lead_task), SID, agent_id, final, agent_type=agent_type,
    )
    stop = _hook("background_hook.py", {
        **_stop_payload(SID, agent_id, agent_type, transcript, final), "cwd": str(wt),
    }, main, "--event", "stop")
    assert stop.returncode == 0, stop.stderr
    rows = [(r["event"], r["verdict"], r["runtime_id"]) for r in _receipts(lead_task)]
    assert rows == [
        ("started", "", f"claude:{SID}:{agent_id}"),
        ("completed", "PASS", f"claude:{SID}:{agent_id}"),
    ]
    assert _receipts(main_task) == before_main
