#!/usr/bin/env python3
import sys, os, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso)

# Stop hook — catches premature completion attempts.
# Uses Claude Code structured JSON API (same pattern as ralph-loop):
#   {"decision": "block", "reason": "<actionable message>"}  → blocks stop, feeds reason back to agent
#   {"decision": "allow"}                                     → allows stop, no conversation injection
# exit 0 always — decision field controls blocking, not exit code.

# Workflow status → next concrete action mapping.
_NEXT_STEP_MAP = {
    "created":     "Run `/harness:plan` to create PLAN.md",
    "planned":     "Invoke `harness:critic-plan` to evaluate the plan",
    "plan_passed": "Invoke `harness:developer` to implement",
    "implemented": "Invoke `harness:critic-runtime` to verify",
    "qa_passed":   "Invoke `harness:writer` to produce DOC_SYNC.md",
    "docs_synced": "Invoke `harness:critic-document` if doc changes; otherwise close",
}


def _next_step(status):
    """Return the next workflow action string for the given task status.

    Pure function — no file I/O, safe to call from tests.
    """
    return _NEXT_STEP_MAP.get(status, "Check TASK_STATE.yaml — resolve ambiguous status")


def _verdict_hints(state_file):
    """Return list of non-PASS verdict hint strings for display.

    Shows plan_verdict and runtime_verdict when they are not PASS,
    so the agent knows exactly what gate still needs to be passed.
    """
    if not state_file or not os.path.exists(state_file):
        return []
    hints = []
    pv = yaml_field("plan_verdict", state_file) or "pending"
    rv = yaml_field("runtime_verdict", state_file) or "pending"
    if pv != "PASS":
        hints.append(f"plan_verdict: {pv}")
    if rv != "PASS":
        hints.append(f"runtime_verdict: {rv}")
    return hints


def allow():
    print(json.dumps({"decision": "allow"}))
    sys.exit(0)

# No harness initialized — allow stop
if not os.path.exists("doc/harness/manifest.yaml"):
    allow()
if not os.path.isdir(TASK_DIR):
    allow()

open_tasks = []
blocked_tasks = []
pending_doc_sync = []

for entry in sorted(os.listdir(TASK_DIR)):
    if not entry.startswith("TASK__"):
        continue  # ignore non-harness directories (numeric Claude Code IDs, etc.)
    task_path = os.path.join(TASK_DIR, entry)
    if not os.path.isdir(task_path):
        continue

    state_file = os.path.join(task_path, "TASK_STATE.yaml")
    task_id = entry

    if not os.path.exists(state_file):
        continue

    status = yaml_field("status", state_file) or ""

    if status in ("closed", "archived", "stale"):
        continue
    elif status == "blocked_env":
        blocked_tasks.append(task_id)
    else:
        open_tasks.append((task_id, status or "unknown"))
        mutates = yaml_field("mutates_repo", state_file) or ""
        if mutates in ("true", "unknown"):
            if not os.path.exists(os.path.join(task_path, "DOC_SYNC.md")):
                pending_doc_sync.append(task_id)

if open_tasks:
    lines = [
        "HARNESS STOP GATE: open tasks remain. Complete or close them before stopping.",
        "",
        "Open tasks:",
    ]
    for task_id, status in open_tasks:
        lines.append(f"  - {task_id} [status: {status}]")
        lines.append(f"    → next: {_next_step(status)}")
        state_file_path = os.path.join(TASK_DIR, task_id, "TASK_STATE.yaml")
        for hint in _verdict_hints(state_file_path):
            lines.append(f"    → {hint}")
        if task_id in pending_doc_sync:
            lines.append(f"    ↳ also needs DOC_SYNC.md")

    if blocked_tasks:
        lines.append("")
        lines.append(f"Blocked-env tasks (need env fix before they can close):")
        for t in blocked_tasks:
            lines.append(f"  - {t}")

    lines += [
        "",
        "Actions you can take:",
        "  • Finish the task and close it (update TASK_STATE.yaml status: closed)",
        "  • Mark abandoned tasks stale: set status: stale in TASK_STATE.yaml",
        "  • Run /harness:maintain to auto-mark stale tasks",
    ]

    print(json.dumps({"decision": "block", "reason": "\n".join(lines)}))
    sys.exit(0)  # exit 0 — decision field controls blocking

if blocked_tasks:
    msg = f"Note: {len(blocked_tasks)} task(s) in blocked_env state (env fix required):\n"
    msg += "\n".join(f"  - {t}" for t in blocked_tasks)
    print(json.dumps({"decision": "allow", "systemMessage": msg}))
    sys.exit(0)

# Clean — no open or blocked tasks
print(json.dumps({"decision": "allow"}))
sys.exit(0)
