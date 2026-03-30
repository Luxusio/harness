#!/usr/bin/env python3
import sys, os, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, hook_json_get, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso,
                  get_workflow_violations, get_agent_run_count,
                  needs_document_critic, is_handoff_stub)

# TaskCompleted hook — completion firewall.
# BLOCKING: exit 2 rejects completion when verdicts are missing.
# stdin: JSON | exit 0: allow | exit 2: BLOCK


def compute_completion_failures(task_dir):
    """Pure function: compute and return list of failure strings for a task.

    Uses TASK_STATE.yaml verdicts as source of truth — not artifact file text.
    Stale PASS (artifact says PASS but YAML says pending) is caught here.
    Also checks provenance (agent run counts) and workflow violations.

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

    # --- HANDOFF.md required and must not be an unfilled stub ---
    handoff_file = os.path.join(task_dir, "HANDOFF.md")
    if not os.path.exists(handoff_file):
        failures.append("missing HANDOFF.md")
    elif is_handoff_stub(handoff_file):
        failures.append(
            "HANDOFF.md is an unfilled stub — add verification breadcrumbs before close"
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
        has_provenance = bool(
            re.search(r"^agent_run_developer_count:", state_content, re.MULTILINE)
        )
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

    # --- CHECKS.yaml open criteria warning (non-blocking) ---
    checks_file = os.path.join(target, "CHECKS.yaml")
    if os.path.exists(checks_file):
        try:
            import yaml
            with open(checks_file) as f:
                checks_data = yaml.safe_load(f)
            checks = (checks_data or {}).get("checks", []) or []
            open_criteria = [c for c in checks if c.get("status") != "passed"]
            if open_criteria:
                by_status = {}
                for c in open_criteria:
                    s = c.get("status", "unknown")
                    by_status.setdefault(s, []).append(c)
                print(f"WARN: {len(open_criteria)} open acceptance criterion/criteria in CHECKS.yaml:")
                for status_label, items in sorted(by_status.items()):
                    ids = ", ".join(c.get("id", "?") for c in items)
                    print(f"  [{status_label}] {ids}")
                    for c in items:
                        title = c.get("title", "")
                        if title:
                            print(f"    - {c.get('id', '?')}: {title}")
        except Exception as e:
            try:
                with open(checks_file) as f:
                    lines = f.readlines()
                open_ids = []
                current_id = None
                current_status = None
                for line in lines:
                    m_id = re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
                    if m_id:
                        if current_id and current_status != "passed":
                            open_ids.append(f"{current_id} [{current_status or 'unknown'}]")
                        current_id = m_id.group(1).strip().strip('"').strip("'")
                        current_status = None
                    m_st = re.match(r"^\s+status\s*:\s*(.+)", line)
                    if m_st:
                        current_status = m_st.group(1).strip().strip('"').strip("'")
                if current_id and current_status != "passed":
                    open_ids.append(f"{current_id} [{current_status or 'unknown'}]")
                if open_ids:
                    print(f"WARN: {len(open_ids)} open criteria in CHECKS.yaml: {', '.join(open_ids)}")
            except Exception:
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
