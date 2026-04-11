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
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

try:
    from _lib import (
        team_artifact_status,
        get_team_worker_name,
        get_agent_role,
        set_task_state_field as lib_set_task_state_field,
        write_task_state_content,
        ensure_checks_schema_content,
        atomic_write_text as lib_atomic_write_text,
        yaml_field,
    )
except Exception:  # pragma: no cover - defensive fallback for standalone CLI use
    team_artifact_status = None
    get_team_worker_name = None
    get_agent_role = None
    lib_set_task_state_field = None
    write_task_state_content = None
    ensure_checks_schema_content = None
    lib_atomic_write_text = None
    yaml_field = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def task_id_from_dir(task_dir):
    """Infer task_id from the basename of task_dir."""
    return os.path.basename(os.path.abspath(task_dir))


def _validate_task_dir(task_dir):
    """Raise SystemExit if task_dir is not a valid harness task directory."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        print(
            f"ERROR: '{task_dir}' is not a harness task directory "
            f"(TASK_STATE.yaml not found). "
            f"Use --task-dir doc/harness/tasks/TASK__<id>.",
            file=sys.stderr,
        )
        sys.exit(1)

    # Verify path is under canonical tasks root
    canonical_root = os.path.normpath(
        os.path.join(_project_root(), "doc", "harness", "tasks")
    )
    abs_task_dir = os.path.normpath(os.path.abspath(task_dir))
    if not abs_task_dir.startswith(canonical_root + os.sep):
        print(
            f"ERROR: '{task_dir}' is outside the canonical tasks root "
            f"'{canonical_root}'. Refusing to write.",
            file=sys.stderr,
        )
        sys.exit(1)


def _project_root():
    """Return git repo root, falling back to cwd."""
    try:
        import subprocess as _sp
        r = _sp.run(["git", "rev-parse", "--show-toplevel"],
                    capture_output=True, text=True)
        if r.returncode == 0:
            return r.stdout.strip()
    except Exception:
        pass
    return os.getcwd()


def _resolve_task_dir(task_id):
    """Accept full path, TASK__slug, or bare slug -> validated absolute task dir."""
    # Already a path
    if os.sep in task_id or task_id.startswith("."):
        p = os.path.abspath(task_id)
        if os.path.isdir(p) and os.path.isfile(os.path.join(p, "TASK_STATE.yaml")):
            return p
    # Slug resolution
    root = _project_root()
    slug = task_id if task_id.startswith("TASK__") else f"TASK__{task_id}"
    p = os.path.join(root, "doc", "harness", "tasks", slug)
    if os.path.isdir(p) and os.path.isfile(os.path.join(p, "TASK_STATE.yaml")):
        return p
    print(f"ERROR: task '{task_id}' not found (tried {p}). "
          f"Use --task-id with a slug like 'critic-intent'.", file=sys.stderr)
    sys.exit(1)


def _yaml_field_local(field, filepath):
    """Minimal yaml_field fallback when _lib is not available."""
    if not os.path.isfile(filepath):
        return None
    with open(filepath, "r", encoding="utf-8") as fh:
        for line in fh:
            m = re.match(r"^" + re.escape(field) + r":\s*(.*)", line)
            if m:
                return m.group(1).strip().strip('"').strip("'") or None
    return None


def _get_yaml_field(field, filepath):
    """Use _lib yaml_field if available, else fallback."""
    if yaml_field is not None:
        return yaml_field(field, filepath)
    return _yaml_field_local(field, filepath)


def _team_header(team_context):
    """Return a newline-joined string of team header lines, or empty string."""
    lines = artifact_team_header_lines(team_context)
    return "\n".join(lines) if lines else ""


def _atomic_write_text(path, content):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".tmp.", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _read_text(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _sha256_text(content):
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def write_file(path, content):
    if lib_atomic_write_text is not None:
        lib_atomic_write_text(path, content)
    else:
        _atomic_write_text(path, content)


def build_meta(artifact_name, task_id, author_role, verdict=None, team_context=None):
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
    return meta


def write_meta(path, artifact_name, task_id, author_role, verdict=None, team_context=None):
    meta = build_meta(artifact_name, task_id, author_role, verdict=verdict, team_context=team_context)
    _atomic_write_text(path, json.dumps(meta, indent=2) + "\n")
    return meta


def finalize_write_result(artifact_path, meta_path, *, state_fields_updated=None, checks_updated=None):
    artifact_content = _read_text(artifact_path)
    meta_content = _read_text(meta_path)
    return {
        "artifact_path": artifact_path,
        "meta_path": meta_path,
        "artifact_sha256": _sha256_text(artifact_content),
        "meta_sha256": _sha256_text(meta_content),
        "artifact_bytes": len(artifact_content.encode("utf-8")),
        "meta_bytes": len(meta_content.encode("utf-8")),
        "state_fields_updated": list(state_fields_updated or []),
        "checks_updated": list(checks_updated or []),
        "readback_ok": True,
    }


def update_task_state_field(task_dir, field, value):
    """Update a single scalar field in TASK_STATE.yaml. Adds it if absent."""
    if lib_set_task_state_field is not None:
        lib_set_task_state_field(task_dir, field, value)
        return

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return
    with open(state_file, "r", encoding="utf-8") as fh:
        content = fh.read()

    ts = now_iso()
    pattern = r"^" + re.escape(field) + r":.*"
    replacement = f"{field}: {value}"
    if re.search(pattern, content, flags=re.MULTILINE):
        content = re.sub(pattern, replacement, content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\n{replacement}\n"

    if re.search(r"^updated:", content, flags=re.MULTILINE):
        content = re.sub(r"^updated:.*", f"updated: \"{ts}\"", content, flags=re.MULTILINE)
    else:
        content = content.rstrip("\n") + f"\nupdated: \"{ts}\"\n"

    _atomic_write_text(state_file, content)


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
    content = re.sub(
        r"^" + re.escape(field) + r":.*",
        f"{field}: {new_val}",
        content,
        flags=re.MULTILINE,
    ) if m else content.rstrip("\n") + f"\n{field}: {new_val}\n"

    ts = now_iso()
    if write_task_state_content is not None:
        write_task_state_content(state_file, content, bump_revision=True, timestamp=ts)
    else:
        if re.search(r"^updated:", content, flags=re.MULTILINE):
            content = re.sub(r"^updated:.*", f"updated: \"{ts}\"", content, flags=re.MULTILINE)
        else:
            content = content.rstrip("\n") + f"\nupdated: \"{ts}\"\n"
        _atomic_write_text(state_file, content)


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


EXPECTED_AGENT_ROLES = {
    "PLAN.md": {"plan-skill"},
    "PLAN.meta.json": {"plan-skill"},
    "CHECKS.yaml": {"plan-skill"},
    "AUDIT_TRAIL.md": {"plan-skill"},
    "CRITIC__runtime.md": {"critic-runtime"},
    "QA__runtime.md": {"critic-runtime"},
    "CRITIC__plan.md": {"critic-plan"},
    "CRITIC__document.md": {"critic-document"},
    "CRITIC__intent.md": {"critic-intent"},
    "HANDOFF.md": {"developer"},
    "DOC_SYNC.md": {"writer"},
}


def enforce_agent_role_for_artifact(artifact_name):
    """Fail closed when the current agent role does not own the artifact.

    MCP tool wrappers set CLAUDE_AGENT_NAME, so coordinator / harness roles are
    visible here. For manual CLI use outside that environment we stay permissive
    when the role is unknown to avoid breaking standalone recovery workflows.
    """
    allowed_roles = EXPECTED_AGENT_ROLES.get(artifact_name)
    if not allowed_roles:
        return ""

    current_role = ""
    if get_agent_role is not None:
        try:
            current_role = str(get_agent_role() or "").strip()
        except Exception:
            current_role = ""
    if not current_role:
        current_role = str(os.environ.get("CLAUDE_AGENT_NAME", "") or "").strip()

    if not current_role:
        return ""
    if current_role in allowed_roles:
        return current_role

    expected = ", ".join(sorted(allowed_roles))
    raise ValueError(
        f"{artifact_name} must be written by [{expected}], not '{current_role}'. "
        "Use the matching harness role / MCP write_* tool so independent critic ownership is preserved."
    )


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

    if ensure_checks_schema_content is not None:
        content = ensure_checks_schema_content(content)

    ts = now_iso()
    for cid, verdict in checks_dict.items():
        new_status = "passed" if verdict == "PASS" else "failed"
        def replace_block(m):
            block = m.group(0)
            block = re.sub(r"(status:\s*)(\S+)", r"\g<1>" + new_status, block, count=1)
            block = re.sub(
                r"(last_updated:\s*)(\S+)",
                r'\g<1>"' + ts + '"',
                block,
                count=1,
            )
            return block

        pattern = r"(  - id: " + re.escape(cid) + r".*?)(?=\n  - id:|\Z)"
        content = re.sub(pattern, replace_block, content, flags=re.DOTALL)

    write_file(checks_file, content)


# ---------------------------------------------------------------------------
# Subcommand: critic-runtime
# ---------------------------------------------------------------------------


def cmd_critic_runtime(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()
    execution_mode = _get_yaml_field("execution_mode", os.path.join(task_dir, "TASK_STATE.yaml")) or "standard"
    artifact_name = "CRITIC__runtime.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    verdict_reason = getattr(args, "verdict_reason", None) or ""

    # Build CRITIC__runtime.md content
    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"execution_mode: {execution_mode}",
        f"summary: {args.summary}",
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
    meta = write_meta(meta_path, artifact_name, task_id, "critic-runtime", verdict=args.verdict, team_context=team_context)

    state_fields_updated = ["runtime_verdict", "runtime_verdict_freshness"]
    update_task_state_field(task_dir, "runtime_verdict", args.verdict)
    update_task_state_field(task_dir, "runtime_verdict_freshness", "current")
    if args.verdict == "FAIL":
        increment_task_state_int(task_dir, "runtime_verdict_fail_count")
        state_fields_updated.append("runtime_verdict_fail_count")
    if args.verdict == "BLOCKED_ENV":
        update_task_state_field(task_dir, "status", "blocked_env")
        state_fields_updated.append("status")

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=state_fields_updated, checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "verdict": args.verdict, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: critic-plan
# ---------------------------------------------------------------------------


def cmd_critic_plan(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    artifact_name = "CRITIC__plan.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"summary: {args.summary}",
    ]
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__plan.meta.json")

    write_file(artifact_path, md_content)
    meta = write_meta(meta_path, artifact_name, task_id, "critic-plan", verdict=args.verdict, team_context=team_context)

    update_task_state_field(task_dir, "plan_verdict", args.verdict)

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=["plan_verdict"], checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "verdict": args.verdict, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: critic-document
# ---------------------------------------------------------------------------


def cmd_critic_document(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    artifact_name = "CRITIC__document.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"summary: {args.summary}",
    ]
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__document.meta.json")

    write_file(artifact_path, md_content)
    meta = write_meta(meta_path, artifact_name, task_id, "critic-document", verdict=args.verdict, team_context=team_context)

    update_task_state_field(task_dir, "document_verdict", args.verdict)
    update_task_state_field(task_dir, "document_verdict_freshness", "current")

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=["document_verdict", "document_verdict_freshness"], checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "verdict": args.verdict, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: critic-intent
# ---------------------------------------------------------------------------


def cmd_critic_intent(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    artifact_name = "CRITIC__intent.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    md_lines = [
        f"verdict: {args.verdict}",
        f"task_id: {task_id}",
        f"summary: {args.summary}",
    ]
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "CRITIC__intent.meta.json")

    write_file(artifact_path, md_content)
    meta = write_meta(meta_path, artifact_name, task_id, "critic-intent", verdict=args.verdict, team_context=team_context)

    update_task_state_field(task_dir, "intent_verdict", args.verdict)
    update_task_state_field(task_dir, "intent_verdict_freshness", "current")

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=["intent_verdict", "intent_verdict_freshness"], checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "verdict": args.verdict, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: handoff
# ---------------------------------------------------------------------------


def cmd_handoff(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()
    artifact_name = "HANDOFF.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    md_lines = [
        f"# Handoff: {task_id}",
        f"written_at: {ts}",
        "",
        "## Summary",
        args.summary,
        "",
        "## Verification",
        args.verification,
    ]
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "HANDOFF.meta.json")

    write_file(artifact_path, md_content)
    meta = write_meta(meta_path, artifact_name, task_id, "developer", team_context=team_context)

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: doc-sync
# ---------------------------------------------------------------------------


def cmd_doc_sync(args):
    task_dir = _resolve_task_dir(args.task_id)
    task_id = task_id_from_dir(task_dir)
    ts = now_iso()
    artifact_name = "DOC_SYNC.md"
    enforce_agent_role_for_artifact(artifact_name)
    team_context = enforce_team_artifact_owner(task_dir, artifact_name)
    team_header = artifact_team_header_lines(team_context)

    md_lines = [
        f"# DOC_SYNC: {task_id}",
        f"written_at: {ts}",
        "",
        "## Summary",
        args.summary,
    ]
    if team_header:
        md_lines[2:2] = team_header
    md_content = "\n".join(md_lines) + "\n"

    artifact_path = os.path.join(task_dir, artifact_name)
    meta_path = os.path.join(task_dir, "DOC_SYNC.meta.json")

    write_file(artifact_path, md_content)
    meta = write_meta(meta_path, artifact_name, task_id, "writer", team_context=team_context)

    result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=[])
    result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})
    print(json.dumps(result, ensure_ascii=False))
    return 0


# ---------------------------------------------------------------------------
# Subcommand: plan
# ---------------------------------------------------------------------------

# NOTE: Intentional divergence from prewrite_gate.py::_check_plan_session_token.
# The hook-level gate accepts phase "context" as well as "write" and does not
# inspect the "source" field — this is correct for the hook because it must
# allow early PLAN_SESSION.json setup writes and other context-phase reads.
# This CLI helper is stricter: it requires state=="open", phase=="write", AND
# source=="plan-skill", because cmd_plan writes the final protected plan artefacts.
# Do not merge or reuse these two helpers.
def _plan_session_write_ok(task_dir):
    """Return True only when PLAN_SESSION.json has state=open, phase=write, source=plan-skill."""
    token_path = os.path.join(task_dir, "PLAN_SESSION.json")
    if not os.path.isfile(token_path):
        return False, "PLAN_SESSION.json not found"
    try:
        with open(token_path, "r", encoding="utf-8") as fh:
            token = json.load(fh)
    except (json.JSONDecodeError, OSError) as exc:
        return False, f"PLAN_SESSION.json unreadable: {exc}"
    state = token.get("state", "")
    phase = token.get("phase", "")
    source = token.get("source", "")
    if state != "open":
        return False, f"state={state!r} (expected 'open')"
    if phase != "write":
        return False, f"phase={phase!r} (expected 'write')"
    if source != "plan-skill":
        return False, f"source={source!r} (expected 'plan-skill')"
    return True, ""


_AUDIT_TRAIL_HEADER = (
    "| # | phase | decision | classification | principle | rationale | rejected_option |\n"
    "|---|---|---|---|---|---|---|\n"
)
_AUDIT_TRAIL_COLUMNS = ["#", "phase", "decision", "classification", "principle", "rationale", "rejected_option"]


def _validate_audit_row(row_text):
    """Return (ok, error_msg) for a pipe-delimited audit row."""
    stripped = row_text.strip()
    if not stripped.startswith("|"):
        return False, "Audit row must start with '|'"
    cells = [c.strip() for c in stripped.strip("|").split("|")]
    if len(cells) < len(_AUDIT_TRAIL_COLUMNS):
        return False, (
            f"Audit row has {len(cells)} column(s); expected {len(_AUDIT_TRAIL_COLUMNS)}: "
            + ", ".join(_AUDIT_TRAIL_COLUMNS)
        )
    return True, ""


def cmd_plan(args):
    task_dir = os.path.abspath(args.task_dir)
    _validate_task_dir(task_dir)
    task_id = task_id_from_dir(task_dir)
    artifact_choice = args.artifact

    # Read input content
    if args.input == "-":
        content = sys.stdin.read()
    else:
        if not os.path.isfile(args.input):
            print(f"ERROR: input file not found: {args.input}", file=sys.stderr)
            sys.exit(1)
        with open(args.input, "r", encoding="utf-8") as fh:
            content = fh.read()

    # Session-token check for plan artefacts (PLAN.md, PLAN.meta.json, CHECKS.yaml, AUDIT_TRAIL.md)
    ok, reason = _plan_session_write_ok(task_dir)
    if not ok:
        print(
            f"PLAN.md write requires active plan session token "
            f"(state=open, phase=write, source=plan-skill). Reason: {reason}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Role enforcement
    artifact_map = {
        "plan": "PLAN.md",
        "plan-meta": "PLAN.meta.json",
        "checks": "CHECKS.yaml",
        "audit": "AUDIT_TRAIL.md",
    }
    artifact_name = artifact_map[artifact_choice]
    try:
        enforce_agent_role_for_artifact(artifact_name)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)

    ts = now_iso()
    checks_updated = []

    if artifact_choice == "plan":
        artifact_path = os.path.join(task_dir, "PLAN.md")
        meta_path = os.path.join(task_dir, "PLAN.meta.json")
        write_file(artifact_path, content)
        meta_extra = {}
        for kv in (args.meta or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                meta_extra[k.strip()] = v.strip()
        meta = build_meta(artifact_name, task_id, "plan-skill")
        if meta_extra:
            meta["plan_meta"] = meta_extra
        _atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
        if args.checks:
            checks_dict = parse_checks_arg(args.checks)
            update_checks_yaml(task_dir, checks_dict)
            checks_updated = list(checks_dict.keys())
        result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=checks_updated)
        result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})

    elif artifact_choice == "plan-meta":
        artifact_path = os.path.join(task_dir, "PLAN.meta.json")
        meta_path = artifact_path  # the artifact IS the meta file
        meta = build_meta(artifact_name, task_id, "plan-skill")
        # Merge passthrough fields
        try:
            input_data = json.loads(content) if content.strip() else {}
        except json.JSONDecodeError:
            input_data = {}
        meta_extra = {}
        for kv in (args.meta or []):
            if "=" in kv:
                k, v = kv.split("=", 1)
                meta_extra[k.strip()] = v.strip()
        if input_data:
            meta["plan_meta"] = input_data
        elif meta_extra:
            meta["plan_meta"] = meta_extra
        _atomic_write_text(artifact_path, json.dumps(meta, indent=2) + "\n")
        if args.checks:
            checks_dict = parse_checks_arg(args.checks)
            update_checks_yaml(task_dir, checks_dict)
            checks_updated = list(checks_dict.keys())
        result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=checks_updated)
        result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})

    elif artifact_choice == "checks":
        artifact_path = os.path.join(task_dir, "CHECKS.yaml")
        meta_path = os.path.join(task_dir, "CHECKS.meta.json")
        write_file(artifact_path, content)
        meta = build_meta(artifact_name, task_id, "plan-skill")
        _atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
        result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=[])
        result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})

    elif artifact_choice == "audit":
        if not args.append:
            print("ERROR: --artifact audit requires --append flag", file=sys.stderr)
            sys.exit(1)
        # Validate the row
        row_ok, row_err = _validate_audit_row(content)
        if not row_ok:
            print(f"ERROR: {row_err}", file=sys.stderr)
            sys.exit(1)
        artifact_path = os.path.join(task_dir, "AUDIT_TRAIL.md")
        meta_path = os.path.join(task_dir, "AUDIT_TRAIL.meta.json")
        if os.path.isfile(artifact_path):
            existing = _read_text(artifact_path)
        else:
            existing = ""
        first_line = existing.lstrip("\n").split("\n")[0] if existing.strip() else ""
        has_header = first_line.startswith("| # |")
        if not existing.strip():
            new_content = _AUDIT_TRAIL_HEADER + content.rstrip("\n") + "\n"
        elif has_header:
            # Header already present — coalesce: append row only, no duplicate header
            new_content = existing.rstrip("\n") + "\n" + content.rstrip("\n") + "\n"
        else:
            # Existing content has no header row — prepend header then append
            new_content = existing.rstrip("\n") + "\n\n" + _AUDIT_TRAIL_HEADER + content.rstrip("\n") + "\n"
        write_file(artifact_path, new_content)
        meta = build_meta(artifact_name, task_id, "plan-skill")
        _atomic_write_text(meta_path, json.dumps(meta, indent=2) + "\n")
        result = finalize_write_result(artifact_path, meta_path, state_fields_updated=[], checks_updated=[])
        result.update({"artifact": artifact_name, "task_id": task_id, "meta_written_at": meta.get("written_at")})

    else:
        valid = ", ".join(sorted(artifact_map.keys()))
        print(f"ERROR: unknown --artifact value {artifact_choice!r}. Valid: {valid}", file=sys.stderr)
        sys.exit(1)

    print(json.dumps(result, ensure_ascii=False))
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
    p_rt.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_rt.add_argument(
        "--verdict", required=True, choices=["PASS", "FAIL", "BLOCKED_ENV"],
        help="Verdict value"
    )
    p_rt.add_argument("--summary", required=True, help="One-sentence summary")
    p_rt.add_argument("--transcript", required=True, help="Command transcript text")
    p_rt.add_argument("--verdict-reason", default=None, help="Optional extended reason")

    # --- critic-plan ---
    p_cp = subparsers.add_parser("critic-plan", help="Write CRITIC__plan.md")
    p_cp.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_cp.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p_cp.add_argument("--summary", required=True)

    # --- critic-document ---
    p_cd = subparsers.add_parser("critic-document", help="Write CRITIC__document.md")
    p_cd.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_cd.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p_cd.add_argument("--summary", required=True)

    # --- critic-intent ---
    p_ci = subparsers.add_parser("critic-intent", help="Write CRITIC__intent.md")
    p_ci.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_ci.add_argument("--verdict", required=True, choices=["PASS", "FAIL"])
    p_ci.add_argument("--summary", required=True)

    # --- handoff ---
    p_ho = subparsers.add_parser("handoff", help="Write HANDOFF.md")
    p_ho.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_ho.add_argument("--summary", required=True, help="Summary of what was done")
    p_ho.add_argument("--verification", required=True, help="Verification steps / commands")

    # --- doc-sync ---
    p_ds = subparsers.add_parser("doc-sync", help="Write DOC_SYNC.md")
    p_ds.add_argument("--task-id", required=True, help="Task slug, TASK__slug, or path")
    p_ds.add_argument("--summary", required=True, help="Summary of documentation changes")

    # --- plan ---
    p_pl = subparsers.add_parser("plan", help="Write PLAN.md / PLAN.meta.json / CHECKS.yaml / AUDIT_TRAIL.md")
    p_pl.add_argument("--task-dir", required=True, help="Absolute or relative path to task directory")
    p_pl.add_argument(
        "--artifact", required=True,
        choices=["plan", "plan-meta", "checks", "audit"],
        help="Which plan artifact to write: plan|plan-meta|checks|audit",
    )
    p_pl.add_argument(
        "--input", required=True,
        help="Path to input file, or '-' to read from stdin",
    )
    p_pl.add_argument(
        "--append", action="store_true", default=False,
        help="Append mode (only valid for --artifact audit)",
    )
    p_pl.add_argument(
        "--checks", default=None,
        help="Optional CHECKS.yaml updates, format: AC-001:PASS,AC-002:FAIL",
    )
    p_pl.add_argument(
        "--meta", action="append", default=None,
        help="Optional metadata passthrough as key=value (repeatable)",
    )

    return parser


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


DISPATCH = {
    "critic-runtime": cmd_critic_runtime,
    "critic-plan": cmd_critic_plan,
    "critic-document": cmd_critic_document,
    "critic-intent": cmd_critic_intent,
    "handoff": cmd_handoff,
    "doc-sync": cmd_doc_sync,
    "plan": cmd_plan,
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
