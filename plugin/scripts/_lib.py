#!/usr/bin/env python3
"""Shared helper for harness hook scripts.
Import at the top of each hook script: from _lib import *
"""

import json
import os
import re
import sys
import glob as _glob
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_DIR = "doc/harness/tasks"
MANIFEST = "doc/harness/manifest.yaml"

# ---------------------------------------------------------------------------
# Lazy stdin reader — read once, cache forever
# ---------------------------------------------------------------------------

_HOOK_INPUT = None
_HOOK_INPUT_READ = False


def read_hook_input():
    """Read stdin JSON once and cache it. Returns the raw string or ''."""
    global _HOOK_INPUT, _HOOK_INPUT_READ
    if _HOOK_INPUT_READ:
        return _HOOK_INPUT or ""
    _HOOK_INPUT_READ = True
    if os.environ.get("HARNESS_SKIP_STDIN"):
        _HOOK_INPUT = ""
        return ""
    # Only read if stdin is not a tty (i.e., piped)
    if sys.stdin.isatty():
        _HOOK_INPUT = ""
        return ""
    try:
        import signal

        def _timeout_handler(signum, frame):
            raise TimeoutError

        old = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(1)
        try:
            _HOOK_INPUT = sys.stdin.read()
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
    except (TimeoutError, OSError, AttributeError):
        _HOOK_INPUT = ""
    return _HOOK_INPUT or ""


# ---------------------------------------------------------------------------
# JSON helpers
# ---------------------------------------------------------------------------


def json_field(field, input_str=None):
    """Parse a string field from JSON. Returns str or ''.

    Signature: json_field(field, input_str)
      - field:     the key to look up
      - input_str: the JSON string to parse (defaults to stdin)
    """
    if input_str is None:
        input_str = read_hook_input()
    if not input_str:
        return ""
    try:
        data = json.loads(input_str)
        val = data.get(field)
        if val is None:
            return ""
        return str(val)
    except (json.JSONDecodeError, AttributeError):
        # Fallback regex: "field": "value"
        m = re.search(
            r'"' + re.escape(field) + r'"\s*:\s*"([^"]*)"', input_str
        )
        return m.group(1) if m else ""


def hook_json_get(input_str, field):
    """Parse a field from JSON hook input payload.

    Signature: hook_json_get(input_str, field)
      - input_str: the raw JSON payload from hook stdin
      - field:     the key to extract

    This wrapper has unambiguous argument order (input first, field second)
    to prevent the easy inversion mistake of json_field(data, "field").
    Use this in all hook entrypoints.
    """
    return json_field(field, input_str)


def json_array(field, input_str=None):
    """Parse a JSON array of strings. Returns list of strings."""
    if input_str is None:
        input_str = read_hook_input()
    if not input_str:
        return []
    try:
        data = json.loads(input_str)
        val = data.get(field)
        if not isinstance(val, list):
            return []
        return [str(x) for x in val]
    except (json.JSONDecodeError, AttributeError):
        # Fallback: extract array contents
        m = re.search(
            r'"' + re.escape(field) + r'"\s*:\s*\[([^\]]*)\]', input_str
        )
        if not m:
            return []
        inner = m.group(1)
        items = []
        for part in inner.split(","):
            part = part.strip().strip('"')
            if part:
                items.append(part)
        return items


# ---------------------------------------------------------------------------
# YAML helpers (line-based, no pyyaml)
# ---------------------------------------------------------------------------


def yaml_field(field, filepath):
    """Parse a scalar field from a YAML file. Returns str or ''."""
    if not filepath or not os.path.isfile(filepath):
        return ""
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^\s*" + re.escape(field) + r":\s*(.*)", line)
                if m:
                    raw = m.group(1).rstrip("\n")
                    # Strip surrounding quotes
                    raw = raw.strip()
                    raw = raw.strip('"').strip("'")
                    return raw
    except OSError:
        pass
    return ""


def yaml_array(field, filepath):
    """Parse a YAML sequence field. Returns list of strings.

    Handles both:
      field: [a, b, c]       # inline array
      field:                 # block sequence
        - a
        - b
    """
    if not filepath or not os.path.isfile(filepath):
        return []
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    # Find the field line
    field_line_idx = None
    for i, line in enumerate(lines):
        if re.match(r"^\s*" + re.escape(field) + r":", line):
            field_line_idx = i
            break

    if field_line_idx is None:
        return []

    field_line = lines[field_line_idx]

    # Try inline array: field: [a, b, c]
    m = re.search(r"\[([^\]]*)\]", field_line)
    if m:
        inner = m.group(1).strip()
        if not inner:
            return []
        items = []
        for part in inner.split(","):
            part = part.strip().strip('"').strip("'")
            if part:
                items.append(part)
        return items

    # Fall back to block sequence
    items = []
    for line in lines[field_line_idx + 1 :]:
        # Stop if we hit a new top-level or same-level key (non-indented, non-dash)
        if re.match(r"^[^\s\-]", line) and not line.strip().startswith("-"):
            break
        bm = re.match(r"^\s+-\s+(.*)", line)
        if bm:
            val = bm.group(1).rstrip("\n").strip().strip('"').strip("'")
            if val:
                items.append(val)
        elif line.strip() and not line.strip().startswith("-"):
            # Non-blank, non-dash line at any indent — we've left the block
            break
    return items


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------


