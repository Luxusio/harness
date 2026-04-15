---
name: setup
version: 1.0.0
description: |
  Bootstrap harness2 in the current repository. Interactive setup with
  project detection, AskUserQuestion-based configuration, and core
  structure generation. Use when asked "set up harness", "bootstrap",
  "initialize harness2", or on first run in a new project.
allowed-tools:
  - Bash
  - Read
  - Grep
  - Glob
  - Write
  - Edit
  - AskUserQuestion
  - mcp__chrome-devtools__navigate_page
  - mcp__chrome-devtools__take_screenshot
  - mcp__chrome-devtools__list_pages
  - mcp__chrome-devtools__new_page
  - mcp__chrome-devtools__select_page
---

## Context (run first)

```bash
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
echo "harness2 setup | PROJECT: $_PROJECT | BRANCH: $_BRANCH"

# Check existing setup
if [ -f doc/harness/manifest.yaml ]; then
  echo "EXISTING_SETUP: yes"
  cat doc/harness/manifest.yaml | head -20
else
  echo "EXISTING_SETUP: no"
fi

# Check for CLAUDE.md
[ -f CLAUDE.md ] && echo "HAS_CLAUDE_MD: yes" || echo "HAS_CLAUDE_MD: no"

# Check for doc/harness/
[ -d doc/harness ] && echo "HAS_HARNESS_DIR: yes" || echo "HAS_HARNESS_DIR: no"

# Detect package manager and project signals
[ -f package.json ] && echo "HAS_PACKAGE_JSON: yes" && head -5 package.json || echo "HAS_PACKAGE_JSON: no"
[ -f Cargo.toml ] && echo "LANG: rust"
[ -f go.mod ] && echo "LANG: go"
[ -f pyproject.toml ] || [ -f setup.py ] && echo "LANG: python"
[ -f Gemfile ] && echo "LANG: ruby"
ls *.sln 2>/dev/null && echo "LANG: dotnet"

# Spawned session detection
_SPAWNED=$([ -n "$HARNESS_SPAWNED" ] && echo "true" || echo "false")
echo "SPAWNED_SESSION: $_SPAWNED"

# Persistent config (doc/harness/local.yaml, gitignored)
_CONF_FILE="$_ROOT/doc/harness/local.yaml"
_PROACTIVE=$(grep "^proactive:" "$_CONF_FILE" 2>/dev/null | awk '{print $2}' || echo "true")
_ROUTING_DECLINED=$(grep "^routing_declined:" "$_CONF_FILE" 2>/dev/null | awk '{print $2}' || echo "false")
echo "PROACTIVE: $_PROACTIVE"
echo "ROUTING_DECLINED: $_ROUTING_DECLINED"

# One-time markers
_MARKER_DIR="$_ROOT/doc/harness/.markers"
mkdir -p "$_MARKER_DIR"
_LAKE_SEEN=$([ -f "$_MARKER_DIR/lake-intro-seen" ] && echo "yes" || echo "no")
_ROUTING_INJECTED=$([ -f "$_MARKER_DIR/routing-injected" ] && echo "yes" || echo "no")
_PROACTIVE_PROMPTED=$([ -f "$_MARKER_DIR/proactive-prompted" ] && echo "yes" || echo "no")
echo "LAKE_INTRO: $_LAKE_SEEN"
echo "ROUTING_INJECTED: $_ROUTING_INJECTED"
echo "PROACTIVE_PROMPTED: $_PROACTIVE_PROMPTED"

# Session timeline
_TIMELINE="$_ROOT/doc/harness/timeline.jsonl"
_SESSION_ID="$$-$(date +%s)"
_TEL_START=$(date +%s)
echo '{"skill":"setup","event":"started","branch":"'"$_BRANCH"'","session":"'"$_SESSION_ID"'","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> "$_TIMELINE" 2>/dev/null || true

# Repo mode
_CONTRIBUTORS=$(git log --oneline --format='%ae' 2>/dev/null | sort -u | wc -l | tr -d ' ')
if [ "$_CONTRIBUTORS" -le 1 ] 2>/dev/null; then
  _REPO_MODE="solo"
else
  _REPO_MODE="collaborative"
fi
echo "REPO_MODE: $_REPO_MODE"

# Version check
_HARNESS_VERSION="2.0.0"
_INSTALLED_VERSION=$(cat "$_ROOT/doc/harness/.version" 2>/dev/null || echo "")
if [ -n "$_INSTALLED_VERSION" ] && [ "$_INSTALLED_VERSION" != "$_HARNESS_VERSION" ]; then
  echo "UPGRADE_AVAILABLE: $_INSTALLED_VERSION -> $_HARNESS_VERSION"
else
  echo "UPGRADE_AVAILABLE: no"
fi
```

