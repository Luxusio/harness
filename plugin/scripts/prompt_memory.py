#!/usr/bin/env python3
"""UserPromptSubmit hook — inject compact harness state on every user prompt.

Emits a short ``[harness-context]`` block on stdout when a harness task is
active, so agents don't burn a turn re-reading ``TASK_STATE.yaml`` /
``CHECKS.yaml`` to orient themselves in fix rounds.

Pending hygiene and suspect-note summaries are intentionally not injected here.
The post-close `hygiene_followup.py` scheduler turns hygiene output into a
separate task so prompt hooks do not dilute the active task's attention scope.

Output is silent outside harness-enabled repos. Total length is hard-capped at
400 chars for the compact prompt context; excess truncates with ``…``. The
``|| true`` wrapper in ``hooks.json`` (C-12 fail-safe) keeps the session healthy
on any crash.
"""
from __future__ import annotations

import os
import re
import sys
import json
import time
import hashlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (  # type: ignore
        read_hook_input,
        read_state,
        find_repo_root,
        is_harness_enabled_repo,
        TASK_DIR,
        _log_gate_error,
        resolve_active_task_dir,
        list_review_receipts,
        goal_command_objective,
        read_current_goal,
        next_goal_task,
        start_harness_goal,
        write_goal_payload_probe,
        append_conversation_entry,
        _goal_probe_runtime,
    )
except Exception:
    sys.exit(0)

try:
    from runbook_memory import render_prompt_block as _render_runbook_block  # type: ignore
except Exception:
    _render_runbook_block = None


MAX_BLOCK_CHARS = 400
MAX_OUTPUT_CHARS = 400
PREFIX = "[harness-context]"
DOC_GATE = (
    "[harness-doc-gate] Durable decisions require doc/ update before final; "
    "if no doc applies, keep the PLAN durable-doc rationale specific."
)
QA_GATE = (
    "[harness-qa] PENDING: ALL_TOOLS→spawn_agent(QA lenses)→await status-map "
    "completion; use available list_agents only if wait omitted identities "
    "→explicit VERDICT→task_verify; start≠PASS."
)
REVIEW_GATE = (
    "[harness-review] PENDING: spawn+await structural completions for required "
    "reviewers; available list_agents is fallback only; fresh PASS before QA; "
    "start≠PASS."
)
REVIEW_RECORDED_GATE = (
    "[harness-review/qa] RECORDED review PASS only; task_verify must validate "
    "lenses/freshness, then run required QA. Do not respawn solely from this hint."
)
GOAL_QUEUE_GATE = (
    "[harness-goal-queue] Child task close is not final; review gaps and "
    "start/queue the next child task unless the Goal is done, blocked, "
    "stopped, or budget-capped."
)
TASK_PACK_GATE = (
    "[harness-task-pack] Task close is not final; claim/start the next queued "
    "task-pack item unless the pack is done, blocked, stopped, or budget-capped."
)
RESTORE_INJECT_CAP = 1400
RESTORE_TOUCHED_CAP = 5
RESTORE_ARTIFACTS = ("REVIEW_RECEIPTS.jsonl", "SUBAGENT_RECEIPTS.jsonl", "BLOCKED.md")
FEEDBACK_FILE = "USER_FEEDBACK.jsonl"
FEEDBACK_PROMPT_CAP = 1200
FEEDBACK_TOUCHED_CAP = 5

_AC_CAP = 3
_AC_TERMINAL = {"passed", "deferred"}
_TITLE_MAX = 24
_REVIEWABLE_SUFFIXES = {
    ".c", ".cc", ".conf", ".config", ".cpp", ".cs", ".css", ".go", ".h",
    ".hpp", ".html", ".ini", ".java", ".js", ".json", ".jsx", ".kt",
    ".lock", ".php", ".pl", ".properties", ".py", ".rb", ".rs", ".sh",
    ".sql", ".swift", ".toml", ".ts", ".tsx", ".vue", ".xml", ".yaml",
    ".yml",
}


