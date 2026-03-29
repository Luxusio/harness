#!/usr/bin/env python3
"""Handoff escalation helper — generates SESSION_HANDOFF.json for failing/complex tasks.

Imported by post_compact_sync.py and session_end_sync.py.
No pip packages — stdlib only.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import yaml_field, yaml_array, now_iso


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _count_runtime_fails(task_dir):
    """Count runtime FAIL verdicts from CRITIC__runtime.md history.

    We check CRITIC__runtime.md for verdict: FAIL lines, and also
    read runtime_verdict_fail_count from TASK_STATE.yaml if present.
    """
    count = 0

    # Check TASK_STATE.yaml for a stored fail count
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    stored = yaml_field("runtime_verdict_fail_count", state_file)
    if stored:
        try:
            return int(stored)
        except ValueError:
            pass

    # Fall back: scan CRITIC__runtime.md for FAIL verdicts
    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if not os.path.isfile(critic_path):
        return 0
    try:
        with open(critic_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        # Count lines like "verdict: FAIL" (case-insensitive)
        for line in content.splitlines():
            m = re.match(r"^\s*verdict\s*:\s*FAIL\s*$", line, re.IGNORECASE)
            if m:
                count += 1
    except OSError:
        pass

    return count


def _count_criterion_reopens(task_dir):
    """Return max reopen_count across all criteria in CHECKS.yaml.

    Returns 0 if CHECKS.yaml is absent (graceful degradation).
    """
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return 0

    max_reopen = 0
    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        for line in content.splitlines():
            m = re.match(r"^\s*reopen_count\s*:\s*(\d+)", line)
            if m:
                val = int(m.group(1))
                if val > max_reopen:
                    max_reopen = val
    except (OSError, ValueError):
        pass

    return max_reopen


def _compaction_occurred(task_dir):
    """Heuristic: check if a compaction marker is present.

    post_compact_sync.py is called after compaction — the caller passes
    this flag explicitly. This helper reads a marker file if present.
    """
    marker = os.path.join(task_dir, ".compaction_occurred")
    return os.path.isfile(marker)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def should_create_handoff(task_dir, compaction_just_occurred=False):
    """Check if any trigger condition is met for this task.

    Returns the trigger name (str) if triggered, or None.

    Triggers:
      1. runtime_verdict FAIL count >= 2
      2. Any criterion with reopen_count >= 2 (from CHECKS.yaml if present)
      3. execution_mode == "sprinted" AND compaction occurred
      4. status == "blocked_env" recovery re-entry
      5. roots_touched grew significantly beyond plan estimate
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None

    status = yaml_field("status", state_file) or ""
    if status in ("closed", "archived", "stale"):
        return None

    # Trigger 1: runtime FAIL count >= 2
    fail_count = _count_runtime_fails(task_dir)
    if fail_count >= 2:
        return "runtime_fail_repeat"

    # Trigger 2: criterion reopen_count >= 2
    max_reopen = _count_criterion_reopens(task_dir)
    if max_reopen >= 2:
        return "criterion_reopen_repeat"

    # Trigger 3: sprinted + compaction
    execution_mode = yaml_field("execution_mode", state_file) or ""
    if execution_mode == "sprinted" and (
        compaction_just_occurred or _compaction_occurred(task_dir)
    ):
        return "sprinted_compaction"

    # Trigger 4: blocked_env recovery re-entry
    # Indicated by status == blocked_env OR by a previous blocked_env in state history
    if status == "blocked_env":
        return "blocked_env_reentry"
    prev_blocked = yaml_field("was_blocked_env", state_file) or ""
    if prev_blocked.lower() in ("true", "1", "yes"):
        return "blocked_env_reentry"

    # Trigger 5: roots_touched grew significantly beyond plan estimate
    roots_touched = yaml_array("roots_touched", state_file)
    roots_estimate_raw = yaml_field("roots_estimate", state_file) or "0"
    try:
        roots_estimate = int(roots_estimate_raw)
    except ValueError:
        roots_estimate = 0
    if roots_estimate > 0 and len(roots_touched) > roots_estimate + 1:
        return "roots_exceeded_estimate"

    return None


