#!/usr/bin/env python3
"""UserPromptSubmit hook: inject relevant context hints into the prompt."""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    MANIFEST,
    TASK_DIR,
    exit_if_unmanaged_repo,
    hook_json_get,
    read_hook_input,
    yaml_array,
    yaml_field,
)
from memory_selectors import _get_registered_roots, select_relevant_notes
from failure_memory import (
    build_failure_case,
    find_similar_failure,
    find_similar_failures,
    format_similar_failure_hint,
    format_similar_failures_hint,
)


def is_casual(prompt):
    """Detect casual/greeting prompts that don't need context injection."""
    if not prompt or len(prompt) < 10:
        return True
    casual_patterns = [
        "hi",
        "hello",
        "hey",
        "thanks",
        "thank you",
        "ok",
        "okay",
        "yes",
        "no",
        "sure",
        "bye",
        "goodbye",
        "good morning",
        "good afternoon",
        "good evening",
        "what's up",
        "howdy",
    ]
    lower = prompt.lower().strip().rstrip("!?.,:;")
    return lower in casual_patterns


def extract_prompt(hook_input):
    """Extract the user prompt from hook input JSON."""
    for field in ["prompt", "message", "content", "input", "query"]:
        val = hook_json_get(hook_input, field)
        if val:
            return val
    return ""


def detect_lane_from_prompt(prompt):
    """Attempt to detect the most relevant lane from the prompt text.

    Language-agnostic: delegates actual classification to the LLM.
    This function only provides a best-effort hint for note retrieval
    scoring — it's OK to return None and let the LLM decide.
    """
    if not prompt:
        return None
    # Check for file path references that hint at lane
    if re.search(r"\btest[s_/]", prompt, re.IGNORECASE):
        return "verify"
    if re.search(r"\bdoc[s/]|README|CHANGELOG", prompt, re.IGNORECASE):
        return "docs-sync"
    # File extension hints
    if re.search(r"\.(test|spec)\.(ts|js|py|go|rs)\b", prompt):
        return "verify"
    if re.search(r"\.md\b", prompt):
        return "docs-sync"
    return None


def classify_prompt_intent(prompt):
    """Classify prompt as answer | investigate | mutating.

    Language-agnostic approach: uses structural signals instead of
    vocabulary lists. The LLM does the real classification — this is
    just for injecting the right context hint level.

    Returns: "answer" | "investigate" | "mutating"
    """
    if not prompt:
        return "answer"

    stripped = prompt.strip()

    # Very short = likely casual/answer
    if len(stripped) < 15:
        return "answer"

    # Questions (any language) → answer
    if stripped.endswith("?"):
        return "answer"

    # File path references with action context → mutating
    # e.g. "@PLAN.md 구현해", "fix src/foo.py", "plugin/scripts/bar.py 수정"
    has_file_ref = bool(re.search(r"[a-zA-Z0-9_/]+\.[a-zA-Z]{1,5}\b", stripped))
    has_code_block = "```" in stripped

    # References to existing task artifacts → likely continuing work
    if re.search(r"PLAN\.md|TASK_STATE|HANDOFF|CRITIC__", stripped):
        return "mutating"

    # Imperative + file reference = likely mutating
    if has_file_ref and len(stripped.split()) <= 10:
        return "mutating"

    # Code blocks in short prompts = mutating (giving code to apply)
    if has_code_block:
        return "mutating"

    # Short imperative sentences (no question mark, few words) = likely mutating
    words = stripped.split()
    if 2 <= len(words) <= 8 and not stripped.endswith("?"):
        # Short command-like prompt
        return "mutating"

    # Longer text without question marks and with file/code references
    if len(words) > 8 and has_file_ref and not stripped.endswith("?"):
        return "investigate"

    return "answer"


