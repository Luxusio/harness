# Codex runtime-native subagent attestation

Codex collaboration agents can run and finish without emitting their tool
calls to plugin PostToolUse. That left independent review and QA receipts empty
even though the runtime had durable parent/child lifecycle evidence.

Harness now starts a repository- and thread-scoped watcher from the Codex
SessionStart hook. It tails only new records in the current root rollout. A
recognized review, QA, or UX start is accepted only after the runtime spawn
call, started activity, successful spawn output, and child session metadata all
agree. At that moment the watcher records the active task, HEAD, base SHA, and
diff fingerprint through the existing protected receipt writer.

Completion requires the same child lineage, a root-delivered final response,
and identical child `final_answer` and `task_complete.last_agent_message`
values. Existing verdict and finding-count parsers remain authoritative. A
source change during the child run produces PENDING rather than PASS.

The watcher fails closed on historical completions, partial or malformed JSONL,
unknown lenses, mismatched repository/session/thread/path data, ambiguous
finals, symlinked or non-regular rollout files, wrong ownership, and bounded
input violations. It coexists with the classic PostToolUse path and does not
permit assistants to write receipt files.

The trust boundary is the local Codex runtime plus the SessionStart-owned
watcher under the current operating-system user. This is not cryptographic
protection against a malicious same-UID process; Harness's existing local hook
and MCP model has the same operating-system boundary.
