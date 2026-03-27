# plan critic project playbook
tags: [critic, plan, project, active]
summary: {{PROJECT_SUMMARY}}
updated: {{SETUP_DATE}}

# Project-specific plan checks
- Plans must reference existing REQ notes when applicable
- Acceptance criteria must be verifiable with available tooling
- {{PLAN_EXTRA_RULES}}

# Contract requirements
- QA mode must be explicit (browser-first, api-smoke, cli-test, etc.)
- Persistence artifacts required: TASK_STATE.yaml updates, HANDOFF.md updates
- Docs sync steps required: notes to add/update/supersede, root indexes to refresh
- Hard fail conditions must be specified
- Scope in / scope out must be defined
- Verification contract must include commands, routes, persistence checks, expected outputs
