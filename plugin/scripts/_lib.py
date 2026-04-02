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

_MANIFEST_CACHE_PATH = None
_MANIFEST_CACHE_MTIME = None
_MANIFEST_CACHE_DATA = None
_MANIFEST_TEMPLATE = "plugin/skills/setup/templates/doc/harness/manifest.yaml"
_MANIFEST_MISSING = object()


def _yaml_scalar_value(raw):
    """Best-effort scalar parser for simple YAML fragments."""
    value = (raw or "").strip()
    if not value:
        return ""

    # Preserve explicitly quoted strings verbatim (minus quotes)
    if (value.startswith('"') and value.endswith('"')) or (
        value.startswith("'") and value.endswith("'")
    ):
        return value[1:-1]

    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        items = []
        for part in inner.split(","):
            parsed = _yaml_scalar_value(part)
            if parsed == "":
                continue
            items.append(parsed)
        return items

    lower = value.lower()
    if lower == "true":
        return True
    if lower == "false":
        return False
    if lower in ("null", "none", "~"):
        return None
    if re.match(r"^-?\d+$", value):
        try:
            return int(value)
        except ValueError:
            pass
    if re.match(r"^-?\d+\.\d+$", value):
        try:
            return float(value)
        except ValueError:
            pass
    return value


def _yaml_next_significant(lines, start_idx):
    """Return (indent, stripped) for the next non-empty, non-comment line."""
    for idx in range(start_idx + 1, len(lines)):
        raw = lines[idx].rstrip("\n")
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        return indent, stripped
    return None, None



def _parse_simple_yaml_mapping(filepath):
    """Parse a small YAML mapping used by the manifest.

    Supports the subset used by harness manifests:
      - nested dictionaries via indentation
      - block sequences of scalars
      - inline scalar arrays: [a, b]
      - booleans / ints / quoted strings

    It is intentionally conservative and returns {} on read errors.
    """
    if not filepath or not os.path.isfile(filepath):
        return {}
    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return {}

    root = {}
    stack = [(-1, root)]

    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        indent = len(line) - len(line.lstrip(" "))
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]

        if stripped.startswith("- "):
            if not isinstance(parent, list):
                continue
            item_value = stripped[2:].strip()
            if item_value:
                parent.append(_yaml_scalar_value(item_value))
            continue

        if ":" not in stripped or not isinstance(parent, dict):
            continue

        key, rest = stripped.split(":", 1)
        key = key.strip()
        rest = rest.strip()

        if not rest:
            next_indent, next_stripped = _yaml_next_significant(lines, i)
            if next_indent is not None and next_indent > indent and next_stripped.startswith("- "):
                container = []
            else:
                container = {}
            parent[key] = container
            stack.append((indent, container))
            continue

        parent[key] = _yaml_scalar_value(rest)

    return root



def _manifest_data(manifest_path=None):
    """Return cached parsed manifest data as nested dict/list scalars."""
    global _MANIFEST_CACHE_PATH, _MANIFEST_CACHE_MTIME, _MANIFEST_CACHE_DATA

    if manifest_path is None:
        manifest_path = MANIFEST
    if not manifest_path or not os.path.isfile(manifest_path):
        return {}

    try:
        mtime = os.path.getmtime(manifest_path)
    except OSError:
        return {}

    if (
        _MANIFEST_CACHE_PATH == manifest_path
        and _MANIFEST_CACHE_MTIME == mtime
        and isinstance(_MANIFEST_CACHE_DATA, dict)
    ):
        return _MANIFEST_CACHE_DATA

    data = _parse_simple_yaml_mapping(manifest_path)
    _MANIFEST_CACHE_PATH = manifest_path
    _MANIFEST_CACHE_MTIME = mtime
    _MANIFEST_CACHE_DATA = data
    return data



def _manifest_lookup(data, path):
    current = data
    for key in path:
        if not isinstance(current, dict) or key not in current:
            return _MANIFEST_MISSING
        current = current[key]
    return current



def _manifest_stringify(value):
    if value is _MANIFEST_MISSING or value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(_manifest_stringify(item) for item in value) + "]"
    if isinstance(value, dict):
        return ""
    return str(value)



