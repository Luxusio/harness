#!/usr/bin/env python3
from __future__ import annotations
import os
import ast
import glob
import itertools
import json
import re
import shlex
import shutil
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
        _is_runtime_provenance,
        _is_source_file,
        _is_workflow_control_surface,
        PROTECTED_ARTIFACTS,
    )
except Exception:
    sys.exit(0)
GATE_NAME = "mcp_bash_guard"
_COMMAND_LENGTH_CAP = 64 * 1024
_GUARD_STDIN_CAP = 128 * 1024
# Bound on how many matches a glob token is expanded to. Applied through
# islice over iglob, so it bounds the directory walk itself and not merely the
# result list — a pathological pattern must not turn a PreToolUse hook into a
# full filesystem traversal.
_GLOB_EXPANSION_CAP = 256
# Redirect operators are matched by shape, never by enumeration. An earlier
# `REDIRECT_TOKENS` set listed spellings, and `punctuation_chars=True` emits a
# whole operator as one token, so `_INLINE_REDIRECT_RE` captured the operator's
# own trailing punctuation as the "path" (`>|` matched with group(2) == "|") and
# the real target — the next token — was never inspected. `echo x >| PLAN.md`
# truncated a protected artifact through the gate, and enumerating the four
# spellings then known still missed `>>|`.
#
# Shape: optional fd digits, an optional leading `&`, one or two `>`, and an
# optional `|`/`&` suffix. A token that is entirely redirect punctuation carries
# no path, so its target is the following token. Do not reintroduce a set.
_PURE_REDIRECT_OP_RE = re.compile(r"^\d*&?>{1,2}[|&]?$")
# Commands that cannot themselves write a file. Naming a protected artifact or
# lifecycle symbol in their arguments (a grep pattern, an `echo` banner) is
# inspection, not mutation, so it must not deny the segment.
#
# Safety: this list only suppresses the *name-mention* heuristics. Redirections
# are detected independently by _extract_redirect_targets() over the token
# stream, so `echo x > RECEIPTS.jsonl` stays denied via its redirect target.
# Deliberately excluded because they can write: tee, dd, cp, mv, install,
# truncate, touch, ln, sed -i, perl -pi, awk (`print > "file"`), env (runs an
# arbitrary command), and sort/diff when given an output option.
NON_MUTATING_COMMANDS = {
    "echo", "printf", "true", "false", ":", "test", "[", "[[",
    "pwd", "date", "seq", "basename", "dirname", "realpath", "readlink",
    "file", "stat", "ls", "nl", "od", "strings", "cut", "comm", "uniq",
    "tr", "jq", "column",
}
_OUTPUT_OPTION_COMMANDS = {"sort", "diff"}
# Git subcommands that never rewrite a working-tree file. `add` and `commit`
# move content into the index and object store; they cannot change what a
# protected artifact contains on disk. Without these, harness lifecycle files
# could not even be staged: `git add plugin/scripts/background_hook.py` tripped
# the name-mention heuristic and was denied.
#
# Deliberately excluded because they DO rewrite the working tree:
# checkout, restore, rm, clean, mv, apply, stash, reset (--hard), revert,
# merge, rebase, cherry-pick, pull.
GIT_NON_MUTATING_SUBCOMMANDS = {
    "add", "commit", "diff", "show", "log", "status", "grep",
    "branch", "rev-parse", "ls-files", "check-ignore", "blame",
}
# Shell keywords and non-mutating builtins. These are not executables, so
# shutil.which() cannot resolve them and they were falling through to the
# "unrecognized executable with gated path" branch, denying every compound
# command that merely mentioned a gated path.
SHELL_CONTROL_WORDS = {
    "for", "while", "until", "if", "then", "else", "elif", "fi",
    "do", "done", "case", "esac", "select", "function", "time",
    "{", "}", "!", "cd", "pushd", "popd", "shift", "return",
    "local", "export", "set", "unset", "read", "declare", "typeset",
}


def _has_output_option(args):
    return any(
        arg in {"-o", "--output"} or arg.startswith(("-o", "--output="))
        for arg in args
    )


