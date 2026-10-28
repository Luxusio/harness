"""Shared test fixtures for harness gate tests.

Exports:
  REPO_ROOT            — repo root (directory containing this file's parent)
  SCRIPTS_DIR          — plugin/scripts absolute path
  invoke_hook(...)     — subprocess runner for a hook script with stdin JSON
  scratch_task_in_real_repo(...) — context manager creating a scratch task dir
                         with clean finally removal (no leaks on exception)
  install_trees_lose_no_files — session-wide guard: a run may add to an
                         installed harness runtime, never remove from one
  make_tmp_harness_root(...) / ledger_rows(...) — a tmp checkout for tests that
                         drive a learnings-ledger writer, and its ledger rows
  learnings_ledger_gains_no_suite_rows — session-wide guard: a run appends no
                         row to this checkout's real learnings ledger
"""
from __future__ import annotations

import collections
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

# Two CPython builds that share a cache tag (here /usr/bin/python3 3.12.3 and
# the mise/.venv 3.12.14; on 2026-09-09 two 3.12.13 builds) accept each
# other's `.pyc` as header-valid but compile unequal code, and `_lib`'s
# Source ownership guards refuse mismatched code objects.
# The suite therefore writes no bytecode into the checkout, for itself or the
# subprocesses it spawns, so a later run under another build cannot read one.
# Set at import so xdist workers inherit the environment. The prune in
# `pytest_configure` is the half that protects this run from caches other
# processes left behind.
sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"


def _default_install_tree_roots() -> list[str]:
    """The installed runtime trees a suite run must never remove files from.

    The home directory comes from the password database rather than
    `Path.home()` for the same reason `install._reject_real_install_root_under_test`
    does: a test that repoints `HOME` at `tmp_path` is the isolated case, and
    `Path.home()` would then report that tmp home as the real one.

    Four roots. `~/.claude/plugins/cache/harness/harness` is the tree Claude
    Code actually runs hooks from (`installed_plugins.json` installPath, one
    version directory per payload since the version stamp of b2bb5e0); the
    mirror `~/.claude/harness-dev` is only the marketplace source. Old version
    directories carry a CLI-written `.orphaned_at` file and are deleted by the
    CLI later, so the *before* inventory skips them (see
    `_install_tree_inventory`). A set `CLAUDE_CONFIG_DIR` moves the real cache
    elsewhere and this literal root then watches the wrong tree — a named
    limitation, kept because the documented snippet must name the same literal
    roots (tests/test_install_tree_removal_guard.py).

    `~/.codex/plugins/cache/harness/harness` is the tree
    Codex actually loads — `install.py` registers its hooks with absolute
    commands into that directory and prunes bytecode inside it — so the
    `rmtree(cache.parent)` class of mutation that started this guard would
    destroy it while the other two roots stayed intact. Only the harness
    marketplace/plugin subtree is watched; sibling marketplaces under
    `~/.codex/plugins/cache` are other tools' trees, and walking them costs more
    than it protects. Measured 2026-09-09: 521 files, ~0.1s per snapshot.
    Re-measured 2026-09-26 with four roots on a WSL host: before 346 files,
    after 781 (the Claude cache's five orphaned versions are walked only
    after), about 5s for both passes together.
    """
    try:
        import pwd

        home = pwd.getpwuid(os.getuid()).pw_dir
    except Exception:  # no password database — nothing to protect
        return []
    return [
        os.path.join(home, ".claude", "harness-dev"),
        os.path.join(home, ".claude", "plugins", "cache", "harness", "harness"),
        os.path.join(home, ".codex", "harness"),
        os.path.join(home, ".codex", "plugins", "cache", "harness", "harness"),
    ]


# Read at fixture time, so the guard's own test can point it at a stand-in tree
# (tests/test_install_tree_removal_guard.py).
_INSTALL_TREE_ROOTS = _default_install_tree_roots()

# A nested pytest session launched by a test in this suite — currently only
# `test_qa_knowledge_shape.red_claim_violation`, which probes whether a
# known-red note's node still fails.
#
# Such a session runs with cwd=REPO_ROOT and so re-enters the session hooks
# below, which is actively harmful rather than merely wasteful: the marker
# snapshot renames the developer's live `.active` away for the probe's
# duration, and `pytest_sessionfinish` unlinks whatever `.active` it finds
# before restoring — so a marker created by an unlocked sibling test running
# concurrently can be destroyed. Measured 2026-09-18 with a 10ms poller: 13
# consecutive samples with `.active` absent and a session-backup sidecar
# present. `doc/harness/REQ__test-suite-determinism-under-xdist.md` rule 2
# forbids exactly this.
#
# The install-tree guard is skipped for the same reason in reverse: the nested
# session inventories ~1500 installed files twice while the outer session is
# already watching them, and its assertion answers a question the outer
# session owns. The learnings-ledger guard is skipped on the same ground: the
# outer session already watches the ledger the probe would write to.
#
# Read at call time rather than import time so the guard is testable: a
# module-level constant would freeze whatever the environment held when pytest
# imported this file, and the call sites could then only be checked by reading
# their source.
def is_nested_probe() -> bool:
    return os.environ.get("HARNESS_NESTED_PROBE") == "1"