def _get_complaint_summary(task_dir):
    """Return short complaint summary if active task has open complaints."""
    try:
        import os
        import sys

        scripts_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)))
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        from feedback_capture import summarize_open_complaints

        summary = summarize_open_complaints(task_dir)
        return summary if summary else ""
    except Exception:
        return ""


def _is_complaint_like(prompt):
    """Return True only for explicit dissatisfaction signals.

    This stays intentionally conservative so normal short prompts do not trigger
    complaint reminders on every turn.
    """
    if not prompt:
        return False

    stripped = prompt.strip()
    if not stripped or stripped.endswith("?"):
        return False

    lower = stripped.lower()
    negation_outcome_en = [
        "still",
        "again",
        "didn't",
        "not working",
        "doesn't work",
        "not fixed",
        "still broken",
        "same issue",
        "still failing",
        "didn't fix",
        "didn't work",
        "not done",
        "wrong",
        "broken",
        "regressed",
    ]
    korean_signals = [
        "아직",
        "여전히",
        "안 됨",
        "안돼",
        "안됨",
        "안 돼",
        "못했",
        "틀렸",
    ]

    return any(signal in lower for signal in negation_outcome_en) or any(
        signal in stripped for signal in korean_signals
    )


def _get_active_task_dir():
    """Return the directory of the most recently active (non-closed) task, or None."""
    task_dir = TASK_DIR
    if not os.path.isdir(task_dir):
        return None
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
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if os.path.isfile(state_file):
        try:
            with open(state_file) as f:
                content = f.read()
            if re.search(r"^runtime_verdict:\s*(FAIL|BLOCKED_ENV)", content, re.MULTILINE):
                return True
        except OSError:
            pass
    if os.path.isfile(os.path.join(task_dir, "SESSION_HANDOFF.json")):
        return True
    return False


def _get_unplanned_hint(task_dir):
    """WS-5: Return a strong hint string if the active task is unplanned.

    Returns non-empty hint when:
      - task mutates_repo (not explicitly false)
      - plan_verdict != PASS
      - status is created or planned (pre-implementation)

    This is soft enforcement — the actual block happens at completion gate.
    """
    if not task_dir or not os.path.isdir(task_dir):
        return ""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return ""
    try:
        with open(state_file) as f:
            content = f.read()

        # Check mutates_repo
        is_non_mutating = bool(
            re.search(r"^mutates_repo:\s*false", content, re.MULTILINE)
        )
        if is_non_mutating:
            return ""

        # Check plan_verdict
        m_pv = re.search(r"^plan_verdict:\s*(\S+)", content, re.MULTILINE)
        plan_verdict = m_pv.group(1) if m_pv else "pending"
        if plan_verdict == "PASS":
            return ""  # Already approved

        # Check status
        m_st = re.search(r"^status:\s*(\S+)", content, re.MULTILINE)
        status = m_st.group(1) if m_st else "created"
        if status not in ("created", "planned"):
            return ""

        # Check for existing violations (even stronger signal)
        has_violation = bool(re.search(r"source_mutation_before_plan_pass", content))
        if has_violation:
            return (
                "plan violation: stop source edits and repair PLAN.md before continuing"
            )

        return "plan required: critic-plan PASS before source edits"
    except OSError:
        return ""


def _get_task_required_hint(prompt, active_task_dir):
    """Return hint if prompt requires a task but none exists.

    Checks prompt classification against active task state.
    """
    intent = classify_prompt_intent(prompt)

    if intent == "answer":
        return ""

    # If there's an active task, no need for task-required hint
    if active_task_dir:
        return ""

    if intent == "mutating":
        return "task required: create a task and run /harness:plan before repo changes"
    elif intent == "investigate":
        return "task required: create a task for structured investigation"
    return ""