def manifest_field(field):
    """Read a scalar field from the manifest. Returns str or ''."""
    return yaml_field(field, MANIFEST)


def manifest_section_field(section, field, expected=None):
    """Check a field inside a YAML section. Returns value str, or bool if expected given."""
    if not os.path.isfile(MANIFEST):
        return False if expected is not None else ""
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return False if expected is not None else ""

    in_section = False
    for line in lines:
        if re.match(r"^" + re.escape(section) + r":", line):
            in_section = True
            continue
        if in_section:
            # Left the section if we hit a new top-level key
            if re.match(r"^[a-zA-Z]", line):
                break
            m = re.match(r"^\s+" + re.escape(field) + r":\s*(.*)", line)
            if m:
                val = m.group(1).rstrip("\n").strip().strip('"').strip("'")
                if expected is not None:
                    return val == expected
                return val
    return False if expected is not None else ""


def is_tooling_ready(field):
    """Return True if manifest tooling.<field> == 'true'."""
    return manifest_section_field("tooling", field, "true")


def is_profile_enabled(field):
    """Return True if manifest profiles.<field> == 'true'."""
    return manifest_section_field("profiles", field, "true")


# ---------------------------------------------------------------------------
# Task state helpers
# ---------------------------------------------------------------------------


def task_state_field(field, task_dir):
    """Read a field from a task's TASK_STATE.yaml."""
    return yaml_field(field, os.path.join(task_dir, "TASK_STATE.yaml"))


def task_touches_path(task_dir, changed_file):
    """Return True if task's touched_paths/roots_touched/verification_targets
    overlap with changed_file. Conservative: returns True if all lists empty."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return True

    touched = yaml_array("touched_paths", state_file)
    roots = yaml_array("roots_touched", state_file)
    vt = yaml_array("verification_targets", state_file)

    if not touched and not roots and not vt:
        return True

    for path in vt:
        if not path:
            continue
        if changed_file == path or changed_file.startswith(path + "/"):
            return True

    for path in touched:
        if not path:
            continue
        if changed_file == path or changed_file.startswith(path + "/"):
            return True

    for root in roots:
        if not root:
            continue
        if changed_file == root or changed_file.startswith(root + "/"):
            return True

    return False


def find_tasks_touching_path(changed_file):
    """Return list of task dirs whose touched_paths/roots_touched overlap with changed_file."""
    if not os.path.isdir(TASK_DIR):
        return []
    result = []
    for task in sorted(_glob.glob(os.path.join(TASK_DIR, "TASK__*/"))):
        if not os.path.isdir(task):
            continue
        state_file = os.path.join(task, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue
        if task_touches_path(task, changed_file):
            result.append(task)
    return result


def find_tasks_with_verification_targets(changed_file):
    """Return list of task dirs whose verification_targets overlap with changed_file.
    Falls back to touched_paths/roots_touched if verification_targets is empty."""
    if not os.path.isdir(TASK_DIR):
        return []
    result = []
    for task in sorted(_glob.glob(os.path.join(TASK_DIR, "TASK__*/"))):
        if not os.path.isdir(task):
            continue
        state_file = os.path.join(task, "TASK_STATE.yaml")
        if not os.path.isfile(state_file):
            continue
        status = yaml_field("status", state_file)
        if status in ("closed", "archived", "stale"):
            continue
        vt = yaml_array("verification_targets", state_file)
        if vt:
            for path in vt:
                if not path:
                    continue
                if changed_file == path or changed_file.startswith(path + "/"):
                    result.append(task)
                    break
        else:
            if task_touches_path(task, changed_file):
                result.append(task)
    return result


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------


def path_root(filepath):
    """Return first path segment before /."""
    return filepath.split("/")[0] if "/" in filepath else filepath


def extract_roots(paths):
    """Return sorted unique first path segments from a list of paths."""
    return sorted(set(path_root(p) for p in paths if p))


def is_doc_path(path):
    """Return True if path is a doc path (not a runtime path)."""
    p = path
    doc_patterns = [
        r"^doc/",
        r"^docs/",
        r"\.md$",
        r"^README",
        r"^CHANGELOG",
        r"^LICENSE",
        r"^DOC_SYNC\.md$",
    ]
    for pattern in doc_patterns:
        if re.match(pattern, p):
            return True
    return False


def normalize_path(path):
    """Strip leading ./ and leading / from a path."""
    p = path
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("/"):
        p = p[1:]
    if p.endswith("/"):
        p = p.rstrip("/")
    return p


def parse_changed_files(input_str=None):
    """Parse changed files from hook stdin JSON payload.

    Handles:
      {"file_path": "src/foo.ts"}
      {"paths": ["src/a.ts", "src/b.ts"]}
      {"files": ["src/a.ts"]}
      {"file": "src/foo.ts"}

    Returns sorted deduplicated list of normalized paths.
    """
    if input_str is None:
        input_str = read_hook_input()
    if not input_str:
        return []

    result = set()

    # Try array fields first
    for field in ("paths", "files", "changed_files"):
        arr = json_array(field, input_str)
        for f in arr:
            if f:
                norm = normalize_path(f)
                if norm:
                    result.add(norm)

    # Try single-value fields if no array results
    if not result:
        for field in ("file_path", "file", "path"):
            val = json_field(field, input_str)
            if val:
                norm = normalize_path(val)
                if norm:
                    result.add(norm)

    # Also check tool_input.file_path for PostToolUse events
    if not result:
        try:
            import json as _json
            if isinstance(input_str, str):
                raw = _json.loads(input_str)
            else:
                raw = input_str or {}
            tool_input = raw.get("tool_input", {}) if isinstance(raw, dict) else {}
            if isinstance(tool_input, dict):
                for field in ("file_path", "file_paths", "path"):
                    val = tool_input.get(field)
                    if val and isinstance(val, str):
                        norm = normalize_path(val)
                        if norm:
                            result.add(norm)
                            break
                    elif val and isinstance(val, list):
                        for item in val:
                            if item and isinstance(item, str):
                                norm = normalize_path(item)
                                if norm:
                                    result.add(norm)
        except Exception:
            pass

    return sorted(result)


# ---------------------------------------------------------------------------
# Team helpers
# ---------------------------------------------------------------------------

def team_state_fields(task_dir):
    """Return dict of team-related fields from TASK_STATE.yaml."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    return {
        "orchestration_mode": yaml_field("orchestration_mode", state_file) or "solo",
        "team_provider": yaml_field("team_provider", state_file) or "none",
        "team_status": yaml_field("team_status", state_file) or "n/a",
        "team_size": yaml_field("team_size", state_file) or "0",
        "team_reason": yaml_field("team_reason", state_file) or "",
        "fallback_used": yaml_field("fallback_used", state_file) or "none",
    }

