#!/usr/bin/env python3
"""calibration_miner.py — Local calibration case generator for critic-runtime.

Scans task directories for repeated failures and generates/updates local
calibration case files in plugin/calibration/local/critic-runtime/.

Trigger conditions (either):
  - CHECKS.yaml criterion with reopen_count >= MIN_REOPEN_COUNT (default: 2)
  - runtime_verdict_fail_count >= MIN_FAIL_COUNT (default: 2)

Usage:
  Called explicitly by /harness:maintain (writes files).
  Called in dry-run mode by session_end_sync.py (reports count only, no writes).

Deduplication: same task → same output file slug; existing file is overwritten
(updated), not duplicated.

No pip packages — stdlib only.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import yaml_field, yaml_array, now_iso, TASK_DIR

CALIBRATION_DIR = os.path.join("plugin", "calibration", "local", "critic-runtime")
MIN_REOPEN_COUNT = 2
MIN_FAIL_COUNT = 2


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _slug_from_task_id(task_id):
    """Convert TASK__foo-bar-v1 to foo-bar-v1."""
    slug = re.sub(r"^TASK__", "", task_id)
    return slug.lower().replace("_", "-")


def _count_runtime_fails(task_dir):
    """Count runtime FAIL verdicts for a task.

    Checks TASK_STATE.yaml for stored runtime_verdict_fail_count first;
    falls back to counting 'verdict: FAIL' lines in CRITIC__runtime.md.
    Returns 0 gracefully if neither is available.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    stored = yaml_field("runtime_verdict_fail_count", state_file)
    if stored:
        try:
            return int(stored)
        except ValueError:
            pass

    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if not os.path.isfile(critic_path):
        return 0

    count = 0
    try:
        with open(critic_path, "r", encoding="utf-8") as fh:
            for line in fh:
                if re.match(r"^\s*verdict\s*:\s*FAIL\s*$", line, re.IGNORECASE):
                    count += 1
    except OSError:
        pass
    return count


def _get_max_reopen_count(task_dir):
    """Return max reopen_count across all criteria in CHECKS.yaml.
    Returns 0 if CHECKS.yaml absent (graceful fallback).
    """
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return 0

    max_reopen = 0
    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^\s+reopen_count\s*:\s*(\d+)", line)
                if m:
                    val = int(m.group(1))
                    if val > max_reopen:
                        max_reopen = val
    except (OSError, ValueError):
        pass
    return max_reopen


def _get_reopened_criteria(task_dir):
    """Return list of (id, title, reopen_count) for criteria meeting MIN_REOPEN_COUNT."""
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return []

    results = []
    current_id = None
    current_title = None
    current_reopen = 0

    def _flush():
        if current_id and current_reopen >= MIN_REOPEN_COUNT:
            results.append((current_id, current_title or current_id, current_reopen))

    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m_id = re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
                if m_id:
                    _flush()
                    current_id = m_id.group(1).strip().strip('"').strip("'")
                    current_title = None
                    current_reopen = 0
                    continue
                m_title = re.match(r"^\s+title\s*:\s*(.+)", line)
                if m_title:
                    current_title = m_title.group(1).strip().strip('"').strip("'")
                    continue
                m_reopen = re.match(r"^\s+reopen_count\s*:\s*(\d+)", line)
                if m_reopen:
                    current_reopen = int(m_reopen.group(1))
    except (OSError, ValueError):
        pass

    _flush()
    return results


def _extract_fail_evidence(task_dir):
    """Extract brief failure description from CRITIC__runtime.md.

    Tries unmet_acceptance, then evidence field. Truncates to 300 chars.
    """
    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if not os.path.isfile(critic_path):
        return "No CRITIC__runtime.md found."
    try:
        with open(critic_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        for field in ("unmet_acceptance", "evidence"):
            m = re.search(r"^" + field + r"\s*:\s*(.+)", content, re.MULTILINE)
            if m:
                val = m.group(1).strip()
                if val and val.lower() not in ("none", "n/a", ""):
                    return val[:300]
    except OSError:
        pass
    return "See CRITIC__runtime.md for details."


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def _has_false_pass_complaint(task_dir):
    """Return True if COMPLAINTS.yaml has any false_pass kind complaint.

    Uses stdlib regex only — no pyyaml dependency.
    """
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    if not os.path.isfile(complaints_file):
        return False
    try:
        with open(complaints_file, "r", encoding="utf-8") as fh:
            content = fh.read()
        # Look for any entry with kind: false_pass
        return bool(re.search(r"^\s+kind\s*:\s*false_pass", content, re.MULTILINE))
    except OSError:
        return False


def find_calibration_candidates(tasks_dir=None):
    """Return list of task_dirs that qualify for calibration case generation.

    Skips closed/archived/stale tasks.
    Qualifies if: reopen_count >= MIN_REOPEN_COUNT OR runtime_fail_count >= MIN_FAIL_COUNT
    OR COMPLAINTS.yaml has a false_pass kind entry.
    """
    if tasks_dir is None:
        tasks_dir = TASK_DIR
    if not os.path.isdir(tasks_dir):
        return []

    candidates = []
    for entry in sorted(os.listdir(tasks_dir)):
        task_path = os.path.join(tasks_dir, entry)
        if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file) or ""
        if status in ("closed", "archived", "stale"):
            continue

        reopen = _get_max_reopen_count(task_path)
        fails = _count_runtime_fails(task_path)
        has_false_pass = _has_false_pass_complaint(task_path)

        if reopen >= MIN_REOPEN_COUNT or fails >= MIN_FAIL_COUNT or has_false_pass:
            candidates.append(task_path)

    return candidates


