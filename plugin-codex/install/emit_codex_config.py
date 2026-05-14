#!/usr/bin/env python3
"""AC-006: emit ~/.codex/config.toml snippet for harness Codex install.

Emits three TOML sections (additive — never overwrite user's existing
config without explicit --force):

  [mcp_servers.harness]    — registers plugin/mcp/harness_server.py with Codex.
  [hooks]                  — wires plugin/scripts/*.py to Codex hook events.
  [hooks.state.<key>]      — trust state ('untrusted' placeholder in v1.5; user
                             runs `codex` interactively once to accept-and-record
                             the real trusted_hash, then re-emits the config).

v1.5 scope (AC-006 of TASK__dual-runtime-v1.5-spike-and-sync):
  - dry-run output to stdout (no ~/.codex/config.toml write)
  - --install flag → additive merge with timestamped .bak backup
  - trusted_hash placeholder requires post-install `codex` trust acceptance;
    full hash computation is v2 (Codex source `codex-rs/hooks/src/engine/mod.rs:79
    HookListEntry.current_hash`; algorithm not documented as public API).

Usage:
  python3 plugin-codex/install/emit_codex_config.py --plugin-root /path/to/plugin
  python3 plugin-codex/install/emit_codex_config.py --plugin-root ... --install
  python3 plugin-codex/install/emit_codex_config.py --plugin-root ... --force-merge
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import shutil
import sys
from pathlib import Path

DEFAULT_CONFIG_PATH = Path.home() / ".codex" / "config.toml"
HOOK_TRUST_PLACEHOLDER = "UNTRUSTED_RUN_codex_interactively_to_register"


HOOKS_TO_EMIT = [
    {
        "event": "SessionStart",
        "matcher": "",
        "scripts": [
            ("note_freshness.py --from-git 1 --quiet", 5),
            ("inject_checkpoint.py", 5),
            ("contract_lint.py --quick --quiet", 5),
            ("hygiene_scan.py --apply-safe", 10),
            ("verification_gap_check.py", 5),
        ],
    },
    {
        "event": "Stop",
        "matcher": "",
        "scripts": [
            ("stop_gate.py", 10),
        ],
    },
    {
        "event": "PreToolUse",
        "matcher": "",
        "scripts": [
            ("prewrite_gate.py", 3),
            ("qa_delegation_gate.py", 3),
        ],
    },
    {
        "event": "PreToolUse",
        "matcher": "Bash",
        "scripts": [
            ("mcp_bash_guard.py", 3),
        ],
    },
    {
        "event": "UserPromptSubmit",
        "matcher": "",
        "scripts": [
            ("prompt_memory.py", 3),
            ("tool_routing.py", 3),
        ],
    },
]


def _hook_key(event: str, index: int) -> str:
    """Stable key for [hooks.state."<key>"] trust table.

    Mirrors Codex's internal key convention from core/tests/common/hooks.rs.
    Format: "<camelCase-event>:<index>". When the user runs `codex` and accepts
    the hook trust prompt, Codex records the real `trusted_hash` against this key.
    """
    camel = event[0].lower() + event[1:]
    return f"{camel}:{index}"


def _mcp_block(plugin_root: str) -> str:
    return (
        "# Registers the harness MCP server with Codex. Same protocol both runtimes consume.\n"
        "[mcp_servers.harness]\n"
        'command = "python3"\n'
        f'args = ["{plugin_root}/mcp/harness_server.py"]\n'
        f'env = {{ HARNESS_PLUGIN_ROOT = "{plugin_root}", CLAUDE_PLUGIN_ROOT = "{plugin_root}" }}\n'
        "startup_timeout_sec = 10\n"
        "tool_timeout_sec = 60\n"
        "enabled = true\n"
        "required = false  # set true to fail-fast in `codex exec` when harness is unreachable\n"
    )


def _hooks_block(plugin_root: str) -> str:
    lines = [
        "# Harness hook wiring. Schema is byte-identical to plugin/hooks/hooks.json.",
        "# Codex's ClaudeHooksEngine (codex-rs/hooks/src/engine/mod.rs:98) consumes the",
        "# same shape Claude does.",
        "[hooks]",
        "",
    ]
    for entry in HOOKS_TO_EMIT:
        for cmd_args, timeout in entry["scripts"]:
            section = f"[[hooks.{entry['event']}]]"
            if entry.get("matcher"):
                section = f'[[hooks.{entry["event"]}]]\nmatcher = "{entry["matcher"]}"'
            lines.append(section)
            lines.append("[[hooks." + entry["event"] + ".hooks]]")
            lines.append('type = "command"')
            lines.append(
                f'command = "python3 {plugin_root}/scripts/{cmd_args}"'
            )
            lines.append(f"timeout = {timeout}")
            lines.append("")
    return "\n".join(lines)


def _trust_state_block() -> str:
    """Emit placeholder trust state. Real trusted_hash is computed by Codex
    when the user accepts the hook trust prompt in an interactive `codex` session.
    The placeholder ensures the schema is complete; setup skill documents the
    one-time trust-acceptance step.
    """
    lines = [
        "# Hook trust state. v1.5 emits placeholder hashes — run `codex` once after",
        "# install and accept the hook trust prompt; Codex will rewrite these entries",
        "# with real `trusted_hash` values. Without trust state, registered hooks are",
        "# loaded but NOT fired (per codex-rs/hooks/src/engine/mod.rs:108 bypass_hook_trust).",
        "",
    ]
    counters: dict[str, int] = {}
    for entry in HOOKS_TO_EMIT:
        evt = entry["event"]
        for _cmd, _timeout in entry["scripts"]:
            idx = counters.get(evt, 0)
            counters[evt] = idx + 1
            key = _hook_key(evt, idx)
            lines.append(f'[hooks.state."{key}"]')
            lines.append(f'trusted_hash = "{HOOK_TRUST_PLACEHOLDER}"')
            lines.append("")
    return "\n".join(lines)


def emit(plugin_root: str) -> str:
    """Compose the full snippet ready to additive-merge into ~/.codex/config.toml."""
    pr = str(Path(plugin_root).resolve())
    header = (
        "# ───────────────────────────────────────────────────────────────────\n"
        f"# harness v2.3.0 — emitted by plugin-codex/install/emit_codex_config.py\n"
        f"# Plugin root: {pr}\n"
        f"# Generated: {_dt.datetime.now(_dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
        "# ───────────────────────────────────────────────────────────────────\n"
        "\n"
    )
    return header + _mcp_block(pr) + "\n" + _hooks_block(pr) + "\n" + _trust_state_block()


def _do_install(snippet: str, config_path: Path, force_merge: bool) -> tuple[bool, str]:
    """Additive merge into ~/.codex/config.toml. Returns (ok, message).

    Refuses to overwrite an existing `[mcp_servers.harness]` block without --force.
    Backs up the original to `<path>.bak.<timestamp>` before writing.
    """
    if config_path.exists():
        existing = config_path.read_text()
        if "[mcp_servers.harness]" in existing and not force_merge:
            return (
                False,
                (
                    "[mcp_servers.harness] already present in "
                    f"{config_path}. Re-run with --force-merge to overwrite "
                    "(timestamped backup will be written). Or merge manually."
                ),
            )
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = config_path.with_suffix(config_path.suffix + f".bak.{ts}")
        shutil.copy2(config_path, backup)
        new_content = existing.rstrip("\n") + "\n\n" + snippet
        config_path.write_text(new_content)
        return True, f"Merged into {config_path}; backup at {backup}"
    else:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(snippet)
        return True, f"Wrote fresh {config_path}"


def emit_and_install(plugin_root: str, config_path: str | Path | None = None,
                     force: bool = False) -> dict:
    """Library entry point — emit Codex config snippet and install it.

    Returns a dict with keys:
      - ok: bool — True on success
      - message: str — human-readable summary
      - snippet: str — the TOML block (always populated)
      - config_path: str — the path that was (or would be) written
      - backup_path: str | None — timestamped .bak if a pre-existing config was backed up
      - blocks_added: list[str] — names of TOML blocks merged in

    Used by repo-root install.py to compose the Codex half of a unified install
    without shelling out to this script's CLI.
    """
    cfg = Path(config_path) if config_path else DEFAULT_CONFIG_PATH
    pr = Path(plugin_root).resolve()
    if not pr.is_dir():
        return {"ok": False, "message": f"plugin root not a directory: {pr}",
                "snippet": "", "config_path": str(cfg), "backup_path": None,
                "blocks_added": []}
    snippet = emit(str(pr))
    pre_existing = cfg.exists() and "[mcp_servers.harness]" in cfg.read_text()
    ok, msg = _do_install(snippet, cfg, force)
    backup = None
    if ok and cfg.exists() and pre_existing:
        # _do_install wrote a .bak; find the most recent matching one
        candidates = sorted(cfg.parent.glob(f"{cfg.name}.bak.*"))
        if candidates:
            backup = str(candidates[-1])
    blocks = ["[mcp_servers.harness]", "[hooks.SessionStart]", "[hooks.Stop]",
              "[hooks.PreToolUse]", "[hooks.UserPromptSubmit]", "[hooks.state.*]"]
    return {"ok": ok, "message": msg, "snippet": snippet, "config_path": str(cfg),
            "backup_path": backup, "blocks_added": blocks if ok else []}


def main() -> int:
    p = argparse.ArgumentParser(description="Emit ~/.codex/config.toml block for harness Codex install.")
    p.add_argument("--plugin-root", required=True, help="Absolute path to plugin/ directory")
    p.add_argument("--install", action="store_true", help="Write to ~/.codex/config.toml (additive merge with backup)")
    p.add_argument("--force-merge", action="store_true", help="Overwrite existing [mcp_servers.harness] block on install")
    p.add_argument("--config-path", default=str(DEFAULT_CONFIG_PATH), help="Override default ~/.codex/config.toml path")
    args = p.parse_args()

    plugin_root = Path(args.plugin_root).resolve()
    if not plugin_root.is_dir():
        print(f"ERROR: plugin root not a directory: {plugin_root}", file=sys.stderr)
        return 1
    snippet = emit(str(plugin_root))

    if args.install:
        ok, msg = _do_install(snippet, Path(args.config_path), args.force_merge)
        if ok:
            print(msg)
            return 0
        print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    # Dry run: print snippet
    sys.stdout.write(snippet)
    return 0


if __name__ == "__main__":
    sys.exit(main())
