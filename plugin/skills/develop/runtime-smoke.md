# Phase 3.9: Runtime Smoke (all project types)

Verify the app actually works before the expensive quality audit pipeline.
Each project type has its own smoke test. All projects run this phase.

## Runtime Services Prelude

Before browser or API smoke, start declared background services:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runtime_services.py start
PYTHONDONTWRITEBYTECODE=1 python3 ${CLAUDE_PLUGIN_ROOT}/scripts/runtime_services.py status
```

The script reads `doc/harness/manifest.yaml` `runtime.services[]`, writes state
to `doc/harness/runtime/services.json`, writes logs under
`doc/harness/runtime/logs/`, waits for healthchecks, and runs bounded
self-healing commands when configured. If a required service remains blocked,
record the exact service, `health_detail`, `failure_class`, `recommended_action`,
and `last_log_excerpt` in the QA transcript instead of silently downgrading live
smoke. Treat `missing_dependency`, `port_conflict`, `missing_env`, and
`migration_or_seed` as actionable setup failures before deciding `BLOCKED_ENV`.

**Browser projects (`browser_qa_supported: true`):**

```bash
# Check if dev server is running
curl -s -o /dev/null -w '%{http_code}' <entry_url> 2>/dev/null || echo "NO_SERVER"
```

If NO_SERVER: prefer `runtime_services.py start <service>` for the frontend
service declared in `runtime.services[]`. Fall back to `dev_command`
(background, wait up to 15s) only when no runtime service is declared.

Steps:
1. Navigate to entry_url. Verify page loads (HTTP 200).
2. Take screenshot — check for blank page, error overlay, or crash screen.
3. Run `evaluate_script` to check DOM health:
   ```javascript
   () => {
     return {
       elementCount: document.querySelectorAll('*').length,
       bodyText: document.body?.innerText?.substring(0, 200) || 'EMPTY'
     }
   }
   ```
   If element count is suspiciously low (< 5), the page likely failed to render.
4. Check for critical console errors:
   ```javascript
   () => {
     return performance.getEntriesByType('navigation').map(e => ({
       type: e.type, duration: Math.round(e.duration), status: e.responseStatus
     }))
   }
   ```
5. If page fails to load or has critical JS errors: fix immediately.
   This is always T1 (our code broke it).
6. **Network request audit:** Check for failed or slow requests:
   ```
   list_network_requests → filter for status >= 400 or resourceType in (xhr, fetch)
   ```
   Flag: 404 (missing asset/route), 5xx (server error), CORS errors.
   If any request fails: investigate — likely a missing route or broken import.
7. **Performance baseline capture:** record navigation timing in the phase
   result for later comparison. Use `performance_start_trace` with
   `autoStop: true, reload: true` when available, then extract key metrics via
   evaluate_script:
   ```javascript
   () => {
     const [nav] = performance.getEntriesByType('navigation');
     return {
       DOMContentLoaded: Math.round(nav.domContentLoadedEventEnd - nav.startTime),
       Load: Math.round(nav.loadEventEnd - nav.startTime),
       TransferSize: nav.transferSize,
       LCP: performance.getEntriesByType('largest-contentful-paint').pop()?.startTime || 'N/A'
     }
   }
   ```
   Include these metrics in the phase result. qa-browser can compare against
   them after implementation when the values remain in context.
8. **Keep dev server running** — Phase 4.5 visual smoke agent will reuse it.

**API projects (`project_type: api` or API endpoints in diff):**

```bash
# Check if API server is running
_API_URL=$(grep "^api_base_url:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
[ -z "$_API_URL" ] && _API_URL="http://localhost:3000"

curl -s -o /dev/null -w '%{http_code}' "$_API_URL" 2>/dev/null || echo "NO_SERVER"
```

If NO_SERVER: prefer `runtime_services.py start <service>` for the API service
declared in `runtime.services[]`. Fall back to the legacy API start command
only when no runtime service is declared.

Steps:
1. **Health check:** Hit a known endpoint (e.g., `/health`, `/api/status`, `/`).
   ```bash
   curl -s -w '\nHTTP %{http_code}\n' "$_API_URL/health" 2>/dev/null || \
   curl -s -w '\nHTTP %{http_code}\n' "$_API_URL/" 2>/dev/null
   ```
   If HTTP 5xx or connection refused: fix immediately. T1 (our code).

2. **Endpoint discovery:** For each endpoint added/modified by this task's ACs:
   ```bash
   # Extract routes from diff
   git diff --name-only HEAD | grep -E "(route|controller|handler|api|endpoint)" || echo "NO_ROUTE_FILES"
   ```
   For each endpoint:
   - GET: `curl -s -w '\nHTTP %{http_code}' <url>`
   - POST: `curl -s -X POST -H 'Content-Type: application/json' -d '<sample_body>' -w '\nHTTP %{http_code}' <url>`
   - Verify: HTTP status is expected (200, 201, 400, 404 — not 500).

3. **Response validation:** For each endpoint hit:
   - Response is valid JSON (if API returns JSON): `curl ... | python3 -m json.tool`
   - Response matches expected schema (fields exist, types correct)
   - No internal error details leaked in response body

4. **Report API smoke results:** summarize endpoints tested, passed count, and
   any failing URL/status in the phase result.

**CLI projects (`project_type: cli` or `project_type: library`):**

Choose each command from its documented semantics before executing it. Use only
documented non-mutating help, version or dry-run options; an unsupported option
is a failed smoke check, never permission to retry the live command. Commands
that mutate state require explicitly authorized disposable fixtures, with every
resource/path directed there. Never run destructive smoke against live user
resources. If no safe invocation is available, report the concrete blocker.

To limit displayed output while preserving the original command exit status,
capture the complete output before displaying it. Piping a running command to
`head` can induce SIGPIPE even with `pipefail`; a plain pipeline can hide failure.
Define this shell helper for the examples below:

```bash
smoke_capture() (
  _SMOKE_LINES=$1
  shift
  _SMOKE_OUTPUT=$(mktemp) || exit $?
  trap 'rm -f -- "$_SMOKE_OUTPUT"' EXIT
  if "$@" >"$_SMOKE_OUTPUT" 2>&1; then
    _SMOKE_STATUS=0
  else
    _SMOKE_STATUS=$?
  fi
  head -n "$_SMOKE_LINES" "$_SMOKE_OUTPUT"
  exit "$_SMOKE_STATUS"
)
```

Steps:
1. **Build check** (if not already done in Phase 3.8):
   ```bash
   smoke_capture 5 <build_command>
   ```
   If build fails: fix immediately. This is Phase 3.8 repeated, skip if already passed.

2. **Binary smoke test:** Run the built command with `--help` or `--version`:
   ```bash
   smoke_capture 10 <binary_path> --help || exit $?
   smoke_capture 10 <binary_path> --version
   ```
   If exit code != 0: binary doesn't start. Fix immediately.

3. **Basic safe command:** For each command added/modified by this task's ACs,
   select the documented safe arguments (including `--dry-run` only when its
   non-mutating semantics are documented), or use an authorized disposable fixture:
   ```bash
   smoke_capture 20 <binary_path> <documented_safe_args>
   ```
   Verify: command runs without crash, output is sensible, and its original exit
   status is zero. A nonzero result stops this check without a live fallback.

4. **Report CLI smoke results:** summarize commands tested, passed count, and
   any failing command/exit code in the phase result.

Report: "Runtime smoke: PASS (<type>: <N> checks)" or "Runtime smoke: FAIL (<details>)".
