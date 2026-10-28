# Harness Codex routing

@CONTRACTS.md

## Harness routing
<!-- harness:routing-injected -->

- Repository mutation (implementation, fix, refactor, test/config/doc behavior change) → invoke `$harness:run` before editing. If a native Goal is active, the run skill opens/resumes its same WBS root.
- Harness bootstrap or repair → invoke `$harness:setup`.
- Read-only questions, explanations, reviews, and status reports → answer directly without starting a task.
- Hooks provide routing reminders and state only; WBS verify/close enforce current outcome checks. The workflow awaits actual independent code-review and conditional security-review PASS before actual QA; current QA/UX FAIL or BLOCKED_ENV takes precedence over machine PASS.
- Changes to managed contracts, Harness skills, or `contract_lint.py` must keep the real-tree contract and skill-weight tests passing.
