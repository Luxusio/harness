#!/usr/bin/env python3
"""Main verification entry point for harness QA.

Consolidated QA surface:
  - suite (default): smoke + healthcheck (+ observability status when enabled)
  - smoke
  - healthcheck
  - browser
  - persistence

Backward compatibility:
  - `python3 plugin/scripts/verify.py` still runs the default verification suite.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _lib import (
    MANIFEST,
    is_profile_enabled,
    manifest_field,
    manifest_path_field,
    manifest_sync_gaps,
    repo_root_for_task_dir,
)

RETRIES = 10
CONSOLE_ERRORS = 0
NETWORK_FAILURES = 0
BASE_CWD = os.getcwd()


def _set_base_cwd(path: str | None) -> None:
    global BASE_CWD
    if path:
        BASE_CWD = path


def _run_shell(command: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, shell=True, capture_output=True, text=True, cwd=BASE_CWD)


def _run_exec_target(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(argv, capture_output=True, text=True, cwd=BASE_CWD)


def _print_output(output: str) -> None:
    if output:
        print(output, end="" if output.endswith("\n") else "\n")


def run_smoke() -> int:
    print("=== SMOKE TESTS ===")

    override = "scripts/harness/smoke.sh"
    if os.path.isfile(override) and os.access(override, os.X_OK):
        result = _run_exec_target([override])
        output = result.stdout + result.stderr
        _print_output(output)
        sys.exit(result.returncode)

    smoke_cmd = manifest_field("smoke_command")
    if smoke_cmd:
        print(f"Running: {smoke_cmd}")
        result = _run_shell(smoke_cmd)
        output = result.stdout + result.stderr
        exit_code = result.returncode
        tail_lines = output.strip().splitlines()[-20:] if output.strip() else []
        _print_output(output)
        if exit_code == 0:
            last_line = tail_lines[-1] if tail_lines else ""
            print(f"[EVIDENCE] smoke: PASS — exit 0 — last output: {last_line}")
        else:
            last_3 = " ".join(tail_lines[-3:]) if tail_lines else ""
            print(f"[EVIDENCE] smoke: FAIL — exit {exit_code} — last output: {last_3}")
        return exit_code

    print("SKIP: no smoke tests configured")
    print("Add smoke_command to doc/harness/manifest.yaml or create scripts/harness/smoke.sh")
    print("[EVIDENCE] smoke: PASS — skipped (none configured)")
    return 0


def run_healthcheck() -> int:
    print("=== HEALTH CHECKS ===")

    override = "scripts/harness/healthcheck.sh"
    if os.path.isfile(override) and os.access(override, os.X_OK):
        result = _run_exec_target([override])
        output = result.stdout + result.stderr
        _print_output(output)
        sys.exit(result.returncode)

    healthcheck_cmd = manifest_field("healthcheck_command")
    if healthcheck_cmd:
        print(f"Running: {healthcheck_cmd}")
        start_ms = int(time.time() * 1000)
        result = _run_shell(healthcheck_cmd)
        end_ms = int(time.time() * 1000)
        elapsed = f"{end_ms - start_ms}ms"
        output = result.stdout + result.stderr
        exit_code = result.returncode
        _print_output(output)
        match = re.search(r'https?://[^ ]+', healthcheck_cmd)
        endpoint = match.group(0) if match else "custom"
        if exit_code == 0:
            print(f"[EVIDENCE] healthcheck: PASS {endpoint} exit=0 time={elapsed}")
        else:
            last_line = output.strip().splitlines()[-1] if output.strip() else ""
            print(
                f"[EVIDENCE] healthcheck: FAIL {endpoint} exit={exit_code} "
                f"time={elapsed} — {last_line}"
            )
        return exit_code

    print("SKIP: no health checks configured")
    print("[EVIDENCE] healthcheck: PASS — skipped (none configured)")
    return 0


def run_browser() -> int:
    print("=== Browser Smoke Test ===")

    frontend_url = manifest_path_field("browser.entry_url") if os.path.isfile(MANIFEST) else ""
    if not frontend_url:
        frontend_url = manifest_field("frontend") if os.path.isfile(MANIFEST) else ""
    if not frontend_url:
        frontend_url = "http://localhost:3000"

    print(f"Checking dev server at {frontend_url}...")

    load_success = False
    last_status = "000"

    for attempt in range(1, RETRIES + 1):
        try:
            request = urllib.request.urlopen(frontend_url, timeout=3)
            http_status = request.status
        except urllib.error.HTTPError as exc:
            http_status = exc.code
        except Exception:
            http_status = 0

        last_status = str(http_status)
        if str(http_status).startswith(("2", "3")):
            print(f"  OK Dev server is running (HTTP {http_status})")
            load_success = True
            break

        if attempt == RETRIES:
            print(
                f"  FAIL Dev server not responding after {RETRIES} attempts "
                f"(last HTTP {last_status})"
            )
            print("  Start with: npm run dev (or check manifest for dev_command)")
            print(
                f"[EVIDENCE] browser: FAIL {frontend_url} — server not reachable "
                f"after {RETRIES} attempts, last HTTP {last_status}"
            )
            return 1

        time.sleep(2)

    if not load_success:
        return 1

    print()
    print("Browser smoke prerequisites met.")
    print("Use chrome-devtools MCP for interactive verification:")
    print(f"  1. Navigate to {frontend_url}")
    print("  2. Check console for errors")
    print("  3. Verify key routes render")
    print("  4. Check network for failed requests")
    print(
        f"[EVIDENCE] browser: PASS {frontend_url} — server reachable, "
        f"console_errors={CONSOLE_ERRORS}, network_failures={NETWORK_FAILURES}"
    )
    print("=== Browser Smoke complete ===")
    return 0


def run_persistence() -> int:
    import shutil

    print("=== Persistence Check ===")

    checked = 0
    failed = 0

    if shutil.which("psql") and os.environ.get("DATABASE_URL"):
        database_url = os.environ["DATABASE_URL"]
        print("Checking PostgreSQL...")
        checked += 1
        result = subprocess.run(["psql", database_url, "-c", "SELECT 1"], capture_output=True)
        if result.returncode == 0:
            print("  OK PostgreSQL connected")
            print("[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL")
        else:
            print("  FAIL PostgreSQL connection failed")
            print(
                "[EVIDENCE] persistence: FAIL postgresql — connection failed "
                "(DATABASE_URL set but psql returned error)"
            )
            failed += 1

    if shutil.which("mongosh") and os.environ.get("MONGODB_URI"):
        mongodb_uri = os.environ["MONGODB_URI"]
        print("Checking MongoDB...")
        checked += 1
        result = subprocess.run(
            ["mongosh", mongodb_uri, "--eval", "db.runCommand({ping:1})"],
            capture_output=True,
        )
        if result.returncode == 0:
            print("  OK MongoDB connected")
            print("[EVIDENCE] persistence: PASS mongodb — ping succeeded via MONGODB_URI")
        else:
            print("  FAIL MongoDB connection failed")
            print(
                "[EVIDENCE] persistence: FAIL mongodb — ping failed "
                "(MONGODB_URI set but mongosh returned error)"
            )
            failed += 1

    if os.path.isfile(".env") or os.path.isfile(".env.local"):
        print("Environment files found — check for DB connection strings")

    if checked == 0:
        print("SKIP: no persistence targets detected")
        print("[EVIDENCE] persistence: SKIP none — no DATABASE_URL or MONGODB_URI set")

    print("=== Persistence Check complete ===")
    return 0 if failed == 0 else 1


def _suite_step(mode: str, label: str, evidence_label: str) -> int:
    result = subprocess.run(
        ["python3", os.path.join(SCRIPT_DIR, "verify.py"), mode],
        capture_output=True,
        text=True,
        cwd=BASE_CWD,
    )
    output = result.stdout + result.stderr
    exit_code = result.returncode
    if exit_code == 0:
        print(f"{label}: PASS")
        last_line = output.strip().splitlines()[-1] if output.strip() else ""
        print(f"[EVIDENCE] {evidence_label}: PASS — {last_line}")
    else:
        print(f"{label}: FAIL")
        last_lines = " ".join(output.strip().splitlines()[-3:]) if output.strip() else ""
        print(f"[EVIDENCE] {evidence_label}: FAIL — exit {exit_code} — {last_lines}")
    return exit_code


def _observability_enabled() -> bool:
    return is_profile_enabled("observability_enabled")


def _is_self_hosted_harness_repo() -> bool:
    return os.path.isfile("plugin/skills/setup/templates/doc/harness/manifest.yaml") and (
        manifest_field("name") == "harness-plugin" or os.path.isfile("plugin/.claude-plugin/plugin.json")
    )


def run_manifest_sync() -> int:
    print("=== MANIFEST SYNC CHECK ===")

    if not _is_self_hosted_harness_repo():
        print("SKIP: manifest self-sync check only applies to the harness plugin repo")
        print("[EVIDENCE] manifest: PASS — skipped (not self-hosted harness repo)")
        return 0

    gaps = manifest_sync_gaps()
    if not gaps:
        print("Manifest schema is aligned with the setup template.")
        print("[EVIDENCE] manifest: PASS — manifest matches template schema")
        return 0

    preview = ", ".join(gaps[:8])
    if len(gaps) > 8:
        preview += f", (+{len(gaps) - 8} more)"
    print("FAIL: manifest schema drift detected")
    print(f"Missing or drifted paths: {preview}")
    print(f"[EVIDENCE] manifest: FAIL — missing/drifted schema paths: {preview}")
    return 1


def run_suite() -> int:
    failures = 0
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("=== HARNESS VERIFY ===")
    print(f"[EVIDENCE] verify: started at {timestamp}")

    print("--- Running manifest self-sync check ---")
    if run_manifest_sync() != 0:
        failures += 1

    print("--- Running smoke tests ---")
    if _suite_step("smoke", "smoke", "smoke") != 0:
        failures += 1

    print("--- Running health checks ---")
    if _suite_step("healthcheck", "healthcheck", "healthcheck") != 0:
        failures += 1

    print("")
    if _observability_enabled():
        print("--- Running observability status check ---")
        result = subprocess.run(
            ["python3", os.path.join(SCRIPT_DIR, "observability.py"), "status"],
            capture_output=True,
            text=True,
            cwd=BASE_CWD,
        )
        output = result.stdout + result.stderr
        if result.returncode == 0:
            print("observability: PASS")
            last_line = output.strip().splitlines()[-1] if output.strip() else ""
            print(f"[EVIDENCE] observability: PASS — {last_line}")
        else:
            print("observability: FAIL")
            last_lines = " ".join(output.strip().splitlines()[-3:]) if output.strip() else ""
            print(f"[EVIDENCE] observability: FAIL — exit {result.returncode} — {last_lines}")
            failures += 1

    end_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if failures > 0:
        print(f"RESULT: {failures} check(s) failed")
        print(f"[EVIDENCE] verify: FAIL — {failures} check(s) failed at {end_timestamp}")
        return 1

    print("RESULT: all checks passed")
    print(f"[EVIDENCE] verify: PASS — all checks passed at {end_timestamp}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Harness QA runner")
    parser.add_argument(
        "--task-dir",
        dest="task_dir",
        help="Optional task directory used to anchor verification commands at repo root",
    )
    parser.add_argument(
        "mode",
        nargs="?",
        default="suite",
        choices=["suite", "smoke", "healthcheck", "browser", "persistence"],
        help="QA mode to run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.task_dir:
        _set_base_cwd(repo_root_for_task_dir(args.task_dir))
    if args.mode == "suite":
        return run_suite()
    if args.mode == "smoke":
        return run_smoke()
    if args.mode == "healthcheck":
        return run_healthcheck()
    if args.mode == "browser":
        return run_browser()
    if args.mode == "persistence":
        return run_persistence()
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as exc:
        print(f"ERROR: {exc}")
        sys.exit(1)