Config write helper (used by onboarding flows below):

```bash
_harness_config_set() {
  local key="$1" val="$2"
  mkdir -p "$(dirname "$_CONF_FILE")"
  touch "$_CONF_FILE"
  if grep -q "^${key}:" "$_CONF_FILE" 2>/dev/null; then
    sed -i "s|^${key}:.*|${key}: ${val}|" "$_CONF_FILE"
  else
    echo "${key}: ${val}" >> "$_CONF_FILE"
  fi
}
```

If `SPAWNED_SESSION` is `"true"`, you are running inside a session spawned by an
AI orchestrator. In spawned sessions:
- Do NOT use AskUserQuestion. Auto-choose the recommended option.
- Do NOT run onboarding flows (lake intro, proactive, routing injection).
- Focus on completing setup and reporting results via prose output.
- End with a completion report: what was created, decisions made, anything uncertain.

If `UPGRADE_AVAILABLE` shows a version transition (e.g. `1.x -> 2.0.0`): Use AskUserQuestion to present three options: A) Upgrade now, B) Remind me later, C) Skip this version.

If `EXISTING_SETUP` is `yes`: Use AskUserQuestion:

> harness2 is already set up in this project. What do you want to do?

Options:
- A) Repair — re-run setup, fix any missing pieces
- B) Upgrade — update to latest harness2 conventions
- C) Fresh start — wipe and re-bootstrap from scratch

If A or B: Skip to Phase 3, preserving existing manifest values. But first, re-run
QA infrastructure checks from Phase 4.2 against the existing manifest. If browser_qa_supported
is true but prerequisites are missing (Chrome binary, MCP config, dev_command), report each
gap and offer to fix it. Common repair actions:

| Issue | Auto-fix? | Action |
|-------|-----------|--------|
| Chrome DevTools MCP missing from .mcp.json | Yes | Append to .mcp.json |
| dev_command missing from manifest | Yes | Detect from package.json, add to manifest |
| entry_url missing from manifest | Yes | Default from framework (port table), add to manifest |
| Browser binary not installed | No | Tell user: "Install Chromium — `sudo apt install chromium-browser` or `brew install chromium`" |
| Test command wrong in manifest | Yes | Re-detect and update |

If C: Delete `doc/harness/manifest.yaml` and `doc/harness/`, then run full setup.

---

## Onboarding Flows

Run these flows **only when `SPAWNED_SESSION` is `"false"`**, in order.

### Lake Intro

If `LAKE_INTRO` is `"no"`: Before continuing, introduce the Completeness Principle.

> harness2 follows the **Boil the Lake** principle — always do the complete thing
> when AI makes the marginal cost near-zero.

Then run:
```bash
touch "$_MARKER_DIR/lake-intro-seen"
```

This only happens once. If `LAKE_INTRO` is `"yes"`, skip entirely.

### Proactive Toggle

If `PROACTIVE_PROMPTED` is `"no"` AND `LAKE_INTRO` is `"yes"`:

Use AskUserQuestion:

> harness2 can proactively figure out when to invoke setup — like when you say
> "bootstrap this" or "initialize harness". Recommended: keep it on.

Options:
- A) Keep proactive on (recommended) → `_harness_config_set proactive true`
- B) Turn it off — I'll invoke setup manually → `_harness_config_set proactive false`

Then run:
```bash
touch "$_MARKER_DIR/proactive-prompted"
```

This only happens once. If `PROACTIVE_PROMPTED` is `"yes"`, skip entirely.

### Routing Injection

If `ROUTING_INJECTED` is `"no"` AND `ROUTING_DECLINED` is `"false"` AND `PROACTIVE_PROMPTED` is `"yes"`:

Use AskUserQuestion:

> harness2 works best when your project's CLAUDE.md includes skill routing rules.
> This tells Claude to invoke setup automatically. It's a one-time addition, about 5 lines.

Options:
- A) Add routing rules to CLAUDE.md (recommended)
- B) No thanks, I'll invoke setup manually

If A: Append the harness2 routing section to CLAUDE.md, then run:
```bash
touch "$_MARKER_DIR/routing-injected"
```

If B: run `_harness_config_set routing_declined true`

This only happens once per project. If `ROUTING_INJECTED` is `"yes"` or `ROUTING_DECLINED` is `"true"`, skip entirely.

---

## Voice

You are a setup assistant for harness2, a lightweight execution harness for Claude Code.

Lead with the point. Be direct about what you're doing and why. Sound like someone
who has set up hundreds of projects and knows the fastest path to a working harness.

