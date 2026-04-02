#!/usr/bin/env python3
"""hctl — harness CLI control plane.

Single entry point for task lifecycle management:
  start        — compile routing fields into TASK_STATE.yaml
  context      — emit compact task pack (--json for machine-readable)
  history      — list indexed failure cases across task history
  top-failures — surface top similar historical failures for a task
  diff-case    — compare two failure cases
  update       — sync touched_paths/roots_touched/verification_targets
  verify       — delegate to verify.py (suite / smoke / healthcheck modes)
  close        — wrap task_completed_gate.py
  artifact     — wrap write_artifact.py

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
    is_doc_path,
    extract_roots,
    set_task_state_field,
    compile_routing,
    emit_compact_context,
    ensure_team_artifacts,
    sync_team_status,
    TASK_DIR,
)
from environment_snapshot import write_environment_snapshot
from failure_memory import (
    write_failure_case_snapshot,
    list_failure_cases,
    find_similar_failures,
    diff_failure_cases,
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
    if not os.path.isabs(task_dir):
        task_dir = os.path.join(os.getcwd(), task_dir)
    task_dir = os.path.normpath(task_dir)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        print(f"ERROR: TASK_STATE.yaml not found in {task_dir}", file=sys.stderr)
        sys.exit(1)
    return task_dir


def _resolve_tasks_dir(raw_tasks_dir):
    tasks_dir = raw_tasks_dir or TASK_DIR
    if not os.path.isabs(tasks_dir):
        tasks_dir = os.path.join(os.getcwd(), tasks_dir)
    tasks_dir = os.path.normpath(tasks_dir)
    if not os.path.isdir(tasks_dir):
        print(f"ERROR: tasks dir not found: {tasks_dir}", file=sys.stderr)
        sys.exit(1)
    return tasks_dir


def _print_case_summary(case, prefix=""):
    head = (
        f"{prefix}{case.get('task_id')}: signals={case.get('failure_signals', 0)} "
        f"lane={case.get('lane', 'unknown')} artifact={case.get('artifact', 'TASK_STATE.yaml')}"
    )
    print(head)
    excerpt = str(case.get("excerpt") or "").strip()
    if excerpt:
        print(f"  excerpt: {excerpt}")
    check_ids = case.get("check_ids") or case.get("matching_check_ids") or []
    if check_ids:
        print(f"  checks: {', '.join(str(x) for x in check_ids[:4])}")
    paths = case.get("path_examples") or case.get("matching_paths") or []
    if paths:
        print(f"  paths: {', '.join(str(x) for x in paths[:4])}")


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

    team_artifact_result = []
    if routing.get("orchestration_mode") == "team":
        try:
            team_artifact_result = ensure_team_artifacts(task_dir, routing=routing)
        except Exception:
            team_artifact_result = []

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

    snapshot_path = ""
    try:
        snapshot_path = write_environment_snapshot(task_dir, repo_root=os.getcwd(), reason="task_start")
    except Exception:
        snapshot_path = ""

    case_path = ""
    try:
        case_path = write_failure_case_snapshot(task_dir, prompt=request_text)
    except Exception:
        case_path = ""

    task_id = yaml_field("task_id", os.path.join(task_dir, "TASK_STATE.yaml")) or os.path.basename(task_dir)
    print(f"routing compiled for {task_id}")
    print(f"  risk_level: {routing['risk_level']}")
    print(f"  maintenance_task: {routing['maintenance_task']}")
    print(f"  workflow_locked: {routing['workflow_locked']}")
    print(f"  planning_mode: {routing['planning_mode']}")
    print(f"  execution_mode: {routing['execution_mode']} (compat)")
    print(f"  orchestration_mode: {routing['orchestration_mode']} (compat)")
    if routing.get("orchestration_mode") != "solo" or routing.get("team_status") not in (None, "n/a", "skipped"):
        print(
            "  team: provider={provider} status={status} size={size} fallback={fallback}".format(
                provider=routing.get("team_provider", "none"),
                status=routing.get("team_status", "n/a"),
                size=routing.get("team_size", 0),
                fallback=routing.get("fallback_used", "none"),
            )
        )
        if routing.get("team_reason"):
            print(f"  team_reason: {routing['team_reason']}")
    if team_artifact_result:
        print(
            "  team_artifacts: " + ", ".join(os.path.basename(item) for item in team_artifact_result)
        )
    if snapshot_path:
        print(f"  env_snapshot: {os.path.basename(snapshot_path)}")
    if case_path:
        print(f"  failure_case: {os.path.basename(case_path)}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: context
# ---------------------------------------------------------------------------


def cmd_context(args):
    """Emit compact task pack."""
    task_dir = _require_task_dir(args)

    try:
        write_failure_case_snapshot(task_dir)
    except Exception:
        pass

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

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
        print(f"Planning: {ctx.get('planning_mode', 'standard')}")
        team = ctx.get("team") or {}
        if team:
            print(
                "Team: provider={provider} status={status} size={size} fallback={fallback}".format(
                    provider=team.get("provider", "none"),
                    status=team.get("status", "n/a"),
                    size=team.get("size", 0),
                    fallback=team.get("fallback_used", "none"),
                )
            )
            if team.get("reason"):
                print(f"Team reason: {team['reason']}")
            if team.get("plan_required") or team.get("synthesis_required") or team.get("provider") not in ("none", ""):
                print(
                    "Team plan: ready={ready} placeholders={placeholders} artifact={artifact}".format(
                        ready="yes" if team.get("plan_ready") else "no",
                        placeholders="yes" if team.get("plan_has_placeholders") else "no",
                        artifact=team.get("plan_artifact", "TEAM_PLAN.md"),
                    )
                )
                if team.get("plan_validation_errors"):
                    print("Team plan ownership: " + "; ".join(team.get("plan_validation_errors", [])[:3]))
                print(
                    "Team synthesis: ready={ready} placeholders={placeholders} artifact={artifact}".format(
                        ready="yes" if team.get("synthesis_ready") else "no",
                        placeholders="yes" if team.get("synthesis_has_placeholders") else "no",
                        artifact=team.get("synthesis_artifact", "TEAM_SYNTHESIS.md"),
                    )
                )
        if ctx.get("must_read"):
            print(f"Must read: {', '.join(ctx['must_read'])}")
        review_focus = ctx.get("review_focus") or {}
        if review_focus.get("evidence_first"):
            trigger = review_focus.get("trigger", "fix_round")
            print(f"Review focus: {trigger} (evidence-first)")
            excerpt = review_focus.get("evidence_excerpt")
            if excerpt:
                print(f"Evidence: {excerpt}")
        if review_focus.get("environment_artifact"):
            reasons = review_focus.get("environment_reasons") or []
            tail = f" ({', '.join(reasons)})" if reasons else ""
            print(f"Environment: {review_focus['environment_artifact']}{tail}")
        similar_cases = review_focus.get("prior_similar_cases") or []
        if similar_cases:
            print("Similar failures:")
            for item in similar_cases[:3]:
                score = item.get("score", 0.0)
                print(f"  - {item.get('task_id')} score={score:.2f} {item.get('artifact')}")
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
# Subcommand: history
# ---------------------------------------------------------------------------


def cmd_history(args):
    """List failure cases across task history."""
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))
    cases = list_failure_cases(
        tasks_dir=tasks_dir,
        limit=getattr(args, "limit", 20),
        lane=getattr(args, "lane", ""),
        min_failure_signals=getattr(args, "min_failure_signals", 1),
    )

    if getattr(args, "json", False):
        print(json.dumps(cases, indent=2))
        return 0

    if not cases:
        print("No failure cases found.")
        return 0

    for case in cases:
        _print_case_summary(case)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: top-failures
# ---------------------------------------------------------------------------


def cmd_top_failures(args):
    """Show top similar failures for the given task."""
    task_dir = _require_task_dir(args)
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))

    try:
        write_failure_case_snapshot(task_dir)
    except Exception:
        pass

    matches = find_similar_failures(
        task_dir,
        tasks_dir=tasks_dir,
        limit=getattr(args, "limit", 3),
    )

    if getattr(args, "json", False):
        print(json.dumps(matches, indent=2))
        return 0

    if not matches:
        print("No similar failures found.")
        return 0

    for idx, match in enumerate(matches, start=1):
        _print_case_summary(match, prefix=f"#{idx} ")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: diff-case
# ---------------------------------------------------------------------------


def cmd_diff_case(args):
    """Compare two failure cases."""
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))
    diff = diff_failure_cases(args.case_a, args.case_b, tasks_dir=tasks_dir)
    if not diff:
        print("ERROR: unable to diff cases", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(diff, indent=2))
        return 0

    case_a = diff.get("case_a") or {}
    case_b = diff.get("case_b") or {}
    print(f"Case A: {case_a.get('task_id')} signals={case_a.get('failure_signals', 0)} lane={case_a.get('lane', 'unknown')}")
    print(f"Case B: {case_b.get('task_id')} signals={case_b.get('failure_signals', 0)} lane={case_b.get('lane', 'unknown')}")
    if diff.get("same_lane"):
        print("Lane: same")
    else:
        print("Lane: different")
    if diff.get("shared_check_ids"):
        print("Shared checks: " + ", ".join(diff["shared_check_ids"]))
    if diff.get("shared_paths"):
        print("Shared paths: " + ", ".join(diff["shared_paths"]))
    if diff.get("shared_keywords"):
        print("Shared keywords: " + ", ".join(diff["shared_keywords"]))
    if diff.get("more_severe"):
        print(f"More severe: {diff['more_severe']}")
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

        team_state = None
        try:
            team_state = sync_team_status(task_dir)
        except Exception:
            team_state = None

        try:
            write_failure_case_snapshot(task_dir)
        except Exception:
            pass

        print(f"Updated touched_paths: {len(touched_paths)} files")
        print(f"Updated roots_touched: {roots_touched}")
        print(f"Updated verification_targets: {len(verification_targets)} files")
        if team_state and team_state.get("orchestration_mode") == "team":
            print(f"Team status: {team_state.get('derived_status', team_state.get('current_status', 'n/a'))} (artifact-driven)")
    else:
        print("Nothing to update. Use --from-git-diff to sync from git.")

    return 0


# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------


def cmd_verify(args):
    """Run verification suite (delegates to verify.py)."""
    _require_task_dir(args)

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
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)

    env = os.environ.copy()
    env["HARNESS_TASK_ID"] = task_id
    env["HARNESS_SKIP_STDIN"] = "1"

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

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

    p_start = subparsers.add_parser("start", help="compile routing into TASK_STATE.yaml")
    p_start.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_start.add_argument("--request-file", metavar="FILE", help="optional REQUEST.md for request-text heuristics")
    p_start.set_defaults(func=cmd_start)

    p_ctx = subparsers.add_parser("context", help="emit compact task pack")
    p_ctx.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_ctx.add_argument("--json", action="store_true", help="output machine-readable JSON")
    p_ctx.set_defaults(func=cmd_context)

    p_hist = subparsers.add_parser("history", help="list failure cases across task history")
    p_hist.add_argument("--tasks-dir", metavar="DIR", help="optional tasks directory (defaults to doc/harness/tasks)")
    p_hist.add_argument("--lane", metavar="LANE", default="", help="optional lane filter")
    p_hist.add_argument("--limit", type=int, default=20, help="maximum cases to show")
    p_hist.add_argument("--min-failure-signals", type=int, default=1, help="minimum failure_signals required")
    p_hist.add_argument("--json", action="store_true", help="output machine-readable JSON")
    p_hist.set_defaults(func=cmd_history)

    p_top = subparsers.add_parser("top-failures", help="show top similar failures for a task")
    p_top.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_top.add_argument("--tasks-dir", metavar="DIR", help="optional tasks directory (defaults to doc/harness/tasks)")
    p_top.add_argument("--limit", type=int, default=3, help="maximum similar failures to show")
    p_top.add_argument("--json", action="store_true", help="output machine-readable JSON")
    p_top.set_defaults(func=cmd_top_failures)

    p_diff = subparsers.add_parser("diff-case", help="diff two failure cases")
    p_diff.add_argument("--case-a", required=True, metavar="TASK_ID", help="first task id or task dir")
    p_diff.add_argument("--case-b", required=True, metavar="TASK_ID", help="second task id or task dir")
    p_diff.add_argument("--tasks-dir", metavar="DIR", help="optional tasks directory (defaults to doc/harness/tasks)")
    p_diff.add_argument("--json", action="store_true", help="output machine-readable JSON")
    p_diff.set_defaults(func=cmd_diff_case)

    p_upd = subparsers.add_parser("update", help="sync task state from git diff")
    p_upd.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_upd.add_argument("--from-git-diff", action="store_true", help="update touched_paths from `git diff --name-only HEAD`")
    p_upd.set_defaults(func=cmd_update)

    p_ver = subparsers.add_parser("verify", help="run verification suite")
    p_ver.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_ver.set_defaults(func=cmd_verify)

    p_cls = subparsers.add_parser("close", help="run task completion gate")
    p_cls.add_argument("--task-dir", required=True, metavar="DIR", help="task directory containing TASK_STATE.yaml")
    p_cls.set_defaults(func=cmd_close)

    p_art = subparsers.add_parser("artifact", help="write harness artifact (wraps write_artifact.py)")
    p_art.add_argument("artifact_args", nargs=argparse.REMAINDER, help="arguments passed through to write_artifact.py")
    p_art.set_defaults(func=cmd_artifact)

    return parser



def main():
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
