#!/usr/bin/env python3
import sys, os, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, hook_json_get, json_field, yaml_field, yaml_array,
                  TASK_DIR, now_iso, increment_agent_run, append_workflow_violation,
                  exit_if_unmanaged_repo, team_artifact_status, get_team_worker_name,
                  get_agent_role, team_worker_summary_relpath)

# SubagentStop hook — records subagent provenance and checks expected artifacts.
# Non-blocking (exit 0 always). Records violations for close-time enforcement.
# stdin: JSON | exit 0: allow | exit 2: block (unused)

# Recognized canonical agent names (without harness: prefix)
_KNOWN_AGENTS = frozenset([
    "developer", "writer",
    "critic-plan", "critic-runtime", "critic-document",
])

# Expected artifacts per role — used for provenance meta checks
_EXPECTED_ARTIFACTS = {
    "developer": ["HANDOFF.md"],
    "writer": ["DOC_SYNC.md"],
    "critic-plan": ["CRITIC__plan.md"],
    "critic-runtime": ["CRITIC__runtime.md"],
    "critic-document": ["CRITIC__document.md"],
}


def _normalize_agent(raw_name):
    """Strip 'harness:' prefix to get canonical agent name."""
    if raw_name.startswith("harness:"):
        return raw_name[len("harness:"):]
    return raw_name


def record_agent_run(task_dir, agent_name):
    """Record that agent_name ran on task_dir. Returns True on success."""
    return increment_agent_run(task_dir, agent_name)