def is_team_task(task_dir):
    """Return True if orchestration_mode is 'team'."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    return yaml_field("orchestration_mode", state_file) == "team"

def manifest_teams_field(field):
    """Read a field from the teams: section of the manifest."""
    return manifest_section_field("teams", field)


# ---------------------------------------------------------------------------
# Browser / project type
# ---------------------------------------------------------------------------


def is_browser_first_project():
    """Return True if manifest declares browser.enabled or qa.browser_qa_supported."""
    if not os.path.isfile(MANIFEST):
        return False
    try:
        with open(MANIFEST, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return False

    # Check browser section for enabled: true
    in_browser = False
    for line in lines:
        if re.match(r"^browser:", line):
            in_browser = True
            continue
        if in_browser:
            if re.match(r"^[a-zA-Z]", line):
                in_browser = False
            elif re.match(r"^\s+enabled\s*:\s*true", line):
                return True

    # Check qa.browser_qa_supported: true
    in_qa = False
    for line in lines:
        if re.match(r"^qa:", line):
            in_qa = True
            continue
        if in_qa:
            if re.match(r"^[a-zA-Z]", line):
                in_qa = False
            elif re.match(r"^\s+browser_qa_supported\s*:\s*true", line):
                return True

    return False


def get_browser_qa_status():
    """Check browser QA status from manifest sections and task states.

    Returns 'disabled', 'enabled', or 'blocked_env'.
    Shared across session_context.py and session_end_sync.py.
    """
    browser_qa = "disabled"

    if not os.path.isfile(MANIFEST):
        return browser_qa

    # Check qa: section for browser_qa_supported
    in_qa = False
    try:
        with open(MANIFEST) as f:
            for line in f:
                if line.startswith("qa:"):
                    in_qa = True
                    continue
                if in_qa:
                    if line and not line[0].isspace():
                        in_qa = False
                        continue
                    if "browser_qa_supported:" in line:
                        val = line.split("browser_qa_supported:", 1)[1].strip().lower()
                        if val == "true":
                            browser_qa = "enabled"
                        break
    except (OSError, IOError):
        pass

    # Check browser: section for enabled
    if browser_qa == "disabled":
        in_browser = False
        try:
            with open(MANIFEST) as f:
                for line in f:
                    if line.startswith("browser:"):
                        in_browser = True
                        continue
                    if in_browser:
                        if line and not line[0].isspace():
                            in_browser = False
                            continue
                        if "enabled:" in line:
                            val = line.split("enabled:", 1)[1].strip().lower()
                            if val == "true":
                                browser_qa = "enabled"
                            break
        except (OSError, IOError):
            pass

    # Check for blocked_env tasks requiring browser
    if browser_qa == "enabled" and os.path.isdir(TASK_DIR):
        for entry in sorted(os.listdir(TASK_DIR)):
            task_path = os.path.join(TASK_DIR, entry)
            if not os.path.isdir(task_path) or not entry.startswith("TASK__"):
                continue
            state_file = os.path.join(task_path, "TASK_STATE.yaml")
            if not os.path.isfile(state_file):
                continue
            status = yaml_field("status", state_file)
            browser_required = yaml_field("browser_required", state_file)
            if status == "blocked_env" and browser_required == "true":
                browser_qa = "blocked_env"
                break

    return browser_qa


# ---------------------------------------------------------------------------
# Timestamp
# ---------------------------------------------------------------------------


def now_iso():
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Note metadata helpers (WS-1)
# ---------------------------------------------------------------------------


def _empty_note_metadata():
    """Return empty note metadata dict."""
    return {
        "status": None,
        "freshness": None,
        "verified_at": None,
        "invalidated_by_paths": [],
        "verification_command": None,
    }


def parse_note_metadata(note_path):
    """Parse structured freshness metadata from a note file.

    Reads the note at note_path and extracts:
      - status
      - freshness
      - verified_at
      - invalidated_by_paths (list — parsed structurally, not substring)
      - verification_command

    Returns dict with above keys. Missing fields are None / [].
    Returns empty metadata dict on read error.
    """
    if not note_path or not os.path.isfile(note_path):
        return _empty_note_metadata()
    try:
        with open(note_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return _empty_note_metadata()

    result = _empty_note_metadata()
    i = 0
    n = len(lines)

    while i < n:
        line = lines[i]

        # Scalar fields
        for field in ("status", "freshness", "verified_at", "verification_command"):
            m = re.match(r"^\s*" + re.escape(field) + r"\s*:\s*(.*)", line)
            if m:
                val = m.group(1).rstrip("\n").strip().strip('"').strip("'")
                result[field] = val if val else None
                break

        # invalidated_by_paths — inline or block
        m = re.match(r"^\s*invalidated_by_paths\s*:\s*(.*)", line)
        if m:
            rest = m.group(1).strip()
            if rest.startswith("["):
                # Inline array: [a, b, c]
                inner = rest.strip("[]")
                items = []
                for part in inner.split(","):
                    part = part.strip().strip('"').strip("'")
                    if part:
                        items.append(part)
                result["invalidated_by_paths"] = items
            else:
                # Block sequence — read subsequent lines
                items = []
                j = i + 1
                while j < n:
                    bm = re.match(r"^\s+-\s+(.*)", lines[j])
                    if bm:
                        val = bm.group(1).rstrip("\n").strip().strip('"').strip("'")
                        if val:
                            items.append(val)
                        j += 1
                    elif lines[j].strip() == "" or lines[j].strip().startswith("#"):
                        j += 1
                    else:
                        break
                result["invalidated_by_paths"] = items

        i += 1

    return result


def set_note_freshness(note_path, freshness, verified_at=None):
    """Update freshness (and optionally verified_at) in a note file in-place.

    If freshness field exists, updates it. Otherwise inserts after first line.
    If verified_at is provided and field exists, updates it; otherwise inserts
    after the freshness line.

    Returns True on success, False on error.
    """
    if not note_path or not os.path.isfile(note_path):
        return False
    try:
        with open(note_path, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    # Update or insert freshness
    if re.search(r"^freshness\s*:", content, flags=re.MULTILINE):
        content = re.sub(
            r"^freshness\s*:.*",
            f"freshness: {freshness}",
            content,
            flags=re.MULTILINE,
        )
    else:
        lines = content.split("\n")
        lines.insert(1, f"freshness: {freshness}")
        content = "\n".join(lines)

    # Update or insert verified_at
    if verified_at:
        if re.search(r"^verified_at\s*:", content, flags=re.MULTILINE):
            content = re.sub(
                r"^verified_at\s*:.*",
                f"verified_at: {verified_at}",
                content,
                flags=re.MULTILINE,
            )
        else:
            # Insert after freshness line
            lines = content.split("\n")
            for idx, line in enumerate(lines):
                if re.match(r"^freshness\s*:", line):
                    lines.insert(idx + 1, f"verified_at: {verified_at}")
                    break
            content = "\n".join(lines)

    try:
        with open(note_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# WS-1: Workflow state helpers — TASK_STATE read/write
# ---------------------------------------------------------------------------


def get_workflow_violations(task_dir):
    """Return list of workflow_violations from TASK_STATE.yaml. Empty list if none."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    return yaml_array("workflow_violations", state_file)


