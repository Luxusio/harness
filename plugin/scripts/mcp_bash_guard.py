#!/usr/bin/env python3
"""PreToolUse hook (matcher: Bash) — block direct Bash file mutations.

Closes the Bash-layer bypass where agents write to source / protected-artifact
/ workflow-control-surface paths via ``sed -i``, ``cp``, ``mv``, ``tee``,
shell redirection, ``python -c "open(...,'w')"``, etc.

Signalling contract matches ``prewrite_gate.py``: deny via stdout JSON envelope
with exit 0; silent on allow; fail-open on unexpected exceptions.

Escape hatch: ``HARNESS_SKIP_MCP_GUARD=1`` → one-shot allow + log ``gate-bypass``.

Known gaps (documented in doc/harness/patterns/mcp-bash-guard.md):
  - ``eval "sed -i ..."`` and command substitution ``$(...)`` / backticks.
  - Symlink resolution (``os.path.realpath``) — not applied before classification.
"""
from __future__ import annotations

import os
import ast
import json
import re
import shlex
import stat
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        emit_permission_decision,
        _log_gate_error,
        _escape_hint,
        log_gate_bypass,
        find_repo_root,
        find_harness_root,
        harness_root_resolution,
        is_harness_enabled_repo,
    )
    from prewrite_gate import (
        _is_protected_artifact,
        _is_claude_subagent_transcript,
        _is_source_file,
        _is_workflow_control_surface,
        PROTECTED_ARTIFACTS,
    )
except Exception:
    sys.exit(0)


GATE_NAME = "mcp_bash_guard"
_COMMAND_LENGTH_CAP = 64 * 1024  # short-circuit extremely large commands
_GUARD_STDIN_CAP = 128 * 1024

REDIRECT_TOKENS = {">", ">>", "1>", "1>>"}
# Note: 2> stderr redirect is intentionally NOT blocked — logs are common.

LAST_ARG_MUTATORS = {"cp", "mv", "install", "touch", "truncate"}
TEE_COMMAND = "tee"
LIFECYCLE_RECEIPT_ENTRYPOINTS = {
    "background_hook.py", "subagent_lifecycle.py", "codex_lifecycle_watcher.py",
}
LIFECYCLE_RECEIPT_MODULES = {
    os.path.splitext(name)[0] for name in LIFECYCLE_RECEIPT_ENTRYPOINTS
} | {"_lib"}
RECEIPT_MUTATION_SYMBOLS = {
    "record_subagent_receipt", "reset_receipt_streams_for_new_run",
    "restore_receipt_streams", "release_receipt_stream_reset",
    "receipt_stream_savepoint", "_bind_runtime_receipt_adapter",
}

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
    "TASK.json": "harness task control MCP",
    "RECEIPTS.jsonl": "runtime review and QA lifecycle hook",
    "TASK_BASELINE.json": "task-start runtime",
    "PLAN.md": "mcp__plugin_harness_harness__write_plan",
    "CONVERSATION.md": "runtime conversation hook",
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


def _normalize_candidate_path(
    token: str, repo_root: str = "", execution_cwd: str = ""
) -> str:
    value = os.path.expanduser(str(token or "").strip().strip("'").strip('"'))
    if not value:
        return ""
    value = value.rstrip(",)")
    cwd = os.path.realpath(execution_cwd or repo_root or os.getcwd())
    root = os.path.realpath(repo_root or cwd)
    candidate = os.path.realpath(
        value if os.path.isabs(value) else os.path.join(cwd, value)
    )
    if _is_claude_subagent_transcript(candidate):
        return candidate
    try:
        if os.path.commonpath((root, candidate)) != root:
            return ""
    except ValueError:
        return ""
    return os.path.relpath(candidate, root)


def _classify_gated_path(path_value: str, repo_root: str) -> str:
    if not path_value:
        return ""
    if _is_claude_subagent_transcript(path_value):
        return "protected-artifact"
    if _is_workflow_control_surface(path_value, repo_root=repo_root):
        return "workflow-control-surface"
    if _is_protected_artifact(path_value):
        return "protected-artifact"
    if _is_source_file(path_value, repo_root=repo_root):
        return "source"
    return ""


def _append_target(targets, token, method, repo_root, execution_cwd=""):
    path_value = _normalize_candidate_path(token, repo_root, execution_cwd)
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


