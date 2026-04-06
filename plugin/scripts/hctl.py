#!/usr/bin/env python3
"""hctl — harness CLI control plane.

Single entry point for task lifecycle management:
  start        — compile routing fields into TASK_STATE.yaml
  context      — emit compact task pack (--json for machine-readable)
  history      — list indexed failure cases across task history
  top-failures — surface top similar historical failures for a task
  diff-case    — compare two failure cases
  update       — sync touched_paths/roots_touched/verification_targets
  verify       — delegate to verify.py (suite / smoke / healthcheck modes)
  close        — wrap task_completed_gate.py
  replay       — run curated golden behavior replays
  artifact     — wrap write_artifact.py

stdlib only (no pip packages).
"""

import argparse
import json
import os
import subprocess
import sys

# Locate the scripts directory (hctl.py lives there)
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

from _lib import (
    TASK_DIR,
    build_team_bootstrap,
    build_team_dispatch,
    build_team_launch,
    build_team_relaunch,
    canonical_task_dir,
    canonical_task_id,
    compile_routing,
    emit_compact_context,
    ensure_task_scaffold,
    ensure_team_artifacts,
    extract_roots,
    find_repo_root,
    is_doc_path,
    merge_task_path_fields,
    repo_relpath,
    repo_root_for_task_dir,
    set_task_state_field,
    sync_team_status,
    yaml_array,
    yaml_field,
)
from environment_snapshot import write_environment_snapshot
from failure_memory import (
    diff_failure_cases,
    find_similar_failures,
    list_failure_cases,
    write_failure_case_snapshot,
)
from golden_replay import run_cli as run_golden_replay
from harness_api import get_task_context
from task_index import clear_active_task, update_active_task

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _repo_root() -> str:
    return find_repo_root(os.getcwd())


def _resolve_task_dir_value(task_dir):
    if not task_dir:
        return ""
    if os.path.isabs(task_dir):
        return os.path.normpath(task_dir)
    return os.path.normpath(os.path.join(_repo_root(), task_dir))


def _expected_canonical_task_dir(raw_task_dir=None, task_id=None, slug=None):
    repo_root = _repo_root()
    return canonical_task_dir(
        task_id=task_id,
        slug=slug,
        task_dir=raw_task_dir,
        tasks_dir=TASK_DIR,
        repo_root=repo_root,
    )


def _bootstrap_start_task(args):
    """Resolve start references and bootstrap canonical task scaffolding."""
    raw_task_dir = getattr(args, "task_dir", None)
    task_id = getattr(args, "task_id", None)
    slug = getattr(args, "slug", None)

    request_text = ""
    request_file = getattr(args, "request_file", None)
    if request_file:
        request_path = _resolve_task_dir_value(request_file)
        if os.path.isfile(request_path):
            try:
                with open(request_path, "r", encoding="utf-8") as fh:
                    request_text = fh.read()
            except OSError:
                request_text = ""

    if raw_task_dir:
        resolved = _resolve_task_dir_value(raw_task_dir)
        state_file = os.path.join(resolved, "TASK_STATE.yaml")
        canonical = _expected_canonical_task_dir(
            raw_task_dir=raw_task_dir, task_id=task_id, slug=slug
        )
        canonical_norm = os.path.normpath(canonical) if canonical else ""
        if os.path.isfile(state_file):
            return (
                resolved,
                request_text,
                {"created": [], "canonical": resolved, "expected": canonical_norm},
            )
        if canonical_norm and resolved != canonical_norm:
            raise ValueError(
                "Non-canonical task_dir. "
                f"Expected {canonical_norm} but received {resolved}. "
                f"Use --task-id {canonical_task_id(task_id=task_id, slug=slug, task_dir=raw_task_dir)!r} "
                f"or --task-dir {canonical_norm!r}."
            )
        scaffold = ensure_task_scaffold(
            resolved,
            canonical_task_id(task_id=task_id, slug=slug, task_dir=raw_task_dir),
            request_text=request_text,
        )
        scaffold.update({"canonical": resolved, "expected": canonical_norm or resolved})
        return resolved, request_text, scaffold

    if not task_id and not slug:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    canonical = _expected_canonical_task_dir(task_id=task_id, slug=slug)
    scaffold = ensure_task_scaffold(
        canonical,
        canonical_task_id(task_id=task_id, slug=slug),
        request_text=request_text,
    )
    scaffold.update({"canonical": canonical, "expected": canonical})
    return canonical, request_text, scaffold


def _require_task_dir(args):
    """Resolve and validate --task-dir. Exits on failure."""
    task_dir = _resolve_task_dir_value(getattr(args, "task_dir", None))
    if not task_dir:
        print("ERROR: --task-dir is required", file=sys.stderr)
        sys.exit(1)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        print(f"ERROR: TASK_STATE.yaml not found in {task_dir}", file=sys.stderr)
        sys.exit(1)
    return task_dir


def _resolve_tasks_dir(raw_tasks_dir):
    tasks_dir = raw_tasks_dir or TASK_DIR
    if not os.path.isabs(tasks_dir):
        tasks_dir = os.path.join(_repo_root(), tasks_dir)
    tasks_dir = os.path.normpath(tasks_dir)
    if not os.path.isdir(tasks_dir):
        print(f"ERROR: tasks dir not found: {tasks_dir}", file=sys.stderr)
        sys.exit(1)
    return tasks_dir


def _print_case_summary(case, prefix=""):
    head = (
        f"{prefix}{case.get('task_id')}: signals={case.get('failure_signals', 0)} "
        f"lane={case.get('lane', 'unknown')} artifact={case.get('artifact', 'TASK_STATE.yaml')}"
    )
    print(head)
    excerpt = str(case.get("excerpt") or "").strip()
    if excerpt:
        print(f"  excerpt: {excerpt}")
    check_ids = case.get("check_ids") or case.get("matching_check_ids") or []
    if check_ids:
        print(f"  checks: {', '.join(str(x) for x in check_ids[:4])}")
    paths = case.get("path_examples") or case.get("matching_paths") or []
    if paths:
        print(f"  paths: {', '.join(str(x) for x in paths[:4])}")


