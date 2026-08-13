---
name: setup
description: |
  Bootstrap harness in the current repository. Interactive setup with
  project detection, conversational configuration, and core structure
  generation. Use when asked "set up harness", "bootstrap", "initialize
  harness", or on first run in a new project.
user-invocable: true
---

# Codex-authored orchestration with runtime-neutral setup sub-files packaged
# beside this SKILL.md.


> **Codex runtime notes** (delta from Claude Code):
> - This skill calls for "ask the user" interactions at decision points. Codex CLI does not have
>   a structured `AskUserQuestion` primitive — emit the question + options to the user
>   conversationally and read the reply from the next user turn. Honor the same Re-ground / Simplify
>   / Recommend / Options structure in plain prose.
> - Codex does not inject plugin-root variables into ordinary shell commands.
>   The adjacent bootstrap/verify sub-files discover the installed
>   `~/.codex/harness/plugins/harness` mirror and infer Codex from its manifest.
> - Browser-QA prerequisite probes (Chrome DevTools MCP) — Codex CAN register Playwright/chrome-
>   devtools MCP servers, but the qa-browser agent prompt is Claude-coupled in v1. Treat
>   `qa.browser_qa_supported: false` as the v1 default on Codex side; v2 will lift this.
>   Setup reads MCP availability from current session tools or Codex/global runtime
>   config. Project-root `.mcp.json` remains user-owned configuration.
> - The public `$harness:run` entry loads the internal workflow inline. When Codex exposes
>   `spawn_agent` directly or through lazy tool discovery, that workflow uses it for independent
>   review and QA; setup itself contains no fan-out call sites.

## Sub-files

| File | Content |
|------|---------|
| `project-interview.md` | Phase 2.0: lightweight purpose/verification interview + fixed defaults |
| `repo-census.md` | Phase 1: project type detection, build/test command detection, summary |
| `bootstrap.md` | Phase 3: directory, manifest.yaml, AGENTS.md, critics, contracts install |
| `verify-report.md` | Phase 4: file verification, QA infra verification, completion report |

Phase 2 (interactive Q1-Q3) stays inline below.

All listed sub-files and templates ship under this installed `skills/setup/`
directory. Resolve them relative to this SKILL.md, never through a source
checkout path.

---

## Context (run first)

```bash
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
if [ ! -e "$_ROOT/.git" ]; then
  _SOURCE_GIT_ROOTS=$(find "$_ROOT" -mindepth 2 -maxdepth 2 -name .git \
    -printf '%h\n' 2>/dev/null | sort -u)
  echo "NON_GIT_CONTROL_ROOT: yes"
  printf 'SOURCE_GIT_ROOT: %s\n' $_SOURCE_GIT_ROOTS
fi
echo "harness setup | PROJECT: $_PROJECT | BRANCH: $_BRANCH"

[ -f doc/harness/manifest.yaml ] && echo "EXISTING_SETUP: yes" && head -20 doc/harness/manifest.yaml || echo "EXISTING_SETUP: no"
[ -f CLAUDE.md ] && echo "HAS_CLAUDE_MD: yes" || echo "HAS_CLAUDE_MD: no"
[ -f AGENTS.md ] && echo "HAS_AGENTS_MD: yes" || echo "HAS_AGENTS_MD: no"
[ -d doc/harness ] && echo "HAS_HARNESS_DIR: yes" || echo "HAS_HARNESS_DIR: no"
[ -f package.json ] && echo "HAS_PACKAGE_JSON: yes" && head -5 package.json || echo "HAS_PACKAGE_JSON: no"
[ -f Cargo.toml ] && echo "LANG: rust"
[ -f go.mod ] && echo "LANG: go"
[ -f pyproject.toml ] || [ -f setup.py ] && echo "LANG: python"
[ -f Gemfile ] && echo "LANG: ruby"
ls *.sln 2>/dev/null && echo "LANG: dotnet"

_SPAWNED=$([ -n "$HARNESS_SPAWNED" ] && echo "true" || echo "false")
echo "SPAWNED_SESSION: $_SPAWNED"

# Persistent config (gitignored)
_CONF_FILE="$_ROOT/doc/harness/local.yaml"
_PROACTIVE=$(grep "^proactive:" "$_CONF_FILE" 2>/dev/null | awk '{print $2}' || echo "true")
_ROUTING_DECLINED=$(grep "^routing_declined:" "$_CONF_FILE" 2>/dev/null | awk '{print $2}' || echo "false")

# One-time markers
_MARKER_DIR="$_ROOT/doc/harness/.markers"
mkdir -p "$_MARKER_DIR"
_LAKE_SEEN=$([ -f "$_MARKER_DIR/lake-intro-seen" ] && echo "yes" || echo "no")
_ROUTING_INJECTED=$([ -f "$_MARKER_DIR/routing-injected" ] && echo "yes" || echo "no")
_PROACTIVE_PROMPTED=$([ -f "$_MARKER_DIR/proactive-prompted" ] && echo "yes" || echo "no")

# Repo mode
_CONTRIBUTORS=$(git log --oneline --format='%ae' 2>/dev/null | sort -u | wc -l | tr -d ' ')
[ "$_CONTRIBUTORS" -le 1 ] 2>/dev/null && _REPO_MODE="solo" || _REPO_MODE="collaborative"

# Version check
_HARNESS_VERSION="2.3.0"
_INSTALLED_VERSION=$(cat "$_ROOT/doc/harness/.version" 2>/dev/null || echo "")
[ -n "$_INSTALLED_VERSION" ] && [ "$_INSTALLED_VERSION" != "$_HARNESS_VERSION" ] && echo "UPGRADE_AVAILABLE: $_INSTALLED_VERSION -> $_HARNESS_VERSION" || echo "UPGRADE_AVAILABLE: no"
```

