#!/usr/bin/env python3
"""Start/status/stop/log background services declared in doc/harness/manifest.yaml.

Stdlib-only on purpose: this runs before project dependencies may be installed.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


DEFAULT_MANIFEST = Path("doc/harness/manifest.yaml")
DEFAULT_RUNTIME_DIR = Path("doc/harness/runtime")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value == "":
        return ""
    if value in {"true", "false"}:
        return value == "true"
    try:
        return int(value)
    except ValueError:
        pass
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [_strip_quotes(part.strip()) for part in inner.split(",")]
    return _strip_quotes(value)


def _parse_manifest_services(path: Path) -> list[dict[str, Any]]:
    """Parse the small manifest subset this script owns: runtime.services[]."""
    if not path.exists():
        raise SystemExit(f"manifest not found: {path}")
    lines = path.read_text(encoding="utf-8").splitlines()
    services: list[dict[str, Any]] = []
    in_runtime = False
    in_services = False
    current: dict[str, Any] | None = None
    pending_list_key: str | None = None

    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()

        if indent == 0:
            in_runtime = line == "runtime:"
            in_services = False
            current = None
            pending_list_key = None
            continue
        if not in_runtime:
            continue
        if indent == 2 and line == "services:":
            in_services = True
            continue
        if not in_services:
            continue
        if indent == 4 and line.startswith("- "):
            current = {}
            services.append(current)
            pending_list_key = None
            rest = line[2:].strip()
            if rest and ":" in rest:
                key, value = rest.split(":", 1)
                current[key.strip()] = _parse_scalar(value)
            continue
        if current is None:
            continue
        if indent == 6 and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if value == "":
                current[key] = []
                pending_list_key = key
            else:
                current[key] = _parse_scalar(value)
                pending_list_key = None
            continue
        if indent >= 8 and line.startswith("- ") and pending_list_key:
            item = line[2:].strip()
            current.setdefault(pending_list_key, [])
            current[pending_list_key].append(_parse_scalar(item))

    for service in services:
        if not service.get("name"):
            raise SystemExit("runtime.services[] entry missing name")
        if not service.get("command"):
            raise SystemExit(f"runtime service {service['name']} missing command")
    return services


def _state_path(runtime_dir: Path) -> Path:
    return runtime_dir / "services.json"


def _load_state(runtime_dir: Path) -> dict[str, Any]:
    path = _state_path(runtime_dir)
    if not path.exists():
        return {"version": 1, "services": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "services": {}}


def _save_state(runtime_dir: Path, state: dict[str, Any]) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _state_path(runtime_dir).write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def _service_log(runtime_dir: Path, name: str) -> Path:
    path = runtime_dir / "logs" / f"{name}.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _append_log(runtime_dir: Path, name: str, message: str) -> None:
    with _service_log(runtime_dir, name).open("a", encoding="utf-8") as fh:
        fh.write(f"[{_now()}] {message}\n")


def _pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _run_shell(command: str, cwd: str | None, timeout: int = 15,
               env: dict[str, str] | None = None) -> tuple[int, str]:
    proc = subprocess.run(
        command,
        shell=True,
        cwd=cwd or None,
        env=env,
        text=True,
        capture_output=True,
        timeout=timeout,
        executable="/bin/bash" if Path("/bin/bash").exists() else None,
    )
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _load_env_file(path: str | None, cwd: str | None) -> dict[str, str]:
    if not path:
        return {}
    env_path = Path(path)
    if not env_path.is_absolute() and cwd:
        env_path = Path(cwd) / env_path
    if not env_path.exists():
        return {}
    values: dict[str, str] = {}
    for raw in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key or not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        values[key] = _strip_quotes(value.strip())
    return values


def _service_env(service: dict[str, Any]) -> dict[str, str]:
    cwd = str(service.get("cwd") or "") or None
    env = dict(os.environ)
    env.update(_load_env_file(str(service.get("env_file") or ""), cwd))
    return env


def _required_env_missing(service: dict[str, Any]) -> list[str]:
    raw = service.get("required_env") or service.get("env_required") or []
    names = [str(item).strip() for item in raw] if isinstance(raw, list) else [str(raw).strip()]
    env = _service_env(service)
    return [name for name in names if name and not env.get(name)]


def _port_open(host: str, port: int, timeout: float = 0.25) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _port_conflict(service: dict[str, Any]) -> tuple[bool, str]:
    port = service.get("port")
    if not port:
        return False, ""
    host = str(service.get("host") or "127.0.0.1")
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return False, f"invalid port: {port}"
    if _port_open(host, port_int):
        return True, f"{host}:{port_int} already accepts connections"
    return False, ""


_FAILURE_PATTERNS: list[tuple[str, str, str]] = [
    ("missing_dependency", r"(command not found|module not found|cannot find module|no module named|gem not found)", "install dependencies or declare install_command/self_heal"),
    ("port_conflict", r"(address already in use|eaddrinuse|port .* already|bind: address|already accepts connections)", "free the port or set kill_port_on_conflict/self_heal"),
    ("missing_env", r"(missing .*env|environment variable .* required|keyerror: ['\"][A-Za-z_][A-Za-z0-9_]*|undefined variable)", "set required env or declare env_file/env_setup_command"),
    ("migration_or_seed", r"(relation .* does not exist|no such table|migration|seed data|database .* empty)", "run migrations/seeds or declare seed_command"),
]


def _classify_failure(text: str) -> tuple[str, str]:
    lower = (text or "").lower()
    for label, pattern, action in _FAILURE_PATTERNS:
        if re.search(pattern, lower):
            return label, action
    return "unknown", "inspect runtime logs and add a bounded self_heal command"


def _log_excerpt(runtime_dir: Path, name: str, limit: int = 4000) -> str:
    path = _service_log(runtime_dir, name)
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")[-limit:]


def _health_ok(service: dict[str, Any], timeout: int = 10) -> tuple[bool, str]:
    healthcheck = str(service.get("healthcheck") or "").strip()
    if not healthcheck:
        return True, "no healthcheck configured"
    try:
        rc, output = _run_shell(
            healthcheck,
            str(service.get("cwd") or "") or None,
            timeout,
            env=_service_env(service),
        )
    except subprocess.TimeoutExpired:
        return False, f"healthcheck timed out after {timeout}s"
    return rc == 0, output or f"exit={rc}"


def _start_process(service: dict[str, Any], runtime_dir: Path) -> int:
    name = str(service["name"])
    log_path = _service_log(runtime_dir, name)
    cwd = str(service.get("cwd") or "") or None
    log = log_path.open("a", encoding="utf-8")
    log.write(f"[{_now()}] starting: {service['command']}\n")
    log.flush()
    proc = subprocess.Popen(
        str(service["command"]),
        shell=True,
        cwd=cwd,
        env=_service_env(service),
        stdout=log,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        executable="/bin/bash" if Path("/bin/bash").exists() else None,
    )
    return int(proc.pid)


def _stop_pid(pid: int | None, timeout: int = 5) -> None:
    if not _pid_alive(pid):
        return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not _pid_alive(pid):
            return
        time.sleep(0.1)
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _heal_commands(service: dict[str, Any]) -> list[str]:
    commands: list[str] = []
    raw = service.get("self_heal")
    if isinstance(raw, list):
        commands.extend(str(item) for item in raw if str(item).strip())
    for key in ("env_setup_command", "install_command", "repair_command", "seed_command", "heal_command"):
        if service.get(key):
            commands.append(str(service[key]))
    return commands


def _record_failure(entry: dict[str, Any], runtime_dir: Path, name: str, detail: str) -> None:
    excerpt = _log_excerpt(runtime_dir, name)
    failure_class, action = _classify_failure("\n".join([detail, excerpt]))
    entry.update({
        "status": "blocked",
        "health": "fail",
        "health_detail": detail,
        "failure_class": failure_class,
        "recommended_action": action,
        "last_log_excerpt": excerpt[-1000:],
        "updated_at": _now(),
    })


def _wait_ready(service: dict[str, Any], runtime_dir: Path, state_entry: dict[str, Any]) -> bool:
    name = str(service["name"])
    timeout = int(service.get("ready_timeout_sec") or 30)
    deadline = time.time() + timeout
    last = "not checked"
    while time.time() < deadline:
        if not _pid_alive(int(state_entry.get("pid") or 0)):
            last = "process exited"
            break
        ok, detail = _health_ok(service)
        last = detail
        if ok:
            state_entry.update({"status": "running", "health": "pass", "health_detail": detail, "updated_at": _now()})
            _append_log(runtime_dir, name, f"ready: {detail}")
            return True
        time.sleep(1)
    _record_failure(state_entry, runtime_dir, name, last)
    _append_log(runtime_dir, name, f"not ready: {last}")
    return False


def _run_heal_command(service: dict[str, Any], runtime_dir: Path, entry: dict[str, Any], cmd: str, attempt_number: int) -> None:
    name = str(service["name"])
    _append_log(runtime_dir, name, f"self-heal attempt {attempt_number}: {cmd}")
    try:
        rc, output = _run_shell(
            cmd,
            str(service.get("cwd") or "") or None,
            int(service.get("heal_timeout_sec") or 60),
            env=_service_env(service),
        )
    except subprocess.TimeoutExpired:
        rc, output = 124, "self-heal command timed out"
    _append_log(runtime_dir, name, f"self-heal result rc={rc}: {output}")
    entry.setdefault("self_heal_attempts", []).append({"ts": _now(), "command": cmd, "exit": rc, "output": output[-1000:]})


def _preflight_service(service: dict[str, Any], runtime_dir: Path, entry: dict[str, Any]) -> bool:
    name = str(service["name"])
    missing_env = _required_env_missing(service)
    if missing_env:
        env_setup = str(service.get("env_setup_command") or "").strip()
        if env_setup:
            _run_heal_command(service, runtime_dir, entry, env_setup, 0)
            missing_env = _required_env_missing(service)
        if missing_env:
            detail = f"missing required env: {', '.join(missing_env)}"
            _record_failure(entry, runtime_dir, name, detail)
            _append_log(runtime_dir, name, detail)
            print(f"{name}: BLOCKED ({detail})")
            return False

    conflict, detail = _port_conflict(service)
    if conflict:
        if service.get("kill_port_on_conflict", False):
            cmd = f"if command -v lsof >/dev/null 2>&1; then lsof -ti tcp:{int(service['port'])} | xargs -r kill; else exit 127; fi"
            _run_heal_command(service, runtime_dir, entry, cmd, 0)
            conflict, detail = _port_conflict(service)
        if conflict:
            _record_failure(entry, runtime_dir, name, detail)
            _append_log(runtime_dir, name, f"port conflict: {detail}")
            print(f"{name}: BLOCKED ({detail})")
            return False
    return True


def _select_services(all_services: list[dict[str, Any]], names: list[str]) -> list[dict[str, Any]]:
    if not names:
        return all_services
    wanted = set(names)
    selected = [service for service in all_services if str(service["name"]) in wanted]
    missing = wanted - {str(service["name"]) for service in selected}
    if missing:
        raise SystemExit(f"unknown runtime service(s): {', '.join(sorted(missing))}")
    return selected


def start_services(args: argparse.Namespace) -> int:
    services = _select_services(_parse_manifest_services(args.manifest), args.services)
    runtime_dir = args.runtime_dir
    state = _load_state(runtime_dir)
    state.setdefault("services", {})
    exit_code = 0

    for service in services:
        name = str(service["name"])
        entry = state["services"].setdefault(name, {})
        pid = int(entry.get("pid") or 0)
        if _pid_alive(pid):
            ok, detail = _health_ok(service)
            if ok:
                entry.update({"status": "running", "health": "pass", "health_detail": detail, "updated_at": _now()})
                _append_log(runtime_dir, name, f"reuse healthy pid={pid}: {detail}")
                print(f"{name}: running pid={pid} ({detail})")
                continue
            _append_log(runtime_dir, name, f"existing pid unhealthy, restarting pid={pid}: {detail}")
            _stop_pid(pid)

        if not _preflight_service(service, runtime_dir, entry):
            exit_code = 1
            _save_state(runtime_dir, state)
            continue

        attempts = ["initial"] + [f"self-heal:{cmd}" for cmd in _heal_commands(service)]
        if not _heal_commands(service) and service.get("restart_on_fail", True):
            attempts.append("restart")
        for attempt_index, attempt in enumerate(attempts, start=1):
            if attempt.startswith("self-heal:"):
                cmd = attempt.split(":", 1)[1]
                _run_heal_command(service, runtime_dir, entry, cmd, attempt_index - 1)

            pid = _start_process(service, runtime_dir)
            entry.update({
                "name": name,
                "pid": pid,
                "command": str(service["command"]),
                "cwd": str(service.get("cwd") or ""),
                "status": "starting",
                "health": "pending",
                "started_at": _now(),
                "updated_at": _now(),
                "log": str(_service_log(runtime_dir, name)),
            })
            _save_state(runtime_dir, state)
            if _wait_ready(service, runtime_dir, entry):
                _save_state(runtime_dir, state)
                print(f"{name}: started pid={pid}")
                break
            _stop_pid(pid)
            _save_state(runtime_dir, state)
        else:
            print(f"{name}: BLOCKED ({entry.get('health_detail')})")
            exit_code = 1
    _save_state(runtime_dir, state)
    return exit_code


def status_services(args: argparse.Namespace) -> int:
    all_services = _parse_manifest_services(args.manifest)
    selected = _select_services(all_services, args.services)
    state = _load_state(args.runtime_dir)
    exit_code = 0
    for service in selected:
        name = str(service["name"])
        entry = state.get("services", {}).get(name, {})
        pid = int(entry.get("pid") or 0)
        alive = _pid_alive(pid)
        ok, detail = _health_ok(service) if alive else (False, "not running")
        status = "running" if alive and ok else "blocked" if entry.get("status") == "blocked" else "stopped"
        print(f"{name}: {status} pid={pid or '-'} health={'pass' if ok else 'fail'} detail={detail}")
        if status != "running":
            exit_code = 1
    return exit_code


def stop_services(args: argparse.Namespace) -> int:
    services = _select_services(_parse_manifest_services(args.manifest), args.services)
    state = _load_state(args.runtime_dir)
    for service in services:
        name = str(service["name"])
        entry = state.get("services", {}).setdefault(name, {})
        stop_command = str(service.get("stop_command") or "").strip()
        if stop_command:
            _append_log(args.runtime_dir, name, f"stop_command: {stop_command}")
            rc, output = _run_shell(stop_command, str(service.get("cwd") or "") or None, int(service.get("stop_timeout_sec") or 30))
            _append_log(args.runtime_dir, name, f"stop_command rc={rc}: {output}")
        else:
            _stop_pid(int(entry.get("pid") or 0), int(service.get("stop_timeout_sec") or 5))
        entry.update({"status": "stopped", "health": "stopped", "updated_at": _now()})
        print(f"{name}: stopped")
    _save_state(args.runtime_dir, state)
    return 0


def logs_services(args: argparse.Namespace) -> int:
    names = args.services or [path.stem for path in sorted((args.runtime_dir / "logs").glob("*.log"))]
    for name in names:
        path = _service_log(args.runtime_dir, name)
        print(f"==> {path} <==")
        if not path.exists():
            print("(no log)")
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        for line in lines[-args.tail:]:
            print(line)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manage harness runtime background services")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--runtime-dir", type=Path, default=DEFAULT_RUNTIME_DIR)
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("start", "status", "stop"):
        p = sub.add_parser(name)
        p.add_argument("services", nargs="*")
    p_logs = sub.add_parser("logs")
    p_logs.add_argument("services", nargs="*")
    p_logs.add_argument("--tail", type=int, default=80)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "start":
        return start_services(args)
    if args.cmd == "status":
        return status_services(args)
    if args.cmd == "stop":
        return stop_services(args)
    if args.cmd == "logs":
        return logs_services(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
