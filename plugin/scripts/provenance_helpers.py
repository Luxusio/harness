#!/usr/bin/env python3
"""Artifact provenance sidecar (.meta.json) utilities.

Each protected artifact (PLAN.md, HANDOFF.md, DOC_SYNC.md, CRITIC__*.md)
has a companion .meta.json that records who created it and under what
workflow_mode.  Completion gate uses these to verify authorship.

Usage:
    from provenance_helpers import write_meta, read_meta, validate_meta

Functions are pure (no side-effects besides file I/O).
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import now_iso

# ---------------------------------------------------------------------------
# Owner registry — single source of truth for artifact → role mapping
# ---------------------------------------------------------------------------

PROTECTED_ARTIFACT_OWNERS = {
    "PLAN.md": {"plan-skill"},
    "HANDOFF.md": {"developer"},
    "DOC_SYNC.md": {"writer"},
    "CRITIC__plan.md": {"critic-plan"},
    "CRITIC__runtime.md": {"critic-runtime"},
    "CRITIC__document.md": {"critic-document"},
    "QA__runtime.md": {"critic-runtime"},
}


def meta_path_for(artifact_path):
    """Return the .meta.json sidecar path for an artifact.

    Example: /path/to/HANDOFF.md -> /path/to/HANDOFF.meta.json
    """
    base, _ = os.path.splitext(artifact_path)
    return base + ".meta.json"


def write_meta(artifact_path, task_id, author_role, author_agent,
               workflow_mode="compliant"):
    """Write a .meta.json sidecar for the given artifact.

    Returns the sidecar path on success, None on error.
    """
    sidecar = meta_path_for(artifact_path)
    data = {
        "artifact": os.path.basename(artifact_path),
        "task_id": task_id,
        "author_role": author_role,
        "author_agent": author_agent,
        "workflow_mode": workflow_mode,
        "created_at": now_iso(),
    }
    try:
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        return sidecar
    except OSError:
        return None


def read_meta(artifact_path):
    """Read a .meta.json sidecar.  Returns dict or None if absent/corrupt."""
    sidecar = meta_path_for(artifact_path)
    if not os.path.isfile(sidecar):
        return None
    try:
        with open(sidecar, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def validate_meta(artifact_path):
    """Validate that artifact's .meta.json exists and has correct owner.

    Returns (ok: bool, message: str).
    """
    basename = os.path.basename(artifact_path)
    allowed_roles = PROTECTED_ARTIFACT_OWNERS.get(basename)
    if allowed_roles is None:
        # Not a protected artifact — always valid
        return True, f"{basename} is not a protected artifact"

    meta = read_meta(artifact_path)
    if meta is None:
        return False, f"{basename} exists but lacks provenance sidecar (.meta.json)"

    author_role = meta.get("author_role", "")
    if author_role not in allowed_roles:
        expected = ", ".join(sorted(allowed_roles))
        return False, (
            f"{basename} authored by '{author_role}' "
            f"but expected one of: {expected}"
        )

    return True, f"{basename} provenance OK (author_role={author_role})"


def check_all_provenance(task_dir):
    """Check provenance for all protected artifacts in a task directory.

    Returns list of failure message strings (empty = all OK).
    """
    failures = []
    for artifact_name in PROTECTED_ARTIFACT_OWNERS:
        artifact_path = os.path.join(task_dir, artifact_name)
        if not os.path.isfile(artifact_path):
            continue  # Missing artifacts are checked elsewhere
        ok, msg = validate_meta(artifact_path)
        if not ok:
            failures.append(msg)
    return failures


def is_protected_artifact(filename):
    """Return True if filename is a protected artifact."""
    basename = os.path.basename(filename) if "/" in filename else filename
    return basename in PROTECTED_ARTIFACT_OWNERS


def get_allowed_roles(filename):
    """Return set of allowed roles for a protected artifact, or None."""
    basename = os.path.basename(filename) if "/" in filename else filename
    return PROTECTED_ARTIFACT_OWNERS.get(basename)
