#!/usr/bin/env python3
import sys, os, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field, yaml_array, manifest_field,
                  is_browser_first_project, is_tooling_ready, is_profile_enabled,
                  TASK_DIR, MANIFEST, now_iso)


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


def maintain_lite_full():
    """Compute full entropy indicators for session end (read-only)."""
    stale_count = 0
    orphan_count = 0
    broken_chain_count = 0
    dead_artifact_count = 0
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

            status = yaml_field(state_file, "status") or ""

            # Dead artifacts: CRITIC__*.md in closed task folders
            if status == "closed":
                for fname in os.listdir(task_path):
                    if fname.startswith("CRITIC__") and fname.endswith(".md"):
                        dead_artifact_count += 1
                continue

            if status in ("archived", "stale"):
                continue

            # Stale tasks
            updated_raw = yaml_field(state_file, "updated") or ""
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

    # Count orphan notes
    doc_common = "doc/common"
    if os.path.isdir(doc_common):
        for fname in os.listdir(doc_common):
            if not fname.endswith(".md"):
                continue
            if fname == "CLAUDE.md":
                continue
            note_path = os.path.join(doc_common, fname)
            if not os.path.isfile(note_path):
                continue
            found = False
            for root, dirs, files in os.walk("doc"):
                if "CLAUDE.md" in files:
                    claude_path = os.path.join(root, "CLAUDE.md")
                    try:
                        with open(claude_path) as f:
                            if fname in f.read():
                                found = True
                                break
                    except (OSError, IOError):
                        pass
            if not found:
                orphan_count += 1

    # Count broken supersede chains
    if os.path.isdir(doc_common):
        for fname in os.listdir(doc_common):
            if not fname.endswith(".md"):
                continue
            note_path = os.path.join(doc_common, fname)
            if not os.path.isfile(note_path):
                continue
            superseded_by = yaml_field(note_path, "superseded_by") or ""
            if superseded_by and superseded_by not in ("null", "~"):
                target = os.path.join(doc_common, superseded_by)
                if not os.path.isfile(target):
                    broken_chain_count += 1

    return stale_count, orphan_count, broken_chain_count, dead_artifact_count


def main():
    read_hook_input()

    if not os.path.isfile(MANIFEST):
        sys.exit(0)

    if not os.path.isdir(TASK_DIR):
        sys.exit(0)

    print("=== HARNESS SESSION END SUMMARY ===")

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
        status = yaml_field(state_file, "status") or "unknown"
        lane = yaml_field(state_file, "lane") or "unknown"

        if status in ("closed", "archived", "stale"):
            continue

        if status == "blocked_env":
            blocked_tasks.append(f"{task_id} [lane: {lane}]")
        else:
            qa_mode = yaml_field(state_file, "qa_mode") or "auto"
            open_tasks.append(f"{task_id} [status: {status}, lane: {lane}, qa_mode: {qa_mode}]")

            plan_v = yaml_field(state_file, "plan_verdict") or "?"
            runtime_v = yaml_field(state_file, "runtime_verdict") or "?"
            doc_v = yaml_field(state_file, "document_verdict") or "?"
            if plan_v == "pending" or runtime_v == "pending" or doc_v == "pending":
                incomplete_verdicts.append(
                    f"{task_id}: plan={plan_v} runtime={runtime_v} document={doc_v}"
                )

            mutates = yaml_field(state_file, "mutates_repo") or ""
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
        print("PENDING VERDICTS:")
        for v in incomplete_verdicts:
            print(f"  - {v}")

    if missing_doc_sync:
        print("MISSING DOC_SYNC (repo-mutating tasks):")
        for d in missing_doc_sync:
            print(f"  - {d}")

    if not open_tasks and not blocked_tasks:
        print("All tasks closed. Clean session end.")

    # Maintain-lite full entropy summary
    stale_count, orphan_count, broken_chain_count, dead_artifact_count = maintain_lite_full()

    print("")
    print("=== MAINTAIN-LITE ===")
    print(f"stale_tasks: {stale_count}")
    print(f"orphan_notes: {orphan_count}")
    print(f"broken_supersede_chains: {broken_chain_count}")
    print(f"dead_artifacts: {dead_artifact_count}")

    total_issues = stale_count + orphan_count + dead_artifact_count
    if broken_chain_count > 0 or total_issues >= 4:
        entropy = "HIGH"
    elif total_issues >= 1:
        entropy = "MEDIUM"
    else:
        entropy = "LOW"

    print(f"entropy: {entropy}")
    if entropy != "LOW":
        print("hint: run /harness:maintain to clean up")


if __name__ == "__main__":
    main()
