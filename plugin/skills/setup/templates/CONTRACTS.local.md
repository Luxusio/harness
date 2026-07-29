# Project-specific contracts

This file is yours except for C-100, which setup owns and reapplies
idempotently. Add contracts numbered C-101 and above to keep clear of both the
managed block in `CONTRACTS.md` and the setup-owned scope rule below.

Use the same four-field structure so `contract_lint.py` can validate them:

### C-100

**Title:** 말하지 않은 범위도 멋대로 수정하는 것
**When:** A task would modify behavior, files, systems, or data outside the user's stated or approved scope.
**Enforced by:** Harness plan scope lock, develop scope checks, and independent review.
**On violation:** hard-block until the user explicitly approves the expanded scope.
**Why:** Unrequested changes reduce trust and make otherwise useful automation costly to review.
