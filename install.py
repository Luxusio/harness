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
    2. codex plugin marketplace add <repo-root>           (idempotent)
    3. Merge [mcp_servers.harness] + [hooks.*] into ~/.codex/config.toml (timestamped backup)
    4. Print first-run instructions (trust prompts on first `codex`)

  Claude:
    1. Verify claude --version
    2. claude plugin marketplace add <repo-root>          (idempotent)
    3. claude plugin install harness@harness              (resolves hooks from plugin.json)
    4. claude mcp add harness ... -- python3 <plugin>/mcp/harness_server.py
    5. Print verification command
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = REPO_ROOT / "plugin"
PLUGIN_CODEX_ROOT = REPO_ROOT / "plugin-codex"
CODEX_VERSION_PIN_FILE = PLUGIN_CODEX_ROOT / ".codex-version"
HARNESS_MCP_SERVER = PLUGIN_ROOT / "mcp" / "harness_server.py"

# Import the Codex emitter as a library
sys.path.insert(0, str(PLUGIN_CODEX_ROOT / "install"))
from emit_codex_config import emit_and_install, emit  # type: ignore


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
    # Step 2: marketplace add (idempotent — codex CLI returns 0 if already present)
    rc, out, err = _run(["codex", "plugin", "marketplace", "add", str(REPO_ROOT)], dry_run)
    if dry_run:
        steps.append(f"would run: codex plugin marketplace add {REPO_ROOT}")
    else:
        if rc != 0 and "already" not in (out + err).lower():
            return InstallResult("codex", False,
                                 f"codex plugin marketplace add failed: {err.strip() or out.strip()}",
                                 steps)
        steps.append("codex plugin marketplace registered")
    # Step 3: TOML merge via library API
    if dry_run:
        snippet_preview = emit(str(PLUGIN_ROOT)).splitlines()[:5]
        steps.append("would merge [mcp_servers.harness] + [hooks.*] + [hooks.state.*] "
                     f"into {config_path or '~/.codex/config.toml'}")
        steps.append("  preview: " + " | ".join(snippet_preview))
        return InstallResult("codex", True,
                             "dry-run — would install Codex (steps above)", steps)
    result = emit_and_install(str(PLUGIN_ROOT), config_path=config_path, force=force)
    if not result["ok"]:
        return InstallResult("codex", False, result["message"], steps,
                             backup_path=result["backup_path"])
    steps.append(result["message"])
    steps.append("first run: `codex` — accept hook trust prompts (one-time)")
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
    # Step 2: marketplace add
    rc, out, err = _run(["claude", "plugin", "marketplace", "add", str(REPO_ROOT)], dry_run)
    if dry_run:
        steps.append(f"would run: claude plugin marketplace add {REPO_ROOT}")
    else:
        combined = (out + err).lower()
        if rc != 0 and "already" not in combined and "exists" not in combined:
            return InstallResult("claude", False,
                                 f"claude plugin marketplace add failed: {err.strip() or out.strip()}",
                                 steps)
        steps.append("claude plugin marketplace registered")
    # Step 3: plugin install (resolves hooks from plugin.json automatically)
    plugin_arg = "harness@harness"
    rc, out, err = _run(["claude", "plugin", "install", plugin_arg], dry_run)
    if dry_run:
        steps.append(f"would run: claude plugin install {plugin_arg}")
    else:
        combined = (out + err).lower()
        if rc != 0 and "already" not in combined:
            return InstallResult("claude", False,
                                 f"claude plugin install failed: {err.strip() or out.strip()}",
                                 steps)
        steps.append(f"claude plugin install {plugin_arg} ok")
    # Step 4: MCP server registration
    if dry_run:
        steps.append(f"would run: claude mcp add harness python3 -- {HARNESS_MCP_SERVER} "
                     f"(env: HARNESS_PLUGIN_ROOT={PLUGIN_ROOT}, CLAUDE_PLUGIN_ROOT={PLUGIN_ROOT})")
        return InstallResult("claude", True,
                             "dry-run — would install Claude (steps above)", steps)
    env_args = [
        "-e", f"HARNESS_PLUGIN_ROOT={PLUGIN_ROOT}",
        "-e", f"CLAUDE_PLUGIN_ROOT={PLUGIN_ROOT}",
    ]
    cmd = ["claude", "mcp", "add", "harness"] + env_args + ["--", "python3", str(HARNESS_MCP_SERVER)]
    rc, out, err = _run(cmd, dry_run)
    combined = (out + err).lower()
    if rc != 0 and "already" not in combined and "exists" not in combined:
        return InstallResult("claude", False,
                             f"claude mcp add failed: {err.strip() or out.strip()}",
                             steps)
    steps.append(f"claude mcp add harness ok (server: {HARNESS_MCP_SERVER})")
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

    tasks: list[tuple[str, callable]] = []
    if not args.claude_only:
        tasks.append(("codex", lambda: install_codex(
            dry_run=args.dry_run, force=args.force, config_path=args.config_path)))
    if not args.codex_only:
        tasks.append(("claude", lambda: install_claude(
            dry_run=args.dry_run, force=args.force)))

    if not tasks:
        print("ERROR: nothing to do (flags blocked all runtimes)", file=sys.stderr)
        return 2

    print(f"harness install — repo root: {REPO_ROOT}")
    print(f"runtimes: {', '.join(name for name, _ in tasks)}"
          + (" (dry-run)" if args.dry_run else ""))
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