def _is_non_mutating_command(cmd, args):
    """Return True when this command word cannot write a file on its own."""
    if cmd in NON_MUTATING_COMMANDS or cmd in SHELL_CONTROL_WORDS:
        return True
    if cmd in _OUTPUT_OPTION_COMMANDS:
        return not _has_output_option(args)
    return False
# Words that may precede a real command without changing what it does. Dispatch
# is on the first token, and `_is_non_mutating_command` treats any
# SHELL_CONTROL_WORDS head as relief, so `time cp <payload> <receipt>` and
# `{ cp …; }` suppressed the entire mutation-verb dispatch, the
# lifecycle-entrypoint deny and the uninspected-inline-runtime deny in one call.
# Strip them and dispatch on what actually runs.
#
# Loop and conditional *headers* (`for`, `while`, `until`, `if`, `case`,
# `select`, `function`) stay out: their first operand is a variable or word
# list, not a command, and stripping them would classify it as one.
COMMAND_PREFIX_WORDS = {"time", "!", "{", "then", "else", "elif", "do", "command",
                        "builtin", "exec", "nohup", "nice"}
LAST_ARG_MUTATORS = {"cp", "mv", "install", "touch", "truncate", "rsync"}
TEE_COMMAND = "tee"
LIFECYCLE_RECEIPT_ENTRYPOINTS = {
    "background_hook.py", "subagent_lifecycle.py", "codex_lifecycle_watcher.py",
}
RECEIPT_MUTATION_SYMBOLS = {
    "record_subagent_receipt", "reset_receipt_streams_for_new_run",
    "restore_receipt_streams", "release_receipt_stream_reset",
    "receipt_stream_savepoint", "_bind_runtime_receipt_adapter",
    "write_task_control", "begin_task_run", "restore_task_control",
    "publish_task_close", "write_active_marker", "clear_active_marker",
    "restore_active_marker_snapshot",
}
GOAL_MUTATION_SYMBOLS = {
    "write_goal_state", "start_harness_goal", "add_goal_task",
    "finish_harness_goal", "handle_goal_start", "handle_goal_add_task",
    "handle_goal_finish", "goal_start", "goal_add_task", "goal_finish",
}
PROTECTED_MUTATION_SYMBOLS = RECEIPT_MUTATION_SYMBOLS | GOAL_MUTATION_SYMBOLS
UNINSPECTED_INLINE_RUNTIMES = dict(
    bun={"-e", "--eval"}, deno={"eval"}, lua={"-e"},
    node={"-e", "--eval", "-p", "--print"},
    nodejs={"-e", "--eval", "-p", "--print"}, perl={"-e", "-E"},
    php={"-r"}, ruby={"-e"}, awk={""}, gawk={""}, mawk={""},
)
BOUNDARY_TOKENS = {"&&", "||", "|", ";", "\n", "&"}
_INLINE_REDIRECT_RE = re.compile(r"^(?:\d*)?(>>?)(.+)$")
_ARTIFACT_TOOL_HINT = {
    "TASK.json": "harness task control MCP",
    "RECEIPTS.jsonl": "runtime review and QA lifecycle hook",
    "PLAN.md": "mcp__plugin_harness_harness__write_plan",
}
RULE_DOCS = {
    "protected-artifact": "doc/harness/patterns/mcp-bash-guard.md",
    "workflow-control-surface": "doc/harness/patterns/mcp-bash-guard.md",
    "source": "doc/harness/patterns/mcp-bash-guard.md",
}
def _is_env_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token or ""))
def _tokenize(command: str):
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
    if _is_runtime_provenance(candidate):
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
    if _is_runtime_provenance(path_value):
        return "protected-artifact"
    if _is_workflow_control_surface(path_value, repo_root=repo_root):
        return "workflow-control-surface"
    if _is_protected_artifact(path_value):
        return "protected-artifact"
    if _is_source_file(path_value, repo_root=repo_root):
        return "source"
    return ""
