#!/usr/bin/env python3
"""Shared helper for harness hook scripts.
Import at the top of each hook script: from _lib import *
"""

import json
import hashlib
import os
import re
import shlex
import sys
import shutil
import subprocess
import tempfile
import glob as _glob
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TASK_DIR = "doc/harness/tasks"
MANIFEST = "doc/harness/manifest.yaml"
CLAUDE_CODE_AGENT_TEAMS_MIN_VERSION = (2, 1, 32)
TASK_STATE_SCHEMA_VERSION = 1
CHECKS_SCHEMA_VERSION = 1
SESSION_HANDOFF_SCHEMA_VERSION = 1


def parse_semver_triplet(text):
    """Extract the first semantic version triplet from text."""
    if not text:
        return None
    match = re.search(r"\b(\d+)\.(\d+)\.(\d+)\b", str(text))
    if not match:
        return None
    return tuple(int(group) for group in match.groups())


def claude_code_agent_teams_min_version_str():
    """Return the minimum Claude Code version required for agent teams."""
    return ".".join(str(part) for part in CLAUDE_CODE_AGENT_TEAMS_MIN_VERSION)


def detect_claude_cli_version(timeout=5):
    """Return raw `claude --version` output or '' when unavailable."""
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=timeout)
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return ""
    if result.returncode != 0:
        return ""
    return (result.stdout or result.stderr or "").strip()


def claude_code_version_supports_agent_teams(version_text):
    """Return True when the Claude Code version supports agent teams."""
    parsed = parse_semver_triplet(version_text)
    return bool(parsed and parsed >= CLAUDE_CODE_AGENT_TEAMS_MIN_VERSION)


def native_agent_teams_runtime_probe():
    """Probe current-session readiness for native Claude Code teams."""
    version_text = detect_claude_cli_version()
    env_enabled = os.environ.get("CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS") == "1"
    version_supported = claude_code_version_supports_agent_teams(version_text)
    return {
        "required_min_version": claude_code_agent_teams_min_version_str(),
        "teams_env_set": env_enabled,
        "claude_version": version_text,
        "claude_available": bool(version_text),
        "claude_version_supported": version_supported,
        "ready": env_enabled and bool(version_text) and version_supported,
    }


def omc_runtime_probe():
    """Probe current-session readiness for oh-my-claudecode teams."""
    omc_available = shutil.which("omc") is not None
    omc_dir_exists = os.path.isdir(".omc") or os.path.isdir(os.path.join(os.path.expanduser("~"), ".omc"))
    return {
        "omc_available": omc_available,
        "omc_dir_exists": omc_dir_exists,
        "ready": omc_available,
    }

# ---------------------------------------------------------------------------
# Lazy stdin reader — read once, cache forever
# ---------------------------------------------------------------------------

_HOOK_INPUT = None
_HOOK_INPUT_READ = False
_YAML_LINES_CACHE = {}


def _invalidate_yaml_cache(filepath):
    """Drop a cached YAML entry after a write."""
    if filepath:
        _YAML_LINES_CACHE.pop(filepath, None)


def atomic_write_text(path, content):
    """Atomically write UTF-8 text content."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix='.tmp.', dir=directory)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
    _invalidate_yaml_cache(path)


def _yaml_line_value_from_content(content, field):
    match = re.search(r'^' + re.escape(field) + r':\s*(.*)$', str(content or ''), flags=re.MULTILINE)
    if not match:
        return None
    return match.group(1).strip()


def _yaml_int_from_content(content, field, default=0):
    raw = _yaml_line_value_from_content(content, field)
    if raw is None:
        return default
    raw = raw.strip().strip('"').strip("'")
    if raw.lower() in ('null', 'none', '~', ''):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _yaml_replace_or_insert_line(content, field, rendered_value, *, after_fields=None, before_field='updated'):
    """Replace or insert a top-level YAML scalar line."""
    text = str(content or '')
    line_text = f'{field}: {rendered_value}'
    pattern = r'^' + re.escape(field) + r':.*$'
    if re.search(pattern, text, flags=re.MULTILINE):
        return re.sub(pattern, line_text, text, flags=re.MULTILINE)

    lines = text.splitlines()
    inserted = False
    if lines:
        for after_field in (after_fields or []):
            for idx, line in enumerate(lines):
                if re.match(r'^' + re.escape(after_field) + r':', line):
                    lines.insert(idx + 1, line_text)
                    inserted = True
                    break
            if inserted:
                break
    if not inserted and before_field:
        for idx, line in enumerate(lines):
            if re.match(r'^' + re.escape(before_field) + r':', line):
                lines.insert(idx, line_text)
                inserted = True
                break
    if not inserted:
        lines.append(line_text)
    return '\n'.join(lines).rstrip('\n') + '\n'


def ensure_task_state_schema_content(content, *, bump_revision=False, timestamp=None):
    """Ensure TASK_STATE.yaml carries schema metadata and optional revisions."""
    text = str(content or '').rstrip('\n') + '\n'
    text = _yaml_replace_or_insert_line(
        text,
        'schema_version',
        TASK_STATE_SCHEMA_VERSION,
        after_fields=['task_id'],
        before_field='status',
    )

    has_state_revision = re.search(r'^state_revision:', text, flags=re.MULTILINE) is not None
    current_revision = _yaml_int_from_content(text, 'state_revision', default=0)
    if not has_state_revision:
        text = _yaml_replace_or_insert_line(
            text,
            'state_revision',
            current_revision,
            after_fields=['schema_version'],
            before_field='status',
        )
    if bump_revision:
        ts = timestamp or now_iso()
        next_revision = current_revision + 1
        text = _yaml_replace_or_insert_line(
            text,
            'state_revision',
            next_revision,
            after_fields=['schema_version'],
            before_field='status',
        )
        text = _yaml_replace_or_insert_line(text, 'updated', ts, before_field=None)
    return text.rstrip('\n') + '\n'


def write_task_state_content(state_file, content, *, bump_revision=False, timestamp=None):
    """Write TASK_STATE.yaml content with schema/revision metadata preserved."""
    final = ensure_task_state_schema_content(content, bump_revision=bump_revision, timestamp=timestamp)
    atomic_write_text(state_file, final)
    return final


def migrate_task_state_file(state_file, *, write=False):
    """Ensure TASK_STATE.yaml carries schema/revision fields.

    Legacy files without explicit schema_version/state_revision are treated as
    version 0 and migrated in-place when ``write`` is true.
    """
    report = {
        'artifact': 'TASK_STATE.yaml',
        'path': state_file,
        'exists': bool(state_file and os.path.isfile(state_file)),
        'changed': False,
        'schema_version_before': 0,
        'schema_version_after': TASK_STATE_SCHEMA_VERSION,
        'state_revision_before': 0,
        'state_revision_after': 0,
        'parent_revision_after': None,
    }
    if not report['exists']:
        return report
    try:
        with open(state_file, 'r', encoding='utf-8') as fh:
            original = fh.read()
    except OSError:
        report['exists'] = False
        report['error'] = 'read_failed'
        return report

    report['schema_version_before'] = _yaml_int_from_content(original, 'schema_version', default=0)
    report['state_revision_before'] = _yaml_int_from_content(original, 'state_revision', default=0)
    migrated = ensure_task_state_schema_content(original, bump_revision=False)
    report['state_revision_after'] = _yaml_int_from_content(migrated, 'state_revision', default=0)
    parent_value = _yaml_line_value_from_content(migrated, 'parent_revision')
    if parent_value is not None:
        parent_value = parent_value.strip().strip('"').strip("'")
        if parent_value.lower() in ('null', 'none', '~', ''):
            report['parent_revision_after'] = None
        else:
            try:
                report['parent_revision_after'] = int(parent_value)
            except (TypeError, ValueError):
                report['parent_revision_after'] = parent_value
    report['changed'] = migrated != original
    if write and report['changed']:
        atomic_write_text(state_file, migrated)
    return report


def ensure_checks_schema_content(content):
    """Ensure CHECKS.yaml has a top-level schema_version."""
    text = str(content or '').rstrip('\n') + '\n'
    return _yaml_replace_or_insert_line(
        text,
        'schema_version',
        CHECKS_SCHEMA_VERSION,
        after_fields=[],
        before_field='close_gate',
    )


def normalize_check_status_value(raw_status):
    """Normalize CHECKS.yaml status values into canonical lowercase forms.

    Canonical statuses are: planned, implemented_candidate, passed, failed,
    blocked, and skipped. Common aliases remain accepted so users do not need
    perfect schema recall during manual recovery work.
    """
    value = str(raw_status or '').strip().strip('\"').strip("'")
    if not value:
        return 'unknown'
    lowered = value.lower()
    aliases = {
        'pending': 'planned',
        'plan': 'planned',
        'planned': 'planned',
        'candidate': 'implemented_candidate',
        'implemented': 'implemented_candidate',
        'implemented_candidate': 'implemented_candidate',
        'pass': 'passed',
        'passed': 'passed',
        'ok': 'passed',
        'fail': 'failed',
        'failed': 'failed',
        'blocked': 'blocked',
        'skip': 'skipped',
        'skipped': 'skipped',
    }
    return aliases.get(lowered, lowered)


def render_default_checks_yaml(close_gate='standard'):
    """Return a canonical starter CHECKS.yaml scaffold."""
    gate = close_gate if close_gate in ('standard', 'strict_high_risk') else 'standard'
    ts = now_iso()
    return (
        f"schema_version: {CHECKS_SCHEMA_VERSION}\n"
        f"close_gate: {gate}\n"
        "checks:\n"
        "  - id: AC-001\n"
        "    title: \"Fill from PLAN.md acceptance criteria\"\n"
        "    status: planned\n"
        "    kind: functional\n"
        "    evidence_refs: []\n"
        "    reopen_count: 0\n"
        f"    last_updated: \"{ts}\"\n"
        "    notes: \"Replace this placeholder after the plan is approved.\"\n"
    )


def ensure_checks_template(task_dir, close_gate='standard'):
    """Ensure a canonical CHECKS.yaml starter exists for the task.

    Returns a small report describing the file path and whether the template was
    created or normalized. Existing non-empty ledgers are preserved except for
    lightweight schema / close_gate backfills.
    """
    task_dir = os.path.abspath(task_dir)
    checks_file = os.path.join(task_dir, 'CHECKS.yaml')
    gate = close_gate if close_gate in ('standard', 'strict_high_risk') else 'standard'
    created = False
    normalized = False

    if not os.path.exists(checks_file):
        atomic_write_text(checks_file, render_default_checks_yaml(gate))
        created = True
        return {
            'path': checks_file,
            'created': created,
            'normalized': normalized,
            'close_gate': gate,
        }

    try:
        with open(checks_file, 'r', encoding='utf-8') as fh:
            original = fh.read()
    except OSError:
        return {
            'path': checks_file,
            'created': created,
            'normalized': normalized,
            'close_gate': parse_checks_close_gate(checks_file),
        }

    migrated = ensure_checks_schema_content(original)
    if re.search(r'^close_gate\s*:', migrated, flags=re.MULTILINE):
        migrated = re.sub(r'^close_gate\s*:\s*.+$', f'close_gate: {gate}', migrated, flags=re.MULTILINE, count=1)
    else:
        lines = migrated.splitlines()
        insertion = [f'close_gate: {gate}']
        if lines and lines[0].startswith('schema_version:'):
            lines = [lines[0], *insertion, *lines[1:]]
        else:
            lines = [*insertion, *lines]
        migrated = '\n'.join(lines).rstrip('\n') + '\n'
    normalized = migrated != original
    if normalized:
        atomic_write_text(checks_file, migrated)

    return {
        'path': checks_file,
        'created': created,
        'normalized': normalized,
        'close_gate': gate,
    }


def migrate_checks_file(checks_file, *, write=False):
    """Ensure CHECKS.yaml carries a top-level schema_version."""
    report = {
        'artifact': 'CHECKS.yaml',
        'path': checks_file,
        'exists': bool(checks_file and os.path.isfile(checks_file)),
        'changed': False,
        'schema_version_before': 0,
        'schema_version_after': CHECKS_SCHEMA_VERSION,
    }
    if not report['exists']:
        return report
    try:
        with open(checks_file, 'r', encoding='utf-8') as fh:
            original = fh.read()
    except OSError:
        report['exists'] = False
        report['error'] = 'read_failed'
        return report
    report['schema_version_before'] = _yaml_int_from_content(original, 'schema_version', default=0)
    migrated = ensure_checks_schema_content(original)
    report['changed'] = migrated != original
    if write and report['changed']:
        atomic_write_text(checks_file, migrated)
    return report


def ensure_session_handoff_payload(payload):
    """Return SESSION_HANDOFF payload with explicit schema_version."""
    data = dict(payload or {})
    data['schema_version'] = SESSION_HANDOFF_SCHEMA_VERSION
    return data


def migrate_session_handoff_file(handoff_path, *, write=False):
    """Ensure SESSION_HANDOFF.json carries a top-level schema_version."""
    report = {
        'artifact': 'SESSION_HANDOFF.json',
        'path': handoff_path,
        'exists': bool(handoff_path and os.path.isfile(handoff_path)),
        'changed': False,
        'schema_version_before': 0,
        'schema_version_after': SESSION_HANDOFF_SCHEMA_VERSION,
    }
    if not report['exists']:
        return report
    try:
        with open(handoff_path, 'r', encoding='utf-8') as fh:
            payload = json.load(fh)
    except (OSError, ValueError, json.JSONDecodeError):
        report['exists'] = False
        report['error'] = 'read_failed'
        return report
    if not isinstance(payload, dict):
        report['error'] = 'non_object_payload'
        return report
    try:
        report['schema_version_before'] = int(payload.get('schema_version') or 0)
    except (TypeError, ValueError):
        report['schema_version_before'] = 0
    migrated = ensure_session_handoff_payload(payload)
    report['changed'] = migrated != payload
    if write and report['changed']:
        atomic_write_text(handoff_path, json.dumps(migrated, indent=2, ensure_ascii=False) + '\n')
    return report


def migrate_task_artifacts(task_dir, *, write=False):
    """Migrate versioned task-local artifacts for a single task directory."""
    task_dir = os.path.abspath(task_dir)
    results = [
        migrate_task_state_file(os.path.join(task_dir, 'TASK_STATE.yaml'), write=write),
        migrate_checks_file(os.path.join(task_dir, 'CHECKS.yaml'), write=write),
        migrate_session_handoff_file(os.path.join(task_dir, 'SESSION_HANDOFF.json'), write=write),
    ]
    return {
        'task_dir': task_dir,
        'write': bool(write),
        'artifacts': results,
        'changed': any(item.get('changed') for item in results if item.get('exists')),
    }


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


def _yaml_read_lines(filepath):
    """Read YAML file lines with a tiny mtime-based cache.

    The harness hot paths repeatedly look up many scalar / array fields from the
    same small TASK_STATE.yaml file. Re-opening the file dozens of times is
    wasted I/O, so cache the raw lines keyed by (mtime_ns, size) and let the
    existing lightweight parsers reuse them.
    """
    if not filepath or not os.path.isfile(filepath):
        return []
    try:
        stat_result = os.stat(filepath)
    except OSError:
        return []

    cache_key = (int(getattr(stat_result, "st_mtime_ns", 0)), int(getattr(stat_result, "st_size", 0)))
    cached = _YAML_LINES_CACHE.get(filepath)
    if cached and cached.get("key") == cache_key:
        return list(cached.get("lines") or [])

    try:
        with open(filepath, "r", encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return []

    _YAML_LINES_CACHE[filepath] = {"key": cache_key, "lines": list(lines)}
    return list(lines)


def yaml_field(field, filepath):
    """Parse a scalar field from a YAML file. Returns str or ''."""
    lines = _yaml_read_lines(filepath)
    for line in lines:
        m = re.match(r"^\s*" + re.escape(field) + r":\s*(.*)", line)
        if m:
            raw = m.group(1).rstrip("\n")
            # Strip surrounding quotes
            raw = raw.strip()
            raw = raw.strip('"').strip("'")
            return raw
    return ""


def yaml_array(field, filepath):
    """Parse a YAML sequence field. Returns list of strings.

    Handles both:
      field: [a, b, c]       # inline array
      field:                 # block sequence
        - a
        - b
    """
    lines = _yaml_read_lines(filepath)
    if not lines:
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


def verdict_freshness_field(verdict_field):
    """Return the TASK_STATE.yaml freshness field for a verdict field."""
    text = str(verdict_field or "").strip()
    if not text:
        return ""
    return f"{text}_freshness"


def verdict_freshness(filepath, verdict_field, default="current"):
    """Return canonical verdict freshness (``current`` or ``stale``).

    Older tasks may not carry explicit freshness fields yet, so missing or
    unknown values default to ``current`` for backward compatibility.
    """
    field_name = verdict_freshness_field(verdict_field)
    if not field_name:
        return str(default or "current").strip().lower() or "current"
    raw = yaml_field(field_name, filepath) or default
    value = str(raw or default or "current").strip().lower()
    if value not in ("current", "stale"):
        return str(default or "current").strip().lower() or "current"
    return value


def verdict_is_current(filepath, verdict_field, default="current"):
    """Return True when the verdict freshness is currently valid."""
    return verdict_freshness(filepath, verdict_field, default=default) == "current"


def format_verdict_with_freshness(verdict, freshness):
    """Format verdict strings as ``PASS`` or ``PASS (stale)`` for UI output."""
    verdict_text = str(verdict or "pending")
    freshness_text = str(freshness or "current").strip().lower() or "current"
    if freshness_text == "current":
        return verdict_text
    return f"{verdict_text} ({freshness_text})"


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


def find_repo_root(start_path=None):
    """Best-effort repository root discovery.

    Searches upward from ``start_path`` (or cwd) for either the harness
    manifest or a git directory. Falls back to the normalized starting
    directory when no marker is found.
    """
    start = start_path or os.getcwd()
    current = os.path.abspath(start)
    if os.path.isfile(current):
        current = os.path.dirname(current)

    while True:
        manifest_candidate = os.path.join(current, *normalize_path(MANIFEST).split("/"))
        git_candidate = os.path.join(current, ".git")
        if os.path.isfile(manifest_candidate) or os.path.isdir(git_candidate):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            if os.path.isfile(start):
                return os.path.dirname(os.path.abspath(start))
            return os.path.abspath(start)
        current = parent


def normalize_path(path):
    """Normalize a filesystem path into slash-separated repo-ish form."""
    if path is None:
        return ""
    p = str(path).strip()
    if not p:
        return ""
    p = p.replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    if p.startswith("/"):
        p = p[1:]
    if p.endswith("/"):
        p = p.rstrip("/")
    return p


def repo_relpath(path, repo_root=None):
    """Return a normalized repo-relative path when possible."""
    if path is None:
        return ""
    text = str(path).strip()
    if not text:
        return ""

    repo_root = os.path.abspath(repo_root or find_repo_root())
    abs_path = os.path.abspath(text) if os.path.isabs(text) else os.path.abspath(os.path.join(repo_root, text))

    try:
        common = os.path.commonpath([repo_root, abs_path])
    except ValueError:
        common = ""
    if common == repo_root:
        rel = os.path.relpath(abs_path, repo_root)
        return normalize_path(rel)

    marker_rel = normalize_path(TASK_DIR)
    marker = f"/{marker_rel}/"
    abs_norm = abs_path.replace("\\", "/")
    if marker in abs_norm:
        suffix = abs_norm.split(marker, 1)[1]
        return normalize_path(f"{marker_rel}/{suffix}")

    return normalize_path(text)


def is_task_artifact_path(path):
    """Return True when a path points inside a canonical task artifact dir."""
    norm = repo_relpath(path)
    task_prefix = normalize_path(TASK_DIR)
    if not norm or not task_prefix:
        return False
    return bool(re.match(r"^" + re.escape(task_prefix) + r"/TASK__[^/]+(?:/|$)", norm))


def canonical_task_id(task_id=None, slug=None, task_dir=None):
    """Return canonical ``TASK__<id>`` from a task id, slug, or task dir."""
    candidate = ""
    if task_id:
        candidate = str(task_id).strip()
    elif slug:
        candidate = str(slug).strip()
    elif task_dir:
        candidate = os.path.basename(str(task_dir).rstrip("/"))
    candidate = candidate.strip()
    if not candidate:
        return ""
    if candidate.startswith("TASK__"):
        return candidate
    return f"TASK__{candidate}"


def canonical_task_dir(task_id=None, slug=None, task_dir=None, tasks_dir=None, repo_root=None):
    """Return the canonical task directory for a task reference."""
    repo_root = os.path.abspath(repo_root or find_repo_root())
    tasks_dir = tasks_dir or TASK_DIR
    if os.path.isabs(str(tasks_dir)):
        tasks_root = os.path.normpath(str(tasks_dir))
    else:
        tasks_root = os.path.normpath(os.path.join(repo_root, str(tasks_dir)))
    task_name = canonical_task_id(task_id=task_id, slug=slug, task_dir=task_dir)
    if not task_name:
        return ""
    return os.path.join(tasks_root, task_name)


def parse_changed_files(input_str=None):
    """Parse changed files from hook stdin JSON payload.

    Handles:
      {"file_path": "src/foo.ts"}
      {"paths": ["src/a.ts", "src/b.ts"]}
      {"files": ["src/a.ts"]}
      {"file": "src/foo.ts"}

    Returns sorted deduplicated list of normalized repo-relative paths.
    """
    if input_str is None:
        input_str = read_hook_input()
    if not input_str:
        return []

    result = set()
    repo_root = find_repo_root()

    # Try array fields first
    for field in ("paths", "files", "changed_files"):
        arr = json_array(field, input_str)
        for f in arr:
            if f:
                norm = repo_relpath(f, repo_root=repo_root)
                if norm:
                    result.add(norm)

    # Try single-value fields if no array results
    if not result:
        for field in ("file_path", "file", "path"):
            val = json_field(field, input_str)
            if val:
                norm = repo_relpath(val, repo_root=repo_root)
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
                        norm = repo_relpath(val, repo_root=repo_root)
                        if norm:
                            result.add(norm)
                            break
                    elif val and isinstance(val, list):
                        for item in val:
                            if item and isinstance(item, str):
                                norm = repo_relpath(item, repo_root=repo_root)
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


TEAM_PLAN_REQUIRED_HEADINGS = (
    "## Worker Roster",
    "## Owned Writable Paths",
    "## Shared Read-Only Paths",
    "## Forbidden Writes",
    "## Synthesis Strategy",
)

TEAM_SYNTHESIS_REQUIRED_HEADINGS = (
    "## Integrated Result",
    "## Cross-Checks",
    "## Verification Summary",
    "## Residual Risks",
)

TEAM_WORKER_SUMMARY_REQUIRED_HEADINGS = (
    "## Completed Work",
    "## Owned Paths Handled",
    "## Verification",
    "## Residual Risks",
)

TEAM_PLACEHOLDER_MARKERS = (
    "TODO:",
    "TBD",
    "<fill",
    "<todo",
    "[todo]",
)

GLOB_META_CHARS = set("*?[")

TEAM_CANONICAL_ROLES = {
    "developer",
    "writer",
    "critic-plan",
    "critic-runtime",
    "critic-document",
    "harness",
    "plan-skill",
}

# Bump when bootstrap / dispatch / launch artifacts change shape so existing
# task-local packs refresh automatically instead of silently reusing stale files.
TEAM_BOOTSTRAP_SCHEMA_VERSION = 2
TEAM_RELAUNCH_SCHEMA_VERSION = 1

AGENT_ROLE_PREFIXES = (
    ("harness:critic-document", "critic-document"),
    ("harness:critic-runtime", "critic-runtime"),
    ("harness:critic-plan", "critic-plan"),
    ("harness:developer", "developer"),
    ("harness:writer", "writer"),
    ("harness:harness", "harness"),
    ("critic-document", "critic-document"),
    ("critic-runtime", "critic-runtime"),
    ("critic-plan", "critic-plan"),
    ("developer", "developer"),
    ("writer", "writer"),
    ("harness", "harness"),
)

TEAM_DOCUMENTATION_ROLE_ALIASES = {
    "writer": "doc_sync",
    "doc-sync": "doc_sync",
    "docsync": "doc_sync",
    "doc-writer": "doc_sync",
    "documentation": "doc_sync",
    "documentation-writer": "doc_sync",
    "docs": "doc_sync",
    "critic-document": "document_critic",
    "document-critic": "document_critic",
    "doc-critic": "document_critic",
    "doc-review": "document_critic",
    "doc-reviewer": "document_critic",
    "documentation-review": "document_critic",
    "documentation-reviewer": "document_critic",
}


def get_agent_role(raw_agent_name=None):
    """Return the canonical harness role from CLAUDE_AGENT_NAME when available."""
    raw = str(
        raw_agent_name
        if raw_agent_name is not None
        else os.environ.get("CLAUDE_AGENT_NAME", "")
    ).strip()
    if not raw:
        return ""
    for prefix, role in AGENT_ROLE_PREFIXES:
        if raw == prefix:
            return role
        if any(raw.startswith(prefix + sep) for sep in (":", "/", "@")):
            return role
    return raw


def get_team_worker_name(known_workers=None, raw_agent_name=None, explicit_worker=None):
    """Best-effort worker identity from env or worker-suffixed agent names."""
    explicit = str(
        explicit_worker
        if explicit_worker is not None
        else os.environ.get("HARNESS_TEAM_WORKER", "")
    ).strip()
    known = [str(item).strip() for item in (known_workers or []) if str(item).strip()]
    if explicit:
        return explicit

    raw = str(
        raw_agent_name
        if raw_agent_name is not None
        else os.environ.get("CLAUDE_AGENT_NAME", "")
    ).strip()
    if not raw:
        return ""

    candidates = [raw]
    for sep in (":", "/", "@"):
        if sep in raw:
            candidates.append(raw.split(sep)[-1])

    seen = set()
    for candidate in candidates:
        worker = candidate.strip()
        if not worker or worker in seen:
            continue
        seen.add(worker)
        if known and worker in known:
            return worker
        if worker in TEAM_CANONICAL_ROLES or worker.startswith("harness:"):
            continue
        if re.match(r"^[A-Za-z0-9_.-]+$", worker) and (
            worker.startswith("worker-")
            or worker.startswith("lead")
            or worker.startswith("integrator")
            or worker.startswith("reviewer")
        ):
            return worker
    return ""


def _team_markdown_sections(text_value):
    """Return {heading -> body} for top-level ## markdown sections."""
    sections = {}
    current = None
    lines = []
    for raw_line in (text_value or "").splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("## "):
            if current is not None:
                sections[current] = "\n".join(lines).strip()
            current = line.strip()
            lines = []
            continue
        if current is not None:
            lines.append(line)
    if current is not None:
        sections[current] = "\n".join(lines).strip()
    return sections