def _prune_checkout_bytecode(root: str) -> None:
    """Remove real `__pycache__` directories under `root`; never raise.

    Bounded to the checkout: symlinked directories are not
    followed and a symlinked `__pycache__` is left alone. A concurrent writer
    can make `rmtree` fail; the next session retries.
    """
    for parent, dirnames, _files in os.walk(root, followlinks=False):
        if "__pycache__" not in dirnames:
            continue
        dirnames.remove("__pycache__")
        cache = os.path.join(parent, "__pycache__")
        if os.path.islink(cache) or not os.path.isdir(cache):
            continue
        shutil.rmtree(cache, ignore_errors=True)


def pytest_configure(config):
    """Discard checkout bytecode another CPython build may have written.

    Runs on the xdist controller only, before any worker collects, and never
    in a nested probe session, which runs inside the real repo while the outer
    session is importing from it.
    """
    if hasattr(config, "workerinput") or is_nested_probe():
        return
    _prune_checkout_bytecode(os.path.join(REPO_ROOT, "plugin"))


def _install_tree_inventory(root: str, exclude_orphaned: bool = False) -> dict[str, int]:
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

    `exclude_orphaned` is for the *before* inventory only: a top-level child
    directory that already holds a `.orphaned_at` file is one the Claude CLI
    has scheduled for deletion, so its disappearance is not this run's doing.
    The after inventory must walk everything — a real install during the run
    orphans the live version directory with all its files still present, and
    skipping it there would report every one of them as removed.
    """
    inventory: dict[str, int] = {}
    for parent, dirnames, filenames in os.walk(root, onerror=lambda _e: None):
        dirnames[:] = [name for name in dirnames if name != "__pycache__"]
        if exclude_orphaned and parent == root:
            dirnames[:] = [
                name for name in dirnames
                if not os.path.isfile(os.path.join(root, name, ".orphaned_at"))
            ]
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
    `plugin/scripts` and `plugin/mcp` and broke runtime imports for the
    session that ran it. Six review rounds had invariant checks in place, but
    every one of them watched `doc/harness/tasks/.active_sessions/` — task
    artifacts — and none watched the tree the hooks actually execute from.
    See `doc/harness/REQ__guards-are-verified-where-they-run.md`.

    Absent trees (fresh machine, CI) inventory as empty and never fail.

    A nested probe session skips this entirely — see `is_nested_probe`. Its
    teardown assertion also made exit 1 ambiguous for the caller: a passing
    selected test plus a failing session-scoped teardown yields
    `1 passed, 1 error` and rc=1, which `red_claim_violation` would read as
    "the named node is still red".
    """
    if is_nested_probe():
        yield
        return
    before = {
        root: _install_tree_inventory(root, exclude_orphaned=True)
        for root in _INSTALL_TREE_ROOTS
    }
    yield
    removed = _install_tree_removals(before)
    assert not removed, (
        "this test run removed "
        f"{len(removed)} file(s) from an installed harness runtime tree:\n  "
        + "\n  ".join(removed)
        + "\nRe-install with `python3 install.py` to repair, then bind the "
        "responsible test to a tmp install root."
    )


# This checkout's learnings ledger. Read at fixture time, so the guard's own
# test can point it at a stand-in (tests/test_learnings_ledger_guard.py).
_LEARNINGS_LEDGER = os.path.join(REPO_ROOT, "doc", "harness", "learnings.jsonl")

# Live session hook writers may append concurrently with an isolated suite.
_LIVE_SESSION_LEDGER_WRITERS = frozenset({"prompt_memory", "tool_routing"})


def make_tmp_harness_root(path) -> str:
    """Make `path` a harness checkout that ledger writers resolve to.

    `.git` stops `find_repo_root` at `path` instead of walking up to the real
    repo, and `doc/harness/manifest.yaml` makes `is_harness_enabled_repo` true,
    which most writers require before they append. A test that drives a ledger
    writer runs it here, so its row lands in `ledger_rows(path)` and not in the
    checkout the suite runs from.
    """
    root = os.fspath(path)
    os.makedirs(os.path.join(root, ".git"), exist_ok=True)
    harness = os.path.join(root, "doc", "harness")
    os.makedirs(harness, exist_ok=True)
    with open(os.path.join(harness, "manifest.yaml"), "w", encoding="utf-8") as f:
        f.write("test_command: pytest\n")
    return root


