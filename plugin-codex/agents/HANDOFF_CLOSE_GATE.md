# HANDOFF Close-Gate Contract

Developer agents write `HANDOFF.md` through `write_handoff`. Before calling the
tool, include the sections below in the `summary` or `verification` text. The
tool can add missing default sections, but agents should not rely on a close
failure to discover the required shape.

```markdown
## User Feedback Disposition

event: <id> status: <promoted|handled-local|deferred|rejected> reason: <why> artifact: <path-or-n/a>

## Commit-backed Learnings

Status: <none|captured|rejected>

- captured: <changed commit-eligible path> - <rule/fact now shared>
- rejected: <candidate> - <why it is task-local, noisy, or not reusable>

## Self-Healing Candidates

Status: <none|applied|deferred|rejected>

- applied: <failure mode> - <changed commit-eligible path> now prevents recurrence
- deferred: <failure mode>
  user_decision: <separate task | not now | other user wording>
  reason: <why not in this task>
  proposed_artifact: <path> | proposed_task: <task>
- rejected: <candidate> - <why it is one-off, noisy, or not worth automating>
```

## Rules Enforced By Close

- `User Feedback Disposition` needs one terminal `event:` line for every
  unresolved `<task_dir>/USER_FEEDBACK.jsonl` id. `needs-user-decision` is not
  closeable.
- `Commit-backed Learnings Status: captured` must name a changed/touched
  commit-eligible repo artifact. `doc/harness/learnings.jsonl`, task-local
  files, ignored files, nonexistent files, and untouched existing files do not
  count.
- `Self-Healing Candidates Status: applied` must name a changed/touched
  commit-eligible repo artifact.
- `Self-Healing Candidates Status: deferred` must include `user_decision:`,
  `reason:`, and `proposed_artifact:` or `proposed_task:`.
- Durable docs must be named when behavior, contracts, or reusable guidance
  changed. If no durable doc changed, write
  `Durable docs: not needed - <specific non-observable reason>`.
