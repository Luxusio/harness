#!/usr/bin/env python3
"""QA Codifier — parse codifiable: YAML blocks from CRITIC__qa.md and emit regression tests.

Reads CRITIC__qa.md from a task dir, extracts codifiable: YAML blocks,
templates them into project-native test format, compile-checks, and moves
validated files to tests/regression/<sanitized-task-id>/<behavior>.<ext>.

Stdlib only. Never blocks task close (always exits 0).

Usage:
  python3 qa_codifier.py --task-dir <path>
  python3 qa_codifier.py --transcript <path>
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import find_repo_root, yaml_field

LEARNINGS = "doc/harness/learnings.jsonl"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _log(repo_root: str, type_: str, key: str, insight: str, task: str = "") -> None:
    """Append a learning entry (best-effort)."""
    try:
        path = os.path.join(repo_root, LEARNINGS)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        entry = json.dumps({
            "ts": _now_iso(),
            "type": type_,
            "source": "qa_codifier",
            "key": key,
            "insight": insight,
            "task": task,
        })
        with open(path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def sanitize_task_id(task_id: str) -> str:
    """TASK__add-feature -> task_add_feature"""
    s = task_id.lower()
    s = re.sub(r"[^a-z0-9_]", "_", s)
    s = re.sub(r"_+", "_", s)
    return s.strip("_")


def _infer_test_format(manifest_path: str) -> str:
    """Infer test format from manifest test_command. Returns 'pytest', 'js', or 'shell'."""
    test_cmd = yaml_field("test_command", manifest_path) or ""
    if "pytest" in test_cmd or "python" in test_cmd:
        return "pytest"
    if any(x in test_cmd for x in ("bun test", "vitest", "node --test", "jest")):
        return "js"
    return "shell"


def _parse_codifiable_blocks(transcript: str) -> list[dict]:
    """Extract codifiable: YAML blocks from transcript text.

    Scans for 'codifiable:' line, then collects indented YAML list items.
    Returns list of dicts with keys: behavior, command, expected_exit,
    expected_stdout_contains, expected_stderr_contains.
    """
    blocks = []
    lines = transcript.splitlines()
    i = 0
    while i < len(lines):
        # Look for codifiable: marker (may be inside a code block)
        stripped = lines[i].strip()
        if stripped == "codifiable:":
            # Collect indented items
            yaml_lines = ["codifiable:\n"]
            j = i + 1
            while j < len(lines):
                # Accept lines that start with spaces/dashes or are list items
                ln = lines[j]
                if ln.strip() == "" and j + 1 < len(lines) and lines[j + 1].startswith(" "):
                    yaml_lines.append(ln + "\n")
                    j += 1
                    continue
                if ln.startswith(" ") or ln.startswith("-"):
                    yaml_lines.append(ln + "\n")
                    j += 1
                else:
                    break
            # Parse the collected YAML manually (no pyyaml)
            raw_yaml = "".join(yaml_lines)
            parsed = _parse_codifiable_yaml(raw_yaml)
            blocks.extend(parsed)
            i = j
        else:
            i += 1
    return blocks


def _parse_codifiable_yaml(text: str) -> list[dict]:
    """Minimal parser for codifiable: YAML list.

    Handles:
      codifiable:
        - behavior: name
          command: "cmd"
          expected_exit: 0
          expected_stdout_contains: ["str1", "str2"]
          expected_stderr_contains: []
    """
    entries = []
    lines = text.splitlines()

    current: dict | None = None
    for ln in lines:
        # New list item
        m = re.match(r"^\s+-\s+behavior:\s*(.+)$", ln)
        if m:
            if current is not None:
                entries.append(current)
            current = {
                "behavior": m.group(1).strip().strip('"').strip("'"),
                "command": "",
                "expected_exit": 0,
                "expected_stdout_contains": [],
                "expected_stderr_contains": [],
            }
            continue
        if current is None:
            continue
        # ac_id field: scalar or inline list
        m_ac = re.match(r"^\s+ac_id:\s*(.*)$", ln)
        if m_ac:
            raw = m_ac.group(1).strip()
            if raw.startswith("[") and raw.endswith("]"):
                # Inline list: [AC-001, AC-002]
                inner = raw[1:-1].strip()
                if inner:
                    items = [x.strip().strip('"').strip("'").upper()
                             for x in inner.split(",")]
                    current["ac_id"] = [x for x in items if x]
                else:
                    current["ac_id"] = None
            elif raw:
                current["ac_id"] = raw.strip('"').strip("'").upper()
            else:
                current["ac_id"] = None
            continue
        # Field lines
        for field in ("command", "expected_exit"):
            m2 = re.match(rf"^\s+{re.escape(field)}:\s*(.+)$", ln)
            if m2:
                val = m2.group(1).strip().strip('"').strip("'")
                if field == "expected_exit":
                    try:
                        val = int(val)
                    except ValueError:
                        val = 0
                current[field] = val
        for list_field in ("expected_stdout_contains", "expected_stderr_contains"):
            m3 = re.match(rf"^\s+{re.escape(list_field)}:\s*\[(.*)\]$", ln)
            if m3:
                inner = m3.group(1).strip()
                if inner:
                    items = [x.strip().strip('"').strip("'") for x in inner.split(",")]
                    current[list_field] = [x for x in items if x]
                else:
                    current[list_field] = []
    if current is not None:
        entries.append(current)
    return entries


def _render_pytest(behavior: str, command: str, expected_exit: int,
                   expected_stdout: list[str], expected_stderr: list[str]) -> str:
    def _assert_line(field: str, s: str) -> str:
        # Use double-quoted string for message to avoid quote conflicts
        msg = "missing in " + field + ": " + repr(s)
        return f"    assert {repr(s)} in r.{field}, {json.dumps(msg)}"
    stdout_asserts = "\n".join(_assert_line("stdout", s) for s in expected_stdout)
    stderr_asserts = "\n".join(_assert_line("stderr", s) for s in expected_stderr)
    parts = [
        "import subprocess\n\n",
        f"def test_{behavior}():\n",
        f"    r = subprocess.run(\n",
        f"        {json.dumps(command)},\n",
        f"        shell=True, capture_output=True, text=True, timeout=30\n",
        f"    )\n",
        f"    assert r.returncode == {expected_exit}, "
        f"f'exit {{r.returncode}}, want {expected_exit}: {{r.stderr}}'\n",
    ]
    if stdout_asserts:
        parts.append(stdout_asserts + "\n")
    if stderr_asserts:
        parts.append(stderr_asserts + "\n")
    return "".join(parts)


def _render_js(behavior: str, command: str, expected_exit: int,
               expected_stdout: list[str], expected_stderr: list[str]) -> str:
    stdout_asserts = "\n".join(
        f'  assert(stdout.includes({json.dumps(s)}), `stdout missing: {json.dumps(s)}`);'
        for s in expected_stdout
    )
    stderr_asserts = "\n".join(
        f'  assert(stderr.includes({json.dumps(s)}), `stderr missing: {json.dumps(s)}`);'
        for s in expected_stderr
    )
    return f"""const {{ execSync }} = require('child_process');
