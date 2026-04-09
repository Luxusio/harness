# harness2 setup skill enrichment
date: 2026-04-09
task: TASK__harness2-setup-enrich

## Decisions

- Three features backported from gstack office-hours into plugin2 setup skill: Voice depth, Context Recovery, Prior Learnings.
- Banned AI vocabulary list (delve, robust, comprehensive, etc.) added to Voice section as an explicit constraint, not a suggestion.
- Context Recovery scans doc/harness/ for existing artifacts to survive context compaction gracefully.

## Changes

- `plugin2/skills/setup/SKILL.md` expanded from 397 to 510 lines.
- Voice section grew to 46 lines with banned vocabulary, writing rules, and user sovereignty principles.
- Context Recovery (line 109) and Prior Learnings (line 143) sections added; learnings.jsonl referenced at 6 locations.

## Verification

- All 5 ACs passed via CRITIC__runtime (verdict: PASS, 2026-04-09).
- wc -l confirmed 510 lines; grep confirmed banned vocab at line 91, sections at lines 109 and 143.