def append_workflow_violation(task_dir, violation):
    """Append a violation string to workflow_violations in TASK_STATE.yaml.

    Idempotent — does not add duplicate entries.
    Returns True on success, False if state file absent.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False

    current = get_workflow_violations(task_dir)
    if violation in current:
        return True  # already recorded

    current.append(violation)
    inline = ", ".join(f'"{v}"' for v in current)

    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    ts = now_iso()
    if re.search(r"^workflow_violations:", content, re.MULTILINE):
        content = re.sub(
            r"^workflow_violations:.*",
            f"workflow_violations: [{inline}]",
            content,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip("\n") + f"\nworkflow_violations: [{inline}]\n"

    content = re.sub(r"^updated: .*", f"updated: {ts}", content, flags=re.MULTILINE)

    try:
        with open(state_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# WS-3: Agent run provenance helpers
# ---------------------------------------------------------------------------


def _agent_field_prefix(agent_name):
    """Convert agent name to flat YAML field prefix.

    e.g. "critic-runtime" → "agent_run_critic_runtime"
    """
    return "agent_run_" + agent_name.replace("-", "_")


def get_agent_run_count(task_dir, agent_name):
    """Return agent run count from TASK_STATE.yaml. Returns 0 if field absent."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    field = _agent_field_prefix(agent_name) + "_count"
    val = yaml_field(field, state_file)
    try:
        return int(val) if val else 0
    except (ValueError, TypeError):
        return 0


