#!/usr/bin/env python3
"""Failure memory utilities for task-local recovery.

This module stays deliberately conservative:
- it only scans task-local artifacts already produced by the harness
- it favors path/check overlap over vague lexical similarity
- it now supports a small *case index* and top-k retrieval for fix rounds

The intent is still not a full memory system. The goal is to make repeated
failures more inspectable without adding heavy runtime context or external
infrastructure.
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
from task_index import load_failure_index, upsert_failure_case

CASE_FILENAME = "FAILURE_CASE.json"
DEFAULT_LIMIT = 3
DEFAULT_MIN_SCORE = 0.18


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
                    current = {
                        "id": m_id.group(1).strip().strip('"').strip("'"),
                        "status": "pending",
                        "title": "",
                        "reopen_count": 0,
                    }
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


def _serialize_case(case: dict) -> dict:
    """Convert internal set-heavy case structure to JSON-serializable form."""
    return {
        "task_id": case.get("task_id", ""),
        "lane": case.get("lane", "unknown"),
        "artifact": case.get("artifact", "TASK_STATE.yaml"),
        "excerpt": case.get("excerpt", ""),
        "failure_signals": int(case.get("failure_signals", 0) or 0),
        "runtime_verdict": case.get("runtime_verdict", "PENDING"),
        "document_verdict": case.get("document_verdict", "PENDING"),
        "runtime_fail_count": int(case.get("runtime_fail_count", 0) or 0),
        "reopened_count": int(case.get("reopened_count", 0) or 0),
        "source_updated": case.get("source_updated", ""),
        "check_ids": sorted(str(x) for x in (case.get("check_ids") or set())),
        "path_tokens": sorted(str(x) for x in (case.get("path_tokens") or set())),
        "path_examples": [str(x) for x in (case.get("path_examples") or [])[:4]],
        "keywords": sorted(str(x) for x in (case.get("keywords") or set()))[:30],
    }


def _deserialize_case(data: dict) -> dict:
    if not isinstance(data, dict):
        return {}
    task_id = str(data.get("task_id") or "").strip()
    if not task_id:
        return {}
    return {
        "task_id": task_id,
        "lane": str(data.get("lane") or "unknown"),
        "artifact": str(data.get("artifact") or "TASK_STATE.yaml"),
        "excerpt": str(data.get("excerpt") or "")[:180],
        "failure_signals": int(data.get("failure_signals") or 0),
        "runtime_verdict": str(data.get("runtime_verdict") or "PENDING").upper(),
        "document_verdict": str(data.get("document_verdict") or "PENDING").upper(),
        "runtime_fail_count": int(data.get("runtime_fail_count") or 0),
        "reopened_count": int(data.get("reopened_count") or 0),
        "source_updated": str(data.get("source_updated") or ""),
        "check_ids": {str(x) for x in (data.get("check_ids") or []) if str(x).strip()},
        "path_tokens": {str(x).lower() for x in (data.get("path_tokens") or []) if str(x).strip()},
        "path_examples": [str(x) for x in (data.get("path_examples") or [])[:4] if str(x).strip()],
        "keywords": {str(x).lower() for x in (data.get("keywords") or []) if str(x).strip()},
    }


def _load_case_snapshot(task_dir: str) -> dict:
    snapshot_path = os.path.join(task_dir, CASE_FILENAME)
    if not os.path.isfile(snapshot_path):
        return {}
    snap = _deserialize_case(_read_json(snapshot_path))
    if not snap:
        return {}

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    state_updated = str(yaml_field("updated", state_file) or "")
    snapshot_updated = str(snap.get("source_updated") or "")
    if state_updated and snapshot_updated and state_updated != snapshot_updated:
        return {}
    return snap


def _task_features(task_dir: str, prompt: str = "") -> dict:
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return {}

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    lane = yaml_field("lane", state_file) or "unknown"
    runtime_verdict = (yaml_field("runtime_verdict", state_file) or "pending").upper()
    document_verdict = (yaml_field("document_verdict", state_file) or "pending").upper()
    updated = yaml_field("updated", state_file) or ""
    fail_count_raw = yaml_field("runtime_verdict_fail_count", state_file) or "0"
    try:
        fail_count = int(fail_count_raw)
    except ValueError:
        fail_count = 0

    session_handoff = _read_json(os.path.join(task_dir, "SESSION_HANDOFF.json"))
    checks = _parse_checks(os.path.join(task_dir, "CHECKS.yaml"))
    focus_checks = [c["id"] for c in checks if c.get("status") in ("failed", "blocked", "implemented_candidate")]
    reopened_checks = [c["id"] for c in checks if int(c.get("reopen_count") or 0) >= 1]
    focus_titles = [
        c.get("title", "")
        for c in checks
        if c.get("id") in focus_checks or int(c.get("reopen_count") or 0) >= 1
    ]

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
        x
        for x in [prompt, request_text, handoff_text, runtime_excerpt, document_excerpt, next_step] + focus_titles + complaint_bits
        if x
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
        "path_examples": [str(x) for x in path_list[:4] if str(x).strip()],
        "check_ids": check_ids,
        "failure_signals": failure_signals,
        "runtime_verdict": runtime_verdict,
        "document_verdict": document_verdict,
        "runtime_fail_count": fail_count,
        "reopened_count": len(reopened_checks),
        "source_updated": str(updated),
        "excerpt": excerpt[:180],
        "artifact": _best_artifact(task_dir),
    }


def build_failure_case(task_dir: str, prompt: str = "") -> dict:
    """Build a structured failure case for one task directory."""
    return _task_features(task_dir, prompt=prompt)


def write_failure_case_snapshot(task_dir: str, prompt: str = "") -> str:
    """Write FAILURE_CASE.json for one task when a case can be derived."""
    if not task_dir or not os.path.isdir(task_dir):
        return ""
    case = build_failure_case(task_dir, prompt=prompt)
    if not case:
        return ""
    path = os.path.join(task_dir, CASE_FILENAME)
    try:
        with open(path, "w", encoding="utf-8") as fh:
            serialized = _serialize_case(case)
            json.dump(serialized, fh, indent=2, sort_keys=True)
            fh.write("\n")
    except OSError:
        return ""
    try:
        upsert_failure_case(serialized, tasks_dir=os.path.dirname(task_dir))
    except Exception:
        pass
    return path


def _candidate_features(task_dir: str) -> dict:
    cached = _load_case_snapshot(task_dir)
    if cached:
        return cached
    return _task_features(task_dir)


def _indexed_candidate_features(tasks_dir: str | None) -> list[dict]:
    cases = []
    for raw_case in load_failure_index(tasks_dir):
        case = _deserialize_case(raw_case)
        if case:
            cases.append(case)
    return cases


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / float(max(len(a | b), 1))


def _score_candidate(current: dict, candidate: dict, tasks_dir: str) -> dict | None:
    if not candidate or candidate.get("failure_signals", 0) <= 0:
        return None

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

    return {
        "task_id": candidate["task_id"],
        "score": round(score, 4),
        "artifact": candidate["artifact"],
        "artifact_path": os.path.join(tasks_dir, candidate["task_id"], candidate["artifact"]),
        "excerpt": candidate["excerpt"],
        "lane": candidate.get("lane", "unknown"),
        "failure_signals": int(candidate.get("failure_signals", 0) or 0),
        "runtime_verdict": candidate.get("runtime_verdict", "PENDING"),
        "document_verdict": candidate.get("document_verdict", "PENDING"),
        "runtime_fail_count": int(candidate.get("runtime_fail_count", 0) or 0),
        "reopened_count": int(candidate.get("reopened_count", 0) or 0),
        "matching_paths": sorted(current["path_tokens"] & candidate["path_tokens"])[:4],
        "matching_check_ids": sorted(current["check_ids"] & candidate["check_ids"])[:4],
        "matching_keywords": sorted(current["keywords"] & candidate["keywords"])[:4],
        "path_examples": [str(x) for x in (candidate.get("path_examples") or [])[:4]],
    }


def find_similar_failures(
    current_task_dir: str,
    tasks_dir: str | None = None,
    prompt: str = "",
    limit: int = DEFAULT_LIMIT,
    min_score: float = DEFAULT_MIN_SCORE,
) -> list[dict]:
    if not current_task_dir or not os.path.isdir(current_task_dir):
        return []
    if tasks_dir is None:
        tasks_dir = TASK_DIR
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return []

    current = _task_features(current_task_dir, prompt=prompt)
    if not current:
        return []

    results = []
    indexed_cases = _indexed_candidate_features(tasks_dir)
    if indexed_cases:
        candidate_cases = [
            case
            for case in indexed_cases
            if str(case.get("task_id") or "") != str(current.get("task_id") or "")
        ]
    else:
        candidate_cases = []
        for entry in sorted(os.listdir(tasks_dir)):
            if not entry.startswith("TASK__"):
                continue
            candidate_dir = os.path.join(tasks_dir, entry)
            if not os.path.isdir(candidate_dir):
                continue
            if os.path.normpath(candidate_dir) == os.path.normpath(current_task_dir):
                continue
            candidate_cases.append(_candidate_features(candidate_dir))

    for candidate in candidate_cases:
        scored = _score_candidate(current, candidate, tasks_dir)
        if not scored or scored["score"] < min_score:
            continue
        results.append(scored)

    results.sort(
        key=lambda item: (
            float(item.get("score", 0.0)),
            int(item.get("failure_signals", 0)),
            int(item.get("reopened_count", 0)),
            int(item.get("runtime_fail_count", 0)),
            item.get("task_id", ""),
        ),
        reverse=True,
    )
    return results[: max(int(limit or 0), 0)]


def find_similar_failure(current_task_dir: str, tasks_dir: str | None = None, prompt: str = "") -> dict | None:
    matches = find_similar_failures(current_task_dir, tasks_dir=tasks_dir, prompt=prompt, limit=1)
    return matches[0] if matches else None


def list_failure_cases(
    tasks_dir: str | None = None,
    *,
    limit: int = 20,
    lane: str = "",
    min_failure_signals: int = 1,
) -> list[dict]:
    """List failure cases across task history for CLI inspection."""
    if tasks_dir is None:
        tasks_dir = TASK_DIR
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return []

    selected_lane = (lane or "").strip().lower()
    cases = []
    indexed_cases = _indexed_candidate_features(tasks_dir)
    if indexed_cases:
        candidate_cases = indexed_cases
    else:
        candidate_cases = []
        for entry in sorted(os.listdir(tasks_dir)):
            if not entry.startswith("TASK__"):
                continue
            task_dir = os.path.join(tasks_dir, entry)
            if not os.path.isdir(task_dir):
                continue
            candidate_cases.append(_candidate_features(task_dir))

    for case in candidate_cases:
        if not case or int(case.get("failure_signals", 0) or 0) < int(min_failure_signals):
            continue
        if selected_lane and str(case.get("lane") or "").lower() != selected_lane:
            continue
        case_view = {
            "task_id": case.get("task_id"),
            "lane": case.get("lane", "unknown"),
            "failure_signals": int(case.get("failure_signals", 0) or 0),
            "runtime_verdict": case.get("runtime_verdict", "PENDING"),
            "document_verdict": case.get("document_verdict", "PENDING"),
            "runtime_fail_count": int(case.get("runtime_fail_count", 0) or 0),
            "reopened_count": int(case.get("reopened_count", 0) or 0),
            "artifact": case.get("artifact", "TASK_STATE.yaml"),
            "excerpt": str(case.get("excerpt") or "")[:180],
            "check_ids": sorted(case.get("check_ids") or [])[:4],
            "path_examples": [str(x) for x in (case.get("path_examples") or [])[:4]],
        }
        cases.append(case_view)

    cases.sort(
        key=lambda item: (
            int(item.get("failure_signals", 0)),
            int(item.get("reopened_count", 0)),
            int(item.get("runtime_fail_count", 0)),
            item.get("task_id", ""),
        ),
        reverse=True,
    )
    return cases[: max(int(limit or 0), 0)]


def _resolve_case(case_ref: str, tasks_dir: str) -> tuple[str, dict] | tuple[None, None]:
    if not case_ref:
        return (None, None)
    task_id = os.path.basename(case_ref.strip())
    task_dir = os.path.join(tasks_dir, task_id)
    if not os.path.isdir(task_dir):
        if os.path.isdir(case_ref):
            task_dir = case_ref
            task_id = os.path.basename(case_ref.rstrip(os.sep))
        else:
            return (None, None)
    case = _candidate_features(task_dir)
    if not case:
        return (None, None)
    return (task_id, case)


def diff_failure_cases(case_a: str, case_b: str, tasks_dir: str | None = None) -> dict | None:
    """Diff two failure cases at the metadata level for CLI usage."""
    if tasks_dir is None:
        tasks_dir = TASK_DIR
    if not tasks_dir or not os.path.isdir(tasks_dir):
        return None

    task_id_a, a = _resolve_case(case_a, tasks_dir)
    task_id_b, b = _resolve_case(case_b, tasks_dir)
    if not a or not b:
        return None

    shared_paths = sorted((a.get("path_tokens") or set()) & (b.get("path_tokens") or set()))[:8]
    shared_checks = sorted((a.get("check_ids") or set()) & (b.get("check_ids") or set()))[:8]
    shared_keywords = sorted((a.get("keywords") or set()) & (b.get("keywords") or set()))[:8]

    severity_a = int(a.get("failure_signals", 0) or 0)
    severity_b = int(b.get("failure_signals", 0) or 0)
    more_severe = ""
    if severity_a > severity_b:
        more_severe = task_id_a
    elif severity_b > severity_a:
        more_severe = task_id_b

    return {
        "case_a": {
            "task_id": task_id_a,
            "lane": a.get("lane", "unknown"),
            "artifact": a.get("artifact", "TASK_STATE.yaml"),
            "failure_signals": severity_a,
            "runtime_fail_count": int(a.get("runtime_fail_count", 0) or 0),
            "reopened_count": int(a.get("reopened_count", 0) or 0),
            "excerpt": str(a.get("excerpt") or "")[:180],
        },
        "case_b": {
            "task_id": task_id_b,
            "lane": b.get("lane", "unknown"),
            "artifact": b.get("artifact", "TASK_STATE.yaml"),
            "failure_signals": severity_b,
            "runtime_fail_count": int(b.get("runtime_fail_count", 0) or 0),
            "reopened_count": int(b.get("reopened_count", 0) or 0),
            "excerpt": str(b.get("excerpt") or "")[:180],
        },
        "same_lane": str(a.get("lane") or "") == str(b.get("lane") or ""),
        "shared_paths": shared_paths,
        "shared_check_ids": shared_checks,
        "shared_keywords": shared_keywords,
        "failure_signal_delta": severity_a - severity_b,
        "more_severe": more_severe,
    }


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


def format_similar_failures_hint(matches: list[dict] | None, max_chars: int = 220) -> str:
    if not matches:
        return ""
    hints = []
    for match in matches[:3]:
        hint = format_similar_failure_hint(match, max_chars=90)
        if hint:
            hints.append(hint)
    compact = " | ".join(hints)
    compact = re.sub(r"\s+", " ", compact).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."
