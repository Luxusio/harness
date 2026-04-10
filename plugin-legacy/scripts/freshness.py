#!/usr/bin/env python3
"""Freshness warning: detect stale PASS verdicts and drifted state."""
import json
import os
import subprocess
import sys
from datetime import datetime

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import yaml_array, is_doc_path, verdict_freshness

def get_git_changed_since(timestamp):
    """Get files changed since a timestamp via git."""
    try:
        result = subprocess.run(
            ["git", "log", "--since={}".format(timestamp), "--name-only", "--pretty=format:"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0:
            files = [f.strip() for f in result.stdout.strip().split("\n") if f.strip()]
            return list(set(files))
    except Exception:
        pass
    return []

def check_stale_verdicts(task_dir="doc/harness/tasks"):
    """Check for PASS verdicts that may be stale due to file changes."""
    warnings = []
    if not os.path.isdir(task_dir):
        return warnings

    for entry in os.listdir(task_dir):
        if not entry.startswith("TASK__"):
            continue
        task_path = os.path.join(task_dir, entry)
        state_file = os.path.join(task_path, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue

        try:
            with open(state_file) as f:
                content = f.read()
        except Exception:
            continue

        # Skip closed tasks
        if any("status: {}".format(s) in content for s in ["closed", "archived", "stale"]):
            continue

        runtime_freshness = verdict_freshness(state_file, "runtime_verdict")
        document_freshness = verdict_freshness(state_file, "document_verdict")

        # First honor explicit freshness fields written by the hook layer.
        if "runtime_verdict: PASS" in content and runtime_freshness == "stale":
            warnings.append({
                "type": "stale_pass",
                "task": entry,
                "verdict": "runtime",
                "changed_files": [],
                "reason": "runtime_verdict_freshness is stale"
            })
        elif "runtime_verdict: PASS" in content:
            critic_file = os.path.join(task_path, "CRITIC__runtime.md")
            if os.path.isfile(critic_file):
                verified_at = extract_verified_at(critic_file)
                if verified_at:
                    changed = get_git_changed_since(verified_at)
                    # Check overlap with verification_targets
                    targets = yaml_array("verification_targets", state_file)
                    overlap = find_overlap(changed, targets)
                    if overlap:
                        warnings.append({
                            "type": "stale_pass",
                            "task": entry,
                            "verdict": "runtime",
                            "changed_files": overlap[:5],
                            "reason": "Files in verification_targets changed after runtime PASS"
                        })

        # Check document verdict
        if "document_verdict: PASS" in content and document_freshness == "stale":
            warnings.append({
                "type": "stale_pass",
                "task": entry,
                "verdict": "document",
                "changed_files": [],
                "reason": "document_verdict_freshness is stale"
            })
        elif "document_verdict: PASS" in content:
            critic_file = os.path.join(task_path, "CRITIC__document.md")
            if os.path.isfile(critic_file):
                verified_at = extract_verified_at(critic_file)
                if verified_at:
                    changed = get_git_changed_since(verified_at)
                    doc_changed = [f for f in changed if is_doc_path(f)]
                    if doc_changed:
                        warnings.append({
                            "type": "stale_pass",
                            "task": entry,
                            "verdict": "document",
                            "changed_files": doc_changed[:5],
                            "reason": "Doc files changed after document PASS"
                        })

    return warnings

def check_doc_sync_staleness(task_dir="doc/harness/tasks"):
    """Check if DOC_SYNC.md content is stale."""
    warnings = []
    if not os.path.isdir(task_dir):
        return warnings

    for entry in os.listdir(task_dir):
        if not entry.startswith("TASK__"):
            continue
        doc_sync = os.path.join(task_dir, entry, "DOC_SYNC.md")
        if not os.path.isfile(doc_sync):
            continue
        state_file = os.path.join(task_dir, entry, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        try:
            with open(state_file) as f:
                state = f.read()
            if any("status: {}".format(s) in state for s in ["closed", "archived", "stale"]):
                continue
        except Exception:
            continue

        # Check if doc_sync file is older than recent changes
        try:
            sync_mtime = os.path.getmtime(doc_sync)
            sync_time = datetime.fromtimestamp(sync_mtime).isoformat()
            changed = get_git_changed_since(sync_time)
            doc_changed = [f for f in changed if is_doc_path(f)]
            if doc_changed:
                warnings.append({
                    "type": "stale_doc_sync",
                    "task": entry,
                    "changed_files": doc_changed[:3],
                    "reason": "Doc files changed after DOC_SYNC.md was written"
                })
        except Exception:
            continue

    return warnings

def extract_verified_at(filepath):
    """Extract verified_at timestamp from a critic file."""
    try:
        with open(filepath) as f:
            for line in f:
                if line.startswith("verified_at:"):
                    return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return None

def find_overlap(changed_files, targets):
    """Find files in changed_files that overlap with targets (prefix match)."""
    overlap = []
    for cf in changed_files:
        for t in targets:
            if cf == t or cf.startswith(t + "/") or t.startswith(cf + "/"):
                overlap.append(cf)
                break
    return overlap

def main():
    warnings = []
    warnings.extend(check_stale_verdicts())
    warnings.extend(check_doc_sync_staleness())

    if warnings:
        print(json.dumps({"warnings": warnings}, indent=2))
    # Exit 0 always — warnings only
    sys.exit(0)

if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
