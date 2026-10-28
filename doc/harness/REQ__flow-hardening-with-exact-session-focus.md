---
tags: [harness, workflow, sessions, verification, migration]
summary: Explicitly approved flow repairs preserve real execution authority and reduce repeated work.
updated: 2026-10-28
freshness: current
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/scripts/prewrite_gate.py
  - plugin/scripts/goal_migration.py
  - plugin/scripts/verify_runner.py
  - plugin/mcp/harness_server.py
  - install.py
---

# Reliable flow with exact session focus

## Accepted user requirements and provenance

Source: conversation on2026-10-28, following the nine-area source audit. The user's exact scoped response was: “1. legacy active자체를 지워 그냥. 2. ㅇㅋ 3. 수정 4. 수정 5. 수정 6. 수정 7. 수정 8. 수정 9. 수정 ㄱ”. The numbered approvals refer to the corresponding audit proposals, not unrelated design assumptions.

| ID | Origin | Confirmed scope |
|---|---|---|
| FLOW-1 | user_explicit; quote “legacy active자체를 지워 그냥” | Remove legacy `.active` itself and runtime compatibility/fallback authority. |
| FLOW-2 | user_confirmed; scoped quote “2. ㅇㅋ” | Allow direct preparation in ordinary registered linked worktrees while retaining coordinator-only managed lifecycle. |
| FLOW-3 | user_confirmed; scoped quote “3. 수정” | Use effective runtime installation locations, verify the selected Claude cache, and accurately report restart needs. |
| FLOW-4 | user_confirmed; scoped quote “4. 수정” | Preserve command diagnostics and reusable successes across cache hits and reuse-only misses. |
| FLOW-5 | user_confirmed; scoped quote “5. 수정” | Admit ready independent nested lanes and consistently apply development concurrency settings. |
| FLOW-6 | user_confirmed; scoped quote “6. 수정” | Branch recovery on actual independent review/QA results; remove obsolete synthesis and transcriptless codifier rituals. |
| FLOW-7 | user_confirmed; scoped quote “7. 수정” | Allow normal live WBS/pool evolution after completed migration while preserving immutable archive custody. |
| FLOW-8 | user_confirmed; scoped quote “8. 수정” | Keep operational history out of current-source diagnostics and avoid unrelated historical promotion work when there is no current signal. |
| FLOW-9 | user_confirmed; scoped quote “9. 수정” | Keep planning concise and autonomous within scope, and distinguish setup defaults from user requirements. |

## Derived implementation constraints

These are technical decisions supporting the accepted outcomes, not additional user product requirements. Reuse exact session/run bindings and their conflict fences rather than introducing a new authority store. Migrate known legacy focus explicitly; do not recreate legacy runtime readers or silently assign ambiguous shared focus to a session. Preserve existing task/run identities, measured checks, failed evidence and historical bytes.

Keep preparation, publication and immutable-history integrity checks strict. Successful cache candidates and unsuccessful latest attempts must retain distinct applicability; a miss never becomes PASS. Logs may have bounded inline excerpts while their useful execution evidence remains accessible. Ordinary user changes, independent review before actual QA, platform skips and safe Git integration/disposal remain protected.

## Agent-derived recovery findings

These implementation observations support the approved repairs; they do not extend the user's nine requirements. Explicit focus migration must include the legacy `default.json` companion omitted by the initial marker inventory, preserve completed archive bytes, and allow an interrupted archive-fsync retry to complete under the existing owner guards. Completed legacy archives remain immutable.

Actual disposal recovery read the same complete source/archive inventories twice before thawing frozen verification directories. Collect and validate candidate directories first, then perform one complete ordinary removal proof immediately before permission changes; retain that proof on no-checkpoint/no-store early exits. Source/archive drift or unknown content must still refuse disposal before chmod. No cached hashes replace current byte equality.

The recovery also identified retired zero-byte `.active.fixture.lock` cache files. Their one-time retirement follows ownership and idle checks; current fixtures place locks under `/tmp`. This is cleanup of obsolete cache state, not permanent fixture compatibility or authority. Preserve user changes and actual failed evidence.

## Verification

Use real hook and public MCP entrypoints for current/stale/fenced session authority, standalone linked-worktree preparation and worktree-to-main/sibling target routing, including a valid authorized control. Test effective CODEX_HOME config/cache/trust/reload, corrupt selected Claude cache despite a healthy mirror, and restart reporting. Exercise execute→reuse→reuse, A→reuse-only miss on B→restored A, completed migration→legitimate live mutation and completed migration→immutable archive corruption.

Check nested wide-first versus independent-pair admission and cap precedence. Active diagnostic source discovery must exclude ignored snapshots without treating a successful empty Git result as unavailable. No-signal automatic promotion must not validate unrelated candidate runs. Workflow checks preserve actual FAIL/BLOCKED_ENV and review-before-QA while eliminating obsolete phases and arbitrary sentence counts.

## Review research is advisory

The user separately asked why AI reviews miss bugs and for latest research/trends and an improvement proposal. Research findings and proposed counterexample-based review changes are advisory until their implementation scope is clearly authorized; they are not new acceptance requirements here. No receipt collector, replacement verdict ledger, new service or generic scheduler is introduced by this repair.
