---
name: validation-loop
description: Use when code or behavior changes need evidence. Applies narrow-to-wide validation and records any remaining gaps honestly.
allowed-tools: Read, Glob, Grep, Bash, Agent
user-invocable: false
---

## Trigger

Activate after any code change, fix, or behavioral modification to close the evidence loop.

## Procedure

### 1. Determine validation scope
Read `harness/manifest.yaml` to understand:
- Project type (web-app, api-service, worker, library)
- Available commands (test, lint, build)
- Key journeys that must keep working

### 2. Run narrow-to-wide validation

**Level 1: Quick static checks** (always run if available)
- Format check
- Lint
- Typecheck
Run the `lint` command from manifest. If it fails, fix before proceeding.

**Level 2: Targeted tests** (always run if available)
- Run tests related to the changed files
- Use the `test` command from manifest with appropriate scope
- All related tests must pass

**Level 3: Smoke / key journey checks** (run when change could affect user flows)
- Run `harness/scripts/smoke.sh` if it exists and is project-specific
- Or manually verify the key journeys from manifest
- For UI changes: delegate to `harness:browser-validator` if tooling is available

**Level 4: Runtime evidence** (run when behavior is hard to prove statically)
- Check logs for errors
- Verify expected output format
- Check for console errors (web apps)
- Validate API responses match contracts

### 3. Record validation outcome
For each level attempted:
- **Passed**: record what was verified
- **Failed**: fix the issue, re-validate, record the fix
- **Skipped**: record why (tooling unavailable, not applicable)
- **Gap**: record what could not be validated and why

### 4. Assess confidence
- **High**: all relevant levels passed with concrete evidence
- **Medium**: core tests pass but some levels were skipped
- **Low**: validation was limited — flag this explicitly

### 5. Report
- What was validated and at which level
- Concrete evidence (test output, command results)
- Any gaps or deferred validation
- Confidence assessment

## Mode-specific strategies

### Web app
- Before/after UI state
- Console error check
- Key user journey smoke
- Responsive/accessibility if relevant

### API service
- Request/response validation
- Contract test
- Log/metric check
- Error response format

### Worker / batch
- Fixture replay
- Retry and failure path coverage
- Idempotency verification

### Library / SDK
- Public API behavior tests
- Example code execution
- Type signature verification

## Guardrails

- Never claim "validated" without running actual checks
- Never skip Level 1 if the commands are available
- Report gaps honestly — "not validated" is better than false confidence
- Do not run expensive tests when narrow tests already prove the point