Config helper:
```bash
_harness_config_set() {
  local key="$1" val="$2"
  mkdir -p "$(dirname "$_CONF_FILE")"
  touch "$_CONF_FILE"
  grep -q "^${key}:" "$_CONF_FILE" 2>/dev/null \
    && sed -i "s|^${key}:.*|${key}: ${val}|" "$_CONF_FILE" \
    || echo "${key}: ${val}" >> "$_CONF_FILE"
}
```

### Spawned mode

If `SPAWNED_SESSION=true`: skip user interactions (auto-choose recommended); no onboarding flows; focus on completing setup and reporting via prose; end with completion report.

### Upgrade path

If `UPGRADE_AVAILABLE` shows a version transition, ask the user conversationally:

> "harness {old} → {new} is available. A) Upgrade now (recommended). B) Remind me later. C) Skip this version."

### Existing setup

If `EXISTING_SETUP: yes`, ask the user:

> "harness is already set up here. A) Repair — re-run and fix missing pieces (recommended for upgrade-time). B) Upgrade — pull in new conventions. C) Fresh start — wipe `doc/harness/` and re-bootstrap."

A/B: skip to Phase 3 preserving existing manifest values; first re-run Phase 4.2 QA infra checks against existing manifest. Repair matrix:

| Issue | Auto-fix? | Action |
|-------|-----------|--------|
| legacy `project_type`/flat QA manifest schema | Yes | Run `setup_finalize.py`; migrate to v5 while preserving unknown fields |
| dev_command missing from manifest | Yes | Detect from package.json, add |
| entry_url missing from manifest | Yes | Default from framework port table |
| Test command wrong in manifest | Yes | Re-detect and update |
| Codex `[mcp_servers.harness]` missing in `~/.codex/config.toml` | Yes | Re-emit via `plugin-codex/config.toml.example` snippet (additive merge with backup) |
| `[hooks.state.<key>]` trust table missing | Yes | Re-emit trust hashes |

C: delete `doc/harness/manifest.yaml` and `doc/harness/`, run full setup.

---

## Onboarding Flows (only when SPAWNED_SESSION=false)

### Lake Intro (once per project)

If `LAKE_INTRO=no`, surface this information once:

> harness follows the **Boil the Lake** principle — always do the complete thing when AI makes the marginal cost near-zero.

Then: `touch "$_MARKER_DIR/lake-intro-seen"`.

### Proactive Toggle + Routing Injection

Do not ask about either setting. On every fresh, repair, or upgrade setup:

1. Run `_harness_config_set proactive true` and
   `_harness_config_set routing_declined false`.
2. Touch `"$_MARKER_DIR/proactive-prompted"`.
3. Emit/replace the Harness routing block in AGENTS.md; touch `"$_MARKER_DIR/routing-injected"`.

The routing block includes the Durable Decision Documentation Gate. Setup
owns these defaults and reapplies them on rerun so onboarding stays consistent.
User-stated durable decisions are not handled until documented under `doc/` or
given a specific no-doc rationale in the PLAN durable-doc decision.

---

## Voice

Direct, concrete, practical. Senior engineer helping a colleague, not a consultant. Name files/commands/config values. No corporate/academic/hype tone. Show what will be created, not vague descriptions of what "the system" does.

- No em dashes (use commas/periods/"…").
- Short paragraphs. Mix one-sentence with 2-3 sentence runs. "Done." "That's it."
- Real names, real paths, real commands.
- Be direct about quality. "Well-configured" or "this is wrong."

