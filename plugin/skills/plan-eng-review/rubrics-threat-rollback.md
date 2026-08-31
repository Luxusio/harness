# Rubrics: Security Threat Model + Rollback depth

Imperative checklist for plan-eng-review Section 1 (Architecture review). **MUST be answered** inline in every Architecture review pass — not browsed on demand. Skipping a question is a compression violation unless the "skip if trivially N/A" condition applies (see below).

This rubric is a **plan-time gut-check**. For runtime security depth, invoke an external `/cso`-equivalent security skill separately — this rubric does not replace that. For harness-native primitives cited in each question, see `plugin/scripts/prewrite_gate.py` and `CONTRACTS.md` § C-05 / C-13.

## Security Threat Model (hybrid: 3 STRIDE + 3 harness-native)

Skip if the plan has zero new trust boundaries AND zero new writes (pure prose refactor, hygiene run, dotfile-only tweak). Otherwise answer all 6.

### STRIDE subset (external-threat lens)

S1. **Spoofing / Auth boundary** — does any new codepath cross a trust boundary (user→service, service→DB, external→internal, LLM-output→executor)? If yes, how is the identity or source verified?

S2. **Tampering / Data integrity** — does any new direct write mutate a protected artifact (PLAN.md, TASK.json, RECEIPTS.jsonl)? Is the owning skill, MCP tool, or hook the only normal-workflow writer (C-05 enforced by `prewrite_gate.py`)? Bash/shell mutation is outside Harness PreToolUse enforcement and must be modeled explicitly when relevant.

S3. **Information disclosure** — does any new log, error message, prompt, or artifact leak secrets, PII, absolute paths with usernames, internal infra names, or task-specific data that should stay private to the task directory?

### Harness-native (this-product lens)

H1. **Audit-trail preservation** — does any step risk breaking the PLAN.md → RECEIPTS.jsonl provenance chain? Receipt ordering and artifact-owner rules must remain intact.

H2. **Protected-artifact provenance** — does develop/verify touch any file in `PROTECTED_ARTIFACTS` without routing through the owning skill/MCP/hook? Any Bash step that writes PLAN.md or RECEIPTS.jsonl via `sed -i`, `>`, `>>`, `tee`, or `python -c open(...,'w')`?

H3. **Contract-bypass vector** — does the plan assume `HARNESS_SKIP_PREWRITE` is set as a normal flow? The bypass must be one-shot, logged as `gate-bypass` in learnings.jsonl, and justified. Flag any session-wide bypass as critical.

## Rollback depth (plan-level revert-safety)

Skip if the plan makes zero mutations to repo state (review-only, advisory-only, read-only). Otherwise answer all 4. Scope is plan-level: what reverts if develop halts before verification and close.

R1. **Blast radius (develop-fail)** — if implementation halts at AC-N where N < last, which file edits are already on disk? What touched paths are already recorded in PROGRESS.md? Zero is the target answer; list all non-zero mutations.

R2. **Schema safety** — does the change alter any on-disk schema (TASK.json, RECEIPTS.jsonl, `hooks.json`, `manifest.yaml`, restore-point format)? If yes, state the cutover and recovery behavior explicitly; unsupported legacy task-control formats must not gain fallback readers.

R3. **Feature-flag path** — can this change be disabled at runtime via a `HARNESS_DISABLE_*` env var or manifest toggle without a code revert? If no, justify why a binary `git revert` is acceptable for this change's blast radius; list the downstream tasks that would need replay.

R4. **Data migration reversibility** — if this change moves, renames, or deletes files under `doc/**`, `plugin/**`, or task directories, is there an undo script or an inverse operation documented in the commit message? `git revert` alone does NOT restore moved task directories if the move crossed a sync boundary.