def _is_goal_control_inode_alias(token: str, repo_root: str, execution_cwd="") -> bool:
    raw = os.path.expanduser(str(token or "").strip().strip("'").strip('"'))
    if not raw:
        return False
    candidate = raw if os.path.isabs(raw) else os.path.join(
        execution_cwd or repo_root or os.getcwd(), raw,
    )
    try:
        candidate_info = os.lstat(candidate)
        if not stat.S_ISREG(candidate_info.st_mode) or candidate_info.st_nlink < 2:
            return False
        goals_dir = os.path.join(os.path.realpath(repo_root), "doc", "harness", "goals")
        with os.scandir(goals_dir) as entries:
            for entry in entries:
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                info = entry.stat(follow_symlinks=False)
                if (info.st_dev, info.st_ino) == (candidate_info.st_dev, candidate_info.st_ino):
                    return True
    except OSError:
        return False
    return False
def _glob_expansions(token, repo_root, execution_cwd=""):
    """Paths a glob token would resolve to at exec time.

    Classification is an exact basename match, but the shell expands the pattern
    *after* the gate has decided. `cp payload <task>/RECEIPT?.jsonl` therefore
    wrote a protected artifact while the gate saw an unremarkable basename.
    """
    if not isinstance(token, str) or not any(ch in token for ch in "*?["):
        return ()
    base = execution_cwd or repo_root or os.getcwd()
    pattern = token if os.path.isabs(token) else os.path.join(base, token)
    try:
        # islice over iglob: the cap must bound the directory walk, not just
        # trim its result.
        matches = tuple(itertools.islice(glob.iglob(pattern), _GLOB_EXPANSION_CAP))
    except (OSError, ValueError):
        return ()
    # A real file whose *name* contains a metacharacter matches its own pattern
    # (`glob("/tmp/x*y")` -> `["/tmp/x*y"]`). Returning it would re-enter with an
    # identical token; dropping it here keeps expansion finite.
    return tuple(match for match in matches if match != token)


def _append_target(targets, token, method, repo_root, execution_cwd=""):
    # Expand once, iteratively. Recursing here was a fail-open: a self-matching
    # glob name recursed until RecursionError, which main()'s catch-all swallowed
    # into sys.exit(0) — a silent allow for the *entire* command, not just the
    # decoy token. One `touch '/tmp/x*y'` disabled every deny the guard has.
    for expansion in _glob_expansions(token, repo_root, execution_cwd):
        _classify_and_append(
            targets, expansion, method, repo_root, execution_cwd,
        )
    _classify_and_append(targets, token, method, repo_root, execution_cwd)


def _classify_and_append(targets, token, method, repo_root, execution_cwd=""):
    path_value = _normalize_candidate_path(token, repo_root, execution_cwd)
    category = _classify_gated_path(path_value, repo_root)
    if not category and _is_goal_control_inode_alias(
        token, repo_root, execution_cwd,
    ):
        category = "protected-artifact"
        path_value = os.path.abspath(os.path.expanduser(str(token)))
    if not category:
        return
    item = {"path": path_value, "category": category, "method": method}
    if item not in targets:
        targets.append(item)
def _strip_command_prefix_words(argv):
    """Drop leading words that only decorate the command that follows."""
    index = 0
    while index < len(argv) and argv[index] in COMMAND_PREFIX_WORDS:
        index += 1
    return argv[index:] if index < len(argv) else argv[:0]


def _last_non_option(tokens):
    for token in reversed(tokens[1:]):
        if token.startswith("-"):
            continue
        return token
    return ""
def _embedded_path_candidates(tokens):
    visible = " ".join(tokens)
    candidates = re.findall(
        r"(?:~|/|\.?\.?/)?[A-Za-z0-9_.:-]+(?:/[A-Za-z0-9_.:-]+)+",
        visible,
    )
    candidates.extend(re.findall(
        r"(?:doc/harness|plugin(?:-codex)?|\.codex|\.claude)/[A-Za-z0-9_./:-]+",
        visible,
    ))
    return candidates
def _target_directory_option(non_env):
    """Destination named by `-t <dir>` / `--target-directory[=<dir>]`.

    Returns (destination, consumed_tokens). `_last_non_option` has no
    option-value model, so `cp -t <taskdir> <src>` made it return `<src>` as the
    destination — the real destination was never classified and the copy landed
    on a protected artifact with the gate silent.
    """
    tokens = list(non_env[1:])
    for index, token in enumerate(tokens):
        if token in {"-t", "--target-directory"}:
            if index + 1 < len(tokens):
                return tokens[index + 1], {token, tokens[index + 1]}
            return "", {token}
        if token.startswith("--target-directory="):
            return token.split("=", 1)[1], {token}
    return "", set()


