#!/usr/bin/env python3
"""Retrieve one similar past failure case from task history.

This module is intentionally conservative:
- it only returns a single top match
- it only scans task-local artifacts already produced by the harness
- it favors path/check overlap over vague lexical similarity

The goal is not to create a full memory system. It is to cheaply surface one
relevant prior failure trace during fix rounds.
"""

from __future__ import annotations

import json
import os
import re
import sys
from typing import Iterable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import TASK_DIR, yaml_array, yaml_field
from memory_selectors import extract_keywords


def _read_text(path: str, limit: int = 1600) -> str:
    if not path or not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return fh.read(limit)
    except OSError:
        return ""


def _read_json(path: str) -> dict:
    if not path or not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError, json.JSONDecodeError):
        return {}


def _normalize_path_tokens(paths: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for raw in paths or []:
        if not raw:
            continue
        lower = str(raw).lower().strip()
        tokens.add(lower)
        for part in re.split(r"[\\/._-]+", lower):
            if part and len(part) > 1:
                tokens.add(part)
    return tokens


def _parse_checks(checks_path: str) -> list[dict]:
    if not checks_path or not os.path.isfile(checks_path):
        return []

    checks = []
    current = {}
    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            for line in fh:
                m_id = re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
                if m_id:
                    if current.get("id"):
                        checks.append(current)
                    current = {"id": m_id.group(1).strip().strip('"').strip("'"), "status": "pending", "title": "", "reopen_count": 0}
                    continue
                m_status = re.match(r"^\s+status\s*:\s*(.+)", line)
                if m_status and current.get("id"):
                    current["status"] = m_status.group(1).strip().strip('"').strip("'").lower()
                    continue
                m_title = re.match(r"^\s+title\s*:\s*(.+)", line)
                if m_title and current.get("id"):
                    current["title"] = m_title.group(1).strip().strip('"').strip("'")
                    continue
                m_reopen = re.match(r"^\s+reopen_count\s*:\s*(\d+)", line)
                if m_reopen and current.get("id"):
                    current["reopen_count"] = int(m_reopen.group(1))
        if current.get("id"):
            checks.append(current)
    except (OSError, ValueError):
        return []
    return checks


def _complaint_texts(task_dir: str) -> list[str]:
    complaints_path = os.path.join(task_dir, "COMPLAINTS.yaml")
    text = _read_text(complaints_path)
    if not text:
        return []
    return [m.group(1).strip().strip('"').strip("'") for m in re.finditer(r"^\s+text\s*:\s*(.+)", text, re.MULTILINE)]


def _critic_excerpt(path: str) -> str:
    text = _read_text(path)
    if not text:
        return ""
    for field in ("summary", "unmet_acceptance", "evidence", "verdict_reason"):
        m = re.search(r"^" + field + r"\s*:\s*(.+)", text, re.MULTILINE)
        if m:
            value = m.group(1).strip()
            if value and value.lower() not in ("none", "n/a"):
                return value[:180]
    compact = re.sub(r"\s+", " ", text).strip()
    return compact[:180]


def _task_features(task_dir: str, prompt: str = "") -> dict:
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return {}

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    lane = yaml_field("lane", state_file) or "unknown"
    runtime_verdict = (yaml_field("runtime_verdict", state_file) or "pending").upper()
    document_verdict = (yaml_field("document_verdict", state_file) or "pending").upper()
    fail_count_raw = yaml_field("runtime_verdict_fail_count", state_file) or "0"
    try:
        fail_count = int(fail_count_raw)
    except ValueError:
        fail_count = 0

    session_handoff = _read_json(os.path.join(task_dir, "SESSION_HANDOFF.json"))
    checks = _parse_checks(os.path.join(task_dir, "CHECKS.yaml"))
    focus_checks = [c["id"] for c in checks if c.get("status") in ("failed", "blocked", "implemented_candidate")]
    reopened_checks = [c["id"] for c in checks if int(c.get("reopen_count") or 0) >= 1]
    focus_titles = [c.get("title", "") for c in checks if c.get("id") in focus_checks or int(c.get("reopen_count") or 0) >= 1]

    path_list = []
    for field in ("verification_targets", "touched_paths"):
        path_list.extend(yaml_array(field, state_file))
    if isinstance(session_handoff.get("paths_in_focus"), list):
        path_list.extend(str(x) for x in session_handoff.get("paths_in_focus") if x)

    request_text = _read_text(os.path.join(task_dir, "REQUEST.md"), limit=1200)
    handoff_text = _read_text(os.path.join(task_dir, "HANDOFF.md"), limit=1200)
    runtime_excerpt = _critic_excerpt(os.path.join(task_dir, "CRITIC__runtime.md"))
    document_excerpt = _critic_excerpt(os.path.join(task_dir, "CRITIC__document.md"))
    complaint_bits = _complaint_texts(task_dir)
    next_step = session_handoff.get("next_step") if isinstance(session_handoff.get("next_step"), str) else ""
    open_check_ids = session_handoff.get("open_check_ids") if isinstance(session_handoff.get("open_check_ids"), list) else []

    merged_text = "\n".join(
        x for x in [prompt, request_text, handoff_text, runtime_excerpt, document_excerpt, next_step] + focus_titles + complaint_bits if x
    )
    keywords = set(extract_keywords(merged_text))
    path_tokens = _normalize_path_tokens(path_list)
    check_ids = {str(x) for x in focus_checks + reopened_checks + [str(x) for x in open_check_ids] if x}

    failure_signals = 0
    if runtime_verdict in ("FAIL", "BLOCKED_ENV"):
        failure_signals += 2
    if document_verdict == "FAIL":
        failure_signals += 2
    if fail_count > 0:
        failure_signals += min(fail_count, 2)
    if session_handoff:
        failure_signals += 1
    if reopened_checks:
        failure_signals += 1
    if complaint_bits:
        failure_signals += 1

    excerpt = runtime_excerpt or document_excerpt or next_step or (focus_titles[0] if focus_titles else "") or "previous failure trace"

    return {
        "task_id": task_id,
        "lane": lane,
        "keywords": keywords,
        "path_tokens": path_tokens,
        "check_ids": check_ids,
        "failure_signals": failure_signals,
        "excerpt": excerpt[:180],
        "artifact": _best_artifact(task_dir),
    }


def _best_artifact(task_dir: str) -> str:
    for name in (
        "SESSION_HANDOFF.json",
        "CRITIC__runtime.md",
        "CRITIC__document.md",
        "HANDOFF.md",
        "CHECKS.yaml",
    ):
        if os.path.isfile(os.path.join(task_dir, name)):
            return name
    return "TASK_STATE.yaml"


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(len(a | b), 1))


