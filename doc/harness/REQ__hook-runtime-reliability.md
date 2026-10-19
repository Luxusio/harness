# Hook runtime reliability

## User-sourced intent

Origin: user_explicit. Source: follow-up messages in this task:
“전반적으로 더 깨질 일 있는지 한번 더 봐봐” and “hook맨날 깨진다고”.
Review the broader change for remaining failures, with emphasis on repeated
hook failures. The preceding explicit request “증거수집도 버그 고쳐” authorizes
repair of confirmed collection defects.

The later user reply “수정ㄱ” confirms the immediately preceding three-finding
review: fix registration lock-budget overruns, ambient Git measurement overrides,
and deeply nested encoded argument crashes. The Git measurement constraint is
owned by [measurable WBS progress](REQ__measurable-wbs-progress.md).

## Confirmed failures

At225ddbb, a scratch linked-worktree experiment parsed all three supported
argument envelopes successfully, yet only tool_input bound the workspace.
input and arguments silently lost workspace at the second parser. The
installed receipt smoke used Claude background_hook and checked only started,
including when invoked for Codex, leaving native manager/completion failures
outside the installation check.

## Derived implementation constraints

These are engineering choices rather than additional user requirements.

Decode supported tool_input/input/arguments once for action and workspace
selection. Accept object and JSON-object strings consistently. An explicitly
malformed envelope or non-string action, or conflicting aliases, cannot fall through to a more
permissive interpretation. Top-level arrays, null, numbers, strings and malformed JSON must not crash
PostToolUse or UserPromptSubmit. Use each wrapper's shared dictionary decoder
before accessing fields. Existing namespace, result, task/run, workspace,
conflict-fence and immutable-offset checks remain authoritative.

The registration budget covers the whole task-result callback, starting before
input decoding and repository discovery, including session/receipt lock admission.
Contention must return within the budget rather than leave the hook waiting for
its external timeout. Actual expiry reports registration failure, grants no new
receipt authority and releases acquired locks; a later successful call may bind
future work through the unchanged registration protocol. Preserve existing caller
alarms and exact bindings. The watcher restoration step receives only the
remaining budget. Deeply nested encoded arguments that exceed the JSON decoder's
recursion limit are invalid inputs and must be rejected without a traceback.

The installed PostToolUse matcher must cover the canonical binding-tool
allowlist. Contract tests compare those surfaces directly so a new public tool
cannot silently miss registration routing.

A Codex installation self-test must exercise installed native registration,
manager admission and matching started/completed PASS records in a unique
temporary repository and CODEX_HOME. Exercise both process-owned eager binding
and the installed PostToolUse wrapper. The parent supervisor owns cleanup if
a timed-out worker is killed before its own finally block. Fixture rollouts are test inputs only;
never place them in the real runtime or treat their receipts as evidence for
current production work. Real current-task independent review and QA remain
separately required. Register before fixture spawn; do not replay historical
proof. Bound the probe and stop its manager before removing fixtures. Broken
registration, manager or completion must fail installation self-test. Preserve
the existing Claude smoke path and avoid writes into installed payloads.

## Verification

Exercise all argument keys/object-string forms with real linked worktrees,
including malformed/conflicting aliases, failed results, nonbinding actions
and workspace mismatches. Compare exact matcher coverage with canonical tool
IDs. Verify a clean Codex projection self-test passes and that broken registrar,
manager or completion fails. Run existing hook/watcher/worktree/install
contracts and full independent QA, then inspect actual current-task receipts.
Exercise both session and receipt lock contention with real subprocesses,
unchanged existing binding/registration, bounded return and recovery after release.
Check deeply nested string arguments through the classifier, registrar and actual
PostToolUse wrapper, alongside the existing caller-alarm regressions.
