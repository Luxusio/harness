# Phase 3: Bootstrap Core Structure

Sub-file for setup/SKILL.md. Creates harness scaffolding from census + user answers. Skip existing files unless Fresh start.

Resolve runtime-specific paths once and reuse them throughout this file:

```bash
_PLUGIN_ROOT="${HARNESS_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT" ]; then
  for _CANDIDATE in \
    "$HOME/.codex/harness/plugins/harness" \
    "$HOME/.claude/harness-dev/plugin"; do
    if [ -f "$_CANDIDATE/scripts/setup_finalize.py" ]; then
      _PLUGIN_ROOT="$_CANDIDATE"
      break
    fi
  done
fi
if [ -z "$_PLUGIN_ROOT" ]; then
  echo "BLOCKED: installed harness plugin root not found"
  return 1 2>/dev/null || exit 1
fi
_SETUP_RUNTIME="${HARNESS_RUNTIME:-}"
if [ -z "$_SETUP_RUNTIME" ]; then
  [ -f "$_PLUGIN_ROOT/.codex-plugin/plugin.json" ] && _SETUP_RUNTIME="codex" || _SETUP_RUNTIME="claude"
fi
if [ "$_SETUP_RUNTIME" = "codex" ]; then
  _PROJECT_DOC="AGENTS.md"
else
  _PROJECT_DOC="CLAUDE.md"
fi
export _PROJECT_DOC
```

---

## 3.1 Directory structure

```
AGENTS.md or CLAUDE.md           # runtime-specific entrypoint
doc/harness/                     # harness state directory
doc/harness/manifest.yaml        # initialization marker + runtime config
doc/harness/critics/
  plan.md
  runtime.md
  document.md
doc/<area>/<TYPE>__<name>.md     # durable knowledge by area / bounded context
```

## 3.2 manifest.yaml

```yaml
version: 5
initialized_at: {date}
name: {project_name}
type: {detected_or_chosen}
languages: [{detected_languages}]
source_git_roots: [{workspace-relative Git roots}] # required only when this control root is not Git
build_command: {cmd}
test_command: {cmd}
dev_command: {cmd or omit}       # browser: dev server start command
entry_url: {url or omit}        # browser: URL after dev server starts
api_base_url: {url or omit}     # API: endpoint base URL

verify_commands:
  - {test_command}

qa:
  default_mode: {browser|api|cli}
  browser_qa_supported: {true|false}
  desktop_qa_supported: {true|false}
  ux_review_supported: false

# --- Health scoring (enabled automatically) ---
health_components:
  - name: test
    command: "{test_command}"
    weight: 1.0
```

`dev_command`, `entry_url`, `api_base_url` are optional — only include the ones relevant to the project type.
Phase 3.5 replaces the default `health_components` entry with every
census-detected test/quality command. In a multi-Git workspace, commands are
control-root-relative (for example `cd pay-api && ./gradlew test`) and component
names include the source-root label. The root must pass the census safe-name
rule and be rendered as `shlex.quote("./" + root)`; serialize the full command
as a quoted YAML scalar. The explicit `./` keeps leading-dash directory names
from being parsed as `cd` options.
`source_git_roots` is omitted for a normal Git repository. For a non-Git
workspace that controls several independent repositories, write the exact
setup-census roots (for example `[pay-api, pay-webapp]`). Harness uses these
roots for baseline, receipt, and QA freshness checks.

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

Setup reports whether Chrome DevTools MCP appears to be available from current
session tools or global runtime config. Treat project-root `.mcp.json` as
user-owned configuration. If browser QA is desired, enable it when the browser
MCP surface is available; otherwise record the missing dependency and set
`browser_qa_supported: false` until the user configures it outside setup.

### x11-mcp prereq check (when desktop_qa_supported: true)

v1 is Linux-only. The x11-mcp server itself is NOT shipped by harness. The user
installs and registers it through their runtime/global MCP configuration outside
setup. Setup reports the missing dependency and keeps `desktop_qa_supported:
false` until the server is available.