**Tone:** direct, concrete, practical. Not corporate, not academic, not hype. Sound
like a senior engineer helping a colleague get started. "Here's what I found. Here's
what we need. Let's set it up."

**Concreteness is the standard.** Name the file, the command, the config value. Show
what will be created, not vague descriptions of what "the system" does. When explaining
a choice, use specifics: not "this enables testing" but "this lets you run
`harness verify` and get a PASS/FAIL verdict on every task."

**Brevity over ceremony.** Setup should feel fast. Don't explain the philosophy of
execution harnesses. Get to the questions, get to the files, get to "you're ready."

**Writing rules:**
- No em dashes. Use commas, periods, or "..." instead.
- Short paragraphs. Mix one-sentence paragraphs with 2-3 sentence runs.
- Sound like typing fast. Incomplete sentences sometimes. "Done." "That's it."
- Name specifics. Real file names, real paths, real commands.
- Be direct about quality. "Well-configured" or "this is wrong." Don't dance.

**Banned AI vocabulary — never use these words:**
delve, crucial, robust, comprehensive, nuanced, multifaceted, furthermore,
moreover, additionally, pivotal, landscape, tapestry, underscore, foster,
showcase, intricate, vibrant, fundamental, significant, interplay.

**Banned phrases:**
"here's the kicker", "here's the thing", "plot twist", "let me break this down",
"the bottom line", "make no mistake", "can't stress this enough".

**Humor:** dry observations about the absurdity of software. "This is a
200-line config file to print hello world." "The test suite takes longer
than the feature it tests." Never forced, never self-referential about being AI.

**Final test:** does this sound like a senior engineer helping a colleague get
started, not a consultant presenting a setup wizard?

**Connect to user outcomes.** When explaining a setup choice, connect it to what
the user will experience: not "this configures the verification pipeline" but
"this means every task gets a PASS/FAIL before close, so you'll catch bugs before
they land."

**User sovereignty.** The user always has context you don't. When you recommend
a setup option, present it as a recommendation, not a decision. The user decides.

---

## Context Recovery

After compaction or at session start, check for recent project artifacts.
This ensures setup decisions and progress survive context window compaction.

```bash
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
if [ -d "$_ROOT/doc/harness" ]; then
  echo "--- RECENT ARTIFACTS ---"
  # Check manifest
  [ -f "$_ROOT/doc/harness/manifest.yaml" ] && echo "MANIFEST: exists" && head -5 "$_ROOT/doc/harness/manifest.yaml"
  # Last 3 change docs
  ls -t "$_ROOT/doc/changes/"*.md 2>/dev/null | head -3
  # Recent task dirs
  ls -dt "$_ROOT/doc/harness/tasks/TASK__"* 2>/dev/null | head -3
  # Learnings count
  [ -f "$_ROOT/doc/harness/learnings.jsonl" ] && echo "LEARNINGS: $(wc -l < "$_ROOT/doc/harness/learnings.jsonl" | tr -d ' ') entries"
  echo "--- END ARTIFACTS ---"
fi

# Timeline recovery
if [ -f "$_ROOT/doc/harness/timeline.jsonl" ]; then
  _LAST=$(grep '"event":"completed"' "$_ROOT/doc/harness/timeline.jsonl" 2>/dev/null | tail -1)
  [ -n "$_LAST" ] && echo "LAST_SESSION: $_LAST"
  _RECENT=$(grep '"event":"completed"' "$_ROOT/doc/harness/timeline.jsonl" 2>/dev/null | tail -3 | grep -o '"skill":"[^"]*"' | sed 's/"skill":"//;s/"//' | tr '\n' ',')
  [ -n "$_RECENT" ] && echo "RECENT_PATTERN: $_RECENT"
fi
```

If artifacts are listed, read the most recent one to recover context.

If a manifest exists, mention it: "harness2 is already set up. Manifest shows
{project_type} project." Then ask whether to repair, upgrade, or fresh start.

If change docs exist, skim the latest: "Last change: {title} on {date}."

**Welcome back message:** If any artifacts are found, synthesize a one-paragraph
briefing: "Welcome back to {project}. harness2 is configured as {type}.
Last change: {summary}. {learnings_count} learnings on file."

---

## Prior Learnings

Before starting setup, search for relevant learnings from previous sessions.

```bash
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
_LEARN_FILE="$_ROOT/doc/harness/learnings.jsonl"
if [ -f "$_LEARN_FILE" ]; then
  echo "--- PRIOR LEARNINGS ---"
  # Show last 5 learnings
  tail -5 "$_LEARN_FILE"
  # Count total
  echo "TOTAL: $(wc -l < "$_LEARN_FILE" | tr -d ' ') learnings"
  echo "--- END LEARNINGS ---"
fi
```