const assert = require('assert');

function test_{behavior}() {{
  let stdout = '', stderr = '', code = 0;
  try {{
    stdout = execSync({json.dumps(command)}, {{ encoding: 'utf8', timeout: 30000 }});
  }} catch (e) {{
    stdout = e.stdout || '';
    stderr = e.stderr || '';
    code = e.status || 1;
  }}
  assert.strictEqual(code, {expected_exit}, `exit ${{code}}, want {expected_exit}: ${{stderr}}`);
{stdout_asserts}
{stderr_asserts}
}}

test_{behavior}();
"""


def _render_shell(behavior: str, command: str, expected_exit: int,
                  expected_stdout: list[str], expected_stderr: list[str]) -> str:
    stdout_checks = "\n".join(
        f'echo "$OUT" | grep -q {json.dumps(s)} || (echo "stdout missing: {s}"; exit 1)'
        for s in expected_stdout
    )
    return f"""#!/bin/bash
set -e
OUT=$({command} 2>/tmp/test_{behavior}_stderr; ACTUAL_EXIT=$?)
[ "$ACTUAL_EXIT" -eq {expected_exit} ] || (echo "exit $ACTUAL_EXIT want {expected_exit}"; exit 1)
{stdout_checks}
echo "PASS: {behavior}"
"""


def _compile_check_python(path: str) -> bool:
    try:
        r = subprocess.run(
            ["python3", "-c", f"compile(open({json.dumps(path)}).read(), {json.dumps(path)}, 'exec')"],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _compile_check_js(path: str) -> bool:
    try:
        r = subprocess.run(
            ["node", "--check", path],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _unique_behavior_name(target_dir: str, behavior: str, ext: str) -> str:
    """Return behavior name that doesn't collide with existing files."""
    if not os.path.exists(os.path.join(target_dir, f"{behavior}.{ext}")):
        return behavior
    suffix = 2
    while os.path.exists(os.path.join(target_dir, f"{behavior}_{suffix}.{ext}")):
        suffix += 1
    return f"{behavior}_{suffix}"