**Banned vocabulary:** delve, crucial, robust, comprehensive, nuanced, multifaceted, furthermore, moreover, additionally, pivotal, landscape, tapestry, underscore, foster, showcase, intricate, vibrant, fundamental, significant, interplay.

**Banned phrases:** "here's the kicker", "the bottom line", "let me break this down", "make no mistake", "can't stress this enough".

**User sovereignty.** The user has context you don't. Recommendations, not decisions. The user decides.

**Connect recommendations to user outcomes.** Not "configures the verification pipeline" but "every task gets a PASS/FAIL before close, so you'll catch bugs before they land."

---

## Context Recovery (session start / post-compaction)

```bash
if [ -d "$_ROOT/doc/harness" ]; then
  [ -f "$_ROOT/doc/harness/manifest.yaml" ] && echo "MANIFEST: exists" && head -5 "$_ROOT/doc/harness/manifest.yaml"
  ls -t "$_ROOT/doc/changes/"*.md 2>/dev/null | head -3
  ls -dt "$_ROOT/doc/harness/tasks/TASK__"* 2>/dev/null | head -3
  [ -f "$_ROOT/doc/harness/learnings.jsonl" ] && echo "LEARNINGS: $(wc -l < "$_ROOT/doc/harness/learnings.jsonl" | tr -d ' ')"
fi
```

If artifacts found: synthesize one-paragraph welcome-back briefing. If manifest exists: "harness is already set up. Manifest shows {type} project." → offer repair/upgrade/fresh.

## Prior Learnings

```bash
_LEARN_FILE="$_ROOT/doc/harness/learnings.jsonl"
[ -f "$_LEARN_FILE" ] && tail -5 "$_LEARN_FILE" && echo "TOTAL: $(wc -l < "$_LEARN_FILE" | tr -d ' ')"
```

When detection matches a prior learning, surface:
> **Prior learning applied: {key} (confidence {N}/10, from {date})**

Compounding visibility. No file → proceed silently.

---

## Conversational ask format

Every interactive choice point in this skill follows the same shape (the Claude side used a structured AskUserQuestion tool; Codex emits the same content as prose):

1. **Re-ground** — project name, branch, step (1 sentence).
2. **Simplify** — plain language, no jargon.
3. **Recommend** — `RECOMMENDATION: Choose [X] because [reason]`. Include `Completeness: X/10` per option (10=complete, 7=happy path, 3=shortcut). When effort-heavy, show `(human: ~X / Codex: ~Y)`.
4. **Options** — lettered with clear descriptions. Tell the user to reply with the letter.

Assume the user hasn't looked at this window in 20 min. If you'd need to read source to understand your own question, it's too complex.

## Completeness — Boil the Lake

AI makes completeness near-free. Always recommend complete setup over shortcuts. A "lake" (full config, critic playbooks, gitignore, AGENTS.md) is boilable; an "ocean" (full codebase migration) is not.

| Task type     | Human team | Codex+harness | Compression |
|---------------|-----------|----------------|-------------|
| Boilerplate   | 2 days    | 15 min         | ~100× |
| Tests setup   | 1 day     | 15 min         | ~50× |
| Feature impl  | 1 week    | 30 min         | ~30× |
| Bug fix       | 4 hours   | 15 min         | ~20× |

## Completion Status Protocol

- **DONE** — all steps complete, list what was created.
- **DONE_WITH_CONCERNS** — completed with issues, list each.
- **BLOCKED** — state what's blocking + what was tried.
- **NEEDS_CONTEXT** — state exactly what info is needed.

**Escalation.** 3 failed attempts on same step → STOP:
```
STATUS: BLOCKED
REASON: [1-2 sentences]
ATTEMPTED: [what was tried]
RECOMMENDATION: [what user should do next]
```

---

# Setup Workflow

## Phase 1: Repo Census

Non-destructive detection. Read the adjacent `repo-census.md` for full
detection bash, build/test command sniffing, and summary format.

## Phase 2: Interactive Configuration

### Phase 2.0: Project interview

Read the adjacent `project-interview.md` and follow in full. Ask only for
missing project purpose or undetectable verification facts. Apply the fixed
audience, workflow, full-loop, and failure-avoidance defaults without asking.
Stage answers until the canonical manifest exists, then apply them in Phase 3.5.

**Skip detection (evaluate in order; any match skips the interview):**

