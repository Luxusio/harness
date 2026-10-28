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
input decoding and repository discovery, including session/task-control lock admission.
Contention must return within the budget rather than leave the hook waiting for
its external timeout. Actual expiry reports registration failure, grants no new
source authority and releases acquired locks; a later successful call may bind
future work through the task binding protocol. Preserve existing caller
alarms and exact bindings. No watcher restoration remains. Deeply nested encoded arguments that exceed the JSON decoder's
recursion limit are invalid inputs and must be rejected without a traceback.

The installed PostToolUse matcher must cover the canonical binding-tool
allowlist. Contract tests compare those surfaces directly so a new public tool
cannot silently miss registration routing.

An installation self-test exercises installed native task binding, the
PostToolUse wrapper and the receipt-free public WBS lifecycle in a unique
temporary repository and CODEX_HOME. Fixture native events are test inputs,
never independent review/QA evidence for production work. A parent supervisor
owns cleanup when a timed-out worker cannot run its finally block. Broken
binding or lifecycle must fail the self-test. Do not write into installed payloads.

## Verification

Exercise argument aliases and object/string envelopes with linked worktrees,
malformed/conflicting inputs, failed results, nonbinding actions and workspace
mismatches. Compare matcher coverage with canonical tool IDs. Test session and
task-control contention, bounded return, unchanged binding on failure and
recovery after release. Test deeply nested encoded arguments without tracebacks.
Run affected hook/worktree/install contracts and actual independent review
before actual QA. No recording collection is required.
