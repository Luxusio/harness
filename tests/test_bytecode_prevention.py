from __future__ import annotations

import importlib.util
import json
import marshal
import os
import re
import stat
import struct
import subprocess
import sys
import tomllib
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
HOOKS_JSON = REPO / "plugin" / "hooks" / "hooks.json"

sys.path.insert(0, str(SCRIPTS))


def _load_install():
    """Import `install.py` by path; it is not an importable package member."""
    name = "harness_install_for_bytecode_test"
    spec = importlib.util.spec_from_file_location(name, REPO / "install.py")
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: `install.py` defines dataclasses, and
    # `dataclasses` resolves annotations through `sys.modules[cls.__module__]`.
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def test_hooks_never_write_bytecode_into_an_installed_tree():
    """AC1: prevention, so no cache can be created to go stale later."""
    config = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    events = config.get("hooks", config)

    commands = [
        hook.get("command", "")
        for matchers in events.values()
        if isinstance(matchers, list)
        for matcher in matchers
        if isinstance(matcher, dict)
        for hook in matcher.get("hooks", [])
    ]
    python_commands = [c for c in commands if "python3" in c]
    assert python_commands, "no python hook commands found — the check would be vacuous"

    for command in python_commands:
        assert command.startswith("PYTHONDONTWRITEBYTECODE=1 "), command
        # C-12 stays intact: this change must not cost the harness fail-safety.
        assert command.rstrip().endswith("|| true"), command

def test_every_process_that_imports_the_installed_tree_disables_bytecode():
    """`plugin/hooks/hooks.json` is not the whole surface, and checking only it
    was the first version's real defect.

    Importer sets overlap, so no single launcher explains the six caches found
    poisoned on 2026-09-16: `_lib` is imported by hook commands, by the MCP
    server, and by most `scripts/*.py` entrypoints. What `hooks.json` alone
    cannot cover is the MCP server and the Codex hook commands, which
    `install.py` generates rather than reading from that file.
    """
    install = _load_install()

    config = install._codex_hooks_config(Path("/installed/plugin"))
    # Every generated Codex hook command, not merely one of them.
    commands = [
        hook["command"]
        for matchers in config["hooks"].values()
        for matcher in matchers
        for hook in matcher["hooks"]
    ]
    assert len(commands) == 4, commands
    for command in commands:
        assert command.startswith("PYTHONDONTWRITEBYTECODE=1 "), command

    codex_mcp = install._codex_mcp_config(Path("/installed/plugin"))
    env = codex_mcp["mcpServers"]["harness"]["env"]
    assert env.get("PYTHONDONTWRITEBYTECODE") == "1", env

    # The Claude MCP registration builds its env as `claude mcp add -e ...`
    # arguments rather than a dict, so assert against the source of that list.
    source = (REPO / "install.py").read_text(encoding="utf-8")
    env_args_block = source.split("env_args = [", 1)[1].split("]", 1)[0]
    assert "PYTHONDONTWRITEBYTECODE=1" in env_args_block, env_args_block

    # The TOML block is the registration Codex actually launches — missing it
    # while the JSON sibling had it is exactly how the first attempt at this
    # test passed while the live Codex MCP process kept writing bytecode.
    block = tomllib.loads(install._mcp_block("/installed/plugin"))
    assert block["mcp_servers"]["harness"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"

    # The plugin's own MCP manifest. This is the launcher for the
    # marketplace-cached Claude plugin, i.e. the tree under
    # ~/.claude/plugins/cache/harness/, and it is registered by the plugin
    # payload rather than by install.py — so every install.py-scoped assertion
    # above is blind to it. Its `__pycache__` was populated in the field.
    manifest = json.loads((REPO / "plugin" / ".mcp.json").read_text(encoding="utf-8"))
    assert manifest["mcpServers"]["harness"]["env"]["PYTHONDONTWRITEBYTECODE"] == "1"

    # And the documented mirror, so the example cannot drift from the writer.
    example = (REPO / "plugin-codex" / "config.toml.example").read_text(
        encoding="utf-8"
    )
    assert tomllib.loads(example)["mcp_servers"]["harness"]["env"][
        "PYTHONDONTWRITEBYTECODE"
    ] == "1"

def test_documented_script_invocations_also_disable_bytecode():
    """Skills and agents run `scripts/*.py` from the installed tree by Bash.

    Those invocations import `_lib` — the single most widely imported module in
    the tree, and one of the six found poisoned. Covering only hook commands and
    MCP registrations left this writer class able to recreate the exact cache
    the REQ is about.
    """
    roots = [REPO / "plugin", REPO / "plugin-codex",
             REPO / "doc" / "harness" / "patterns"]
    extra = [REPO / "README.md", REPO / "README.codex.md"]
    # Any plugin-root variable spelling, braced or not, quoted or not, and any
    # script name including the extensionless `review-read`/`review-log`. The
    # first version of this test matched only `${CLAUDE|HARNESS_PLUGIN_ROOT}`
    # with a `.py` suffix, so it could not see nine real invocations that used
    # `${_PLUGIN_ROOT}` or an unbraced `$HARNESS_PLUGIN_ROOT` — the class it
    # exists to prevent walked straight past it.
    pattern = re.compile(
        r"(?P<prefix>\S*\s*)python3 \"?\$\{?[A-Za-z_]*PLUGIN_ROOT\}?/scripts/"
    )

    unprefixed = []
    seen = 0
    for root in roots:
        for path in list(root.rglob("*.md")) + (extra if root == roots[0] else []):
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                for match in pattern.finditer(line):
                    seen += 1
                    if "PYTHONDONTWRITEBYTECODE=1 " not in match.group("prefix"):
                        unprefixed.append(f"{path.relative_to(REPO)}: {line.strip()}")

    assert seen, "no documented script invocations found — the check would be vacuous"
    assert not unprefixed, unprefixed
