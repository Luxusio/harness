#!/usr/bin/env python3
"""Prepare, verify, and finalize the mechanical harness setup contract."""
from __future__ import annotations

import argparse
import ctypes
import errno
import fnmatch
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

from _lib import (  # type: ignore
    GitBindingError,
    _direct_gitlink_index_entries,
    _registered_source_metadata_binding,
    _trusted_git_env,
)


HARNESS_VERSION = "2.3.0"
MANIFEST_SCHEMA = 5
ROUTING_MARKER = "<!-- harness:routing-injected -->"
CODEX_RUN_POLICY = "skills/run/agents/openai.yaml"
ROUTING_BLOCK = """## Harness routing
<!-- harness:routing-injected -->
- On Codex, every repo-mutating request → invoke `$harness:run` before editing; it loads the internal canonical workflow, syncs a native Goal when present, and otherwise opens/resumes a Harness task
- On Claude Code, run the full cycle (plan → develop → verify → close) through native `/goal` for explicit goals or the runtime's canonical task route for plain repo-mutating requests
- Bootstrap harness in a new project / repair existing → `Skill(harness:setup)`
- Plan-only requests → sync/create Goal and stop after the internal plan phase if the user explicitly asks not to implement
- Implement an approved PLAN.md / develop only → resume the active Goal child task through the internal develop path
- Contract drift / post-upgrade cleanup → continuous maintenance flow in the active/next Goal child task
- Read-only question or explanation → answer directly, no Harness run skill

### Durable Decision Documentation Gate

A user-stated durable decision is not handled until it is documented under `doc/`.
If the user establishes, corrects, or confirms a lasting product, design,
architecture, domain, workflow, or implementation rule, update the matching
`doc/` file before finalizing. Conversation history is not durable memory. If
no matching document exists, create one under the appropriate `doc/` area; if no
doc is needed, record the specific no-doc rationale in the PLAN durable-doc
decision.
<!-- /harness:routing-injected -->
"""

OPERATIONAL_IGNORES = (
    "doc/harness/tasks/",
    "doc/harness/goals/",
    "doc/harness/reviews/",
    "doc/harness/learnings.jsonl",
    "doc/harness/runbook_candidates.yaml",
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
    "doc/harness/.maintain-last-run",
    "doc/harness/.maintain-observe.log",
    "doc/harness/.maintain-pending.json",
)

