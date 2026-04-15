# Browser Verification

This sub-file covers two browser verification phases that run during develop:

1. **Phase 2 baseline screenshot** — capture before-implementation visual state
2. **Phase 3 per-AC verification** — visual + interaction testing after each AC

Both are skipped for non-browser projects (when `browser_qa_supported` is not `true`).

---

## Phase 2: Baseline Screenshot (browser projects only)

If `browser_qa_supported: true` in manifest, capture the current state of affected pages
before any implementation changes. This creates a visual baseline for comparison:

1. Verify dev server is running. If not, start it.
2. For each page that will be modified (identified from PLAN.md target files):
   a. Navigate to the page.
   b. Take screenshot → `<task_dir>/audit/screenshots/baseline-<page-slug>.png`
   c. Take snapshot for element inventory.
3. If the page does not exist yet (new page): skip baseline, note "new page — no baseline."

This baseline enables:
- Per-AC visual verification (Phase 3) to compare before vs after
- qa-browser to detect unintended visual changes outside the task scope
- Design review to verify the implementation matches the intended change

Do NOT modify any files during baseline capture. Read-only.

---

## Phase 3: Per-AC Visual Verification (browser projects only)

If `browser_qa_supported: true` in manifest AND the AC touches UI files
(templates, components, styles, layouts, pages):

```bash
mkdir -p <task_dir>/audit/screenshots
```

1. Verify dev server is running (`curl -s -o /dev/null -w '%{http_code}' <entry_url>`).
   If not running: start `dev_command` (background), wait up to 15s.
2. Navigate to the relevant page for this AC.
3. Take snapshot + screenshot → save to `<task_dir>/audit/screenshots/AC-NNN-after.png`.
4. Verify expected elements exist (from AC description). Check snapshot for:
   - Missing components or empty containers
   - Error overlays or crash screens
   - Broken layout (elements outside viewport)
5. Check console errors via evaluate_script. If critical JS errors: fix immediately.
6. If the AC involves interaction: perform it, wait for response,
   take screenshot → `AC-NNN-after-interaction.png`.

This catches visual/layout issues while implementation context is fresh — before
they compound into expensive QA cycles. Skip for non-UI ACs (data logic, config, backend).

---

## Phase 3: Per-AC Interaction Testing (browser projects, interactive ACs only)

If the AC describes a user interaction (form submit, button click, navigation,
toggle, drag-and-drop, modal open/close), verify the interaction actually works:

1. Identify the interaction elements from the AC description.
2. Find them via take_snapshot (get uid values).
3. Perform the interaction:
   - Form input: `fill(uid, value)` for each field, then `click(submit_uid)`
   - Button click: `click(uid)`
   - Navigation: `click(link_uid)` or `navigate_page(url)`
   - Toggle/checkbox: `click(uid)`
   - Dropdown/select: `fill(uid, value)`
4. `wait_for` the expected result (success message, page change, element appear).
5. Take screenshot → `<task_dir>/audit/screenshots/AC-NNN-interaction.png`.
6. Verify the result matches the AC description:
   - Expected element visible?
   - No error message appeared?
   - Page navigated to correct URL?
7. If interaction fails: fix immediately. Common causes:
   - Missing event handler (onClick not wired)
   - Wrong selector (element uid not found)
   - Validation blocking submit (required field missing)
   - API call failing (check network requests)

This is a smoke test, not exhaustive QA. One happy-path interaction per AC is enough.
Edge cases and error states are qa-browser's job.

Skip for non-interactive ACs (layout changes, styling, static content).
