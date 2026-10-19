# Requirement provenance

## User-sourced requirement

| Claim (writer interpretation) | Origin | Source | Exact evidence | Confirmed proposal (writer interpretation) |
| --- | --- | --- | --- | --- |
| Distinguish the user's actual requirements from LLM inventions and mistaken interpretations in REQ documents. | user_explicit | Conversation: initial provenance request and follow-up | “사용자가 요청한 요구사항이랑 LLM이 거기서 지어낸 요구사항을 구분하지 않고 다 REQ로 적는현상이 있는데 이것도 좀 구분하는 방법이 필요해.” / “사용자의 의도를 잘못 파악하고 REQ로 적는 경우가 있더라?” | — |
| Keep user requirements, AI-derived implementation work and unconfirmed candidates distinct in the same REQ; review source entailment, keep candidates out of mandatory scope and preserve routine implementation autonomy. | user_confirmed | Conversation: latest user approval following the REQ provenance proposal | “ㅇㅇ ㄱㄱ 그렇게 해 줘” | The immediately preceding proposal to separate these three groups, source excerpts from interpretation, and scoped approvals, using the existing REQ and review process without an extra ledger. This confirmation does not authorize unrelated assumptions. |

## Derived implementation constraints

The following mechanisms implement the user-sourced requirement; they were
chosen during implementation and are not additional requests from the user.

REQ claims distinguish `user_explicit`, `user_confirmed`, `derived`,
`assumption`, `proposal`, and `unattributed`. User-sourced claims include an
identifiable source and relevant exact excerpt. Confirmed interpretations
also name the particular proposal/scope confirmed. A bare “continue” or “yes”
does not authorize unrelated assumptions. Derived constraints cite their
existing requirement/technical basis and reasoning; they never become user
quotes or new business scope by changing a label. The table separates the
author's claim, source locator, exact quote/derivation basis and confirmed
proposal. The confirmation scope is authored interpretation, not part of the
user's exact quote.

The scaffold defaults to `candidate`, including empty-status inputs. Bare
legacy behavior text remains `unattributed`, not accepted intent. Explicit
accepted scaffolding requires at least one grounded user-origin item. Only
`user_explicit` and scoped `user_confirmed` rows can be accepted. An accepted
document may also contain derived work and candidates, but document status
does not accept those rows. Derived rows are implementation-only, never new
acceptance requirements. Their source identifies the existing requirement or
standing constraint and their evidence explains the derivation. Missing basis
stays unverified/non-binding. Missing user grounding in candidate input becomes
unattributed; accepted user input without grounding is rejected before writing.
Candidate items remain non-binding, including when appended to a historical
accepted document. Existing documents are not bulk relabelled as verified
user requirements. Intent summaries, verification methods and implementation
choices are author interpretations unless independently sourced.

Source labels and nonempty quotes are structural evidence, not semantic
proof. Independent document review compares each claim with accessible user
messages or cited documents: an authentic quote can still fail to support an
added detail. Inaccessible evidence stays unverified. Reject scope expansion,
misread intent and self-authored approval; do not manufacture a substitute
quote. Direct REQ edits follow the same rule as scaffolded content.

Use the same REQ, PLAN and existing review process. No extra approval ledger
or prompt-capture service is required. Routine implementation decisions stay
autonomous in PLAN/ADR; ask only when unresolved ambiguity changes the user's
outcome, scope or other material decision. Unconfirmed assumptions cannot be
used as mandatory WBS outcomes, acceptance criteria or close blockers.
Supported derived work may be implemented and checked as necessary to satisfy
an accepted outcome; it cannot introduce a new product outcome, external cost,
service or user-facing constraint merely by being labelled derived. These
boundaries apply when new requests or development discoveries update WBS too.

## Verification

Scaffold regressions cover candidate defaults, accepted free-text rejection,
missing/blank evidence, scoped confirmation, nonbinding derived rows, mixed origins,
candidate append under an accepted document and refusal before file writes.
Document review checks semantic support against the actual source; a keyword
match or asserted origin alone cannot establish it.