def ledger_rows(root) -> list[dict]:
    """The parsed rows of `root`'s learnings ledger; empty when it is absent."""
    path = os.path.join(os.fspath(root), "doc", "harness", "learnings.jsonl")
    try:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except FileNotFoundError:
        return []


def _ledger_snapshot(path: str) -> bytes:
    """The ledger's complete lines as bytes; empty when absent or unreadable.

    A trailing line without its newline is a writer mid-append. Leaving it out
    here means a teardown read counts it once it is whole, rather than
    reporting the fragment that follows the recorded offset.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError:
        return b""
    return data[: data.rfind(b"\n") + 1]


def _ledger_rows_appended(path: str, before: bytes) -> list[str]:
    """Rows present now that the `before` snapshot did not hold.

    Normally the ledger only grows, and the answer is everything past the
    recorded size. If it shrank, was replaced, or was rewritten in place, that
    offset no longer marks where this run began, so the lines are compared as
    a multiset instead: each line still present from `before` cancels one
    occurrence, and whatever is left over was added.
    """
    after = _ledger_snapshot(path)
    if after.startswith(before):
        added = after[len(before):].splitlines()
    else:
        remaining = collections.Counter(before.splitlines())
        added = []
        for line in after.splitlines():
            if remaining[line]:
                remaining[line] -= 1
            else:
                added.append(line)
    return [line.decode("utf-8", "replace") for line in added if line.strip()]


def _suite_ledger_rows(lines: list[str]) -> list[str]:
    """Drop rows whose writer is one of `_LIVE_SESSION_LEDGER_WRITERS`.

    The writer is the row's `source`, or `script` for a `gate-crash` row, which
    carries no `source`. A line that is not a JSON object is kept: no hook
    writes one, so nothing attributes it to the live session.
    """
    kept = []
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            row = None
        if isinstance(row, dict):
            writer = str(row.get("source") or row.get("script") or "")
            if writer.split(":", 1)[0] in _LIVE_SESSION_LEDGER_WRITERS:
                continue
        kept.append(line)
    return kept


@pytest.fixture(scope="session", autouse=True)
def learnings_ledger_gains_no_suite_rows():
    """Fail the run if it appended a row to this checkout's learnings ledger.

    `doc/harness/learnings.jsonl` is gitignored live state (rule 2 of
    `doc/harness/REQ__test-suite-determinism-under-xdist.md`), and in a batch
    worktree `batch_harvest` copies it into the main checkout, so a test's
    `gate-bypass` row becomes the developer's history. A test that drives a
    ledger writer runs it against `make_tmp_harness_root` instead.

    Rows the developer's live session appends in this checkout during the run
    are not the suite's; `_LIVE_SESSION_LEDGER_WRITERS` names the writers
    ignored and why. Two costs follow. A suite-driven row from one of those
    writers is invisible here. A row the session appends from any other writer
    during the run, such as a write-gate bypass or a learning added by hand,
    fails the run; the message names the row so its owner can tell.

    Under pytest-xdist every worker runs this against the shared ledger. Each
    worker records its snapshot before its own first test, so every row a test
    appends is reported by at least the worker that ran it, and possibly also
    by a sibling whose session was still open. A ledger that shrinks or is
    replaced mid-run is handled by `_ledger_rows_appended`.

    A nested probe session skips this entirely, as it does the install-tree
    guard; see `is_nested_probe`.
    """
    if is_nested_probe():
        yield
        return
    path = _LEARNINGS_LEDGER
    before = _ledger_snapshot(path)
    yield
    appended = _suite_ledger_rows(_ledger_rows_appended(path, before))
    assert not appended, (
        f"this test run appended {len(appended)} row(s) to the learnings ledger "
        f"{path}:\n  "
        + "\n  ".join(appended)
        + "\nRun the responsible test's writer against a tmp checkout "
        "(`make_tmp_harness_root`) and assert the row there. Under pytest-xdist "
        "another worker's test may have written a row listed here; run suspect "
        "tests one node at a time with `-n0` to attribute it."
    )


@pytest.fixture(autouse=True)
def restore_harness_server_global():
    """Undo `McpServer.__init__`'s assignment to the module global `_SERVER`.

    Production runs one server per process. Tests construct several servers,
    so restore the global to keep runtime state isolated across test cases.

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
    if is_nested_probe():
        _SESSION_ACTIVE_BACKUP = None
        return
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
    if is_nested_probe():
        _SESSION_ACTIVE_BACKUP = None
        return
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
    "make_tmp_harness_root",
    "ledger_rows",
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
