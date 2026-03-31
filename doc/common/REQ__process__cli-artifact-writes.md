# REQ process cli-artifact-writes
summary: Agents must use CLI tool for all protected artifact writes
status: active
updated: 2026-03-31
freshness: current
verified_at: 2026-03-31T00:00:00Z
derived_from:
  - plugin/scripts/write_artifact.py
  - plugin/CLAUDE.md
confidence: high
kind: process
source: User directive stated 2026-03-31 — "harness문서를 agent가 직접 write하는게 있다면 그건 전부 cli tool로 밀어넣어. 직접 write하는건 너무 토큰낭비가 심해" (All harness document writes by agents should go through CLI tool. Direct writes waste too many tokens.)

All protected artifacts (CRITIC__*.md, HANDOFF.md, DOC_SYNC.md) must be written via the CLI tool:

```bash
HARNESS_SKIP_PREWRITE=1 python3 plugin/scripts/write_artifact.py <subcommand> \
  --task-dir <path> [options]
```

Subcommands: `critic-runtime`, `critic-plan`, `critic-document`, `handoff`, `doc-sync`.

Agents must NOT output artifact file content inline in their responses. Inline writes cost 500-2000 tokens per artifact; CLI calls cost ~50-100 tokens.

| Subcommand | Artifact | Caller |
|---|---|---|
| `critic-runtime` | CRITIC__runtime.md + meta.json | critic-runtime |
| `critic-plan` | CRITIC__plan.md + meta.json | critic-plan |
| `critic-document` | CRITIC__document.md + meta.json | critic-document |
| `handoff` | HANDOFF.md + meta.json | developer |
| `doc-sync` | DOC_SYNC.md + meta.json | writer |
