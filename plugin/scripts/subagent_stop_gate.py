#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, hook_json_get, json_field, yaml_field, yaml_array,
                  TASK_DIR, now_iso, increment_agent_run)

# SubagentStop hook — records subagent provenance and checks expected artifacts.
# Non-blocking (exit 0 always).
# stdin: JSON | exit 0: allow | exit 2: block (unused)

# Recognized canonical agent names (without harness: prefix)
_KNOWN_AGENTS = frozenset([
    "developer", "writer",
    "critic-plan", "critic-runtime", "critic-document",
])


def _normalize_agent(raw_name):
    """Strip 'harness:' prefix to get canonical agent name."""
    if raw_name.startswith("harness:"):
        return raw_name[len("harness:"):]
    return raw_name


def record_agent_run(task_dir, agent_name):
    """Record that agent_name ran on task_dir. Returns True on success."""
    return increment_agent_run(task_dir, agent_name)


def check_agent_artifacts(task_dir, raw_agent_name):
    """Return list of reminder strings for missing expected artifacts.

    Checks based on the raw (possibly prefixed) agent name so both
    'developer' and 'harness:developer' are handled.
    """
    reminders = []
    task_id = os.path.basename(task_dir.rstrip("/"))

    if raw_agent_name in ("developer", "harness:developer"):
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        handoff_file = os.path.join(task_dir, "HANDOFF.md")

        if not os.path.exists(state_file):
            reminders.append(
                f"REMINDER: {task_id} — developer should update TASK_STATE.yaml"
            )
        if not os.path.exists(handoff_file):
            reminders.append(
                f"REMINDER: {task_id} — developer should update HANDOFF.md"
                " with verification breadcrumbs"
            )
        if os.path.exists(state_file):
            status = yaml_field("status", state_file) or ""
            if status not in ("implemented", "blocked_env"):
                reminders.append(
                    f"REMINDER: {task_id} — developer finished but status is"
                    f" '{status}', expected 'implemented'"
                )

    elif raw_agent_name in ("writer", "harness:writer"):
        is_mutating = True
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if re.search(r"^mutates_repo:\s*false", content, re.MULTILINE):
                    is_mutating = False
            except OSError:
                pass
        if is_mutating:
            doc_sync = os.path.join(task_dir, "DOC_SYNC.md")
            if not os.path.exists(doc_sync):
                reminders.append(
                    f"REMINDER: {task_id} — writer should produce DOC_SYNC.md"
                    " for repo-mutating task (content may be 'none' if no docs changed)"
                )

    elif raw_agent_name in ("critic-runtime", "harness:critic-runtime"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__runtime.md")):
            reminders.append(
                f"REMINDER: {task_id} — runtime critic should write CRITIC__runtime.md"
            )

    elif raw_agent_name in ("critic-plan", "harness:critic-plan"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__plan.md")):
            reminders.append(
                f"REMINDER: {task_id} — plan critic should write CRITIC__plan.md"
            )

    elif raw_agent_name in ("critic-document", "harness:critic-document"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__document.md")):
            reminders.append(
                f"REMINDER: {task_id} — document critic should write CRITIC__document.md"
            )

    return reminders


def main():
    data = read_hook_input()

    # WS-1 fix: hook_json_get(data, field) instead of json_field(data, field)
    raw_agent = (
        hook_json_get(data, "agent_name")
        or hook_json_get(data, "agent")
        or os.environ.get("CLAUDE_AGENT_NAME", "unknown")
    )
    task_id = hook_json_get(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

    if not task_id:
        sys.exit(0)

    target = os.path.join(TASK_DIR, task_id)
    if not os.path.isdir(target):
        sys.exit(0)

    canonical = _normalize_agent(raw_agent)

    # WS-3: Record provenance in TASK_STATE.yaml
    if canonical in _KNOWN_AGENTS:
        if record_agent_run(target, canonical):
            print(f"PROVENANCE: {task_id} — recorded {canonical} run")

    # Check artifact reminders (soft enforcement)
    for reminder in check_agent_artifacts(target, raw_agent):
        print(reminder)

    sys.exit(0)


if __name__ == "__main__":
    main()
