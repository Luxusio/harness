# Change: harness setup skill — completeness principle enrichment
date: 2026-04-09
task: TASK__harness-setup-completeness
file: plugin2/skills/setup/SKILL.md

## Decisions
- Adopt "boil the lake, flag the ocean" framing from gstack office-hours as the canonical completeness model for setup skill recommendations.
- Always show dual effort scale (human: ~X / CC: ~Y) per option so cost of completeness is concrete, not abstract.
- Humor guidance scoped narrowly: dry observations about software absurdity only; never forced, never AI self-referential.

## Changes
- `plugin2/skills/setup/SKILL.md` grew from 510 to 540 lines (+30).
- Voice section gained `Humor:` block and `Final test:` check after banned phrases.
- AskUserQuestion Format item 3 added: `Completeness: X/10` calibration (10=all edge cases, 7=happy path, 3=shortcut) plus `(human: ~X / CC: ~Y)` scale.
- New `## Completeness Principle — Boil the Lake` section inserted after AskUserQuestion Format with lake/ocean framing and 4-row effort reference table (~100x to ~20x compression ratios).

## Caveats
- Effort table figures (e.g. "Boilerplate: 2 days human / 15 min CC+harness") are illustrative benchmarks, not measured data.
- Change is confined to plugin2; plugin/ (harness1 templates) was not updated — no template-sync obligation triggered since plugin2 is a separate plugin root.

## Verification
- critic-runtime PASS: all 6 grep ACs confirmed at expected line numbers (196, 207, 186+212, 99, 103).
- Line count 540 meets AC-006 minimum.
- No regression to prior enrichments (Voice banned vocab, Context Recovery, Prior Learnings, Smart Defaults from TASK__harness-setup-enrich).
