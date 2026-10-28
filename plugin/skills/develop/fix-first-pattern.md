# Fix-First Pattern

A pre-review self-check over your own diff, plus evidence-driven recovery for every
fix loop in develop.

## Pre-review self-check

After each AC and once over the whole diff before Phase 6.6 review, reread
your change against the minimum-sufficient ladder in
`${CLAUDE_PLUGIN_ROOT}/agents/developer.md` and fix what you find in the lines you changed:

- `delete`: dead code, unused flexibility, or a speculative feature you added.
- `reuse`: something you wrote that already exists in the codebase.
- `stdlib` / `native`: hand-rolled logic the standard library or platform ships.
- `yagni`: an abstraction with one implementation, a config nobody sets, or a
  defense against a state the code already rules out.

Stay inside the authorized outcome: do not rename, retype, or restructure
unrelated adjacent code. Necessary implementation alternatives amend the current
PLAN/WBS through its owner; they do not become new user requirements. Ask only
for an unresolved material user decision or missing authorization, including
external or destructive effects.

## Evidence-driven recovery

Applies to every authorized fix loop: per-AC, Phase 7 verification and browser
debugging. Attempt counts and file counts do not require renewed approval.
Do not blindly repeat an unchanged failing approach. When attempts yield no new
evidence, reread the failure, trace the actual caller and state transitions, and
use `hypothesis-driven-debugging.md` to distinguish product, fixture, tool and
environment causes before the next targeted repair.

Inspect actual owner state and effects after interruption or timeout. Retry only
when effects and active writers are known and retry is safe; unknown effects
remain unfinished with a concrete unblock condition. Preserve the same task/run,
failure history, ownership guards and applicable unchanged evidence.

Repair authorized failures autonomously. Restore affected independent review PASS
before actual QA; reuse unaffected applicable results. Required FAIL or
BLOCKED_ENV remains unfinished: elapsed effort never authorizes skip, threshold
reduction or completion. Keep concise causal evidence in working context.
Do not add an `attempts` key to canonical PROGRESS.md or another attempt ledger.
