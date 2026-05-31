# OBS design-planning harness friction
tags: [observation, harness, planning, design-iteration, status:active]
summary: Harness preserves durable decisions well, but product/design exploration needs a lighter loop than full implementation tasks.
updated: 2026-05-31
freshness: current
confidence: medium
source: User-requested retrospective. First section (2026-05-29) covered planning/design exploration; second section (2026-05-31) covers implementation-track friction from a long product-dev session.

## Observation

Harness helped preserve durable decisions that would otherwise remain only in
chat history. The doc gate is especially useful when a user corrects product
direction, rejects a UI pattern, or establishes a reusable process rule.

The same flow is heavier during rapid product/design exploration. Those loops
often move through critique, doc adjustment, prompt or artifact update, review,
and another iteration. A full implementation-style task scaffold can cost more
attention than it saves when there is no runtime behavior to verify.

## Friction Points

1. Full task scaffolding is too heavy for exploratory design rounds.
   `PLAN`, `CHECKS`, `CRITIC__qa`, `DOC_SYNC`, and `HANDOFF` fit code changes,
   but feel mismatched for concept review, prompt iteration, and image-only
   artifacts.

2. The correct documentation target is not always obvious. One user correction
   can affect source-of-truth specs, flow docs, design docs, prompt files, and
   concept review notes. The agent must manually decide which documents need
   sync.

3. Concept artifacts lack first-class state. Old prompts, rejected visuals, and
   superseded notes remain searchable beside current guidance, so future agents
   can accidentally treat stale context as active.

4. Generated artifact tracking is manual. Important metadata such as source
   prompt, generated path, accepted path, review verdict, caveats, and
   replacement history is usually scattered across prose notes.

5. User-perspective review is useful but not integrated as a lightweight mode.
   Reviewer findings can catch planning conflicts, but they still need manual
   copy/paste into durable concept notes.

6. Stale background or subagent work can create operational uncertainty. The
   main agent needs a clear active-agent inventory and a cleanup reminder after
   findings have been integrated.

7. The doc gate can encourage over-documentation during live ideation. Harness
   needs a clearer distinction between exploratory hypotheses and accepted
   durable decisions.

## Improvement Candidates

Add a lightweight concept task mode for planning, UX, design, and image
iteration. A concept round could use `CONCEPT.md`, `PROMPTS.md`, `REVIEW.md`,
`ARTIFACTS.yaml`, and `DECISIONS.md` instead of full implementation QA
artifacts.

Add a doc target resolver. Given a concise decision statement, harness should
suggest the likely source-of-truth document, related documents that need sync,
and concept files that should be marked superseded.

Add decision state labels for durable docs and concept artifacts:
`accepted`, `candidate`, `rejected`, `superseded`, and
`implementation-caveat`.

Add artifact manifest automation for generated images and other concept assets.
The manifest should record artifact id, file path, source tool, prompt
reference, review verdict, caveats, and replacement history.

Add a lightweight UX review runner that can run configured reviewer lenses and
append findings directly to a concept folder's review notes.

Add stale-agent visibility and cleanup. Harness should expose active agent id,
task, age, and status, then prompt cleanup once review output is captured.

Add a doc sync conflict check. Even a configurable grep-style check for rejected
labels, stale CTAs, superseded prompt patterns, or conflicting source-of-truth
phrases would catch many design-doc regressions.

Add an explicit planning-only close path. It should record that runtime
verification is not applicable, while artifact verification and document sync
were completed.

## Recommended Priority

1. Lightweight concept task mode.
2. Artifact manifest automation.
3. Decision state labels.
4. Doc target resolver.
5. UX review runner with durable note append.
6. Stale-agent visibility and cleanup.
7. Doc sync conflict checks.
8. Planning-only close path.

---

## Implementation-track friction (2026-05-31)

Source: long product-development session in another repo (`hipago`) where harness drove code changes.
Confidence: high — root cause for the user-visible pollution bug confirmed by file mtime diff.

### Critical bug — stale install propagates pollution to non-harness repos

