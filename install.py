#!/usr/bin/env python3
"""Unified install entry point for harness — Codex CLI and Claude Code, in parallel.

Auto-detects which CLIs are present and installs harness on every runtime it finds.

Usage:
    python3 install.py                  # install on every detected runtime in parallel
    python3 install.py --codex-only     # only Codex (skip Claude even if present)
    python3 install.py --claude-only    # only Claude
    python3 install.py --dry-run        # print what each runtime would do; no mutation
    python3 install.py --if-stale       # refresh only stale canonical runtime payloads
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
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
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


PAYLOAD_SYNCHRONIZED = "SYNCHRONIZED"
PAYLOAD_STALE = "STALE"
PAYLOAD_ERROR = "ERROR"
PAYLOAD_MAX_ENTRIES = 100_000
PAYLOAD_MAX_BYTES = 256 * 1024 * 1024
_VOLATILE_DIR_NAMES = {"__pycache__", ".pytest_cache"}


def _trusted_inventory_directory(fd: int) -> bool:
    try:
        info = os.fstat(fd)
    except OSError:
        return False
    return bool(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid == os.getuid()
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def _open_inventory_root(root: Path) -> int:
    """Open every root component without following symlinks."""
    absolute = Path(os.path.abspath(root))
    current_fd = os.open(
        os.path.sep,
        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
    )
    try:
        for index, part in enumerate(absolute.parts[1:]):
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=current_fd,
            )
            os.close(current_fd)
            current_fd = next_fd
            info = os.fstat(current_fd)
            mode = stat.S_IMODE(info.st_mode)
            final = index == len(absolute.parts[1:]) - 1
            trusted_owner = info.st_uid in {os.getuid(), 0}
            sticky_shared = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
            if (
                not stat.S_ISDIR(info.st_mode)
                or not trusted_owner
                or (mode & 0o022 and not sticky_shared)
                or (final and not _trusted_inventory_directory(current_fd))
            ):
                raise PermissionError(f"unsafe payload path component: {absolute}")
        return current_fd
    except BaseException:
        os.close(current_fd)
        raise


def _tree_inventory(root: Path) -> tuple[str, dict[str, tuple[str, int, str]], str]:
    """Return a bounded no-follow inventory for one installer-owned tree."""
    root = Path(root)
    if not os.path.lexists(root):
        absolute = Path(os.path.abspath(root))
        current_fd = -1
        try:
            current_fd = os.open(
                os.path.sep,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
            )
            parts = absolute.parts[1:]
            for index, part in enumerate(parts):
                try:
                    next_fd = os.open(
                        part,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=current_fd,
                    )
                except FileNotFoundError:
                    parent_info = os.fstat(current_fd)
                    parent_mode = stat.S_IMODE(parent_info.st_mode)
                    sticky_shared_leaf = bool(
                        index == len(parts) - 1
                        and parent_info.st_uid == 0
                        and parent_mode & stat.S_ISVTX
                    )
                    if not (
                        _trusted_inventory_directory(current_fd)
                        or sticky_shared_leaf
                    ):
                        return PAYLOAD_ERROR, {}, (
                            f"unsafe nearest existing ancestor for missing target: {absolute}"
                        )
                    return PAYLOAD_STALE, {}, "target is missing"
                info = os.fstat(next_fd)
                mode = stat.S_IMODE(info.st_mode)
                trusted_owner = info.st_uid in {os.getuid(), 0}
                sticky_shared = info.st_uid == 0 and bool(mode & stat.S_ISVTX)
                if (
                    not stat.S_ISDIR(info.st_mode)
                    or not trusted_owner
                    or (mode & 0o022 and not sticky_shared)
                ):
                    os.close(next_fd)
                    return PAYLOAD_ERROR, {}, f"unsafe payload path component: {absolute}"
                os.close(current_fd)
                current_fd = next_fd
        except OSError as exc:
            return PAYLOAD_ERROR, {}, f"missing target ancestor inspection failed: {exc}"
        finally:
            if current_fd >= 0:
                os.close(current_fd)
        return PAYLOAD_ERROR, {}, "target disappeared during inspection"
    root_fd = -1
    inventory: dict[str, tuple[str, int, str]] = {}
    counters = {"entries": 0, "bytes": 0}

    def fail(reason: str) -> None:
        raise RuntimeError(reason)

    def walk(directory_fd: int, prefix: str, ignored: bool = False) -> None:
        directory_before = os.fstat(directory_fd)
        try:
            iterator = os.scandir(directory_fd)
            entries = []
            try:
                for entry in iterator:
                    counters["entries"] += 1
                    if counters["entries"] > PAYLOAD_MAX_ENTRIES:
                        fail("payload entry limit exceeded")
                    entries.append(entry)
            finally:
                iterator.close()
            entries.sort(key=lambda item: item.name)
        except OSError as exc:
            fail(f"directory scan failed at {prefix or '.'}: {exc}")
        for entry in entries:
            name = entry.name
            rel = f"{prefix}/{name}" if prefix else name
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                fail(f"entry stat failed at {rel}: {exc}")
            if stat.S_ISDIR(info.st_mode):
                child_fd = -1
                try:
                    child_fd = os.open(
                        name,
                        os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                        dir_fd=directory_fd,
                    )
                    opened_child = os.fstat(child_fd)
                    if (info.st_dev, info.st_ino) != (
                        opened_child.st_dev, opened_child.st_ino,
                    ):
                        fail(f"directory changed before open at {rel}")
                    if not _trusted_inventory_directory(child_fd):
                        fail(f"unsafe directory at {rel}")
                    child_ignored = ignored or name in _VOLATILE_DIR_NAMES
                    if not child_ignored:
                        inventory[rel + "/"] = ("dir", 0, "")
                    # Ignored runtime directories are still traversed so an
                    # unsafe node cannot hide behind a volatility exemption.
                    walk(child_fd, rel, child_ignored)
                except OSError as exc:
                    fail(f"directory open failed at {rel}: {exc}")
                finally:
                    if child_fd >= 0:
                        os.close(child_fd)
                continue
            if not stat.S_ISREG(info.st_mode):
                fail(f"unsupported file type at {rel}")
            file_fd = -1
            try:
                file_fd = os.open(
                    name,
                    os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
                    dir_fd=directory_fd,
                )
                before = os.fstat(file_fd)
                if (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                    fail(f"file changed before open at {rel}")
                mode = stat.S_IMODE(before.st_mode)
                if (
                    not stat.S_ISREG(before.st_mode)
                    or before.st_uid != os.getuid()
                    or before.st_nlink != 1
                    or mode not in {0o600, 0o644, 0o755}
                ):
                    fail(f"unsafe regular file at {rel}")
                file_ignored = ignored or name.endswith(".pyc")
                digest = hashlib.sha256()
                size = 0
                while True:
                    chunk = os.read(file_fd, 1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    counters["bytes"] += len(chunk)
                    if counters["bytes"] > PAYLOAD_MAX_BYTES:
                        fail("payload byte limit exceeded")
                    digest.update(chunk)
                after = os.fstat(file_fd)
                identity_before = (
                    before.st_dev, before.st_ino, before.st_size, before.st_mode,
                    before.st_mtime_ns, before.st_ctime_ns,
                )
                identity_after = (
                    after.st_dev, after.st_ino, after.st_size, after.st_mode,
                    after.st_mtime_ns, after.st_ctime_ns,
                )
                if identity_before != identity_after or size != after.st_size:
                    fail(f"file changed during comparison at {rel}")
                if not file_ignored:
                    inventory[rel] = ("file", mode, digest.hexdigest())
            except OSError as exc:
                fail(f"file read failed at {rel}: {exc}")
            finally:
                if file_fd >= 0:
                    os.close(file_fd)
        directory_after = os.fstat(directory_fd)
        directory_identity_before = (
            directory_before.st_dev, directory_before.st_ino,
            directory_before.st_mtime_ns, directory_before.st_ctime_ns,
        )
        directory_identity_after = (
            directory_after.st_dev, directory_after.st_ino,
            directory_after.st_mtime_ns, directory_after.st_ctime_ns,
        )
        if directory_identity_before != directory_identity_after:
            fail(f"directory changed during comparison at {prefix or '.'}")

    try:
        root_fd = _open_inventory_root(root)
        opened = os.fstat(root_fd)
        if not _trusted_inventory_directory(root_fd):
            return PAYLOAD_ERROR, {}, "unsafe payload root"
        walk(root_fd, "")
        current = os.lstat(root)
        if (
            not stat.S_ISDIR(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            return PAYLOAD_ERROR, {}, "payload root changed during comparison"
    except (OSError, RuntimeError) as exc:
        return PAYLOAD_ERROR, {}, str(exc)
    finally:
        if root_fd >= 0:
            os.close(root_fd)
    return PAYLOAD_SYNCHRONIZED, inventory, ""


def _compare_payload_trees(expected: Path, actual: Path) -> tuple[str, str]:
    expected_state, expected_inventory, expected_reason = _tree_inventory(expected)
    if expected_state != PAYLOAD_SYNCHRONIZED:
        return PAYLOAD_ERROR, f"expected payload unavailable: {expected_reason}"
    actual_state, actual_inventory, actual_reason = _tree_inventory(actual)
    if actual_state == PAYLOAD_ERROR:
        return PAYLOAD_ERROR, actual_reason
    if actual_state == PAYLOAD_STALE:
        return PAYLOAD_STALE, actual_reason
    confirmation_state, confirmation_inventory, confirmation_reason = _tree_inventory(actual)
    if confirmation_state != PAYLOAD_SYNCHRONIZED:
        return PAYLOAD_ERROR, (
            confirmation_reason or "installed payload changed during confirmation"
        )
    if confirmation_inventory != actual_inventory:
        return PAYLOAD_ERROR, "installed payload changed during comparison"
    if actual_inventory != expected_inventory:
        return PAYLOAD_STALE, "installed payload differs from canonical projection"
    return PAYLOAD_SYNCHRONIZED, ""


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
        f'env = {{ HARNESS_PLUGIN_ROOT = "{plugin_root}", CLAUDE_PLUGIN_ROOT = "{plugin_root}", HARNESS_RUNTIME = "codex" }}\n'
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
        ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".pytest_cache", ".omc",
        ),
    )


def _activate_staged_tree(staged: Path, target: Path) -> None:
    """Activate a fully-built tree while retaining the prior tree on failure."""
    backup = target.with_name(f".{target.name}.previous")
    if backup.exists():
        shutil.rmtree(backup)
    had_target = target.exists()
    if had_target:
        os.replace(target, backup)
    try:
        os.replace(staged, target)
    except BaseException:
        if had_target and backup.exists():
            os.replace(backup, target)
        raise
    if backup.exists():
        shutil.rmtree(backup)


def _build_codex_payload(target: Path, final_root: Path) -> None:
    _copytree_clean(PLUGIN_CODEX_ROOT, target)
    _copytree_clean(PLUGIN_ROOT / "scripts", target / "scripts")
    _copytree_clean(PLUGIN_ROOT / "mcp", target / "mcp")
    shared_skill_files = {
        "internal-skills/develop": (
            "fix-first-pattern.md", "runtime-smoke.md",
            "quality-audit-pipeline.md", "verification-gate.md",
            "test-failure-triage.md", "hypothesis-driven-debugging.md",
        ),
        "internal-skills/plan": (
            "decision-principles.md", "intake.md", "review-phases.md", "write-artifacts.md",
        ),
        "internal-skills/plan-devex-review": ("dx-hall-of-fame.md",),
        "internal-skills/plan-eng-review": ("rubrics-threat-rollback.md",),
        "internal-skills/run": ("self-improvement.md",),
    }
    for relative_dir, names in shared_skill_files.items():
        source_dir = PLUGIN_ROOT / "skills" / relative_dir.removeprefix("internal-skills/")
        destination_dir = target / relative_dir
        for name in names:
            source_path = source_dir / name
            destination_path = destination_dir / name
            text = source_path.read_text(encoding="utf-8")
            text = text.replace(
                "${CLAUDE_PLUGIN_ROOT}/skills/",
                "${HARNESS_PLUGIN_ROOT}/internal-skills/",
            ).replace(
                "${CLAUDE_PLUGIN_ROOT}", "${HARNESS_PLUGIN_ROOT}",
            ).replace(
                "plugin-codex/agents/", "${HARNESS_PLUGIN_ROOT}/agents/",
            )
            destination_path.write_text(text, encoding="utf-8")
    codex_setup = target / "skills" / "setup"
    claude_setup = PLUGIN_ROOT / "skills" / "setup"
    for name in ("repo-census.md", "project-interview.md", "bootstrap.md", "verify-report.md"):
        shutil.copy2(claude_setup / name, codex_setup / name)
    _copytree_clean(claude_setup / "templates", codex_setup / "templates")
    (target / "hooks.json").write_text(
        json.dumps(_codex_hooks_config(final_root), indent=2) + "\n"
    )
    (target / ".mcp.json").write_text(
        json.dumps(_codex_mcp_config(final_root), indent=2) + "\n"
    )
    for rel in (
        "skills/run/SKILL.md",
        "skills/run/agents/openai.yaml",
        "internal-skills/run/SKILL.md",
        "skills/setup/bootstrap.md",
        "skills/setup/verify-report.md",
        "skills/setup/templates/CONTRACTS.md",
        "scripts/setup_finalize.py",
        "mcp/harness_server.py",
    ):
        if not (target / rel).is_file():
            raise RuntimeError(f"incomplete Codex payload: {rel}")


def _codex_marketplace_payload() -> dict:
    return {
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


def _write_codex_marketplace(install_root: Path) -> None:
    marketplace_dir = install_root / ".agents" / "plugins"
    marketplace_dir.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(
        dir=marketplace_dir.parent, prefix=".plugins-staging-",
    ))
    try:
        (staged / "marketplace.json").write_text(
            json.dumps(_codex_marketplace_payload(), indent=2) + "\n"
        )
        _activate_staged_tree(staged, marketplace_dir)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged)
        raise


def _build_codex_cache_payload(source_root: Path, target: Path, final_root: Path) -> None:
    _copytree_clean(source_root, target)
    (target / "hooks.json").write_text(
        json.dumps(_codex_hooks_config(final_root), indent=2) + "\n"
    )
    (target / ".mcp.json").write_text(
        json.dumps(_codex_mcp_config(final_root), indent=2) + "\n"
    )


def _build_claude_payload(target: Path) -> None:
    shutil.copytree(REPO_ROOT / ".claude-plugin", target / ".claude-plugin")
    shutil.copytree(
        PLUGIN_ROOT,
        target / "plugin",
        ignore=shutil.ignore_patterns(
            ".git", "__pycache__", "*.pyc", ".pytest_cache", ".omc",
        ),
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
                    "matcher": "Write|Edit|MultiEdit|apply_patch|collaboration\\.spawn_agent",
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
                            "timeout": 8,
                            "statusMessage": "Loading harness memory",
                        }
                    ]
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "Bash|.*create_goal",
                    "hooks": [
                        {
                            "type": "command",
                            "command": f"{shlex.quote(_python_cmd())} {shlex.quote(str(hooks['PostToolUse']))}",
                            "timeout": 3,
                            "statusMessage": "Checking harness routing and QA completion",
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
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(dir=target.parent, prefix=".harness-claude-staging-"))
    try:
        _build_claude_payload(staged)
        _activate_staged_tree(staged, target)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged)
        raise
    return target / "plugin"


def sync_codex_payload(install_root: Path | None = None) -> Path:
    """Copy the Codex plugin source under ~/.codex and return its plugin root."""
    if install_root is None:
        install_root = CODEX_INSTALL_ROOT
    install_root.mkdir(parents=True, exist_ok=True)
    legacy_paths = (
        install_root / "plugin-codex",
        install_root / "plugin",
        install_root / ".codex-plugin",
        install_root / "marketplace.json",
    )
    codex_plugin_root = install_root / "plugins" / "harness"
    codex_plugin_root.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(dir=codex_plugin_root.parent, prefix=".harness-staging-"))
    try:
        _build_codex_payload(staged, codex_plugin_root)
        _activate_staged_tree(staged, codex_plugin_root)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged)
        raise
    for legacy_path in legacy_paths:
        if os.path.lexists(legacy_path):
            legacy_info = os.lstat(legacy_path)
            if stat.S_ISDIR(legacy_info.st_mode):
                shutil.rmtree(legacy_path)
            else:
                legacy_path.unlink()
    _write_codex_marketplace(install_root)
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
    if (
        len(version) > 128
        or version in {".", ".."}
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+-]*", version)
    ):
        raise ValueError(f"unsafe Codex plugin version: {version!r}")
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
    target.parent.mkdir(parents=True, exist_ok=True)
    staged = Path(tempfile.mkdtemp(dir=target.parent, prefix=".cache-staging-"))
    try:
        _build_codex_cache_payload(source_root, staged, target)
        _activate_staged_tree(staged, target)
    except BaseException:
        if staged.exists():
            shutil.rmtree(staged)
        raise
    # Keep prior version directories intact. Already-running Codex sessions
    # retain the absolute hook commands from the cache version they loaded;
    # deleting that directory mid-session breaks SessionStart and
    # UserPromptSubmit until the process restarts. Codex can select the new
    # cachebuster on the next thread while existing sessions finish safely.
    return target


def _codex_payload_state(config_path: str | Path | None = None) -> tuple[str, str]:
    mirror = CODEX_INSTALL_ROOT / "plugins" / CODEX_PLUGIN_NAME
    legacy_paths = (
        CODEX_INSTALL_ROOT / "plugin-codex",
        CODEX_INSTALL_ROOT / "plugin",
        CODEX_INSTALL_ROOT / ".codex-plugin",
        CODEX_INSTALL_ROOT / "marketplace.json",
    )
    stale_reasons: list[str] = []
    for legacy in legacy_paths:
        if os.path.lexists(legacy):
            try:
                info = os.lstat(legacy)
                if stat.S_ISLNK(info.st_mode):
                    return PAYLOAD_ERROR, f"unsafe legacy path: {legacy}"
                if stat.S_ISDIR(info.st_mode):
                    legacy_state, _, legacy_reason = _tree_inventory(legacy)
                    if legacy_state == PAYLOAD_ERROR:
                        return PAYLOAD_ERROR, f"unsafe legacy path {legacy}: {legacy_reason}"
                elif stat.S_ISREG(info.st_mode):
                    if (
                        info.st_uid != os.getuid()
                        or info.st_nlink != 1
                        or stat.S_IMODE(info.st_mode) not in {0o600, 0o644, 0o755}
                    ):
                        return PAYLOAD_ERROR, f"unsafe legacy file: {legacy}"
                else:
                    return PAYLOAD_ERROR, f"unsafe legacy node type: {legacy}"
            except OSError as exc:
                return PAYLOAD_ERROR, f"legacy path inspection failed: {exc}"
            stale_reasons.append(f"legacy payload path remains: {legacy}")
    codex_home = _codex_home_for_config(config_path)
    version = _codex_plugin_version(PLUGIN_CODEX_ROOT)
    cache = codex_home / "plugins" / "cache" / CODEX_PLUGIN_MARKETPLACE / CODEX_PLUGIN_NAME / version
    marketplace = CODEX_INSTALL_ROOT / ".agents" / "plugins"
    try:
        with tempfile.TemporaryDirectory(prefix="harness-codex-compare-") as tmp:
            expected_root = Path(tmp)
            expected_mirror = expected_root / "mirror"
            _build_codex_payload(expected_mirror, mirror)
            state, reason = _compare_payload_trees(expected_mirror, mirror)
            if state == PAYLOAD_ERROR:
                return state, f"Codex mirror: {reason}"
            if state == PAYLOAD_STALE:
                stale_reasons.append(f"Codex mirror: {reason}")

            expected_market_root = expected_root / "market-root"
            _write_codex_marketplace(expected_market_root)
            expected_marketplace = expected_market_root / ".agents" / "plugins"
            state, reason = _compare_payload_trees(expected_marketplace, marketplace)
            if state == PAYLOAD_ERROR:
                return state, f"Codex marketplace payload: {reason}"
            if state == PAYLOAD_STALE:
                stale_reasons.append(f"Codex marketplace payload: {reason}")

            expected_cache = expected_root / "cache"
            _build_codex_cache_payload(expected_mirror, expected_cache, cache)
            state, reason = _compare_payload_trees(expected_cache, cache)
            if state == PAYLOAD_ERROR:
                return state, f"Codex current cache: {reason}"
            if state == PAYLOAD_STALE:
                stale_reasons.append(f"Codex current cache: {reason}")
    except (OSError, RuntimeError, ValueError) as exc:
        return PAYLOAD_ERROR, f"Codex expected payload build failed: {exc}"
    if stale_reasons:
        return PAYLOAD_STALE, "; ".join(stale_reasons)
    return PAYLOAD_SYNCHRONIZED, ""


def _claude_payload_state(install_root: Path | None = None) -> tuple[str, str]:
    target = install_root or Path(os.environ.get("HARNESS_DEST", DEFAULT_CLAUDE_INSTALL_ROOT))
    try:
        with tempfile.TemporaryDirectory(prefix="harness-claude-compare-") as tmp:
            expected = Path(tmp) / "mirror"
            _build_claude_payload(expected)
            return _compare_payload_trees(expected, target)
    except (OSError, RuntimeError, ValueError) as exc:
        return PAYLOAD_ERROR, f"Claude expected payload build failed: {exc}"


def install_codex(*, dry_run: bool, force: bool,
                  config_path: str | None, if_stale: bool = False) -> InstallResult:
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
    if if_stale:
        payload_state, payload_reason = _codex_payload_state(config_path)
        if payload_state == PAYLOAD_ERROR:
            return InstallResult(
                "codex", False,
                f"Codex payload comparison failed: {payload_reason}", steps,
            )
        if payload_state == PAYLOAD_SYNCHRONIZED:
            steps.append("payload comparison: SYNCHRONIZED")
            return InstallResult(
                "codex", True,
                "Codex payload SYNCHRONIZED — install skipped "
                "(config/registry health not checked)",
                steps,
            )
        steps.append(f"payload comparison: STALE ({payload_reason})")
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
    if force or if_stale:
        _run(["codex", "plugin", "marketplace", "remove", "harness"], dry_run)

    # Step 5: TOML merge via library API
    config_plugin_root = source_plugin_root if not dry_run else CODEX_INSTALL_ROOT / "plugins" / CODEX_PLUGIN_NAME
    if dry_run:
        snippet_preview = emit_codex_config(str(config_plugin_root), config_path).splitlines()[:5]
        steps.append("would merge [plugins.\"harness@harness\"] + [mcp_servers.harness] "
                     f"into {config_path or '~/.codex/config.toml'}")
        steps.append("  preview: " + " | ".join(snippet_preview))
        return InstallResult("codex", True,
                             "dry-run — would install Codex (steps above)", steps)
    result = emit_and_install_codex_config(
        str(config_plugin_root),
        config_path=config_path,
        force=force or if_stale,
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


def install_claude(*, dry_run: bool, force: bool, if_stale: bool = False) -> InstallResult:
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
    if if_stale:
        payload_state, payload_reason = _claude_payload_state()
        if payload_state == PAYLOAD_ERROR:
            return InstallResult(
                "claude", False,
                f"Claude payload comparison failed: {payload_reason}", steps,
            )
        if payload_state == PAYLOAD_SYNCHRONIZED:
            steps.append("payload comparison: SYNCHRONIZED")
            return InstallResult(
                "claude", True,
                "Claude payload SYNCHRONIZED — install skipped "
                "(config/registry health not checked)",
                steps,
            )
        steps.append(f"payload comparison: STALE ({payload_reason})")

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
                     f"CLAUDE_PLUGIN_ROOT={installed_plugin_root}, "
                     "HARNESS_RUNTIME=claude)")
        return InstallResult("claude", True,
                             "dry-run — would install Claude (steps above)", steps)
    env_args = [
        "-e", f"HARNESS_PLUGIN_ROOT={installed_plugin_root}",
        "-e", f"CLAUDE_PLUGIN_ROOT={installed_plugin_root}",
        "-e", "HARNESS_RUNTIME=claude",
    ]
    if force or if_stale:
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
    p.add_argument(
        "--if-stale", action="store_true",
        help=(
            "Refresh only runtimes whose canonical installed payload differs from source; "
            "does not diagnose config/registry-only drift"
        ),
    )
    p.add_argument("--config-path", default=None,
                   help="Override Codex ~/.codex/config.toml path (testing)")
    args = p.parse_args()

    if args.codex_only and args.claude_only:
        print("ERROR: --codex-only and --claude-only are mutually exclusive", file=sys.stderr)
        return 2
    if args.force and args.if_stale:
        print("ERROR: --force and --if-stale are mutually exclusive", file=sys.stderr)
        return 2

    explicit_runtime = args.codex_only or args.claude_only
    tasks: list[tuple[str, callable]] = []
    skipped: list[str] = []
    if not args.claude_only and (explicit_runtime or shutil.which("codex")):
        tasks.append(("codex", lambda: install_codex(
            dry_run=args.dry_run, force=args.force, config_path=args.config_path,
            if_stale=args.if_stale)))
    elif not args.claude_only:
        skipped.append("codex (codex CLI not found in PATH)")
    if not args.codex_only and (explicit_runtime or shutil.which("claude")):
        tasks.append(("claude", lambda: install_claude(
            dry_run=args.dry_run, force=args.force, if_stale=args.if_stale)))
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
        if not r.ok:
            outcome = "ERROR"
        elif "install skipped" in r.summary:
            outcome = "SKIPPED"
        elif args.dry_run:
            outcome = "DRY_RUN"
        else:
            outcome = "APPLIED"
        print(f"{marker} [{r.runtime}] STATUS: {outcome} — {r.summary}")
        for step in r.steps:
            print(f"    · {step}")
        if r.backup_path:
            print(f"    backup: {r.backup_path}")
        if not r.ok:
            any_failed = True
            if args.if_stale:
                print(
                    "    repair after correcting the reported cause: "
                    f"python3 install.py --{r.runtime}-only --force"
                )
        print()

    if any_failed:
        if args.if_stale:
            print(
                "Payload comparison covers installer-owned trees only; "
                "config/registry health not checked."
            )
        print("Install completed with errors. Re-run failed runtimes after addressing the issue above.",
              file=sys.stderr)
        return 1
    if args.dry_run:
        print("Dry-run complete. Re-run without --dry-run to apply.")
    elif args.if_stale and all("install skipped" in result.summary for result in results):
        print("Payloads already current. No install performed.")
    elif args.if_stale:
        print("Conditional install complete. Refreshed stale runtime payloads only.")
    else:
        print("Install complete on all detected runtimes.")
    if args.if_stale:
        print("Payload comparison covers installer-owned trees only; config/registry health not checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
