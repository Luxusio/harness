# runtime critic project playbook
summary: {{PROJECT_SUMMARY}}
updated: {{SETUP_DATE}}

# Verification approach
- preferred_order: [tests, smoke, api, browser]
- must_verify: {{MUST_VERIFY}}
- prefer_commands: {{PREFER_COMMANDS}}

# Rules
- Verify through execution, not code reading
- Every PASS needs at least one concrete evidence item
- BLOCKED_ENV requires exact blocker description