# AC-004 trivial-command filter regexes (compiled once at module load)
# Pattern 1: bare echo with no pipe/ampersand/semicolon/subshell chars after first space.
#   Rejects: "echo hello"
#   Accepts: "echo hello | grep x", "echo $(date)", "echo `date`"
# Note: pattern 2 rejects "myapp --version > /dev/null" as an accepted cost (documented in HANDOFF).
_RE_TRIVIAL_ECHO = re.compile(r"^echo\s+[^|&;$`()]*$")
_RE_TRIVIAL_VERSION = re.compile(r"^[\w/.-]+\s*--version\s*$")
_RE_TRIVIAL_TRUE = re.compile(r"^(true|:)\s*$")


def _is_trivial_command(cmd: str) -> bool:
    """Return True if cmd is a trivial no-product-contact command.

    Trivial patterns (each compiled as a module-level regex):
    - Bare echo with no pipes, ampersands, semicolons, or subshells: rejects "echo hello"
    - Bare --version flag with no chaining: rejects "python3 --version"
    - Bare true or : shell no-ops
    """
    cmd = cmd.strip()
    return bool(
        _RE_TRIVIAL_ECHO.match(cmd)
        or _RE_TRIVIAL_VERSION.match(cmd)
        or _RE_TRIVIAL_TRUE.match(cmd)
    )


# AC-003 ac_id validation regex
_RE_VALID_AC_ID = re.compile(r"^AC-\d+$")