def _expanded_sources(source, execution_cwd, repo_root):
    """Names a source contributes to the destination directory.

    `cp -r dir/. dest` and `cp -a dir/* dest` copy the directory's *contents*,
    so `<dest>/<source basename>` is wrong for them — the basename is `.` or a
    glob. Enumerate what would actually land.
    """
    base = execution_cwd or repo_root or os.getcwd()
    stripped = source.rstrip("/")
    if any(ch in source for ch in "*?["):
        pattern = source if os.path.isabs(source) else os.path.join(base, source)
        try:
            return [os.path.basename(m) for m in glob.glob(pattern)][
                :_GLOB_EXPANSION_CAP
            ]
        except (OSError, ValueError):
            return []
    if stripped.endswith("/.") or source.endswith("/"):
        directory = stripped[:-2] if stripped.endswith("/.") else stripped
        resolved = directory if os.path.isabs(directory) else os.path.join(
            base, directory,
        )
        try:
            return sorted(os.listdir(resolved))[:_GLOB_EXPANSION_CAP]
        except OSError:
            return []
    basename = os.path.basename(stripped)
    return [basename] if basename else []


def _append_directory_destination_targets(
    cmd, non_env, destination, targets, repo_root, execution_cwd="",
):
    """Classify what a copy into a directory would actually produce.

    `cp`, `mv`, `install` and `rsync` accept a directory destination and derive
    each filename from its source. That derived path is never a token, so
    last-operand classification saw only the directory and allowed the write.
    """
    option_destination, consumed = _target_directory_option(non_env)
    destination = option_destination or destination
    if not destination:
        return
    resolved = destination if os.path.isabs(destination) else os.path.join(
        execution_cwd or repo_root or os.getcwd(), destination,
    )
    try:
        if not os.path.isdir(resolved):
            return
    except OSError:
        return
    sources = [
        token for token in non_env[1:]
        if not token.startswith("-") and token != destination
        and token not in consumed
    ]
    for source in sources:
        for name in _expanded_sources(source, execution_cwd, repo_root):
            _append_target(
                targets, os.path.join(destination, name),
                f"{cmd} into directory", repo_root, execution_cwd,
            )


