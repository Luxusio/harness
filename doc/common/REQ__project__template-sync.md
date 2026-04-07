# REQ project template-sync
tags: [req, root:common, source:user, status:active]
summary: All runtime changes must propagate to setup templates — this repo is self-referential (plugin source = setup output origin)
source: user directive on 2026-03-30
updated: 2026-03-30
freshness: suspect
verified_at: 2026-03-30T00:00:00Z
confidence: high
invalidated_by_paths:
  - plugin/skills/setup/templates/gitignore-harness
  - plugin/skills/setup/templates/CLAUDE.md
  - plugin/skills/setup/SKILL.md

## Rule

This repository IS the harness plugin. Files under `plugin/skills/setup/templates/` are the origin for what `/harness:setup` generates in target projects.

When any of the following change in the plugin source, the corresponding setup template MUST be updated in the same commit:

| Source change | Template to sync |
|---------------|-----------------|
| `.gitignore` patterns | `plugin/skills/setup/templates/gitignore-harness` |
| `CLAUDE.md` operating rules | `plugin/skills/setup/templates/CLAUDE.md` |
| `_lib.py` path constants (`TASK_DIR`, `MANIFEST`) | All templates referencing those paths |
| Hook output JSON schema | Setup SKILL.md procedure text (if it describes hook behavior) |
| Agent definition changes | Setup SKILL.md Phase 4 minimum output set |
| Critic rubric changes | `plugin/skills/setup/templates/.claude/harness/critics/*.md` |
| New gitignored paths | Both `.gitignore` and `gitignore-harness` template |

## Why

Without this rule, the plugin source diverges from what setup generates. New projects bootstrapped by `/harness:setup` get outdated defaults while the plugin itself has moved on.
