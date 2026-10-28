---
tags: [harness, review, receipts, evidence]
summary: 정식 리뷰 원문은 영수증과 같은 해시로 태스크 로컬 저장되며 한 번에 한 건만 선택 조회된다.
updated: 2026-09-28
freshness: suspect
invalidated_by_paths:
  - plugin/skills/batch/SKILL.md
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
freshness_updated: 2026-09-18T08:10:49Z
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

   Batch integration carry (Claude-only): the `review-code` lens of the
   post-wave `harness:batch` integration task does not re-review a lead range
   that its own lead already reviewed and QA'd when the integration is proven
   equivalent. A carry requires that lead's closed WBS and integration proof; historical receipts do not bind
   no tree. Per integrated lead the endpoints are `old_base` = the coordinator
   HEAD from batch step b.5, `old_tip` = the lead's returned `commit` (leads
   commit after `task_close`, so it is the tree their review and QA passed),
   `new_base` = the main HEAD before that lead's rebase (the previous lead's
   `new_tip`, or `old_base` for the first lead), and `new_tip` = the
   integrated tip. A range is carried only when all three hold: the
   rebase-LIGHT proof holds at its fast-forward point, with one-to-one
   equivalence shown by identical ordered `git patch-id --stable` patch ids
   over the same commit count; it integrated through the step d procedure
   and its rebase never stopped, not through the step e conflict path;
   and its harvested archive shows the lead closed with PASS. A range that
   fails a condition is residual: missing proof alone only rejects the carry,
   while conflict, semantic difference, overlap, or evidence loss (including
   an endpoint that no longer resolves) selects DEEP. The residual is
   everything from `old_base` to the current worktree outside the carried
   ranges. Carried ranges do not count toward cross-component or dual-domain
   and contribute no other depth predicate; depth follows the normal
   precedence over the residual only: conflict resolutions (manual-conflict,
   DEEP), cross-lead interactions (overlap with earlier leads, DEEP),
   non-carried ranges, then the integration task's own edits. An empty
   residual is LIGHT. The formal reviewer invocation lists each lead's
   endpoints, patch-id result, and carried or residual status; the reviewer
   sweeps the residual, verifies every carry itself, and reports a carry it
   cannot reproduce as the existing under-classification `FIX_NOW`. The full
   suite and `qa-cli` still run on the combined result.

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
   corrections use deterministic checks without hunters and forfeit the
   remaining discovery budget. Cycle two is final.
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

3. Actual independent formal review finals remain the workflow results. Return
   the required verdict, counts and structured review detail directly. The
   coordinator awaits actual independent review PASS before actual QA. Missing
   recording never warrants retries, repair or parking. No automatic detail
   store, digest binding, read/write CLI or replacement ledger is required.
   Historical review details remain non-authoritative preserved artifacts.

## Verification

- Prompt-contract tests pin the decision-to-fan-out relations for deterministic
  zero/one/two hunter routing, every conjunct of the rebase-LIGHT instructions,
  the two-cycle ceiling and change-class retry matrix, bounded hunter context,
  coordinator-owned reroutes, formal-only exhaustion path,
  the exact three-field schema, independent full-sweep verifier behavior, and
  the unchanged single formal authority. They do not attest a runtime
  classifier because selection is orchestration instruction, not lifecycle
  code.
- `tests/test_batch_integration_review_scope.py` pins the batch integration
  carry: section placement after the fan-out rules, the four endpoints and
  their sources, the all-of carry conditions, the residual complement and its
  precedence order with DEEP mappings, empty residual LIGHT, the invocation
  listing, unchanged full suite and `qa-cli`, the Claude-only reviewer
  paragraph outside the shared role core, and avoidance of the anchors the
  existing review contract tests locate by first or last occurrence.
- Installation checks prove both runtime bundles retain the hunter role and
  actual independent formal reviewer output contract. Current machine
  verify/close tests use measured WBS checks without receipt files.
