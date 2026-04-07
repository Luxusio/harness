#!/usr/bin/env python3
"""Plugin-local MCP server for the harness control plane.

This server exposes the LLM-facing harness workflow as structured MCP tools so
Claude does not need to assemble fragile Bash strings such as:

  python3 plugin/scripts/hctl.py start --task-dir ...

CLI scripts are retained as the backend for hooks, manual fallback, and tests,
but model-facing control operations should go through MCP tools.

stdlib only — no external dependencies.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"
PLUGIN_MANIFEST = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
MAX_TEXT_CHARS = 12000

sys.path.insert(0, str(SCRIPTS_DIR))

import calibration_miner  # type: ignore  # noqa: E402
import harness_api  # type: ignore  # noqa: E402
import observability  # type: ignore  # noqa: E402
from _lib import canonical_task_dir, canonical_task_id, find_repo_root, yaml_array, yaml_field  # type: ignore  # noqa: E402


def _server_version() -> str:
    try:
        with open(PLUGIN_MANIFEST, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        version = data.get("version")
        if isinstance(version, str) and version:
            return version
    except (OSError, json.JSONDecodeError):
        pass
    return "0.0.0"


SERVER_INFO = {
    "name": "harness",
    "title": "Harness Control Plane",
    "version": _server_version(),
}


def _cap_text(value: str | None, limit: int = MAX_TEXT_CHARS) -> str:
    text = value or ""
    if len(text) <= limit:
        return text
    head = limit // 2
    tail = limit - head
    return (
        f"{text[:head]}\n\n...[truncated {len(text) - limit} chars]...\n\n{text[-tail:]}"
    )


def _json_text(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True)


def _result(data: dict[str, Any], *, text: str | None = None) -> dict[str, Any]:
    return {
        "content": [
            {
                "type": "text",
                "text": text or _json_text(data),
            }
        ],
        "structuredContent": data,
    }


def _tool_error(message: str, *, data: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"error": message}
    if data:
        payload.update(data)
    return {
        "content": [{"type": "text", "text": _json_text(payload)}],
        "structuredContent": payload,
        "isError": True,
    }


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _resolve_script(script_name: str) -> str:
    return str(SCRIPTS_DIR / script_name)


def _run_script(
    script_name: str,
    args: list[str] | None = None,
    *,
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    argv = [sys.executable, _resolve_script(script_name)] + list(args or [])
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    result = subprocess.run(
        argv,
        capture_output=True,
        text=True,
        cwd=cwd or os.getcwd(),
        env=merged_env,
    )
    return {
        "ok": result.returncode == 0,
        "argv": argv,
        "exit_code": result.returncode,
        "stdout": _cap_text(result.stdout),
        "stderr": _cap_text(result.stderr),
    }


def _require_str(args: dict[str, Any], key: str) -> str:
    value = args.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    value = args.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    value = value.strip()
    return value or None


def _optional_bool(args: dict[str, Any], key: str, default: bool = False) -> bool:
    value = args.get(key, default)
    if isinstance(value, bool):
        return value
    raise ValueError(f"{key} must be a boolean")


def _load_context(task_dir: str) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    try:
        parsed = harness_api.get_task_context(task_dir)
        response = {
            "ok": True,
            "method": "direct",
            "exit_code": 0,
            "stdout": _cap_text(json.dumps(parsed, ensure_ascii=False)),
            "stderr": "",
        }
        return parsed, response
    except Exception as exc:
        direct_error = {
            "ok": False,
            "method": "direct",
            "exit_code": 1,
            "stdout": "",
            "stderr": _cap_text(str(exc)),
        }

    response = _run_script("hctl.py", ["context", "--task-dir", task_dir, "--json"])
    if not response["ok"]:
        response.setdefault("fallback_from", direct_error)
        return None, response
    try:
        parsed = json.loads(response["stdout"])
    except json.JSONDecodeError:
        parsed = None
    response.setdefault("fallback_from", direct_error)
    return parsed, response


def _minimal_fetch(response: dict[str, Any]) -> dict[str, Any]:
    payload = {
        "ok": bool(response.get("ok")),
        "method": str(response.get("method") or "cli"),
        "exit_code": int(response.get("exit_code") or 0),
    }
    fallback = response.get("fallback_from")
    if isinstance(fallback, dict):
        payload["fallback_from"] = {
            "ok": bool(fallback.get("ok")),
            "method": str(fallback.get("method") or "direct"),
            "exit_code": int(fallback.get("exit_code") or 0),
        }
    return payload


def _debug_response(response: dict[str, Any]) -> dict[str, Any]:
    return {
        key: response.get(key)
        for key in ("ok", "method", "exit_code", "argv", "stdout", "stderr", "fallback_from")
        if key in response
    }


def _state_file(task_dir: str) -> str:
    return os.path.join(task_dir, "TASK_STATE.yaml")


def _task_paths_summary(task_dir: str) -> dict[str, Any]:
    state_file = _state_file(task_dir)
    touched = list(yaml_array("touched_paths", state_file) or [])
    roots = list(yaml_array("roots_touched", state_file) or [])
    targets = list(yaml_array("verification_targets", state_file) or [])
    return {
        "path_count": len(touched),
        "top_paths": touched[-3:],
        "roots_touched": roots[:4],
        "verification_targets": targets[:3],
    }


def _result_summary_line(response: dict[str, Any]) -> str:
    lines = []
    for source in (response.get("stdout") or "", response.get("stderr") or ""):
        for raw in str(source).splitlines():
            line = raw.strip()
            if line.startswith("RESULT:"):
                lines.append(line)
    if lines:
        return lines[-1]
    evidence = []
    for source in (response.get("stdout") or "", response.get("stderr") or ""):
        for raw in str(source).splitlines():
            line = raw.strip()
            if line.startswith("[EVIDENCE]"):
                evidence.append(line)
    if evidence:
        return evidence[-1]
    tail = []
    for source in (response.get("stdout") or "", response.get("stderr") or ""):
        tail.extend([raw.strip() for raw in str(source).splitlines() if raw.strip()])
    return tail[-1] if tail else ""


def _task_relpath(task_dir: str, filename: str) -> str:
    task_name = Path(task_dir).name
    return f"doc/harness/tasks/{task_name}/{filename}" if filename else ""


def _artifact_if_exists(task_dir: str, filename: str) -> str:
    return _task_relpath(task_dir, filename) if os.path.isfile(os.path.join(task_dir, filename)) else ""


def _brief_context_payload(task_dir: str, context: dict[str, Any] | None, fetch: dict[str, Any], *, debug: bool = False) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_dir": task_dir,
        "task_context": context,
        "context_revision": (context or {}).get("context_revision"),
        "fetch": _minimal_fetch(fetch),
    }
    if debug:
        payload["debug"] = {"fetch": _debug_response(fetch)}
    return payload


def handle_task_start(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _optional_str(args, "task_dir")
    task_id = _optional_str(args, "task_id")
    slug = _optional_str(args, "slug")
    request_file = _optional_str(args, "request_file")
    debug = _optional_bool(args, "debug", default=False)
    if not task_dir and not task_id and not slug:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    repo_root = find_repo_root(os.getcwd())
    resolved_task_dir = task_dir
    if not resolved_task_dir:
        resolved_task_dir = canonical_task_dir(task_id=task_id, slug=slug, repo_root=repo_root)

    argv = ["start"]
    if task_dir:
        argv.extend(["--task-dir", task_dir])
    if task_id:
        argv.extend(["--task-id", task_id])
    if slug:
        argv.extend(["--slug", slug])
    if request_file:
        argv.extend(["--request-file", request_file])
    response = _run_script("hctl.py", argv)
    context, context_response = _load_context(resolved_task_dir)
    failure_payload = {
        "task_dir": resolved_task_dir,
        "task_id": canonical_task_id(task_id=task_id, slug=slug, task_dir=resolved_task_dir),
        "start": response,
        "task_context": context,
        "task_context_fetch": context_response,
    }
    if not response["ok"] or context is None:
        return _tool_error("task_start failed", data=failure_payload)

    payload = {
        "task_dir": resolved_task_dir,
        "task_id": canonical_task_id(task_id=task_id, slug=slug, task_dir=resolved_task_dir),
        "status": context.get("status"),
        "planning_mode": context.get("planning_mode"),
        "team_status": (context.get("team") or {}).get("status"),
        "source_write_allowed": context.get("source_write_allowed"),
        "context_revision": context.get("context_revision"),
        "task_context": context,
        "start": {"ok": True, "exit_code": int(response.get("exit_code") or 0)},
    }
    if debug:
        payload["debug"] = {
            "start": _debug_response(response),
            "task_context_fetch": _debug_response(context_response),
        }
    return _result(payload)


def handle_task_context(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    debug = _optional_bool(args, "debug", default=False)
    try:
        context = harness_api.get_task_context(
            task_dir,
            team_worker=team_worker,
            agent_name=agent_name,
        )
        response = {
            "ok": True,
            "method": "direct",
            "exit_code": 0,
            "stdout": _cap_text(json.dumps(context, ensure_ascii=False)),
            "stderr": "",
        }
    except Exception as exc:
        argv = ["context", "--task-dir", task_dir, "--json"]
        if team_worker:
            argv.extend(["--team-worker", team_worker])
        if agent_name:
            argv.extend(["--agent-name", agent_name])
        response = _run_script("hctl.py", argv)
        response.setdefault(
            "fallback_from",
            {
                "ok": False,
                "method": "direct",
                "exit_code": 1,
                "stdout": "",
                "stderr": _cap_text(str(exc)),
            },
        )
        if response["ok"]:
            try:
                context = json.loads(response["stdout"])
            except json.JSONDecodeError:
                context = None
        else:
            context = None
    failure_payload = {"task_dir": task_dir, "task_context": context, "fetch": response}
    if context is None:
        return _tool_error("task_context failed", data=failure_payload)
    return _result(_brief_context_payload(task_dir, context, response, debug=debug))


def handle_team_bootstrap(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    write_files = _optional_bool(args, "write_files", default=False)
    argv = ["team-bootstrap", "--task-dir", task_dir, "--json"]
    if write_files:
        argv.append("--write-files")
    response = _run_script("hctl.py", argv)
    try:
        bootstrap = json.loads(response["stdout"])
    except json.JSONDecodeError:
        bootstrap = None
    payload = {
        "task_dir": task_dir,
        "team_bootstrap": bootstrap,
        "fetch": response,
    }
    if bootstrap is None or not response["ok"]:
        return _tool_error("team_bootstrap failed", data=payload)
    return _result(payload)


def handle_team_dispatch(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    write_files = _optional_bool(args, "write_files", default=False)
    argv = ["team-dispatch", "--task-dir", task_dir, "--json"]
    if write_files:
        argv.append("--write-files")
    response = _run_script("hctl.py", argv)
    try:
        dispatch = json.loads(response["stdout"])
    except json.JSONDecodeError:
        dispatch = None
    payload = {
        "task_dir": task_dir,
        "team_dispatch": dispatch,
        "fetch": response,
    }
    if dispatch is None or not response["ok"]:
        return _tool_error("team_dispatch failed", data=payload)
    return _result(payload)


def handle_team_launch(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    write_files = _optional_bool(args, "write_files", default=False)
    execute = _optional_bool(args, "execute", default=False)
    no_auto_refresh = _optional_bool(args, "no_auto_refresh", default=False)
    target = _optional_str(args, "target") or "auto"
    argv = ["team-launch", "--task-dir", task_dir, "--json", "--target", target]
    if write_files or execute:
        argv.append("--write-files")
    if execute:
        argv.append("--execute")
    if no_auto_refresh:
        argv.append("--no-auto-refresh")
    response = _run_script("hctl.py", argv)
    try:
        launch = json.loads(response["stdout"])
    except json.JSONDecodeError:
        launch = None
    payload = {
        "task_dir": task_dir,
        "team_launch": launch,
        "fetch": response,
    }
    if launch is None or not response["ok"]:
        return _tool_error("team_launch failed", data=payload)
    return _result(payload)


def handle_team_relaunch(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    write_files = _optional_bool(args, "write_files", default=False)
    execute = _optional_bool(args, "execute", default=False)
    no_auto_refresh = _optional_bool(args, "no_auto_refresh", default=False)
    worker = _optional_str(args, "worker")
    phase = _optional_str(args, "phase") or "auto"
    argv = ["team-relaunch", "--task-dir", task_dir, "--json", "--phase", phase]
    if worker:
        argv.extend(["--worker", worker])
    if write_files or execute:
        argv.append("--write-files")
    if execute:
        argv.append("--execute")
    if no_auto_refresh:
        argv.append("--no-auto-refresh")
    response = _run_script("hctl.py", argv)
    try:
        relaunch = json.loads(response["stdout"])
    except json.JSONDecodeError:
        relaunch = None
    payload = {
        "task_dir": task_dir,
        "team_relaunch": relaunch,
        "fetch": response,
    }
    if relaunch is None or not response["ok"]:
        return _tool_error("team_relaunch failed", data=payload)
    return _result(payload)


def handle_task_update_from_git_diff(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    debug = _optional_bool(args, "debug", default=False)
    response = _run_script(
        "hctl.py",
        ["update", "--task-dir", task_dir, "--from-git-diff"],
    )
    failure_payload = {"task_dir": task_dir, "update": response}
    if not response["ok"]:
        return _tool_error("task_update_from_git_diff failed", data=failure_payload)
    summary = _task_paths_summary(task_dir)
    changed = "No changed files detected from git diff" not in str(response.get("stdout") or "")
    payload = {"task_dir": task_dir, "changed": changed, **summary}
    if debug:
        payload["debug"] = {"update": _debug_response(response)}
    return _result(payload)


def handle_task_update_paths(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    debug = _optional_bool(args, "debug", default=False)
    touched_paths = args.get("touched_paths") or []
    roots_touched = args.get("roots_touched") or []
    verification_targets = args.get("verification_targets") or []

    for key, value in (
        ("touched_paths", touched_paths),
        ("roots_touched", roots_touched),
        ("verification_targets", verification_targets),
    ):
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"{key} must be an array of strings")

    argv = ["update", "--task-dir", task_dir]
    for item in touched_paths:
        argv.extend(["--touched-path", item])
    for item in roots_touched:
        argv.extend(["--root-touched", item])
    for item in verification_targets:
        argv.extend(["--verification-target", item])

    response = _run_script("hctl.py", argv)
    failure_payload = {
        "task_dir": task_dir,
        "touched_paths": touched_paths,
        "roots_touched": roots_touched,
        "verification_targets": verification_targets,
        "update": response,
    }
    if not response["ok"]:
        return _tool_error("task_update_paths failed", data=failure_payload)
    payload = {"task_dir": task_dir, "changed": True, **_task_paths_summary(task_dir)}
    if debug:
        payload["debug"] = {"update": _debug_response(response)}
    return _result(payload)


def handle_record_agent_run(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    agent_name = _require_str(args, "agent_name")
    count = args.get("count", 1)
    if not isinstance(count, int) or count < 1:
        raise ValueError("count must be a positive integer")
    observed_at = _optional_str(args, "observed_at")
    argv = ["record-agent-run", "--task-dir", task_dir, "--agent-name", agent_name, "--count", str(count), "--json"]
    if observed_at:
        argv.extend(["--observed-at", observed_at])
    response = _run_script("hctl.py", argv)
    try:
        recorded = json.loads(response["stdout"])
    except json.JSONDecodeError:
        recorded = None
    payload = {"task_dir": task_dir, "agent_name": agent_name, "record_agent_run": recorded, "record": response}
    if recorded is None or not response["ok"]:
        return _tool_error("record_agent_run failed", data=payload)
    return _result(payload)


# Coordinator-settable fields allowlist
COORDINATOR_SETTABLE_FIELDS = frozenset({
    "maintenance_task",
    "lane",
    "mutates_repo",
    "doc_sync_required",
    "qa_required",
    "browser_required",
    "risk_level",
    "parallelism",
    "doc_sync_expected",
})

# Fields that must go through dedicated tools
BLOCKED_FIELDS_REASONS = {
    "plan_verdict": "use write_critic_plan",
    "runtime_verdict": "use write_critic_runtime",
    "document_verdict": "use write_critic_document",
    "runtime_verdict_freshness": "use write_critic_runtime",
    "document_verdict_freshness": "use write_critic_document",
    "status": "use task_close",
    "state_revision": "internal — managed by harness",
    "parent_revision": "internal — managed by harness",
    "schema_version": "internal — managed by harness",
    "touched_paths": "use task_update_paths",
    "roots_touched": "use task_update_paths",
    "verification_targets": "use task_update_paths",
}


def handle_task_set_fields(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    fields = args.get("fields")
    if not isinstance(fields, dict) or not fields:
        raise ValueError("fields must be a non-empty object")
    debug = _optional_bool(args, "debug", default=False)

    updated = {}
    rejected = {}

    for key, value in fields.items():
        # Block agent_run_* prefix
        if key.startswith("agent_run_"):
            rejected[key] = "use record_agent_run"
            continue
        # Block known protected fields
        if key in BLOCKED_FIELDS_REASONS:
            rejected[key] = BLOCKED_FIELDS_REASONS[key]
            continue
        # Only allow allowlisted fields
        if key not in COORDINATOR_SETTABLE_FIELDS:
            rejected[key] = f"not in coordinator-settable allowlist; allowed: {sorted(COORDINATOR_SETTABLE_FIELDS)}"
            continue

        # Coerce value
        coerced: Any
        if isinstance(value, bool):
            coerced = value
        elif isinstance(value, str) and value.lower() in ("true", "false"):
            coerced = value.lower() == "true"
        elif isinstance(value, (int, float)):
            coerced = value
        else:
            coerced = str(value)

        from _lib import set_task_state_field as _set_field
        ok = _set_field(task_dir, key, coerced)
        if ok:
            updated[key] = coerced
        else:
            rejected[key] = "set_task_state_field returned False (task_dir missing or IO error)"

    if not updated and rejected:
        payload = {"task_dir": task_dir, "updated": updated, "rejected": rejected}
        return _tool_error("task_set_fields: no fields were updated", data=payload)

    state_file = _state_file(task_dir)
    revision = yaml_field("state_revision", state_file) or "unknown"

    payload = {
        "task_dir": task_dir,
        "updated": updated,
        "rejected": rejected,
        "state_revision": revision,
        "updated_at": _now_iso(),
    }
    if debug:
        payload["debug"] = {"fields_input": fields}
    return _result(payload)


def handle_task_verify(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    debug = _optional_bool(args, "debug", default=False)
    response = _run_script("hctl.py", ["verify", "--task-dir", task_dir])
    context, context_fetch = _load_context(task_dir)
    failure_payload = {"task_dir": task_dir, "verify": response, "task_context": context, "task_context_fetch": context_fetch}
    if not response["ok"]:
        return _tool_error("task_verify failed", data=failure_payload)
    state_file = _state_file(task_dir)
    payload = {
        "task_dir": task_dir,
        "ok": True,
        "summary": _result_summary_line(response),
        "next_action": (context or {}).get("next_action", ""),
        "missing_for_close": list((context or {}).get("missing_for_close") or [])[:4],
        "runtime_verdict": yaml_field("runtime_verdict", state_file) or "pending",
        "runtime_freshness": yaml_field("runtime_verdict_freshness", state_file) or "",
        "report_path": _artifact_if_exists(task_dir, "CRITIC__runtime.md"),
    }
    if debug:
        payload["debug"] = {"verify": _debug_response(response), "task_context_fetch": _debug_response(context_fetch)}
    return _result(payload)


def handle_task_close(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    debug = _optional_bool(args, "debug", default=False)
    response = _run_script("hctl.py", ["close", "--task-dir", task_dir])
    context, context_fetch = _load_context(task_dir)
    state_file = _state_file(task_dir)
    closed = bool(response.get("ok")) or (yaml_field("status", state_file) or "").strip().lower() in {"closed", "archived", "stale"}
    failure_payload = {"task_dir": task_dir, "close": response, "task_context": context, "task_context_fetch": context_fetch}
    if not response["ok"]:
        return _tool_error("task_close failed", data=failure_payload)
    payload = {
        "task_dir": task_dir,
        "closed": closed,
        "status": yaml_field("status", state_file) or "unknown",
        "summary": _result_summary_line(response) or ("close gate PASSED" if closed else "close gate finished"),
        "missing_for_close": list((context or {}).get("missing_for_close") or [])[:4],
        "next_action": (context or {}).get("next_action", ""),
        "gate_artifact": _artifact_if_exists(task_dir, "HANDOFF.md"),
    }
    if debug:
        payload["debug"] = {"close": _debug_response(response), "task_context_fetch": _debug_response(context_fetch)}
    return _result(payload)


def handle_verify_run(args: dict[str, Any]) -> dict[str, Any]:
    mode = args.get("mode", "suite")
    if mode not in {"suite", "smoke", "healthcheck", "browser", "persistence"}:
        raise ValueError("mode must be one of suite|smoke|healthcheck|browser|persistence")
    response = _run_script("verify.py", [mode])
    payload = {"mode": mode, "verify": response}
    if not response["ok"]:
        return _tool_error("verify_run failed", data=payload)
    return _result(payload)


def _artifact_response(
    subcommand: str,
    args: list[str],
    *,
    artifact: str,
    team_worker: str | None = None,
    agent_name: str | None = None,
) -> dict[str, Any]:
    env = {"HARNESS_SKIP_PREWRITE": "1"}
    if team_worker:
        env["HARNESS_TEAM_WORKER"] = team_worker
    if agent_name:
        env["CLAUDE_AGENT_NAME"] = agent_name
    response = _run_script(
        "write_artifact.py",
        [subcommand] + args,
        env=env,
    )
    task_dir = args[args.index("--task-dir") + 1] if "--task-dir" in args else None
    artifact_write = None
    if response["ok"]:
        try:
            artifact_write = json.loads(response["stdout"])
        except json.JSONDecodeError:
            artifact_write = None
    payload = {
        "artifact": artifact,
        "subcommand": subcommand,
        "task_dir": task_dir,
        "artifact_write": artifact_write,
        "write": response,
    }
    if not response["ok"]:
        return _tool_error(f"{subcommand} artifact write failed", data=payload)
    return _result(payload)


def handle_write_critic_runtime(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    verdict = _require_str(args, "verdict")
    execution_mode = _require_str(args, "execution_mode")
    summary = _require_str(args, "summary")
    transcript = _require_str(args, "transcript")
    checks = _optional_str(args, "checks")
    verdict_reason = _optional_str(args, "verdict_reason")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = [
        "--task-dir", task_dir,
        "--verdict", verdict,
        "--execution-mode", execution_mode,
        "--summary", summary,
        "--transcript", transcript,
    ]
    if checks:
        argv.extend(["--checks", checks])
    if verdict_reason:
        argv.extend(["--verdict-reason", verdict_reason])
    return _artifact_response(
        "critic-runtime",
        argv,
        artifact="CRITIC__runtime.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_write_critic_plan(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    verdict = _require_str(args, "verdict")
    summary = _require_str(args, "summary")
    checks = _optional_str(args, "checks")
    issues = _optional_str(args, "issues")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = ["--task-dir", task_dir, "--verdict", verdict, "--summary", summary]
    if checks:
        argv.extend(["--checks", checks])
    if issues:
        argv.extend(["--issues", issues])
    return _artifact_response(
        "critic-plan",
        argv,
        artifact="CRITIC__plan.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_write_critic_document(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    verdict = _require_str(args, "verdict")
    summary = _require_str(args, "summary")
    checks = _optional_str(args, "checks")
    issues = _optional_str(args, "issues")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = ["--task-dir", task_dir, "--verdict", verdict, "--summary", summary]
    if checks:
        argv.extend(["--checks", checks])
    if issues:
        argv.extend(["--issues", issues])
    return _artifact_response(
        "critic-document",
        argv,
        artifact="CRITIC__document.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_write_critic_intent(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    verdict = _require_str(args, "verdict")
    summary = _require_str(args, "summary")
    checks = _optional_str(args, "checks")
    issues = _optional_str(args, "issues")
    blocker_ids = _optional_str(args, "blocker_ids")
    opportunity_ids = _optional_str(args, "opportunity_ids")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = ["--task-dir", task_dir, "--verdict", verdict, "--summary", summary]
    if checks:
        argv.extend(["--checks", checks])
    if issues:
        argv.extend(["--issues", issues])
    if blocker_ids:
        argv.extend(["--blocker-ids", blocker_ids])
    if opportunity_ids:
        argv.extend(["--opportunity-ids", opportunity_ids])
    return _artifact_response(
        "critic-intent",
        argv,
        artifact="CRITIC__intent.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_write_handoff(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    verify_cmd = _require_str(args, "verify_cmd")
    what_changed = _require_str(args, "what_changed")
    expected_output = _optional_str(args, "expected_output")
    do_not_regress = _optional_str(args, "do_not_regress")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = [
        "--task-dir", task_dir,
        "--verify-cmd", verify_cmd,
        "--what-changed", what_changed,
    ]
    if expected_output:
        argv.extend(["--expected-output", expected_output])
    if do_not_regress:
        argv.extend(["--do-not-regress", do_not_regress])
    return _artifact_response(
        "handoff",
        argv,
        artifact="HANDOFF.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_write_doc_sync(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _require_str(args, "task_dir")
    what_changed = _require_str(args, "what_changed")
    new_files = _optional_str(args, "new_files")
    updated_files = _optional_str(args, "updated_files")
    deleted_files = _optional_str(args, "deleted_files")
    notes = _optional_str(args, "notes")
    team_worker = _optional_str(args, "team_worker")
    agent_name = _optional_str(args, "agent_name")
    argv = ["--task-dir", task_dir, "--what-changed", what_changed]
    if new_files:
        argv.extend(["--new-files", new_files])
    if updated_files:
        argv.extend(["--updated-files", updated_files])
    if deleted_files:
        argv.extend(["--deleted-files", deleted_files])
    if notes:
        argv.extend(["--notes", notes])
    return _artifact_response(
        "doc-sync",
        argv,
        artifact="DOC_SYNC.md",
        team_worker=team_worker,
        agent_name=agent_name,
    )


def handle_calibration_mine(args: dict[str, Any]) -> dict[str, Any]:
    tasks_dir = _optional_str(args, "tasks_dir")
    output_dir = _optional_str(args, "output_dir")
    dry_run = _optional_bool(args, "dry_run", default=False)
    cases = calibration_miner.run_mining(
        tasks_dir=tasks_dir,
        output_dir=output_dir,
        dry_run=dry_run,
    )
    payload = {
        "dry_run": dry_run,
        "tasks_dir": tasks_dir,
        "output_dir": output_dir,
        "count": len(cases),
        "cases": cases,
    }
    return _result(payload)


def handle_observability_detect(args: dict[str, Any]) -> dict[str, Any]:
    del args
    payload = observability.detect()
    return _result(payload)


def handle_observability_status(args: dict[str, Any]) -> dict[str, Any]:
    del args
    payload = observability.status()
    return _result(payload)


def handle_observability_hint(args: dict[str, Any]) -> dict[str, Any]:
    context = _optional_str(args, "context")
    task_dir = _optional_str(args, "task_dir")
    force_task_overlay = _optional_bool(args, "force_task_overlay", default=False)
    payload = observability.hint(
        context,
        task_dir=task_dir,
        force_task_overlay=force_task_overlay,
    )
    return _result(payload)


def handle_observability_policy(args: dict[str, Any]) -> dict[str, Any]:
    task_dir = _optional_str(args, "task_dir")
    payload = observability.evaluate_policy(task_dir)
    return _result(payload)


TOOL_DEFS: list[dict[str, Any]] = [
    {
        "name": "task_start",
        "title": "Compile routing for a task",
        "description": "Run the harness task start step and return the fresh task pack. New or resumed tasks should usually stop here instead of calling task_context again immediately.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Canonical task directory (doc/harness/tasks/TASK__<id>)"},
                "task_id": {"type": "string", "description": "Canonical task id or slug to bootstrap (TASK__ prefix optional)"},
                "slug": {"type": "string", "description": "Task slug to bootstrap under doc/harness/tasks/TASK__<slug>"},
                "request_file": {"type": "string", "description": "Optional request file path"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "additionalProperties": False,
        },
        "handler": handle_task_start,
    },
    {
        "name": "task_context",
        "title": "Read the canonical task pack",
        "description": "Return the compact machine-readable task context for routing and workflow state. Use it for refresh, personalization, or when hook-provided state is stale.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "team_worker": {"type": "string", "description": "Optional team worker id for personalized context"},
                "agent_name": {"type": "string", "description": "Optional agent name override for personalized context"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_task_context,
    },
    {
        "name": "team_bootstrap",
        "title": "Generate worker bootstrap specs",
        "description": "Return per-worker bootstrap briefs and optional env/brief files for a ready team task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "write_files": {"type": "boolean", "description": "When true, write team/bootstrap briefs + env files into the task directory"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_team_bootstrap,
    },
    {
        "name": "team_dispatch",
        "title": "Generate provider launch artifacts",
        "description": "Return provider-ready launch prompts, worker phase prompts, and optional run helpers for a ready team task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "write_files": {"type": "boolean", "description": "When true, write provider prompts + run helpers into the task directory"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_team_dispatch,
    },
    {
        "name": "team_launch",
        "title": "Prepare or execute the team launch entrypoint",
        "description": "Auto-refresh stale bootstrap/dispatch artifacts if needed, then return or execute the default provider/implementer launch plan for a ready team task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "write_files": {"type": "boolean", "description": "When true, write the launch manifest into the task directory"},
                "execute": {"type": "boolean", "description": "When true, spawn the launcher in detached mode when supported"},
                "no_auto_refresh": {"type": "boolean", "description": "When true, require existing bootstrap/dispatch artifacts instead of auto-refreshing them"},
                "target": {"type": "string", "enum": ["auto", "provider", "implementers"], "description": "Which launcher to prepare or execute"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_team_launch,
    },
    {
        "name": "team_relaunch",
        "title": "Prepare or execute a worker/phase relaunch",
        "description": "Auto-refresh stale bootstrap/dispatch artifacts if needed, then return or execute the best worker/phase relaunch plan for the current team recovery state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "write_files": {"type": "boolean", "description": "When true, write the relaunch manifest into the task directory"},
                "execute": {"type": "boolean", "description": "When true, spawn the selected worker phase in detached mode when supported"},
                "no_auto_refresh": {"type": "boolean", "description": "When true, require existing bootstrap/dispatch artifacts instead of auto-refreshing them"},
                "worker": {"type": "string", "description": "Optional worker id to relaunch"},
                "phase": {"type": "string", "description": "Optional phase override: auto|implement|synthesis|final_runtime_verification|documentation_sync|documentation_review|handoff_refresh"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_team_relaunch,
    },
    {
        "name": "task_update_from_git_diff",
        "title": "Sync changed paths into task state",
        "description": "Manual or fallback path sync: populate touched_paths, roots_touched, and verification_targets from git diff.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_task_update_from_git_diff,
    },
    {
        "name": "task_update_paths",
        "title": "Manually sync changed paths into task state",
        "description": "Merge explicit touched paths, roots, and verification targets into TASK_STATE.yaml without relying on git.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "touched_paths": {"type": "array", "items": {"type": "string"}, "description": "Changed repo-relative paths to merge into touched_paths"},
                "roots_touched": {"type": "array", "items": {"type": "string"}, "description": "Changed roots to merge into roots_touched"},
                "verification_targets": {"type": "array", "items": {"type": "string"}, "description": "Runtime paths to merge into verification_targets"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_task_update_paths,
    },
    {
        "name": "record_agent_run",
        "title": "Explicitly record an agent run",
        "description": "Persist a worker/critic run into TASK_STATE.yaml when hook-based provenance delivery is unavailable or needs manual recovery.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "agent_name": {"type": "string", "description": "Canonical agent name: developer|writer|critic-plan|critic-runtime|critic-document"},
                "count": {"type": "integer", "minimum": 1, "description": "Optional increment amount (default: 1)"},
                "observed_at": {"type": "string", "description": "Optional ISO timestamp to store as the latest observed run time"}
            },
            "required": ["task_dir", "agent_name"],
            "additionalProperties": False,
        },
        "handler": handle_record_agent_run,
    },
    {
        "name": "task_set_fields",
        "title": "Set coordinator-settable fields in TASK_STATE",
        "description": "Update coordinator-settable fields in TASK_STATE.yaml (e.g. maintenance_task, lane, mutates_repo). Protected fields like verdicts and status are rejected. Use this instead of directly editing TASK_STATE.yaml.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "fields": {
                    "type": "object",
                    "description": "Key/value pairs to set. Allowed: maintenance_task, lane, mutates_repo, doc_sync_required, qa_required, browser_required, risk_level, parallelism, doc_sync_expected.",
                    "additionalProperties": True,
                },
                "debug": {"type": "boolean", "description": "When true, include raw debug payloads"},
            },
            "required": ["task_dir", "fields"],
            "additionalProperties": False,
        },
        "handler": handle_task_set_fields,
    },
    {
        "name": "task_verify",
        "title": "Run the task verification entry point",
        "description": "Auto-sync changed paths from git diff, then run the harness verification suite for a task-scoped workflow.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_task_verify,
    },
    {
        "name": "task_close",
        "title": "Run the completion gate",
        "description": "Auto-sync changed paths from git diff, then attempt to close the task through the harness completion gate.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Path to the task directory"},
                "debug": {"type": "boolean", "description": "When true, include raw CLI/debug payloads"},
            },
            "required": ["task_dir"],
            "additionalProperties": False,
        },
        "handler": handle_task_close,
    },
    {
        "name": "verify_run",
        "title": "Run repo verification",
        "description": "Run verify.py in suite, smoke, healthcheck, browser, or persistence mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["suite", "smoke", "healthcheck", "browser", "persistence"],
                    "description": "Verification mode to run",
                }
            },
            "additionalProperties": False,
        },
        "handler": handle_verify_run,
    },
    {
        "name": "write_critic_runtime",
        "title": "Write runtime critic artifact",
        "description": "Write CRITIC__runtime.md and update task state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]},
                "execution_mode": {"type": "string"},
                "summary": {"type": "string"},
                "transcript": {"type": "string"},
                "checks": {"type": "string"},
                "verdict_reason": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id for team-owned artifact enforcement"},
                "agent_name": {"type": "string", "description": "Optional agent name to forward into write_artifact.py"},
            },
            "required": ["task_dir", "verdict", "execution_mode", "summary", "transcript"],
            "additionalProperties": False,
        },
        "handler": handle_write_critic_runtime,
    },
    {
        "name": "write_critic_plan",
        "title": "Write plan critic artifact",
        "description": "Write CRITIC__plan.md and update task state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "summary": {"type": "string"},
                "checks": {"type": "string"},
                "issues": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id for team-owned artifact enforcement"},
                "agent_name": {"type": "string", "description": "Optional agent name to forward into write_artifact.py"},
            },
            "required": ["task_dir", "verdict", "summary"],
            "additionalProperties": False,
        },
        "handler": handle_write_critic_plan,
    },
    {
        "name": "write_critic_document",
        "title": "Write document critic artifact",
        "description": "Write CRITIC__document.md and update task state.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "summary": {"type": "string"},
                "checks": {"type": "string"},
                "issues": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id for team-owned artifact enforcement"},
                "agent_name": {"type": "string", "description": "Optional agent name to forward into write_artifact.py"},
            },
            "required": ["task_dir", "verdict", "summary"],
            "additionalProperties": False,
        },
        "handler": handle_write_critic_document,
    },
    {
        "name": "write_critic_intent",
        "title": "Write intent critic artifact",
        "description": "Write CRITIC__intent.md and update intent_verdict in TASK_STATE.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
                "summary": {"type": "string"},
                "checks": {"type": "string"},
                "issues": {"type": "string"},
                "blocker_ids": {"type": "string"},
                "opportunity_ids": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id"},
                "agent_name": {"type": "string", "description": "Optional agent name"},
            },
            "required": ["task_dir", "verdict", "summary"],
            "additionalProperties": False,
        },
        "handler": handle_write_critic_intent,
    },
    {
        "name": "write_handoff",
        "title": "Write developer handoff",
        "description": "Write HANDOFF.md for the current task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "verify_cmd": {"type": "string"},
                "what_changed": {"type": "string"},
                "expected_output": {"type": "string"},
                "do_not_regress": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id for team-owned artifact enforcement"},
                "agent_name": {"type": "string", "description": "Optional agent name to forward into write_artifact.py"},
            },
            "required": ["task_dir", "verify_cmd", "what_changed"],
            "additionalProperties": False,
        },
        "handler": handle_write_handoff,
    },
    {
        "name": "write_doc_sync",
        "title": "Write DOC_SYNC artifact",
        "description": "Write DOC_SYNC.md for the current task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string"},
                "what_changed": {"type": "string"},
                "new_files": {"type": "string"},
                "updated_files": {"type": "string"},
                "deleted_files": {"type": "string"},
                "notes": {"type": "string"},
                "team_worker": {"type": "string", "description": "Optional team worker id for team-owned artifact enforcement"},
                "agent_name": {"type": "string", "description": "Optional agent name to forward into write_artifact.py"},
            },
            "required": ["task_dir", "what_changed"],
            "additionalProperties": False,
        },
        "handler": handle_write_doc_sync,
    },
    {
        "name": "calibration_mine",
        "title": "Generate or preview local calibration cases",
        "description": "Run the calibration miner with optional dry-run mode.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "dry_run": {"type": "boolean", "description": "Preview without writing files"},
                "tasks_dir": {"type": "string", "description": "Optional override for the task directory"},
                "output_dir": {"type": "string", "description": "Optional override for the calibration output directory"},
            },
            "additionalProperties": False,
        },
        "handler": handle_calibration_mine,
    },
    {
        "name": "observability_detect",
        "title": "Detect observability readiness",
        "description": "Check whether the repo and environment are ready for the local observability stack.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": handle_observability_detect,
    },
    {
        "name": "observability_status",
        "title": "Check observability stack status",
        "description": "Inspect whether the local observability stack appears to be running.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "handler": handle_observability_status,
    },
    {
        "name": "observability_hint",
        "title": "Get observability investigation hints",
        "description": "Return Grafana/Loki/Tempo/Prometheus hints for a runtime investigation context.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "context": {"type": "string", "description": "Short context such as intermittent latency or pool exhaustion"},
                "task_dir": {"type": "string", "description": "Optional task directory for overlay detection"},
                "force_task_overlay": {"type": "boolean", "description": "Treat task overlay as active even when the global profile is off"},
            },
            "additionalProperties": False,
        },
        "handler": handle_observability_hint,
    },
    {
        "name": "observability_policy",
        "title": "Evaluate observability activation policy",
        "description": "Decide whether the observability review overlay should be active for a task.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_dir": {"type": "string", "description": "Task directory to evaluate"},
            },
            "additionalProperties": False,
        },
        "handler": handle_observability_policy,
    },
]

TOOLS = {tool["name"]: tool for tool in TOOL_DEFS}


def list_tools() -> list[dict[str, Any]]:
    return [
        {k: v for k, v in tool.items() if k != "handler"}
        for tool in TOOL_DEFS
    ]


def call_tool(name: str, args: dict[str, Any] | None) -> dict[str, Any]:
    if name not in TOOLS:
        return _tool_error(f"Unknown tool: {name}")
    handler: Callable[[dict[str, Any]], dict[str, Any]] = TOOLS[name]["handler"]
    try:
        return handler(args or {})
    except ValueError as exc:
        return _tool_error(str(exc))
    except Exception as exc:  # pragma: no cover - defensive server boundary
        return _tool_error(f"{name} failed: {exc}")


class McpServer:
    def __init__(self) -> None:
        self.initialized = False
        self.protocol_version = SUPPORTED_PROTOCOLS[0]

    def _read_message(self) -> dict[str, Any] | None:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        line = line.strip()
        if not line:
            return None
        return json.loads(line.decode("utf-8"))

    def _write_message(self, payload: dict[str, Any]) -> None:
        line = (json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n").encode("utf-8")
        sys.stdout.buffer.write(line)
        sys.stdout.buffer.flush()

    def _reply(self, msg_id: Any, result: Any) -> None:
        self._write_message({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id: Any, code: int, message: str) -> None:
        self._write_message(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "error": {"code": code, "message": message},
            }
        )

    def handle_request(self, request: dict[str, Any]) -> None:
        method = request.get("method")
        msg_id = request.get("id")
        params = request.get("params") or {}

        if method == "initialize":
            requested = params.get("protocolVersion")
            if isinstance(requested, str) and requested in SUPPORTED_PROTOCOLS:
                self.protocol_version = requested
            else:
                self.protocol_version = SUPPORTED_PROTOCOLS[0]
            self._reply(
                msg_id,
                {
                    "protocolVersion": self.protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": SERVER_INFO,
                    "instructions": (
                        "Use these tools instead of assembling Bash strings for harness control-plane "
                        "operations such as hctl, verify.py, write_artifact.py, calibration_miner.py, "
                        "and observability.py."
                    ),
                },
            )
            return

        if method == "notifications/initialized":
            self.initialized = True
            return

        if method == "ping":
            self._reply(msg_id, {})
            return

        if method == "tools/list":
            self._reply(msg_id, {"tools": list_tools()})
            return

        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                self._error(msg_id, -32602, "Tool name must be a string")
                return
            if not isinstance(arguments, dict):
                self._error(msg_id, -32602, "Tool arguments must be an object")
                return
            self._reply(msg_id, call_tool(name, arguments))
            return

        self._error(msg_id, -32601, f"Method not found: {method}")

    def serve_forever(self) -> None:
        while True:
            request = self._read_message()
            if request is None:
                return
            self.handle_request(request)


def main() -> int:
    server = McpServer()
    server.serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
