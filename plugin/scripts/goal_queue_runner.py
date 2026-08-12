#!/usr/bin/env python3
"""Persistent queue runner for Goal child tasks.

The state file is JSON-compatible YAML so it can live at
doc/harness/goal-queue.json without requiring PyYAML.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_STATE = Path("doc/harness/goal-queue.json")
STOP_MARKERS = ("BLOCKED_ENV", "USER_DECISION_REQUIRED", "GOAL_QUEUE_STOP")
NON_RETRYABLE_FAILURES = {"auth_required", "browser_unavailable", "user_decision_required"}
FAILURE_POLICIES: dict[str, dict[str, Any]] = {
    "auth_required": {
        "retryable": False,
        "recommended_action": "Re-authenticate the CLI or service, then run recover and restart the loop.",
    },
    "network_unavailable": {
        "retryable": True,
        "recommended_action": "Restore network access or proxy/DNS settings if retries keep failing.",
    },
    "dependency_missing": {
        "retryable": True,
        "recommended_action": "Install or restore the missing dependency, then rerun the slice.",
    },
    "test_failure": {
        "retryable": True,
        "recommended_action": "Feed the failing test output back through the active Goal child task.",
    },
    "harness_close_missing": {
        "retryable": True,
        "recommended_action": "Re-run the slice until its harness task closes with runtime_verdict PASS.",
    },
    "browser_unavailable": {
        "retryable": False,
        "recommended_action": "Install/start browser QA tooling or adjust browser scope with user approval.",
    },
    "port_conflict": {
        "retryable": True,
        "recommended_action": "Stop the conflicting process or change the declared port.",
    },
    "timeout": {
        "retryable": True,
        "recommended_action": "Inspect logs and increase timeout only if progress is real.",
    },
    "user_decision_required": {
        "retryable": False,
        "recommended_action": "Ask the user to decide; do not infer product, billing, auth, or architecture choices.",
    },
    "unknown": {
        "retryable": True,
        "recommended_action": "Inspect the transcript and add a classifier rule if this repeats.",
    },
}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"goal queue state not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"goal queue state must be JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("slices"), list):
        raise SystemExit("goal queue state missing slices[]")
    return data


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def event_path(state_path: Path) -> Path:
    return state_path.parent / "goal-queue-events.jsonl"


def heartbeat_path(state_path: Path) -> Path:
    return state_path.parent / "runtime" / "goal-queue-heartbeat.json"


def append_event(state_path: Path, event_type: str, **fields: Any) -> None:
    event = {"ts": now_iso(), "type": event_type}
    event.update({k: v for k, v in fields.items() if v not in (None, "")})
    path = event_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def write_heartbeat(state_path: Path, item: dict[str, Any] | None,
                    status: str, pid: int | None = None) -> None:
    payload = {
        "ts": now_iso(),
        "status": status,
        "pid": pid,
        "slice_id": item.get("id") if item else "",
        "task_id": item.get("task_id") if item else "",
    }
    atomic_write(heartbeat_path(state_path), json.dumps(payload, indent=2, sort_keys=True) + "\n")


def parse_iso(value: str) -> float:
    try:
        return _dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=_dt.timezone.utc
        ).timestamp()
    except Exception:
        return 0.0


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def parse_slice(raw: str, index: int) -> dict[str, Any]:
    if ":" in raw:
        slice_id, title = raw.split(":", 1)
        slice_id = slice_id.strip()
        title = title.strip()
    else:
        slice_id = f"slice-{index:03d}"
        title = raw.strip()
    if not slice_id or not title:
        raise SystemExit("--slice must be 'id:title' or a non-empty title")
    return {
        "id": slice_id,
        "title": title,
        "status": "pending",
        "attempts": 0,
        "task_id": f"TASK__goal-queue-{slice_id}",
        "last_result": "",
        "last_command": "",
        "updated_at": now_iso(),
    }


def backlog_item_from_slice(item: dict[str, Any], index: int) -> dict[str, Any]:
    title = str(item.get("title") or item.get("id") or f"slice-{index:03d}")
    return {
        "id": str(item.get("id") or f"slice-{index:03d}"),
        "title": title,
        "kind": str(item.get("kind") or "MVP"),
        "user_value": str(item.get("user_value") or f"User can experience: {title}"),
        "hypothesis": str(item.get("hypothesis") or f"Delivering {title} increases product completeness."),
        "acceptance": item.get("acceptance") if isinstance(item.get("acceptance"), list) else [title],
        "priority": int(item.get("priority") or max(1, 1000 - index)),
        "status": str(item.get("status") or "pending"),
        "learned_from": str(item.get("learned_from") or ""),
    }


def ensure_agile_fields(state: dict[str, Any]) -> None:
    state.setdefault("current_iteration", 1)
    state.setdefault("iteration_reviews", [])
    slices = state.get("slices") if isinstance(state.get("slices"), list) else []
    existing = state.get("backlog") if isinstance(state.get("backlog"), list) else []
    by_id = {
        str(item.get("id")): item
        for item in existing
        if isinstance(item, dict) and item.get("id")
    }
    backlog = []
    for index, item in enumerate(slices, start=1):
        if not isinstance(item, dict):
            continue
        merged = backlog_item_from_slice(item, index)
        existing_item = by_id.get(merged["id"])
        if isinstance(existing_item, dict):
            merged.update(existing_item)
        merged["status"] = str(item.get("status") or merged.get("status") or "pending")
        backlog.append(merged)
    state["backlog"] = backlog


def find_slice(state: dict[str, Any], slice_id: str) -> dict[str, Any] | None:
    for item in state.get("slices", []):
        if isinstance(item, dict) and item.get("id") == slice_id:
            return item
    return None


def find_backlog_item(state: dict[str, Any], slice_id: str) -> dict[str, Any] | None:
    ensure_agile_fields(state)
    for item in state.get("backlog", []):
        if isinstance(item, dict) and item.get("id") == slice_id:
            return item
    return None


def set_slice_status(state: dict[str, Any], slice_id: str, status: str) -> None:
    item = find_slice(state, slice_id)
    if item is not None:
        item["status"] = status
        item["updated_at"] = now_iso()
    backlog_item = find_backlog_item(state, slice_id)
    if backlog_item is not None:
        backlog_item["status"] = status


def init_state(args: argparse.Namespace) -> int:
    if args.state.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing state: {args.state}")
    if not args.slice:
        raise SystemExit("at least one --slice is required")
    state = {
        "version": 1,
        "status": "active",
        "product": args.product,
        "stack": args.stack,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "settings": {
            "max_attempts": args.max_attempts,
        },
        "slices": [parse_slice(raw, i + 1) for i, raw in enumerate(args.slice)],
    }
    ensure_agile_fields(state)
    save_state(args.state, state)
    append_event(args.state, "initialized", product=args.product, slices=len(state["slices"]))
    print(f"goal queue initialized: {args.state} ({len(state['slices'])} child tasks)")
    return 0


def counts(state: dict[str, Any]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in state.get("slices", []):
        status = str(item.get("status") or "pending")
        out[status] = out.get(status, 0) + 1
    return out


def next_slice(state: dict[str, Any]) -> dict[str, Any] | None:
    ensure_agile_fields(state)
    max_attempts = int((state.get("settings") or {}).get("max_attempts") or 3)
    preferred = str(state.get("next_slice_id") or "")
    if preferred:
        item = find_slice(state, preferred)
        if item is not None:
            status = str(item.get("status") or "pending")
            attempts = int(item.get("attempts") or 0)
            if status == "pending" or (status == "failed" and attempts < max_attempts):
                return item
    priority = {
        str(item.get("id")): int(item.get("priority") or 0)
        for item in state.get("backlog", [])
        if isinstance(item, dict) and item.get("id")
    }
    slices = sorted(
        [item for item in state.get("slices", []) if isinstance(item, dict)],
        key=lambda item: (-priority.get(str(item.get("id")), 0), str(item.get("id") or "")),
    )
    for item in slices:
        status = str(item.get("status") or "pending")
        attempts = int(item.get("attempts") or 0)
        if status == "pending":
            return item
        if status == "failed" and attempts < max_attempts:
            return item
    return None


def latest_reviews_by_slice(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    reviews: dict[str, dict[str, Any]] = {}
    for review_entry in state.get("iteration_reviews", []):
        if not isinstance(review_entry, dict):
            continue
        slice_id = str(review_entry.get("slice_id") or "")
        if slice_id:
            reviews[slice_id] = review_entry
    return reviews


def has_runnable_slice_after(state: dict[str, Any], slice_id: str) -> bool:
    candidate = next_slice(state)
    return bool(candidate and str(candidate.get("id") or "") != slice_id)


def evaluate_review_quality(review_entry: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    warnings: list[str] = []
    blockers: list[str] = []
    slice_id = str(review_entry.get("slice_id") or "")

    demo_result = str(review_entry.get("demo_result") or "")
    workflow_status = str(review_entry.get("user_workflow_status") or "")
    qa_result = str(review_entry.get("qa_result") or "")
    ux_result = str(review_entry.get("ux_result") or "")

    if demo_result == "partial":
        warnings.append(f"{slice_id}: demo is partial; confirm the next slice addresses the visible gap")
    elif demo_result == "fail":
        blockers.append(f"{slice_id}: demo failed; fix the user-visible workflow before continuing")

    if workflow_status == "partial":
        warnings.append(f"{slice_id}: user workflow is partial; carry the missing path into backlog")
    elif workflow_status == "blocked":
        blockers.append(f"{slice_id}: user workflow is blocked")

    if qa_result == "not_run":
        warnings.append(f"{slice_id}: QA did not run; run or justify the missing lens before shipping")
    elif qa_result == "FAIL":
        blockers.append(f"{slice_id}: QA failed")
    elif qa_result == "BLOCKED_ENV":
        blockers.append(f"{slice_id}: QA is BLOCKED_ENV")

    if ux_result == "FAIL":
        warnings.append(f"{slice_id}: UX failed; treat the finding as product work unless waived")

    if not review_entry.get("learnings"):
        warnings.append(f"{slice_id}: review has no learning; record what changed or why nothing changed")
    if not review_entry.get("backlog_changes"):
        warnings.append(f"{slice_id}: review has no backlog change; record keep/reorder/defer rationale")
    if has_runnable_slice_after(state, slice_id) and not review_entry.get("next_slice_reason"):
        warnings.append(f"{slice_id}: next slice reason is missing")

    quality = "complete"
    if warnings:
        quality = "warning"
    if blockers:
        quality = "blocked"
    return {
        "review_quality": quality,
        "quality_warnings": warnings,
        "quality_blockers": blockers,
    }


def preflight_findings(state: dict[str, Any], *, require_review_before_next: bool = False) -> dict[str, list[str]]:
    ensure_agile_fields(state)
    warnings: list[str] = []
    blockers: list[str] = []
    reviews = latest_reviews_by_slice(state)

    for item in state.get("slices", []):
        if not isinstance(item, dict) or item.get("status") != "passed":
            continue
        slice_id = str(item.get("id") or "")
        review_entry = reviews.get(slice_id)
        if not review_entry:
            message = f"{slice_id}: passed slice has no iteration review"
            if require_review_before_next:
                blockers.append(message)
            else:
                warnings.append(message)
            continue
        for warning in review_entry.get("quality_warnings") or []:
            warnings.append(str(warning))
        for blocker in review_entry.get("quality_blockers") or []:
            blockers.append(str(blocker))

    item = next_slice(state)
    if item and state.get("next_slice_id") and not state.get("next_slice_reason"):
        warnings.append(f"{state.get('next_slice_id')}: selected next slice has no reason")

    return {"warnings": warnings, "blockers": blockers}


def print_preflight(findings: dict[str, list[str]]) -> None:
    blockers = findings.get("blockers") or []
    warnings = findings.get("warnings") or []
    if blockers:
        print("preflight: BLOCK")
    elif warnings:
        print("preflight: WARN")
    else:
        print("preflight: PASS")
    for warning in warnings:
        print(f"warning: {warning}")
    for blocker in blockers:
        print(f"blocker: {blocker}")


def preflight(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    findings = preflight_findings(
        state,
        require_review_before_next=args.require_review_before_next,
    )
    print_preflight(findings)
    append_event(
        args.state,
        "preflight_checked",
        warnings=len(findings.get("warnings") or []),
        blockers=len(findings.get("blockers") or []),
        require_review_before_next=args.require_review_before_next,
    )
    return 2 if findings.get("blockers") else 0


def enforce_preflight(args: argparse.Namespace, state: dict[str, Any]) -> int:
    if not getattr(args, "require_review_before_next", False):
        return 0
    findings = preflight_findings(state, require_review_before_next=True)
    if findings.get("blockers"):
        print_preflight(findings)
        append_event(
            args.state,
            "preflight_blocked",
            warnings=len(findings.get("warnings") or []),
            blockers=len(findings.get("blockers") or []),
        )
        return 2
    if findings.get("warnings"):
        print_preflight(findings)
    return 0


def refresh_top_status(state: dict[str, Any]) -> None:
    statuses = [str(item.get("status") or "pending") for item in state.get("slices", [])]
    if statuses and all(status == "passed" for status in statuses):
        state["status"] = "done"
    elif any(status == "blocked" for status in statuses):
        state["status"] = "blocked"
    elif state.get("status") != "stopped":
        state["status"] = "active"


def print_status(state: dict[str, Any]) -> None:
    ensure_agile_fields(state)
    refresh_top_status(state)
    summary = counts(state)
    print(f"status: {state.get('status')}")
    print(f"product: {state.get('product')}")
    print(f"stack: {state.get('stack')}")
    print(f"iteration: {state.get('current_iteration')}")
    print("slices: " + ", ".join(f"{key}={summary[key]}" for key in sorted(summary)))
    item = next_slice(state)
    if item:
        print(f"next: {item['id']} - {item['title']} (attempts={item.get('attempts', 0)})")
        if state.get("next_slice_reason"):
            print(f"next_reason: {state.get('next_slice_reason')}")
    else:
        print("next: none")
    for item in state.get("slices", []):
        if item.get("status") in {"failed", "blocked"} and item.get("failure_class"):
            print(
                "failure: "
                f"{item.get('id')} class={item.get('failure_class')} "
                f"retryable={str(item.get('retryable')).lower()} "
                f"action={item.get('recommended_action')}"
            )
            break


def format_command(template: str, item: dict[str, Any], state: dict[str, Any]) -> str:
    values = {
        "slice_id": item.get("id", ""),
        "title": item.get("title", ""),
        "task_id": item.get("task_id", ""),
        "product": state.get("product", ""),
        "stack": state.get("stack", ""),
        "prompt": (
            f"/goal child task {item.get('id')}: {item.get('title')} "
            f"for product {state.get('product')} using stack {state.get('stack')}"
        ),
    }
    try:
        return template.format(**values)
    except KeyError as exc:
        raise SystemExit(f"unknown command placeholder: {exc}") from exc


def run_command(command: str, timeout: int, state_path: Path,
                item: dict[str, Any]) -> tuple[int, str]:
    proc = subprocess.Popen(
        command,
        shell=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        executable="/bin/bash" if Path("/bin/bash").exists() else None,
    )
    write_heartbeat(state_path, item, "running", pid=proc.pid)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.send_signal(signal.SIGTERM)
            stdout, stderr = proc.communicate(timeout=5)
        except Exception:
            proc.kill()
            stdout, stderr = proc.communicate()
        output = (stdout + stderr).strip()
        raise subprocess.TimeoutExpired(command, timeout, output=output)
    output = (stdout + stderr).strip()
    return proc.returncode, output


def repo_root_for_state(state_path: Path) -> Path:
    parent = state_path.parent
    if parent.name == "harness" and parent.parent.name == "doc":
        return parent.parent.parent
    return parent


def harness_task_passed(state_path: Path, item: dict[str, Any]) -> tuple[bool, str]:
    task_id = str(item.get("task_id") or "")
    if not task_id:
        return False, "slice has no task_id"
    task_dir = repo_root_for_state(state_path) / "doc" / "harness" / "tasks" / task_id
    scripts = repo_root_for_state(state_path) / "plugin" / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    try:
        from _lib import (read_task_control, receipt_runtime_verdict, task_control_status)
        control = read_task_control(str(task_dir))
        status = task_control_status(str(task_dir), control)
        verdict = receipt_runtime_verdict(str(task_dir), control)
    except Exception:
        status, verdict = "invalid", "PENDING"
    if status == "closed" and verdict == "PASS":
        return True, f"{task_id} closed with runtime_verdict PASS"
    return False, f"{task_id} not closed/PASS (status={status or 'missing'}, runtime_verdict={verdict or 'missing'})"


def classify_failure(returncode: int, output: str) -> dict[str, Any]:
    text = output.lower()
    if "user_decision_required" in text:
        name = "user_decision_required"
    elif "auth" in text and any(token in text for token in ("login", "expired", "unauthorized", "forbidden")):
        name = "auth_required"
    elif "browser" in text and any(token in text for token in ("not found", "unavailable", "missing", "cannot connect")):
        name = "browser_unavailable"
    elif "harness_close_required" in text:
        name = "harness_close_missing"
    elif returncode == 124 or "timed out" in text or "timeout" in text:
        name = "timeout"
    elif "address already in use" in text or "port conflict" in text or "eaddrinuse" in text:
        name = "port_conflict"
    elif any(token in text for token in ("network is unreachable", "temporary failure in name resolution", "could not resolve host", "connection refused")):
        name = "network_unavailable"
    elif any(token in text for token in ("cannot find module", "module not found", "no module named", "command not found", "missing dependency")):
        name = "dependency_missing"
    elif any(token in text for token in ("failed", "assertionerror", "traceback", "pytest", "test failure", "qa fail")):
        name = "test_failure"
    else:
        name = "unknown"
    policy = FAILURE_POLICIES[name]
    return {
        "failure_class": name,
        "retryable": bool(policy["retryable"]),
        "recommended_action": str(policy["recommended_action"]),
    }


def apply_failure(item: dict[str, Any], classification: dict[str, Any]) -> None:
    item["failure_class"] = classification["failure_class"]
    item["retryable"] = classification["retryable"]
    item["recommended_action"] = classification["recommended_action"]


def mark_result(state: dict[str, Any], item: dict[str, Any], returncode: int, output: str,
                state_path: Path) -> str:
    max_attempts = int((state.get("settings") or {}).get("max_attempts") or 3)
    item["last_result"] = output[-2000:]
    item["updated_at"] = now_iso()
    if returncode == 0:
        item["status"] = "passed"
        set_slice_status(state, str(item.get("id")), "passed")
        item["failure_class"] = ""
        item["recommended_action"] = ""
        item["retryable"] = True
        append_event(state_path, "slice_passed", slice_id=item.get("id"), task_id=item.get("task_id"))
        return "passed"
    classification = classify_failure(returncode, output)
    apply_failure(item, classification)
    if not classification["retryable"] or any(marker in output for marker in STOP_MARKERS):
        item["status"] = "blocked"
        set_slice_status(state, str(item.get("id")), "blocked")
        state["status"] = "blocked"
        append_event(
            state_path, "slice_blocked", slice_id=item.get("id"),
            task_id=item.get("task_id"), reason=classification["failure_class"],
        )
        return "blocked"
    if int(item.get("attempts") or 0) >= max_attempts:
        item["status"] = "blocked"
        set_slice_status(state, str(item.get("id")), "blocked")
        state["status"] = "blocked"
        append_event(
            state_path, "slice_blocked", slice_id=item.get("id"),
            task_id=item.get("task_id"), reason=classification["failure_class"],
        )
        return "blocked"
    item["status"] = "failed"
    set_slice_status(state, str(item.get("id")), "failed")
    append_event(state_path, "slice_failed", slice_id=item.get("id"), task_id=item.get("task_id"))
    return "failed"


def run_once(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    preflight_rc = enforce_preflight(args, state)
    if preflight_rc:
        return preflight_rc
    refresh_top_status(state)
    if state.get("status") in {"done", "blocked", "stopped"}:
        print(f"goal queue {state['status']}: no runnable child task")
        return 0 if state.get("status") == "done" else 2
    item = next_slice(state)
    if not item:
        refresh_top_status(state)
        save_state(args.state, state)
        print(f"goal queue {state['status']}: no runnable child task")
        return 0
    if not args.command_template:
        print(f"next: {item['id']} - {item['title']}")
        print("prompt: " + format_command("{prompt}", item, state))
        print("run-once requires --command-template to execute unattended")
        return 2

    command = format_command(args.command_template, item, state)
    item["status"] = "running"
    item["attempts"] = int(item.get("attempts") or 0) + 1
    item["last_command"] = command
    item["updated_at"] = now_iso()
    save_state(args.state, state)
    append_event(
        args.state, "slice_started", slice_id=item.get("id"),
        task_id=item.get("task_id"), attempt=item.get("attempts"),
    )
    write_heartbeat(args.state, item, "running")

    print(f"running: {item['id']} attempt {item['attempts']}")
    print(f"command: {shlex.quote(command)}")
    try:
        returncode, output = run_command(command, args.timeout, args.state, item)
    except subprocess.TimeoutExpired as exc:
        returncode = 124
        output = f"GOAL_QUEUE_STOP command timed out after {args.timeout}s: {exc}"

    if returncode == 0 and args.require_harness_close:
        ok, detail = harness_task_passed(args.state, item)
        if not ok:
            returncode = 1
            output = (output + "\n" if output else "") + f"HARNESS_CLOSE_REQUIRED {detail}"
        else:
            output = (output + "\n" if output else "") + detail

    status = mark_result(state, item, returncode, output, args.state)
    refresh_top_status(state)
    save_state(args.state, state)
    write_heartbeat(args.state, item, status)
    print(f"result: {item['id']} {status} rc={returncode}")
    if output:
        print(output[-4000:])
    return 0 if status == "passed" else 1


def loop(args: argparse.Namespace) -> int:
    deadline = time.time() + max(0.0, args.max_hours) * 3600
    iterations = 0
    last_rc = 0
    while True:
        if args.max_iterations and iterations >= args.max_iterations:
            print("goal queue stopped: max iterations reached")
            return last_rc
        if args.max_hours and time.time() >= deadline:
            print("goal queue stopped: time budget reached")
            return last_rc
        iterations += 1
        last_rc = run_once(args)
        state = load_state(args.state)
        refresh_top_status(state)
        save_state(args.state, state)
        if state.get("status") in {"done", "blocked", "stopped"}:
            print(f"goal queue {state['status']}")
            return 0 if state.get("status") == "done" else 2
        if last_rc not in (0, 1):
            return last_rc
        if args.sleep_sec:
            time.sleep(args.sleep_sec)


def recover(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    heartbeat = {}
    hb_path = heartbeat_path(args.state)
    if hb_path.exists():
        try:
            heartbeat = json.loads(hb_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            heartbeat = {}
    hb_age = time.time() - parse_iso(str(heartbeat.get("ts") or ""))
    hb_pid = heartbeat.get("pid") if isinstance(heartbeat.get("pid"), int) else None
    stale = not heartbeat or hb_age > args.stale_sec or not pid_alive(hb_pid)
    recovered = 0
    max_attempts = int((state.get("settings") or {}).get("max_attempts") or 3)
    for item in state.get("slices", []):
        if item.get("status") != "running":
            continue
        if not stale:
            print(f"running slice still appears active: {item.get('id')}")
            continue
        if int(item.get("attempts") or 0) >= max_attempts:
            item["status"] = "blocked"
            reason = "recovered_to_blocked"
        else:
            item["status"] = "failed"
            reason = "recovered_to_failed"
        item["last_result"] = f"Recovered stale/dead running slice from heartbeat {hb_path}"
        item["updated_at"] = now_iso()
        append_event(args.state, "slice_recovered", slice_id=item.get("id"), reason=reason)
        recovered += 1
    refresh_top_status(state)
    save_state(args.state, state)
    write_heartbeat(args.state, None, "recovered")
    print(f"recovered: {recovered}")
    return 0


def review(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    ensure_agile_fields(state)
    if find_slice(state, args.slice_id) is None:
        raise SystemExit(f"unknown slice_id: {args.slice_id}")
    review_entry = {
        "iteration": int(state.get("current_iteration") or 1),
        "slice_id": args.slice_id,
        "demo_result": args.demo_result,
        "user_workflow_status": args.user_workflow_status,
        "qa_result": args.qa_result,
        "ux_result": args.ux_result,
        "learnings": args.learning or [],
        "backlog_changes": args.backlog_change or [],
        "next_slice_id": args.next_slice_id,
        "next_slice_reason": args.next_slice_reason,
        "reviewed_at": now_iso(),
    }
    review_entry.update(evaluate_review_quality(review_entry, state))
    state.setdefault("iteration_reviews", []).append(review_entry)
    state["current_iteration"] = int(state.get("current_iteration") or 1) + 1
    if args.next_slice_id:
        state["next_slice_id"] = args.next_slice_id
    if args.next_slice_reason:
        state["next_slice_reason"] = args.next_slice_reason
    append_event(args.state, "iteration_reviewed", slice_id=args.slice_id, next_slice_id=args.next_slice_id)
    save_state(args.state, state)
    print(f"review recorded: iteration {review_entry['iteration']} slice {args.slice_id}")
    print(f"review_quality: {review_entry['review_quality']}")
    for warning in review_entry["quality_warnings"]:
        print(f"warning: {warning}")
    for blocker in review_entry["quality_blockers"]:
        print(f"blocker: {blocker}")
    return 0


def parse_assignment(raw: str, *, value_name: str) -> tuple[str, str]:
    if ":" not in raw:
        raise SystemExit(f"{value_name} must be slice_id:value")
    left, right = raw.split(":", 1)
    left = left.strip()
    right = right.strip()
    if not left or not right:
        raise SystemExit(f"{value_name} must be slice_id:value")
    return left, right


def replan(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    ensure_agile_fields(state)
    changes = []
    for raw in args.set_priority or []:
        slice_id, value = parse_assignment(raw, value_name="--set-priority")
        item = find_backlog_item(state, slice_id)
        if item is None:
            raise SystemExit(f"unknown backlog slice: {slice_id}")
        item["priority"] = int(value)
        changes.append(f"priority:{slice_id}:{value}")
    for raw in args.set_status or []:
        slice_id, value = parse_assignment(raw, value_name="--set-status")
        if find_slice(state, slice_id) is None:
            raise SystemExit(f"unknown slice: {slice_id}")
        set_slice_status(state, slice_id, value)
        changes.append(f"status:{slice_id}:{value}")
    if args.next_slice_id:
        if find_slice(state, args.next_slice_id) is None:
            raise SystemExit(f"unknown next slice: {args.next_slice_id}")
        state["next_slice_id"] = args.next_slice_id
    if args.next_slice_reason:
        state["next_slice_reason"] = args.next_slice_reason
    append_event(
        args.state, "backlog_replanned",
        next_slice_id=args.next_slice_id, changes=",".join(changes),
    )
    refresh_top_status(state)
    save_state(args.state, state)
    print(f"replanned: {len(changes)} changes")
    if state.get("next_slice_id"):
        print(f"next: {state.get('next_slice_id')}")
    if state.get("next_slice_reason"):
        print(f"next_reason: {state.get('next_slice_reason')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persistent Goal child-task queue runner")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--product", required=True)
    p_init.add_argument("--stack", required=True)
    p_init.add_argument("--slice", action="append", default=[])
    p_init.add_argument("--max-attempts", type=int, default=3)
    p_init.add_argument("--force", action="store_true")

    sub.add_parser("status")
    sub.add_parser("next")
    p_preflight = sub.add_parser("preflight")
    p_preflight.add_argument("--require-review-before-next", action="store_true")
    p_recover = sub.add_parser("recover")
    p_recover.add_argument("--stale-sec", type=int, default=300)

    p_review = sub.add_parser("review")
    p_review.add_argument("--slice-id", required=True)
    p_review.add_argument("--demo-result", choices=("pass", "fail", "partial"), required=True)
    p_review.add_argument(
        "--user-workflow-status", choices=("complete", "partial", "blocked"), required=True
    )
    p_review.add_argument("--qa-result", choices=("PASS", "FAIL", "BLOCKED_ENV", "not_run"), required=True)
    p_review.add_argument("--ux-result", choices=("PASS", "FAIL", "not_applicable"), default="not_applicable")
    p_review.add_argument("--learning", action="append", default=[])
    p_review.add_argument("--backlog-change", action="append", default=[])
    p_review.add_argument("--next-slice-id", default="")
    p_review.add_argument("--next-slice-reason", default="")

    p_replan = sub.add_parser("replan")
    p_replan.add_argument("--set-priority", action="append", default=[])
    p_replan.add_argument("--set-status", action="append", default=[])
    p_replan.add_argument("--next-slice-id", default="")
    p_replan.add_argument("--next-slice-reason", default="")

    p_run = sub.add_parser("run-once")
    p_run.add_argument("--command-template", default="")
    p_run.add_argument("--timeout", type=int, default=1800)
    p_run.add_argument("--require-harness-close", action="store_true")
    p_run.add_argument("--require-review-before-next", action="store_true")

    p_loop = sub.add_parser("loop")
    p_loop.add_argument("--command-template", required=True)
    p_loop.add_argument("--timeout", type=int, default=1800)
    p_loop.add_argument("--max-hours", type=float, default=24.0)
    p_loop.add_argument("--max-iterations", type=int, default=0)
    p_loop.add_argument("--sleep-sec", type=float, default=5.0)
    p_loop.add_argument("--require-harness-close", action="store_true")
    p_loop.add_argument("--require-review-before-next", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "init":
        return init_state(args)
    if args.cmd == "status":
        state = load_state(args.state)
        print_status(state)
        return 0
    if args.cmd == "next":
        state = load_state(args.state)
        item = next_slice(state)
        if not item:
            print("next: none")
            return 1
        print(f"next: {item['id']} - {item['title']}")
        print("prompt: " + format_command("{prompt}", item, state))
        return 0
    if args.cmd == "run-once":
        return run_once(args)
    if args.cmd == "loop":
        return loop(args)
    if args.cmd == "preflight":
        return preflight(args)
    if args.cmd == "recover":
        return recover(args)
    if args.cmd == "review":
        return review(args)
    if args.cmd == "replan":
        return replan(args)
    raise SystemExit(f"unknown command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
