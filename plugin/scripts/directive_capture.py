#!/usr/bin/env python3
"""UserPromptSubmit hook: inject directive-awareness reminder.

Instead of trying to detect directives via language-specific regex patterns,
this hook injects a standing context reminder that tells the LLM to watch
for user directives. The LLM is inherently multilingual and can detect
directives in any language — the hook just needs to remind it.

When an active task has pending directives, also injects a summary.

Non-blocking (exit 0 always). Outputs additionalContext hints.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    read_hook_input, hook_json_get, yaml_field, yaml_array,
    TASK_DIR, MANIFEST, now_iso, write_task_state_content,
)


def _is_short_or_casual(prompt):
    """Return True if prompt is too short or casual to contain a directive."""
    if not prompt or len(prompt.strip()) < 15:
        return True
    # Single-word or very short responses
    words = prompt.strip().split()
    if len(words) <= 2:
        return True
    return False


def _has_directive_structural_signals(prompt):
    """Check for language-agnostic structural signals that suggest a directive.

    These are universal patterns that don't depend on specific vocabulary:
    - Imperative tone (short, declarative, no question mark)
    - Correction reference (quotes, "you did X", past-tense critique)
    - Rule-like structure (if/when → then, universals)
    - Emphasis markers (caps, bold, exclamation)

    Returns a confidence score 0.0-1.0. Above 0.3 = worth mentioning.
    """
    if not prompt:
        return 0.0

    score = 0.0
    stripped = prompt.strip()

    # Questions are unlikely to be directives
    if stripped.endswith("?"):
        score -= 0.3

    # Exclamation suggests emphasis / imperative
    if "!" in stripped:
        score += 0.1

    # ALL CAPS words suggest emphasis
    caps_words = [w for w in stripped.split() if w.isupper() and len(w) > 2]
    if caps_words:
        score += 0.15

    # Markdown bold (**word**) suggests emphasis
    if "**" in stripped:
        score += 0.1

    # Short declarative statements (not questions) are more likely directives
    sentences = re.split(r'[.!?\n]', stripped)
    short_declaratives = [s for s in sentences if s.strip() and len(s.strip()) < 80 and not s.strip().endswith("?")]
    if short_declaratives and len(short_declaratives) >= len(sentences) * 0.5:
        score += 0.1

    # Multiple sentences suggest explanation/rule
    if len(sentences) >= 3:
        score += 0.05

    # Quoted text suggests referencing behavior
    if '"' in stripped or "'" in stripped or "`" in stripped:
        score += 0.05

    return min(max(score, 0.0), 1.0)


def stage_directive(task_dir, directive_text, directive_kind="process"):
    """Stage a directive into DIRECTIVES_PENDING.yaml.

    Called by the harness/writer when a directive is actually identified
    (by the LLM, not by regex). This is the staging API.

    Returns directive ID on success, None on failure.
    """
    if not task_dir or not directive_text:
        return None

    pending_file = os.path.join(task_dir, "DIRECTIVES_PENDING.yaml")
    ts = now_iso()
    dir_id = f"dir_{ts.replace('-','').replace(':','').replace('T','').replace('Z','')}"

    # Read existing
    existing = ""
    if os.path.isfile(pending_file):
        try:
            with open(pending_file, "r", encoding="utf-8") as f:
                existing = f.read()
        except OSError:
            pass

    # Dedup
    if directive_text in existing:
        return None

    entry = (
        f"  - id: {dir_id}\n"
        f"    kind: {directive_kind}\n"
        f"    text: \"{directive_text}\"\n"
        f"    captured_at: {ts}\n"
        f"    source_prompt_ref: user_prompt\n"
        f"    scope: repo\n"
        f"    supersedes: null\n"
        f"    status: pending\n"
    )

    try:
        if not existing or "directives:" not in existing:
            content = f"directives:\n{entry}"
        else:
            content = existing.rstrip("\n") + "\n" + entry

        with open(pending_file, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError:
        return None

    # Update TASK_STATE.yaml
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if os.path.isfile(state_file):
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                state_content = f.read()
            state_content = re.sub(
                r"^directive_capture_state:.*",
                "directive_capture_state: pending",
                state_content, flags=re.MULTILINE,
            )
            existing_ids = yaml_array("pending_directive_ids", state_file)
            all_ids = existing_ids + [dir_id]
            inline = ", ".join(f'"{i}"' for i in all_ids)
            state_content = re.sub(
                r"^pending_directive_ids:.*",
                f"pending_directive_ids: [{inline}]",
                state_content, flags=re.MULTILINE,
            )
            write_task_state_content(state_file, state_content, bump_revision=True)
        except OSError:
            pass

    return dir_id


def _get_active_task_dir():
    """Return the most recently active task directory, or None."""
    if not os.path.isdir(TASK_DIR):
        return None
    best = None
    best_updated = ""
    for entry in os.listdir(TASK_DIR):
        if not entry.startswith("TASK__"):
            continue
        tp = os.path.join(TASK_DIR, entry)
        state_file = os.path.join(tp, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue
        updated = yaml_field("updated", state_file) or ""
        if updated >= best_updated:
            best_updated = updated
            best = tp
    return best


def _get_pending_directive_count(task_dir):
    """Count pending directives in a task."""
    if not task_dir:
        return 0
    pending_file = os.path.join(task_dir, "DIRECTIVES_PENDING.yaml")
    if not os.path.isfile(pending_file):
        return 0
    try:
        with open(pending_file) as f:
            return f.read().count("status: pending")
    except OSError:
        return 0


def extract_prompt(hook_input):
    """Extract user prompt from hook input."""
    for field in ["prompt", "message", "content", "input", "query"]:
        val = hook_json_get(hook_input, field)
        if val:
            return val
    return ""


def main():
    if not os.path.isfile(MANIFEST):
        sys.exit(0)

    hook_input = read_hook_input()
    prompt = extract_prompt(hook_input)

    if _is_short_or_casual(prompt):
        sys.exit(0)

    hints = []

    # Check for pending directives on active task
    task_dir = _get_active_task_dir()
    pending_count = _get_pending_directive_count(task_dir)
    if pending_count > 0:
        task_id = os.path.basename(task_dir) if task_dir else "?"
        hints.append(
            f"DIRECTIVES PENDING: {pending_count} directive(s) in "
            f"{task_id}/DIRECTIVES_PENDING.yaml awaiting promotion"
        )

    # Structural signal check — if prompt looks directive-like, add reminder
    signal_score = _has_directive_structural_signals(prompt)
    if signal_score >= 0.3:
        hints.append(
            "DIRECTIVE CHECK: this message may contain a user rule/constraint/correction. "
            "If so, flag for writer to capture as REQ note."
        )

    if not hints:
        sys.exit(0)

    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": " | ".join(hints)
        }
    }
    print(json.dumps(output))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
