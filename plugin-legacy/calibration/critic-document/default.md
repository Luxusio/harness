# Calibration: critic-document / default

> These examples help calibrate judgment. They are reference patterns, not a rigid checklist.

## False PASS pattern A — OBS note missing invalidated_by_paths

**Scenario**: Writer created an OBS note documenting API response format after adding a new endpoint.
**What was submitted**: Note has `freshness: current`, `verified_at` set, but `invalidated_by_paths: []` (empty).
**Why this should FAIL**: OBS notes MUST have `invalidated_by_paths` populated. Without it, the file-changed-sync hook cannot transition the note to `suspect` when the source files change. An OBS note with empty `invalidated_by_paths` will never become suspect, meaning stale observations will silently appear `current`.
**Correct verdict**: FAIL — OBS note has empty `invalidated_by_paths`; must reference the source file(s) the observation was derived from

---

## False PASS pattern B — DOC_SYNC.md claims "none" but doc files changed

**Scenario**: Developer added a new agent file and updated `plugin/CLAUDE.md` index during implementation.
**What was submitted**: DOC_SYNC.md states `notes_created: none`, `index_updates: none`, `docs_changed: none`.
**Why this should FAIL**: `git diff --name-only` shows `plugin/CLAUDE.md` was modified. DOC_SYNC.md claiming "none" when a doc file actually changed on disk is an explicit hard FAIL condition. The critic must compare DOC_SYNC.md claims against actual disk state.
**Correct verdict**: FAIL — DOC_SYNC.md claims no changes but `plugin/CLAUDE.md` was modified; doc_sync_drift detected

---

## False PASS pattern C — superseded note still active

**Scenario**: Writer created a new note `obs-db-pool-v2` to supersede `obs-db-pool-v1`. DOC_SYNC.md records the supersede action.
**What was submitted**: `obs-db-pool-v2` has `status: active`, `supersedes: obs-db-pool-v1`. But `obs-db-pool-v1` still has `status: active` (not updated to `superseded`).
**Why this should FAIL**: A broken supersede chain means two notes with contradictory content are both marked active. The old note must be updated to `status: superseded` with `superseded_by: obs-db-pool-v2` to maintain chain integrity.
**Correct verdict**: FAIL — supersede chain broken; `obs-db-pool-v1` still `status: active` after being superseded

---

## Correct judgment example

**Scenario**: New OBS note created documenting rate-limit behavior; root CLAUDE.md index updated.
**Evidence presented**:
- `obs-rate-limit-behavior.md` exists on disk with `freshness: current`, `invalidated_by_paths: [src/middleware/rate-limiter.ts]`
- DOC_SYNC.md lists note under `notes_created`
- `plugin/CLAUDE.md` updated index entry confirmed in `git diff`
- No prior note on same topic — no supersede chain needed
**Verdict**: PASS — note exists on disk, DOC_SYNC.md accurately reflects creation, `invalidated_by_paths` populated for OBS note, root index updated.
