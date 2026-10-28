#!/usr/bin/env python3
"""Exercise imports, WBS checks and source ownership in an installed runtime.

All fixture writes are isolated from the installed payload. The supervising
process cleans worker descendants and scratch directories on success or timeout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import stat
import subprocess
import sys
import tempfile
import uuid

CORE_MODULES = ("_lib", "codex_hook_registration")
HOOK_SCRIPT_REFERENCE = re.compile(r"scripts/([A-Za-z0-9_]+)\.py")
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
    """Use the installer's pinned interpreter, inherited by this smoke process."""
    return os.path.realpath(sys.executable)


def _probe_env(plugin_root: str, session_id: str) -> dict:
    env = os.environ.copy()
    env["HARNESS_PLUGIN_ROOT"] = plugin_root
    env["CLAUDE_PLUGIN_ROOT"] = plugin_root
    env["HARNESS_SESSION_ID"] = session_id
    # Keep inspection read-only against the installed payload, including
    # interpreter-specific bytecode caches.
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
    against an unchanged failure. Canonical source validation rejects modules
    carrying group/other-write bits.

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
            "runtime task operations are unavailable from this tree",
            *[f"        {line}" for line in detail[-3:]],
            *_refusal_cause(plugin_root),
        ]
    return True, [f"ok import: {len(modules)} hook modules ({', '.join(modules)})"]


def _wbs_probe_worker(plugin_root: str, timeout: float) -> int:
    """Drive actual installed owner APIs, without lifecycle event fixtures."""
    from pathlib import Path
    original_cwd = os.getcwd()
    with tempfile.TemporaryDirectory(prefix="harness-wbs-smoke-") as scratch:
        repo = Path(scratch) / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=timeout)
        manifest = repo / "doc/harness/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("type: cli\n")
        (repo / "source.txt").write_text("ok\n")
        sys.path[:0] = [str(Path(plugin_root) / "scripts"), str(Path(plugin_root) / "mcp")]
        import _lib
        import harness_server
        import wbs
        sid = "install-smoke-" + uuid.uuid4().hex
        os.environ["HARNESS_SESSION_ID"] = sid
        os.environ["HARNESS_RUNTIME"] = (
            "codex" if Path(plugin_root, ".codex-plugin/plugin.json").is_file() else "claude"
        )
        try:
            os.chdir(repo)
            def call(action, **args):
                response = harness_server.call_tool("wbs", {"action": action, "wbs_id": "install_smoke", **args})
                if response.get("isError"):
                    raise RuntimeError(f"WBS {action} failed: {response}")
                return response
            opened = call("open")
            body = opened["structuredContent"]
            task_dir = body["task_dir"]
            if os.environ["HARNESS_RUNTIME"] == "codex":
                hook = str(Path(plugin_root) / "scripts/hook_post_tool_use.py")
                payload = json.dumps({"cwd": str(repo), "session_id": sid, "tool_name": "wbs",
                                      "arguments": {"action": "open", "wbs_id": "install_smoke"},
                                      "tool_response": opened})
                result = _run([_runtime_python(), hook], cwd=str(repo), env=dict(os.environ),
                              stdin=payload, timeout=timeout)
                if result.returncode:
                    raise RuntimeError("installed PostToolUse binding failed: " + result.stderr[-500:])
            control = _lib.read_task_control(task_dir)
            if not _lib.active_task_binding_matches(str(repo), task_dir, control, session_id=sid):
                raise RuntimeError("installed runtime lost exact session/task/run focus")
            nodes = [{"id": "A", "title": "Installed runtime reads actual source", "weight": 1,
                      "inputs": {"source": ["source.txt"]},
                      "check": [_runtime_python(), "-c",
                                "from pathlib import Path; assert Path('source.txt').read_text().strip() == 'ok'"]}]
            plan = "# Installation smoke\n```wbs\n" + json.dumps(nodes) + "\n```\n"
            call("plan", plan=plan)
            host = dict(capacity=2, live_agents=[dict(id="coordinator", role="coordinator")],
                        reserved_slots=[], verification_reserve=1,
                        spawn_available=False, continuation_available=False)
            call("dispatch", ownership={"A": ["source.txt"]}, host=host)
            wbs.require_source_assignment(str(repo), Path(task_dir).name, "source.txt")
            try:
                wbs.require_source_assignment(str(repo), Path(task_dir).name, "outside.txt")
            except ValueError:
                pass
            else:
                raise RuntimeError("installed source scope accepted an unassigned path")
            call("check", node_id="A")
            verified = call("verify")["structuredContent"]
            if verified.get("runtime_verdict") != "PASS":
                raise RuntimeError("installed WBS check is not current: " + str(verified.get("missing_for_close")) + "; " + str(verified.get("progress")))
            call("close")
            if not _lib.read_task_control(task_dir).get("closed"):
                raise RuntimeError("installed task owner did not publish closure")
            return 0
        except Exception as exc:
            print("FAIL WBS: " + str(exc), file=sys.stderr)
            return 1
        finally:
            os.chdir(original_cwd)


def _wbs_probe(plugin_root: str, timeout: float) -> tuple[bool, list[str]]:
    server = os.path.join(plugin_root, "mcp", "harness_server.py")
    if not os.path.isfile(server):
        return False, [f"FAIL WBS: installed tree has no {server}"]
    env = _probe_env(plugin_root, "isolated-install-fixture")
    with tempfile.TemporaryDirectory(prefix="harness-wbs-probe-") as scratch:
        env["TMPDIR"] = scratch
        proc = None
        try:
            proc = subprocess.Popen([
                _runtime_python(), os.path.abspath(__file__), "--plugin-root", plugin_root,
                "--wbs-probe-worker", "--timeout", str(timeout),
            ], cwd=plugin_root, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
               text=True, start_new_session=True)
            stdout, stderr = proc.communicate(timeout=timeout)
        except (OSError, subprocess.SubprocessError) as exc:
            return False, [f"FAIL WBS: isolated installed fixture did not complete: {exc}"]
        finally:
            if proc is not None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
        if proc.returncode:
            details = (stderr or stdout).strip().splitlines()[-3:]
            return False, ["FAIL WBS: installed task/source fixture failed", *[line[:500] for line in details]]
        return True, ["ok WBS: installed task/session ownership, source scope and actual checks through close"]


def smoke_installed_runtime(
    plugin_root: str, *, timeout: float = DEFAULT_TIMEOUT,
) -> tuple[bool, list[str]]:
    """Return (ok, report lines) for one installed plugin root."""
    plugin_root = os.path.abspath(os.path.expanduser(str(plugin_root)))
    if not os.path.isdir(os.path.join(plugin_root, "scripts")):
        return False, [f"FAIL smoke: {plugin_root} is not an installed plugin root"]
    lines: list[str] = []
    ok = True
    for probe in (_import_probe, _wbs_probe):
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
    parser.add_argument("--wbs-probe-worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.wbs_probe_worker:
        return _wbs_probe_worker(os.path.abspath(args.plugin_root), args.timeout)
    ok, lines = smoke_installed_runtime(args.plugin_root, timeout=args.timeout)
    for line in lines:
        print(line)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
