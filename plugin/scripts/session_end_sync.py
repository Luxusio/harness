#!/usr/bin/env python3
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field, yaml_array, manifest_field,
                  is_browser_first_project, is_tooling_ready, is_profile_enabled,
                  get_browser_qa_status,
                  TASK_DIR, MANIFEST, now_iso,
                  needs_document_critic, verdict_freshness, format_verdict_with_freshness)
from handoff_escalation import should_create_handoff, generate_handoff


def maintain_lite_full():
    """Compute full entropy indicators for session end (read-only)."""
    stale_count = 0
    orphan_count = 0
    broken_chain_count = 0
    dead_artifact_count = 0
    degraded_team_count = 0
    now_epoch = time.time()
    stale_threshold = 7 * 24 * 3600

    # Count stale tasks and dead artifacts
    if os.path.isdir(TASK_DIR):
        for entry in sorted(os.listdir(TASK_DIR)):
            task_path = os.path.join(TASK_DIR, entry)
            if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
                continue
            state_file = os.path.join(task_path, "TASK_STATE.yaml")
            if not os.path.isfile(state_file):
                continue

            status = yaml_field("status", state_file) or ""

            # Dead artifacts: CRITIC__*.md in closed task folders
            if status == "closed":
                for fname in os.listdir(task_path):
                    if fname.startswith("CRITIC__") and fname.endswith(".md"):
                        dead_artifact_count += 1
                continue

            if status in ("archived", "stale"):
                continue

            # Stale tasks
            updated_raw = yaml_field("updated", state_file) or ""
            if updated_raw:
                try:
                    from datetime import datetime
                    updated_raw_clean = updated_raw.replace("Z", "+00:00")
                    dt = datetime.fromisoformat(updated_raw_clean)
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

            # Count degraded team tasks
            orch = yaml_field("orchestration_mode", state_file) or ""
            ts = yaml_field("team_status", state_file) or ""
            if orch == "team" and ts == "degraded":
                degraded_team_count += 1

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
                superseded_by = yaml_field("superseded_by", note_path) or ""
                if superseded_by and superseded_by not in ("null", "~"):
                    # Check in same root directory first, then any doc/* root
                    target_same = os.path.join(root_dir, superseded_by)
                    target_common = os.path.join(doc_base, "common", superseded_by)
                    if not os.path.isfile(target_same) and not os.path.isfile(target_common):
                        broken_chain_count += 1

    return stale_count, orphan_count, broken_chain_count, dead_artifact_count, degraded_team_count


