# Evidence Bundle Reference

updated: 2026-03-28

This document specifies the standardized evidence bundle format produced by critic-runtime and the verification scripts.

---

## Purpose

The evidence bundle is a structured record of verification actions and their outcomes. It serves three functions:

1. **Verdict support** — proves the PASS/FAIL verdict is based on execution, not reading
2. **Fix-round reuse** — when a FAIL occurs, the next fix round can read the bundle to understand exactly what failed and reproduce it
3. **Audit trail** — provides a timestamped chain of evidence for task history

---

## Evidence Bundle Format

The bundle is appended to `CRITIC__runtime.md` after the verdict header fields. Each section is optional unless otherwise noted.

```markdown
## Evidence Bundle
### Command Transcript
<summary of commands run and their exit codes — REQUIRED for all verdicts>

### Server/App Log Tail
<last N lines of relevant server or application logs, if available>
<"n/a" if not applicable>

### Browser Console
<console errors and warnings captured during browser QA>
<"n/a" if not a browser QA task>

### Network Requests
<failed or notable HTTP requests captured during browser QA>
<"n/a" if not a browser QA task>

### Healthcheck Results
<full output of healthcheck.sh including [EVIDENCE] lines>
<"skipped" if not run>

### Smoke Test Results
<full output of smoke.sh including [EVIDENCE] lines>
<"skipped" if not run>

### Persistence Check
<full output of persistence-check.sh including [EVIDENCE] lines>
<"skipped" if not applicable>

### Screenshot/Snapshot
<file path to screenshot captured during browser QA, or textual DOM snapshot>
<"n/a" if not browser QA>

### Request Evidence
<request IDs, endpoint URLs, and response bodies for API tasks>
<"n/a" if not an API task>
```

---

## Required vs Optional Evidence

### PASS verdict

| Section | Requirement |
|---------|-------------|
| Command Transcript | **Required** |
| At least one of: Smoke Test Results, Healthcheck Results, Browser Console, Request Evidence | **Required** |
| All other sections | Optional (include when available) |

### FAIL verdict

| Section | Requirement |
|---------|-------------|
| Command Transcript | **Required** |
| Specific failure description | **Required** — name the exact check that failed |
| Repro steps | **Required** — exact commands a fixer can run to reproduce |
| All other sections | Include any that show failure detail |

### BLOCKED_ENV verdict

| Section | Requirement |
|---------|-------------|
| Command Transcript | **Required** — show what was attempted |
| Exact blocker description | **Required** — what environment condition prevents verification |
| All other sections | Include if partially completed before blockage |

---

## Script Output Format

All verification scripts emit `[EVIDENCE]` tagged lines to stdout for easy extraction by the critic agent.

### Format

```
[EVIDENCE] <type>: <PASS|FAIL|SKIP> <target> — <detail>
```

| Field | Description |
|-------|-------------|
| `type` | Script category: `smoke`, `healthcheck`, `browser`, `persistence`, `verify` |
| `PASS/FAIL/SKIP` | Outcome |
| `target` | Endpoint URL, db type, or description of what was checked |
| `detail` | Human-readable summary: exit code, HTTP status, error message, timing |

### Per-script examples

**verify.py**
```
[EVIDENCE] verify: started at 2026-03-28T10:00:00Z
[EVIDENCE] smoke: PASS — exit 0 — Tests: 42 passed
[EVIDENCE] healthcheck: PASS http://localhost:3000/health exit=0 time=45ms
[EVIDENCE] verify: PASS — all checks passed at 2026-03-28T10:00:12Z
```

**smoke.sh**
```
[EVIDENCE] smoke: PASS — exit 0 — last output: Tests: 42 passed, 0 failed
[EVIDENCE] smoke: FAIL — exit 1 — last output: FAILED: auth.test.ts TypeError cannot read...
```

**healthcheck.sh**
```
[EVIDENCE] healthcheck: PASS http://localhost:8080/health exit=0 time=23ms
[EVIDENCE] healthcheck: FAIL http://localhost:8080/health exit=1 time=3002ms — curl: (7) Failed to connect
[EVIDENCE] healthcheck: PASS — skipped (none configured)
```

**browser-smoke.sh**
```
[EVIDENCE] browser: PASS http://localhost:3000 — server reachable, console_errors=0, network_failures=0
[EVIDENCE] browser: FAIL http://localhost:3000 — server not reachable after 10 attempts, last HTTP 000
```

**persistence-check.sh**
```
[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL
[EVIDENCE] persistence: FAIL mongodb — ping failed (MONGODB_URI set but mongosh returned error)
[EVIDENCE] persistence: SKIP none — no DATABASE_URL or MONGODB_URI set
```

---

## Feedback into Fix Rounds

