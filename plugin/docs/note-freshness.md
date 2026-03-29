# Note Freshness Reference

updated: 2026-03-30

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
- **`verification_command`**: If provided, running this command and exiting 0 constitutes re-verification. Enables automated freshness recovery at task completion.
- **`supersedes` / `superseded_by`**: Form a doubly-linked chain across note versions. Only the head of the chain (no `superseded_by`) is the authoritative note.

---

## Freshness Transitions

```
                     file in invalidated_by_paths changes
    current  ──────────────────────────────────────────► suspect
       ▲                                                      │
       │  verification_command exits 0 at task completion     │  > 3 task completions
       │  OR critic-runtime PASS (related area)               │  without re-verification
       │  OR writer re-verifies with new evidence             │
       └──────────────────────────────────────────── stale ◄──┘
                  only via explicit re-verification
```

### Transition rules

| Transition | Trigger |
|-----------|---------|
| `current` → `suspect` | Any file in `invalidated_by_paths` changes (file-changed-sync hook, structural path match) |
| `suspect` → `current` | `verification_command` exits 0 at task completion (auto-reverify), OR critic-runtime PASS covers related area, OR writer re-verifies with new evidence |
| `suspect` → `stale` | Note has remained `suspect` across more than 3 task completions without re-verification |
| `stale` → `current` | Explicit writer re-verification only |

---

## Auto-Reverify at Task Completion (WS-1)

When a task completes successfully, `task_completed_gate.py` runs a bounded auto-reverify pass on suspect notes. This closes the `suspect → current` loop without requiring manual writer intervention.

### How it works

1. Collect all notes with `freshness: suspect` AND a non-empty `verification_command`.
2. Filter to notes whose `invalidated_by_paths` overlaps with the task's `touched_paths` or `verification_targets` (structural path match — not substring).
3. Run each note's `verification_command` (max `MAX_NOTES=5`, per-command timeout `CMD_TIMEOUT=10s`).
4. Exit 0 → update `freshness: current`, refresh `verified_at`.
5. Non-zero exit → leave `freshness: suspect`, print failure reason.

### Guarantees

- **Non-blocking**: reverify failures never prevent task completion.
- **Bounded**: at most 5 notes per completion, 10s timeout per command.
- **doc/ absent → no-op**: graceful when no doc root exists.
- **No verification_command → skipped**: notes without a command stay suspect (existing behavior).

### Structural path matching

The auto-reverify system uses structural path comparison, not substring matching:

| Changed file | inv path | Matches? |
|---|---|---|
| `src/api.py` | `src/api.py` | ✓ exact |
| `src/api/v2.py` | `src/api` | ✓ prefix |
| `src/api-v2.py` | `src/api.py` | ✗ not a prefix or exact |

This prevents false positives from coincidental substring matches in note body text.

### Implementation

- `plugin/scripts/_lib.py`: `parse_note_metadata()`, `set_note_freshness()`
- `plugin/scripts/note_reverify.py`: `reverify_suspect_notes()`, `collect_suspect_notes()`, `paths_overlap()`
- `plugin/scripts/task_completed_gate.py`: calls `reverify_suspect_notes()` after gate checks pass

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
2. For each active note, parse `invalidated_by_paths` structurally via `parse_note_metadata()`.
3. If any path matches (exact or directory-prefix — NOT substring), transition `freshness: current` → `suspect`.
4. Notes already at `suspect` are not re-marked (idempotent).

The structural parser in `_lib.parse_note_metadata()` handles both inline (`[a, b]`) and block sequence formats.

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

### OBS note with full freshness metadata including verification_command

```markdown
# OBS api obs-rate-limit-behavior
summary: API returns HTTP 429 with Retry-After header when rate limit exceeded
status: active
updated: 2026-03-30
freshness: current
verified_at: 2026-03-30T10:15:00Z
derived_from:
  - src/middleware/rate-limiter.ts
  - tests/integration/rate-limit.test.ts
confidence: high
invalidated_by_paths:
  - src/middleware/rate-limiter.ts
verification_command: "npm test -- --grep 'rate limit'"
evidence: Integration test `returns 429 on limit exceeded` PASS; curl confirmed Retry-After header present.
```

When `src/middleware/rate-limiter.ts` changes:
- `file_changed_sync.py` marks this note `suspect` (structural path match).
- When the next task completes and its `touched_paths` includes `src/middleware/rate-limiter.ts`, `note_reverify.py` runs `npm test -- --grep 'rate limit'`.
- If exit 0: `freshness: current`, `verified_at` updated.
- If non-zero: stays `suspect`.

### REQ note (no invalidation paths needed — user-stated)

```markdown
# REQ root req-no-external-auth
summary: System must not depend on external auth providers; all auth must be self-hosted
status: active
updated: 2026-03-30
freshness: current
verified_at: 2026-03-30T09:00:00Z
derived_from: []
confidence: high
invalidated_by_paths: []
source: User statement 2026-03-30 session
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
| CHECKS summary | 0–1 | Fix rounds only — focus/guardrail criterion IDs (max 120 chars) |
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

### Backward compatibility

- Repos with only `doc/common/` behave identically to before — no configuration change needed.
- Notes without `root`, `lane`, `path_scope`, or `topic_tags` fields use defaults.
- The 600-character context budget is unchanged.

---

## Optional Note Metadata Fields

The following fields improve retrieval scoring but are **not required**.

```yaml
root: common          # doc root this note belongs to (default: common)
lane: build           # most relevant workflow lane (optional)
path_scope:           # file paths this note covers (optional)
  - src/api/users.py
topic_tags:           # semantic labels (optional)
  - authentication
```

See `plugin/docs/retrieval-selection.md` for the full retrieval reference.
