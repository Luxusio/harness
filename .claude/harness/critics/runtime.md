# runtime critic project playbook
tags: [critic, runtime, project, active]
summary: Claude Code execution harness plugin
must_verify: [verify.sh, healthcheck.py, smoke.py]
prefer: [bash plugin/scripts/verify.sh, python plugin/scripts/healthcheck.py]
block_if: execution-skipped-without-reason, evidence-free-pass
updated: 2026-03-30

# Environment map
- Python scripts in plugin/scripts/ (no venv, stdlib only)
- No build step, no external dependencies
- Hook scripts invoked by Claude Code runtime via hooks.json
