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

TASK_DIR = ".claude/harness/tasks"
MANIFEST = ".claude/harness/manifest.yaml"

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
    """Parse a string field from JSON. Returns str or ''."""
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
        r"^\.claude/harness/critics/",
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
