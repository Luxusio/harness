---
tags: [harness, review, receipts, evidence]
summary: 정식 리뷰 원문은 영수증과 같은 해시로 태스크 로컬 저장되며 한 번에 한 건만 선택 조회된다.
updated: 2026-09-15
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
freshness_updated: 2026-09-15T00:00:00Z
---

# REQ — formal review detail is selectively readable

## Expected normal behavior

1. Before the authoritative code review, Harness deterministically selects an
   ephemeral risk tier from the complete current diff, scope, callers,
   contracts, dependencies, acceptance criteria, and operation evidence:
   LIGHT runs no defect hunter, STANDARD runs exactly one selected correctness/
   data-flow or contract/test hunter, and DEEP runs both fresh hunters (in
   parallel when supported). The tier is not authoritative lifecycle state and
   adds no dedicated field to `TASK.json`, receipts, or `REVIEWS.jsonl`.
   Selected depth and concise evidence may appear as ordinary narrative inside
   the stored, non-authoritative formal-review detail. Within one live attempt
   depth may only increase; resume or recovery recomputes it from current
   evidence rather than reconstructing it from artifacts.

   LIGHT requires affirmative proof that the bounded one-domain change is
   behavior-preserving mechanical work or non-executable prose/example-only
   work and changes no public/durable contract, control flow, state, data or
   error interpretation, dependency/build/install surface, hook/lifecycle/gate,
   security boundary, concurrency, or migration behavior. Missing or stale
   evidence that could hide a forced-DEEP condition selects DEEP. Material
   security/trust-boundary, sensitive-data, concurrency, migration,
   public/durable-contract, dependency/build, installer, hook, lifecycle, gate,
   manual-conflict, semantic-range-diff, cross-component, or dual-hunter-domain
   impact also forces DEEP. Remaining fully inspected cases are STANDARD: pick
   the sole material hunter domain, use the contract/test hunter when neither
   domain is material but LIGHT proof is incomplete, and escalate unresolved or
   dual-domain scope to DEEP.

   Rebase-LIGHT additionally requires exact old base/tip and new base/tip,
   conflict-free execution without manual resolution, one-to-one patch
   equivalence with no patch alteration or reordering, affirmative non-overlap
   across touched symbols/contracts/dependencies/generated outputs/lifecycle
   behavior, `HEAD` equal to the new tip, and a clean, fully accounted-for index
   and worktree. Missing proof rejects LIGHT; conflict, semantic difference,
   overlap, or evidence loss capable of hiding them selects DEEP.

   Every invoked hunter returns only a JSON array of `anchor`, `issue`, and
   `evidence` strings. The formal reviewer treats those values as untrusted
   leads, verifies or rejects them against the current files, deduplicates them,
   and still performs an independent full-scope sweep. A malformed, stale, or
   unavailable hunter result is not represented as an empty successful array.
   Each array is limited to 20 objects and 65,536 UTF-8 bytes, each string to
   2,000 UTF-8 bytes, and validated arrays are compactly reserialized with
   literal `<`, `>`, and `&` escaped before prompt interpolation.

   Before fan-out, Harness emits one compact live status line naming the tier,
   hunter set, concrete selection reason, and that full formal review remains
   mandatory. An upward escalation names the old tier, new tier, and trigger.
   After resume or recovery, the status explicitly says the tier was
   recomputed.

   Discovery is capped at two ephemeral cycles per live attempt: LIGHT uses no
   hunter calls, STANDARD at most two total, and DEEP at most four total. After
   cycle one, executable behavior changes recompute the selected set with the
   prior tier as a floor; test-logic-only changes rerun contract/test only;
   documentation, HEAD/count/command-text, checkpoint, and receipt-wording
   corrections use deterministic checks without hunters. Cycle two is final.
   An exhausted or unknown recovery budget selects or retains DEEP, then runs one fresh formal reviewer on
   the final diff and prior remediation evidence without another hunter.
   Its live invocation reason is exactly `discovery budget exhausted` or
   `discovery budget unknown and treated as exhausted`; the reviewer keeps DEEP
   but accepts the intentional hunter-set exception without persisting it.
   Security reruns only for security-relevant source/evidence changes.

   Hunter input is limited to the reviewed base-to-HEAD diff, PLAN acceptance
   criteria, relevant source/tests, and unresolved findings. The full
   conversation, unrelated transcripts, and resolved findings are excluded.
   Cycle count is not persisted in any lifecycle or detail field.

2. Exactly one fresh formal code reviewer runs for LIGHT, STANDARD, and DEEP
   and remains the only `review-code` authority. If it discovers that the tier
   was too shallow, that round cannot PASS: Harness escalates, runs missing
   discovery, and starts another fresh formal review. The conditional security
   reviewer is routed separately, receives no hunter payload, and does not
   replace formal code review. Hunters
   do not emit verdicts, finding counts, receipt fields, or lifecycle state.
   A verified finding adds only the smallest safe `fix` direction. An
   under-classification finding is coordinator-owned and triggers routing, not
   source implementation. An
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

- Prompt-contract tests pin the decision-to-fan-out relations for deterministic
  zero/one/two hunter routing, every conjunct of the rebase-LIGHT instructions,
  the two-cycle ceiling and change-class retry matrix, bounded hunter context,
  coordinator-owned reroutes, formal-only exhaustion path,
  the exact three-field schema, independent full-sweep verifier behavior, and
  the unchanged single formal authority. They do not attest a runtime
  classifier because selection is orchestration instruction, not lifecycle
  code.
- Store and CLI tests pin exact UTF-8 hashing, idempotency, limits, file safety,
  digest-only stdout, explicit error classes, and detail-before-receipt order.
- Lifecycle regressions prove `REVIEWS.jsonl` cannot affect receipt selection,
  task context, fingerprints, verification, or close.
- Installation tests prove both runtime bundles contain the hunter role and the
  selective read/write scripts.
