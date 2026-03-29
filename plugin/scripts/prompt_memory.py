#!/usr/bin/env python3
"""UserPromptSubmit hook: inject relevant context hints into the prompt."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_hook_input, json_field
from memory_selectors import select_relevant_notes, select_active_tasks, select_recent_verdicts, _get_registered_roots

def is_casual(prompt):
    """Detect casual/greeting prompts that don't need context injection."""
    if not prompt or len(prompt) < 10:
        return True
    casual_patterns = [
        "hi", "hello", "hey", "thanks", "thank you", "ok", "okay",
        "yes", "no", "sure", "bye", "goodbye", "good morning",
        "good afternoon", "good evening", "what's up", "howdy"
    ]
    lower = prompt.lower().strip().rstrip("!?.,:;")
    return lower in casual_patterns

def extract_prompt(hook_input):
    """Extract the user prompt from hook input JSON."""
    for field in ["prompt", "message", "content", "input", "query"]:
        val = json_field(field, hook_input)
        if val:
            return val
    return ""

def detect_lane_from_prompt(prompt):
    """Attempt to detect the most relevant lane from the prompt text."""
    if not prompt:
        return None
    lower = prompt.lower()
    if any(w in lower for w in ["build", "compile", "bundle", "install", "package"]):
        return "build"
    if any(w in lower for w in ["debug", "fix", "error", "exception", "crash", "broken"]):
        return "debug"
    if any(w in lower for w in ["test", "verify", "check", "assert", "spec"]):
        return "verify"
    if any(w in lower for w in ["refactor", "cleanup", "rename", "reorganize", "restructure"]):
        return "refactor"
    if any(w in lower for w in ["doc", "document", "readme", "changelog", "comment"]):
        return "docs-sync"
    return None

def _get_active_task_dir():
    """Return the directory of the most recently active (non-closed) task, or None."""
    task_dir = ".claude/harness/tasks"
    if not os.path.isdir(task_dir):
        return None
    import re
    candidates = []
    for entry in os.listdir(task_dir):
        if not entry.startswith("TASK__"):
            continue
        tp = os.path.join(task_dir, entry)
        state_file = os.path.join(tp, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        try:
            with open(state_file) as f:
                content = f.read()
            # Skip closed/archived/stale
            m = re.search(r"^status:\s*(\S+)", content, re.MULTILINE)
            status = m.group(1) if m else ""
            if status in ("closed", "archived", "stale"):
                continue
            # Read updated timestamp for recency
            m_upd = re.search(r"^updated:\s*(\S+)", content, re.MULTILINE)
            updated = m_upd.group(1) if m_upd else ""
            candidates.append((updated, tp))
        except (OSError, AttributeError):
            continue
    if not candidates:
        return None
    # Most recently updated task
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]

def _is_fix_round(task_dir):
    """Heuristic: True if this looks like a fix round (runtime FAIL or open checks).

    Used to decide whether to inject checks summary.
    A fix round is indicated by:
      - runtime_verdict: FAIL in TASK_STATE.yaml
      - SESSION_HANDOFF.json present (implies prior failure)
      - CHECKS.yaml has focus-status criteria
    """
    if not task_dir or not os.path.isdir(task_dir):
        return False
    import re
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if os.path.isfile(state_file):
        try:
            with open(state_file) as f:
                content = f.read()
            if re.search(r"^runtime_verdict:\s*FAIL", content, re.MULTILINE):
                return True
        except OSError:
            pass
    if os.path.isfile(os.path.join(task_dir, "SESSION_HANDOFF.json")):
        return True
    return False