```bash
_SKIP_INTERVIEW="false"
_SKIP_REASON=""

# Explicit flag — check $ARGUMENTS and env
case " ${ARGUMENTS:-} " in *" --skip-interview "*) _SKIP_INTERVIEW="true"; _SKIP_REASON="--skip-interview flag" ;; esac
[ "${HARNESS_SKIP_INTERVIEW:-}" = "1" ] && _SKIP_INTERVIEW="true" && _SKIP_REASON="HARNESS_SKIP_INTERVIEW=1"

# Upgrade/rerun: existing summary + manifest
if [ "$_SKIP_INTERVIEW" = "false" ] \
   && [ -f doc/common/CLAUDE.md ] \
   && [ -f doc/harness/manifest.yaml ] \
   && grep -qE "^summary:[[:space:]]*\S" doc/common/CLAUDE.md 2>/dev/null; then
  _SKIP_INTERVIEW="true"; _SKIP_REASON="existing summary + manifest (upgrade/rerun)"
fi

# Maintenance-only install
if [ "$_SKIP_INTERVIEW" = "false" ] \
   && ls doc/harness/tasks/TASK__*/MAINTENANCE 2>/dev/null | head -1 | grep -q .; then
  _SKIP_INTERVIEW="true"; _SKIP_REASON="MAINTENANCE marker in task dir"
fi

[ "$_SKIP_INTERVIEW" = "true" ] && echo "Phase 2.0 skipped: $_SKIP_REASON"
```

If any condition matched: skip Phase 2.0. The active/next harness task can re-open the interview later when drift is suspected.

Interview output narrows Q1-Q3 below — check
`doc/harness/.interview-answers.json` before asking each remaining question.
Health scoring is a fixed setup default and is never part of the interview.

### Q1: Project Type Confirmation

Skip if census determined type clearly.

Ask:
> Setting up harness for {project} on {branch}. Detected as {detected_type}. Right?
>
> RECOMMENDATION: Choose detected unless wrong. Completeness 10/10 for A, 7/10 for B-D.
>
> A) {detected_type} (detected)
> B) Web frontend — browser-rendered UI (React/Vue/Next.js/…)
> C) API / backend — server-side only
> D) CLI / library — no server, no UI

### Q2: Key Commands

Skip if build/test commands auto-detected.

Ask:
> I need build and test commands so harness can verify tasks.
> A) Auto-detected: `{build_cmd}` / `{test_cmd}` — looks right
> B) Let me specify

If B: follow up with two free-text asks (build, test) in subsequent turns.

### Q3: QA Strategy

Branch by project type. Check prerequisites before asking. If all met, auto-enable and inform.

**Web frontend (`qa.browser_qa_supported`):** On Codex side, route MCP availability from current session tools or Codex/global runtime config. Keep project `.mcp.json` as user-owned configuration. Default to `qa.browser_qa_supported: false` in manifest unless browser tools are already available and the user explicitly wants browser QA.

**Desktop / native GUI (desktop_qa_supported):** Same config ownership on Codex side: setup reports missing desktop MCP tools and preserves project `.mcp.json` as user-owned configuration. Default to `desktop_qa_supported: false` unless the required desktop tools are already available and the user explicitly wants desktop QA.

**API project:** Ask `A) Enable API QA (recommended). B) Skip — tests only`. curl/httpie assumed present.

**CLI/library:** Ask `A) Enable CLI QA (recommended). B) Skip — tests only`.

**Fullstack (frontend + API):** Enable qa-api for backend by default. Enable browser QA only when current session tools or Codex/global runtime config already provide the browser MCP surface and the user explicitly wants browser QA.

### Health scoring default (never ask)

Always enable health scoring from the census-detected test and quality
commands. For a non-Git control workspace, keep each command executable from
the control root (for example `cd pay-api && ./gradlew test` and
`cd pay-webapp && npm test`) and give each component a root-qualified name.
Only use source roots accepted by the census safe-name rule, render them with
`shlex.quote("./" + root)` so leading-dash names cannot become `cd` options,
and serialize each complete command as a quoted YAML scalar.
Do not ask whether to enable health scoring.

## Phase 2.5: Health Stack Auto-Detection

Run detection after the project interview, before bootstrap. Prefer the exact
test commands already found by the census, including every registered API and
frontend source root. Supplement those commands with the quality-tool signals
below. Do not write a fresh manifest here. Stage the result in
`_PENDING_HEALTH_COMPONENTS`; apply it only after Phase 3 creates the canonical
manifest. On repair/upgrade, preserve an existing non-empty
`health_components` list. Treat `health_components: []` as an old disabled
default and replace it with the detected/default components.