def mine_calibration_case(task_dir, output_dir=None, write=True):
    """Generate or update a local calibration case file for a qualifying task.

    Args:
        task_dir: path to the task directory
        output_dir: where to write .md files (default: CALIBRATION_DIR)
        write: if False, return case dict without writing (dry-run)

    Returns:
        dict with case fields, or None if task does not qualify.

    Deduplication: output filename = <slug>.md. Existing file is overwritten
    (updated timestamp), not duplicated.
    """
    if output_dir is None:
        output_dir = CALIBRATION_DIR

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    reopen = _get_max_reopen_count(task_dir)
    fails = _count_runtime_fails(task_dir)
    has_false_pass = _has_false_pass_complaint(task_dir)

    if reopen < MIN_REOPEN_COUNT and fails < MIN_FAIL_COUNT and not has_false_pass:
        return None

    # Build case content
    if has_false_pass and reopen < MIN_REOPEN_COUNT and fails < MIN_FAIL_COUNT:
        trigger = "false_pass_complaint"
        why_wrong = (
            "A false_pass complaint was staged — the previous PASS verdict was accepted "
            "by the critic but the user found the outcome did not meet requirements. "
            "The critic did not verify the acceptance condition with sufficient runtime evidence."
        )
        what_to_check = (
            "Next time: reproduce the user-reported failure scenario explicitly before declaring PASS. "
            "Do not rely on code-reading or partial test coverage. "
            "Check COMPLAINTS.yaml for related_check_ids to focus verification."
        )
    elif reopen >= MIN_REOPEN_COUNT:
        trigger = f"reopen_count={reopen}"
        reopened = _get_reopened_criteria(task_dir)
        if reopened:
            criteria_info = "; ".join(
                f"{cid} ({title})" for cid, title, _ in reopened
            )
        else:
            criteria_info = "unknown criterion"
        why_wrong = (
            f"Criterion {criteria_info} was marked passed but reopened "
            f"{reopen}+ times — the previous PASS did not verify the "
            f"acceptance condition robustly enough."
        )
        what_to_check = (
            f"Next time: verify the specific acceptance condition for "
            f"{criteria_info} with explicit runtime evidence before marking passed."
        )
    else:
        trigger = f"runtime_fail_count={fails}"
        fail_evidence = _extract_fail_evidence(task_dir)
        why_wrong = (
            f"Runtime verification failed {fails}+ times. "
            f"Last known failure: {fail_evidence}"
        )
        what_to_check = (
            "Next time: reproduce the failure scenario explicitly "
            "before declaring PASS. Do not rely on code-reading alone."
        )

    slug = _slug_from_task_id(task_id)
    evidence_refs = []
    if os.path.isfile(os.path.join(task_dir, "CRITIC__runtime.md")):
        evidence_refs.append("CRITIC__runtime.md")
    if os.path.isfile(os.path.join(task_dir, "CHECKS.yaml")):
        evidence_refs.append("CHECKS.yaml")
    if os.path.isfile(os.path.join(task_dir, "COMPLAINTS.yaml")):
        evidence_refs.append("COMPLAINTS.yaml")

    updated = now_iso()

    case = {
        "slug": slug,
        "task_id": task_id,
        "trigger": trigger,
        "why_wrong": why_wrong,
        "what_to_check": what_to_check,
        "evidence_refs": evidence_refs,
        "updated": updated,
    }

    if not write:
        return case

    # Write calibration case file (create dirs if needed)
    os.makedirs(output_dir, exist_ok=True)
    out_path = os.path.join(output_dir, f"{slug}.md")

    evidence_list = "\n".join(f"- {r}" for r in evidence_refs) if evidence_refs else "- none"
    content = f"""# Calibration Case: {slug}

pattern_title: {slug}
source_task_id: {task_id}
trigger: {trigger}
updated: {updated}

## Why previous PASS was wrong

{why_wrong}

## What critic must check next time

{what_to_check}

## Evidence refs

{evidence_list}
"""

    try:
        with open(out_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        print(f"CALIBRATION: wrote/updated {out_path} (trigger: {trigger})")
    except OSError as e:
        print(f"CALIBRATION ERROR: could not write {out_path}: {e}", file=sys.stderr)
        return None

    return case


def run_mining(tasks_dir=None, output_dir=None, dry_run=False):
    """Scan all qualifying tasks and mine calibration cases.

    Args:
        tasks_dir: task directory (default: TASK_DIR)
        output_dir: calibration output dir (default: CALIBRATION_DIR)
        dry_run: if True, report without writing files

    Returns:
        list of mined case dicts
    """
    candidates = find_calibration_candidates(tasks_dir)
    cases = []
    for task_dir in candidates:
        case = mine_calibration_case(
            task_dir,
            output_dir=output_dir or CALIBRATION_DIR,
            write=not dry_run,
        )
        if case:
            cases.append(case)
    return cases


def count_candidates(tasks_dir=None):
    """Return count of calibration candidates (read-only, no writes).

    Suitable for session_end_sync.py advisory reporting.
    """
    return len(find_calibration_candidates(tasks_dir))


if __name__ == "__main__":
    import argparse

    os.environ.setdefault("HARNESS_SKIP_STDIN", "1")
    parser = argparse.ArgumentParser(description="Mine local runtime calibration cases")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report candidates without writing files"
    )
    parser.add_argument("--tasks-dir", default=None, help="Override task directory")
    parser.add_argument("--output-dir", default=None, help="Override calibration output directory")
    args = parser.parse_args()

    cases = run_mining(
        tasks_dir=args.tasks_dir,
        output_dir=args.output_dir,
        dry_run=args.dry_run,
    )

    if cases:
        verb = "Would mine" if args.dry_run else "Mined"
        print(f"{verb} {len(cases)} calibration case(s):")
        for c in cases:
            print(f"  - {c['slug']} ({c['trigger']})")
    else:
        print("No calibration candidates found")
