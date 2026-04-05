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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from _lib import team_artifact_status, get_team_worker_name, get_agent_role
except Exception:  # pragma: no cover - defensive fallback for standalone CLI use
    team_artifact_status = None
    get_team_worker_name = None
    get_agent_role = None


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


def write_meta(path, artifact_name, task_id, author_role, verdict=None, team_context=None):
    meta = {
        "artifact": artifact_name,
        "task_id": task_id,
        "author_role": author_role,
        "written_at": now_iso(),
        "cli_invoked": True,
    }
    if verdict is not None:
        meta["verdict"] = verdict
    if team_context:
        meta["orchestration_mode"] = "team"
        if team_context.get("team_status"):
            meta["team_status"] = team_context["team_status"]
        if team_context.get("current_worker"):
            meta["team_worker"] = team_context["current_worker"]
        if team_context.get("current_role"):
            meta["agent_name"] = team_context["current_role"]
        if team_context.get("phase"):
            meta["team_phase"] = team_context["phase"]
        owners = [str(item).strip() for item in (team_context.get("expected_workers") or []) if str(item).strip()]
        if owners:
            meta["team_expected_workers"] = owners
        if team_context.get("expected_owner_label"):
            meta["team_expected_owner"] = team_context["expected_owner_label"]
        if team_context.get("expected_owner_source"):
            meta["team_expected_owner_source"] = team_context["expected_owner_source"]
        if team_context.get("owner_match") is not None:
            meta["team_owner_match"] = bool(team_context["owner_match"])
        if team_context.get("owner_enforced"):
            meta["team_owner_enforced"] = True
        if team_context.get("current_worker_inferred"):
            meta["team_worker_inferred"] = True
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


def _clean_workers(values):
    result = []
    for value in values or []:
        worker = str(value or "").strip()
        if worker and worker not in result:
            result.append(worker)
    return result


def _current_team_worker(task_dir, known_workers=None):
    explicit = os.environ.get("HARNESS_TEAM_WORKER", "").strip()
    if explicit:
        return explicit, False
    if get_team_worker_name is None:
        return "", False
    worker = get_team_worker_name(known_workers=known_workers or [])
    return worker, False


def _expected_team_owners(artifact_name, team_state):
    owners = []
    owner_label = ""
    owner_source = ""
    phase = ""
    enforce = False

    if artifact_name in ("CRITIC__runtime.md", "QA__runtime.md"):
        owners = _clean_workers(
            team_state.get("team_runtime_verification_owners") or team_state.get("synthesis_workers") or []
        )
        owner_label = str(
            team_state.get("team_runtime_verification_owner_label")
            or ", ".join(owners)
            or "lead/integrator"
        )
        owner_source = "team-runtime"
        phase = "verification"
        final_phase_started = bool(
            team_state.get("synthesis_ready") or team_state.get("team_runtime_verification_needed")
        )
        enforce = bool(final_phase_started and owners)
    elif artifact_name == "DOC_SYNC.md":
        owners = _clean_workers(team_state.get("team_doc_sync_owners") or [])
        owner_label = str(team_state.get("team_doc_sync_owner_label") or ", ".join(owners) or "writer")
        owner_source = str(team_state.get("team_doc_sync_owner_source") or "")
        phase = "documentation"
        enforce = bool(owners and owner_source in ("explicit", "inferred"))
    elif artifact_name == "CRITIC__document.md":
        owners = _clean_workers(team_state.get("team_document_critic_owners") or [])
        owner_label = str(
            team_state.get("team_document_critic_owner_label")
            or ", ".join(owners)
            or "critic-document"
        )
        owner_source = str(team_state.get("team_document_critic_owner_source") or "")
        phase = "documentation"
        enforce = bool(owners and owner_source in ("explicit", "inferred"))
    elif artifact_name == "HANDOFF.md":
        owners = _clean_workers(team_state.get("synthesis_workers") or [])
        owner_label = ", ".join(owners) or "lead/integrator"
        owner_source = "team-synthesis"
        phase = "handoff"
        enforce = bool(owners)

    return {
        "owners": owners,
        "owner_label": owner_label,
        "owner_source": owner_source,
        "phase": phase,
        "enforce": enforce,
    }


