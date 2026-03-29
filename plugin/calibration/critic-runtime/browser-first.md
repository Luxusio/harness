# Calibration: critic-runtime / browser-first

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern A — health endpoint only

**Scenario**: New checkout flow added to a browser-first e-commerce app.
**What was submitted**: Evidence shows `curl http://localhost:3000/health` returned HTTP 200. No browser interaction performed.
**Why this should FAIL**: A health probe only confirms the server is running. Browser-first QA requires actual browser navigation to the UI route, interaction with the feature, and observation of the `expected_dom_signal`. Confirming the server responds to a health endpoint does not verify the checkout flow exists or functions.
**Correct verdict**: FAIL — browser not opened; health-only verification is insufficient for browser-first QA

---

## False PASS pattern B — browser console errors ignored

**Scenario**: Feature adds a data visualization chart to a dashboard.
**What was submitted**: Browser was opened, chart rendered visually, screenshot captured. Browser console shows `TypeError: Cannot read properties of undefined (reading 'map')` and `Failed to load resource: 404`.
**Why this should FAIL**: Console errors indicate the feature has runtime JavaScript errors and a missing resource. These are not cosmetic — they signal broken behavior that may affect functionality under different data conditions. Console errors must be investigated and resolved, not ignored.
**Correct verdict**: FAIL — browser console errors present (TypeError + 404); errors must be resolved before PASS

---

## Correct judgment example

**Scenario**: New user profile settings page added; browser-first project.
**Evidence presented**:
- Server started on port 3000
- `[EVIDENCE] healthcheck: PASS http://localhost:3000/health exit=0 time=22ms`
- Browser navigated to `/settings/profile`; form fields rendered; updated display name; save button clicked
- DOM signal observed: `<div data-testid="save-success">Profile saved</div>` present after submission
- Browser console: 0 errors, 0 warnings
- Network: POST `/api/user/profile` returned HTTP 200
**Verdict**: PASS — server started, health probe passed, browser opened and navigated to correct route, feature interaction performed, expected DOM signal confirmed, no console errors, persistence via API response verified.