def generate_handoff(task_dir, trigger):
    """Generate SESSION_HANDOFF.json with structured recovery context.

    Reads TASK_STATE.yaml, CHECKS.yaml (if present), CRITIC files.
    Writes SESSION_HANDOFF.json into task_dir.
    Returns the handoff dict (for caller display), or None on error.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    roots_touched = yaml_array("roots_touched", state_file)
    touched_paths = yaml_array("touched_paths", state_file)

    # --- open_check_ids: from CHECKS.yaml criteria not yet PASS ---
    open_check_ids = []
    last_known_good_checks = []
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if os.path.isfile(checks_path):
        open_check_ids, last_known_good_checks = _parse_checks(checks_path)

    # --- last_fail_evidence_refs: CRITIC files with FAIL verdict ---
    last_fail_evidence_refs = _find_fail_critic_files(task_dir)

    # --- paths_in_focus: most recently failed paths from critic content ---
    paths_in_focus = _extract_paths_from_critics(task_dir, touched_paths)

    # --- do_not_regress: criteria that passed + stable endpoints ---
    do_not_regress = _build_do_not_regress(task_dir, last_known_good_checks)

    # --- next_step: derive from trigger ---
    next_step = _derive_next_step(trigger, task_dir)

    # --- files_to_read_first: essential recovery reading list ---
    files_to_read_first = _build_read_first_list(task_dir, last_fail_evidence_refs)

    handoff = {
        "task_id": task_id,
        "trigger": trigger,
        "created_at": now_iso(),
        "open_check_ids": open_check_ids,
        "last_fail_evidence_refs": last_fail_evidence_refs,
        "last_known_good_checks": last_known_good_checks,
        "next_step": next_step,
        "roots_in_focus": roots_touched,
        "paths_in_focus": paths_in_focus,
        "do_not_regress": do_not_regress,
        "files_to_read_first": files_to_read_first,
    }

    # Write to task_dir/SESSION_HANDOFF.json
    handoff_path = os.path.join(task_dir, "SESSION_HANDOFF.json")
    try:
        with open(handoff_path, "w", encoding="utf-8") as fh:
            json.dump(handoff, fh, indent=2)
            fh.write("\n")
    except OSError as e:
        # Non-blocking — report but don't crash
        print(f"  [HANDOFF] Warning: could not write {handoff_path}: {e}", file=sys.stderr)
        return None

    return handoff


# ---------------------------------------------------------------------------
# Private helpers for generate_handoff
# ---------------------------------------------------------------------------

def _parse_checks(checks_path):
    """Parse CHECKS.yaml, return (open_check_ids, last_known_good_checks).

    Handles simple YAML block structure:
      - id: AC-001
        status: PASS
      - id: AC-002
        status: open
    """
    open_ids = []
    good_ids = []

    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return open_ids, good_ids

    current_id = None
    current_status = None

    for line in lines:
        id_m = re.match(r"^\s+-?\s*id\s*:\s*(.+)", line)
        if id_m:
            # Flush previous
            if current_id is not None:
                if current_status and current_status.upper() == "PASS":
                    good_ids.append(current_id)
                else:
                    open_ids.append(current_id)
            current_id = id_m.group(1).strip().strip('"').strip("'")
            current_status = None
            continue

        status_m = re.match(r"^\s+status\s*:\s*(.+)", line)
        if status_m and current_id is not None:
            current_status = status_m.group(1).strip().strip('"').strip("'")

    # Flush last entry
    if current_id is not None:
        if current_status and current_status.upper() == "PASS":
            good_ids.append(current_id)
        else:
            open_ids.append(current_id)

    return open_ids, good_ids


def _find_fail_critic_files(task_dir):
    """Return list of relative filenames for CRITIC__*.md files with FAIL."""
    fail_refs = []
    try:
        for fname in sorted(os.listdir(task_dir)):
            if not (fname.startswith("CRITIC__") and fname.endswith(".md")):
                continue
            fpath = os.path.join(task_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    content = fh.read()
                for line in content.splitlines():
                    if re.match(r"^\s*verdict\s*:\s*FAIL\s*$", line, re.IGNORECASE):
                        fail_refs.append(fname)
                        break
            except OSError:
                pass
    except OSError:
        pass
    return fail_refs


def _extract_paths_from_critics(task_dir, touched_paths):
    """Extract file paths mentioned in FAIL critic files.

    Falls back to touched_paths if nothing extracted.
    """
    paths = set()
    try:
        for fname in sorted(os.listdir(task_dir)):
            if not (fname.startswith("CRITIC__") and fname.endswith(".md")):
                continue
            fpath = os.path.join(task_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    content = fh.read()
                # Find file path patterns like src/foo/bar.py or plugin/scripts/x.py
                for m in re.finditer(r'\b([\w\-]+/[\w\-./]+\.\w+)\b', content):
                    candidate = m.group(1)
                    # Basic sanity: skip URLs, long strings
                    if len(candidate) < 100 and ".." not in candidate:
                        paths.add(candidate)
            except OSError:
                pass
    except OSError:
        pass

    if paths:
        return sorted(paths)[:10]  # Limit to 10 most relevant
    # Fall back to touched_paths (non-doc)
    return [p for p in touched_paths if not _is_doc_path(p)][:10]


def _is_doc_path(path):
    """Return True if path is documentation (not runtime)."""
    doc_patterns = [
        r"^doc/", r"^docs/", r"\.md$", r"^README", r"^CHANGELOG",
        r"^LICENSE", r"^\.claude/harness/critics/", r"^DOC_SYNC\.md$",
    ]
    for pattern in doc_patterns:
        if re.match(pattern, path):
            return True
    return False


def _build_do_not_regress(task_dir, last_known_good_checks):
    """Build do_not_regress list from passing criteria + stable indicators."""
    items = []

    # Add passing check descriptions if we can find them
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if os.path.isfile(checks_path) and last_known_good_checks:
        items.extend([f"criterion {cid} remains passing" for cid in last_known_good_checks])

    # Add any runtime PASS evidence from CRITIC__runtime.md
    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if os.path.isfile(critic_path):
        try:
            with open(critic_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            # Look for PASS evidence lines
            for line in content.splitlines():
                if re.search(r'\[EVIDENCE\].*PASS', line):
                    # Extract the description
                    m = re.search(r'\[EVIDENCE\]\s+\S+:\s+PASS\s+\S+\s+—\s+(.+)', line)
                    if m:
                        items.append(m.group(1).strip()[:120])
                    elif len(items) < 5:
                        # Add the raw line if short enough
                        stripped = line.strip()
                        if len(stripped) < 120:
                            items.append(stripped)
        except OSError:
            pass

    return items[:8]  # Cap at 8 items


def _derive_next_step(trigger, task_dir):
    """Derive a single clear next_step sentence from the trigger."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    blockers = yaml_field("blockers", state_file) or ""

    steps = {
        "runtime_fail_repeat": (
            "Reproduce the failure using the commands in CRITIC__runtime.md "
            "and fix the root cause before re-running QA."
        ),
        "criterion_reopen_repeat": (
            "Review CHECKS.yaml for repeatedly reopened criteria and address "
            "the underlying acceptance condition that keeps failing."
        ),
        "sprinted_compaction": (
            "Resume from PLAN.md sprint contract — verify roots_in_focus and "
            "check do_not_regress items before continuing implementation."
        ),
        "blocked_env_reentry": (
            f"Resolve the environment blocker ({blockers or 'see TASK_STATE.yaml'}) "
            "before resuming implementation."
        ),
        "roots_exceeded_estimate": (
            "Re-assess plan scope — roots_touched has grown beyond the original "
            "estimate; consider whether a mode escalation to sprinted is needed."
        ),
    }
    return steps.get(trigger, "Review the task context and resume from the last known good state.")


def _build_read_first_list(task_dir, last_fail_evidence_refs):
    """Build the essential recovery reading list."""
    candidates = ["PLAN.md", "TASK_STATE.yaml"]

    # Add fail critic files
    for ref in last_fail_evidence_refs:
        if ref not in candidates:
            candidates.append(ref)

    # Add HANDOFF.md if present
    if os.path.isfile(os.path.join(task_dir, "HANDOFF.md")):
        if "HANDOFF.md" not in candidates:
            candidates.append("HANDOFF.md")

    # Add CHECKS.yaml if present
    if os.path.isfile(os.path.join(task_dir, "CHECKS.yaml")):
        if "CHECKS.yaml" not in candidates:
            candidates.append("CHECKS.yaml")

    return candidates