```bash
# Idempotent check: preserve an existing configured list, but migrate [].
_MANIFEST="$_ROOT/doc/harness/manifest.yaml"
if [ -f "$_MANIFEST" ] \
   && grep -q "^health_components:" "$_MANIFEST" 2>/dev/null \
   && ! grep -qE "^health_components:[[:space:]]*\[\][[:space:]]*$" "$_MANIFEST" 2>/dev/null; then
  echo "health_components already configured — preserving"
else
  # 9-signal scan
  _DETECTED=""
  [ -f tsconfig.json ] && _DETECTED="$_DETECTED typecheck:npx tsc --noEmit"
  [ -f biome.json ] || [ -f biome.jsonc ] && _DETECTED="$_DETECTED lint:npx biome check ."
  ls eslint.config.* 2>/dev/null | grep -q . && _DETECTED="$_DETECTED lint:npx eslint ."
  if [ -f pyproject.toml ]; then
    grep -q "pytest" pyproject.toml 2>/dev/null && _DETECTED="$_DETECTED test:pytest"
    grep -q "ruff" pyproject.toml 2>/dev/null && _DETECTED="$_DETECTED lint:ruff check ."
  fi
  if [ -f package.json ]; then
    grep -q '"test"' package.json 2>/dev/null && _DETECTED="$_DETECTED test:npm test"
    grep -q '"knip"' package.json 2>/dev/null && _DETECTED="$_DETECTED deadcode:npx knip"
  fi
  [ -f Cargo.toml ] && _DETECTED="$_DETECTED test:cargo test"
  [ -f go.mod ] && _DETECTED="$_DETECTED test:go test ./..."
  command -v shellcheck >/dev/null 2>&1 && ls *.sh 2>/dev/null | grep -q . && _DETECTED="$_DETECTED shell:shellcheck *.sh"
  echo "Detected health components: $_DETECTED"
fi
```

Stage every census test command and detected signal automatically. Never ask for confirmation.
If the signal scan is empty but `test_command` exists, stage
one `test` component wrapping that exact command. For multi-root workspaces,
stage one root-qualified component per detected source test command. Only when
no executable verification command exists may setup stage
`health_components: []`, and it must report that Health scoring could not be
enabled rather than asking a policy question. Never create a partial manifest
during this phase.

## Phase 3: Bootstrap Core Structure

See `bootstrap.md` — directory creation, manifest.yaml (with smart-defaults table and setup-time MCP reporting), AGENTS.md (or CLAUDE.md fallback via `project_doc_fallback_filenames` in `~/.codex/config.toml`), critic playbooks, doc/harness/ directory + gitignore, non-destructive contracts installation (CONTRACTS.md + CONTRACTS.local.md + @import line + lint check). On Codex, project setup preserves project-root `.mcp.json` as user-owned configuration.

## Phase 3.5: Apply staged configuration

After the canonical manifest exists, apply staged interview fields and
`_PENDING_HEALTH_COMPONENTS` with targeted edits. Do not bulk-rewrite an
existing manifest. Then run the adjacent `verify-report.md` preparation
command (`setup_finalize.py --prepare`) before QA infrastructure checks.

Codex-specific bootstrap steps (delta from Claude):
- Emit `[mcp_servers.harness]` block into `~/.codex/config.toml` via `plugin-codex/config.toml.example` (additive merge with timestamped backup).
- Emit `[hooks]` + `[hooks.state.<key>].trusted_hash` table (AC-006 emitter). Without trust state, Codex refuses to fire registered hooks.
- AGENTS.md is preferred over CLAUDE.md on Codex; the routing block goes into AGENTS.md.

## Phase 4: Verify & Report

See `verify-report.md` — file existence checks, QA infrastructure verification, completion report with action-required branches (Codex CLI version pin mismatch, missing manifest fields, hook trust state not registered) and optional smoke test.

Codex-specific verifications:
- `codex --version` against `plugin-codex/.codex-version` (minimum 0.130.0).
- `codex mcp test harness` returns "server reachable".
- The installed public `skills/run/SKILL.md` exists, its
  `agents/openai.yaml` enables implicit invocation, and AGENTS.md routes
  repository mutations to `$harness:run`.
- (Best effort) Trigger a benign `codex exec` to fire PreToolUse + Stop hooks; verify no `gate-crash` entries in `learnings.jsonl`.

---

## Operational Self-Improvement

Before completing, log genuine operational discoveries (would save 5+ min in future):
```bash
mkdir -p doc/harness
echo '{"skill":"setup","runtime":"codex","type":"operational","key":"SHORT_KEY","insight":"DESCRIPTION","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> doc/harness/learnings.jsonl
```

---
