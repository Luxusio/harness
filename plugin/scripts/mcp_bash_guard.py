#!/usr/bin/env python3
from __future__ import annotations
import os
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
_OUTPUT_OPTION_COMMANDS = {"sort", "diff"}
# Git subcommands that never rewrite a working-tree file. `add` and `commit`
# move content into the index and object store; they cannot change what a
# protected artifact contains on disk. Without these, harness lifecycle files
# could not even be staged: `git add plugin/scripts/background_hook.py` tripped
# the name-mention heuristic and was denied.
#
# Deliberately excluded because they DO rewrite the working tree:
# checkout, restore, rm, clean, mv, apply, stash, reset (--hard), revert,
# merge, rebase, cherry-pick, pull. `restore` carries one exception handled in
# the git branch: `--staged` without `--worktree` only unstages, so it belongs
# with `add` rather than here.
GIT_NON_MUTATING_SUBCOMMANDS = {
    "add", "commit", "diff", "show", "log", "status", "grep",
    "branch", "rev-parse", "ls-files", "check-ignore", "blame",
}
# Words that may precede a real command without changing what it does. Dispatch
# is on the first token, so a decorating prefix used to select the relief path
# and suppress every verb branch: `time cp <payload> <receipt>` and `{ cp …; }`
# wrote a protected artifact with the guard silent. Strip them and dispatch on
# what actually runs.
#
# Loop and conditional *headers* (`for`, `while`, `until`, `if`, `case`,
# `select`, `function`) stay out: their first operand is a variable or word
# list, not a command, and stripping them would classify it as one.
COMMAND_PREFIX_WORDS = {"time", "!", "{", "then", "else", "elif", "do", "command",
                        "builtin", "exec", "coproc"}
