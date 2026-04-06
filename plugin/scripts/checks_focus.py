#!/usr/bin/env python3
"""checks_focus.py — CHECKS.yaml focus/guardrail set computation.

Computes three sets from CHECKS.yaml for delta verification:
  focus_ids:    criteria needing immediate attention (failed / implemented_candidate / blocked)
  open_ids:     all criteria not yet passed
  guardrail_ids: passing criteria worth regression-checking

If SESSION_HANDOFF.json is present, its open_check_ids and last_known_good_checks
take precedence (they contain more recent recovery context).

Legacy fallback: if CHECKS.yaml is absent, returns empty sets without error.

No pip packages — stdlib only.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from _lib import normalize_check_status_value

# Statuses that put a criterion in the focus set
FOCUS_STATUSES = frozenset({"failed", "implemented_candidate", "blocked"})
PASS_STATUS = "passed"

# Summary formatting limits
MAX_FOCUS_DEFAULT = 3
MAX_GUARDRAIL_DEFAULT = 2
SUMMARY_MAX_CHARS = 120


def parse_checks(checks_path):
    """Parse CHECKS.yaml into a list of criterion dicts.

    Each dict has: id (str), status (str), title (str), reopen_count (int).
    Returns [] if file is absent or unreadable.
    """
    if not checks_path or not os.path.isfile(checks_path):
        return []

    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    checks = []
    current = {}

    for line in lines:
        # Detect start of a new criterion block (dash + id or just dash)
        m_dash_id = re.match(r"^\s*-\s+id\s*:\s*(.+)", line)
        if m_dash_id:
            if current.get("id"):
                checks.append(_finalize_check(current))
            current = {"id": m_dash_id.group(1).strip().strip('"').strip("'")}
            continue

        m_dash = re.match(r"^\s*-\s*$", line)
        if m_dash:
            if current.get("id"):
                checks.append(_finalize_check(current))
            current = {}
            continue

        # Fields inside a criterion block
        m_id = re.match(r"^\s+id\s*:\s*(.+)", line)
        if m_id and "id" not in current:
            current["id"] = m_id.group(1).strip().strip('"').strip("'")
            continue

        m_status = re.match(r"^\s+status\s*:\s*(.+)", line)
        if m_status:
            current["status"] = m_status.group(1).strip().strip('"').strip("'")
            continue

        m_title = re.match(r"^\s+title\s*:\s*(.+)", line)
        if m_title:
            current["title"] = m_title.group(1).strip().strip('"').strip("'")
            continue

        m_reopen = re.match(r"^\s+reopen_count\s*:\s*(\d+)", line)
        if m_reopen:
            current["reopen_count"] = int(m_reopen.group(1))
            continue

    if current.get("id"):
        checks.append(_finalize_check(current))

    return checks


def _finalize_check(raw):
    """Normalize a raw criterion dict with defaults."""
    return {
        "id": raw.get("id", "?"),
        "status": normalize_check_status_value(raw.get("status") or "unknown"),
        "title": raw.get("title", ""),
        "reopen_count": raw.get("reopen_count", 0),
    }


def compute_focus_sets(checks_path, session_handoff_path=None):
    """Compute focus, open, and guardrail sets.

    Args:
        checks_path: path to CHECKS.yaml (may be None or missing)
        session_handoff_path: path to SESSION_HANDOFF.json (optional)

    Returns dict:
        {
          'focus_ids':    list[str],  # failed / implemented_candidate / blocked
          'open_ids':     list[str],  # status != passed
          'guardrail_ids': list[str], # passed — regression-check candidates
          'has_checks':   bool,       # False if no CHECKS.yaml and no handoff data
        }
    """
    result = {
        "focus_ids": [],
        "open_ids": [],
        "guardrail_ids": [],
        "has_checks": False,
    }

    # Load SESSION_HANDOFF.json if available — it has higher-priority recovery context
    handoff_open = None
    handoff_guardrails = None
    if session_handoff_path and os.path.isfile(session_handoff_path):
        try:
            with open(session_handoff_path, "r", encoding="utf-8") as fh:
                handoff = json.load(fh)
            raw_open = handoff.get("open_check_ids")
            if isinstance(raw_open, list):
                handoff_open = [str(x) for x in raw_open if x]
            raw_good = handoff.get("last_known_good_checks")
            if isinstance(raw_good, list):
                handoff_guardrails = [str(x) for x in raw_good if x]
        except (OSError, ValueError, KeyError):
            pass

    # Load CHECKS.yaml
    if not checks_path or not os.path.isfile(checks_path):
        # No CHECKS.yaml — use handoff data if available
        if handoff_open is not None:
            result["has_checks"] = True
            result["open_ids"] = handoff_open
            result["focus_ids"] = list(handoff_open)  # treat all open as focus
        if handoff_guardrails is not None:
            result["has_checks"] = True
            result["guardrail_ids"] = handoff_guardrails
        return result

    result["has_checks"] = True
    checks = parse_checks(checks_path)

    if not checks:
        return result

    # Build sets from CHECKS.yaml
    focus_ids_from_yaml = []
    open_ids_from_yaml = []
    guardrail_ids_from_yaml = []

    for c in checks:
        cid = c["id"]
        status = c["status"]

        if status == PASS_STATUS:
            guardrail_ids_from_yaml.append(cid)
        else:
            open_ids_from_yaml.append(cid)
            if status in FOCUS_STATUSES:
                focus_ids_from_yaml.append(cid)

    # Merge with handoff data — handoff takes precedence for open/guardrail lists
    if handoff_open is not None:
        result["open_ids"] = handoff_open
        # Focus: intersection of handoff open + yaml focus statuses, plus any
        # handoff open IDs that appear as focus in yaml
        yaml_focus_set = set(focus_ids_from_yaml)
        result["focus_ids"] = [cid for cid in handoff_open if cid in yaml_focus_set]
        # If handoff open has IDs not in yaml focus, include them too
        # (they might be open for a reason we don't know)
        yaml_open_set = set(open_ids_from_yaml)
        for cid in handoff_open:
            if cid not in yaml_focus_set and cid not in result["focus_ids"]:
                if cid in yaml_open_set:
                    result["focus_ids"].append(cid)
    else:
        result["open_ids"] = open_ids_from_yaml
        result["focus_ids"] = focus_ids_from_yaml

    result["guardrail_ids"] = handoff_guardrails if handoff_guardrails is not None else guardrail_ids_from_yaml

    return result


def format_checks_summary(
    focus_ids,
    guardrail_ids,
    max_focus=MAX_FOCUS_DEFAULT,
    max_guardrail=MAX_GUARDRAIL_DEFAULT,
):
    """Format a short summary string for prompt injection.

    Returns empty string if nothing to report.

    Examples:
      'Checks: focus AC-002, AC-005 | guardrails AC-001'
      'Checks: focus AC-001, AC-002, AC-003 (+1 more)'
      'Checks: guardrails AC-001, AC-002'
    """
    if not focus_ids and not guardrail_ids:
        return ""

    parts = []

    if focus_ids:
        limited = focus_ids[:max_focus]
        focus_str = "focus " + ", ".join(limited)
        extra = len(focus_ids) - max_focus
        if extra > 0:
            focus_str += f" (+{extra} more)"
        parts.append(focus_str)

    if guardrail_ids:
        limited = guardrail_ids[:max_guardrail]
        parts.append("guardrails " + ", ".join(limited))

    summary = "Checks: " + " | ".join(parts)

    # Hard truncate to SUMMARY_MAX_CHARS
    if len(summary) > SUMMARY_MAX_CHARS:
        summary = summary[: SUMMARY_MAX_CHARS - 3] + "..."

    return summary


def get_checks_summary_for_task(task_dir):
    """Convenience: compute and format a checks summary for a task directory.

    Returns '' if CHECKS.yaml absent or nothing to report.
    Intended for injection into prompt memory context.
    """
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    handoff_path = os.path.join(task_dir, "SESSION_HANDOFF.json")

    sets = compute_focus_sets(
        checks_path if os.path.isfile(checks_path) else None,
        session_handoff_path=handoff_path if os.path.isfile(handoff_path) else None,
    )

    if not sets["has_checks"]:
        return ""

    return format_checks_summary(sets["focus_ids"], sets["guardrail_ids"])


if __name__ == "__main__":
    import argparse

    os.environ.setdefault("HARNESS_SKIP_STDIN", "1")
    parser = argparse.ArgumentParser(description="Compute CHECKS focus sets")
    parser.add_argument("task_dir", help="Task directory containing CHECKS.yaml")
    args = parser.parse_args()

    summary = get_checks_summary_for_task(args.task_dir)
    if summary:
        print(summary)
    else:
        print("(no CHECKS data)")
