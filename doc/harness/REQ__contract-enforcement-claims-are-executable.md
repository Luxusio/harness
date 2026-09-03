---
tags: [harness, contracts, enforcement, lint]
summary: 계약의 Enforced by 필드는 실행되는 메커니즘을 지명해야 한다. 명시적 관례 선언은 예외지만, 존재하지 않는 훅을 지명하는 것은 결함이다.
updated: 2026-09-03
freshness: current
invalidated_by_paths:
  - CONTRACTS.md
  - CONTRACTS.local.md
  - plugin/scripts/contract_lint.py
  - plugin/hooks/hooks.json
  - plugin/skills/setup/templates/CONTRACTS.md
  - tests/test_contract_lint_real_tree.py
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

## Known remaining gap

The root `CONTRACTS.md` and the setup template have diverged beyond the line
fixed here — § 0 wording, C-03's **Why**, substantive text drift in the C-13 and
C-14 bodies, and most consequentially **two whole contracts absent from the
template**: `C-17` (turn-end rule) and `C-14a` (highest-available verification).
Neither the § 1 matrix row nor the § 2 section exists for either. A fresh
`setup` in a new project therefore installs a `CONTRACTS.md` that never states
the turn-end rule, even though `stop_gate.py` enforces it in that project from
the first session.

The lint's own count understates this: it reports "16 contracts" for the
template against 17 for the root, a difference of one, because its
`^### (C-\d+)$` heading regex does not match the `C-14a` suffix form. Anyone
scoping the follow-up from the count alone will fix C-17 and re-ship a template
still missing C-14a.

Linting cannot surface this. Both files lint clean in isolation — the matrix
check compares a file against itself, so template-vs-root divergence is
invisible to it by construction. Detecting it needs a comparison the lint does
not perform. Out of scope for the task that wrote this REQ; tracked as follow-up
work.