def _extract_redirect_targets(tokens, targets, repo_root, execution_cwd=""):
    for index, token in enumerate(tokens):
        if _PURE_REDIRECT_OP_RE.match(token) and index + 1 < len(tokens):
            _append_target(
                targets, tokens[index + 1], "shell redirection",
                repo_root, execution_cwd,
            )
            continue
        inline = _INLINE_REDIRECT_RE.match(token)
        if inline:
            candidate = inline.group(2).strip()
            # `&1`/`&2` are fd duplications, and a candidate made only of redirect
            # punctuation is the operator's own tail, not a path.
            if candidate and candidate not in ("&1", "&2") and candidate.strip("|&"):
                _append_target(
                    targets, candidate, "shell redirection",
                    repo_root, execution_cwd,
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
def _safe_lifecycle_source_inspection(argv):
    if not argv:
        return False
    cmd = os.path.basename(argv[0])
    args = argv[1:]
    if cmd in {"pytest", "py.test"}:
        return True
    if cmd == "git" and args and args[0] in GIT_NON_MUTATING_SUBCOMMANDS:
        return True
    if cmd in {"cat", "head", "tail", "rg", "grep", "less", "more", "wc"}:
        return True
    if cmd == "sed" and not any(arg == "-i" or arg.startswith("-i") for arg in args):
        return True
    return _is_non_mutating_command(cmd, args)
def _safe_gated_path_inspection(argv, raw_argv=()):
    if not argv:
        return False
    cmd, args = os.path.basename(argv[0]), argv[1:]
    # Shell keywords/builtins never resolve via which(); check them before the
    # executable-identity test so compound commands are not misread as an
    # unrecognized executable holding a gated path.
    if _is_non_mutating_command(cmd, args):
        return True
    resolved = shutil.which(cmd)
    if not resolved or os.sep in argv[0] and os.path.realpath(argv[0]) != os.path.realpath(resolved):
        return False
    if any(
        _is_env_assignment(arg) and arg.split("=", 1)[0] in {
            "PATH", "LESSOPEN", "LESSCLOSE", "GIT_EXTERNAL_DIFF", "GIT_PAGER",
        }
        for arg in raw_argv
    ):
        return False
    readers = {"cat", "file", "head", "ls", "more", "readlink", "realpath", "grep", "stat", "tail", "wc"}
    if cmd in readers:
        return True
    output_option = any(arg in {"-o", "--output"} or arg.startswith(("-o", "--output=")) for arg in args)
    if cmd == "less": return not any(arg.startswith(("-o", "-O")) for arg in args)
    if cmd == "rg": return not any(arg == "--pre" or arg.startswith("--pre=") for arg in args)
    if cmd == "git":
        index = 0
        while index < len(args) and (args[index].startswith("--") or args[index] == "-C"):
            index += 2 if args[index] == "-C" else 1
        return index < len(args) and args[index] in GIT_NON_MUTATING_SUBCOMMANDS and not output_option and not any("open-files-in-pager" in arg or arg in {"--ext-diff", "--textconv"} for arg in args)
    if cmd == "sed": return not any(arg in {"-i", "--in-place"} or arg.startswith(("-i", "--in-place=")) or re.search(r"(?:^|[;/0-9,$ ])(?:w|W|e)(?:\s|[A-Za-z0-9_.-]+/|$)", arg) for arg in args)
    if cmd == "diff": return not output_option
    if cmd == "find": return not any(arg.startswith(("-delete", "-exec", "-execdir", "-ok", "-okdir", "-fls", "-fprint")) for arg in args)
    return False
def _uninspected_inline(argv):
    flags = UNINSPECTED_INLINE_RUNTIMES.get(os.path.basename(argv[0]), set())
    if "" in flags and len(argv) > 1:
        return True
    return any(
        arg in flags
        or any(arg.startswith(flag + "=") for flag in flags if flag.startswith("--"))
        or any(
            arg.startswith("-") and not arg.startswith("--") and flag[1:] in arg[1:]
            for flag in flags if len(flag) == 2
        )
        for arg in argv[1:]
    )
def _inline_mutation_risk(argv):
    code = " ".join(argv[1:]).lower()
    if os.path.basename(argv[0]) in {"awk", "gawk", "mawk"}:
        return bool(re.search(r"\b(?:print|printf)\b[^;]*>{1,2}", code))
    return bool(re.search(
        r"(?:writefilesync|appendfilesync|copyfilesync|rename|replace|unlink|"
        r"remove|delete|truncate|chmod|hardlink|file\.write|file\.open|"
        r"\bopen\s*\([^)]*['\"](?:[wax]|r\+|>))",
        code,
    ))
def _gated_path_risk(tokens, repo_root, execution_cwd):
    candidates = _embedded_path_candidates(tokens)
    if any(_classify_gated_path(
        _normalize_candidate_path(value, repo_root, execution_cwd), repo_root,
    ) for value in candidates):
        return True
    compact = re.sub(r"[^a-z0-9]", "", " ".join(tokens).lower())
    return (
        "receiptsjsonl" in compact or "taskjson" in compact
        or "docharnessgoals" in compact and "json" in compact
        or "activesessions" in compact
    )
def _process_segment(segment_tokens, targets, repo_root, execution_cwd=""):
    if not segment_tokens:
        return
    idx = 0
    while idx < len(segment_tokens) and _is_env_assignment(segment_tokens[idx]):
        idx += 1
    if idx >= len(segment_tokens):
        return
    raw_argv = segment_tokens[idx:]
    non_env = _strip_command_prefix_words(_unwrap_execution(raw_argv))
    if not non_env:
        return
    cmd = os.path.basename(non_env[0])

    if cmd == "eval":
        nested = " ".join(non_env[1:])
        if nested:
            targets.extend(_extract_mutation_targets(nested, repo_root, execution_cwd))
        return

    if cmd in {"bash", "sh"} and "-c" in non_env[1:]:
        try:
            nested = non_env[non_env.index("-c") + 1]
        except IndexError:
            nested = ""
        if nested:
            if _gated_path_risk(raw_argv, repo_root, execution_cwd) and re.search(
                r"(?:write|append|unlink|rename|replace|remove|delete|truncate|chmod|>)",
                nested, re.I,
            ):
                targets.append({
                    "path": "doc/harness/goals/current.json",
                    "category": "protected-artifact",
                    "method": "nested runtime with gated environment",
                })
                return
            targets.extend(_extract_mutation_targets(
                nested, repo_root, execution_cwd,
            ))
            if targets:
                return

    # No execution-based deny. Invoking a lifecycle entrypoint, importing a
    # receipt writer, or running an uninspected inline runtime is not blocked
    # here — that is agent discipline, not a gate. What remains below is the
    # only thing this layer can actually decide: a command whose *verb* writes a
    # file, and whose target resolves to a protected path.

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
    if cmd == "cp" and any(
        option == "--link" or re.fullmatch(r"-[^-]*l[^-]*", option)
        for option in non_env[1:]
    ):
        for operand in non_env[1:]:
            if not operand.startswith("-"):
                _append_target(
                    targets, operand, "cp hard-link source",
                    repo_root, execution_cwd,
                )
        return
    if cmd in {"mv", "rm", "unlink", "chmod", "chown", "chgrp"}:
        for operand in non_env[1:]:
            if not operand.startswith("-"):
                _append_target(
                    targets, operand, f"{cmd} protected operand",
                    repo_root, execution_cwd,
                )
        if cmd == "mv":
            _append_directory_destination_targets(
                cmd, non_env, _last_non_option(non_env), targets,
                repo_root, execution_cwd,
            )
        return
    if cmd in LAST_ARG_MUTATORS:
        destination = _last_non_option(non_env)
        _append_target(targets, destination, cmd, repo_root, execution_cwd)
        # A directory destination hides the effective target: `cp forged
        # doc/harness/tasks/T/` writes `<dir>/<source basename>`, a path that
        # never appears as a token, so classifying only the last operand let a
        # one-call receipt forgery through. Reconstruct what each source would
        # land on.
        _append_directory_destination_targets(
            cmd, non_env, destination, targets, repo_root, execution_cwd,
        )
        return
    if cmd in {"ln", "link"}:
        operands = [token for token in non_env[1:] if not token.startswith("-")]
        for source in operands:
            _append_target(
                targets, source, f"{cmd} protected source", repo_root, execution_cwd
            )
        return
    if cmd == TEE_COMMAND:
        for token in non_env[1:]:
            if token.startswith("-"):
                continue
            _append_target(targets, token, "tee", repo_root, execution_cwd)
        return
    if cmd == "dd":
        for token in non_env[1:]:
            if token.startswith("of="):
                _append_target(
                    targets, token[3:], "dd output", repo_root, execution_cwd,
                )
        return
    if cmd.startswith(("python", "python3", "pypy")):
        # Inline `python -c` code is not inspected. Reading program semantics
        # off a command line was a losing game: every round of review found
        # another spelling (command substitution quoted and unquoted, `$VAR`,
        # base64/`exec`, computed imports, `os.system`, argv-list subprocess),
        # and two of the attempted fixes were themselves worse than the gap —
        # one denied ordinary `subprocess.run(['pytest', path])`, another
        # recursed until the whole guard failed open. What a program does once
        # it starts is left to agent discipline, exactly as script execution is.
        return
    if cmd == "git":
        # git is not a mutation verb in general, but `checkout`, `restore` and
        # `rm` rewrite the working tree, which is file mutation rather than
        # execution. Everything in GIT_NON_MUTATING_SUBCOMMANDS moves content
        # into the index or object store and cannot change a protected file on
        # disk, so `git add plugin/scripts/background_hook.py` stays allowed.
        # `-C <dir>` and `-c <k=v>` take a value, so the first non-option token
        # is not necessarily the subcommand: `git -C <root> diff` would read
        # `<root>` as the subcommand and deny a read-only diff.
        value_options = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}
        index = 1
        while index < len(non_env) and non_env[index].startswith("-"):
            index += 2 if non_env[index] in value_options else 1
        subcommand = non_env[index] if index < len(non_env) else ""
        if subcommand and subcommand not in GIT_NON_MUTATING_SUBCOMMANDS:
            for operand in non_env[index + 1:]:
                if not operand.startswith("-") and operand != "--":
                    _append_target(
                        targets, operand, f"git {subcommand}",
                        repo_root, execution_cwd,
                    )
        return
    # `node -e`, `perl -e`, `ruby -e` and friends are not inspected either, for
    # the same reason inline python is not. And an unrecognized executable that
    # merely *carries* a gated path is not denied: that branch could not tell
    # `ruff check <guard>` or `git checkout <guard>` from a write, so it blocked
    # read-only tooling and even reverting this file.
