"""Purpose-specific execution over existing WBS owners; no additional state store."""
from __future__ import annotations

import json
import os
from pathlib import Path, PurePosixPath
import re


KINDS = frozenset({"development", "document", "plan"})
DEFAULT_DOCUMENT_PATHS = ["doc/common", "docs", "README.md", "README.codex.md", "CHANGELOG.md"]
FIELDS = frozenset({"document_review", "verification", "install", "document_paths", "executable_paths"})
INSTRUCTION_NAMES = frozenset({"agents.md", "claude.md", "contracts.md", "skill.md"})
EXECUTABLE_ROOTS = ("plugin", "plugin-codex", "doc/harness", ".git", ".github", ".claude", ".codex", ".agents")
INSTRUCTION_DIRS = frozenset({"skills", "internal-skills", "agents", "prompts"})


def _require(ok, message):
    if not ok:
        raise ValueError("workflow policy: " + message)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result, "duplicate work declaration key")
        result[key] = value
    return result


def kind_from_plan(text):
    blocks = re.findall(r"^```wbs-work\s*\n(.*?)^```\s*$", text, re.M | re.S)
    _require(len(blocks) <= 1, "use one wbs-work fence")
    if not blocks:
        return "development"
    value = json.loads(blocks[0], object_pairs_hook=_unique)
    _require(isinstance(value, dict) and set(value) == {"kind"}
             and isinstance(value["kind"], str) and value["kind"] in KINDS,
             "wbs-work must declare kind: document, plan or development")
    return value["kind"]


def _paths(value):
    _require(isinstance(value, list) and len(value) <= 64, "paths must be a bounded literal list")
    for path in value:
        _require(isinstance(path, str) and 0 < len(path) <= 2048 and "\0" not in path
                 and "\\" not in path and not path.startswith("/")
                 and not re.match(r"^[A-Za-z]:", path)
                 and all(p not in {"", ".", ".."} for p in path.split("/")),
                 "paths must be canonical checkout-relative literals")
    _require(len(set(value)) == len(value), "duplicate policy path")
    return sorted(value)


def load(wbs_dir, repo=None, *, plan=None):
    """Read bounded PLAN/manifest metadata only. No Git, source scans or writes."""
    from _lib import _strict_regular_text_snapshot, find_repo_root
    from document_destination import _manifest_bytes, _proves_no_key, _yaml_mapping
    repo = Path(repo or find_repo_root(wbs_dir))
    if plan is None:
        snapshot = _strict_regular_text_snapshot(str(Path(wbs_dir) / "PLAN.md"), max_size=1024 * 1024)
        plan = snapshot["text"] if snapshot["exists"] else ""
    kind = kind_from_plan(plan)
    raw = _manifest_bytes(repo)
    configured = {}
    if raw is not None:
        text = raw.decode("utf-8", errors="strict")
        if not _proves_no_key(text, "workflow"):
            manifest = _yaml_mapping(text)
            configured = manifest.get("workflow", {})
    _require(isinstance(configured, dict) and not set(configured) - FIELDS, "unknown workflow settings")
    result = dict(kind=kind, document_review="self", verification="affected", install="changed",
                  document_paths=list(DEFAULT_DOCUMENT_PATHS), executable_paths=[])
    result.update(configured)
    for field, choices in (("document_review", {"self", "independent"}),
                           ("verification", {"affected", "full"}), ("install", {"changed", "manual"})):
        _require(isinstance(result[field], str) and result[field] in choices, "invalid " + field)
    result["document_paths"] = _paths(result["document_paths"])
    result["executable_paths"] = _paths(result["executable_paths"])
    # Only meaningful effects enter each consumer's identity. In particular,
    # delivery preferences must not invalidate unchanged reviews/test results.
    result["admission_identity"] = None if kind == "development" else {
        key: result[key] for key in ("kind", "document_paths", "executable_paths")}
    result["verification_identity"] = (None if result["verification"] == "affected" else
                                       {"kind": kind, "verification": result["verification"]}) if kind == "development" else {
        key: result[key] for key in ("kind", "document_review", "document_paths", "executable_paths")}
    return result


def required_lenses(policy, declared=None, *, explicit=False):
    from _lib import LENS_ORDER, SUPPORTED_LENSES
    if declared is not None:
        _require(isinstance(declared, list) and all(isinstance(v, str) and v in SUPPORTED_LENSES for v in declared)
                 and len(declared) == len(set(declared)), "invalid required_lenses")
    selected = set(declared or [])
    if policy["kind"] == "development":
        if explicit:
            _require("review-code" in selected and any(v.startswith("qa-") for v in selected),
                     "development requires review-code and applicable QA")
        selected.add("review-code")
        if not any(v.startswith("qa-") for v in selected):
            selected.add("qa-cli")
    elif policy["document_review"] == "independent":
        if explicit:
            _require("review-document" in selected, "independent documents require review-document")
        selected.add("review-document")
    return [lens for lens in LENS_ORDER if lens in selected]


def _covers(root, path):
    return path == root or path.startswith(root + "/")


def document_path(policy, path):
    """Positive narrative roles with immutable instruction/runtime exclusions."""
    path = str(path)
    if (not path or "\\" in path or path.startswith("/") or "\0" in path
            or any(p in {"", ".", ".."} for p in path.split("/"))):
        return False
    parts = PurePosixPath(path).parts
    if (parts[-1].casefold() in INSTRUCTION_NAMES or any(p.casefold() in INSTRUCTION_DIRS for p in parts[:-1])
            or any(_covers(root.casefold(), path.casefold()) for root in EXECUTABLE_ROOTS)
            or any(_covers(root, path) for root in policy["executable_paths"])):
        return False
    return (PurePosixPath(path).suffix.lower() in {".md", ".rst", ".txt", ".adoc"}
            and any(_covers(root, path) for root in policy["document_paths"]))


def require_write(policy, path):
    if policy["kind"] != "development":
        _require(document_path(policy, path),
                 "non-narrative or unknown source impact; retain this WBS/run and promote its plan to development before editing")


def proof_definition(node, policy):
    from wbs import digest
    identity = policy["verification_identity"]
    return digest(node if identity is None else [node, identity])


def capture_source_base(repo):
    """One minimal delivery fact; unknown remains unknown on later replans."""
    from _lib import _trusted_git_env
    from wbs import _execute
    if not os.path.lexists(Path(repo) / ".git"):
        return None
    result = _execute(["git", "rev-parse", "--verify", "HEAD"], str(repo), 3,
                      env=_trusted_git_env(), output_limit=1024, retain=1024)
    value = result["output"].strip()
    return value if result["returncode"] == 0 and not result["error"] and re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", value) else None
