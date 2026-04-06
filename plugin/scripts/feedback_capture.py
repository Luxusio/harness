#!/usr/bin/env python3
"""feedback_capture.py — Stage and manage complaint artifacts for the harness.

Provides:
  - stage_complaint()         — create/update COMPLAINTS.yaml entry; update TASK_STATE
  - get_open_complaints()     — return list of open complaint dicts
  - summarize_open_complaints() — short human-readable summary string
  - mark_promoted()           — transition complaint to promoted
  - mark_resolved()           — transition complaint to resolved
  - mark_dismissed()          — transition complaint to dismissed

No pip dependencies — stdlib + pattern helpers only.
"""

import os
import re
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import now_iso, yaml_field, yaml_array, write_task_state_content


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_file(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError:
        return ""


def _write_file(path, content):
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _timestamp_id():
    """Generate a short unique ID based on current UTC timestamp."""
    now = datetime.now(timezone.utc)
    return "cmp_" + now.strftime("%Y%m%d%H%M%S%f")[:18]


def _parse_complaints(complaints_file):
    """Parse COMPLAINTS.yaml into a list of complaint dicts.

    Uses line-based regex to stay stdlib-only. Parses entries separated by '  - id:' markers.
    """
    complaints = []
    if not os.path.isfile(complaints_file):
        return complaints

    content = _read_file(complaints_file)
    if not content.strip():
        return complaints

    # Split into entry blocks: each entry starts with "  - id:"
    # We collect lines for each entry between markers
    current_block = []
    in_complaints = False

    for line in content.splitlines():
        if re.match(r'^complaints\s*:', line):
            in_complaints = True
            continue
        if not in_complaints:
            continue
        # New entry start
        if re.match(r'^\s{2}-\s+id\s*:', line):
            if current_block:
                complaints.append(_parse_entry_block(current_block))
                current_block = []
        current_block.append(line)

    if current_block:
        complaints.append(_parse_entry_block(current_block))

    return [c for c in complaints if c.get("id")]


def _parse_entry_block(lines):
    """Parse a single complaint entry block into a dict."""
    entry = {
        "id": "",
        "status": "open",
        "kind": "outcome_fail",
        "lane": "objective",
        "scope": "task",
        "text": "",
        "captured_at": "",
        "source_prompt_ref": "user_prompt",
        "related_check_ids": [],
        "blocks_close": True,
        "calibration_candidate": False,
        "promoted_note_path": None,
        "promoted_directive_id": None,
        "evidence_refs": [],
        "resolution": "",
    }

    for line in lines:
        # id
        m = re.match(r'^\s*-?\s*id\s*:\s*(.+)', line)
        if m:
            entry["id"] = m.group(1).strip().strip('"').strip("'")
            continue
        # status
        m = re.match(r'^\s+status\s*:\s*(.+)', line)
        if m:
            entry["status"] = m.group(1).strip().strip('"').strip("'")
            continue
        # kind
        m = re.match(r'^\s+kind\s*:\s*(.+)', line)
        if m:
            entry["kind"] = m.group(1).strip().strip('"').strip("'")
            continue
        # lane
        m = re.match(r'^\s+lane\s*:\s*(.+)', line)
        if m:
            entry["lane"] = m.group(1).strip().strip('"').strip("'")
            continue
        # scope
        m = re.match(r'^\s+scope\s*:\s*(.+)', line)
        if m:
            entry["scope"] = m.group(1).strip().strip('"').strip("'")
            continue
        # text
        m = re.match(r'^\s+text\s*:\s*(.+)', line)
        if m:
            entry["text"] = m.group(1).strip().strip('"').strip("'")
            continue
        # captured_at
        m = re.match(r'^\s+captured_at\s*:\s*(.+)', line)
        if m:
            entry["captured_at"] = m.group(1).strip().strip('"').strip("'")
            continue
        # source_prompt_ref
        m = re.match(r'^\s+source_prompt_ref\s*:\s*(.+)', line)
        if m:
            entry["source_prompt_ref"] = m.group(1).strip().strip('"').strip("'")
            continue
        # blocks_close
        m = re.match(r'^\s+blocks_close\s*:\s*(.+)', line)
        if m:
            val = m.group(1).strip().lower()
            entry["blocks_close"] = val not in ("false", "no", "0")
            continue
        # calibration_candidate
        m = re.match(r'^\s+calibration_candidate\s*:\s*(.+)', line)
        if m:
            val = m.group(1).strip().lower()
            entry["calibration_candidate"] = val in ("true", "yes", "1")
            continue
        # promoted_note_path
        m = re.match(r'^\s+promoted_note_path\s*:\s*(.+)', line)
        if m:
            val = m.group(1).strip()
            entry["promoted_note_path"] = None if val.lower() in ("null", "none", '""', "''") else val.strip('"').strip("'")
            continue
        # promoted_directive_id
        m = re.match(r'^\s+promoted_directive_id\s*:\s*(.+)', line)
        if m:
            val = m.group(1).strip()
            entry["promoted_directive_id"] = None if val.lower() in ("null", "none", '""', "''") else val.strip('"').strip("'")
            continue
        # resolution
        m = re.match(r'^\s+resolution\s*:\s*(.+)', line)
        if m:
            entry["resolution"] = m.group(1).strip().strip('"').strip("'")
            continue
        # related_check_ids (inline list only)
        m = re.match(r'^\s+related_check_ids\s*:\s*\[([^\]]*)\]', line)
        if m:
            ids_str = m.group(1).strip()
            if ids_str:
                entry["related_check_ids"] = [
                    s.strip().strip('"').strip("'")
                    for s in ids_str.split(",")
                    if s.strip()
                ]
            continue
        # evidence_refs (inline list only)
        m = re.match(r'^\s+evidence_refs\s*:\s*\[([^\]]*)\]', line)
        if m:
            refs_str = m.group(1).strip()
            if refs_str:
                entry["evidence_refs"] = [
                    s.strip().strip('"').strip("'")
                    for s in refs_str.split(",")
                    if s.strip()
                ]
            continue

    return entry


def _serialize_entry(e):
    """Serialize a complaint entry dict to YAML block lines."""
    def _bool(v):
        return "true" if v else "false"

    def _null_or_quoted(v):
        if v is None:
            return "null"
        return f'"{v}"'

    def _inline_list(lst):
        if not lst:
            return "[]"
        items = ", ".join(f'"{x}"' for x in lst)
        return f"[{items}]"

    lines = [
        f"  - id: {e['id']}",
        f"    status: {e['status']}",
        f"    kind: {e['kind']}",
        f"    lane: {e['lane']}",
        f"    scope: {e['scope']}",
        f"    text: \"{e['text'].replace(chr(34), chr(39))}\"",
        f"    captured_at: \"{e['captured_at']}\"",
        f"    source_prompt_ref: {e['source_prompt_ref']}",
        f"    related_check_ids: {_inline_list(e.get('related_check_ids') or [])}",
        f"    blocks_close: {_bool(e.get('blocks_close', True))}",
        f"    calibration_candidate: {_bool(e.get('calibration_candidate', False))}",
        f"    promoted_note_path: {_null_or_quoted(e.get('promoted_note_path'))}",
        f"    promoted_directive_id: {_null_or_quoted(e.get('promoted_directive_id'))}",
        f"    evidence_refs: {_inline_list(e.get('evidence_refs') or [])}",
        f"    resolution: \"{e.get('resolution', '').replace(chr(34), chr(39))}\"",
    ]
    return "\n".join(lines)


def _write_complaints(complaints_file, entries):
    """Write a list of complaint entry dicts to COMPLAINTS.yaml."""
    blocks = "\n".join(_serialize_entry(e) for e in entries)
    content = f"complaints:\n{blocks}\n" if entries else "complaints: []\n"
    _write_file(complaints_file, content)


def _update_task_state(task_dir, open_ids):
    """Update TASK_STATE.yaml complaint-related fields using line-replace strategy."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return

    content = _read_file(state_file)

    # complaint_capture_state
    new_capture_state = "pending" if open_ids else "clean"
    if re.search(r'^complaint_capture_state\s*:', content, re.MULTILINE):
        content = re.sub(
            r'^complaint_capture_state\s*:.*$',
            f"complaint_capture_state: {new_capture_state}",
            content, flags=re.MULTILINE
        )
    else:
        # Append after directive_capture_state if present, else at end
        if re.search(r'^directive_capture_state\s*:', content, re.MULTILINE):
            content = re.sub(
                r'^(directive_capture_state\s*:.*\n)',
                r'\1' + f"complaint_capture_state: {new_capture_state}\n",
                content, flags=re.MULTILINE, count=1
            )
        else:
            content = content.rstrip("\n") + f"\ncomplaint_capture_state: {new_capture_state}\n"

    # pending_complaint_ids
    if open_ids:
        ids_inline = ", ".join(f'"{i}"' for i in open_ids)
        new_ids_val = f"[{ids_inline}]"
    else:
        new_ids_val = "[]"

    if re.search(r'^pending_complaint_ids\s*:', content, re.MULTILINE):
        content = re.sub(
            r'^pending_complaint_ids\s*:.*$',
            f"pending_complaint_ids: {new_ids_val}",
            content, flags=re.MULTILINE
        )
    else:
        content = re.sub(
            r'^(complaint_capture_state\s*:.*\n)',
            r'\1' + f"pending_complaint_ids: {new_ids_val}\n",
            content, flags=re.MULTILINE, count=1
        )

    # last_complaint_at
    ts = now_iso()
    if re.search(r'^last_complaint_at\s*:', content, re.MULTILINE):
        content = re.sub(
            r'^last_complaint_at\s*:.*$',
            f'last_complaint_at: "{ts}"',
            content, flags=re.MULTILINE
        )
    else:
        content = re.sub(
            r'^(pending_complaint_ids\s*:.*\n)',
            r'\1' + f'last_complaint_at: "{ts}"\n',
            content, flags=re.MULTILINE, count=1
        )

    write_task_state_content(state_file, content, bump_revision=True)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def stage_complaint(
    task_dir,
    text,
    kind="outcome_fail",
    lane="objective",
    scope="task",
    blocks_close=True,
    calibration_candidate=False,
    related_check_ids=None,
):
    """Create or update a complaint entry in COMPLAINTS.yaml.

    Dedupe rule: if an entry with the same text already exists with status
    in (open, promoted), update it instead of creating a new one.

    After staging, updates TASK_STATE.yaml complaint fields.

    Args:
        task_dir: path to the task directory
        text: human-readable description of the complaint
        kind: outcome_fail | process_fail | preference_fail | false_pass | unclear
        lane: objective | subjective | mixed
        scope: task | repo | session
        blocks_close: True if this complaint blocks task close
        calibration_candidate: True if this should be a calibration candidate
        related_check_ids: list of CHECKS.yaml criterion IDs related to this complaint

    Returns:
        The complaint entry dict as stored.
    """
    if related_check_ids is None:
        related_check_ids = []

    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    entries = _parse_complaints(complaints_file)

    # Dedupe: check for same text with status in (open, promoted)
    normalized_text = text.strip()
    for existing in entries:
        if (
            existing.get("text", "").strip() == normalized_text
            and existing.get("status") in ("open", "promoted")
        ):
            # Update existing entry
            existing["kind"] = kind
            existing["lane"] = lane
            existing["scope"] = scope
            existing["blocks_close"] = blocks_close
            existing["calibration_candidate"] = calibration_candidate
            if related_check_ids:
                existing["related_check_ids"] = related_check_ids
            _write_complaints(complaints_file, entries)
            open_ids = [e["id"] for e in entries if e.get("status") == "open"]
            _update_task_state(task_dir, open_ids)
            return existing

    # New entry
    new_entry = {
        "id": _timestamp_id(),
        "status": "open",
        "kind": kind,
        "lane": lane,
        "scope": scope,
        "text": normalized_text,
        "captured_at": now_iso(),
        "source_prompt_ref": "user_prompt",
        "related_check_ids": related_check_ids,
        "blocks_close": blocks_close,
        "calibration_candidate": calibration_candidate,
        "promoted_note_path": None,
        "promoted_directive_id": None,
        "evidence_refs": [],
        "resolution": "",
    }
    entries.append(new_entry)
    _write_complaints(complaints_file, entries)

    open_ids = [e["id"] for e in entries if e.get("status") == "open"]
    _update_task_state(task_dir, open_ids)
    return new_entry


def get_open_complaints(task_dir):
    """Return list of open complaint entry dicts for the given task dir."""
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    entries = _parse_complaints(complaints_file)
    return [e for e in entries if e.get("status") == "open"]


def summarize_open_complaints(task_dir):
    """Return a short summary string of open complaints, or '' if none.

    Format: "Complaints: cmp_xxx outcome_fail (blocking) | cmp_yyy process_fail"
    """
    open_complaints = get_open_complaints(task_dir)
    if not open_complaints:
        return ""

    parts = []
    for c in open_complaints:
        cid = c.get("id", "?")
        kind = c.get("kind", "unclear")
        blocking = " (blocking)" if c.get("blocks_close", True) else ""
        parts.append(f"{cid} {kind}{blocking}")

    return "Complaints: " + " | ".join(parts)


def mark_promoted(task_dir, complaint_id, promoted_note_path=None, promoted_directive_id=None):
    """Update complaint status to 'promoted'.

    Args:
        task_dir: path to the task directory
        complaint_id: complaint ID to update
        promoted_note_path: path to the note this was promoted to (optional)
        promoted_directive_id: ID of directive this was promoted to (optional)

    Returns:
        Updated entry dict, or None if not found.
    """
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    entries = _parse_complaints(complaints_file)

    for e in entries:
        if e.get("id") == complaint_id:
            e["status"] = "promoted"
            if promoted_note_path is not None:
                e["promoted_note_path"] = promoted_note_path
            if promoted_directive_id is not None:
                e["promoted_directive_id"] = promoted_directive_id
            _write_complaints(complaints_file, entries)
            open_ids = [x["id"] for x in entries if x.get("status") == "open"]
            _update_task_state(task_dir, open_ids)
            return e

    return None


def mark_resolved(task_dir, complaint_id, resolution=""):
    """Update complaint status to 'resolved'.

    Args:
        task_dir: path to the task directory
        complaint_id: complaint ID to update
        resolution: short description of how it was resolved

    Returns:
        Updated entry dict, or None if not found.
    """
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    entries = _parse_complaints(complaints_file)

    for e in entries:
        if e.get("id") == complaint_id:
            e["status"] = "resolved"
            e["resolution"] = resolution
            _write_complaints(complaints_file, entries)
            open_ids = [x["id"] for x in entries if x.get("status") == "open"]
            _update_task_state(task_dir, open_ids)
            return e

    return None


def mark_dismissed(task_dir, complaint_id, reason=""):
    """Update complaint status to 'dismissed'.

    Args:
        task_dir: path to the task directory
        complaint_id: complaint ID to update
        reason: short explanation for dismissal

    Returns:
        Updated entry dict, or None if not found.
    """
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    entries = _parse_complaints(complaints_file)

    for e in entries:
        if e.get("id") == complaint_id:
            e["status"] = "dismissed"
            e["resolution"] = reason
            _write_complaints(complaints_file, entries)
            open_ids = [x["id"] for x in entries if x.get("status") == "open"]
            _update_task_state(task_dir, open_ids)
            return e

    return None


if __name__ == "__main__":
    import argparse

    os.environ.setdefault("HARNESS_SKIP_STDIN", "1")

    parser = argparse.ArgumentParser(description="Feedback capture utility for harness")
    subparsers = parser.add_subparsers(dest="command")

    # stage
    p_stage = subparsers.add_parser("stage", help="Stage a complaint")
    p_stage.add_argument("--task-dir", required=True)
    p_stage.add_argument("--text", required=True)
    p_stage.add_argument("--kind", default="outcome_fail")
    p_stage.add_argument("--lane", default="objective")
    p_stage.add_argument("--scope", default="task")
    p_stage.add_argument("--no-block", action="store_true")
    p_stage.add_argument("--calibration-candidate", action="store_true")

    # summary
    p_summary = subparsers.add_parser("summary", help="Summarize open complaints")
    p_summary.add_argument("--task-dir", required=True)

    # resolve
    p_resolve = subparsers.add_parser("resolve", help="Mark complaint resolved")
    p_resolve.add_argument("--task-dir", required=True)
    p_resolve.add_argument("--id", required=True)
    p_resolve.add_argument("--resolution", default="")

    # dismiss
    p_dismiss = subparsers.add_parser("dismiss", help="Mark complaint dismissed")
    p_dismiss.add_argument("--task-dir", required=True)
    p_dismiss.add_argument("--id", required=True)
    p_dismiss.add_argument("--reason", default="")

    args = parser.parse_args()

    if args.command == "stage":
        entry = stage_complaint(
            task_dir=args.task_dir,
            text=args.text,
            kind=args.kind,
            lane=args.lane,
            scope=args.scope,
            blocks_close=not args.no_block,
            calibration_candidate=args.calibration_candidate,
        )
        print(f"Staged: {entry['id']} ({entry['status']})")
    elif args.command == "summary":
        s = summarize_open_complaints(args.task_dir)
        print(s or "(no open complaints)")
    elif args.command == "resolve":
        e = mark_resolved(args.task_dir, args.id, args.resolution)
        if e:
            print(f"Resolved: {e['id']}")
        else:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "dismiss":
        e = mark_dismissed(args.task_dir, args.id, args.reason)
        if e:
            print(f"Dismissed: {e['id']}")
        else:
            print(f"Not found: {args.id}", file=sys.stderr)
            sys.exit(1)
    else:
        parser.print_help()