def _extract_mutation_targets(command, repo_root, execution_cwd=""):
    targets: list[dict] = []
    tokens = _tokenize(command)
    if not tokens:
        return targets

    _extract_redirect_targets(tokens, targets, repo_root, execution_cwd)
    if ("$(" in command or "`" in command) and _gated_path_risk(
        tokens, repo_root, execution_cwd,
    ) and re.search(
        r"(?:--junitxml|--output|-o(?:\S|\s))", command,
    ):
        targets.append({
            "path": "doc/harness/goals/current.json",
            "category": "protected-artifact",
            "method": "unresolved dynamic output path",
        })
        return targets

    # `shlex(whitespace_split=True)` consumes newlines as whitespace and never
    # emits them, so the "\n" entry in BOUNDARY_TOKENS never matched and a
    # multi-line command collapsed into ONE segment. `_process_segment`
    # dispatches on the first command word, so any mutator on a later line was
    # never classified: a leading `echo start` was enough to walk `cp`, `tee`,
    # `sed -i` or `dd` straight onto a protected artifact. It cut the other way
    # too — a heredoc whose later line named a lifecycle symbol denied the whole
    # call. Walk each line separately; splitting only ever adds segments, so it
    # cannot create a new allow. Assignments carry forward across lines, as in a
    # real shell.
    shell_values: dict[str, str] = {}
    for line in _unquoted_lines(command):
        line_tokens = _tokenize(line) if line.strip() else []
        if line_tokens:
            _walk_segments(
                line_tokens, shell_values, targets, repo_root, execution_cwd,
            )

    return targets


