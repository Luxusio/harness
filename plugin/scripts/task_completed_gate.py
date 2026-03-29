#!/usr/bin/env python3
import sys, os, re, subprocess
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (read_hook_input, json_field, json_array, yaml_field, yaml_array,
                  manifest_field, is_browser_first_project, is_doc_path,
                  extract_roots, TASK_DIR, MANIFEST, now_iso)

# TaskCompleted hook — completion firewall.
# BLOCKING: exit 2 rejects completion when verdicts are missing.
# stdin: JSON | exit 0: allow | exit 2: BLOCK

data = read_hook_input()

task_id = json_field(data, "task_id") or os.environ.get("HARNESS_TASK_ID", "")

if not task_id:
    sys.exit(0)

target = os.path.join(TASK_DIR, task_id)
if not os.path.isdir(target):
    sys.exit(0)

failures = []

# --- TASK_STATE.yaml required ---
state_file = os.path.join(target, "TASK_STATE.yaml")
is_mutating = True

if not os.path.exists(state_file):
    failures.append("missing TASK_STATE.yaml")
else:
    content = open(state_file).read()

    if re.search(r'^status: blocked_env', content, re.MULTILINE):
        failures.append("status is blocked_env — resolve the blocker first")

    if re.search(r'^mutates_repo: false', content, re.MULTILINE):
        is_mutating = False

    # --- Auto-populate touched_paths if empty ---
    existing_touched = yaml_array("touched_paths", state_file)
    if not existing_touched:
        # Attempt to derive from git diff against previous commit
        try:
            result = subprocess.run(
                ["git", "diff", "--name-only", "HEAD~1"],
                capture_output=True, text=True
            )
            auto_touched = result.stdout.strip()
        except Exception:
            auto_touched = ""

        if not auto_touched:
            try:
                result = subprocess.run(
                    ["git", "diff", "--name-only"],
                    capture_output=True, text=True
                )
                auto_touched = result.stdout.strip()
            except Exception:
                auto_touched = ""

        if auto_touched:
            paths = [p for p in auto_touched.splitlines() if p.strip()]

            # Build inline YAML array string and replace touched_paths
            inline = ", ".join(f'"{p}"' for p in paths)
            content = content.replace("touched_paths: []", f"touched_paths: [{inline}]")

            # Derive and populate roots_touched
            roots = list(extract_roots(paths))
            inline_roots = ", ".join(f'"{r}"' for r in roots)
            content = content.replace("roots_touched: []", f"roots_touched: [{inline_roots}]")

            # Derive and populate verification_targets (non-doc paths only)
            vt_paths = [p for p in paths if not is_doc_path(p)]
            if vt_paths:
                inline_vt = ", ".join(f'"{p}"' for p in vt_paths)
                content = content.replace("verification_targets: []", f"verification_targets: [{inline_vt}]")

            with open(state_file, "w") as f:
                f.write(content)

            print("AUTO-POPULATED: touched_paths, roots_touched, verification_targets from git diff")
        else:
            print("WARN: touched_paths is empty and git diff returned no files — invalidation precision will be reduced")

# --- PLAN.md required ---
if not os.path.exists(os.path.join(target, "PLAN.md")):
    failures.append("missing PLAN.md")

# --- CRITIC__plan.md with verdict: PASS ---
critic_plan = os.path.join(target, "CRITIC__plan.md")
if not os.path.exists(critic_plan):
    failures.append("missing plan critic verdict (CRITIC__plan.md)")
else:
    critic_content = open(critic_plan).read()
    if not re.search(r'^verdict:\s*PASS\s*$', critic_content, re.MULTILINE):
        failures.append("plan critic did not PASS")

# --- HANDOFF.md required ---
if not os.path.exists(os.path.join(target, "HANDOFF.md")):
    failures.append("missing HANDOFF.md")