def manifest_value(*path, manifest_path=None, default=None):
    """Return raw manifest value for a nested path, or default when absent.

    Supports any of:
      manifest_value("browser", "entry_url")
      manifest_value("browser.entry_url")
      manifest_value(["browser", "entry_url"])
    """
    if len(path) == 1:
        first = path[0]
        if isinstance(first, str) and "." in first:
            path = tuple(part for part in first.split(".") if part)
        elif isinstance(first, (list, tuple)):
            path = tuple(str(part) for part in first if str(part))
    data = _manifest_data(manifest_path=manifest_path)
    if not path:
        return data or default
    value = _manifest_lookup(data, tuple(path))
    return default if value is _MANIFEST_MISSING else value



def manifest_path_field(*path, expected=None, manifest_path=None):
    """Read a nested manifest field and normalize it to the legacy string API."""
    value = manifest_value(*path, manifest_path=manifest_path, default=_MANIFEST_MISSING)
    if expected is not None:
        return _manifest_stringify(value) == expected
    return _manifest_stringify(value)



def manifest_path_exists(*path, manifest_path=None):
    """Return True when the manifest contains the provided nested path."""
    value = manifest_value(*path, manifest_path=manifest_path, default=_MANIFEST_MISSING)
    return value is not _MANIFEST_MISSING



def manifest_field(field):
    """Read a scalar top-level field from the manifest. Returns str or ''."""
    return manifest_path_field(field)



def is_harness_initialized(manifest_path=None):
    """Return True when the current repository is harness-managed.

    The manifest is the setup marker written by /harness:setup. Hooks should
    treat repositories without this file as unmanaged and no-op silently.
    """
    if manifest_path is None:
        manifest_path = MANIFEST
    return os.path.isfile(manifest_path)



def exit_if_unmanaged_repo(manifest_path=None):
    """Exit the current hook silently when harness is not initialized."""
    if manifest_path is None:
        manifest_path = MANIFEST
    if not is_harness_initialized(manifest_path):
        raise SystemExit(0)



def manifest_section_field(section, field, expected=None):
    """Check a field inside a YAML section. Returns value str, or bool if expected given."""
    return manifest_path_field(section, field, expected=expected)



def is_tooling_ready(field):
    """Return True if manifest tooling.<field> == 'true'."""
    value = manifest_value("tooling", field, default=False)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"



def is_profile_enabled(field):
    """Return True if manifest profiles.<field> == 'true'."""
    value = manifest_value("profiles", field, default=False)
    if isinstance(value, bool):
        return value
    return str(value).lower() == "true"



def _flatten_manifest_paths(data, prefix=()):
    paths = []
    if isinstance(data, dict):
        for key, value in data.items():
            next_prefix = prefix + (str(key),)
            if isinstance(value, dict):
                nested = _flatten_manifest_paths(value, next_prefix)
                if nested:
                    paths.extend(nested)
                else:
                    paths.append(next_prefix)
            else:
                paths.append(next_prefix)
    elif prefix:
        paths.append(prefix)
    return paths



def manifest_sync_gaps(manifest_path=None, template_path=None):
    """Return missing manifest leaf paths compared to the setup template.

    This is a presence/self-sync check, not a value-equivalence check.
    The goal is to catch schema drift where runtime readers expect fields that
    the repo-local manifest no longer declares.
    """
    if manifest_path is None:
        manifest_path = MANIFEST
    if template_path is None:
        template_path = _MANIFEST_TEMPLATE

    template_data = _parse_simple_yaml_mapping(template_path)
    if not template_data:
        return []
    manifest_data = _parse_simple_yaml_mapping(manifest_path)
    if not manifest_data:
        return ["manifest missing"]

    gaps = []
    for path in _flatten_manifest_paths(template_data):
        if _manifest_lookup(manifest_data, path) is _MANIFEST_MISSING:
            gaps.append(".".join(path))

    top_type = _manifest_lookup(manifest_data, ("type",))
    shape = _manifest_lookup(manifest_data, ("project_meta", "shape"))
    if top_type is not _MANIFEST_MISSING and shape is not _MANIFEST_MISSING:
        if str(top_type) != str(shape):
            gaps.append("project_meta.shape!=type")

    return sorted(set(gaps))
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
    return (
        manifest_path_field("browser.enabled") == "true"
        or manifest_path_field("qa.browser_qa_supported") == "true"
    )


def get_browser_qa_status():
    """Check browser QA status from manifest sections and task states.

    Returns 'disabled', 'enabled', or 'blocked_env'.
    Shared across session_context.py and session_end_sync.py.
    """
    browser_qa = "disabled"

    if not os.path.isfile(MANIFEST):
        return browser_qa

    if (
        manifest_path_field("qa.browser_qa_supported") == "true"
        or manifest_path_field("browser.enabled") == "true"
    ):
        browser_qa = "enabled"

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


