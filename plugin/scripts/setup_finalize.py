#!/usr/bin/env python3
"""Prepare, verify, and finalize the mechanical harness setup contract."""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path


HARNESS_VERSION = "2.3.0"
MANIFEST_SCHEMA = 5
ROUTING_MARKER = "<!-- harness:routing-injected -->"

OPERATIONAL_IGNORES = (
    "doc/harness/tasks/",
    "doc/harness/goals/",
    "doc/harness/goal-queue.json",
    "doc/harness/goal-queue-events.jsonl",
    "doc/harness/legacy/goal-queue-pre-native-state.*.json",
    "doc/harness/task-packs/",
    "doc/harness/reviews/",
    "doc/harness/debug/goal-hook-payloads/",
    "doc/harness/learnings.jsonl",
    "doc/harness/checkpoints/",
    "doc/harness/visual-baselines/",
    "doc/harness/local.yaml",
    "doc/harness/.markers/",
    "doc/harness/.interview-answers.json",
    "doc/harness/retros/",
    "doc/harness/runtime/",
    "doc/harness/maintenance/",
    "doc/harness/archive/",
    "doc/harness/.routing-state.json",
    "doc/harness/timeline.jsonl",
    "doc/harness/health-history.jsonl",
    "doc/harness/benchmark/",
    "doc/harness/audits/",
    "doc/harness/quality-trend.jsonl",
    "doc/harness/hygiene.yaml",
    "doc/harness/.hygiene-last-run",
    "doc/harness/.hygiene-observe.log",
    "doc/harness/.hygiene-pending.json",
    "doc/harness/.hygiene.lock",
    "doc/harness/.hygiene-session-count",
    "doc/harness/.maintain-last-run",
    "doc/harness/.maintain-observe.log",
    "doc/harness/.maintain-pending.json",
)

REQUIRED_SETUP_RESOURCES = (
    "skills/setup/SKILL.md",
    "skills/setup/repo-census.md",
    "skills/setup/project-interview.md",
    "skills/setup/bootstrap.md",
    "skills/setup/verify-report.md",
    "skills/setup/templates/CONTRACTS.md",
    "skills/setup/templates/CONTRACTS.local.md",
    "skills/setup/templates/hygiene.yaml",
    "scripts/contract_lint.py",
    "scripts/goal_queue_migrate.py",
    "scripts/setup_finalize.py",
)

_TOP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):(.*)$")
_LEGACY_RENAMES = {
    "project": "name",
    "project_type": "type",
    "created": "initialized_at",
}
_FLAT_QA = ("browser_qa_supported", "desktop_qa_supported", "ux_review_supported")


def safe_path(repo: Path, relative: str) -> Path:
    path = repo / relative
    try:
        parts = path.relative_to(repo).parts
    except ValueError as exc:
        raise ValueError(f"managed path escapes repository: {relative}") from exc
    current = repo
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise ValueError(f"managed path contains symlink: {current.relative_to(repo)}")
    return path


def atomic_write(path: Path, text: str, *, default_mode: int = 0o644) -> None:
    mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else default_mode
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def render_gitignore(original: str) -> str:
    managed_headers = {
        "# harness - operational artifacts (ephemeral, not durable knowledge)",
        "# harness — operational artifacts (ephemeral, not durable knowledge)",
        "# auto-hygiene runtime state (per-user; template at plugin/skills/setup/templates/hygiene.yaml)",
    }
    managed = set(OPERATIONAL_IGNORES)
    lines = [
        line for line in original.splitlines()
        if line not in managed and line not in managed_headers
    ]
    while lines and not lines[-1].strip():
        lines.pop()
    if lines:
        lines.append("")
    lines.append("# harness - operational artifacts (ephemeral, not durable knowledge)")
    lines.extend(OPERATIONAL_IGNORES)
    return "\n".join(lines).rstrip() + "\n"


