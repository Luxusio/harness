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

---

## Prompt Memory Integration

The prompt memory system (`plugin/scripts/prompt_memory.py`) uses note freshness metadata when selecting context for injection into user prompts.

### Freshness weights

| State | Weight | Behavior |
|-------|--------|----------|
| `current` | 1.0 | Included directly, no label |
| `suspect` | 0.5 | Included with `[suspect]` label — may be outdated |
| `stale` | 0.1 | Included only if no better candidates, with `[re-verify needed]` label |
| `superseded` | 0.0 | Excluded entirely — follow superseded_by chain instead |

### Selection budget

The prompt memory system selects context within a 600-character budget:

| Slot | Count | Source |
|------|-------|--------|
| Relevant notes | Top 2 | All `doc/*/` notes scored by multi-signal formula |
| Active task | Top 1 | Open tasks scored by prompt relevance |
| Recent verdict | Top 1 | Most recent critic verdict |
| Blocker | 1 (if any) | Tasks with `status: blocked_env` |
| Tooling hints | As-is | From manifest tooling/profile flags |

### Scoring

Note relevance is computed using a 5-signal linear combination:

```
score = lexical × 0.40 + freshness × 0.25 + root_match × 0.15 + path_overlap × 0.10 + lane_match × 0.10
```

| Signal | Weight | Description |
|--------|--------|-------------|
| `lexical` | 0.40 | Fraction of extracted keywords present in note text |
| `freshness` | 0.25 | Freshness weight: current=1.0, suspect=0.5, stale=0.1, superseded=0.0 |
| `root_match` | 0.15 | 1.0 if note's root is in active_roots, else 0.5 |
| `path_overlap` | 0.10 | Overlap between prompt keywords and note's `path_scope` list |
| `lane_match` | 0.10 | 1.0 if note's lane matches detected prompt lane, 0.7 if unknown/mismatch |

A highly relevant but `suspect` note (e.g., lexical=0.8, freshness=0.5) scores 0.8×0.4 + 0.5×0.25 + ... which may rank below a moderately relevant `current` note, preserving the freshness-first principle.

Casual prompts (greetings, confirmations) skip context injection entirely.

---

## Multi-Root Retrieval

Notes can be stored in multiple `doc/*` subdirectories (roots). The retrieval system scans all registered roots when selecting context.

### Root registration

Roots are declared in the manifest under `registered_roots`:

```yaml
registered_roots:
  - common
  - frontend
  - api
```

If `registered_roots` is absent from the manifest, the system falls back to scanning all `doc/*` subdirectories. `doc/common` is always included.

### Root display in context

Notes from non-common roots are prefixed with their root name in the injected context:

```
Note: [api] Rate limiter returns 429 with Retry-After header
Note: Authentication flow requires session token in cookie
```

The second note (from `doc/common`) has no prefix. The first note (from `doc/api`) is prefixed with `[api]`.

### Backward compatibility

- Repos with only `doc/common/` behave identically to before — no configuration change needed.
- Notes without `root`, `lane`, `path_scope`, or `topic_tags` fields use defaults: `root=common`, `lane=None`, `path_scope=[]`, `topic_tags=[]`.
- The 0.1 minimum threshold and top-2 selection limit are unchanged.
- The 600-character context budget is unchanged.

---

## Optional Note Metadata Fields

The following fields improve retrieval scoring but are **not required**. Existing notes without them continue to work.

```yaml
root: common          # doc root this note belongs to (default: common)
lane: build           # most relevant workflow lane (optional)
path_scope:           # file paths this note covers (optional)
  - src/api/users.py
topic_tags:           # semantic labels (optional)
  - authentication
```

See `plugin/docs/retrieval-selection.md` for the full retrieval reference.
