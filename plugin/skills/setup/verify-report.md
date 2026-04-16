# Phase 4: Verify & Report

Sub-file for setup/SKILL.md.

---

## 4.1 Verify created files

```bash
echo "--- SETUP VERIFICATION ---"
[ -f doc/harness/manifest.yaml ] && echo "manifest.yaml: OK" || echo "manifest.yaml: MISSING"
[ -f CLAUDE.md ] && echo "CLAUDE.md: OK" || echo "CLAUDE.md: MISSING"
grep -q "harness:routing-injected" CLAUDE.md 2>/dev/null && echo "  Harness routing block: present" || echo "  Harness routing block: MISSING — run setup routing-injection"
[ -d doc/harness ] && echo "doc/harness/: OK" || echo "doc/harness/: MISSING"
[ -f doc/harness/critics/plan.md ] && echo "critics/plan.md: OK" || echo "critics/plan.md: MISSING"
[ -f doc/harness/critics/runtime.md ] && echo "critics/runtime.md: OK" || echo "critics/runtime.md: MISSING"
[ -f doc/harness/critics/document.md ] && echo "critics/document.md: OK" || echo "critics/document.md: MISSING"
```

### Runtime deps (test runner)

For library/CLI projects with a pytest-based `test_command`, confirm pytest is importable. Setup should surface a missing test runner here rather than letting future verify gates FAIL cryptically.

```bash
_TEST_CMD=$(grep -E "^test_command:" doc/harness/manifest.yaml 2>/dev/null | cut -d'"' -f2)
if echo "$_TEST_CMD" | grep -q "pytest"; then
  if ! python3 -m pytest --version >/dev/null 2>&1; then
    echo "  pytest: MISSING — install via one of:"
    echo "    pip install --user pytest"
    echo "    pip install --user --break-system-packages pytest   # if PEP 668 blocks"
    echo "    pipx install pytest"
    echo "  Note: test_command is '$_TEST_CMD' but pytest is not importable. verify gate will FAIL."
  else
    echo "  pytest: $(python3 -m pytest --version 2>&1 | head -1)"
  fi
fi
```

## 4.2 QA infrastructure verification

```bash
_PROJECT_TYPE=$(grep "^project_type:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
_BROWSER_QA=$(grep "^browser_qa_supported:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')

if [ "$_BROWSER_QA" = "true" ]; then
  echo "QA Strategy: browser"

  _BROWSER_BIN=$(which chromium 2>/dev/null || which google-chrome 2>/dev/null || which chromium-browser 2>/dev/null)
  [ -n "$_BROWSER_BIN" ] && echo "  Browser: OK ($_BROWSER_BIN)" || echo "  Browser: MISSING — install Chromium or Chrome"

  if grep -q "chrome-devtools" .mcp.json 2>/dev/null; then
    echo "  Chrome MCP: OK (.mcp.json)"
  elif [ -f ~/.claude/mcp.json ] && grep -q "chrome-devtools" ~/.claude/mcp.json 2>/dev/null; then
    echo "  Chrome MCP: OK (global)"
  else
    echo "  Chrome MCP: MISSING"
  fi

  _DEV_CMD=$(grep "^dev_command:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
  [ -n "$_DEV_CMD" ] && [ "$_DEV_CMD" != "null" ] && echo "  Dev command: OK ($_DEV_CMD)" || echo "  Dev command: MISSING"

  _ENTRY_URL=$(grep "^entry_url:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
  [ -n "$_ENTRY_URL" ] && [ "$_ENTRY_URL" != "null" ] && echo "  Entry URL: OK ($_ENTRY_URL)" || echo "  Entry URL: MISSING"

elif [ "$_PROJECT_TYPE" = "api" ]; then
  echo "QA Strategy: API"
  which curl 2>/dev/null && echo "  HTTP client: OK (curl)" || echo "  HTTP client: MISSING"
else
  echo "QA Strategy: CLI/tests only"
fi
```

**Failure reporting:** every gap gets a fix action.
```
QA INFRASTRUCTURE ISSUES:
  - Browser binary: MISSING
    FIX: sudo apt install chromium-browser  /  brew install chromium
  - Chrome MCP: MISSING
    FIX: Re-run setup and select "Add Chrome DevTools MCP to .mcp.json"
  - Dev command: MISSING
    FIX: Add "dev_command: npm run dev" to doc/harness/manifest.yaml
```

Never silently continue. Offer auto-fix via AskUserQuestion when possible (MCP config, manifest fields).

## 4.3 Completion report

**MCP change notice (always surface when .mcp.json was modified):**
```
I updated .mcp.json. You need to restart Claude Code for the Chrome DevTools
MCP server to load. Run /exit and start a new session.
```
Do NOT skip. Without restart, browser QA silently fails.

### Report format

```
STATUS: DONE

harness is set up for {project}.

Created:
  - doc/harness/manifest.yaml — {project_type}, harness v2
  - doc/harness/critics/ — plan, runtime, document playbooks
  - doc/harness/ — harness state directory
  - CLAUDE.md — {created|updated} with harness section
  {If MCP was added: "  - .mcp.json — Chrome DevTools MCP configured"}

QA Strategy: {browser|api|cli|tests_only}
  {browser: "Browser QA enabled. Dev server: {dev_command} → {entry_url}"}
  {api: "API QA enabled."}
  {cli: "CLI QA enabled."}
  {tests_only: "Tests only — no runtime QA configured."}

QA Infrastructure: {all checks passed | ISSUES (see below)}
```

### Next-step branches

All checks passed:
```
You're ready. Try: "I want to build [feature]" or "there's a bug in [area]".
```

MCP config changed:
```
ACTION REQUIRED: Restart Claude Code.
  1. /exit
  2. Start a new session
  3. Come back and start building
```

Browser binary missing:
```
ACTION REQUIRED: Install Chrome/Chromium.
  1. sudo apt install chromium-browser (Linux) / brew install chromium (macOS)
  2. Restart Claude Code
  3. Run "setup harness" again
```

dev_command or entry_url missing:
```
ACTION REQUIRED: Browser QA needs dev server config.
  Edit doc/harness/manifest.yaml:
    dev_command: {suggested}
    entry_url: {suggested}
  Then run "setup harness" to verify.
```

Multiple issues:
```
ACTION REQUIRED: Fix these before browser QA works:
  1. [issue with fix]
  2. [issue with fix]
  After fixing, run "setup harness" to verify.
```

### Smoke test offer (optional, browser projects with all checks passing)

```
Want me to verify browser QA works right now?
  A) Yes — spin up dev server, take a screenshot
  B) No — I'll trust the setup
```

If A: run `dev_command` in background, wait for `entry_url`, screenshot via Chrome DevTools MCP. Show user. Success → "Browser QA verified. You're ready." Failure → specific error + fix instructions.

### Missing-file path

```
STATUS: DONE_WITH_CONCERNS

Created: [list]
Missing: [list]

CONCERNS:
  - {file}: {what went wrong}

RECOMMENDATION: {what to try}
```