def _normalize_free_text(text):
    """Normalize free text for lightweight routing heuristics."""
    return re.sub(r"\s+", " ", str(text or "").lower()).strip()


def _contains_any_phrase(text, phrases):
    normalized = _normalize_free_text(text)
    return any(phrase in normalized for phrase in phrases)


def _count_path_anchors(text):
    """Count likely file / directory anchors in free-form request text."""
    if not text:
        return 0
    seen = set()
    for match in re.findall(r"(?:^|[\s`'\"(])((?:[\w.-]+/)+[\w.-]+)", text):
        cleaned = match.strip("`'\"()[]{}<>,.;:")
        if cleaned:
            seen.add(cleaned)
    for match in re.findall(
        r"\b[\w.-]+\.(?:py|ts|tsx|js|jsx|mjs|cjs|md|json|yaml|yml|css|scss|html|sql|sh|go|rs|java|kt|swift|rb|php)\b",
        text,
    ):
        seen.add(match)
    return len(seen)


def _is_short_high_level_request(text):
    """Return True for short, high-level requests (roughly 1-4 sentences)."""
    if not text:
        return False
    sentence_count = len(
        [
            part
            for part in re.split(r"[.!?。！？\n]+", text)
            if re.search(r"[a-z0-9가-힣]", part, re.IGNORECASE)
        ]
    )
    token_count = len(re.findall(r"[\w가-힣]+", text, re.UNICODE))
    return 1 <= sentence_count <= 4 and token_count <= 80


def _looks_like_detailed_spec(text):
    """Conservative detector for already-detailed technical requests."""
    if not text:
        return False
    normalized = _normalize_free_text(text)
    token_count = len(re.findall(r"[\w가-힣]+", normalized, re.UNICODE))
    if "```" in text:
        return True
    if token_count >= 120:
        return True
    if _count_path_anchors(text) >= 2:
        return True

    bullet_count = len(re.findall(r"^\s*(?:[-*]|\d+\.)\s+", text, flags=re.MULTILINE))
    if bullet_count >= 3 and token_count >= 50:
        return True

    low_level_hints = {
        "function", "class", "method", "module", "component api", "endpoint",
        "route", "schema", "migration", "sql", "file", "directory", "path",
        "api contract", "hook", "script", "테이블", "엔드포인트", "스키마",
        "마이그레이션", "파일", "경로", "함수", "클래스",
    }
    hint_hits = sum(1 for hint in low_level_hints if hint in normalized)
    return hint_hits >= 3 and token_count >= 40


def _estimate_surface_count(text):
    """Estimate how many repo surfaces / roots the request likely spans."""
    normalized = _normalize_free_text(text)
    if not normalized:
        return 0

    surface_hints = {
        "app": {
            "frontend", "front-end", "front end", "ui", "ux", "page", "screen",
            "dashboard", "site", "website", "web app", "webapp", "admin",
            "layout", "component", "browser", "responsive", "페이지", "화면",
            "프론트", "대시보드", "웹앱", "사이트",
        },
        "api": {
            "api", "backend", "back-end", "server", "endpoint", "route",
            "controller", "service layer", "graphql", "rest", "백엔드", "서버",
            "엔드포인트", "라우트",
        },
        "db": {
            "database", "db", "schema", "migration", "postgres", "mysql",
            "sqlite", "persistence", "persist", "query", "sql", "데이터베이스",
            "스키마", "마이그레이션", "영속",
        },
        "infra": {
            "deploy", "deployment", "docker", "kubernetes", "k8s", "infra",
            "terraform", "ci", "cd", "pipeline", "hosting", "인프라", "배포",
        },
        "docs": {"docs", "documentation", "readme", "문서"},
        "worker": {"worker", "queue", "job", "cron", "background", "워커", "큐"},
    }

    matched = set()
    for surface, hints in surface_hints.items():
        if any(hint in normalized for hint in hints):
            matched.add(surface)

    root_map = {
        "app": "app",
        "src": "app",
        "web": "app",
        "frontend": "app",
        "ui": "app",
        "api": "api",
        "server": "api",
        "backend": "api",
        "db": "db",
        "database": "db",
        "infra": "infra",
        "deploy": "infra",
        "ops": "infra",
        "docs": "docs",
        "doc": "docs",
        "worker": "worker",
        "jobs": "worker",
    }
    for match in re.findall(r"(?:^|[\s`'\"(])((?:[\w.-]+/)+[\w.-]+)", normalized):
        root = match.split("/", 1)[0]
        surface = root_map.get(root)
        if surface:
            matched.add(surface)

    return len(matched)


