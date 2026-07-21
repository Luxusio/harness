# Harness Codex routing

@CONTRACTS.md

## Harness routing
<!-- harness:routing-injected -->

- Repository mutation (implementation, fix, refactor, test/config/doc behavior change) → invoke `$harness:run` before editing. If a native Goal is active, the run skill synchronizes it and owns its child task.
- Harness bootstrap or repair → invoke `$harness:setup`.
- Read-only questions, explanations, reviews, and status reports → answer directly without starting a task.
- Hooks provide routing reminders and state only; `task_verify` and `task_close` are authoritative for fresh code-review, conditional security-review, and QA evidence.
