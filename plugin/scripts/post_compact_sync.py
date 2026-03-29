#!/usr/bin/env python3
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field, yaml_array, manifest_field,
                  is_browser_first_project, is_tooling_ready, is_profile_enabled,
                  TASK_DIR, MANIFEST, now_iso)
from handoff_escalation import should_create_handoff, generate_handoff


def get_browser_qa_status():
    """Check browser QA status from manifest sections."""
    browser_qa = "disabled"

    in_qa = False
    try:
        with open(MANIFEST) as f:
            for line in f:
                if line.startswith("qa:"):
                    in_qa = True
                    continue
                if in_qa:
                    if line and not line[0].isspace():
                        in_qa = False
                        continue
                    if "browser_qa_supported:" in line:
                        val = line.split("browser_qa_supported:", 1)[1].strip().lower()
                        if val == "true":
                            browser_qa = "enabled"
                        break
    except (OSError, IOError):
        pass

    if browser_qa == "disabled":
        in_browser = False
        try:
            with open(MANIFEST) as f:
                for line in f:
                    if line.startswith("browser:"):
                        in_browser = True
                        continue
                    if in_browser:
                        if line and not line[0].isspace():
                            in_browser = False
                            continue
                        if "enabled:" in line:
                            val = line.split("enabled:", 1)[1].strip().lower()
                            if val == "true":
                                browser_qa = "enabled"
                            break
        except (OSError, IOError):
            pass

    return browser_qa


def maintain_lite_entropy():
    """Compute entropy indicators (read-only)."""
    stale_count = 0
    orphan_count = 0
    broken_chain_count = 0
    now_epoch = time.time()
    stale_threshold = 7 * 24 * 3600

    # Count stale tasks
    if os.path.isdir(TASK_DIR):
        for entry in sorted(os.listdir(TASK_DIR)):
            task_path = os.path.join(TASK_DIR, entry)
            if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
                continue
            state_file = os.path.join(task_path, "TASK_STATE.yaml")
            if not os.path.isfile(state_file):
                continue
            status = yaml_field(state_file, "status") or ""
            if status in ("closed", "archived", "stale"):
                continue
            # Use file mtime as fallback, try updated: field first
            updated_raw = yaml_field(state_file, "updated") or ""
            if updated_raw:
                try:
                    from datetime import datetime, timezone
                    # Handle ISO 8601 with or without timezone
                    updated_raw_clean = updated_raw.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(updated_raw_clean)
                    if dt.tzinfo is None:
                        updated_epoch = dt.timestamp()
                    else:
                        updated_epoch = dt.timestamp()
                    age = now_epoch - updated_epoch
                    if age > stale_threshold:
                        stale_count += 1
                except (ValueError, OSError):
                    pass
            else:
                mtime = os.path.getmtime(state_file)
                if (now_epoch - mtime) > stale_threshold:
                    stale_count += 1

    # Count orphan notes across all doc/* roots
    doc_base = "doc"
    if os.path.isdir(doc_base):
        for root_entry in os.listdir(doc_base):
            root_dir = os.path.join(doc_base, root_entry)
            if not os.path.isdir(root_dir):
                continue
            for fname in os.listdir(root_dir):
                if not fname.endswith(".md"):
                    continue
                if fname == "CLAUDE.md":
                    continue
                note_path = os.path.join(root_dir, fname)
                if not os.path.isfile(note_path):
                    continue
                # Check if note filename appears in any CLAUDE.md under doc/
                found = False
                for walk_root, dirs, files in os.walk(doc_base):
                    if "CLAUDE.md" in files:
                        claude_path = os.path.join(walk_root, "CLAUDE.md")
                        try:
                            with open(claude_path) as f:
                                if fname in f.read():
                                    found = True
                                    break
                        except (OSError, IOError):
                            pass
                if not found:
                    orphan_count += 1

    # Count broken supersede chains across all doc/* roots
    if os.path.isdir(doc_base):
        for root_entry in os.listdir(doc_base):
            root_dir = os.path.join(doc_base, root_entry)
            if not os.path.isdir(root_dir):
                continue
            for fname in os.listdir(root_dir):
                if not fname.endswith(".md"):
                    continue
                note_path = os.path.join(root_dir, fname)
                if not os.path.isfile(note_path):
                    continue
                superseded_by = yaml_field(note_path, "superseded_by") or ""
                if superseded_by and superseded_by not in ("null", "~"):
                    # Check in same root directory first, then any doc/* root
                    target_same = os.path.join(root_dir, superseded_by)
                    target_common = os.path.join(doc_base, "common", superseded_by)
                    if not os.path.isfile(target_same) and not os.path.isfile(target_common):
                        broken_chain_count += 1

    return stale_count, orphan_count, broken_chain_count


