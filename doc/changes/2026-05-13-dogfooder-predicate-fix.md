# 2026-05-13 — dogfooder predicate reads task state instead of git diff

## What changed

`plugin/skills/develop/SKILL.md` Phase 7.7 used to decide whether to spawn the dogfooder via `git diff --name-only HEAD~1 HEAD`. That command returns the diff of the single commit at HEAD, which means:
- When develop's Phase 6 (Bisectable Commits) ran AFTER Phase 7.7, working changes were uncommitted → invisible.
- When develop emitted multiple bisectable commits, only the last commit was visible.

Net result: Phase 7.7 routinely read the PREVIOUS task's files. In a session probe immediately before this fix, the predicate returned `CONTRACTS.md`, `plugin/scripts/stop_gate.py` etc. — files from the prior closed task — while the current task's diff was `CLAUDE.md`, `plugin/CLAUDE.md`, `plugin/skills/develop/*`. The dogfooder was skipping or running based on the wrong file set.

The new predicate reads `TASK_STATE.yaml.touched_paths` directly. That field is the union of files modified during the task, maintained by `task_verify`, so it's always current regardless of when Phase 6 commits or how many commits develop emits.

## What didn't change

- The user-facing globs (which file types trigger dogfooder) are unchanged.
- The default-skip-on-error behavior is preserved: missing TASK_STATE.yaml or yaml parse error → empty → SKIP_DOGFOOD.
- The dogfooder agent itself is unchanged.

## Follow-up

- Other phases that call `git diff` for scope decisions should be audited for the same bug family. Likely candidates: any phase that runs before commits land.
- Encode the predicate as `plugin/scripts/should_dogfood.py` instead of inline python heredoc — prose-embedded scripts rot.
