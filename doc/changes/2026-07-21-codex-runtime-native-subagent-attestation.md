# Codex runtime-native subagent attestation

Codex collaboration agents can run and finish without emitting their tool
calls to plugin PostToolUse. That left independent review and QA receipts empty
even though the runtime had durable parent/child lifecycle evidence.

Harness now has every installed Codex root hook validate and restore a repository-
and thread-scoped root-rollout registration without launching a detached process.
SessionStart is the primary registration point; later hooks repair missing or
invalid state. The
existing Harness MCP server discovers that registration and hosts the watcher
as a daemon thread. It tails only new records from the registered root offset. A
recognized review, QA, or UX start is accepted only after the runtime spawn
call, started activity, successful spawn output, and child session metadata all
agree. At that moment the watcher records the active task, HEAD, base SHA, and
diff fingerprint through the existing protected receipt writer.

Codex MCP processes do not receive the root thread id in their environment.
The watcher therefore accepts the exact task binding from a successful Harness
`task_start` or `task_context` event in the already trusted root rollout. It
rejects failed calls, invalid task paths or states, and any binding first seen
after the child has completed.

The common helper reads the official hook `session_id`; `CODEX_THREAD_ID` is
only a validated matching fallback. SessionStart retries registration for one
bounded second when hook delivery races creation of the root rollout.
PreToolUse, UserPromptSubmit, PostToolUse, and the available Stop wrapper make
one fail-open recovery attempt. Existing valid offsets remain immutable; late
recovery starts at the current offset and cannot attest past completions.

Completion requires the same child lineage, a root-delivered final response,
and identical child `final_answer` and `task_complete.last_agent_message`
values. Existing verdict and finding-count parsers remain authoritative. A
source change during the child run produces PENDING rather than PASS.

MCP restart replays the immutable registration offset and relies on protected
receipt deduplication, so a restart cannot convert historical output into new
evidence. The watcher fails closed on historical completions, partial or malformed JSONL,
unknown lenses, mismatched repository/session/thread/path data, ambiguous
finals, symlinked or non-regular rollout files, wrong ownership, and bounded
input violations. It coexists with the classic PostToolUse path and does not
permit assistants to write receipt files.

Rollout reads are bound to no-follow file descriptors and recheck device/inode
identity, ownership, link count, non-group/world-writable mode, size, and the owner-controlled session
directory chain. Path replacement fails closed. A per-root interprocess lease
allows only one MCP-hosted worker to consume a registration at a time, keeping
receipt deduplication deterministic when multiple MCP servers coexist.
The repository task-root chain is validated before resolution, so a symlinked
`doc/harness/tasks` cannot redirect trusted receipts into another repository.

The trust boundary is the local Codex runtime, the trusted root-hook-owned
registration, and the passive MCP-hosted watcher under the current
operating-system user. MCP tools do not expose a receipt-authoring API. This is
not cryptographic protection against a malicious same-UID process; Harness's
existing local hook and MCP model has the same operating-system boundary.

Codex orchestration also avoids noisy short-poll loops while agents run. It
does useful coordinator work first, waits in intervals of up to 60 seconds,
posts one compact status after a timeout, and calls `list_agents` once after the
whole required batch completes.

Verified installation permits only the repository's exact tracked, inert
`README` and `CHANGELOG` payload paths that static review intentionally
excludes. Executable, symlinked, relocated, or unknown unreviewed payloads are
rejected, and payload fingerprints cover file type and mode as well as bytes.
Strict relative-path normalization preserves `.claude-plugin`, rejects path
escapes, and snapshots each payload through descriptor-relative `O_NOFOLLOW`
opens. Hashing and copying use the same stable file descriptor, with Git mode
matching and pre-execution source/verification revalidation. Every opened
source directory must be root- or current-user-owned and not group/world
writable; Git index-query failures also fail closed.

An explicit `task_start` on a previously blocked task now resumes it by
clearing the stale `BLOCKED_ENV` state and blocker artifact before fresh
verification. This keeps resolved environment blockers from permanently
poisoning later QA and verified installation.

## Feedback-derived rule

When Codex waits for Harness subagents, do not repeatedly issue short
`wait_agent` calls. Use useful local work plus one bounded wait per status
interval, and verify the batch with a single final `list_agents` call.

When trusted Codex root hooks share the same canonical session identity, put
idempotent registration recovery in their common path instead of relying on a
single startup event. Verify that the original offset remains immutable and
that late recovery accepts only future subagent starts.
