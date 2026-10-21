#!/usr/bin/env python3
"""Run manifest verify_commands with optional parallel execution.

The runner is intentionally small and deterministic: output order always
matches manifest order, even when commands run concurrently.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# Loaded lazily by the argv job path; the legacy manifest runner stays standalone.
SCHEMA = "harness.verification-execution"
VERSION = 1


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _file_path(root, name):
    import wbs
    wbs.input_policy({"source": [name]})
    if name == ".":
        raise ValueError("output/artifact must name a file")
    path = root / name
    for part in (path, *path.parents):
        if part.is_symlink():
            raise ValueError("symlink output/artifact: " + name)
        if part == root:
            break
    if path.exists() and not path.is_file():
        raise ValueError("output/artifact must be a regular file: " + name)
    return path


def _lease(keys):
    """Try all stable host-wide leases; waiters never occupy pool workers."""
    import fcntl
    import stat
    directory = Path.home() / ".cache" / "harness-verification-leases"
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError("unsafe lease directory")
    held = []
    try:
        for key, exclusive in sorted(keys.items()):
            fd = os.open(directory / _digest(key), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            held.append(fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError("unsafe lease file")
            try:
                fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                for opened in held:
                    os.close(opened)
                return None
        return held
    except BaseException:
        for fd in held:
            os.close(fd)
        raise


def _path_leases(keys, path, exclusive):
    # Shared ancestors permit independent sibling files but exclude a writer
    # whose declared leaf is an ancestor of this path.
    for parent in path.parents:
        keys.setdefault("file:" + str(parent), False)
    key = "file:" + str(path)
    keys[key] = keys.get(key, False) or exclusive


def _scoped_call(code, value, root, env, held=()):
    # Context probes see exactly the command environment. A command supervisor
    # inherits ownership and survives runner death until _execute reaps its group.
    prefix = "import json,os,sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);value=json.loads(sys.argv[2]);root=Path(sys.argv[3])\n"
    proc = subprocess.run([sys.executable, "-B", "-c", prefix + code,
                           str(Path(__file__).resolve().parent), json.dumps(value), str(root)],
                          cwd=str(root), env=env, pass_fds=tuple(held), capture_output=True, text=True)
    if proc.returncode:
        raise ValueError("scoped execution unavailable: " + proc.stderr[-4000:])
    return json.loads(proc.stdout)


def _job_spec(value, root):
    import verification_target as vt
    allowed = {"job_id", "outcome_id", "kind", "argv", "inputs", "outputs", "resources", "requires", "environment", "toolchains"}
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError("unknown job fields")
    for key in ("job_id", "outcome_id"):
        if not isinstance(value.get(key), str) or not value[key] or len(value[key]) > 128:
            raise ValueError("explicit bounded " + key + " required")
    if value.get("kind") not in {"producer", "consumer"}:
        raise ValueError("kind must be producer or consumer")
    spec = vt._spec(dict(inputs=value.get("inputs"), commands=[value.get("argv")],
                         environment=value.get("environment", []), toolchains=value.get("toolchains", []), purpose="diagnostic"))
    job = dict(value, spec=spec)
    for key in ("outputs", "resources", "requires"):
        items = value.get(key, [])
        if not isinstance(items, list) or len(items) > 32 or any(not isinstance(x, str) or not x or len(x) > 2048 for x in items) or len(set(items)) != len(items):
            raise ValueError("invalid " + key)
        job[key] = items
    if job["kind"] == "consumer" and job["outputs"]:
        raise ValueError("consumers must be read-only")
    for name in job["outputs"] + spec["inputs"]["artifacts"]:
        _file_path(root, name)
    vt._paths(root, dict(spec["inputs"], artifacts=[]))
    return job


def run_jobs(jobs, repo_root, *, task_id, run_id, execution_generation, results_path,
             capabilities=(), max_workers=4, timeout=60):
    """One producer and its read-only consumers; records are advisory, not QA."""
    import verification_target as vt
    import wbs
    from _lib import _atomic_text_write
    from prewrite_gate import _is_protected_artifact
    root, results_path = Path(repo_root).resolve(), Path(results_path).absolute()
    _file_path(Path(results_path.anchor), results_path.relative_to(results_path.anchor).as_posix())
    if _is_protected_artifact(str(results_path), repo_root=str(root)):
        raise ValueError("execution results cannot overwrite protected lifecycle artifacts")
    if os.name != "posix" or not all(isinstance(x, str) and x for x in (task_id, run_id, execution_generation)):
        raise ValueError("POSIX host and explicit task/run/execution_generation required")
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= 32 or not 0 < timeout <= 3600 or not 1 <= max_workers <= 32:
        raise ValueError("bounded jobs, workers and timeout required")
    jobs = [_job_spec(job, root) for job in jobs]
    producers = [job for job in jobs if job["kind"] == "producer"]
    if len(producers) > 1 or len({job["job_id"] for job in jobs}) != len(jobs):
        raise ValueError("unique job IDs and at most one producer per invocation required")
    outputs = [name for job in jobs for name in job["outputs"]]
    for i, name in enumerate(outputs):
        if any(wbs.covers(name, other) or wbs.covers(other, name) for other in outputs[i + 1:]):
            raise ValueError("overlapping output ownership")
    for job in jobs:
        policy = job["spec"]["inputs"]
        for name in outputs:
            if any(wbs.covers(p, name) or wbs.covers(name, p) for p in policy["source"]):
                raise ValueError("output overlaps source inputs")
        if job["kind"] == "producer" and any(wbs.covers(a, o) or wbs.covers(o, a) for a in policy["artifacts"] for o in outputs):
            raise ValueError("producer cannot consume its own output")
        for name in policy["source"] + policy["artifacts"] + outputs:
            selected = root / name
            if results_path == selected or selected in results_path.parents or results_path in selected.parents:
                raise ValueError("results path overlaps selected inputs/outputs")
    # Serialize only callers writing this same result file; jobs in different
    # invocations share the resource/artifact leases below.
    keys = {}
    _path_leases(keys, results_path, True)
    deadline = time.monotonic() + timeout
    while (result_lease := _lease(keys)) is None:
        if time.monotonic() >= deadline:
            raise TimeoutError("result lease timeout")
        time.sleep(0.01)
    records = {}
    try:
        try:
            previous = json.loads(results_path.read_text())
            if not isinstance(previous, dict) or any(previous.get(k) != v for k, v in dict(schema=SCHEMA, version=VERSION, task_id=task_id, run_id=run_id).items()):
                previous = {}
            if not isinstance(previous.get("execution_generation"), str) or not previous.get("execution_generation"):
                previous = {}
            old = {r["job_id"]: r for r in previous.get("commands", []) if isinstance(r, dict) and isinstance(r.get("job_id"), str)}
        except (OSError, ValueError, TypeError, KeyError):
            old = {}

        def publish():
            ordered = [records[j["job_id"]] for j in jobs if j["job_id"] in records]
            ok = len(ordered) == len(jobs) and all(r["status"] == "PASS" for r in ordered)
            payload = dict(schema=SCHEMA, version=VERSION, task_id=task_id, run_id=run_id,
                           execution_generation=execution_generation, status="PASS" if ok else "FAIL",
                           returncode=0 if ok else 1, commands=ordered)
            _atomic_text_write(str(results_path), json.dumps(payload, sort_keys=True))
            return payload

        def execute(job, held):
            record = dict(job_id=job["job_id"], outcome_id=job["outcome_id"], kind=job["kind"],
                          argv=job["argv"], scope=job["spec"]["inputs"], outputs=job["outputs"],
                          execution_generation=execution_generation, origin_generation=execution_generation,
                          state="executor_error", status="FAIL", returncode=None, reused=False)
            try:
                available = set(capabilities) | {"platform:" + sys.platform}
                missing = [c for c in job["requires"] if c not in available and not (c.startswith("executable:") and shutil.which(c[11:]))]
                if missing:
                    record.update(state="capability_unavailable", error="missing capabilities: " + ", ".join(missing))
                    return record
                search_path = os.pathsep.join(str((root / p).resolve()) if not os.path.isabs(p) else p
                                             for p in os.get_exec_path())
                command = job["argv"][0]
                executable = shutil.which(str(root / command) if os.sep in command else command, path=search_path)
                if not executable:
                    record.update(state="start_failed", error="executable unavailable")
                    return record
                spec = dict(job["spec"])
                spec["environment"] = sorted(set(spec["environment"]) | {"PATH"})
                spec["toolchains"] = sorted(set(spec["toolchains"]) | {executable})
                argv = [executable, *job["argv"][1:]]
                env = {key: os.environ[key] for key in spec["environment"] if key in os.environ}
                def observe_context():
                    return _scoped_call("import verification_target as vt\nprint(json.dumps(vt._context(value, root)))", spec, root, env)
                context = observe_context()
                def observe():
                    return vt._manifest(root, vt._paths(root, spec["inputs"]))
                inputs = observe()
                if job["kind"] == "consumer":
                    expected = {row[0]: row for p in producers for row in records[p["job_id"]].get("artifact_manifest", [])}
                    if any(row != expected[row[0]] for row in inputs if row[0] in expected):
                        raise ValueError("prepared artifact changed before consumer admission")
                fingerprint = _digest(dict(job=job, context=context, argv=argv, inputs=inputs))
                record.update(input_fingerprint=_digest(inputs), context_fingerprint=fingerprint)
                prior = old.get(job["job_id"], {})
                def artifacts():
                    return vt._manifest(root, job["outputs"])
                try:
                    output_hash = _digest(artifacts())
                except (OSError, ValueError):
                    output_hash = None
                if (prior.get("context_fingerprint") == fingerprint and prior.get("artifact_fingerprint") == output_hash
                        and output_hash is not None and prior.get("status") == "PASS" and prior.get("state") == "completed"
                        and prior.get("returncode") == 0 and prior.get("outcome_id") == job["outcome_id"]
                        and isinstance(prior.get("execution_generation"), str) and prior["execution_generation"]
                        and isinstance(prior.get("origin_generation"), str) and prior["origin_generation"]
                        and prior.get("input_fingerprint") == record["input_fingerprint"]
                        and prior.get("argv") == job["argv"] and prior.get("scope") == record["scope"]
                        and prior.get("kind") == job["kind"] and prior.get("outputs") == job["outputs"]):
                    if inputs != observe() or context != observe_context():
                        raise ValueError("inputs/context changed during reuse validation")
                    record.update(state="completed", status="PASS", returncode=0, reused=True,
                                  artifact_fingerprint=output_hash, artifact_manifest=artifacts(), origin_generation=prior.get("origin_generation"))
                    return record
                try:
                    code = "from wbs import _execute\ntry:\n result=_execute(value['argv'], str(root), value['timeout'], env=dict(os.environ))\nexcept OSError as exc:\n result=dict(returncode=None,error=str(exc),start_failed=True,output='')\nprint(json.dumps(result))"
                    result = _scoped_call(code, dict(argv=argv, timeout=timeout), root, env, [*held, *result_lease])
                except OSError as exc:
                    record.update(state="start_failed", error=str(exc))
                    return record
                state = "start_failed" if result.get("start_failed") else "timeout" if result["error"] == "timeout" else "executor_error" if result["error"] else "command_failure" if result["returncode"] else "completed"
                record.update(result, state=state)
                if inputs != observe() or context != observe_context():
                    record.update(state="executor_error", error="inputs/context changed during execution")
                if record["state"] == "completed":
                    output_manifest = artifacts()
                    record.update(artifact_fingerprint=_digest(output_manifest), artifact_manifest=output_manifest, status="PASS")
                return record
            except Exception as exc:
                record.update(state="executor_error", error=str(exc))
                return record

        for stage in (producers, [j for j in jobs if j["kind"] == "consumer"]):
            pending, running = list(stage), {}
            lease_deadline = time.monotonic() + timeout
            if stage and stage[0]["kind"] == "consumer" and any(records[p["job_id"]]["status"] != "PASS" for p in producers):
                for job in stage:
                    records[job["job_id"]] = dict(job_id=job["job_id"], outcome_id=job["outcome_id"], kind="consumer", argv=job["argv"], state="blocked_dependency", status="FAIL", returncode=None, reused=False, execution_generation=execution_generation)
                publish()
                continue
            with ThreadPoolExecutor(max_workers=max_workers) as pool:
                while pending or running:
                    for job in list(pending):
                        if len(running) >= max_workers:
                            break
                        keys = {"resource:" + r: True for r in job["resources"]}
                        for name in job["outputs"]:
                            _path_leases(keys, _file_path(root, name), True)
                        for name in job["spec"]["inputs"]["artifacts"]:
                            _path_leases(keys, _file_path(root, name), False)
                        held = _lease(keys)
                        if held is not None:
                            pending.remove(job)
                            running[pool.submit(execute, job, held)] = held
                    for future in list(running):
                        if future.done():
                            held = running.pop(future)
                            try:
                                record = future.result()
                                records[record["job_id"]] = record
                                publish()
                                lease_deadline = time.monotonic() + timeout
                            finally:
                                for fd in held:
                                    os.close(fd)
                    if pending and not running and time.monotonic() >= lease_deadline:
                        raise TimeoutError("job resource lease timeout")
                    if pending or running:
                        time.sleep(0.01)
        return publish()
    finally:
        for fd in result_lease:
            os.close(fd)


def _find_repo_root(start: str | None = None) -> Path:
    cur = Path(start or os.getcwd()).resolve()
    while cur != cur.parent:
        # A linked worktree's ``.git`` is a gitfile; stopping there keeps
        # verify commands in the worktree instead of the main checkout.
        git = cur / ".git"
        if git.is_dir() or git.is_file():
            return cur
        cur = cur.parent
    return Path(start or os.getcwd()).resolve()


def _read_verify_commands(repo_root: Path) -> list[str]:
    manifest = repo_root / "doc" / "harness" / "manifest.yaml"
    if not manifest.is_file():
        return []
    lines = manifest.read_text(encoding="utf-8").splitlines()
    commands: list[str] = []
    in_block = False
    for line in lines:
        if line.startswith("verify_commands:"):
            in_block = True
            rest = line.split(":", 1)[1].strip()
            if rest and rest not in {"[]", "null", "~"}:
                if rest.startswith("[") and rest.endswith("]"):
                    inner = rest[1:-1].strip()
                    if inner:
                        commands.extend(x.strip().strip('"').strip("'") for x in inner.split(",") if x.strip())
                else:
                    commands.append(rest.strip('"').strip("'"))
            continue
        if in_block:
            if line.startswith("  - "):
                commands.append(line.split("  - ", 1)[1].strip().strip('"').strip("'"))
                continue
            if line and not line.startswith(" ") and not line.startswith("#"):
                break
    return [cmd for cmd in commands if cmd]


def _run_one(index: int, command: str, cwd: Path, timeout: int | None) -> dict:
    started = time.time()
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
        status = "PASS" if proc.returncode == 0 else "FAIL"
        return {
            "index": index,
            "command": command,
            "status": status,
            "returncode": proc.returncode,
            "duration_sec": round(time.time() - started, 3),
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "index": index,
            "command": command,
            "status": "FAIL",
            "returncode": 124,
            "duration_sec": round(time.time() - started, 3),
            "stdout": (exc.stdout or "")[-4000:] if isinstance(exc.stdout, str) else "",
            "stderr": f"timeout after {timeout}s",
        }


def run(commands: list[str], repo_root: Path, *, parallel: bool, max_workers: int, timeout: int | None) -> dict:
    started = time.time()
    if not commands:
        return {
            "status": "PASS",
            "returncode": 0,
            "parallel": parallel,
            "commands": [],
            "duration_sec": 0.0,
        }

    if parallel and len(commands) > 1:
        results: list[dict | None] = [None] * len(commands)
        workers = max(1, min(max_workers, len(commands)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(_run_one, index, command, repo_root, timeout): index
                for index, command in enumerate(commands)
            }
            for future in as_completed(futures):
                result = future.result()
                results[result["index"]] = result
        ordered = [r for r in results if r is not None]
    else:
        ordered = [_run_one(index, command, repo_root, timeout) for index, command in enumerate(commands)]

    ok = all(result["returncode"] == 0 for result in ordered)
    return {
        "status": "PASS" if ok else "FAIL",
        "returncode": 0 if ok else 1,
        "parallel": bool(parallel and len(commands) > 1),
        "commands": ordered,
        "duration_sec": round(time.time() - started, 3),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true", help="Emit JSON summary.")
    parser.add_argument("--parallel", action="store_true", help="Run commands concurrently.")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout", type=int, default=0, help="Per-command timeout seconds; 0 disables.")
    parser.add_argument("--root", type=Path, help="Exact execution root, including a frozen view without .git.")
    parser.add_argument("--jobs", type=Path, help="JSON array: at most one producer and its read-only consumers (argv only).")
    parser.add_argument("--results", type=Path, help="Durable advisory execution JSON, outside selected inputs/outputs.")
    parser.add_argument("--task-id")
    parser.add_argument("--run-id")
    parser.add_argument("--generation")
    parser.add_argument("--capability", action="append", default=[], help="Actual host-provided agent capability; tools/platform are detected.")
    parser.add_argument("commands", nargs="*", help="Override manifest verify_commands.")
    args = parser.parse_args(argv)

    repo_root = args.root.resolve() if args.root else _find_repo_root()
    if args.jobs:
        if args.commands or not args.results:
            parser.error("--jobs requires --results and cannot be combined with shell commands")
        try:
            payload = run_jobs(json.loads(args.jobs.read_text()), repo_root, task_id=args.task_id,
                               run_id=args.run_id, execution_generation=args.generation,
                               results_path=args.results, capabilities=args.capability,
                               max_workers=args.max_workers, timeout=args.timeout or 60)
        except (OSError, ValueError, TypeError) as exc:
            parser.error(str(exc))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return payload["returncode"]
    commands = args.commands or _read_verify_commands(repo_root)
    payload = run(
        commands,
        repo_root,
        parallel=args.parallel,
        max_workers=args.max_workers,
        timeout=args.timeout or None,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        for result in payload["commands"]:
            print(f"[{result['status']}] {result['command']} ({result['duration_sec']}s)")
        print(f"verify_runner: {payload['status']} ({payload['duration_sec']}s)")
    return int(payload["returncode"])


if __name__ == "__main__":
    sys.exit(main())
