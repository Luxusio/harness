#!/usr/bin/env python3
"""hctl — harness CLI control plane.

Single entry point for task lifecycle management:
  start    — compile routing fields into TASK_STATE.yaml
  context  — emit compact task pack (--json for machine-readable)
  update   — sync touched_paths/roots_touched/verification_targets
  verify   — delegate to verify.py (suite / smoke / healthcheck modes)
  close    — wrap task_completed_gate.py
  artifact — wrap write_artifact.py

stdlib only (no pip packages).
"""

import argparse
import json
import os
import subprocess
import sys

# Locate the scripts directory (hctl.py lives there)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _lib import (
    yaml_field,
    yaml_array,
    is_doc_path,
    extract_roots,
    set_task_state_field,
    compile_routing,
    emit_compact_context,
    TASK_DIR,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_task_dir(args):
    """Resolve and validate --task-dir. Exits on failure."""
    task_dir = getattr(args, "task_dir", None)
    if not task_dir:
        print("ERROR: --task-dir is required", file=sys.stderr)
        sys.exit(1)
    # Support relative paths from cwd
    if not os.path.isabs(task_dir):
        task_dir = os.path.join(os.getcwd(), task_dir)
    task_dir = os.path.normpath(task_dir)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        print(f"ERROR: TASK_STATE.yaml not found in {task_dir}", file=sys.stderr)
        sys.exit(1)
    return task_dir


# ---------------------------------------------------------------------------
# Subcommand: start
# ---------------------------------------------------------------------------

def cmd_start(args):
    """Compile routing and write canonical fields to TASK_STATE.yaml."""
    task_dir = _require_task_dir(args)

    request_text = ""
    request_file = getattr(args, "request_file", None)
    if request_file and os.path.isfile(request_file):
        try:
            with open(request_file, "r", encoding="utf-8") as fh:
                request_text = fh.read()
        except OSError:
            pass

    routing = compile_routing(task_dir, request_text=request_text)

    for field, value in routing.items():
        set_task_state_field(task_dir, field, value)

    task_id = yaml_field("task_id", os.path.join(task_dir, "TASK_STATE.yaml")) or os.path.basename(task_dir)
    print(f"routing compiled for {task_id}")
    print(f"  risk_level: {routing['risk_level']}")
    print(f"  maintenance_task: {routing['maintenance_task']}")
    print(f"  workflow_locked: {routing['workflow_locked']}")
    print(f"  execution_mode: {routing['execution_mode']} (compat)")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: context
# ---------------------------------------------------------------------------

def cmd_context(args):
    """Emit compact task pack."""
    task_dir = _require_task_dir(args)

    ctx = emit_compact_context(task_dir)

    if getattr(args, "json", False):
        print(json.dumps(ctx, indent=2))
    else:
        checks = ctx.get("checks", {})
        print(f"Task: {ctx['task_id']} [{ctx.get('status', 'unknown')}]")
        print(
            "Route: lane={lane} risk={risk} qa={qa} doc_sync={doc} browser={browser}".format(
                lane=ctx["lane"],
                risk=ctx["risk_level"],
                qa="required" if ctx["qa_required"] else "skip",
                doc="required" if ctx["doc_sync_required"] else "skip",
                browser="yes" if ctx["browser_required"] else "no",
            )
        )
        print(
            "Compat: execution_mode={execution_mode} orchestration_mode={orchestration_mode}".format(
                execution_mode=ctx["compat"]["execution_mode"],
                orchestration_mode=ctx["compat"]["orchestration_mode"],
            )
        )
        if ctx.get("must_read"):
            print(f"Must read: {', '.join(ctx['must_read'])}")
        if checks:
            print(
                "Checks: total={total} open={open_count} failed={failed_count} blocked={blocked_count}".format(
                    total=checks.get("total", 0),
                    open_count=len(checks.get("open_ids", [])),
                    failed_count=len(checks.get("failed_ids", [])),
                    blocked_count=len(checks.get("blocked_ids", [])),
                )
            )
        if ctx.get("open_failures"):
            print(f"Open failures: {', '.join(ctx['open_failures'])}")
        if ctx.get("notes"):
            print(f"Notes: {' | '.join(ctx['notes'])}")
        if ctx.get("next_action"):
            print(f"Next: {ctx['next_action']}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: update
# ---------------------------------------------------------------------------

def cmd_update(args):
    """Update touched_paths/roots_touched/verification_targets in TASK_STATE.yaml."""
    task_dir = _require_task_dir(args)

    if getattr(args, "from_git_diff", False):
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=os.getcwd(),
        )
        if result.returncode != 0:
            # Try staged+unstaged
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=os.getcwd(),
            )
        changed_files = [
            f.strip() for f in result.stdout.strip().splitlines() if f.strip()
        ]

        if not changed_files:
            print("No changed files detected from git diff")
            return 0

        touched_paths = changed_files
        roots_touched = extract_roots(changed_files)
        verification_targets = [f for f in changed_files if not is_doc_path(f)]

        set_task_state_field(task_dir, "touched_paths", touched_paths)
        set_task_state_field(task_dir, "roots_touched", roots_touched)
        set_task_state_field(task_dir, "verification_targets", verification_targets)

        print(f"Updated touched_paths: {len(touched_paths)} files")
        print(f"Updated roots_touched: {roots_touched}")
        print(f"Updated verification_targets: {len(verification_targets)} files")
    else:
        print("Nothing to update. Use --from-git-diff to sync from git.")

    return 0


# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------

def cmd_verify(args):
    """Run verification suite (delegates to verify.py)."""
    task_dir = _require_task_dir(args)

    verify_script = os.path.join(SCRIPT_DIR, "verify.py")
    result = subprocess.run(
        ["python3", verify_script],
        cwd=os.getcwd(),
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Subcommand: close
# ---------------------------------------------------------------------------

def cmd_close(args):
    """Run completion gate (delegates to task_completed_gate.py)."""
    task_dir = _require_task_dir(args)

    gate_script = os.path.join(SCRIPT_DIR, "task_completed_gate.py")

    # task_completed_gate.py reads task_id from stdin JSON or env.
    # Pass via environment variable.
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)

    env = os.environ.copy()
    env["HARNESS_TASK_ID"] = task_id
    env["HARNESS_SKIP_STDIN"] = "1"

    result = subprocess.run(
        ["python3", gate_script],
        env=env,
        cwd=os.getcwd(),
    )
    if result.returncode == 0:
        print(f"close gate PASSED for {task_id}")
    else:
        print(f"close gate BLOCKED for {task_id} (exit {result.returncode})", file=sys.stderr)
    return result.returncode


# ---------------------------------------------------------------------------
# Subcommand: artifact
# ---------------------------------------------------------------------------

def cmd_artifact(args):
    """Delegate to write_artifact.py with remaining args."""
    write_artifact_script = os.path.join(SCRIPT_DIR, "write_artifact.py")

    extra = getattr(args, "artifact_args", [])

    env = os.environ.copy()
    env["HARNESS_SKIP_PREWRITE"] = "1"

    result = subprocess.run(
        ["python3", write_artifact_script] + extra,
        env=env,
        cwd=os.getcwd(),
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------

def build_parser():
    parser = argparse.ArgumentParser(
        prog="hctl",
        description="harness CLI control plane — task lifecycle management",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subparsers.required = True

    # start
    p_start = subparsers.add_parser("start", help="compile routing into TASK_STATE.yaml")
    p_start.add_argument("--task-dir", required=True, metavar="DIR",
                         help="task directory containing TASK_STATE.yaml")
    p_start.add_argument("--request-file", metavar="FILE",
                         help="optional REQUEST.md for request-text heuristics")
    p_start.set_defaults(func=cmd_start)

    # context
    p_ctx = subparsers.add_parser("context", help="emit compact task pack")
    p_ctx.add_argument("--task-dir", required=True, metavar="DIR",
                       help="task directory containing TASK_STATE.yaml")
    p_ctx.add_argument("--json", action="store_true",
                       help="output machine-readable JSON")
    p_ctx.set_defaults(func=cmd_context)

    # update
    p_upd = subparsers.add_parser("update", help="sync task state from git diff")
    p_upd.add_argument("--task-dir", required=True, metavar="DIR",
                       help="task directory containing TASK_STATE.yaml")
    p_upd.add_argument("--from-git-diff", action="store_true",
                       help="update touched_paths from `git diff --name-only HEAD`")
    p_upd.set_defaults(func=cmd_update)

    # verify
    p_ver = subparsers.add_parser("verify", help="run verification suite")
    p_ver.add_argument("--task-dir", required=True, metavar="DIR",
                       help="task directory containing TASK_STATE.yaml")
    p_ver.set_defaults(func=cmd_verify)

    # close
    p_cls = subparsers.add_parser("close", help="run task completion gate")
    p_cls.add_argument("--task-dir", required=True, metavar="DIR",
                       help="task directory containing TASK_STATE.yaml")
    p_cls.set_defaults(func=cmd_close)

    # artifact
    p_art = subparsers.add_parser("artifact", help="write harness artifact (wraps write_artifact.py)")
    p_art.add_argument("artifact_args", nargs=argparse.REMAINDER,
                       help="arguments passed through to write_artifact.py")
    p_art.set_defaults(func=cmd_artifact)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
