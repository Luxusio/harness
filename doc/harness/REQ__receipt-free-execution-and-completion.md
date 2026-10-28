# Receipt-free execution and completion

## User intent

User_explicit, 2026-10-28:

> 야 그냥 영수증 빼자.

> 영수증이 계속 작업을 느리게 만들고 오히러 불안정하게 만든다.

These statements authorize removing mandatory receipt collection and attestation from active execution, verification, completion and installation. They do not request weaker substantive review or QA.

## Derived engineering requirements

- Remove receipt collectors, watcher startup/registration, capability checks, spawn-name binding rules, receipt-only retries and missing-attestation parking. Do not introduce a replacement ledger, toggle or service.
- Await actual independent code review and conditional security review PASS before actual QA. Fix substantive failures; publish real environmental blockers. Coordinator summaries and copied text do not substitute for independent results. Keep the existing review-depth and bounded hunter protocol.
- Existing WBS assertions remain the machine basis for progress, verify and close. Failed, unrun or stale required checks block completion. Machine verdicts explicitly identify WBS checks as their basis and never claim machine-attested review or QA.
- Retain read-only protection for the native session metadata still used to authenticate source focus even from a non-Harness editor cwd, and for historical receipt/review bytes. This does not restore transcript-event scanning, collectors or completion attestation.
- Keep task/run/session/source ownership, declared scope applicability, stable installation snapshots, stopped-writer checks, Git ancestry and safe worktree integration/disposal. A task closes only through its public owner.
- New four-field TASK.json controls use `closed: boolean` in place of `close_receipt_fingerprint`. Explicit `wbs(action="migrate")` preview/apply converts existing controls and pools while preserving task IDs, runs, WBS nodes/checks and historical receipt files. Normal context/verify/install/close never read receipts. Migration validates archived controls before publication, updates existing archive fingerprints, and supports idempotent partial recovery through its migration journal.
- Already-unsupported, unreferenced historical pre-v7 task controls remain byte-preserved and are reported explicitly by migration. Active or pool-referenced controls cannot be silently skipped. Migration does not mint replacement run IDs for unsupported historical artifacts.
- Historical receipt absence, corruption or changes have no effect on active task authorization. Generic archived-byte integrity still covers every archived byte, including historical receipts; this is archive integrity, not receipt attestation.
- Preserve historical receipt bytes and historical requirements/ADRs. Their receipt rules are superseded by this requirement. Review findings remain in actual agent responses, without a persisted completion ledger.

## Managed contract maintenance

The active MAINTENANCE task implements the user's explicit removal request by replacing/removing obsolete receipt clauses in CONTRACTS.md and its setup template together. Keep managed markers intact and preserve unrelated project guidance.

## Verification and delivery

Exercise public receipt-free open/plan/check/verify/close, explicit migration, source focus, managed integration/disposal and stable verified installation. Check both runtime payloads, real-tree contracts and skill weight. Complete independent review before QA. Do not resurrect receipt collection to finish its removal. Format rollback requires an explicit compatible migration/recovery path; reverting source alone cannot revert migrated controls.

The public lifecycle is covered by `test_public_measured_lifecycle_closes_without_receipts_or_events` in `tests/test_receipt_free_lifecycle.py`. Same-run migration and historical-byte preservation are covered by `test_public_preview_apply_preserves_run_checks_and_history` in `tests/test_receipt_free_migration.py`.