# ---------------------------------------------------------------------------
# Subcommand: start
# ---------------------------------------------------------------------------


def cmd_start(args):
    """Compile routing and write canonical fields to TASK_STATE.yaml."""
    try:
        task_dir, request_text, bootstrap = _bootstrap_start_task(args)
    except ValueError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    repo_root = repo_root_for_task_dir(task_dir)

    routing = compile_routing(task_dir, request_text=request_text)

    for field, value in routing.items():
        set_task_state_field(task_dir, field, value)

    team_artifact_result = []
    if routing.get("orchestration_mode") == "team":
        try:
            team_artifact_result = ensure_team_artifacts(task_dir, routing=routing)
        except Exception:
            team_artifact_result = []

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

    snapshot_path = ""
    try:
        snapshot_path = write_environment_snapshot(
            task_dir, repo_root=repo_root, reason="task_start"
        )
    except Exception:
        snapshot_path = ""

    case_path = ""
    try:
        case_path = write_failure_case_snapshot(task_dir, prompt=request_text)
    except Exception:
        case_path = ""

    try:
        update_active_task(task_dir, tasks_dir=os.path.dirname(task_dir))
    except Exception:
        pass

    task_id = yaml_field(
        "task_id", os.path.join(task_dir, "TASK_STATE.yaml")
    ) or os.path.basename(task_dir)
    print(f"routing compiled for {task_id}")
    print(f"  task_dir: {task_dir}")
    if bootstrap.get("created"):
        print(
            "  bootstrap_created: "
            + ", ".join(
                os.path.basename(item) for item in bootstrap.get("created") or []
            )
        )
    print(f"  risk_level: {routing['risk_level']}")
    print(f"  maintenance_task: {routing['maintenance_task']}")
    print(f"  workflow_locked: {routing['workflow_locked']}")
    print(f"  planning_mode: {routing['planning_mode']}")
    print(f"  execution_mode: {routing['execution_mode']} (compat)")
    print(f"  orchestration_mode: {routing['orchestration_mode']} (compat)")
    if routing.get("orchestration_mode") != "solo" or routing.get(
        "team_status"
    ) not in (None, "n/a", "skipped"):
        print(
            "  team: provider={provider} status={status} size={size} fallback={fallback}".format(
                provider=routing.get("team_provider", "none"),
                status=routing.get("team_status", "n/a"),
                size=routing.get("team_size", 0),
                fallback=routing.get("fallback_used", "none"),
            )
        )
        if routing.get("team_reason"):
            print(f"  team_reason: {routing['team_reason']}")
    if team_artifact_result:
        print(
            "  team_artifacts: "
            + ", ".join(os.path.basename(item) for item in team_artifact_result)
        )
    if snapshot_path:
        print(f"  env_snapshot: {os.path.basename(snapshot_path)}")
    if case_path:
        print(f"  failure_case: {os.path.basename(case_path)}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: context
# ---------------------------------------------------------------------------


def cmd_context(args):
    """Emit compact task pack."""
    task_dir = _require_task_dir(args)

    ctx = get_task_context(
        task_dir,
        team_worker=getattr(args, "team_worker", None),
        agent_name=getattr(args, "agent_name", None),
    )

    if getattr(args, "json", False):
        print(json.dumps(ctx, indent=2))
    else:
        checks = ctx.get("checks", {})
        print(f"Task: {ctx['task_id']} [{ctx.get('status', 'unknown')}]")
        print(
            "Route: lane={lane} risk={risk} qa={qa} doc_sync={doc} browser={browser}".format(
                lane=ctx["lane"],
                risk=ctx["risk_level"],
                qa="required" if ctx["qa_required"] else "skip",
                doc="required" if ctx["doc_sync_required"] else "skip",
                browser="yes" if ctx["browser_required"] else "no",
            )
        )
        print(
            "Compat: execution_mode={execution_mode} orchestration_mode={orchestration_mode}".format(
                execution_mode=ctx["compat"]["execution_mode"],
                orchestration_mode=ctx["compat"]["orchestration_mode"],
            )
        )
        print(f"Planning: {ctx.get('planning_mode', 'standard')}")
        team = ctx.get("team") or {}
        if team:
            print(
                "Team: provider={provider} status={status} size={size} fallback={fallback}".format(
                    provider=team.get("provider", "none"),
                    status=team.get("status", "n/a"),
                    size=team.get("size", 0),
                    fallback=team.get("fallback_used", "none"),
                )
            )
            if team.get("reason"):
                print(f"Team reason: {team['reason']}")
            if (
                team.get("plan_required")
                or team.get("synthesis_required")
                or team.get("provider") not in ("none", "")
            ):
                print(
                    "Team plan: ready={ready} ownership_ready={ownership} placeholders={placeholders} artifact={artifact}".format(
                        ready="yes" if team.get("plan_ready") else "no",
                        ownership="yes" if team.get("plan_ownership_ready") else "no",
                        placeholders="yes"
                        if team.get("plan_has_placeholders")
                        else "no",
                        artifact=team.get("plan_artifact", "TEAM_PLAN.md"),
                    )
                )
                if team.get("plan_workers"):
                    print(
                        "Team ownership: workers={workers} owned_paths={owned_paths}".format(
                            workers=", ".join(team.get("plan_workers", [])[:6]),
                            owned_paths=team.get("plan_owned_path_count", 0),
                        )
                    )
                synthesis_workers = team.get("synthesis_workers") or []
                if synthesis_workers:
                    print(
                        "Team synthesis owners: "
                        + ", ".join(str(x) for x in synthesis_workers[:6])
                    )
                semantic_errors = team.get("plan_semantic_errors") or []
                if semantic_errors:
                    print(
                        "Team ownership errors: "
                        + " | ".join(str(x) for x in semantic_errors[:3])
                    )
                if team.get("bootstrap_available"):
                    print(
                        "Team bootstrap: ready={ready} stale={stale} artifact={artifact}".format(
                            ready="yes" if team.get("bootstrap_generated") else "no",
                            stale="yes"
                            if team.get("bootstrap_refresh_needed")
                            else "no",
                            artifact=team.get(
                                "bootstrap_index", "team/bootstrap/index.json"
                            ),
                        )
                    )
                    if (
                        team.get("bootstrap_reason")
                        and team.get("bootstrap_reason") != "current"
                    ):
                        print(
                            "Team bootstrap reason: "
                            + str(team.get("bootstrap_reason"))
                        )
                if team.get("dispatch_available"):
                    print(
                        "Team dispatch: ready={ready} stale={stale} artifact={artifact}".format(
                            ready="yes" if team.get("dispatch_generated") else "no",
                            stale="yes"
                            if team.get("dispatch_refresh_needed")
                            else "no",
                            artifact=team.get(
                                "dispatch_index",
                                "team/bootstrap/provider/dispatch.json",
                            ),
                        )
                    )
                    if (
                        team.get("dispatch_reason")
                        and team.get("dispatch_reason") != "current"
                    ):
                        print(
                            "Team dispatch reason: " + str(team.get("dispatch_reason"))
                        )
                if team.get("launch_available"):
                    print(
                        "Team launch: ready={ready} stale={stale} target={target} artifact={artifact}".format(
                            ready="yes" if team.get("launch_generated") else "no",
                            stale="yes" if team.get("launch_refresh_needed") else "no",
                            target=team.get("launch_target", "auto"),
                            artifact=team.get(
                                "launch_manifest", "team/bootstrap/provider/launch.json"
                            ),
                        )
                    )
                    if team.get("launch_command_preview"):
                        print(
                            "Team launch command: "
                            + str(team.get("launch_command_preview"))
                        )
                    if team.get("launch_provider_prompt"):
                        print(
                            "Team launch prompt: "
                            + str(team.get("launch_provider_prompt"))
                        )
                    if team.get("launch_execute_supported"):
                        execute_target = (
                            team.get("launch_execute_target")
                            or team.get("launch_target")
                            or "auto"
                        )
                        print(
                            "Team launch execute: supported via " + str(execute_target)
                        )
                        if team.get("launch_execute_command_preview") and team.get(
                            "launch_execute_command_preview"
                        ) != team.get("launch_command_preview"):
                            print(
                                "Team launch execute command: "
                                + str(team.get("launch_execute_command_preview"))
                            )
                        if team.get("launch_execute_resolution_reason"):
                            print(
                                "Team launch execute reason: "
                                + str(team.get("launch_execute_resolution_reason"))
                            )
                    if (
                        team.get("launch_reason")
                        and team.get("launch_reason") != "current"
                    ):
                        print("Team launch reason: " + str(team.get("launch_reason")))
                if team.get("worker_summary_required"):
                    print(
                        "Team workers: summaries_ready={ready} present={present}/{expected} dir={team_dir}".format(
                            ready="yes" if team.get("worker_summary_ready") else "no",
                            present=team.get("worker_summary_present_count", 0),
                            expected=team.get("worker_summary_expected_count", 0),
                            team_dir=team.get("worker_summary_dir", "team"),
                        )
                    )
                    missing_workers = team.get("worker_summary_missing_workers") or []
                    if missing_workers:
                        print(
                            "Team worker summaries missing: "
                            + ", ".join(str(x) for x in missing_workers[:6])
                        )
                    worker_errors = team.get("worker_summary_errors") or []
                    if worker_errors:
                        print(
                            "Team worker summary errors: "
                            + " | ".join(str(x) for x in worker_errors[:3])
                        )
                print(
                    "Team synthesis: ready={ready} placeholders={placeholders} artifact={artifact}".format(
                        ready="yes" if team.get("synthesis_ready") else "no",
                        placeholders="yes"
                        if team.get("synthesis_has_placeholders")
                        else "no",
                        artifact=team.get("synthesis_artifact", "TEAM_SYNTHESIS.md"),
                    )
                )
                synthesis_errors = team.get("synthesis_semantic_errors") or []
                if synthesis_errors:
                    print(
                        "Team synthesis errors: "
                        + " | ".join(str(x) for x in synthesis_errors[:3])
                    )
                if team.get("runtime_verification_needed"):
                    print(
                        "Team final verification: ready={ready} artifact={artifact}".format(
                            ready="yes"
                            if team.get("runtime_verification_ready")
                            else "no",
                            artifact=team.get(
                                "runtime_verification_artifact", "CRITIC__runtime.md"
                            ),
                        )
                    )
                if team.get("documentation_needed"):
                    print(
                        "Team docs: ready={ready} doc_sync={doc_sync} document_critic={critic}".format(
                            ready="yes" if team.get("documentation_ready") else "no",
                            doc_sync=team.get("doc_sync_artifact", "DOC_SYNC.md"),
                            critic=team.get(
                                "document_critic_artifact", "CRITIC__document.md"
                            ),
                        )
                    )
                    doc_sync_owners = team.get("doc_sync_owners") or []
                    document_critic_owners = team.get("document_critic_owners") or []
                    if doc_sync_owners or document_critic_owners:
                        parts = []
                        if doc_sync_owners:
                            parts.append(
                                "writer="
                                + ", ".join(str(x) for x in doc_sync_owners[:4])
                            )
                        if document_critic_owners:
                            parts.append(
                                "critic-document="
                                + ", ".join(str(x) for x in document_critic_owners[:4])
                            )
                        print("Team docs owners: " + " | ".join(parts))
                    if team.get("documentation_reason"):
                        print(
                            "Team docs reason: " + str(team.get("documentation_reason"))
                        )
                if team.get("current_worker"):
                    role = str(team.get("current_agent_role") or "")
                    if role:
                        print(f"Current worker role: {role}")
        if ctx.get("must_read"):
            print(f"Must read: {', '.join(ctx['must_read'])}")
        review_focus = ctx.get("review_focus") or {}
        if review_focus.get("evidence_first"):
            trigger = review_focus.get("trigger", "fix_round")
            print(f"Review focus: {trigger} (evidence-first)")
            excerpt = review_focus.get("evidence_excerpt")
            if excerpt:
                print(f"Evidence: {excerpt}")
        if review_focus.get("environment_artifact"):
            reasons = review_focus.get("environment_reasons") or []
            tail = f" ({', '.join(reasons)})" if reasons else ""
            print(f"Environment: {review_focus['environment_artifact']}{tail}")
        similar_cases = review_focus.get("prior_similar_cases") or []
        if similar_cases:
            print("Similar failures:")
            for item in similar_cases[:3]:
                score = item.get("score", 0.0)
                print(
                    f"  - {item.get('task_id')} score={score:.2f} {item.get('artifact')}"
                )
        if checks:
            print(
                "Checks: total={total} open={open_count} failed={failed_count} blocked={blocked_count}".format(
                    total=checks.get("total", 0),
                    open_count=len(checks.get("open_ids", [])),
                    failed_count=len(checks.get("failed_ids", [])),
                    blocked_count=len(checks.get("blocked_ids", [])),
                )
            )
        if ctx.get("open_failures"):
            print(f"Open failures: {', '.join(ctx['open_failures'])}")
        if ctx.get("notes"):
            print(f"Notes: {' | '.join(ctx['notes'])}")
        if ctx.get("next_action"):
            print(f"Next: {ctx['next_action']}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: team-bootstrap
# ---------------------------------------------------------------------------


def cmd_team_bootstrap(args):
    """Generate or preview per-worker bootstrap specs for team tasks."""
    task_dir = _require_task_dir(args)

    bootstrap = build_team_bootstrap(
        task_dir,
        write_files=getattr(args, "write_files", False),
    )

    if getattr(args, "json", False):
        print(json.dumps(bootstrap, indent=2))
        return 0 if bootstrap.get("ready") else 1

    task_id = bootstrap.get("task_id") or os.path.basename(task_dir)
    if not bootstrap.get("ready"):
        print(
            f"team bootstrap not ready for {task_id}: {bootstrap.get('reason') or 'unknown reason'}",
            file=sys.stderr,
        )
        return 1

    print(
        "team bootstrap ready for {task_id} (provider={provider}, status={status})".format(
            task_id=task_id,
            provider=bootstrap.get("provider", "none"),
            status=bootstrap.get("team_status", "n/a"),
        )
    )
    print(f"  bootstrap_dir: {bootstrap.get('bootstrap_dir')}")
    print(f"  bootstrap_index: {bootstrap.get('bootstrap_index')}")
    for spec in bootstrap.get("workers") or []:
        owned = ", ".join((spec.get("owned_paths") or [])[:2]) or "none"
        print(
            "  - {worker}: scope={scope} owned={owned} default_env={env}".format(
                worker=spec.get("worker", "worker"),
                scope=spec.get("role_scope", "worker"),
                owned=owned,
                env=spec.get("default_env_file", ""),
            )
        )
        for phase in spec.get("phases") or []:
            print(
                "      phase={phase_name} agent={agent} env={env} artifact={artifact}".format(
                    phase_name=phase.get("phase", "phase"),
                    agent=phase.get("agent_name", ""),
                    env=phase.get("env_file", ""),
                    artifact=phase.get("artifact", ""),
                )
            )
    generated = bootstrap.get("generated_files") or []
    if generated:
        print("  generated:")
        for item in generated:
            print(f"    - {item}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: team-dispatch
# ---------------------------------------------------------------------------


def cmd_team_dispatch(args):
    """Generate or preview provider launch artifacts for a team task."""
    task_dir = _require_task_dir(args)

    dispatch = build_team_dispatch(
        task_dir,
        write_files=getattr(args, "write_files", False),
    )

    if getattr(args, "json", False):
        print(json.dumps(dispatch, indent=2))
        return 0 if dispatch.get("ready") else 1

    task_id = dispatch.get("task_id") or os.path.basename(task_dir)
    if not dispatch.get("ready"):
        print(
            f"team dispatch not ready for {task_id}: {dispatch.get('reason') or 'unknown reason'}",
            file=sys.stderr,
        )
        return 1

    print(
        "team dispatch ready for {task_id} (provider={provider})".format(
            task_id=task_id,
            provider=dispatch.get("provider", "none"),
        )
    )
    print(f"  provider_prompt: {dispatch.get('provider_prompt')}")
    print(f"  provider_launcher: {dispatch.get('provider_launcher')}")
    if dispatch.get("launch_command_preview"):
        print(f"  launch: {dispatch.get('launch_command_preview')}")
    for worker in dispatch.get("workers") or []:
        owned = ", ".join((worker.get("owned_paths") or [])[:2]) or "none"
        print(
            "  - {worker}: scope={scope} owned={owned}".format(
                worker=worker.get("worker", "worker"),
                scope=worker.get("role_scope", "worker"),
                owned=owned,
            )
        )
        for phase in worker.get("phases") or []:
            print(
                "      phase={phase_name} prompt={prompt} run={run} artifact={artifact}".format(
                    phase_name=phase.get("phase", "phase"),
                    prompt=phase.get("prompt_file", ""),
                    run=phase.get("run_script", ""),
                    artifact=phase.get("artifact", ""),
                )
            )
    generated = dispatch.get("generated_files") or []
    if generated:
        print("  generated:")
        for item in generated:
            print(f"    - {item}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: team-launch
# ---------------------------------------------------------------------------


def cmd_team_launch(args):
    """Generate or execute the default team launch entrypoint."""
    task_dir = _require_task_dir(args)

    launch = build_team_launch(
        task_dir,
        write_files=getattr(args, "write_files", False)
        or getattr(args, "execute", False),
        execute=getattr(args, "execute", False),
        auto_refresh=not getattr(args, "no_auto_refresh", False),
        target=getattr(args, "target", "auto"),
    )

    if getattr(args, "json", False):
        print(json.dumps(launch, indent=2))
        if getattr(args, "execute", False):
            return 0 if launch.get("execution", {}).get("spawned") else 1
        return 0 if launch.get("ready") else 1

    task_id = launch.get("task_id") or os.path.basename(task_dir)
    if not launch.get("ready"):
        print(
            f"team launch not ready for {task_id}: {launch.get('reason') or 'unknown reason'}",
            file=sys.stderr,
        )
        return 1

    print(
        "team launch ready for {task_id} (provider={provider}, target={target})".format(
            task_id=task_id,
            provider=launch.get("provider", "none"),
            target=launch.get("target", "auto"),
        )
    )
    print(f"  launch_manifest: {launch.get('launch_manifest')}")
    print(f"  launch_script: {launch.get('launch_script')}")
    if launch.get("provider_prompt"):
        print(f"  provider_prompt: {launch.get('provider_prompt')}")
    if launch.get("implement_dispatcher"):
        print(f"  implement_dispatcher: {launch.get('implement_dispatcher')}")
    if launch.get("launch_command_preview"):
        print(f"  launch: {launch.get('launch_command_preview')}")
    print(
        "  auto_refresh: bootstrap={bootstrap} dispatch={dispatch}".format(
            bootstrap="yes" if launch.get("bootstrap_refreshed") else "no",
            dispatch="yes" if launch.get("dispatch_refreshed") else "no",
        )
    )
    if launch.get("interactive_required"):
        print("  interactive: native lead session required")
    if launch.get("execute_supported"):
        execute_target = launch.get("execute_target") or launch.get("target") or "auto"
        print(f"  execute: supported via {execute_target}")
        if launch.get("execute_launch_script") and launch.get(
            "execute_launch_script"
        ) != launch.get("launch_script"):
            print(f"  execute_script: {launch.get('execute_launch_script')}")
        if launch.get("execute_command_preview") and launch.get(
            "execute_command_preview"
        ) != launch.get("launch_command_preview"):
            print(f"  execute_launch: {launch.get('execute_command_preview')}")
        if launch.get("execute_resolution_reason"):
            print(f"  execute_resolution: {launch.get('execute_resolution_reason')}")
    elif launch.get("execute_blocker"):
        print(f"  execute blocked: {launch.get('execute_blocker')}")
    generated = launch.get("generated_files") or []
    if generated:
        print("  generated:")
        for item in generated:
            print(f"    - {item}")
    execution = launch.get("execution") or {}
    if getattr(args, "execute", False):
        if execution.get("spawned"):
            print(
                "  spawned: pid={pid} stdout={stdout_log} stderr={stderr_log}".format(
                    pid=execution.get("pid", "?"),
                    stdout_log=execution.get(
                        "stdout_log", launch.get("stdout_log", "")
                    ),
                    stderr_log=execution.get(
                        "stderr_log", launch.get("stderr_log", "")
                    ),
                )
            )
            return 0
        error = (
            execution.get("error")
            or launch.get("execute_blocker")
            or "launch spawn failed"
        )
        print(f"team launch execute failed for {task_id}: {error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Subcommand: team-relaunch
# ---------------------------------------------------------------------------


def cmd_team_relaunch(args):
    """Generate or execute a worker/phase-specific relaunch manifest."""
    task_dir = _require_task_dir(args)

    relaunch = build_team_relaunch(
        task_dir,
        worker=getattr(args, "worker", None),
        phase=getattr(args, "phase", "auto"),
        write_files=getattr(args, "write_files", False)
        or getattr(args, "execute", False),
        execute=getattr(args, "execute", False),
        auto_refresh=not getattr(args, "no_auto_refresh", False),
    )

    if getattr(args, "json", False):
        print(json.dumps(relaunch, indent=2))
        if getattr(args, "execute", False):
            return 0 if relaunch.get("execution", {}).get("spawned") else 1
        return 0 if relaunch.get("ready") else 1

    task_id = relaunch.get("task_id") or os.path.basename(task_dir)
    if not relaunch.get("ready"):
        print(
            f"team relaunch not ready for {task_id}: {relaunch.get('reason') or 'unknown reason'}",
            file=sys.stderr,
        )
        return 1

    print(
        "team relaunch ready for {task_id} (worker={worker}, phase={phase})".format(
            task_id=task_id,
            worker=relaunch.get("worker", "?"),
            phase=relaunch.get("phase", "?"),
        )
    )
    print(f"  relaunch_manifest: {relaunch.get('relaunch_manifest')}")
    print(f"  run_script: {relaunch.get('run_script')}")
    if relaunch.get("command_preview"):
        print(f"  launch: {relaunch.get('command_preview')}")
    if relaunch.get("artifact"):
        print(f"  artifact: {relaunch.get('artifact')}")
    if relaunch.get("selection_reason"):
        print(f"  selection: {relaunch.get('selection_reason')}")
    print(
        "  auto_refresh: bootstrap={bootstrap} dispatch={dispatch}".format(
            bootstrap="yes" if relaunch.get("bootstrap_refreshed") else "no",
            dispatch="yes" if relaunch.get("dispatch_refreshed") else "no",
        )
    )
    if relaunch.get("execute_supported"):
        print("  execute: supported")
    elif relaunch.get("execute_blocker"):
        print(f"  execute blocked: {relaunch.get('execute_blocker')}")
    generated = relaunch.get("generated_files") or []
    if generated:
        print("  generated:")
        for item in generated:
            print(f"    - {item}")
    execution = relaunch.get("execution") or {}
    if getattr(args, "execute", False):
        if execution.get("spawned"):
            print(
                "  spawned: pid={pid} stdout={stdout_log} stderr={stderr_log} phase_log={phase_log}".format(
                    pid=execution.get("pid", "?"),
                    stdout_log=execution.get(
                        "stdout_log", relaunch.get("stdout_log", "")
                    ),
                    stderr_log=execution.get(
                        "stderr_log", relaunch.get("stderr_log", "")
                    ),
                    phase_log=execution.get("phase_log", relaunch.get("log_file", "")),
                )
            )
            return 0
        error = (
            execution.get("error")
            or relaunch.get("execute_blocker")
            or "relaunch spawn failed"
        )
        print(f"team relaunch execute failed for {task_id}: {error}", file=sys.stderr)
        return 1
    return 0


# ---------------------------------------------------------------------------
# Subcommand: history
# ---------------------------------------------------------------------------


def cmd_history(args):
    """List failure cases across task history."""
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))
    cases = list_failure_cases(
        tasks_dir=tasks_dir,
        limit=getattr(args, "limit", 20),
        lane=getattr(args, "lane", ""),
        min_failure_signals=getattr(args, "min_failure_signals", 1),
    )

    if getattr(args, "json", False):
        print(json.dumps(cases, indent=2))
        return 0

    if not cases:
        print("No failure cases found.")
        return 0

    for case in cases:
        _print_case_summary(case)
    return 0


# ---------------------------------------------------------------------------
# Subcommand: top-failures
# ---------------------------------------------------------------------------


def cmd_top_failures(args):
    """Show top similar failures for the given task."""
    task_dir = _require_task_dir(args)
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))

    try:
        write_failure_case_snapshot(task_dir)
    except Exception:
        pass

    matches = find_similar_failures(
        task_dir,
        tasks_dir=tasks_dir,
        limit=getattr(args, "limit", 3),
    )

    if getattr(args, "json", False):
        print(json.dumps(matches, indent=2))
        return 0

    if not matches:
        print("No similar failures found.")
        return 0

    for idx, match in enumerate(matches, start=1):
        _print_case_summary(match, prefix=f"#{idx} ")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: diff-case
# ---------------------------------------------------------------------------


def cmd_diff_case(args):
    """Compare two failure cases."""
    tasks_dir = _resolve_tasks_dir(getattr(args, "tasks_dir", None))
    diff = diff_failure_cases(args.case_a, args.case_b, tasks_dir=tasks_dir)
    if not diff:
        print("ERROR: unable to diff cases", file=sys.stderr)
        return 1

    if getattr(args, "json", False):
        print(json.dumps(diff, indent=2))
        return 0

    case_a = diff.get("case_a") or {}
    case_b = diff.get("case_b") or {}
    print(
        f"Case A: {case_a.get('task_id')} signals={case_a.get('failure_signals', 0)} lane={case_a.get('lane', 'unknown')}"
    )
    print(
        f"Case B: {case_b.get('task_id')} signals={case_b.get('failure_signals', 0)} lane={case_b.get('lane', 'unknown')}"
    )
    if diff.get("same_lane"):
        print("Lane: same")
    else:
        print("Lane: different")
    if diff.get("shared_check_ids"):
        print("Shared checks: " + ", ".join(diff["shared_check_ids"]))
    if diff.get("shared_paths"):
        print("Shared paths: " + ", ".join(diff["shared_paths"]))
    if diff.get("shared_keywords"):
        print("Shared keywords: " + ", ".join(diff["shared_keywords"]))
    if diff.get("more_severe"):
        print(f"More severe: {diff['more_severe']}")
    return 0


# ---------------------------------------------------------------------------
# Subcommand: update
# ---------------------------------------------------------------------------


def cmd_update(args):
    """Update touched_paths/roots_touched/verification_targets in TASK_STATE.yaml."""
    task_dir = _require_task_dir(args)
    repo_root = repo_root_for_task_dir(task_dir)

    explicit_touched = [
        repo_relpath(p, repo_root=repo_root)
        for p in (getattr(args, "touched_path", None) or [])
        if repo_relpath(p, repo_root=repo_root)
    ]
    explicit_roots = [p for p in (getattr(args, "root_touched", None) or []) if p]
    explicit_vt = [
        repo_relpath(p, repo_root=repo_root)
        for p in (getattr(args, "verification_target", None) or [])
        if repo_relpath(p, repo_root=repo_root)
    ]

    if explicit_touched or explicit_roots or explicit_vt:
        merged = merge_task_path_fields(
            task_dir,
            touched_paths=explicit_touched,
            roots_touched=explicit_roots,
            verification_targets=explicit_vt,
        )
        try:
            update_active_task(task_dir, tasks_dir=os.path.dirname(task_dir))
        except Exception:
            pass
        print(f"Updated touched_paths: {len(merged['touched_paths'])} files")
        print(f"Updated roots_touched: {merged['roots_touched']}")
        print(
            f"Updated verification_targets: {len(merged['verification_targets'])} files"
        )
        return 0

    if getattr(args, "from_git_diff", False):
        result = subprocess.run(
            ["git", "diff", "--name-only", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        if result.returncode != 0:
            result = subprocess.run(
                ["git", "diff", "--name-only"],
                capture_output=True,
                text=True,
                cwd=repo_root,
            )
        changed_files = [
            repo_relpath(f.strip(), repo_root=repo_root)
            for f in result.stdout.strip().splitlines()
            if f.strip()
        ]

        if not changed_files:
            print("No changed files detected from git diff")
            return 0

        merged = merge_task_path_fields(
            task_dir,
            touched_paths=changed_files,
            roots_touched=extract_roots(changed_files),
            verification_targets=[f for f in changed_files if not is_doc_path(f)],
        )

        team_state = None
        try:
            team_state = sync_team_status(task_dir)
        except Exception:
            team_state = None

        try:
            write_failure_case_snapshot(task_dir)
        except Exception:
            pass

        try:
            update_active_task(task_dir, tasks_dir=os.path.dirname(task_dir))
        except Exception:
            pass

        print(f"Updated touched_paths: {len(merged['touched_paths'])} files")
        print(f"Updated roots_touched: {merged['roots_touched']}")
        print(
            f"Updated verification_targets: {len(merged['verification_targets'])} files"
        )
        if team_state and team_state.get("orchestration_mode") == "team":
            print(
                f"Team status: {team_state.get('derived_status', team_state.get('current_status', 'n/a'))} (artifact-driven)"
            )
    else:
        print(
            "Nothing to update. Use --from-git-diff or explicit --touched-path / --verification-target values."
        )

    return 0


# ---------------------------------------------------------------------------
# Subcommand: verify
# ---------------------------------------------------------------------------


def cmd_verify(args):
    """Run verification suite (delegates to verify.py)."""
    task_dir = _require_task_dir(args)
    repo_root = repo_root_for_task_dir(task_dir)

    verify_script = os.path.join(SCRIPT_DIR, "verify.py")
    result = subprocess.run(
        ["python3", verify_script, "--task-dir", task_dir],
        cwd=repo_root,
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Subcommand: close
# ---------------------------------------------------------------------------


def cmd_close(args):
    """Run completion gate (delegates to task_completed_gate.py)."""
    task_dir = _require_task_dir(args)
    repo_root = repo_root_for_task_dir(task_dir)

    gate_script = os.path.join(SCRIPT_DIR, "task_completed_gate.py")
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)

    env = os.environ.copy()
    env["HARNESS_TASK_ID"] = task_id
    env["HARNESS_SKIP_STDIN"] = "1"

    try:
        sync_team_status(task_dir)
    except Exception:
        pass

    result = subprocess.run(
        ["python3", gate_script],
        env=env,
        cwd=repo_root,
    )
    try:
        state_status = (yaml_field("status", state_file) or "").strip().lower()
        if result.returncode == 0 or state_status in ("closed", "archived", "stale"):
            clear_active_task(task_dir=task_dir, tasks_dir=os.path.dirname(task_dir))
        else:
            update_active_task(task_dir, tasks_dir=os.path.dirname(task_dir))
    except Exception:
        pass
    if result.returncode == 0:
        print(f"close gate PASSED for {task_id}")
    else:
        print(
            f"close gate BLOCKED for {task_id} (exit {result.returncode})",
            file=sys.stderr,
        )
    return result.returncode


# ---------------------------------------------------------------------------
# Subcommand: artifact
# ---------------------------------------------------------------------------


def cmd_artifact(args):
    """Delegate to write_artifact.py with remaining args."""
    write_artifact_script = os.path.join(SCRIPT_DIR, "write_artifact.py")

    extra = getattr(args, "artifact_args", [])

    env = os.environ.copy()
    env["HARNESS_SKIP_PREWRITE"] = "1"

    result = subprocess.run(
        ["python3", write_artifact_script] + extra,
        env=env,
        cwd=_repo_root(),
    )
    return result.returncode


# ---------------------------------------------------------------------------
# Subcommand: replay
# ---------------------------------------------------------------------------


def cmd_replay(args):
    """Run the curated golden replay corpus."""
    return run_golden_replay(
        corpus_path=getattr(args, "corpus", None),
        repo_root=_repo_root(),
        kind_filters=getattr(args, "kind", None),
        case_ids=getattr(args, "case_ids", None),
        json_output=bool(getattr(args, "json", False)),
    )


# ---------------------------------------------------------------------------
# Argument parser
# ---------------------------------------------------------------------------


def build_parser():
    parser = argparse.ArgumentParser(
        prog="hctl",
        description="harness CLI control plane — task lifecycle management",
    )
    subparsers = parser.add_subparsers(dest="subcommand", metavar="SUBCOMMAND")
    subparsers.required = True

    p_start = subparsers.add_parser(
        "start", help="compile routing into TASK_STATE.yaml"
    )
    p_start.add_argument(
        "--task-dir",
        metavar="DIR",
        help="canonical task directory (doc/harness/tasks/TASK__<id>)",
    )
    p_start.add_argument(
        "--task-id",
        metavar="TASK_ID",
        help="canonical task id or slug to bootstrap (TASK__ prefix optional)",
    )
    p_start.add_argument(
        "--slug",
        metavar="SLUG",
        help="task slug to bootstrap under doc/harness/tasks/TASK__<slug>",
    )
    p_start.add_argument(
        "--request-file",
        metavar="FILE",
        help="optional REQUEST.md for request-text heuristics",
    )
    p_start.set_defaults(func=cmd_start)

    p_ctx = subparsers.add_parser("context", help="emit compact task pack")
    p_ctx.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_ctx.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_ctx.add_argument(
        "--team-worker",
        metavar="WORKER",
        help="optional team worker id for personalized context",
    )
    p_ctx.add_argument(
        "--agent-name",
        metavar="NAME",
        help="optional agent name override for personalized context",
    )
    p_ctx.set_defaults(func=cmd_context)

    p_team_bootstrap = subparsers.add_parser(
        "team-bootstrap", help="generate team worker bootstrap specs"
    )
    p_team_bootstrap.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_team_bootstrap.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_team_bootstrap.add_argument(
        "--write-files",
        action="store_true",
        help="materialize team/bootstrap briefs + env files",
    )
    p_team_bootstrap.set_defaults(func=cmd_team_bootstrap)

    p_team_dispatch = subparsers.add_parser(
        "team-dispatch", help="generate provider launch artifacts for a team task"
    )
    p_team_dispatch.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_team_dispatch.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_team_dispatch.add_argument(
        "--write-files",
        action="store_true",
        help="materialize provider prompts + run helpers",
    )
    p_team_dispatch.set_defaults(func=cmd_team_dispatch)

    p_team_launch = subparsers.add_parser(
        "team-launch", help="prepare or execute the default team fan-out entrypoint"
    )
    p_team_launch.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_team_launch.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_team_launch.add_argument(
        "--write-files", action="store_true", help="materialize the launch manifest"
    )
    p_team_launch.add_argument(
        "--execute",
        action="store_true",
        help="spawn the selected launcher in detached mode",
    )
    p_team_launch.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="do not regenerate stale/missing bootstrap or dispatch artifacts",
    )
    p_team_launch.add_argument(
        "--target",
        choices=("auto", "provider", "implementers"),
        default="auto",
        help="which launcher to prepare or execute",
    )
    p_team_launch.set_defaults(func=cmd_team_launch)

    p_team_relaunch = subparsers.add_parser(
        "team-relaunch", help="prepare or execute a worker/phase-specific relaunch"
    )
    p_team_relaunch.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_team_relaunch.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_team_relaunch.add_argument(
        "--write-files", action="store_true", help="materialize the relaunch manifest"
    )
    p_team_relaunch.add_argument(
        "--execute",
        action="store_true",
        help="spawn the selected worker phase in detached mode",
    )
    p_team_relaunch.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="do not regenerate stale/missing bootstrap or dispatch artifacts",
    )
    p_team_relaunch.add_argument(
        "--worker",
        metavar="WORKER",
        help="target worker id (defaults to the current or pending worker)",
    )
    p_team_relaunch.add_argument(
        "--phase",
        metavar="PHASE",
        default="auto",
        help="target phase: auto|implement|synthesis|final_runtime_verification|documentation_sync|documentation_review|handoff_refresh",
    )
    p_team_relaunch.set_defaults(func=cmd_team_relaunch)

    p_hist = subparsers.add_parser(
        "history", help="list failure cases across task history"
    )
    p_hist.add_argument(
        "--tasks-dir",
        metavar="DIR",
        help="optional tasks directory (defaults to doc/harness/tasks)",
    )
    p_hist.add_argument(
        "--lane", metavar="LANE", default="", help="optional lane filter"
    )
    p_hist.add_argument("--limit", type=int, default=20, help="maximum cases to show")
    p_hist.add_argument(
        "--min-failure-signals",
        type=int,
        default=1,
        help="minimum failure_signals required",
    )
    p_hist.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_hist.set_defaults(func=cmd_history)

    p_top = subparsers.add_parser(
        "top-failures", help="show top similar failures for a task"
    )
    p_top.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_top.add_argument(
        "--tasks-dir",
        metavar="DIR",
        help="optional tasks directory (defaults to doc/harness/tasks)",
    )
    p_top.add_argument(
        "--limit", type=int, default=3, help="maximum similar failures to show"
    )
    p_top.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_top.set_defaults(func=cmd_top_failures)

    p_diff = subparsers.add_parser("diff-case", help="diff two failure cases")
    p_diff.add_argument(
        "--case-a", required=True, metavar="TASK_ID", help="first task id or task dir"
    )
    p_diff.add_argument(
        "--case-b", required=True, metavar="TASK_ID", help="second task id or task dir"
    )
    p_diff.add_argument(
        "--tasks-dir",
        metavar="DIR",
        help="optional tasks directory (defaults to doc/harness/tasks)",
    )
    p_diff.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_diff.set_defaults(func=cmd_diff_case)

    p_upd = subparsers.add_parser("update", help="sync task state from git diff")
    p_upd.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_upd.add_argument(
        "--from-git-diff",
        action="store_true",
        help="update touched_paths from `git diff --name-only HEAD`",
    )
    p_upd.add_argument(
        "--touched-path",
        action="append",
        default=[],
        metavar="PATH",
        help="manually add a touched path (repeatable)",
    )
    p_upd.add_argument(
        "--root-touched",
        action="append",
        default=[],
        metavar="ROOT",
        help="manually add a touched root (repeatable)",
    )
    p_upd.add_argument(
        "--verification-target",
        action="append",
        default=[],
        metavar="PATH",
        help="manually add a runtime verification target (repeatable)",
    )
    p_upd.set_defaults(func=cmd_update)

    p_ver = subparsers.add_parser("verify", help="run verification suite")
    p_ver.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_ver.set_defaults(func=cmd_verify)

    p_cls = subparsers.add_parser("close", help="run task completion gate")
    p_cls.add_argument(
        "--task-dir",
        required=True,
        metavar="DIR",
        help="task directory containing TASK_STATE.yaml",
    )
    p_cls.set_defaults(func=cmd_close)

    p_replay = subparsers.add_parser("replay", help="run golden behavior replay corpus")
    p_replay.add_argument(
        "--corpus",
        metavar="FILE",
        help="optional JSON corpus path (defaults to doc/harness/replays/golden-corpus.json)",
    )
    p_replay.add_argument(
        "--kind",
        action="append",
        choices=(
            "routing",
            "close_gate",
            "prompt_notes",
            "next_step",
            "handoff",
            "context",
            "team_launch",
            "team_relaunch",
        ),
        help="filter by case kind (repeatable)",
    )
    p_replay.add_argument(
        "--case",
        action="append",
        dest="case_ids",
        metavar="ID",
        help="filter by case id (repeatable or comma-separated)",
    )
    p_replay.add_argument(
        "--json", action="store_true", help="output machine-readable JSON"
    )
    p_replay.set_defaults(func=cmd_replay)

    p_art = subparsers.add_parser(
        "artifact", help="write harness artifact (wraps write_artifact.py)"
    )
    p_art.add_argument(
        "artifact_args",
        nargs=argparse.REMAINDER,
        help="arguments passed through to write_artifact.py",
    )
    p_art.set_defaults(func=cmd_artifact)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
