#!/usr/bin/env python3
"""PostToolUse hook (matcher: Bash) — read-only routing hint on known failures.

Inspects the Bash tool's ``tool_response.{stdout, stderr}``. Emits a short
``[harness-hint] <suggestion>`` line on stdout when a known failure pattern
matches. Never blocks: this is advice for the agent, not a gate.

Patterns:
  - ``command not found: pytest|npm|bun|pnpm|yarn``
    → suggest the manifest ``test_command`` (or build/dev as appropriate).
  - ``No such file or directory: plugin/scripts/<name>.py``
    → suggest a neighbor in ``plugin/scripts/`` if one exists.

Outside those patterns the hook is silent. ``|| true`` in hooks.json keeps
the session healthy on any crash; try/except wraps ``main``.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (  # type: ignore
        read_hook_input,
        yaml_field,
        find_repo_root,
        MANIFEST_PATH,
        _log_gate_error,
    )
except Exception:
    sys.exit(0)


_HINT_PREFIX = "[harness-hint]"

# Two canonical shell shapes for "not found":
#   bash: pytest: command not found    (bash)
#   command not found: pytest          (zsh / some shells)
# Also: -bash: npm: command not found  (login bash)
_CMD_NOT_FOUND_RE = re.compile(
    r"(?:(?:^|\s)(?:-?\w+:\s*)?(pytest|npm|bun|pnpm|yarn|node|python3?)\s*:\s*(?:command\s+)?not\s+found"
    r"|command\s+not\s+found\s*:\s*(pytest|npm|bun|pnpm|yarn|node|python3?))",
    re.MULTILINE,
)
_NO_SUCH_SCRIPT_RE = re.compile(
    r"No such file or directory[:\s]+['\"]?((?:plugin/scripts|plugin/mcp)/[\w\-.]+\.py)",
)


def _hint_for_command(cmd: str, repo_root: str) -> str:
    manifest = os.path.join(repo_root, MANIFEST_PATH)
    hint_parts = [f"`{cmd}` not found."]
    test_cmd = yaml_field("test_command", manifest) or ""
    build_cmd = yaml_field("build_command", manifest) or ""
    if cmd in ("pytest", "python", "python3") and test_cmd:
        hint_parts.append(f"Use manifest test_command: `{test_cmd}`.")
    elif cmd in ("npm", "bun", "pnpm", "yarn", "node") and test_cmd:
        hint_parts.append(f"Use manifest test_command: `{test_cmd}`.")
    elif build_cmd and cmd in ("npm", "bun", "pnpm", "yarn"):
        hint_parts.append(f"build_command: `{build_cmd}`.")
    else:
        hint_parts.append(
            "Check doc/harness/manifest.yaml for the canonical test/build command."
        )
    return " ".join(hint_parts)


def _neighbor_scripts(repo_root: str, missing_path: str) -> list[str]:
    """Return up to 3 near-neighbor basenames from the script's directory."""
    scripts_dir = os.path.join(repo_root, os.path.dirname(missing_path))
    if not os.path.isdir(scripts_dir):
        return []
    missing_base = os.path.basename(missing_path)
    try:
        entries = sorted(os.listdir(scripts_dir))
    except OSError:
        return []
    pys = [e for e in entries if e.endswith(".py") and e != missing_base]
    if not pys:
        return []
    # Prefer entries sharing a 3+ char prefix with the miss.
    prefix = missing_base[:3].lower()
    ranked = sorted(pys, key=lambda e: (0 if e.lower().startswith(prefix) else 1, e))
    return ranked[:3]


def _hint_for_missing_script(missing: str, repo_root: str) -> str:
    neighbors = _neighbor_scripts(repo_root, missing)
    if not neighbors:
        return f"Script `{missing}` not found. Check `plugin/scripts/` for the correct name."
    return (
        f"Script `{missing}` not found. "
        f"Nearby: {', '.join('`' + n + '`' for n in neighbors)}."
    )


def _extract_error_text(data: dict) -> str:
    """Best-effort extraction of stderr/stdout from the hook payload."""
    tr = data.get("tool_response") or data.get("toolResult") or {}
    parts: list[str] = []
    for key in ("stderr", "stdout", "output", "error"):
        val = tr.get(key) if isinstance(tr, dict) else None
        if isinstance(val, str) and val:
            parts.append(val)
    # Fallback: some hook shapes put the result as plain string
    if not parts:
        val = data.get("tool_response")
        if isinstance(val, str):
            parts.append(val)
    return "\n".join(parts)[:8192]


def main() -> int:
    data = read_hook_input()
    if not data:
        return 0
    if data.get("tool_name") != "Bash":
        return 0
    error_text = _extract_error_text(data)
    if not error_text:
        return 0

    repo_root = find_repo_root()
    m_cmd = _CMD_NOT_FOUND_RE.search(error_text)
    m_miss = _NO_SUCH_SCRIPT_RE.search(error_text)
    hint = ""
    if m_cmd:
        cmd = m_cmd.group(1) or m_cmd.group(2)
        hint = _hint_for_command(cmd, repo_root)
    elif m_miss:
        hint = _hint_for_missing_script(m_miss.group(1), repo_root)
    if not hint:
        return 0
    sys.stdout.write(f"{_HINT_PREFIX} {hint}\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "tool_routing")
        except Exception:
            pass
        sys.exit(0)
