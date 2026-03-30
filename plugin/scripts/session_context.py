#!/usr/bin/env python3
import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, yaml_field, yaml_array, manifest_field,
                  manifest_section_field, is_browser_first_project, is_tooling_ready,
                  is_profile_enabled, get_browser_qa_status,
                  TASK_DIR, MANIFEST, now_iso)


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

    print("harness: initialized (v2).")
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

    # Browser QA status (uses shared helper from _lib)
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

    # Team readiness status
    teams_provider = manifest_section_field("teams", "provider") or ""
    if teams_provider:
        native_ready = manifest_section_field("teams", "native_ready") or "false"
        omc_ready = manifest_section_field("teams", "omc_ready") or "false"
        print("=== TEAM READINESS ===")
        print(f"Provider: {teams_provider}")
        print(f"Native ready: {native_ready}")
        print(f"OMC ready: {omc_ready}")
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
            status = yaml_field("status", state_file) or "unknown"
            lane = yaml_field("lane", state_file) or "unknown"
            qa_mode = yaml_field("qa_mode", state_file) or "auto"

            if status in ("closed", "archived", "stale"):
                continue

            found_open = True

            if status == "blocked_env":
                print(f"- {task_id} [lane: {lane}, BLOCKED_ENV, qa_mode: {qa_mode}]")
                found_blocked = True
            else:
                plan_v = yaml_field("plan_verdict", state_file) or "?"
                runtime_v = yaml_field("runtime_verdict", state_file) or "?"
                mutates = yaml_field("mutates_repo", state_file) or ""

                doc_sync_status = "n/a"
                if mutates in ("true", "unknown"):
                    if os.path.isfile(os.path.join(task_path, "DOC_SYNC.md")):
                        doc_sync_status = "present"
                    else:
                        doc_sync_status = "missing"

                print(f"- {task_id} [lane: {lane}, status: {status}, qa_mode: {qa_mode}, "
                      f"plan: {plan_v}, runtime: {runtime_v}, doc_sync: {doc_sync_status}]")
                orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
                if orch_mode != "solo":
                    team_status_val = yaml_field("team_status", state_file) or "n/a"
                    print(f"  orchestration: {orch_mode}, team_status: {team_status_val}")

    if not found_open:
        print("(no open tasks)")

    if found_blocked:
        print("")
        print("WARNING: blocked_env tasks need environment fixes before completion.")

    print("")
    print("Follow CLAUDE.md instructions for request handling.")


if __name__ == "__main__":
    main()
