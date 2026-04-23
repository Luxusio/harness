# Rubrics: Security Threat Model + Rollback depth

Imperative checklist for plan-eng-review Section 1 (Architecture review). **MUST be answered** inline in every Architecture review pass — not browsed on demand. Skipping a question is a compression violation unless the "skip if trivially N/A" condition applies (see below).

This rubric is a **plan-time gut-check**. For runtime security depth, invoke an external `/cso`-equivalent security skill separately — this rubric does not replace that. For harness-native primitives cited in each question, see `plugin/scripts/prewrite_gate.py`, `plugin/scripts/mcp_bash_guard.py`, and `CONTRACTS.md` § C-05 / C-13 / C-16.

## Security Threat Model (hybrid: 3 STRIDE + 3 harness-native)

Skip if the plan has zero new trust boundaries AND zero new writes (pure prose refactor, hygiene run, dotfile-only tweak). Otherwise answer all 6.

### STRIDE subset (external-threat lens)

S1. **Spoofing / Auth boundary** — does any new codepath cross a trust boundary (user→service, service→DB, external→internal, LLM-output→executor)? If yes, how is the identity or source verified?

S2. **Tampering / Data integrity** — does any new write mutate a protected artifact (PLAN.md, CHECKS.yaml, HANDOFF.md, DOC_SYNC.md, CRITIC__qa.md)? Is the owning skill or CLI the only writer (C-05 enforced by `prewrite_gate.py` and `mcp_bash_guard.py`)? Any Bash pattern that could slip past the guard?

S3. **Information disclosure** — does any new log, error message, prompt, or artifact leak secrets, PII, absolute paths with usernames, internal infra names, or task-specific data that should stay private to the task directory?

### Harness-native (this-product lens)

H1. **Audit-trail preservation** — does any step risk breaking the PLAN.md → CHECKS.yaml → AUDIT_TRAIL.md provenance chain? Append-only for AUDIT_TRAIL, `reopen_count` preserved, artifact-owner rule respected on every write?

H2. **Protected-artifact provenance** — does develop/verify touch any file in `PROTECTED_ARTIFACTS` without routing through the owning skill/CLI? Any Bash step that writes CHECKS.yaml / HANDOFF / DOC_SYNC via `sed -i`, `>`, `>>`, `tee`, or `python -c open(...,'w')`?

H3. **Contract-bypass vector** — does the plan assume `HARNESS_SKIP_PREWRITE` or `HARNESS_SKIP_MCP_GUARD` is set as a normal flow? Each bypass must be one-shot, logged as `gate-bypass` in learnings.jsonl, and justified. Flag any session-wide bypass as critical.

## Rollback depth (plan-level revert-safety)

Skip if the plan makes zero mutations to repo state (review-only, advisory-only, read-only). Otherwise answer all 4. Scope is plan-level (what reverts if develop halts mid-task) — not artifact-level, because harness has no rollback primitive for CHECKS.yaml / AUDIT_TRAIL.md / HANDOFF.md / DOC_SYNC.md beyond PLAN.md restore-points.

R1. **Blast radius (develop-fail)** — if implementation halts at AC-N where N < last, which ACs already reached `implemented_candidate` in CHECKS.yaml? Which file edits are already on disk? What touched_paths are already stamped in TASK_STATE.yaml? Zero is the target answer; list all non-zero mutations.

R2. **Schema safety** — does the change alter any on-disk schema (TASK_STATE.yaml 7-field, CHECKS.yaml AC schema, `hooks.json`, `manifest.yaml`, restore-point format)? If yes, is the migration forward-AND-backward compatible with existing in-flight tasks, or does revert require manual `maintain_restore.py`-style repair? See C-16 archive pattern for precedent.

R3. **Feature-flag path** — can this change be disabled at runtime via a `HARNESS_DISABLE_*` env var or manifest toggle without a code revert? If no, justify why a binary `git revert` is acceptable for this change's blast radius; list the downstream tasks that would need replay.

R4. **Data migration reversibility** — if this change moves, renames, or deletes files under `doc/**`, `plugin/**`, or task directories, is there an undo script (`maintain_restore.py`-style) or an inverse operation documented in the commit message? `git revert` alone does NOT restore moved task directories if the move crossed a sync boundary.