def main():
    read_hook_input()

    if not os.path.isfile(MANIFEST):
        sys.exit(0)

    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    print("=== HARNESS SESSION END SUMMARY ===")

    # Browser QA status (uses shared helper from _lib)
    browser_qa = get_browser_qa_status()
    print(f"browser_qa: {browser_qa}")
    print("")

    open_tasks = []
    blocked_tasks = []
    missing_doc_sync = []
    incomplete_verdicts = []

    for entry in sorted(os.listdir(TASK_DIR)):
        task_path = os.path.join(TASK_DIR, entry)
        if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
            continue
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue

        task_id = entry
        status = yaml_field("status", state_file) or "unknown"
        lane = yaml_field("lane", state_file) or "unknown"

        if status in ("closed", "archived", "stale"):
            continue

        if status == "blocked_env":
            blocked_tasks.append(f"{task_id} [lane: {lane}]")
        else:
            qa_mode = yaml_field("qa_mode", state_file) or "auto"
            open_tasks.append(f"{task_id} [status: {status}, lane: {lane}, qa_mode: {qa_mode}]")
            orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
            if orch_mode != "solo":
                team_status_val = yaml_field("team_status", state_file) or "n/a"
                open_tasks[-1] += f", orch: {orch_mode}, team: {team_status_val}"

            plan_v = yaml_field("plan_verdict", state_file) or "?"
            runtime_v = yaml_field("runtime_verdict", state_file) or "?"
            runtime_freshness = verdict_freshness(state_file, "runtime_verdict")
            doc_v = yaml_field("document_verdict", state_file) or "?"
            doc_freshness = verdict_freshness(state_file, "document_verdict")
            doc_needed = needs_document_critic(task_path)

            runtime_display = format_verdict_with_freshness(runtime_v, runtime_freshness)
            doc_display = format_verdict_with_freshness(doc_v, doc_freshness)
            runtime_incomplete = runtime_v != "PASS" or runtime_freshness != "current"
            document_incomplete = doc_needed and (doc_v != "PASS" or doc_freshness != "current")

            if plan_v == "pending" or runtime_incomplete or document_incomplete:
                incomplete_verdicts.append(
                    f"{task_id}: plan={plan_v} runtime={runtime_display} document={doc_display}"
                )

            mutates = yaml_field("mutates_repo", state_file) or ""
            if mutates in ("true", "unknown"):
                if not os.path.isfile(os.path.join(task_path, "DOC_SYNC.md")):
                    missing_doc_sync.append(task_id)

    if open_tasks:
        print(f"OPEN TASKS ({len(open_tasks)}):")
        for t in open_tasks:
            print(f"  - {t}")

    if blocked_tasks:
        print(f"BLOCKED_ENV TASKS ({len(blocked_tasks)}):")
        for t in blocked_tasks:
            print(f"  - {t}")

    if incomplete_verdicts:
        print("UNREADY VERDICTS:")
        for v in incomplete_verdicts:
            print(f"  - {v}")

    if missing_doc_sync:
        print("MISSING DOC_SYNC (repo-mutating tasks):")
        for d in missing_doc_sync:
            print(f"  - {d}")

    if not open_tasks and not blocked_tasks:
        print("All tasks closed. Clean session end.")

    # Handoff escalation — check open tasks and generate handoffs where needed
    handoff_reported = False
    if os.path.isdir(TASK_DIR):
        for entry in sorted(os.listdir(TASK_DIR)):
            task_path = os.path.join(TASK_DIR, entry)
            if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
                continue
            state_file = os.path.join(task_path, "TASK_STATE.yaml")
            if not os.path.isfile(state_file):
                continue
            status = yaml_field("status", state_file) or "unknown"
            if status in ("closed", "archived", "stale"):
                continue

            handoff_path = os.path.join(task_path, "SESSION_HANDOFF.json")
            existing_handoff = None
            if os.path.isfile(handoff_path):
                import json as _json
                try:
                    with open(handoff_path) as fh:
                        existing_handoff = _json.load(fh)
                except (OSError, ValueError):
                    existing_handoff = None

            if existing_handoff:
                if not handoff_reported:
                    print("")
                    print("HANDOFF ESCALATIONS:")
                    handoff_reported = True
                trigger = existing_handoff.get("trigger", "unknown")
                next_step = existing_handoff.get("next_step", "")
                read_first = existing_handoff.get("files_to_read_first", [])
                print(f"  - {entry}: SESSION_HANDOFF.json present (trigger: {trigger})")
                if next_step:
                    print(f"    Next step: {next_step}")
                if read_first:
                    print(f"    Read first: {', '.join(read_first)}")
            else:
                # Generate handoff now if runtime FAIL >= 2 and no handoff yet
                trigger = should_create_handoff(task_path, compaction_just_occurred=False)
                if trigger:
                    handoff = generate_handoff(task_path, trigger)
                    if handoff:
                        if not handoff_reported:
                            print("")
                            print("HANDOFF ESCALATIONS:")
                            handoff_reported = True
                        next_step = handoff.get("next_step", "")
                        read_first = handoff.get("files_to_read_first", [])
                        print(f"  - {entry}: SESSION_HANDOFF.json generated (trigger: {trigger})")
                        if next_step:
                            print(f"    Next step: {next_step}")
                        if read_first:
                            print(f"    Read first: {', '.join(read_first)}")

    # Maintain-lite full entropy summary
    stale_count, orphan_count, broken_chain_count, dead_artifact_count, degraded_team_count = maintain_lite_full()

    print("")
    print("=== MAINTAIN-LITE ===")
    print(f"stale_tasks: {stale_count}")
    print(f"orphan_notes: {orphan_count}")
    print(f"broken_supersede_chains: {broken_chain_count}")
    print(f"dead_artifacts: {dead_artifact_count}")
    print(f"degraded_teams: {degraded_team_count}")

    total_issues = stale_count + orphan_count + dead_artifact_count + degraded_team_count
    if broken_chain_count > 0 or total_issues >= 4:
        entropy = "HIGH"
    elif total_issues >= 1:
        entropy = "MEDIUM"
    else:
        entropy = "LOW"

    print(f"entropy: {entropy}")
    if entropy != "LOW":
        print("hint: run /harness:maintain to clean up")

    # Calibration candidate report (WS-3) — read-only, no file writes.
    # Counts tasks qualifying for local calibration case generation.
    # Actual case files are only written by /harness:maintain or calibration_miner.py.
    try:
        from calibration_miner import count_candidates
        cal_candidates = count_candidates()
        if cal_candidates > 0:
            print("")
            print("=== CALIBRATION ===")
            print(f"calibration_candidates: {cal_candidates}")
            print("hint: run /harness:maintain to generate local calibration cases")
    except Exception:
        pass  # Non-blocking — calibration reporting must never break session end


if __name__ == "__main__":
    main()