def _extract_redirect_targets(tokens, targets, repo_root, execution_cwd=""):
    for index, token in enumerate(tokens):
        if token in REDIRECT_TOKENS and index + 1 < len(tokens):
            _append_target(
                targets, tokens[index + 1], "shell redirection",
                repo_root, execution_cwd,
            )
            continue
        inline = _INLINE_REDIRECT_RE.match(token)
        if inline:
            candidate = inline.group(2).strip()
            if candidate and candidate not in ("&1", "&2"):
                _append_target(
                    targets, candidate, "shell redirection",
                    repo_root, execution_cwd,
                )


def _extract_python_inline_targets(tokens, targets, repo_root, execution_cwd=""):
    if "-c" not in tokens:
        return
    try:
        code = tokens[tokens.index("-c") + 1]
    except IndexError:
        return
    for pat in _PY_PATTERNS:
        for match in pat.findall(code):
            _append_target(
                targets, match, "python inline write", repo_root, execution_cwd
            )


def _unwrap_execution(tokens):
    """Remove supported command wrappers and return the actual executable argv."""
    argv = list(tokens)
    while argv:
        wrapper = os.path.basename(argv[0])
        if wrapper == "env":
            index = 1
            while index < len(argv):
                token = argv[index]
                if token in {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}:
                    index += 2
                elif token.startswith("-") or _is_env_assignment(token):
                    index += 1
                else:
                    break
            argv = argv[index:]
            continue
        if wrapper == "uv":
            try:
                argv = argv[argv.index("run") + 1:]
            except ValueError:
                return []
            value_options = {
                "--directory", "--project", "--python", "--with", "--with-editable",
                "--with-requirements", "--index", "--default-index", "--index-strategy",
                "--resolution", "--prerelease", "--fork-strategy", "--config-file",
                "--env-file", "-C", "-p",
            }
            while argv and argv[0].startswith("-"):
                option = argv[0].split("=", 1)[0]
                argv = argv[2:] if option in value_options and "=" not in argv[0] else argv[1:]
            continue
        if wrapper in {"command", "exec"}:
            argv = argv[1:]
            while argv and argv[0].startswith("-"):
                argv = argv[1:]
            continue
        break
    return argv


def _python_code_exposes_lifecycle(code):
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return False

    def string_value(node):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left, right = string_value(node.left), string_value(node.right)
            return left + right if left is not None and right is not None else None
        return None

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            if any(alias.name.rsplit(".", 1)[-1] in LIFECYCLE_RECEIPT_MODULES for alias in node.names):
                return True
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").rsplit(".", 1)[-1] in LIFECYCLE_RECEIPT_MODULES:
                return True
            if any(alias.name in RECEIPT_MUTATION_SYMBOLS for alias in node.names):
                return True
        elif isinstance(node, ast.Attribute):
            if (
                node.attr in LIFECYCLE_RECEIPT_MODULES
                or node.attr in RECEIPT_MUTATION_SYMBOLS
                or "receipt_stream" in node.attr and "append" in node.attr
            ):
                return True
        elif isinstance(node, ast.Name) and (
            node.id in RECEIPT_MUTATION_SYMBOLS
            or "receipt_stream" in node.id and "append" in node.id
        ):
            return True
        elif isinstance(node, ast.Call):
            func_name = getattr(node.func, "id", "") or getattr(node.func, "attr", "")
            if func_name in {"exec", "eval", "compile"}:
                return True
            if func_name in {"__import__", "import_module"}:
                imported = string_value(node.args[0]) if node.args else None
                if imported is None:
                    return True
                if imported and imported.rsplit(".", 1)[-1] in LIFECYCLE_RECEIPT_MODULES:
                    return True
                for keyword in node.keywords:
                    if keyword.arg == "fromlist" and any(
                        string_value(item) in LIFECYCLE_RECEIPT_MODULES
                        for item in getattr(keyword.value, "elts", [])
                    ):
                        return True
            if func_name == "getattr" and len(node.args) > 1:
                attribute = string_value(node.args[1])
                if attribute is None or attribute in RECEIPT_MUTATION_SYMBOLS:
                    return True
    return False


