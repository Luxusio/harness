#!/usr/bin/env python3
"""Lint CONTRACTS.md — markdown is source of truth, no YAML.

Checks (in order of severity):

  1. Managed-block markers present, well-formed, not duplicated. [hard]
  2. Every contract heading (### C-##) between markers has the four
     required fields: Title, When, Enforced by, On violation, Why. [hard]
  3. § 1 matrix references exactly match the § 2 contract id set. [soft]
  4. `Enforced by:` paths that look like repo files actually exist. [soft]
  5. No duplicate C-## ids. [hard]

Modes:
  --quick   Only checks 1, 3, 5 (fast — for a caller on a latency budget).
  (default) Runs all checks.

Callers: `plugin/skills/setup/bootstrap.md` runs this at setup, and
`tests/test_contract_lint_real_tree.py` runs it against this repository's own
CONTRACTS.md and skill trees on every suite invocation. It is deliberately
registered in no `hooks.json` event — an earlier docstring claimed a SessionStart
hook that never existed, which is the same false-enforcement defect the lint is
meant to catch.

Exit code:
  0   OK or soft-warn only
  1   Hard drift (markers broken, fields missing, duplicate ids)

Stdlib only. The exit code is advisory; should this ever be wired to a hook,
C-12 requires the command to end in `|| true`.
"""
from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
from dataclasses import dataclass, field

MANAGED_BEGIN = re.compile(r"<!--\s*harness:managed-begin(?:\s+v(\d+))?\s*-->")
MANAGED_END = re.compile(r"<!--\s*harness:managed-end\s*-->")
# `C-14a`-style suffixes are part of the id. Until 2026-09-04 both this
# pattern and MATRIX_LINK below required the id to end in a digit, so C-14a was
# invisible to every check here: no four-field validation, no matrix
# cross-reference, no path-hint check. It had no matrix row in the root
# CONTRACTS.md and the lint reported "17 contracts, 17 matrix refs OK" because
# it could not see the contract on either side of that comparison.
#
# The count was misleading in a second way: root read 17 against the setup
# template's 16, so the drift looked like a single missing contract when two
# were missing. REQ__contract-enforcement-claims-are-executable predicted
# exactly that misread.
CONTRACT_HEADING = re.compile(r"^###\s+(C-\d+[a-z]*)\s*$", re.MULTILINE)
FIELD_LINE = re.compile(r"^\*\*(Title|When|Enforced by|On violation|Why):\*\*", re.MULTILINE)
MATRIX_LINK = re.compile(r"\[(C-\d+[A-Za-z]*)\]\(#(c-\d+[A-Za-z]*)\)")
PATH_HINT = re.compile(r"`((?:plugin/|/|\.\.?/)[^`\s]+?\.[a-zA-Z]+)`")

REQUIRED_FIELDS = {"Title", "When", "Enforced by", "On violation", "Why"}


@dataclass
class LintReport:
    hard: list[str] = field(default_factory=list)
    soft: list[str] = field(default_factory=list)
    info: list[str] = field(default_factory=list)

    def is_hard(self) -> bool:
        return bool(self.hard)

    def render(self) -> str:
        out = []
        for label, items in (("HARD", self.hard), ("SOFT", self.soft), ("INFO", self.info)):
            for msg in items:
                out.append(f"[{label}] {msg}")
        return "\n".join(out) if out else "contract_lint: OK"


def _find_managed_block(text: str) -> tuple[int, int] | None:
    begins = list(MANAGED_BEGIN.finditer(text))
    ends = list(MANAGED_END.finditer(text))
    if not begins or not ends:
        return None
    if len(begins) > 1 or len(ends) > 1:
        return None
    b = begins[0]
    e = ends[0]
    if e.start() <= b.end():
        return None
    return b.end(), e.start()


def _extract_contracts(block_text: str) -> dict[str, str]:
    """Map C-## -> body text up to the next contract heading."""
    matches = list(CONTRACT_HEADING.finditer(block_text))
    contracts: dict[str, list[str]] = {}
    for i, m in enumerate(matches):
        cid = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(block_text)
        contracts.setdefault(cid, []).append(block_text[start:end])
    return {k: "\n".join(v) for k, v in contracts.items()}