def increment_agent_run(task_dir, agent_name):
    """Increment agent_run_<name>_count and update agent_run_<name>_last in TASK_STATE.yaml.

    Returns True on success, False if state file absent or write error.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False

    count_field = _agent_field_prefix(agent_name) + "_count"
    last_field = _agent_field_prefix(agent_name) + "_last"

    current_count = get_agent_run_count(task_dir, agent_name)
    new_count = current_count + 1
    ts = now_iso()

    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    # Update count field
    if re.search(r"^" + re.escape(count_field) + r":", content, re.MULTILINE):
        content = re.sub(
            r"^" + re.escape(count_field) + r":.*",
            f"{count_field}: {new_count}",
            content,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip("\n") + f"\n{count_field}: {new_count}\n"

    # Update last field
    if re.search(r"^" + re.escape(last_field) + r":", content, re.MULTILINE):
        content = re.sub(
            r"^" + re.escape(last_field) + r":.*",
            f"{last_field}: {ts}",
            content,
            flags=re.MULTILINE,
        )
    else:
        content = content.rstrip("\n") + f"\n{last_field}: {ts}\n"

    # Update global updated timestamp
    content = re.sub(r"^updated: .*", f"updated: {ts}", content, flags=re.MULTILINE)

    try:
        with open(state_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# WS-4: Verdict / doc critic / HANDOFF helpers
# ---------------------------------------------------------------------------


def is_plan_passed(task_dir):
    """Return True if plan_verdict == PASS in TASK_STATE.yaml."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    return yaml_field("plan_verdict", state_file) == "PASS"


def needs_document_critic(task_dir):
    """Return True if document critic is required for this task.

    Criteria (any of):
      1. doc_changes_detected: true in TASK_STATE.yaml
      2. DOC_SYNC.md exists with meaningful content beyond metadata + "none" sections

    A DOC_SYNC.md with only headers, metadata lines (updated:, etc.), and "none"
    or "(or \"none\")" entries is considered empty — no document critic needed.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if yaml_field("doc_changes_detected", state_file) == "true":
        return True

    doc_sync = os.path.join(task_dir, "DOC_SYNC.md")
    if os.path.isfile(doc_sync):
        try:
            with open(doc_sync, "r", encoding="utf-8") as fh:
                content = fh.read()
            # Filter out: blank lines, comment/header lines (#/##),
            # metadata lines (key: value at top), and "none"/"(or \"none\")" lines
            content_lines = []
            for ln in content.split("\n"):
                stripped = ln.strip()
                if not stripped:
                    continue
                # Skip markdown headers
                if stripped.startswith("#"):
                    continue
                # Skip metadata lines like "updated: 2026-03-30"
                if re.match(r"^[a-z_]+\s*:", stripped):
                    continue
                # Skip "none" variants
                lower = stripped.lower().strip('"').strip("'")
                if lower in ("none", "(or \"none\")", '(or "none")'):
                    continue
                # Skip list items that are just "none"
                if re.match(r"^-\s*none\s*$", stripped, re.IGNORECASE):
                    continue
                content_lines.append(stripped)
            if content_lines:
                return True
        except OSError:
            pass

    return False


def is_handoff_stub(handoff_path):
    """Return True if HANDOFF.md is an unfilled stub.

    A stub is detected by:
      - fewer than 5 non-empty lines, OR
      - no '##' section headers (no real content sections)
    """
    if not handoff_path or not os.path.isfile(handoff_path):
        return True
    try:
        with open(handoff_path, "r", encoding="utf-8") as fh:
            content = fh.read()
        lines = [ln for ln in content.strip().split("\n") if ln.strip()]
        if len(lines) < 5:
            return True
        if not any(ln.startswith("##") for ln in lines):
            return True
        return False
    except OSError:
        return True


# ---------------------------------------------------------------------------
# WS-1 v2: CHECKS.yaml close_gate parser
# ---------------------------------------------------------------------------


def parse_checks_close_gate(checks_file):
    """Parse the top-level close_gate field from CHECKS.yaml.

    Returns 'standard' (default) or 'strict_high_risk'.
    Missing field or absent file → 'standard' (backward compatible).
    """
    if not checks_file or not os.path.isfile(checks_file):
        return "standard"
    try:
        with open(checks_file, "r", encoding="utf-8") as fh:
            for line in fh:
                m = re.match(r"^close_gate\s*:\s*(.+)", line)
                if m:
                    val = m.group(1).strip().strip('"').strip("'")
                    if val in ("standard", "strict_high_risk"):
                        return val
                    return "standard"
    except OSError:
        pass
    return "standard"


def should_set_strict_close_gate(state_file):
    """Determine if a task should use strict_high_risk close gate.

    Returns True when any of:
      - execution_mode: sprinted
      - review_overlays contains 'security' or 'performance'
      - risk_tags contains 'structural', 'migration', 'schema', or 'cross-root'

    Args:
        state_file: path to TASK_STATE.yaml

    Returns:
        bool
    """
    if not state_file or not os.path.isfile(state_file):
        return False

    exec_mode = yaml_field("execution_mode", state_file)
    if exec_mode == "sprinted":
        return True

    overlays = yaml_array("review_overlays", state_file)
    if "security" in overlays or "performance" in overlays:
        return True

    risk_tags = yaml_array("risk_tags", state_file)
    high_risk_tags = {"structural", "migration", "schema", "cross-root"}
    if high_risk_tags.intersection(risk_tags):
        return True

    return False


# ---------------------------------------------------------------------------
# WS-2 v2: Planning mode helper
# ---------------------------------------------------------------------------


def get_planning_mode(state_file):
    """Read planning_mode from TASK_STATE.yaml.

    Returns 'standard' (default) or 'broad-build'.
    Missing field → 'standard' (backward compatible).
    """
    if not state_file or not os.path.isfile(state_file):
        return "standard"
    val = yaml_field("planning_mode", state_file)
    if val in ("standard", "broad-build"):
        return val
    return "standard"


# ---------------------------------------------------------------------------
# Task-pack helpers (MCP-backed hctl support)
# ---------------------------------------------------------------------------


def set_task_state_field(task_dir, field, value):
    """Update a single field in TASK_STATE.yaml using regex line replacement.

    If the field exists, updates it in-place.
    If the field is missing, appends it before the final 'updated:' line.
    Also refreshes the 'updated:' timestamp.
    Returns True on success, False on error.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    # Represent value correctly
    if isinstance(value, bool):
        yaml_val = "true" if value else "false"
    elif isinstance(value, list):
        inline = ", ".join(f'"{v}"' for v in value)
        yaml_val = f"[{inline}]"
    elif value is None:
        yaml_val = "null"
    else:
        yaml_val = str(value)

    ts = now_iso()

    if re.search(r"^" + re.escape(field) + r":", content, re.MULTILINE):
        content = re.sub(
            r"^" + re.escape(field) + r":.*",
            f"{field}: {yaml_val}",
            content,
            flags=re.MULTILINE,
        )
    else:
        # Append before 'updated:' line if present, else at end
        if re.search(r"^updated:", content, re.MULTILINE):
            content = re.sub(
                r"^(updated:.*)$",
                f"{field}: {yaml_val}\nupdated: {ts}",
                content,
                flags=re.MULTILINE,
                count=1,
            )
            # Don't update updated again below
            try:
                with open(state_file, "w", encoding="utf-8") as fh:
                    fh.write(content)
                return True
            except OSError:
                return False
        else:
            content = content.rstrip("\n") + f"\n{field}: {yaml_val}\n"

    # Update timestamp
    if re.search(r"^updated:", content, re.MULTILINE):
        content = re.sub(r"^updated:.*", f"updated: {ts}", content, flags=re.MULTILINE)

    try:
        with open(state_file, "w", encoding="utf-8") as fh:
            fh.write(content)
        return True
    except OSError:
        return False