def _protected_lifecycle_execution(argv):
    if not argv:
        return False
    cmd = os.path.basename(argv[0])
    args = argv[1:]
    if cmd in LIFECYCLE_RECEIPT_ENTRYPOINTS:
        return True
    if cmd.startswith(("python", "pypy")):
        if "-m" in args:
            try:
                module = args[args.index("-m") + 1]
            except IndexError:
                return False
            return module.rsplit(".", 1)[-1] in LIFECYCLE_RECEIPT_MODULES
        if "-c" in args:
            try:
                code = args[args.index("-c") + 1]
            except IndexError:
                return False
            return _python_code_exposes_lifecycle(code)
        value_options = {"-X", "-W", "-Q", "--check-hash-based-pycs"}
        index = 0
        while index < len(args) and args[index].startswith("-"):
            option = args[index]
            if option in value_options:
                index += 2
            else:
                index += 1
        script = args[index] if index < len(args) else ""
        if not script or script == "-":
            return True
        if os.path.basename(script) in LIFECYCLE_RECEIPT_ENTRYPOINTS:
            return True
        if script:
            try:
                info = os.lstat(script)
                if (
                    stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and not info.st_mode & 0o022
                    and info.st_size <= _COMMAND_LENGTH_CAP
                ):
                    with open(script, "r", encoding="utf-8") as handle:
                        code = handle.read(_COMMAND_LENGTH_CAP + 1)
                    if len(code) <= _COMMAND_LENGTH_CAP and _python_code_exposes_lifecycle(code):
                        return True
                else:
                    return True
            except (OSError, UnicodeError):
                return True
        return False
    if cmd in {"bash", "sh"}:
        script = next((arg for arg in args if not arg.startswith("-")), "")
        return os.path.basename(script) in LIFECYCLE_RECEIPT_ENTRYPOINTS
    return False


def _safe_lifecycle_source_inspection(argv):
    if not argv:
        return False
    cmd = os.path.basename(argv[0])
    args = argv[1:]
    if cmd in {"pytest", "py.test"}:
        return True
    if cmd == "git" and args and args[0] in {"diff", "show", "log", "status", "grep"}:
        return True
    if cmd in {"cat", "head", "tail", "rg", "grep", "less", "more", "wc"}:
        return True
    if cmd == "sed" and not any(arg == "-i" or arg.startswith("-i") for arg in args):
        return True
    return False


def _process_segment(segment_tokens, targets, repo_root, execution_cwd=""):
    """Classify a single command segment (between shell operators)."""
    if not segment_tokens:
        return
    # Skip leading env assignments (fixes `FOO=bar sed -i ...` bypass).
    idx = 0
    while idx < len(segment_tokens) and _is_env_assignment(segment_tokens[idx]):
        idx += 1
    if idx >= len(segment_tokens):
        return
    raw_argv = segment_tokens[idx:]
    non_env = _unwrap_execution(raw_argv)
    if not non_env:
        return
    cmd = os.path.basename(non_env[0])

    if cmd in {"bash", "sh"} and "-c" in non_env[1:]:
        try:
            nested = non_env[non_env.index("-c") + 1]
        except IndexError:
            nested = ""
        if nested:
            targets.extend(_extract_mutation_targets(
                nested, repo_root, execution_cwd,
            ))
            if targets:
                return

    visible = " ".join(raw_argv)
    compact = re.sub(r"[^A-Za-z0-9]", "", visible).lower()
    protected_marker = (
        any(name.replace("_", "").replace(".py", "") in compact
            for name in LIFECYCLE_RECEIPT_ENTRYPOINTS)
        or any(name.replace("_", "") in compact for name in RECEIPT_MUTATION_SYMBOLS)
    )
    if (
        (protected_marker and not _safe_lifecycle_source_inspection(non_env))
        or _protected_lifecycle_execution(non_env)
    ):
        targets.append({
            "path": "RECEIPTS.jsonl",
            "category": "protected-artifact",
            "method": "direct lifecycle receipt entrypoint invocation",
        })
        return

    if cmd == "sed" and any(t == "-i" or t.startswith("-i") for t in non_env[1:]):
        _append_target(
            targets, _last_non_option(non_env), "sed -i",
            repo_root, execution_cwd,
        )
        return
    if cmd == "perl" and any(t == "-pi" or t.startswith("-pi") for t in non_env[1:]):
        _append_target(
            targets, _last_non_option(non_env), "perl -pi",
            repo_root, execution_cwd,
        )
        return
    if cmd in LAST_ARG_MUTATORS:
        _append_target(
            targets, _last_non_option(non_env), cmd, repo_root, execution_cwd
        )
        return
    if cmd == TEE_COMMAND:
        for token in non_env[1:]:
            if token.startswith("-"):
                continue
            _append_target(targets, token, "tee", repo_root, execution_cwd)
        return
    if cmd.startswith(("python", "python3", "pypy")):
        _extract_python_inline_targets(
            non_env, targets, repo_root, execution_cwd
        )
        return