def _team_bullet_lines(section_text):
    items = []
    for raw_line in (section_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if re.match(r"^[-*+]\s+", line):
            items.append(re.sub(r"^[-*+]\s+", "", line).strip())
    return items


def _team_first_meaningful_line(section_text):
    for raw_line in (section_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*+]\s+", "", line).strip()
        if not line:
            continue
        return line[:180]
    return ""


def _split_path_specs(raw_value):
    if raw_value is None:
        return []
    pieces = re.split(r"[;,]", str(raw_value))
    result = []
    for piece in pieces:
        clean = re.sub(r'[\s\U0001F000-\U0010FFFF\u2000-\u2BFF\u2600-\u26FF\u2700-\u27BF\u2500-\u25FF]+$', '', piece.strip())
        norm = normalize_path(clean)
        if not norm:
            continue
        if norm.lower() in ("none", "n/a"):
            continue
        result.append(norm)
    return result


def _normalize_team_worker_name(raw_value):
    worker = str(raw_value or "").strip()
    if not worker:
        return ""
    return re.sub(r"\s+", "-", worker)


def _parse_team_roster(section_text):
    workers = []
    worker_roles = {}
    errors = []
    for item in _team_bullet_lines(section_text):
        m = re.match(r"^([^:–—]+?)(?:\s*[:–—]\s*(.+))?$", item)
        if not m:
            errors.append(f"invalid worker roster entry '{item}'")
            continue
        worker = _normalize_team_worker_name(m.group(1))
        if not worker:
            errors.append(f"invalid worker roster entry '{item}'")
            continue
        if worker not in workers:
            workers.append(worker)
        role = (m.group(2) or "").strip()
        if role:
            worker_roles[worker] = role
    return workers, worker_roles, errors


def _parse_team_assignment_section(section_text, workers):
    mapping = {}
    errors = []
    roster = list(workers or [])
    implicit_single_worker = roster[0] if len(roster) == 1 else ""
    for item in _team_bullet_lines(section_text):
        m = re.match(r"^([^:–—]+?)\s*[:–—]\s*(.+)$", item)
        worker = ""
        payload = ""
        if m:
            worker = _normalize_team_worker_name(m.group(1))
            payload = (m.group(2) or "").strip()
        elif implicit_single_worker:
            worker = implicit_single_worker
            payload = item
        else:
            errors.append(f"assignment '{item}' must use '<worker>: <path>' format")
            continue

        paths = _split_path_specs(payload)
        if not paths and str(payload).strip().lower() not in ("none", "n/a"):
            errors.append(f"assignment for '{worker}' has no writable paths")
            continue
        mapping.setdefault(worker, []).extend(paths)
    return mapping, errors


def _parse_team_shared_paths(section_text):
    shared_paths = []
    for item in _team_bullet_lines(section_text):
        shared_paths.extend(_split_path_specs(item))
    return shared_paths


def _parse_team_synthesis_strategy(section_text, workers, worker_roles):
    """Infer which worker(s) act as synthesis owners / lead integrators.

    Heuristic and deliberately conservative:
      - strong signal: worker name starts with lead/integrator
      - strong signal: worker role text includes lead/integrator/synthesis/merge
      - strong signal: synthesis strategy mentions a worker near merge/integrate/synthesis terms
      - if there is only one worker, that worker is implicitly the synthesis owner

    When no explicit synthesis owner is recoverable, we keep all workers in the
    summary-required set for backward compatibility.
    """
    roster = [str(item).strip() for item in (workers or []) if str(item).strip()]
    roles = {str(k).strip(): str(v or '').strip() for k, v in (worker_roles or {}).items()}
    normalized_section = re.sub(r"\s+", " ", str(section_text or "").lower())

    strong_candidates = []
    for worker in roster:
        worker_norm = str(worker or "").strip().lower()
        role_norm = roles.get(worker, "").lower()
        strong = False
        if worker_norm.startswith(("lead", "integrator")):
            strong = True
        elif any(token in role_norm for token in ("lead", "integrat", "synthes", "merge")):
            strong = True
        else:
            escaped = re.escape(worker_norm)
            patterns = (
                rf"\b{escaped}\b[^.\n]{{0,60}}\b(merge|integrat|synthes)\w*\b",
                rf"\b(merge|integrat|synthes)\w*\b[^.\n]{{0,60}}\b{escaped}\b",
            )
            strong = any(re.search(pattern, normalized_section) for pattern in patterns)
        if strong and worker not in strong_candidates:
            strong_candidates.append(worker)

    generic_role_hint = ""
    if re.search(r"\blead\b", normalized_section):
        generic_role_hint = "lead"
    elif re.search(r"\bintegrator\b", normalized_section):
        generic_role_hint = "integrator"

    synthesis_workers = list(strong_candidates)
    if not synthesis_workers and len(roster) == 1:
        synthesis_workers = [roster[0]]

    summary_workers = [worker for worker in roster if worker not in synthesis_workers]
    if not synthesis_workers:
        summary_workers = list(roster)

    return {
        "synthesis_workers": synthesis_workers,
        "summary_workers": summary_workers,
        "generic_role_hint": generic_role_hint,
        "has_explicit_synthesis_owner": bool(synthesis_workers),
    }


def _split_team_names(raw_value):
    names = []
    for piece in re.split(r"[;,]", str(raw_value or "")):
        worker = _normalize_team_worker_name(piece)
        if not worker:
            continue
        if worker.lower() in ("none", "n/a"):
            continue
        if worker not in names:
            names.append(worker)
    return names


def _normalize_documentation_role_name(raw_value):
    key = re.sub(r"\s+", "-", str(raw_value or "").strip().lower()).replace("_", "-")
    return TEAM_DOCUMENTATION_ROLE_ALIASES.get(key, "")


def _infer_documentation_workers(workers, worker_roles, synthesis_workers):
    roster = [str(item).strip() for item in (workers or []) if str(item).strip()]
    roles = {str(k).strip(): str(v or "").strip() for k, v in (worker_roles or {}).items()}
    doc_sync = []
    document_critic = []

    def _add(target, worker):
        if worker and worker not in target:
            target.append(worker)

    for worker in roster:
        combined = f"{worker} {roles.get(worker, '')}".lower()
        writer_hint = bool(
            re.search(r"\b(writer|doc-writer|docsync|doc-sync|documentation|docs)\b", combined)
        )
        document_hint = bool(
            re.search(r"\b(doc|docs|documentation)\b", combined)
        )
        critic_hint = bool(
            re.search(r"\b(critic|review|reviewer|qa)\b", combined)
        )
        if writer_hint:
            _add(doc_sync, worker)
        if critic_hint and (document_hint or worker.startswith("reviewer") or "critic-document" in combined):
            _add(document_critic, worker)

    doc_sync_source = ""
    document_critic_source = ""
    if doc_sync:
        doc_sync_source = "inferred"
    elif synthesis_workers:
        doc_sync = [worker for worker in (synthesis_workers or []) if worker in roster]
        if doc_sync:
            doc_sync_source = "fallback"
    elif len(roster) == 1:
        doc_sync = [roster[0]]
        doc_sync_source = "fallback"

    if document_critic:
        document_critic_source = "inferred"
    elif len(roster) == 1:
        document_critic = [roster[0]]
        document_critic_source = "fallback"

    return {
        "doc_sync_workers": doc_sync,
        "doc_sync_owner_source": doc_sync_source,
        "document_critic_workers": document_critic,
        "document_critic_owner_source": document_critic_source,
    }


def _parse_team_documentation_ownership(section_text, workers, worker_roles, synthesis_workers):
    explicit_doc_sync = []
    explicit_document_critic = []
    errors = []

    for item in _team_bullet_lines(section_text):
        match = re.match(r"^([^:–—]+?)\s*[:–—]\s*(.+)$", item)
        if not match:
            errors.append(f"documentation ownership entry '{item}' must use '<role>: <worker>' format")
            continue
        role_key = _normalize_documentation_role_name(match.group(1))
        if not role_key:
            errors.append(
                f"documentation ownership entry '{item}' must target writer or critic-document"
            )
            continue
        names = _split_team_names(match.group(2))
        if not names:
            errors.append(f"documentation ownership for '{match.group(1).strip()}' has no workers")
            continue
        unknown = [name for name in names if name not in (workers or [])]
        if unknown:
            errors.append(
                f"documentation ownership for '{match.group(1).strip()}' references unknown workers: "
                + ", ".join(unknown)
            )
            continue
        target = explicit_doc_sync if role_key == "doc_sync" else explicit_document_critic
        for name in names:
            if name not in target:
                target.append(name)

    inferred = _infer_documentation_workers(workers, worker_roles, synthesis_workers)
    doc_sync_workers = list(explicit_doc_sync or inferred.get("doc_sync_workers") or [])
    doc_sync_source = "explicit" if explicit_doc_sync else str(inferred.get("doc_sync_owner_source") or "")
    document_critic_workers = list(explicit_document_critic or inferred.get("document_critic_workers") or [])
    document_critic_source = "explicit" if explicit_document_critic else str(inferred.get("document_critic_owner_source") or "")

    return {
        "doc_sync_workers": doc_sync_workers,
        "doc_sync_owner_source": doc_sync_source,
        "document_critic_workers": document_critic_workers,
        "document_critic_owner_source": document_critic_source,
        "has_explicit_documentation_owner": bool(explicit_doc_sync or explicit_document_critic),
        "errors": errors,
    }

def _team_glob_to_regex(pattern_value):
    pattern = normalize_path(str(pattern_value or "").strip())
    if not pattern:
        return re.compile(r"a^")

    out = ["^"]
    i = 0
    while i < len(pattern):
        char = pattern[i]
        if char == "*":
            if i + 1 < len(pattern) and pattern[i + 1] == "*":
                i += 2
                if i < len(pattern) and pattern[i] == "/":
                    out.append("(?:.*/)?")
                    i += 1
                else:
                    out.append(".*")
                continue
            out.append("[^/]*")
        elif char == "?":
            out.append("[^/]")
        elif char == "[":
            closing = pattern.find("]", i + 1)
            if closing == -1:
                out.append(r"\[")
            else:
                out.append(pattern[i:closing + 1])
                i = closing
        else:
            out.append(re.escape(char))
        i += 1
    out.append("$")
    return re.compile("".join(out))


def team_glob_match(path_value, pattern_value):
    """Return True when normalized path_value matches the ownership glob."""
    path_norm = normalize_path(str(path_value or "").strip())
    pattern_norm = normalize_path(str(pattern_value or "").strip())
    if not path_norm or not pattern_norm:
        return False
    try:
        return bool(_team_glob_to_regex(pattern_norm).match(path_norm))
    except re.error:
        return path_norm == pattern_norm


def _team_glob_samples(pattern_value):
    pattern = normalize_path(str(pattern_value or "").strip())
    if not pattern:
        return []

    def _build(replacement_for_double_star):
        chars = []
        i = 0
        while i < len(pattern):
            char = pattern[i]
            if char == "*":
                if i + 1 < len(pattern) and pattern[i + 1] == "*":
                    chars.append(replacement_for_double_star)
                    i += 2
                    continue
                chars.append("item")
            elif char == "?":
                chars.append("q")
            elif char == "[":
                closing = pattern.find("]", i + 1)
                if closing == -1:
                    chars.append("x")
                else:
                    inner = pattern[i + 1:closing]
                    chars.append(inner[:1] or "x")
                    i = closing
            else:
                chars.append(char)
            i += 1
        sample = normalize_path("".join(chars).replace("//", "/"))
        return sample or pattern

    samples = []
    for repl in ("nested/file", "file"):
        sample = _build(repl)
        if sample and sample not in samples:
            samples.append(sample)
    literal_fallback = re.sub(r"[*?\[\]]", "", pattern).replace("//", "/")
    literal_fallback = normalize_path(literal_fallback)
    if literal_fallback and literal_fallback not in samples:
        samples.append(literal_fallback)
    return samples


def _team_static_prefix(pattern_value):
    pattern = normalize_path(str(pattern_value or "").strip())
    if not pattern:
        return ""
    stop = len(pattern)
    for idx, char in enumerate(pattern):
        if char in GLOB_META_CHARS:
            stop = idx
            break
    prefix = pattern[:stop]
    if prefix.endswith("/"):
        return prefix.rstrip("/")
    if "/" in prefix:
        return prefix.rsplit("/", 1)[0]
    return prefix


def team_patterns_overlap(left_pattern, right_pattern):
    """Best-effort conservative overlap test for two ownership globs."""
    left = normalize_path(str(left_pattern or "").strip())
    right = normalize_path(str(right_pattern or "").strip())
    if not left or not right:
        return False
    if left == right:
        return True

    left_meta = any(char in left for char in GLOB_META_CHARS)
    right_meta = any(char in right for char in GLOB_META_CHARS)
    if not left_meta and not right_meta:
        return left == right
    if not left_meta:
        return team_glob_match(left, right)
    if not right_meta:
        return team_glob_match(right, left)

    for sample in _team_glob_samples(left):
        if team_glob_match(sample, right):
            return True
    for sample in _team_glob_samples(right):
        if team_glob_match(sample, left):
            return True

    left_prefix = _team_static_prefix(left)
    right_prefix = _team_static_prefix(right)
    if left_prefix and right_prefix:
        return (
            left_prefix == right_prefix
            or left_prefix.startswith(right_prefix + "/")
            or right_prefix.startswith(left_prefix + "/")
        )
    return False


def parse_team_plan(path_value):
    """Parse TEAM_PLAN.md into worker ownership + synthesis metadata."""
    empty = {
        "exists": False,
        "workers": [],
        "worker_roles": {},
        "owned_paths": {},
        "shared_read_only_paths": [],
        "forbidden_paths": {},
        "synthesis_workers": [],
        "summary_workers": [],
        "synthesis_role_hint": "",
        "has_explicit_synthesis_owner": False,
        "doc_sync_workers": [],
        "doc_sync_owner_source": "",
        "document_critic_workers": [],
        "document_critic_owner_source": "",
        "has_explicit_documentation_owner": False,
        "errors": [],
        "ownership_ready": False,
        "owned_path_count": 0,
    }
    if not path_value or not os.path.isfile(path_value):
        return dict(empty)

    try:
        with open(path_value, "r", encoding="utf-8") as fh:
            text_value = fh.read()
    except OSError:
        return dict(empty)

    sections = _team_markdown_sections(text_value)
    workers, worker_roles, roster_errors = _parse_team_roster(sections.get("## Worker Roster", ""))
    owned_paths, owned_errors = _parse_team_assignment_section(
        sections.get("## Owned Writable Paths", ""),
        workers,
    )
    forbidden_paths, forbidden_errors = _parse_team_assignment_section(
        sections.get("## Forbidden Writes", ""),
        workers,
    )
    shared_paths = _parse_team_shared_paths(sections.get("## Shared Read-Only Paths", ""))
    synthesis_meta = _parse_team_synthesis_strategy(
        sections.get("## Synthesis Strategy", ""),
        workers,
        worker_roles,
    )
    documentation_meta = _parse_team_documentation_ownership(
        sections.get("## Documentation Ownership", ""),
        workers,
        worker_roles,
        synthesis_meta.get("synthesis_workers") or [],
    )

    errors = list(roster_errors) + list(owned_errors) + list(forbidden_errors) + list(documentation_meta.get("errors") or [])

    if not workers:
        errors.append("worker roster must list at least one worker")

    unknown_owned = sorted(worker for worker in owned_paths if worker not in workers)
    if unknown_owned:
        errors.append("owned writable paths reference unknown workers: " + ", ".join(unknown_owned))

    unknown_forbidden = sorted(worker for worker in forbidden_paths if worker not in workers)
    if unknown_forbidden:
        errors.append("forbidden writes reference unknown workers: " + ", ".join(unknown_forbidden))

    missing_owned = [worker for worker in workers if not owned_paths.get(worker)]
    if missing_owned:
        errors.append("owned writable paths missing for: " + ", ".join(missing_owned))

    missing_forbidden = [worker for worker in workers if worker not in forbidden_paths]
    if missing_forbidden and len(workers) > 1:
        errors.append("forbidden writes missing for: " + ", ".join(missing_forbidden))

    overlap_messages = []
    for idx, worker_a in enumerate(workers):
        for worker_b in workers[idx + 1:]:
            for left in owned_paths.get(worker_a, []):
                for right in owned_paths.get(worker_b, []):
                    if team_patterns_overlap(left, right):
                        overlap_messages.append(f"{worker_a}:{left} overlaps {worker_b}:{right}")
    if overlap_messages:
        errors.append("overlapping writable ownership: " + "; ".join(overlap_messages[:4]))

    shared_conflicts = []
    for shared in shared_paths:
        for worker in workers:
            for owned in owned_paths.get(worker, []):
                if team_patterns_overlap(shared, owned):
                    shared_conflicts.append(f"{shared} overlaps {worker}:{owned}")
    if shared_conflicts:
        errors.append("shared read-only paths overlap writable ownership: " + "; ".join(shared_conflicts[:4]))

    self_conflicts = []
    for worker in workers:
        forbidden_for_worker = forbidden_paths.get(worker, [])
        for owned in owned_paths.get(worker, []):
            for forbidden in forbidden_for_worker:
                if team_patterns_overlap(owned, forbidden):
                    self_conflicts.append(f"{worker}:{owned} conflicts with forbidden {forbidden}")
    if self_conflicts:
        errors.append("worker owned paths overlap their forbidden paths: " + "; ".join(self_conflicts[:4]))

    cross_coverage = []
    for worker in workers:
        other_owned = []
        for other_worker in workers:
            if other_worker == worker:
                continue
            other_owned.extend(owned_paths.get(other_worker, []))
        forbidden_for_worker = forbidden_paths.get(worker, [])
        missing_coverage = [
            pattern for pattern in other_owned
            if not any(team_patterns_overlap(pattern, forbidden) for forbidden in forbidden_for_worker)
        ]
        if missing_coverage:
            cross_coverage.append(
                f"{worker} missing coverage for: " + ", ".join(missing_coverage[:3])
            )
    if cross_coverage:
        errors.append("forbidden writes do not cover peer-owned paths: " + "; ".join(cross_coverage[:3]))

    synthesis_workers = [worker for worker in (synthesis_meta.get("synthesis_workers") or []) if worker in workers]
    summary_workers = [worker for worker in (synthesis_meta.get("summary_workers") or []) if worker in workers]
    if not synthesis_workers:
        summary_workers = list(workers)

    owned_path_count = sum(len(values) for values in owned_paths.values())
    return {
        "exists": True,
        "workers": workers,
        "worker_roles": worker_roles,
        "owned_paths": {worker: list(values) for worker, values in owned_paths.items()},
        "shared_read_only_paths": list(shared_paths),
        "forbidden_paths": {worker: list(values) for worker, values in forbidden_paths.items()},
        "synthesis_workers": synthesis_workers,
        "summary_workers": summary_workers,
        "synthesis_role_hint": str(synthesis_meta.get("generic_role_hint") or ""),
        "has_explicit_synthesis_owner": bool(synthesis_meta.get("has_explicit_synthesis_owner")),
        "doc_sync_workers": list(documentation_meta.get("doc_sync_workers") or []),
        "doc_sync_owner_source": str(documentation_meta.get("doc_sync_owner_source") or ""),
        "document_critic_workers": list(documentation_meta.get("document_critic_workers") or []),
        "document_critic_owner_source": str(documentation_meta.get("document_critic_owner_source") or ""),
        "has_explicit_documentation_owner": bool(documentation_meta.get("has_explicit_documentation_owner")),
        "errors": errors,
        "ownership_ready": not errors and bool(workers) and owned_path_count > 0,
        "owned_path_count": owned_path_count,
    }


def resolve_team_path_ownership(plan_data, filepath):
    """Return ownership facts for a candidate repo write under TEAM_PLAN.md."""
    normalized_path = normalize_path(str(filepath or "").strip())
    info = {
        "path": normalized_path,
        "owners": [],
        "shared_read_only": False,
        "forbidden_by": [],
    }
    if not normalized_path or not isinstance(plan_data, dict):
        return info

    for shared_pattern in plan_data.get("shared_read_only_paths", []):
        if team_glob_match(normalized_path, shared_pattern):
            info["shared_read_only"] = True
            break

    for worker, patterns in (plan_data.get("owned_paths") or {}).items():
        if any(team_glob_match(normalized_path, pattern) for pattern in patterns):
            info["owners"].append(worker)

    for worker, patterns in (plan_data.get("forbidden_paths") or {}).items():
        if any(team_glob_match(normalized_path, pattern) for pattern in patterns):
            info["forbidden_by"].append(worker)

    return info


def team_worker_summary_relpath(worker_name):
    """Return canonical relative path for a team worker summary artifact."""
    worker = _normalize_team_worker_name(worker_name)
    if not worker:
        return ""
    filename = worker if worker.startswith("worker-") else f"worker-{worker}"
    return normalize_path(os.path.join("team", filename + ".md"))


def _team_worker_summary_parse(path_value, worker_name, plan_data=None):
    """Parse one worker summary and validate its claimed owned paths."""
    readiness = _team_artifact_readiness(path_value, TEAM_WORKER_SUMMARY_REQUIRED_HEADINGS)
    result = {
        "exists": bool(readiness.get("exists")),
        "ready": bool(readiness.get("ready")),
        "missing_sections": list(readiness.get("missing_sections") or []),
        "has_placeholders": bool(readiness.get("has_placeholders")),
        "errors": list(readiness.get("semantic_errors") or []),
        "mtime": float(readiness.get("mtime") or 0.0),
        "owned_paths_handled": [],
        "explicit_none": False,
        "completed_excerpt": "",
        "verification_excerpt": "",
        "residual_risks_excerpt": "",
    }
    if not result["exists"]:
        return result

    try:
        with open(path_value, "r", encoding="utf-8") as fh:
            text_value = fh.read()
    except OSError:
        return result

    sections = _team_markdown_sections(text_value)
    completed_body = sections.get("## Completed Work", "").strip()
    verification_body = sections.get("## Verification", "").strip()
    risk_body = sections.get("## Residual Risks", "").strip()
    owned_body = sections.get("## Owned Paths Handled", "").strip()

    result["completed_excerpt"] = _team_first_meaningful_line(completed_body)
    result["verification_excerpt"] = _team_first_meaningful_line(verification_body)
    result["residual_risks_excerpt"] = _team_first_meaningful_line(risk_body)

    if not completed_body:
        result["errors"].append("completed work section must not be empty")
    if not verification_body:
        result["errors"].append("verification section must not be empty")
    if not risk_body:
        result["errors"].append("residual risks section must not be empty")

    owned_items = _team_bullet_lines(owned_body)
    owned_paths = []
    explicit_none = False
    for item in owned_items:
        if str(item).strip().lower() in ("none", "n/a"):
            explicit_none = True
            continue
        owned_paths.extend(_split_path_specs(item))
    if not owned_paths and not explicit_none:
        result["errors"].append("owned paths handled must list at least one path or `none`")

    worker = _normalize_team_worker_name(worker_name)
    for handled_path in owned_paths:
        ownership = resolve_team_path_ownership(plan_data or {}, handled_path)
        owners = list(ownership.get("owners") or [])
        if ownership.get("shared_read_only"):
            result["errors"].append(
                f"owned paths handled includes shared read-only path '{handled_path}'"
            )
            continue
        if worker and worker not in owners:
            if owners:
                result["errors"].append(
                    f"owned path '{handled_path}' is owned by {', '.join(owners)}"
                )
            else:
                result["errors"].append(
                    f"owned path '{handled_path}' falls outside TEAM_PLAN.md ownership"
                )
        if worker and worker in (ownership.get("forbidden_by") or []):
            result["errors"].append(
                f"owned path '{handled_path}' is forbidden for '{worker}'"
            )

    result["owned_paths_handled"] = owned_paths
    result["explicit_none"] = explicit_none
    result["ready"] = (
        not result["missing_sections"]
        and not result["has_placeholders"]
        and not result["errors"]
    )
    return result

def team_worker_summary_status(task_dir, plan_data=None, plan_ready=False):
    """Return readiness for expected team/worker summary artifacts."""
    plan_workers = list((plan_data or {}).get("workers") or [])
    expected_workers = list((plan_data or {}).get("summary_workers") or plan_workers)
    synthesis_workers = list((plan_data or {}).get("synthesis_workers") or [])
    team_dir = os.path.join(task_dir, "team")
    required = bool(plan_ready and expected_workers)
    state = {
        "required": required,
        "team_dir": team_dir,
        "expected_workers": expected_workers,
        "synthesis_workers": synthesis_workers,
        "all_workers": plan_workers,
        "expected_count": len(expected_workers),
        "present_count": 0,
        "ready_count": 0,
        "ready": not required,
        "missing_workers": [],
        "errors": [],
        "artifacts": [],
        "latest_mtime": 0.0,
        "extra_files": [],
        "per_worker": {},
    }
    if not required:
        return state

    expected_basenames = {}
    owned_paths = dict((plan_data or {}).get("owned_paths") or {})
    for worker in list(expected_workers) + [w for w in synthesis_workers if w not in expected_workers]:
        relpath = team_worker_summary_relpath(worker)
        if relpath:
            expected_basenames[os.path.basename(relpath)] = worker
    for worker in expected_workers:
        relpath = team_worker_summary_relpath(worker)
        if relpath:
            state["artifacts"].append(relpath)
        abs_path = os.path.join(task_dir, relpath) if relpath else ""
        parsed = _team_worker_summary_parse(abs_path, worker, plan_data=plan_data)
        parsed["artifact"] = relpath
        parsed["planned_owned_paths"] = list(owned_paths.get(worker) or [])
        parsed["status"] = "ready"
        state["per_worker"][worker] = parsed
        state["latest_mtime"] = max(state["latest_mtime"], float(parsed.get("mtime") or 0.0))
        if parsed.get("exists"):
            state["present_count"] += 1
        else:
            parsed["status"] = "missing"
            state["missing_workers"].append(worker)
            continue
        if parsed.get("ready"):
            state["ready_count"] += 1
        else:
            parsed["status"] = "incomplete"
            reasons = []
            if parsed.get("missing_sections"):
                reasons.append("missing sections: " + ", ".join(parsed.get("missing_sections") or []))
            if parsed.get("has_placeholders"):
                reasons.append("remove TODO/TBD placeholders")
            reasons.extend(list(parsed.get("errors") or [])[:3])
            joined = "; ".join(reasons or ["fill required sections"])
            state["errors"].append(f"{worker} ({joined})")

    if os.path.isdir(team_dir):
        for entry in sorted(os.listdir(team_dir)):
            if not entry.startswith("worker-") or not entry.endswith(".md"):
                continue
            if entry not in expected_basenames:
                state["extra_files"].append(normalize_path(os.path.join("team", entry)))

    state["ready"] = not state["missing_workers"] and not state["errors"]
    return state

def _team_artifact_readiness(path_value, required_headings, artifact_kind="generic"):
    """Return structured readiness for TEAM_PLAN / TEAM_SYNTHESIS artifacts."""
    if not path_value or not os.path.isfile(path_value):
        return {
            "exists": False,
            "ready": False,
            "missing_sections": list(required_headings),
            "has_placeholders": True,
            "semantic_errors": [],
            "parsed": {},
            "mtime": 0.0,
        }

    try:
        with open(path_value, "r", encoding="utf-8") as fh:
            text_value = fh.read()
    except OSError:
        return {
            "exists": False,
            "ready": False,
            "missing_sections": list(required_headings),
            "has_placeholders": True,
            "semantic_errors": [],
            "parsed": {},
            "mtime": 0.0,
        }

    missing_sections = [
        heading for heading in required_headings if heading not in text_value
    ]
    normalized = text_value.lower()
    has_placeholders = any(marker.lower() in normalized for marker in TEAM_PLACEHOLDER_MARKERS)

    parsed = {}
    semantic_errors = []
    if artifact_kind == "plan":
        parsed = parse_team_plan(path_value)
        semantic_errors = list(parsed.get("errors") or [])

    try:
        mtime = os.path.getmtime(path_value)
    except OSError:
        mtime = 0.0

    return {
        "exists": True,
        "ready": not missing_sections and not has_placeholders and not semantic_errors,
        "missing_sections": missing_sections,
        "has_placeholders": has_placeholders,
        "semantic_errors": semantic_errors,
        "parsed": parsed,
        "mtime": mtime,
    }


def _team_owner_label(workers, fallback_label):
    owners = [str(item).strip() for item in (workers or []) if str(item).strip()]
    if owners:
        return ", ".join(owners[:3])
    return str(fallback_label or "")


def team_runtime_verification_status(task_dir, team_state=None):
    """Return freshness for the final runtime verification after synthesis.

    Team tasks often finish with a lead / integrator pass that writes
    TEAM_SYNTHESIS.md and then runs the final runtime verification. We treat the
    latest runtime critic artifact as stale when it predates the current
    TEAM_SYNTHESIS.md revision.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    runtime_verdict = (yaml_field("runtime_verdict", state_file) or "pending").upper()
    runtime_freshness = verdict_freshness(state_file, "runtime_verdict")
    mutates_repo = str(yaml_field("mutates_repo", state_file) or "false").strip().lower() in ("true", "1", "yes")

    artifact_candidates = []
    for relpath in ("CRITIC__runtime.md", "QA__runtime.md"):
        abs_path = os.path.join(task_dir, relpath)
        if not os.path.isfile(abs_path):
            continue
        try:
            artifact_candidates.append((os.path.getmtime(abs_path), relpath))
        except OSError:
            pass

    runtime_artifact_mtime = 0.0
    runtime_artifact = ""
    if artifact_candidates:
        runtime_artifact_mtime, runtime_artifact = max(artifact_candidates, key=lambda item: item[0])

    synthesis_ready = bool((team_state or {}).get("synthesis_ready"))
    synthesis_mtime = float((team_state or {}).get("synthesis_mtime") or 0.0)
    synthesis_workers = [
        str(item).strip()
        for item in ((team_state or {}).get("synthesis_workers") or [])
        if str(item).strip()
    ]

    active = bool(orch_mode == "team" and synthesis_ready and mutates_repo)
    runtime_artifact_exists = bool(runtime_artifact)
    stale_after_synthesis = bool(active and synthesis_mtime and runtime_artifact_exists and runtime_artifact_mtime < synthesis_mtime)
    missing_after_synthesis = bool(active and not runtime_artifact_exists)
    verification_needed = bool(
        active and (
            runtime_verdict != "PASS"
            or runtime_freshness != "current"
            or stale_after_synthesis
            or missing_after_synthesis
        )
    )
    verification_ready = bool(
        active
        and runtime_verdict == "PASS"
        and runtime_freshness == "current"
        and runtime_artifact_exists
        and not stale_after_synthesis
    )

    reason = ""
    if verification_needed:
        if missing_after_synthesis:
            reason = "record final runtime verification after TEAM_SYNTHESIS.md before close"
        elif stale_after_synthesis:
            reason = "rerun final runtime verification after the latest TEAM_SYNTHESIS.md update"
        elif runtime_verdict != "PASS":
            reason = "run final runtime verification after TEAM_SYNTHESIS.md before close"
        elif runtime_freshness != "current":
            reason = "rerun final runtime verification to refresh stale verdict freshness before close"
        else:
            reason = "refresh final runtime verification before close"

    owner_label = ", ".join(synthesis_workers[:3]) if synthesis_workers else "the synthesis owner"
    return {
        "active": active,
        "runtime_verdict": runtime_verdict,
        "runtime_verdict_freshness": runtime_freshness,
        "runtime_artifact": runtime_artifact,
        "runtime_artifact_exists": runtime_artifact_exists,
        "runtime_artifact_mtime": runtime_artifact_mtime,
        "verification_needed": verification_needed,
        "verification_ready": verification_ready,
        "verification_reason": reason,
        "verification_owners": synthesis_workers,
        "verification_owner_label": owner_label,
        "stale_after_synthesis": stale_after_synthesis,
        "missing_after_synthesis": missing_after_synthesis,
    }


def team_documentation_status(task_dir, team_state=None):
    """Return freshness for team documentation sync after final verification.

    Team repo-mutating tasks should refresh DOC_SYNC.md after the final runtime
    verification pass so close artifacts reflect the last verified state. When
    document review is required, critic-document should then re-run against the
    refreshed DOC_SYNC.md / final verification pair.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    mutates_repo = str(yaml_field("mutates_repo", state_file) or "false").strip().lower() in ("true", "1", "yes")
    document_verdict = (yaml_field("document_verdict", state_file) or "pending").upper()
    document_freshness = verdict_freshness(state_file, "document_verdict")

    synthesis_ready = bool((team_state or {}).get("synthesis_ready"))
    runtime_ready = bool((team_state or {}).get("runtime_verification_ready"))
    runtime_mtime = float((team_state or {}).get("runtime_verification_mtime") or 0.0)
    runtime_artifact = str((team_state or {}).get("runtime_verification_artifact") or "")
    doc_sync_workers = [
        str(item).strip()
        for item in ((team_state or {}).get("doc_sync_workers") or [])
        if str(item).strip()
    ]
    doc_sync_owner_source = str((team_state or {}).get("doc_sync_owner_source") or "")
    document_critic_workers = [
        str(item).strip()
        for item in ((team_state or {}).get("document_critic_workers") or [])
        if str(item).strip()
    ]
    document_critic_owner_source = str((team_state or {}).get("document_critic_owner_source") or "")

    active = bool(orch_mode == "team" and mutates_repo and synthesis_ready and runtime_ready)

    doc_sync_path = os.path.join(task_dir, "DOC_SYNC.md")
    doc_sync_exists = os.path.isfile(doc_sync_path)
    try:
        doc_sync_mtime = os.path.getmtime(doc_sync_path) if doc_sync_exists else 0.0
    except OSError:
        doc_sync_mtime = 0.0

    doc_sync_missing_after_verification = bool(active and not doc_sync_exists)
    doc_sync_stale_after_verification = bool(
        active and doc_sync_exists and runtime_mtime and doc_sync_mtime < runtime_mtime
    )
    doc_sync_needed = bool(
        active and (doc_sync_missing_after_verification or doc_sync_stale_after_verification)
    )

    document_critic_needed = bool(active and needs_document_critic(task_dir))
    document_artifact = "CRITIC__document.md"
    document_path = os.path.join(task_dir, document_artifact)
    document_exists = os.path.isfile(document_path)
    try:
        document_mtime = os.path.getmtime(document_path) if document_exists else 0.0
    except OSError:
        document_mtime = 0.0

    documentation_baseline_mtime = max(runtime_mtime, doc_sync_mtime)
    document_critic_missing_after_docs = bool(active and document_critic_needed and not document_exists)
    document_critic_stale_after_docs = bool(
        active
        and document_critic_needed
        and document_exists
        and documentation_baseline_mtime
        and document_mtime < documentation_baseline_mtime
    )
    document_critic_pending = bool(
        active and document_critic_needed and (
            document_verdict != "PASS" or document_freshness != "current"
        )
    )

    documentation_needed = bool(
        active and (
            doc_sync_needed
            or (
                document_critic_needed
                and (
                    document_critic_missing_after_docs
                    or document_critic_stale_after_docs
                    or document_critic_pending
                )
            )
        )
    )
    documentation_ready = bool(active and not documentation_needed)

    reason = ""
    if documentation_needed:
        if doc_sync_missing_after_verification:
            reason = "refresh DOC_SYNC.md after final team runtime verification"
        elif doc_sync_stale_after_verification:
            reason = "refresh DOC_SYNC.md after the latest final team runtime verification"
        elif document_critic_missing_after_docs:
            reason = "run critic-document after the latest DOC_SYNC.md / final team runtime verification"
        elif document_critic_stale_after_docs:
            reason = "rerun critic-document after the latest DOC_SYNC.md / final team runtime verification"
        elif document_critic_pending:
            if document_verdict == "PASS" and document_freshness != "current":
                reason = "rerun critic-document to refresh stale document verdict freshness before close"
            else:
                reason = "rerun critic-document after refreshing DOC_SYNC.md and final team runtime verification"
        else:
            reason = "refresh team documentation sync before close"

    doc_sync_owner_label = _team_owner_label(doc_sync_workers, "writer")
    document_critic_owner_label = _team_owner_label(document_critic_workers, "critic-document")
    owner_label = doc_sync_owner_label
    if document_critic_needed:
        owner_label = f"writer={doc_sync_owner_label}; critic-document={document_critic_owner_label}"

    return {
        "active": active,
        "documentation_needed": documentation_needed,
        "documentation_ready": documentation_ready,
        "documentation_reason": reason,
        "documentation_owner_label": owner_label,
        "doc_sync_workers": doc_sync_workers,
        "doc_sync_owner_label": doc_sync_owner_label,
        "doc_sync_owner_source": doc_sync_owner_source,
        "doc_sync_exists": doc_sync_exists,
        "doc_sync_mtime": doc_sync_mtime,
        "doc_sync_needed": doc_sync_needed,
        "doc_sync_missing_after_verification": doc_sync_missing_after_verification,
        "doc_sync_stale_after_verification": doc_sync_stale_after_verification,
        "doc_sync_artifact": "DOC_SYNC.md",
        "document_critic_workers": document_critic_workers,
        "document_critic_owner_label": document_critic_owner_label,
        "document_critic_owner_source": document_critic_owner_source,
        "document_critic_needed": document_critic_needed,
        "document_critic_pending": document_critic_pending,
        "document_critic_exists": document_exists,
        "document_critic_mtime": document_mtime,
        "document_critic_missing_after_docs": document_critic_missing_after_docs,
        "document_critic_stale_after_docs": document_critic_stale_after_docs,
        "document_critic_artifact": document_artifact,
        "document_verdict": document_verdict,
        "document_verdict_freshness": document_freshness,
        "runtime_artifact": runtime_artifact,
    }


def team_handoff_status(task_dir, team_state=None):
    """Return HANDOFF freshness relative to the latest team artifact updates."""
    handoff_path = os.path.join(task_dir, "HANDOFF.md")
    exists = os.path.isfile(handoff_path)
    try:
        handoff_mtime = os.path.getmtime(handoff_path) if exists else 0.0
    except OSError:
        handoff_mtime = 0.0

    latest_candidates = []
    plan_mtime = float((team_state or {}).get("plan_mtime") or 0.0)
    if plan_mtime:
        latest_candidates.append((plan_mtime, "TEAM_PLAN.md"))
    worker_mtime = float((team_state or {}).get("worker_summary_latest_mtime") or 0.0)
    if worker_mtime:
        latest_candidates.append((worker_mtime, "team/worker-<name>.md"))
    synthesis_mtime = float((team_state or {}).get("synthesis_mtime") or 0.0)
    if synthesis_mtime:
        latest_candidates.append((synthesis_mtime, "TEAM_SYNTHESIS.md"))
    runtime_mtime = float((team_state or {}).get("runtime_verification_mtime") or 0.0)
    runtime_artifact = str((team_state or {}).get("runtime_verification_artifact") or "")
    if runtime_mtime:
        latest_candidates.append((runtime_mtime, runtime_artifact or "CRITIC__runtime.md"))
    doc_sync_mtime = float((team_state or {}).get("documentation_sync_mtime") or 0.0)
    if doc_sync_mtime:
        latest_candidates.append((doc_sync_mtime, "DOC_SYNC.md"))
    document_mtime = float((team_state or {}).get("document_critic_mtime") or 0.0)
    if document_mtime:
        latest_candidates.append((document_mtime, "CRITIC__document.md"))

    latest_team_mtime = 0.0
    latest_team_artifact = ""
    if latest_candidates:
        latest_team_mtime, latest_team_artifact = max(latest_candidates, key=lambda item: item[0])

    refresh_needed = bool(exists and latest_team_mtime and latest_team_mtime > handoff_mtime)
    refresh_reason = ""
    if refresh_needed:
        if latest_team_artifact == "TEAM_SYNTHESIS.md":
            refresh_reason = "refresh HANDOFF.md after TEAM_SYNTHESIS.md"
        elif latest_team_artifact.startswith("team/"):
            refresh_reason = "refresh HANDOFF.md after the latest worker summary update"
        elif latest_team_artifact == "TEAM_PLAN.md":
            refresh_reason = "refresh HANDOFF.md after TEAM_PLAN.md"
        elif latest_team_artifact in ("CRITIC__runtime.md", "QA__runtime.md"):
            refresh_reason = "refresh HANDOFF.md after final team runtime verification"
        elif latest_team_artifact == "DOC_SYNC.md":
            refresh_reason = "refresh HANDOFF.md after DOC_SYNC.md"
        elif latest_team_artifact == "CRITIC__document.md":
            refresh_reason = "refresh HANDOFF.md after critic-document"
        else:
            refresh_reason = "refresh HANDOFF.md after the latest team artifact update"

    return {
        "handoff_exists": exists,
        "handoff_mtime": handoff_mtime,
        "handoff_stub": is_handoff_stub(handoff_path) if exists else True,
        "handoff_refresh_needed": refresh_needed,
        "handoff_refresh_reason": refresh_reason,
        "latest_team_mtime": latest_team_mtime,
        "latest_team_artifact": latest_team_artifact,
    }


def _team_artifact_skip_state(task_dir, orch_mode, current_status, fallback_used):
    """Return a lightweight default team artifact payload for non-team modes."""
    return {
        "orchestration_mode": str(orch_mode or "solo"),
        "current_status": str(current_status or "n/a"),
        "derived_status": str(current_status or "n/a"),
        "fallback_used": str(fallback_used or "none"),
        "plan_path": os.path.join(task_dir, "TEAM_PLAN.md"),
        "plan_exists": False,
        "plan_ready": False,
        "plan_missing_sections": list(TEAM_PLAN_REQUIRED_HEADINGS),
        "plan_has_placeholders": True,
        "plan_semantic_errors": [],
        "plan_workers": [],
        "plan_owned_paths": {},
        "plan_forbidden_paths": {},
        "plan_shared_read_only_paths": [],
        "plan_synthesis_workers": [],
        "plan_summary_workers": [],
        "plan_synthesis_role_hint": "",
        "plan_doc_sync_workers": [],
        "plan_doc_sync_owner_source": "",
        "plan_document_critic_workers": [],
        "plan_document_critic_owner_source": "",
        "plan_owned_path_count": 0,
        "plan_ownership_ready": False,
        "plan_mtime": 0.0,
        "worker_summary_dir": os.path.join(task_dir, "team"),
        "worker_summary_required": False,
        "worker_summary_ready": True,
        "worker_summary_expected_workers": [],
        "worker_summary_synthesis_workers": [],
        "worker_summary_expected_count": 0,
        "worker_summary_present_count": 0,
        "worker_summary_ready_count": 0,
        "worker_summary_missing_workers": [],
        "worker_summary_errors": [],
        "worker_summary_artifacts": [],
        "worker_summary_latest_mtime": 0.0,
        "worker_summary_extra_files": [],
        "worker_summary_per_worker": {},
        "summary_workers": [],
        "synthesis_workers": [],
        "synthesis_path": os.path.join(task_dir, "TEAM_SYNTHESIS.md"),
        "synthesis_exists": False,
        "synthesis_ready": False,
        "synthesis_missing_sections": list(TEAM_SYNTHESIS_REQUIRED_HEADINGS),
        "synthesis_has_placeholders": True,
        "synthesis_semantic_errors": [],
        "synthesis_refreshed_after_degraded": False,
        "synthesis_mtime": 0.0,
        "team_runtime_verification_active": False,
        "team_runtime_verification_needed": False,
        "team_runtime_verification_ready": False,
        "team_runtime_verification_reason": "",
        "team_runtime_verification_owners": [],
        "team_runtime_verification_owner_label": "",
        "team_runtime_artifact": "",
        "team_runtime_artifact_exists": False,
        "team_runtime_mtime": 0.0,
        "team_runtime_stale_after_synthesis": False,
        "team_documentation_active": False,
        "team_documentation_needed": False,
        "team_documentation_ready": False,
        "team_documentation_reason": "",
        "team_documentation_owner_label": "",
        "team_doc_sync_exists": False,
        "team_doc_sync_mtime": 0.0,
        "team_doc_sync_needed": False,
        "team_doc_sync_owners": [],
        "team_doc_sync_owner_label": "",
        "team_doc_sync_owner_source": "",
        "team_doc_sync_missing_after_verification": False,
        "team_doc_sync_stale_after_verification": False,
        "team_doc_sync_artifact": "DOC_SYNC.md",
        "team_document_critic_needed": False,
        "team_document_critic_pending": False,
        "team_document_critic_exists": False,
        "team_document_critic_mtime": 0.0,
        "team_document_critic_owners": [],
        "team_document_critic_owner_label": "",
        "team_document_critic_owner_source": "",
        "team_document_critic_missing_after_docs": False,
        "team_document_critic_stale_after_docs": False,
        "team_document_critic_artifact": "CRITIC__document.md",
        "team_document_verdict": "pending",
        "handoff_exists": False,
        "handoff_stub": True,
        "handoff_mtime": 0.0,
        "handoff_refresh_needed": False,
        "handoff_refresh_reason": "",
        "latest_team_artifact": "",
        "latest_team_mtime": 0.0,
    }


def team_artifact_status(task_dir):
    """Return readiness + derived status for team artifacts in a task dir."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    current_status = yaml_field("team_status", state_file) or "n/a"
    fallback_used = yaml_field("fallback_used", state_file) or "none"

    if orch_mode != "team":
        return _team_artifact_skip_state(task_dir, orch_mode, current_status, fallback_used)

    plan_path = os.path.join(task_dir, "TEAM_PLAN.md")
    synthesis_path = os.path.join(task_dir, "TEAM_SYNTHESIS.md")
    plan_state = _team_artifact_readiness(plan_path, TEAM_PLAN_REQUIRED_HEADINGS, artifact_kind="plan")
    parsed_plan = plan_state.get("parsed") or {}
    worker_state = team_worker_summary_status(
        task_dir,
        parsed_plan,
        plan_ready=bool(plan_state.get("ready")),
    )
    synthesis_state = _team_artifact_readiness(
        synthesis_path, TEAM_SYNTHESIS_REQUIRED_HEADINGS
    )

    synthesis_semantic_errors = list(synthesis_state.get("semantic_errors") or [])
    if worker_state.get("required"):
        missing_workers = list(worker_state.get("missing_workers") or [])
        if missing_workers:
            synthesis_semantic_errors.append(
                "missing worker summaries: " + ", ".join(missing_workers[:6])
            )
        worker_errors = list(worker_state.get("errors") or [])
        if worker_errors:
            synthesis_semantic_errors.append(
                "incomplete worker summaries: " + " | ".join(worker_errors[:3])
            )
        if (
            synthesis_state.get("exists")
            and worker_state.get("ready")
            and float(worker_state.get("latest_mtime") or 0.0) > float(synthesis_state.get("mtime") or 0.0)
        ):
            synthesis_semantic_errors.append(
                "refresh TEAM_SYNTHESIS.md after the latest worker summary update"
            )

    synthesis_ready = bool(
        synthesis_state.get("exists")
        and not synthesis_state.get("missing_sections")
        and not synthesis_state.get("has_placeholders")
        and not synthesis_semantic_errors
    )

    try:
        state_mtime = os.path.getmtime(state_file)
    except OSError:
        state_mtime = 0.0

    synthesis_refreshed_after_degraded = bool(
        synthesis_ready and synthesis_state["mtime"] > state_mtime
    )

    if orch_mode != "team":
        derived_status = current_status
    elif fallback_used != "none":
        derived_status = "fallback"
    elif current_status == "degraded" and not synthesis_refreshed_after_degraded:
        derived_status = "degraded"
    elif synthesis_ready:
        derived_status = "complete"
    elif plan_state["ready"]:
        derived_status = "running"
    else:
        derived_status = "planned"

    verification_state = team_runtime_verification_status(
        task_dir,
        {
            "orchestration_mode": orch_mode,
            "plan_ready": bool(plan_state.get("ready")),
            "synthesis_ready": bool(synthesis_ready),
            "synthesis_mtime": float(synthesis_state.get("mtime") or 0.0),
            "synthesis_workers": list(parsed_plan.get("synthesis_workers") or []),
        },
    )

    documentation_state = team_documentation_status(
        task_dir,
        {
            "synthesis_ready": bool(synthesis_ready),
            "runtime_verification_ready": bool(verification_state.get("verification_ready")),
            "runtime_verification_mtime": float(verification_state.get("runtime_artifact_mtime") or 0.0),
            "runtime_verification_artifact": str(verification_state.get("runtime_artifact") or ""),
            "doc_sync_workers": list(parsed_plan.get("doc_sync_workers") or []),
            "doc_sync_owner_source": str(parsed_plan.get("doc_sync_owner_source") or ""),
            "document_critic_workers": list(parsed_plan.get("document_critic_workers") or []),
            "document_critic_owner_source": str(parsed_plan.get("document_critic_owner_source") or ""),
        },
    )

    handoff_state = team_handoff_status(
        task_dir,
        {
            "plan_mtime": float(plan_state.get("mtime") or 0.0),
            "worker_summary_latest_mtime": float(worker_state.get("latest_mtime") or 0.0),
            "synthesis_mtime": float(synthesis_state.get("mtime") or 0.0),
            "runtime_verification_mtime": float(verification_state.get("runtime_artifact_mtime") or 0.0) if verification_state.get("active") else 0.0,
            "runtime_verification_artifact": str(verification_state.get("runtime_artifact") or "") if verification_state.get("active") else "",
            "documentation_sync_mtime": float(documentation_state.get("doc_sync_mtime") or 0.0) if documentation_state.get("active") else 0.0,
            "document_critic_mtime": float(documentation_state.get("document_critic_mtime") or 0.0) if documentation_state.get("active") else 0.0,
        },
    )

    return {
        "orchestration_mode": orch_mode,
        "current_status": current_status,
        "derived_status": derived_status,
        "fallback_used": fallback_used,
        "plan_path": plan_path,
        "plan_exists": plan_state["exists"],
        "plan_ready": plan_state["ready"],
        "plan_missing_sections": plan_state["missing_sections"],
        "plan_has_placeholders": plan_state["has_placeholders"],
        "plan_semantic_errors": list(plan_state.get("semantic_errors") or []),
        "plan_workers": list(parsed_plan.get("workers") or []),
        "plan_worker_roles": dict(parsed_plan.get("worker_roles") or {}),
        "plan_owned_paths": dict(parsed_plan.get("owned_paths") or {}),
        "plan_forbidden_paths": dict(parsed_plan.get("forbidden_paths") or {}),
        "plan_shared_read_only_paths": list(parsed_plan.get("shared_read_only_paths") or []),
        "plan_synthesis_workers": list(parsed_plan.get("synthesis_workers") or []),
        "plan_summary_workers": list(parsed_plan.get("summary_workers") or []),
        "plan_synthesis_role_hint": str(parsed_plan.get("synthesis_role_hint") or ""),
        "plan_doc_sync_workers": list(parsed_plan.get("doc_sync_workers") or []),
        "plan_doc_sync_owner_source": str(parsed_plan.get("doc_sync_owner_source") or ""),
        "plan_document_critic_workers": list(parsed_plan.get("document_critic_workers") or []),
        "plan_document_critic_owner_source": str(parsed_plan.get("document_critic_owner_source") or ""),
        "plan_owned_path_count": int(parsed_plan.get("owned_path_count") or 0),
        "plan_ownership_ready": bool(parsed_plan.get("ownership_ready")),
        "plan_mtime": float(plan_state.get("mtime") or 0.0),
        "worker_summary_dir": os.path.join(task_dir, "team"),
        "worker_summary_required": bool(worker_state.get("required")),
        "worker_summary_ready": bool(worker_state.get("ready")),
        "worker_summary_expected_workers": list(worker_state.get("expected_workers") or []),
        "worker_summary_synthesis_workers": list(worker_state.get("synthesis_workers") or []),
        "worker_summary_expected_count": int(worker_state.get("expected_count") or 0),
        "worker_summary_present_count": int(worker_state.get("present_count") or 0),
        "worker_summary_ready_count": int(worker_state.get("ready_count") or 0),
        "worker_summary_missing_workers": list(worker_state.get("missing_workers") or []),
        "worker_summary_errors": list(worker_state.get("errors") or []),
        "worker_summary_artifacts": list(worker_state.get("artifacts") or []),
        "worker_summary_latest_mtime": float(worker_state.get("latest_mtime") or 0.0),
        "worker_summary_extra_files": list(worker_state.get("extra_files") or []),
        "worker_summary_per_worker": dict(worker_state.get("per_worker") or {}),
        "summary_workers": list(parsed_plan.get("summary_workers") or []),
        "synthesis_workers": list(parsed_plan.get("synthesis_workers") or []),
        "synthesis_path": synthesis_path,
        "synthesis_exists": synthesis_state["exists"],
        "synthesis_ready": synthesis_ready,
        "synthesis_missing_sections": synthesis_state["missing_sections"],
        "synthesis_has_placeholders": synthesis_state["has_placeholders"],
        "synthesis_semantic_errors": synthesis_semantic_errors,
        "synthesis_refreshed_after_degraded": synthesis_refreshed_after_degraded,
        "synthesis_mtime": float(synthesis_state.get("mtime") or 0.0),
        "team_runtime_verification_active": bool(verification_state.get("active")),
        "team_runtime_verification_needed": bool(verification_state.get("verification_needed")),
        "team_runtime_verification_ready": bool(verification_state.get("verification_ready")),
        "team_runtime_verification_reason": str(verification_state.get("verification_reason") or ""),
        "team_runtime_verification_owners": list(verification_state.get("verification_owners") or []),
        "team_runtime_verification_owner_label": str(verification_state.get("verification_owner_label") or ""),
        "team_runtime_artifact": str(verification_state.get("runtime_artifact") or ""),
        "team_runtime_artifact_exists": bool(verification_state.get("runtime_artifact_exists")),
        "team_runtime_mtime": float(verification_state.get("runtime_artifact_mtime") or 0.0),
        "team_runtime_stale_after_synthesis": bool(verification_state.get("stale_after_synthesis")),
        "team_documentation_active": bool(documentation_state.get("active")),
        "team_documentation_needed": bool(documentation_state.get("documentation_needed")),
        "team_documentation_ready": bool(documentation_state.get("documentation_ready")),
        "team_documentation_reason": str(documentation_state.get("documentation_reason") or ""),
        "team_documentation_owner_label": str(documentation_state.get("documentation_owner_label") or ""),
        "team_doc_sync_exists": bool(documentation_state.get("doc_sync_exists")),
        "team_doc_sync_mtime": float(documentation_state.get("doc_sync_mtime") or 0.0),
        "team_doc_sync_needed": bool(documentation_state.get("doc_sync_needed")),
        "team_doc_sync_owners": list(documentation_state.get("doc_sync_workers") or []),
        "team_doc_sync_owner_label": str(documentation_state.get("doc_sync_owner_label") or ""),
        "team_doc_sync_owner_source": str(documentation_state.get("doc_sync_owner_source") or ""),
        "team_doc_sync_missing_after_verification": bool(documentation_state.get("doc_sync_missing_after_verification")),
        "team_doc_sync_stale_after_verification": bool(documentation_state.get("doc_sync_stale_after_verification")),
        "team_doc_sync_artifact": str(documentation_state.get("doc_sync_artifact") or "DOC_SYNC.md"),
        "team_document_critic_needed": bool(documentation_state.get("document_critic_needed")),
        "team_document_critic_pending": bool(documentation_state.get("document_critic_pending")),
        "team_document_critic_exists": bool(documentation_state.get("document_critic_exists")),
        "team_document_critic_mtime": float(documentation_state.get("document_critic_mtime") or 0.0),
        "team_document_critic_owners": list(documentation_state.get("document_critic_workers") or []),
        "team_document_critic_owner_label": str(documentation_state.get("document_critic_owner_label") or ""),
        "team_document_critic_owner_source": str(documentation_state.get("document_critic_owner_source") or ""),
        "team_document_critic_missing_after_docs": bool(documentation_state.get("document_critic_missing_after_docs")),
        "team_document_critic_stale_after_docs": bool(documentation_state.get("document_critic_stale_after_docs")),
        "team_document_critic_artifact": str(documentation_state.get("document_critic_artifact") or "CRITIC__document.md"),
        "team_document_verdict": str(documentation_state.get("document_verdict") or "pending"),
        "handoff_exists": bool(handoff_state.get("handoff_exists")),
        "handoff_stub": bool(handoff_state.get("handoff_stub")),
        "handoff_mtime": float(handoff_state.get("handoff_mtime") or 0.0),
        "handoff_refresh_needed": bool(handoff_state.get("handoff_refresh_needed")),
        "handoff_refresh_reason": str(handoff_state.get("handoff_refresh_reason") or ""),
        "latest_team_artifact": str(handoff_state.get("latest_team_artifact") or ""),
        "latest_team_mtime": float(handoff_state.get("latest_team_mtime") or 0.0),
    }

def sync_team_status(task_dir):
    """Synchronize TASK_STATE.yaml team_status from artifact readiness."""
    artifact_state = team_artifact_status(task_dir)
    if artifact_state.get("orchestration_mode") != "team":
        return artifact_state
    derived_status = artifact_state.get("derived_status")
    current_status = artifact_state.get("current_status")
    if derived_status and derived_status != current_status:
        set_task_state_field(task_dir, "team_status", derived_status)
        artifact_state["current_status"] = derived_status
    return artifact_state


def ensure_team_artifacts(task_dir, routing=None):
    """Create TEAM_PLAN / TEAM_SYNTHESIS scaffolds for team tasks if missing."""
    if routing is not None:
        orch_mode = str(routing.get("orchestration_mode") or "solo")
    else:
        state_file = os.path.join(task_dir, "TASK_STATE.yaml")
        orch_mode = yaml_field("orchestration_mode", state_file) or "solo"
    if orch_mode != "team":
        return []

    created = []

    team_dir = os.path.join(task_dir, "team")
    if not os.path.isdir(team_dir):
        os.makedirs(team_dir, exist_ok=True)

    plan_path = os.path.join(task_dir, "TEAM_PLAN.md")
    if not os.path.isfile(plan_path):
        with open(plan_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Plan\n\n"
                "## Worker Roster\n"
                "- TODO: assign each worker, scope, and handoff order\n\n"
                "## Owned Writable Paths\n"
                "- TBD\n\n"
                "## Shared Read-Only Paths\n"
                "- TBD\n\n"
                "## Forbidden Writes\n"
                "- TBD\n\n"
                "## Synthesis Strategy\n"
                "- TODO: describe merge + verification flow\n\n"
                "## Documentation Ownership (optional)\n"
                "Use bullet assignments like `- writer: worker-b` and `- critic-document: worker-c` only when "
                "docs / document review should be limited to specific workers. "
                "Values must be worker names listed in ## Worker Roster.\n"
            )
        created.append(plan_path)

    synthesis_path = os.path.join(task_dir, "TEAM_SYNTHESIS.md")
    if not os.path.isfile(synthesis_path):
        with open(synthesis_path, "w", encoding="utf-8") as fh:
            fh.write(
                "# Team Synthesis\n\n"
                "## Integrated Result\n"
                "- TODO: summarize the merged worker output\n\n"
                "## Cross-Checks\n"
                "- TBD\n\n"
                "## Verification Summary\n"
                "- TODO: list verification commands + outcomes\n\n"
                "## Residual Risks\n"
                "- TODO: record remaining risk or write `none`\n"
            )
        created.append(synthesis_path)

    return created


def team_bootstrap_signature(task_dir, team_state=None, provider=None):
    """Return a stable signature for the current team bootstrap inputs."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    if provider is None:
        provider = yaml_field("team_provider", state_file) or "none"

    payload = {
        "schema_version": TEAM_BOOTSTRAP_SCHEMA_VERSION,
        "provider": str(provider or "none"),
        "orchestration_mode": str(team_state.get("orchestration_mode") or "solo"),
        "team_status": str(team_state.get("derived_status") or team_state.get("current_status") or "n/a"),
        "plan_ready": bool(team_state.get("plan_ready")),
        "plan_ownership_ready": bool(team_state.get("plan_ownership_ready")),
        "workers": list(team_state.get("plan_workers") or []),
        "owned_paths": dict(team_state.get("plan_owned_paths") or {}),
        "forbidden_paths": dict(team_state.get("plan_forbidden_paths") or {}),
        "shared_read_only_paths": list(team_state.get("plan_shared_read_only_paths") or []),
        "worker_roles": dict(team_state.get("plan_worker_roles") or {}),
        "summary_workers": list(team_state.get("summary_workers") or []),
        "synthesis_workers": list(team_state.get("synthesis_workers") or []),
        "runtime_workers": list(team_state.get("team_runtime_verification_owners") or []),
        "doc_sync_workers": list(team_state.get("team_doc_sync_owners") or []),
        "document_critic_workers": list(team_state.get("team_document_critic_owners") or []),
        "runtime_artifact": str(team_state.get("team_runtime_artifact") or "CRITIC__runtime.md"),
        "doc_sync_artifact": str(team_state.get("team_doc_sync_artifact") or "DOC_SYNC.md"),
        "document_critic_artifact": str(team_state.get("team_document_critic_artifact") or "CRITIC__document.md"),
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()


def _team_bootstrap_phase_roles(team_state, worker_name):
    """Return ordered phase-role names for a worker in the bootstrap pack."""
    roles = ["developer"]
    runtime_workers = [str(x) for x in (team_state.get("team_runtime_verification_owners") or []) if str(x).strip()]
    doc_sync_workers = [str(x) for x in (team_state.get("team_doc_sync_owners") or []) if str(x).strip()]
    document_critic_workers = [str(x) for x in (team_state.get("team_document_critic_owners") or []) if str(x).strip()]
    if worker_name in runtime_workers and "critic-runtime" not in roles:
        roles.append("critic-runtime")
    if worker_name in doc_sync_workers and "writer" not in roles:
        roles.append("writer")
    if worker_name in document_critic_workers and "critic-document" not in roles:
        roles.append("critic-document")
    return roles


def _team_bootstrap_expected_relpaths(task_dir, team_state=None):
    """Return expected task-relative bootstrap relpaths without reading context."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    relpaths = [normalize_path(os.path.join("team", "bootstrap", "index.json"))]
    workers = [str(x) for x in (team_state.get("plan_workers") or []) if str(x).strip()]
    for worker_name in workers:
        relpaths.append(normalize_path(os.path.join("team", "bootstrap", f"{worker_name}.md")))
        for role_name in _team_bootstrap_phase_roles(team_state, worker_name):
            relpaths.append(normalize_path(os.path.join("team", "bootstrap", f"{worker_name}.{role_name}.env")))
    seen = []
    for relpath in relpaths:
        if relpath and relpath not in seen:
            seen.append(relpath)
    return seen


def team_bootstrap_status(task_dir, team_state=None):
    """Return freshness + completeness state for team/bootstrap artifacts."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    provider = yaml_field("team_provider", state_file) or "none"
    task_root = os.path.join(TASK_DIR, task_id)
    rel_dir = normalize_path(os.path.join("team", "bootstrap"))
    rel_index = normalize_path(os.path.join(rel_dir, "index.json"))
    status = {
        "task_id": task_id,
        "provider": str(provider or "none"),
        "available": False,
        "generated": False,
        "stale": False,
        "refresh_needed": False,
        "reason": "",
        "bootstrap_dir": rel_dir,
        "bootstrap_index": rel_index,
        "current_signature": "",
        "generated_signature": "",
        "generated_at": "",
        "expected_files": [],
        "missing_files": [],
        "refresh_command": f"python3 plugin/scripts/hctl.py team-bootstrap --task-dir {task_root} --write-files",
    }

    if team_state.get("orchestration_mode") != "team":
        status["reason"] = "orchestration_mode is not team"
        return status
    if not team_state.get("plan_ready"):
        status["reason"] = "TEAM_PLAN.md is not ready"
        return status
    if not team_state.get("plan_ownership_ready"):
        status["reason"] = "TEAM_PLAN.md ownership semantics are incomplete"
        return status
    workers = [str(x) for x in (team_state.get("plan_workers") or []) if str(x).strip()]
    if not workers:
        status["reason"] = "no workers declared in TEAM_PLAN.md"
        return status

    status["available"] = True
    status["expected_files"] = _team_bootstrap_expected_relpaths(task_dir, team_state=team_state)
    status["current_signature"] = team_bootstrap_signature(task_dir, team_state=team_state, provider=provider)

    index_abs = os.path.join(task_dir, rel_index)
    if not os.path.isfile(index_abs):
        status["reason"] = "team/bootstrap has not been generated yet"
        return status
    status["generated"] = True

    try:
        with open(index_abs, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, json.JSONDecodeError):
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "team/bootstrap/index.json is unreadable"
        return status

    status["generated_signature"] = str(parsed.get("bootstrap_signature") or "")
    status["generated_at"] = str(parsed.get("generated_at") or "")

    expected_files = status["expected_files"] or [
        normalize_path(str(path or "").replace(f"{task_root}/", "", 1))
        for path in (parsed.get("expected_files") or [])
        if str(path or "").strip()
    ]
    if expected_files:
        status["expected_files"] = expected_files

    missing = []
    for relpath in status["expected_files"]:
        if relpath and not os.path.isfile(os.path.join(task_dir, relpath)):
            missing.append(relpath)
    status["missing_files"] = missing
    if missing:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = f"bootstrap files missing: {', '.join(missing[:4])}"
        return status

    generated_provider = str(parsed.get("provider") or "none")
    if generated_provider != str(provider or "none"):
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = f"bootstrap provider changed ({generated_provider} → {provider})"
        return status

    if not status["generated_signature"]:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "bootstrap signature is missing"
        return status

    if status["generated_signature"] != status["current_signature"]:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "TEAM_PLAN.md or team ownership changed since bootstrap generation"
        return status

    status["reason"] = "current"
    return status


def build_team_bootstrap(task_dir, write_files=False):
    """Return provider-agnostic per-worker bootstrap specs for team tasks.

    The output is designed to help the lead spawn or resume workers without
    hand-copying identity/env details. When ``write_files`` is true, the
    function materializes task-local bootstrap artifacts under ``team/bootstrap``:

      - ``index.json`` — machine-readable bootstrap manifest
      - ``<worker>.md`` — human/LLM-readable worker brief
      - ``<worker>.<role>.env`` — sourceable env snippets for each role phase

    The env snippets stay provider-agnostic on purpose: they only export the
    task id, worker id, and recommended ``CLAUDE_AGENT_NAME`` for that phase.
    """

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    team_state = team_artifact_status(task_dir)
    provider = yaml_field("team_provider", state_file) or "none"
    task_root = os.path.join(TASK_DIR, task_id)
    bootstrap_signature_value = team_bootstrap_signature(task_dir, team_state=team_state, provider=provider)

    def _task_rel(name):
        return f"{task_root}/{normalize_path(name)}"

    def _env_lines(worker_name, agent_name):
        return [
            f"export HARNESS_TASK_ID='{task_id}'",
            f"export HARNESS_TEAM_WORKER='{worker_name}'",
            f"export CLAUDE_AGENT_NAME='{agent_name}'",
        ]

    def _context_command(worker_name, agent_name):
        return (
            "python3 plugin/scripts/hctl.py context "
            f"--task-dir {task_root} --json --team-worker {worker_name} --agent-name {agent_name}"
        )

    def _worker_brief_lines(spec):
        worker_name = spec["worker"]
        phases = spec.get("phases") or []
        role_scope = spec.get("role_scope") or "worker"
        lines = [
            f"# Team Worker Bootstrap — {worker_name}",
            "",
            "## Task",
            f"- task_id: {task_id}",
            f"- provider: {provider}",
            f"- team_status: {team_state.get('derived_status') or team_state.get('current_status') or 'n/a'}",
            f"- refresh command: {spec.get('refresh_command') or ''}",
            "",
            "## Identity",
            f"- worker: {worker_name}",
            f"- scope: {role_scope}",
            f"- default agent session: {spec.get('default_agent_name') or ''}",
            f"- default env: {spec.get('default_env_file') or ''}",
            f"- default context command: {spec.get('default_context_command') or ''}",
            "",
            "## Must Read",
        ]
        must_read = list(spec.get("must_read") or [])
        if must_read:
            lines.extend([f"- {item}" for item in must_read[:6]])
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Owned Writable Paths",
        ])
        owned_paths = list(spec.get("owned_paths") or [])
        if owned_paths:
            lines.extend([f"- {item}" for item in owned_paths])
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Forbidden Writes",
        ])
        forbidden_paths = list(spec.get("forbidden_paths") or [])
        if forbidden_paths:
            lines.extend([f"- {item}" for item in forbidden_paths])
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Shared Read-Only Paths",
        ])
        shared_paths = list(spec.get("shared_read_only_paths") or [])
        if shared_paths:
            lines.extend([f"- {item}" for item in shared_paths])
        else:
            lines.append("- none")

        lines.extend([
            "",
            "## Deliverables",
            f"- worker summary: {spec.get('summary_artifact') or 'none'}",
        ])
        extra_artifacts = []
        for phase in phases:
            artifact = str(phase.get("artifact") or "").strip()
            if artifact and artifact != spec.get("summary_artifact"):
                extra_artifacts.append(f"- {phase.get('phase')}: {artifact}")
        if extra_artifacts:
            lines.extend(extra_artifacts)

        lines.extend([
            "",
            "## Recommended First Action",
            f"- {spec.get('next_action') or 'Read TEAM_PLAN.md, stay inside owned paths, verify, then update your worker summary.'}",
            "",
            "## Role Phases",
        ])
        if phases:
            for phase in phases:
                lines.append(
                    f"- {phase.get('phase')}: agent={phase.get('agent_name')} env={phase.get('env_file')} artifact={phase.get('artifact')}"
                )
                lines.append(f"  context command: {phase.get('context_command')}")
        else:
            lines.append("- none")
        return lines

    bootstrap = {
        "task_id": task_id,
        "task_dir": task_dir,
        "provider": provider,
        "orchestration_mode": str(team_state.get("orchestration_mode") or "solo"),
        "team_status": str(team_state.get("derived_status") or team_state.get("current_status") or "n/a"),
        "ready": False,
        "reason": "",
        "bootstrap_dir": _task_rel(os.path.join("team", "bootstrap")),
        "bootstrap_index": _task_rel(os.path.join("team", "bootstrap", "index.json")),
        "refresh_command": f"python3 plugin/scripts/hctl.py team-bootstrap --task-dir {task_root} --write-files",
        "bootstrap_signature": bootstrap_signature_value,
        "workers": [],
        "expected_files": [],
        "generated_files": [],
    }

    if team_state.get("orchestration_mode") != "team":
        bootstrap["reason"] = "orchestration_mode is not team"
        return bootstrap
    if not team_state.get("plan_ready"):
        bootstrap["reason"] = "TEAM_PLAN.md is not ready"
        return bootstrap
    if not team_state.get("plan_ownership_ready"):
        bootstrap["reason"] = "TEAM_PLAN.md ownership semantics are incomplete"
        return bootstrap

    workers = list(team_state.get("plan_workers") or [])
    if not workers:
        bootstrap["reason"] = "no workers declared in TEAM_PLAN.md"
        return bootstrap

    bootstrap["ready"] = True
    bootstrap["reason"] = "ready"

    shared_paths = list(team_state.get("plan_shared_read_only_paths") or [])
    owned_paths_map = dict(team_state.get("plan_owned_paths") or {})
    forbidden_paths_map = dict(team_state.get("plan_forbidden_paths") or {})
    synthesis_workers = list(team_state.get("synthesis_workers") or [])
    runtime_workers = list(team_state.get("team_runtime_verification_owners") or synthesis_workers)
    doc_sync_workers = list(team_state.get("team_doc_sync_owners") or [])
    document_critic_workers = list(team_state.get("team_document_critic_owners") or [])
    worker_roles = dict(team_state.get("plan_worker_roles") or {})

    def _phase_spec(worker_name, role_name, phase_name, artifact_name, context):
        env_rel = normalize_path(os.path.join("team", "bootstrap", f"{worker_name}.{role_name}.env"))
        agent_name = f"harness:{role_name}:{worker_name}"
        return {
            "phase": phase_name,
            "role": role_name,
            "agent_name": agent_name,
            "env_file": _task_rel(env_rel),
            "artifact": _task_rel(artifact_name) if artifact_name else "",
            "must_read": list(context.get("must_read") or [])[:6],
            "next_action": str(context.get("next_action") or ""),
            "notes": list(context.get("notes") or [])[:4],
            "env_lines": _env_lines(worker_name, agent_name),
            "env_relpath": env_rel,
            "context_command": _context_command(worker_name, agent_name),
        }

    for worker_name in workers:
        developer_context = emit_compact_context(
            task_dir,
            raw_agent_name=f"harness:developer:{worker_name}",
            explicit_worker=worker_name,
        )
        phases = [
            _phase_spec(
                worker_name,
                "developer",
                "implement",
                team_worker_summary_relpath(worker_name),
                developer_context,
            )
        ]
        if worker_name in synthesis_workers:
            phases.append(
                _phase_spec(
                    worker_name,
                    "developer",
                    "synthesis",
                    "TEAM_SYNTHESIS.md",
                    developer_context,
                )
            )
        if worker_name in runtime_workers:
            runtime_context = emit_compact_context(
                task_dir,
                raw_agent_name=f"harness:critic-runtime:{worker_name}",
                explicit_worker=worker_name,
            )
            phases.append(
                _phase_spec(
                    worker_name,
                    "critic-runtime",
                    "final_runtime_verification",
                    team_state.get("team_runtime_artifact") or "CRITIC__runtime.md",
                    runtime_context,
                )
            )
        if worker_name in doc_sync_workers:
            writer_context = emit_compact_context(
                task_dir,
                raw_agent_name=f"harness:writer:{worker_name}",
                explicit_worker=worker_name,
            )
            phases.append(
                _phase_spec(
                    worker_name,
                    "writer",
                    "documentation_sync",
                    team_state.get("team_doc_sync_artifact") or "DOC_SYNC.md",
                    writer_context,
                )
            )
        if worker_name in document_critic_workers:
            critic_context = emit_compact_context(
                task_dir,
                raw_agent_name=f"harness:critic-document:{worker_name}",
                explicit_worker=worker_name,
            )
            phases.append(
                _phase_spec(
                    worker_name,
                    "critic-document",
                    "documentation_review",
                    team_state.get("team_document_critic_artifact") or "CRITIC__document.md",
                    critic_context,
                )
            )
        if worker_name in synthesis_workers:
            phases.append(
                _phase_spec(
                    worker_name,
                    "developer",
                    "handoff_refresh",
                    "HANDOFF.md",
                    developer_context,
                )
            )

        spec = {
            "worker": worker_name,
            "role_scope": str(worker_roles.get(worker_name) or "worker"),
            "owned_paths": list(owned_paths_map.get(worker_name, []) or []),
            "forbidden_paths": list(forbidden_paths_map.get(worker_name, []) or []),
            "shared_read_only_paths": list(shared_paths or []),
            "summary_artifact": _task_rel(team_worker_summary_relpath(worker_name)),
            "must_read": list(developer_context.get("must_read") or [])[:6],
            "next_action": str(developer_context.get("next_action") or ""),
            "notes": list(developer_context.get("notes") or [])[:4],
            "default_agent_name": f"harness:developer:{worker_name}",
            "default_env_file": _task_rel(os.path.join("team", "bootstrap", f"{worker_name}.developer.env")),
            "default_context_command": _context_command(worker_name, f"harness:developer:{worker_name}"),
            "refresh_command": bootstrap["refresh_command"],
            "phases": phases,
            "is_synthesis_owner": worker_name in synthesis_workers,
            "is_handoff_owner": worker_name in synthesis_workers,
            "is_runtime_verification_owner": worker_name in runtime_workers,
            "is_doc_sync_owner": worker_name in doc_sync_workers,
            "is_document_critic_owner": worker_name in document_critic_workers,
        }
        bootstrap["workers"].append(spec)

    bootstrap["expected_files"] = [
        _task_rel(relpath) for relpath in _team_bootstrap_expected_relpaths(task_dir, team_state=team_state)
    ]

    if not write_files:
        return bootstrap

    bootstrap_dir_abs = os.path.join(task_dir, "team", "bootstrap")
    os.makedirs(bootstrap_dir_abs, exist_ok=True)
    generated = []

    index_payload = {
        "task_id": bootstrap["task_id"],
        "provider": bootstrap["provider"],
        "team_status": bootstrap["team_status"],
        "generated_at": now_iso(),
        "bootstrap_signature": bootstrap_signature_value,
        "refresh_command": bootstrap["refresh_command"],
        "expected_files": bootstrap["expected_files"],
        "workers": bootstrap["workers"],
    }
    index_abs = os.path.join(bootstrap_dir_abs, "index.json")
    with open(index_abs, "w", encoding="utf-8") as fh:
        json.dump(index_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    generated.append(_task_rel(os.path.join("team", "bootstrap", "index.json")))

    for spec in bootstrap["workers"]:
        brief_rel = normalize_path(os.path.join("team", "bootstrap", f"{spec['worker']}.md"))
        brief_abs = os.path.join(task_dir, brief_rel)
        with open(brief_abs, "w", encoding="utf-8") as fh:
            fh.write("\n".join(_worker_brief_lines(spec)).rstrip() + "\n")
        generated.append(_task_rel(brief_rel))

        for phase in spec.get("phases") or []:
            env_rel = normalize_path(str(phase.get("env_relpath") or ""))
            if not env_rel:
                continue
            env_abs = os.path.join(task_dir, env_rel)
            with open(env_abs, "w", encoding="utf-8") as fh:
                fh.write("\n".join(phase.get("env_lines") or []).rstrip() + "\n")
            generated.append(_task_rel(env_rel))

    bootstrap["generated_files"] = generated
    return bootstrap


def repo_root_for_task_dir(task_dir):
    """Best-effort repository root inference from a task directory."""
    abs_task_dir = os.path.abspath(task_dir)
    marker_rel = os.path.join(*normalize_path(TASK_DIR).split("/"))
    marker = f"{os.sep}{marker_rel}{os.sep}"
    if marker in abs_task_dir:
        return abs_task_dir.split(marker, 1)[0] or os.getcwd()
    return find_repo_root(abs_task_dir)


def ensure_task_scaffold(task_dir, task_id, request_text=""):
    """Create a minimal canonical task scaffold when it does not exist.

    Returns a dict describing whether files were created.
    """
    task_dir = os.path.abspath(task_dir)
    task_id = canonical_task_id(task_id=task_id, task_dir=task_dir)
    created = []
    os.makedirs(task_dir, exist_ok=True)

    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.exists(state_file):
        browser_required = "false"
        if is_browser_first_project():
            browser_required = "true"

        with open(state_file, "w", encoding="utf-8") as f:
            f.write(
                f"""task_id: {task_id}
schema_version: {TASK_STATE_SCHEMA_VERSION}
state_revision: 0
status: created
execution_mode: pending
planning_mode: standard
plan_verdict: pending
runtime_verdict: pending
runtime_verdict_freshness: current
document_verdict: pending
document_verdict_freshness: current
runtime_verdict_fail_count: 0
intent_verdict: pending
intent_verdict_freshness: current
browser_required: {browser_required}
doc_sync_required: false
doc_changes_detected: false
touched_paths: []
verification_targets: []
orchestration_mode: pending
risk_level: pending
parallelism: 1
workflow_locked: true
maintenance_task: false
routing_compiled: false
plan_session_state: closed
agent_run_developer_count: 0
agent_run_writer_count: 0
agent_run_critic_plan_count: 0
agent_run_critic_runtime_count: 0
agent_run_critic_document_count: 0
updated: {now_iso()}
"""
            )
        created.append(state_file)

    request_file = os.path.join(task_dir, "REQUEST.md")
    if not os.path.exists(request_file):
        body = request_text.strip() if str(request_text or "").strip() else "<!-- Request details pending -->"
        with open(request_file, "w", encoding="utf-8") as f:
            f.write(
                f"# Request: {task_id}\n"
                f"created: {now_iso()}\n\n"
                f"{body}\n"
            )
        created.append(request_file)

    return {
        "task_dir": task_dir,
        "task_id": task_id,
        "created": created,
        "created_any": bool(created),
    }


def build_team_dispatch(task_dir, write_files=False):
    """Build provider-ready launch artifacts from a fresh team bootstrap pack."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    provider = yaml_field("team_provider", state_file) or "none"
    team_state = team_artifact_status(task_dir)
    bootstrap = build_team_bootstrap(task_dir, write_files=False)
    bootstrap_state = team_bootstrap_status(task_dir, team_state=team_state)
    repo_root = repo_root_for_task_dir(task_dir)
    task_root = os.path.join(TASK_DIR, task_id)
    dispatch_dir_rel = normalize_path(os.path.join("team", "bootstrap", "provider"))
    dispatch_index_rel = normalize_path(os.path.join(dispatch_dir_rel, "dispatch.json"))

    def _task_rel(name):
        return f"{task_root}/{normalize_path(name)}"

    dispatch = {
        "task_id": task_id,
        "task_dir": task_dir,
        "provider": provider,
        "bootstrap_signature": str(bootstrap_state.get("current_signature") or ""),
        "bootstrap_refresh_needed": bool(bootstrap_state.get("refresh_needed")),
        "bootstrap_refresh_reason": str(bootstrap_state.get("reason") or ""),
        "ready": False,
        "reason": "",
        "dispatch_dir": _task_rel(dispatch_dir_rel),
        "dispatch_index": _task_rel(dispatch_index_rel),
        "provider_prompt": "",
        "provider_launcher": "",
        "launch_command_preview": "",
        "implement_dispatcher": "",
        "workers": [],
        "expected_files": [],
        "generated_files": [],
    }

    if not bootstrap.get("ready"):
        dispatch["reason"] = str(bootstrap.get("reason") or "team bootstrap is not ready")
        return dispatch
    if bootstrap_state.get("refresh_needed"):
        dispatch["reason"] = str(bootstrap_state.get("reason") or "refresh the bootstrap pack first")
        return dispatch

    workers = list(bootstrap.get("workers") or [])
    if not workers:
        dispatch["reason"] = "no workers declared in bootstrap"
        return dispatch

    allowed_tools = [
        "Read", "Glob", "Grep", "Write", "Edit", "MultiEdit", "Bash", "Skill", "TodoWrite",
        "TaskCreate", "TaskGet", "TaskList", "TaskUpdate",
        "mcp__plugin_harness_harness__task_context",
        "mcp__plugin_harness_harness__task_update_from_git_diff",
        "mcp__plugin_harness_harness__task_verify",
        "mcp__plugin_harness_harness__task_close",
        "mcp__plugin_harness_harness__write_critic_runtime",
        "mcp__plugin_harness_harness__write_critic_document",
        "mcp__plugin_harness_harness__write_critic_plan",
        "mcp__plugin_harness_harness__write_doc_sync",
        "mcp__plugin_harness_harness__write_handoff",
    ]

    def _phase_prompt_text(spec, phase):
        worker_name = str(spec.get("worker") or "worker")
        role_name = str(phase.get("role") or "worker")
        phase_name = str(phase.get("phase") or "implement")
        lines = [
            f"# Team Worker Phase Prompt — {worker_name} / {phase_name}",
            "",
            f"You are `{worker_name}` for task `{task_id}`.",
            f"Current phase: `{phase_name}`.",
            f"Current role: `{role_name}`.",
            "",
            "## Read first",
            f"- {_task_rel('TEAM_PLAN.md')}",
            f"- {_task_rel(os.path.join('team', 'bootstrap', f'{worker_name}.md'))}",
            f"- env snippet: {phase.get('env_file')}",
            "",
            "## Hard rules",
            f"- Only mutate paths owned by `{worker_name}`.",
            "- Never edit another worker's owned paths, shared read-only paths, or forbidden paths.",
            "- Verify before claiming completion.",
            "- Leave the expected artifact(s) before you stop.",
            "",
            "## Owned writable paths",
        ]
        lines.extend([f"- {item}" for item in (spec.get("owned_paths") or [])] or ["- none"])
        lines.extend(["", "## Forbidden writes"])
        lines.extend([f"- {item}" for item in (spec.get("forbidden_paths") or [])] or ["- none"])
        lines.extend(["", "## Shared read-only paths"])
        lines.extend([f"- {item}" for item in (spec.get("shared_read_only_paths") or [])] or ["- none"])
        lines.extend([
            "",
            "## Deliverables",
            f"- phase artifact: {phase.get('artifact') or 'none'}",
            f"- worker summary: {spec.get('summary_artifact') or 'none'}",
            "",
            "## First action",
            f"- {phase.get('next_action') or spec.get('next_action') or 'Read TEAM_PLAN.md, stay inside owned paths, then verify and summarize your slice.'}",
            "",
            "## Refresh worker-specific context",
            f"- {phase.get('context_command') or 'python3 plugin/scripts/hctl.py context --json'}",
        ])
        return "\n".join(lines).rstrip() + "\n"

    def _run_script_text(prompt_rel, env_rel, log_rel, session_name):
        lines = [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            f"REPO_ROOT={shlex.quote(repo_root)}",
            f"PROMPT_FILE={shlex.quote(os.path.join(task_dir, normalize_path(prompt_rel)))}",
            f"ENV_FILE={shlex.quote(os.path.join(task_dir, normalize_path(env_rel)))}",
            f"LOG_FILE={shlex.quote(os.path.join(task_dir, normalize_path(log_rel)))}",
            f"SESSION_NAME={shlex.quote(session_name)}",
            'if [ ! -f "$PROMPT_FILE" ]; then echo "missing prompt file: $PROMPT_FILE" >&2; exit 1; fi',
            'if [ ! -f "$ENV_FILE" ]; then echo "missing env file: $ENV_FILE" >&2; exit 1; fi',
            'mkdir -p "$(dirname "$LOG_FILE")"',
            "set -a",
            'source "$ENV_FILE"',
            "set +a",
            'cd "$REPO_ROOT"',
            'CMD=(claude -p "$(cat \"$PROMPT_FILE\")" --output-format json --permission-mode dontAsk --max-turns 80 --name "$SESSION_NAME")',
        ]
        for tool_name in allowed_tools:
            lines.append(f'CMD+=(--allowedTools {shlex.quote(tool_name)})')
        lines.extend(['"${CMD[@]}" > "$LOG_FILE" 2>&1', 'echo "$LOG_FILE"'])
        return "\n".join(lines).rstrip() + "\n"

    provider_prompt_rel = normalize_path(os.path.join(dispatch_dir_rel, f"{provider or 'none'}-team.prompt.md"))
    provider_launcher_rel = normalize_path(os.path.join(dispatch_dir_rel, f"launch-{provider or 'none'}-team.sh"))
    implement_dispatcher_rel = normalize_path(os.path.join(dispatch_dir_rel, "dispatch-implementers.sh"))
    dispatch["provider_prompt"] = _task_rel(provider_prompt_rel)
    dispatch["provider_launcher"] = _task_rel(provider_launcher_rel)
    dispatch["implement_dispatcher"] = _task_rel(implement_dispatcher_rel)

    phase_prompt_files = []
    phase_run_scripts = []
    implement_launches = []
    provider_worker_lines = []
    for spec in workers:
        worker_name = str(spec.get("worker") or "worker")
        owned_preview = ", ".join(spec.get("owned_paths") or []) or "none"
        provider_worker_lines.append(f"- {worker_name}: owned={owned_preview}; bootstrap={_task_rel(os.path.join('team', 'bootstrap', f'{worker_name}.md'))}")
        worker_entry = {"worker": worker_name, "role_scope": str(spec.get("role_scope") or "worker"), "owned_paths": list(spec.get("owned_paths") or []), "phases": []}
        for phase in spec.get("phases") or []:
            role_name = str(phase.get("role") or "worker")
            phase_name = str(phase.get("phase") or "implement")
            prompt_rel = normalize_path(os.path.join("team", "bootstrap", f"{worker_name}.{phase_name}.{role_name}.prompt.md"))
            env_rel = normalize_path(str(phase.get("env_file") or "").replace(f"{task_root}/", "", 1))
            run_rel = normalize_path(os.path.join("team", "bootstrap", f"run-{worker_name}-{phase_name}.sh"))
            log_rel = normalize_path(os.path.join("team", "bootstrap", "logs", f"{worker_name}-{phase_name}.json"))
            session_name = f"{task_id.lower()}-{worker_name}-{phase_name}".replace("_", "-")
            phase_prompt_files.append((prompt_rel, _phase_prompt_text(spec, phase)))
            phase_run_scripts.append((run_rel, _run_script_text(prompt_rel, env_rel, log_rel, session_name)))
            phase_entry = {
                "phase": phase_name,
                "role": role_name,
                "agent_name": str(phase.get("agent_name") or ""),
                "artifact": str(phase.get("artifact") or ""),
                "prompt_file": _task_rel(prompt_rel),
                "env_file": str(phase.get("env_file") or ""),
                "run_script": _task_rel(run_rel),
                "log_file": _task_rel(log_rel),
                "session_name": session_name,
                "command_preview": "bash " + shlex.quote(os.path.join(repo_root, normalize_path(run_rel))),
            }
            worker_entry["phases"].append(phase_entry)
            if phase_name == "implement":
                implement_launches.append(phase_entry["command_preview"])
        dispatch["workers"].append(worker_entry)

    expected = [provider_prompt_rel, provider_launcher_rel, implement_dispatcher_rel, dispatch_index_rel]
    for worker_entry in dispatch["workers"]:
        for phase_entry in worker_entry.get("phases") or []:
            prompt_rel = normalize_path(str(phase_entry.get("prompt_file") or "").replace(f"{task_root}/", "", 1))
            run_rel = normalize_path(str(phase_entry.get("run_script") or "").replace(f"{task_root}/", "", 1))
            if prompt_rel:
                expected.append(prompt_rel)
            if run_rel:
                expected.append(run_rel)
    seen_expected = []
    for relpath in expected:
        if relpath and relpath not in seen_expected:
            seen_expected.append(relpath)
    dispatch["expected_files"] = [_task_rel(relpath) for relpath in seen_expected]

    provider_prompt_lines = [
        f"# Team Dispatch Prompt — {task_id}",
        "",
        f"Create or resume a team for task `{task_id}`.",
        f"Preferred provider: `{provider}`.",
        f"Planned worker count: {len(workers)}.",
        "",
        "## Ground truth",
        f"- TEAM_PLAN.md: {_task_rel('TEAM_PLAN.md')}",
        f"- TEAM_SYNTHESIS.md: {_task_rel('TEAM_SYNTHESIS.md')}",
        f"- bootstrap index: {bootstrap.get('bootstrap_index')}",
        "",
        "## Non-negotiable rules",
        "- Each worker must stay inside its owned writable paths.",
        "- Shared read-only and forbidden paths are off-limits.",
        "- Contributors must leave team/worker-<name>.md before synthesis.",
        "- Lead / synthesis owners must finish TEAM_SYNTHESIS.md, final verification, docs sync, and HANDOFF refresh in order.",
        "",
        "## Planned workers",
        *provider_worker_lines,
        "",
        "## Dispatch order",
        "1. Read TEAM_PLAN.md and team/bootstrap/index.json.",
        "2. Spawn or message each worker with its bootstrap brief and phase prompt.",
        "3. Keep writable ownership disjoint.",
        "4. Wait for worker summaries before synthesis.",
        "5. After synthesis, follow runtime verification → documentation → HANDOFF refresh → close.",
    ]
    provider_prompt_text = "\n".join(provider_prompt_lines).rstrip() + "\n"

    if provider == "omc":
        launcher_lines = [
            "#!/usr/bin/env bash", "set -euo pipefail", f"REPO_ROOT={shlex.quote(repo_root)}",
            f"PROMPT_FILE={shlex.quote(os.path.join(task_dir, provider_prompt_rel))}",
            'if [ ! -f "$PROMPT_FILE" ]; then echo "missing provider prompt: $PROMPT_FILE" >&2; exit 1; fi',
            'cd "$REPO_ROOT"', 'TEAM_PROMPT="$(cat "$PROMPT_FILE")"', f"exec omc team {len(workers)}:executor \"$TEAM_PROMPT\"",
        ]
        dispatch["launch_command_preview"] = "bash " + shlex.quote(os.path.join(repo_root, provider_launcher_rel))
    elif provider == "native":
        launcher_lines = [
            "#!/usr/bin/env bash", "set -euo pipefail", f"PROMPT_FILE={shlex.quote(os.path.join(task_dir, provider_prompt_rel))}",
            'cat <<"EOF"',
            "Native Claude Code teams are created from a running Claude lead session.",
            "1. Ensure CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1 is enabled for the lead session.",
            "2. Open or resume the lead Claude Code session in the repo root.",
            '3. Paste the contents of "$PROMPT_FILE" into that lead session.',
            "4. Let the lead create teammates that match the planned worker ids and owned paths.",
            "EOF", 'echo "$PROMPT_FILE"',
        ]
        dispatch["launch_command_preview"] = "cat " + shlex.quote(os.path.join(repo_root, provider_prompt_rel))
    else:
        launcher_lines = ["#!/usr/bin/env bash", "set -euo pipefail", 'cat <<"EOF"', "No native team provider is active. Use the generated per-worker run-*.sh helpers as a headless multi-session fallback.", "EOF"]
        dispatch["launch_command_preview"] = implement_launches[0] if implement_launches else ""

    dispatch["ready"] = True
    dispatch["reason"] = "ready"
    if not write_files:
        return dispatch

    provider_dir_abs = os.path.join(task_dir, dispatch_dir_rel)
    logs_dir_abs = os.path.join(task_dir, "team", "bootstrap", "logs")
    os.makedirs(provider_dir_abs, exist_ok=True)
    os.makedirs(logs_dir_abs, exist_ok=True)
    generated = []
    for relpath, content in phase_prompt_files:
        abs_path = os.path.join(task_dir, relpath)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        generated.append(_task_rel(relpath))
    for relpath, content in phase_run_scripts:
        abs_path = os.path.join(task_dir, relpath)
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(content)
        try:
            os.chmod(abs_path, 0o755)
        except OSError:
            pass
        generated.append(_task_rel(relpath))
    provider_prompt_abs = os.path.join(task_dir, provider_prompt_rel)
    with open(provider_prompt_abs, "w", encoding="utf-8") as fh:
        fh.write(provider_prompt_text)
    generated.append(_task_rel(provider_prompt_rel))
    provider_launcher_abs = os.path.join(task_dir, provider_launcher_rel)
    with open(provider_launcher_abs, "w", encoding="utf-8") as fh:
        fh.write("\n".join(launcher_lines).rstrip() + "\n")
    try:
        os.chmod(provider_launcher_abs, 0o755)
    except OSError:
        pass
    generated.append(_task_rel(provider_launcher_rel))
    dispatcher_lines = ["#!/usr/bin/env bash", "set -euo pipefail", f"REPO_ROOT={shlex.quote(repo_root)}", 'cd "$REPO_ROOT"']
    for command_preview in implement_launches:
        dispatcher_lines.append(f"{command_preview} &")
    dispatcher_lines.append("wait")
    dispatcher_abs = os.path.join(task_dir, implement_dispatcher_rel)
    with open(dispatcher_abs, "w", encoding="utf-8") as fh:
        fh.write("\n".join(dispatcher_lines).rstrip() + "\n")
    try:
        os.chmod(dispatcher_abs, 0o755)
    except OSError:
        pass
    generated.append(_task_rel(implement_dispatcher_rel))
    dispatch_index_abs = os.path.join(task_dir, dispatch_index_rel)
    dispatch_index_payload = {
        "task_id": task_id, "provider": provider, "bootstrap_signature": dispatch["bootstrap_signature"], "generated_at": now_iso(),
        "provider_prompt": dispatch["provider_prompt"], "provider_launcher": dispatch["provider_launcher"],
        "launch_command_preview": dispatch["launch_command_preview"], "implement_dispatcher": dispatch["implement_dispatcher"],
        "workers": dispatch["workers"], "generated_files": generated + [_task_rel(dispatch_index_rel)],
    }
    with open(dispatch_index_abs, "w", encoding="utf-8") as fh:
        json.dump(dispatch_index_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
        fh.write("\n")
    generated.append(_task_rel(dispatch_index_rel))
    dispatch["generated_files"] = generated
    return dispatch


def team_dispatch_status(task_dir, team_state=None):
    """Return freshness + completeness state for provider dispatch artifacts."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    bootstrap_state = team_bootstrap_status(task_dir, team_state=team_state)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    provider = yaml_field("team_provider", state_file) or "none"
    task_root = os.path.join(TASK_DIR, task_id)
    rel_dir = normalize_path(os.path.join("team", "bootstrap", "provider"))
    rel_index = normalize_path(os.path.join(rel_dir, "dispatch.json"))
    status = {
        "provider": str(provider or "none"), "available": bool(bootstrap_state.get("available") and not bootstrap_state.get("refresh_needed")),
        "generated": False, "stale": False, "refresh_needed": False, "reason": "", "dispatch_dir": rel_dir, "dispatch_index": rel_index,
        "generated_at": "", "generated_signature": "", "current_signature": str(bootstrap_state.get("current_signature") or ""),
        "expected_files": [], "missing_files": [],
        "refresh_command": f"python3 plugin/scripts/hctl.py team-dispatch --task-dir {task_root} --write-files",
    }
    if not status["available"]:
        status["reason"] = str(bootstrap_state.get("reason") or "team bootstrap is not ready")
        return status
    index_abs = os.path.join(task_dir, rel_index)
    if not os.path.isfile(index_abs):
        status["reason"] = "team dispatch has not been generated yet"
        return status
    status["generated"] = True
    try:
        with open(index_abs, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
    except (OSError, json.JSONDecodeError):
        status["stale"] = True; status["refresh_needed"] = True; status["reason"] = "team/bootstrap/provider/dispatch.json is unreadable"; return status
    status["generated_at"] = str(parsed.get("generated_at") or "")
    status["generated_signature"] = str(parsed.get("bootstrap_signature") or "")
    status["expected_files"] = [normalize_path(str(relpath or "").replace(f"{task_root}/", "", 1)) for relpath in (parsed.get("generated_files") or []) if str(relpath or "").strip()]
    missing = []
    for relpath in status["expected_files"]:
        if relpath and not os.path.isfile(os.path.join(task_dir, relpath)):
            missing.append(relpath)
    status["missing_files"] = missing
    if missing:
        status["stale"] = True; status["refresh_needed"] = True; status["reason"] = f"dispatch files missing: {', '.join(missing[:4])}"; return status
    generated_provider = str(parsed.get("provider") or "none")
    if generated_provider != str(provider or "none"):
        status["stale"] = True; status["refresh_needed"] = True; status["reason"] = f"dispatch provider changed ({generated_provider} → {provider})"; return status
    if not status["generated_signature"]:
        status["stale"] = True; status["refresh_needed"] = True; status["reason"] = "dispatch signature is missing"; return status
    if status["generated_signature"] != status["current_signature"]:
        status["stale"] = True; status["refresh_needed"] = True; status["reason"] = "team dispatch is out of date with the current bootstrap signature"; return status
    status["reason"] = "current"
    return status



def _team_launch_default_target(provider):
    """Return the default launch target for a provider."""
    provider_name = str(provider or "none").strip().lower() or "none"
    return "provider" if provider_name in ("native", "omc") else "implementers"



def _team_launch_target_metadata(task_dir, task_root, dispatch_index, provider, target_name):
    """Return launch metadata for a concrete provider or implementers target."""
    repo_root = repo_root_for_task_dir(task_dir)
    provider_prompt = normalize_path(str(dispatch_index.get("provider_prompt") or "").replace(f"{task_root}/", "", 1))
    provider_launcher = normalize_path(str(dispatch_index.get("provider_launcher") or "").replace(f"{task_root}/", "", 1))
    implement_dispatcher = normalize_path(str(dispatch_index.get("implement_dispatcher") or "").replace(f"{task_root}/", "", 1))

    if target_name == "provider":
        launch_script_rel = provider_launcher
        command_preview = str(dispatch_index.get("launch_command_preview") or "")
    else:
        launch_script_rel = implement_dispatcher
        command_preview = (
            f"bash {shlex.quote(os.path.join(repo_root, implement_dispatcher))}"
            if implement_dispatcher else ""
        )

    interactive_required = target_name == "provider" and str(provider or "none") == "native"
    execute_blocker = ""
    if not launch_script_rel:
        execute_blocker = "team launch script is missing from the dispatch pack"
    elif interactive_required:
        execute_blocker = "native team launch requires an interactive lead Claude session"
    elif target_name == "provider" and str(provider or "none") == "omc":
        omc_ready = bool(omc_runtime_probe().get("ready"))
        if not omc_ready:
            execute_blocker = "omc CLI not found for provider launch"
    elif target_name == "implementers":
        if shutil.which("claude") is None:
            execute_blocker = "claude CLI not found for worker run helpers"

    return {
        "target": target_name,
        "launch_script": launch_script_rel,
        "launch_command_preview": command_preview,
        "interactive_required": bool(interactive_required),
        "execute_supported": bool(launch_script_rel) and not bool(execute_blocker),
        "execute_blocker": execute_blocker,
        "provider_prompt": provider_prompt,
        "implement_dispatcher": implement_dispatcher,
        "ready": bool(launch_script_rel),
    }



def _team_launch_auto_execute_target(task_dir, task_root, dispatch_index, provider, preferred_meta):
    """Return the best auto-execute target for team-launch.

    The default team plan remains provider-first for native/omc, but when the
    preferred target cannot be executed headlessly we allow ``team-launch
    --execute`` to fall back to the implementer dispatcher instead of failing.
    """
    if preferred_meta.get("execute_supported"):
        return preferred_meta, ""
    fallback_meta = _team_launch_target_metadata(
        task_dir,
        task_root,
        dispatch_index,
        provider,
        "implementers",
    )
    if fallback_meta.get("execute_supported"):
        reason = (
            f"preferred target '{preferred_meta.get('target')}' is not auto-executable "
            f"({preferred_meta.get('execute_blocker') or 'blocked'}); falling back to 'implementers'"
        )
        return fallback_meta, reason
    return preferred_meta, ""



def team_launch_signature(task_dir, team_state=None):
    """Signature for the current default team launch plan."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
    provider = yaml_field("team_provider", os.path.join(task_dir, "TASK_STATE.yaml")) or "none"
    return f"{dispatch_state.get('current_signature') or ''}:{_team_launch_default_target(provider)}"



def team_launch_status(task_dir, team_state=None):
    """Return readiness + freshness state for the default team launch plan."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    provider = yaml_field("team_provider", state_file) or "none"
    task_root = os.path.join(TASK_DIR, task_id)
    launch_manifest_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "launch.json"))
    target = _team_launch_default_target(provider)
    status = {
        "provider": str(provider or "none"),
        "target": target,
        "available": False,
        "ready": False,
        "generated": False,
        "stale": False,
        "refresh_needed": False,
        "reason": "",
        "launch_manifest": launch_manifest_rel,
        "launch_script": "",
        "launch_command_preview": "",
        "provider_prompt": "",
        "implement_dispatcher": "",
        "interactive_required": False,
        "execute_supported": False,
        "execute_blocker": "",
        "preferred_execute_blocker": "",
        "execute_target": "",
        "execute_launch_script": "",
        "execute_command_preview": "",
        "execute_fallback_available": False,
        "execute_resolution_reason": "",
        "generated_at": "",
        "generated_signature": "",
        "current_signature": str(team_launch_signature(task_dir, team_state=team_state) or ""),
        "refresh_command": f"python3 plugin/scripts/hctl.py team-launch --task-dir {task_root} --write-files",
    }
    if not dispatch_state.get("available"):
        status["reason"] = str(dispatch_state.get("reason") or "team dispatch is not ready")
        return status
    if not dispatch_state.get("generated"):
        status["reason"] = "team dispatch has not been generated yet"
        return status
    if dispatch_state.get("refresh_needed"):
        status["reason"] = str(dispatch_state.get("reason") or "team dispatch is stale")
        status["refresh_needed"] = True
        return status

    index_abs = os.path.join(task_dir, str(dispatch_state.get("dispatch_index") or ""))
    try:
        with open(index_abs, "r", encoding="utf-8") as fh:
            dispatch_index = json.load(fh)
    except (OSError, json.JSONDecodeError):
        status["reason"] = "team/bootstrap/provider/dispatch.json is unreadable"
        status["refresh_needed"] = True
        return status

    preferred_meta = _team_launch_target_metadata(task_dir, task_root, dispatch_index, provider, target)
    auto_execute_meta, execute_reason = _team_launch_auto_execute_target(
        task_dir,
        task_root,
        dispatch_index,
        provider,
        preferred_meta,
    )

    status["available"] = bool(preferred_meta.get("ready"))
    status["ready"] = bool(preferred_meta.get("ready"))
    status["launch_script"] = str(preferred_meta.get("launch_script") or "")
    status["launch_command_preview"] = str(preferred_meta.get("launch_command_preview") or "")
    status["provider_prompt"] = str(preferred_meta.get("provider_prompt") or "")
    status["implement_dispatcher"] = str(preferred_meta.get("implement_dispatcher") or "")
    status["interactive_required"] = bool(preferred_meta.get("interactive_required"))
    status["preferred_execute_blocker"] = str(preferred_meta.get("execute_blocker") or "")
    status["execute_target"] = str(auto_execute_meta.get("target") or "")
    status["execute_launch_script"] = str(auto_execute_meta.get("launch_script") or "")
    status["execute_command_preview"] = str(auto_execute_meta.get("launch_command_preview") or "")
    status["execute_fallback_available"] = bool(
        auto_execute_meta.get("execute_supported") and auto_execute_meta.get("target") != target
    )
    status["execute_resolution_reason"] = str(execute_reason or "")
    status["execute_supported"] = bool(auto_execute_meta.get("execute_supported"))
    status["execute_blocker"] = "" if status["execute_supported"] else str(
        auto_execute_meta.get("execute_blocker") or preferred_meta.get("execute_blocker") or ""
    )

    manifest_abs = os.path.join(task_dir, launch_manifest_rel)
    if not os.path.isfile(manifest_abs):
        status["reason"] = "team launch plan has not been generated yet"
        return status

    status["generated"] = True
    try:
        with open(manifest_abs, "r", encoding="utf-8") as fh:
            launch_manifest = json.load(fh)
    except (OSError, json.JSONDecodeError):
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "team/bootstrap/provider/launch.json is unreadable"
        return status

    status["generated_at"] = str(launch_manifest.get("generated_at") or "")
    status["generated_signature"] = str(launch_manifest.get("launch_signature") or "")
    recorded_target = str(launch_manifest.get("target") or target)
    recorded_script = normalize_path(str(launch_manifest.get("launch_script") or preferred_meta.get("launch_script") or "").replace(f"{task_root}/", "", 1))
    if recorded_target != target:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = f"launch target changed ({recorded_target} → {target})"
        return status
    if status["generated_signature"] and status["generated_signature"] != status["current_signature"]:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "team launch plan is out of date with the current dispatch signature"
        return status
    if not status["generated_signature"]:
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = "team launch signature is missing"
        return status
    if recorded_script and not os.path.isfile(os.path.join(task_dir, recorded_script)):
        status["stale"] = True
        status["refresh_needed"] = True
        status["reason"] = f"launch script missing: {recorded_script}"
        return status
    status["reason"] = "current"
    return status