def compile_routing(task_dir, request_text=""):
    """Compute routing fields from TASK_STATE.yaml + request text heuristics.

    Returns a dict of fields to set in TASK_STATE.yaml:
      risk_level, parallelism, workflow_locked, maintenance_task,
      routing_compiled, routing_source,
      execution_mode (compat), orchestration_mode (compat)

    Rules (from PLAN.md §9):
    risk_level:
      - low: lane in [docs-sync, answer, investigate] OR (single-file, no keywords)
      - high: maintenance/harness/template keywords, browser+failures, multi-root risk_tags
      - medium: default

    maintenance_task:
      - true if: "maintenance-task" in risk_tags, "harness-source" in risk_tags,
        OR (lane in [refactor, build] AND "template-sync-required" in risk_tags)

    workflow_locked:
      - false if maintenance_task=true, else true

    parallelism: always 1 in v1
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")

    lane = yaml_field("lane", state_file) or "unknown"
    risk_tags = yaml_array("risk_tags", state_file)
    browser_required = yaml_field("browser_required", state_file) or "false"
    fail_count_raw = yaml_field("runtime_verdict_fail_count", state_file) or "0"
    try:
        fail_count = int(fail_count_raw)
    except (ValueError, TypeError):
        fail_count = 0

    req = request_text.lower()

    # --- maintenance_task ---
    HIGH_MAINTENANCE_TAGS = {"maintenance-task", "harness-source"}
    maintenance_task = False
    if HIGH_MAINTENANCE_TAGS.intersection(set(risk_tags)):
        maintenance_task = True
    elif lane in ("refactor", "build") and "template-sync-required" in risk_tags:
        maintenance_task = True

    # Keyword heuristic from request text
    MAINTENANCE_KEYWORDS = {
        "harness", "plugin", "workflow", "hook", "hctl", "setup", "template",
        "control surface", "prewrite", "session_context", "prompt_memory",
    }
    if not maintenance_task and any(kw in req for kw in MAINTENANCE_KEYWORDS):
        # Only if also touching CLAUDE.md / plugin files
        if any(kw in req for kw in ("claude.md", "plugin/", "hooks.json", "execution-modes")):
            maintenance_task = True

    # --- risk_level ---
    LOW_LANES = {"docs-sync", "answer", "investigate"}
    HIGH_RISK_TAGS = {"multi-root", "destructive", "structural", "harness-source",
                      "maintenance-task", "template-sync-required"}

    if lane in LOW_LANES:
        risk_level = "low"
    elif (
        HIGH_RISK_TAGS.intersection(set(risk_tags))
        or (browser_required == "true" and fail_count >= 2)
        or maintenance_task
    ):
        risk_level = "high"
    else:
        risk_level = "medium"

    # Request text high-risk keywords
    HIGH_RISK_KEYWORDS = {
        "setup", "template", "control surface", "harness-source", "hooks.json",
        "execution-modes", "orchestration-modes", "prewrite_gate", "hctl",
    }
    if risk_level != "high" and any(kw in req for kw in HIGH_RISK_KEYWORDS):
        risk_level = "high"

    # --- parallelism (always 1 in v1) ---
    parallelism = 1

    # --- workflow_locked ---
    workflow_locked = not maintenance_task

    # --- compat fields ---
    EXEC_MODE_MAP = {"low": "light", "medium": "standard", "high": "sprinted"}
    execution_mode = EXEC_MODE_MAP.get(risk_level, "standard")
    orchestration_mode = "solo" if parallelism <= 1 else "subagents"

    return {
        "risk_level": risk_level,
        "parallelism": parallelism,
        "workflow_locked": workflow_locked,
        "maintenance_task": maintenance_task,
        "routing_compiled": True,
        "routing_source": "hctl",
        "execution_mode": execution_mode,
        "orchestration_mode": orchestration_mode,
    }


def emit_compact_context(task_dir):
    """Return a brief task pack for runtime control.

    The default task pack is intentionally compact:
      - task-local must_read only (cap 4)
      - summarized checks instead of full criterion dumps
      - short notes / next_action hints
      - hctl commands only

    This keeps the runtime control plane small enough to replace repeated
    rereads of global harness docs.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")

    def _bool(val):
        if isinstance(val, bool):
            return val
        return str(val).lower() in ("true", "1", "yes")

    def _int(val):
        try:
            return int(val)
        except (ValueError, TypeError):
            return 0

    def _display_path(path_value):
        try:
            cwd = os.getcwd()
            if os.path.isabs(path_value):
                rel = os.path.relpath(path_value, cwd)
                if not rel.startswith(".."):
                    return rel
        except (OSError, ValueError):
            pass
        return path_value

    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir)
    status = yaml_field("status", state_file) or "unknown"
    lane = yaml_field("lane", state_file) or "unknown"
    risk_level = yaml_field("risk_level", state_file) or "medium"
    qa_required = _bool(yaml_field("qa_required", state_file) or "true")
    doc_sync_required = _bool(yaml_field("doc_sync_required", state_file) or "false")
    browser_required = _bool(yaml_field("browser_required", state_file) or "false")
    parallelism = _int(yaml_field("parallelism", state_file) or "1")
    workflow_locked = _bool(yaml_field("workflow_locked", state_file) or "true")
    maintenance_task = _bool(yaml_field("maintenance_task", state_file) or "false")

    execution_mode = yaml_field("execution_mode", state_file) or "standard"
    orchestration_mode = yaml_field("orchestration_mode", state_file) or "solo"

    task_root = os.path.join(TASK_DIR, task_id)
    must_read_pairs = [
        (f"{task_root}/TASK_STATE.yaml", os.path.join(task_dir, "TASK_STATE.yaml")),
        (f"{task_root}/PLAN.md", os.path.join(task_dir, "PLAN.md")),
        (f"{task_root}/CHECKS.yaml", os.path.join(task_dir, "CHECKS.yaml")),
        (f"{task_root}/HANDOFF.md", os.path.join(task_dir, "HANDOFF.md")),
        (f"{task_root}/SESSION_HANDOFF.json", os.path.join(task_dir, "SESSION_HANDOFF.json")),
        (f"{task_root}/RESULT.md", os.path.join(task_dir, "RESULT.md")),
    ]
    must_read = []
    seen = set()
    for rel_path, abs_path in must_read_pairs:
        if rel_path in seen:
            continue
        if os.path.isfile(abs_path):
            must_read.append(rel_path)
            seen.add(rel_path)
        if len(must_read) >= 4:
            break

    display_task_dir = _display_path(task_dir)
    commands = {
        "update": "mcp__plugin_harness_harness__task_update_from_git_diff",
        "verify": "mcp__plugin_harness_harness__task_verify",
        "close": "mcp__plugin_harness_harness__task_close",
    }

    checks_file = os.path.join(task_dir, "CHECKS.yaml")
    check_items = []
    if os.path.isfile(checks_file):
        try:
            with open(checks_file, "r", encoding="utf-8") as fh:
                lines = fh.readlines()
            current = {}
            for line in lines:
                m_id = re.match(r"^\s*-?\s*id\s*:\s*(.+)", line)
                if m_id:
                    if current.get("id"):
                        check_items.append(current)
                    current = {
                        "id": m_id.group(1).strip().strip('"').strip("'"),
                        "status": "pending",
                        "title": "",
                    }
                    continue
                m_st = re.match(r"^\s+status\s*:\s*(.+)", line)
                if m_st and current.get("id"):
                    current["status"] = m_st.group(1).strip().strip('"').strip("'")
                    continue
                m_title = re.match(r"^\s+title\s*:\s*(.+)", line)
                if m_title and current.get("id"):
                    current["title"] = m_title.group(1).strip().strip('"').strip("'")
                    continue
            if current.get("id"):
                check_items.append(current)
        except OSError:
            pass

    failed_ids = [c["id"] for c in check_items if c.get("status") == "failed"]
    blocked_ids = [c["id"] for c in check_items if c.get("status") == "blocked"]
    candidate_ids = [c["id"] for c in check_items if c.get("status") == "implemented_candidate"]
    open_ids = [
        c["id"]
        for c in check_items
        if c.get("status") not in ("passed", "skipped")
    ]
    top_open_titles = [
        c.get("title", "")[:90]
        for c in check_items
        if c.get("status") not in ("passed", "skipped") and c.get("title")
    ][:2]
    open_failures = failed_ids + [cid for cid in blocked_ids if cid not in failed_ids]

    checks = {
        "total": len(check_items),
        "open_ids": open_ids[:8],
        "failed_ids": failed_ids[:8],
        "blocked_ids": blocked_ids[:8],
        "candidate_ids": candidate_ids[:8],
        "top_open_titles": top_open_titles,
    }

    notes = []
    routing_compiled = str(yaml_field("routing_compiled", state_file) or "false").lower()
    plan_verdict = yaml_field("plan_verdict", state_file) or "pending"
    runtime_verdict = yaml_field("runtime_verdict", state_file) or "pending"

    if routing_compiled != "true":
        notes.append("routing not compiled yet")
    if plan_verdict != "PASS":
        notes.append(f"plan_verdict={plan_verdict}")
    if runtime_verdict == "FAIL":
        notes.append("runtime fix round open")
    if maintenance_task:
        notes.append("maintenance task: workflow surface unlocked")
    notes = notes[:3]

    if routing_compiled != "true":
        next_action = "Run mcp__plugin_harness_harness__task_start, then re-open mcp__plugin_harness_harness__task_context before planning or implementation."
    elif plan_verdict != "PASS":
        next_action = "Get PLAN.md to critic-plan PASS before mutating source files."
    elif runtime_verdict == "FAIL":
        next_action = "Fix open runtime failures, run mcp__plugin_harness_harness__task_verify, then re-check critics."
    elif lane == "investigate":
        next_action = "Write RESULT.md with findings and close after verification gates pass."
    else:
        next_action = "Implement the smallest diff for open checks, then run mcp__plugin_harness_harness__task_update_from_git_diff, task_verify, and task_close."

    return {
        "task_id": task_id,
        "status": status,
        "lane": lane,
        "risk_level": risk_level,
        "qa_required": qa_required,
        "doc_sync_required": doc_sync_required,
        "browser_required": browser_required,
        "parallelism": parallelism,
        "workflow_locked": workflow_locked,
        "maintenance_task": maintenance_task,
        "compat": {
            "execution_mode": execution_mode,
            "orchestration_mode": orchestration_mode,
        },
        "must_read": must_read,
        "commands": commands,
        "checks": checks,
        "open_failures": open_failures,
        "notes": notes,
        "next_action": next_action,
    }