def manifest_maps(text: str) -> tuple[dict[str, str], dict[str, str], list[str]]:
    top: dict[str, str] = {}
    qa: dict[str, str] = {}
    errors: list[str] = []
    in_qa = False
    qa_sections = 0
    for line_no, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw:
            errors.append(f"manifest tabs are unsupported at line {line_no}")
        if raw.startswith("<<:"):
            errors.append(f"manifest YAML aliases/anchors are unsupported at line {line_no}")
        if raw == "qa:":
            qa_sections += 1
            in_qa = True
            continue
        match = _TOP_RE.match(raw)
        if match:
            in_qa = False
            key, value = match.groups()
            cleaned = value.strip()
            if cleaned.startswith(("&", "*")) or key == "<<":
                errors.append(f"manifest YAML aliases/anchors are unsupported at line {line_no}")
            if key in top:
                errors.append(f"manifest duplicate top-level key: {key}")
            top[key] = cleaned.strip('"').strip("'")
            continue
        if (
            in_qa
            and raw.startswith("  ")
            and not raw.startswith("   ")
            and ":" in raw
            and not raw.lstrip().startswith("#")
        ):
            key, value = raw.strip().split(":", 1)
            cleaned = value.strip()
            if cleaned.startswith(("&", "*")) or key == "<<":
                errors.append(f"manifest YAML aliases/anchors are unsupported at line {line_no}")
            if key in qa:
                errors.append(f"manifest duplicate qa key: {key}")
            qa[key] = cleaned.strip('"').strip("'")
    if qa_sections > 1:
        errors.append("manifest duplicate qa section")
    return top, qa, errors


def migrate_manifest_text(original: str) -> tuple[str, list[str]]:
    top, existing_qa, errors = manifest_maps(original)
    raw_version = top.get("version")
    if raw_version:
        try:
            version = int(raw_version)
        except ValueError:
            errors.append(f"manifest version must be an integer, got {raw_version!r}")
            return original, errors
        if version > MANIFEST_SCHEMA:
            errors.append(f"manifest version {version} is newer than supported schema {MANIFEST_SCHEMA}")
            return original, errors
        if version == MANIFEST_SCHEMA:
            legacy = sorted((set(_LEGACY_RENAMES) | {"harness_version"} | set(_FLAT_QA)) & set(top))
            if legacy:
                errors.append("schema v5 manifest contains legacy keys: " + ", ".join(legacy))
            return original, errors

    for legacy, canonical in _LEGACY_RENAMES.items():
        if legacy in top and canonical in top:
            errors.append(f"manifest contains both legacy {legacy} and canonical {canonical}")
    if errors:
        return original, errors

    flat_qa = {key: top[key] for key in _FLAT_QA if key in top}
    out: list[str] = []
    saw_version = False
    for raw in original.splitlines():
        match = _TOP_RE.match(raw)
        if not match:
            out.append(raw)
            continue
        key, rest = match.groups()
        if key == "version":
            out.append(f"version: {MANIFEST_SCHEMA}")
            saw_version = True
        elif key == "harness_version" or key in _FLAT_QA:
            continue
        elif key in _LEGACY_RENAMES:
            out.append(f"{_LEGACY_RENAMES[key]}:{rest}")
        else:
            out.append(raw)
    if not saw_version:
        out.insert(0, f"version: {MANIFEST_SCHEMA}")

    qa_start = next((i for i, line in enumerate(out) if line == "qa:"), None)
    if qa_start is None:
        out.extend(["", "qa:"])
        qa_start = len(out) - 1
    additions: list[str] = []
    if "default_mode" not in existing_qa:
        migrated_top, _, _ = manifest_maps("\n".join(out))
        if flat_qa.get("browser_qa_supported", "false").lower() == "true":
            default_mode = "browser"
        elif flat_qa.get("desktop_qa_supported", "false").lower() == "true":
            default_mode = "desktop"
        elif migrated_top.get("type") == "api":
            default_mode = "api"
        else:
            default_mode = "cli"
        additions.append(f"  default_mode: {default_mode}")
    for key in _FLAT_QA:
        if key not in existing_qa:
            additions.append(f"  {key}: {flat_qa.get(key, 'false')}")
    out[qa_start + 1:qa_start + 1] = additions
    return "\n".join(out).rstrip() + "\n", []


def has_verify_command(text: str) -> bool:
    lines = text.splitlines()
    for index, raw in enumerate(lines):
        if raw == "verify_commands:":
            for candidate in lines[index + 1:]:
                if candidate and not candidate[0].isspace() and not candidate.startswith("#"):
                    break
                stripped = candidate.lstrip()
                if stripped.startswith("- ") and stripped[2:].strip():
                    return True
    return False


