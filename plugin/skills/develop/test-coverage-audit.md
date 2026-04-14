# Test Coverage Audit

Loaded by `develop/SKILL.md` Phase 4.5.
Traces every changed codepath, maps test coverage, generates tests for gaps.

## 1. Detect test framework

```bash
# Check CLAUDE.md first
[ -f CLAUDE.md ] && grep -A2 "## Testing" CLAUDE.md

# Auto-detect
[ -f Gemfile ] && echo "RUNTIME:ruby"
[ -f package.json ] && echo "RUNTIME:node"
[ -f requirements.txt ] || [ -f pyproject.toml ] && echo "RUNTIME:python"
[ -f go.mod ] && echo "RUNTIME:go"
[ -f Cargo.toml ] && echo "RUNTIME:rust"
ls jest.config.* vitest.config.* playwright.config.* .rspec pytest.ini phpunit.xml 2>/dev/null
ls -d test/ tests/ spec/ __tests__/ cypress/ e2e/ 2>/dev/null
```

If framework found: read 2-3 existing test files to learn conventions (naming, imports, assertion style, setup patterns).
If no framework: skip test generation. Note in HANDOFF.md: "No test framework detected — coverage audit ran without test generation."

## 2. Trace every changed codepath

Read the diff and every changed file in full. For each file, trace data flow:

1. **Entry points**: route handlers, exported functions, event listeners, component renders.
2. **Data flow**: input source → transforms → output destination.
3. **Branches**: every if/else, switch, ternary, guard clause, early return.
4. **Error paths**: try/catch, error boundaries, fallbacks.
5. **Edges**: null input, empty array, invalid type, concurrent access.

## 3. Map user flows

For each changed feature, map real user interactions:

- **Happy path**: full journey from action to result.
- **Error states**: what the user sees when something fails. Clear message or silent failure?
- **Edge interactions**: double-click, stale data, slow connection, concurrent tabs.
- **Boundary states**: zero results, max input, single character.

## 4. Check existing coverage

For each codepath and user flow, search for an existing test:

Quality scoring:
- **★★★** Tests behavior with edge cases AND error paths
- **★★** Tests correct behavior, happy path only
- **★** Smoke test / trivial assertion ("it renders", "it doesn't throw")

## 5. Build ASCII coverage diagram

```
CODE PATH COVERAGE
===========================
[+] src/services/billing.ts
    │
    ├── processPayment()
    │   ├── [★★★ TESTED] Happy + declined + timeout — billing.test.ts:42
    │   ├── [GAP]         Network timeout — NO TEST
    │   └── [GAP]         Invalid currency — NO TEST
    │
    └── refundPayment()
        ├── [★★  TESTED] Full refund — billing.test.ts:89
        └── [★   TESTED] Partial refund (non-throw only) — billing.test.ts:101

USER FLOW COVERAGE
===========================
[+] Payment checkout flow
    │
    ├── [★★★ TESTED] Complete purchase — checkout.e2e.ts:15
    ├── [GAP]         Double-click submit — NO TEST
    └── [★   TESTED]  Form validation (render only) — checkout.test.ts:40

─────────────────────────────────
COVERAGE: 5/9 paths tested (56%)
GAPS: 4 paths need tests
─────────────────────────────────
```

Fast path: all paths covered → "Test coverage: all paths covered ✓". Skip generation.

## 6. Regression rule (mandatory)

When the diff modifies existing behavior (not new code) and no test covers the changed path:
1. Write a regression test immediately. No exceptions.
2. Commit as `test: regression test for {what changed}`.

Regressions are highest priority — they prove something broke.

## 7. Generate tests for gaps

For uncovered paths:
1. Prioritize error handlers and edge cases first.
2. Match existing test conventions exactly.
3. Mock all external dependencies.
4. Run each test. Passes → commit as `test: coverage for {feature}`.
5. Fails → fix once. Still fails → revert, note gap in diagram.

Include the final coverage diagram in HANDOFF.md under the "Test Coverage" section.
