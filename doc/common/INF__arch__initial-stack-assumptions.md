# INF arch initial-stack-assumptions
tags: [inf, root:common, confidence:high, status:active]
summary: Python stdlib-only hook scripts, no build step, no external deps
basis: No package.json/pyproject.toml/requirements.txt found; scripts use only stdlib
updated: 2026-03-30
verify_by: grep for import statements in plugin/scripts/

## Stack
- Python 3.x (stdlib only) for hook scripts
- Markdown for agent definitions, calibration packs, documentation
- JSON for plugin manifest and hooks config
- YAML referenced in docs but parsed via Python
- No build system, no test framework, no CI/CD
