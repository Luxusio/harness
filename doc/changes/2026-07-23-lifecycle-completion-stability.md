# Lifecycle completion stability

Task source ownership now includes paths committed after `TASK_BASELINE.json`'s
HEAD, not only the current dirty worktree. Review routing and verified plugin
installation therefore retain the task payload after a clean commit.
Baseline reads are bounded, regular-file-only, no-follow operations with
repository, revision, path, fingerprint, and ancestry validation. Invalid or
unavailable baselines fail closed, while unchanged pre-task dirt remains out of
scope even if it is committed later.
New Git-backed tasks require successful baseline capture before task state is
created; an unborn repository or transient snapshot failure blocks task start
instead of silently creating an unreviewable committed scope. A missing baseline
on any Git-backed task fails closed regardless of deletion method; older
baseline-less tasks must be restarted explicitly instead of being inferred as
legacy from ambiguous absence.

Task and Goal terminal transitions are coupled: `task_close` closes the active
Goal child, while `goal_finish(complete)` requires at least one canonical child
whose task state is closed with fresh receipt-backed QA PASS. Calling `goal_start` on
the same completed or blocked Goal explicitly reactivates it and preserves its
queue; terminal Goals reject child mutation and repeated finish calls until
that explicit restart.

Every registered Claude hook command is fail-safe with `|| true`. Codex run and
develop guidance uses fresh names with `qa_cli_`, `qa_api_`, `qa_browser_`, or
`qa_desktop_` prefixes. The watcher binds receipts from the prefix while the
suffix prevents same-thread collisions across sequential tasks.