# ---------------------------------------------------------------------------
# WS-3 v2: Observability activation policy
# ---------------------------------------------------------------------------


def should_activate_observability(manifest_ready, project_kind, review_overlays,
                                  runtime_fail_count, context_text=""):
    """Determine if observability overlay should be activated for a task.

    Returns (bool, str) — (should_activate, reason).

    Required conditions (ALL must be true):
      - manifest_ready is True (tooling.observability_ready)
      - project_kind is web/api/fullstack/worker family

    Plus at least ONE additional signal:
      - 'performance' in review_overlays
      - runtime_fail_count >= 2
      - context_text contains investigation keywords (intermittent, flaky,
        cross-service, latency spike, p95, p99, trace, log correlation)

    Returns (False, reason) when:
      - manifest_ready is False
      - project_kind is library/cli
      - No additional signal present
    """
    # Gate: readiness must be true
    if not manifest_ready:
        return (False, "observability_ready is false")

    # Gate: project kind must be suitable
    suitable_kinds = {"web", "api", "fullstack", "fullstack_web", "web_frontend",
                      "web-frontend", "worker", "service"}
    kind_lower = (project_kind or "").lower().replace("-", "_").replace(" ", "_")
    # Check if any suitable kind is a substring of or matches the project kind
    kind_match = False
    for sk in suitable_kinds:
        sk_normalized = sk.replace("-", "_")
        if sk_normalized == kind_lower or sk_normalized in kind_lower:
            kind_match = True
            break
    if not kind_match:
        return (False, f"project kind '{project_kind}' not suitable (need web/api/fullstack/worker)")

    # Additional signals — need at least one
    reasons = []

    if isinstance(review_overlays, (list, tuple)) and "performance" in review_overlays:
        reasons.append("performance overlay active")

    try:
        fail_count = int(runtime_fail_count) if runtime_fail_count is not None else 0
    except (ValueError, TypeError):
        fail_count = 0
    if fail_count >= 2:
        reasons.append(f"runtime_verdict_fail_count={fail_count}")

    # Context keyword scan
    ctx = (context_text or "").lower()
    investigation_keywords = [
        "intermittent", "flaky", "cross-service", "cross_service",
        "latency spike", "latency_spike", "p95", "p99",
        "trace", "log correlation", "log_correlation",
        "metric correlation", "metric_correlation",
    ]
    for kw in investigation_keywords:
        if kw in ctx:
            reasons.append(f"context keyword: {kw}")
            break  # one keyword is enough

    if not reasons:
        return (False, "no activation signal (performance overlay, fail count >= 2, or investigation keywords)")

    return (True, "; ".join(reasons))