def find_similar_failure(current_task_dir: str, tasks_dir: str | None = None, prompt: str = "") -> dict | None:
    if not current_task_dir or not os.path.isdir(current_task_dir):
        return None
    if tasks_dir is None:
        tasks_dir = TASK_DIR
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return None

    current = _task_features(current_task_dir, prompt=prompt)
    if not current:
        return None

    best = None
    for entry in sorted(os.listdir(tasks_dir)):
        if not entry.startswith("TASK__"):
            continue
        candidate_dir = os.path.join(tasks_dir, entry)
        if not os.path.isdir(candidate_dir):
            continue
        if os.path.normpath(candidate_dir) == os.path.normpath(current_task_dir):
            continue

        candidate = _task_features(candidate_dir)
        if not candidate or candidate.get("failure_signals", 0) <= 0:
            continue

        path_score = _overlap(current["path_tokens"], candidate["path_tokens"])
        keyword_score = _overlap(current["keywords"], candidate["keywords"])
        check_score = _overlap(current["check_ids"], candidate["check_ids"])
        lane_score = 1.0 if current["lane"] and current["lane"] == candidate["lane"] else 0.0
        confidence = min(candidate.get("failure_signals", 0) / 4.0, 1.0)

        score = (
            path_score * 0.45
            + keyword_score * 0.30
            + check_score * 0.15
            + lane_score * 0.05
            + confidence * 0.05
        )

        if score < 0.18:
            continue

        result = {
            "task_id": candidate["task_id"],
            "score": round(score, 4),
            "artifact": candidate["artifact"],
            "artifact_path": os.path.join(tasks_dir, candidate["task_id"], candidate["artifact"]),
            "excerpt": candidate["excerpt"],
            "matching_paths": sorted(current["path_tokens"] & candidate["path_tokens"])[:3],
            "matching_check_ids": sorted(current["check_ids"] & candidate["check_ids"])[:3],
        }

        if best is None or result["score"] > best["score"]:
            best = result

    return best


def format_similar_failure_hint(match: dict | None, max_chars: int = 130) -> str:
    if not match:
        return ""

    pieces = [f"similar:{match['task_id']}"]
    if match.get("matching_check_ids"):
        pieces.append("checks " + ", ".join(match["matching_check_ids"]))
    elif match.get("matching_paths"):
        pieces.append("paths " + ", ".join(match["matching_paths"]))
    pieces.append((match.get("excerpt") or "prior failure trace").strip())

    text = " — ".join(pieces)
    compact = re.sub(r"\s+", " ", text).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