When a FAIL verdict is issued, the evidence bundle feeds directly into the next fix round:

1. **Developer reads** `CRITIC__runtime.md` — verdict header + evidence bundle
2. **Command Transcript** shows which commands failed and their exit codes
3. **Smoke/Healthcheck/Persistence sections** show exact failure output
4. **Repro steps** let the developer reproduce locally before fixing
5. After fix, critic-runtime runs a fresh verification cycle — the previous bundle is archived in `QA__runtime.md`

The evidence bundle should be specific enough that a fixer can reproduce the failure without reading any other file.

---

## Performance Comparison (conditional)

When the task is a performance task (`performance_task: true` or `performance` overlay selected), the evidence bundle MUST include an additional section:

```markdown
### Performance Comparison
- baseline: <before measurements — numeric, with units>
- after: <after measurements — numeric, with units>
- delta: <change — numeric, with percentage or absolute>
- workload parity: same | different
- guardrail status: pass | fail
```

**Required for:** Any task with `performance_task: true` or `review_overlays` containing `performance`.

**Not required for:** Normal feature, bugfix, docs, or refactor tasks without performance signals.

### Performance Comparison verdict rules

| Condition | Verdict |
|-----------|---------|
| Numeric before/after present, target improved, guardrails pass | PASS |
| Qualitative claims only ("faster", "improved") without numbers | FAIL |
| No baseline recorded | FAIL |
| Workload changed without explanation | FAIL |
| Target metric regressed unexplained | FAIL |
| Benchmark not reproducible | FAIL |

---

## Complete Example: PASS verdict

```markdown
verdict: PASS
task_id: TASK__add-rate-limiting
evidence: Smoke tests pass (42/42). Health probe returns 200. Rate limit endpoint returns 429 with Retry-After header as verified via curl.
repro_steps: npm test && curl -X POST http://localhost:3000/api/data -H "Authorization: Bearer $TOKEN" (repeat 11 times to trigger limit)
unmet_acceptance: none
blockers: none

## Evidence Bundle
### Command Transcript
1. npm test — exit 0 (42 passed, 0 failed)
2. curl http://localhost:3000/health — exit 0, HTTP 200
3. curl -X POST http://localhost:3000/api/data (x11) — first 10: HTTP 200, 11th: HTTP 429

### Server/App Log Tail
[2026-03-28T10:01:45Z] Rate limit exceeded for user:123 — returning 429

### Browser Console
n/a

### Network Requests
n/a

### Healthcheck Results
=== HEALTH CHECKS ===
Running: curl -sf http://localhost:3000/health
{"status":"ok"}
[EVIDENCE] healthcheck: PASS http://localhost:3000/health exit=0 time=18ms

### Smoke Test Results
=== SMOKE TESTS ===
Running: npm test
Test Suites: 3 passed, 3 total
Tests: 42 passed, 42 total
[EVIDENCE] smoke: PASS — exit 0 — last output: Tests: 42 passed, 42 total

### Persistence Check
[EVIDENCE] persistence: PASS postgresql — connected via DATABASE_URL

### Screenshot/Snapshot
n/a

### Request Evidence
POST /api/data attempt 11: HTTP 429, body: {"error":"rate_limit_exceeded","retry_after":60}
Retry-After header: 60
```

---

## Complete Example: FAIL verdict

```markdown
verdict: FAIL
task_id: TASK__add-rate-limiting
evidence: Smoke tests fail — rate limit test returns HTTP 200 on 11th request instead of 429.
repro_steps: npm test -- --grep "rate limit" OR curl -X POST http://localhost:3000/api/data (x11)
unmet_acceptance:
  - Rate limit endpoint must return HTTP 429 after 10 requests per minute
blockers: none

## Evidence Bundle
### Command Transcript
1. npm test — exit 1 (41 passed, 1 failed)
2. curl http://localhost:3000/health — exit 0, HTTP 200

### Server/App Log Tail
[2026-03-28T10:05:12Z] Request 11 processed — rate limiter not triggered

### Browser Console
n/a

### Network Requests
n/a

### Healthcheck Results
[EVIDENCE] healthcheck: PASS http://localhost:3000/health exit=0 time=22ms

### Smoke Test Results
=== SMOKE TESTS ===
Running: npm test
FAIL tests/rate-limit.test.ts
  ● returns 429 after 10 requests
    Expected: 429
    Received: 200
[EVIDENCE] smoke: FAIL — exit 1 — last output: Tests: 41 passed, 1 failed

### Persistence Check
[EVIDENCE] persistence: SKIP none — no DATABASE_URL or MONGODB_URI set

### Screenshot/Snapshot
n/a

### Request Evidence
POST /api/data attempt 11: HTTP 200 (expected 429)
```
