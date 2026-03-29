#!/usr/bin/env python3
"""UserPromptSubmit hook: inject relevant context hints into the prompt."""
import json
import os
import sys

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import read_hook_input, json_field

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

    # 2. Check active tasks
    task_dir = ".claude/harness/tasks"
    if os.path.isdir(task_dir):
        active_tasks = []
        try:
            for entry in os.listdir(task_dir):
                if not entry.startswith("TASK__"):
                    continue
                state_file = os.path.join(task_dir, entry, "TASK_STATE.yaml")
                if not os.path.isfile(state_file):
                    continue
                with open(state_file) as f:
                    content = f.read()
                # Skip closed/archived/stale
                if any("status: {}".format(s) in content for s in ["closed", "archived", "stale"]):
                    continue
                # Extract status and lane
                status = ""
                lane = ""
                for line in content.split("\n"):
                    if line.startswith("status:"):
                        status = line.split(":", 1)[1].strip()
                    elif line.startswith("lane:"):
                        lane = line.split(":", 1)[1].strip()
                if status:
                    active_tasks.append("{} [{}]".format(entry, status))
        except Exception:
            pass
        if active_tasks:
            context_parts.append("Active tasks: " + "; ".join(active_tasks[:3]))

    # 3. Check recent critic verdicts
    if os.path.isdir(task_dir):
        try:
            recent_verdict = None
            for entry in sorted(os.listdir(task_dir), reverse=True):
                if not entry.startswith("TASK__"):
                    continue
                critic_file = os.path.join(task_dir, entry, "CRITIC__runtime.md")
                if os.path.isfile(critic_file):
                    with open(critic_file) as f:
                        first_lines = f.read(200)
                    if "verdict: PASS" in first_lines:
                        recent_verdict = "{}: PASS".format(entry)
                    elif "verdict: FAIL" in first_lines:
                        recent_verdict = "{}: FAIL".format(entry)
                    break
            if recent_verdict:
                context_parts.append("Recent verdict: " + recent_verdict)
        except Exception:
            pass

    # 4. Check for blockers/handoffs
    if os.path.isdir(task_dir):
        try:
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

    # Format as additionalContext (max ~500 chars)
    context = " | ".join(context_parts)
    if len(context) > 500:
        context = context[:497] + "..."

    # Output for hook
    output = {"additionalContext": context}
    print(json.dumps(output))

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
