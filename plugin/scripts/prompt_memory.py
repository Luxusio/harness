#!/usr/bin/env python3
"""UserPromptSubmit hook: inject relevant context hints into the prompt."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_hook_input, json_field
from memory_selectors import select_relevant_notes, select_active_tasks, select_recent_verdicts

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

def gather_context(prompt):
    """Gather relevant context based on the prompt."""
    context_parts = []

    # 1. Check manifest for tooling status
    manifest_path = ".claude/harness/manifest.yaml"
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
        blocked = select_active_tasks(prompt)
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

    # 5. Relevant notes (top 2, freshness-aware)
    try:
        notes = select_relevant_notes(prompt)
        for note_path, score, first_line, freshness in notes:
            if freshness == "current":
                context_parts.append("Note: {}".format(first_line))
            elif freshness == "suspect":
                context_parts.append("Note [suspect]: {}".format(first_line))
            elif freshness == "stale":
                # Only include stale if no current/suspect notes available
                has_better = any(f in ("current", "suspect") for _, _, _, f in notes)
                if not has_better:
                    context_parts.append("Note [re-verify needed]: {}".format(first_line))
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
