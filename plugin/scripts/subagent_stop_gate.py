#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso)

# SubagentStop hook — checks subagent left expected artifacts.
# Warn-only (exit 0 always).
# stdin: JSON | exit 0: allow | exit 2: block (unused)

data = read_hook_input()

agent_name = (
    json_field(data, "agent_name")
    or json_field(data, "agent")
    or os.environ.get("CLAUDE_AGENT_NAME", "unknown")
)

task_id = json_field(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

if not task_id:
    sys.exit(0)

target = os.path.join(TASK_DIR, task_id)
if not os.path.isdir(target):
    sys.exit(0)

if agent_name in ("developer", "harness:developer"):
    # Developer must leave TASK_STATE.yaml and HANDOFF.md.
    # Developers never write critic files — do not check for them.
    state_file = os.path.join(target, "TASK_STATE.yaml")
    handoff_file = os.path.join(target, "HANDOFF.md")

    if not os.path.exists(state_file):
        print(f"REMINDER: {task_id} — developer should update TASK_STATE.yaml")
    if not os.path.exists(handoff_file):
        print(f"REMINDER: {task_id} — developer should update HANDOFF.md with verification breadcrumbs")

    if os.path.exists(state_file):
        status = yaml_field("status", state_file) or ""
        if status not in ("implemented", "blocked_env"):
            print(f"REMINDER: {task_id} — developer finished but status is '{status}', expected 'implemented'")

elif agent_name in ("writer", "harness:writer"):
    # Writer must produce DOC_SYNC.md for repo-mutating tasks.
    is_mutating = True
    state_file = os.path.join(target, "TASK_STATE.yaml")
    if os.path.exists(state_file):
        content = open(state_file).read()
        if re.search(r'^mutates_repo: false', content, re.MULTILINE):
            is_mutating = False

    if is_mutating:
        doc_sync = os.path.join(target, "DOC_SYNC.md")
        if not os.path.exists(doc_sync):
            print(f"REMINDER: {task_id} — writer should produce DOC_SYNC.md for repo-mutating task (content may be 'none' if no docs changed)")

elif agent_name in ("critic-runtime", "harness:critic-runtime"):
    if not os.path.exists(os.path.join(target, "CRITIC__runtime.md")):
        print(f"REMINDER: {task_id} — runtime critic should write CRITIC__runtime.md")

elif agent_name in ("critic-plan", "harness:critic-plan"):
    if not os.path.exists(os.path.join(target, "CRITIC__plan.md")):
        print(f"REMINDER: {task_id} — plan critic should write CRITIC__plan.md")

elif agent_name in ("critic-document", "harness:critic-document"):
    if not os.path.exists(os.path.join(target, "CRITIC__document.md")):
        print(f"REMINDER: {task_id} — document critic should write CRITIC__document.md")

sys.exit(0)
