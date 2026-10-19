#!/usr/bin/env python3
"""Drive an installed harness runtime once and assert what it must produce.

The suite tests the *source* tree; hooks execute from the *installed* tree.
When those diverge the suite is green and the runtime is dead — the state that
persisted for a month, twice, because nothing ever exercised the installed
copy. A single stale `.pyc` under `~/.claude/harness-dev/plugin/scripts` makes
`_lib` refuse `subagent_lifecycle`'s receipt-adapter binding, every
`SubagentStart`/`SubagentStop` dies before `main()`, and the only symptom is an
absence of receipts. See
`doc/harness/REQ__receipt-subsystem-failures-are-observable.md` and
`doc/harness/REQ__guards-are-verified-where-they-run.md`.

Two probes, both against the tree that was just installed:

  1. **Import** — every hook module the runtime loads is imported in a fresh
     interpreter with the installed `scripts/` on `sys.path`. `_lib` and
     `subagent_lifecycle` are always included: they are the modules the
     adapter-identity guard rejects, and `background_hook` itself cannot stand
     in for them because it catches its own import failure and exits 0 by
     design (C-12).
  2. **Receipt** — a throwaway repository exercises its runtime owner.
     Claude drives background_hook.py with a fixture SubagentStart. Codex
     drives installed MCP registration and its manager against isolated native
     rollout fixtures, requiring matching started and completed PASS records.
     These are installation self-tests, never current-task attestation.

Read-only with respect to the installed tree: everything it writes lives in a
temporary directory. Stdlib only, and deliberately independent of `_lib` — the
point is to work when `_lib` is exactly what is broken.

Run by `install.py` after each runtime's payload is in place, and usable by
hand:

    python3 plugin/scripts/install_smoke.py --plugin-root ~/.claude/harness-dev/plugin
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

CORE_MODULES = ("_lib", "subagent_lifecycle")
HOOK_SCRIPT_REFERENCE = re.compile(r"scripts/([A-Za-z0-9_]+)\.py")
SMOKE_TASK_ID = "TASK__install_smoke"
DEFAULT_TIMEOUT = 60.0


def _hook_modules(plugin_root: str) -> list[str]:
    """Module names for the hook scripts this installed tree registers.

    Discovered from `hooks/hooks.json` (Claude) and from the `hook_*.py`
    wrappers (Codex) rather than listed here, so a newly registered hook is
    covered without editing this file.
    """
    names = set(CORE_MODULES)
    scripts_dir = os.path.join(plugin_root, "scripts")
    hooks_json = os.path.join(plugin_root, "hooks", "hooks.json")
    try:
        with open(hooks_json, "r", encoding="utf-8") as handle:
            names.update(HOOK_SCRIPT_REFERENCE.findall(handle.read()))
    except OSError:
        pass
    try:
        names.update(
            entry[: -len(".py")]
            for entry in os.listdir(scripts_dir)
            if entry.startswith("hook_") and entry.endswith(".py")
        )
    except OSError:
        pass
    return sorted(
        name for name in names
        if os.path.isfile(os.path.join(scripts_dir, name + ".py"))
    )


def _run(argv: list[str], *, cwd: str, env: dict, stdin: str = "",
         timeout: float) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv, input=stdin, capture_output=True, text=True,
        cwd=cwd, env=env, timeout=timeout,
    )


def _runtime_python() -> str:
    """The interpreter the installed runtime actually runs under.

    `install.py` registers hooks and the MCP server as literal `python3`, so
    that is the build whose view of the installed tree matters. It is not
    interchangeable with `sys.executable`: `_lib`'s adapter-identity guard
    compares the imported module's code object against a fresh compile, and two
    different CPython 3.12.13 builds (a venv interpreter and the system
    `python3`) produce code objects that compare unequal when one reads a
    `__pycache__` entry the other wrote. Probing with the wrong interpreter
    would report a failure the runtime never sees — or miss one it does.
    """
    return shutil.which("python3") or sys.executable


def _probe_env(plugin_root: str, session_id: str) -> dict:
    env = os.environ.copy()
    env["HARNESS_PLUGIN_ROOT"] = plugin_root
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    env["HARNESS_SESSION_ID"] = session_id
    # Read-only against the installed tree. Without this the probe leaves
    # `__pycache__` entries behind, and bytecode written by a different
    # interpreter build is exactly what disables the receipt subsystem
    # (see `_runtime_python`) — a smoke test that breaks what it inspects.
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # The installed tree is the subject; never let the caller's own repo,
    # session, or pytest context decide what these probes observe.
    for name in ("HARNESS_SESSION_HINT", "CODEX_SESSION_ID", "CODEX_THREAD_ID",
                 "CLAUDE_SESSION_ID", "PYTEST_CURRENT_TEST"):
        env.pop(name, None)
    return env


def _world_writable_sources(plugin_root: str, limit: int = 3) -> list[str]:
    """Installed `.py` files carrying group/other-write bits, bounded sample.

    This is the *other* way a tree gets refused by the canonical-import guards,
    and until 2026-09-17 the only wording offered was the stale-`__pycache__`
    one — so a user on a Windows bind mount, where every synced file lands
    `0o777`, re-ran `install.py --force` (which clears caches and nothing else)
    against an unchanged failure. `_lib.bind` refuses on
    `before.st_mode & 0o022` and the receipt-adapter bind refuses on the same
    term inside `structural`, which is why that failure surfaces as the generic
    `PermissionError` rather than `StaleBytecodeCacheError`.

    Symlinks are not followed: the mode that matters is the one the guard
    lstat()s. Never raises — a diagnosis helper must not replace the failure it
    is trying to explain.
    """
    found: list[str] = []
    for parent, dirnames, filenames in os.walk(plugin_root):
        # Sorted, and with the modules the guards actually reject first, so the
        # bounded sample is deterministic and names something recognizable
        # rather than whichever file the directory happened to yield first.
        dirnames.sort()
        for name in sorted(
            filenames, key=lambda item: (item[: -len(".py")] not in CORE_MODULES, item),
        ):
            if not name.endswith(".py"):
                continue
            path = os.path.join(parent, name)
            try:
                info = os.lstat(path)
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode) or not stat.S_IMODE(info.st_mode) & 0o022:
                continue
            found.append(path)
            if len(found) >= limit:
                return found
    return found


def _refusal_cause(plugin_root: str) -> list[str]:
    """The cause line for a probe failure, chosen by what the tree actually is.

    Writable modes are reported when present because they are both checkable
    and actionable; the `__pycache__` wording stays as the fallback, which is
    what it always was — a known cause, not an observed one.
    """
    writable = _world_writable_sources(plugin_root)
    if not writable:
        return [
            "        cause: a stale __pycache__ is the known cause; "
            "`python3 install.py --force` clears it",
        ]
    return [
        "        cause: this tree is group/other-writable, which the "
        "canonical-import guard refuses (not a stale __pycache__ — "
        "`--force` will not fix it)",
        *[f"          writable: {path}" for path in writable],
        f"        remedy: chmod -R go-w {plugin_root}",
        "          (a fixed installer also normalizes this on the next install)",
    ]


def _import_probe(plugin_root: str, timeout: float) -> tuple[bool, list[str]]:
    modules = _hook_modules(plugin_root)
    if not modules:
        return False, [f"FAIL import: no hook modules found under {plugin_root}/scripts"]
    scripts_dir = os.path.join(plugin_root, "scripts")
    program = (
        "import importlib, sys\n"
        f"sys.path.insert(0, {scripts_dir!r})\n"
        f"for name in {list(modules)!r}:\n"
        "    try:\n"
        "        importlib.import_module(name)\n"
        "    except BaseException as exc:\n"
        "        print(f'{name}: {type(exc).__name__}: {exc}')\n"
        "        sys.exit(1)\n"
    )
    with tempfile.TemporaryDirectory(prefix="harness-smoke-import-") as tmp:
        try:
            result = _run(
                [_runtime_python(), "-c", program], cwd=tmp,
                env=_probe_env(plugin_root, "install-smoke"), timeout=timeout,
            )
        except subprocess.SubprocessError as exc:
            return False, [f"FAIL import: probe did not complete: {exc}"]
    if result.returncode != 0:
        detail = (result.stdout + result.stderr).strip().splitlines()
        return False, [
            "FAIL import: an installed hook module does not import — "
            "receipts cannot be recorded from this tree",
            *[f"        {line}" for line in detail[-3:]],
            *_refusal_cause(plugin_root),
        ]
    return True, [f"ok import: {len(modules)} hook modules ({', '.join(modules)})"]


def _receipt_probe(plugin_root: str, timeout: float) -> tuple[bool, list[str]]:
    server = os.path.join(plugin_root, "mcp", "harness_server.py")
    hook = os.path.join(plugin_root, "scripts", "background_hook.py")
    for path in (server, hook):
        if not os.path.isfile(path):
            return False, [f"FAIL receipt: installed tree has no {path}"]

    session_id = "install-smoke-" + uuid.uuid4().hex[:8]
    env = _probe_env(plugin_root, session_id)
    with tempfile.TemporaryDirectory(prefix="harness-smoke-repo-") as tmp:
        repo = os.path.join(tmp, "repo")
        os.makedirs(os.path.join(repo, ".git"))
        os.makedirs(os.path.join(repo, "doc", "harness"))
        with open(os.path.join(repo, "doc", "harness", "manifest.yaml"),
                  "w", encoding="utf-8") as handle:
            handle.write("type: install-smoke\n")

        open_task = (
            "import importlib.util, sys\n"
            f"spec = importlib.util.spec_from_file_location('harness_server', {server!r})\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules['harness_server'] = module\n"
            "spec.loader.exec_module(module)\n"
            f"result = module.call_tool('task_start', {{'task_id': {SMOKE_TASK_ID!r}}})\n"
            "sys.exit(1 if result.get('isError') else 0)\n"
        )
        try:
            started = _run([_runtime_python(), "-c", open_task], cwd=repo, env=env,
                           timeout=timeout)
        except subprocess.SubprocessError as exc:
            return False, [f"FAIL receipt: installed MCP server did not respond: {exc}"]
        if started.returncode != 0:
            detail = (started.stdout + started.stderr).strip().splitlines()[-3:]
            return False, [
                "FAIL receipt: the installed MCP server could not open a task",
                *[f"        {line}" for line in detail],
                *_refusal_cause(plugin_root),
            ]

        payload = json.dumps({
            "cwd": repo,
            "session_id": session_id,
            "agent_id": "install-smoke-agent",
            "agent_type": "harness:code-reviewer",
            "hook_event_name": "SubagentStart",
        })
        try:
            _run([_runtime_python(), hook, "--event", "start"], cwd=repo, env=env,
                 stdin=payload, timeout=timeout)
        except subprocess.SubprocessError as exc:
            return False, [f"FAIL receipt: background_hook did not complete: {exc}"]

        receipts = os.path.join(
            repo, "doc", "harness", "tasks", SMOKE_TASK_ID, "RECEIPTS.jsonl",
        )
        events = []
        try:
            with open(receipts, "r", encoding="utf-8") as handle:
                for line in handle:
                    if line.strip():
                        events.append(json.loads(line).get("event"))
        except OSError:
            pass
        except ValueError as exc:
            return False, [f"FAIL receipt: RECEIPTS.jsonl is not readable JSONL: {exc}"]

        if "started" not in events:
            breadcrumb = ""
            try:
                with open(os.path.join(repo, "doc", "harness", "learnings.jsonl"),
                          "r", encoding="utf-8") as handle:
                    for line in handle:
                        if "receipt-subsystem-unavailable" in line:
                            breadcrumb = json.loads(line).get("error", "")
            except (OSError, ValueError):
                pass
            return False, [
                "FAIL receipt: a bound subagent produced no receipt row from the "
                "installed tree — task_close will refuse for every task"
                + (f" ({breadcrumb})" if breadcrumb else ""),
            ]
    return True, ["ok receipt: bound subagent produced a started row"]



def _codex_probe_worker(plugin_root: str, timeout: float) -> int:
    """Exercise installed ownership with isolated test inputs, never real attestation."""
    from datetime import datetime
    from pathlib import Path
    import time

    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="harness-codex-smoke-") as scratch:
        base = Path(scratch)
        repo, home = base / "repo", base / "codex"
        (repo / ".git").mkdir(parents=True)
        manifest = repo / "doc/harness/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("type: install-smoke\n")
        for key in ("HARNESS_SESSION_ID", "HARNESS_SESSION_HINT", "CODEX_SESSION_ID",
                    "CODEX_THREAD_ID", "CLAUDE_SESSION_ID", "PYTEST_CURRENT_TEST"):
            os.environ.pop(key, None)
        os.environ.update(CODEX_HOME=str(home), HARNESS_RUNTIME="codex")
        sys.path[:0] = [str(Path(plugin_root) / "scripts"), str(Path(plugin_root) / "mcp")]
        import _lib
        import harness_server
        import codex_lifecycle_watcher as watcher

        root_id, child_id = _lib.new_uuid7(), _lib.new_uuid7()
        os.environ["CODEX_THREAD_ID"] = root_id
        def rollout(thread_id):
            created = datetime.fromtimestamp(int(thread_id.replace("-", "")[:12], 16) / 1000).astimezone()
            return home / "sessions" / created.strftime("%Y/%m/%d") / ("rollout-" + thread_id + ".jsonl")
        def append(path, events):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as handle:
                for event in events:
                    event["timestamp"] = datetime.now().astimezone().isoformat()
                    handle.write(json.dumps(event) + "\n")
        root, child = rollout(root_id), rollout(child_id)
        append(root, [{"type": "session_meta", "payload": {
            "id": root_id, "session_id": root_id, "cwd": str(repo), "thread_source": "user",
        }}])
        server = None
        deadline = time.monotonic() + max(1, min(10, timeout - 1))
        def wait_for(predicate, message):
            while time.monotonic() < deadline:
                if predicate():
                    return
                time.sleep(0.05)
            raise RuntimeError(message)
        try:
            os.chdir(repo)
            server = harness_server.McpServer()
            server.handle_request({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
                "protocolVersion": "2025-11-25", "capabilities": {},
                "clientInfo": {"name": "isolated-installation-fixture", "version": "1"},
            }})
            result = harness_server.call_tool("wbs", {"action": "open", "wbs_id": "WBS__install_smoke"})
            if result.get("isError"):
                raise RuntimeError("installed native MCP could not open fixture task")
            body = result["structuredContent"]
            task_dir, run_id = body["task_dir"], body["run_id"]
            admitted = watcher.registrations(str(repo))
            if len(admitted) != 1 or (admitted[0]["task_id"], admitted[0]["run_id"]) != (SMOKE_TASK_ID, run_id):
                raise RuntimeError("installed native registration lost its task/run generation")
            # Also exercise the ordinary shared-host PostToolUse boundary.
            # A second isolated native root has no eager process identity.
            eager_offset = admitted[0]["offset"]
            if eager_offset != root.stat().st_size:
                raise RuntimeError("eager registration did not begin at current offset")
            root_id = _lib.new_uuid7()
            root = rollout(root_id)
            append(root, [{"type": "session_meta", "payload": {
                "id": root_id, "session_id": root_id, "cwd": str(repo), "thread_source": "user",
            }}])
            os.environ.pop("CODEX_THREAD_ID")
            context = harness_server.call_tool("wbs", {"action": "context", "wbs_id": "WBS__install_smoke"})
            hook = str(Path(plugin_root) / "scripts/hook_post_tool_use.py")
            payload = json.dumps({"cwd": str(repo), "session_id": root_id, "tool_name": "wbs",
                                  "arguments": {"action": "context", "wbs_id": "WBS__install_smoke"},
                                  "tool_response": context})
            result = _run([_runtime_python(), hook], cwd=str(repo), env=dict(os.environ),
                          stdin=payload, timeout=max(0.1, deadline - time.monotonic()))
            admitted = [item for item in watcher.registrations(str(repo)) if item["thread_id"] == root_id]
            if result.returncode or len(admitted) != 1 or admitted[0]["run_id"] != run_id:
                raise RuntimeError("installed PostToolUse did not bind the native fixture")
            offset = admitted[0]["offset"]
            if offset != root.stat().st_size:
                raise RuntimeError("native fixture registration did not begin at current offset")
            wait_for(lambda: watcher.registration_host_live(str(repo), root_id), "native fixture manager never acquired its lease")
            name, path, call_id = "review_code_install_smoke", "/root/review_code_install_smoke", "call_install_smoke"
            append(child, [{"type": "session_meta", "payload": {
                "id": child_id, "session_id": root_id, "parent_thread_id": root_id,
                "cwd": str(repo), "thread_source": "subagent", "agent_path": path,
                "source": {"subagent": {"thread_spawn": {
                    "parent_thread_id": root_id, "depth": 1, "agent_path": path,
                }}},
            }}, {"type": "response_item", "payload": {
                "type": "agent_message", "author": "/root", "recipient": path,
                "content": [{"type": "input_text", "text": "Message Type: NEW_TASK\nPayload:\nInstallation fixture"}],
            }}])
            append(root, [{"type": "response_item", "payload": {
                "type": "function_call", "namespace": "collaboration", "name": "spawn_agent",
                "call_id": call_id, "arguments": json.dumps({"task_name": name}),
            }}, {"type": "event_msg", "payload": {"type": "item_completed", "item": {
                "type": "SubAgentActivity", "kind": "started", "id": call_id,
                "agent_thread_id": child_id, "agent_path": path,
            }}}, {"type": "response_item", "payload": {
                "type": "function_call_output", "call_id": call_id, "output": json.dumps({"task_name": path}),
            }}])
            def entries():
                return list(_lib.receipt_snapshot(task_dir).entries)
            wait_for(lambda: len(entries()) == 1, "native fixture start was not collected")
            final = ('VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n'
                     'REVIEW_DETAIL: {"blocker":null,"findings":[]}\nIsolated installation fixture only.')
            append(child, [{"type": "event_msg", "payload": {
                "type": "agent_message", "phase": "final_answer", "message": final,
            }}, {"type": "event_msg", "payload": {"type": "task_complete", "last_agent_message": final}}])
            append(root, [{"type": "response_item", "payload": {
                "type": "agent_message", "author": path, "recipient": "/root",
                "content": [{"type": "input_text", "text": "Message Type: FINAL_ANSWER\nPayload:\n" + final}],
            }}])
            wait_for(lambda: len(entries()) == 2, "native fixture completion was not collected")
            rows = entries()
            expected_id = "codex:" + root_id + ":" + call_id + ":" + child_id
            if [row["event"] for row in rows] != ["started", "completed"] or any(
                row["runtime_id"] != expected_id or row["task_run_id"] != run_id
                or row["agent_id"] != path or row["lens"] != "review-code"
                or row["source"] != "codex_session_watcher:collaboration" for row in rows
            ) or rows[1]["verdict"] != "PASS":
                raise RuntimeError("native fixture receipt identity or completion did not match")
            return 0
        except Exception as exc:
            print("FAIL codex receipt: " + str(exc), file=sys.stderr)
            return 1
        finally:
            if server is not None:
                server.close()
            os.chdir(original_cwd)


def _codex_receipt_probe(plugin_root: str, timeout: float) -> tuple[bool, list[str]]:
    env = _probe_env(plugin_root, "isolated-codex-install-fixture")
    # The supervising process owns cleanup even when subprocess.run must kill
    # a wedged worker before the worker's own finally block can run.
    with tempfile.TemporaryDirectory(prefix="harness-codex-probe-") as scratch:
        env["TMPDIR"] = scratch
        proc = None
        try:
            proc = subprocess.Popen([
                _runtime_python(), os.path.abspath(__file__), "--plugin-root", plugin_root,
                "--codex-probe-worker", "--timeout", str(timeout),
            ], cwd=plugin_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, start_new_session=True)
            stdout, stderr = proc.communicate(timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, [f"FAIL codex receipt: isolated native fixture did not complete: {exc}"]
        finally:
            if proc is not None:
                # Include a hook/helper outliving its worker, before deleting
                # their shared fixture directory on success or failure.
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
        if proc.returncode:
            details = (stderr or stdout).strip().splitlines()[-3:]
            return False, ["FAIL codex receipt: installed native lifecycle fixture failed", *[line[:500] for line in details]]
        return True, ["ok codex receipt: isolated native fixture collected matching start and completed PASS (self-test only)"]


def smoke_installed_runtime(
    plugin_root: str, *, timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, list[str]]:
    """Return (ok, report lines) for one installed plugin root."""
    plugin_root = os.path.abspath(os.path.expanduser(str(plugin_root)))
    if not os.path.isdir(os.path.join(plugin_root, "scripts")):
        return False, [f"FAIL smoke: {plugin_root} is not an installed plugin root"]
    lines: list[str] = []
    ok = True
    receipt_probe = (_codex_receipt_probe if os.path.isfile(os.path.join(plugin_root, ".codex-plugin", "plugin.json"))
                     else _receipt_probe)
    for probe in (_import_probe, receipt_probe):
        probe_ok, probe_lines = probe(plugin_root, timeout)
        ok = ok and probe_ok
        lines.extend(probe_lines)
    return ok, lines


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Smoke-test an installed harness runtime tree",
    )
    parser.add_argument("--plugin-root", required=True,
                        help="Installed plugin root (…/harness-dev/plugin)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--codex-probe-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.codex_probe_worker:
        return _codex_probe_worker(os.path.abspath(args.plugin_root), args.timeout)
    ok, lines = smoke_installed_runtime(args.plugin_root, timeout=args.timeout)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