# --- Repo-mutating task requirements ---
if is_mutating:
    # --- DOC_SYNC.md required for repo-mutating tasks ---
    if not os.path.exists(os.path.join(target, "DOC_SYNC.md")):
        failures.append("repo-mutating task requires DOC_SYNC.md (may contain 'none' if no docs changed)")

    # --- Runtime critic required for repo-mutating tasks ---
    critic_runtime = os.path.join(target, "CRITIC__runtime.md")
    if not os.path.exists(critic_runtime):
        failures.append("repo-mutating task needs runtime critic verdict (CRITIC__runtime.md)")
    else:
        runtime_content = open(critic_runtime).read()
        if not re.search(r'^verdict:\s*PASS\s*$', runtime_content, re.MULTILINE):
            failures.append("runtime critic did not PASS")

# --- Document critic when DOC_SYNC.md exists with content other than "none",
#     or when doc_changes_detected: true in TASK_STATE.yaml ---
doc_critic_needed = False

doc_sync = os.path.join(target, "DOC_SYNC.md")
if os.path.exists(doc_sync):
    # Check if DOC_SYNC.md has meaningful content (not just "none")
    doc_sync_content = ""
    for line in open(doc_sync):
        if not line.startswith("#"):
            doc_sync_content += line
    doc_sync_content = doc_sync_content.replace(" ", "").replace("\t", "").replace("\n", "")
    if doc_sync_content and doc_sync_content != "none":
        doc_critic_needed = True

if os.path.exists(state_file):
    state_content = open(state_file).read()
    if re.search(r'^doc_changes_detected: true', state_content, re.MULTILINE):
        doc_critic_needed = True

if doc_critic_needed:
    critic_doc = os.path.join(target, "CRITIC__document.md")
    if not os.path.exists(critic_doc):
        failures.append("doc changes detected — needs document critic verdict (CRITIC__document.md)")
    else:
        doc_content = open(critic_doc).read()
        if not re.search(r'^verdict:\s*PASS\s*$', doc_content, re.MULTILINE):
            failures.append("document critic did not PASS")

# --- Team mode gates ---
if os.path.exists(state_file):
    orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    if orch_mode == "team":
        if not os.path.exists(os.path.join(target, "TEAM_PLAN.md")):
            failures.append("team task requires TEAM_PLAN.md")
        if not os.path.exists(os.path.join(target, "TEAM_SYNTHESIS.md")):
            failures.append("team task requires TEAM_SYNTHESIS.md")
        team_status_val = yaml_field("team_status", state_file) or ""
        if team_status_val not in ("complete", "fallback"):
            failures.append(f"team_status must be 'complete' or 'fallback', got '{team_status_val}'")
        if team_status_val == "fallback":
            fallback_val = yaml_field("fallback_used", state_file) or "none"
            if fallback_val == "none":
                failures.append("team_status is 'fallback' but fallback_used is 'none'")

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
            # Show grouped by status for clarity
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
        # yaml not available — fallback to line-based parse
        try:
            with open(checks_file) as f:
                lines = f.readlines()
            open_ids = []
            current_id = None
            current_status = None
            for line in lines:
                import re as _re
                m_id = _re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
                if m_id:
                    if current_id and current_status != "passed":
                        open_ids.append(f"{current_id} [{current_status or 'unknown'}]")
                    current_id = m_id.group(1).strip().strip('"').strip("'")
                    current_status = None
                m_st = _re.match(r"^\s+status\s*:\s*(.+)", line)
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
# Attempt to recover suspect notes whose invalidated_by_paths overlap with this task.
# Failures here do not block task completion.
try:
    from note_reverify import reverify_suspect_notes
    results = reverify_suspect_notes(target)
    recovered = sum(1 for _, s in results if s == "recovered")
    if recovered:
        print(f"NOTE FRESHNESS: {recovered} note(s) restored to current via reverify")
except Exception as _e:
    # Non-blocking — reverify errors must never prevent task completion
    print(f"NOTE REVERIFY: skipped ({_e})")

sys.exit(0)
