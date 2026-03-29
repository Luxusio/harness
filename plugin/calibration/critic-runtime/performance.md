# Calibration: critic-runtime / performance

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern

**Scenario**: Task claims to reduce p99 API latency by adding a cache layer.
**What was submitted**: Evidence bundle states "performance improved significantly — the endpoint now responds much faster with cache hits." No baseline measurement. No after measurement. No benchmark command recorded.
**Why this should FAIL**: Qualitative claims ("much faster", "significantly improved") are explicitly insufficient for performance tasks. A numeric baseline and numeric after measurement are required. Without the benchmark command, the result is not reproducible. Guardrails (e.g., p99 must stay below 200ms) cannot be verified without numbers.
**Correct verdict**: FAIL — no numeric baseline; no numeric after measurement; no benchmark command; qualitative-only claim

---

## Correct judgment example

**Scenario**: Cache layer added to `/api/products` endpoint; performance_task: true.
**Evidence presented**:
```
### Performance Comparison
- baseline: p99 = 420ms (50 RPS, k6 run against staging, 2026-03-28T09:00Z)
- after: p99 = 85ms (50 RPS, same k6 script, 2026-03-28T10:30Z)
- delta: -335ms (-79.8%)
- workload parity: same (identical k6 script, same RPS, same dataset)
- guardrail status: pass (p99 < 200ms threshold met; error rate 0.1% → 0.0%)
```
**Verdict**: PASS — numeric baseline and after measurements present with units, same benchmark command used for both runs (workload parity demonstrated), target metric improved, guardrail threshold met, no unexplained regressions.
