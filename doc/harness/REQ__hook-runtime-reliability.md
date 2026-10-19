# Hook runtime reliability

## User-sourced intent

Origin: user_explicit. Source: follow-up messages in this task:
“전반적으로 더 깨질 일 있는지 한번 더 봐봐” and “hook맨날 깨진다고”.
Review the broader change for remaining failures, with emphasis on repeated
hook failures. The preceding explicit request “증거수집도 버그 고쳐” authorizes
repair of confirmed collection defects.

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
permissive interpretation. Existing namespace, result, task/run, workspace,
conflict-fence and immutable-offset checks remain authoritative.

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
