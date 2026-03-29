# Calibration: critic-plan / sprinted

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern

**Scenario**: Sprinted-mode plan for a new billing module spanning app + api + db surfaces.
**What was submitted**: Sprint contract present (surfaces listed). Risk matrix has two rows: "DB migration fails — High — revert commit" and "API errors — Medium — check logs." Rollback steps say "revert commit and redeploy." Dependency graph is absent.
**Why this should FAIL**: Risk matrix entries have no mitigation beyond "revert commit" — that is a generic response, not a mitigation strategy. Rollback steps are vague ("revert commit and redeploy" does not specify which migrations to roll back, in what order, or how to handle data already written). Dependency graph is missing entirely for a 3-surface change.
**Correct verdict**: FAIL — rollback steps are generic (not ordered, not specific to destructive DB migration); dependency graph missing for multi-surface change

---

## Correct judgment example

**Scenario**: Sprinted-mode plan for adding OAuth2 provider integration touching auth service, user DB schema, and frontend login page.
**Evidence presented**:
- Sprint contract: surfaces = [app/auth, api/users, db/schema], roots = [src/auth, api, db/migrations], rollback trigger = "OAuth callback returns 500 on >5% of requests", staged delivery = false
- Risk matrix: 3 rows with likelihood/impact/mitigation each (e.g., "Schema migration fails — High — Run `npm run db:rollback` targeting migration `add_oauth_provider_id` specifically")
- Rollback steps: 5 ordered steps including `npm run db:rollback -- --target add_oauth_provider_id`, `git revert <sha-range>`, cache flush command
- Dependency graph: OAuth provider → auth service → user table (FK constraint) → frontend session token
**Verdict**: PASS — sprint contract complete, risk matrix has real mitigations per risk, rollback steps are specific and ordered for the destructive DB operation, dependency graph present and accurate.