def _extract_matrix_refs(text: str) -> set[str]:
    """Matrix links whose anchor actually points at the contract they label.

    The label and the anchor are matched independently by the regex, so
    `[C-14a](#c-14)` would otherwise count as a reference to C-14a while
    rendering a link that jumps to C-14. Before suffixed ids existed the two
    could not disagree by one character; now they can, and a wrong anchor is
    not a reference — dropping it here surfaces the contract as missing from
    the matrix, which is the true statement.

    Markdown lowercases heading anchors, so the comparison is case-folded.
    """
    return {
        label for label, anchor in MATRIX_LINK.findall(text)
        if anchor.lower() == label.lower()
    }


def _missing_fields(body: str) -> set[str]:
    present = {m.group(1) for m in FIELD_LINE.finditer(body)}
    return REQUIRED_FIELDS - present


def _referenced_paths(body: str) -> list[str]:
    return PATH_HINT.findall(body)


CONTRACT_REFERENCE_MATCH_LIMIT = 256


def _reference_path_issue(
    repo_root: str,
    ref: str,
    *,
    match_limit: int = CONTRACT_REFERENCE_MATCH_LIMIT,
) -> str | None:
    """Return a bounded diagnostic when a contract path has no safe file match."""
    if (
        os.path.isabs(ref)
        or "\\" in ref
        or ".." in ref.split("/")
        or "**" in ref
    ):
        return "is unsafe (absolute, parent, recursive, or non-POSIX reference)"

    repo_real = os.path.realpath(repo_root)
    inspected = 0
    unsafe = 0
    valid = 0
    try:
        candidates = [repo_real]
        for component in ref.split("/"):
            if not component:
                return "is unsafe (empty path component)"
            if not any(token in component for token in ("*", "?", "[")):
                candidates = [os.path.join(base, component) for base in candidates]
                continue

            expanded = []
            for base in candidates:
                if not os.path.exists(base):
                    if os.path.lexists(base):
                        unsafe += 1
                    continue
                resolved_base = os.path.realpath(base)
                try:
                    base_confined = (
                        os.path.commonpath((repo_real, resolved_base)) == repo_real
                    )
                except ValueError:
                    base_confined = False
                if not base_confined or not os.path.isdir(base):
                    unsafe += 1
                    continue
                try:
                    with os.scandir(base) as entries:
                        for entry in entries:
                            inspected += 1
                            if inspected > match_limit:
                                return f"exceeds bounded match limit ({match_limit})"
                            if fnmatch.fnmatchcase(entry.name, component):
                                expanded.append(entry.path)
                except (FileNotFoundError, NotADirectoryError):
                    continue
            candidates = expanded
            if not candidates:
                break

        for candidate in candidates:
            resolved = os.path.realpath(candidate)
            try:
                confined = os.path.commonpath((repo_real, resolved)) == repo_real
            except ValueError:
                confined = False
            if not confined or not os.path.isfile(candidate):
                unsafe += 1
                continue
            valid += 1
    except (OSError, ValueError):
        return "could not be checked safely"

    if unsafe:
        return "matches unsafe, escaping, or non-regular entries"
    if not valid:
        return "does not match a regular file"
    return None


