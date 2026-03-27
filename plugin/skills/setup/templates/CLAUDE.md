# CLAUDE.md
tags: [root, harness, bootstrap]
summary: repo entrypoint and durable root registry
always_load_paths: [doc/common/CLAUDE.md]
registered_roots: [common]
updated: {{SETUP_DATE}}

@doc/common/CLAUDE.md

# Operating mode
- Default operating agent is harness.
- Every substantial repo-mutating task follows:
  request -> contract plan -> plan critic -> implement -> runtime QA -> persistence -> docs sync -> document critic -> close.
- New durable roots or durable structure changes go through critic-document.
- `.claude/harness/manifest.yaml` is the initialization marker.