def validate_structure(
    repo: Path,
    plugin_root: Path,
    project_doc: str,
    manifest_text: str,
    gitignore_text: str,
) -> list[str]:
    errors: list[str] = []
    top, qa, parse_errors = manifest_maps(manifest_text)
    errors.extend(parse_errors)
    if top.get("version") != str(MANIFEST_SCHEMA):
        errors.append(f"manifest version must be {MANIFEST_SCHEMA}")
    for key in ("name", "type"):
        if not top.get(key):
            errors.append(f"manifest top-level {key} is missing")
    if not top.get("test_command") and not has_verify_command(manifest_text):
        errors.append("manifest requires test_command or a non-empty verify_commands list")
    if "browser_qa_supported" not in qa:
        errors.append("manifest qa.browser_qa_supported is missing")

    project_path = safe_path(repo, project_doc)
    project_text = project_path.read_text(encoding="utf-8") if project_path.is_file() else ""
    if not project_text:
        errors.append(f"{project_doc} is missing or empty")
    else:
        if ROUTING_MARKER not in project_text:
            errors.append(f"{project_doc} is missing the harness routing marker")
        if not any(line.strip() == "@CONTRACTS.md" for line in project_text.splitlines()):
            errors.append(f"{project_doc} is missing @CONTRACTS.md import")

    for rel in ("CONTRACTS.md", "CONTRACTS.local.md"):
        path = safe_path(repo, rel)
        if not path.is_file() or not path.read_text(encoding="utf-8").strip():
            errors.append(f"{rel} is missing or empty")
    for lens in ("plan", "runtime", "document"):
        rel = f"doc/harness/critics/{lens}.md"
        path = safe_path(repo, rel)
        body = path.read_text(encoding="utf-8") if path.is_file() else ""
        lowered = body.lower()
        if not body.strip():
            errors.append(f"{rel} is missing or empty")
        elif "critic" not in lowered or lens not in lowered or not any(
            line.lstrip().startswith("- ") for line in body.splitlines()
        ):
            errors.append(f"{rel} is a placeholder, expected a {lens} critic playbook")
    for rel in REQUIRED_SETUP_RESOURCES:
        path = plugin_root / rel
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f"installed setup resource is missing or empty: {rel}")

    contract = safe_path(repo, "CONTRACTS.md")
    lint = plugin_root / "scripts/contract_lint.py"
    if contract.is_file() and lint.is_file():
        result = subprocess.run(
            [sys.executable, str(lint), "--path", str(contract), "--repo-root", str(repo), "--quick"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            detail = (result.stdout + result.stderr).strip().splitlines()
            errors.append("CONTRACTS.md failed contract_lint" + (f": {detail[-1]}" if detail else ""))

    ignored = set(gitignore_text.splitlines())
    for entry in OPERATIONAL_IGNORES:
        if entry not in ignored:
            errors.append(f".gitignore is missing: {entry}")
    return errors


def operational_symlink_errors(repo: Path) -> list[str]:
    errors: list[str] = []
    for pattern in OPERATIONAL_IGNORES:
        base = pattern.rstrip("/")
        candidates = list(repo.glob(base)) if "*" in base else [repo / base]
        for candidate in candidates:
            if not candidate.exists() and not candidate.is_symlink():
                continue
            try:
                relative = str(candidate.relative_to(repo))
                safe_path(repo, relative)
            except ValueError as exc:
                errors.append(str(exc))
                continue
            if pattern == "doc/harness/debug/goal-hook-payloads/" and candidate.is_dir():
                for current, dirs, files in os.walk(candidate, followlinks=False):
                    for name in dirs + files:
                        nested = Path(current) / name
                        if nested.is_symlink():
                            errors.append(
                                f"operational path contains symlink: {nested.relative_to(repo)}"
                            )
    return errors


def representative_path(pattern: str) -> str:
    if pattern.endswith("/"):
        return pattern + "__harness_probe__"
    if "*" in pattern:
        return pattern.replace("*", "probe")
    return pattern


def effective_ignore_errors(repo: Path) -> list[str]:
    errors: list[str] = []
    probes = {representative_path(pattern) for pattern in OPERATIONAL_IGNORES}
    existing: set[Path] = set()
    for pattern in OPERATIONAL_IGNORES:
        base = pattern.rstrip("/")
        candidates = list(repo.glob(base)) if "*" in base else [repo / base]
        for candidate in candidates:
            if candidate.is_file() or candidate.is_symlink():
                existing.add(candidate)
            elif pattern == "doc/harness/debug/goal-hook-payloads/" and candidate.is_dir():
                # Payloads are flat. Check real sensitive files in addition to
                # the directory probe without walking every task/runtime file.
                existing.update(path for path in candidate.iterdir() if path.is_file() or path.is_symlink())
    existing_rel = {str(path.relative_to(repo)) for path in existing}
    requested = sorted(probes | existing_rel)
    result = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "--no-index", "-z", "--stdin"],
        input="\0".join(requested) + "\0",
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        errors.append("git check-ignore failed while validating operational paths")
    ignored = set(filter(None, result.stdout.split("\0")))
    for rel in sorted(probes - ignored):
        errors.append(f"operational path is not effectively ignored: {rel}")
    for rel in sorted(existing_rel - ignored):
        errors.append(f"existing operational path is not effectively ignored: {rel}")
    tracked = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"], capture_output=True, text=True
    )
    if tracked.returncode != 0:
        errors.append("repository is not a readable git worktree")
        return errors
    for rel in filter(None, tracked.stdout.split("\0")):
        for pattern in OPERATIONAL_IGNORES:
            matches = rel.startswith(pattern) if pattern.endswith("/") else fnmatch.fnmatch(rel, pattern)
            if matches:
                errors.append(f"operational artifact is already tracked: {rel}")
                break
    return errors


