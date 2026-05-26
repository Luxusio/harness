# Autopilot Failure Policy

This policy is the stable reference for `plugin/scripts/autopilot_runner.py`
failure classification. The runner implements these rules deterministically and
stores the result on each slice as `failure_class`, `recommended_action`, and
`retryable`.

## Classes

| failure_class | Retry | Blocking Behavior | Recommended Action |
|---|---|---|---|
| `auth_required` | no | block immediately | Re-authenticate the CLI or service, then run `recover` and restart the loop. |
| `network_unavailable` | yes | retry until max attempts | Restore network access or proxy/DNS settings if retries keep failing. |
| `dependency_missing` | yes | retry until max attempts | Install or restore the missing dependency, then rerun. |
| `test_failure` | yes | retry until max attempts | Feed the failing test output back through `/harness:run` develop. |
| `harness_close_missing` | yes | retry until max attempts | Re-run the slice until its harness task closes with `runtime_verdict: PASS`. |
| `browser_unavailable` | no | block immediately | Install/start browser QA tooling or disable browser scope only with user approval. |
| `port_conflict` | yes | retry until max attempts | Stop the conflicting process or change the declared port. |
| `timeout` | yes | retry until max attempts | Inspect logs and increase timeout only if progress is real. |
| `user_decision_required` | no | block immediately | Ask the user to decide; do not infer product, billing, auth, or architecture choices. |
| `unknown` | yes | retry until max attempts | Inspect the transcript and add a classifier rule if this repeats. |

## Matching Order

Specific blockers win over generic failures:

1. `user_decision_required`
2. `auth_required`
3. `browser_unavailable`
4. `harness_close_missing`
5. `timeout`
6. `port_conflict`
7. `network_unavailable`
8. `dependency_missing`
9. `test_failure`
10. `unknown`

## Rules

- A non-retryable class blocks immediately.
- A retryable class moves to `failed` until `max_attempts`, then blocks.
- `returncode == 0` remains `passed` unless `--require-harness-close` injects
  `HARNESS_CLOSE_REQUIRED`.
- `USER_DECISION_REQUIRED` is always a hard stop. Autopilot must not invent
  missing user/product decisions during long runs.
