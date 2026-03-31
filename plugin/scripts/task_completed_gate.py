#!/usr/bin/env python3
import sys, os, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, hook_json_get, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso,
                  get_workflow_violations, get_agent_run_count,
                  needs_document_critic, is_handoff_stub,
                  parse_checks_close_gate)

# TaskCompleted hook — completion firewall.
# BLOCKING: exit 2 rejects completion when verdicts are missing.
# stdin: JSON | exit 0: allow | exit 2: BLOCK


def _parse_checks_yaml(checks_file):
    """Parse CHECKS.yaml using stdlib-only line-based regex (no pyyaml dependency).

    Returns list of dicts with 'id', 'status', and 'title' keys.
    """
    criteria = []
    try:
        with open(checks_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return criteria

    current = {}
    for line in lines:
        # New criterion entry (starts with "  - id:" or "- id:")
        m_id = re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
        if m_id:
            if current.get("id"):
                criteria.append(current)
            current = {
                "id": m_id.group(1).strip().strip('"').strip("'"),
                "status": None,
                "title": "",
            }
            continue
        # Status field within a criterion
        m_st = re.match(r"^\s+status\s*:\s*(.+)", line)
        if m_st and current.get("id"):
            current["status"] = m_st.group(1).strip().strip('"').strip("'")
            continue
        # Title field within a criterion
        m_title = re.match(r"^\s+title\s*:\s*(.+)", line)
        if m_title and current.get("id"):
            current["title"] = m_title.group(1).strip().strip('"').strip("'")
            continue

    # Don't forget last entry
    if current.get("id"):
        criteria.append(current)

    return criteria


def _check_artifact_provenance(task_dir):
    """Check provenance sidecars (.meta.json) for protected artifacts.

    Returns list of failure strings. Empty means all OK.
    Only enforced when artifact_provenance_required: true.
    """
    failures = []
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    provenance_required = yaml_field("artifact_provenance_required", state_file)
    if provenance_required != "true":
        return failures

    try:
        from provenance_helpers import check_all_provenance
        failures = check_all_provenance(task_dir)
    except ImportError:
        pass  # provenance_helpers not available — skip

    return failures


def _strict_close_gate_failures(checks_file, criteria):
    """Check strict_high_risk close gate: all criteria must be 'passed'.

    Returns list of failure strings grouped by status.
    """
    failures = []
    non_passed = [c for c in criteria if c.get("status") != "passed"]
    if not non_passed:
        return failures

    # Group by status for clear messaging
    by_status = {}
    for c in non_passed:
        s = c.get("status") or "unknown"
        by_status.setdefault(s, []).append(c)

    parts = []
    for status_label in ("failed", "implemented_candidate", "planned", "blocked", "unknown"):
        items = by_status.get(status_label, [])
        if not items:
            continue
        ids = ", ".join(c.get("id", "?") for c in items)
        detail_lines = []
        for c in items:
            title = c.get("title", "")
            if title:
                detail_lines.append(f"    {c.get('id', '?')}: {title}")
        status_desc = {
            "failed": "critic FAIL",
            "implemented_candidate": "implementation claimed but not critic-verified",
            "planned": "not yet implemented or verified",
            "blocked": "env/dependency blocker unresolved",
            "unknown": "status unknown",
        }.get(status_label, status_label)
        part = f"  [{status_label}] {ids} — {status_desc}"
        if detail_lines:
            part += "\n" + "\n".join(detail_lines)
        parts.append(part)

    count = len(non_passed)
    failures.append(
        f"STRICT CLOSE GATE (close_gate: strict_high_risk): "
        f"{count} criterion/criteria not passed in CHECKS.yaml — "
        f"all must be 'passed' for high-risk task close:\n" + "\n".join(parts)
    )

    return failures


def _get_blocking_open_complaints(complaints_file):
    """Parse COMPLAINTS.yaml and return list of IDs where blocks_close: true AND status: open.

    Uses stdlib regex only — no pyyaml dependency.
    Returns list of complaint ID strings. Empty list if none found or parse error.
    """
    blocking = []
    try:
        with open(complaints_file, "r", encoding="utf-8") as f:
            content = f.read()
    except OSError:
        return blocking

    # Split into entry blocks by finding "  - id:" markers
    current_id = None
    current_status = None
    current_blocks = True  # default blocks_close is true

    for line in content.splitlines():
        # New entry
        m_id = re.match(r'^\s*-?\s*id\s*:\s*(.+)', line)
        if m_id:
            # Flush previous entry
            if current_id and current_status == "open" and current_blocks:
                blocking.append(current_id)
            current_id = m_id.group(1).strip().strip('"').strip("'")
            current_status = None
            current_blocks = True  # default
            continue

        m_status = re.match(r'^\s+status\s*:\s*(.+)', line)
        if m_status and current_id:
            current_status = m_status.group(1).strip().strip('"').strip("'")
            continue

        m_blocks = re.match(r'^\s+blocks_close\s*:\s*(.+)', line)
        if m_blocks and current_id:
            val = m_blocks.group(1).strip().lower()
            current_blocks = val not in ("false", "no", "0")
            continue

    # Flush last entry
    if current_id and current_status == "open" and current_blocks:
        blocking.append(current_id)

    return blocking


def compute_completion_failures(task_dir):
    """Pure function: compute and return list of failure strings for a task.

    Uses TASK_STATE.yaml verdicts as source of truth — not artifact file text.
    Stale PASS (artifact says PASS but YAML says pending) is caught here.
    Also checks provenance (agent run counts), workflow violations,
    CHECKS.yaml failed criteria (hard threshold per Anthropic requirement),
    strict close gate for high-risk tasks,
    artifact authorship, investigate RESULT gate, capability/compliance gate,
    and directive capture gate.

    Does NOT call git or do side effects. Safe to call from tests.
    """
    failures = []
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")

    if not os.path.exists(state_file):
        failures.append("missing TASK_STATE.yaml")
        return failures

    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            state_content = fh.read()
    except OSError:
        failures.append("cannot read TASK_STATE.yaml")
        return failures

    # --- blocked_env cannot close ---
    if re.search(r"^status:\s*blocked_env", state_content, re.MULTILINE):
        failures.append("status is blocked_env — resolve the blocker first")

    is_mutating = not re.search(r"^mutates_repo:\s*false", state_content, re.MULTILINE)

    # --- workflow violations block close ---
    violations = get_workflow_violations(task_dir)
    if violations:
        failures.append(f"workflow_violations present: {', '.join(violations)}")

    # --- execution_mode / orchestration_mode must be set for mutating tasks ---
    if is_mutating:
        exec_mode = yaml_field("execution_mode", state_file)
        orch_mode = yaml_field("orchestration_mode", state_file)
        if exec_mode == "pending":
            failures.append(
                "execution_mode is 'pending' — must be explicitly set (light|standard|sprinted) before close"
            )
        if orch_mode == "pending":
            failures.append(
                "orchestration_mode is 'pending' — must be explicitly set (solo|subagents|team) before close"
            )

    # --- PLAN.md required ---
    if not os.path.exists(os.path.join(task_dir, "PLAN.md")):
        failures.append("missing PLAN.md")

    # --- plan_verdict must be PASS in TASK_STATE.yaml (YAML-driven) ---
    plan_verdict_val = yaml_field("plan_verdict", state_file)
    if plan_verdict_val != "PASS":
        failures.append(
            f"plan_verdict is '{plan_verdict_val}' in TASK_STATE.yaml (not PASS)"
        )

    # --- CRITIC__plan.md required ---
    if not os.path.exists(os.path.join(task_dir, "CRITIC__plan.md")):
        failures.append("missing plan critic verdict (CRITIC__plan.md)")

    # --- critic-plan must have run at least once (hard requirement) ---
    has_provenance = bool(
        re.search(r"^agent_run_developer_count:", state_content, re.MULTILINE)
    )
    if has_provenance:
        critic_plan_count = get_agent_run_count(task_dir, "critic-plan")
        if critic_plan_count == 0:
            failures.append(
                "critic-plan has no recorded runs — plan critic must be invoked"
            )

    # --- HANDOFF.md required and must not be an unfilled stub ---
    handoff_file = os.path.join(task_dir, "HANDOFF.md")
    if not os.path.exists(handoff_file):
        failures.append("missing HANDOFF.md")
    elif is_handoff_stub(handoff_file):
        failures.append(
            "HANDOFF.md is an unfilled stub — add verification breadcrumbs before close"
        )

    # --- Investigate lane: RESULT.md required ---
    lane = yaml_field("lane", state_file)
    result_required = yaml_field("result_required", state_file)
    if lane == "investigate" or result_required == "true":
        if not os.path.exists(os.path.join(task_dir, "RESULT.md")):
            failures.append(
                "investigate task missing RESULT.md — summarize findings before close"
            )

    # --- Repo-mutating requirements ---
    if is_mutating:
        # DOC_SYNC.md required
        if not os.path.exists(os.path.join(task_dir, "DOC_SYNC.md")):
            failures.append(
                "repo-mutating task requires DOC_SYNC.md (may contain 'none' if no docs changed)"
            )

        # runtime_verdict must be PASS in YAML — stale artifact PASS does not count
        runtime_verdict_val = yaml_field("runtime_verdict", state_file)
        if runtime_verdict_val != "PASS":
            failures.append(
                f"runtime_verdict is '{runtime_verdict_val}' in TASK_STATE.yaml (not PASS)"
                " — stale PASS artifact does not count"
            )

        # CRITIC__runtime.md required
        if not os.path.exists(os.path.join(task_dir, "CRITIC__runtime.md")):
            failures.append(
                "repo-mutating task needs runtime critic verdict (CRITIC__runtime.md)"
            )

        # Provenance checks — only enforced when tracking fields are present
        if has_provenance:
            # critic-runtime must have run at least once
            critic_rt_count = get_agent_run_count(task_dir, "critic-runtime")
            if critic_rt_count == 0:
                failures.append(
                    "critic-runtime has no recorded runs — was runtime critic actually invoked?"
                )

            # developer must have run when verification_targets is non-empty
            vt = yaml_array("verification_targets", state_file)
            if vt:
                dev_count = get_agent_run_count(task_dir, "developer")
                if dev_count == 0:
                    failures.append(
                        "runtime paths in verification_targets but developer has no recorded runs"
                    )

            # writer must have run for repo-mutating tasks
            writer_count = get_agent_run_count(task_dir, "writer")
            if writer_count == 0:
                failures.append(
                    "repo-mutating task but writer has no recorded runs"
                )

    # --- Document critic when doc changes detected ---
    doc_critic_needed = needs_document_critic(task_dir)

    if doc_critic_needed:
        # document_verdict must be PASS in YAML
        doc_verdict_val = yaml_field("document_verdict", state_file)
        if doc_verdict_val != "PASS":
            failures.append(
                f"document_verdict is '{doc_verdict_val}' in TASK_STATE.yaml (not PASS)"
                " — stale PASS artifact does not count"
            )

        # CRITIC__document.md required
        critic_doc = os.path.join(task_dir, "CRITIC__document.md")
        if not os.path.exists(critic_doc):
            failures.append(
                "doc changes detected — needs document critic verdict (CRITIC__document.md)"
            )

        # Provenance: critic-document must have run
        has_doc_provenance = bool(
            re.search(r"^agent_run_critic_document_count:", state_content, re.MULTILINE)
        )
        if has_doc_provenance:
            doc_critic_count = get_agent_run_count(task_dir, "critic-document")
            if doc_critic_count == 0:
                failures.append(
                    "doc critic needed but critic-document has no recorded runs"
                )

    # --- Artifact provenance (meta.json sidecars) ---
    provenance_failures = _check_artifact_provenance(task_dir)
    failures.extend(provenance_failures)

    # --- Capability / compliance gate ---
    workflow_mode = yaml_field("workflow_mode", state_file) or "compliant"
    capability = yaml_field("capability_delegation", state_file) or "unknown"
    compliance_claim = yaml_field("compliance_claim", state_file) or "strict"

    if workflow_mode == "compliant" and capability == "unavailable":
        failures.append(
            "workflow_mode=compliant but delegation capability is unavailable — "
            "cannot close as compliant without delegation. "
            "Get user approval for collapsed_approved mode or resolve delegation."
        )

    if workflow_mode == "collapsed_approved":
        if compliance_claim != "degraded":
            failures.append(
                "workflow_mode=collapsed_approved requires compliance_claim=degraded "
                f"but got '{compliance_claim}'"
            )
        collapsed_approved = yaml_field("collapsed_mode_approved", state_file)
        if collapsed_approved != "true":
            failures.append(
                "workflow_mode=collapsed_approved but collapsed_mode_approved is not true"
            )

    # --- Directive capture gate ---
    directive_state = yaml_field("directive_capture_state", state_file)
    if directive_state == "pending":
        failures.append(
            "pending user directives were not captured into durable notes — "
            "writer must promote DIRECTIVES_PENDING.yaml entries before close"
        )

    # --- Complaint close gate ---
    complaint_state = yaml_field("complaint_capture_state", state_file)
    if complaint_state == "pending":
        failures.append(
            "pending user complaint was not triaged — "
            "triage and resolve complaint before close"
        )

    # Check COMPLAINTS.yaml for blocking open complaints
    complaints_file = os.path.join(task_dir, "COMPLAINTS.yaml")
    if os.path.exists(complaints_file):
        try:
            blocking = _get_blocking_open_complaints(complaints_file)
            if blocking:
                ids = ", ".join(blocking)
                failures.append(
                    f"open blocking complaint exists ({ids}) — "
                    "triage and resolve complaint before close"
                )
        except Exception:
            pass

    # --- Team mode gates ---
    orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    if orch_mode == "team":
        if not os.path.exists(os.path.join(task_dir, "TEAM_PLAN.md")):
            failures.append("team task requires TEAM_PLAN.md")
        if not os.path.exists(os.path.join(task_dir, "TEAM_SYNTHESIS.md")):
            failures.append("team task requires TEAM_SYNTHESIS.md")
        team_status_val = yaml_field("team_status", state_file) or ""
        if team_status_val not in ("complete", "fallback"):
            failures.append(
                f"team_status must be 'complete' or 'fallback', got '{team_status_val}'"
            )
        if team_status_val == "fallback":
            fallback_val = yaml_field("fallback_used", state_file) or "none"
            if fallback_val == "none":
                failures.append("team_status is 'fallback' but fallback_used is 'none'")

    # --- CHECKS.yaml gates ---
    checks_file = os.path.join(task_dir, "CHECKS.yaml")
    if os.path.exists(checks_file):
        try:
            criteria = _parse_checks_yaml(checks_file)
            close_gate = parse_checks_close_gate(checks_file)

            if close_gate == "strict_high_risk":
                # Strict gate: ALL criteria must be 'passed'
                strict_failures = _strict_close_gate_failures(checks_file, criteria)
                failures.extend(strict_failures)
            else:
                # Standard gate: only 'failed' criteria block completion
                failed_criteria = [
                    c for c in criteria if c.get("status") == "failed"
                ]
                if failed_criteria:
                    ids = ", ".join(c.get("id", "?") for c in failed_criteria)
                    failures.append(
                        f"CHECKS.yaml has {len(failed_criteria)} failed criterion/criteria: {ids}"
                        " — all acceptance criteria must pass before completion"
                    )
        except Exception:
            pass  # parse errors are non-blocking

    return failures


def main():
    data = read_hook_input()

    # WS-1 fix: hook_json_get(data, field) instead of json_field(data, field)
    task_id = hook_json_get(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

    if not task_id:
        sys.exit(0)

    target = os.path.join(TASK_DIR, task_id)
    if not os.path.isdir(target):
        sys.exit(0)

    # --- Auto-populate touched_paths if empty ---
    state_file = os.path.join(target, "TASK_STATE.yaml")
    if os.path.exists(state_file):
        existing_touched = yaml_array("touched_paths", state_file)
        if not existing_touched:
            auto_touched = ""
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only", "HEAD~1"],
                    capture_output=True, text=True
                )
                auto_touched = result.stdout.strip()
            except Exception:
                pass

            if not auto_touched:
                try:
                    result = subprocess.run(
                        ["git", "diff", "--name-only"],
                        capture_output=True, text=True
                    )
                    auto_touched = result.stdout.strip()
                except Exception:
                    pass

            if auto_touched:
                paths = [p for p in auto_touched.splitlines() if p.strip()]
                try:
                    with open(state_file, "r", encoding="utf-8") as fh:
                        content = fh.read()
                    inline = ", ".join(f'"{p}"' for p in paths)
                    content = content.replace("touched_paths: []", f"touched_paths: [{inline}]")
                    roots = list(extract_roots(paths))
                    inline_roots = ", ".join(f'"{r}"' for r in roots)
                    content = content.replace("roots_touched: []", f"roots_touched: [{inline_roots}]")
                    vt_paths = [p for p in paths if not is_doc_path(p)]
                    if vt_paths:
                        inline_vt = ", ".join(f'"{p}"' for p in vt_paths)
                        content = content.replace(
                            "verification_targets: []", f"verification_targets: [{inline_vt}]"
                        )
                    with open(state_file, "w", encoding="utf-8") as fh:
                        fh.write(content)
                    print("AUTO-POPULATED: touched_paths, roots_touched, verification_targets from git diff")
                except OSError:
                    pass
            else:
                print(
                    "WARN: touched_paths is empty and git diff returned no files"
                    " — invalidation precision will be reduced"
                )

    # --- Run completion checks (pure function) ---
    failures = compute_completion_failures(target)

    # --- CHECKS.yaml open criteria info (non-blocking, for visibility) ---
    checks_file = os.path.join(target, "CHECKS.yaml")
    if os.path.exists(checks_file):
        try:
            criteria = _parse_checks_yaml(checks_file)
            close_gate = parse_checks_close_gate(checks_file)
            # Show non-passed, non-failed criteria as info (failed ones are already in failures)
            # For strict gate, all non-passed are already blocking — show as info anyway
            if close_gate == "strict_high_risk":
                # Info already provided via blocking message
                pass
            else:
                open_criteria = [
                    c for c in criteria
                    if c.get("status") not in ("passed", "failed")
                ]
                if open_criteria:
                    by_status = {}
                    for c in open_criteria:
                        s = c.get("status") or "unknown"
                        by_status.setdefault(s, []).append(c)
                    print(f"INFO: {len(open_criteria)} non-passed acceptance criterion/criteria in CHECKS.yaml:")
                    for status_label, items in sorted(by_status.items()):
                        ids = ", ".join(c.get("id", "?") for c in items)
                        print(f"  [{status_label}] {ids}")
                        for c in items:
                            title = c.get("title", "")
                            if title:
                                print(f"    - {c.get('id', '?')}: {title}")
        except Exception as e:
            print(f"WARN: could not parse CHECKS.yaml: {e}")

    # --- Report and block ---
    if failures:
        print(f"BLOCKED: {task_id}")
        for f in failures:
            print(f"  - {f}")
        sys.exit(2)

    # --- Note auto-reverify (non-blocking, runs only on successful completion) ---
    try:
        from note_reverify import reverify_suspect_notes
        results = reverify_suspect_notes(target)
        recovered = sum(1 for _, s in results if s == "recovered")
        if recovered:
            print(f"NOTE FRESHNESS: {recovered} note(s) restored to current via reverify")
    except Exception as _e:
        print(f"NOTE REVERIFY: skipped ({_e})")

    sys.exit(0)


if __name__ == "__main__":
    main()
