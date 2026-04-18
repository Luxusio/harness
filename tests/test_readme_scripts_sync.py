"""Regression guard: README ↔ plugin/scripts/ drift.

Fails if:
1. A script exists in plugin/scripts/*.py but its basename is not mentioned in README.md
   (except internal helpers in INTERNAL_EXCLUDES).
2. README.md references a *.py file that does not exist in plugin/scripts/.

The check is lenient — basename match anywhere in README counts. This prevents
unnecessary churn when scripts are reorganized but still flags genuine drift.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = REPO_ROOT / "plugin" / "scripts"
MCP_DIR = REPO_ROOT / "plugin" / "mcp"
README = REPO_ROOT / "README.md"

# Internal helpers not expected in user-facing README.
INTERNAL_EXCLUDES = {"_lib.py"}

# Ignore scripts mentioned with these prefixes (paths that obviously aren't plugin/scripts/).
_PY_PATTERN = re.compile(r"([a-zA-Z_][a-zA-Z0-9_]*)\.py")


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


def _scripts_on_disk() -> set[str]:
    return {p.name for p in SCRIPTS_DIR.glob("*.py") if p.name not in INTERNAL_EXCLUDES}


def _py_names_mentioned_in_readme() -> set[str]:
    text = _readme_text()
    return {f"{m.group(1)}.py" for m in _PY_PATTERN.finditer(text)}


def test_every_plugin_script_is_mentioned_in_readme():
    disk = _scripts_on_disk()
    readme_names = _py_names_mentioned_in_readme()
    missing = sorted(disk - readme_names)
    assert not missing, (
        f"README.md is missing mentions of {len(missing)} script(s) in plugin/scripts/: "
        f"{missing}. Either reference each in README (e.g. quality scripts table) or "
        f"add it to tests/test_readme_scripts_sync.py::INTERNAL_EXCLUDES."
    )


def test_every_readme_script_mention_exists():
    disk = (
        _scripts_on_disk()
        | INTERNAL_EXCLUDES
        | {p.name for p in MCP_DIR.glob("*.py")}
    )
    readme_names = _py_names_mentioned_in_readme()
    # README may mention scripts from other paths (e.g., "get-pip.py" in prose). Only flag
    # .py names that look like plugin scripts (i.e., snake_case, no dashes, no dots).
    candidate = {n for n in readme_names if re.fullmatch(r"[a-z][a-z0-9_]*\.py", n)}
    ghosts = sorted(candidate - disk)
    assert not ghosts, (
        f"README.md mentions {len(ghosts)} plugin .py file(s) not present under "
        f"plugin/scripts/ or plugin/mcp/: {ghosts}. Either remove the reference "
        f"or restore the file."
    )
