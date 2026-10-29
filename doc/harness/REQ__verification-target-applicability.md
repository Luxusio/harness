# Verification target applicability

## User requirements

Users request reusable review and QA evidence bound to actual inputs,
relevant-change invalidation, preserved authentic history, diagnostic experiments
without product acceptance inflation, and no repeated checks solely to repair
recording. Restoring identical source at another path preserves applicability.
Unrelated documentation edits must not invalidate product QA.

## Approved correction (2026-10-29)

user_confirmed: “ㄱㄱ 차례대로” approves the immediately preceding audit's finding that a retired review lens still blocks close. Derived from that approval and the current-lens declarations: active applicability follows canonical current `TASK.json.required_lenses`, while immutable historical targets remain available. A retired lens cannot block current checks; reintroducing it again requires applicable target inputs. This does not waive independent reviews or actual QA.

## Derived mechanism

Before review/QA against a copied source view, the owner uses
`wbs(action="target", wbs_id=..., target=...)`. The target declares explicit WBS
`inputs`, argv-array `commands`, `lenses`, and `purpose` (`acceptance` or
`diagnostic`), plus optional environment key names and toolchain executable names.
Actual platform, toolchain contents and selected environment hashes join the
copied input manifest. Build flags, dependencies and configuration belong in
those actual inputs. Independent review checks scope adequacy.

The returned persistent `snapshot_root` contains copied, read-only inputs with
no hardlinks to the source. Execute roles and declared commands against that
exact root. Names are for operator readability; they do not bind verification
authority. There is no spawn-name parser, watcher or lifecycle attestation.

Declare actual runtime output directories through `output_dirs`. Place caches
and fixture/temp artifacts outside selected inputs or disable optional caches.
This repository uses `output_dirs:["doc/harness/tasks"]` for its existing pytest
session guard. Every input remains read-only. Output paths cannot overlap or
be ancestors of selected inputs, and must be explicitly excluded when inside a
selected source root. Output preparation is not source identity.

Use the latest returned snapshot root after output preparation. A reconstructed
view can retain identical input identity at another path. Do not chmod frozen
sources or omit required inputs to repair an output failure. Equivalent selected
bytes, commands, environment, toolchain and review scope preserve applicability;
rerun only actually blocked or changed substantive execution. No missing
recording warrants a rerun.

## Observable behavior

- Issued snapshot A stays A while the checkout changes A → B → A. Applicability
  compares actual selected inputs and execution context with the copied view.
- Actual independent code review and conditional security review PASS precede
  actual QA. Direct independent finals supply workflow findings; copied text,
  coordinator summaries and diagnostic runs cannot replace them.
- Context/open/plan do not invent source certification. Explicit verify, close
  and verified installation enforce current declared source applicability.
  Machine verdicts measure current required WBS checks and do not attest review/QA.
- Relevant changes require an appropriate new target and affected substantive
  verification. Equivalent content restored elsewhere stays applicable;
  unselected documentation edits do not stale product results.
- Missing copied views can be reconstructed only from identical current bytes.
  A stale or unavailable target names its actual correction and cannot authorize close.
- Diagnostic targets may declare no lenses for direct runner experiments. They
  cannot replace required acceptance checks or independent review/QA.
- Target history remains in existing task work storage, with a bounded active
  lens index in WBS. No second completion ledger or scheduler is introduced.
- Final publication checks that task/run, control, WBS and target inputs still
  match. Measurement is private in-memory state; close and verified installation
  retain fresh applicability checks and stable source snapshot requirements.

A copied view is not an OS sandbox. Tools must receive the explicit snapshot
cwd; repository autodiscovery must not silently select mutable source. Same-UID
hostile owners and arbitrary shell mutation remain outside this protection.

## Input limits and errors

Canonical WBS policy and streaming measurement budgets apply. Required files
are not removed to obtain PASS. Unsupported symlink/nonregular inputs, missing
dependencies, unsafe ownership/modes, nested checkout roots and recursive
task/runtime inputs fail explicitly. Select concrete nested-module inputs.
Target metadata is bounded to 1 MiB per immutable record; refine actual scope
when needed without hiding required evidence.

Owner-private readable source such as 0600/0640 remains valid without changing
original permissions. Descriptor reads enforce ownership, single link, no-follow
and stability. Reject group/other write, special permission bits, unreadable owner
modes and executable bits without owner execute. Copies remain private and
read-only. Installer payload and frozen-copy permissions retain their own checks.

## Verification

Exercise copied-input ABA behavior, equivalent restoration, unrelated doc edits,
changed command/environment/toolchain context, output isolation, missing views,
unsafe paths, and current applicability at verify/close/install. Preserve actual
independent review-before-QA without lifecycle collection or name binding. See
[receipt-free execution and completion](REQ__receipt-free-execution-and-completion.md).
