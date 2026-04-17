#!/usr/bin/env python3
"""PreToolUse hook — enforce artifact ownership, plan-first rule, and scope lock.

Exits 0 to allow, exits 2 to block.
"""

import fnmatch
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_state, find_repo_root, TASK_DIR, yaml_array

PROTECTED_ARTIFACTS = {
    "PLAN.md": "plan-skill",
    "CRITIC__runtime.md": "qa-browser",  # also qa-api, qa-cli — checked by prefix match
    "HANDOFF.md": "developer",
    "DOC_SYNC.md": "developer",
    "CHECKS.yaml": "plan-skill + update_checks.py CLI",
}


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log_gate_parse_fail(repo_root, reason):
    """Append gate-parse-fail to learnings.jsonl (best-effort)."""
    try:
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = json.dumps({
            "ts": _now_iso(),
            "type": "gate-parse-fail",
            "source": "prewrite_gate",
            "key": "gate-parse-fail",
            "insight": reason,
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _read_progress_paths(active_dir):
    """Read allowed_paths, test_paths, forbidden_paths from PROGRESS.md YAML.

    Returns dict with three keys, each a list of strings (may be empty).
    Raises on YAML parse error so caller can catch and fall through.
    """
    progress_path = os.path.join(active_dir, "PROGRESS.md")
    if not os.path.isfile(progress_path):
        return None  # No PROGRESS.md — scope lock not active

    allowed = yaml_array("allowed_paths", progress_path)
    test_paths = yaml_array("test_paths", progress_path)
    forbidden = yaml_array("forbidden_paths", progress_path)
    return {
        "allowed_paths": allowed,
        "test_paths": test_paths,
        "forbidden_paths": forbidden,
    }


def _path_matches(file_path, patterns, repo_root):
    """Check if file_path matches any of the given patterns.

    Patterns are repo-relative globs. file_path is an absolute path.
    """
    try:
        rel = os.path.relpath(file_path, repo_root)
    except ValueError:
        return False  # Different drive on Windows
    for pat in patterns:
        pat = pat.strip()
        if not pat:
            continue
        # Canonicalize pattern: reject absolute or out-of-tree
        if os.path.isabs(pat):
            continue
        if pat.startswith(".."):
            continue
        if fnmatch.fnmatch(rel, pat):
            return True
        # Also match if pattern is a prefix directory
        if fnmatch.fnmatch(rel, pat.rstrip("/") + "/**"):
            return True
        # Allow pattern like "tests/fixtures/gstack_adoption/" to match files under it
        if pat.endswith("/") and rel.startswith(pat):
            return True
    return False


def _parse_progress_paths_safe(active_dir, repo_root):
    """Parse PROGRESS.md paths safely, logging parse failures.

    Returns dict or None (None = scope lock not active / parse failed).
    """
    try:
        result = _read_progress_paths(active_dir)
        if result is None:
            return None
        # Canonicalize: reject absolute or out-of-tree entries, log them
        cleaned = {}
        for key in ("allowed_paths", "test_paths", "forbidden_paths"):
            valid = []
            for raw in result.get(key, []):
                raw = raw.strip()
                if not raw:
                    continue
                if os.path.isabs(raw):
                    _log_gate_parse_fail(
                        repo_root,
                        f"PROGRESS.md {key} entry is absolute path (skipped): {raw}"
                    )
                    continue
                if raw.startswith(".."):
                    _log_gate_parse_fail(
                        repo_root,
                        f"PROGRESS.md {key} entry escapes repo (skipped): {raw}"
                    )
                    continue
                # Check realpath is inside repo
                candidate = os.path.realpath(os.path.join(repo_root, raw))
                if not candidate.startswith(os.path.realpath(repo_root)):
                    _log_gate_parse_fail(
                        repo_root,
                        f"PROGRESS.md {key} entry resolves out-of-tree (skipped): {raw}"
                    )
                    continue
                valid.append(raw)
            cleaned[key] = valid
        return cleaned
    except Exception as exc:
        _log_gate_parse_fail(repo_root, f"PROGRESS.md parse error: {exc}")
        return None  # Fall through to existing gate behavior


def _handle_scope_lock(file_path, active_dir, repo_root, task_id):
    """Enforce scope lock from PROGRESS.md. Returns (should_block, message) or (False, None)."""
    # One-shot bypass: HARNESS_DISABLE_SCOPE_LOCK=1
    if os.environ.get("HARNESS_DISABLE_SCOPE_LOCK") == "1":
        # Create bypass memo and proceed
        try:
            audit_dir = os.path.join(active_dir, "audit")
            os.makedirs(audit_dir, exist_ok=True)
            flag_path = os.path.join(audit_dir, "scope-lock-bypass.flag")
            with open(flag_path, "w") as f:
                f.write(f"bypass at {_now_iso()} for {file_path}\n")
        except Exception:
            pass
        return False, None

    # Delete bypass flag if it exists (one-shot: flag presence doesn't grant bypass)
    try:
        flag_path = os.path.join(active_dir, "audit", "scope-lock-bypass.flag")
        if os.path.isfile(flag_path):
            os.unlink(flag_path)
    except Exception:
        pass

    paths = _parse_progress_paths_safe(active_dir, repo_root)
    if paths is None:
        return False, None  # No PROGRESS.md or parse failed — fall through

    forbidden = paths.get("forbidden_paths", [])
    allowed = paths.get("allowed_paths", [])
    test_paths = paths.get("test_paths", [])

    # Check forbidden first
    if forbidden and _path_matches(file_path, forbidden, repo_root):
        matching = next(
            (p for p in forbidden if _path_matches(file_path, [p], repo_root)), "?"
        )
        allowed_summary = ", ".join(allowed[:3])
        if len(allowed) > 3:
            allowed_summary += ", ..."
        msg = (
            f"BLOCKED: scope-lock — {os.path.relpath(file_path, repo_root)} "
            f"is in forbidden_paths for {task_id}.\n"
            f"  forbidden: {matching}\n"
            f"  allowed:   {allowed_summary}\n"
            f"  fix options:\n"
            f"    (a) edit doc/harness/tasks/{task_id}/PROGRESS.md "
            f"→ move to allowed_paths\n"
            f"    (b) revert this edit and move it to a separate task\n"
            f"    (c) one-shot bypass: HARNESS_DISABLE_SCOPE_LOCK=1 (next command only)\n"
            f"  docs: doc/harness/patterns/scope-lock.md"
        )
        return True, msg

    # If allowed/test paths defined, check membership
    all_allowed = allowed + test_paths
    if all_allowed:
        if not _path_matches(file_path, all_allowed, repo_root):
            # Not in allowed list — warn but don't block (auto-add behavior per SKILL.md)
            # We log but allow through; this is a WARN not a BLOCK
            pass

    return False, None


def main():
    try:
        data = json.loads(sys.stdin.read())
    except Exception:
        sys.exit(0)

    tool_name = data.get("tool_name", "")
    if tool_name not in ("Write", "Edit"):
        sys.exit(0)

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("path") or ""
    if not file_path:
        sys.exit(0)

    file_path = os.path.abspath(file_path)
    repo_root = find_repo_root()
    tasks_dir = os.path.join(repo_root, TASK_DIR)

    # Allow writes inside task dirs (except protected artifacts there)
    inside_task_dir = file_path.startswith(tasks_dir)

    # Block protected artifacts only inside task directories
    basename = os.path.basename(file_path)
    if inside_task_dir and basename in PROTECTED_ARTIFACTS:
        owner = PROTECTED_ARTIFACTS[basename]
        print(f"BLOCKED: {basename} is owned by {owner}. Use the owning skill or MCP tool (e.g. Skill(harness:plan) for PLAN.md).", file=sys.stderr)
        sys.exit(2)

    if inside_task_dir:
        sys.exit(0)

    # Exempt paths — allowed without an active task
    exempt_prefixes = [
        os.path.join(repo_root, "doc", "harness", "learnings.jsonl"),
        os.path.join(repo_root, "doc", "harness", "qa"),
        os.path.join(repo_root, "doc", "harness", "checkpoints"),
        os.path.join(repo_root, "doc", "harness", "health-history.jsonl"),
        os.path.join(repo_root, "doc", "harness", "patterns"),
        os.path.join(repo_root, "doc", "harness", "retros"),
        os.path.join(repo_root, "doc", "harness", "visual-baselines"),
        os.path.join(repo_root, "doc", "harness", "benchmark"),
        os.path.join(repo_root, "doc", "harness", "audits"),
    ]
    for prefix in exempt_prefixes:
        if file_path == prefix or file_path.startswith(prefix + os.sep):
            sys.exit(0)

    # For source files, require an active task with PLAN.md
    active_file = os.path.join(tasks_dir, ".active")
    if not os.path.isfile(active_file):
        print(
            "BLOCKED: No active task. Source writes require the canonical loop. "
            "Run Skill(harness:run) or Skill(harness:plan) first.",
            file=sys.stderr,
        )
        sys.exit(2)

    try:
        with open(active_file) as f:
            active_dir = f.read().strip()
        if active_dir and os.path.isdir(active_dir) and active_dir.startswith(tasks_dir):
            if not os.path.isfile(os.path.join(active_dir, "PLAN.md")):
                if not os.path.isfile(os.path.join(active_dir, "MAINTENANCE")):
                    print("BLOCKED: PLAN.md does not exist yet. Run Skill(harness:plan) first.", file=sys.stderr)
                    sys.exit(2)
        else:
            print(
                "BLOCKED: Active task points to invalid path. "
                "Run Skill(harness:run) to create a new task.",
                file=sys.stderr,
            )
            sys.exit(2)
    except Exception as exc:
        print(
            f"BLOCKED: Cannot read .active file ({exc}). "
            "Run Skill(harness:run) to create a new task.",
            file=sys.stderr,
        )
        sys.exit(2)

    # Scope lock enforcement (after active task confirmed)
    try:
        task_id = os.path.basename(active_dir)
        should_block, msg = _handle_scope_lock(file_path, active_dir, repo_root, task_id)
        if should_block:
            print(msg, file=sys.stderr)
            sys.exit(2)
    except Exception as exc:
        _log_gate_parse_fail(repo_root, f"scope-lock enforcement error: {exc}")
        # Fall through — never block due to gate bug

    sys.exit(0)


if __name__ == "__main__":
    main()
