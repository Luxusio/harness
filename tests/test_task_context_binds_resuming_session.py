"""Resuming a task binds source focus without changing task identity or state."""
from __future__ import annotations

def call_execution(*args, **kwargs):
    from test_harness_mcp_server import call_execution as execute
    return execute(*args, **kwargs)

import importlib.util
import json
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

from conftest import REPO_ROOT, SCRIPTS_DIR  # type: ignore

sys.path.insert(0, SCRIPTS_DIR)
import _lib  # noqa: E402

SESSION_A = "a1b2c3d4-5e6f-7890-abcd-ef1234567890"
SESSION_B = "d1866f9e-1d00-4179-91f8-dc676f461d3e"
TASK_ID = "TASK__resume"


def _server():
    """Reuse any existing instance: the control-writer authority binds once."""
    if "harness_server" in sys.modules:
        return sys.modules["harness_server"]
    path = os.path.join(REPO_ROOT, "plugin", "mcp", "harness_server.py")
    spec = importlib.util.spec_from_file_location("harness_server", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _marker_path(repo: str, session_id: str) -> Path:
    """The session marker file `write_active_marker` would create for `sid`."""
    return Path(_lib._session_active_path(repo, session_id))


def _seed_open_task_and_focus(repo: str, task_id: str, session_id: str) -> str:
    """Create the second-open-task fixture without exercising task_start focus policy."""
    task_dir = Path(repo) / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True)
    run_id = _lib.new_uuid7()
    (task_dir / "TASK.json").write_text(json.dumps({
        "run_id": run_id,
        "execution_mode": "standard",
        "required_lenses": ["review-code", "qa-cli"],
        "closed": False,
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (task_dir / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    marker = _marker_path(repo, session_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "session_id": session_id,
        "task_dir": str(task_dir),
        "task_id": task_id,
        "run_id": run_id,
        "updated": _lib.now_iso(),
    }, sort_keys=True) + "\n", encoding="utf-8")
    (Path(repo) / "doc/harness/tasks/.active").write_text(
        str(task_dir) + "\n", encoding="utf-8",
    )
    return str(task_dir)


def _open_task(tmp_path: Path, session_id: str | None) -> tuple[str, str]:
    """Open a real task for `session_id` through the MCP surface.

    `session_id=None` is the Codex shape: nothing outside the Claude
    `UserPromptSubmit` hook writes `.session-hint`, so the marker is keyed on
    `current_session_id()` — `default` — instead.
    """
    repo = tmp_path / "repo"
    (repo / ".git").mkdir(parents=True)
    (repo / "doc/harness").mkdir(parents=True)
    (repo / "doc/harness/manifest.yaml").write_text("type: test\n", encoding="utf-8")
    server = _server()
    if session_id is not None:
        assert _lib.write_session_hint(str(repo), session_id)
    with mock.patch.object(server, "find_repo_root", return_value=str(repo)):
        result = call_execution(server, "task_start", {"task_id": TASK_ID})
    assert "isError" not in result, result
    return str(repo), str(repo / "doc/harness/tasks" / TASK_ID)


def test_resume_binds_new_session_without_changing_control(tmp_path):
    server = _server()
    repo, task_dir = _open_task(tmp_path, SESSION_A)
    control_path = Path(task_dir) / "TASK.json"
    before = control_path.read_bytes()
    _lib.write_session_hint(repo, SESSION_B)
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        context = call_execution(server, "task_context", {"task_id": TASK_ID})
    assert "isError" not in context, context
    assert control_path.read_bytes() == before
    for sid in (SESSION_A, SESSION_B):
        bound = _lib.resolve_session_task_binding(repo, sid)
        assert bound.get("task_dir") == task_dir
        assert bound.get("run_id") == _lib.read_task_control(task_dir)["run_id"]
    assert not (Path(task_dir) / "RECEIPTS.jsonl").exists()


def test_reading_a_parked_task_does_not_steal_write_focus(tmp_path):
    """A parked task takes no marker even when nothing else holds focus.

    This is the only case that reaches the status guard, and it reaches it
    only because there is no legacy pointer. That is not staged: the park below
    runs through the real `task_blocked`, which calls `clear_active_marker`,
    which unlinks `.active` whenever it names the task being parked — no
    session condition, and `strict=True` raises if the pointer survives. So
    "parked task, no pointer left behind" is the state the field produces.
    Leaving any competing pointer in place makes the session guard answer first
    and the status check unreachable — an earlier version of this fixture did
    exactly that and stayed green with the status conjunct deleted, which is
    the failure mode the REQ names one level up.

    Reading such a task must write nothing at all: `write_active_marker` also
    recreates the single-valued legacy `.active` that the stop gate reads, so
    a bare read would hand write focus to a task nobody is working.
    """
    server = _server()
    repo, parked = _open_task(tmp_path, SESSION_A)
    active = Path(repo) / "doc/harness/tasks/.active"
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        assert "isError" not in call_execution(server, "task_blocked", {
            "task_id": TASK_ID,
            "blocked_reason": "external service unavailable",
            "unblock_condition": "service reachable again",
        })
    assert _lib.task_control_status(parked, _lib.read_task_control(parked)) == "blocked"
    assert not active.exists()  # clear_active_marker unlinked it, not the test

    _lib.write_session_hint(repo, SESSION_B)
    assert _lib.resolve_active_task_dir(repo, session_id=SESSION_B) == ""
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        context = call_execution(server, "task_context", {"task_id": TASK_ID})
    assert "isError" not in context, context
    assert not _marker_path(repo, SESSION_B).exists()
    assert not active.exists()


def test_a_session_holding_nothing_at_all_binds_on_resume(tmp_path):
    """The unbound branch itself, with neither marker present.

    Every other resume case here reaches `write_active_marker` through the
    already-names-this-task branch, because `.active` still points at the task
    being resumed. It stops pointing anywhere the moment that task is parked
    or closed — both call `clear_active_marker`, which unlinks the legacy file
    when it names them — and a session that never called `task_start` has no
    marker of its own. A
    guard that treats "holding nothing" as "holding something else" refuses
    exactly the resume this REQ exists to make work, and no other test here
    would notice.
    """
    server = _server()
    repo, task_dir = _open_task(tmp_path, SESSION_A)
    Path(repo, "doc/harness/tasks/.active").unlink()
    _lib.write_session_hint(repo, SESSION_B)
    assert _lib.resolve_active_task_dir(repo, session_id=SESSION_B) == ""

    with mock.patch.object(server, "find_repo_root", return_value=repo):
        assert "isError" not in call_execution(server, "task_context", {"task_id": TASK_ID})

    bound = _lib.resolve_session_task_binding(repo, SESSION_B)
    assert os.path.realpath(str(bound.get("task_dir") or "")) == os.path.realpath(
        task_dir
    )


def test_a_refused_marker_write_is_reported_not_swallowed(tmp_path):
    """An `except Exception: pass` here would restore the silent failure.

    Task-focus binding failures must be observable. A
    control-writer refusal means no session can ever be bound, which is a
    defect in the runtime, not a degraded-context nicety worth hiding.
    """
    server = _server()
    repo, _ = _open_task(tmp_path, SESSION_A)
    _lib.write_session_hint(repo, SESSION_B)
    refusal = PermissionError("active task binding requires the task-control runtime")
    with mock.patch.object(server, "find_repo_root", return_value=repo), \
            mock.patch.object(server, "write_active_marker", side_effect=refusal):
        context = call_execution(server, "task_context", {"task_id": TASK_ID})
    assert context.get("isError") is True, context
    assert "task-control runtime" in str(context)


def test_reading_another_open_task_does_not_steal_write_focus(tmp_path, monkeypatch):
    """The same theft, from the direction the status guard could not see.

    C-09 refuses a second task only for the session whose focus it would
    take; it closes nothing, and other sessions hold their own focus, so
    several tasks are open at any time. Gating the marker write on "the task
    being read is open" therefore let a pure `task_context` read of any other
    open task rebind this session and repoint the shared legacy `.active` —
    the next source write could target the peeked task instead of the active one.

    The peeking session holds **no marker of its own** — a fresh session that
    has never called `task_start`, which is precisely the shape this feature
    targets. That is what forces the guard through the legacy `.active`
    fallback: give the peeker its own marker and the same-session branch
    answers alone, the fallback is never reached, and deleting it leaves this
    test green while the theft returns.
    """
    server = _server()
    repo, old_task = _open_task(tmp_path, SESSION_A)
    live = _seed_open_task_and_focus(repo, "TASK__live", SESSION_A)
    active = Path(repo) / "doc/harness/tasks/.active"
    assert active.read_text(encoding="utf-8").strip() == live
    assert _lib.task_control_status(old_task, _lib.read_task_control(old_task)) == "open"

    _lib.write_session_hint(repo, SESSION_B)
    assert not _marker_path(repo, SESSION_B).exists()
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        context = call_execution(server, "task_context", {"task_id": TASK_ID})
    assert "isError" not in context, context

    # The peek changed nothing: focus, binding, and the legacy marker all hold.
    assert active.read_text(encoding="utf-8").strip() == live
    assert not _marker_path(repo, SESSION_B).exists()
    bound = _lib.resolve_session_task_binding(repo, SESSION_A)
    assert os.path.realpath(str(bound.get("task_dir") or "")) == os.path.realpath(live)


def _force_hintless_default_session(monkeypatch):
    """Make `current_session_id()` resolve to `default`, as it does on Codex."""
    for name in (
        "HARNESS_SESSION_ID", "CODEX_SESSION_ID",
        "CODEX_THREAD_ID", "CLAUDE_SESSION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(_lib, "_LAST_HOOK_INPUT", {}, raising=False)
    assert _lib.current_session_id() == "default"


def test_a_peek_without_a_session_hint_does_not_steal_write_focus(
    tmp_path, monkeypatch,
):
    """The whole class the hint-keyed tests could not see.

    Every other case here writes `.session-hint` first. Only
    `prompt_memory.py` does that, registered as the Claude `UserPromptSubmit`
    hook, so on Codex the hint is permanently empty — and the first version of
    the guard asked `resolve_session_task_binding` about that empty id while
    the write fell back to `current_session_id()`. An empty id and `default`
    both resolve to no binding, so the guard answered "unbound" for every
    Codex session and a single `task_context` peek repointed the marker and the
    legacy `.active` at the peeked task.

    The default marker must continue to name the focused task until an
    explicit open or trusted task-result binding changes that focus.
    """
    _force_hintless_default_session(monkeypatch)
    server = _server()
    repo, peeked = _open_task(tmp_path, None)
    live = _seed_open_task_and_focus(repo, "TASK__live", "default")
    active = Path(repo) / "doc/harness/tasks/.active"

    # Two open tasks, no hint, and focus on the one that was started last.
    assert _lib.read_session_hint(repo) == ""
    assert _lib.task_control_status(peeked, _lib.read_task_control(peeked)) == "open"
    assert active.read_text(encoding="utf-8").strip() == live
    assert _lib.resolve_active_task_dir(repo, session_id="default") == live

    with mock.patch.object(server, "find_repo_root", return_value=repo):
        context = call_execution(server, "task_context", {"task_id": TASK_ID})
    assert "isError" not in context, context

    # Both markers still name the live task — including the one the Codex
    # pre-spawn hook promotes to the rollout thread id.
    assert active.read_text(encoding="utf-8").strip() == live
    assert _lib.resolve_active_task_dir(repo, session_id="default") == live


def test_resuming_the_same_task_refreshes_a_rotated_run_id(tmp_path):
    """Why the same-task branch is kept rather than dropped.

    An explicit fresh `task_start` from a second session rotates `run_id`, which
    leaves the first session's marker naming the right task with the wrong run.
    `resolve_session_task_binding` rejects exactly that mismatch, so the first
    session is unbound for source ownership while looking bound to other
    readers. Re-writing its own marker here repairs the exact run binding.
    """
    server = _server()
    repo, task_dir = _open_task(tmp_path, SESSION_A)
    first_run = _lib.read_task_control(task_dir)["run_id"]

    _lib.write_session_hint(repo, SESSION_B)
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        assert "isError" not in call_execution(server,
            "task_start", {"task_id": TASK_ID, "fresh_run": True},
        )
    second_run = _lib.read_task_control(task_dir)["run_id"]
    assert second_run != first_run
    _lib.write_session_hint(repo, SESSION_A)
    assert _lib.resolve_session_task_binding(repo, SESSION_A) == {}

    with mock.patch.object(server, "find_repo_root", return_value=repo):
        assert "isError" not in call_execution(server, "task_context", {"task_id": TASK_ID})

    bound = _lib.resolve_session_task_binding(repo, SESSION_A)
    assert bound.get("run_id") == second_run
    assert os.path.realpath(str(bound.get("task_dir") or "")) == os.path.realpath(
        task_dir
    )


def test_a_legacy_pointer_to_an_unvalidatable_task_does_not_block_a_resume(tmp_path):
    """The guard must refuse theft, not refuse binding.

    Parking and closing both call `clear_active_marker`, which unlinks the
    legacy `.active` whenever it names the task leaving `open` — measured for
    both a same-session and a cross-session park — so neither can strand a
    pointer here. What does is a pointer whose task no longer *validates*:
    `task_control_status` answers `invalid` when the control cannot be read,
    and nothing clears the pointer because nothing announced the transition.
    Task directories are gitignored ephemeral artifacts, so a task removed from
    `doc/harness/tasks/` leaves its sibling `.active` behind, and so does a
    truncated or unreadable `TASK.json`.

    `resolve_active_task_dir` returns such a path anyway — the legacy fallback
    is deliberately taken with `require_live_state=False`. A task whose control
    does not validate holds no write focus, so treating its pointer as a
    competing binding would lock every markerless session out of the very
    resume this REQ exists to make work, permanently and with no route back
    except a safe `task_start`/`task_context` rebind.
    """
    server = _server()
    repo, task_dir = _open_task(tmp_path, SESSION_A)
    stale = Path(_seed_open_task_and_focus(repo, "TASK__gone", SESSION_A))
    active = Path(repo) / "doc/harness/tasks/.active"
    assert active.read_text(encoding="utf-8").strip() == str(stale)
    shutil.rmtree(stale)  # ephemeral task tree pruned; the pointer survives it
    assert _lib.task_control_status(str(stale), _lib.read_task_control(str(stale))) == (
        "invalid"
    )
    assert _lib.resolve_active_task_dir(repo, session_id=SESSION_B) == str(stale)

    _lib.write_session_hint(repo, SESSION_B)
    with mock.patch.object(server, "find_repo_root", return_value=repo):
        assert "isError" not in call_execution(server, "task_context", {"task_id": TASK_ID})

    bound = _lib.resolve_session_task_binding(repo, SESSION_B)
    assert os.path.realpath(str(bound.get("task_dir") or "")) == os.path.realpath(
        task_dir
    )