def _unquoted_lines(command):
    """Split on newlines that are outside quotes.

    Splitting the raw text turned the body of a quoted multi-line argument into
    command segments: a `git commit -m` whose message named a lifecycle symbol
    was denied as a RECEIPTS.jsonl mutation — a command that mutates nothing,
    and this repo's own commit convention.
    """
    lines = []
    current = []
    quote = ""
    for char in command:
        if quote:
            if char == quote:
                quote = ""
            current.append(char)
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            continue
        if char == "\n":
            lines.append("".join(current))
            current = []
            continue
        current.append(char)
    lines.append("".join(current))
    return lines


def _walk_segments(tokens, shell_values, targets, repo_root, execution_cwd=""):
    idx = 0
    while idx < len(tokens):
        j = idx
        while j < len(tokens) and tokens[j] not in BOUNDARY_TOKENS:
            j += 1
        segment = tokens[idx:j]
        assignments = [token for token in segment if _is_env_assignment(token)]
        if assignments and len(assignments) == len(segment):
            for token in assignments:
                name, value = token.split("=", 1)
                shell_values[name] = value
        local_values = dict(shell_values)
        for token in segment:
            if not _is_env_assignment(token):
                break
            name, value = token.split("=", 1)
            local_values[name] = value
        expanded = []
        for token in segment:
            value = re.sub(
                r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}|\$([A-Za-z_][A-Za-z0-9_]*)",
                lambda match: local_values.get(match.group(1) or match.group(2), match.group(0)),
                token,
            )
            expanded.append(value)
        _process_segment(expanded, targets, repo_root, execution_cwd)
        idx = j + 1
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
    base = os.path.basename(rel)
    _NEXT = {
        "TASK.json": "Use task_start, write_plan, task_blocked, or task_close",
        "PLAN.md": "mcp__plugin_harness_harness__write_plan",
        "RECEIPTS.jsonl": "Spawn and await the required reviewer or QA agent; lifecycle hooks record this file",
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
        try:
            from _lib import log_gate_crash, last_hook_input
            log_gate_crash(exc, "mcp_bash_guard", last_hook_input())
        except Exception:
            try:
                _log_gate_error(exc, "mcp_bash_guard")
            except Exception:
                pass
        sys.exit(0)