def _extract_mutation_targets(command, repo_root, execution_cwd=""):
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

    _extract_redirect_targets(tokens, targets, repo_root, execution_cwd)

    idx = 0
    while idx < len(tokens):
        # Find the end of this segment (next boundary operator or EOL).
        j = idx
        while j < len(tokens) and tokens[j] not in BOUNDARY_TOKENS:
            j += 1
        _process_segment(tokens[idx:j], targets, repo_root, execution_cwd)
        idx = j + 1  # advance past the boundary token

    return targets


# ── Deny emission ──────────────────────────────────────────────────────────


def _deny(target, command):
    rel = target.get("path", "")
    category = target.get("category", "file")
    method = target.get("method", "bash mutation")
    owner = {
        "protected-artifact": _ARTIFACT_TOOL_HINT.get(
            os.path.basename(rel), "mcp__plugin_harness_harness__write_*"),
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
    # 2026-05-12 gate-friction retro: include next_action_command so the
    # orchestrator gets an actionable resolution path inline.
    base = os.path.basename(rel)
    _NEXT = {
        "TASK.json": "Use task_start, write_plan, task_blocked, or task_close",
        "PLAN.md": "mcp__plugin_harness_harness__write_plan",
        "RECEIPTS.jsonl": "Spawn and await the required reviewer or QA agent; lifecycle hooks record this file",
        "CONVERSATION.md": "Let the runtime hooks record conversation history",
    }
    next_action = _NEXT.get(base, "")
    if not next_action and category == "source":
        next_action = "Invoke $harness:run and let its developer phase perform the edit"
    if not next_action and category == "workflow-control-surface":
        next_action = ("Create doc/harness/tasks/<active-task>/MAINTENANCE marker "
                       "and handle through the active harness task's close-time Self-Healing Candidates")
    emit_permission_decision(
        "deny", f"{tail} {human}\n{hint}",
        next_action_command=next_action,
        owner_skill=owner,
        docs=docs,
    )


# ── Main ───────────────────────────────────────────────────────────────────


def main():
    try:
        raw_input = sys.stdin.read(_GUARD_STDIN_CAP + 1)
    except Exception:
        raw_input = ""
    if len(raw_input) > _GUARD_STDIN_CAP:
        _deny({
            "path": "RECEIPTS.jsonl",
            "category": "protected-artifact",
            "method": "uninspectable oversized hook payload",
        }, "oversized hook payload")
        return 0
    try:
        parsed_input = json.loads(raw_input) if raw_input else {}
        data = parsed_input if isinstance(parsed_input, dict) else {}
    except (TypeError, ValueError):
        data = {}

    # Escape hatch: one-shot allow + audit.
    if os.environ.get("HARNESS_SKIP_MCP_GUARD") == "1":
        tool_input = data.get("tool_input") or {}
        cmd = tool_input.get("command", "")
        log_gate_bypass(GATE_NAME, cmd[:200])
        return 0

    if not data:
        return 0
    if data.get("tool_name") not in ("Bash", "shell"):
        return 0

    tool_input = data.get("tool_input") or {}
    command = tool_input.get("command") or tool_input.get("cmd") or ""
    if not isinstance(command, str) or not command:
        return 0

    payload_cwd = str(data.get("cwd") or "").strip()
    hook_cwd = os.path.realpath(payload_cwd or os.getcwd())
    if payload_cwd:
        harness_root, harness_error = harness_root_resolution(hook_cwd)
        repo_root = harness_root or find_repo_root(hook_cwd)
    else:
        candidate_root = find_repo_root()
        harness_root, harness_error = harness_root_resolution(candidate_root)
        repo_root = harness_root or candidate_root
    if harness_error:
        _deny(
            {
                "path": ".",
                "category": "workflow-control-surface",
                "method": f"invalid Harness workspace: {harness_error}",
            },
            command,
        )
        return 0
    if not is_harness_enabled_repo(repo_root):
        return 0
    if len(command) > _COMMAND_LENGTH_CAP:
        _deny({
            "path": "RECEIPTS.jsonl",
            "category": "protected-artifact",
            "method": "uninspectable oversized command",
        }, command[:200])
        return 0
    targets = _extract_mutation_targets(command, repo_root, hook_cwd)
    if targets:
        _deny(targets[0], command)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        # AC-007: payload-aware crash log (gate-crash).
        try:
            from _lib import log_gate_crash, last_hook_input
            log_gate_crash(exc, "mcp_bash_guard", last_hook_input())
        except Exception:
            try:
                _log_gate_error(exc, "mcp_bash_guard")
            except Exception:
                pass
        sys.exit(0)
