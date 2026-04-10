# Calibration: critic-intent / default

## Bias

PASS-biased. Only FAIL for clear missing must-haves explicitly stated in REQUEST.

## Blocker criteria (FAIL)

A finding is a **blocker** when ALL of the following are true:
1. The missing item is explicitly mentioned in REQUEST.md (not inferred)
2. The item is a core function/flow (not a style preference or enhancement)
3. The item is absent from HANDOFF.md evidence or CRITIC__runtime.md evidence
4. The item is not explicitly marked out-of-scope in PLAN.md

## Opportunity criteria (note, not FAIL)

A finding is an **opportunity** when:
- The item is NOT explicitly stated in REQUEST.md
- It would improve the result but the REQUEST doesn't require it
- It is a future feature, UX polish, or edge case not described in REQUEST

## Common false-FAIL patterns to avoid

- Failing because PLAN.md didn't include something (if REQUEST didn't mention it either)
- Failing because a feature "could be better" (opportunity, not blocker)
- Failing because tests are sparse (runtime critic's job)
- Failing because docs are incomplete (document critic's job)
- Failing because the implementation style differs from expectation

## PASS with opportunities example

```
verdict: PASS
summary: All must-have items from REQUEST covered. Two improvements noted as opportunities.
blockers: []
opportunities:
  - "Error message on invalid input not shown (not in REQUEST, suggested for UX)"
  - "No rate limiting on API (out of stated scope)"
```

## FAIL example

```
verdict: FAIL
summary: REQUEST explicitly requires X but X is not implemented.
blockers:
  - "REQUEST states 'must handle Y flow' — no evidence of Y in HANDOFF or runtime"
opportunities: []
```