def _get_pending_directives_hint(task_dir):
    """Return hint if there are pending directives needing promotion."""
    if not task_dir or not os.path.isdir(task_dir):
        return ""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    directive_state = yaml_field("directive_capture_state", state_file)
    if directive_state != "pending":
        return ""

    pending_file = os.path.join(task_dir, "DIRECTIVES_PENDING.yaml")
    if not os.path.isfile(pending_file):
        return ""

    # Count pending directives
    try:
        with open(pending_file) as f:
            content = f.read()
        pending_count = content.count("status: pending")
        if pending_count > 0:
            return f"directives pending: {pending_count}"
    except OSError:
        pass
    return ""


def _latest_task_verdict(task_dir):
    """Return a short verdict summary for the active task only."""
    if not task_dir or not os.path.isdir(task_dir):
        return ""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return ""

    runtime_v = yaml_field("runtime_verdict", state_file) or "pending"
    if runtime_v in ("FAIL", "BLOCKED_ENV"):
        return f"runtime={runtime_v}"

    document_v = yaml_field("document_verdict", state_file) or "pending"
    if document_v == "FAIL":
        return "document=FAIL"

    plan_v = yaml_field("plan_verdict", state_file) or "pending"
    if plan_v == "FAIL":
        return "plan=FAIL"

    return ""


def _compact_hint(text, max_chars=120):
    compact = re.sub(r"\s+", " ", str(text or "")).strip()
    if not compact:
        return ""
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _read_session_handoff(task_dir):
    path = os.path.join(task_dir, "SESSION_HANDOFF.json")
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _get_repair_focus_hint(task_dir, prompt):
    """Build an evidence-first repair hint for fix rounds.

    The goal is to surface the *current failing evidence* first, then the
    narrowest repair target from SESSION_HANDOFF / CHECKS, and finally a small
    amount of similar-failure history.
    """
    if not task_dir or not os.path.isdir(task_dir):
        return ""

    case = build_failure_case(task_dir, prompt=prompt) or {}
    handoff = _read_session_handoff(task_dir)
    if not case and not handoff:
        return ""

    focus_ids = [str(x).strip() for x in (handoff.get("open_check_ids") or []) if str(x).strip()]
    if not focus_ids:
        focus_ids = sorted(str(x).strip() for x in (case.get("check_ids") or []) if str(x).strip())[:2]

    paths = [str(x).strip() for x in (handoff.get("paths_in_focus") or case.get("path_examples") or []) if str(x).strip()]
    next_step = _compact_hint(handoff.get("next_step", ""), max_chars=88)
    current_excerpt = _compact_hint(case.get("excerpt", ""), max_chars=110)

    similar_matches = find_similar_failures(
        task_dir,
        tasks_dir=TASK_DIR,
        prompt=prompt,
        limit=2,
    )
    prior_hint = format_similar_failures_hint(similar_matches, max_chars=150)

    pieces = []
    if current_excerpt:
        pieces.append(f"current {current_excerpt}")
    if focus_ids:
        pieces.append("focus " + ", ".join(focus_ids[:2]))
    if paths:
        pieces.append("path " + _compact_hint(paths[0], max_chars=48))
    if next_step:
        pieces.append(f"next {next_step}")
    if prior_hint:
        pieces.append("prior " + prior_hint)

    if not pieces:
        return ""

    return _compact_hint("repair: " + " | ".join(pieces), max_chars=320)


def _prioritize_context_parts(parts):
    """Keep the prompt injection compact while favoring actionable evidence."""
    if not parts:
        return []

    def _priority(item):
        if item.startswith("task required:") or item.startswith("plan violation:"):
            return 0
        if item.startswith("plan required:") or item.startswith("directives pending:"):
            return 1
        if item.startswith("repair:"):
            return 2
        if item.startswith("Complaints:") or item.startswith("complaint hint:"):
            return 3
        if item.startswith("similar:"):
            return 4
        if item.startswith("runtime=") or item.startswith("document=") or item.startswith("plan="):
            return 5
        if item.startswith("Checks:"):
            return 6
        if item.startswith("active:"):
            return 7
        if item.startswith("note"):
            return 8
        return 7

    indexed = list(enumerate(parts))
    indexed.sort(key=lambda pair: (_priority(pair[1]), pair[0]))
    return [item for _, item in indexed[:4]]


