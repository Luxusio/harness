#!/usr/bin/env python3
from __future__ import annotations
import os
import ast
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
REDIRECT_TOKENS = {">", ">>", "1>", "1>>"}
LAST_ARG_MUTATORS = {"cp", "mv", "install", "touch", "truncate"}
TEE_COMMAND = "tee"
LIFECYCLE_RECEIPT_ENTRYPOINTS = {
    "background_hook.py", "subagent_lifecycle.py", "codex_lifecycle_watcher.py",
}
LIFECYCLE_RECEIPT_MODULES = {
    os.path.splitext(name)[0] for name in LIFECYCLE_RECEIPT_ENTRYPOINTS
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
_PY_PATTERNS = [
    re.compile(r"(?:pathlib\.)?Path\(\s*['\"]([^'\"]+)['\"]\s*\)\.(?:write_text|write_bytes)"),
    re.compile(r"os\.replace\([^,]+,\s*['\"]([^'\"]+)['\"]\)"),
    re.compile(r"shutil\.copy(?:2)?\([^,]+,\s*['\"]([^'\"]+)['\"]\)"),
]
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
def _append_target(targets, token, method, repo_root, execution_cwd=""):
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
    if code.startswith("$"):
        try:
            code = bytes(code[1:], "utf-8").decode("unicode_escape")
        except (UnicodeDecodeError, UnicodeEncodeError):
            _append_target(
                targets, "doc/harness/goals/current.json",
                "unresolved ANSI-C Python command", repo_root, execution_cwd,
            )
            return
    for pat in _PY_PATTERNS:
        for match in pat.findall(code):
            _append_target(
                targets, match, "python inline write", repo_root, execution_cwd
            )
    try:
        tree = ast.parse(code)
    except (SyntaxError, ValueError):
        return
    strings = {}
    string_history = set()
    call_environments = {}
    def string_value(node, environment=None):
        environment = strings if environment is None else environment
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return environment.get(node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = string_value(node.left, environment)
            right = string_value(node.right, environment)
            return left + right if left is not None and right is not None else None
        return None
    def bound_names(target):
        if isinstance(target, ast.Name):
            return {target.id}
        if isinstance(target, (ast.Tuple, ast.List)):
            return set().union(*(bound_names(item) for item in target.elts))
        return set()

    def argument_names(arguments):
        names = {
            arg.arg for arg in (
                list(arguments.posonlyargs) + list(arguments.args)
                + list(arguments.kwonlyargs)
            )
        }
        if arguments.vararg:
            names.add(arguments.vararg.arg)
        if arguments.kwarg:
            names.add(arguments.kwarg.arg)
        return names
    def merge_environments(environment, candidates):
        if not candidates:
            return
        common = set.intersection(*(set(item) for item in candidates))
        merged = {
            name: candidates[0][name]
            for name in common
            if all(item[name] == candidates[0][name] for item in candidates[1:])
        }
        environment.clear()
        environment.update(merged)
    def stamp_expression_calls(node, environment):
        if isinstance(node, ast.Lambda):
            for default in (
                list(node.args.defaults)
                + [item for item in node.args.kw_defaults if item is not None]
            ):
                stamp_expression_calls(default, environment)
            child = dict(environment)
            for name in argument_names(node.args):
                child.pop(name, None)
            stamp_expression_calls(node.body, child)
            return
        if isinstance(node, ast.NamedExpr):
            stamp_expression_calls(node.value, environment)
            value = string_value(node.value, environment)
            for name in bound_names(node.target):
                if value is None:
                    environment.pop(name, None)
                else:
                    environment[name] = value
                    string_history.add(value)
            return
        if isinstance(node, ast.IfExp):
            stamp_expression_calls(node.test, environment)
            branches = []
            for expression in (node.body, node.orelse):
                child = dict(environment)
                stamp_expression_calls(expression, child)
                branches.append(child)
            merge_environments(environment, branches)
            return
        if isinstance(node, ast.BoolOp):
            continuation = dict(environment)
            outcomes = []
            for value in node.values:
                stamp_expression_calls(value, continuation)
                outcomes.append(dict(continuation))
            merge_environments(environment, outcomes)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            child = dict(environment)
            for generator in node.generators:
                stamp_expression_calls(generator.iter, child)
                for name in bound_names(generator.target):
                    child.pop(name, None)
                for condition in generator.ifs:
                    stamp_expression_calls(condition, child)
            values = (
                (node.key, node.value) if isinstance(node, ast.DictComp)
                else (node.elt,)
            )
            for value in values:
                stamp_expression_calls(value, child)
            return
        if isinstance(node, ast.Call):
            stamp_expression_calls(node.func, environment)
            for argument in node.args:
                stamp_expression_calls(argument, environment)
            for keyword in node.keywords:
                stamp_expression_calls(keyword.value, environment)
            call_environments[id(node)] = dict(environment)
            return
        for child in ast.iter_child_nodes(node):
            if not isinstance(child, ast.stmt):
                stamp_expression_calls(child, environment)
    def body_environment(node, environment):
        child = dict(environment)
        names = set()
        if isinstance(node, (ast.For, ast.AsyncFor)):
            names.update(bound_names(node.target))
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            names.update(argument_names(node.args))
        for item in getattr(node, "items", ()):
            if item.optional_vars is not None:
                names.update(bound_names(item.optional_vars))
        for name in names:
            child.pop(name, None)
        return child
    def process_statements(statements, environment):
        for node in statements:
            stamp_expression_calls(node, environment)
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                value = string_value(node.value, environment)
                targets_nodes = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets_nodes:
                    names = bound_names(target)
                    for name in names:
                        if value is not None and isinstance(target, ast.Name):
                            environment[name] = value
                            string_history.add(value)
                        else:
                            environment.pop(name, None)
                continue
            if isinstance(node, ast.AugAssign):
                for name in bound_names(node.target):
                    environment.pop(name, None)
                continue
            if isinstance(node, ast.If):
                test = node.test.value if isinstance(node.test, ast.Constant) else None
                if test is True or test is False:
                    selected = node.body if test else node.orelse
                    process_statements(selected, environment)
                else:
                    outcomes = [dict(environment)] if not node.orelse else []
                    for block in (node.body, node.orelse):
                        child = dict(environment)
                        process_statements(block, child)
                        outcomes.append(child)
                    merge_environments(environment, outcomes)
                continue
            child_blocks = []
            for field in ("body", "orelse", "finalbody"):
                block = getattr(node, field, None)
                if isinstance(block, list):
                    child_blocks.append(block)
            outcomes = [dict(environment)]
            for handler in getattr(node, "handlers", ()):
                handler_environment = dict(environment)
                if handler.name:
                    handler_environment.pop(handler.name, None)
                process_statements(getattr(handler, "body", []), handler_environment)
                outcomes.append(handler_environment)
            for case in getattr(node, "cases", ()):
                child_blocks.append(getattr(case, "body", []))
            for block in child_blocks:
                child = body_environment(node, environment)
                process_statements(block, child)
                outcomes.append(child)
            if child_blocks:
                merge_environments(environment, outcomes)
    process_statements(tree.body, strings)
    filesystem_mutators = {
        "link", "rename", "replace", "remove", "unlink", "write_text",
        "write_bytes", "hardlink_to", "link_to", "chmod",
    }
    open_aliases = {"open"}
    os_open_aliases = set()
    io_modules = {"io", "builtins"}
    os_modules = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"io", "builtins"}:
            open_aliases.update(
                alias.asname or alias.name
                for alias in node.names if alias.name == "open"
            )
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            os_open_aliases.update(
                alias.asname or alias.name
                for alias in node.names if alias.name == "open"
            )
        elif isinstance(node, ast.Import):
            io_modules.update(
                alias.asname or alias.name
                for alias in node.names if alias.name in {"io", "builtins"}
            )
            os_modules.update(
                alias.asname or alias.name
                for alias in node.names if alias.name == "os"
            )
    changed = True
    while changed:
        changed = False
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Assign, ast.AnnAssign)):
                continue
            value = node.value
            aliases_open = (
                isinstance(value, ast.Name) and value.id in open_aliases
            ) or (
                isinstance(value, ast.Attribute)
                and value.attr == "open"
                and isinstance(value.value, ast.Name)
                and value.value.id in io_modules
            )
            aliases_os_open = (
                isinstance(value, ast.Name) and value.id in os_open_aliases
            ) or (
                isinstance(value, ast.Attribute)
                and value.attr == "open"
                and isinstance(value.value, ast.Name)
                and value.value.id in os_modules
            )
            if aliases_os_open:
                targets_nodes = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets_nodes:
                    if isinstance(target, ast.Name) and target.id not in os_open_aliases:
                        os_open_aliases.add(target.id)
                        changed = True
            if not aliases_open:
                continue
            targets_nodes = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets_nodes:
                if isinstance(target, ast.Name) and target.id not in open_aliases:
                    open_aliases.add(target.id)
                    changed = True
    open_mutation = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        direct_open = isinstance(node.func, ast.Name) and node.func.id in open_aliases
        module_open = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in io_modules
        )
        path_open = getattr(node.func, "attr", "") == "open" and not module_open
        getattr_open = (
            isinstance(node.func, ast.Call)
            and isinstance(node.func.func, ast.Name)
            and node.func.func.id == "getattr"
            and len(node.func.args) > 1
            and string_value(node.func.args[1]) == "open"
        )
        os_open = (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "open"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in os_modules
        ) or (isinstance(node.func, ast.Name) and node.func.id in os_open_aliases)
        if not direct_open and not module_open and not path_open and not getattr_open and not os_open:
            continue
        if os_open:
            flags = node.args[1] if len(node.args) > 1 else None
            readonly_flag = (
                isinstance(flags, ast.Constant) and flags.value == os.O_RDONLY
            ) or (
                isinstance(flags, ast.Attribute)
                and flags.attr == "O_RDONLY"
            and isinstance(flags.value, ast.Name)
            and flags.value.id in os_modules
            ) or (
                isinstance(flags, ast.Call)
                and isinstance(flags.func, ast.Name)
                and flags.func.id == "getattr"
                and len(flags.args) > 1
                and isinstance(flags.args[0], ast.Name)
                and flags.args[0].id in os_modules
                and string_value(
                    flags.args[1], call_environments.get(id(node), strings)
                ) == "O_RDONLY"
            )
            open_mutation = not (
                readonly_flag
            )
            if open_mutation:
                break
            continue
        mode_index = 1 if direct_open or module_open or getattr_open else 0
        mode_node = node.args[mode_index] if len(node.args) > mode_index else next(
            (item.value for item in node.keywords if item.arg == "mode"), None,
        )
        if mode_node is None:
            continue
        mode_value = string_value(
            mode_node, call_environments.get(id(node), strings),
        )
        if mode_value is not None:
            open_mutation = bool(set(mode_value) & set("wax+"))
        else:
            open_mutation = True
        if open_mutation:
            break
    filesystem_mutation = open_mutation or any(
        isinstance(node, ast.Call)
        and (getattr(node.func, "id", "") or getattr(node.func, "attr", ""))
        in filesystem_mutators
        for node in ast.walk(tree)
    )
    if filesystem_mutation:
        for value in string_history:
            _append_target(
                targets, value, "python filesystem mutation",
                repo_root, execution_cwd,
            )
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                _append_target(
                    targets, node.value, "python filesystem mutation",
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
            if any(alias.name in PROTECTED_MUTATION_SYMBOLS for alias in node.names):
                return True
        elif isinstance(node, ast.Attribute):
            if (
                node.attr in LIFECYCLE_RECEIPT_MODULES
                or node.attr in PROTECTED_MUTATION_SYMBOLS
                or "receipt_stream" in node.attr and "append" in node.attr
            ):
                return True
        elif isinstance(node, ast.Name) and (
            node.id in PROTECTED_MUTATION_SYMBOLS
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
                if attribute is None or attribute in PROTECTED_MUTATION_SYMBOLS:
                    return True
    return False
def _protected_lifecycle_execution(argv, execution_cwd=""):
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
            return "$(" in code or "`" in code or _python_code_exposes_lifecycle(code)
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
            inspected_script = script if os.path.isabs(script) else os.path.join(
                execution_cwd or os.getcwd(), script,
            )
            try:
                info = os.lstat(inspected_script)
                if (
                    stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and not info.st_mode & 0o022
                    and info.st_size <= _COMMAND_LENGTH_CAP
                ):
                    with open(inspected_script, "r", encoding="utf-8") as handle:
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
def _safe_gated_path_inspection(argv, raw_argv=()):
    if not argv:
        return False
    cmd, args = os.path.basename(argv[0]), argv[1:]
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
        return index < len(args) and args[index] in {"diff", "show", "log", "status", "grep"} and not output_option and not any("open-files-in-pager" in arg or arg in {"--ext-diff", "--textconv"} for arg in args)
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
    non_env = _unwrap_execution(raw_argv)
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

    visible = " ".join(raw_argv)
    compact = re.sub(r"[^A-Za-z0-9]", "", visible).lower()
    protected_marker = (
        any(name.replace("_", "").replace(".py", "") in compact
            for name in LIFECYCLE_RECEIPT_ENTRYPOINTS)
        or any(name.replace("_", "") in compact for name in PROTECTED_MUTATION_SYMBOLS)
    )
    if (
        (protected_marker and not _safe_lifecycle_source_inspection(non_env))
        or _protected_lifecycle_execution(non_env, execution_cwd)
    ):
        goal_control = any(
            name.replace("_", "") in compact for name in GOAL_MUTATION_SYMBOLS
        )
        targets.append({
            "path": (
                "doc/harness/goals/current.json" if goal_control
                else "RECEIPTS.jsonl"
            ),
            "category": "protected-artifact",
            "method": (
                "direct native Goal control entrypoint invocation" if goal_control
                else "direct lifecycle receipt entrypoint invocation"
            ),
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
        return
    if cmd in LAST_ARG_MUTATORS:
        _append_target(
            targets, _last_non_option(non_env), cmd, repo_root, execution_cwd
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
        _extract_python_inline_targets(
            non_env, targets, repo_root, execution_cwd
        )
        return
    if _uninspected_inline(non_env):
        if _gated_path_risk(
            segment_tokens, repo_root, execution_cwd,
        ):
            targets.append({
                "path": "doc/harness/goals/current.json",
                "category": "protected-artifact",
                "method": "uninspected inline runtime",
            })
        return
    if _safe_gated_path_inspection(non_env, segment_tokens):
        return
    for candidate in _embedded_path_candidates(non_env[1:]):
        before = len(targets)
        _append_target(
            targets, candidate, "unrecognized executable with gated path",
            repo_root, execution_cwd,
        )
        if len(targets) > before and targets[-1]["category"] == "source":
            targets.pop()
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

    shell_values = {}
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

    return targets
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