REQUIRED_SETUP_RESOURCES = (
    "skills/run/SKILL.md",
    "skills/setup/SKILL.md",
    "skills/setup/repo-census.md",
    "skills/setup/project-interview.md",
    "skills/setup/bootstrap.md",
    "skills/setup/verify-report.md",
    "skills/setup/templates/CONTRACTS.md",
    "skills/setup/templates/CONTRACTS.local.md",
    "scripts/contract_lint.py",
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
        # Retained so regeneration strips this header from repos set up before
        # the hygiene subsystem was removed.
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


def manifest_array(text: str, key: str) -> list[str]:
    lines = text.splitlines()
    prefix = key + ":"
    for index, raw in enumerate(lines):
        if not raw.startswith(prefix):
            continue
        rest = raw[len(prefix):].strip()
        if rest.startswith("[") and rest.endswith("]"):
            return [
                item.strip().strip('"').strip("'")
                for item in rest[1:-1].split(",")
                if item.strip()
            ]
        values: list[str] = []
        for child in lines[index + 1:]:
            match = re.match(r"^  -\s+(.+?)\s*$", child)
            if not match:
                break
            values.append(match.group(1).strip().strip('"').strip("'"))
        return values
    return []


def source_git_root_errors(repo: Path, manifest_text: str) -> list[str]:
    values = manifest_array(manifest_text, "source_git_roots")
    git_control = (repo / ".git").exists()
    if git_control and not values:
        return []
    if not values:
        return ["non-Git Harness workspace requires source_git_roots"]
    errors: list[str] = []
    roots: list[Path] = []
    try:
        direct_gitlinks = _direct_gitlink_index_entries(str(repo)) if git_control else {}
    except RuntimeError as exc:
        return [str(exc)]
    for value in values:
        if (
            not value
            or Path(value).is_absolute()
            or value in {".", ".."}
            or value.startswith("../")
            or not re.fullmatch(r"[A-Za-z0-9._/-]+", value)
            or any(part in {"", ".", ".."} for part in value.replace("\\", "/").split("/"))
        ):
            errors.append(f"invalid source_git_roots entry: {value}")
            continue
        try:
            candidate = safe_path(repo, value)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        normalized = value.replace("\\", "/").rstrip("/")
        if git_control and normalized not in direct_gitlinks:
            errors.append(
                "[REGISTERED_SOURCE_NOT_DIRECT_GITLINK] "
                f"source_git_roots entry '{normalized}' is not an exact direct "
                "mode-160000 entry in the control repository index"
            )
            continue
        if git_control and not candidate.is_dir():
            try:
                _registered_source_metadata_binding(
                    str(repo), str(candidate), normalized,
                )
            except GitBindingError as exc:
                errors.append(
                    f"{exc}; path={exc.path}; invariant={exc.invariant}; "
                    f"next_action={exc.next_action}"
                )
            continue
        if not candidate.is_dir():
            errors.append(f"source_git_roots entry is not a directory: {value}")
            continue
        try:
            result = subprocess.run(
                ["git", "-C", str(candidate), "rev-parse", "--show-toplevel"],
                capture_output=True, text=True, timeout=5,
                env=_trusted_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired):
            errors.append(f"source_git_roots entry is not a readable Git root: {value}")
            continue
        if result.returncode != 0 or Path(result.stdout.strip()).resolve() != candidate.resolve():
            errors.append(f"source_git_roots entry is not an exact Git root: {value}")
            continue
        if git_control:
            try:
                _registered_source_metadata_binding(
                    str(repo), str(candidate.resolve()), normalized,
                )
            except GitBindingError as exc:
                errors.append(
                    f"{exc}; path={exc.path}; invariant={exc.invariant}; "
                    f"next_action={exc.next_action}"
                )
                continue
        resolved = candidate.resolve()
        if resolved in roots:
            errors.append(f"duplicate source_git_roots entry: {value}")
            continue
        if any(root in resolved.parents or resolved in root.parents for root in roots):
            errors.append(f"nested source_git_roots entries are not allowed: {value}")
            continue
        roots.append(resolved)
    return errors


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


def codex_run_allows_implicit_invocation(text: str) -> bool:
    """Parse the one required nested policy flag without a YAML dependency."""
    lines = text.splitlines()
    policy_indexes = [i for i, raw in enumerate(lines) if raw == "policy:"]
    if len(policy_indexes) != 1:
        return False
    policy_index = policy_indexes[0]
    matches = []
    for raw in lines[policy_index + 1:]:
        if raw and not raw[0].isspace():
            break
        if "allow_implicit_invocation" not in raw:
            continue
        if not raw.startswith("  ") or raw.startswith("   ") or ":" not in raw:
            return False
        key, value = raw[2:].split(":", 1)
        if key != "allow_implicit_invocation":
            return False
        matches.append(value.strip().lower())
    return matches == ["true"]


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
    errors.extend(source_git_root_errors(repo, manifest_text))
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
        if project_doc == "AGENTS.md":
            routing_text = project_text.split(ROUTING_MARKER, 1)[-1]
            if "$harness:run" not in routing_text or not re.search(
                r"repo(?:sitory)?[- ]mutat", routing_text, re.IGNORECASE
            ):
                errors.append(
                    "AGENTS.md harness routing block must route repository mutation "
                    "to $harness:run"
                )

    if project_doc == "AGENTS.md":
        policy_path = plugin_root / CODEX_RUN_POLICY
        policy_text = policy_path.read_text(encoding="utf-8") if policy_path.is_file() else ""
        if not policy_text:
            errors.append(f"installed setup resource is missing or empty: {CODEX_RUN_POLICY}")
        elif not codex_run_allows_implicit_invocation(policy_text):
            errors.append(
                f"{CODEX_RUN_POLICY} must set policy.allow_implicit_invocation: true"
            )

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
    return errors


def representative_path(pattern: str) -> str:
    if pattern.endswith("/"):
        return pattern + "__harness_probe__"
    if "*" in pattern:
        return pattern.replace("*", "probe")
    return pattern


def effective_ignore_errors(repo: Path) -> list[str]:
    errors: list[str] = []
    if not (repo / ".git").exists():
        return errors
    probes = {representative_path(pattern) for pattern in OPERATIONAL_IGNORES}
    existing: set[Path] = set()
    for pattern in OPERATIONAL_IGNORES:
        base = pattern.rstrip("/")
        candidates = list(repo.glob(base)) if "*" in base else [repo / base]
        for candidate in candidates:
            if candidate.is_file() or candidate.is_symlink():
                existing.add(candidate)
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


def _project_doc_text(path: Path) -> tuple[str, os.stat_result | None]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return "", None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise ValueError("runtime project document must be a regular non-symlink file")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("runtime project document changed during setup")
        with os.fdopen(fd, "r", encoding="utf-8", newline="") as handle:
            fd = -1
            text = handle.read(2 * 1024 * 1024 + 1)
        if len(text) > 2 * 1024 * 1024:
            raise ValueError("runtime project document is too large")
        after = os.lstat(path)
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise ValueError("runtime project document changed during setup")
        return text, before
    finally:
        if fd >= 0:
            os.close(fd)


def _with_contract_import(text: str) -> str:
    if any(line.strip() == "@CONTRACTS.md" for line in text.splitlines()):
        return text
    newline = "\r\n" if "\r\n" in text else "\n"
    lines = text.splitlines(keepends=True)
    insert_after = 0
    if lines and lines[0].strip() == "---":
        for index, line in enumerate(lines[1:], 1):
            if line.strip() == "---":
                insert_after = index + 1
                break
    if insert_after == 0:
        for index, line in enumerate(lines):
            if line.startswith("# "):
                insert_after = index + 1
                break
    offset = sum(len(line) for line in lines[:insert_after])
    prefix = text[:offset]
    suffix = text[offset:]
    separator = "" if not prefix or prefix.endswith(("\n", "\r")) else newline
    return prefix + separator + "@CONTRACTS.md" + newline + suffix


def _with_routing_block(text: str) -> str:
    newline = "\r\n" if "\r\n" in text else "\n"
    legacy = re.compile(
        r"(?m)^[ \t]*- Default agent is harness[ \t]*(?:\r?\n|$)"
    )
    working = legacy.sub("", text)
    marker = re.search(r"(?m)^<!-- harness:routing-injected -->[ \t]*(?:\r?\n|$)", working)
    block = ROUTING_BLOCK.rstrip("\n").replace("\n", newline) + newline
    if marker is None:
        separator = ""
        if working:
            separator = newline if working.endswith(("\n", "\r")) else newline * 2
        return working + separator + block
    start = marker.start()
    prefix = working[:start]
    prior_heading = re.search(
        r"(?m)^## Harness routing[ \t]*(?:\r?\n)$", prefix
    )
    if prior_heading and not prefix[prior_heading.end():].strip():
        start = prior_heading.start()
    closing = re.search(
        r"(?m)^<!-- /harness:routing-injected -->[ \t]*(?:\r?\n|$)",
        working[marker.end():],
    )
    if closing is not None:
        end = marker.end() + closing.end()
    else:
        next_heading = re.search(r"(?m)^## .*(?:\r?\n|$)", working[marker.end():])
        # A legacy unbounded block is only safe to replace through a following
        # section boundary. At EOF, preserve the ambiguous suffix as user data.
        end = (
            marker.end()
            if next_heading is None
            else marker.end() + next_heading.start()
        )
    return working[:start] + block + working[end:]


def _exchange_project_doc(left: str, right: str) -> None:
    """Atomically exchange two files, or fail closed when unsupported."""
    try:
        renameat2 = ctypes.CDLL(None, use_errno=True).renameat2
    except (AttributeError, OSError) as exc:
        raise OSError(errno.ENOTSUP, "atomic project-document exchange unavailable") from exc
    renameat2.argtypes = [
        ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if renameat2(
        -100, os.fsencode(left), -100, os.fsencode(right), 2
    ) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


def _same_project_doc_snapshot(
    text: str,
    info: os.stat_result | None,
    expected_text: str,
    expected: os.stat_result | None,
) -> bool:
    if expected is None:
        return info is None and text == expected_text
    return (
        info is not None
        and text == expected_text
        and (info.st_dev, info.st_ino) == (expected.st_dev, expected.st_ino)
        and info.st_size == expected.st_size
        and info.st_mtime_ns == expected.st_mtime_ns
    )


def _same_file_identity(path: Path, expected: os.stat_result) -> bool:
    try:
        current = os.lstat(path)
    except OSError:
        return False
    return (
        stat.S_ISREG(current.st_mode)
        and not stat.S_ISLNK(current.st_mode)
        and (current.st_dev, current.st_ino)
        == (expected.st_dev, expected.st_ino)
    )


def update_project_doc(
    repo: Path,
    project_doc: str,
    *,
    ensure_routing: bool,
    ensure_contract_import: bool,
) -> bool:
    path = safe_path(repo, project_doc)
    original, before = _project_doc_text(path)
    candidate = original
    if ensure_routing:
        candidate = _with_routing_block(candidate)
    if ensure_contract_import:
        candidate = _with_contract_import(candidate)
    if candidate == original:
        return False
    latest, latest_stat = _project_doc_text(path)
    if latest != original:
        raise ValueError("runtime project document content changed during setup")
    if before is None:
        if latest_stat is not None:
            raise ValueError("runtime project document appeared during setup")
    elif (
        latest_stat is None
        or (latest_stat.st_dev, latest_stat.st_ino) != (before.st_dev, before.st_ino)
        or latest_stat.st_size != before.st_size
        or latest_stat.st_mtime_ns != before.st_mtime_ns
        or latest_stat.st_ctime_ns != before.st_ctime_ns
    ):
        raise ValueError("runtime project document changed during setup")

    mode = stat.S_IMODE(before.st_mode) if before else 0o644
    fd, tmp = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    exchanged = False
    preserve_tmp = False
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(candidate)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, mode)
        final_text, final_stat = _project_doc_text(path)
        if final_text != original:
            raise ValueError("runtime project document content changed during setup")
        if before is None:
            if final_stat is not None:
                raise ValueError("runtime project document appeared during setup")
        elif (
            final_stat is None
            or (final_stat.st_dev, final_stat.st_ino) != (before.st_dev, before.st_ino)
            or final_stat.st_size != before.st_size
            or final_stat.st_mtime_ns != before.st_mtime_ns
            or final_stat.st_ctime_ns != before.st_ctime_ns
        ):
            raise ValueError("runtime project document changed during setup")
        if before is None:
            try:
                os.link(tmp, path, follow_symlinks=False)
            except FileExistsError as exc:
                raise ValueError(
                    "runtime project document appeared during setup"
                ) from exc
            os.unlink(tmp)
        else:
            candidate_stat = os.lstat(tmp)
            _exchange_project_doc(tmp, str(path))
            exchanged = True
            try:
                displaced_text, displaced_stat = _project_doc_text(Path(tmp))
                if not _same_project_doc_snapshot(
                    displaced_text, displaced_stat, original, before
                ):
                    raise ValueError(
                        "runtime project document changed during setup"
                    )
            except BaseException:
                if not _same_file_identity(path, candidate_stat):
                    preserve_tmp = True
                    raise RuntimeError(
                        f"runtime project document changed again during rollback; "
                        f"original preserved at {tmp}"
                    )
                try:
                    _exchange_project_doc(tmp, str(path))
                    exchanged = False
                except OSError as rollback_exc:
                    preserve_tmp = True
                    raise RuntimeError(
                        f"runtime project document changed and rollback failed; "
                        f"original preserved at {tmp}"
                    ) from rollback_exc
                raise
            if not _same_file_identity(path, candidate_stat):
                preserve_tmp = True
                raise RuntimeError(
                    f"runtime project document changed after atomic exchange; "
                    f"original preserved at {tmp}"
                )
            os.unlink(tmp)
            exchanged = False
    except BaseException:
        if exchanged and not preserve_tmp:
            if not _same_file_identity(path, candidate_stat):
                preserve_tmp = True
            else:
                try:
                    _exchange_project_doc(tmp, str(path))
                    exchanged = False
                except OSError:
                    preserve_tmp = True
        if not preserve_tmp:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
        raise
    return True


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
    parser.add_argument("--project-doc-only", action="store_true", help="only apply safe runtime-document edits")
    parser.add_argument("--ensure-routing", action="store_true")
    parser.add_argument("--ensure-contract-import", action="store_true")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    plugin_root = args.plugin_root.resolve()
    if args.project_doc_only:
        try:
            changed = update_project_doc(
                repo,
                args.project_doc,
                ensure_routing=args.ensure_routing,
                ensure_contract_import=args.ensure_contract_import,
            )
        except (OSError, ValueError) as exc:
            print(f"SETUP_ERROR: {exc}")
            return 1
        print(f"SETUP_PROJECT_DOC_OK: updated={str(changed).lower()}")
        return 0
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
