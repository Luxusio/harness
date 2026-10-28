# plugin/scripts/

Minimal harness scripts. Self-contained — no plugin-legacy dependency.

## Files

- `_lib.py` — shared YAML, scaffold, routing, context, task-control transactions, session/source ownership and path helpers
- `wbs.py` — measured outcome assertions, progress and work-pack integration checks
- `verification_target.py` — copied source views and declared input applicability for verification and stable installation
- `note_freshness.py` — marks explicitly selected invalidated notes suspect
- `prompt_memory.py` — UserPromptSubmit context from stored task/WBS state, Goal routing and runbook reminders
- `hook_post_tool_use.py` — Codex PostToolUse routing for native Goal, Bash hints and successful WBS task-result binding
- `codex_hook_registration.py` — authenticates native session metadata and binds exact session/task/run/workspace source focus under conflict fences and bounded lock admission
- `setup_finalize.py` — canonical operational ignores, manifest migration, setup resource/routing validation and legacy version-file removal; `SETUP_OK` is the success signal
- `contract_lint.py` — managed CONTRACTS checks and C-13 skill-weight checks
- `prewrite_gate.py` — direct-write artifact ownership, source focus, plan-first and scope enforcement
- `install_smoke.py` — installed-hook imports and the receipt-free public WBS lifecycle in a temporary repository
- `install_verified.py` — trusted source/task ownership, current WBS applicability and stable payload delivery
- `golden_replay.py` — regression smoke checks for shared scripts
- `runbook_memory.py` — approved runbooks and pending candidates surfaced by prompt memory and reviewed through the normal workflow

# Runtime services

`runtime_services.py` starts, checks, logs, and stops background services from
`doc/harness/manifest.yaml` `runtime.services[]`.

```bash
python3 plugin/scripts/runtime_services.py start
python3 plugin/scripts/runtime_services.py status
python3 plugin/scripts/runtime_services.py logs api
python3 plugin/scripts/runtime_services.py stop
```

It stores state in `doc/harness/runtime/services.json`, logs in
`doc/harness/runtime/logs/`, waits for service healthchecks, and performs bounded
self-healing commands declared in the manifest.

# Review, QA and completion

Await actual independent code review and conditional security review PASS before
actual QA. Agents return concrete findings in their final responses. Coordinator
summaries and copied text cannot replace independent results. Fix real failures;
report real environmental blockers with an actionable unblock condition.

`wbs(action="verify")` and `wbs(action="close")` use current required measured
WBS checks, blocked state and source applicability. Machine verdicts do not
attest review or QA. No receipt collector, watcher, spawn-name binding, automatic
review-detail store or recording repair is required. Missing recording never
warrants reruns or parking. Historical receipt/review files remain preserved
non-authoritative artifacts; active lifecycle calls do not read them.

See [receipt-free execution and completion](../../doc/harness/REQ__receipt-free-execution-and-completion.md).
