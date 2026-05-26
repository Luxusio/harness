# REQ - Process Autopilot Quality Gates

## Intent
Define the observable requirements for autopilot Agile quality gates so future changes preserve the intended preflight-first flow.

## Observable Behavior
- Autopilot review must record deterministic review quality fields: review_quality, quality_warnings, and quality_blockers. Autopilot preflight must report PASS, WARN, or BLOCK before unattended execution. Warning-only findings must be surfaced as guidance and continue by default. Strict mode via --require-review-before-next must block completed slices with no iteration review and completed slices whose latest review records quality blockers. Quality blockers include known failed or blocked evidence such as failed demo, blocked user workflow, QA FAIL, or QA BLOCKED_ENV. The gate should help the agent detect and avoid bad next steps before colliding with harness close gates.

## Acceptance Signals
- Autopilot review must record deterministic review quality fields: review_quality, quality_warnings, and quality_blockers. Autopilot preflight must report PASS, WARN, or BLOCK before unattended execution. Warning-only findings must be surfaced as guidance and continue by default. Strict mode via --require-review-before-next must block completed slices with no iteration review and completed slices whose latest review records quality blockers. Quality blockers include known failed or blocked evidence such as failed demo, blocked user workflow, QA FAIL, or QA BLOCKED_ENV. The gate should help the agent detect and avoid bad next steps before colliding with harness close gates.

## Verification Cues
- Verify with tests/test_autopilot_runner.py for review quality persistence, preflight WARN/BLOCK output, missing-review strict blocking, warning-only continuation, and recorded quality blocker blocking. Verify skill/documentation sync with tests/test_autopilot_skill.py and doc/harness/patterns/autopilot-agile-loop.md.

## Non-Goals
- This requirement does not define the full autopilot product-planning workflow, the failure classifier policy, or user research/product-market validation. It does not require warnings to block default execution.

## Source
- created: 2026-05-26
- source: task: TASK__add-autopilot-quality-gates-req-doc
