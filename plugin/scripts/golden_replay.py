#!/usr/bin/env python3
"""Golden replay runner for harness behavior baselines.

This replays a curated corpus of historical requests and task snapshots against
current harness logic to catch behavioral drift after harness-surface edits.

Case kinds:
  - routing       → compile_routing(task_dir)
  - close_gate    → compute_completion_failures(task_dir)
  - prompt_notes  → select_prompt_notes(prompt)
  - next_step     → stop_gate._next_step(status)
  - handoff       → preview_handoff(task_dir)
  - context       → emit_compact_context(task_dir)
  - team_launch   → team_launch_status(task_dir)
  - team_relaunch → select_team_relaunch_target(task_dir)

The corpus is stored as JSON so the runner stays stdlib-only.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import sys
from typing import Any, Dict, Iterable, List, Optional

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import _lib  # type: ignore
from _lib import find_repo_root  # type: ignore
from memory_selectors import select_prompt_notes  # type: ignore
from stop_gate import _next_step  # type: ignore
from task_completed_gate import compute_completion_failures  # type: ignore
from handoff_escalation import preview_handoff  # type: ignore

DEFAULT_CORPUS_REL = os.path.join("doc", "harness", "replays", "golden-corpus.json")
VALID_KINDS = {"routing", "close_gate", "prompt_notes", "next_step", "handoff", "context", "team_launch", "team_relaunch"}


def default_corpus_path(repo_root: Optional[str] = None) -> str:
    root = repo_root or find_repo_root(os.getcwd())
    return os.path.join(root, DEFAULT_CORPUS_REL)


def _resolve_repo_path(repo_root: str, value: str) -> str:
    if os.path.isabs(value):
        return os.path.normpath(value)
    return os.path.normpath(os.path.join(repo_root, value))


def load_corpus(corpus_path: str) -> Dict[str, Any]:
    with open(corpus_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError("golden corpus must be a JSON object")
    cases = data.get("cases")
    if not isinstance(cases, list):
        raise ValueError("golden corpus must contain a 'cases' list")
    return data


@contextlib.contextmanager
def _patched_provider_probe(spec: Optional[Dict[str, Any]] = None):
    spec = spec or {}
    native_ready = bool(spec.get("native_ready", False))
    omc_ready = bool(spec.get("omc_ready", False))
    claude_available = spec.get("claude_available")
    omc_available = spec.get("omc_available")

    original_native = _lib.native_agent_teams_runtime_probe
    original_omc = _lib.omc_runtime_probe
    original_which = shutil.which

    def _fake_native():
        return {"ready": native_ready, "source": "golden-replay"}

    def _fake_omc():
        return {"ready": omc_ready, "source": "golden-replay"}

    def _fake_which(cmd: str, *args: Any, **kwargs: Any):
        tool = os.path.basename(str(cmd or "")).strip()
        if tool == "claude" and claude_available is not None:
            return "/tmp/fake-claude" if bool(claude_available) else None
        if tool == "omc" and omc_available is not None:
            return "/tmp/fake-omc" if bool(omc_available) else None
        return original_which(cmd, *args, **kwargs)

    _lib.native_agent_teams_runtime_probe = _fake_native
    _lib.omc_runtime_probe = _fake_omc
    shutil.which = _fake_which
    try:
        yield
    finally:
        _lib.native_agent_teams_runtime_probe = original_native
        _lib.omc_runtime_probe = original_omc
        shutil.which = original_which


@contextlib.contextmanager
def _pushd(path_value: str):
    previous = os.getcwd()
    os.chdir(path_value)
    try:
        yield
    finally:
        os.chdir(previous)


def _base_result(case: Dict[str, Any], *, passed: bool, actual: Any = None, details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": str(case.get("id") or "unnamed-case"),
        "kind": str(case.get("kind") or "unknown"),
        "description": str(case.get("description") or ""),
        "passed": bool(passed),
        "actual": actual,
        "details": details or {},
    }


def _run_routing_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    with _pushd(repo_root), _patched_provider_probe(case.get("provider_probe")):
        actual = _lib.compile_routing(task_dir)

    mismatches = []
    compared = {}
    for field, expected_value in expected.items():
        actual_value = actual.get(field)
        compared[field] = {"expected": expected_value, "actual": actual_value}
        if actual_value != expected_value:
            mismatches.append(field)

    details = {
        "task_dir": os.path.relpath(task_dir, repo_root),
        "compared": compared,
        "mismatches": mismatches,
    }
    return _base_result(case, passed=not mismatches, actual=actual, details=details)


def _run_close_gate_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    failures = compute_completion_failures(task_dir)
    blocked = bool(failures)
    expected_blocked = bool(expected.get("blocked", False))

    passed = blocked == expected_blocked
    required_substrings = list(expected.get("required_substrings") or [])
    forbidden_substrings = list(expected.get("forbidden_substrings") or [])
    joined = "\n".join(str(item) for item in failures)
    missing_required = [needle for needle in required_substrings if needle not in joined]
    present_forbidden = [needle for needle in forbidden_substrings if needle in joined]
    if missing_required or present_forbidden:
        passed = False

    details = {
        "task_dir": os.path.relpath(task_dir, repo_root),
        "expected_blocked": expected_blocked,
        "actual_blocked": blocked,
        "missing_required_substrings": missing_required,
        "present_forbidden_substrings": present_forbidden,
    }
    actual = {
        "blocked": blocked,
        "failures": failures,
    }
    return _base_result(case, passed=passed, actual=actual, details=details)


def _run_handoff_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    trigger_override = case.get("trigger")
    compaction_just_occurred = bool(case.get("compaction_just_occurred", False))
    handoff = preview_handoff(
        task_dir,
        trigger=trigger_override,
        compaction_just_occurred=compaction_just_occurred,
    )

    actual = {
        "exists": bool(handoff),
        "trigger": str(handoff.get("trigger") or "") if isinstance(handoff, dict) else None,
        "next_step": str(handoff.get("next_step") or "") if isinstance(handoff, dict) else None,
        "files_to_read_first": list(handoff.get("files_to_read_first") or []) if isinstance(handoff, dict) else [],
        "open_check_ids": list(handoff.get("open_check_ids") or []) if isinstance(handoff, dict) else [],
    }
    team = handoff.get("team_recovery") if isinstance(handoff, dict) else None
    if isinstance(team, dict):
        actual["team_recovery"] = {
            "phase": str(team.get("phase") or ""),
            "pending_artifacts": list(team.get("pending_artifacts") or []),
            "pending_workers": list(team.get("pending_workers") or []),
            "doc_sync_owners": list(team.get("doc_sync_owners") or []),
            "document_critic_owners": list(team.get("document_critic_owners") or []),
        }

    passed = True
    details: Dict[str, Any] = {"task_dir": os.path.relpath(task_dir, repo_root)}

    expected_exists = expected.get("exists")
    if expected_exists is not None and bool(expected_exists) != bool(handoff):
        passed = False
        details["exists"] = {"expected": bool(expected_exists), "actual": bool(handoff)}

    expected_trigger = expected.get("trigger")
    if expected_trigger is not None:
        actual_trigger = actual.get("trigger")
        if actual_trigger != expected_trigger:
            passed = False
        details["trigger"] = {"expected": expected_trigger, "actual": actual_trigger}

    contains = expected.get("next_step_contains")
    if contains is not None:
        actual_step = str(actual.get("next_step") or "")
        if str(contains).lower() not in actual_step.lower():
            passed = False
        details["next_step_contains"] = {"expected": contains, "actual": actual_step}

    read_first_contains = list(expected.get("files_to_read_first_contains") or [])
    if read_first_contains:
        actual_files = [str(item) for item in (actual.get("files_to_read_first") or [])]
        missing = [needle for needle in read_first_contains if needle not in actual_files]
        if missing:
            passed = False
        details["files_to_read_first_contains"] = {"expected": read_first_contains, "missing": missing}

    open_check_ids_contains = list(expected.get("open_check_ids_contains") or [])
    if open_check_ids_contains:
        actual_open = [str(item) for item in (actual.get("open_check_ids") or [])]
        missing = [needle for needle in open_check_ids_contains if needle not in actual_open]
        if missing:
            passed = False
        details["open_check_ids_contains"] = {"expected": open_check_ids_contains, "missing": missing}

    team_expect = dict(expected.get("team_recovery") or {})
    if team_expect:
        actual_team = dict(actual.get("team_recovery") or {})
        phase = team_expect.get("phase")
        if phase is not None:
            actual_phase = actual_team.get("phase")
            if actual_phase != phase:
                passed = False
            details["team_phase"] = {"expected": phase, "actual": actual_phase}

        for key in ("pending_artifacts_contains", "pending_workers_contains", "doc_sync_owners", "document_critic_owners"):
            expected_items = list(team_expect.get(key) or [])
            if not expected_items:
                continue
            actual_key = key.replace("_contains", "") if key.endswith("_contains") else key
            actual_items = [str(item) for item in (actual_team.get(actual_key) or [])]
            missing = [needle for needle in expected_items if needle not in actual_items]
            if missing:
                passed = False
            details[key] = {"expected": expected_items, "missing": missing}

    return _base_result(case, passed=passed, actual=actual, details=details)


def _match_expected_members(actual_map: Dict[str, Any], expected_map: Dict[str, Any]) -> Dict[str, Any]:
    compared: Dict[str, Any] = {}
    mismatches: List[str] = []
    for field, expected_value in expected_map.items():
        actual_value = actual_map.get(field)
        compared[field] = {"expected": expected_value, "actual": actual_value}
        if actual_value != expected_value:
            mismatches.append(field)
    return {"compared": compared, "mismatches": mismatches}


def _run_context_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    with _pushd(repo_root), _patched_provider_probe(case.get("provider_probe")):
        actual = _lib.emit_compact_context(
            task_dir,
            raw_agent_name=str(case.get("agent_name") or "") or None,
            explicit_worker=str(case.get("team_worker") or "") or None,
        )

    passed = True
    details: Dict[str, Any] = {"task_dir": os.path.relpath(task_dir, repo_root)}

    exact = expected.get("exact")
    if isinstance(exact, dict):
        result = _match_expected_members(actual, exact)
        if result["mismatches"]:
            passed = False
        details["exact"] = result

    next_action_exact = expected.get("next_action_exact")
    if next_action_exact is not None:
        actual_next = str(actual.get("next_action") or "")
        if actual_next != str(next_action_exact):
            passed = False
        details["next_action_exact"] = {"expected": str(next_action_exact), "actual": actual_next}

    next_action_contains = expected.get("next_action_contains")
    if next_action_contains is not None:
        actual_next = str(actual.get("next_action") or "")
        if str(next_action_contains).lower() not in actual_next.lower():
            passed = False
        details["next_action_contains"] = {"expected": str(next_action_contains), "actual": actual_next}

    must_read_contains = list(expected.get("must_read_contains") or [])
    if must_read_contains:
        actual_read = [str(item) for item in (actual.get("must_read") or [])]
        missing = [needle for needle in must_read_contains if not any(str(item).endswith(str(needle)) or str(item) == str(needle) for item in actual_read)]
        if missing:
            passed = False
        details["must_read_contains"] = {"expected": must_read_contains, "missing": missing}

    team_expect = expected.get("team")
    if isinstance(team_expect, dict):
        actual_team = dict(actual.get("team") or {})
        result = _match_expected_members(actual_team, team_expect)
        if result["mismatches"]:
            passed = False
        details["team"] = result

    review_focus_expect = expected.get("review_focus")
    if isinstance(review_focus_expect, dict):
        actual_review_focus = dict(actual.get("review_focus") or {})
        result = _match_expected_members(actual_review_focus, review_focus_expect)
        if result["mismatches"]:
            passed = False
        details["review_focus"] = result

    return _base_result(case, passed=passed, actual=actual, details=details)


def _run_team_launch_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    with _pushd(repo_root), _patched_provider_probe(case.get("provider_probe")):
        actual = _lib.team_launch_status(task_dir)

    passed = True
    details: Dict[str, Any] = {"task_dir": os.path.relpath(task_dir, repo_root)}
    exact_fields = {k: v for k, v in expected.items() if k not in {"reason_contains", "execute_resolution_reason_contains"}}
    if exact_fields:
        result = _match_expected_members(actual, exact_fields)
        if result["mismatches"]:
            passed = False
        details["exact"] = result

    reason_contains = expected.get("reason_contains")
    if reason_contains is not None:
        actual_reason = str(actual.get("reason") or "")
        if str(reason_contains).lower() not in actual_reason.lower():
            passed = False
        details["reason_contains"] = {"expected": str(reason_contains), "actual": actual_reason}

    execute_contains = expected.get("execute_resolution_reason_contains")
    if execute_contains is not None:
        actual_reason = str(actual.get("execute_resolution_reason") or "")
        if str(execute_contains).lower() not in actual_reason.lower():
            passed = False
        details["execute_resolution_reason_contains"] = {"expected": str(execute_contains), "actual": actual_reason}

    return _base_result(case, passed=passed, actual=actual, details=details)


def _run_team_relaunch_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    task_dir = _resolve_repo_path(repo_root, str(case.get("task_dir") or ""))
    expected = dict(case.get("expect") or {})
    if not os.path.isdir(task_dir):
        return _base_result(case, passed=False, actual=None, details={"error": f"task dir not found: {task_dir}"})

    with _pushd(repo_root), _patched_provider_probe(case.get("provider_probe")):
        actual = _lib.select_team_relaunch_target(
            task_dir,
            raw_agent_name=str(case.get("agent_name") or "") or None,
            explicit_worker=str(case.get("team_worker") or "") or None,
        )

    passed = True
    details: Dict[str, Any] = {"task_dir": os.path.relpath(task_dir, repo_root)}
    exact_fields = {k: v for k, v in expected.items() if k not in {"selection_reason_contains", "reason_contains"}}
    if exact_fields:
        result = _match_expected_members(actual, exact_fields)
        if result["mismatches"]:
            passed = False
        details["exact"] = result

    selection_contains = expected.get("selection_reason_contains")
    if selection_contains is not None:
        actual_reason = str(actual.get("selection_reason") or "")
        if str(selection_contains).lower() not in actual_reason.lower():
            passed = False
        details["selection_reason_contains"] = {"expected": str(selection_contains), "actual": actual_reason}

    reason_contains = expected.get("reason_contains")
    if reason_contains is not None:
        actual_reason = str(actual.get("reason") or "")
        if str(reason_contains).lower() not in actual_reason.lower():
            passed = False
        details["reason_contains"] = {"expected": str(reason_contains), "actual": actual_reason}

    return _base_result(case, passed=passed, actual=actual, details=details)


def _run_prompt_notes_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    prompt = str(case.get("prompt") or "").strip()
    expected = dict(case.get("expect") or {})
    if not prompt:
        return _base_result(case, passed=False, actual=None, details={"error": "prompt_notes case missing prompt"})

    query_context = dict(case.get("query_context") or {})
    max_notes = int(case.get("max_notes") or 2)

    with _pushd(repo_root):
        notes = select_prompt_notes(prompt, query_context=query_context, max_notes=max_notes)

    actual_paths = [os.path.basename(str(item[0] or "")) for item in notes]
    actual_roots = [str(item[4] or "") for item in notes]
    actual = {
        "paths": actual_paths,
        "roots": actual_roots,
        "count": len(notes),
    }

    passed = True
    details: Dict[str, Any] = {}

    expected_count = expected.get("count")
    if expected_count is not None and len(notes) != int(expected_count):
        passed = False
        details["count"] = {"expected": int(expected_count), "actual": len(notes)}

    primary = expected.get("primary")
    if primary is not None:
        primary_actual = actual_paths[0] if actual_paths else None
        if primary_actual != primary:
            passed = False
        details["primary"] = {"expected": primary, "actual": primary_actual}

    secondary = expected.get("secondary")
    if secondary is not None:
        secondary_actual = actual_paths[1] if len(actual_paths) > 1 else None
        if secondary_actual != secondary:
            passed = False
        details["secondary"] = {"expected": secondary, "actual": secondary_actual}

    primary_root = expected.get("primary_root")
    if primary_root is not None:
        actual_root = actual_roots[0] if actual_roots else None
        if actual_root != primary_root:
            passed = False
        details["primary_root"] = {"expected": primary_root, "actual": actual_root}

    return _base_result(case, passed=passed, actual=actual, details=details)


def _run_next_step_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    _ = repo_root
    status = str(case.get("status") or "")
    expected = dict(case.get("expect") or {})
    actual = _next_step(status)

    passed = True
    details: Dict[str, Any] = {}

    exact = expected.get("exact")
    if exact is not None:
        if actual != exact:
            passed = False
        details["exact"] = {"expected": exact, "actual": actual}

    contains = expected.get("contains")
    if contains is not None:
        if str(contains).lower() not in actual.lower():
            passed = False
        details["contains"] = {"expected": contains, "actual": actual}

    return _base_result(case, passed=passed, actual=actual, details=details)


def run_case(case: Dict[str, Any], repo_root: str) -> Dict[str, Any]:
    kind = str(case.get("kind") or "").strip()
    if kind == "routing":
        return _run_routing_case(case, repo_root)
    if kind == "close_gate":
        return _run_close_gate_case(case, repo_root)
    if kind == "prompt_notes":
        return _run_prompt_notes_case(case, repo_root)
    if kind == "next_step":
        return _run_next_step_case(case, repo_root)
    if kind == "handoff":
        return _run_handoff_case(case, repo_root)
    if kind == "context":
        return _run_context_case(case, repo_root)
    if kind == "team_launch":
        return _run_team_launch_case(case, repo_root)
    if kind == "team_relaunch":
        return _run_team_relaunch_case(case, repo_root)
    return _base_result(case, passed=False, actual=None, details={"error": f"unsupported case kind: {kind}"})


def _normalize_case_ids(values: Optional[Iterable[str]]) -> List[str]:
    ids: List[str] = []
    for value in values or []:
        if not value:
            continue
        for item in str(value).split(","):
            item = item.strip()
            if item:
                ids.append(item)
    return ids


def execute_replay(
    *,
    corpus_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    kind_filters: Optional[Iterable[str]] = None,
    case_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    resolved_repo_root = repo_root or find_repo_root(os.getcwd())
    resolved_corpus_path = corpus_path or default_corpus_path(resolved_repo_root)
    corpus = load_corpus(resolved_corpus_path)

    raw_cases = list(corpus.get("cases") or [])
    requested_kinds = {str(item).strip() for item in (kind_filters or []) if str(item).strip()}
    requested_ids = set(_normalize_case_ids(case_ids))

    if requested_kinds - VALID_KINDS:
        unknown = sorted(requested_kinds - VALID_KINDS)
        raise ValueError(f"unsupported kind filter(s): {', '.join(unknown)}")

    selected: List[Dict[str, Any]] = []
    for case in raw_cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id") or "")
        case_kind = str(case.get("kind") or "")
        if requested_kinds and case_kind not in requested_kinds:
            continue
        if requested_ids and case_id not in requested_ids:
            continue
        selected.append(case)

    results = [run_case(case, resolved_repo_root) for case in selected]
    passed = sum(1 for item in results if item.get("passed"))
    failed = len(results) - passed

    return {
        "corpus_path": os.path.relpath(resolved_corpus_path, resolved_repo_root),
        "repo_root": resolved_repo_root,
        "summary": {
            "total": len(results),
            "passed": passed,
            "failed": failed,
        },
        "results": results,
    }


def emit_report(report: Dict[str, Any], *, json_output: bool = False) -> None:
    if json_output:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return

    summary = report.get("summary") or {}
    total = int(summary.get("total") or 0)
    passed = int(summary.get("passed") or 0)
    failed = int(summary.get("failed") or 0)
    print(f"golden replay: {passed}/{total} passed ({failed} failed)")
    print(f"corpus: {report.get('corpus_path')}")

    for item in report.get("results") or []:
        status = "PASS" if item.get("passed") else "FAIL"
        print(f"[{status}] {item.get('id')} ({item.get('kind')})")
        if item.get("description"):
            print(f"  {item['description']}")
        details = item.get("details") or {}
        if item.get("kind") == "routing":
            mismatches = details.get("mismatches") or []
            if mismatches:
                print(f"  mismatches: {', '.join(mismatches)}")
        elif item.get("kind") == "close_gate":
            actual = item.get("actual") or {}
            print(f"  blocked={actual.get('blocked')} failures={len(actual.get('failures') or [])}")
            for failure in (actual.get("failures") or [])[:3]:
                print(f"    - {failure}")
        elif item.get("kind") == "prompt_notes":
            actual = item.get("actual") or {}
            print(f"  notes: {', '.join(actual.get('paths') or []) or '(none)'}")
        elif item.get("kind") == "next_step":
            print(f"  next: {item.get('actual')}")
        elif item.get("kind") == "handoff":
            actual = item.get("actual") or {}
            print(f"  trigger={actual.get('trigger')} read_first={len(actual.get('files_to_read_first') or [])}")
            if actual.get("team_recovery"):
                print(f"  team phase={actual['team_recovery'].get('phase')}")
        elif item.get("kind") == "context":
            actual = item.get("actual") or {}
            team = actual.get("team") or {}
            print(f"  next: {actual.get('next_action')}")
            if team:
                print(f"  team launch={team.get('launch_generated')} relaunch={team.get('relaunch_worker')}:{team.get('relaunch_phase')}")
        elif item.get("kind") == "team_launch":
            actual = item.get("actual") or {}
            print(f"  provider={actual.get('provider')} target={actual.get('target')} generated={actual.get('generated')} stale={actual.get('stale')}")
        elif item.get("kind") == "team_relaunch":
            actual = item.get("actual") or {}
            print(f"  worker={actual.get('worker')} phase={actual.get('phase')} ready={actual.get('ready')}")
        error = details.get("error")
        if error:
            print(f"  error: {error}")


def run_cli(
    *,
    corpus_path: Optional[str] = None,
    repo_root: Optional[str] = None,
    kind_filters: Optional[Iterable[str]] = None,
    case_ids: Optional[Iterable[str]] = None,
    json_output: bool = False,
) -> int:
    try:
        report = execute_replay(
            corpus_path=corpus_path,
            repo_root=repo_root,
            kind_filters=kind_filters,
            case_ids=case_ids,
        )
    except Exception as exc:
        if json_output:
            print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        else:
            print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    emit_report(report, json_output=json_output)
    failed = int((report.get("summary") or {}).get("failed") or 0)
    return 0 if failed == 0 else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="golden_replay",
        description="Replay curated harness behavior baselines against the current repo",
    )
    parser.add_argument("--corpus", metavar="FILE", help=f"JSON corpus path (default: {DEFAULT_CORPUS_REL})")
    parser.add_argument(
        "--kind",
        action="append",
        choices=sorted(VALID_KINDS),
        help="filter by case kind (repeatable)",
    )
    parser.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        metavar="ID",
        help="filter by case id (repeatable or comma-separated)",
    )
    parser.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return run_cli(
        corpus_path=args.corpus,
        kind_filters=args.kind,
        case_ids=args.case_ids,
        json_output=bool(args.json),
    )


if __name__ == "__main__":
    sys.exit(main())
