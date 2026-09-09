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
  2. **Receipt** — a throwaway repository gets a real task through the
     installed MCP server, the installed `background_hook.py` is driven with a
     synthetic `SubagentStart` payload, and the task's `RECEIPTS.jsonl` must
     gain a `started` row. This is the observable whose absence *is* the
     outage.

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
            "receipts cannot be recorded from this tree "
            "(a stale __pycache__ is the known cause; `python3 install.py --force` clears it)",
            *[f"        {line}" for line in detail[-3:]],
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


def smoke_installed_runtime(
    plugin_root: str, *, timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, list[str]]:
    """Return (ok, report lines) for one installed plugin root."""
    plugin_root = os.path.abspath(os.path.expanduser(str(plugin_root)))
    if not os.path.isdir(os.path.join(plugin_root, "scripts")):
        return False, [f"FAIL smoke: {plugin_root} is not an installed plugin root"]
    lines: list[str] = []
    ok = True
    for probe in (_import_probe, _receipt_probe):
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
    args = parser.parse_args()
    ok, lines = smoke_installed_runtime(args.plugin_root, timeout=args.timeout)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
