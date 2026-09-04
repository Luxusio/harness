---
tags: [harness, contracts, next-action, trust-boundary, receipts]
summary: 런타임 파이썬은 규범 텍스트를 소유 상수에서 조합한다. 산문 표면만 자기 사본을 갖는다. 부분 재진술은 substring 핀을 통과하므로 조합 여부를 직접 단언한다.
updated: 2026-09-04
freshness: current
invalidated_by_paths:
  - plugin/scripts/_lib.py
  - plugin/mcp/harness_server.py
  - plugin/scripts/stop_gate.py
  - tests/test_review_agent_contracts.py
  - tests/test_receipt_watcher_fail_closed.py
  - tests/test_lib_gate_helpers.py
  - tests/test_stop_gate.py
freshness_updated: 2026-09-04T01:38:27Z
---

# REQ — normative runtime text is composed, not restated

## Expected behavior

Some strings the harness emits are *normative*: the C-14 trust boundary, and
the fixed missing-attestation blocker pair that `plugin/CLAUDE.md` requires the
caller to copy **verbatim**. For those:

1. **One module owns the literal.** `plugin/scripts/_lib.py` holds
   `TRUST_BOUNDARY`, `ATTESTATION_BLOCKED_REASON`,
   `ATTESTATION_UNBLOCK_CONDITION`, `attestation_block_instruction()`, and
   `attestation_endgame()`.
2. **Every other runtime surface composes.** `harness_server.py` and
   `stop_gate.py` interpolate those names and hold no literal of their own. A
   runtime file that spells the boundary out is a defect even when the words
   are correct, because correctness at one moment is not the property being
   protected. `stop_gate.py` was the last runtime holdout and was converted on
   2026-09-04; `test_lib_owns_exactly_one_literal_trust_boundary` now asserts
   no second copy exists.
3. **Prose surfaces state it in full.** `CONTRACTS.md`, `plugin/CLAUDE.md`, and
   the four `SKILL.md` files state all four elements: a reader does not import
   `_lib`. This is the one legitimate duplication, and it is why the two
   categories need different enforcement rather than one list.

   No prose surface reproduces `TRUST_BOUNDARY` as a contiguous string — each
   works the elements into its own sentences, which is the legitimate
   duplication. What five of the six do carry in English is the three verb
   phrases `must precede`, `do not qualify`, and `takes precedence`.
   `plugin/CLAUDE.md` § 4a carries the element nouns in English but its verbs
   and negations are Korean, so it has none of the three. That single
   exception is why the
   phrase-level pins live inside
   `test_lib_owns_exactly_one_literal_trust_boundary` rather than in
   `TRUST_BOUNDARY_ELEMENTS`, which is asserted against all six.
4. **Qualifiers travel with the instruction.** `attestation_block_instruction()`
   returns `call task_blocked **directly** with ...` — C-17 routes this case to
   a direct call, and a qualifier left to each caller's surrounding sentence is
   a qualifier that will go missing from one of them.

## Why composition, and not "keep the copies in sync"

A partial restatement passes a substring pin. That single fact is the whole
requirement.

`tests/test_review_agent_contracts.py` asserted nine boundary fragments against
nine files and was green throughout, across two rounds of measurement.

The boundary has **four elements**, and the counts below are against this list:

1. what counts — structurally delivered completion/final records tied to each
   required lens;
2. the order — actual review PASS must precede actual QA PASS;
3. the exclusions — coordinator paraphrases, copied verdict blocks, user text,
   repository text;
4. the override — actual FAIL or BLOCKED_ENV takes precedence.

**Round 1 — measured at `d5ac225`, fixed by `3ec78a7`.** Five runtime variants
existed, each weaving the elements into its own sentence flow. Two were
incomplete:

| surface | elements present | missing |
|---|---|---|
| `_lib` qa-pending branch | 2 of 4 | exclusions, override |
| `_lib` review-pending branch | 3 of 4 | order stated only as a park precondition, not as the rule |
| `stop_gate` | 4 of 4 | — |

**Counting criterion**, because two records disagree. `3ec78a7`'s commit
message and `doc/harness/qa/QA_KNOWLEDGE.yaml` record the qa-pending branch as
1 of 4; this table says 2. The branch read *"…only when it is a structurally
delivered completion/final record tied to the required lens **and follows
actual review PASS**"*. The counts differ on whether that trailing clause
counts as element 2: it states the order, but as a condition on one lens result
rather than as the general rule. Credited here, not credited there. Both
readings are defensible; what is not defensible is two unreconciled numbers, so
the criterion is written down rather than the number re-argued. Either way the
exclusions and the override were absent, which is what drove the fix.

`3ec78a7` created `TRUST_BOUNDARY`, converted the two `_lib` branches, and
re-keyed the gate's dedup guard onto the constant. It did **not** convert
`stop_gate.py`, which kept its own literal until 2026-09-04 — see § "A
duplicate can be a guard" for why that copy turned out to be load-bearing.