def codify(task_dir: str, transcript_path: str | None = None, target_root: str | None = None) -> int:
    """Main codification pipeline. Always returns 0."""
    try:
        repo_root = target_root if target_root is not None else find_repo_root()
        task_id = os.path.basename(os.path.normpath(task_dir))

        # Read transcript
        if transcript_path is None:
            transcript_path = os.path.join(task_dir, "CRITIC__qa.md")
        if not os.path.isfile(transcript_path):
            _log(repo_root, "codifier-empty", "codifier-empty",
                 f"no transcript at {transcript_path}", task_id)
            return 0

        with open(transcript_path, "r", encoding="utf-8") as f:
            transcript = f.read()

        blocks = _parse_codifiable_blocks(transcript)
        if not blocks:
            _log(repo_root, "codifier-empty", "codifier-empty",
                 "no codifiable blocks found in transcript", task_id)
            return 0

        # Infer test format (uses the resolved repo_root, not a second find_repo_root())
        manifest_path = os.path.join(repo_root, "doc", "harness", "manifest.yaml")
        fmt = _infer_test_format(manifest_path)
        ext = "py" if fmt == "pytest" else ("js" if fmt == "js" else "sh")

        sanitized = sanitize_task_id(task_id)

        # Staging dir
        staging_dir = os.path.join(task_dir, "audit", "regression-draft", sanitized)
        os.makedirs(staging_dir, exist_ok=True)

        # Target dir
        target_dir = os.path.join(repo_root, "tests", "regression", sanitized)

        moved = 0
        for block in blocks:
            behavior = re.sub(r"[^a-z0-9_]", "_", block.get("behavior", "unknown").lower()).strip("_")
            if not behavior:
                behavior = "test_block"
            command = block.get("command", "")
            expected_exit = block.get("expected_exit", 0)
            expected_stdout = block.get("expected_stdout_contains", [])
            expected_stderr = block.get("expected_stderr_contains", [])

            # AC-003: validate ac_id — skip + log on missing or malformed
            ac_id = block.get("ac_id")
            if ac_id is None:
                _log(repo_root, "codifier-rejected", "missing-ac_id", behavior, task_id)
                continue
            # Validate scalar or list
            if isinstance(ac_id, list):
                valid = all(_RE_VALID_AC_ID.match(str(x)) for x in ac_id)
                first_ac = ac_id[0] if ac_id else None
            else:
                valid = bool(_RE_VALID_AC_ID.match(str(ac_id)))
                first_ac = ac_id
            if not valid or first_ac is None:
                _log(repo_root, "codifier-rejected", "invalid-ac_id", behavior, task_id)
                continue
            # Build filename prefix: ac_001__ from first ac_id
            ac_num = re.search(r"\d+", str(first_ac))
            ac_prefix = f"ac_{int(ac_num.group()):03d}__" if ac_num else "ac_000__"
            # Prefix with 'test_' so pytest auto-discovers under tests/regression/<task>/.
            # Without this prefix, files land in the right directory but the runner
            # skips them because pytest only globs test_*.py / *_test.py by default.
            prefixed_behavior = "test_" + ac_prefix + behavior

            # AC-004: reject trivial commands — skip + log
            if _is_trivial_command(command):
                _log(repo_root, "codifier-rejected", "trivial-command",
                     f"{behavior}::{command}", task_id)
                continue

            # Render
            if fmt == "pytest":
                code = _render_pytest(prefixed_behavior, command, expected_exit, expected_stdout, expected_stderr)
            elif fmt == "js":
                code = _render_js(prefixed_behavior, command, expected_exit, expected_stdout, expected_stderr)
            else:
                code = _render_shell(prefixed_behavior, command, expected_exit, expected_stdout, expected_stderr)

            # Stage
            stage_path = os.path.join(staging_dir, f"{prefixed_behavior}.{ext}")
            with open(stage_path, "w", encoding="utf-8") as f:
                f.write(code)

            # Compile check
            ok = False
            if fmt == "pytest":
                ok = _compile_check_python(stage_path)
            elif fmt == "js":
                ok = _compile_check_js(stage_path)
            else:
                ok = True  # Shell: no check available

            if not ok:
                _log(repo_root, "codifier-fail", "codifier-fail",
                     f"compile-check failed for {prefixed_behavior} in {task_id}, left in staging",
                     task_id)
                continue

            # Move to target
            os.makedirs(target_dir, exist_ok=True)
            final_behavior = _unique_behavior_name(target_dir, prefixed_behavior, ext)
            target_path = os.path.join(target_dir, f"{final_behavior}.{ext}")
            shutil.move(stage_path, target_path)
            moved += 1
            print(f"codifier: staged {final_behavior}.{ext} -> {os.path.relpath(target_path, repo_root)}")

        if moved == 0 and blocks:
            _log(repo_root, "codifier-fail", "codifier-fail",
                 f"all {len(blocks)} blocks failed compile-check in {task_id}", task_id)

    except Exception as exc:
        try:
            repo_root = find_repo_root()
            _log(repo_root, "codifier-fail", "codifier-fail",
                 f"codifier crashed: {exc}", "")
        except Exception:
            pass
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="Codify QA transcript into regression tests")
    p.add_argument("--task-dir", default=None)
    p.add_argument("--transcript", default=None)
    args = p.parse_args()

    if args.task_dir is None and args.transcript is None:
        p.error("provide --task-dir or --transcript")

    task_dir = args.task_dir
    if task_dir is None:
        task_dir = os.path.dirname(os.path.abspath(args.transcript))

    return codify(task_dir, args.transcript)


if __name__ == "__main__":
    sys.exit(main())
