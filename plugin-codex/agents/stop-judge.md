# Deprecated compatibility path: stop-judge

This file is intentionally not an agent definition. It has no agent
frontmatter, tool grants, verdict protocol, or runtime authority and must not be
routed for new work.

Harness now parks unfinished work directly through `task_blocked` only for:

- a genuine external environment blocker;
- an observed review or QA `BLOCKED_ENV`; or
- required attestation still missing after substantive review and QA complete
  and one fresh `task_verify` has run.

`task_blocked` publishes durable `BLOCKED.md` state. It never authorizes PASS or
close. Difficulty, time pressure, and retry exhaustion are not blockers.

This path remains for one compatibility phase so old links fail clearly instead
of silently changing behavior. Remove it after that compatibility window.
