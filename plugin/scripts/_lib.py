#!/usr/bin/env python3
"""harness2 minimal library — stdlib only, 7-field TASK_STATE.

TASK_STATE schema:
  task_id, status, runtime_verdict,
  touched_paths, plan_session_state, closed_at, updated

Routing is computed on-the-fly from manifest + artifacts. Never stored.
Provenance is derived from artifact existence, not counters.
"""

import os
import re
import subprocess
from datetime import datetime, timezone

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"

SCHEMA_FIELDS = (
    "task_id", "status", "runtime_verdict",
    "touched_paths", "plan_session_state",
    "closed_at", "updated",
)


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── YAML helpers (simple key-value + block arrays, no pyyaml) ────────────


def yaml_field(field, path):
    """Read a scalar field from a flat YAML file."""
    if not os.path.isfile(path):
        return None
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(prefix):
                val = line[len(prefix):].strip()
                if val in ("null", "~", "", "[]"):
                    return None
                return val.strip('"').strip("'")
    return None


def yaml_array(field, path):
    """Read a YAML array field (compact [] or block - item)."""
    if not os.path.isfile(path):
        return []
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            if rest == "[]":
                return []
            items = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.*)", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def _yaml_fmt(val):
    """Format a value for YAML output."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        if not val:
            return "[]"
        return "\n" + "\n".join(f"  - {v}" for v in val)
    return str(val)


# ── Task state read/write ────────────────────────────────────────────────


def state_file(task_dir):
    return os.path.join(task_dir, "TASK_STATE.yaml")


def read_state(task_dir):
    """Read all fields from TASK_STATE.yaml."""
    path = state_file(task_dir)
    result = {}
    if not os.path.isfile(path):
        return result
    for field in SCHEMA_FIELDS:
        if field == "touched_paths":
            result[field] = yaml_array(field, path)
        else:
            result[field] = yaml_field(field, path)
    return result


def write_state(task_dir, fields):
    """Write TASK_STATE.yaml preserving field order."""
    path = state_file(task_dir)
    os.makedirs(task_dir, exist_ok=True)
    lines = []
    for field in SCHEMA_FIELDS:
        lines.append(f"{field}: {_yaml_fmt(fields.get(field))}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return True


def set_state_field(task_dir, field, value):
    """Set a single field, rewriting the file."""
    fields = read_state(task_dir)
    if not fields:
        return False
    fields[field] = value
    fields["updated"] = now_iso()
    return write_state(task_dir, fields)


# ── Path resolution ──────────────────────────────────────────────────────


def find_repo_root(start_dir=None):
    """Find git repo root."""
    d = os.path.abspath(start_dir or os.getcwd())
    while d != "/":
        if os.path.isdir(os.path.join(d, ".git")):
            return d
        d = os.path.dirname(d)
    return os.path.abspath(start_dir or os.getcwd())


def _normalize_task_id(task_id=None, slug=None, task_dir=None):
    """Derive canonical TASK__<id> from arguments."""
    if task_id:
        return task_id if task_id.startswith("TASK__") else f"TASK__{task_id}"
    if slug:
        return f"TASK__{slug}"
    if task_dir:
        name = os.path.basename(os.path.normpath(task_dir))
        return name if name.startswith("TASK__") else f"TASK__{name}"
    return None


def canonical_task_dir(task_id=None, slug=None, task_dir=None,
                       tasks_dir=TASK_DIR, repo_root=None):
    """Resolve canonical task directory path."""
    repo_root = repo_root or find_repo_root()
    tid = _normalize_task_id(task_id, slug, task_dir)
    if not tid:
        return ""
    return os.path.join(repo_root, tasks_dir, tid)


def canonical_task_id(task_id=None, slug=None, task_dir=None,
                      tasks_dir=TASK_DIR, repo_root=None):
    """Derive canonical task id string."""
    return _normalize_task_id(task_id, slug, task_dir) or ""


# ── Scaffold ─────────────────────────────────────────────────────────────


def ensure_task_scaffold(task_dir, task_id, request_text=""):
    """Create task dir with minimal 7-field TASK_STATE.yaml."""
    os.makedirs(task_dir, exist_ok=True)
    tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    fields = {
        "task_id": tid,
        "status": "created",
        "runtime_verdict": "pending",
        "touched_paths": [],
        "plan_session_state": "closed",
        "closed_at": None,
        "updated": now_iso(),
    }
    write_state(task_dir, fields)

    created = [state_file(task_dir)]
    if request_text:
        req_path = os.path.join(task_dir, "REQUEST.md")
        if not os.path.isfile(req_path):
            with open(req_path, "w", encoding="utf-8") as f:
                f.write(request_text)
            created.append(req_path)
    return {"created": created, "task_dir": task_dir, "task_id": tid}


# ── Manifest ─────────────────────────────────────────────────────────────


def read_manifest_field(field, repo_root=None):
    repo_root = repo_root or find_repo_root()
    return yaml_field(field, os.path.join(repo_root, MANIFEST_PATH))


def is_maintenance_task(task_dir, repo_root=None):
    if os.path.isfile(os.path.join(task_dir, "MAINTENANCE")):
        return True
    return str(read_manifest_field("maintenance_default", repo_root) or "").lower() == "true"


# ── Routing (on-the-fly, never stored) ───────────────────────────────────


def compile_routing(task_dir, repo_root=None):
    repo_root = repo_root or find_repo_root()
    maintenance = is_maintenance_task(task_dir, repo_root)
    return {
        "maintenance_task": maintenance,
        "workflow_locked": not maintenance,
        "risk_level": "high" if maintenance else "medium",
        "execution_mode": "standard",
        "orchestration_mode": "solo",
        "planning_mode": "standard",
    }


# ── Task context ─────────────────────────────────────────────────────────


def emit_compact_context(task_dir):
    """Build the canonical task pack with on-the-fly routing."""
    st = read_state(task_dir)
    if not st:
        return {"error": "no TASK_STATE.yaml", "task_dir": task_dir}

    routing = compile_routing(task_dir)
    runtime_verdict = (st.get("runtime_verdict") or "pending").upper()
    touched = st.get("touched_paths") or []

    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_write_allowed = has_plan
    why_blocked = "" if source_write_allowed else "PLAN.md does not exist yet"

    missing_for_close = []
    if not has_plan:
        missing_for_close.append("PLAN.md")
    if runtime_verdict != "PASS":
        missing_for_close.append("runtime_verdict PASS")

    if not has_plan:
        next_action = "Create PLAN.md via plan skill before source writes."
    elif runtime_verdict != "PASS":
        next_action = "Run task_verify to check runtime verification."
    else:
        next_action = "Runtime verdict PASS — run task_close."

    return {
        "task_id": st.get("task_id") or os.path.basename(task_dir),
        "status": st.get("status") or "unknown",
        "task_dir": task_dir,
        "routing": routing,
        "runtime_verdict": runtime_verdict,
        "source_write_allowed": source_write_allowed,
        "why_source_write_blocked": why_blocked,
        "touched_paths": touched,
        "path_count": len(touched),
        "missing_for_close": missing_for_close,
        "next_action": next_action,
        "effective_close_gate": "standard",
    }


# ── Path sync ────────────────────────────────────────────────────────────


def sync_touched_paths(task_dir, new_paths=None):
    """Merge new paths into touched_paths."""
    st = read_state(task_dir)
    existing = st.get("touched_paths") or []
    incoming = [p for p in (new_paths or []) if p]
    merged = list(dict.fromkeys(existing + incoming))
    set_state_field(task_dir, "touched_paths", merged)
    return merged


def sync_from_git_diff(task_dir):
    """Sync touched paths from git diff."""
    repo_root = find_repo_root(task_dir)
    result = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True, text=True, cwd=repo_root,
    )
    if result.returncode != 0:
        result = subprocess.run(
            ["git", "diff", "--name-only"],
            capture_output=True, text=True, cwd=repo_root,
        )
    changed = [f.strip() for f in result.stdout.strip().splitlines() if f.strip()]
    if not changed:
        return []
    return sync_touched_paths(task_dir, changed)


# ── Artifact helpers ─────────────────────────────────────────────────────


def artifact_exists(task_dir, filename):
    return os.path.isfile(os.path.join(task_dir, filename))


def provenance_from_artifacts(task_dir):
    """Derive provenance from artifact existence."""
    return {
        agent: artifact_exists(task_dir, fn)
        for agent, fn in {
            "plan-skill": "PLAN.md",
            "developer": "HANDOFF.md",
            "qa-browser": "CRITIC__runtime.md",
            "qa-api": "CRITIC__runtime.md",
            "qa-cli": "CRITIC__runtime.md",
        }.items()
    }