# Wrappers whose tail is a whole command, mapped to their value-taking options.
# `sudo cp /tmp/f <receipt>` and `xargs -I{} cp /tmp/f <receipt>` put both the
# verb and the target in the token stream; without unwrapping, dispatch saw the
# wrapper as the command word and classified nothing.
_COMMAND_CARRYING_WRAPPERS = {
    "sudo": {"-u", "--user", "-g", "--group", "-C", "--close-from", "-p", "--prompt"},
    "doas": {"-u", "-C"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "stdbuf": {"-i", "-o", "-e", "--input", "--output", "--error"},
    "setsid": set(),
    "xargs": {"-I", "-i", "--replace", "-n", "--max-args", "-P", "--max-procs",
              "-d", "--delimiter", "-E", "-s", "--max-chars", "-a", "--arg-file"},
    "ionice": {"-c", "-n", "-p"},
    "chroot": set(),
    # `nice`/`nohup` belong here, not in COMMAND_PREFIX_WORDS: that list advances
    # only while the token *is* a prefix word, so `nice -n5 cp <receipt>` left
    # `-n5` as the command word and no verb branch ran. Their sibling `ionice`
    # was already modelled with value options; these two were not.
    "nice": {"-n", "--adjustment"},
    "nohup": set(),
}
# Verbs whose destination is the last operand (the others are sources).
LAST_ARG_MUTATORS = {"cp", "mv", "install", "rsync"}
# Verbs that rewrite EVERY file operand. `touch <receipt> /tmp/pad` and
# `truncate -s0 <receipt> /tmp/pad` both hit the receipt, so last-operand
# classification let one extra filename walk the real target.
ALL_OPERAND_MUTATORS = {"touch", "truncate"}
TEE_COMMAND = "tee"
# `(` and `)` are boundaries, not command words. `punctuation_chars=True` emits
# `(` as its own token, so without this a subshell made the segment's command
# word `"("`, dispatch fell through, and `( cp /tmp/f <receipt> )` wrote a
# protected artifact with the guard silent. Splitting only ever adds segments,
# so it cannot create an allow.
# No `"\n"` member: `shlex(whitespace_split=True)` never emits one, which is why
# `_unquoted_lines` splits the raw text first.
BOUNDARY_TOKENS = {"&&", "||", "|", ";", "&", "(", ")",
                   # `punctuation_chars=True` emits these as single tokens too.
                   # Missing them was worse than a laundered follower: a
                   # trailing `|& cat` moved `_last_non_option` off the real
                   # destination, so a plain `cp <payload> <receipt> |& cat`
                   # allowed where `cp <payload> <receipt>` denies.
                   "|&", ";;", ";&", ";;&"}
# Shells whose `-c` argument is another command line to inspect.
NESTED_SHELLS = {"bash", "sh", "dash", "zsh", "ksh", "ash", "busybox"}
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


def _perl_cluster_switches(token):
    """Single-letter perl switches in one option token.

    Several perl switches swallow the rest of the token as their value, so a
    substring test was wrong: `-Iinc` was read as containing `-i` and denied a
    read-only `perl -Iinc -pe print <file>`. Stop at the first value-taking
    switch.
    """
    if not token.startswith("-") or token.startswith("--"):
        return ""
    switches = []
    for char in token[1:]:
        if char in "IMmeEFxDS":
            break
        if not char.isalpha():
            break  # e.g. the `.bak` suffix of `-pi.bak`
        switches.append(char)
    return "".join(switches)


def _append_operands(
    targets, non_env, method, repo_root, execution_cwd="",
    skip_first=False, value_options=frozenset(),
):
    """Classify every non-option operand, not just the last one."""
    skipped = not skip_first
    index = 1
    while index < len(non_env):
        token = non_env[index]
        if token == "--":
            index += 1
            continue
        if token.startswith("-"):
            index += 2 if token in value_options else 1
            continue
        if not skipped:
            skipped = True
            index += 1
            continue
        _append_target(targets, token, method, repo_root, execution_cwd)
        index += 1


def _nested_shell_script(non_env):
    """The script string a nested shell would run, or "".

    Matching a standalone `-c` token and taking the next one missed the most
    ordinary spellings: `bash -lc "…"` clusters the flags, `bash -c -- "…"`
    puts `--` where the script was expected, and `dash`/`zsh`/`ksh` were not
    recognized as shells at all.
    """
    tokens = list(non_env[1:])
    # `busybox sh -c …`: the applet name is an ordinary operand, so recognizing
    # `busybox` as a shell alone changed nothing.
    if os.path.basename(non_env[0]) == "busybox" and tokens \
            and tokens[0] in NESTED_SHELLS:
        tokens = tokens[1:]
    # Shell options that take a separate value. Returning at the first
    # non-option token treated that value as the script, so
    # `bash -o errexit -c '<write>'` classified nothing at all.
    value_options = {"-o", "+o", "-O", "+O", "--rcfile", "--init-file"}
    saw_command_flag = False
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if not token.startswith("-"):
            return token if saw_command_flag else ""
        if token == "--":
            index += 1
            continue
        if token in value_options:
            index += 2
            continue
        if token == "--command" or token.startswith("--command="):
            if "=" in token:
                return token.split("=", 1)[1]
            saw_command_flag = True
        elif not token.startswith("--") and token.endswith("c"):
            # A short-option cluster runs the script only when `c` is last:
            # `-lc`, `-ec`, `-xc`. `-cl` would consume `l` as the script.
            saw_command_flag = True
        index += 1
    return ""


_INPUT_REDIRECT_OP_RE = re.compile(r"^\d*<{1,3}[&|]?$")


def _strip_redirect_syntax(tokens):
    """Remove redirect operators and their operands from a segment.

    Redirect operands are not the verb's operands, but they stayed in the token
    list, so `_last_non_option` returned the redirect target: a plain
    `cp <src> <receipt> 2>/dev/null` picked `/dev/null` as the destination and
    allowed, while the same command without the redirect denies. The fd digit is
    its own token under `punctuation_chars=True`, so a bare trailing digit has
    to go with it.

    Redirect *targets* are still classified — `_extract_redirect_targets` runs
    over the untrimmed segment.
    """
    kept = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if _PURE_REDIRECT_OP_RE.match(token) or _INPUT_REDIRECT_OP_RE.match(token):
            if kept and re.fullmatch(r"\d+", kept[-1]):
                kept.pop()
            index += 2  # the operator and its operand
            continue
        if _INLINE_REDIRECT_RE.match(token) and not os.path.isabs(token):
            index += 1
            continue
        kept.append(token)
        index += 1
    return kept


def _last_non_option(tokens):
    for token in reversed(tokens[1:]):
        if token.startswith("-"):
            continue
        return token
    return ""
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
                # `exec -a <name> <cmd>`: `-a` takes a value, so skipping only
                # the flag left `<name>` as the command word.
                argv = argv[2:] if argv[0] == "-a" and len(argv) > 1 else argv[1:]
            continue
        # Wrappers that carry a complete command as their tail. Both the verb
        # and the protected target are ordinary tokens here — this is the kept
        # verb class wearing a one-word decoration, not an execution route.
        if wrapper in _COMMAND_CARRYING_WRAPPERS:
            value_options = _COMMAND_CARRYING_WRAPPERS[wrapper]
            index = 1
            while index < len(argv) and argv[index].startswith("-"):
                option = argv[index].split("=", 1)[0]
                if option in value_options and "=" not in argv[index]:
                    index += 2
                else:
                    index += 1
            if wrapper == "timeout" and index < len(argv):
                # `timeout <duration> <command>`: the duration is not an option.
                index += 1
            argv = argv[index:]
            continue
        break
    return argv
def _process_segment(segment_tokens, targets, repo_root, execution_cwd=""):
    if not segment_tokens:
        return
    idx = 0
    while idx < len(segment_tokens) and _is_env_assignment(segment_tokens[idx]):
        idx += 1
    if idx >= len(segment_tokens):
        return
    raw_argv = _strip_redirect_syntax(segment_tokens[idx:])
    non_env = _strip_command_prefix_words(_unwrap_execution(raw_argv))
    if not non_env:
        return
    cmd = os.path.basename(non_env[0])

    if cmd == "eval":
        nested = " ".join(non_env[1:])
        if nested:
            targets.extend(_extract_mutation_targets(nested, repo_root, execution_cwd))
        return

    if cmd in NESTED_SHELLS:
        nested = _nested_shell_script(non_env)
        if nested:
            # Extract first. Reporting the synthetic goal path before trying the
            # real extraction named a file the command never touches, which the
            # REQ forbids: a deny reason must name the actual cause.
            targets.extend(_extract_mutation_targets(
                nested, repo_root, execution_cwd,
            ))
            if targets:
                return
            # A keyword heuristic used to fire when extraction found nothing:
            # any gated-path mention plus a word like "write"/"append"/">"
            # anywhere in the nested string denied with a synthetic
            # `goals/current.json` target. It denied `bash -c "grep -n write
            # <receipt>"` — a read — while naming a file the command never
            # touches. The nested extraction above already reports truthful
            # targets; nothing replaces this.
            return

    # No execution-based deny. Invoking a lifecycle entrypoint, importing a
    # receipt writer, or running an uninspected inline runtime is not blocked
    # here — that is agent discipline, not a gate. What remains below is the
    # only thing this layer can actually decide: a command whose *verb* writes a
    # file, and whose target resolves to a protected path.

    # `-i` has a long spelling and perl separates `-i` from `-p`/`-n`. Matching
    # only `-i…`/`-pi…` let `sed --in-place <receipt>` and `perl -i -pe … <receipt>`
    # through — both really rewrite the named file.
    if cmd == "sed" and any(
        token == "-i" or token.startswith(("-i", "--in-place"))
        for token in non_env[1:]
    ):
        # Every file operand is rewritten, not just the last. Classifying only
        # `_last_non_option` meant appending one harmless filename walked the
        # real target: `sed -i s/a/b/ <receipt> /tmp/pad` rewrote the receipt.
        # The script expression is the first non-option operand, so skip it.
        # The first operand is the script expression *only* when the script was
        # not supplied by an option. With `--expression=…`/`--file=…` the first
        # operand is already the target file, and skipping it let
        # `sed -i --expression=s/a/b/ <plan>` rewrite a protected artifact.
        script_from_option = any(
            token in {"-e", "-f"} or token.startswith(("--expression", "--file"))
            for token in non_env[1:]
        )
        _append_operands(
            targets, non_env, "sed -i", repo_root, execution_cwd,
            skip_first=not script_from_option, value_options={"-e", "-f"},
        )
        return
    if cmd == "perl":
        options = [token for token in non_env[1:] if token.startswith("-")]
        switches = [_perl_cluster_switches(token) for token in options]
        in_place = any("i" in cluster for cluster in switches)
        if in_place and any(
            "p" in cluster or "n" in cluster for cluster in switches
        ):
            # `-e <expr>` carries the program, so operands after it are files.
            _append_operands(
                targets, non_env, "perl -i", repo_root, execution_cwd,
                value_options={"-e", "-E"},
            )
            return
    # `sort`/`diff` are readers until given an output option, which names the
    # file they overwrite. The rule existed but lost its only caller when the
    # execution branches were removed.
    if cmd in _OUTPUT_OPTION_COMMANDS:
        for index, token in enumerate(non_env[1:], start=1):
            target = ""
            if token in {"-o", "--output"} and index + 1 < len(non_env):
                target = non_env[index + 1]
            elif token.startswith("--output="):
                target = token.split("=", 1)[1]
            elif token.startswith("-o") and len(token) > 2:
                target = token[2:]
            if target:
                _append_target(
                    targets, target, f"{cmd} output option",
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
    if cmd in ALL_OPERAND_MUTATORS:
        _append_operands(
            targets, non_env, cmd, repo_root, execution_cwd,
            value_options={"-s", "--size", "-r", "--reference", "-d", "--date",
                           "-t", "--time"},
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
        tail = non_env[index + 1:]
        # `git restore --staged <path>` unstages; it cannot change the file on
        # disk, so it belongs with `git add` rather than with worktree rewrites.
        # `--worktree` alongside it does write, so require its absence.
        index_only_restore = (
            subcommand == "restore"
            and any(token in {"--staged", "-S"} for token in tail)
            and not any(token in {"--worktree", "-W"} for token in tail)
        )
        if subcommand and subcommand not in GIT_NON_MUTATING_SUBCOMMANDS \
                and not index_only_restore:
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

    # Redirect classification moved into `_walk_segments`, where tokens have
    # been expanded; running it here saw only literal `$VAR` text.
    # An execution heuristic used to fire here: command substitution anywhere +
    # any gated-path mention + an `-o`-shaped token produced a synthetic
    # `goals/current.json` deny. It matched `grep -o`, `mypy --output`,
    # `pytest -o` and killed ordinary read-only work while naming a file the
    # command never touched. Removed with the rest of execution gating.

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
    index = 0
    while index < len(command):
        char = command[index]
        if quote:
            # Inside double quotes a backslash still escapes; inside single
            # quotes it does not.
            if quote == '"' and char == "\\" and index + 1 < len(command):
                current.append(char)
                current.append(command[index + 1])
                index += 2
                continue
            if char == quote:
                quote = ""
            current.append(char)
            index += 1
            continue
        # An escaped character outside quotes is literal. Treating `\'` as a
        # quote-opener desynchronized the tracker, so `echo it\'s` swallowed the
        # following newline and a mutator on the next line was never classified
        # — the laundering this function exists to prevent.
        if char == "\\" and index + 1 < len(command) and command[index + 1] != "\n":
            current.append(char)
            current.append(command[index + 1])
            index += 2
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            continue
        # `\` + newline is a line continuation: bash joins the lines, so the
        # verb and its target stay one command. Splitting there separated
        # `cp /tmp/f` from its destination and classified neither.
        if char == "\\" and index + 1 < len(command) and command[index + 1] == "\n":
            current.append(" ")
            index += 2
            continue
        if char == "\n":
            lines.append("".join(current))
            current = []
            index += 1
            continue
        current.append(char)
        index += 1
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
        # Classify redirects here, on the *expanded* tokens. Running this over
        # raw whole-command tokens missed `V=<plan>; echo hi > $V` (the literal
        # `$V` classified as nothing) and also produced a false deny for
        # `D=/outside; cat f > "$D/x.py"`, since the unexpanded token was
        # normalized against the repo root and `.py` read as source.
        _extract_redirect_targets(expanded, targets, repo_root, execution_cwd)
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
