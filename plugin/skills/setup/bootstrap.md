# Phase 3: Bootstrap Core Structure

Sub-file for setup/SKILL.md. Creates harness2 scaffolding from census + user answers. Skip existing files unless Fresh start.

---

## 3.1 Directory structure

```
CLAUDE.md                        # root entrypoint (create or append)
doc/harness/                     # harness state directory
doc/harness/manifest.yaml        # initialization marker + runtime config
doc/harness/critics/
  plan.md
  runtime.md
  document.md
```

## 3.2 manifest.yaml

```yaml
project: {project_name}
project_type: {detected_or_chosen}
harness_version: 2
browser_qa_supported: {true|false}
build_command: {cmd}
test_command: {cmd}
dev_command: {cmd or omit}       # browser: dev server start command
entry_url: {url or omit}        # browser: URL after dev server starts
api_base_url: {url or omit}     # API: endpoint base URL
created: {date}
```

`dev_command`, `entry_url`, `api_base_url` are optional — only include the ones relevant to the project type.

### Browser project fields (required when browser_qa_supported: true)

Auto-detect from framework:

| Framework | dev_command | entry_url |
|-----------|------------|-----------|
| Next.js | `npm run dev` | `http://localhost:3000` |
| Vite | `npm run dev` | `http://localhost:5173` |
| Nuxt | `npm run dev` | `http://localhost:3000` |
| Astro | `npm run dev` | `http://localhost:4321` |
| Angular | `npm start` | `http://localhost:4200` |
| SvelteKit | `npm run dev` | `http://localhost:5173` |
| Remix | `npm run dev` | `http://localhost:3000` |

Use census-detected `dev_command` if present; otherwise ask the user.

### API project field

`api_base_url` defaults: Node.js `http://localhost:3000`, Python/Django `http://localhost:8000`, Go `http://localhost:8080`. Only include if non-default.

### Chrome DevTools MCP config (when browser_qa_supported: true)

```bash
if [ -f .mcp.json ]; then
  python3 -c "
import json
with open('.mcp.json') as f:
    config = json.load(f)
if 'mcpServers' not in config:
    config['mcpServers'] = {}
config['mcpServers']['chrome-devtools'] = {
    'command': 'npx',
    'args': ['@anthropic-ai/chrome-devtools-mcp@latest']
}
with open('.mcp.json', 'w') as f:
    json.dump(config, f, indent=2)
print('Chrome DevTools MCP added to .mcp.json')
" 2>/dev/null || echo "FAILED: could not update .mcp.json"
else
  cat > .mcp.json << 'MCPJSON'
{
  "mcpServers": {
    "harness": {
      "command": "python3",
      "args": ["${CLAUDE_PLUGIN_ROOT}/mcp/harness2_server.py"]
    },
    "chrome-devtools": {
      "command": "npx",
      "args": ["@anthropic-ai/chrome-devtools-mcp@latest"]
    }
  }
}
MCPJSON
fi
```

Skip MCP config if user selected "already configured globally".

## 3.3 Smart defaults

| Project type | browser_qa | test_command | build_command | dev_command | entry_url |
|-------------|-----------|-------------|---------------|------------|-----------|
| Next.js | true | `npm test` or `npx jest` | `npm run build` | `npm run dev` | `http://localhost:3000` |
| Vite + React | true | `npx vitest run` | `npx vite build` | `npm run dev` | `http://localhost:5173` |
| Nuxt | true | `npm test` | `npm run build` | `npm run dev` | `http://localhost:3000` |
| Astro | true | `npm test` | `npm run build` | `npm run dev` | `http://localhost:4321` |
| Angular | true | `ng test` | `npm run build` | `npm start` | `http://localhost:4200` |
| SvelteKit | true | `npm test` | `npm run build` | `npm run dev` | `http://localhost:5173` |
| Remix | true | `npm test` | `npm run build` | `npm run dev` | `http://localhost:3000` |
| API (Express/Fastify) | false | `npm test` | `npm run build` | — | — |
| Python (Django) | false | `pytest` | — | `python manage.py runserver` | — |
| Python (FastAPI) | false | `pytest` | — | `uvicorn main:app` | — |
| Rust | false | `cargo test` | `cargo build` | — | — |
| Go | false | `go test ./...` | `go build ./...` | — | — |
| CLI / library | false | varies | varies | — | — |
| Monorepo | ask user | workspace-level | workspace-level | ask user | ask user |