def build_team_launch(task_dir, write_files=False, execute=False, auto_refresh=True, target="auto"):
    """Build or execute the provider/worker launch entrypoint for a team task.

    The helper sits one step above bootstrap + dispatch. With ``auto_refresh`` it
    regenerates stale or missing bootstrap/dispatch artifacts before producing a
    launch manifest. ``execute`` uses a detached background spawn so the caller
    gets a PID + log paths without blocking on the child process. For native team
    providers, auto-execute can fall back to the implementer dispatcher while the
    frozen launch manifest still points at the provider-first control surface.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    provider = yaml_field("team_provider", state_file) or "none"
    repo_root = repo_root_for_task_dir(task_dir)
    task_root = os.path.join(TASK_DIR, task_id)
    launch_manifest_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "launch.json"))
    stdout_log_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "launch.stdout.log"))
    stderr_log_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "launch.stderr.log"))

    def _task_rel(name):
        return f"{task_root}/{normalize_path(name)}"

    payload = {
        "task_id": task_id,
        "task_dir": task_dir,
        "provider": provider,
        "requested_target": str(target or "auto"),
        "target": "",
        "ready": False,
        "reason": "",
        "auto_refresh": bool(auto_refresh),
        "bootstrap_refreshed": False,
        "dispatch_refreshed": False,
        "launch_manifest": _task_rel(launch_manifest_rel),
        "launch_script": "",
        "launch_command_preview": "",
        "provider_prompt": "",
        "implement_dispatcher": "",
        "interactive_required": False,
        "execute_supported": False,
        "execute_blocker": "",
        "execute_target": "",
        "execute_launch_script": "",
        "execute_command_preview": "",
        "execute_fallback_available": False,
        "execute_resolution_reason": "",
        "stdout_log": _task_rel(stdout_log_rel),
        "stderr_log": _task_rel(stderr_log_rel),
        "generated_files": [],
        "execution": {},
    }

    team_state = team_artifact_status(task_dir)
    if auto_refresh:
        bootstrap_state = team_bootstrap_status(task_dir, team_state=team_state)
        if bootstrap_state.get("available") and (not bootstrap_state.get("generated") or bootstrap_state.get("refresh_needed")):
            build_team_bootstrap(task_dir, write_files=True)
            payload["bootstrap_refreshed"] = True
            team_state = team_artifact_status(task_dir)
        dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
        if dispatch_state.get("available") and (not dispatch_state.get("generated") or dispatch_state.get("refresh_needed")):
            build_team_dispatch(task_dir, write_files=True)
            payload["dispatch_refreshed"] = True
            team_state = team_artifact_status(task_dir)

    dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
    if not dispatch_state.get("available"):
        payload["reason"] = str(dispatch_state.get("reason") or "team launch is not ready")
        return payload
    if not dispatch_state.get("generated"):
        payload["reason"] = "team dispatch has not been generated yet"
        return payload
    if dispatch_state.get("refresh_needed"):
        payload["reason"] = str(dispatch_state.get("reason") or "team dispatch is stale")
        return payload

    dispatch_index_path = os.path.join(task_dir, str(dispatch_state.get("dispatch_index") or ""))
    try:
        with open(dispatch_index_path, "r", encoding="utf-8") as fh:
            dispatch_index = json.load(fh)
    except (OSError, json.JSONDecodeError):
        payload["reason"] = "team/bootstrap/provider/dispatch.json is unreadable"
        return payload

    requested_target = str(target or "auto").strip().lower() or "auto"
    if requested_target == "auto":
        target_name = _team_launch_default_target(provider)
    elif requested_target in ("provider", "implementers"):
        target_name = requested_target
    else:
        payload["reason"] = "target must be one of: auto, provider, implementers"
        return payload

    target_meta = _team_launch_target_metadata(task_dir, task_root, dispatch_index, provider, target_name)
    auto_execute_meta = target_meta
    execute_reason = ""
    if requested_target == "auto":
        auto_execute_meta, execute_reason = _team_launch_auto_execute_target(
            task_dir,
            task_root,
            dispatch_index,
            provider,
            target_meta,
        )

    payload["target"] = target_name
    payload["launch_script"] = _task_rel(target_meta["launch_script"]) if target_meta.get("launch_script") else ""
    payload["launch_command_preview"] = str(target_meta.get("launch_command_preview") or "")
    payload["provider_prompt"] = _task_rel(target_meta["provider_prompt"]) if target_meta.get("provider_prompt") else ""
    payload["implement_dispatcher"] = _task_rel(target_meta["implement_dispatcher"]) if target_meta.get("implement_dispatcher") else ""
    payload["interactive_required"] = bool(target_meta.get("interactive_required"))
    payload["execute_target"] = str(auto_execute_meta.get("target") or "")
    payload["execute_launch_script"] = _task_rel(auto_execute_meta["launch_script"]) if auto_execute_meta.get("launch_script") else ""
    payload["execute_command_preview"] = str(auto_execute_meta.get("launch_command_preview") or "")
    payload["execute_fallback_available"] = bool(
        auto_execute_meta.get("execute_supported") and auto_execute_meta.get("target") != target_name
    )
    payload["execute_resolution_reason"] = str(execute_reason or "")
    payload["execute_supported"] = bool(auto_execute_meta.get("execute_supported"))
    payload["execute_blocker"] = "" if payload["execute_supported"] else str(
        auto_execute_meta.get("execute_blocker") or target_meta.get("execute_blocker") or ""
    )

    if not target_meta.get("ready"):
        payload["reason"] = str(target_meta.get("execute_blocker") or "team launch script is missing from the dispatch pack")
        return payload

    payload["ready"] = True
    payload["reason"] = "ready"

    launch_signature_value = f"{dispatch_state.get('current_signature') or ''}:{target_name}"
    manifest_payload = {
        "task_id": task_id,
        "provider": provider,
        "requested_target": requested_target,
        "target": target_name,
        "generated_at": now_iso(),
        "launch_signature": launch_signature_value,
        "dispatch_signature": str(dispatch_state.get("current_signature") or ""),
        "launch_script": payload["launch_script"],
        "launch_command_preview": payload["launch_command_preview"],
        "provider_prompt": payload["provider_prompt"],
        "implement_dispatcher": payload["implement_dispatcher"],
        "interactive_required": payload["interactive_required"],
        "execute_supported": payload["execute_supported"],
        "execute_blocker": payload["execute_blocker"],
        "execute_target": payload["execute_target"],
        "execute_launch_script": payload["execute_launch_script"],
        "execute_command_preview": payload["execute_command_preview"],
        "execute_fallback_available": payload["execute_fallback_available"],
        "execute_resolution_reason": payload["execute_resolution_reason"],
        "auto_refresh": bool(auto_refresh),
        "bootstrap_refreshed": bool(payload["bootstrap_refreshed"]),
        "dispatch_refreshed": bool(payload["dispatch_refreshed"]),
        "stdout_log": payload["stdout_log"],
        "stderr_log": payload["stderr_log"],
        "workers": list(dispatch_index.get("workers") or []),
    }

    launch_manifest_abs = os.path.join(task_dir, launch_manifest_rel)
    stdout_log_abs = os.path.join(task_dir, stdout_log_rel)
    stderr_log_abs = os.path.join(task_dir, stderr_log_rel)
    if write_files or execute:
        os.makedirs(os.path.dirname(launch_manifest_abs), exist_ok=True)
        with open(launch_manifest_abs, "w", encoding="utf-8") as fh:
            json.dump(manifest_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        payload["generated_files"] = [_task_rel(launch_manifest_rel)]

    if execute:
        if not payload["execute_supported"]:
            payload["execution"] = {
                "requested": True,
                "spawned": False,
                "error": payload["execute_blocker"] or "launch spawn failed",
            }
            return payload
        launch_script_abs = os.path.join(task_dir, normalize_path(str(auto_execute_meta.get("launch_script") or "")))
        os.makedirs(os.path.dirname(stdout_log_abs), exist_ok=True)
        with open(stdout_log_abs, "a", encoding="utf-8") as out_fh, open(stderr_log_abs, "a", encoding="utf-8") as err_fh:
            proc = subprocess.Popen(
                ["bash", launch_script_abs],
                cwd=repo_root,
                stdout=out_fh,
                stderr=err_fh,
                start_new_session=True,
            )
        payload["execution"] = {
            "requested": True,
            "spawned": True,
            "mode": "detached",
            "pid": proc.pid,
            "started_at": now_iso(),
            "requested_target": requested_target,
            "resolved_target": payload["execute_target"],
            "target_resolution_reason": payload["execute_resolution_reason"],
            "launch_script": payload["execute_launch_script"],
            "command_preview": payload["execute_command_preview"],
            "stdout_log": payload["stdout_log"],
            "stderr_log": payload["stderr_log"],
        }
        if write_files or execute:
            manifest_payload["execution"] = payload["execution"]
            with open(launch_manifest_abs, "w", encoding="utf-8") as fh:
                json.dump(manifest_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
        for relpath in (stdout_log_rel, stderr_log_rel):
            if _task_rel(relpath) not in payload["generated_files"]:
                payload["generated_files"].append(_task_rel(relpath))
    return payload

def _normalize_team_phase_name(phase_name):
    """Return a canonical worker-phase identifier."""
    value = str(phase_name or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "": "",
        "auto": "auto",
        "implement": "implement",
        "worker_summary": "implement",
        "worker_summaries": "implement",
        "summary": "implement",
        "synthesis": "synthesis",
        "team_synthesis": "synthesis",
        "final_runtime_verification": "final_runtime_verification",
        "runtime": "final_runtime_verification",
        "runtime_verification": "final_runtime_verification",
        "documentation_sync": "documentation_sync",
        "doc_sync": "documentation_sync",
        "docs": "documentation_sync",
        "writer": "documentation_sync",
        "documentation_review": "documentation_review",
        "doc_review": "documentation_review",
        "critic_document": "documentation_review",
        "document_critic": "documentation_review",
        "handoff": "handoff_refresh",
        "handoff_refresh": "handoff_refresh",
    }
    return aliases.get(value, value)


def _load_team_dispatch_index(task_dir, team_state=None):
    """Return parsed dispatch index + state when current and readable."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
    if not dispatch_state.get("available"):
        return None, dispatch_state
    if not dispatch_state.get("generated") or dispatch_state.get("refresh_needed"):
        return None, dispatch_state
    dispatch_index_rel = str(dispatch_state.get("dispatch_index") or "")
    if not dispatch_index_rel:
        dispatch_state = dict(dispatch_state)
        dispatch_state["reason"] = str(dispatch_state.get("reason") or "team dispatch index is missing")
        return None, dispatch_state
    dispatch_index_abs = os.path.join(task_dir, dispatch_index_rel)
    try:
        with open(dispatch_index_abs, "r", encoding="utf-8") as fh:
            parsed = json.load(fh)
        if not isinstance(parsed, dict):
            raise ValueError("dispatch index is not a dict")
    except (OSError, ValueError, json.JSONDecodeError):
        dispatch_state = dict(dispatch_state)
        dispatch_state["reason"] = "team/bootstrap/provider/dispatch.json is unreadable"
        dispatch_state["refresh_needed"] = True
        return None, dispatch_state
    return parsed, dispatch_state


