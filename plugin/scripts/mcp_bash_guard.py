#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — block direct Bash file mutations.

Closes the Bash-layer bypass where agents write to source / protected-artifact
/ workflow-control-surface paths via ``sed -i``, ``cp``, ``mv``, ``tee``,
shell redirection, ``python -c "open(...,'w')"``, etc.

Signalling contract matches ``prewrite_gate.py``: deny via stdout JSON envelope
with exit 0; silent on allow; fail-open on unexpected exceptions.

Escape hatch: ``HARNESS_SKIP_MCP_GUARD=1`` → one-shot allow + log ``gate-bypass``.

Known gaps (documented in doc/harness/patterns/mcp-bash-guard.md):
  - Nested shells: ``bash -c "sed -i x file"`` — the mutation is hidden inside
    ``-c``'s argument as a single shlex token; not recursed.
  - ``eval "sed -i ..."`` and command substitution ``$(...)`` / backticks.
  - Base64 / obfuscated ``python -c`` writes.
  - Symlink resolution (``os.path.realpath``) — not applied before classification.
"""
from __future__ import annotations

import os
import re
import shlex
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        read_hook_input,
        emit_permission_decision,
        _log_gate_error,
        _escape_hint,
        log_gate_bypass,
        find_repo_root,
    )
    from prewrite_gate import (
        _is_protected_artifact,
        _is_source_file,
        _is_workflow_control_surface,
        PROTECTED_ARTIFACTS,
    )
except Exception:
    sys.exit(0)


GATE_NAME = "mcp_bash_guard"
_COMMAND_LENGTH_CAP = 64 * 1024  # short-circuit extremely large commands

REDIRECT_TOKENS = {">", ">>", "1>", "1>>"}
# Note: 2> stderr redirect is intentionally NOT blocked — logs are common.

LAST_ARG_MUTATORS = {"cp", "mv", "install", "touch", "truncate"}
TEE_COMMAND = "tee"

# Shell operators that separate command units. We shlex-tokenize first
# (respects quotes — so `;` inside a `python -c "..."` string stays intact)
# and then walk tokens with these markers resetting per-segment state.
BOUNDARY_TOKENS = {"&&", "||", "|", ";", "\n", "&"}

# Precompiled once at module load (perf: hook spawns fresh python per call).
_INLINE_REDIRECT_RE = re.compile(r"^(?:\d*)?(>>?)(.+)$")

_PY_PATTERNS = [
    re.compile(r"open\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"][wa+]"),
    re.compile(r"(?:pathlib\.)?Path\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:write_text|write_bytes|open)"),
    re.compile(r"os\.replace\([^,]+,\s*['\"]([^'\"]+)['\"]\)"),
    re.compile(r"shutil\.copy(?:2)?\([^,]+,\s*['\"]([^'\"]+)['\"]\)"),
]

# Protected-artifact → owning MCP/CLI tool (for human-text fix hint).
_ARTIFACT_TOOL_HINT = {
    "CRITIC__runtime.md": "mcp__harness__write_critic_runtime",
    "HANDOFF.md": "mcp__harness__write_handoff",
    "DOC_SYNC.md": "mcp__harness__write_doc_sync",
    "PLAN.md": "Skill(harness:plan)",
    "PLAN.meta.json": "Skill(harness:plan)",
    "CHECKS.yaml": "plan-skill + scripts/update_checks.py",
    "AUDIT_TRAIL.md": "plan-skill",
}

RULE_DOCS = {
    "protected-artifact": "doc/harness/patterns/mcp-bash-guard.md",
    "workflow-control-surface": "doc/harness/patterns/mcp-bash-guard.md",
    "source": "doc/harness/patterns/mcp-bash-guard.md",
}


# ── Token helpers ──────────────────────────────────────────────────────────


def _is_env_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token or ""))


def _tokenize(command: str):
    """shlex-parse a command, emitting shell operators as distinct tokens.

    Uses ``shlex.shlex`` with ``punctuation_chars=True`` so ``&&``, ``||``,
    ``|``, ``;``, ``&`` become their own tokens while quoted strings stay
    intact. On malformed input (unclosed quote etc.), falls back to a
    whitespace split.
    """
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        return [t for t in lexer if t]
    except ValueError:
        return command.split()


def _normalize_candidate_path(token: str) -> str:
    value = str(token or "").strip().strip("'").strip('"')
    if not value:
        return ""
    if value.startswith("./"):
        value = value[2:]
    cwd = os.getcwd()
    if os.path.isabs(value):
        try:
            rel = os.path.relpath(value, cwd)
        except ValueError:
            return ""
        if rel.startswith(".."):
            return ""
        value = rel
    return value.rstrip(",)")


def _classify_gated_path(path_value: str, repo_root: str) -> str:
    if not path_value:
        return ""
    if _is_workflow_control_surface(path_value, repo_root=repo_root):
        return "workflow-control-surface"
    if _is_protected_artifact(path_value):
        return "protected-artifact"
    if _is_source_file(path_value, repo_root=repo_root):
        return "source"
    return ""


def _append_target(targets, token, method, repo_root):
    path_value = _normalize_candidate_path(token)
    category = _classify_gated_path(path_value, repo_root)
    if not category:
        return
    item = {"path": path_value, "category": category, "method": method}
    if item not in targets:
        targets.append(item)


def _last_non_option(tokens):
    for token in reversed(tokens[1:]):
        if token.startswith("-"):
            continue
        return token
    return ""


# ── Mutation-target extraction ─────────────────────────────────────────────


def _extract_redirect_targets(tokens, targets, repo_root):
    for index, token in enumerate(tokens):
        if token in REDIRECT_TOKENS and index + 1 < len(tokens):
            _append_target(targets, tokens[index + 1], "shell redirection", repo_root)
            continue
        inline = _INLINE_REDIRECT_RE.match(token)
        if inline:
            candidate = inline.group(2).strip()
            if candidate and candidate not in ("&1", "&2"):
                _append_target(targets, candidate, "shell redirection", repo_root)


def _extract_python_inline_targets(tokens, targets, repo_root):
    if "-c" not in tokens:
        return
    try:
        code = tokens[tokens.index("-c") + 1]
    except IndexError:
        return
    for pat in _PY_PATTERNS:
        for match in pat.findall(code):
            _append_target(targets, match, "python inline write", repo_root)


def _process_segment(segment_tokens, targets, repo_root):
    """Classify a single command segment (between shell operators)."""
    if not segment_tokens:
        return
    # Skip leading env assignments (fixes `FOO=bar sed -i ...` bypass).
    idx = 0
    while idx < len(segment_tokens) and _is_env_assignment(segment_tokens[idx]):
        idx += 1
    if idx >= len(segment_tokens):
        return
    non_env = segment_tokens[idx:]
    cmd = os.path.basename(non_env[0])

    if cmd == "sed" and any(t == "-i" or t.startswith("-i") for t in non_env[1:]):
        _append_target(targets, _last_non_option(non_env), "sed -i", repo_root)
        return
    if cmd == "perl" and any(t == "-pi" or t.startswith("-pi") for t in non_env[1:]):
        _append_target(targets, _last_non_option(non_env), "perl -pi", repo_root)
        return
    if cmd in LAST_ARG_MUTATORS:
        _append_target(targets, _last_non_option(non_env), cmd, repo_root)
        return
    if cmd == TEE_COMMAND:
        for token in non_env[1:]:
            if token.startswith("-"):
                continue
            _append_target(targets, token, "tee", repo_root)
        return
    if cmd.startswith(("python", "python3", "pypy")):
        _extract_python_inline_targets(non_env, targets, repo_root)
        return


def _extract_mutation_targets(command, repo_root):
    """Extract paths the command would mutate + classify against gated categories.

    Shell-aware: shlex-tokenizes first (respecting quotes), then walks the
    token list with ``BOUNDARY_TOKENS`` marking segment starts. Redirections
    are scanned across the whole token list; per-command heuristics run on
    each inter-boundary segment.
    """
    targets: list[dict] = []
    tokens = _tokenize(command)
    if not tokens:
        return targets

    _extract_redirect_targets(tokens, targets, repo_root)

    idx = 0
    while idx < len(tokens):
        # Find the end of this segment (next boundary operator or EOL).
        j = idx
        while j < len(tokens) and tokens[j] not in BOUNDARY_TOKENS:
            j += 1
        _process_segment(tokens[idx:j], targets, repo_root)
        idx = j + 1  # advance past the boundary token

    return targets


# ── Deny emission ──────────────────────────────────────────────────────────


def _deny(target, command):
    rel = target.get("path", "")
    category = target.get("category", "file")
    method = target.get("method", "bash mutation")
    owner = {
        "protected-artifact": _ARTIFACT_TOOL_HINT.get(
            os.path.basename(rel), "mcp__harness__write_*"),
        "workflow-control-surface": "maintain-skill",
        "source": "developer",
    }.get(category, "developer")
    docs = RULE_DOCS.get(category, "doc/harness/patterns/mcp-bash-guard.md")
    tail = (
        f"[gate={GATE_NAME} rule={category} "
        f"path={rel} owner={owner} docs={docs}]"
    )
    trimmed_cmd = command if len(command) <= 200 else (command[:197] + "...")
    human = (
        f"Direct Bash {category} mutation via {method}. "
        f"Use {owner} instead of editing via shell."
        f" Command: {trimmed_cmd}"
    )
    hint = _escape_hint(GATE_NAME)
    emit_permission_decision("deny", f"{tail} {human}\n{hint}")


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    # Escape hatch: one-shot allow + audit.
    if os.environ.get("HARNESS_SKIP_MCP_GUARD") == "1":
        data = read_hook_input()
        tool_input = data.get("tool_input") or {}
        cmd = tool_input.get("command", "")
        log_gate_bypass(GATE_NAME, cmd[:200])
        return 0

    data = read_hook_input()
    if not data:
        return 0
    if data.get("tool_name") != "Bash":
        return 0

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command", "")
    if not isinstance(command, str) or not command:
        return 0

    # Short-circuit extremely large commands to protect the 3 s timeout budget.
    if len(command) > _COMMAND_LENGTH_CAP:
        return 0

    repo_root = find_repo_root()
    targets = _extract_mutation_targets(command, repo_root)
    if targets:
        _deny(targets[0], command)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "mcp_bash_guard")
        except Exception:
            pass
        sys.exit(0)
