# Development runbook

## Commands

- **Dev server:** `claude --plugin-dir ./plugin`
- **Build:** (none — plugin is config/docs only)
- **Test:** `claude --plugin-dir ./plugin --print 'list harness skills'`
- **Lint:** (none)

## Common tasks

- **Test plugin locally:** `claude --plugin-dir ./plugin` then invoke `/harness:setup`
- **Install via marketplace:** `/plugin marketplace add https://github.com/Luxusio/harness.git` then `/plugin install harness@harness`
- **Update marketplace clone:** `cd ~/.claude/plugins/marketplaces/harness && git fetch origin master && git reset --hard origin/master`

## Memory index operations

### Build the compiled memory index
```bash
bash harness/scripts/build-memory-index.sh
```
Regenerates `harness/memory-index/` from all durable source files. Deterministic — same input always produces same output. Run after modifying any file in `harness/docs/`, `harness/state/`, or `harness/policies/`.

### Check for stale index
```bash
bash harness/scripts/check-memory-index.sh
```
Rebuilds to a temp directory and diffs against committed index. Exit 0 = up to date, exit 1 = stale.

### Query the memory index
```bash
bash harness/scripts/query-memory.sh --query "approval rules" --format markdown
bash harness/scripts/query-memory.sh --query "plugin structure" --paths "plugin/" --top 5 --format json
```

### Query with explain mode
```bash
bash harness/scripts/query-memory.sh --query "approval" --explain --format markdown
```
Shows admission reasons and score breakdown per result.

### Query for orchestrator pack
```bash
bash harness/scripts/query-memory.sh --query "approval" --format pack
```
Returns structured JSON for orchestrator consumption. The pack includes `facts` (pre-scored records), `source_files_to_verify` (raw files to open for verification), and `unresolved_conflicts` (subjects with multiple active records).

### Query by identifier
```bash
bash harness/scripts/query-memory.sh --query "ADR-0002" --top 5 --format markdown
```
Loads the `by-identifier/` shard for ADR-0002 directly — no full index scan.

### Query by source file
```bash
bash harness/scripts/query-memory.sh --query "approval" --paths "harness/policies/approvals.yaml" --format markdown
```
Loads the `by-source-path/` shard for the given file — returns only records whose provenance matches that source file.

### Temporal query
```bash
bash harness/scripts/query-memory.sh --query "what changed before ADR-0002" --format pack
```
Operator tokens (before, after, latest) are NOT used for lexical matching — they drive temporal logic to filter by relation chains and timeline order.

### Rebuild after source changes
After modifying durable memory sources, always run:
```bash
bash harness/scripts/build-memory-index.sh
bash harness/scripts/check-memory-index.sh
```

### Never edit generated files
Files under `harness/memory-index/source-shards/`, `active/`, and `timeline/` are generated. Edit the source documents instead, then rebuild.

## Overlay operations

### Build the local overlay
```bash
bash harness/scripts/build-memory-overlay.sh
```
Generates `.harness-cache/memory-overlay/records.jsonl` from current session state (current-task.yaml, last-session-summary.md). Overlay is gitignored — per-session only.

### Query with overlay
```bash
bash harness/scripts/query-memory.sh --query "current task" --include-overlay --format markdown
```

### Run regression tests
```bash
bash harness/scripts/test-memory-index.sh
```

## Debugging notes

<!-- Add debugging insights from bug fixes -->

- Marketplace install: This repo uses Git-based marketplace add (`/plugin marketplace add <git-url>`). The relative plugin source (`./plugin`) in `marketplace.json` works with Git-based installs. Raw `marketplace.json` URL-based installs are not compatible with this repo's distribution shape.
- Validation scripts: `harness/scripts/validate.sh` and `harness/scripts/smoke.sh` use manifest commands (`harness/manifest.yaml > commands.*`) as their primary source. Auto-detect fallback is used only when a manifest command is empty.
