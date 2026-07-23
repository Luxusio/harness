# Lifecycle completion stability

Task source ownership now includes paths committed after `TASK_BASELINE.json`'s
HEAD, not only the current dirty worktree. Review routing and verified plugin
installation therefore retain the task payload after a clean commit.
Baseline reads are bounded, regular-file-only, no-follow operations with
repository, revision, path, fingerprint, and ancestry validation. Invalid or
unavailable baselines fail closed, while unchanged pre-task dirt remains out of
scope even if it is committed later.

Task and Goal terminal transitions are coupled: `task_close` closes the active
Goal child, while `goal_finish(complete)` requires at least one canonical child
whose task state is closed with fresh receipt-backed QA PASS. Calling `goal_start` on
the same completed or blocked Goal explicitly reactivates it and preserves its
queue.

Every registered Claude hook command is fail-safe with `|| true`. Codex run and
develop guidance uses the exact QA task names `qa_cli`, `qa_api`, `qa_browser`,
and `qa_desktop`, which are the stable lifecycle receipt bindings.
