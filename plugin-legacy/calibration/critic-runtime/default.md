# Calibration: critic-runtime / default

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern A — display-only verification

**Scenario**: Feature adds user preferences that persist across page reloads.
**What was submitted**: Evidence bundle shows the UI renders a preferences panel and toggle switches are interactive. No persistence check performed.
**Why this should FAIL**: The acceptance criteria require data to persist across reloads. Confirming the UI renders is not sufficient — persistence must be independently verified (reload the page, query the DB, or check the API response on a fresh session).
**Correct verdict**: FAIL — persistence not verified; UI rendering alone does not satisfy the acceptance criterion

---

## False PASS pattern B — CLI verification on a browser-first project

**Scenario**: manifest declares `browser.enabled: true`. Feature adds a new dashboard widget.
**What was submitted**: Evidence bundle contains `npm test` results (all passing) and a healthcheck response. No browser interaction performed.
**Why this should FAIL**: Browser-first projects require actual browser verification before falling back to CLI. The critic must attempt `mcp__chrome-devtools` navigation to the UI route. Passing CLI tests does not confirm the widget renders or is interactive in a browser context.
**Correct verdict**: FAIL — browser verification not attempted on a browser-first project; CLI-only evidence is insufficient

---

## Correct judgment example

**Scenario**: API endpoint added; non-browser project; persistence to PostgreSQL required.
**Evidence presented**:
- `npm test` — exit 0, 38 passed
- `curl POST /api/items` — HTTP 201, response body contains new item `id`
- `curl GET /api/items/{id}` — HTTP 200, data matches submitted payload (persistence confirmed)
- `[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL`
**Verdict**: PASS — execution verified (not code-reading), persistence independently confirmed via separate GET request, concrete evidence bundle present with command transcript and request evidence.
