---
tags: [harness, review, receipts, evidence]
summary: 정식 리뷰 원문은 영수증과 같은 해시로 태스크 로컬 저장되며 한 번에 한 건만 선택 조회된다.
updated: 2026-09-14
freshness: current
invalidated_by_paths:
  - plugin/agents/defect-hunter.md
  - plugin/agents/code-reviewer.md
  - plugin-codex/agents/defect-hunter.md
  - plugin-codex/agents/code-reviewer.md
  - plugin/scripts/_lib.py
  - plugin/scripts/codex_lifecycle_watcher.py
  - plugin/scripts/subagent_lifecycle.py
  - plugin/scripts/background_hook.py
  - plugin/scripts/review-log
  - plugin/scripts/review-read
  - plugin/skills/develop/SKILL.md
  - plugin/skills/develop/quality-audit-pipeline.md
  - plugin-codex/internal-skills/develop/SKILL.md
freshness_updated: 2026-09-14T00:00:00Z
---

# REQ — formal review detail is selectively readable

## Expected normal behavior

1. Before the authoritative code review, Harness runs two fresh, non-attesting
   defect-discovery passes: one for correctness and data flow, and one for
   contracts, error paths, and test adequacy. Each pass returns only a JSON
   array of `anchor`, `issue`, and `evidence` strings. The formal reviewer
   treats those values as untrusted leads, verifies or rejects them against the
   current files, deduplicates them, and still performs an independent sweep.
   Each array is limited to 20 objects and 65,536 UTF-8 bytes, each string to
   2,000 UTF-8 bytes, and validated arrays are compactly reserialized with
   literal `<`, `>`, and `&` escaped before prompt interpolation.

2. The formal code reviewer remains the only `review-code` authority. Hunters
   do not emit verdicts, finding counts, receipt fields, or lifecycle state.
   A verified finding adds only the smallest safe `fix` direction. An
   environmental inability to inspect takes precedence as `BLOCKED_ENV`;
   otherwise verified findings produce `FAIL`, and their absence produces
   `PASS`.

3. `RECEIPTS.jsonl` remains the compact and authoritative lifecycle stream.
   The exact final text of each formal code or security review completion is
   stored separately in the same task directory as one append-only
   `REVIEWS.jsonl` row with exactly two string fields: `detail_sha256` and
   `detail`. The hash is SHA-256 of the exact UTF-8 detail and matches the
   receipt's `DETAIL_SHA256` value. No context, verification, fingerprint, or
   close decision reads `REVIEWS.jsonl`.

4. New detail is durably appended under the existing task receipt lock after
   current-run and nonterminal checks and before the corresponding compact
   receipt. A detail failure publishes no receipt. A failure after the detail
   fsync restores any partial receipt append to its exact prior bytes and may
   leave an orphan row; retrying the same hash and body reuses it without adding
   a duplicate.

5. Review detail is bounded to 2 MiB per UTF-8 body and 16 MiB per task store.
   The store is an owner-only regular file, is never followed through a
   symlink, and is rejected when ownership, link count, mode, row shape, hash,
   or file identity is unsafe. Same-hash/different-body data is an integrity
   error.

6. `review-read [--task-dir TASK_DIR] <64-lowercase-hex>` returns only the
   selected exact detail on stdout. It never lists or dumps unrelated rows.
   `review-log [--task-dir TASK_DIR]` reads one detail from stdin, uses the same
   shared writer, and prints only `DETAIL_SHA256:<hex>` on success. Omitting
   `--task-dir` resolves the active task. Explicit `review-read` task selection
   continues to work after close; `review-log` refuses a terminal task.

7. Existing receipts and formal-review finals that predate this storage remain
   valid. A missing file or digest is reported as not found and may mean the
   receipt predates detail storage; it never invalidates review, QA, or task
   close. Old runtimes may ignore the additive file. No migration, backfill,
   fresh run, or cleanup state is required.

## Verification

- Prompt-contract tests pin fresh hunter routing, the exact three-field schema,
  independent verifier behavior, and the unchanged single formal authority.
- Store and CLI tests pin exact UTF-8 hashing, idempotency, limits, file safety,
  digest-only stdout, explicit error classes, and detail-before-receipt order.
- Lifecycle regressions prove `REVIEWS.jsonl` cannot affect receipt selection,
  task context, fingerprints, verification, or close.
- Installation tests prove both runtime bundles contain the hunter role and the
  selective read/write scripts.