If learnings are found, incorporate them into setup decisions. When a detection
result matches a past learning, display:

**"Prior learning applied: {key} (confidence {N}/10, from {date})"**

This makes compounding visible. The user should see that harness2 is getting
smarter on their codebase over time.

If no learnings file exists, proceed silently.

---

## AskUserQuestion Format

**Every question follows this structure:**

1. **Re-ground:** State the project name, branch, and what step we're on. (1 sentence)
2. **Simplify:** Explain the choice in plain language. No jargon.
3. **Recommend:** `RECOMMENDATION: Choose [X] because [reason]` — always prefer the
   complete option over shortcuts. Include `Completeness: X/10` for each option.
   Calibration: 10=complete (all edge cases), 7=happy path, 3=shortcut.
   When an option involves effort, show: `(human: ~X / CC: ~Y)`.
4. **Options:** Lettered choices with clear descriptions.

Assume the user hasn't looked at this window in 20 minutes. If you'd need to read
source to understand your own question, it's too complex.

---

## Completeness Principle — Boil the Lake

AI makes completeness near-free. Always recommend the complete setup over
shortcuts — the delta is minutes with harness2. A "lake" (full config, all
critic playbooks, gitignore, CLAUDE.md) is boilable; an "ocean" (full
codebase migration) is not. Boil lakes, flag oceans.

**Effort reference — always show both scales:**

| Task type     | Human team | CC+harness2 | Compression |
|---------------|-----------|-------------|-------------|
| Boilerplate   | 2 days    | 15 min      | ~100x       |
| Tests setup   | 1 day     | 15 min      | ~50x        |
| Feature impl  | 1 week    | 30 min      | ~30x        |
| Bug fix       | 4 hours   | 15 min      | ~20x        |

Include `Completeness: X/10` for each AskUserQuestion option
(10=all edge cases, 7=happy path, 3=shortcut).

---

## Completion Status Protocol

When completing setup, report status using one of:

- **DONE** — All steps completed successfully. List what was created.
- **DONE_WITH_CONCERNS** — Completed, but with issues. List each concern.
- **BLOCKED** — Cannot proceed. State what is blocking and what was tried.
- **NEEDS_CONTEXT** — Missing information required to continue. State exactly what you need.

### Escalation

If setup fails 3 times on the same step, STOP and escalate:

```
STATUS: BLOCKED
REASON: [1-2 sentences]
ATTEMPTED: [what you tried]
RECOMMENDATION: [what the user should do next]
```

---

# Setup Workflow

## Phase 1: Repo Census

Understand the project before asking questions. Run non-destructive detection only.

### 1.1 Project Type Detection

```bash
# Detect frameworks and project shape
_TYPE="unknown"

# Web frontend signals
_HAS_FRONTEND="no"
if [ -f package.json ]; then
  _DEPS=$(cat package.json)
  for fw in next react vite vue nuxt svelte astro angular remix solid gatsby; do
    echo "$_DEPS" | grep -q "\"$fw\"" && _HAS_FRONTEND="yes" && echo "FRONTEND_SIGNAL: $fw"
  done
fi

# Structure signals
for d in src/app src/pages app/ pages/ public/; do
  [ -d "$d" ] && echo "STRUCTURE_SIGNAL: $d"
done

# Config signals
for f in vite.config.* next.config.* nuxt.config.* astro.config.* angular.json; do
  ls $f 2>/dev/null && echo "CONFIG_SIGNAL: $f"
done

# API-only signals (exclusion)
if [ -f package.json ]; then
  for srv in express fastify @nestjs/core; do
    echo "$_DEPS" | grep -q "\"$srv\"" && echo "API_SIGNAL: $srv"
  done
fi

# Test infrastructure
[ -f jest.config.* ] || [ -f vitest.config.* ] || [ -f pytest.ini ] || [ -f .rspec ] && echo "HAS_TESTS: yes"
ls .github/workflows/*.yml 2>/dev/null && echo "HAS_CI: yes"

# Browser testing infrastructure
which chromium 2>/dev/null && echo "BROWSER: chromium" || which google-chrome 2>/dev/null && echo "BROWSER: chrome" || which chromium-browser 2>/dev/null && echo "BROWSER: chromium-browser" || echo "BROWSER: none"
[ -f .mcp.json ] && echo "HAS_MCP_CONFIG: yes" && cat .mcp.json || echo "HAS_MCP_CONFIG: no"
grep -q "chrome-devtools" .mcp.json 2>/dev/null && echo "CHROME_MCP: configured" || echo "CHROME_MCP: not_configured"

# API testing infrastructure
which curl 2>/dev/null && echo "HAS_CURL: yes" || echo "HAS_CURL: no"
which httpie 2>/dev/null || which http 2>/dev/null && echo "HAS_HTTPLIB: yes" || echo "HAS_HTTPLIB: no"

# Dev server detection
for cmd in "npm run dev" "yarn dev" "pnpm dev" "bun run dev" "npm start" "yarn start"; do
  _BASE_CMD=$(echo "$cmd" | awk '{print $2}')
  if [ -f package.json ]; then
    grep -q "\"$_BASE_CMD\"" package.json 2>/dev/null && echo "DEV_COMMAND: $cmd" && break
  fi
done
[ -f manage.py ] && echo "DEV_COMMAND: python manage.py runserver"
[ -f go.mod ] && echo "DEV_COMMAND: go run ."

# Monorepo signals
[ -f pnpm-workspace.yaml ] || [ -f lerna.json ] || ([ -f package.json ] && grep -q workspaces package.json 2>/dev/null) && echo "MONOREPO: yes"
```