def select_team_relaunch_target(task_dir, team_state=None, worker=None, phase="auto", raw_agent_name=None, explicit_worker=None):
    """Return the best worker/phase relaunch target for the current team state."""
    if team_state is None:
        team_state = team_artifact_status(task_dir)
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    task_root = os.path.join(TASK_DIR, task_id)
    payload = {
        "task_id": task_id,
        "available": False,
        "ready": False,
        "reason": "",
        "selection_reason": "",
        "selection_source": "",
        "worker": "",
        "phase": "",
        "artifact": "",
        "prompt_file": "",
        "env_file": "",
        "run_script": "",
        "log_file": "",
        "session_name": "",
        "command_preview": "",
        "dispatch_index": "team/bootstrap/provider/dispatch.json",
        "refresh_command": f"python3 plugin/scripts/hctl.py team-relaunch --task-dir {task_root} --write-files",
    }
    if team_state.get("orchestration_mode") != "team":
        payload["reason"] = "orchestration_mode is not team"
        return payload

    dispatch_index, dispatch_state = _load_team_dispatch_index(task_dir, team_state=team_state)
    payload["dispatch_index"] = str(dispatch_state.get("dispatch_index") or payload["dispatch_index"])
    if dispatch_index is None:
        payload["reason"] = str(dispatch_state.get("reason") or "team dispatch is not ready")
        return payload

    phase_by_worker = {}
    phase_order_by_worker = {}
    workers = []
    for worker_entry in (dispatch_index.get("workers") or []):
        if not isinstance(worker_entry, dict):
            continue
        worker_name = str(worker_entry.get("worker") or "").strip()
        if not worker_name:
            continue
        workers.append(worker_name)
        phase_map = {}
        phase_order = []
        for phase_entry in (worker_entry.get("phases") or []):
            if not isinstance(phase_entry, dict):
                continue
            canonical_phase = _normalize_team_phase_name(phase_entry.get("phase"))
            if not canonical_phase:
                continue
            phase_copy = dict(phase_entry)
            phase_copy["phase"] = canonical_phase
            phase_map[canonical_phase] = phase_copy
            if canonical_phase not in phase_order:
                phase_order.append(canonical_phase)
        phase_by_worker[worker_name] = phase_map
        phase_order_by_worker[worker_name] = phase_order

    if not phase_by_worker:
        payload["reason"] = "team dispatch has no worker phase entries"
        return payload

    requested_worker = str(worker or "").strip()
    requested_phase = _normalize_team_phase_name(phase)
    missing_workers = [str(x).strip() for x in (team_state.get("worker_summary_missing_workers") or []) if str(x).strip()]
    synthesis_workers = [str(x).strip() for x in (team_state.get("synthesis_workers") or []) if str(x).strip()]
    runtime_workers = [str(x).strip() for x in (team_state.get("team_runtime_verification_owners") or []) if str(x).strip()]
    doc_sync_workers = [str(x).strip() for x in (team_state.get("team_doc_sync_owners") or []) if str(x).strip()]
    document_critic_workers = [str(x).strip() for x in (team_state.get("team_document_critic_owners") or []) if str(x).strip()]

    current_worker = get_team_worker_name(
        known_workers=list(phase_by_worker.keys()),
        raw_agent_name=raw_agent_name,
        explicit_worker=explicit_worker,
    )

    selection_source = ""
    selection_reason = ""
    selected_worker = requested_worker
    if selected_worker:
        if selected_worker not in phase_by_worker:
            payload["reason"] = f"worker '{selected_worker}' is not present in the current dispatch pack"
            return payload
        selection_source = "requested_worker"
    elif current_worker and current_worker in phase_by_worker:
        selected_worker = current_worker
        selection_source = "current_worker"
    elif team_state.get("worker_summary_required") and not team_state.get("worker_summary_ready") and missing_workers:
        selected_worker = next((item for item in missing_workers if item in phase_by_worker), "")
        selection_source = "pending_worker_summary"
    elif not team_state.get("synthesis_ready") and synthesis_workers:
        selected_worker = next((item for item in synthesis_workers if item in phase_by_worker), "")
        selection_source = "synthesis_owner"
    elif (
        str(team_state.get("current_status") or "") == "degraded"
        and team_state.get("synthesis_ready")
        and not team_state.get("synthesis_refreshed_after_degraded")
        and synthesis_workers
    ):
        selected_worker = next((item for item in synthesis_workers if item in phase_by_worker), "")
        selection_source = "degraded_synthesis_owner"
    elif team_state.get("team_runtime_verification_needed") and runtime_workers:
        selected_worker = next((item for item in runtime_workers if item in phase_by_worker), "")
        selection_source = "runtime_owner"
    elif team_state.get("team_documentation_needed") and team_state.get("team_doc_sync_needed") and doc_sync_workers:
        selected_worker = next((item for item in doc_sync_workers if item in phase_by_worker), "")
        selection_source = "doc_sync_owner"
    elif team_state.get("team_documentation_needed") and (
        team_state.get("team_document_critic_missing_after_docs")
        or team_state.get("team_document_critic_stale_after_docs")
        or team_state.get("team_document_critic_pending")
    ) and document_critic_workers:
        selected_worker = next((item for item in document_critic_workers if item in phase_by_worker), "")
        selection_source = "document_critic_owner"
    elif team_state.get("handoff_refresh_needed") and synthesis_workers:
        selected_worker = next((item for item in synthesis_workers if item in phase_by_worker), "")
        selection_source = "handoff_owner"
    else:
        selected_worker = workers[0]
        selection_source = "first_worker"

    if not selected_worker:
        payload["reason"] = "could not determine a relaunch worker from the current dispatch pack"
        return payload

    phase_map = phase_by_worker.get(selected_worker) or {}
    phase_order = phase_order_by_worker.get(selected_worker) or []
    selected_phase = requested_phase
    if selected_phase and selected_phase != "auto":
        if selected_phase not in phase_map:
            payload["reason"] = f"worker '{selected_worker}' has no phase '{selected_phase}' in the current dispatch pack"
            return payload
        selection_reason = f"requested phase '{selected_phase}'"
    else:
        if selected_worker in missing_workers and "implement" in phase_map:
            selected_phase = "implement"
            selection_reason = "worker summary is still missing or incomplete"
        elif not team_state.get("synthesis_ready") and selected_worker in synthesis_workers and "synthesis" in phase_map:
            selected_phase = "synthesis"
            selection_reason = "TEAM_SYNTHESIS.md is still pending"
        elif (
            str(team_state.get("current_status") or "") == "degraded"
            and team_state.get("synthesis_ready")
            and not team_state.get("synthesis_refreshed_after_degraded")
            and selected_worker in synthesis_workers
            and "synthesis" in phase_map
        ):
            selected_phase = "synthesis"
            selection_reason = "TEAM_SYNTHESIS.md must be refreshed after the degraded team round"
        elif team_state.get("team_runtime_verification_needed") and selected_worker in runtime_workers and "final_runtime_verification" in phase_map:
            selected_phase = "final_runtime_verification"
            selection_reason = str(team_state.get("team_runtime_verification_reason") or "final runtime verification is still pending")
        elif team_state.get("team_documentation_needed") and team_state.get("team_doc_sync_needed") and selected_worker in doc_sync_workers and "documentation_sync" in phase_map:
            selected_phase = "documentation_sync"
            selection_reason = str(team_state.get("team_documentation_reason") or "documentation sync is still pending")
        elif team_state.get("team_documentation_needed") and (
            team_state.get("team_document_critic_missing_after_docs")
            or team_state.get("team_document_critic_stale_after_docs")
            or team_state.get("team_document_critic_pending")
        ) and selected_worker in document_critic_workers and "documentation_review" in phase_map:
            selected_phase = "documentation_review"
            selection_reason = str(team_state.get("team_documentation_reason") or "document review is still pending")
        elif team_state.get("handoff_refresh_needed") and selected_worker in synthesis_workers and "handoff_refresh" in phase_map:
            selected_phase = "handoff_refresh"
            selection_reason = str(team_state.get("handoff_refresh_reason") or "team handoff is stale")
        elif "implement" in phase_map:
            selected_phase = "implement"
            selection_reason = "default implementer rerun"
        elif phase_order:
            selected_phase = phase_order[0]
            selection_reason = f"defaulted to the first available phase '{selected_phase}'"
        else:
            payload["reason"] = f"worker '{selected_worker}' has no launchable phases"
            return payload

    phase_entry = dict(phase_map.get(selected_phase) or {})
    if not phase_entry:
        payload["reason"] = f"worker '{selected_worker}' phase '{selected_phase}' is missing from the dispatch pack"
        return payload

    payload.update({
        "available": True,
        "ready": True,
        "reason": "ready",
        "selection_source": selection_source,
        "selection_reason": selection_reason,
        "worker": selected_worker,
        "phase": selected_phase,
        "artifact": str(phase_entry.get("artifact") or ""),
        "prompt_file": str(phase_entry.get("prompt_file") or ""),
        "env_file": str(phase_entry.get("env_file") or ""),
        "run_script": str(phase_entry.get("run_script") or ""),
        "log_file": str(phase_entry.get("log_file") or ""),
        "session_name": str(phase_entry.get("session_name") or ""),
        "command_preview": str(phase_entry.get("command_preview") or ""),
    })
    return payload