```bash
# Platform gate — warn on non-Linux
_OS=$(uname -s 2>/dev/null || echo unknown)
if [ "$_OS" != "Linux" ]; then
  echo "WARN: desktop_qa_supported=true on $_OS — qa-desktop v1 is Linux-only."
  echo "      macOS (XQuartz) / Windows (WSLg) are deferred to v2."
fi

# Xvfb availability (for headless CI / WSL without WSLg)
command -v Xvfb >/dev/null 2>&1 || {
  echo "MISSING: Xvfb — install with: sudo apt-get install -y xvfb"
  echo "        The qa-desktop agent can also install it on-demand via sudo -n apt-get,"
  echo "        but pre-installing avoids BLOCKED_ENV on first run."
}

```

If your x11-mcp server publishes tools under a different MCP name (e.g.
`mcp__x11-mcp__*`, `mcp__xdotool__*`), update the `tools:` list in
`${_PLUGIN_ROOT}/agents/qa-desktop.md` frontmatter to match.

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

## 3.4 Runtime project document

Create `$_PROJECT_DOC` if absent; append the harness section if present. Codex
uses `AGENTS.md`; Claude Code uses `CLAUDE.md`. Keep it under 40 lines.

### Harness routing block (emit into the runtime project document)

Use the setup finalizer's containment-safe, no-follow project-document helper.
It rejects symlinks, preserves unrelated content, and atomically replaces only
the Harness routing block. Marker: `<!-- harness:routing-injected -->`.
This is the idempotent replace/append path. The emitted block routes
repo-mutating work to `$harness:run` and contains the
`Durable Decision Documentation Gate`: a durable decision is not handled until it is documented under `doc/`;
Conversation history is not durable memory, and
the task records a specific PLAN durable-doc decision when no doc applies.

```bash
python3 "${_PLUGIN_ROOT}/scripts/setup_finalize.py" \
  --repo "$_ROOT" --plugin-root "$_PLUGIN_ROOT" \
  --project-doc "$_PROJECT_DOC" --project-doc-only --ensure-routing
```

Note: no `Default agent is X` line. The harness routes via skills, not agent switching.
Pre-native orchestration is unsupported; setup owns only this routing block.

## 3.5 Critic playbooks

**doc/harness/critics/plan.md:** scope bounded, ACs testable, verification commands exist. PASS when a dev can implement without guessing intent.

**doc/harness/critics/runtime.md:** commands run without error, outputs match expectations, ACs met, implementation satisfies user intent (not just literal spec). PASS when evidence bundle proves operation AND intent adequacy.

**doc/harness/critics/document.md:** identify itself as the document critic
playbook and include at least one bullet requiring durable docs to match code,
tests, and observable behavior. PASS when changed durable knowledge is accurate,
discoverable, and non-contradictory.

Documentation review is performed by the documentation-review subagent and
`task_verify`. It reads typed durable docs, PLAN/TASK.json, and declared lenses
paths. It does not require legacy document-sync artifacts.

## 3.6 doc/harness/ directory

```bash
mkdir -p doc/common
touch doc/harness/.gitkeep
```

Durable project knowledge uses typed docs:
`doc/<area>/<TYPE>__<name>.md`. Treat `area` as a DDD-style area or bounded
context: `ui`, `api`, `auth`, `billing`, `catalog`, `runtime`,
`verification`, or `common`. Use `REQ` for behavior/contracts that QA must
verify, `GUIDE` for reusable coding/design/test guidance, `ADR` for technical
decisions and tradeoffs, `POLICY` for external security/legal/data/approval
constraints, and `OBS`/`INF` for facts and inferences. Keep harness-internal
execution rules in skills, agents, scripts, and tests.

Install the canonical operational ignore list. The shared finalizer owns this
list so runtime writers and setup cannot drift apart:

```bash
python3 "${_PLUGIN_ROOT}/scripts/setup_finalize.py" \
  --repo "$_ROOT" --plugin-root "$_PLUGIN_ROOT" \
  --project-doc "$_PROJECT_DOC" --gitignore-only
```

This early call applies ignores without validating or stamping a version.
Phase 4 runs the full finalizer after every required artifact exists.

## 3.7 Contracts installation (non-destructive)

Potentially destructive replacement of an unmanaged contract file requires
confirmation. Deterministic, idempotent additions such as the runtime import
line are applied automatically while preserving unrelated content.

### 3.7.1 CONTRACTS.md (harness-managed)

```bash
_TEMPLATE="${_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.md"
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
_LOCAL_TEMPLATE="${_PLUGIN_ROOT}/skills/setup/templates/CONTRACTS.local.md"
if [ ! -f CONTRACTS.local.md ]; then
  cp "$_LOCAL_TEMPLATE" CONTRACTS.local.md
fi
```
After creation, preserve every user-authored rule. On setup rerun, replace only
the setup-owned C-100 block with the current fixed default from the template;
never bulk-rewrite or modify any other content.