def main():
    read_hook_input()

    if not os.path.isfile(MANIFEST):
        sys.exit(0)

    if not os.path.isdir(TASK_DIR):
        print("harness: no active tasks after compaction.")
        sys.exit(0)

    print("=== HARNESS POST-COMPACT SUMMARY ===")

    browser_qa = get_browser_qa_status()
    print(f"browser_qa: {browser_qa}")
    print("")

    open_count = 0
    blocked_count = 0
    pending_verdicts = 0

    for entry in sorted(os.listdir(TASK_DIR)):
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue

        task_id = entry
        status = yaml_field(state_file, "status") or "unknown"
        lane = yaml_field(state_file, "lane") or "unknown"

        if status in ("closed", "archived", "stale"):
            continue

        if status == "blocked_env":
            blocked_count += 1
            print(f"- {task_id} [BLOCKED_ENV, lane: {lane}]")
            blockers = yaml_field(state_file, "blockers") or ""
            if blockers and blockers != "[]":
                print(f"  blockers: {blockers}")
        else:
            open_count += 1
            plan_v = yaml_field(state_file, "plan_verdict") or "?"
            runtime_v = yaml_field(state_file, "runtime_verdict") or "?"
            doc_v = yaml_field(state_file, "document_verdict") or "?"
            qa_mode = yaml_field(state_file, "qa_mode") or "auto"
            mutates = yaml_field(state_file, "mutates_repo") or ""

            doc_sync_status = "n/a"
            if mutates in ("true", "unknown"):
                if os.path.isfile(os.path.join(task_path, "DOC_SYNC.md")):
                    doc_sync_status = "present"
                else:
                    doc_sync_status = "missing"

            print(f"- {task_id} [{status}, lane: {lane}, qa_mode: {qa_mode}]")
            print(f"  verdicts: plan={plan_v} runtime={runtime_v} document={doc_v}")
            print(f"  doc_sync: {doc_sync_status}")

            if plan_v == "pending":
                pending_verdicts += 1
            if runtime_v == "pending":
                pending_verdicts += 1
            if doc_v == "pending":
                pending_verdicts += 1

    if open_count == 0 and blocked_count == 0:
        print("(no active tasks)")
    else:
        print("")
        print(f"Summary: {open_count} open, {blocked_count} blocked_env, {pending_verdicts} pending verdicts")

    # Handoff escalation — check open tasks for trigger conditions
    for entry in sorted(os.listdir(TASK_DIR)):
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field(state_file, "status") or "unknown"
        if status in ("closed", "archived", "stale"):
            continue

        trigger = should_create_handoff(task_path, compaction_just_occurred=True)
        if trigger:
            handoff = generate_handoff(task_path, trigger)
            if handoff:
                print("")
                print(f"[HANDOFF] {entry} has SESSION_HANDOFF.json (trigger: {trigger})")
                print(f"  Next step: {handoff.get('next_step', '')}")
                read_first = handoff.get("files_to_read_first", [])
                if read_first:
                    print(f"  Read first: {', '.join(read_first)}")

    # Maintain-lite entropy
    stale_count, orphan_count, broken_chain_count = maintain_lite_entropy()

    total_issues = stale_count + orphan_count
    if broken_chain_count > 0 or total_issues >= 4:
        entropy = "HIGH"
    elif total_issues >= 1:
        entropy = "MEDIUM"
    else:
        entropy = "LOW"

    print("")
    print(f"harness health: entropy={entropy}")
    if stale_count > 0:
        print(f"  {stale_count} task(s) may be stale (updated > 7 days ago)")
    if orphan_count > 0:
        print(f"  {orphan_count} note(s) are orphaned (not in any CLAUDE.md index)")
    if broken_chain_count > 0:
        print(f"  {broken_chain_count} broken supersede chain(s)")
    if entropy != "LOW":
        print("  hint: run /harness:maintain to clean up")


if __name__ == "__main__":
    main()