def _build_goal_block(repo_root: str, synced_goal: dict | None = None) -> str:
    """Inject the runtime Goal sync procedure.

    Hooks cannot call agent-only tools such as Codex get_goal, so the hook
    provides the exact MCP sequence the agent should run when native goal
    context is present.
    """
    try:
        goal = synced_goal or read_current_goal(repo_root)
    except Exception:
        goal = synced_goal or {}
    goal_id = str(goal.get("goal_id") or "")
    objective = _sanitize_prompt_text(str(goal.get("objective") or ""))
    tasks = goal.get("tasks") if isinstance(goal.get("tasks"), list) else []
    try:
        next_info = next_goal_task(repo_root) if goal else {"task": None}
    except Exception:
        next_info = {"task": None}
    task = next_info.get("task") if isinstance(next_info, dict) else None
    next_task = task.get("task_id") if isinstance(task, dict) else ""

    status = ""
    if goal_id:
        status = f" active={goal_id} tasks={len(tasks)}"
        if next_task:
            status += f" next={next_task}"
        if objective:
            status += f" objective={objective[:80]}"
    return (
        "[harness-goal] native goal? get_goal->goal_start; "
        "plain mutating request? task_start; no child: task_start->goal_add_task; "
        "next: goal_next_task."
        + status
    )


def _find_active_task_dir(repo_root: str) -> str:
    td = resolve_active_task_dir(repo_root)
    return td if td and os.path.isdir(td) else ""


_CHECKS_ID_RE = re.compile(r"^-\s+id:\s*(\S+)", re.MULTILINE)
_CHECKS_BLOCK_RE = re.compile(r"^-\s+id:\s*", re.MULTILINE)


