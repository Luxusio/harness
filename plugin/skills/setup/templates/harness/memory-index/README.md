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

## Determinism guarantee

Two consecutive runs with no source changes produce zero git diff.
The hash in each `id` is `sha256(kind + subject_key + statement)[:8]`.
No build timestamps or commit hashes are embedded.