def restore(path: Path, original: str | None, mode: int | None) -> None:
    if original is None:
        if path.exists() and not path.is_dir():
            path.unlink()
        return
    atomic_write(path, original, default_mode=mode or 0o644)
    if mode is not None:
        os.chmod(path, mode)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or finalize a harness setup")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--plugin-root", type=Path, required=True)
    parser.add_argument("--project-doc", choices=("AGENTS.md", "CLAUDE.md"), required=True)
    parser.add_argument("--check", action="store_true", help="verify without modifying files")
    parser.add_argument("--prepare", action="store_true", help="apply migration and ignores without stamping")
    parser.add_argument("--gitignore-only", action="store_true", help="only apply and verify operational ignores")
    parser.add_argument("--qa-verified", action="store_true", help="attest that setup QA prerequisites passed")
    parser.add_argument("--runtime-verified", action="store_true", help="attest that runtime checks passed")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    plugin_root = args.plugin_root.resolve()
    try:
        gitignore_path = safe_path(repo, ".gitignore")
        manifest_path = safe_path(repo, "doc/harness/manifest.yaml")
        version_path = safe_path(repo, "doc/harness/.version")
    except ValueError as exc:
        print(f"SETUP_ERROR: {exc}")
        return 1

    gitignore_original = gitignore_path.read_text(encoding="utf-8") if gitignore_path.is_file() else None
    manifest_original = manifest_path.read_text(encoding="utf-8") if manifest_path.is_file() else None
    gitignore_mode = stat.S_IMODE(gitignore_path.stat().st_mode) if gitignore_path.exists() else None
    manifest_mode = stat.S_IMODE(manifest_path.stat().st_mode) if manifest_path.exists() else None
    version_original = version_path.read_text(encoding="utf-8") if version_path.is_file() else None
    version_mode = stat.S_IMODE(version_path.stat().st_mode) if version_path.exists() else None
    gitignore_candidate = render_gitignore(gitignore_original or "")

    if args.gitignore_only:
        atomic_write(gitignore_path, gitignore_candidate)
        errors = effective_ignore_errors(repo)
        if errors:
            restore(gitignore_path, gitignore_original, gitignore_mode)
            for error in errors:
                print(f"SETUP_ERROR: {error}")
            return 1
        print(f"SETUP_GITIGNORE_OK: updated={str(gitignore_candidate != (gitignore_original or '')).lower()}")
        return 0

    if manifest_original is None:
        manifest_candidate, migration_errors = "", ["doc/harness/manifest.yaml is missing"]
    else:
        manifest_candidate, migration_errors = migrate_manifest_text(manifest_original)
    errors = migration_errors + validate_structure(
        repo, plugin_root, args.project_doc, manifest_candidate, gitignore_candidate
    ) + operational_symlink_errors(repo)
    if not (args.check or args.prepare):
        if not args.qa_verified:
            errors.append("finalization requires --qa-verified after QA infrastructure checks")
        if not args.runtime_verified:
            errors.append("finalization requires --runtime-verified after runtime checks")
    if errors:
        for error in dict.fromkeys(errors):
            print(f"SETUP_ERROR: {error}")
        return 1
    if args.check:
        errors = effective_ignore_errors(repo)
        for error in errors:
            print(f"SETUP_ERROR: {error}")
        if errors:
            return 1
        print("SETUP_CHECK_OK: setup contract verified without writes")
        return 0

    try:
        atomic_write(gitignore_path, gitignore_candidate)
        atomic_write(manifest_path, manifest_candidate)
        errors = effective_ignore_errors(repo)
        if errors:
            raise RuntimeError("\n".join(errors))
        if args.prepare:
            print("SETUP_PREPARED: manifest and operational ignores verified")
            return 0
        atomic_write(version_path, HARNESS_VERSION + "\n")
    except BaseException as exc:
        restore(gitignore_path, gitignore_original, gitignore_mode)
        restore(manifest_path, manifest_original, manifest_mode)
        restore(version_path, version_original, version_mode)
        for error in str(exc).splitlines() or [repr(exc)]:
            print(f"SETUP_ERROR: {error}")
        return 1
    print(f"SETUP_OK: runtime document={args.project_doc}; version={HARNESS_VERSION}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