def check_agent_artifacts(task_dir, raw_agent_name):
    """Return list of reminder strings for missing expected artifacts.

    Checks based on the raw (possibly prefixed) agent name so both
    'developer' and 'harness:developer' are handled.
    """
    reminders = []
    task_id = os.path.basename(task_dir.rstrip("/"))

    if raw_agent_name in ("developer", "harness:developer"):
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        handoff_file = os.path.join(task_dir, "HANDOFF.md")

        if not os.path.exists(handoff_file):
            reminders.append(
                f"REMINDER: {task_id} — developer must create HANDOFF.md"
                " with verification breadcrumbs"
            )
        if os.path.exists(state_file):
            status = yaml_field("status", state_file) or ""
            if status not in ("implemented", "blocked_env"):
                reminders.append(
                    f"REMINDER: {task_id} — developer finished but status is"
                    f" '{status}', expected 'implemented'"
                )

    elif raw_agent_name in ("writer", "harness:writer"):
        is_mutating = True
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if re.search(r"^mutates_repo:\s*false", content, re.MULTILINE):
                    is_mutating = False
            except OSError:
                pass
        if is_mutating:
            doc_sync = os.path.join(task_dir, "DOC_SYNC.md")
            if not os.path.exists(doc_sync):
                reminders.append(
                    f"REMINDER: {task_id} — writer should produce DOC_SYNC.md"
                    " for repo-mutating task (content may be 'none' if no docs changed)"
                )

    elif raw_agent_name in ("critic-runtime", "harness:critic-runtime"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__runtime.md")):
            reminders.append(
                f"REMINDER: {task_id} — runtime critic should write CRITIC__runtime.md"
            )

    elif raw_agent_name in ("critic-plan", "harness:critic-plan"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__plan.md")):
            reminders.append(
                f"REMINDER: {task_id} — plan critic should write CRITIC__plan.md"
            )

    elif raw_agent_name in ("critic-document", "harness:critic-document"):
        if not os.path.exists(os.path.join(task_dir, "CRITIC__document.md")):
            reminders.append(
                f"REMINDER: {task_id} — document critic should write CRITIC__document.md"
            )

    return reminders


def check_team_artifacts(task_dir, raw_agent_name):
    """Return targeted reminders for team artifact phases."""
    team_state = team_artifact_status(task_dir)
    if team_state.get("orchestration_mode") != "team":
        return []

    task_id = os.path.basename(task_dir.rstrip("/"))
    plan_workers = list(team_state.get("plan_workers") or [])
    current_worker = get_team_worker_name(plan_workers, raw_agent_name=raw_agent_name)
    current_role = get_agent_role(raw_agent_name)
    reminders = []

    if not team_state.get("plan_ready"):
        if current_worker or current_role in ("developer", "harness"):
            reminders.append(
                f"REMINDER: {task_id} — complete TEAM_PLAN.md before team workers stop or mutate source files"
            )
        return reminders

    if team_state.get("worker_summary_required") and not team_state.get("worker_summary_ready"):
        missing_workers = list(team_state.get("worker_summary_missing_workers") or [])
        if current_worker:
            current_summary = dict((team_state.get("worker_summary_per_worker") or {}).get(current_worker) or {})
            if current_worker in missing_workers or current_summary.get("status") == "incomplete":
                rel_name = team_worker_summary_relpath(current_worker) or f"team/worker-{current_worker}.md"
                owned_paths = list((team_state.get("plan_owned_paths") or {}).get(current_worker, []) or [])
                owned_preview = ", ".join(owned_paths[:2]) or "owned writable paths"
                reminders.append(
                    f"REMINDER: {task_id} — {current_worker} should refresh {rel_name} after finishing {owned_preview}"
                )
            elif current_worker in (team_state.get("synthesis_workers") or []):
                pending_preview = ", ".join(missing_workers[:3]) or "the remaining workers"
                reminders.append(
                    f"REMINDER: {task_id} — synthesis owner {current_worker} should wait for {pending_preview} before refreshing TEAM_SYNTHESIS.md"
                )
        return reminders

    if not team_state.get("synthesis_ready"):
        synthesis_workers = list(team_state.get("synthesis_workers") or [])
        if current_worker and current_worker in synthesis_workers:
            reminders.append(
                f"REMINDER: {task_id} — {current_worker} should refresh TEAM_SYNTHESIS.md before final team verification"
            )
        return reminders

    if team_state.get("team_runtime_verification_needed"):
        owners = list(team_state.get("team_runtime_verification_owners") or team_state.get("synthesis_workers") or [])
        artifact = str(team_state.get("team_runtime_artifact") or "CRITIC__runtime.md")
        if current_worker and current_worker in owners:
            reminders.append(
                f"REMINDER: {task_id} — {current_worker} should refresh {artifact} before close"
            )
        elif current_role == "critic-runtime" and owners:
            preview = ", ".join(owners[:3])
            reminders.append(
                f"REMINDER: {task_id} — final runtime verification belongs to [{preview}]; set HARNESS_TEAM_WORKER before writing {artifact}"
            )
        return reminders

    if team_state.get("team_documentation_needed"):
        doc_sync_owners = list(team_state.get("team_doc_sync_owners") or [])
        document_critic_owners = list(team_state.get("team_document_critic_owners") or [])
        doc_sync_artifact = str(team_state.get("team_doc_sync_artifact") or "DOC_SYNC.md")
        document_artifact = str(team_state.get("team_document_critic_artifact") or "CRITIC__document.md")

        if current_worker and current_worker in doc_sync_owners and team_state.get("team_doc_sync_needed"):
            reminders.append(
                f"REMINDER: {task_id} — {current_worker} should refresh {doc_sync_artifact} after final team verification"
            )
        if current_worker and current_worker in document_critic_owners and (
            team_state.get("team_document_critic_missing_after_docs")
            or team_state.get("team_document_critic_stale_after_docs")
            or team_state.get("team_document_critic_pending")
        ):
            reminders.append(
                f"REMINDER: {task_id} — {current_worker} should refresh {document_artifact} after the latest team documentation update"
            )
        elif current_role == "writer" and doc_sync_owners:
            preview = ", ".join(doc_sync_owners[:3])
            reminders.append(
                f"REMINDER: {task_id} — documentation sync belongs to [{preview}]; set HARNESS_TEAM_WORKER before writing {doc_sync_artifact}"
            )
        elif current_role == "critic-document" and document_critic_owners:
            preview = ", ".join(document_critic_owners[:3])
            reminders.append(
                f"REMINDER: {task_id} — document review belongs to [{preview}]; set HARNESS_TEAM_WORKER before writing {document_artifact}"
            )
        return reminders

    if team_state.get("handoff_refresh_needed"):
        synthesis_workers = list(team_state.get("synthesis_workers") or [])
        if current_worker and current_worker in synthesis_workers:
            reminders.append(
                f"REMINDER: {task_id} — {current_worker} should refresh HANDOFF.md ({team_state.get('handoff_refresh_reason') or 'team handoff is stale'})"
            )
        elif current_role == "developer" and synthesis_workers:
            preview = ", ".join(synthesis_workers[:3])
            reminders.append(
                f"REMINDER: {task_id} — HANDOFF.md refresh belongs to synthesis owner(s) [{preview}]; set HARNESS_TEAM_WORKER before writing it"
            )

    return reminders


def check_artifact_provenance(task_dir, canonical_agent):
    """Check that expected artifacts have proper .meta.json provenance.

    Records workflow_violation if artifact exists but meta is missing or wrong.
    This is recording only — actual close blocking is done by task_completed_gate.
    """
    expected = _EXPECTED_ARTIFACTS.get(canonical_agent, [])
    task_id = os.path.basename(task_dir.rstrip("/"))

    for artifact_name in expected:
        artifact_path = os.path.join(task_dir, artifact_name)
        if not os.path.isfile(artifact_path):
            continue  # Artifact not yet created — that's the reminder's job

        # Check for .meta.json sidecar
        try:
            from provenance_helpers import read_meta, PROTECTED_ARTIFACT_OWNERS
            meta = read_meta(artifact_path)
            if meta is None:
                # Artifact exists without provenance — record violation
                append_workflow_violation(
                    task_dir,
                    f"missing_provenance_{artifact_name.replace('.', '_').lower()}"
                )
                print(
                    f"PROVENANCE WARNING: {task_id} — {artifact_name} exists "
                    f"but lacks .meta.json sidecar"
                )
                continue

            # Check author_role matches
            allowed_roles = PROTECTED_ARTIFACT_OWNERS.get(artifact_name, set())
            author_role = meta.get("author_role", "")
            if allowed_roles and author_role not in allowed_roles:
                append_workflow_violation(
                    task_dir,
                    f"unauthorized_artifact_write_{artifact_name.replace('.', '_').lower()}"
                )
                print(
                    f"PROVENANCE WARNING: {task_id} — {artifact_name} authored by "
                    f"'{author_role}' but expected one of: {sorted(allowed_roles)}"
                )
        except ImportError:
            pass  # provenance_helpers not available — skip meta checks


def _main_impl():
    exit_if_unmanaged_repo()

    data = read_hook_input()

    # WS-1 fix: hook_json_get(data, field) instead of json_field(data, field)
    raw_agent = (
        hook_json_get(data, "agent_name")
        or hook_json_get(data, "agent")
        or os.environ.get("CLAUDE_AGENT_NAME", "unknown")
    )
    task_id = hook_json_get(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

    if not task_id:
        sys.exit(0)

    target = os.path.join(TASK_DIR, task_id)
    if not os.path.isdir(target):
        sys.exit(0)

    canonical = _normalize_agent(raw_agent)

    # WS-3: Record provenance in TASK_STATE.yaml
    if canonical in _KNOWN_AGENTS:
        if record_agent_run(target, canonical):
            print(f"PROVENANCE: {task_id} — recorded {canonical} run")

    # Check artifact reminders (soft enforcement)
    for reminder in check_agent_artifacts(target, raw_agent):
        print(reminder)
    for reminder in check_team_artifacts(target, raw_agent):
        print(reminder)

    # Check artifact provenance (.meta.json) — records violations, does not block
    if canonical in _KNOWN_AGENTS:
        check_artifact_provenance(target, canonical)

    sys.exit(0)


def main():
    try:
        _main_impl()
    except SystemExit:
        raise   # sys.exit(0), sys.exit(2) propagate normally
    except Exception as e:
        print(
            f"GATE INFRA ERROR (non-blocking): {type(e).__name__}: {e}",
            file=sys.stderr
        )
        sys.exit(0)   # infra errors don't block


if __name__ == "__main__":
    main()
