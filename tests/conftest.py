"""Shared test fixtures for harness gate tests.

Exports:
  REPO_ROOT            — repo root (directory containing this file's parent)
  SCRIPTS_DIR          — plugin/scripts absolute path
  invoke_hook(...)     — subprocess runner for a hook script with stdin JSON
  scratch_task_in_real_repo(...) — context manager creating a scratch task dir
                         with clean finally removal (no leaks on exception)
  install_trees_lose_no_files — session-wide guard: a run may add to an
                         installed harness runtime, never remove from one
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import shutil
import subprocess
import sys
import uuid

import pytest


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "plugin", "scripts")


def _default_install_tree_roots() -> list[str]:
    """The installed runtime trees a suite run must never remove files from.

    The home directory comes from the password database rather than
    `Path.home()` for the same reason `install._reject_real_install_root_under_test`
    does: a test that repoints `HOME` at `tmp_path` is the isolated case, and
    `Path.home()` would then report that tmp home as the real one.

    Three roots, not two. `~/.codex/plugins/cache/harness/harness` is the tree
    Codex actually loads — `install.py` registers its hooks with absolute
    commands into that directory and prunes bytecode inside it — so the
    `rmtree(cache.parent)` class of mutation that started this guard would
    destroy it while the other two roots stayed intact. Only the harness
    marketplace/plugin subtree is watched; sibling marketplaces under
    `~/.codex/plugins/cache` are other tools' trees, and walking them costs more
    than it protects. Measured 2026-09-09: 521 files, ~0.1s per snapshot.
    """
    try:
        import pwd

        home = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # no password database — nothing to protect
        return []
    return [
        os.path.join(home, ".claude", "harness-dev"),
        os.path.join(home, ".codex", "harness"),
        os.path.join(home, ".codex", "plugins", "cache", "harness", "harness"),
    ]


# Read at fixture time, so the guard's own test can point it at a stand-in tree
# (tests/test_install_tree_removal_guard.py).
_INSTALL_TREE_ROOTS = _default_install_tree_roots()


def _install_tree_inventory(root: str) -> dict[str, int]:
    """Map file path -> size for one install tree; empty when it is absent.

    `__pycache__` is skipped on purpose. Live session hooks regenerate bytecode
    during a run and the installer prunes those directories outright, so
    including them would make the guard fire on normal, harmless activity — and
    a guard that cries wolf gets disabled. Every file that constitutes the
    runtime is still covered, including the `plugin/scripts` and `plugin/mcp`
    sources a `rmtree(cache.parent)` mutation actually deleted on 2026-09-09.

    Sizes are recorded so the failure message can name what was lost; only the
    *set of paths* is compared. Content and mtime deliberately are not: a hook
    rewriting a file it owns is not a removal, and comparing those produced a
    false alarm on every run when it was tried by hand.
    """
    inventory: dict[str, int] = {}
    for parent, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        for name in filenames:
            path = os.path.join(parent, name)
            try:
                inventory[path] = os.lstat(path).st_size
            except OSError:
                continue  # vanished mid-walk: never recorded, never reported
    return inventory


def _install_tree_removals(before: dict[str, dict[str, int]]) -> list[str]:
    """Paths present in `before` that no longer exist. Additions are ignored."""
    removals = []
    for root, snapshot in sorted(before.items()):
        after = _install_tree_inventory(root)
        removals.extend(
            f"{path} ({size} bytes)"
            for path, size in sorted(snapshot.items())
            if path not in after
        )
    return removals


@pytest.fixture(scope="session", autouse=True)
def install_trees_lose_no_files():
    """Fail the run if it removed a file from an installed harness runtime.

    `install._reject_real_install_root_under_test` and the `HARNESS_DEST`
    fixture below are the prevention layers; this is the detection layer behind
    them, and it must catch a removal even when both are bypassed — a test can
    reach `~/.claude/harness-dev` through any path, not only the installer.

    On 2026-09-09 a whole-suite mutation run deleted the installed
    `plugin/scripts` and `plugin/mcp` and killed receipt recording for the
    session that ran it. Six review rounds had invariant checks in place, but
    every one of them watched `doc/harness/tasks/.active_sessions/` — task
    artifacts — and none watched the tree the hooks actually execute from.
    See `doc/harness/REQ__guards-are-verified-where-they-run.md`.

    Absent trees (fresh machine, CI) inventory as empty and never fail.
    """
    before = {root: _install_tree_inventory(root) for root in _INSTALL_TREE_ROOTS}
    yield
    removed = _install_tree_removals(before)
    assert not removed, (
        "this test run removed "
        f"{len(removed)} file(s) from an installed harness runtime tree:\n  "
        + "\n  ".join(removed)
        + "\nRe-install with `python3 install.py` to repair, then bind the "
        "responsible test to a tmp install root."
    )


@pytest.fixture(autouse=True)
def restore_harness_server_global():
    """Undo `McpServer.__init__`'s assignment to the module global `_SERVER`.

    Production runs one server per process, so owning that global is correct
    there. `tests/test_harness_mcp_server.py` constructs a dozen servers and
    nothing restored the previous value, so a server built by one test stayed
    installed for every later test sharing its xdist worker. `_watcher_status`
    reads `_SERVER.runtime` and `_SERVER.watcher_manager`; a leaked
    codex-runtime server with no manager yields `manager_running is False`,
    hence `receipts_recordable is False`, and `_gate_next_action` then replaces
    `next_action` wholesale. Whether the leak reached a given test depended on
    which siblings shared its worker, so it presented as a ~20% flake rather
    than a deterministic failure.

    Lives here rather than in the test module because `test_*.py` files may not
    import pytest at the top level (`test_no_toplevel_third_party_imports`).
    A no-op for every test that never loads the MCP server: when the module is
    absent at setup the pre-test state is "no server", which is what teardown
    restores if a test loaded it.
    """
    saved = getattr(sys.modules.get("harness_server"), "_SERVER", None)
    try:
        yield
    finally:
        module = sys.modules.get("harness_server")
        if module is not None:
            module._SERVER = saved


@pytest.fixture(autouse=True)
def isolate_claude_install_root(tmp_path_factory):
    """Point the installer's default Claude root at a tmp path for every test.

    `install.py` resolves its Claude target from `HARNESS_DEST`, defaulting to
    the real `~/.claude/harness-dev`. Any test calling `install_claude`,
    `sync_claude_payload`, or `_claude_payload_state` without naming a root
    therefore operated on the live runtime. This fixture removes the default
    exposure suite-wide; `install._reject_real_install_root_under_test` is the
    backstop for roots that carry no env indirection (`CODEX_INSTALL_ROOT`).

    A test that sets `HARNESS_DEST` itself (via `monkeypatch.setenv` or a
    subprocess env) still wins: the fixture only supplies the default.
    """
    original = os.environ.get("HARNESS_DEST")
    os.environ["HARNESS_DEST"] = str(
        tmp_path_factory.mktemp("harness-dest") / "harness-dev"
    )
    try:
        yield
    finally:
        if original is None:
            os.environ.pop("HARNESS_DEST", None)
        else:
            os.environ["HARNESS_DEST"] = original


@contextlib.contextmanager
def active_marker_lock(repo_root: str | None = None):
    """Serialize real-repo `.active` mutations across pytest-xdist workers."""
    root = repo_root or REPO_ROOT
    tasks_dir = os.path.join(root, "doc", "harness", "tasks")
    os.makedirs(tasks_dir, exist_ok=True)
    lock_path = os.path.join(tasks_dir, ".active.fixture.lock")
    with open(lock_path, "w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def invoke_hook(
    script_path: str,
    tool_name: str,
    tool_input: dict | None = None,
    *,
    env_extra: dict | None = None,
    cwd: str | None = None,
    timeout: float = 5.0,
):
    """Run a hook script with a crafted PreToolUse payload on stdin."""
    payload = json.dumps({
        "tool_name": tool_name,
        "tool_input": tool_input or {},
    })
    env = os.environ.copy()
    env.setdefault("CLAUDE_PLUGIN_ROOT", os.path.join(REPO_ROOT, "plugin"))
    # Keep hook subprocesses isolated from a real task_start-created
    # default-session marker; fixtures intentionally own the legacy marker.
    env.setdefault("HARNESS_SESSION_ID", "pytest-hook")
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, script_path],
        input=payload,
        capture_output=True,
        text=True,
        cwd=cwd or REPO_ROOT,
        env=env,
        timeout=timeout,
    )


def parse_decision(stdout: str):
    """Parse a hook's stdout JSON envelope. Returns (decision, reason) or (None, None)."""
    if not stdout or not stdout.strip():
        return None, None
    try:
        data = json.loads(stdout)
        hso = data.get("hookSpecificOutput") or {}
        return hso.get("permissionDecision"), hso.get("permissionDecisionReason")
    except Exception:
        return None, None