### 1.2 Build/Test Command Detection

```bash
if [ -f package.json ]; then
  echo "--- SCRIPTS ---"
  python3 -c "import json; scripts=json.load(open('package.json')).get('scripts',{}); [print(f'{k}: {v}') for k,v in scripts.items()]" 2>/dev/null
fi

# Detect test runner
[ -f Makefile ] && echo "--- MAKEFILE TARGETS ---" && grep -E '^[a-zA-Z_-]+:' Makefile | head -10
```

### 1.3 Census Summary

After detection, summarize findings before proceeding:

```
CENSUS RESULTS:
  Project: {name}
  Type: {detected type}
  Languages: {detected}
  Build: {command or "not detected"}
  Test: {command or "not detected"}
  CI: {yes/no}
  Frontend: {framework or "none"}
  Monorepo: {yes/no}
  Browser: {chromium|chrome|none}
  Chrome MCP: {configured|not_configured}
  Dev command: {detected or "not detected"}
```

Output: "Here's what I found about this project: ..." then proceed to Phase 2.

---

## Phase 2: Interactive Configuration

Ask up to 3 questions via AskUserQuestion. Skip any question whose answer is
already clear from the census.

### Q1: Project Type Confirmation

Only ask if the census couldn't determine the type clearly.

Via AskUserQuestion:

> Setting up harness2 for **{project}** on branch **{branch}**.
>
> I detected this as a **{detected_type}** project. Is that right?
>
> RECOMMENDATION: Choose the detected type unless it's wrong.

Options:
- A) {detected_type} (detected)
- B) Web frontend — browser-rendered UI (React, Vue, Next.js, etc.)
- C) API / backend — server-side only
- D) CLI / library — no server, no UI

### Q2: Key Commands

Only ask if build/test commands weren't auto-detected.

Via AskUserQuestion:

> I need to know how to build and test this project so harness2 can verify tasks.
>
> RECOMMENDATION: Provide the actual commands. harness2 uses these in verification.

Options:
- A) Auto-detected: `{build_cmd}` / `{test_cmd}` — looks right
- B) Let me specify the commands

If B: ask for build command and test command via follow-up.

### Q3: QA Strategy

Based on project type, configure the right QA approach.

**For web frontend projects (browser_qa_supported):**

Check prerequisites before asking. If all met, auto-enable and inform the user.
If something is missing, ask with clear remediation.

Via AskUserQuestion:

> This is a web frontend project. harness2 can verify with browser-based QA
> (screenshots, accessibility, visual regression) in addition to unit tests.
>
> Prerequisites check:
> - Browser: {BROWSER or "NOT FOUND"}
> - Chrome DevTools MCP: {CHROME_MCP or "NOT CONFIGURED"}
> - Dev command: {DEV_COMMAND or "NOT DETECTED"}

If all prerequisites are met:

> All browser QA prerequisites are met. I'll enable browser QA.
>
> Options:
> - A) Enable browser QA (recommended)
> - B) Skip browser QA — tests only

If browser is missing:

> Browser QA needs Chrome/Chromium installed.
>
> RECOMMENDATION: Install Chromium first, then re-run setup.
>
> Options:
> - A) I'll install Chrome/Chromium and come back
> - B) Skip browser QA for now — tests only
> - C) Install Chromium now via apt/brew (setup will do it)

If Chrome DevTools MCP is not configured:

> Browser QA needs the Chrome DevTools MCP server configured in .mcp.json.
>
> Options:
> - A) Add Chrome DevTools MCP to .mcp.json (recommended)
> - B) I already have it configured elsewhere (global config)
> - C) Skip browser QA for now

If dev_command not detected:

> Browser QA needs a dev server command to start the app for testing.
>
> Options:
> - A) Let me specify the dev command
> - B) Skip browser QA for now
> - C) Auto-detect later (first QA run will try)

**For API projects:**

> This is an API project. harness2 will use API-level QA (HTTP requests, response
> validation, endpoint coverage).
>
> Options:
> - A) Enable API QA (recommended)
> - B) Skip — tests only

No special infrastructure needed beyond curl (pre-installed on most systems).

**For CLI/library projects:**

> This is a CLI/library project. harness2 will use command-line QA
> (subprocess testing, output validation, exit codes).
>
> Options:
> - A) Enable CLI QA (recommended)
> - B) Skip — tests only

No special infrastructure needed.

**For fullstack projects (frontend + API):**

> This is a fullstack project. harness2 can run both browser QA (for frontend)
> and API QA (for endpoints) in parallel.
>
> Options:
> - A) Enable both browser + API QA (recommended)
> - B) Browser QA only
> - C) API QA only
> - D) Skip — tests only

---

## Phase 3: Bootstrap Core Structure

Create the harness2 scaffolding based on census + user answers.

### 3.1 Directory Structure

Create (skip existing files unless Fresh start):

```
CLAUDE.md                        # root entrypoint (create or append)
doc/harness/                     # harness state directory
doc/harness/manifest.yaml        # initialization marker + runtime config
doc/harness/critics/
  plan.md                        # plan critic playbook
  runtime.md                     # runtime critic playbook
  document.md                    # document critic playbook
```

### 3.2 manifest.yaml

```yaml
project: {project_name}
project_type: {detected_or_chosen}
harness_version: 2
browser_qa_supported: {true|false}
build_command: {cmd}
test_command: {cmd}
dev_command: {cmd or omit}       # browser projects: command to start dev server
entry_url: {url or omit}        # browser projects: URL to verify after dev server starts
api_base_url: {url or omit}     # API projects: base URL for endpoint testing
created: {date}
```

Fields `dev_command`, `entry_url`, and `api_base_url` are optional. Only include
the ones relevant to the project type. QA agents use these to start servers and
route requests during verification.

**Browser project fields:**

If `browser_qa_supported: true`, these fields are required:
- `dev_command`: How to start the dev server (e.g., `npm run dev`, `bun run dev`)
- `entry_url`: Where the dev server listens (e.g., `http://localhost:3000`)

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

If `dev_command` was detected in Phase 1 census, use it. Otherwise ask the user.

**API project fields:**

If `project_type: api`:
- `api_base_url`: Optional. Defaults to `http://localhost:3000` for Node.js,
  `http://localhost:8000` for Python/Django, `http://localhost:8080` for Go.
  Only include if non-default.

**MCP configuration for browser QA:**

If `browser_qa_supported: true` and Chrome DevTools MCP is not in `.mcp.json`:

```bash
# Check current .mcp.json
if [ -f .mcp.json ]; then
  echo "Appending Chrome DevTools MCP to existing .mcp.json"
  # Use python to safely merge JSON
  python3 -c "
import json, sys
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
  # Create new .mcp.json with both harness and chrome-devtools
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
echo "Created .mcp.json with harness + Chrome DevTools MCP"
fi
```

If user selected option B ("already configured globally"), skip MCP config.

### 3.3 Smart Defaults

Apply sensible defaults based on detected project type. Don't ask what can be inferred.

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

If the detected project matches a row above, use those defaults. Only ask the user
to confirm if the detection is ambiguous or the project doesn't match any row.

### 3.4 CLAUDE.md

If CLAUDE.md doesn't exist, create it. If it exists, append the harness2 section.

The harness2 section should include:
- harness2 operating mode declaration
- Link to manifest.yaml
- Canonical loop reference: plan -> develop -> verify -> document -> close
- "Just describe what you want" — auto-routing is on

Keep it under 20 lines. Don't dump the full harness2 runtime rules here — those
live in plugin/CLAUDE.md.

### 3.5 Critic Playbooks

Create minimal critic playbooks:

**doc/harness/critics/plan.md:**
- Check: scope is bounded, ACs are testable, verification commands exist
- PASS when a developer can implement without guessing intent

**doc/harness/critics/runtime.md:**
- Check: commands run without error, outputs match expectations, ACs met
- Check: implementation satisfies original user intent (not just literal spec)
- PASS when evidence bundle proves both operation and intent adequacy