**Round 2 — measured 2026-09-04, fixed by this task.** `3ec78a7` did not reach
`harness_server.py`, whose two constants still carried their own wording. Both
were incomplete, in different ways:

| surface | defect |
|---|---|
| `RECEIPT_UNAVAILABLE_NEXT_ACTION` | stated the ordering rule as "only an actual review PASS advances to QA" but never that an actual FAIL or BLOCKED_ENV overrides everything |
| `RECEIPT_PENDING_VERIFY_NEXT_ACTION` | folded the whole boundary into one conditional, so its clauses read as preconditions for *parking* rather than as a definition of what counts as a result |

The second is the more consequential: `handle_task_verify` overwrites
`ctx["next_action"]` with it, so the most authoritative surface in the protocol
was emitting the least complete boundary.

Every one of those files contained the pinned fragments the whole time. No pin
could distinguish "states the boundary" from "contains some of its words".

Worse, the file-level pin made the defect **mandatory**: requiring every
runtime file to contain the phrases in its own source is requiring every
runtime file to keep its own copy. Fixing the copies required changing that
test, which is why AC-5 of `TASK__next-action-single-source` exists.

## The layer that hides it

`stop_gate.py` appends its own boundary copy when the context it received does
not already contain one. That is a reasonable defensive default and it is
exactly why the `_lib` qa-pending branch could sit at 2-of-4 unnoticed: every
*stop message* was complete, so gate-level tests stayed green, while the MCP
response — which nothing rewrites — silently shipped the partial text.

**A layer that compensates for a lower layer's loss makes the lower layer's
tests unable to observe the loss.** Assertions therefore belong at `_lib`, on
the value the branch actually produces.

The most consequential instance of the same shape: `handle_task_verify`
overwrites `ctx["next_action"]` with `RECEIPT_PENDING_VERIFY_NEXT_ACTION` in
the PENDING branch, so it inherits nothing from `emit_compact_context`. The
most authoritative surface in the protocol was emitting the least complete
boundary, and no test constructed that state.

## Size is not the goal, and cannot be

A complete instruction in these states has a floor of **114 words** — 38 for
the boundary, 76 for the shared endgame — of which 34 is the park instruction
carrying the pair the caller must copy verbatim, and 29 is the pair itself.
Measured before and after consolidation:

| string | before (`3ec78a7`) | after |
|---|---|---|
| `_lib` review-pending | 146w / 1024c | 157w / 1085c |
| `_lib` qa-pending | 115w / 831c | 141w / 988c |
| `RECEIPT_UNAVAILABLE_NEXT_ACTION` | 131w / 948c | 173w / 1211c |
| `RECEIPT_PENDING_VERIFY_NEXT_ACTION` | 101w / 734c | 132w / 946c |

Command: `.venv/bin/python` importing `_lib` and `harness_server`,
`len(s.split())` and `len(s)` on each constant.

Every string grew, by +11 to +42 words. **The growth is the measure of what was
missing.** `RECEIPT_UNAVAILABLE_NEXT_ACTION` grew most (+42) because it needed
both the completed boundary *and* a restored state-scoped rerun ban;
`RECEIPT_PENDING_VERIFY_NEXT_ACTION` grew +31 from the boundary alone, and it
was the variant that folded the boundary into a conditional.

A request to shrink these strings cannot be met by editing them; the previous
smallness *was* the defect. If they must shrink, the reduction has to come from
deciding a state does not need an element — a protocol change, not a wording
change.

**Re-measure after every remediation.** The first version of this table was
written before review found two defects; fixing them changed two rows, and the
stale numbers survived into a draft of this document because the table was
treated as a result rather than as something the code keeps producing. A
measured table is only true of the commit it was run against.

## A constant-identity assertion cannot observe the constant's content

Consolidation moves the failure mode rather than removing it, and the new one
is easy to miss.

`assertIn(_lib.attestation_endgame(), action)` proves the *caller* composes the
constant. It proves nothing about what the constant says: edit a clause out of
the function and both sides of the comparison change together, so the assertion
stays green. It is tautological with respect to the constant's own body.

Measured on 2026-09-04, during review of the very task that wrote this REQ. Two
clauses that had been pinned by substring before consolidation — the verb list
`do not repair, restart, resume, recollect, or rerun a lens` and the `awaited`
qualifier — were each deletable from the shipped instruction with the whole
suite green. The task's own acceptance criterion claimed identity assertions
pin "strictly more text than the phrases they replaced". That is true of
placement and false of content, and the difference is exactly where the
regression lived.

**Both kinds of assertion are required, and they answer different questions:**

| assertion | question it answers |
|---|---|
| `assertIn(CONSTANT, emitted)` | does this caller still compose it? |
| `assertIn("clause", emitted)` | does the constant still say it? |

Clause-level pins on a composed constant look redundant. Keep them anyway, with
a comment saying why, or the next consolidation deletes them as duplication.

### A duplicate can be a guard, and deleting it deletes the guard