### 3.7.3 Runtime project-document import line

```bash
python3 "${_PLUGIN_ROOT}/scripts/setup_finalize.py" \
  --repo "$_ROOT" --plugin-root "$_PLUGIN_ROOT" \
  --project-doc "$_PROJECT_DOC" --project-doc-only \
  --ensure-contract-import
```

When missing, do not ask. The same containment-safe, no-follow helper inserts
`@CONTRACTS.md` once, immediately after the closing delimiter of the first
frontmatter block, or after the first H1 when no frontmatter exists. If neither
exists, it prepends the line. It rejects symlinked project documents, preserves
all existing bytes outside the insertion, and uses an atomic replacement.

### 3.7.4 Verify contract lint

```bash
python3 "${_PLUGIN_ROOT}/scripts/contract_lint.py" \
  --path CONTRACTS.md --repo-root . --quick || \
  echo "WARN: contract_lint reported issues — handle in the active/next harness task"
```

WARN is non-blocking. Use the continuous maintenance flow to repair drift.

### 3.7.5 Close-time hygiene bootstrap (single atomic step)

This step installs the close-time hygiene system. **Order is critical:** C-16 in
CONTRACTS.md MUST land before close-time hygiene is considered enabled, so that
`hygiene_scan.py`'s own C-16 self-detect passes on first run. Do not add
`hygiene_scan.py` to SessionStart hooks; the Goal child-task executor invokes it post-close
from `self-improvement.md` and schedules any pending cleanup as a separate task.

Run as a single logical transaction (all-or-nothing):

**Step A — hygiene.yaml stub (idempotent)**

```bash
_HYGIENE_YAML="${_ROOT}/doc/harness/hygiene.yaml"
_HYGIENE_TEMPLATE="${_PLUGIN_ROOT}/skills/setup/templates/hygiene.yaml"
if [ ! -f "$_HYGIENE_YAML" ]; then
  cp "$_HYGIENE_TEMPLATE" "$_HYGIENE_YAML"
  echo "hygiene.yaml installed"
else
  echo "hygiene.yaml already present — skip"
fi
```

**Step B — CONTRACTS.md C-16 patch (idempotent, managed-block only)**

Fresh install: template already includes C-16 (§3.7.1 above). For upgrade
installs where CONTRACTS.md exists with `harness:managed-begin` but lacks C-16,
apply a managed-block patch. Per C-15, show AskUserQuestion diff preview first:

```bash
if grep -q "harness:managed-begin" CONTRACTS.md 2>/dev/null && \
   ! grep -q "### C-16" CONTRACTS.md 2>/dev/null; then
  echo "CONTRACTS.md missing C-16 — upgrade patch needed"
  # AskUserQuestion: show diff of C-16 addition, A) Apply B) Skip
  # On A: Edit tool appends C-16 stanza inside managed-block BEFORE managed-end marker
fi
```

The C-16 text to insert is the full 4-field stanza from the managed template.
Also ensure C-11 names `hygiene_scan.py` as authorized additive writer (already
in template; for upgrades, patch C-11 body via the same AskUserQuestion flow).
Also ensure C-05 contains the AC-019 doc/changes note (already in template).

**Step C — close-time invocation check (idempotent, AFTER steps A+B)**

Only after CONTRACTS.md has C-16 (step B verified), confirm that the run skill
invokes hygiene from `self-improvement.md`:

```bash
if grep -q "hygiene_scan.py --apply-safe" "${_PLUGIN_ROOT}/skills/run/self-improvement.md" 2>/dev/null; then
  echo "hygiene_scan.py close-time invocation present"
else
  echo "WARN: self-improvement.md missing hygiene_scan.py close-time invocation"
fi
```

**Invariant:** If step B fails or is skipped, step C must NOT claim hygiene is
enabled. Hygiene runs after close, never as a SessionStart reminder.

## 3.8 Finalize only after verification

Phase 4 invokes `setup_finalize.py` without `--check`. The command applies the
canonical `.gitignore`, verifies the manifest and packaged setup resources,
and writes `doc/harness/.version` only when every required check passes. Never
write the version stamp earlier.
