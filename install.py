#!/usr/bin/env python3
"""Unified install entry point for harness — Codex CLI and Claude Code, in parallel.

Auto-detects which CLIs are present and installs harness on every runtime it finds.

Usage:
    python3 install.py                  # install on every detected runtime in parallel
    python3 install.py --codex-only     # only Codex (skip Claude even if present)
    python3 install.py --claude-only    # only Claude
    python3 install.py --dry-run        # print what each runtime would do; no mutation
    python3 install.py --force          # overwrite existing harness MCP config without prompting
    python3 install.py --config-path P  # override Codex ~/.codex/config.toml path (testing)

Stdlib-only. Compatible with the Python ships in mise / system / venv.

Per-runtime steps:
  Codex:
    1. Verify codex --version >= .codex-version pin
    2. Copy plugin payload into ~/.codex/harness/
    3. Install harness@harness into Codex's plugin cache
    4. codex plugin marketplace add ~/.codex/harness      (idempotent)
    5. Merge [plugins."harness@harness"] + [mcp_servers.harness] into config.toml

  Claude:
    1. Verify claude --version
    2. Copy plugin payload into $HARNESS_DEST/plugin or ~/.claude/harness-dev/plugin
    3. Copy root marketplace manifest into $HARNESS_DEST/.claude-plugin/
    4. claude plugin marketplace add/update installed mirror root
    5. claude plugin install harness@harness on first install
    6. claude mcp add harness ... -- python3 <installed plugin>/mcp/harness_server.py
    7. Print verification command
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = REPO_ROOT / "plugin"
PLUGIN_CODEX_ROOT = REPO_ROOT / "plugin-codex"
CODEX_INSTALL_ROOT = Path.home() / ".codex" / "harness"
CODEX_PLUGIN_ID = "harness@harness"
CODEX_PLUGIN_MARKETPLACE = "harness"
CODEX_PLUGIN_NAME = "harness"
CODEX_VERSION_PIN_FILE = PLUGIN_CODEX_ROOT / ".codex-version"
HARNESS_MCP_SERVER = PLUGIN_ROOT / "mcp" / "harness_server.py"
DEFAULT_CODEX_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
DEFAULT_CLAUDE_INSTALL_ROOT = Path.home() / ".claude" / "harness-dev"


def _python_cmd() -> str:
    """Return a Python executable that bypasses cwd-sensitive shims."""
    return str(Path(sys.executable).resolve())

@dataclass
class InstallResult:
    runtime: str
    ok: bool
    summary: str
    steps: list[str] = field(default_factory=list)
    backup_path: str | None = None


def _read_codex_pin() -> str | None:
    if not CODEX_VERSION_PIN_FILE.is_file():
        return None
    for line in CODEX_VERSION_PIN_FILE.read_text().splitlines():
        s = line.strip()
        if s and not s.startswith("#"):
            return s
    return None


def _version_tuple(v: str) -> tuple[int, ...]:
    out: list[int] = []
    for part in v.split("."):
        digits = "".join(c for c in part if c.isdigit())
        if not digits:
            break
        out.append(int(digits))
    return tuple(out)


def _run(cmd: list[str], dry: bool) -> tuple[int, str, str]:
    if dry:
        return 0, f"[dry-run] {' '.join(cmd)}", ""
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    return p.returncode, p.stdout, p.stderr


def _mcp_block(plugin_root: str) -> str:
    return (
        "# Registers the harness MCP server with Codex. Same protocol both runtimes consume.\n"
        "[mcp_servers.harness]\n"
        f'command = "{_python_cmd()}"\n'
        f'args = ["{plugin_root}/mcp/harness_server.py"]\n'
        f'env = {{ HARNESS_PLUGIN_ROOT = "{plugin_root}", CLAUDE_PLUGIN_ROOT = "{plugin_root}" }}\n'
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 60\n"
        "enabled = true\n"
        "required = false  # set true to fail-fast in `codex exec` when harness is unreachable\n"
    )


def _codex_plugin_block() -> str:
    return (
        "# Marks the local harness plugin as installed/enabled in Codex.\n"
        f'[plugins."{CODEX_PLUGIN_ID}"]\n'
        "enabled = true\n"
    )


def _codex_marketplace_block() -> str:
    return (
        "# Registers the installed local harness marketplace with Codex.\n"
        f"[marketplaces.{CODEX_PLUGIN_MARKETPLACE}]\n"
        f'last_updated = "{_dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")}"\n'
        'source_type = "local"\n'
        f'source = "{CODEX_INSTALL_ROOT}"\n'
    )


def emit_codex_config(plugin_root: str, config_path: str | Path | None = None) -> str:
    pr = str(Path(plugin_root).resolve())
    header = (
        "# ───────────────────────────────────────────────────────────────────\n"
        "# harness v2.3.0 — emitted by install.py\n"
        f"# Plugin root: {pr}\n"
        f"# Generated: {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        "# ───────────────────────────────────────────────────────────────────\n"
        "\n"
    )
    return (
        header
        + _codex_plugin_block()
        + "\n"
        + _codex_marketplace_block()
        + "\n"
        + _mcp_block(pr)
    )


def _do_codex_config_install(snippet: str, config_path: Path, force_merge: bool) -> tuple[bool, str]:
    if config_path.exists():
        existing = config_path.read_text()
        if (
            ("[mcp_servers.harness]" in existing or f'[plugins."{CODEX_PLUGIN_ID}"]' in existing)
            and not force_merge
        ):
            return (
                False,
                (
                    "harness Codex config already present in "
                    f"{config_path}. Re-run with --force to overwrite "
                    "(timestamped backup will be written). Or merge manually."
                ),
            )
        timestamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = config_path.with_suffix(config_path.suffix + f".bak.{timestamp}")
        shutil.copy2(config_path, backup)
        if force_merge:
            stripped = _dedupe_codex_toml_tables(
                _strip_codex_harness_sections(existing),
                ("[mcp_servers.",),
            )
            if stripped.strip():
                new_content = stripped.rstrip("\n") + "\n\n" + snippet
            else:
                new_content = snippet
        else:
            new_content = existing.rstrip("\n") + "\n\n" + snippet
        new_content = _dedupe_codex_toml_tables(
            _dedupe_codex_marketplace_block(new_content),
            ("[mcp_servers.",),
        )
        config_path.write_text(new_content)
        return True, f"Merged into {config_path}; backup at {backup}"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(snippet)
    return True, f"Wrote fresh {config_path}"


def _strip_codex_harness_sections(content: str) -> str:
    """Remove all harness-owned TOML tables and generated harness blocks."""
    target_tables = {
        f'[plugins."{CODEX_PLUGIN_ID}"]',
        f"[marketplaces.{CODEX_PLUGIN_MARKETPLACE}]",
        "[mcp_servers.harness]",
    }
    lines = content.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("# harness v") or (
            stripped.startswith("# ─") and i + 1 < len(lines) and lines[i + 1].strip().startswith("# harness v")
        ):
            i += 1
            while i < len(lines) and (not lines[i].strip() or lines[i].strip().startswith("#")):
                i += 1
            continue
        if stripped in target_tables:
            i += 1
            while i < len(lines):
                next_stripped = lines[i].strip()
                if next_stripped.startswith("[") or next_stripped == "# ccc-managed-mcp begin" or next_stripped.startswith("# harness v"):
                    break
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).strip("\n") + ("\n" if out else "")


def _dedupe_codex_marketplace_block(content: str) -> str:
    lines = content.splitlines()
    out: list[str] = []
    seen = False
    i = 0
    while i < len(lines):
        if lines[i].strip() == f"[marketplaces.{CODEX_PLUGIN_MARKETPLACE}]":
            if seen:
                i += 1
                while i < len(lines) and not lines[i].startswith("[") and not lines[i].startswith("# ccc-managed-mcp end"):
                    i += 1
                continue
            seen = True
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip("\n") + "\n"


def _dedupe_codex_toml_tables(content: str, prefixes: tuple[str, ...]) -> str:
    """Keep the newest occurrence of duplicate TOML tables for selected prefixes."""
    lines = content.splitlines()
    spans: list[tuple[int, int, str]] = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            table = stripped
            if any(table.startswith(prefix) for prefix in prefixes):
                start = i
                i += 1
                while i < len(lines):
                    next_stripped = lines[i].strip()
                    if next_stripped.startswith("[") and next_stripped.endswith("]"):
                        break
                    i += 1
                spans.append((start, i, table))
                continue
        i += 1

    last_span_index = {table: index for index, (_, _, table) in enumerate(spans)}
    remove: set[int] = set()
    for index, (start, end, table) in enumerate(spans):
        if last_span_index[table] != index:
            remove.update(range(start, end))
    if not remove:
        return content.rstrip("\n") + ("\n" if content else "")

    out = [line for index, line in enumerate(lines) if index not in remove]
    return "\n".join(out).rstrip("\n") + ("\n" if out else "")


def emit_and_install_codex_config(
    plugin_root: str,
    config_path: str | Path | None = None,
    force: bool = False,
) -> dict:
    cfg = Path(config_path) if config_path else DEFAULT_CODEX_CONFIG_PATH
    plugin_path = Path(plugin_root).resolve()
    if not plugin_path.is_dir():
        return {
            "ok": False,
            "message": f"plugin root not a directory: {plugin_path}",
            "snippet": "",
            "config_path": str(cfg),
            "backup_path": None,
            "blocks_added": [],
        }
    snippet = emit_codex_config(str(plugin_path), cfg)
    pre_existing = cfg.exists() and (
        "[mcp_servers.harness]" in cfg.read_text()
        or f'[plugins."{CODEX_PLUGIN_ID}"]' in cfg.read_text()
    )
    ok, message = _do_codex_config_install(snippet, cfg, force)
    backup = None
    if ok and cfg.exists() and pre_existing:
        candidates = sorted(cfg.parent.glob(f"{cfg.name}.bak.*"))
        if candidates:
            backup = str(candidates[-1])
    blocks = [
        f'[plugins."{CODEX_PLUGIN_ID}"]',
        "[mcp_servers.harness]",
    ]
    return {
        "ok": ok,
        "message": message,
        "snippet": snippet,
        "config_path": str(cfg),
        "backup_path": backup,
        "blocks_added": blocks if ok else [],
    }


def _copytree_clean(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(
        src,
        dst,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"),
    )


def _codex_hooks_config(plugin_root: Path) -> dict:
    root = plugin_root.resolve()
    hooks = {
        "SessionStart": root / "scripts" / "hook_session_start.py",
        "PreToolUse": root / "scripts" / "hook_pre_tool_use.py",
        "UserPromptSubmit": root / "scripts" / "hook_user_prompt_submit.py",
        "PostToolUse": root / "scripts" / "hook_post_tool_use.py",
    }

    return {
        "hooks": {
            "SessionStart": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(_python_cmd())} {shlex.quote(str(hooks['SessionStart']))}",
                            "timeout": 20,
                            "statusMessage": "Loading harness context",
                        }
                    ]
                }
            ],
            "PreToolUse": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(_python_cmd())} {shlex.quote(str(hooks['PreToolUse']))}",
                            "timeout": 5,
                            "statusMessage": "Checking harness gates",
                        }
                    ]
                }
            ],
            "UserPromptSubmit": [
                {
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(_python_cmd())} {shlex.quote(str(hooks['UserPromptSubmit']))}",
                            "timeout": 3,
                            "statusMessage": "Loading harness memory",
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(_python_cmd())} {shlex.quote(str(hooks['PostToolUse']))}",
                            "timeout": 3,
                            "statusMessage": "Checking harness routing",
                        }
                    ],
                }
            ],
        }
    }


_CODEX_HOOK_EVENT_LABELS = {
    "SessionStart": "session_start",
    "PreToolUse": "pre_tool_use",
    "UserPromptSubmit": "user_prompt_submit",
    "PostToolUse": "post_tool_use",
}


def _canonical_json(value):
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def _codex_hook_trust_hash(event_name: str, entry: dict, hook: dict) -> str:
    """Reproduce Codex's normalized command-hook trust hash.

    Codex does not currently expose a public CLI for installers to trust hooks.
    The TUI stores a hash of the normalized hook identity in config.toml; this
    mirrors that identity for harness-owned plugin hooks only.
    """
    identity = {
        "event_name": _CODEX_HOOK_EVENT_LABELS[event_name],
        "hooks": [
            {
                "async": False,
                "command": hook["command"],
                "timeout": max(1, int(hook.get("timeout") or 600)),
                "type": "command",
            }
        ],
    }
    if entry.get("matcher"):
        identity["matcher"] = entry["matcher"]
    if hook.get("statusMessage"):
        identity["hooks"][0]["statusMessage"] = hook["statusMessage"]
    serialized = json.dumps(_canonical_json(identity), separators=(",", ":"))
    return "sha256:" + hashlib.sha256(serialized.encode()).hexdigest()


def _codex_hook_trust_state(plugin_id: str, hooks_config: dict) -> dict[str, str]:
    state: dict[str, str] = {}
    hooks = hooks_config.get("hooks", {})
    for event_name, entries in hooks.items():
        if event_name not in _CODEX_HOOK_EVENT_LABELS:
            continue
        for group_index, entry in enumerate(entries):
            for handler_index, hook in enumerate(entry.get("hooks", [])):
                if hook.get("type") != "command":
                    continue
                key = (
                    f"{plugin_id}:hooks.json:"
                    f"{_CODEX_HOOK_EVENT_LABELS[event_name]}:"
                    f"{group_index}:{handler_index}"
                )
                state[key] = _codex_hook_trust_hash(event_name, entry, hook)
    return state


def _toml_escape_basic(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _existing_hook_state_enabled_lines(content: str, keys: set[str]) -> dict[str, str]:
    lines = content.splitlines()
    enabled: dict[str, str] = {}
    wanted_tables = {f'[hooks.state."{_toml_escape_basic(key)}"]': key for key in keys}
    i = 0
    while i < len(lines):
        key = wanted_tables.get(lines[i].strip())
        if not key:
            i += 1
            continue
        i += 1
        while i < len(lines) and not lines[i].strip().startswith("["):
            stripped = lines[i].strip()
            if stripped.startswith("enabled "):
                enabled[key] = stripped
            i += 1
    return enabled


def _strip_codex_hook_state_tables(content: str, keys: set[str]) -> str:
    if not keys:
        return content
    target_tables = {f'[hooks.state."{_toml_escape_basic(key)}"]' for key in keys}
    lines = content.splitlines()
    out: list[str] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() in target_tables:
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("["):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out).rstrip("\n") + ("\n" if out else "")


def _dedupe_hooks_state_parent_table(content: str) -> str:
    """Keep at most one bare [hooks.state] parent table.

    Codex itself may already have emitted the parent table. Re-emitting it makes
    TOML parsing fail with a duplicate-key error, so installers must only keep
    the first occurrence.
    """
    out: list[str] = []
    seen = False
    for line in content.splitlines():
        if line.strip() == "[hooks.state]":
            if seen:
                continue
            seen = True
        out.append(line)
    return "\n".join(out).rstrip("\n") + ("\n" if out else "")


def _codex_hook_trust_block(state: dict[str, str], enabled_lines: dict[str, str] | None = None) -> str:
    if not state:
        return ""
    enabled_lines = enabled_lines or {}
    lines = [
        "# harness-owned Codex hook trust state",
        "# Recomputed by install.py so harness hook updates do not require manual /hooks review.",
        "",
    ]
    for key, trusted_hash in sorted(state.items()):
        lines.append(f'[hooks.state."{_toml_escape_basic(key)}"]')
        if key in enabled_lines:
            lines.append(enabled_lines[key])
        lines.append(f'trusted_hash = "{_toml_escape_basic(trusted_hash)}"')
        lines.append("")
    return "\n".join(lines).rstrip("\n") + "\n"


def install_codex_hook_trust_state(
    config_path: str | Path,
    hooks_config: dict,
    plugin_id: str = CODEX_PLUGIN_ID,
) -> dict:
    cfg = Path(config_path)
    if not cfg.exists():
        return {"ok": False, "message": f"Codex config not found: {cfg}", "state": {}}
    state = _codex_hook_trust_state(plugin_id, hooks_config)
    if not state:
        return {"ok": True, "message": "no Codex hook trust state to write", "state": state}
    existing = cfg.read_text()
    enabled_lines = _existing_hook_state_enabled_lines(existing, set(state))
    stripped = _dedupe_hooks_state_parent_table(
        _strip_codex_hook_state_tables(existing, set(state))
    ).rstrip("\n")
    block = _codex_hook_trust_block(state, enabled_lines)
    cfg.write_text((stripped + "\n\n" + block).lstrip("\n"))
    return {
        "ok": True,
        "message": f"trusted {len(state)} Codex harness hooks in {cfg}",
        "state": state,
    }


def _codex_mcp_config(shared_plugin_root: Path) -> dict:
    plugin_root = shared_plugin_root.resolve()
    return {
        "mcpServers": {
            "harness": {
                "command": _python_cmd(),
                "args": [str(plugin_root / "mcp" / "harness_server.py")],
                "env": {
                    "HARNESS_PLUGIN_ROOT": str(plugin_root),
                    "CLAUDE_PLUGIN_ROOT": str(plugin_root),
                },
            }
        }
    }


def sync_claude_payload(install_root: Path | None = None) -> Path:
    """Copy the Claude plugin payload under ~/.claude and return plugin/ root."""
    target = install_root or Path(os.environ.get("HARNESS_DEST", DEFAULT_CLAUDE_INSTALL_ROOT))
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO_ROOT / ".claude-plugin", target / ".claude-plugin")
    plugin_target = target / "plugin"
    shutil.copytree(
        PLUGIN_ROOT,
        plugin_target,
        ignore=shutil.ignore_patterns(
            ".git",
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
        ),
    )
    return plugin_target


def sync_codex_payload(install_root: Path | None = None) -> Path:
    """Copy the Codex plugin source under ~/.codex and return its plugin root."""
    if install_root is None:
        install_root = CODEX_INSTALL_ROOT
    install_root.mkdir(parents=True, exist_ok=True)
    for legacy_path in (
        install_root / "plugin-codex",
        install_root / "plugin",
        install_root / ".codex-plugin",
        install_root / "marketplace.json",
    ):
        if legacy_path.exists():
            if legacy_path.is_dir():
                shutil.rmtree(legacy_path)
            else:
                legacy_path.unlink()
    _copytree_clean(PLUGIN_CODEX_ROOT, install_root / "plugins" / "harness")
    codex_plugin_root = install_root / "plugins" / "harness"
    _copytree_clean(PLUGIN_ROOT / "scripts", codex_plugin_root / "scripts")
    _copytree_clean(PLUGIN_ROOT / "mcp", codex_plugin_root / "mcp")
    (codex_plugin_root / "hooks.json").write_text(
        json.dumps(_codex_hooks_config(codex_plugin_root), indent=2) + "\n"
    )
    (codex_plugin_root / ".mcp.json").write_text(
        json.dumps(_codex_mcp_config(codex_plugin_root), indent=2) + "\n"
    )
    marketplace_dir = install_root / ".agents" / "plugins"
    marketplace_dir.mkdir(parents=True, exist_ok=True)
    marketplace = {
        "name": "harness",
        "display_name": "Harness",
        "description": "Task harness plugin for Codex.",
        "owner": {"name": "harness", "url": "https://example.invalid/harness"},
        "plugins": [
            {
                "name": "harness",
                "source": "./plugins/harness",
                "category": "productivity",
                "version": "2.3.0",
                "description": "MCP-backed task planning, development, QA, and docs sync workflow.",
            }
        ],
    }
    (marketplace_dir / "marketplace.json").write_text(json.dumps(marketplace, indent=2) + "\n")
    return codex_plugin_root


def _codex_home_for_config(config_path: str | Path | None) -> Path:
    if config_path:
        return Path(config_path).expanduser().resolve().parent
    return DEFAULT_CODEX_CONFIG_PATH.parent


def _codex_plugin_version(source_root: Path) -> str:
    manifest_path = source_root / ".codex-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text())
    version = str(manifest.get("version", "")).strip()
    if not version:
        return "local"
    return version


def install_codex_plugin_cache(source_root: Path, codex_home: Path) -> Path:
    version = _codex_plugin_version(source_root)
    target = (
        codex_home
        / "plugins"
        / "cache"
        / CODEX_PLUGIN_MARKETPLACE
        / CODEX_PLUGIN_NAME
        / version
    )
    if target.parent.exists():
        shutil.rmtree(target.parent)
    target.parent.mkdir(parents=True, exist_ok=True)
    _copytree_clean(source_root, target)
    (target / "hooks.json").write_text(
        json.dumps(_codex_hooks_config(target), indent=2) + "\n"
    )
    (target / ".mcp.json").write_text(
        json.dumps(_codex_mcp_config(target), indent=2) + "\n"
    )
    return target


def install_codex(*, dry_run: bool, force: bool,
                  config_path: str | None) -> InstallResult:
    steps: list[str] = []
    if not shutil.which("codex"):
        return InstallResult("codex", False, "codex CLI not found in PATH", steps)
    # Step 1: version check
    rc, out, err = _run(["codex", "--version"], dry_run)
    if dry_run:
        steps.append("would run: codex --version")
    else:
        if rc != 0:
            return InstallResult("codex", False, f"codex --version failed: {err}", steps)
        version = out.strip().split()[-1]
        pin = _read_codex_pin()
        if pin and _version_tuple(version) < _version_tuple(pin):
            return InstallResult("codex", False,
                                 f"codex {version} < pin {pin} (see {CODEX_VERSION_PIN_FILE})",
                                 steps)
        steps.append(f"codex {version} >= pin {pin or 'unset'}")
    # Step 2: copy runtime payload into Codex's home so config never points at
    # a transient project checkout.
    if dry_run:
        source_plugin_root = CODEX_INSTALL_ROOT / "plugins" / CODEX_PLUGIN_NAME
        steps.append(f"would sync plugin payload to {CODEX_INSTALL_ROOT}")
    else:
        source_plugin_root = sync_codex_payload()
        steps.append(f"synced plugin payload to {CODEX_INSTALL_ROOT}")

    codex_home = _codex_home_for_config(config_path)
    codex_plugin_source_root = CODEX_INSTALL_ROOT / "plugins" / CODEX_PLUGIN_NAME
    cached_plugin_root = (
        codex_home
        / "plugins"
        / "cache"
        / CODEX_PLUGIN_MARKETPLACE
        / CODEX_PLUGIN_NAME
        / _codex_plugin_version(source_plugin_root if not dry_run else PLUGIN_CODEX_ROOT)
    )
    if dry_run:
        steps.append(f"would install Codex plugin cache entry {CODEX_PLUGIN_ID}")
        steps.append("would install Codex plugin-local hooks.json")
        steps.append("would refresh Codex hook trust state for harness plugin hooks")
    else:
        cached_plugin_root = install_codex_plugin_cache(codex_plugin_source_root, codex_home)
        steps.append(f"installed Codex plugin cache entry {CODEX_PLUGIN_ID} at {cached_plugin_root}")
        steps.append("installed Codex plugin-local hooks.json")

    # Step 4: marketplace add. Dry-run reports it here; real install performs
    # it after the TOML merge so force-merge block replacement cannot trim the
    # [marketplaces.harness] entry Codex writes.
    if dry_run:
        steps.append(f"would run: codex plugin marketplace add {CODEX_INSTALL_ROOT}")
        steps.append("would run: codex features enable plugin_hooks")

    # Remove any stale source before the TOML merge. The merge writes the
    # canonical installed-copy marketplace block, and the add below refreshes
    # Codex's own bookkeeping without deleting that block first.
    if force:
        _run(["codex", "plugin", "marketplace", "remove", "harness"], dry_run)

    # Step 5: TOML merge via library API
    if dry_run:
        snippet_preview = emit_codex_config(str(cached_plugin_root), config_path).splitlines()[:5]
        steps.append("would merge [plugins.\"harness@harness\"] + [mcp_servers.harness] "
                     f"into {config_path or '~/.codex/config.toml'}")
        steps.append("  preview: " + " | ".join(snippet_preview))
        return InstallResult("codex", True,
                             "dry-run — would install Codex (steps above)", steps)
    result = emit_and_install_codex_config(
        str(cached_plugin_root),
        config_path=config_path,
        force=force,
    )
    if not result["ok"]:
        return InstallResult("codex", False, result["message"], steps,
                             backup_path=result["backup_path"])
    steps.append(result["message"])
    trust_result = install_codex_hook_trust_state(
        config_path or DEFAULT_CODEX_CONFIG_PATH,
        _codex_hooks_config(cached_plugin_root),
    )
    if not trust_result["ok"]:
        return InstallResult("codex", False, trust_result["message"],
                             steps, backup_path=result["backup_path"])
    steps.append(trust_result["message"])
    rc, out, err = _run(["codex", "features", "enable", "plugin_hooks"], dry_run)
    if rc != 0:
        return InstallResult("codex", False,
                             f"codex features enable plugin_hooks failed: {err.strip() or out.strip()}",
                             steps, backup_path=result["backup_path"])
    steps.append("codex plugin_hooks feature enabled")
    # Step 5: marketplace add. The config block already points at the installed
    # copy under ~/.codex/harness; this call lets Codex refresh any marketplace
    # metadata it maintains around that source.
    rc, out, err = _run(["codex", "plugin", "marketplace", "add", str(CODEX_INSTALL_ROOT)], dry_run)
    if rc != 0 and "already" not in (out + err).lower():
        return InstallResult("codex", False,
                             f"codex plugin marketplace add failed: {err.strip() or out.strip()}",
                             steps, backup_path=result["backup_path"])
    steps.append("codex plugin marketplace registered")
    return InstallResult("codex", True, "Codex install complete", steps,
                         backup_path=result["backup_path"])


def install_claude(*, dry_run: bool, force: bool) -> InstallResult:
    steps: list[str] = []
    if not shutil.which("claude"):
        return InstallResult("claude", False, "claude CLI not found in PATH", steps)
    # Step 1: version
    rc, out, err = _run(["claude", "--version"], dry_run)
    if dry_run:
        steps.append("would run: claude --version")
    else:
        if rc != 0:
            return InstallResult("claude", False, f"claude --version failed: {err}", steps)
        steps.append(f"claude {out.strip()}")

    # Step 2: mirror the checkout into Claude's runtime install path.
    claude_install_root = Path(os.environ.get("HARNESS_DEST", DEFAULT_CLAUDE_INSTALL_ROOT))
    installed_plugin_root = claude_install_root / "plugin"
    if dry_run:
        steps.append(f"would sync plugin payload to {claude_install_root} (.git excluded)")
    else:
        installed_plugin_root = sync_claude_payload(claude_install_root)
        steps.append(f"synced plugin payload to {claude_install_root} (.git excluded)")

    # Step 3: register marketplace + install plugin on first install, refresh on update.
    # Register the installed mirror root. Its .claude-plugin/marketplace.json
    # is copied from the repo root and points at source "./plugin".
    marketplace_source = claude_install_root
    rc, out, err = _run(["claude", "plugin", "marketplace", "list"], dry_run)
    if dry_run:
        steps.append("would run: claude plugin marketplace list")
        steps.append(f"would run if missing or source changed: claude plugin marketplace add {marketplace_source}")
        steps.append("would run if source changed: claude plugin marketplace remove harness")
        steps.append("would run if newly added: claude plugin install harness@harness")
        steps.append("would run if already registered to install mirror: claude plugin marketplace update harness")
    else:
        if rc != 0:
            return InstallResult("claude", False,
                                 f"claude plugin marketplace list failed: {err.strip() or out.strip()}",
                                 steps)
        marketplace_list = out + "\n" + err
        registered = any(part == "harness" for part in marketplace_list.replace("@", " ").split())
        registered_to_install_root = str(marketplace_source) in marketplace_list
        source_visible = "Source:" in marketplace_list
        if registered and source_visible and not registered_to_install_root:
            rc, out, err = _run(["claude", "plugin", "marketplace", "remove", "harness"], dry_run)
            if rc != 0:
                return InstallResult("claude", False,
                                     f"claude plugin marketplace remove failed: {err.strip() or out.strip()}",
                                     steps)
            steps.append("removed existing claude harness marketplace with stale source")
            registered = False

        if registered:
            rc, out, err = _run(["claude", "plugin", "marketplace", "update", "harness"], dry_run)
            if rc != 0:
                return InstallResult("claude", False,
                                     f"claude plugin marketplace update failed: {err.strip() or out.strip()}",
                                     steps)
            steps.append(f"claude marketplace harness refreshed from {marketplace_source}")
        else:
            rc, out, err = _run(["claude", "plugin", "marketplace", "add", str(marketplace_source)], dry_run)
            if rc != 0:
                return InstallResult("claude", False,
                                     f"claude plugin marketplace add failed: {err.strip() or out.strip()}",
                                     steps)
            steps.append(f"claude plugin marketplace added from {marketplace_source}")
            plugin_arg = "harness@harness"
            rc, out, err = _run(["claude", "plugin", "install", plugin_arg], dry_run)
            combined = (out + err).lower()
            if rc != 0 and "already" not in combined:
                return InstallResult("claude", False,
                                     f"claude plugin install failed: {err.strip() or out.strip()}",
                                     steps)
            steps.append(f"claude plugin install {plugin_arg} ok")

    # Step 4: MCP server registration
    installed_mcp_server = installed_plugin_root / "mcp" / "harness_server.py"
    if dry_run:
        steps.append(f"would run: claude mcp add harness python3 -- {installed_mcp_server} "
                     f"(env: HARNESS_PLUGIN_ROOT={installed_plugin_root}, "
                     f"CLAUDE_PLUGIN_ROOT={installed_plugin_root})")
        return InstallResult("claude", True,
                             "dry-run — would install Claude (steps above)", steps)
    env_args = [
        "-e", f"HARNESS_PLUGIN_ROOT={installed_plugin_root}",
        "-e", f"CLAUDE_PLUGIN_ROOT={installed_plugin_root}",
    ]
    if force:
        _run(["claude", "mcp", "remove", "harness"], dry_run)
    cmd = ["claude", "mcp", "add", "harness"] + env_args + ["--", "python3", str(installed_mcp_server)]
    rc, out, err = _run(cmd, dry_run)
    combined = (out + err).lower()
    if rc != 0 and "already" not in combined and "exists" not in combined:
        return InstallResult("claude", False,
                             f"claude mcp add failed: {err.strip() or out.strip()}",
                             steps)
    steps.append(f"claude mcp add harness ok (server: {installed_mcp_server})")
    steps.append("verify: `claude --debug api` and look for harness MCP server connection")
    return InstallResult("claude", True, "Claude install complete", steps)


def main() -> int:
    p = argparse.ArgumentParser(
        description="Unified install entry point for harness (Codex + Claude, parallel).",
    )
    p.add_argument("--codex-only", action="store_true", help="Install only on Codex CLI")
    p.add_argument("--claude-only", action="store_true", help="Install only on Claude Code")
    p.add_argument("--dry-run", action="store_true", help="Print plan, do not mutate")
    p.add_argument("--force", action="store_true",
                   help="Overwrite existing harness MCP config without prompting")
    p.add_argument("--config-path", default=None,
                   help="Override Codex ~/.codex/config.toml path (testing)")
    args = p.parse_args()

    if args.codex_only and args.claude_only:
        print("ERROR: --codex-only and --claude-only are mutually exclusive", file=sys.stderr)
        return 2

    explicit_runtime = args.codex_only or args.claude_only
    tasks: list[tuple[str, callable]] = []
    skipped: list[str] = []
    if not args.claude_only and (explicit_runtime or shutil.which("codex")):
        tasks.append(("codex", lambda: install_codex(
            dry_run=args.dry_run, force=args.force, config_path=args.config_path)))
    elif not args.claude_only:
        skipped.append("codex (codex CLI not found in PATH)")
    if not args.codex_only and (explicit_runtime or shutil.which("claude")):
        tasks.append(("claude", lambda: install_claude(
            dry_run=args.dry_run, force=args.force)))
    elif not args.codex_only:
        skipped.append("claude (claude CLI not found in PATH)")

    if not tasks:
        detail = f"; skipped: {', '.join(skipped)}" if skipped else ""
        print(f"ERROR: no supported runtime CLI found{detail}", file=sys.stderr)
        return 2

    print(f"harness install — repo root: {REPO_ROOT}")
    print(f"runtimes: {', '.join(name for name, _ in tasks)}"
          + (" (dry-run)" if args.dry_run else ""))
    for item in skipped:
        print(f"skipping: {item}")
    print()

    results: list[InstallResult] = []
    with ThreadPoolExecutor(max_workers=len(tasks)) as ex:
        futures = {ex.submit(fn): name for name, fn in tasks}
        for f in as_completed(futures):
            name = futures[f]
            try:
                r = f.result()
            except Exception as e:
                r = InstallResult(name, False, f"crashed: {e!r}")
            results.append(r)

    # Render summary
    print("─" * 70)
    any_failed = False
    for r in sorted(results, key=lambda x: x.runtime):
        marker = "✓" if r.ok else "✗"
        print(f"{marker} [{r.runtime}] {r.summary}")
        for step in r.steps:
            print(f"    · {step}")
        if r.backup_path:
            print(f"    backup: {r.backup_path}")
        if not r.ok:
            any_failed = True
        print()

    if any_failed:
        print("Install completed with errors. Re-run failed runtimes after addressing the issue above.",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("Dry-run complete. Re-run without --dry-run to apply.")
    else:
        print("Install complete on all detected runtimes.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
