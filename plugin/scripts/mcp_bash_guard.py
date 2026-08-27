#!/usr/bin/env python3
from __future__ import annotations
import os
import collections
import glob
import itertools
import json
import re
import shlex
import stat
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (
        emit_permission_decision,
        _log_gate_error,
        _escape_hint,
        log_gate_bypass,
        find_repo_root,
        harness_root_resolution,
        is_harness_enabled_repo,
    )
    from prewrite_gate import (
        _is_protected_artifact,
        _is_runtime_provenance,
        _is_source_file,
        _is_workflow_control_surface,
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
# Wildcard-bearing path components a pattern may have before the guard refuses
# to expand it. `glob` walks every directory a wildcard component matches, so
# cost is multiplicative in this count, not in the pattern's length:
# `cp /*/*/*/*/*/*/*/*/* <dir>/` took 66 s against a 3 s hook budget, and a
# killed hook emits no decision — so prefixing one cheap-looking `cp` with a
# deep glob converted every deny on the line into an allow. Refusing to expand
# costs a missed classification for that one operand; timing out costs the
# whole line.
_GLOB_COMPONENT_CAP = 4
# Wall-clock budget for one guard invocation, well under the 3 s hook timeout.
# Per-item caps keep being defeated by repetition: capping one glob's depth
# left 250 shallow globs on one line at 4 s, and bounding the substitution scan
# by "closers remaining" left an opener that can never close (`$((` consumes no
# `)`) rescanning to end of line, so 24 KB of padding took 4.4 s. Both convert
# every deny on the line into an allow, because a killed hook emits no
# decision. A deadline bounds the whole invocation regardless of which
# repetition is used; exhausting it degrades to "not extracted", never to
# deleting a token.
_ANALYSIS_BUDGET_SECONDS = 1.0
# Set in main(), so it is None for in-process callers — importing the module and
# calling _extract_mutation_targets directly runs unbounded. That is deliberate:
# a shared clock across many calls in one process would exhaust and silently
# stop classifying. It does mean an in-process test cannot observe the budget;
# assert cost through the hook entry point (see the cost tests).
_deadline = None


def _budget_exhausted():
    return _deadline is not None and time.monotonic() > _deadline
# How far `eval` / `bash -c` nesting is followed. `eval eval eval … cp <src>
# <receipt>` recursed until RecursionError, and RecursionError reaches
# main()'s catch-all, which exits 0 — a silent allow for the whole line. The
# cost was super-linear too, so it hit both fail-open classes at once. Not
# descending past the cap degrades to the existing 'nested content not
# extracted' allow and adds no over-block.
_NESTED_DESCENT_CAP = 8
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
# Characters a control operator can be built from. A token made only of these
# is punctuation, never a word, so it can be decomposed safely.
_CONTROL_PUNCTUATION = set("()&|;")
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
        # bash treats `#` as a comment only at word start; shlex's default
        # truncated `cp /tmp/x#y <artifact>` to `['cp', '/tmp/x']`, dropping
        # the write target entirely. Word-start comments are removed earlier,
        # by `_unquoted_lines`, which knows the quote state.
        lexer.commenters = ""
        # Empty quoted words are KEPT. Dropping them shifted every positional
        # consumption one place left, so `cp payload <<"" <receipt>` had the
        # receipt eaten as the heredoc delimiter. The invariant this restores:
        # the token list must be positionally identical to bash's word list.
        return list(lexer)
    except ValueError:
        return command.split()


def _quoted_flags(command: str, token_count: int):
    r"""Which tokens were quoted in the source text.

    posix mode discards quoting, so a *literal argument* `'<'` arrives as the
    token `<`, indistinguishable from a real operator. That cut both ways:
    `sed -i s/a/b/ '<' <receipt>` had its real target eaten as a redirect
    operand, and `grep -n '2>' <file>` was denied as a source mutation. A
    non-posix pass keeps the quote characters, so the two can be told apart.

    A backslash escape counts as quoting too, and non-posix mode emits a lone
    `\` as its own token. Left unmerged the two lexers disagree on count, the
    all-False fallback fires, and `sed -i s/a/b/ \< <receipt>` gets its target
    eaten exactly as the quoted form did — so the fallback was itself the
    bypass, not a safe degrade.

    Alignment is established by construction, not merely checked. `_tokenize`
    drops falsy tokens, so a posix lex discards an empty quoted word `''`; the
    non-posix lex keeps it as the truthy two-character token `''`. One such word
    anywhere on the line desynchronised the two lexes by exactly one, and the
    old all-False fallback then turned quote-awareness off for the whole line —
    `sed -i s/a/b/ '' '<' <receipt>` walked straight through. The bypass
    precondition was two characters supplied by the very string being inspected,
    so this filters the raw list with the same emptiness semantics instead.

    Returns None when alignment still cannot be established. Callers must pick
    their own conservative default for that case; there is no single safe one,
    because the two consumers fail safe in opposite directions.
    """
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw = list(lexer)
    except ValueError:
        return None
    merged = []
    index = 0
    while index < len(raw):
        token = raw[index]
        if token == "\\" and index + 1 < len(raw):
            merged.append("\\" + raw[index + 1])
            index += 2
            continue
        merged.append(token)
        index += 1
    if len(merged) != token_count:
        return None
    return [token[:1] in ("'", '"', "\\") for token in merged]
def _normalize_candidate_path(
    token: str, repo_root: str = "", execution_cwd: str = ""
) -> str:
    value = os.path.expanduser(str(token or "").strip().strip("'").strip('"'))
    if not value:
        return ""
    value = value.rstrip(",)")
    cwd = os.path.realpath(execution_cwd or repo_root or os.getcwd())
    root = os.path.realpath(repo_root or cwd)
    try:
        candidate = os.path.realpath(
            value if os.path.isabs(value) else os.path.join(cwd, value)
        )
    except (ValueError, OSError):
        # An unrepresentable path (embedded NUL, bad surrogate) is not a target,
        # but it must not raise: the exception would reach main()'s catch-all
        # and exit 0, allowing every other target on the same command line.
        return ""
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
    except (OSError, ValueError):
        return False
    return False
def _glob_is_too_broad(pattern, base=""):
    """True when expanding `pattern` could walk an unbounded number of dirs.

    Cost comes from the *tree* the pattern is anchored to, not from how it is
    spelled, and the first version of this check ignored that. Measured:
    `<repo>/*/*/*/*/RECEIPT?.jsonl` is 0.06 s and names live artifacts, while
    `/*/*/*/*/*/*/*/*/*zzzznomatch` is 50 s — yet a plain component count
    refused both. That re-opened the glob-in-basename route AC-003 covers:
    `cp <payload> */*/*/*/RECEIPT?.jsonl` stopped being classified.

    A pattern rooted inside the repository is bounded by the repository, so it
    is always expanded. Only patterns reaching outside are capped, and a
    deadline covers whatever the cap lets through.

    Containment is decided *physically*, on the literal prefix before the first
    wildcard, normalized. A textual `startswith` looked equivalent and was not:
    `os.path.join(base, "../../*/*/…")` starts with `base`, so `..` traversal
    was exempted from the cap and one operand ran 49 s — past the hook timeout,
    which allows the whole line. A lexical prefix is not a containment proof.
    """
    if base:
        literal = pattern
        for index, character in enumerate(pattern):
            if character in "*?[":
                literal = pattern[:index]
                break
        anchor = os.path.normpath(os.path.dirname(literal) or literal)
        root = os.path.normpath(base)
        if anchor == root or anchor.startswith(root.rstrip(os.sep) + os.sep):
            return False
    wildcard_components = sum(
        1 for component in pattern.split(os.sep)
        if any(ch in component for ch in "*?[")
    )
    return wildcard_components > _GLOB_COMPONENT_CAP


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
    # islice bounds how many matches are *taken*, but reaching even the first
    # match of `/*/*/*/*/*/*/*/*/*` means walking every directory the earlier
    # components matched. Laziness is not a cost bound here; the component cap
    # is.
    if _glob_is_too_broad(pattern, base) or _budget_exhausted():
        return ()
    try:
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
        # Each operand costs a realpath, so a long operand list is the
        # dominant cost path and the one that overran the hook timeout. Stop
        # here once the budget is gone; main() turns an exhausted budget into
        # a deny, so abandoning the walk fails closed rather than allowing.
        if not index % 256 and _budget_exhausted():
            return
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
        elif not token.startswith("--") and "c" in token[1:]:
            # `c` anywhere in the cluster runs the script: `bash -cl '<cmd>'`
            # and `-cvx` execute it just as `-lc` does. Requiring `c` last was
            # justified by a false claim (that `-cl` consumes `l` as the
            # script) and let `bash -cx '<write>'` through.
            saw_command_flag = True
        index += 1
    return ""


_INPUT_REDIRECT_OP_RE = re.compile(r"^\d*<{1,3}[&|]?$")


def _strip_redirect_syntax(tokens, quoted=None):
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
        # Unknown alignment (`quoted is None`) is treated as quoted here:
        # consuming the following token is the laundering direction, so the
        # conservative choice is to leave the operand in place.
        is_quoted = (
            True if quoted is None
            else index < len(quoted) and quoted[index]
        )
        if not is_quoted and (
            _PURE_REDIRECT_OP_RE.match(token) or _INPUT_REDIRECT_OP_RE.match(token)
        ):
            if kept and re.fullmatch(r"\d+", kept[-1]):
                kept.pop()
            index += 2  # the operator and its operand
            continue
        if not is_quoted and _INLINE_REDIRECT_RE.match(token):
            index += 1
            continue
        kept.append(token)
        index += 1
    return kept


# Options whose *value* is a separate token. GNU getopt permutes, so these can
# trail the operands: `install <src> <receipt> -m 644` really writes the
# receipt, but the value `644` was read as the destination.
_VERB_VALUE_OPTIONS = {
    "cp": {"-S", "--suffix", "-t", "--target-directory", "-Z", "--context"},
    "mv": {"-S", "--suffix", "-t", "--target-directory"},
    "install": {"-m", "--mode", "-o", "--owner", "-g", "--group", "-S",
                "--suffix", "-t", "--target-directory", "-Z", "--context"},
    "rsync": {"--log-file", "--exclude", "--include", "--files-from",
              "--rsh", "-e", "--chmod", "--out-format"},
    "truncate": {"-s", "--size", "-r", "--reference"},
    "touch": {"-r", "--reference", "-d", "--date", "-t"},
}


def _last_non_option(tokens, value_options=frozenset()):
    skip_next = False
    last = ""
    for token in tokens[1:]:
        if skip_next:
            skip_next = False
            continue
        if token.startswith("-"):
            if token in value_options:
                skip_next = True
            continue
        last = token
    return last
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
        # Attached short form: `cp -t<dir> <src>`.
        if token.startswith("-t") and len(token) > 2 and not token.startswith("--"):
            return token[2:], {token}
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
        if _glob_is_too_broad(pattern, base) or _budget_exhausted():
            return []
        try:
            # islice over iglob: the cap has to bound the directory walk, not
            # just the result list. `glob.glob(...)[:cap]` completed the whole
            # walk first, which is the timeout fail-open.
            return [
                os.path.basename(match)
                for match in itertools.islice(
                    glob.iglob(pattern), _GLOB_EXPANSION_CAP,
                )
            ]
        except (OSError, ValueError):
            return []
    if stripped.endswith("/.") or source.endswith("/"):
        directory = stripped[:-2] if stripped.endswith("/.") else stripped
        resolved = directory if os.path.isabs(directory) else os.path.join(
            base, directory,
        )
        # The glob branch above consults the budget; this one enumerates a
        # directory and did not, so repeating a directory-shaped source operand
        # multiplied `listdir` cost without ever being observed. Either this
        # check or the one in the calling loop is sufficient on its own —
        # measured — but both are kept, because which one runs first depends on
        # operand shape and neither is on a hot path.
        if _budget_exhausted():
            return []
        try:
            # islice over scandir, not `sorted(listdir(...))[:cap]`: the cap has
            # to bound the walk, not trim its result. The budget is checked
            # before the call but cannot preempt one syscall, so a single
            # enormous directory would otherwise spend its whole enumeration
            # uninterruptibly — the same distinction already applied to the glob
            # branch above.
            with os.scandir(resolved) as entries:
                return sorted(
                    entry.name
                    for entry in itertools.islice(entries, _GLOB_EXPANSION_CAP)
                )
        except (OSError, ValueError):
            return []
    basename = os.path.basename(stripped)
    return [basename] if basename else []


_CWD_IDIOM_RE = re.compile(r"\$\{PWD\}|\$PWD|\$\(pwd\)|`pwd`")


def _resolve_directory_destination(destination, execution_cwd, repo_root):
    """Resolve a copy destination to an existing directory, or None.

    The destination used to be tested verbatim, so only the literal and
    `$(pwd)/…` spellings resolved — `$PWD/<dir>`, `` `pwd`/<dir> `` and a
    trailing glob all missed, and each of those is ordinary phrasing rather
    than evasion. Every spelling reaches the same directory, so the gap was
    purely which one had been modelled.

    Returns the first expansion that is a directory; the caller only needs one,
    because the derived filenames are identical for all of them.
    """
    base = execution_cwd or repo_root or os.getcwd()
    candidate = _CWD_IDIOM_RE.sub(lambda _: base, destination)
    # _glob_expansions returns nothing for a token with no wildcard, so the
    # literal candidate has to be tried in its own right.
    for expansion in (candidate,) + tuple(
        _glob_expansions(candidate, repo_root, execution_cwd)
    ):
        resolved = expansion if os.path.isabs(expansion) else os.path.join(
            base, expansion,
        )
        try:
            if os.path.isdir(resolved):
                return resolved
        except (OSError, ValueError):
            continue
    return None


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
    resolved = _resolve_directory_destination(
        destination, execution_cwd, repo_root,
    )
    if resolved is None:
        return
    sources = [
        token for token in non_env[1:]
        if not token.startswith("-") and token != destination
        and token not in consumed
    ]
    for source in sources:
        # Cost here is (repeated directory-source operands) x (entries in each,
        # capped at _GLOB_EXPANSION_CAP), which is a per-item cap defeated by
        # repeating the item — the exact shape the whole-invocation deadline
        # exists to bound. 30 repetitions of `cp -r <400-entry dir>/.` ran past
        # the 3s hook timeout, and a killed hook allows the line.
        if _budget_exhausted():
            return
        for name in _expanded_sources(source, execution_cwd, repo_root):
            _append_target(
                targets, os.path.join(destination, name),
                f"{cmd} into directory", repo_root, execution_cwd,
            )


def _extract_redirect_targets(tokens, targets, repo_root, execution_cwd="",
                              quoted=None, quoted_literal=()):
    """Classify redirect targets in one segment.

    `quoted_literal` is one flag per token: was *this* occurrence quoted in the
    source. It must be sliced to the same span as `tokens`. A set of spellings
    is not a substitute and was tried twice — presence let one quoted `>`
    suppress every real one, and counting refused to skip whenever a line held
    both a quoted and a real occurrence.
    """
    for index, token in enumerate(tokens):
        # The last cost loop outside the fail-closed handoff. Each redirect
        # operator costs a path resolution, so ~16 KB of `> ` padding overran
        # the 3 s hook timeout — and a killed hook allows. main() turns an
        # exhausted budget into a deny, so returning here fails closed.
        if not index % 256 and _budget_exhausted():
            return
        # A quoted token is a literal argument, not an operator: `grep -n '2>'`
        # was being denied as a source mutation. Unknown alignment is treated
        # as unquoted here — the opposite default from _strip_redirect_syntax,
        # because failing safe means still classifying the redirect target.
        if quoted is not None and index < len(quoted) and quoted[index]:
            continue
        # When alignment is unknown, apply the same evidence rule the segment
        # path uses, but per occurrence: was *this* token quoted in the source.
        # Without the skip, `grep -n ">" "$PWD"/<source>` read the `>` as a
        # real operator and denied a reader, naming a fabricated `$PWD/...`
        # path. With a spelling-level answer instead of a positional one it was
        # wrong in both directions — presence let one quoted `>` suppress every
        # real one, counting refused to skip whenever a line held both.
        if quoted is None and index < len(quoted_literal) and quoted_literal[index]:
            continue
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
def _process_segment(segment_tokens, targets, repo_root, execution_cwd="",
                     quoted=None, depth=0):
    """Dispatch one segment, unioning both readings when quoting is unknown.

    `quoted is None` is not rare: adjacent-quote concatenation (`"a"b`,
    `/tmp/a\\ b`) desynchronises the two lexes just as an empty quoted word
    did. Picking a side was wrong in both directions — treating unknown as
    quoted leaves the redirect operand in argv, where `_last_non_option`
    then reads it as the destination, so an everyday
    `cp src "<dir>"/RECEIPTS.jsonl 2>/dev/null` stopped denying.

    The safe default is not a side but the union: classify under both
    readings and keep every target either produces. A deny from either
    interpretation denies.
    """
    if quoted is not None:
        _process_segment_once(
            segment_tokens, targets, repo_root, execution_cwd, quoted, depth,
        )
        return
    for reading in (True, False):
        found: list[dict] = []
        _process_segment_once(
            segment_tokens, found, repo_root, execution_cwd,
            [reading] * len(segment_tokens), depth,
        )
        targets.extend(item for item in found if item not in targets)


def _process_segment_once(segment_tokens, targets, repo_root, execution_cwd="",
                          quoted=None, depth=0):
    if not segment_tokens:
        return
    idx = 0
    while idx < len(segment_tokens) and _is_env_assignment(segment_tokens[idx]):
        idx += 1
    if idx >= len(segment_tokens):
        return
    raw_argv = _strip_redirect_syntax(
        segment_tokens[idx:], None if quoted is None else list(quoted[idx:]),
    )
    non_env = _strip_command_prefix_words(_unwrap_execution(raw_argv))
    if not non_env:
        return
    cmd = os.path.basename(non_env[0])

    if cmd == "eval":
        # Fold `eval eval eval … X` into one descent. The depth cap exists to
        # stop RecursionError (which main()'s catch-all turns into a silent
        # allow for the whole line), but charging a level per repeated `eval`
        # made the cap itself a bypass: nine of them walked past it and wrote
        # the artifact with the gate silent. Repetition adds no nesting to
        # analyse, and folding it costs nothing, so only genuinely alternating
        # nesting can reach the cap now.
        rest = non_env[1:]
        while rest and os.path.basename(rest[0]) == "eval":
            rest = rest[1:]
        nested = " ".join(rest)
        if nested:
            if depth < _NESTED_DESCENT_CAP:
                targets.extend(_extract_mutation_targets(
                    nested, repo_root, execution_cwd, depth + 1,
                ))
        return

    if cmd in NESTED_SHELLS:
        nested = _nested_shell_script(non_env)
        if nested:
            # Extract first. Reporting the synthetic goal path before trying the
            # real extraction named a file the command never touches, which the
            # REQ forbids: a deny reason must name the actual cause.
            if depth < _NESTED_DESCENT_CAP:
                targets.extend(_extract_mutation_targets(
                    nested, repo_root, execution_cwd, depth + 1,
                ))
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
        # Attached forms count too: `-es/a/b/` and `-f/tmp/s` are neither equal
        # to `-e`/`-f` nor prefixed `--expression`, so the sole operand — the
        # artifact — was being skipped.
        script_from_option = any(
            token in {"-e", "-f"}
            or token.startswith(("--expression", "--file"))
            or (token.startswith(("-e", "-f")) and not token.startswith("--"))
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
                cmd, non_env,
                _last_non_option(non_env, _VERB_VALUE_OPTIONS.get(cmd, ())),
                targets,
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
        destination = _last_non_option(
            non_env, _VERB_VALUE_OPTIONS.get(cmd, ()),
        )
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
def _extract_mutation_targets(command, repo_root, execution_cwd="", depth=0):
    targets: list[dict] = []
    # The recursion itself is a repetition, and it was the one repetition the
    # deadline never saw. `eval`/`bash -c` descent re-enters here up to
    # _NESTED_DESCENT_CAP, and each level pays the two both-readings unions, so
    # cost is roughly 4^depth. Eight `bash -c` wrappers around a plain
    # `cp /tmp/f <receipt>` — no padding at all — took 6.3 s against a 3 s hook
    # timeout, and a killed hook emits no decision. Wrapping alone converted
    # every deny on the line into an allow. The strided checks further in
    # cannot help: each nested segment is tiny, so no inner loop runs long
    # enough to reach its stride.
    if _budget_exhausted():
        return targets
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
        if not line_tokens:
            continue
        line_quoted = _quoted_flags(line, len(line_tokens))
        if line_quoted is not None:
            collapsed, collapsed_quoted = _collapse_substitutions(
                line_tokens, line_quoted, line,
            )
            _walk_segments(
                collapsed, shell_values, targets, repo_root, execution_cwd,
                collapsed_quoted, depth, command=line,
            )
            continue
        # Alignment failed, so the collapse cannot know whether a `` ` `` or a
        # `$(` is an operator or a literal. Guessing "operator" made it delete
        # tokens — including a following `; cp <payload> <receipt>` — and no
        # downstream union could recover them, because they were gone before
        # segmentation. Union at this level instead: classify with the collapse
        # applied AND with it skipped, and keep every target either produces.
        for collapse in (True, False):
            variant, variant_quoted = (
                _collapse_substitutions(line_tokens, None, line) if collapse
                else (line_tokens, None)
            )
            found: list[dict] = []
            _walk_segments(
                variant, dict(shell_values), found, repo_root, execution_cwd,
                variant_quoted, depth, command=line,
            )
            targets.extend(item for item in found if item not in targets)

    return targets


def _unquoted_lines(command):
    """Split on newlines that are outside quotes.

    Splitting the raw text turned the body of a quoted multi-line argument into
    command segments: a `git commit -m` whose message named a lifecycle symbol
    was denied as a RECEIPTS.jsonl mutation — a command that mutates nothing,
    and this repo's own commit convention.

    Word-start `#` comments are removed here too, because this is the only place
    that knows the quote state. bash starts a comment only at a word boundary,
    so `/tmp/x#y` is a path while `cp a b # note` ends at the `#`. Leaving the
    comment words in the stream put one of them in the destination slot, and
    `cp payload <receipt> #` overwrote the artifact with the gate silent.
    """
    lines = []
    current = []
    quote = ""
    index = 0
    # True only where bash would start a new word. Testing `current[-1]` for
    # whitespace was not the same thing: escaped whitespace (`/tmp/a\ `) is part
    # of the preceding word, so `cp /tmp/a\ #x ; cp payload <receipt>` had the
    # rest of the line — including a real second command — treated as a comment
    # and dropped. Only the plain-character branch below sets this.
    at_word_start = True
    while index < len(command):
        if not quote and command[index] == "#" and at_word_start:
            while index < len(command) and command[index] != "\n":
                index += 1
            continue
        char = command[index]
        if quote:
            # Inside double quotes a backslash still escapes; inside single
            # quotes it does not.
            if quote == '"' and char == "\\" and index + 1 < len(command):
                current.append(char)
                current.append(command[index + 1])
                index += 2
                at_word_start = False
                continue
            if char == quote:
                quote = ""
            current.append(char)
            index += 1
            at_word_start = False
            continue
        # An escaped character outside quotes is literal. Treating `\'` as a
        # quote-opener desynchronized the tracker, so `echo it\'s` swallowed the
        # following newline and a mutator on the next line was never classified
        # — the laundering this function exists to prevent.
        if char == "\\" and index + 1 < len(command) and command[index + 1] != "\n":
            current.append(char)
            current.append(command[index + 1])
            index += 2
            at_word_start = False
            continue
        if char in "'\"":
            quote = char
            current.append(char)
            index += 1
            at_word_start = False
            continue
        # `\` + newline is a line continuation: bash joins the lines, so the
        # verb and its target stay one command. Splitting there separated
        # `cp /tmp/f` from its destination and classified neither.
        if char == "\\" and index + 1 < len(command) and command[index + 1] == "\n":
            current.append(" ")
            index += 2
            at_word_start = True
            continue
        if char == "\n":
            lines.append("".join(current))
            current = []
            index += 1
            at_word_start = True
            continue
        current.append(char)
        index += 1
        at_word_start = char in " \t"
    lines.append("".join(current))
    return lines



# Stands in for a command-substitution span. Not a path, not an option, not a
# boundary, so it holds one operand slot without being classified.
#
# Deliberately free of NUL and other characters `os.path.realpath` rejects: a
# `ValueError` from the normalizer reaches `main()`'s catch-all and exits 0,
# which is a silent allow for the whole command.
_SUBSTITUTION_PLACEHOLDER = "$()"


def _glued_closer_ordinals(command, closer):
    """1-based ordinals of `closer` characters in `command` followed by `/`.

    Used to decide whether a collapsed substitution was written glued to the
    path that follows it (`$(pwd)/doc`) or merely near one (`cd $(dirname .)/.
    ; sed -i $(echo s/a/b/) <artifact>`). Deciding that with a substring test
    over the whole command conflated the two and glued unrelated words
    together, which deletes a word — the same failure class as an unbounded
    span consuming the rest of the line.

    When there is no raw command to consult the caller gets an empty set, so
    the merge is simply skipped; splitting a path is a missed classification,
    while gluing two operands destroys one.
    """
    if not command:
        return frozenset()
    ordinals = set()
    count = 0
    for position, character in enumerate(command):
        if character != closer:
            continue
        count += 1
        # A quote counts as glue too: bash concatenates adjacent quoted parts
        # into one word, so `$(pwd)'/'<artifact>` is a single operand. Reading
        # only a literal `/` left the following token as a standalone absolute
        # path, which resolves outside the repo root and gets dropped — the
        # artifact was written with the gate silent.
        if command[position + 1:position + 2] in ("/", "'", '"'):
            ordinals.add(count)
    return frozenset(ordinals)


def _collapse_substitutions(tokens, quoted, command=""):
    """Collapse `$( … )`, `<( … )`, `>( … )` and backtick spans to one token.

    `punctuation_chars=True` emits `$`, `(`, `pwd`, `)` as four words where bash
    builds one (process substitution) or zero-or-more (command substitution).
    Because `(` and `)` are boundaries, the segment ended mid-command and the
    destination landed alone in the next segment as its own "command word",
    where no verb branch matched — `cp payload $(pwd)/<receipt>` wrote the
    artifact with the gate silent, and the unquoted `$(pwd)/…` spelling is
    everyday phrasing.

    Two rules keep this from becoming a bypass of its own:

    * **Never delete a token the scan could not bound.** An unterminated span
      used to consume the rest of the line — including a following
      `; cp <payload> <receipt>` — which is a laundering direction the
      positional-identity invariant does not cover, because it removes words
      rather than moving them. If no closer is found, nothing is collapsed.
    * **Punctuation arrives clustered.** shlex emits `))`, `);`, `)&&`, `)|`
      and `)>>` as single tokens, so a closer is detected by *counting* parens
      inside each token, and whatever follows the closing paren in that same
      token is pushed back so the boundary it carries still splits.
    * **A failed scan must not be repeated.** "Never delete" was first written
      as a plain `index += 1`, which made this function quadratic: n unclosed
      openers each rescanning to end of line. 40 KB of `'$('` padding then blew
      the 3 s hook budget, and a timeout emits no decision — so the padding
      disabled every deny on the line, which is the fail-open the rule was
      meant to avoid. A scan finding no closer in `pending[index+1:]` proves
      none exists for any later opener either, because `index` only advances,
      so the result is latched instead of recomputed.
    * **Adjacency belongs to the span, not to the line.** `$(pwd)/doc` is one
      word to bash while shlex yields `)` then `/doc`, so the two are merged.
      Deciding that with `")/" in command` tested the whole command: a stray
      `)/` anywhere — including the ordinary idiom `cd $(dirname .)/.` — turned
      the merge back on for an unrelated span and glued two real words into
      one, so `sed -i $(echo s/O/X/) <artifact>` swallowed the artifact as
      sed's script expression and classified nothing. The merge now asks about
      the character following *this* span's own closer.
    """
    collapsed = []
    flags = [] if quoted is not None else None
    pending = list(tokens)
    pending_quoted = list(quoted) if quoted is not None else None
    # Ordinals (1-based) of the `)` and backtick characters in the raw command
    # that are immediately followed by `/`. Closer characters survive
    # tokenization one-for-one and in order, so the k-th closer character of
    # the token stream is the k-th in the source; a span can then ask about its
    # own closer instead of about the whole line.
    glued_parens = _glued_closer_ordinals(command, ")")
    seen_paren = 0
    # Closer characters still ahead in `pending`, so a scan can be skipped when
    # there is provably nothing to find. A boolean "this scan failed, so every
    # later one will too" latch was wrong: a scan also fails when no closer
    # brings *this* opener's depth to zero (`echo $((` leaves depth 2), and a
    # later opener with smaller depth would still have found one. That latch
    # suppressed collapse for the rest of the line, so a single `$((` token
    # ahead of `cp /tmp/x $(pwd)/<artifact>` stopped the merge and allowed the
    # write — a cheaper fail-open than the timeout it replaced.
    parens_left = sum(token.count(")") for token in pending)
    backticks_left = sum(token.count("`") for token in pending)
    index = 0
    while index < len(pending):
        token = pending[index]
        is_quoted = (
            pending_quoted is not None
            and index < len(pending_quoted)
            and pending_quoted[index]
        )
        opener_len = 0
        backtick = False
        if not is_quoted:
            if token == "`":
                backtick = True
                opener_len = 1
            elif token in ("$", "<", ">") and index + 1 < len(pending) \
                    and pending[index + 1].startswith("("):
                opener_len = 1
            elif token.endswith("$") and index + 1 < len(pending) \
                    and pending[index + 1].startswith("("):
                # Glued opener: `-t$(`, `--target-directory=$(`, `V=$(`.
                opener_len = 1
            elif "(" in token and token.rstrip("(").endswith(("$", "<", ">")):
                opener_len = 1
        if not opener_len:
            collapsed.append(token)
            if flags is not None:
                flags.append(is_quoted)
            # Ordinals count closer characters wherever they appear, including
            # inside quoted words: `"a)/b"` contributes a `)` to the source, so
            # skipping it here would make every later span read one ordinal too
            # low and inherit some other paren's adjacency.
            seen_paren += token.count(")")
            parens_left -= token.count(")")
            backticks_left -= token.count("`")
            index += 1
            continue

        # Nothing left to find means the scan cannot succeed; skipping it is
        # what keeps the walk linear.
        if backtick:
            reachable = backticks_left - token.count("`") > 0
        else:
            reachable = parens_left - token.count(")") > 0
        depth = token.count("(")
        scan = index + 1
        tail = ""
        found = False
        # Closer characters passed while scanning, and how many of those fall
        # at or before the one that actually closes this span.
        span_parens = 0
        span_backticks = 0
        upto_closer = 0
        while reachable and scan < len(pending):
            # time.monotonic() per token would cost more than the scan; the
            # stride keeps the check off the hot path.
            if not scan % 512 and _budget_exhausted():
                break
            current = pending[scan]
            if backtick:
                # A backtick span still consumes whatever parens sit inside it.
                # Counting only backticks here left every later span reading a
                # too-low paren ordinal, so it inherited some other paren's
                # adjacency — both a missed merge and a false one.
                span_parens += current.count(")")
                if current == "`":
                    found = True
                    span_backticks += 1
                    break
                span_backticks += current.count("`")
            else:
                # A paren span swallows backticks the same way a backtick span
                # swallows parens; counting only one direction let
                # `backticks_left` drift upward. Harmless today — an
                # over-estimate only runs a scan that then fails anyway — but
                # the invariant is what the next reader will rely on.
                span_backticks += current.count("`")
                depth += current.count("(")
                closers = current.count(")")
                if closers:
                    depth -= closers
                    if depth <= 0:
                        found = True
                        # `);` and `)&&` carry a boundary after the closer.
                        cut = current.rindex(")")
                        tail = current[cut + 1:]
                        upto_closer = span_parens + current[:cut + 1].count(")")
                        span_parens += closers
                        break
                    span_parens += closers
            scan += 1
        if not found:
            # Unbounded span: keep the token as-is rather than deleting the
            # remainder of the line.
            collapsed.append(token)
            if flags is not None:
                flags.append(is_quoted)
            seen_paren += token.count(")")
            parens_left -= token.count(")")
            backticks_left -= token.count("`")
            index += 1
            continue

        # Keep whatever the opener was glued to: `--target-directory=$(pwd)/x`
        # must stay `--target-directory=x`, or the option name is lost and the
        # destination stops being read as one.
        prefix = token[:token.index("(")] if "(" in token else token
        prefix = prefix.rstrip("$<>`")
        if backtick:
            # A backtick span can never be glued to a following `/` token: `/`
            # is a word character, so `` `pwd`/doc `` lexes as a single token
            # and this branch is never reached with a `/`-leading successor.
            # Tracking backtick ordinals for a decision that cannot fire was
            # dead machinery that still cost a full-line scan per call.
            glued_suffix = False
        else:
            closer_ordinal = seen_paren + token.count(")") + upto_closer
            glued_suffix = closer_ordinal in glued_parens
        # Every closer character between index and scan is consumed here.
        seen_paren += token.count(")") + span_parens
        parens_left -= token.count(")") + span_parens
        backticks_left -= token.count("`") + span_backticks
        index = scan + 1
        if tail:
            pending.insert(index, tail)
            if pending_quoted is not None:
                pending_quoted.insert(index, False)
        # `$(pwd)/doc/…` is ONE word to bash, but shlex leaves `/doc/…` as a
        # separate token, and an absolute path resolves outside the repo so the
        # artifact stopped being classified. Merge only when the source text
        # really has `)` immediately followed by `/` — otherwise a genuinely
        # separate operand would be rebased into the repo.
        if (not tail and glued_suffix and index < len(pending)
                and pending[index].startswith("/")):
            collapsed.append(prefix + pending[index].lstrip("/"))
            if flags is not None:
                flags.append(False)
            index += 1
            continue
        # Keep the prefix here too. `> <dir>/$(echo RECEIPTS.jsonl)` had the
        # directory dropped along with the substitution, leaving a bare
        # placeholder as the whole operand — the same deletion the merge path
        # already guards against. Preserving it does not by itself deny (the
        # basename is still unknown, so the write allows; see the REQ's
        # bypass-class list), but it keeps the word count honest and leaves the
        # directory visible to any later rule that wants it.
        collapsed.append(prefix + _SUBSTITUTION_PLACEHOLDER)
        if flags is not None:
            flags.append(False)
    return collapsed, flags

def _split_control_cluster(token):
    """Split a clustered control operator into the operators bash would see.

    `punctuation_chars=True` glues a closing paren to whatever follows it, so a
    plain subshell emits `');'`, `')&&'`, `')|'`, `')&'` as ONE token. None of
    those strings is in `BOUNDARY_TOKENS`, which matches exactly — so the
    segment never ended, and `_process_segment_once` dispatched the whole rest
    of the line on the subshell's first command word. `( echo hi ); cp
    <payload> <receipt>` allowed, while the identical line with a space before
    the `;` denied. That is ordinary phrasing, not evasion.

    `_collapse_substitutions` already had to learn that punctuation arrives
    clustered; this is the same lesson applied to segmentation. Splitting only
    ever adds boundaries, so it cannot turn a deny into an allow.
    """
    if len(token) < 2 or not set(token) <= _CONTROL_PUNCTUATION:
        return None
    parts = []
    position = 0
    while position < len(token):
        # Longest match first, so `&&` never decomposes into two `&`.
        for size in (3, 2, 1):
            piece = token[position:position + size]
            if piece in BOUNDARY_TOKENS:
                parts.append(piece)
                position += size
                break
        else:
            return None
    return parts if len(parts) > 1 else None


def _expand_control_clusters(tokens, quoted):
    """Rewrite a token list so clustered operators are separate boundaries.

    Returns the inputs unchanged (by identity) when there is nothing to split,
    which is how the caller detects that the two readings coincide. Under
    unknown quote alignment the caller classifies both readings rather than
    choosing one — see `_walk_segments`.
    """
    if not any(
        _split_control_cluster(token) for index, token in enumerate(tokens)
        if not (quoted is not None and index < len(quoted) and quoted[index])
    ):
        return tokens, quoted
    out = []
    out_quoted = [] if quoted is not None else None
    for index, token in enumerate(tokens):
        is_quoted = (
            quoted is not None and index < len(quoted) and quoted[index]
        )
        parts = None if is_quoted else _split_control_cluster(token)
        for piece in (parts or [token]):
            out.append(piece)
            if out_quoted is not None:
                out_quoted.append(is_quoted)
    return out, out_quoted


def _quotable_operators(command):
    """Count the whole quoted words that spell a control operator.

    Returns a `Counter`, because presence is not enough — see the caller.

    The alternate readings — merging a pair of segments across a boundary, and
    classifying the unsplit line — exist for one shape: a *quoted* operator that
    is really a filename (`touch '|' <artifact>`). They were applied whenever
    quote alignment was unknown, which is the everyday case (`"$PWD"/x`,
    `"$(pwd)"/x`, `/tmp/a\\ b` all defeat `_quoted_flags`), so an ordinary line
    like `ls "$PWD"/doc ; rm /tmp/x ; wc -l <task>/PLAN.md` was read as one
    command and denied — a reader, blocked, with the deny naming an `rm` operand
    it never had.

    A quoted operator appears in the source as a *quoted word* — `'|'`, `"&&"` —
    so the test is against the lexer's raw words, not the raw text.

    Two earlier spellings of this both leaked, in the same direction, and the
    difference between them is the lesson. First it looked for a quote merely
    *touching* an operator anywhere on the line, so the `"` closing `"ok"` lent
    its mark to an unrelated `;` and `echo "ok"; rm -rf /tmp/build; cat
    "$PWD"/<task>/RECEIPTS.jsonl` denied. Narrowing that to the substring
    `"'" + op + "'"` looked like it fixed the class and did not: `-d';'`
    contains `';'` while the word it produces is `-d;`, so every everyday
    delimiter idiom — `cut -d';'`, `sort -t';'`, `awk -F';'`, `IFS=';'` — lent
    its quote to the real `;` separators on the line and denied ordinary
    readers, one of them naming an `install.py` that appeared nowhere in the
    command.

    A substring is not a word. Only a whole word that is a quoted spelling of
    the operator is evidence that a boundary might be a filename; anything
    glued to other characters is a normal argument that happens to contain
    quotes.
    """
    # No empty-command guard: `_unquoted_lines` skips blank lines, and
    # `command` is keyword-only and required, so omission is a TypeError rather
    # than a silent empty result. `shlex` on "" yields no words anyway.
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw_words = list(lexer)
    except ValueError:
        raw_words = command.split()
    found = collections.Counter()
    for word in raw_words:
        # No `$'…'` branch. It looks like the same shape but is not: posix
        # `shlex` turns `$';'` into the token `$;`, which is not a boundary, so
        # counting it raised `quotable[op]` without raising `boundaries[op]` and
        # the comparison ran between mismatched populations. The result was
        # false denies only — `cp /tmp/a $';' ; cat <plan>` blocked a reader and
        # named a PLAN.md mutation the line never performs. It bought nothing
        # either: `touch $'|' <receipt>` denies without it, because a `$'…'`
        # word is a literal string in bash and can never be the separator the
        # merge is reinterpreting.
        if len(word) > 1 and word[0] == word[-1] and word[0] in "'\"":
            inner = word[1:-1]
        else:
            continue
        if inner in BOUNDARY_TOKENS:
            found[inner] += 1
    return found


def _quoted_operator_words(command, tokens):
    """One flag per token: was *this* occurrence a quoted literal in the source.

    Same evidence rule as `_quotable_operators`, applied to redirect operators
    rather than segment boundaries. `_extract_redirect_targets` skipped a
    quoted token only when the quoting was *known*, so under unknown alignment
    — which one adjacent-quote word anywhere on the line produces — every
    redirect-shaped token was read as a real operator and the next token
    classified as its target. `grep -n ">" "$PWD"/plugin/scripts/_lib.py`
    therefore denied: a pure reader, blocked, naming a literal `$PWD/...` path
    that appears nowhere on the line.

    Unlike the segment path there is no both-readings union behind this call to
    correct it, so the operator has to be recognised as a literal here or not
    at all.

    Returns one flag per token: was *this* occurrence quoted in the source.

    Two weaker rules were tried and both were wrong, in opposite directions.
    Presence ("some quoted `>` exists") let one quoted `>` suppress every real
    one, so `grep -n ">" "$PWD"/f ; echo x > <receipt>` wrote the artifact.
    Counting ("as many quoted as occurrences") then refused to skip whenever a
    line held both, so `grep -c ">" <file> > /tmp/out` — a reader with an
    ordinary redirect — denied again.

    The question was never how many; it is *which one*. "The k-th raw word
    reducing to a spelling is the k-th token of that spelling" was a third
    wrong answer — the two lexes disagree about **word boundaries**, not only
    about quoting, so `'>'q` is two raw words against one token `>q` and the
    quoted word donated its flag to the next real `>`.

    What answers it is the joint walk below: accumulate the posix text of raw
    words until it equals the token. A token assembled from more than one raw
    word is never a quoted literal. Computing that posix text needs a character
    scan, because a quote span can open mid-word and swallow whitespace
    (`-m'fix  a b'` is three raw words and one token) and a trailing backslash
    escapes a character the splitter consumed (`/tmp/a\\ b`).

    Returns `[]` — not a list of False — when the walk cannot complete or a lex
    degrades. The caller reads it positionally, so an empty list skips nothing
    and classifies every redirect-shaped token: fail closed.
    """
    if not command:
        return []
    try:
        lexer = shlex.shlex(command, posix=False, punctuation_chars=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        raw_words = list(lexer)
    except ValueError:
        # `punctuation_chars=True` raises on a quote run glued to a word when
        # the run holds shell punctuation — `-d';'`, `-F'|'`, `sort -t'|'`,
        # `IFS=';'`, the very idioms this module's own docstrings call
        # everyday. Giving up there cost the whole line its quote awareness, so
        # `grep -n '>' <plan> | cut -d'|' -f1` read the quoted `>` as a real
        # operator and denied a pure reader. The character walk below already
        # reconciles the different word boundaries this lex produces, and a
        # walk that cannot complete exactly still returns [].
        try:
            lexer = shlex.shlex(command, posix=False, punctuation_chars=False)
            lexer.whitespace_split = True
            lexer.commenters = ""
            raw_words = list(lexer)
        except ValueError:
            return []
    try:
        # `_tokenize` degrades independently: ANSI-C quoting (`$'a\'b'`) lexes
        # fine non-posix and raises posix, so `tokens` arrive from
        # `command.split()` with their quotes still attached while raw words
        # have theirs stripped. The two lists then speak different alphabets,
        # and the quoted flag lands on the wrong occurrence — that is how the
        # real `>` in `$'a\'b' ; grep '>' f ; echo x > <receipt>` got skipped
        # and the artifact was written. Mirror `_tokenize`'s own condition
        # rather than guessing which lex failed.
        posix_lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
        posix_lexer.whitespace_split = True
        posix_lexer.commenters = ""
        list(posix_lexer)
    except ValueError:
        return []
    # Walk raw words and tokens together instead of bucketing by spelling.
    # The k-th-word correspondence was false: the non-posix lex splits at a
    # quote boundary while posix merges, so `'>'q` is two raw words and one
    # token `>q`. The quoted word then consumed occurrence 0 of spelling `>`
    # with no token behind it, and the *real* `>` later on the line inherited
    # its True and was skipped — `echo '>'q > <receipt>` wrote the artifact.
    #
    # Concatenating until the accumulation equals the token reconciles the
    # boundary directly. A token assembled from more than one raw word is never
    # a quoted literal, and any walk that cannot be completed exactly gives up
    # the skip entirely.
    merged = []
    index = 0
    while index < len(raw_words):
        word = raw_words[index]
        if word == "\\" and index + 1 < len(raw_words):
            merged.append("\\" + raw_words[index + 1])
            index += 2
            continue
        merged.append(word)
        index += 1

    def _value(word, quote=""):
        """(posix text of this raw word, was all of it quoted, open span).

        Computing this by pattern — "is the whole word quote-delimited?" —
        was wrong twice. `'>'q` needed the *concatenation* to reconcile, and
        `-m'fix a b'` or `/tmp/'a b'` open a quote span mid-word that the
        non-posix lexer then splits on whitespace, so no whole-word rule can
        reproduce the posix token. Walking characters gives the real value;
        anything less made the walk fail and every quoted redirect literal on
        the line get re-read as a real operator, denying pure readers.
        """
        text = []
        quoted_chars = 0
        trailing_escape = False
        index = 0
        while index < len(word):
            char = word[index]
            if quote:
                if char == quote:
                    quote = ""
                else:
                    text.append(char)
                    quoted_chars += 1
            elif char in "'\"":
                quote = char
            elif char == "\\" and index + 1 < len(word):
                text.append(word[index + 1])
                quoted_chars += 1
                index += 1
            elif char == "\\":
                # Trailing backslash: it escaped the character the splitter
                # consumed, so it contributes nothing to this word's text and
                # leaves the same "posix joined across the split" state an open
                # quote span does.
                trailing_escape = True
            else:
                text.append(char)
            index += 1
        value = "".join(text)
        # Returns the still-open quote character (truthy) rather than a bool:
        # a span can cross the non-posix lexer's word split, and the words
        # inside it are wholly quoted even though they carry no quote
        # character of their own. `-m'fix a b'` is three raw words and one
        # token, and resetting the state per word lost the spaces.
        return (value, bool(value) and quoted_chars == len(value),
                quote or ("\\" if trailing_escape else ""))

    flags = []
    cursor = 0
    for token in tokens:
        accumulated = ""
        start = cursor
        carried_quote = ""
        while cursor < len(merged) and accumulated != token:
            word = merged[cursor]
            value, _, open_span = _value(word, carried_quote)
            carried_quote = open_span if isinstance(open_span, str) else ""
            accumulated += value
            cursor += 1
            # Whitespace the splitter consumed but posix kept: either escaped
            # by a trailing backslash (`/tmp/a\ b`) or sitting inside a quote
            # span the word left open (`-m'fix a b'`). Consume the run the
            # token actually shows, not a single space — a span holding two
            # spaces or a tab (`-m'fix  a b'`, a typo anyone makes) never
            # reconciled, the walk gave up, and the quoted `>` on the line was
            # re-read as a real operator, denying a reader.
            while (open_span and accumulated != token
                   and token[len(accumulated):len(accumulated) + 1].isspace()):
                accumulated += token[len(accumulated)]
        if accumulated != token:
            return []
        if cursor - start == 1:
            flags.append(_value(merged[start])[1])  # noqa: E501
        else:
            # Assembled from more than one raw word, so not a quoted literal.
            flags.append(False)
    return flags


def _walk_segments(tokens, shell_values, targets, repo_root, execution_cwd="",
                   quoted=None, depth=0, *, command):
    """Segment the line and classify each segment.

    `command` is keyword-only and required on purpose: without it
    `_quotable_operators` finds no quoted operators, which disables both
    alternate readings — the allow-leaning direction, in a helper whose job is
    conservative unioning. A default would let a future call site opt out of
    classification by omission.

    When the quoting is known, clustered operators are decomposed and there is
    one reading. When it is not — `_quoted_flags` returns None for ordinary
    adjacent-quote concatenation such as `"$PWD"/doc` or `/tmp/a\\ b` — both
    readings are classified and their targets unioned.

    Picking one was tried and was wrong in both directions. Expanding under
    unknown alignment decomposed a quoted operator *literal* (`tee ');'
    <artifact>`, where `');'` is a filename) into real boundaries that
    truncated the segment before the artifact. Declining to expand reopened the
    clustered-closer bypass for any line that also contains one adjacent-quote
    word, so `(ls "$PWD"/doc); cp <payload> <receipt>` allowed — ordinary
    phrasing, and the exact route the expansion was added to close. Neither
    reading is safe alone; a deny from either denies.
    """
    expanded, expanded_quoted = _expand_control_clusters(tokens, quoted)
    if quoted is None and expanded is not tokens:
        both: list[dict] = []
        _walk_segments_once(
            tokens, dict(shell_values), both, repo_root, execution_cwd,
            quoted, depth, command=command,
        )
        _walk_segments_once(
            expanded, dict(shell_values), both, repo_root, execution_cwd,
            expanded_quoted, depth, command=command,
        )
        targets.extend(item for item in both if item not in targets)
        return
    _walk_segments_once(
        expanded, shell_values, targets, repo_root, execution_cwd,
        expanded_quoted, depth, command=command,
    )


def _walk_segments_once(tokens, shell_values, targets, repo_root,
                        execution_cwd="", quoted=None, depth=0, *, command):
    idx = 0
    ambiguous_previous = None
    quotable = (
        _quotable_operators(command) if quoted is None
        else collections.Counter()
    )
    # A quoted spelling only explains as many occurrences as there are of it.
    # Skipping on presence let one quoted `>` suppress every real `>` on the
    # line — the same laundering the count rule already prevents for segment
    # boundaries just below.
    quoted_literal = (
        _quoted_operator_words(command, tokens) if quoted is None else []
    )
    boundaries = collections.Counter(
        token for token in tokens if token in BOUNDARY_TOKENS
    )
    # An operator is only reinterpretable when there are at least as many
    # quoted spellings of it as there are boundaries of it. This gates the
    # pairwise merge as well as the whole-line reading: one quoted `;` was
    # licensing a merge across a *real* `;`, which joined `rm -rf "$PWD"/build`
    # to `find . -name '*.py' …` and glob-expanded into a deny on `install.py`,
    # a file appearing nowhere on the line.
    mergeable = {
        operator for operator, available in quotable.items()
        if boundaries[operator] <= available
    }
    segments_walked = 0
    while idx < len(tokens):
        # Segment count is its own cost multiplier, independent of operand
        # count: 1000 two-operand segments took 3.06 s while the per-operand
        # check above could never fire, because `index % 256` never comes round
        # for a two-operand segment. main() turns an exhausted budget into a
        # deny, so stopping here fails closed.
        segments_walked += 1
        if not segments_walked % 64 and _budget_exhausted():
            return
        j = idx
        # A *quoted* control operator is a literal argument, not a boundary.
        # `sed -i s/a/b/ '|' <receipt>` used to end the segment here, leaving
        # the artifact as the next segment's command word where no verb branch
        # matches — so the write was never classified. Real operators are never
        # quoted, so consulting the flags cannot suppress a true split.
        while j < len(tokens) and not (
            tokens[j] in BOUNDARY_TOKENS
            and not (quoted is not None and j < len(quoted) and quoted[j])
        ):
            j += 1
        segment = tokens[idx:j]
        segment_quoted = None if quoted is None else list(quoted[idx:j])
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
        _extract_redirect_targets(
            expanded, targets, repo_root, execution_cwd, segment_quoted,
            quoted_literal[idx:j],
        )
        _process_segment(
            expanded, targets, repo_root, execution_cwd, segment_quoted, depth,
        )
        if quoted is None:
            # Unknown alignment means this split may be wrong: the operator at
            # `j` could be a quoted literal argument. Classify this segment
            # joined to the previous one under that reading too.
            #
            # The whole-line union below cannot cover it — that reading
            # dispatches on the line's *first* command word, so
            # `echo "a"b ; touch '|' <artifact>` was still dispatched on
            # `echo` and the write allowed. Pairwise merging is linear: every
            # token appears in at most two merged pairs.
            if (ambiguous_previous is not None
                    and tokens[idx - 1] in mergeable):
                merged: list[dict] = []
                _process_segment(
                    ambiguous_previous + [tokens[idx - 1]] + expanded,
                    merged, repo_root, execution_cwd, None, depth,
                )
                targets.extend(item for item in merged if item not in targets)
            ambiguous_previous = expanded
        idx = j + 1
    # Every boundary must be accounted for by a *distinct* quoted-operator
    # word. Presence alone is not enough: `find . -exec grep -l foo {} ';' ;
    # wc -l <plan>` has two `;` boundaries and only one quoted `;` to explain
    # them, so the whole-line reading is impossible however you assign it —
    # one real separator is left over. Treating one quoted word as licence for
    # every boundary of its kind denied that ordinary `find` idiom.
    unsplit_is_possible = bool(quotable) and all(
        operator in mergeable for operator in boundaries
    )
    if quoted is None and unsplit_is_possible:
        # The split itself may be wrong: a quoted `|` is an argument, not a
        # boundary, so classify the unsplit line too and union. Gated on the
        # raw text actually showing a quoted operator — without one there is no
        # reading in which the line is a single command, and taking it anyway
        # denied ordinary readers (`rm -rf "$PWD"/build ; git diff -- <plan>`).
        #
        # *Every* boundary must be quotable, not just one: a single unquoted
        # operator disproves the whole-line reading outright, so one quoted
        # operator elsewhere must not drag the rest of the line into it.
        #
        # Once per line, NOT once per segment. Inside the loop this was
        # O(segments x tokens), and `quoted is None` is the ordinary
        # adjacent-quote case — a 22 KB line of `echo "a"b;` padding took longer
        # than the hook's 3s budget, which emits no decision and therefore
        # allows. That made any deny convertible to an allow by padding the
        # command. The target set is identical either way; the call does not
        # depend on `idx`.
        unsplit: list[dict] = []
        _process_segment(tokens, unsplit, repo_root, execution_cwd, None, depth)
        targets.extend(item for item in unsplit if item not in targets)
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
    global _deadline
    _deadline = time.monotonic() + _ANALYSIS_BUDGET_SECONDS
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
    # Overrunning the budget is not a quiet "classified nothing" — a killed
    # hook emits no decision, which allows the whole line, so a line that runs
    # out of budget fails *closed* like the oversize path above. Degrading to
    # "not extracted" would be the allow itself.
    #
    # This can only fire if some inner loop noticed the budget and returned;
    # the two that do are operand classification (one realpath per operand:
    # ~35 KB of short operands spent 2.2 s of CPU before reaching the real
    # write) and the segment walk (segment count multiplies cost independently
    # of operand count). A cost path that consults neither still overruns
    # without ever reaching this line.
    if _budget_exhausted():
        _deny({
            "path": "RECEIPTS.jsonl",
            "category": "protected-artifact",
            "method": "uninspectable command (analysis budget exhausted)",
        }, command[:200])
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
