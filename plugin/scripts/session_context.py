#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field, yaml_array, manifest_field,
                  is_browser_first_project, is_tooling_ready, is_profile_enabled,
                  TASK_DIR, MANIFEST, now_iso)


def get_browser_qa_status():
    """Check browser QA status from manifest sections and task states."""
    browser_qa = "disabled"

    # Check qa: section for browser_qa_supported
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

    # Check browser: section for enabled
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

    # Check for blocked_env tasks requiring browser
    if browser_qa == "enabled" and os.path.isdir(TASK_DIR):
        for entry in sorted(os.listdir(TASK_DIR)):
            task_path = os.path.join(TASK_DIR, entry)
            if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
                continue
            state_file = os.path.join(task_path, "TASK_STATE.yaml")
            if not os.path.isfile(state_file):
                continue
            status = yaml_field(state_file, "status")
            browser_required = yaml_field(state_file, "browser_required")
            if status == "blocked_env" and browser_required == "true":
                browser_qa = "blocked_env"
                break

    return browser_qa


def get_tooling_status():
    """Read tooling profile statuses from manifest."""
    symbol_lane = "disabled"
    structural_search = "disabled"
    observability = "disabled"

    if not os.path.isfile(MANIFEST):
        return symbol_lane, structural_search, observability

    try:
        with open(MANIFEST) as f:
            content = f.read()
    except (OSError, IOError):
        return symbol_lane, structural_search, observability

    import re

    # Symbol lane
    if re.search(r'^\s+symbol_lane_enabled:\s*true', content, re.MULTILINE):
        symbol_lane = "enabled"
    elif re.search(r'^\s+(lsp_ready|cclsp_ready):\s*true', content, re.MULTILINE):
        symbol_lane = "not-ready (tooling available)"

    # Structural search (ast-grep)
    if re.search(r'^\s+ast_grep_enabled:\s*true', content, re.MULTILINE):
        structural_search = "enabled"
    elif re.search(r'^\s+ast_grep_ready:\s*true', content, re.MULTILINE):
        structural_search = "not-ready (tooling available)"

    # Observability
    if re.search(r'^\s+observability_enabled:\s*true', content, re.MULTILINE):
        observability = "enabled"
    elif re.search(r'^\s+observability_ready:\s*true', content, re.MULTILINE):
        observability = "dormant (scaffold available)"

    return symbol_lane, structural_search, observability


def main():
    read_hook_input()

    if not os.path.isfile(MANIFEST):
        print("harness: plugin installed but repo not initialized.")
        print("Run /harness:setup to bootstrap.")
        sys.exit(0)

    print("harness: initialized (v4).")
    print("")

    # Project shape from manifest
    project_name = manifest_field("name")
    project_type = manifest_field("type")
    if project_name or project_type:
        print("=== PROJECT ===")
        if project_name:
            print(f"name: {project_name}")
        if project_type:
            print(f"type: {project_type}")
        print("")

    # Browser QA status
    browser_qa = get_browser_qa_status()
    print(f"=== BROWSER QA: {browser_qa} ===")
    print("")

    # Tooling status
    symbol_lane, structural_search, observability = get_tooling_status()
    print("=== TOOLING STATUS ===")
    print(f"Symbol lane: {symbol_lane}")
    print(f"Structural search: {structural_search}")
    print(f"Observability: {observability}")
    print("")

    # Open tasks
    print("=== OPEN TASKS ===")
    found_open = False
    found_blocked = False

    if os.path.isdir(TASK_DIR):
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
            qa_mode = yaml_field(state_file, "qa_mode") or "auto"

            if status in ("closed", "archived", "stale"):
                continue

            found_open = True

            if status == "blocked_env":
                print(f"- {task_id} [lane: {lane}, BLOCKED_ENV, qa_mode: {qa_mode}]")
                found_blocked = True
            else:
                plan_v = yaml_field(state_file, "plan_verdict") or "?"
                runtime_v = yaml_field(state_file, "runtime_verdict") or "?"
                mutates = yaml_field(state_file, "mutates_repo") or ""

                doc_sync_status = "n/a"
                if mutates in ("true", "unknown"):
                    if os.path.isfile(os.path.join(task_path, "DOC_SYNC.md")):
                        doc_sync_status = "present"
                    else:
                        doc_sync_status = "missing"

                print(f"- {task_id} [lane: {lane}, status: {status}, qa_mode: {qa_mode}, "
                      f"plan: {plan_v}, runtime: {runtime_v}, doc_sync: {doc_sync_status}]")

    if not found_open:
        print("(no open tasks)")

    if found_blocked:
        print("")
        print("WARNING: blocked_env tasks need environment fixes before completion.")

    print("")
    print("Follow CLAUDE.md instructions for request handling.")


if __name__ == "__main__":
    main()