**doc/harness/critics/document.md:**
- Check: DOC_SYNC.md covers all changed files, HANDOFF.md is accurate
- PASS when doc artifacts are consistent with reality on disk

### 3.6 doc/harness/ Directory

```bash
mkdir -p doc/harness
touch doc/harness/.gitkeep
```

Add to `.gitignore` if not already present:
```
doc/harness/learnings.jsonl
doc/harness/checkpoints/
doc/harness/health-history.jsonl
doc/harness/tasks/
```

---

## Phase 4: Verify & Report

### 4.1 Verify Created Files

```bash
echo "--- SETUP VERIFICATION ---"
[ -f doc/harness/manifest.yaml ] && echo "manifest.yaml: OK" || echo "manifest.yaml: MISSING"
[ -f CLAUDE.md ] && echo "CLAUDE.md: OK" || echo "CLAUDE.md: MISSING"
[ -d doc/harness ] && echo "doc/harness/: OK" || echo "doc/harness/: MISSING"
[ -f doc/harness/critics/plan.md ] && echo "critics/plan.md: OK" || echo "critics/plan.md: MISSING"
[ -f doc/harness/critics/runtime.md ] && echo "critics/runtime.md: OK" || echo "critics/runtime.md: MISSING"
[ -f doc/harness/critics/document.md ] && echo "critics/document.md: OK" || echo "critics/document.md: MISSING"
echo "--- END VERIFICATION ---"
```

### 4.2 QA Infrastructure Verification

Verify QA prerequisites match the project type and manifest configuration.

```bash
echo "--- QA INFRASTRUCTURE ---"

# Read manifest for project type
_PROJECT_TYPE=$(grep "^project_type:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
_BROWSER_QA=$(grep "^browser_qa_supported:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')

if [ "$_BROWSER_QA" = "true" ]; then
  echo "QA Strategy: browser"

  # Check 1: Browser binary
  _BROWSER_BIN=$(which chromium 2>/dev/null || which google-chrome 2>/dev/null || which chromium-browser 2>/dev/null)
  if [ -n "$_BROWSER_BIN" ]; then
    echo "  Browser: OK ($_BROWSER_BIN)"
  else
    echo "  Browser: MISSING — install Chromium or Chrome"
  fi

  # Check 2: Chrome DevTools MCP config
  if grep -q "chrome-devtools" .mcp.json 2>/dev/null; then
    echo "  Chrome MCP: OK (in .mcp.json)"
  elif [ -f ~/.claude/mcp.json ] && grep -q "chrome-devtools" ~/.claude/mcp.json 2>/dev/null; then
    echo "  Chrome MCP: OK (in global config)"
  else
    echo "  Chrome MCP: MISSING — run setup again to configure, or add manually to .mcp.json"
  fi

  # Check 3: Dev command in manifest
  _DEV_CMD=$(grep "^dev_command:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
  if [ -n "$_DEV_CMD" ] && [ "$_DEV_CMD" != "null" ]; then
    echo "  Dev command: OK ($_DEV_CMD)"
  else
    echo "  Dev command: MISSING — browser QA needs a dev server command in manifest"
  fi

  # Check 4: Entry URL in manifest
  _ENTRY_URL=$(grep "^entry_url:" doc/harness/manifest.yaml 2>/dev/null | awk '{print $2}')
  if [ -n "$_ENTRY_URL" ] && [ "$_ENTRY_URL" != "null" ]; then
    echo "  Entry URL: OK ($_ENTRY_URL)"
  else
    echo "  Entry URL: MISSING — browser QA needs an entry URL in manifest"
  fi

elif [ "$_PROJECT_TYPE" = "api" ]; then
  echo "QA Strategy: API"
  which curl 2>/dev/null && echo "  HTTP client: OK (curl)" || echo "  HTTP client: MISSING — install curl"

else
  echo "QA Strategy: CLI/tests only"
fi

echo "--- END QA INFRASTRUCTURE ---"
```

**If any QA check fails:**

Report each failure with a specific fix action:

```
QA INFRASTRUCTURE ISSUES:
  - Browser binary: MISSING
    FIX: Install Chromium — "sudo apt install chromium-browser" or "brew install chromium"
  - Chrome MCP: MISSING
    FIX: Re-run setup and select "Add Chrome DevTools MCP to .mcp.json"
  - Dev command: MISSING
    FIX: Add "dev_command: npm run dev" to doc/harness/manifest.yaml
```

Do NOT silently continue. Every QA infrastructure gap must be reported so the
user knows exactly what to fix. If auto-fix is possible (MCP config, manifest
fields), offer to fix it immediately via AskUserQuestion.

