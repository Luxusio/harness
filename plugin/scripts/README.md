# plugin/scripts/

CLI tools for the harness plugin.

## Overview

This directory is the canonical home for harness CLI tools under the new
`plugin/` tree.  During the ongoing `plugin-legacy/` to `plugin/` migration,
some scripts here are shims that delegate to `plugin-legacy/scripts/`.

## write_artifact.py

`write_artifact.py` is the protected-artifact CLI.  It exposes subcommands for
writing PLAN.md, CRITIC__*.md, HANDOFF.md, DOC_SYNC.md, and related files
through an enforced provenance path.

**Current status:** this copy is a thin shim.  It loads
`plugin-legacy/scripts/write_artifact.py` at runtime via `importlib.util` and
delegates `main()` to it.  The shim adds no logic of its own.

Both invocation paths are equivalent:

```bash
python3 plugin/scripts/write_artifact.py  plan --task-dir <path> ...
python3 plugin-legacy/scripts/write_artifact.py  plan --task-dir <path> ...
```

**Migration plan:** when `plugin-legacy/` is removed, this shim will be
replaced by a direct copy or reorganisation of the source.  No callers need to
change — only the file contents here will change.

## review-log / review-read

These two scripts are standalone tools (not shims).  They do not delegate to
the legacy tree and can be updated independently.

## Adding new scripts

Place new CLI tools directly in this directory.  If the tool has a
corresponding legacy implementation during the transition period, use the
same importlib shim pattern as `write_artifact.py`.
