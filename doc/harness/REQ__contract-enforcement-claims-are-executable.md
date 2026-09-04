---
tags: [harness, contracts, enforcement, lint]
summary: 계약의 Enforced by 필드는 실행되는 메커니즘을 지명해야 한다. 명시적 관례 선언은 예외지만, 존재하지 않는 훅을 지명하는 것은 결함이다.
updated: 2026-09-04
freshness: current
invalidated_by_paths:
  - CONTRACTS.md
  - CONTRACTS.local.md
  - plugin/scripts/contract_lint.py
  - plugin/hooks/hooks.json
  - plugin/skills/setup/templates/CONTRACTS.md
  - tests/test_contract_lint_real_tree.py
  - plugin/scripts/stop_gate.py
  - tests/test_stop_gate.py
---

# REQ — a contract's "Enforced by" names something that actually runs

## Expected behavior

`CONTRACTS.md` § 0 states the invariant: *"Prefer machine-enforced gates over
prose. A prose-only rule is commentary."* The **Enforced by** field is where a
contract makes that claim, so the field carries an obligation:

1. **A named mechanism must exist and must run.** If the field names a hook,
   that hook is registered in `plugin/hooks/hooks.json`. If it names a script,
   something invokes that script automatically — a hook, a gate, or a test.
   If it names an MCP tool, that tool performs the check.
2. **Convention may be declared, but only explicitly.** `Enforced by:
   Convention: ...` is a legitimate value — C-12 uses it, and the honest label
   is what makes it reviewable. What is not legitimate is naming an automated
   mechanism that no automation reaches.
3. **The claim is specific about *which* automation.** "contract_lint.py
   (SessionStart hook)" and "contract_lint.py (setup/explicit check)" are
   different promises with different reliability. The field states the one that
   is true.

A contract whose enforcement claim fails (1) is worse than a contract labelled
`Convention`: it reads as a machine gate, so reviewers stop checking it by hand,
while nothing checks it at all.

## Observed gap (2026-09-03)

C-11 (managed block not hand-edited) and C-13 (SKILL.md weight budget) both
named `plugin/scripts/contract_lint.py`. C-11 specified "(SessionStart hook)".
Measurement:

| Claim | Reality |
|---|---|
| `contract_lint.py` runs at SessionStart | Not registered in any `hooks.json` event. SessionStart ran an inline probe, `verification_gap_check.py`, and `drift_warn.py`. |
| Tests cover it | `tests/test_contract_lint.py` builds a `tempfile` repo; `tests/test_skill_weight_contract.py` writes under `tmp_path`. Neither touched this repository's `CONTRACTS.md` or real skill trees. |

Both contracts were therefore commentary, by the managed block's own
definition. The failure was silent in the way § 0 predicts — the lint logic was
correct, well-tested, and simply never pointed at the repository.

Two aggravating details:

- `plugin-codex/internal-skills/develop/SKILL.md` sat at exactly
  `SKILL_WEIGHT_LIMIT` (500). Zero headroom, and no automated observer.
- `contract_lint.py --check-weight` defaults `--plugin-root` to `./plugin`, so
  even a manual invocation never reached the `plugin-codex` tree that held that
  boundary file.

The root `CONTRACTS.md` had also drifted from
`plugin/skills/setup/templates/CONTRACTS.md`, which already read
"(setup/explicit check)". The false claim existed only downstream of the
template.

## Second instance (2026-09-03)

The same shape, found immediately afterwards in a different file.
`plugin/scripts/stop_gate.py`'s module docstring stated that "the reason text
now also names the exact next action — derived from emit_compact_context's
missing_for_close". It did not: the reason was a fixed paragraph, and the
derived state reached the caller only through the `next_action_command` field.
The claim had been prose since the 2026-05-12 retro that prompted it.

Worth noting because the claim was *about the code's own output*, not about an
external gate — the cheapest possible thing to verify, and still wrong for
months. `tests/test_stop_gate.py` now pins the behaviour rather than the
promise, including a counter-case so a constant string cannot satisfy it.

## Enforcement of this REQ

`tests/test_contract_lint_real_tree.py` runs `lint()` against the repository's
own `CONTRACTS.md` (no hard and no soft issues) and `check_skill_weights()`
against every discovered plugin root. The hard-drift and weight assertions are
each mutation-paired, because a scan that reaches nothing also reports nothing.
The soft-channel assertion is not mutation-paired there; its detector is
fixture-proven in `tests/test_contract_lint.py`, which is therefore load-bearing
coverage rather than a redundant duplicate. Recorded as C-102 in
`CONTRACTS.local.md`.

A hook was deliberately not added. Enforcement belongs on the cheapest surface
that still runs on every change; a SessionStart hook would charge every session
for a check that the suite already performs, which C-13's own weight budget
argues against.

## The gap this REQ tracked — RESOLVED 2026-09-04

Recorded here as history because the prediction below turned out to be the
useful part; the current rule lives in
`doc/harness/REQ__setup-template-installs-the-current-contract.md`.

**What it was.** The root `CONTRACTS.md` and the setup template had diverged
beyond the line fixed by this REQ's own task — § 0 wording, C-03's **Why**,
substantive drift in C-13 and C-14, and most consequentially **two whole
contracts absent from the template**: `C-17` (turn-end rule) and `C-14a`
(highest-available verification), matrix row and § 2 section alike. A fresh
`setup` therefore installed a `CONTRACTS.md` that never stated the turn-end
rule, while `stop_gate.py` enforced it from the first session.

**The prediction, which held.** This note warned that the lint's count
understated the gap — "16 contracts" for the template against 17 for the root,
a difference of one, because `^### (C-\d+)$` does not match the `C-14a` suffix
form — and that *"anyone scoping the follow-up from the count alone will fix
C-17 and re-ship a template still missing C-14a."*

The follow-up task confirmed something worse than the note assumed: because the
regex was blind on **both** sides of the comparison, `C-14a` had never been
four-field validated or matrix-cross-checked in the *root* either, and the root
§ 1 matrix had no C-14a row at all. Fixing the regex surfaced that immediately.

**Why it needed a comparison the lint could not do.** Both files linted clean in
isolation: the matrix check compares a file against itself, so template-vs-root
divergence was invisible by construction. That comparison now exists as
`tests/test_contract_lint_real_tree.py::SetupTemplateShipsTheSameContracts`,
which asserts the two files declare the same contract id set.
