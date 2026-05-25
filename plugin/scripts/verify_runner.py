#!/usr/bin/env python3
"""Run manifest verify_commands with optional parallel execution.

The runner is intentionally small and deterministic: output order always
matches manifest order, even when commands run concurrently.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def _find_repo_root(start: str | None = None) -> Path:
    cur = Path(start or os.getcwd()).resolve()
    while cur != cur.parent:
        if (cur / ".git").is_dir():
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
    parser.add_argument("commands", nargs="*", help="Override manifest verify_commands.")
    args = parser.parse_args(argv)

    repo_root = _find_repo_root()
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
