# Observability Investigation Playbook

Use this playbook when you need to investigate runtime issues using the observability stack (Grafana, Prometheus, Loki, Tempo).

---

## When to Use Observability vs Plain Log Reading

| Use observability | Use plain logs |
|---|---|
| Production incident with live traffic | Local dev debugging |
| Cross-service latency regression | Single-service logic error |
| Intermittent errors (hard to reproduce) | Build/startup failures |
| Resource exhaustion (memory, connections) | Test failures |
| Correlating metrics + logs + traces | Quick grep for known message |

---

## Starting the Stack

```bash
# Start observability services (no impact on app services)
docker compose -f docker-compose.yml -f docker-compose.observability.yml --profile observability up -d

# Verify stack is running
python3 plugin/scripts/observability.py status

# Open Grafana
open http://localhost:3100
```

Default credentials: anonymous access enabled, admin/admin for admin panel.

---

## Log Correlation Across Services

1. Open Grafana → Explore → select **Loki** datasource
2. Query all app logs: `{job="app"}`
3. Filter to a time window matching the incident
4. Add label filters: `{job="app", service="api"}` for service isolation
5. Use `|= "request_id"` to filter by a specific trace/request ID
6. Switch to **Tempo** datasource, paste the trace ID from a log line to jump to the full trace

**Tip:** Log lines should include `trace_id` field. If they do not, add structured logging to your app.

---

## Trace Analysis

1. Grafana → Explore → **Tempo**
2. Search by service name or use TraceQL: `{ .http.status_code = 500 }`
3. Click a trace to expand the span waterfall
4. Look for:
   - Spans with long gaps (waiting on I/O or locks)
   - Error spans (red) — click for attributes and events
   - DB spans with repeated patterns (N+1 queries)
5. Use "Logs for this span" button to jump to correlated Loki logs

---

## Metric Correlation

Open Grafana → **App Overview** dashboard for the summary view, or use Explore for ad-hoc queries.

| What to check | PromQL |
|---|---|
| Request rate | `rate(app_http_requests_total[5m])` |
| Error rate | `rate(app_http_requests_total{status=~"5.."}[5m])` |
| Latency p50 | `histogram_quantile(0.50, rate(app_http_request_duration_seconds_bucket[5m]))` |
| Latency p99 | `histogram_quantile(0.99, rate(app_http_request_duration_seconds_bucket[5m]))` |
| Memory usage | `process_resident_memory_bytes` |

---

## Common Patterns

### 5xx Diagnosis

1. Check error rate panel — identify spike window
2. Loki: `{job="app"} |= "error" | logfmt | level="error"`
3. Look for stack traces or repeated error messages
4. Grab a `trace_id` from log line, open in Tempo
5. Find the failing span, check its attributes for root cause

### Latency Regression

1. Compare p50 vs p99 — wide gap means tail latency issue
2. Tempo: search for traces with duration > threshold
3. Compare span durations before and after the regression window
4. Check for slow DB queries or external HTTP calls in trace spans

### Pool Exhaustion

1. Loki: `{job="app"} |= "pool" |= "exhaust"` or `|= "timeout"`
2. Check metric: `app_db_pool_checked_out / app_db_pool_size`
3. Trace: find requests with long waits before first DB span
4. Remedy: increase pool size or reduce query duration

---

## Fallback When Stack Not Available

If Docker is unavailable or the project is not suitable for observability:

```bash
# Plain log tail
tail -f logs/app.log

# Grep for errors
grep -E "ERROR|FATAL|panic" logs/app.log | tail -50

# Check process health
ps aux | grep <app-name>
```

Use `plugin/scripts/observability.py detect` to check readiness before attempting to start the stack.
