#!/usr/bin/env python3
"""CLI tool for writing protected harness artifacts.

Usage:
  python3 write_artifact.py <subcommand> --task-dir <path> [options]

Subcommands:
  critic-runtime   Write CRITIC__runtime.md + meta.json, update TASK_STATE.yaml
  critic-plan      Write CRITIC__plan.md + meta.json, update TASK_STATE.yaml
  critic-document  Write CRITIC__document.md + meta.json, update TASK_STATE.yaml
  handoff          Write HANDOFF.md + meta.json
  doc-sync         Write DOC_SYNC.md + meta.json
"""

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_id_from_dir(task_dir):
    """Infer task_id from the basename of task_dir."""
    return os.path.basename(os.path.abspath(task_dir))


def write_file(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(content)


def write_meta(path, artifact_name, task_id, author_role, verdict=None):
    meta = {
        "artifact": artifact_name,
        "task_id": task_id,
        "author_role": author_role,
        "written_at": now_iso(),
        "cli_invoked": True,
    }
    if verdict is not None:
        meta["verdict"] = verdict
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")


def update_task_state_field(task_dir, field, value):
    """Update a single scalar field in TASK_STATE.yaml. Adds it if absent."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return  # nothing to update
    with open(state_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    ts = now_iso()
    pattern = r"^" + re.escape(field) + r":.*"
    replacement = f"{field}: {value}"
    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{replacement}\n"

    # Always update the `updated` timestamp
    if re.search(r"^updated:", content, flags=re.MULTILINE):
        content = re.sub(r"^updated:.*", f"updated: \"{ts}\"", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\nupdated: \"{ts}\"\n"

    with open(state_file, "w", encoding="utf-8") as fh:
        fh.write(content)


def increment_task_state_int(task_dir, field):
    """Increment an integer field in TASK_STATE.yaml (adds at 1 if absent)."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return
    with open(state_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    current = 0
    m = re.search(r"^" + re.escape(field) + r":\s*(\d+)", content, flags=re.MULTILINE)
    if m:
        current = int(m.group(1))
    new_val = current + 1

    ts = now_iso()
    replacement = f"{field}: {new_val}"
    if m:
        content = re.sub(
            r"^" + re.escape(field) + r":.*", replacement, content, flags=re.MULTILINE
        )
    else:
        content = content.rstrip("\n") + f"\n{replacement}\n"

    if re.search(r"^updated:", content, flags=re.MULTILINE):
        content = re.sub(r"^updated:.*", f"updated: \"{ts}\"", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\nupdated: \"{ts}\"\n"

    with open(state_file, "w", encoding="utf-8") as fh:
        fh.write(content)


def parse_checks_arg(checks_str):
    """Parse 'AC-001:PASS,AC-002:FAIL' into dict {id: status}."""
    if not checks_str or checks_str.strip().lower() == "none":
        return {}
    result = {}
    for part in checks_str.split(","):
        part = part.strip()
        if ":" in part:
            cid, verdict = part.split(":", 1)
            result[cid.strip()] = verdict.strip().upper()
    return result


def update_checks_yaml(task_dir, checks_dict):
    """Update CHECKS.yaml criteria statuses from a dict {id: PASS|FAIL}."""
    if not checks_dict:
        return
    checks_file = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_file):
        return
    with open(checks_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    ts = now_iso()
    for cid, verdict in checks_dict.items():
        new_status = "passed" if verdict == "PASS" else "failed"
        # Match the id line, then update the status on the following status: line
        # We do a block replacement: find '  - id: AC-001' block and update status within it
        def replace_block(m):
            block = m.group(0)
            # Replace status: <anything> within this block
            block = re.sub(r"(status:\s*)(\S+)", r"\g<1>" + new_status, block, count=1)
            # Replace last_updated within this block
            block = re.sub(
                r"(last_updated:\s*)(\S+)",
                r'\g<1>"' + ts + '"',
                block,
                count=1,
            )
            return block

        # Match from '  - id: <cid>' to the next '  - id:' or end
        pattern = r"(  - id: " + re.escape(cid) + r".*?)(?=\n  - id:|\Z)"
        content = re.sub(pattern, replace_block, content, flags=re.DOTALL)

    with open(checks_file, "w", encoding="utf-8") as fh:
        fh.write(content)


# ---------------------------------------------------------------------------
# Subcommand: critic-runtime
# ---------------------------------------------------------------------------


def cmd_critic_runtime(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()

    checks_str = getattr(args, "checks", None) or "none"
    checks_dict = parse_checks_arg(checks_str)
    verdict_reason = getattr(args, "verdict_reason", None) or ""

    # Build CRITIC__runtime.md content
    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"execution_mode: {args.execution_mode}",
        f"summary: {args.summary}",
        f"checks_updated: {checks_str if checks_str else 'none'}",
        "",
        "## Transcript",
        args.transcript,
    ]
    if verdict_reason:
        md_lines.insert(5, f"verdict_reason: {verdict_reason}")

    md_content = "\n".join(md_lines) + "\n"

    # Write artifact
    artifact_name = "CRITIC__runtime.md"
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__runtime.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-runtime", verdict=args.verdict)

    # Update TASK_STATE.yaml
    update_task_state_field(task_dir, "runtime_verdict", args.verdict)
    if args.verdict == "FAIL":
        increment_task_state_int(task_dir, "runtime_verdict_fail_count")
    if args.verdict == "BLOCKED_ENV":
        update_task_state_field(task_dir, "status", "blocked_env")

    # Update CHECKS.yaml
    update_checks_yaml(task_dir, checks_dict)

    print(f"wrote {artifact_name} + CRITIC__runtime.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: critic-plan
# ---------------------------------------------------------------------------


def cmd_critic_plan(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)

    checks_str = getattr(args, "checks", None) or "none"
    checks_dict = parse_checks_arg(checks_str)
    issues = getattr(args, "issues", None) or "none"

    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"summary: {args.summary}",
        f"issues: {issues}",
        f"checks_updated: {checks_str if checks_str else 'none'}",
    ]
    md_content = "\n".join(md_lines) + "\n"

    artifact_name = "CRITIC__plan.md"
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__plan.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-plan", verdict=args.verdict)

    # Update TASK_STATE.yaml
    update_task_state_field(task_dir, "plan_verdict", args.verdict)

    # Update CHECKS.yaml
    update_checks_yaml(task_dir, checks_dict)

    print(f"wrote {artifact_name} + CRITIC__plan.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: critic-document
# ---------------------------------------------------------------------------


def cmd_critic_document(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)

    checks_str = getattr(args, "checks", None) or "none"
    checks_dict = parse_checks_arg(checks_str)
    issues = getattr(args, "issues", None) or "none"

    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"summary: {args.summary}",
        f"issues: {issues}",
        f"checks_updated: {checks_str if checks_str else 'none'}",
    ]
    md_content = "\n".join(md_lines) + "\n"

    artifact_name = "CRITIC__document.md"
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__document.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-document", verdict=args.verdict)

    # Update TASK_STATE.yaml
    update_task_state_field(task_dir, "document_verdict", args.verdict)

    # Update CHECKS.yaml
    update_checks_yaml(task_dir, checks_dict)

    print(f"wrote {artifact_name} + CRITIC__document.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: handoff
# ---------------------------------------------------------------------------


def cmd_handoff(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()

    expected_output = getattr(args, "expected_output", None) or "see transcript"
    do_not_regress = getattr(args, "do_not_regress", None) or "n/a"

    md_lines = [
        f"# Handoff: {task_id}",
        f"written_at: {ts}",
        "",
        "## Verification",
        "```bash",
        args.verify_cmd,
        "```",
        f"Expected output: {expected_output}",
        "",
        "## What changed",
        args.what_changed,
        "",
        "## Do not regress",
        do_not_regress,
    ]
    md_content = "\n".join(md_lines) + "\n"

    artifact_name = "HANDOFF.md"
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "HANDOFF.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "developer")

    print(f"wrote {artifact_name} + HANDOFF.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: doc-sync
# ---------------------------------------------------------------------------


def cmd_doc_sync(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()

    new_files = getattr(args, "new_files", None) or "none"
    updated_files = getattr(args, "updated_files", None) or "none"
    deleted_files = getattr(args, "deleted_files", None) or "none"
    notes = getattr(args, "notes", None) or "none"

    md_lines = [
        f"# DOC_SYNC: {task_id}",
        f"written_at: {ts}",
        "",
        "## What changed",
        args.what_changed,
        "",
        "## New files",
        new_files,
        "",
        "## Updated files",
        updated_files,
        "",
        "## Deleted files",
        deleted_files,
        "",
        "## Notes",
        notes,
    ]
    md_content = "\n".join(md_lines) + "\n"

    artifact_name = "DOC_SYNC.md"
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "DOC_SYNC.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "writer")

    print(f"wrote {artifact_name} + DOC_SYNC.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        description="Write protected harness artifacts via CLI (saves ~500-2000 tokens vs inline)."
    )
    subparsers = parser.add_subparsers(dest="subcommand", required=True)

    # --- critic-runtime ---
    p_rt = subparsers.add_parser("critic-runtime", help="Write CRITIC__runtime.md")
    p_rt.add_argument("--task-dir", required=True, help="Path to task directory")
    p_rt.add_argument(
        "--verdict", required=True, choices=["PASS", "FAIL", "BLOCKED_ENV"],
        help="Verdict value"
    )
    p_rt.add_argument("--execution-mode", required=True, help="Execution mode (light/standard/sprinted)")
    p_rt.add_argument("--summary", required=True, help="One-sentence summary")
    p_rt.add_argument("--transcript", required=True, help="Command transcript text")
    p_rt.add_argument(
        "--checks", default=None,
        help="Comma-separated AC-001:PASS,AC-002:FAIL entries"
    )
    p_rt.add_argument("--verdict-reason", default=None, help="Optional extended reason")

    # --- critic-plan ---
    p_cp = subparsers.add_parser("critic-plan", help="Write CRITIC__plan.md")
    p_cp.add_argument("--task-dir", required=True)
    p_cp.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p_cp.add_argument("--summary", required=True)
    p_cp.add_argument("--checks", default=None)
    p_cp.add_argument("--issues", default=None)

    # --- critic-document ---
    p_cd = subparsers.add_parser("critic-document", help="Write CRITIC__document.md")
    p_cd.add_argument("--task-dir", required=True)
    p_cd.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p_cd.add_argument("--summary", required=True)
    p_cd.add_argument("--checks", default=None)
    p_cd.add_argument("--issues", default=None)

    # --- handoff ---
    p_ho = subparsers.add_parser("handoff", help="Write HANDOFF.md")
    p_ho.add_argument("--task-dir", required=True)
    p_ho.add_argument("--verify-cmd", required=True)
    p_ho.add_argument("--what-changed", required=True)
    p_ho.add_argument("--expected-output", default=None)
    p_ho.add_argument("--do-not-regress", default=None)

    # --- doc-sync ---
    p_ds = subparsers.add_parser("doc-sync", help="Write DOC_SYNC.md")
    p_ds.add_argument("--task-dir", required=True)
    p_ds.add_argument("--what-changed", required=True)
    p_ds.add_argument("--new-files", default=None)
    p_ds.add_argument("--updated-files", default=None)
    p_ds.add_argument("--deleted-files", default=None)
    p_ds.add_argument("--notes", default=None)

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


DISPATCH = {
    "critic-runtime": cmd_critic_runtime,
    "critic-plan": cmd_critic_plan,
    "critic-document": cmd_critic_document,
    "handoff": cmd_handoff,
    "doc-sync": cmd_doc_sync,
}


def main():
    parser = build_parser()
    args = parser.parse_args()
    fn = DISPATCH.get(args.subcommand)
    if fn is None:
        print(f"Unknown subcommand: {args.subcommand}", file=sys.stderr)
        sys.exit(1)
    try:
        rc = fn(args)
        sys.exit(rc if rc is not None else 0)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