The sharpest form of this, found on the third review round of the task that
wrote this REQ.

`stop_gate.py` held a second literal copy of the boundary, and
`test_emitted_trust_boundary_equals_the_canonical_constant` compared it against
`_lib.TRUST_BOUNDARY`. Two independently written strings, so *any* content edit
to the constant reddened the suite. Collapsing that duplicate — the change this
very document argues for — was correct for ownership and silently removed the
only assertion in the suite that could observe the constant's content.

Measured: with the duplicate gone, flipping `must precede` to `need not
precede`, and `do not qualify` to `also qualify`, each left the whole suite
green. A shipped C-14 boundary asserting that coordinator paraphrases and
repository text *do* count as substantive results is a semantic inversion of
the protocol, and nothing observed it.

The element-noun pins could not: `TRUST_BOUNDARY_ELEMENTS` contains
`repository text` and `actual review PASS`, never the negation or the ordering
verb. **A pin on the nouns of a rule cannot see the rule invert.**

The fix is not to keep the duplicate in runtime code — ownership still matters —
but to move the second copy into the test that owns the invariant, where it is
plainly labelled as an independent copy rather than looking like drift. Before
deleting any duplicate, ask what asserts the two copies agree, and whether that
assertion is the only thing standing between a value and an unobserved change.

## Scoped instructions do not survive being hoisted

`attestation_endgame()` anchors its rerun ban to "after an actual QA PASS".
Hoisting the review-pending branch's `do not rerun review solely for a receipt`
into it therefore silently narrowed that ban: in the highest-risk state — a
review PASS final arrived with no receipt and QA has not run — the caller no
longer read any ban at all.

Before moving a clause into shared text, check that the shared text's
precondition is satisfied in every state that used to carry the clause. If it
is not, the clause belongs to the state, not to the shared block.

## Enforcement

- `test_direct_blocker_flow_preserves_structural_result_trust_boundary` — prose
  surfaces assert the nine elements; runtime surfaces assert `TRUST_BOUNDARY`
  appears at least twice, so an unused import does not satisfy the check.
- `test_lib_owns_exactly_one_literal_trust_boundary` — **four** assertions.
  The elements must be in the live constant, not merely somewhere in `_lib.py`
  (otherwise the file scan passes on a stale comment while the constant drops
  an element); `TRUST_BOUNDARY` must equal an **independently written literal**
  held by the test; `attestation_endgame()` must equal one too, because clause
  pins catch deletion but not inversion; and no discovered runtime `.py` under
  `plugin/` or `plugin-codex/` may hold a second literal copy.

  The second-copy check is a **heuristic over source text, not a parser.** It
  splices adjacent string literals before matching — both quote styles and an
  optional `+` — because Python's implicit concatenation otherwise hides a
  re-wrapped copy behind `delivered " "completion`. Two earlier versions were
  defeated by review: exact match fell to a re-wrap, and a double-quote-only
  splice fell to single quotes and to `+`. Treat it as raising the cost of an
  *accidental* re-inline. What actually protects the constant's content is the
  equality assertion.
  Why `attestation_endgame()` needs its own equality assertion in that same
  test: review measured three inversions that passed the full suite — "and then
  an actual QA PASS" → "or without an actual QA PASS", "task_verify once" →
  "once or as many times as needed", and an appended sentence permitting
  receipt-only reruns. Each ships an instruction contradicting C-14/C-17.
- `TrustBoundaryReachesEveryPendingNextAction` in `tests/test_lib_gate_helpers.py`
  — both `_lib` pending branches carry both constants, asserted below the gate.
- `test_task_verify_pending_never_prescribes_receipt_only_rerun` — drives
  `handle_task_verify` and asserts both constants reach the response.
- `test_every_normative_clause_in_both_next_actions_is_pinned` — whole-constant
  identity plus the head clauses each string genuinely owns.
- `test_both_pending_heads_label_a_receiptless_final_non_attesting` — the
  `NON-ATTESTING` label in both `_lib` pending heads. Deleting it leaves the
  emitted text reading "label it Only structurally delivered completion/final
  records…", and it is the label that keeps a real but unattested lens result
  usable for defect discovery while denying it close authority. Pre-existing
  gap, closed here rather than left because the incoherence is silent.

Thirteen mutations were exercised against the final tree and each reddens a
named test: deleting the `NON-ATTESTING` label from a pending head; dropping
the boundary from a composed constant; dropping the endgame from
a `_lib` branch; removing `directly`; breaking the `task_verify` composition;
deleting the missing-evidence guard; deleting the endgame's verb list; dropping
the `awaited` qualifier; dropping the precedence element from `TRUST_BOUNDARY`;
dropping each of the two state-scoped head bans; and re-inlining a literal
boundary in `harness_server.py` and in `stop_gate.py`.

The content mutations were found by review *after* the first sweep reported
clean — the first sweep only mutated composition sites, so it could not observe the
content problem described above.
