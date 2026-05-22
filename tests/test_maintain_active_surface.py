"""Regression guard for standalone maintain-skill removal.

The active plugin surface must not route users or agents back to
`harness:maintain`. Compatibility names such as `maintain_restore.py` and
legacy `.maintain-*` state fallbacks are allowed.
"""
from __future__ import annotations

from pathlib import Path


REPO = Path(__file__).resolve().parents[1]

SCAN_ROOTS = [
    "README.md",
    ".claude-plugin",
    ".codex-plugin",
    "plugin",
    "plugin-codex",
    "tests",
    "doc/common",
    "doc/harness/patterns",
]

FORBIDDEN = [
    "/harness:maintain",
    "Skill(maintain)",
    "Skill(harness:maintain)",
    "maintain-suggested",
    "run maintain skill",
    "maintain skill",
]

ALLOWED_FILES = {
    # This file documents the compatibility names and the historical removal.
    "doc/harness/patterns/maintenance-state-naming.md",
    # This file contains one explanatory sentence about the historical path.
    "doc/harness/patterns/continuous-maintenance-flow.md",
    # Test file necessarily contains the forbidden literals above.
    "tests/test_maintain_active_surface.py",
}

ALLOWED_SUBSTRINGS = [
    "maintain_restore.py",
    ".maintain-",
    "standalone maintain skill",
    "after standalone maintain skill removal",
    "old `.maintain-*` names",
]


def _iter_text_files():
    for root in SCAN_ROOTS:
        path = REPO / root
        if path.is_file():
            yield path
            continue
        for child in path.rglob("*"):
            if not child.is_file():
                continue
            if child.suffix not in {".md", ".py", ".json", ".yaml", ".yml", ".toml"}:
                continue
            rel = child.relative_to(REPO).as_posix()
            if rel.startswith("doc/harness/tasks/") or rel.startswith("doc/changes/"):
                continue
            yield child


def _line_allowed(rel: str, line: str) -> bool:
    if rel in ALLOWED_FILES:
        return True
    return any(token in line for token in ALLOWED_SUBSTRINGS)


def test_active_surface_does_not_route_to_standalone_maintain():
    violations: list[str] = []
    for path in _iter_text_files():
        rel = path.relative_to(REPO).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            if _line_allowed(rel, line):
                continue
            for phrase in FORBIDDEN:
                if phrase in line:
                    violations.append(f"{rel}:{lineno}: {phrase}: {line.strip()}")
    assert not violations, "\n".join(violations)

