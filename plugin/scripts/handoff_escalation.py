#!/usr/bin/env python3
"""Handoff escalation helper — generates SESSION_HANDOFF.json for failing/complex tasks.

Imported by post_compact_sync.py and session_end_sync.py.
No pip packages — stdlib only.
"""

import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    is_doc_path,
    now_iso,
    select_team_relaunch_target,
    team_artifact_status,
    team_bootstrap_status,
    team_dispatch_status,
    team_launch_status,
    team_worker_summary_relpath,
    yaml_array,
    yaml_field,
)

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _count_runtime_fails(task_dir):
    """Count runtime FAIL verdicts from CRITIC__runtime.md history.

    We check CRITIC__runtime.md for verdict: FAIL lines, and also
    read runtime_verdict_fail_count from TASK_STATE.yaml if present.
    """
    count = 0

    # Check TASK_STATE.yaml for a stored fail count
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    stored = yaml_field("runtime_verdict_fail_count", state_file)
    if stored:
        try:
            return int(stored)
        except ValueError:
            pass

    # Fall back: scan CRITIC__runtime.md for FAIL verdicts
    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if not os.path.isfile(critic_path):
        return 0
    try:
        with open(critic_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        # Count lines like "verdict: FAIL" (case-insensitive)
        for line in content.splitlines():
            m = re.match(r"^\s*verdict\s*:\s*FAIL\s*$", line, re.IGNORECASE)
            if m:
                count += 1
    except OSError:
        pass

    return count


def _count_criterion_reopens(task_dir):
    """Return max reopen_count across all criteria in CHECKS.yaml.

    Returns 0 if CHECKS.yaml is absent (graceful degradation).
    """
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return 0

    max_reopen = 0
    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        for line in content.splitlines():
            m = re.match(r"^\s*reopen_count\s*:\s*(\d+)", line)
            if m:
                val = int(m.group(1))
                if val > max_reopen:
                    max_reopen = val
    except (OSError, ValueError):
        pass

    return max_reopen


def _compaction_occurred(task_dir):
    """Heuristic: check if a compaction marker is present.

    post_compact_sync.py is called after compaction — the caller passes
    this flag explicitly. This helper reads a marker file if present.
    """
    marker = os.path.join(task_dir, ".compaction_occurred")
    return os.path.isfile(marker)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def should_create_handoff(task_dir, compaction_just_occurred=False):
    """Check if any trigger condition is met for this task.

    Returns the trigger name (str) if triggered, or None.

    Triggers:
      1. runtime_verdict FAIL count >= 2
      2. Any criterion with reopen_count >= 2 (from CHECKS.yaml if present)
      3. execution_mode == "sprinted" AND compaction occurred
      4. status == "blocked_env" recovery re-entry
      5. roots_touched grew significantly beyond plan estimate
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None

    status = yaml_field("status", state_file) or ""
    if status in ("closed", "archived", "stale"):
        return None

    # Trigger 1: runtime FAIL count >= 2
    fail_count = _count_runtime_fails(task_dir)
    if fail_count >= 2:
        return "runtime_fail_repeat"

    # Trigger 2: criterion reopen_count >= 2
    max_reopen = _count_criterion_reopens(task_dir)
    if max_reopen >= 2:
        return "criterion_reopen_repeat"

    # Trigger 3: sprinted + compaction
    execution_mode = yaml_field("execution_mode", state_file) or ""
    if execution_mode == "sprinted" and (
        compaction_just_occurred or _compaction_occurred(task_dir)
    ):
        return "sprinted_compaction"

    # Trigger 4: blocked_env recovery re-entry
    # Indicated by status == blocked_env OR by a previous blocked_env in state history
    if status == "blocked_env":
        return "blocked_env_reentry"
    prev_blocked = yaml_field("was_blocked_env", state_file) or ""
    if prev_blocked.lower() in ("true", "1", "yes"):
        return "blocked_env_reentry"

    # Trigger 5: roots_touched grew significantly beyond plan estimate
    roots_touched = yaml_array("roots_touched", state_file)
    roots_estimate_raw = yaml_field("roots_estimate", state_file) or "0"
    try:
        roots_estimate = int(roots_estimate_raw)
    except ValueError:
        roots_estimate = 0
    if roots_estimate > 0 and len(roots_touched) > roots_estimate + 1:
        return "roots_exceeded_estimate"

    return None


def generate_handoff(task_dir, trigger):
    """Generate SESSION_HANDOFF.json with structured recovery context.

    Reads TASK_STATE.yaml, CHECKS.yaml (if present), CRITIC files.
    Writes SESSION_HANDOFF.json into task_dir.
    Returns the handoff dict (for caller display), or None on error.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return None

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    roots_touched = yaml_array("roots_touched", state_file)
    touched_paths = yaml_array("touched_paths", state_file)
    team_recovery = _build_team_recovery(task_dir)

    # --- open_check_ids: from CHECKS.yaml criteria not yet PASS ---
    open_check_ids = []
    last_known_good_checks = []
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if os.path.isfile(checks_path):
        open_check_ids, last_known_good_checks = _parse_checks(checks_path)

    # --- last_fail_evidence_refs: CRITIC files with FAIL verdict ---
    last_fail_evidence_refs = _find_fail_critic_files(task_dir)

    # --- paths_in_focus: most recently failed paths from critic content ---
    paths_in_focus = _extract_paths_from_critics(task_dir, touched_paths)
    if team_recovery:
        pending_paths = [
            path
            for path in (team_recovery.get("pending_owned_paths") or [])
            if path not in paths_in_focus
        ]
        paths_in_focus = list(paths_in_focus) + pending_paths

    # --- do_not_regress: criteria that passed + stable endpoints ---
    do_not_regress = _build_do_not_regress(task_dir, last_known_good_checks)

    # --- next_step: derive from trigger ---
    next_step = _derive_next_step(trigger, task_dir, team_recovery=team_recovery)

    # --- files_to_read_first: essential recovery reading list ---
    files_to_read_first = _build_read_first_list(
        task_dir,
        last_fail_evidence_refs,
        team_recovery=team_recovery,
    )

    handoff = {
        "task_id": task_id,
        "trigger": trigger,
        "created_at": now_iso(),
        "open_check_ids": open_check_ids,
        "last_fail_evidence_refs": last_fail_evidence_refs,
        "last_known_good_checks": last_known_good_checks,
        "next_step": next_step,
        "roots_in_focus": roots_touched,
        "paths_in_focus": paths_in_focus,
        "do_not_regress": do_not_regress,
        "files_to_read_first": files_to_read_first,
    }
    if team_recovery:
        handoff["team_recovery"] = team_recovery

    # Write to task_dir/SESSION_HANDOFF.json
    handoff_path = os.path.join(task_dir, "SESSION_HANDOFF.json")
    try:
        with open(handoff_path, "w", encoding="utf-8") as fh:
            json.dump(handoff, fh, indent=2)
            fh.write("\n")
    except OSError as e:
        # Non-blocking — report but don't crash
        print(
            f"  [HANDOFF] Warning: could not write {handoff_path}: {e}", file=sys.stderr
        )
        return None

    return handoff


# ---------------------------------------------------------------------------
# Private helpers for generate_handoff
# ---------------------------------------------------------------------------


def _extract_worker_names(error_messages):
    """Return worker ids parsed from worker_summary_errors entries."""
    names = []
    for raw in error_messages or []:
        text = str(raw or "").strip()
        if not text:
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)\s*(?:\(|:|$)", text)
        if not match:
            continue
        worker = match.group(1).strip()
        if worker and worker not in names:
            names.append(worker)
    return names


def _build_team_recovery(task_dir):
    """Return team-specific recovery state for SESSION_HANDOFF.json."""
    team_state = team_artifact_status(task_dir)
    if team_state.get("orchestration_mode") != "team":
        return None
    bootstrap_state = team_bootstrap_status(task_dir, team_state=team_state)
    dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
    launch_state = team_launch_status(task_dir, team_state=team_state)
    relaunch_state = select_team_relaunch_target(task_dir, team_state=team_state)

    expected_workers = list(team_state.get("worker_summary_expected_workers") or [])
    missing_workers = list(team_state.get("worker_summary_missing_workers") or [])
    error_workers = _extract_worker_names(team_state.get("worker_summary_errors") or [])
    incomplete_workers = [
        worker for worker in error_workers if worker and worker not in missing_workers
    ]
    pending_workers = []
    for worker in expected_workers:
        if worker in missing_workers or worker in incomplete_workers:
            pending_workers.append(worker)
    ready_workers = [
        worker for worker in expected_workers if worker not in pending_workers
    ]
    has_worker_progress = bool(
        team_state.get("worker_summary_present_count")
        or team_state.get("worker_summary_ready")
    )
    synthesis_started = bool(
        team_state.get("synthesis_exists") or team_state.get("synthesis_ready")
    )

    phase = (
        team_state.get("derived_status") or team_state.get("current_status") or "n/a"
    )
    if not team_state.get("plan_ready"):
        phase = "plan"
    elif (
        not has_worker_progress
        and not synthesis_started
        and bootstrap_state.get("available")
        and (
            not bootstrap_state.get("generated")
            or bootstrap_state.get("refresh_needed")
        )
    ):
        phase = "bootstrap"
    elif (
        not has_worker_progress
        and not synthesis_started
        and dispatch_state.get("available")
        and (
            not dispatch_state.get("generated") or dispatch_state.get("refresh_needed")
        )
    ):
        phase = "dispatch"
    elif (
        not has_worker_progress
        and not synthesis_started
        and launch_state.get("available")
        and (not launch_state.get("generated") or launch_state.get("refresh_needed"))
    ):
        phase = "launch"
    elif team_state.get("worker_summary_required") and not team_state.get(
        "worker_summary_ready"
    ):
        phase = "worker_summaries"
    elif team_state.get("synthesis_ready") and team_state.get(
        "team_runtime_verification_needed"
    ):
        phase = "verification"
    elif team_state.get("team_documentation_needed"):
        phase = "documentation"
    elif team_state.get("synthesis_ready"):
        phase = "complete"
    elif team_state.get("synthesis_exists") or team_state.get("plan_ready"):
        phase = "synthesis"

    pending_artifacts = []
    if not team_state.get("plan_ready"):
        pending_artifacts.append("TEAM_PLAN.md")
    if (
        phase == "bootstrap"
        and bootstrap_state.get("available")
        and (
            not bootstrap_state.get("generated")
            or bootstrap_state.get("refresh_needed")
        )
    ):
        bootstrap_index = str(
            bootstrap_state.get("bootstrap_index") or "team/bootstrap/index.json"
        )
        if bootstrap_index not in pending_artifacts:
            pending_artifacts.append(bootstrap_index)
    if (
        phase == "dispatch"
        and dispatch_state.get("available")
        and (
            not dispatch_state.get("generated") or dispatch_state.get("refresh_needed")
        )
    ):
        dispatch_index = str(
            dispatch_state.get("dispatch_index")
            or "team/bootstrap/provider/dispatch.json"
        )
        if dispatch_index not in pending_artifacts:
            pending_artifacts.append(dispatch_index)
    if (
        phase == "launch"
        and launch_state.get("available")
        and (not launch_state.get("generated") or launch_state.get("refresh_needed"))
    ):
        launch_manifest = str(
            launch_state.get("launch_manifest") or "team/bootstrap/provider/launch.json"
        )
        if launch_manifest not in pending_artifacts:
            pending_artifacts.append(launch_manifest)
    if team_state.get("worker_summary_required") and not team_state.get(
        "worker_summary_ready"
    ):
        for worker in pending_workers or missing_workers or incomplete_workers:
            relpath = team_worker_summary_relpath(worker)
            if relpath and relpath not in pending_artifacts:
                pending_artifacts.append(relpath)
    if not team_state.get("synthesis_ready"):
        pending_artifacts.append("TEAM_SYNTHESIS.md")
    elif team_state.get("team_runtime_verification_needed"):
        runtime_artifact = str(
            team_state.get("team_runtime_artifact") or "CRITIC__runtime.md"
        )
        if runtime_artifact and runtime_artifact not in pending_artifacts:
            pending_artifacts.append(runtime_artifact)
    elif team_state.get("team_documentation_needed"):
        doc_sync_artifact = str(
            team_state.get("team_doc_sync_artifact") or "DOC_SYNC.md"
        )
        if (
            team_state.get("team_doc_sync_needed")
            and doc_sync_artifact not in pending_artifacts
        ):
            pending_artifacts.append(doc_sync_artifact)
        document_artifact = str(
            team_state.get("team_document_critic_artifact") or "CRITIC__document.md"
        )
        if (
            team_state.get("team_document_critic_needed")
            and document_artifact not in pending_artifacts
        ):
            pending_artifacts.append(document_artifact)

    pending_owned_paths = []
    for worker in pending_workers:
        for path in (team_state.get("plan_owned_paths") or {}).get(worker, []) or []:
            if path not in pending_owned_paths:
                pending_owned_paths.append(path)

    existing_worker_artifacts = []
    for relpath in team_state.get("worker_summary_artifacts") or []:
        if os.path.isfile(os.path.join(task_dir, relpath)):
            existing_worker_artifacts.append(relpath)

    worker_details = {}
    per_worker = dict(team_state.get("worker_summary_per_worker") or {})
    for worker in expected_workers:
        parsed = dict(per_worker.get(worker) or {})
        relpath = parsed.get("artifact") or team_worker_summary_relpath(worker)
        worker_details[worker] = {
            "status": str(
                parsed.get("status")
                or ("missing" if worker in missing_workers else "ready")
            ),
            "pending": bool(worker in pending_workers),
            "artifact": relpath,
            "owned_writable_paths": list(
                parsed.get("planned_owned_paths")
                or (team_state.get("plan_owned_paths") or {}).get(worker)
                or []
            ),
            "owned_paths_handled": list(parsed.get("owned_paths_handled") or []),
            "completed_excerpt": str(parsed.get("completed_excerpt") or ""),
            "verification_excerpt": str(parsed.get("verification_excerpt") or ""),
            "residual_risks_excerpt": str(parsed.get("residual_risks_excerpt") or ""),
            "errors": list(parsed.get("errors") or [])[:3],
        }

    return {
        "status": team_state.get("derived_status")
        or team_state.get("current_status")
        or "n/a",
        "phase": phase,
        "plan_ready": bool(team_state.get("plan_ready")),
        "synthesis_ready": bool(team_state.get("synthesis_ready")),
        "verification_needed": bool(team_state.get("team_runtime_verification_needed")),
        "verification_reason": str(
            team_state.get("team_runtime_verification_reason") or ""
        ),
        "verification_owners": list(
            team_state.get("team_runtime_verification_owners") or []
        ),
        "runtime_artifact": str(team_state.get("team_runtime_artifact") or ""),
        "documentation_needed": bool(team_state.get("team_documentation_needed")),
        "documentation_reason": str(team_state.get("team_documentation_reason") or ""),
        "documentation_owner_label": str(
            team_state.get("team_documentation_owner_label") or ""
        ),
        "doc_sync_needed": bool(team_state.get("team_doc_sync_needed")),
        "doc_sync_artifact": str(
            team_state.get("team_doc_sync_artifact") or "DOC_SYNC.md"
        ),
        "doc_sync_owners": list(team_state.get("team_doc_sync_owners") or []),
        "doc_sync_owner_label": str(team_state.get("team_doc_sync_owner_label") or ""),
        "document_critic_needed": bool(team_state.get("team_document_critic_needed")),
        "document_critic_pending": bool(team_state.get("team_document_critic_pending")),
        "document_critic_stale_after_docs": bool(
            team_state.get("team_document_critic_stale_after_docs")
        ),
        "document_critic_artifact": str(
            team_state.get("team_document_critic_artifact") or "CRITIC__document.md"
        ),
        "document_critic_owners": list(
            team_state.get("team_document_critic_owners") or []
        ),
        "document_critic_owner_label": str(
            team_state.get("team_document_critic_owner_label") or ""
        ),
        "expected_workers": expected_workers,
        "summary_workers": list(team_state.get("summary_workers") or expected_workers),
        "synthesis_workers": list(team_state.get("synthesis_workers") or []),
        "ready_workers": ready_workers,
        "pending_workers": pending_workers,
        "missing_workers": missing_workers,
        "incomplete_workers": incomplete_workers,
        "pending_artifacts": pending_artifacts,
        "worker_summary_artifacts": existing_worker_artifacts,
        "pending_owned_paths": pending_owned_paths,
        "worker_summary_errors": list(team_state.get("worker_summary_errors") or []),
        "synthesis_semantic_errors": list(
            team_state.get("synthesis_semantic_errors") or []
        ),
        "bootstrap_available": bool(bootstrap_state.get("available")),
        "bootstrap_generated": bool(bootstrap_state.get("generated")),
        "bootstrap_refresh_needed": bool(bootstrap_state.get("refresh_needed")),
        "bootstrap_stale": bool(bootstrap_state.get("stale")),
        "bootstrap_reason": str(bootstrap_state.get("reason") or ""),
        "bootstrap_index": str(
            bootstrap_state.get("bootstrap_index") or "team/bootstrap/index.json"
        ),
        "bootstrap_missing_files": list(bootstrap_state.get("missing_files") or []),
        "dispatch_available": bool(dispatch_state.get("available")),
        "dispatch_generated": bool(dispatch_state.get("generated")),
        "dispatch_refresh_needed": bool(dispatch_state.get("refresh_needed")),
        "dispatch_stale": bool(dispatch_state.get("stale")),
        "dispatch_reason": str(dispatch_state.get("reason") or ""),
        "dispatch_index": str(
            dispatch_state.get("dispatch_index")
            or "team/bootstrap/provider/dispatch.json"
        ),
        "dispatch_missing_files": list(dispatch_state.get("missing_files") or []),
        "launch_available": bool(launch_state.get("available")),
        "launch_generated": bool(launch_state.get("generated")),
        "launch_refresh_needed": bool(launch_state.get("refresh_needed")),
        "launch_stale": bool(launch_state.get("stale")),
        "launch_reason": str(launch_state.get("reason") or ""),
        "launch_manifest": str(
            launch_state.get("launch_manifest") or "team/bootstrap/provider/launch.json"
        ),
        "launch_target": str(launch_state.get("target") or "auto"),
        "launch_command_preview": str(launch_state.get("launch_command_preview") or ""),
        "launch_provider_prompt": str(launch_state.get("provider_prompt") or ""),
        "launch_implement_dispatcher": str(
            launch_state.get("implement_dispatcher") or ""
        ),
        "launch_interactive_required": bool(launch_state.get("interactive_required")),
        "launch_execute_supported": bool(launch_state.get("execute_supported")),
        "launch_execute_target": str(launch_state.get("execute_target") or ""),
        "launch_execute_command_preview": str(
            launch_state.get("execute_command_preview") or ""
        ),
        "launch_execute_fallback_available": bool(
            launch_state.get("execute_fallback_available")
        ),
        "launch_execute_resolution_reason": str(
            launch_state.get("execute_resolution_reason") or ""
        ),
        "relaunch_available": bool(relaunch_state.get("available")),
        "relaunch_ready": bool(relaunch_state.get("ready")),
        "relaunch_reason": str(relaunch_state.get("reason") or ""),
        "relaunch_selection_reason": str(relaunch_state.get("selection_reason") or ""),
        "relaunch_selection_source": str(relaunch_state.get("selection_source") or ""),
        "relaunch_worker": str(relaunch_state.get("worker") or ""),
        "relaunch_phase": str(relaunch_state.get("phase") or ""),
        "relaunch_artifact": str(relaunch_state.get("artifact") or ""),
        "relaunch_prompt_file": str(relaunch_state.get("prompt_file") or ""),
        "relaunch_run_script": str(relaunch_state.get("run_script") or ""),
        "relaunch_log_file": str(relaunch_state.get("log_file") or ""),
        "relaunch_command_preview": str(relaunch_state.get("command_preview") or ""),
        "handoff_refresh_needed": bool(team_state.get("handoff_refresh_needed")),
        "handoff_refresh_reason": str(team_state.get("handoff_refresh_reason") or ""),
        "workers": worker_details,
    }


def _parse_checks(checks_path):
    """Parse CHECKS.yaml, return (open_check_ids, last_known_good_checks).

    Handles simple YAML block structure:
      - id: AC-001
        status: PASS
      - id: AC-002
        status: open
    """
    open_ids = []
    good_ids = []

    try:
        with open(checks_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return open_ids, good_ids

    current_id = None
    current_status = None

    for line in lines:
        id_m = re.match(r"^\s+-?\s*id\s*:\s*(.+)", line)
        if id_m:
            # Flush previous
            if current_id is not None:
                if current_status and current_status.lower() == "passed":
                    good_ids.append(current_id)
                else:
                    open_ids.append(current_id)
            current_id = id_m.group(1).strip().strip('"').strip("'")
            current_status = None
            continue

        status_m = re.match(r"^\s+status\s*:\s*(.+)", line)
        if status_m and current_id is not None:
            current_status = status_m.group(1).strip().strip('"').strip("'")

    # Flush last entry
    if current_id is not None:
        if current_status and current_status.lower() == "passed":
            good_ids.append(current_id)
        else:
            open_ids.append(current_id)

    return open_ids, good_ids


def _find_fail_critic_files(task_dir):
    """Return list of relative filenames for CRITIC__*.md files with FAIL."""
    fail_refs = []
    try:
        for fname in sorted(os.listdir(task_dir)):
            if not (fname.startswith("CRITIC__") and fname.endswith(".md")):
                continue
            fpath = os.path.join(task_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    content = fh.read()
                for line in content.splitlines():
                    if re.match(r"^\s*verdict\s*:\s*FAIL\s*$", line, re.IGNORECASE):
                        fail_refs.append(fname)
                        break
            except OSError:
                pass
    except OSError:
        pass
    return fail_refs


def _extract_paths_from_critics(task_dir, touched_paths):
    """Extract file paths mentioned in FAIL critic files.

    Falls back to touched_paths if nothing extracted.
    """
    paths = set()
    try:
        for fname in sorted(os.listdir(task_dir)):
            if not (fname.startswith("CRITIC__") and fname.endswith(".md")):
                continue
            fpath = os.path.join(task_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    content = fh.read()
                # Find file path patterns like src/foo/bar.py or plugin/scripts/x.py
                for m in re.finditer(r"\b([\w\-]+/[\w\-./]+\.\w+)\b", content):
                    candidate = m.group(1)
                    # Basic sanity: skip URLs, long strings
                    if len(candidate) < 100 and ".." not in candidate:
                        paths.add(candidate)
            except OSError:
                pass
    except OSError:
        pass

    if paths:
        return sorted(paths)[:10]  # Limit to 10 most relevant
    # Fall back to touched_paths (non-doc)
    return [p for p in touched_paths if not is_doc_path(p)][:10]


def _build_do_not_regress(task_dir, last_known_good_checks):
    """Build do_not_regress list from passing criteria + stable indicators."""
    items = []

    # Add passing check descriptions if we can find them
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if os.path.isfile(checks_path) and last_known_good_checks:
        items.extend(
            [f"criterion {cid} remains passing" for cid in last_known_good_checks]
        )

    # Add any runtime PASS evidence from CRITIC__runtime.md
    critic_path = os.path.join(task_dir, "CRITIC__runtime.md")
    if os.path.isfile(critic_path):
        try:
            with open(critic_path, "r", encoding="utf-8") as fh:
                content = fh.read()
            # Look for PASS evidence lines
            for line in content.splitlines():
                if re.search(r"\[EVIDENCE\].*PASS", line):
                    # Extract the description
                    m = re.search(r"\[EVIDENCE\]\s+\S+:\s+PASS\s+\S+\s+—\s+(.+)", line)
                    if m:
                        items.append(m.group(1).strip()[:120])
                    elif len(items) < 5:
                        # Add the raw line if short enough
                        stripped = line.strip()
                        if len(stripped) < 120:
                            items.append(stripped)
        except OSError:
            pass

    return items[:8]  # Cap at 8 items


def _derive_next_step(trigger, task_dir, team_recovery=None):
    """Derive a single clear next_step sentence from the trigger."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    blockers = yaml_field("blockers", state_file) or ""

    if isinstance(team_recovery, dict):
        phase = str(team_recovery.get("phase") or "")
        pending_workers = list(team_recovery.get("pending_workers") or [])
        if (
            phase == "bootstrap"
            and team_recovery.get("bootstrap_available")
            and (
                not team_recovery.get("bootstrap_generated")
                or team_recovery.get("bootstrap_refresh_needed")
            )
        ):
            bootstrap_index = str(
                team_recovery.get("bootstrap_index") or "team/bootstrap/index.json"
            )
            bootstrap_reason = str(
                team_recovery.get("bootstrap_reason") or "bootstrap is stale"
            )
            if team_recovery.get("bootstrap_generated"):
                return (
                    f"Rerun team-bootstrap to refresh {bootstrap_index}, worker briefs, and env snippets before resuming fan-out. "
                    f"Reason: {bootstrap_reason}."
                )
            return f"Run team-bootstrap first to generate {bootstrap_index}, worker briefs, and env snippets before resuming fan-out."
        if (
            phase == "dispatch"
            and team_recovery.get("dispatch_available")
            and (
                not team_recovery.get("dispatch_generated")
                or team_recovery.get("dispatch_refresh_needed")
            )
        ):
            dispatch_index = str(
                team_recovery.get("dispatch_index")
                or "team/bootstrap/provider/dispatch.json"
            )
            dispatch_reason = str(
                team_recovery.get("dispatch_reason") or "dispatch is stale"
            )
            if team_recovery.get("dispatch_generated"):
                return (
                    f"Rerun team-dispatch to refresh {dispatch_index}, provider launch prompts, and worker run helpers before resuming fan-out. "
                    f"Reason: {dispatch_reason}."
                )
            return f"Run team-dispatch after bootstrap to generate {dispatch_index}, provider launch prompts, and worker run helpers before resuming fan-out."
        if (
            phase == "launch"
            and team_recovery.get("launch_available")
            and (
                not team_recovery.get("launch_generated")
                or team_recovery.get("launch_refresh_needed")
            )
        ):
            launch_manifest = str(
                team_recovery.get("launch_manifest")
                or "team/bootstrap/provider/launch.json"
            )
            launch_reason = str(
                team_recovery.get("launch_reason") or "launch plan is stale"
            )
            provider_prompt = str(team_recovery.get("launch_provider_prompt") or "")
            execute_fallback = bool(
                team_recovery.get("launch_execute_fallback_available")
            )
            execute_target = str(team_recovery.get("launch_execute_target") or "")
            if team_recovery.get("launch_generated"):
                if (
                    team_recovery.get("launch_interactive_required")
                    and execute_fallback
                ):
                    return (
                        f"Rerun team-launch to refresh {launch_manifest}, the native lead prompt {provider_prompt or 'team/bootstrap/provider/<provider>-team.prompt.md'}, "
                        f"and the {execute_target or 'implementer'} fallback before resuming fan-out. Reason: {launch_reason}."
                    )
                return (
                    f"Rerun team-launch to refresh {launch_manifest}, the default provider/implementer entrypoint, and the detached spawn command before resuming fan-out. "
                    f"Reason: {launch_reason}."
                )
            if team_recovery.get("launch_interactive_required") and execute_fallback:
                return (
                    f"Run team-launch after dispatch to generate {launch_manifest}, the native lead prompt {provider_prompt or 'team/bootstrap/provider/<provider>-team.prompt.md'}, "
                    f"and the {execute_target or 'implementer'} execute fallback before resuming worker execution."
                )
            return f"Run team-launch after dispatch to generate {launch_manifest}, auto-refresh stale bootstrap / dispatch artifacts if needed, and prepare the default fan-out command before resuming worker execution."
        synthesis_workers = [
            str(x).strip()
            for x in (team_recovery.get("synthesis_workers") or [])
            if str(x).strip()
        ]
        synthesis_preview = ", ".join(synthesis_workers[:3]) or "the synthesis owner"
        handoff_refresh_needed = bool(team_recovery.get("handoff_refresh_needed"))
        handoff_refresh_reason = str(team_recovery.get("handoff_refresh_reason") or "")
        relaunch_available = bool(team_recovery.get("relaunch_available"))
        relaunch_worker = str(team_recovery.get("relaunch_worker") or "")
        relaunch_phase = str(team_recovery.get("relaunch_phase") or "")
        relaunch_selection_reason = str(
            team_recovery.get("relaunch_selection_reason") or ""
        )
        if phase == "plan":
            return (
                "Complete TEAM_PLAN.md first — assign worker ownership, writable paths, forbidden writes, "
                "and synthesis strategy before resuming team execution."
            )
        if phase == "worker_summaries":
            if relaunch_available and relaunch_worker and relaunch_phase == "implement":
                return (
                    f"Use team-relaunch for {relaunch_worker}'s implement phase, let that worker refresh its owned-path work and worker summary, "
                    f"then hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                )
            if pending_workers:
                worker_list = ", ".join(pending_workers[:4])
                return (
                    f"Collect missing or incomplete worker summaries for {worker_list} under team/worker-<name>.md, "
                    f"then hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                )
            return (
                f"Collect per-worker summaries under team/worker-<name>.md before {synthesis_preview} refreshes TEAM_SYNTHESIS.md "
                "or resuming verification."
            )
        if phase == "synthesis":
            if relaunch_available and relaunch_worker and relaunch_phase == "synthesis":
                return (
                    f"Use team-relaunch for {relaunch_worker}'s synthesis phase to refresh TEAM_SYNTHESIS.md after the latest worker summaries, "
                    "then continue with verification or close."
                )
            if handoff_refresh_needed:
                return (
                    f"{synthesis_preview} should refresh TEAM_SYNTHESIS.md after the latest worker summaries, then refresh HANDOFF.md from that "
                    "merged state before resuming verification or close."
                )
            return (
                f"{synthesis_preview} should refresh TEAM_SYNTHESIS.md after the latest worker summaries, integrate cross-checks, "
                "and only then resume verification or close."
            )
        if phase == "verification":
            verification_reason = str(
                team_recovery.get("verification_reason")
                or "run final runtime verification after TEAM_SYNTHESIS.md"
            )
            runtime_artifact = str(
                team_recovery.get("runtime_artifact") or "CRITIC__runtime.md"
            )
            if (
                relaunch_available
                and relaunch_worker
                and relaunch_phase == "final_runtime_verification"
            ):
                return (
                    f"Use team-relaunch for {relaunch_worker}'s final runtime verification phase to refresh {runtime_artifact}, "
                    "then refresh HANDOFF.md before close."
                )
            return (
                f"{synthesis_preview} should {verification_reason}, refresh {runtime_artifact}, "
                "then refresh HANDOFF.md before close."
            )
        if phase == "documentation":
            documentation_reason = str(
                team_recovery.get("documentation_reason")
                or "refresh DOC_SYNC.md after final team runtime verification"
            )
            doc_sync_artifact = str(
                team_recovery.get("doc_sync_artifact") or "DOC_SYNC.md"
            )
            document_artifact = str(
                team_recovery.get("document_critic_artifact") or "CRITIC__document.md"
            )
            doc_sync_owners = [
                str(x).strip()
                for x in (team_recovery.get("doc_sync_owners") or [])
                if str(x).strip()
            ]
            document_critic_owners = [
                str(x).strip()
                for x in (team_recovery.get("document_critic_owners") or [])
                if str(x).strip()
            ]
            doc_sync_preview = ", ".join(doc_sync_owners[:3]) or "writer"
            document_critic_preview = (
                ", ".join(document_critic_owners[:3]) or "critic-document"
            )
            if (
                relaunch_available
                and relaunch_worker
                and relaunch_phase == "documentation_sync"
            ):
                return (
                    f"Use team-relaunch for {relaunch_worker}'s documentation sync phase to refresh {doc_sync_artifact}, "
                    f"then let {document_critic_preview} rerun {document_artifact} before HANDOFF.md is refreshed."
                )
            if (
                relaunch_available
                and relaunch_worker
                and relaunch_phase == "documentation_review"
            ):
                return (
                    f"Use team-relaunch for {relaunch_worker}'s documentation review phase to rerun {document_artifact} after the latest {doc_sync_artifact}, "
                    "then refresh HANDOFF.md before close."
                )
            if team_recovery.get("document_critic_needed"):
                return (
                    f"Complete the documentation pass after final team verification — {doc_sync_preview} should {documentation_reason}, refresh {doc_sync_artifact}, "
                    f"then {document_critic_preview} should rerun {document_artifact} before {synthesis_preview} refreshes HANDOFF.md and closes."
                )
            return (
                f"Complete the documentation pass after final team verification — {doc_sync_preview} should {documentation_reason}, refresh {doc_sync_artifact}, "
                f"then {synthesis_preview} should refresh HANDOFF.md before close."
            )
        if phase == "complete" and handoff_refresh_needed:
            if (
                relaunch_available
                and relaunch_worker
                and relaunch_phase == "handoff_refresh"
            ):
                return f"Use team-relaunch for {relaunch_worker}'s handoff refresh phase to update HANDOFF.md from the latest team artifacts before closing."
            return f"{handoff_refresh_reason or 'Refresh HANDOFF.md after the latest team artifact update'}, then resume close or recovery from that updated handoff."

    steps = {
        "runtime_fail_repeat": (
            "Reproduce the failure using the commands in CRITIC__runtime.md "
            "and fix the root cause before re-running QA."
        ),
        "criterion_reopen_repeat": (
            "Review CHECKS.yaml for repeatedly reopened criteria and address "
            "the underlying acceptance condition that keeps failing."
        ),
        "sprinted_compaction": (
            "Resume from PLAN.md sprint contract — verify roots_in_focus and "
            "check do_not_regress items before continuing implementation."
        ),
        "blocked_env_reentry": (
            f"Resolve the environment blocker ({blockers or 'see TASK_STATE.yaml'}) "
            "before resuming implementation."
        ),
        "roots_exceeded_estimate": (
            "Re-assess plan scope — roots_touched has grown beyond the original "
            "estimate; consider whether a mode escalation to sprinted is needed."
        ),
    }
    return steps.get(
        trigger, "Review the task context and resume from the last known good state."
    )


def _build_read_first_list(task_dir, last_fail_evidence_refs, team_recovery=None):
    """Build the essential recovery reading list."""
    candidates = ["PLAN.md", "TASK_STATE.yaml"]

    if os.path.isfile(os.path.join(task_dir, "TEAM_PLAN.md")):
        candidates.append("TEAM_PLAN.md")

    if isinstance(team_recovery, dict):
        bootstrap_index = str(team_recovery.get("bootstrap_index") or "").strip()
        if bootstrap_index and os.path.isfile(os.path.join(task_dir, bootstrap_index)):
            if bootstrap_index not in candidates:
                candidates.append(bootstrap_index)
        dispatch_index = str(team_recovery.get("dispatch_index") or "").strip()
        if dispatch_index and os.path.isfile(os.path.join(task_dir, dispatch_index)):
            if dispatch_index not in candidates:
                candidates.append(dispatch_index)
        launch_manifest = str(team_recovery.get("launch_manifest") or "").strip()
        if launch_manifest and os.path.isfile(os.path.join(task_dir, launch_manifest)):
            if launch_manifest not in candidates:
                candidates.append(launch_manifest)
        for relpath in team_recovery.get("worker_summary_artifacts") or []:
            if relpath not in candidates:
                candidates.append(relpath)
        if os.path.isfile(os.path.join(task_dir, "TEAM_SYNTHESIS.md")):
            if "TEAM_SYNTHESIS.md" not in candidates:
                candidates.append("TEAM_SYNTHESIS.md")
        runtime_artifact = str(team_recovery.get("runtime_artifact") or "").strip()
        if runtime_artifact and os.path.isfile(
            os.path.join(task_dir, runtime_artifact)
        ):
            if runtime_artifact not in candidates:
                candidates.append(runtime_artifact)
        doc_sync_artifact = str(team_recovery.get("doc_sync_artifact") or "").strip()
        if doc_sync_artifact and os.path.isfile(
            os.path.join(task_dir, doc_sync_artifact)
        ):
            if doc_sync_artifact not in candidates:
                candidates.append(doc_sync_artifact)
        document_artifact = str(
            team_recovery.get("document_critic_artifact") or ""
        ).strip()
        if document_artifact and os.path.isfile(
            os.path.join(task_dir, document_artifact)
        ):
            if document_artifact not in candidates:
                candidates.append(document_artifact)
        if team_recovery.get("handoff_refresh_needed") and os.path.isfile(
            os.path.join(task_dir, "HANDOFF.md")
        ):
            if "HANDOFF.md" not in candidates:
                candidates.append("HANDOFF.md")

    # Add fail critic files
    for ref in last_fail_evidence_refs:
        if ref not in candidates:
            candidates.append(ref)

    # Add HANDOFF.md if present
    if os.path.isfile(os.path.join(task_dir, "HANDOFF.md")):
        if "HANDOFF.md" not in candidates:
            candidates.append("HANDOFF.md")

    # Add CHECKS.yaml if present
    if os.path.isfile(os.path.join(task_dir, "CHECKS.yaml")):
        if "CHECKS.yaml" not in candidates:
            candidates.append("CHECKS.yaml")

    return candidates