def _open_acs(task_dir: str) -> "tuple[list[tuple[str, str]], int]":
    """Return ``(non_terminal_acs, reopen_total)``."""
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return [], 0
    try:
        text = open(checks_path, encoding="utf-8").read()
    except OSError:
        return [], 0
    blocks: list = []
    current: list = []
    for line in text.splitlines():
        if _CHECKS_BLOCK_RE.match(line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    out: list = []
    reopen_total = 0
    for block in blocks:
        m_id = _CHECKS_ID_RE.match(block)
        if not m_id:
            continue
        m_status = re.search(r"^\s+status:\s*(\S+)", block, re.MULTILINE)
        status = (m_status.group(1) if m_status else "open").strip()
        if status in _AC_TERMINAL:
            continue
        m_title = re.search(r'^\s+title:\s*"?(.*?)"?\s*$', block, re.MULTILINE)
        title = (m_title.group(1) if m_title else "").strip().strip('"').strip("'")
        if len(title) > _TITLE_MAX:
            title = title[: _TITLE_MAX - 1] + "…"
        m_reopen = re.search(r"^\s+reopen_count:\s*(\d+)", block, re.MULTILINE)
        if m_reopen:
            try:
                reopen_total += int(m_reopen.group(1))
            except ValueError:
                pass
        out.append((m_id.group(1), title))
        if len(out) >= _AC_CAP:
            break
    return out, reopen_total


def _truncate(block: str) -> str:
    if len(block) <= MAX_BLOCK_CHARS:
        return block
    return block[: MAX_BLOCK_CHARS - 2].rstrip() + " …"


def _truncate_output(output: str) -> str:
    if len(output) <= MAX_OUTPUT_CHARS:
        return output
    return output[: MAX_OUTPUT_CHARS - 2].rstrip() + " …"


def _build_block(task_dir: str) -> str:
    st = read_state(task_dir)
    if not st:
        return ""
    task_id = st.get("task_id") or os.path.basename(task_dir)
    status = st.get("status") or "unknown"
    verdict = (st.get("runtime_verdict") or "pending").upper()

    pieces: list = [PREFIX, f"task={task_id}", f"status={status}"]
    pieces.append(f"recorded_verdict={verdict}")

    acs, reopen_total = _open_acs(task_dir)
    if acs:
        ac_strs = [f"{ac_id}:{title}" if title else ac_id for ac_id, title in acs]
        summary = "open=" + ",".join(ac_strs)
        if reopen_total > 0:
            summary += f" ⚠reopened={reopen_total}"
        pieces.append(summary)

    block = " ".join(pieces)
    return _truncate(block)


def _build_qa_gate(task_dir: str) -> str:
    """Use the last persisted task verdict; task_verify remains authoritative."""
    try:
        st = read_state(task_dir)
        return "" if str(st.get("runtime_verdict") or "").upper() == "PASS" else QA_GATE
    except Exception:
        return QA_GATE


def _has_reviewable_touched_path(state: dict) -> bool:
    for raw in state.get("touched_paths") or []:
        rel = str(raw or "").replace("\\", "/").lower()
        if not rel or "__pycache__/" in rel or rel.endswith((".pyc", ".pyo", ".pyd")):
            continue
        suffix = os.path.splitext(rel)[1]
        if suffix == ".md" and rel.startswith(("plugin/", "plugin-codex/")):
            return True
        if suffix in _REVIEWABLE_SUFFIXES:
            if rel.startswith("doc/"):
                continue
            return True
    return False


def _build_review_gate(task_dir: str) -> str:
    """Keep source review advisory until the persisted runtime verdict passes.

    Without Git, prompt-time code cannot prove receipt freshness or discover a
    newly-required security lens. It therefore never treats review receipts as
    authoritative; task_verify/task_close make that decision.
    """
    try:
        st = read_state(task_dir)
        if not _has_reviewable_touched_path(st):
            return ""
        if str(st.get("runtime_verdict") or "").upper() == "PASS":
            return ""
        receipts = list_review_receipts(task_dir)
        latest = {}
        for index, item in enumerate(receipts):
            lens = str(item.get("lens") or "").lower()
            if lens.startswith("review-"):
                latest[lens] = (index, item)

        def has_valid_pass(pair: tuple[int, dict]) -> bool:
            completion_index, completion = pair
            return (
                str(completion.get("status") or "").lower() in {"completed", "done"}
                and str(completion.get("verdict") or "").upper() == "PASS"
                and any(
                    str(start.get("status") or "").lower() == "started"
                    and start.get("lens") == completion.get("lens")
                    and start.get("agent_id") == completion.get("agent_id")
                    and start.get("head_sha") == completion.get("head_sha")
                    and start.get("diff_fingerprint") == completion.get("diff_fingerprint")
                    and start.get("head_sha")
                    and start.get("diff_fingerprint")
                    for start in receipts[:completion_index]
                )
            )

        if "review-code" in latest and all(has_valid_pass(pair) for pair in latest.values()):
            return REVIEW_RECORDED_GATE
        return REVIEW_GATE
    except Exception:
        return REVIEW_GATE


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SYSTEM_REMINDER_RE = re.compile(r"</?system-reminder[^>]*>", re.IGNORECASE)


def _sanitize_path(path: str) -> str:
    """Sanitize a path for injection into system-reminder output.

    Removes control chars, newlines, and system-reminder tag fragments.
    """
    # Strip control chars and newlines
    cleaned = _CONTROL_CHAR_RE.sub("", path)
    # Escape system-reminder tags
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return cleaned.strip()


def _sanitize_prompt_text(text: str) -> str:
    cleaned = _CONTROL_CHAR_RE.sub(" ", str(text or ""))
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return " ".join(cleaned.split()).strip()


def _first_meaningful_line(path: str, cap: int = 180) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = _sanitize_prompt_text(line)
                if not line or line.startswith("#"):
                    continue
                return line[:cap]
    except OSError:
        return ""
    return ""


def _build_restore_block(task_dir: str) -> str:
    """Build a capped resume digest for the active task."""
    st = read_state(task_dir)
    if not st:
        return ""
    lines = ["<system-reminder>[harness-restore]"]
    touched = [
        _sanitize_path(str(p)) for p in (st.get("touched_paths") or [])[:RESTORE_TOUCHED_CAP]
        if str(p).strip()
    ]
    if touched:
        lines.append("recent touched: " + ", ".join(touched))

    artifact_lines: list[str] = []
    for name in RESTORE_ARTIFACTS:
        snippet = _first_meaningful_line(os.path.join(task_dir, name))
        if snippet:
            artifact_lines.append(f"{name}: {snippet}")
    if artifact_lines:
        lines.append("latest artifacts:")
        lines.extend(f"  - {line}" for line in artifact_lines[:3])

    if len(lines) == 1:
        return ""
    lines.append("</system-reminder>")
    block = "\n".join(lines)
    if len(block) > RESTORE_INJECT_CAP:
        block = block[: RESTORE_INJECT_CAP - 22].rstrip() + "\n...truncated\n</system-reminder>"
    return block


def _build_goal_queue_block(repo_root: str) -> str:
    path = os.path.join(repo_root, "doc", "harness", "goal-queue.json")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return ""
    status = str(state.get("status") or "").lower()
    if status in {"done", "blocked", "stopped"}:
        return ""
    slices = state.get("slices") if isinstance(state.get("slices"), list) else []
    if slices and all(str(item.get("status") or "") == "passed" for item in slices if isinstance(item, dict)):
        return ""
    return GOAL_QUEUE_GATE


def _build_task_pack_block(repo_root: str) -> str:
    path = os.path.join(repo_root, "doc", "harness", "task-packs", "current.json")
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:
        return ""
    status = str(state.get("status") or "").lower()
    if status in {"done", "blocked", "stopped"}:
        return ""
    tasks = state.get("tasks") if isinstance(state.get("tasks"), list) else []
    if tasks and all(
        str(item.get("status") or "") in {"closed", "blocked", "skipped"}
        for item in tasks
        if isinstance(item, dict)
    ):
        return ""
    return TASK_PACK_GATE


def _extract_user_prompt(data: dict) -> str:
    """Return the user prompt text from known Claude/Codex hook payload shapes."""
    for key in ("prompt", "user_prompt", "message", "text", "content"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return _sanitize_prompt_text(value)[:FEEDBACK_PROMPT_CAP]
    return ""


def _feedback_event_id(task_id: str, prompt: str) -> str:
    ts_ns = str(time.time_ns())
    digest = hashlib.sha1(f"{task_id}\0{ts_ns}\0{prompt}".encode("utf-8")).hexdigest()[:12]
    return f"ufe-{digest}"


def _append_feedback_event(task_dir: str, repo_root: str, data: dict) -> bool:
    """Append a context-rich, task-local user-feedback event.

    The hook only records evidence. It does not classify, promote, or mutate
    durable docs because doing that without phase context would be unsafe.
    """
    prompt = _extract_user_prompt(data)
    if not prompt:
        return False
    st = read_state(task_dir)
    if not st:
        return False
    task_id = st.get("task_id") or os.path.basename(task_dir)
    open_acs, _reopen_total = _open_acs(task_dir)
    touched = [
        _sanitize_path(str(p))
        for p in (st.get("touched_paths") or [])[:FEEDBACK_TOUCHED_CAP]
        if str(p).strip()
    ]
    event_id = _feedback_event_id(str(task_id), prompt)
    event = {
        "id": event_id,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "task_id": task_id,
        "status": st.get("status") or "unknown",
        "plan_session_state": st.get("plan_session_state") or "",
        "runtime_verdict": (st.get("runtime_verdict") or "pending").upper(),
        # Keep UserPromptSubmit read-only and fast. The canonical next action
        # remains available through task_context; recomputing its full receipt
        # and Git-freshness graph here can consume the hook timeout.
        "next_action": "Refresh task_context before the next phase transition.",
        "open_acs": [
            {"id": ac_id, "title": title}
            for ac_id, title in open_acs
        ],
        "touched_paths": touched,
        "prompt_excerpt": prompt,
        "source": "user_prompt_hook",
    }
    path = os.path.join(task_dir, FEEDBACK_FILE)
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
        append_conversation_entry(
            task_dir,
            role="user",
            text=prompt,
            source="user_prompt_hook",
            event_id=event_id,
        )
        return True
    except OSError:
        return False


def main() -> int:
    data = read_hook_input()
    repo_root = find_repo_root()
    if not is_harness_enabled_repo(repo_root):
        return 0
    write_goal_payload_probe(repo_root, data, source="UserPromptSubmit")
    synced_goal = None
    objective = goal_command_objective(data.get("prompt") if isinstance(data, dict) else "")
    if objective:
        try:
            synced_goal = start_harness_goal(
                repo_root,
                objective,
                source={
                    "runtime": _goal_probe_runtime(data),
                    "hook_event": "UserPromptSubmit",
                    "session_id": data.get("session_id") if isinstance(data, dict) else "",
                    "transcript_path": data.get("transcript_path") if isinstance(data, dict) else "",
                },
            )
        except Exception:
            synced_goal = None
    task_dir = _find_active_task_dir(repo_root)

    output_parts = [DOC_GATE]
    # Prompt submission is advisory and deliberately performs no Git queries.
    # Authoritative freshness/fingerprint checks remain in task_verify/task_close.
    if task_dir:
        _append_feedback_event(task_dir, repo_root, data)
        review_gate = _build_review_gate(task_dir)
        if review_gate:
            output_parts.append(review_gate)
        else:
            qa_gate = _build_qa_gate(task_dir)
            if qa_gate:
                output_parts.append(qa_gate)
        block = _build_block(task_dir)
        if block:
            output_parts.append(block)
        restore_block = _build_restore_block(task_dir)
        if restore_block:
            output_parts.append(restore_block)

    # Approved runbooks + pending candidates are repo-local execution memory.
    # They are advisory and capped inside runbook_memory.py.
    if _render_runbook_block is not None:
        runbook_block = _render_runbook_block(repo_root)
        if runbook_block:
            output_parts.append(runbook_block)

    goal_queue_block = _build_goal_queue_block(repo_root)
    if goal_queue_block:
        output_parts.append(goal_queue_block)

    task_pack_block = _build_task_pack_block(repo_root)
    if task_pack_block:
        output_parts.append(task_pack_block)

    if synced_goal or not task_dir:
        output_parts.append(_build_goal_block(repo_root, synced_goal))

    if output_parts:
        sys.stdout.write(_truncate_output("\n".join(output_parts)))
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "prompt_memory")
        except Exception:
            pass
        sys.exit(0)