def gather_context(prompt):
    """Gather relevant context based on the prompt."""
    context_parts = []

    # 1. Check manifest for tooling status and registered roots
    manifest_path = ".claude/harness/manifest.yaml"
    active_roots = []
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path) as f:
                manifest = f.read()
            tooling_hints = []
            if "symbol_lane_enabled: true" in manifest:
                tooling_hints.append("Symbol lane: active")
            if "ast_grep_enabled: true" in manifest:
                tooling_hints.append("Structural search: active")
            if "observability_enabled: true" in manifest:
                tooling_hints.append("Observability: active")
            if tooling_hints:
                context_parts.append("Tooling: " + ", ".join(tooling_hints))
        except Exception:
            pass

    # Determine active roots for query context
    try:
        active_roots = _get_registered_roots("doc")
    except Exception:
        active_roots = ["common"]

    # 2. Active tasks (top 1 via selector)
    try:
        active = select_active_tasks(prompt)
        if active:
            task_id, status, _ = active[0]
            context_parts.append("Active task: {} [{}]".format(task_id, status))
    except Exception:
        pass

    # 3. Recent critic verdicts (top 1 via selector)
    try:
        verdicts = select_recent_verdicts()
        if verdicts:
            task_id, verdict_type, verdict = verdicts[0]
            context_parts.append("Recent verdict: {}/{}: {}".format(task_id, verdict_type, verdict))
    except Exception:
        pass

    # 4. Check for blockers (tasks with blocked_env status)
    try:
        task_dir = ".claude/harness/tasks"
        if os.path.isdir(task_dir):
            for entry in os.listdir(task_dir):
                if not entry.startswith("TASK__"):
                    continue
                state_file = os.path.join(task_dir, entry, "TASK_STATE.yaml")
                if not os.path.isfile(state_file):
                    continue
                with open(state_file) as f:
                    content = f.read()
                if "status: blocked_env" in content:
                    context_parts.append("BLOCKED: {} needs env fix".format(entry))
                    break
    except Exception:
        pass

    # 5. CHECKS focus summary for fix rounds (WS-2)
    # Inject only when: there is an active task in a fix round, AND CHECKS.yaml has
    # focus-status criteria. Does not inject on casual/first-round prompts.
    # Budget: max 120 chars (SUMMARY_MAX_CHARS from checks_focus), counted within 600 total.
    try:
        active_task_dir = _get_active_task_dir()
        if active_task_dir and _is_fix_round(active_task_dir):
            from checks_focus import get_checks_summary_for_task
            checks_summary = get_checks_summary_for_task(active_task_dir)
            if checks_summary:
                context_parts.append(checks_summary)
    except Exception:
        pass

    # 6. Relevant notes (top 2, freshness-aware, multi-root)
    try:
        current_lane = detect_lane_from_prompt(prompt)
        query_context = {
            "active_roots": active_roots,
            "current_lane": current_lane,
        }
        notes = select_relevant_notes(prompt, query_context=query_context)
        for note_entry in notes:
            note_path, score, first_line, freshness, root_name = note_entry
            # Prefix with root name for non-common roots
            if root_name and root_name != "common":
                label_prefix = "[{}] ".format(root_name)
            else:
                label_prefix = ""

            if freshness == "current":
                context_parts.append("Note: {}{}".format(label_prefix, first_line))
            elif freshness == "suspect":
                context_parts.append("Note [suspect]: {}{}".format(label_prefix, first_line))
            elif freshness == "stale":
                # Only include stale if no current/suspect notes available
                has_better = any(f in ("current", "suspect") for _, _, _, f, _ in notes)
                if not has_better:
                    context_parts.append("Note [re-verify needed]: {}{}".format(label_prefix, first_line))
            # superseded notes are never included (already filtered in selector)
    except Exception:
        pass

    return context_parts

def main():
    hook_input = read_hook_input()
    prompt = extract_prompt(hook_input)

    # Skip casual prompts
    if is_casual(prompt):
        sys.exit(0)

    # Gather relevant context
    context_parts = gather_context(prompt)

    if not context_parts:
        sys.exit(0)

    # Format as additionalContext (max ~600 chars)
    context = " | ".join(context_parts)
    if len(context) > 600:
        context = context[:597] + "..."

    # Output for hook
    output = {"additionalContext": context}
    print(json.dumps(output))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