`learnings.jsonl`, `runtime/background.json`, and `background.json.lock`
appeared in repos where harness was never set up. Root cause: the installed
plugin at `~/.claude/harness-dev/plugin/` lagged source by ~6 days. Commit
`0c5dd7b` (2026-05-27) added `is_harness_enabled_repo` guards to 10 hook
scripts, but the install had not been refreshed since 2026-05-21. Source IS
guarded — install propagation failed. The post-task `install.py --force`
rule (CLAUDE.md) did not propagate for those commits.

Process gaps that allowed this:

- No SessionStart drift-warn (source SHA vs installed SHA).
- No release-time test that AST-greps every hook in `plugin/hooks/hooks.json`
  and asserts an `is_harness_enabled_repo` guard before any write.
- `install.py --force` is convention-only; nothing fails loudly when it is
  skipped.

### Heavy implementation ceremony for trivial changes

A one-line config or cache-purge still pays full ceremony: PLAN → CHECKS →
implement → update_checks per AC → spawn qa lens → HANDOFF → DOC_SYNC →
task_close → commit + force install. For changes that are not runtime-verifiable
locally (Java/device-pending), the qa-* agent re-runs the same vitest/tsc/lint
already run inline and adds 2–4 min per task with no new evidence.

Recommended: `size: trivial / risk: low` lightweight track. Self-verifiable
changes (inline test + lint + tsc clean) skip the qa-agent spawn; CRITIC__qa.md
becomes a one-line "no remote verification path, inline checks attached" stub.
Plan-skill dual-voice review is also overkill for single-file edits with no
design surface.

### task_close error messages mislead

`Status: captured` in HANDOFF triggered "missing Commit-backed Learnings"
even though the section existed. The actual rule was "captured items must
name a commit-eligible repo artifact path". Cost: one rewrite cycle.
`stop_gate._next_action_for_missing` already maps these — the close-gate
should reuse the same phrasing instead of "missing".

### task_blocked input validation rejects valid payloads

`unblock_condition` parameter was rejected 5× despite being supplied. Could
not park `TASK__sqlite-treeshake-webpack-fix` as BLOCKED_ENV. Likely an
empty-after-trim or whitespace-only check in the MCP tool input handler.

### SLOP false positives on legitimate defensive code

The slop heuristic fired 10+ times on "fallback" in commits that were
user-approved + test-verified defensive paths. The noise pushed toward
self-censoring legitimate patterns. Detector needs context — keyword +
adjacent test evidence + commit-eligible artifact reference should suppress.

### Standing user constraints not captured durably

Constraints surfaced mid-session ("commit only, never push", "pnpm install
forbidden — 9p driver kills it", "QA theater forbidden", "DB init must not
time out") lived in volatile conversation context. One compaction would
drop them. `CONTRACTS.local.md` is the right home, but there is no
prompt-time helper that detects "this looks like a standing rule" and offers
to capture it.

### task_close does not commit its own durable outputs

`doc/changes/*.md` and DOC_SYNC.md remained untracked after close. Without a
follow-up developer commit, institutional memory accumulates as `??`. Close
gate should either auto-stage or auto-commit harness-owned doc outputs.

### Routine git pollution in working repos

In the actively-worked repo (`hipago`), tracked runtime files
(`runtime/background.json`, `.hygiene-last-run`, `.hygiene-observe.log`)
churn every session. The `.gitignore` covers `learnings.jsonl` /
`timeline.jsonl` / `tasks/` but misses the three above. `git status` is
permanently dirty. Setup should ship a complete `.gitignore` template, and
existing repos need a `git rm --cached` migration step.

### Recommended priority (implementation-track)

1. Stale-install drift warn at SessionStart + release-time guard-coverage test.
2. `size: trivial` lightweight track (skip qa-agent for self-verifiable changes).
3. Auto-commit / auto-stage durable harness outputs at task_close.
4. Complete `.gitignore` template + `git rm --cached` migration for existing setups.
5. Standing-constraint capture prompt.
6. task_blocked input validation fix + task_close error message rewrite.
7. SLOP detector context-awareness (suppress on test-verified, user-approved diffs).
