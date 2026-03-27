# runtime critic project playbook
tags: [critic, runtime, project, active]
summary: {{PROJECT_SUMMARY}}
must_verify: {{MUST_VERIFY}}
prefer: {{PREFER_COMMANDS}}
block_if: execution-skipped-without-reason, evidence-free-pass
updated: {{SETUP_DATE}}

# Environment map
{{ENVIRONMENT_MAP}}

# Browser-first QA map
- preferred_verification_order: [tests, smoke, api, persistence, browser]
- health_checks: {{HEALTHCHECKS}}
- seed_reset_commands: {{SEED_RESET_COMMANDS}}
- persistence_checks: {{PERSISTENCE_CHECKS}}

# QA evidence requirements
- QA__runtime.md must be written before verdict
- Every PASS needs at least one concrete evidence item
- BLOCKED_ENV requires exact blocker description for TASK_STATE.yaml
