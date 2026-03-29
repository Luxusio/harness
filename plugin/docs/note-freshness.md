# Note Freshness Reference

updated: 2026-03-28

This document specifies the freshness lifecycle for durable notes managed by the writer agent.

---

## Freshness States

| State | Meaning |
|-------|---------|
| `current` | Note has been verified and its source files have not changed since verification |
| `suspect` | A file in `invalidated_by_paths` has changed since `verified_at` — truth may have drifted |
| `stale` | Note has been `suspect` for more than 3 task completions without re-verification |
| `superseded` | Note has been replaced by a newer note; follow `superseded_by` chain |

---

## Metadata Fields

Every note carries the following freshness metadata (in the note header):

```yaml
freshness: current | suspect | stale          # default: current on creation
verified_at: <ISO 8601>                        # timestamp of last verification
derived_from: [list of source paths]           # files this note's truth depends on
confidence: high | medium | low               # writer's confidence at verification time
invalidated_by_paths: []                      # paths whose change makes this note suspect
verification_command: ""                       # optional shell command to re-verify
supersedes: <note-slug>                        # note this replaces (if applicable)
superseded_by: <note-slug>                    # reverse link (set on old note when superseded)
```

### Field semantics

- **`freshness`**: Current validity state. Set to `current` on creation; transitions are triggered automatically by file-change events or by re-verification.
- **`verified_at`**: Updated whenever a writer performs explicit re-verification or creates the note from fresh evidence.
- **`derived_from`**: Source files read to produce the note's content. Used to know what changes could affect note accuracy.
- **`invalidated_by_paths`**: The specific subset of `derived_from` (or additional paths) whose modification should trigger a `current` → `suspect` transition. OBS notes MUST populate this.
- **`confidence`**: Writer's judgment of how robust the evidence was. `high` = direct runtime observation; `medium` = inferred from related evidence; `low` = assumption.
- **`verification_command`**: If provided, running this command and observing its output constitutes re-verification. Enables automated freshness recovery.
- **`supersedes` / `superseded_by`**: Form a doubly-linked chain across note versions. Only the head of the chain (no `superseded_by`) is the authoritative note.

---

## Freshness Transitions

```
                     file in invalidated_by_paths changes
    current  ──────────────────────────────────────────► suspect
       ▲                                                      │
       │  critic-runtime PASS (related area)                  │  > 3 task completions
       │  OR writer re-verifies with new evidence             │  without re-verification
       └──────────────────────────────────────────── stale ◄──┘
                  only via explicit re-verification
```

### Transition rules

| Transition | Trigger |
|-----------|---------|
| `current` → `suspect` | Any file in `invalidated_by_paths` changes (file-changed-sync hook) |
| `suspect` → `current` | critic-runtime issues PASS covering the related area, or writer re-verifies with new evidence and updates `verified_at` |
| `suspect` → `stale` | Note has remained `suspect` across more than 3 task completions without re-verification |
| `stale` → `current` | Explicit writer re-verification only — writer must run evidence, observe result, update note content if needed, refresh `verified_at` |

---

## Retrieval Priority

When retrieving notes for context, prefer by freshness in this order:

1. **`current`** — use directly
2. **`suspect`** — use with caution; flag to the agent that the note may be outdated
3. **`stale`** — do not rely on without re-verification; flag explicitly
4. **`superseded`** — deprioritized; follow `superseded_by` link to the current head note

If the only note on a topic is `stale`, flag it as needing re-verification and do not pass it as authoritative context. Instead, prompt for re-verification before proceeding.

---

## Integration with file-changed-sync

The file-changed-sync hook (FileChanged harness hook) is responsible for marking notes `suspect` when their `invalidated_by_paths` are modified:

1. On each `FileChanged` event, collect the changed file paths.
2. For each active note (`status: active`, `freshness: current`), check if any changed path matches `invalidated_by_paths`.
3. If matched, transition `freshness: current` → `suspect`.
4. Record the transition in `TASK_STATE.yaml` under `suspect_notes`.

The writer agent is responsible for the reverse transition: restoring `suspect` → `current` after re-verification.

---

## Supersede Chains

When a note's content changes materially, the writer creates a new note and links the two:

**Old note (being superseded):**
```yaml
status: superseded
superseded_by: obs-api-response-format-v2
```

**New note (superseding):**
```yaml
status: active
freshness: current
supersedes: obs-api-response-format-v1
verified_at: 2026-03-28T12:00:00Z
```

Rules:
- Never silently overwrite a note — always create a supersede chain for material changes.
- Minor corrections (typos, wording) may be applied in-place with a refreshed `verified_at`.
- Only the head of the chain (no `superseded_by` set) is authoritative.

---

## Examples

### OBS note with full freshness metadata

```markdown
# OBS api obs-rate-limit-behavior
summary: API returns HTTP 429 with Retry-After header when rate limit exceeded
status: active
updated: 2026-03-28
freshness: current
verified_at: 2026-03-28T10:15:00Z
derived_from:
  - src/middleware/rate-limiter.ts
  - tests/integration/rate-limit.test.ts
confidence: high
invalidated_by_paths:
  - src/middleware/rate-limiter.ts
verification_command: "npm test -- --grep 'rate limit'"
evidence: Integration test `returns 429 on limit exceeded` PASS; curl confirmed Retry-After header present.
```

### REQ note (no invalidation paths needed — user-stated)

```markdown
# REQ root req-no-external-auth
summary: System must not depend on external auth providers; all auth must be self-hosted
status: active
updated: 2026-03-28
freshness: current
verified_at: 2026-03-28T09:00:00Z
derived_from: []
confidence: high
invalidated_by_paths: []
source: User statement 2026-03-28 session
```

### INF note showing medium confidence

```markdown
# INF api inf-db-pool-size
summary: Database pool size of 10 is likely sufficient for current load
status: active
updated: 2026-03-28
freshness: suspect
verified_at: 2026-03-20T14:00:00Z
derived_from:
  - src/db/pool.ts
confidence: medium
invalidated_by_paths:
  - src/db/pool.ts
verify_by: "Run load test at 100 RPS and observe pool exhaustion errors"
```