def get_team_artifact_context(task_dir, artifact_name):
    if team_artifact_status is None:
        return {}
    try:
        team_state = team_artifact_status(task_dir)
    except Exception:
        return {}
    if team_state.get("orchestration_mode") != "team":
        return {}

    owner_info = _expected_team_owners(artifact_name, team_state)
    known_workers = _clean_workers(team_state.get("plan_workers") or [])
    current_worker, inferred_worker = _current_team_worker(task_dir, known_workers)
    if not current_worker and owner_info.get("enforce") and len(owner_info.get("owners") or []) == 1:
        current_worker = owner_info["owners"][0]
        inferred_worker = True

    current_role = get_agent_role() if get_agent_role is not None else os.environ.get("CLAUDE_AGENT_NAME", "").strip()
    owner_match = None
    if owner_info.get("owners"):
        owner_match = bool(current_worker and current_worker in owner_info["owners"])

    return {
        "team_enabled": True,
        "team_status": str(team_state.get("derived_status") or team_state.get("current_status") or "planned"),
        "plan_workers": known_workers,
        "current_worker": current_worker,
        "current_worker_inferred": inferred_worker,
        "current_role": str(current_role or ""),
        "expected_workers": list(owner_info.get("owners") or []),
        "expected_owner_label": str(owner_info.get("owner_label") or ""),
        "expected_owner_source": str(owner_info.get("owner_source") or ""),
        "phase": str(owner_info.get("phase") or ""),
        "owner_enforced": bool(owner_info.get("enforce")),
        "owner_match": owner_match,
    }


def enforce_team_artifact_owner(task_dir, artifact_name):
    context = get_team_artifact_context(task_dir, artifact_name)
    if not context or not context.get("owner_enforced"):
        return context

    owners = context.get("expected_workers") or []
    current_worker = str(context.get("current_worker") or "").strip()
    if owners and not current_worker:
        preview = ", ".join(owners[:4])
        raise ValueError(
            f"{artifact_name} is reserved for team owner(s) [{preview}]. "
            "Set HARNESS_TEAM_WORKER (or pass team_worker via the MCP write_* tool) before writing it."
        )
    if owners and current_worker not in owners:
        preview = ", ".join(owners[:4])
        raise ValueError(
            f"{artifact_name} is reserved for team owner(s) [{preview}]. "
            f"Current worker is '{current_worker}'."
        )
    return context


def artifact_team_header_lines(team_context):
    if not team_context:
        return []
    lines = []
    worker = str(team_context.get("current_worker") or "").strip()
    if worker:
        lines.append(f"team_worker: {worker}")
    phase = str(team_context.get("phase") or "").strip()
    if phase:
        lines.append(f"team_phase: {phase}")
    owner_label = str(team_context.get("expected_owner_label") or "").strip()
    if owner_label:
        lines.append(f"team_owner: {owner_label}")
    owners = [str(item).strip() for item in (team_context.get("expected_workers") or []) if str(item).strip()]
    if owners:
        lines.append("team_expected_workers: " + ", ".join(owners))
    if team_context.get("current_worker_inferred"):
        lines.append("team_worker_inferred: true")
    return lines


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
    artifact_name = "CRITIC__runtime.md"
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

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
    if team_header:
        md_lines[2:2] = team_header
    if verdict_reason:
        blank_idx = md_lines.index("") if "" in md_lines else len(md_lines)
        md_lines.insert(blank_idx, f"verdict_reason: {verdict_reason}")

    md_content = "\n".join(md_lines) + "\n"

    # Write artifact
    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__runtime.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-runtime", verdict=args.verdict, team_context=team_context)

    # Update TASK_STATE.yaml
    update_task_state_field(task_dir, "runtime_verdict", args.verdict)
    update_task_state_field(task_dir, "runtime_verdict_freshness", "current")
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
    artifact_name = "CRITIC__plan.md"
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

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
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__plan.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-plan", verdict=args.verdict, team_context=team_context)

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
    artifact_name = "CRITIC__document.md"
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

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
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__document.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "critic-document", verdict=args.verdict, team_context=team_context)

    # Update TASK_STATE.yaml
    update_task_state_field(task_dir, "document_verdict", args.verdict)
    update_task_state_field(task_dir, "document_verdict_freshness", "current")

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
    artifact_name = "HANDOFF.md"
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

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
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "HANDOFF.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "developer", team_context=team_context)

    print(f"wrote {artifact_name} + HANDOFF.meta.json")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: doc-sync
# ---------------------------------------------------------------------------


def cmd_doc_sync(args):
    task_dir = os.path.abspath(args.task_dir)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()
    artifact_name = "DOC_SYNC.md"
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

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
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "DOC_SYNC.meta.json")

    write_file(artifact_path, md_content)
    write_meta(meta_path, artifact_name, task_id, "writer", team_context=team_context)

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
