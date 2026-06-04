---
date: 2026-06-04
source: user dogfooding retrospective
type: feedback
freshness: current
---

# Harness Dogfooding Friction: Mobile UI Iteration Session

Context: a long session on a Next.js web/Tauri/Capacitor comic reader with
`browser_qa_supported: true`. The work mixed full `plan -> develop -> verify
-> close` loops with `micro` tasks while iterating on mobile UI details.

This is feedback for the harness plugin backlog, ordered by expected developer
time saved. Repository review on 2026-06-04 confirmed most implementation
touchpoints exist in `plugin/scripts/_lib.py`, `plugin/scripts/update_checks.py`,
`plugin/scripts/verify_runner.py`, and the browser-QA regression tests.

## P1: Durable-doc edits stale runtime QA

Observed behavior: `task_verify` / `task_close` resync touched paths and mark a
fresh runtime verdict stale when files changed after `CRITIC__qa.md`. Current
code already skips task artifacts, `doc/harness/**`, and `doc/changes/**`, but
REQ/GUIDE/ADR/POLICY docs under roots such as `doc/common/**` or `doc/ui/**`
can still make runtime QA stale even though they do not change runtime behavior.
This can cause repeated browser QA runs after documentation-only edits.

Tension: the prewrite gate asks agents to create durable requirements before
source work, while close gates tend to require QA to be the freshest artifact.
REQ or handoff updates can fall between those constraints and force redundant
verification.

Proposal: route durable-doc-only edits through the existing document critic
freshness path instead of runtime verdict staleness. Runtime PASS should be
invalidated by source, test, build, fixture, or config changes that can affect
behavior. Implementing this likely means reusing the durable-doc classifier near
`_durable_docs_touched()` / `_document_critic_status()` rather than broadening
the stale skip list to every `doc/**` path.

## P2: Micro UI tweaks need a lighter runtime tier

Observed behavior: when `browser_qa_supported: true`, closing even a one-line
CSS tweak requires a fresh browser QA PASS. In this session the user repeatedly
verified on a real device and declined browser QA because the browser harness was
slow and sometimes rendered the wrong mode. The result was either redundant QA
or micro tasks left open.

Proposal: for `execution_mode: micro` or a future UI-tweak mode, allow a lighter
runtime verification tier:

- deterministic developer checks such as typecheck, lint, and relevant tests;
- explicit user-attested device verification recorded in HANDOFF or QA evidence;
- browser QA still available when no user attestation exists or the change has
  broad behavioral risk.

## P3: Add JSON/data lint to verify manifests

Observed behavior: a stray backtick in an i18n JSON file caused the app to 500.
Typecheck and unit tests did not catch it early due to cache/import timing; the
failure surfaced later through browser QA.

Proposal: add a low-cost JSON parse lint step to harness verify manifests,
especially for data and i18n directories such as `tags-i18n/*.json`. This would
catch a broad class of build-breaking data edits before browser QA.

## P4: Gate errors should state the missing field or flag

Observed behavior: gate failures sometimes described the wrong level of problem:

- `task_close` reported `missing: Self-Healing Candidates section` even when the
  section existed; the actual issue was status/field format.
- functional AC promotion failed quietly with unrelated guidance about missing
  `kind` fields; the actual issue was missing test evidence or the need for a
  `--no-test-required` style path.

Proposal: gate failures should name the exact missing or invalid field, the
artifact being parsed, and the minimal corrective action. For self-healing
candidates, include classification hints such as when a one-off fix should be
`rejected` instead of `applied`.

## P5: Reduce slop hook false positives for valid fallback usage

Observed behavior: slop warnings fired on legitimate `fallback` usage, including
React Suspense fallback UI, router history fallback behavior, and deep-link
fallback handling. The warning became noise rather than useful signal.

Repository review note: the warning source was not found in the checked-in
`plugin/scripts/hook_pre_tool_use.py` path, which currently delegates to
`prewrite_gate.py`, `qa_delegation_gate.py`, and `mcp_bash_guard.py`. This may
live in a host/runtime-level hook outside this repository, or under a generated
install artifact not present in the checkout.

Proposal: whitelist common valid contexts such as Suspense, history navigation,
deep-link resolution, and user-facing recovery paths.

## P6: Add an explicit polish or batch mode for related micro changes

Observed behavior: very small edits paid the full REQ/HANDOFF/verify/close
ceremony. In practice, several related visual tweaks accumulated under one open
micro task, which blurred scope and provenance. Starting a new mutating task
while another micro task was open also made the C-09 boundary feel ambiguous.

Proposal: add a polish/batch mode for related small edits under one REQ with one
verification pass at the end, or provide an explicit path to expand the open task
scope with clear C-09 guidance.

## Keep: heavyweight planning for large work

The dual-voice plan process was useful on larger work. It caught design and
scope issues before implementation, including unnecessary component work,
feature demotion risk, and missing core UI elements.

Recommendation: keep the heavyweight plan path for large changes, but route
micro and visual-polish work through a lighter path automatically.

## Prompt improvement: require evidence-backed backlog shaping

Some raw retrospective claims can be directionally useful but technically
overbroad. Example: "durable docs stale runtime QA" was directionally correct,
but repository review showed that `doc/harness/**`, `doc/changes/**`, and task
artifacts were already skipped; the remaining issue is narrower durable docs
under roots such as `doc/common/**` or `doc/ui/**`.

Recommended prompt rule for harness-maintenance agents:

1. Treat user or agent retrospectives as hypotheses, not implementation facts.
2. Before proposing a harness change, inspect the current code and tests for the
   named gate, hook, or artifact.
3. Classify each item as confirmed, partially confirmed, already handled,
   duplicate, not found, or needs runtime/install-artifact investigation.
4. When partially confirmed, rewrite the issue to the smallest accurate failing
   case before planning implementation.
5. Preserve existing safety intent. If a proposal weakens a gate such as
   browser QA, propose a new explicit evidence tier instead of silently removing
   the gate.
6. Include file/function/test references in the backlog item so the next agent
   can start from evidence rather than redoing the same search.

Suggested prompt snippet:

```text
When reviewing dogfooding feedback, do not convert claims directly into tasks.
For each claim, first find the owning code path and tests. Report:
status={confirmed|partial|already-handled|not-found|needs-runtime-check},
evidence=<file:function/test>, corrected_scope=<smallest true problem>,
safe_fix_direction=<change that preserves the original gate's safety intent>.
If evidence contradicts the claim, rewrite the proposal before planning.
```