Match → apply without asking. Only confirm if ambiguous or no match.

## 3.4 CLAUDE.md

Create if absent; append harness2 section if present. Under 20 lines. Include: harness2 mode declaration, manifest.yaml link, canonical loop (plan→develop→verify→close), "just describe what you want — auto-routing is on". Don't dump full runtime rules — those live in `plugin/CLAUDE.md`.

## 3.5 Critic playbooks

**doc/harness/critics/plan.md:** scope bounded, ACs testable, verification commands exist. PASS when a dev can implement without guessing intent.

**doc/harness/critics/runtime.md:** commands run without error, outputs match expectations, ACs met, implementation satisfies user intent (not just literal spec). PASS when evidence bundle proves operation AND intent adequacy.

**doc/harness/critics/document.md:** DOC_SYNC.md covers all changed files, HANDOFF.md accurate. PASS when doc artifacts consistent with reality on disk.

## 3.6 doc/harness/ directory

```bash
mkdir -p doc/harness
touch doc/harness/.gitkeep
```

Add to `.gitignore` if absent:
```
doc/harness/learnings.jsonl
doc/harness/checkpoints/
doc/harness/health-history.jsonl
doc/harness/tasks/
```

## 3.7 Contracts installation (non-destructive)

Every decision that could touch existing files uses AskUserQuestion. Never overwrite silently.

### 3.7.1 CONTRACTS.md (harness-managed)

```bash
_TEMPLATE="${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.md"
if [ ! -f CONTRACTS.md ]; then
  cp "$_TEMPLATE" CONTRACTS.md
elif grep -q "harness:managed-begin" CONTRACTS.md; then
  echo "CONTRACTS.md already managed — skip (maintain handles upgrades)"
else
  echo "CONTRACTS.md exists without markers — ask user"
fi
```

Unmanaged existing CONTRACTS.md:
```
AskUserQuestion:
  Question: "CONTRACTS.md already exists without harness markers. How to proceed?"
  Options:
    - A) Rename existing to CONTRACTS.user.md and install fresh managed version
    - B) Skip — leave your file alone (contracts system disabled)
    - C) Show me the diff first
```

### 3.7.2 CONTRACTS.local.md (user's project-specific stub)

```bash
_LOCAL_TEMPLATE="${CLAUDE_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.local.md"
if [ ! -f CONTRACTS.local.md ]; then
  cp "$_LOCAL_TEMPLATE" CONTRACTS.local.md
fi
```
Never touched by harness after creation.

### 3.7.3 CLAUDE.md import line

```bash
if grep -qF "@CONTRACTS.md" CLAUDE.md 2>/dev/null; then
  echo "@CONTRACTS.md import already present"
else
  # ask user
fi
```

If missing:
```
AskUserQuestion:
  Question: "Add '@CONTRACTS.md' import to your CLAUDE.md? (one line at top, existing content preserved)"
  Options:
    - A) Yes — insert after frontmatter / first heading
    - B) Skip — I will add it manually
    - C) Show me the proposed diff first
```

On A, Edit tool inserts `@CONTRACTS.md` as a new line immediately after the first `---` frontmatter block (or after first H1 if no frontmatter). Never bulk-rewrite.

### 3.7.4 Verify contract lint

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/contract_lint.py" \
  --path CONTRACTS.md --repo-root . --quick || \
  echo "WARN: contract_lint reported issues — run maintain skill"
```

WARN is non-blocking. `maintain` exists to repair drift.