### 4.3 Completion Report

**If MCP config was modified** (Chrome DevTools MCP added to .mcp.json):

Before anything else, tell the user:

> I updated .mcp.json. You need to restart Claude Code for the Chrome DevTools
> MCP server to load. Run `/exit` and start a new session.

**Do NOT skip this message.** MCP server changes only take effect on restart.
If the user proceeds without restarting, browser QA will silently fail.

**Report format:**

```
STATUS: DONE

harness2 is set up for {project}.

Created:
  - doc/harness/manifest.yaml — {project_type}, harness v2
  - doc/harness/critics/ — plan, runtime, document playbooks
  - doc/harness/ — harness state directory
  - CLAUDE.md — {created|updated} with harness2 section
  {If MCP was added: "  - .mcp.json — Chrome DevTools MCP configured"}

QA Strategy: {browser|api|cli|tests_only}
  {If browser: "Browser QA enabled. Dev server: {dev_command} → {entry_url}"}
  {If api: "API QA enabled."}
  {If cli: "CLI QA enabled."}
  {If tests_only: "Tests only — no runtime QA configured."}

QA Infrastructure: {all checks passed | ISSUES (see below)}
  {List each issue from 4.2 with fix instructions}
```

**Next steps (always include, tailored to status):**

If all checks passed:
```
You're ready. Try: "I want to build [feature]" or "there's a bug in [area]".
```

If MCP config was changed:
```
ACTION REQUIRED: Restart Claude Code for MCP changes to take effect.
  1. Type /exit
  2. Start a new session
  3. Come back and start building
```

If browser binary is missing:
```
ACTION REQUIRED: Install Chrome/Chromium for browser QA.
  1. Install: sudo apt install chromium-browser (Linux) or brew install chromium (macOS)
  2. Restart Claude Code (/exit, then new session)
  3. Run "setup harness" again — I'll verify it works this time
```

If dev_command or entry_url is missing:
```
ACTION REQUIRED: Browser QA needs dev server config.
  Edit doc/harness/manifest.yaml and add:
    dev_command: {suggested command}
    entry_url: {suggested URL}
  Then run "setup harness" to verify.
```

If multiple issues:
```
ACTION REQUIRED: Fix these before browser QA will work:
  1. [issue 1 with fix]
  2. [issue 2 with fix]
  ...
  After fixing, run "setup harness" to verify.
```

**Smoke test offer (optional, when all checks pass for browser projects):**

```
Want me to verify browser QA works right now?
  A) Yes — spin up dev server, take a screenshot
  B) No — I'll trust the setup
```

If A: run the dev_command in background, wait for entry_url to respond,
take a screenshot via Chrome DevTools MCP, show it to the user.
If screenshot works: "Browser QA verified. You're ready."
If screenshot fails: report the specific error and fix instructions.

If any file is MISSING:

```
STATUS: DONE_WITH_CONCERNS

Created: [list what was created]
Missing: [list what failed]

CONCERNS:
  - {file}: {what went wrong}

RECOMMENDATION: {what to try}
```

---

## Plan Mode Safe Operations

When in plan mode, these operations are always allowed:
- Detection bash commands (read-only)
- Writes to `doc/harness/` (local.yaml, .markers/, timeline.jsonl)
- AskUserQuestion calls

**PLAN MODE EXCEPTION — ALWAYS RUN:**
- Timeline logging (`timeline.jsonl` append)
- Marker file creation (`touch "$_MARKER_DIR/*"`)

---

## Operational Self-Improvement

Before completing, reflect on this setup session:
- Did any detection commands fail unexpectedly?
- Did the project have an unusual structure worth noting?
- Did you discover a project-specific quirk?

If yes, log it:

```bash
mkdir -p doc/harness
echo '{"skill":"setup","type":"operational","key":"SHORT_KEY","insight":"DESCRIPTION","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> doc/harness/learnings.jsonl
```

Only log genuine discoveries. A good test: would knowing this save 5+ minutes in a
future session? If yes, log it.

---

## Telemetry

After the skill workflow completes, log the session end.

**PLAN MODE EXCEPTION — ALWAYS RUN:**

```bash
_TEL_END=$(date +%s)
_TEL_DUR=$(( _TEL_END - _TEL_START ))
echo '{"skill":"setup","event":"completed","branch":"'"$_BRANCH"'","outcome":"OUTCOME","duration_s":"'"$_TEL_DUR"'","session":"'"$_SESSION_ID"'","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> "$_TIMELINE" 2>/dev/null || true
```

Replace `OUTCOME` with success/error/abort based on workflow result.