@contextlib.contextmanager
def scratch_task_in_real_repo(
    name: str = "scratch-test",
    *,
    plan: bool = True,
    maintenance: bool = False,
    progress: str | None = None,
    repo_root: str | None = None,
):
    """Create a scratch task dir under the real repo's doc/harness/tasks/.

    The task is removed in ``finally`` — no leaks even on test exception.
    Also writes ``.active`` pointing at this task for the duration.
    """
    root = repo_root or REPO_ROOT
    with active_marker_lock(root):
        tasks_dir = os.path.join(root, "doc", "harness", "tasks")
        task_id = f"TASK__{name}"
        task_dir = os.path.join(tasks_dir, task_id)
        os.makedirs(task_dir, exist_ok=True)

        # Save the real .active out of the way via atomic os.rename to a unique
        # sidecar path. In-memory save (the previous approach) was lost whenever an
        # exception fired between capture and restore — corrupting the harness's
        # canonical focus marker. If the process is SIGKILLed mid-fixture, the
        # sidecar `.active.fixture-backup.<pid>.<uuid>` survives and can be
        # restored manually with `mv`.
        active_marker = os.path.join(tasks_dir, ".active")
        backup_marker = (
            f"{active_marker}.fixture-backup.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        )
        had_existing = os.path.isfile(active_marker)
        if had_existing:
            os.rename(active_marker, backup_marker)

        try:
            if plan:
                with open(os.path.join(task_dir, "PLAN.md"), "w", encoding="utf-8") as f:
                    f.write("# Test plan\n")
            if maintenance:
                open(os.path.join(task_dir, "MAINTENANCE"), "w").close()
            if progress is not None:
                with open(os.path.join(task_dir, "PROGRESS.md"), "w", encoding="utf-8") as f:
                    f.write(progress)
            with open(active_marker, "w", encoding="utf-8") as f:
                f.write(task_dir)
            yield task_dir
        finally:
            shutil.rmtree(task_dir, ignore_errors=True)
            try:
                os.unlink(active_marker)
            except OSError:
                pass
            if had_existing and os.path.isfile(backup_marker):
                os.rename(backup_marker, active_marker)


_SESSION_ACTIVE_BACKUP: str | None = None


def pytest_sessionstart(session):
    """Snapshot the real repo's `.active` marker at session start via atomic
    rename to a sidecar path. Restored in `pytest_sessionfinish`.

    This is a safety net for test paths that mutate `.active` outside the
    `scratch_task_in_real_repo` fixture — notably `test_harness_mcp_server.py`,
    which calls `task_close` against the real `find_repo_root()` and removes
    the marker as a side effect of the close protocol.

    The fixture-level save/restore (`scratch_task_in_real_repo`) remains the
    primary line of defense for normal cases; this hook catches everything
    else with a single move.
    """
    global _SESSION_ACTIVE_BACKUP
    with active_marker_lock(REPO_ROOT):
        active_path = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")
        if not os.path.isfile(active_path):
            _SESSION_ACTIVE_BACKUP = None
            return
        backup = (
            f"{active_path}.session-backup.{os.getpid()}.{uuid.uuid4().hex[:8]}"
        )
        try:
            os.rename(active_path, backup)
            _SESSION_ACTIVE_BACKUP = backup
        except OSError:
            _SESSION_ACTIVE_BACKUP = None


def pytest_sessionfinish(session, exitstatus):
    """Restore the snapshotted `.active` from `pytest_sessionstart`.

    Best-effort: if a test process was hard-killed mid-suite, the sidecar
    file (`.active.session-backup.<pid>.<uuid>`) survives and the human can
    `mv` it back manually.
    """
    global _SESSION_ACTIVE_BACKUP
    backup = _SESSION_ACTIVE_BACKUP
    _SESSION_ACTIVE_BACKUP = None
    if not backup or not os.path.isfile(backup):
        return
    with active_marker_lock(REPO_ROOT):
        active_path = os.path.join(REPO_ROOT, "doc", "harness", "tasks", ".active")
        # Clear any in-flight scratch marker first so rename can succeed.
        try:
            os.unlink(active_path)
        except OSError:
            pass
        try:
            os.rename(backup, active_path)
        except OSError:
            pass


__all__ = [
    "REPO_ROOT",
    "SCRIPTS_DIR",
    "invoke_hook",
    "parse_decision",
    "scratch_task_in_real_repo",
    "active_marker_lock",
]


def unnamed_agent_id(label: str) -> str:
    """A realistic *unnamed* Claude spawn id for a fixture label.

    The id shape carries meaning since 2026-09-17: `_lib.UNNAMED_AGENT_ID_RE`
    matches `a` + 16 hex, which is what the CLI emits when no `name=` was
    passed, and `nonparsing_completion_note` reads it to tell a malformed report
    apart from a named spawn whose resolved type never reached the hook. A
    literal like `agent-<label>` models neither shape and reads as named, so
    fixtures that mean "an ordinary spawn whose report was wrong" must use this.

    Deterministic per label so a `started` row and its `completed` row pair.
    See `doc/harness/REQ__unbound-verdict-names-the-spawn-shape.md`.
    """
    import hashlib

    return "a" + hashlib.sha256(str(label).encode()).hexdigest()[:16]
