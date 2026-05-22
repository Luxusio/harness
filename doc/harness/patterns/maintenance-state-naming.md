# Maintenance State Naming

`harness:maintain` is no longer an active standalone skill, but several
`maintain`-prefixed paths remain as compatibility surfaces. Do not rename them
in-place without a migration layer.

## Current Legacy-Compatible Names

| Name | Current owner | Why it remains |
| --- | --- | --- |
| `doc/harness/.maintain-pending.json` | `hygiene_scan.py`, `doc_hygiene.py`, `prompt_memory.py` | Carries pending REVIEW and Tier C drift entries across sessions. Existing repos may already have this file. |
| `doc/harness/.maintain-last-run` | `hygiene_scan.py`, `doc_hygiene.py` | Drives once-per-day hygiene idempotency. Renaming without fallback can cause duplicate hygiene runs. |
| `doc/harness/.maintain-observe.log` | `hygiene_scan.py` | Records observer-mode decisions during early sessions. |
| `plugin/scripts/maintain_restore.py` | legacy wrapper for doc archive recovery CLI | Restore commands may already be embedded in handoffs, logs, and commit messages. |

These names refer to historical state and recovery compatibility, not to a
standalone maintenance skill.

## Migration Rule

If these names are renamed, the new implementation must ship as a compatibility
migration, not a direct replacement.

Required sequence:

1. Introduce the new canonical name next to the old one.
2. Read both old and new locations.
3. Write the new location.
4. If the old location exists and the new location does not, migrate
   atomically with a backup-safe path.
5. Keep restore CLI aliases or wrappers for at least one release.
6. Update tests to cover old-file read fallback, new-file write behavior, and
   no-loss migration of pending entries.
7. Only remove old names after installed runtimes and repo templates have had a
   compatibility release.

## Proposed Canonical Names

| Legacy name | Future canonical name |
| --- | --- |
| `.maintain-pending.json` | `.hygiene-pending.json` |
| `.maintain-last-run` | `.hygiene-last-run` |
| `.maintain-observe.log` | `.hygiene-observe.log` |
| `maintain_restore.py` | `hygiene_restore.py` |

## Implemented Slice

The first compatibility slice is implemented:

- `hygiene_scan.py`, `doc_hygiene.py`, and `prompt_memory.py` write/read the
  canonical `.hygiene-*` names where they own writes.
- Legacy `.maintain-*` files remain read fallbacks.
- Old and new pending queues are not merged when both exist; the canonical
  `.hygiene-pending.json` wins to avoid replaying stale legacy entries.
- `hygiene_restore.py` is the canonical restore CLI.
- `maintain_restore.py` remains as a compatibility wrapper.

## Decision

Use canonical `.hygiene-*` state files and `hygiene_restore.py` going forward
while preserving legacy `.maintain-*` read fallback and the `maintain_restore.py`
wrapper.