def build_team_relaunch(task_dir, worker=None, phase="auto", write_files=False, execute=False, auto_refresh=True, raw_agent_name=None, explicit_worker=None):
    """Build or execute a worker/phase-specific relaunch manifest for team recovery."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    task_id = yaml_field("task_id", state_file) or os.path.basename(task_dir.rstrip("/"))
    provider = yaml_field("team_provider", state_file) or "none"
    task_root = os.path.join(TASK_DIR, task_id)
    repo_root = repo_root_for_task_dir(task_dir)
    relaunch_manifest_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "relaunch.json"))
    stdout_log_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "relaunch.stdout.log"))
    stderr_log_rel = normalize_path(os.path.join("team", "bootstrap", "provider", "relaunch.stderr.log"))

    def _task_rel(name):
        return f"{task_root}/{normalize_path(name)}"

    payload = {
        "task_id": task_id,
        "task_dir": task_dir,
        "provider": provider,
        "worker": "",
        "phase": "",
        "ready": False,
        "reason": "",
        "selection_reason": "",
        "selection_source": "",
        "auto_refresh": bool(auto_refresh),
        "bootstrap_refreshed": False,
        "dispatch_refreshed": False,
        "artifact": "",
        "prompt_file": "",
        "env_file": "",
        "run_script": "",
        "log_file": "",
        "session_name": "",
        "command_preview": "",
        "relaunch_manifest": _task_rel(relaunch_manifest_rel),
        "stdout_log": _task_rel(stdout_log_rel),
        "stderr_log": _task_rel(stderr_log_rel),
        "execute_supported": False,
        "execute_blocker": "",
        "generated_files": [],
        "execution": {},
    }

    if auto_refresh:
        team_state = team_artifact_status(task_dir)
        bootstrap_state = team_bootstrap_status(task_dir, team_state=team_state)
        if bootstrap_state.get("available") and (not bootstrap_state.get("generated") or bootstrap_state.get("refresh_needed")):
            build_team_bootstrap(task_dir, write_files=True)
            payload["bootstrap_refreshed"] = True
            team_state = team_artifact_status(task_dir)
        dispatch_state = team_dispatch_status(task_dir, team_state=team_state)
        if dispatch_state.get("available") and (not dispatch_state.get("generated") or dispatch_state.get("refresh_needed")):
            build_team_dispatch(task_dir, write_files=True)
            payload["dispatch_refreshed"] = True

    team_state = team_artifact_status(task_dir)
    selection = select_team_relaunch_target(
        task_dir,
        team_state=team_state,
        worker=worker,
        phase=phase,
        raw_agent_name=raw_agent_name,
        explicit_worker=explicit_worker,
    )
    payload.update({
        "worker": str(selection.get("worker") or ""),
        "phase": str(selection.get("phase") or ""),
        "selection_reason": str(selection.get("selection_reason") or ""),
        "selection_source": str(selection.get("selection_source") or ""),
        "artifact": str(selection.get("artifact") or ""),
        "prompt_file": str(selection.get("prompt_file") or ""),
        "env_file": str(selection.get("env_file") or ""),
        "run_script": str(selection.get("run_script") or ""),
        "log_file": str(selection.get("log_file") or ""),
        "session_name": str(selection.get("session_name") or ""),
        "command_preview": str(selection.get("command_preview") or ""),
    })
    if not selection.get("available"):
        payload["reason"] = str(selection.get("reason") or "team relaunch is not ready")
        return payload

    execute_blocker = ""
    run_script_rel = normalize_path(str(selection.get("run_script") or "").replace(f"{task_root}/", "", 1))
    if not run_script_rel:
        execute_blocker = "worker relaunch script is missing"
    elif shutil.which("claude") is None:
        execute_blocker = "claude CLI not found for worker phase relaunch"
    payload["execute_blocker"] = execute_blocker
    payload["execute_supported"] = not bool(execute_blocker)
    payload["ready"] = True
    payload["reason"] = "ready"

    manifest_payload = {
        "task_id": task_id,
        "provider": provider,
        "generated_at": now_iso(),
        "schema_version": TEAM_RELAUNCH_SCHEMA_VERSION,
        "worker": payload["worker"],
        "phase": payload["phase"],
        "selection_reason": payload["selection_reason"],
        "selection_source": payload["selection_source"],
        "artifact": payload["artifact"],
        "prompt_file": payload["prompt_file"],
        "env_file": payload["env_file"],
        "run_script": payload["run_script"],
        "log_file": payload["log_file"],
        "session_name": payload["session_name"],
        "command_preview": payload["command_preview"],
        "auto_refresh": bool(auto_refresh),
        "bootstrap_refreshed": bool(payload["bootstrap_refreshed"]),
        "dispatch_refreshed": bool(payload["dispatch_refreshed"]),
        "execute_supported": payload["execute_supported"],
        "execute_blocker": payload["execute_blocker"],
        "stdout_log": payload["stdout_log"],
        "stderr_log": payload["stderr_log"],
    }

    relaunch_manifest_abs = os.path.join(task_dir, relaunch_manifest_rel)
    stdout_log_abs = os.path.join(task_dir, stdout_log_rel)
    stderr_log_abs = os.path.join(task_dir, stderr_log_rel)
    if write_files or execute:
        os.makedirs(os.path.dirname(relaunch_manifest_abs), exist_ok=True)
        with open(relaunch_manifest_abs, "w", encoding="utf-8") as fh:
            json.dump(manifest_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        payload["generated_files"] = [_task_rel(relaunch_manifest_rel)]

    if execute:
        if execute_blocker:
            payload["execution"] = {
                "requested": True,
                "spawned": False,
                "error": execute_blocker,
            }
            return payload
        run_script_abs = os.path.join(task_dir, run_script_rel)
        os.makedirs(os.path.dirname(stdout_log_abs), exist_ok=True)
        with open(stdout_log_abs, "a", encoding="utf-8") as out_fh, open(stderr_log_abs, "a", encoding="utf-8") as err_fh:
            proc = subprocess.Popen(
                ["bash", run_script_abs],
                cwd=repo_root,
                stdout=out_fh,
                stderr=err_fh,
                start_new_session=True,
            )
        payload["execution"] = {
            "requested": True,
            "spawned": True,
            "mode": "detached",
            "pid": proc.pid,
            "started_at": now_iso(),
            "stdout_log": payload["stdout_log"],
            "stderr_log": payload["stderr_log"],
            "phase_log": payload["log_file"],
        }
        manifest_payload["execution"] = payload["execution"]
        with open(relaunch_manifest_abs, "w", encoding="utf-8") as fh:
            json.dump(manifest_payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        for relpath in (stdout_log_rel, stderr_log_rel):
            task_rel = _task_rel(relpath)
            if task_rel not in payload["generated_files"]:
                payload["generated_files"].append(task_rel)
    return payload

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
        return True

    current.append(violation)
    inline = ", ".join(f'"{v}"' for v in current)

    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    content = _yaml_replace_or_insert_line(content, "workflow_violations", f"[{inline}]")
    try:
        write_task_state_content(state_file, content, bump_revision=True)
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

    content = _yaml_replace_or_insert_line(content, count_field, new_count)
    content = _yaml_replace_or_insert_line(content, last_field, ts)
    try:
        write_task_state_content(state_file, content, bump_revision=True, timestamp=ts)
        return True
    except OSError:
        return False


def record_agent_run(task_dir, agent_name, *, observed_at=None, count_increment=1):
    """Record a durable agent run event in TASK_STATE.yaml.

    This is the explicit control-plane fallback when hook-based provenance
    tracking is unavailable. ``count_increment`` defaults to ``1`` and may be
    larger when importing an external execution log. ``observed_at`` overrides
    the ``*_last`` timestamp when the caller already knows when the run
    completed (for example, when reconciling from an artifact's meta sidecar).
    """
    try:
        steps = int(count_increment)
    except (TypeError, ValueError):
        steps = 1
    if steps < 1:
        steps = 1

    ok = True
    for _ in range(steps):
        ok = increment_agent_run(task_dir, agent_name) and ok
    if ok and observed_at:
        ok = set_task_state_field(task_dir, _agent_field_prefix(agent_name) + "_last", observed_at) and ok
    return ok


_AGENT_RUN_RECONCILIATION_ARTIFACTS = {
    "developer": {"artifact": "HANDOFF.md", "meta": "HANDOFF.meta.json", "roles": {"developer"}},
    "writer": {"artifact": "DOC_SYNC.md", "meta": "DOC_SYNC.meta.json", "roles": {"writer"}},
    "critic-plan": {"artifact": "CRITIC__plan.md", "meta": "CRITIC__plan.meta.json", "roles": {"critic-plan"}},
    "critic-runtime": {"artifact": "CRITIC__runtime.md", "meta": "CRITIC__runtime.meta.json", "roles": {"critic-runtime"}},
    "critic-document": {"artifact": "CRITIC__document.md", "meta": "CRITIC__document.meta.json", "roles": {"critic-document"}},
}


def _artifact_observed_at_iso(task_dir, artifact_name, meta_name, allowed_roles):
    artifact_path = os.path.join(task_dir, artifact_name)
    if not os.path.isfile(artifact_path):
        return None, "artifact_missing", ""

    meta_path = os.path.join(task_dir, meta_name)
    if os.path.isfile(meta_path):
        try:
            with open(meta_path, "r", encoding="utf-8") as fh:
                meta = json.load(fh)
            author_role = str(meta.get("author_role") or "").strip()
            written_at = str(meta.get("written_at") or "").strip()
            if author_role and allowed_roles and author_role not in allowed_roles:
                return None, "meta_role_mismatch", author_role
            if written_at:
                return written_at, "meta_written_at", author_role
            if author_role:
                return None, "meta_missing_written_at", author_role
        except (OSError, ValueError, json.JSONDecodeError):
            pass

    try:
        observed_at = datetime.fromtimestamp(os.path.getmtime(artifact_path), tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return None, "artifact_stat_error", ""
    return observed_at, "artifact_mtime", ""


def reconcile_agent_run_counts(task_dir, *, apply=True):
    """Backfill missing agent run counters from durable task artifacts.

    This keeps provenance event-driven while still providing a safe repair path
    when hook delivery is missed. Reconciliation is intentionally conservative:
    it only repairs agents whose count is currently ``0`` and only when a
    durable role-owned artifact exists. Existing non-zero counts are preserved.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    summary = {"task_dir": task_dir, "reconciled": [], "skipped": []}
    if not os.path.isfile(state_file):
        summary["skipped"].append({"reason": "missing_task_state"})
        return summary

    for agent_name, spec in _AGENT_RUN_RECONCILIATION_ARTIFACTS.items():
        current_count = get_agent_run_count(task_dir, agent_name)
        artifact_name = spec["artifact"]
        if current_count > 0:
            summary["skipped"].append({"agent": agent_name, "artifact": artifact_name, "reason": "already_recorded", "count": current_count})
            continue

        observed_at, source, role_hint = _artifact_observed_at_iso(task_dir, artifact_name, spec["meta"], set(spec.get("roles") or []))
        if not observed_at:
            summary["skipped"].append({"agent": agent_name, "artifact": artifact_name, "reason": source, "role_hint": role_hint})
            continue

        applied = False
        if apply:
            applied = record_agent_run(task_dir, agent_name, observed_at=observed_at, count_increment=1)

        summary["reconciled"].append({
            "agent": agent_name,
            "artifact": artifact_name,
            "observed_at": observed_at,
            "source": source,
            "count_before": current_count,
            "count_after": get_agent_run_count(task_dir, agent_name) if applied else max(current_count, 1),
            "applied": bool(applied),
        })

    return summary


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
    """Update a single field in TASK_STATE.yaml with schema/revision support."""
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return False
    try:
        with open(state_file, "r", encoding="utf-8") as fh:
            content = fh.read()
    except OSError:
        return False

    if isinstance(value, bool):
        yaml_val = "true" if value else "false"
    elif isinstance(value, list):
        inline = ", ".join(f'"{v}"' for v in value)
        yaml_val = f"[{inline}]"
    elif value is None:
        yaml_val = "null"
    else:
        yaml_val = str(value)

    content = _yaml_replace_or_insert_line(content, field, yaml_val)
    try:
        write_task_state_content(state_file, content, bump_revision=True)
        return True
    except OSError:
        return False


def merge_task_path_fields(task_dir, touched_paths=None, roots_touched=None, verification_targets=None):
    """Merge path-ledger fields into TASK_STATE.yaml.

    Returns a dict containing the merged values. Task-local artifact paths are
    ignored so workflow metadata writes do not pollute ownership tracking.
    """
    state_file = os.path.join(task_dir, "TASK_STATE.yaml")
    if not os.path.isfile(state_file):
        return {
            "touched_paths": [],
            "roots_touched": [],
            "verification_targets": [],
        }

    existing_touched = [p for p in yaml_array("touched_paths", state_file) if p]
    existing_roots = [p for p in yaml_array("roots_touched", state_file) if p]
    existing_vt = [p for p in yaml_array("verification_targets", state_file) if p]

    incoming_touched = [repo_relpath(p) for p in (touched_paths or []) if repo_relpath(p) and not is_task_artifact_path(p)]
    incoming_roots = [normalize_path(p) for p in (roots_touched or []) if normalize_path(p)]
    incoming_vt = [repo_relpath(p) for p in (verification_targets or []) if repo_relpath(p) and not is_task_artifact_path(p)]

    merged_touched = list(dict.fromkeys(existing_touched + incoming_touched))
    merged_roots = list(dict.fromkeys(existing_roots + incoming_roots + extract_roots(incoming_touched + incoming_vt)))
    merged_vt = list(dict.fromkeys(existing_vt + incoming_vt))

    set_task_state_field(task_dir, "touched_paths", merged_touched)
    set_task_state_field(task_dir, "roots_touched", merged_roots)
    set_task_state_field(task_dir, "verification_targets", merged_vt)
    return {
        "touched_paths": merged_touched,
        "roots_touched": merged_roots,
        "verification_targets": merged_vt,
    }


def compile_routing(task_dir, request_text=""):
    """Compute routing fields from TASK_STATE.yaml + request text heuristics.

    Returns canonical routing fields plus compatibility orchestration fields.

    The policy is deliberately team-first but still conservative about safety:
      - solo is reserved for clearly small / low-overhead tasks
      - team is preferred for broad-build, multi-surface, and maintenance work
      - when team ownership or provider readiness is unclear, fall back to
        subagents rather than collapsing to solo
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
    risk_tag_set = set(risk_tags)
    planning_mode = infer_planning_mode(task_dir, request_text=request_text)

    def _flag(value):
        return str(value or "").strip().lower() == "true"

    def _contains_any(text_value, needles):
        return any(needle in text_value for needle in needles)

    def _surface_hits(text_value):
        groups = {
            "frontend": ("frontend", "client", "ui", "page", "component", "react", "vue"),
            "backend": ("backend", "api", "server", "route", "service", "endpoint"),
            "tests": ("test", "tests", "pytest", "playwright", "spec"),
            "docs": ("readme", "docs", "documentation", "doc_sync", "doc sync"),
            "infra": ("docker", "workflow", "ci", "build", "deploy", "migration", "schema", "database", "db"),
            "harness": ("harness", "plugin", "hook", "mcp", "template", "prompt_memory", "prewrite"),
        }
        hits = []
        for name, keywords in groups.items():
            if _contains_any(text_value, keywords):
                hits.append(name)
        return hits

    def _preferred_team_provider():
        provider_pref = str(manifest_teams_field("provider") or "auto").strip().lower() or "auto"
        native_ready = bool(native_agent_teams_runtime_probe().get("ready"))
        omc_ready = bool(omc_runtime_probe().get("ready"))

        if provider_pref == "none":
            return "none", False
        if provider_pref == "native":
            return "native", native_ready
        if provider_pref == "omc":
            return "omc", omc_ready
        if native_ready:
            return "native", True
        if omc_ready:
            return "omc", True
        return "none", False

    # --- maintenance_task ---
    HIGH_MAINTENANCE_TAGS = {"maintenance-task", "harness-source"}
    maintenance_task = False
    if HIGH_MAINTENANCE_TAGS.intersection(risk_tag_set):
        maintenance_task = True
    elif lane in ("refactor", "build") and "template-sync-required" in risk_tag_set:
        maintenance_task = True

    MAINTENANCE_KEYWORDS = {
        "harness", "plugin", "workflow", "hook", "hctl", "setup", "template",
        "control surface", "prewrite", "session_context", "prompt_memory",
    }
    if not maintenance_task and _contains_any(req, MAINTENANCE_KEYWORDS):
        if _contains_any(req, ("claude.md", "plugin/", "hooks.json", "execution-modes")):
            maintenance_task = True

    # --- risk_level ---
    LOW_LANES = {"docs-sync", "answer", "investigate"}
    HIGH_RISK_TAGS = {"multi-root", "destructive", "structural", "harness-source",
                      "maintenance-task", "template-sync-required"}

    if lane in LOW_LANES:
        risk_level = "low"
    elif (
        HIGH_RISK_TAGS.intersection(risk_tag_set)
        or (browser_required == "true" and fail_count >= 2)
        or maintenance_task
    ):
        risk_level = "high"
    else:
        risk_level = "medium"

    HIGH_RISK_KEYWORDS = {
        "setup", "template", "control surface", "harness-source", "hooks.json",
        "execution-modes", "orchestration-modes", "prewrite_gate", "hctl",
    }
    if risk_level != "high" and _contains_any(req, HIGH_RISK_KEYWORDS):
        risk_level = "high"

    # --- workflow_locked ---
    workflow_locked = not maintenance_task

    # --- execution / orchestration ---
    EXEC_MODE_MAP = {"low": "light", "medium": "standard", "high": "sprinted"}
    execution_mode = EXEC_MODE_MAP.get(risk_level, "standard")

    trivial_keywords = {
        "typo", "rename", "comment", "readme", "docs only", "doc-only", "one-line",
        "one line", "single-file", "single file", "one file", "small bugfix", "minor fix",
        "tiny fix", "small task",
    }
    sequential_keywords = {
        "sequential", "step by step", "serial", "same file", "single file", "one file",
    }
    surface_hits = _surface_hits(req)
    multi_surface = len(surface_hits) >= 2
    small_task = lane in LOW_LANES or _contains_any(req, trivial_keywords)
    sequential_or_conflict = _contains_any(req, sequential_keywords)

    team_reason = ""
    team_preferred = False
    if not small_task and not sequential_or_conflict:
        if planning_mode == "broad-build":
            team_preferred = True
            team_reason = "team-preferred: broad-build scope"
        elif maintenance_task:
            team_preferred = True
            team_reason = "team-preferred: maintenance / harness work"
        elif HIGH_RISK_TAGS.intersection(risk_tag_set):
            team_preferred = True
            team_reason = "team-preferred: structural or multi-root risk"
        elif multi_surface:
            team_preferred = True
            team_reason = "team-preferred: multi-surface request"
        elif browser_required == "true" and lane in ("build", "debug", "verify"):
            team_preferred = True
            team_reason = "team-preferred: browser-backed implementation + verification"

    fallback_mode = str(manifest_teams_field("fallback") or "subagents").strip().lower() or "subagents"
    if fallback_mode not in ("subagents", "solo"):
        fallback_mode = "subagents"
    try:
        configured_team_size = int(str(manifest_teams_field("default_size") or "3"))
    except ValueError:
        configured_team_size = 3
    configured_team_size = max(2, configured_team_size)

    parallelism = 1  # compatibility placeholder; orchestration_mode drives routing
    team_provider = "none"
    team_status = "skipped"
    team_size = 0
    team_plan_required = False
    team_synthesis_required = False
    fallback_used = "none"

    if small_task:
        orchestration_mode = "solo"
        team_reason = "solo: small / low-overhead task"
        team_status = "skipped"
    elif team_preferred:
        provider, ready = _preferred_team_provider()
        if ready and provider in ("native", "omc"):
            orchestration_mode = "team"
            team_provider = provider
            team_status = "planned"
            team_size = configured_team_size
            team_plan_required = True
            team_synthesis_required = True
        else:
            orchestration_mode = fallback_mode
            team_provider = f"fallback-{fallback_mode}"
            team_status = "fallback"
            fallback_used = fallback_mode
            if not team_reason:
                team_reason = "fallback: team-preferred task but no ready provider"
            else:
                team_reason = f"{team_reason}; fallback: no ready provider"
    else:
        orchestration_mode = "subagents"
        team_reason = "subagents: non-trivial task but disjoint ownership unclear"
        team_status = "skipped"

    # --- orchestration_mode lock ---
    # If routing was already compiled and orchestration_mode was set,
    # preserve the existing value unless force_orchestration_mode is set.
    orchestration_mode_locked = False
    existing_compiled = yaml_field("routing_compiled", state_file)
    if str(existing_compiled).lower() == "true":
        existing_orch = yaml_field("orchestration_mode", state_file) or ""
        force_orch = yaml_field("force_orchestration_mode", state_file)
        if existing_orch and str(force_orch).lower() != "true":
            orchestration_mode = existing_orch
            orchestration_mode_locked = True

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
        "orchestration_mode_locked": orchestration_mode_locked,
        "team_provider": team_provider,
        "team_status": team_status,
        "team_size": team_size,
        "team_reason": team_reason,
        "team_plan_required": team_plan_required,
        "team_synthesis_required": team_synthesis_required,
        "fallback_used": fallback_used,
    }


def emit_compact_context(task_dir, raw_agent_name=None, explicit_worker=None):
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
    qa_required = _bool(yaml_field("qa_required", state_file) or "false")
    doc_sync_required = _bool(yaml_field("doc_sync_required", state_file) or "false")
    browser_required = _bool(yaml_field("browser_required", state_file) or "false")
    parallelism = _int(yaml_field("parallelism", state_file) or "1")
    workflow_locked = _bool(yaml_field("workflow_locked", state_file) or "true")
    maintenance_task = _bool(yaml_field("maintenance_task", state_file) or "false")

    execution_mode = yaml_field("execution_mode", state_file) or "standard"
    orchestration_mode = yaml_field("orchestration_mode", state_file) or "solo"
    is_team_mode = orchestration_mode == "team"
    team_provider = yaml_field("team_provider", state_file) or "none"
    team_status = yaml_field("team_status", state_file) or "n/a"
    team_size = _int(yaml_field("team_size", state_file) or "0")
    team_reason = yaml_field("team_reason", state_file) or ""
    team_plan_required = _bool(yaml_field("team_plan_required", state_file) or "false")
    team_synthesis_required = _bool(yaml_field("team_synthesis_required", state_file) or "false")
    fallback_used = yaml_field("fallback_used", state_file) or "none"
    planning_mode = get_planning_mode(state_file)
    runtime_fail_count = _int(yaml_field("runtime_verdict_fail_count", state_file) or "0")
    team_artifacts = _team_artifact_skip_state(task_dir, orchestration_mode, team_status, fallback_used)
    current_team_worker = ""
    current_worker_owned_paths = []
    current_worker_summary = {}
    current_worker_relpath = ""
    current_worker_pending = False
    current_agent_role = get_agent_role(raw_agent_name)
    team_synthesis_workers = []
    team_summary_workers = []
    team_doc_sync_workers = []
    team_document_critic_workers = []
    current_worker_is_synthesis_owner = False
    current_worker_is_doc_sync_owner = False
    current_worker_is_document_critic_owner = False
    team_plan_name = "TEAM_PLAN.md"
    team_synthesis_name = "TEAM_SYNTHESIS.md"
    team_bootstrap = {}
    team_bootstrap_dir_name = normalize_path(os.path.join("team", "bootstrap"))
    team_bootstrap_index_name = normalize_path(os.path.join(team_bootstrap_dir_name, "index.json"))
    team_bootstrap_ready = False
    team_bootstrap_generated = False
    team_bootstrap_stale = False
    team_bootstrap_refresh_needed = False
    team_bootstrap_reason = ""
    team_dispatch = {}
    team_dispatch_dir_name = normalize_path(os.path.join("team", "bootstrap", "provider"))
    team_dispatch_index_name = normalize_path(os.path.join(team_dispatch_dir_name, "dispatch.json"))
    team_dispatch_available = False
    team_dispatch_generated = False
    team_dispatch_stale = False
    team_dispatch_refresh_needed = False
    team_dispatch_reason = ""
    team_launch = {}
    team_launch_manifest_name = normalize_path(os.path.join("team", "bootstrap", "provider", "launch.json"))
    team_launch_available = False
    team_launch_generated = False
    team_launch_stale = False
    team_launch_refresh_needed = False
    team_launch_reason = ""
    team_relaunch = {}

    if is_team_mode:
        team_artifacts = team_artifact_status(task_dir)
        team_status = team_artifacts.get("derived_status", team_status)
        current_team_worker = get_team_worker_name(
            team_artifacts.get("plan_workers") or [],
            raw_agent_name=raw_agent_name,
            explicit_worker=explicit_worker,
        )
        current_worker_owned_paths = list((team_artifacts.get("plan_owned_paths") or {}).get(current_team_worker, []) or [])
        current_worker_summary = dict((team_artifacts.get("worker_summary_per_worker") or {}).get(current_team_worker) or {})
        current_worker_relpath = team_worker_summary_relpath(current_team_worker) if current_team_worker else ""
        current_worker_pending = bool(
            current_team_worker
            and (
                current_team_worker in (team_artifacts.get("worker_summary_missing_workers") or [])
                or current_worker_summary.get("status") == "incomplete"
            )
        )
        team_synthesis_workers = list(team_artifacts.get("synthesis_workers") or [])
        team_summary_workers = list(team_artifacts.get("summary_workers") or [])
        team_doc_sync_workers = list(team_artifacts.get("team_doc_sync_owners") or [])
        team_document_critic_workers = list(team_artifacts.get("team_document_critic_owners") or [])
        current_worker_is_synthesis_owner = bool(
            current_team_worker and current_team_worker in team_synthesis_workers
        )
        current_worker_is_doc_sync_owner = bool(
            current_team_worker and current_team_worker in team_doc_sync_workers
        )
        current_worker_is_document_critic_owner = bool(
            current_team_worker and current_team_worker in team_document_critic_workers
        )
        team_bootstrap = team_bootstrap_status(task_dir, team_state=team_artifacts)
        team_bootstrap_dir_name = normalize_path(str(team_bootstrap.get("bootstrap_dir") or os.path.join("team", "bootstrap")))
        team_bootstrap_index_name = normalize_path(str(team_bootstrap.get("bootstrap_index") or os.path.join(team_bootstrap_dir_name, "index.json")))
        team_bootstrap_ready = bool(team_bootstrap.get("available"))
        team_bootstrap_generated = bool(team_bootstrap.get("generated"))
        team_bootstrap_stale = bool(team_bootstrap.get("stale"))
        team_bootstrap_refresh_needed = bool(team_bootstrap.get("refresh_needed"))
        team_bootstrap_reason = str(team_bootstrap.get("reason") or "")
        team_dispatch = team_dispatch_status(task_dir, team_state=team_artifacts)
        team_dispatch_dir_name = normalize_path(str(team_dispatch.get("dispatch_dir") or os.path.join("team", "bootstrap", "provider")))
        team_dispatch_index_name = normalize_path(str(team_dispatch.get("dispatch_index") or os.path.join(team_dispatch_dir_name, "dispatch.json")))
        team_dispatch_available = bool(team_dispatch.get("available"))
        team_dispatch_generated = bool(team_dispatch.get("generated"))
        team_dispatch_stale = bool(team_dispatch.get("stale"))
        team_dispatch_refresh_needed = bool(team_dispatch.get("refresh_needed"))
        team_dispatch_reason = str(team_dispatch.get("reason") or "")
        team_launch = team_launch_status(task_dir, team_state=team_artifacts)
        team_launch_manifest_name = normalize_path(str(team_launch.get("launch_manifest") or os.path.join("team", "bootstrap", "provider", "launch.json")))
        team_launch_available = bool(team_launch.get("available"))
        team_launch_generated = bool(team_launch.get("generated"))
        team_launch_stale = bool(team_launch.get("stale"))
        team_launch_refresh_needed = bool(team_launch.get("refresh_needed"))
        team_launch_reason = str(team_launch.get("reason") or "")
        team_relaunch = select_team_relaunch_target(
            task_dir,
            team_state=team_artifacts,
            raw_agent_name=raw_agent_name,
            explicit_worker=explicit_worker,
        )

    task_root = os.path.join(TASK_DIR, task_id)
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
                        "status": "planned",
                        "title": "",
                    }
                    continue
                m_st = re.match(r"^\s+status\s*:\s*(.+)", line)
                if m_st and current.get("id"):
                    current["status"] = normalize_check_status_value(m_st.group(1))
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
        c.get("title", "")[:56]
        for c in check_items
        if c.get("status") not in ("passed", "skipped") and c.get("title")
    ][:2]

    checks = {
        "total": len(check_items),
        "open_ids": open_ids[:5],
        "failed_ids": failed_ids[:5],
        "blocked_ids": blocked_ids[:4],
        "candidate_ids": candidate_ids[:4],
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
    runtime_freshness = verdict_freshness(state_file, "runtime_verdict")
    document_freshness = verdict_freshness(state_file, "document_verdict")
    intent_verdict = (yaml_field("intent_verdict", state_file) or "pending").upper()
    intent_fix_round = intent_verdict == "FAIL"

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
    runtime_fix_round = (
        runtime_verdict == "FAIL"
        or runtime_critic_verdict == "FAIL"
        or (runtime_verdict == "PASS" and runtime_freshness != "current")
    )
    blocked_env_round = status == "blocked_env" or runtime_verdict == "BLOCKED_ENV" or runtime_critic_verdict == "BLOCKED_ENV"
    document_fix_round = document_critic_verdict == "FAIL" or (
        (yaml_field("document_verdict", state_file) or "pending").upper() == "PASS"
        and document_freshness != "current"
    )

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
    if team_synthesis_workers:
        review_focus["team_synthesis_workers"] = team_synthesis_workers[:4]
    if current_team_worker:
        review_focus["team_current_worker"] = current_team_worker
        if current_agent_role:
            review_focus["team_current_agent_role"] = current_agent_role
        review_focus["team_current_worker_is_synthesis_owner"] = bool(current_worker_is_synthesis_owner)
        review_focus["team_current_worker_is_doc_sync_owner"] = bool(current_worker_is_doc_sync_owner)
        review_focus["team_current_worker_is_document_critic_owner"] = bool(current_worker_is_document_critic_owner)
        if current_worker_owned_paths:
            review_focus["team_current_worker_owned_paths"] = current_worker_owned_paths[:4]
        if current_worker_relpath:
            review_focus["team_current_worker_artifact"] = _task_rel(current_worker_relpath)
        if current_worker_summary.get("verification_excerpt"):
            review_focus["team_current_worker_verification"] = str(current_worker_summary.get("verification_excerpt"))
    if team_artifacts.get("team_runtime_verification_needed"):
        review_focus["team_final_verification_needed"] = True
        if team_artifacts.get("team_runtime_verification_reason"):
            review_focus["team_final_verification_reason"] = str(team_artifacts.get("team_runtime_verification_reason"))
        if team_artifacts.get("team_runtime_verification_owners"):
            review_focus["team_final_verification_owners"] = list(team_artifacts.get("team_runtime_verification_owners") or [])[:4]
        runtime_artifact_name = str(team_artifacts.get("team_runtime_artifact") or "")
        if runtime_artifact_name:
            review_focus["team_final_verification_artifact"] = _task_rel(runtime_artifact_name)
    if team_artifacts.get("team_documentation_needed"):
        review_focus["team_documentation_needed"] = True
        if team_artifacts.get("team_documentation_reason"):
            review_focus["team_documentation_reason"] = str(team_artifacts.get("team_documentation_reason"))
        if team_artifacts.get("team_doc_sync_owners"):
            review_focus["team_doc_sync_owners"] = list(team_artifacts.get("team_doc_sync_owners") or [])[:4]
        if team_artifacts.get("team_doc_sync_artifact"):
            review_focus["team_doc_sync_artifact"] = _task_rel(team_artifacts.get("team_doc_sync_artifact") or doc_sync_name)
        if team_artifacts.get("team_document_critic_owners"):
            review_focus["team_document_critic_owners"] = list(team_artifacts.get("team_document_critic_owners") or [])[:4]
        if team_artifacts.get("team_document_critic_artifact"):
            review_focus["team_document_critic_artifact"] = _task_rel(team_artifacts.get("team_document_critic_artifact") or document_critic_name)
        review_focus["team_document_critic_needed"] = bool(team_artifacts.get("team_document_critic_needed"))

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
            team_recovery = handoff_data.get("team_recovery")
            if isinstance(team_recovery, dict):
                phase = str(team_recovery.get("phase") or "")
                if phase:
                    review_focus["team_recovery_phase"] = phase
                pending_workers = [
                    str(item).strip()
                    for item in (team_recovery.get("pending_workers") or [])
                    if str(item).strip()
                ]
                if pending_workers:
                    review_focus["team_pending_workers"] = pending_workers[:4]
                pending_artifacts = [
                    _task_rel(str(item).strip())
                    for item in (team_recovery.get("pending_artifacts") or [])
                    if str(item).strip()
                ]
                if pending_artifacts:
                    review_focus["team_pending_artifacts"] = pending_artifacts[:4]
                if team_recovery.get("handoff_refresh_needed"):
                    review_focus["team_handoff_refresh_needed"] = True
                    reason = str(team_recovery.get("handoff_refresh_reason") or "")
                    if reason:
                        review_focus["team_handoff_refresh_reason"] = reason
                if team_recovery.get("documentation_needed"):
                    review_focus["team_documentation_needed"] = True
                    documentation_reason = str(team_recovery.get("documentation_reason") or "")
                    if documentation_reason:
                        review_focus["team_documentation_reason"] = documentation_reason
                    doc_sync_owners = [
                        str(item).strip()
                        for item in (team_recovery.get("doc_sync_owners") or [])
                        if str(item).strip()
                    ]
                    if doc_sync_owners:
                        review_focus["team_doc_sync_owners"] = doc_sync_owners[:4]
                    doc_sync_artifact = str(team_recovery.get("doc_sync_artifact") or "").strip()
                    if doc_sync_artifact:
                        review_focus["team_doc_sync_artifact"] = _task_rel(doc_sync_artifact)
                    document_critic_owners = [
                        str(item).strip()
                        for item in (team_recovery.get("document_critic_owners") or [])
                        if str(item).strip()
                    ]
                    if document_critic_owners:
                        review_focus["team_document_critic_owners"] = document_critic_owners[:4]
                    document_artifact = str(team_recovery.get("document_critic_artifact") or "").strip()
                    if document_artifact:
                        review_focus["team_document_critic_artifact"] = _task_rel(document_artifact)
                    review_focus["team_document_critic_needed"] = bool(team_recovery.get("document_critic_needed"))
                if team_recovery.get("verification_needed"):
                    review_focus["team_final_verification_needed"] = True
                    verification_reason = str(team_recovery.get("verification_reason") or "")
                    if verification_reason:
                        review_focus["team_final_verification_reason"] = verification_reason
                    verification_owners = [
                        str(item).strip()
                        for item in (team_recovery.get("verification_owners") or [])
                        if str(item).strip()
                    ]
                    if verification_owners:
                        review_focus["team_final_verification_owners"] = verification_owners[:4]
                    runtime_artifact = str(team_recovery.get("runtime_artifact") or "").strip()
                    if runtime_artifact:
                        review_focus["team_final_verification_artifact"] = _task_rel(runtime_artifact)
                if current_team_worker:
                    workers = team_recovery.get("workers") or {}
                    current_worker_recovery = workers.get(current_team_worker) if isinstance(workers, dict) else None
                    if isinstance(current_worker_recovery, dict):
                        review_focus["team_current_worker_pending"] = bool(current_worker_recovery.get("pending"))
                        if current_worker_recovery.get("artifact"):
                            review_focus["team_current_worker_artifact"] = _task_rel(str(current_worker_recovery.get("artifact")))
                        if current_worker_recovery.get("owned_writable_paths"):
                            review_focus["team_current_worker_owned_paths"] = list(current_worker_recovery.get("owned_writable_paths") or [])[:4]
                        if current_worker_recovery.get("verification_excerpt"):
                            review_focus["team_current_worker_verification"] = str(current_worker_recovery.get("verification_excerpt"))
                        if current_worker_recovery.get("residual_risks_excerpt"):
                            review_focus["team_current_worker_risks"] = str(current_worker_recovery.get("residual_risks_excerpt"))
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
    elif orchestration_mode == "team" and team_plan_required and not team_artifacts.get("plan_ready"):
        priority_must_read.extend([team_plan_name, "TASK_STATE.yaml", request_name, "PLAN.md", "CHECKS.yaml"])
    elif (
        orchestration_mode == "team"
        and team_artifacts.get("worker_summary_required")
        and not team_artifacts.get("worker_summary_ready")
    ):
        worker_artifacts = list(team_artifacts.get("worker_summary_artifacts") or [])
        prioritized_worker_artifacts = []
        if current_worker_relpath:
            prioritized_worker_artifacts.append(current_worker_relpath)
        prioritized_worker_artifacts.extend(
            [item for item in worker_artifacts if item != current_worker_relpath][:2]
        )
        priority_must_read.extend(
            [
                team_plan_name,
                *prioritized_worker_artifacts,
                "TASK_STATE.yaml",
                request_name,
                "CHECKS.yaml",
            ]
        )
    elif (
        orchestration_mode == "team"
        and team_synthesis_required
        and team_artifacts.get("plan_ready")
        and team_status in ("running", "degraded")
    ):
        priority_must_read.extend([
            team_plan_name,
            current_worker_relpath,
            team_synthesis_name,
            "TASK_STATE.yaml",
            request_name,
            "CHECKS.yaml",
        ])
    elif (
        orchestration_mode == "team"
        and team_synthesis_required
        and team_artifacts.get("synthesis_ready")
        and team_status == "degraded"
        and not team_artifacts.get("synthesis_refreshed_after_degraded")
    ):
        priority_must_read.extend([
            team_plan_name,
            team_synthesis_name,
            "TASK_STATE.yaml",
            request_name,
            "CHECKS.yaml",
        ])
    elif orchestration_mode == "team" and team_artifacts.get("team_runtime_verification_needed"):
        priority_must_read.extend([
            team_plan_name,
            team_synthesis_name,
            team_artifacts.get("team_runtime_artifact") or runtime_critic_name,
            team_artifacts.get("team_doc_sync_artifact") or doc_sync_name,
            team_artifacts.get("team_document_critic_artifact") or document_critic_name,
            "TASK_STATE.yaml",
            "CHECKS.yaml",
            "HANDOFF.md",
        ])
    elif orchestration_mode == "team" and team_artifacts.get("team_documentation_needed"):
        priority_must_read.extend([
            team_plan_name,
            team_synthesis_name,
            team_artifacts.get("team_runtime_artifact") or runtime_critic_name,
            team_artifacts.get("team_doc_sync_artifact") or doc_sync_name,
            team_artifacts.get("team_document_critic_artifact") or document_critic_name,
            "TASK_STATE.yaml",
            "CHECKS.yaml",
            "HANDOFF.md",
        ])
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
    if orchestration_mode == "team" and team_plan_required and not team_artifacts.get("plan_ready"):
        notes.append("team plan pending")
    elif (
        orchestration_mode == "team"
        and team_artifacts.get("worker_summary_required")
        and not team_artifacts.get("worker_summary_ready")
    ):
        notes.append("worker summaries pending")
    elif (
        orchestration_mode == "team"
        and team_synthesis_required
        and team_artifacts.get("plan_ready")
        and team_status in ("running", "degraded")
        and not team_artifacts.get("synthesis_ready")
    ):
        notes.append("team synthesis pending")
    elif orchestration_mode == "team" and team_artifacts.get("team_runtime_verification_needed"):
        notes.append("team final verification pending")
    elif orchestration_mode == "team" and team_artifacts.get("team_documentation_needed"):
        notes.append("team documentation pending")
    if orchestration_mode == "team" and team_artifacts.get("handoff_refresh_needed") and not team_artifacts.get("team_documentation_needed"):
        notes.append("team handoff stale")
    if current_team_worker:
        notes.append(f"worker={current_team_worker}")
    if blocked_env_round:
        notes.append("blocked env: read environment snapshot")
    elif runtime_fix_round:
        notes.append("runtime fix round: evidence-first")
    elif intent_fix_round:
        notes.append("intent fix round: replan required")
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
    elif orchestration_mode == "team" and team_plan_required and not team_artifacts.get("plan_ready"):
        next_action = "Complete TEAM_PLAN.md first — assign worker ownership, writable paths, and synthesis rules before spawning workers or mutating source files."
    elif (
        orchestration_mode == "team"
        and team_artifacts.get("worker_summary_required")
        and not team_artifacts.get("worker_summary_ready")
    ):
        synthesis_preview = ", ".join(team_synthesis_workers[:3]) or "the synthesis owner"
        if current_team_worker and current_worker_pending:
            owned_preview = ", ".join(current_worker_owned_paths[:3]) or "your owned writable paths"
            artifact_name = current_worker_relpath or "team/worker-<name>.md"
            next_action = (
                f"As {current_team_worker}, finish or verify {owned_preview}, then update {artifact_name} with completed work, "
                f"owned paths handled, verification, and residual risks before {synthesis_preview} refreshes TEAM_SYNTHESIS.md."
            )
        elif current_team_worker and current_worker_is_synthesis_owner:
            missing_workers = list(team_artifacts.get("worker_summary_missing_workers") or [])
            pending_preview = ", ".join(missing_workers[:3]) or "the remaining workers"
            next_action = (
                f"As synthesis owner {current_team_worker}, wait for or unblock {pending_preview}, then refresh TEAM_SYNTHESIS.md once the last worker summaries land."
            )
        elif current_team_worker:
            next_action = (
                f"Your summary for {current_team_worker} is already present — help unblock the remaining workers, "
                f"then hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
            )
        else:
            if team_bootstrap_ready and team_bootstrap_generated and team_bootstrap_refresh_needed:
                next_action = (
                    "TEAM_PLAN.md or team ownership changed since the last bootstrap — rerun mcp__plugin_harness_harness__team_bootstrap to refresh worker briefs and env snippets "
                    f"before further fan-out. Reason: {team_bootstrap_reason or 'bootstrap is stale'}."
                )
            elif team_dispatch_available and team_dispatch_generated and team_dispatch_refresh_needed:
                next_action = (
                    "The provider dispatch pack is stale — rerun mcp__plugin_harness_harness__team_dispatch to refresh launch prompts, run scripts, "
                    f"and provider helpers before further fan-out. Reason: {team_dispatch_reason or 'dispatch is stale'}."
                )
            elif team_bootstrap_ready and not team_bootstrap_generated:
                next_action = (
                    "Generate worker briefs with mcp__plugin_harness_harness__team_bootstrap first, then fan out contributors — each planned worker should use its bootstrap brief, "
                    "implement only its owned writable paths, and leave worker summaries under team/worker-<name>.md before "
                    f"{synthesis_preview} refreshes TEAM_SYNTHESIS.md."
                )
            elif team_dispatch_available and not team_dispatch_generated:
                next_action = (
                    "Generate the provider dispatch pack with mcp__plugin_harness_harness__team_dispatch after bootstrap — it will freeze the launch prompt, per-phase worker prompts, "
                    f"and run helpers before contributors start work and {synthesis_preview} later refreshes TEAM_SYNTHESIS.md."
                )
            elif team_launch_available and team_launch_generated and team_launch_refresh_needed:
                if team_launch.get("execute_fallback_available"):
                    next_action = (
                        "The default team launch plan is stale — rerun mcp__plugin_harness_harness__team_launch with write_files=true to refresh the launch manifest, native lead prompt, "
                        f"and implementer fallback before fan-out. Reason: {team_launch_reason or 'team launch is stale'}."
                    )
                else:
                    next_action = (
                        "The default team launch plan is stale — rerun mcp__plugin_harness_harness__team_launch with write_files=true to refresh the launch manifest "
                        f"before fan-out. Reason: {team_launch_reason or 'team launch is stale'}."
                    )
            elif team_launch_available and not team_launch_generated:
                if team_launch.get("interactive_required") and team_launch.get("execute_fallback_available"):
                    next_action = (
                        "Prepare the default fan-out entrypoint with mcp__plugin_harness_harness__team_launch(write_files=true) — it will auto-refresh stale bootstrap / dispatch artifacts, "
                        f"materialize the native lead prompt plus the implementer fallback, and hand contributors the current owned-path prompts before {synthesis_preview} later refreshes TEAM_SYNTHESIS.md."
                    )
                elif team_launch.get("interactive_required"):
                    next_action = (
                        "Prepare the default fan-out entrypoint with mcp__plugin_harness_harness__team_launch(write_files=true) — it will auto-refresh stale bootstrap / dispatch artifacts, "
                        f"materialize the native lead prompt, and hand contributors the current owned-path prompts before {synthesis_preview} later refreshes TEAM_SYNTHESIS.md."
                    )
                else:
                    next_action = (
                        "Prepare the default fan-out entrypoint with mcp__plugin_harness_harness__team_launch(write_files=true) — it will auto-refresh stale bootstrap / dispatch artifacts, "
                        f"materialize the provider launch plan, and hand contributors the current owned-path prompts before {synthesis_preview} later refreshes TEAM_SYNTHESIS.md."
                    )
            elif team_launch_available and team_launch_generated:
                if team_launch.get("interactive_required") and team_launch.get("execute_fallback_available"):
                    next_action = (
                        "Use the current team launch plan to fan out contributors — paste the frozen native provider prompt into the lead session, or run team_launch --execute to use the implementer fallback. "
                        f"Keep each worker inside owned writable paths, update team/worker-<name>.md, and hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                    )
                elif team_launch.get("interactive_required"):
                    next_action = (
                        "Use the current team launch plan to fan out contributors — paste the frozen native provider prompt into the lead session, keep each worker inside owned writable paths, "
                        f"update team/worker-<name>.md, and hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                    )
                else:
                    next_action = (
                        "Use the current team launch plan to fan out contributors — it already captures the provider launcher, implementer dispatcher, and current bootstrap state. "
                        f"Keep each worker inside owned writable paths, update team/worker-<name>.md, and hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                    )
            elif team_bootstrap_ready and team_bootstrap_generated:
                next_action = (
                    "Use the current team/bootstrap briefs and provider dispatch helpers for fan-out — each planned worker should stay inside owned writable paths, "
                    f"update team/worker-<name>.md, and hand off to {synthesis_preview} for TEAM_SYNTHESIS.md."
                )
            else:
                next_action = (
                    "Collect per-worker summaries under team/worker-<name>.md first — each planned contributor should record completed work, "
                    f"owned paths handled, verification, and residual risks before {synthesis_preview} writes TEAM_SYNTHESIS.md."
                )
    elif (
        orchestration_mode == "team"
        and team_synthesis_required
        and team_artifacts.get("plan_ready")
        and team_status in ("running", "degraded")
        and not team_artifacts.get("synthesis_ready")
    ):
        if current_team_worker and current_worker_is_synthesis_owner:
            next_action = "As the synthesis owner, write TEAM_SYNTHESIS.md with integrated result, cross-checks, and verification summary before running task_close."
        elif team_synthesis_workers:
            next_action = (
                "Worker summaries are ready — hand off to "
                + ", ".join(team_synthesis_workers[:3])
                + " to refresh TEAM_SYNTHESIS.md before task_close."
            )
        else:
            next_action = "Write TEAM_SYNTHESIS.md with integrated result, cross-checks, and verification summary before running task_close."
    elif (
        orchestration_mode == "team"
        and team_synthesis_required
        and team_artifacts.get("synthesis_ready")
        and team_status == "degraded"
        and not team_artifacts.get("synthesis_refreshed_after_degraded")
    ):
        if current_team_worker and current_worker_is_synthesis_owner:
            next_action = (
                f"As synthesis owner {current_team_worker}, refresh TEAM_SYNTHESIS.md after the degraded team round before resuming verification or close."
            )
        elif team_synthesis_workers:
            next_action = (
                "The team round degraded after synthesis — hand off to "
                + ", ".join(team_synthesis_workers[:3])
                + " to refresh TEAM_SYNTHESIS.md before resuming verification or close."
            )
        else:
            next_action = "Refresh TEAM_SYNTHESIS.md after the degraded team round before resuming verification or close."
    elif orchestration_mode == "team" and team_artifacts.get("team_runtime_verification_needed"):
        verification_reason = team_artifacts.get("team_runtime_verification_reason") or "run final runtime verification after TEAM_SYNTHESIS.md"
        runtime_artifact_name = team_artifacts.get("team_runtime_artifact") or runtime_critic_name
        if current_team_worker and current_worker_is_synthesis_owner:
            next_action = (
                f"As synthesis owner {current_team_worker}, {verification_reason}, refresh {runtime_artifact_name}, "
                "then refresh HANDOFF.md before task_close."
            )
        elif team_synthesis_workers:
            next_action = (
                "TEAM_SYNTHESIS.md is ready — hand off to "
                + ", ".join(team_synthesis_workers[:3])
                + f" for final runtime verification and {runtime_artifact_name}, then refresh HANDOFF.md before close."
            )
        else:
            next_action = (
                f"Rerun final runtime verification after TEAM_SYNTHESIS.md, refresh {runtime_artifact_name}, "
                "then refresh HANDOFF.md before task_close."
            )
    elif orchestration_mode == "team" and team_artifacts.get("team_documentation_needed"):
        documentation_reason = team_artifacts.get("team_documentation_reason") or "refresh DOC_SYNC.md after final team runtime verification"
        doc_sync_artifact = team_artifacts.get("team_doc_sync_artifact") or doc_sync_name
        document_artifact = team_artifacts.get("team_document_critic_artifact") or document_critic_name
        doc_sync_preview = ", ".join(team_doc_sync_workers[:3]) or "the writer"
        doc_critic_preview = ", ".join(team_document_critic_workers[:3]) or "critic-document"
        doc_sync_needed = bool(team_artifacts.get("team_doc_sync_needed"))
        document_critic_needed = bool(team_artifacts.get("team_document_critic_needed"))
        if doc_sync_needed and current_team_worker and current_worker_is_doc_sync_owner:
            actor = f"As {current_team_worker}" if current_agent_role == "writer" else f"As documentation owner {current_team_worker}"
            role_suffix = "" if current_agent_role == "writer" else " using the writer role"
            if document_critic_needed:
                next_action = (
                    f"Complete the documentation pass — {actor}{role_suffix}, {documentation_reason}, refresh {doc_sync_artifact}, then hand off to {doc_critic_preview} "
                    f"for {document_artifact} before the synthesis owner refreshes HANDOFF.md and closes."
                )
            else:
                next_action = (
                    f"Complete the documentation pass — {actor}{role_suffix}, {documentation_reason}, refresh {doc_sync_artifact}, then hand off to the synthesis owner "
                    "for the final HANDOFF.md refresh before close."
                )
        elif (
            not doc_sync_needed
            and document_critic_needed
            and current_team_worker
            and current_worker_is_document_critic_owner
        ):
            role_prefix = f"As {current_team_worker}" if current_agent_role == "critic-document" else f"As document critic owner {current_team_worker}"
            role_suffix = "" if current_agent_role == "critic-document" else " using the critic-document role"
            next_action = (
                f"Complete the documentation pass — {role_prefix}{role_suffix}, rerun {document_artifact} after the refreshed {doc_sync_artifact} / final verification pair, "
                "then hand off to the synthesis owner for the final HANDOFF.md refresh before close."
            )
        elif current_team_worker and current_worker_is_synthesis_owner and (doc_sync_preview or doc_critic_preview):
            if doc_sync_needed:
                next_action = (
                    f"As synthesis owner {current_team_worker}, wait for {doc_sync_preview} to refresh {doc_sync_artifact}, "
                    f"then wait for {doc_critic_preview} to finish {document_artifact} before refreshing HANDOFF.md and closing."
                    if document_critic_needed
                    else f"As synthesis owner {current_team_worker}, wait for {doc_sync_preview} to refresh {doc_sync_artifact}, then refresh HANDOFF.md before close."
                )
            else:
                next_action = (
                    f"As synthesis owner {current_team_worker}, wait for {doc_critic_preview} to finish {document_artifact}, then refresh HANDOFF.md before close."
                )
        elif document_critic_needed:
            if doc_sync_needed:
                next_action = (
                    f"Complete the documentation pass after final team verification — {doc_sync_preview} should {documentation_reason}, refresh {doc_sync_artifact}, "
                    f"then {doc_critic_preview} should rerun {document_artifact} before the synthesis owner refreshes HANDOFF.md and closes."
                )
            else:
                next_action = (
                    f"Complete the documentation review after final team verification — {doc_critic_preview} should {documentation_reason}, refresh {document_artifact}, "
                    "then the synthesis owner should refresh HANDOFF.md before close."
                )
        else:
            next_action = (
                f"Complete the documentation pass after final team verification — {doc_sync_preview} should {documentation_reason}, refresh {doc_sync_artifact}, "
                "then the synthesis owner should refresh HANDOFF.md before close."
            )
    elif orchestration_mode == "team" and team_artifacts.get("handoff_refresh_needed"):
        handoff_reason = team_artifacts.get("handoff_refresh_reason") or "refresh HANDOFF.md from the latest team worker summaries and TEAM_SYNTHESIS.md"
        if current_team_worker and current_worker_is_synthesis_owner:
            next_action = f"As synthesis owner {current_team_worker}, {handoff_reason} before closing or handing off."
        elif team_synthesis_workers:
            next_action = (
                f"{handoff_reason} — hand off to "
                + ", ".join(team_synthesis_workers[:3])
                + " before close or resume."
            )
        else:
            next_action = f"{handoff_reason} before closing or handing off."
    elif blocked_env_round:
        next_action = "Read ENVIRONMENT_SNAPSHOT.md, fix the missing tool/setup assumption, then run task_verify."
    elif runtime_fix_round:
        if env_snapshot_surface:
            next_action = "Read the surfaced runtime evidence, consult ENVIRONMENT_SNAPSHOT.md, run task_verify, then re-check critics."
        else:
            next_action = "Read the surfaced runtime evidence first, fix the failing path, run task_verify, then re-check critics."
    elif intent_fix_round:
        next_action = (
            "intent critic FAIL: request coverage gap — open PLAN.md, add missing AC "
            "for the REQUEST must-goals, get critic-plan PASS, then implement and re-verify."
        )
    elif document_fix_round:
        next_action = "Read the surfaced document evidence first, repair DOC_SYNC / notes, then re-run critic-document before closing."
    elif handoff_data:
        next_action = "Resume from SESSION_HANDOFF.json next_step and only then broaden repo exploration."
    elif lane == "investigate":
        next_action = "Write RESULT.md with findings and close after verification gates pass."
    else:
        if env_snapshot_surface:
            next_action = "Read ENVIRONMENT_SNAPSHOT.md, implement the smallest diff for open checks, then run task_verify and task_close."
        else:
            next_action = "Implement the smallest diff for open checks, then run task_verify and task_close."

    checks_configured_close_gate = parse_checks_close_gate(checks_file)
    effective_close_gate = (
        "strict_high_risk"
        if checks_configured_close_gate == "strict_high_risk" or should_set_strict_close_gate(state_file)
        else "standard"
    )
    checks_non_passed_count = len([c for c in check_items if c.get("status") != "passed"])
    checks_failed_count = len(failed_ids)

    plan_exists = os.path.isfile(_task_abs("PLAN.md"))
    plan_critic_exists = os.path.isfile(_task_abs("CRITIC__plan.md"))
    handoff_exists = os.path.isfile(handoff_path)
    handoff_ready = handoff_exists and not is_handoff_stub(handoff_path)
    doc_sync_exists = os.path.isfile(doc_sync_path)
    document_verdict = (yaml_field("document_verdict", state_file) or "pending").upper()
    document_critic_exists = os.path.isfile(document_critic_path)
    document_critic_needed = needs_document_critic(task_dir)
    result_required = lane == "investigate" or _bool(yaml_field("result_required", state_file) or "false")
    result_exists = os.path.isfile(_task_abs("RESULT.md"))
    is_mutating = str(yaml_field("mutates_repo", state_file) or "true").lower() != "false"

    source_write_allowed = True
    why_source_write_blocked = ""
    if routing_compiled != "true":
        source_write_allowed = False
        why_source_write_blocked = "routing not compiled yet — run task_start first"
    elif plan_verdict != "PASS":
        source_write_allowed = False
        why_source_write_blocked = "plan_verdict is not PASS — source writes require critic-plan approval"
    elif is_team_mode and team_plan_required and not team_artifacts.get("plan_ready"):
        source_write_allowed = False
        why_source_write_blocked = "TEAM_PLAN.md is not complete yet — worker ownership must be finalized before source writes"
    elif current_agent_role and current_agent_role not in ("developer", "harness", ""):
        source_write_allowed = False
        why_source_write_blocked = f"current role '{current_agent_role}' should not mutate source files"
    elif is_team_mode and current_team_worker and team_artifacts.get("plan_ready") and current_agent_role in ("developer", "harness", ""):
        if not current_worker_owned_paths:
            source_write_allowed = False
            why_source_write_blocked = f"worker '{current_team_worker}' has no owned writable paths in TEAM_PLAN.md"

    entry_requirements = []
    if routing_compiled != "true":
        entry_requirements.append("routing compiled")
    if planning_mode == "broad-build":
        entry_requirements.append("spec trio ready")
    entry_requirements.append("PLAN.md ready")
    entry_requirements.append("plan PASS before writes")
    if is_team_mode and team_plan_required:
        entry_requirements.append("TEAM_PLAN ready before writes")

    close_requirements = [
        "PLAN PASS + CRITIC__plan.md",
        "HANDOFF.md ready",
    ]
    if is_team_mode:
        close_requirements.extend(["TEAM_PLAN ready", "TEAM_SYNTHESIS ready"])
    if result_required:
        close_requirements.append("RESULT.md")
    if is_mutating:
        close_requirements.extend([
            "runtime PASS + CRITIC__runtime.md",
            "DOC_SYNC.md",
        ])
    if document_critic_needed:
        close_requirements.append("document PASS + CRITIC__document.md")
    if effective_close_gate == "strict_high_risk":
        close_requirements.append("CHECKS all passed")
    elif check_items:
        close_requirements.append("CHECKS no failed criteria")

    missing_for_close = []
    if not plan_exists:
        missing_for_close.append("PLAN.md")
    if plan_verdict != "PASS" or not plan_critic_exists:
        missing_for_close.append("plan PASS / CRITIC__plan.md")
    if not handoff_ready:
        missing_for_close.append("HANDOFF.md")
    if is_team_mode and not team_artifacts.get("plan_ready"):
        missing_for_close.append("TEAM_PLAN.md ready")
    if is_team_mode and team_synthesis_required and not team_artifacts.get("synthesis_ready"):
        missing_for_close.append("TEAM_SYNTHESIS.md ready")
    if result_required and not result_exists:
        missing_for_close.append("RESULT.md")
    if is_mutating and (runtime_verdict != "PASS" or runtime_freshness != "current" or not os.path.isfile(runtime_critic_path)):
        missing_for_close.append("runtime PASS")
    if is_mutating and not doc_sync_exists:
        missing_for_close.append("DOC_SYNC.md")
    if document_critic_needed and (document_verdict != "PASS" or document_freshness != "current" or not document_critic_exists):
        missing_for_close.append("document PASS")
    if effective_close_gate == "strict_high_risk" and checks_non_passed_count:
        missing_for_close.append(f"CHECKS all passed ({checks_non_passed_count} remaining)")
    elif effective_close_gate == "standard" and checks_failed_count:
        missing_for_close.append(f"CHECKS failed={checks_failed_count}")

    checks_template_path = _task_rel("CHECKS.yaml")
    context_revision = hashlib.sha1(
        json.dumps(
            {
                "task_id": task_id,
                "status": status,
                "updated": yaml_field("updated", state_file) or "",
                "state_revision": yaml_field("state_revision", state_file) or "",
                "plan_verdict": plan_verdict,
                "runtime_verdict": runtime_verdict,
                "document_verdict": document_verdict,
                "team_status": team_status,
                "must_read": must_read,
                "review_focus": {
                    "trigger": review_focus.get("trigger"),
                    "evidence_first": review_focus.get("evidence_first"),
                },
                "next_action": next_action,
            },
            sort_keys=True,
            ensure_ascii=False,
        ).encode("utf-8")
    ).hexdigest()[:12]

    if is_team_mode:
        team_payload = {
            "provider": team_provider,
            "status": team_status,
            "size": team_size,
            "reason": team_reason,
            "plan_required": team_plan_required,
            "synthesis_required": team_synthesis_required,
            "fallback_used": fallback_used,
            "plan_artifact": _task_rel(team_plan_name),
            "plan_exists": bool(team_artifacts.get("plan_exists")),
            "plan_ready": bool(team_artifacts.get("plan_ready")),
            "plan_missing_sections": list(team_artifacts.get("plan_missing_sections") or []),
            "plan_has_placeholders": bool(team_artifacts.get("plan_has_placeholders")),
            "plan_semantic_errors": list(team_artifacts.get("plan_semantic_errors") or []),
            "plan_ownership_ready": bool(team_artifacts.get("plan_ownership_ready")),
            "plan_workers": list(team_artifacts.get("plan_workers") or []),
            "bootstrap_available": bool(team_bootstrap_ready),
            "bootstrap_generated": bool(team_bootstrap_generated),
            "bootstrap_stale": bool(team_bootstrap_stale),
            "bootstrap_refresh_needed": bool(team_bootstrap_refresh_needed),
            "bootstrap_reason": str(team_bootstrap_reason or ""),
            "bootstrap_generated_at": str(team_bootstrap.get("generated_at") or ""),
            "bootstrap_signature": str(team_bootstrap.get("current_signature") or ""),
            "bootstrap_generated_signature": str(team_bootstrap.get("generated_signature") or ""),
            "bootstrap_missing_files": [_task_rel(relpath) for relpath in (team_bootstrap.get("missing_files") or []) if relpath],
            "bootstrap_expected_files": [_task_rel(relpath) for relpath in (team_bootstrap.get("expected_files") or []) if relpath],
            "bootstrap_refresh_command": f"mcp__plugin_harness_harness__team_bootstrap({{task_dir: '{task_root}', write_files: true}})",
            "bootstrap_dir": _task_rel(team_bootstrap_dir_name),
            "bootstrap_index": _task_rel(team_bootstrap_index_name),
            "dispatch_available": bool(team_dispatch_available),
            "dispatch_generated": bool(team_dispatch_generated),
            "dispatch_stale": bool(team_dispatch_stale),
            "dispatch_refresh_needed": bool(team_dispatch_refresh_needed),
            "dispatch_reason": str(team_dispatch_reason or ""),
            "dispatch_generated_at": str(team_dispatch.get("generated_at") or ""),
            "dispatch_signature": str(team_dispatch.get("current_signature") or ""),
            "dispatch_generated_signature": str(team_dispatch.get("generated_signature") or ""),
            "dispatch_missing_files": [_task_rel(relpath) for relpath in (team_dispatch.get("missing_files") or []) if relpath],
            "dispatch_expected_files": [_task_rel(relpath) for relpath in (team_dispatch.get("expected_files") or []) if relpath],
            "dispatch_refresh_command": f"mcp__plugin_harness_harness__team_dispatch({{task_dir: '{task_root}', write_files: true}})",
            "dispatch_dir": _task_rel(team_dispatch_dir_name),
            "dispatch_index": _task_rel(team_dispatch_index_name),
            "launch_available": bool(team_launch_available),
            "launch_generated": bool(team_launch_generated),
            "launch_stale": bool(team_launch_stale),
            "launch_refresh_needed": bool(team_launch_refresh_needed),
            "launch_reason": str(team_launch_reason or ""),
            "launch_generated_at": str(team_launch.get("generated_at") or ""),
            "launch_signature": str(team_launch.get("current_signature") or ""),
            "launch_generated_signature": str(team_launch.get("generated_signature") or ""),
            "launch_manifest": _task_rel(team_launch_manifest_name),
            "launch_target": str(team_launch.get("target") or "auto"),
            "launch_script": _task_rel(str(team_launch.get("launch_script") or team_launch_manifest_name)) if team_launch.get("launch_script") else "",
            "launch_command_preview": str(team_launch.get("launch_command_preview") or ""),
            "launch_provider_prompt": _task_rel(str(team_launch.get("provider_prompt") or "")) if team_launch.get("provider_prompt") else "",
            "launch_implement_dispatcher": _task_rel(str(team_launch.get("implement_dispatcher") or "")) if team_launch.get("implement_dispatcher") else "",
            "launch_interactive_required": bool(team_launch.get("interactive_required")),
            "launch_execute_supported": bool(team_launch.get("execute_supported")),
            "launch_execute_blocker": str(team_launch.get("execute_blocker") or ""),
            "launch_execute_target": str(team_launch.get("execute_target") or ""),
            "launch_execute_launch_script": _task_rel(str(team_launch.get("execute_launch_script") or "")) if team_launch.get("execute_launch_script") else "",
            "launch_execute_command_preview": str(team_launch.get("execute_command_preview") or ""),
            "launch_execute_fallback_available": bool(team_launch.get("execute_fallback_available")),
            "launch_execute_resolution_reason": str(team_launch.get("execute_resolution_reason") or ""),
            "launch_refresh_command": f"mcp__plugin_harness_harness__team_launch({{task_dir: '{task_root}', write_files: true}})",
            "relaunch_available": bool(team_relaunch.get("available")),
            "relaunch_ready": bool(team_relaunch.get("ready")),
            "relaunch_reason": str(team_relaunch.get("reason") or ""),
            "relaunch_selection_reason": str(team_relaunch.get("selection_reason") or ""),
            "relaunch_selection_source": str(team_relaunch.get("selection_source") or ""),
            "relaunch_worker": str(team_relaunch.get("worker") or ""),
            "relaunch_phase": str(team_relaunch.get("phase") or ""),
            "relaunch_artifact": str(team_relaunch.get("artifact") or ""),
            "relaunch_prompt_file": str(team_relaunch.get("prompt_file") or ""),
            "relaunch_run_script": str(team_relaunch.get("run_script") or ""),
            "relaunch_log_file": str(team_relaunch.get("log_file") or ""),
            "relaunch_command_preview": str(team_relaunch.get("command_preview") or ""),
            "relaunch_refresh_command": "mcp__plugin_harness_harness__team_relaunch({task_dir: '" + task_root + "', write_files: true})",
            "summary_workers": list(team_summary_workers or []),
            "synthesis_workers": list(team_synthesis_workers or []),
            "plan_owned_path_count": int(team_artifacts.get("plan_owned_path_count") or 0),
            "plan_shared_read_only_paths": list(team_artifacts.get("plan_shared_read_only_paths") or []),
            "worker_summary_dir": _task_rel("team"),
            "worker_summary_required": bool(team_artifacts.get("worker_summary_required")),
            "worker_summary_ready": bool(team_artifacts.get("worker_summary_ready")),
            "worker_summary_expected_workers": list(team_artifacts.get("worker_summary_expected_workers") or []),
            "worker_summary_expected_count": int(team_artifacts.get("worker_summary_expected_count") or 0),
            "worker_summary_present_count": int(team_artifacts.get("worker_summary_present_count") or 0),
            "worker_summary_ready_count": int(team_artifacts.get("worker_summary_ready_count") or 0),
            "worker_summary_missing_workers": list(team_artifacts.get("worker_summary_missing_workers") or []),
            "worker_summary_errors": list(team_artifacts.get("worker_summary_errors") or []),
            "worker_summary_artifacts": [
                _task_rel(item)
                for item in list(team_artifacts.get("worker_summary_artifacts") or [])
            ],
            "current_worker": current_team_worker,
            "current_agent_role": current_agent_role,
            "current_worker_is_synthesis_owner": bool(current_worker_is_synthesis_owner),
            "current_worker_is_doc_sync_owner": bool(current_worker_is_doc_sync_owner),
            "current_worker_is_document_critic_owner": bool(current_worker_is_document_critic_owner),
            "current_worker_pending": bool(current_worker_pending),
            "current_worker_owned_paths": list(current_worker_owned_paths or []),
            "current_worker_summary_artifact": _task_rel(current_worker_relpath) if current_worker_relpath else "",
            "current_worker_summary_status": str(current_worker_summary.get("status") or "missing"),
            "current_worker_summary_ready": bool(current_worker_summary.get("ready")),
            "current_worker_handled_paths": list(current_worker_summary.get("owned_paths_handled") or []),
            "current_worker_verification_excerpt": str(current_worker_summary.get("verification_excerpt") or ""),
            "current_worker_residual_risks_excerpt": str(current_worker_summary.get("residual_risks_excerpt") or ""),
            "runtime_verification_needed": bool(team_artifacts.get("team_runtime_verification_needed")),
            "runtime_verification_ready": bool(team_artifacts.get("team_runtime_verification_ready")),
            "runtime_verification_reason": str(team_artifacts.get("team_runtime_verification_reason") or ""),
            "runtime_verification_owners": list(team_artifacts.get("team_runtime_verification_owners") or []),
            "runtime_verification_artifact": _task_rel(team_artifacts.get("team_runtime_artifact") or runtime_critic_name),
            "runtime_verification_artifact_exists": bool(team_artifacts.get("team_runtime_artifact_exists")),
            "documentation_needed": bool(team_artifacts.get("team_documentation_needed")),
            "documentation_ready": bool(team_artifacts.get("team_documentation_ready")),
            "documentation_reason": str(team_artifacts.get("team_documentation_reason") or ""),
            "documentation_owner_label": str(team_artifacts.get("team_documentation_owner_label") or ""),
            "doc_sync_artifact": _task_rel(team_artifacts.get("team_doc_sync_artifact") or doc_sync_name),
            "doc_sync_exists": bool(team_artifacts.get("team_doc_sync_exists")),
            "doc_sync_needed": bool(team_artifacts.get("team_doc_sync_needed")),
            "doc_sync_owners": list(team_artifacts.get("team_doc_sync_owners") or []),
            "doc_sync_owner_label": str(team_artifacts.get("team_doc_sync_owner_label") or ""),
            "doc_sync_stale_after_verification": bool(team_artifacts.get("team_doc_sync_stale_after_verification")),
            "document_critic_needed": bool(team_artifacts.get("team_document_critic_needed")),
            "document_critic_artifact": _task_rel(team_artifacts.get("team_document_critic_artifact") or document_critic_name),
            "document_critic_exists": bool(team_artifacts.get("team_document_critic_exists")),
            "document_critic_pending": bool(team_artifacts.get("team_document_critic_pending")),
            "document_critic_owners": list(team_artifacts.get("team_document_critic_owners") or []),
            "document_critic_owner_label": str(team_artifacts.get("team_document_critic_owner_label") or ""),
            "document_critic_stale_after_docs": bool(team_artifacts.get("team_document_critic_stale_after_docs")),
            "document_verdict": str(team_artifacts.get("team_document_verdict") or "pending"),
            "handoff_refresh_needed": bool(team_artifacts.get("handoff_refresh_needed")),
            "handoff_refresh_reason": str(team_artifacts.get("handoff_refresh_reason") or ""),
            "latest_team_artifact": str(team_artifacts.get("latest_team_artifact") or ""),
            "synthesis_artifact": _task_rel(team_synthesis_name),
            "synthesis_exists": bool(team_artifacts.get("synthesis_exists")),
            "synthesis_ready": bool(team_artifacts.get("synthesis_ready")),
            "synthesis_missing_sections": list(team_artifacts.get("synthesis_missing_sections") or []),
            "synthesis_has_placeholders": bool(team_artifacts.get("synthesis_has_placeholders")),
            "synthesis_semantic_errors": list(team_artifacts.get("synthesis_semantic_errors") or []),
            "synthesis_refreshed_after_degraded": bool(team_artifacts.get("synthesis_refreshed_after_degraded")),
        }
    else:
        team_payload = {
            "status": team_status,
            "size": team_size,
        }

    return {
        "task_id": task_id,
        "status": status,
        "risk_level": risk_level,
        "qa_required": qa_required,
        "doc_sync_required": doc_sync_required,
        "browser_required": browser_required,
        "planning_mode": planning_mode,
        "parallelism": parallelism,
        "workflow_locked": workflow_locked,
        "maintenance_task": maintenance_task,
        "context_revision": context_revision,
        "compat": {
            "execution_mode": execution_mode,
            "orchestration_mode": orchestration_mode,
        },
        "team": team_payload,
        "source_write_allowed": source_write_allowed,
        "why_source_write_blocked": why_source_write_blocked,
        "missing_for_close": missing_for_close[:6],
        "must_read": must_read,
        "checks": checks,
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
