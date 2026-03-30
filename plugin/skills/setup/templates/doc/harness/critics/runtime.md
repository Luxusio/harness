# runtime critic project playbook
summary: {{PROJECT_SUMMARY}}
updated: {{SETUP_DATE}}

# Primary rule

Verify through execution, not through code reading. Do not give PASS from static analysis alone when runtime verification is feasible.

# Verification approach

## For browser-first projects (manifest.browser.enabled: true or qa.default_mode: browser-first)

Execute in this priority order:

1. **Start server** — launch the application (use HANDOFF.md command or manifest `runtime.start_command`)
2. **Health probe** — confirm server is responding (HTTP check or equivalent)
3. **Browser interaction** — use MCP chrome-devtools to navigate to UI route from HANDOFF.md `browser_context.ui_route`, interact with feature, confirm `expected_dom_signal`
4. **Persistence / API / logs** — confirm data written, API returned expected response, or logs show expected output
5. **Architecture check** (optional) — run constraint checks if present

Do NOT fall back to CLI-only verification when browser verification is feasible. Attempt browser first; fall back only if environment genuinely blocks it (record as BLOCKED_ENV).

## For non-browser projects

1. Run targeted tests / lint / smoke commands
2. Exercise API endpoints or user flows
3. Verify persistence or side effects when relevant
4. Run architecture constraint checks if present

# Project-specific settings

- preferred_order: [{{PREFERRED_ORDER}}]
- must_verify: {{MUST_VERIFY}}
- prefer_commands: {{PREFER_COMMANDS}}

# Rules

- Every PASS needs at least one concrete evidence item
- BLOCKED_ENV requires exact blocker description
- A FAIL verdict must list specific unmet acceptance criteria
- Evidence is natural language summaries of command output — no metadata schemas needed
