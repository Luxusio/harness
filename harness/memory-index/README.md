# harness memory-index

This directory is **generated**. Do not edit files here manually.

Rebuild with:
```
bash harness/scripts/build-memory-index.sh
```

Check for staleness with:
```
bash harness/scripts/check-memory-index.sh
```

## What this is

The memory index is a deterministic, structured JSON representation of all
durable knowledge in this harness control plane. It compiles:

- `harness/docs/constraints/` — confirmed project constraints
- `harness/docs/decisions/` — Architecture Decision Records
- `harness/docs/requirements/` — requirement specs
- `harness/docs/architecture/` — observed facts and confirmed patterns
- `harness/docs/runbooks/` — runbook notes
- `harness/policies/approvals.yaml` — approval gate rules
- `harness/state/recent-decisions.md` — recent context (lower authority)
- `harness/state/unknowns.md` — hypotheses and open questions

## Directory layout

```
memory-index/
  manifest.json              — top-level metadata (version, record_count, sources)
  VERSION                    — schema version ("1")
  source-shards/             — one JSON file per source document
    docs/constraints/...
    docs/decisions/...
    docs/architecture/...
    docs/runbooks/...
    docs/requirements/...
    policies/...
    state/...
  active/
    by-subject/<key>.json    — active records grouped by subject_key
    by-domain/<domain>.json  — active records grouped by domain
    by-path/<path>.json      — active records grouped by scope path
  timeline/
    <subject>.json           — all records (active + resolved) for a subject
```

## Record schema

Each record has:
- `id` — stable identifier: `mem:<kind>:<subject_key>:<8-char-sha256>`
- `kind` — `constraint | decision | approval_rule | observed_fact | runbook_note | requirement | hypothesis | open_question`
- `authority` — `hypothesis | observed | confirmed | enforced`
- `index_status` — `active | superseded | resolved`
- `statement` — the fact, rule, or decision text
- `provenance` — source file, section, and type
- `temporal` — dates extracted from source content (never build time)
- `scope` — paths, domains, api_surfaces this record applies to
- `relations` — supersedes, extends, resolves, conflicts_with links
- `tags` — free-form labels

## Scope indexing

`active/by-domain/` and `active/by-path/` are populated on every rebuild.
- `by-domain/<domain>.json` — all active records scoped to a domain (e.g., `plugin`, `harness`, `memory`)
- `by-path/<encoded-path>.json` — all active records scoped to a specific file or directory path

Use `--paths` flag in `query-memory.sh` to filter by path scope.

## Temporal relations

Records track supersession chains through `relations` fields:
- `supersedes`: this record replaces an older one (e.g., new ADR supersedes prior decision)
- `extends`: this record builds on another
- `resolves`: this record answers an open question
- `conflicts_with`: this record contradicts another (needs resolution)

Superseded records are not deleted — they remain in `timeline/<subject>.json` for historical queries.
Temporal relations are populated automatically from ADR `supersedes` frontmatter during index build.

## Overlay integration

The local overlay (`.harness-cache/memory-overlay/records.jsonl`) is a per-session, gitignored supplement:
- Built by `bash harness/scripts/build-memory-overlay.sh` from `current-task.yaml` and `last-session-summary.md`
- Merged at query time with `--include-overlay` flag — overlay records override compiled index for same subject
- Not committed to git; cleaned up when session changes are committed

## Regression test suite

Run automated checks against the compiled index:
```bash
bash harness/scripts/test-memory-index.sh
```
Checks include: schema validity, determinism (two consecutive builds produce zero diff), scope coverage (by-domain and by-path populated), temporal relations present, query output format correctness.

## Path key encoding

`active/by-path/<encoded-path>.json` uses filesystem-safe encoding:
- Forward slashes (`/`) are replaced with `__`
- Leading path separators are stripped
- Example: `harness/docs/constraints/` → `harness__docs__constraints__.json`

Use the `--paths` flag in `query-memory.sh` with the original path (encoding is handled internally).

## Canonical subject key strategy

Subject keys are derived per source type to ensure stable, collision-free identifiers:
- **ADR / decisions**: `adr-<slug>` derived from filename (e.g., `adr-0001-harness-bootstrap`)
- **Constraints**: `constraint-<slug>` derived from section heading
- **Requirements**: `req-<id>-<slug>` derived from filename
- **Approval rules**: `approval-<rule-slug>` derived from rule name or path pattern
- **Observed facts / runbook notes**: `fact-<source-slug>-<hash8>` — hash prevents collisions for multiple facts per file
- **Unknowns / hypotheses**: `unknown-<slug>` or `hypothesis-<slug>` derived from question text

## Current limitations

**What works:**
- Scope indexing (by-domain, by-path) is active on every rebuild
- Supersession edges populated for ADRs with `supersedes` frontmatter
- Resolved unknowns generate `resolves` edges
- Query planner loads only relevant shards (not full index)
- Admission gate filters out unrelated high-authority records

**What is planned but not yet active:**
- Freeform docs (runbooks, constraints, requirements) do not automatically generate cross-reference edges
- `extends` and `conflicts_with` edges are sparse — only populated when explicitly declared in source frontmatter
- Full-text semantic similarity across shards is not implemented; scoring is keyword + authority-weighted

## Determinism guarantee

Two consecutive runs with no source changes produce zero git diff.
The hash in each `id` is `sha256(kind + subject_key + statement)[:8]`.
No build timestamps or commit hashes are embedded.