def gather_context(prompt):
    """Gather a small amount of high-signal context for the next turn."""
    context_parts = []

    try:
        active_roots = _get_registered_roots("doc")
    except Exception:
        active_roots = ["common"]

    active_task_dir = None
    try:
        active_task_dir = _get_active_task_dir()
    except Exception:
        pass

    try:
        task_hint = _get_task_required_hint(prompt, active_task_dir)
        if task_hint:
            return [task_hint]
    except Exception:
        pass

    try:
        unplanned_hint = _get_unplanned_hint(active_task_dir)
        if unplanned_hint:
            context_parts.append(unplanned_hint)
    except Exception:
        pass

    if active_task_dir:
        try:
            state_file = os.path.join(active_task_dir, "TASK_STATE.yaml")
            task_id = yaml_field("task_id", state_file) or os.path.basename(
                active_task_dir
            )
            status = yaml_field("status", state_file) or "unknown"
            context_parts.append(f"active:{task_id}[{status}]")
        except Exception:
            pass

    try:
        directives_hint = _get_pending_directives_hint(active_task_dir)
        if directives_hint:
            context_parts.append(directives_hint)
    except Exception:
        pass

    try:
        if active_task_dir:
            complaint_summary = _get_complaint_summary(active_task_dir)
            if complaint_summary:
                context_parts.append(complaint_summary)
            elif _is_complaint_like(prompt):
                context_parts.append(
                    "complaint hint: capture dissatisfaction before proceeding"
                )
    except Exception:
        pass

    try:
        verdict = _latest_task_verdict(active_task_dir)
        if verdict:
            context_parts.append(verdict)
    except Exception:
        pass

    try:
        if active_task_dir and _is_fix_round(active_task_dir):
            from checks_focus import get_checks_summary_for_task

            checks_summary = get_checks_summary_for_task(active_task_dir)
            if checks_summary:
                context_parts.append(checks_summary)

            repair_hint = _get_repair_focus_hint(active_task_dir, prompt)
            if repair_hint:
                context_parts.append(repair_hint)
            else:
                similar_failure = find_similar_failure(active_task_dir, tasks_dir=TASK_DIR, prompt=prompt)
                similar_hint = format_similar_failure_hint(similar_failure)
                if similar_hint:
                    context_parts.append(similar_hint)
    except Exception:
        pass

    try:
        current_lane = detect_lane_from_prompt(prompt)
        query_context = {
            "active_roots": active_roots,
            "current_lane": current_lane,
        }
        notes = select_relevant_notes(prompt, query_context=query_context)
        if notes:
            _, _, first_line, freshness, root_name = notes[0]
            prefix = f"[{root_name}] " if root_name and root_name != "common" else ""
            if freshness == "current":
                context_parts.append(f"note:{prefix}{first_line}")
            elif freshness == "suspect":
                context_parts.append(f"note[suspect]:{prefix}{first_line}")
    except Exception:
        pass

    return _prioritize_context_parts(context_parts)


def main():
    exit_if_unmanaged_repo()

    hook_input = read_hook_input()
    prompt = extract_prompt(hook_input)

    # Skip casual prompts
    if is_casual(prompt):
        sys.exit(0)

    # Gather relevant context
    context_parts = gather_context(prompt)

    if not context_parts:
        sys.exit(0)

    # Format as additionalContext (keep it small, but allow slightly more
    # room for fix-round evidence-first repair hints).
    context = " | ".join(context_parts)
    max_chars = 520 if any(part.startswith("repair:") for part in context_parts) else 320
    if len(context) > max_chars:
        context = context[: max_chars - 3] + "..."

    # Output for hook — hookSpecificOutput schema
    output = {
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": context,
        }
    }
    print(json.dumps(output))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