def lint(path: str, quick: bool = False, repo_root: str = ".") -> LintReport:
    report = LintReport()
    if not os.path.isfile(path):
        report.hard.append(f"{path} not found")
        return report

    try:
        with open(path, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError as e:
        report.hard.append(f"cannot read {path}: {e}")
        return report

    block = _find_managed_block(text)
    if block is None:
        report.hard.append(
            f"{path}: managed-block markers missing, duplicated, or inverted. "
            "Expected exactly one `<!-- harness:managed-begin -->` and one "
            "`<!-- harness:managed-end -->`."
        )
        return report
    block_text = text[block[0]:block[1]]

    # Duplicate ids
    heading_ids = [m.group(1) for m in CONTRACT_HEADING.finditer(block_text)]
    seen: set[str] = set()
    dups: set[str] = set()
    for cid in heading_ids:
        if cid in seen:
            dups.add(cid)
        seen.add(cid)
    for cid in sorted(dups):
        report.hard.append(f"duplicate contract id {cid}")

    contracts = _extract_contracts(block_text)

    # Matrix vs § 2 cross-check
    refs = _extract_matrix_refs(text)
    only_in_matrix = refs - contracts.keys()
    only_in_body = contracts.keys() - refs
    for cid in sorted(only_in_matrix):
        report.soft.append(f"matrix references {cid} but no § 2 entry exists")
    for cid in sorted(only_in_body):
        report.soft.append(f"{cid} defined in § 2 but missing from matrix")

    if quick:
        report.info.append(f"{len(contracts)} contracts, {len(refs)} matrix refs (quick mode)")
        return report

    # Field completeness
    for cid in sorted(contracts):
        missing = _missing_fields(contracts[cid])
        if missing:
            report.hard.append(
                f"{cid} is missing required fields: {', '.join(sorted(missing))}"
            )

    # Referenced file paths exist
    for cid in sorted(contracts):
        for ref in _referenced_paths(contracts[cid]):
            issue = _reference_path_issue(repo_root, ref)
            if issue:
                report.soft.append(f"{cid}: referenced path `{ref}` {issue}")

    report.info.append(f"{len(contracts)} contracts, {len(refs)} matrix refs OK")
    return report


SKILL_WEIGHT_LIMIT = 500  # C-13: SKILL.md hot path line budget


def check_skill_weights(plugin_root: str) -> list[tuple[str, int]]:
    """Return [(path, line_count), ...] for SKILL.md files over C-13 budget.

    Both layouts are scanned: the Claude tree keeps skills under `skills/` and
    the Codex tree under `internal-skills/`. Scanning only the former left the
    Codex twins — including the one file sitting exactly at the cap — with no
    machine guard at all.
    """
    over = []
    for parent in ("skills", "internal-skills"):
        skills_dir = os.path.join(plugin_root, parent)
        if not os.path.isdir(skills_dir):
            continue
        for entry in sorted(os.listdir(skills_dir)):
            skill_md = os.path.join(skills_dir, entry, "SKILL.md")
            if not os.path.isfile(skill_md):
                continue
            try:
                with open(skill_md, "r", encoding="utf-8") as f:
                    n = sum(1 for _ in f)
            except OSError:
                continue
            if n > SKILL_WEIGHT_LIMIT:
                over.append((skill_md, n))
    return over


def main() -> int:
    p = argparse.ArgumentParser(description="Lint CONTRACTS.md managed block")
    p.add_argument("--path", default="CONTRACTS.md",
                   help="Path to CONTRACTS.md (default: ./CONTRACTS.md)")
    p.add_argument("--repo-root", default=".",
                   help="Repo root for resolving `Enforced by` paths")
    p.add_argument("--quick", action="store_true",
                   help="Fast check — markers + matrix + duplicates only")
    p.add_argument("--quiet", action="store_true",
                   help="Suppress output on OK; still exits non-zero on hard drift")
    p.add_argument("--check-weight", action="store_true",
                   help=f"Also enforce C-13: SKILL.md <= {SKILL_WEIGHT_LIMIT} lines")
    # Dual-name env read: HARNESS_PLUGIN_ROOT (preferred) then CLAUDE_PLUGIN_ROOT
    # (deprecated; v2.5.0 drops support per CHANGELOG). Repo-root run without
    # env set falls back to ./plugin for ergonomics.
    _plugin_root_default = (
        os.environ.get("HARNESS_PLUGIN_ROOT")
        or os.environ.get("CLAUDE_PLUGIN_ROOT")
        or "plugin"
    )
    p.add_argument("--plugin-root", default=_plugin_root_default,
                   help="Plugin root for --check-weight (default: $HARNESS_PLUGIN_ROOT or $CLAUDE_PLUGIN_ROOT or ./plugin)")
    args = p.parse_args()

    if not os.path.isabs(args.path):
        # Prefer project root / working dir — allows hook to chdir-less use.
        candidates = [args.path, os.path.join(args.repo_root, args.path)]
        for c in candidates:
            if os.path.isfile(c):
                args.path = c
                break
        else:
            # Missing is not hard for a --quick caller — the project may not
            # have installed CONTRACTS.md yet.
            if args.quick:
                if not args.quiet:
                    print("contract_lint: no CONTRACTS.md (run setup to install)")
                return 0

    report = lint(args.path, quick=args.quick, repo_root=args.repo_root)

    if args.check_weight:
        for skill_md, n in check_skill_weights(args.plugin_root):
            report.soft.append(
                f"C-13 weight: {skill_md} is {n} lines (>{SKILL_WEIGHT_LIMIT}); "
                "extract phases to sub-files"
            )

    if report.is_hard():
        print(report.render(), file=sys.stderr)
        return 1
    if not args.quiet:
        print(report.render())
    return 0


if __name__ == "__main__":
    sys.exit(main())