def _clean_request_text(text):
    """Strip REQUEST.md framing so heuristics see only the user request."""
    if not text:
        return ""
    lines = []
    for raw_line in str(text).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        lower = stripped.lower()
        if lower.startswith("# request:") or lower.startswith("created:"):
            continue
        if stripped.startswith("<!--") and stripped.endswith("-->"):
            continue
        lines.append(stripped)
    return "\n".join(lines).strip()


def load_request_text(task_dir, request_text=""):
    """Load request text from explicit input or task-local REQUEST.md."""
    cleaned = _clean_request_text(request_text)
    if cleaned:
        return cleaned

    request_file = os.path.join(task_dir, "REQUEST.md")
    if not os.path.isfile(request_file):
        return ""
    try:
        with open(request_file, "r", encoding="utf-8") as fh:
            return _clean_request_text(fh.read())
    except OSError:
        return ""


def infer_planning_mode(task_dir, request_text=""):
    """Infer planning_mode for a task.

    broad-build is promoted conservatively and only before a plan contract exists.
    Broad-build requires:
      - lane == build
      - a broad product/build request
      - at least 2 supporting signals from the execution-modes reference

    Explicit broad-build already present in TASK_STATE.yaml is preserved.
    Existing PLAN.md keeps the current mode to avoid mid-task churn.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    current_mode = get_planning_mode(state_file)
    if current_mode == "broad-build":
        return "broad-build"

    lane = yaml_field("lane", state_file) or "unknown"
    if lane != "build":
        return "standard"

    if os.path.isfile(os.path.join(task_dir, "PLAN.md")):
        return current_mode

    if str(yaml_field("maintenance_task", state_file) or "false").lower() == "true":
        return "standard"

    request_body = load_request_text(task_dir, request_text=request_text)
    if not request_body:
        return "standard"

    normalized = _normalize_free_text(request_body)

    exclusion_phrases = {
        "fix", "bug", "broken", "failing", "regression", "repair", "patch",
        "hotfix", "debug", "performance", "latency", "optimize", "refactor",
        "rename", "enforcement", "lint", "docs", "documentation", "single endpoint",
        "single component", "endpoint", "route", "component", "modal", "button",
        "schema", "migration", "hook", "스크립트", "버그", "오류", "수정",
        "고쳐", "회귀", "성능", "최적화", "리팩터", "문서", "엔드포인트",
        "컴포넌트", "스키마", "마이그레이션",
    }
    if any(phrase in normalized for phrase in exclusion_phrases):
        return "standard"

    if _looks_like_detailed_spec(request_body):
        return "standard"

    build_verbs = {
        "build", "create", "make", "design", "prototype", "launch", "scaffold",
        "set up", "spin up", "assemble", "craft", "construct", "만들", "구축",
        "설계", "제작", "생성", "출시", "시작",
    }
    product_nouns = {
        "app", "application", "dashboard", "site", "website", "web app", "webapp",
        "landing page", "portal", "platform", "admin", "admin panel", "workspace",
        "product", "experience", "앱", "애플리케이션", "대시보드", "사이트",
        "웹앱", "포털", "플랫폼", "관리자", "워크스페이스",
    }
    greenfield_hints = {
        "greenfield", "from scratch", "new app", "new product", "new dashboard",
        "new site", "new website", "blank repo", "starter", "mvp", "처음부터",
        "신규", "새로운", "새 앱", "새 사이트", "초기 버전", "프로토타입",
    }
    ui_hints = {
        "ui", "ux", "frontend", "front-end", "front end", "browser", "page",
        "screen", "visual", "responsive", "layout", "design system", "화면",
        "프론트", "브라우저", "페이지", "디자인", "반응형",
    }

    broad_request = (
        any(verb in normalized for verb in build_verbs)
        and any(noun in normalized for noun in product_nouns)
    ) or any(hint in normalized for hint in greenfield_hints)
    if not broad_request:
        return "standard"

    signals = 0
    if _is_short_high_level_request(request_body):
        signals += 1
    if any(hint in normalized for hint in greenfield_hints):
        signals += 1
    if _count_path_anchors(request_body) == 0:
        signals += 1

    browser_required = (yaml_field("browser_required", state_file) or "false").lower()
    if browser_required == "true" or any(hint in normalized for hint in ui_hints):
        signals += 1

    if _estimate_surface_count(request_body) >= 2:
        signals += 1

    needs_contract_narrowing = (
        _is_short_high_level_request(request_body)
        and _count_path_anchors(request_body) == 0
        and not _looks_like_detailed_spec(request_body)
    )
    if needs_contract_narrowing:
        signals += 1

    return "broad-build" if signals >= 2 else "standard"


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
      routing_compiled, routing_source, planning_mode,
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

    request_text = load_request_text(task_dir, request_text=request_text)
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
    planning_mode = infer_planning_mode(task_dir, request_text=request_text)

    return {
        "risk_level": risk_level,
        "parallelism": parallelism,
        "workflow_locked": workflow_locked,
        "maintenance_task": maintenance_task,
        "routing_compiled": True,
        "routing_source": "hctl",
        "planning_mode": planning_mode,
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

    When a task is in a fix round, the task pack switches to an evidence-first
    posture: it elevates the most relevant failing critic / handoff artifact,
    includes a short evidence excerpt, and surfaces the current repair focus.

    This keeps the runtime control plane small enough to replace repeated
    rereads of global harness docs while still making failing evidence hard to
    miss during recovery work.
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

    def _task_rel(name):
        return f"{task_root}/{name}"

    def _task_abs(name):
        return os.path.join(task_dir, name)

    def _read_text(path_value):
        if not path_value or not os.path.isfile(path_value):
            return ""
        try:
            with open(path_value, "r", encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def _compact_excerpt(text_value, limit=240):
        compact = re.sub(r"\s+", " ", text_value or "").strip()
        if len(compact) <= limit:
            return compact
        return compact[: limit - 3].rstrip() + "..."

    def _load_handoff_json(path_value):
        if not path_value or not os.path.isfile(path_value):
            return {}
        try:
            with open(path_value, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError, json.JSONDecodeError):
            return {}

    def _critic_verdict(path_value):
        if not path_value or not os.path.isfile(path_value):
            return ""
        return (yaml_field("verdict", path_value) or "").upper()

    def _excerpt_from_critic(path_value):
        if not path_value or not os.path.isfile(path_value):
            return ""
        summary = yaml_field("summary", path_value)
        verdict_reason = yaml_field("verdict_reason", path_value)
        pieces = []
        if summary:
            pieces.append(summary)
        if verdict_reason and verdict_reason.lower() != "none":
            pieces.append(verdict_reason)

        text_value = _read_text(path_value)
        lines = text_value.splitlines()
        transcript_started = False
        transcript_bits = []
        for line in lines:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.lower() == "## transcript":
                transcript_started = True
                continue
            if not transcript_started:
                continue
            transcript_bits.append(stripped)
            if len(" ".join(transcript_bits)) >= 160 or len(transcript_bits) >= 2:
                break

        if transcript_bits:
            pieces.append(" ".join(transcript_bits))
        if not pieces:
            for line in lines:
                stripped = line.strip()
                if not stripped or stripped.startswith("##"):
                    continue
                pieces.append(stripped)
                break

        return _compact_excerpt(" — ".join(pieces))

    def _excerpt_from_handoff(handoff_data):
        if not isinstance(handoff_data, dict):
            return ""
        pieces = []
        next_step = handoff_data.get("next_step")
        if isinstance(next_step, str) and next_step.strip():
            pieces.append(next_step.strip())
        open_ids = handoff_data.get("open_check_ids")
        if isinstance(open_ids, list) and open_ids:
            pieces.append("open checks: " + ", ".join(str(x) for x in open_ids[:3]))
        paths = handoff_data.get("paths_in_focus")
        if isinstance(paths, list) and paths:
            pieces.append("paths: " + ", ".join(str(x) for x in paths[:2]))
        return _compact_excerpt(" | ".join(pieces))

    def _excerpt_from_env_snapshot(path_value):
        text_value = _read_text(path_value)
        if not text_value:
            return ""
        lines = []
        for raw in text_value.splitlines():
            stripped = raw.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("captured_at:") or stripped.startswith("reason:"):
                continue
            lines.append(stripped.lstrip("- "))
            if len(lines) >= 3:
                break
        return _compact_excerpt(" | ".join(lines))

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
    planning_mode = get_planning_mode(state_file)
    runtime_fail_count = _int(yaml_field("runtime_verdict_fail_count", state_file) or "0")

    task_root = os.path.join(TASK_DIR, task_id)
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

    def _pick_focus_paths(handoff_data):
        raw_paths = handoff_data.get("paths_in_focus") if isinstance(handoff_data, dict) else None
        if isinstance(raw_paths, list) and raw_paths:
            return [str(x) for x in raw_paths[:4] if str(x).strip()]

        verification_targets = yaml_array("verification_targets", state_file)
        for candidates in (verification_targets, yaml_array("touched_paths", state_file)):
            picked = [p for p in candidates if p and not is_doc_path(p)]
            if picked:
                return picked[:4]
        return []

    def _pick_open_check_ids(handoff_data):
        raw_open = handoff_data.get("open_check_ids") if isinstance(handoff_data, dict) else None
        if isinstance(raw_open, list) and raw_open:
            return [str(x) for x in raw_open[:4] if str(x).strip()]
        focus_ids = failed_ids + [cid for cid in candidate_ids if cid not in failed_ids]
        focus_ids += [cid for cid in blocked_ids if cid not in focus_ids]
        if focus_ids:
            return focus_ids[:4]
        return open_ids[:4]

    def _pick_do_not_regress(handoff_data):
        raw_items = handoff_data.get("do_not_regress") if isinstance(handoff_data, dict) else None
        if isinstance(raw_items, list) and raw_items:
            return [str(x) for x in raw_items[:3] if str(x).strip()]
        return []

    routing_compiled = str(yaml_field("routing_compiled", state_file) or "false").lower()
    plan_verdict = yaml_field("plan_verdict", state_file) or "pending"
    runtime_verdict = (yaml_field("runtime_verdict", state_file) or "pending").upper()

    session_handoff_name = "SESSION_HANDOFF.json"
    runtime_critic_name = "CRITIC__runtime.md"
    document_critic_name = "CRITIC__document.md"
    doc_sync_name = "DOC_SYNC.md"
    handoff_name = "HANDOFF.md"
    request_name = "REQUEST.md"
    env_snapshot_name = "ENVIRONMENT_SNAPSHOT.md"

    session_handoff_path = _task_abs(session_handoff_name)
    runtime_critic_path = _task_abs(runtime_critic_name)
    document_critic_path = _task_abs(document_critic_name)
    doc_sync_path = _task_abs(doc_sync_name)
    handoff_path = _task_abs(handoff_name)
    env_snapshot_path = _task_abs(env_snapshot_name)

    handoff_data = _load_handoff_json(session_handoff_path)
    runtime_critic_verdict = _critic_verdict(runtime_critic_path)
    document_critic_verdict = _critic_verdict(document_critic_path)
    runtime_fix_round = runtime_verdict == "FAIL" or runtime_critic_verdict == "FAIL"
    blocked_env_round = status == "blocked_env" or runtime_verdict == "BLOCKED_ENV" or runtime_critic_verdict == "BLOCKED_ENV"
    document_fix_round = document_critic_verdict == "FAIL"

    verify_commands_config = yaml_array("verify_commands", MANIFEST) if os.path.isfile(MANIFEST) else []
    request_preview = (_read_text(_task_abs(request_name)) or "")[:900]
    normalized_request_preview = re.sub(r"\s+", " ", (request_preview or "").lower())
    env_signal_reasons = []
    if planning_mode == "broad-build" and plan_verdict != "PASS":
        env_signal_reasons.append("broad_build")
    if blocked_env_round:
        env_signal_reasons.append("blocked_env")
    if execution_mode == "sprinted":
        env_signal_reasons.append("sprinted")
    if orchestration_mode == "team" or parallelism > 1:
        env_signal_reasons.append("team")
    if browser_required:
        env_signal_reasons.append("browser")
    if runtime_fail_count >= 1:
        env_signal_reasons.append("runtime_fail_history")
    if len(verify_commands_config) >= 2 or any(len(str(cmd)) > 90 for cmd in verify_commands_config):
        env_signal_reasons.append("verify_stack")
    if any(
        phrase in normalized_request_preview
        for phrase in (
            "setup", "install", "dependency", "dependencies", "toolchain",
            "environment", "env", "package manager", "dev server", "playwright",
            "browser", "docker", "vite", "webpack", "pytest", "node", "python",
        )
    ):
        env_signal_reasons.append("toolchain")
    env_snapshot_surface = os.path.isfile(env_snapshot_path) and (
        blocked_env_round
        or (planning_mode == "broad-build" and plan_verdict != "PASS")
        or len(env_signal_reasons) >= 2
    )

    review_focus = {
        "evidence_first": False,
    }

    focus_trigger = ""
    focus_critic_name = ""
    focus_support_name = ""
    evidence_excerpt = ""
    similar_failure = None

    if runtime_fix_round or document_fix_round or blocked_env_round or handoff_data:
        review_focus["evidence_first"] = True
        if runtime_fix_round:
            focus_trigger = "runtime_fail"
            focus_critic_name = runtime_critic_name if os.path.isfile(runtime_critic_path) else ""
            focus_support_name = handoff_name if os.path.isfile(handoff_path) else ""
        elif document_fix_round:
            focus_trigger = "document_fail"
            focus_critic_name = document_critic_name if os.path.isfile(document_critic_path) else ""
            focus_support_name = doc_sync_name if os.path.isfile(doc_sync_path) else ""
        elif blocked_env_round:
            focus_trigger = "blocked_env"
            focus_support_name = env_snapshot_name if os.path.isfile(env_snapshot_path) else ""
        else:
            focus_trigger = "session_handoff"

        if focus_critic_name == runtime_critic_name:
            evidence_excerpt = _excerpt_from_critic(runtime_critic_path)
        elif focus_critic_name == document_critic_name:
            evidence_excerpt = _excerpt_from_critic(document_critic_path)
        elif focus_support_name == env_snapshot_name:
            evidence_excerpt = _excerpt_from_env_snapshot(env_snapshot_path)

        if not evidence_excerpt:
            evidence_excerpt = _excerpt_from_handoff(handoff_data)

        if handoff_data:
            review_focus["handoff_trigger"] = str(handoff_data.get("trigger") or "")
        review_focus["trigger"] = focus_trigger
        if focus_critic_name:
            review_focus["critic_artifact"] = _task_rel(focus_critic_name)
        if handoff_data:
            review_focus["supporting_artifact"] = _task_rel(session_handoff_name)
        elif focus_support_name:
            review_focus["supporting_artifact"] = _task_rel(focus_support_name)
        if evidence_excerpt:
            review_focus["evidence_excerpt"] = evidence_excerpt

        focus_check_ids = _pick_open_check_ids(handoff_data)
        if focus_check_ids:
            review_focus["focus_check_ids"] = focus_check_ids
        paths_in_focus = _pick_focus_paths(handoff_data)
        if paths_in_focus:
            review_focus["paths_in_focus"] = paths_in_focus
        do_not_regress = _pick_do_not_regress(handoff_data)
        if do_not_regress:
            review_focus["do_not_regress"] = do_not_regress

        if env_snapshot_surface:
            review_focus["environment_artifact"] = _task_rel(env_snapshot_name)
            review_focus["environment_reasons"] = env_signal_reasons[:4]

        try:
            from failure_memory import find_similar_failures

            similar_failures = find_similar_failures(task_dir, limit=3)
            similar_failure = similar_failures[0] if similar_failures else None
        except Exception:
            similar_failure = None
            similar_failures = []

        if similar_failure:
            review_focus["prior_similar_task"] = similar_failure.get("task_id")
            review_focus["prior_similar_artifact"] = os.path.join(
                TASK_DIR,
                str(similar_failure.get("task_id") or ""),
                str(similar_failure.get("artifact") or "TASK_STATE.yaml"),
            )
            if similar_failure.get("excerpt"):
                review_focus["prior_similar_excerpt"] = str(similar_failure.get("excerpt"))
            review_focus["prior_similar_count"] = len(similar_failures)
            review_focus["prior_similar_cases"] = [
                {
                    "task_id": str(item.get("task_id") or ""),
                    "artifact": str(item.get("artifact") or "TASK_STATE.yaml"),
                    "score": float(item.get("score") or 0.0),
                    "matching_check_ids": list(item.get("matching_check_ids") or [])[:3],
                    "matching_paths": list(item.get("matching_paths") or [])[:3],
                    "excerpt": str(item.get("excerpt") or "")[:120],
                }
                for item in similar_failures[:3]
            ]

    default_must_read_order = [
        "TASK_STATE.yaml",
        "PLAN.md",
        "CHECKS.yaml",
        "HANDOFF.md",
        "SESSION_HANDOFF.json",
        "RESULT.md",
    ]

    priority_must_read = []
    if review_focus.get("evidence_first"):
        if handoff_data:
            priority_must_read.append(session_handoff_name)
        if focus_critic_name:
            priority_must_read.append(focus_critic_name)
        if env_snapshot_surface and os.path.isfile(env_snapshot_path):
            priority_must_read.append(env_snapshot_name)
        for raw_name in handoff_data.get("files_to_read_first", []) if isinstance(handoff_data, dict) else []:
            if isinstance(raw_name, str) and raw_name.strip():
                priority_must_read.append(raw_name.strip())
        if focus_support_name:
            priority_must_read.append(focus_support_name)
        priority_must_read.extend(["TASK_STATE.yaml", request_name, "PLAN.md", "CHECKS.yaml", "HANDOFF.md"])
    elif planning_mode == "broad-build" and plan_verdict != "PASS":
        priority_must_read.extend(["TASK_STATE.yaml", request_name, env_snapshot_name, "PLAN.md", "CHECKS.yaml"])
    elif env_snapshot_surface:
        priority_must_read.extend(["TASK_STATE.yaml", env_snapshot_name, request_name, "PLAN.md", "CHECKS.yaml"])
    else:
        priority_must_read.extend(default_must_read_order)

    must_read = []
    seen = set()
    for rel_name in priority_must_read + default_must_read_order:
        if not rel_name or rel_name in seen:
            continue
        seen.add(rel_name)
        abs_path = _task_abs(rel_name)
        if os.path.isfile(abs_path):
            must_read.append(_task_rel(rel_name))
        if len(must_read) >= 4:
            break

    notes = []
    if routing_compiled != "true":
        notes.append("routing not compiled yet")
    if planning_mode == "broad-build":
        notes.append("planning_mode=broad-build")
    if plan_verdict != "PASS":
        notes.append(f"plan_verdict={plan_verdict}")
    if blocked_env_round:
        notes.append("blocked env: read environment snapshot")
    elif runtime_fix_round:
        notes.append("runtime fix round: evidence-first")
    elif document_fix_round:
        notes.append("document fix round: evidence-first")
    elif handoff_data:
        notes.append("session handoff present")
    if similar_failure and similar_failure.get("task_id"):
        notes.append(f"similar failure: {similar_failure['task_id']}")
    if env_snapshot_surface and not blocked_env_round:
        notes.append("env snapshot surfaced")
    if maintenance_task:
        notes.append("maintenance task: workflow surface unlocked")
    notes = notes[:3]

    if routing_compiled != "true":
        next_action = "Run mcp__plugin_harness_harness__task_start, then re-open mcp__plugin_harness_harness__task_context before planning or implementation."
    elif plan_verdict != "PASS":
        if planning_mode == "broad-build":
            next_action = (
                "Read REQUEST.md and ENVIRONMENT_SNAPSHOT.md first, then use the plan skill broad-build path to write "
                "01_product_spec.md, 02_design_language.md, 03_architecture.md, then PLAN.md before source changes."
            )
        else:
            next_action = "Get PLAN.md to critic-plan PASS before mutating source files."
    elif blocked_env_round:
        next_action = "Read ENVIRONMENT_SNAPSHOT.md first, repair the missing tool or setup assumption, then rerun task_verify before continuing."
    elif runtime_fix_round:
        if env_snapshot_surface:
            next_action = "Read the surfaced runtime evidence first, then consult ENVIRONMENT_SNAPSHOT.md before changing setup or toolchain assumptions, run mcp__plugin_harness_harness__task_verify, and re-check critics."
        else:
            next_action = "Read the surfaced runtime evidence first, fix the failing path, run mcp__plugin_harness_harness__task_verify, then re-check critics."
    elif document_fix_round:
        next_action = "Read the surfaced document evidence first, repair DOC_SYNC / notes, then re-run critic-document before closing."
    elif handoff_data:
        next_action = "Resume from SESSION_HANDOFF.json next_step and only then broaden repo exploration."
    elif lane == "investigate":
        next_action = "Write RESULT.md with findings and close after verification gates pass."
    else:
        if env_snapshot_surface:
            next_action = "Read ENVIRONMENT_SNAPSHOT.md before making setup or verification assumptions, implement the smallest diff for open checks, then run mcp__plugin_harness_harness__task_update_from_git_diff, task_verify, and task_close."
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
        "planning_mode": planning_mode,
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
        "review_focus": review_focus,
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
