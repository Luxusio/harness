---
name: setup
description: |
  Bootstrap harness in the current repository. Interactive setup with
  project detection, conversational configuration, and core structure
  generation. Use when asked "set up harness", "bootstrap", "initialize
  harness", or on first run in a new project.
user-invocable: true
---

# GENERATED-CANDIDATE — hand-ported v1.5 spike from plugin/skills/setup/SKILL.md (469L source).
# Source is canonical at plugin/skills/setup/SKILL.md. v1.5 AC-005 sync engine will replace this
# hand-port with mechanical emission. Hand-port lives here ONLY to measure the porting friction
# for AC-001 of TASK__dual-runtime-v1.5-spike-and-sync.


> **Codex runtime notes** (delta from Claude Code):
> - This skill calls for "ask the user" interactions at decision points. Codex CLI does not have
>   a structured `AskUserQuestion` primitive — emit the question + options to the user
>   conversationally and read the reply from the next user turn. Honor the same Re-ground / Simplify
>   / Recommend / Options structure in plain prose.
> - `${CLAUDE_PLUGIN_ROOT}` is not injected on Codex. The harness env wires `${HARNESS_PLUGIN_ROOT}`
>   instead (Codex install via `codex plugin marketplace add plugin-codex/` sets it).
> - Browser-QA prerequisite probes (Chrome DevTools MCP) — Codex CAN register Playwright/chrome-
>   devtools MCP servers, but the qa-browser agent prompt is Claude-coupled in v1. Treat
>   `browser_qa_supported: false` as the v1 default on Codex side; v2 will lift this.
>   Setup reads MCP availability from current session tools or Codex/global runtime
>   config. Project-root `.mcp.json` remains user-owned configuration.
> - `Skill()` chaining and `Agent()` fan-out have no Codex equivalent. setup contains zero such
>   call sites today (port was clean), so this caveat is informational only.

## Sub-files

| File | Content |
|------|---------|
| `project-interview.md` | Phase 2.0: 6 forcing questions (office-hours style) |
| `repo-census.md` | Phase 1: project type detection, build/test command detection, summary |
| `bootstrap.md` | Phase 3: directory, manifest.yaml, AGENTS.md, critics, contracts install |
| `verify-report.md` | Phase 4: file verification, QA infra verification, completion report |

Phase 2 (interactive Q1-Q3) stays inline below.

> v1.5 has NOT ported the sub-files yet — only the main SKILL.md is in this spike. Sub-files
> remain at `plugin/skills/setup/<file>.md` and any Codex run that needs them today reads the
> Claude originals. AC-005 sync engine output will regenerate sub-files alongside main SKILL.md.

---

## Context (run first)

```bash
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
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

Both are independent objective questions. Ask them together when neither marker has fired yet. Skip the one that has fired.

**Branching:**

- Both pending (`PROACTIVE_PROMPTED=no` AND `ROUTING_INJECTED=no` AND
  `ROUTING_DECLINED=false` AND `LAKE_INTRO=yes`): ask both in one turn.
- Only one pending: ask that one.
- Neither pending: skip.

**Both-pending conversational ask:**

> Two quick choices to wire harness into this project:
>
> **(Q1) Proactive routing.** harness can proactively figure out when to invoke setup. Recommended: keep on. Reply `A` to keep on, `B` to turn off.
>
> **(Q2) Routing rules in AGENTS.md.** harness works best when AGENTS.md includes ~5 lines of skill-routing rules. Reply `A` to add the rules (recommended), `B` to skip.

After the reply, apply per-question:

- Proactive A/B: `_harness_config_set proactive true|false`, then
  `touch "$_MARKER_DIR/proactive-prompted"`.
- Routing A: emit the idempotent routing block from `bootstrap.md`
  Section 3.4 (marker: `harness:routing-injected`) into AGENTS.md, then
  `touch "$_MARKER_DIR/routing-injected"`. Before injection, run the
  legacy cleanup from §3.4 to strip any stale `Default agent is harness`
  line. The emitted block includes the Durable Decision Documentation Gate:
  user-stated durable product/design/architecture/domain/workflow/implementation
  decisions are not handled until documented under `doc/` or recorded with a
  specific no-doc rationale in the PLAN durable-doc decision.
- Routing B: `_harness_config_set routing_declined true`.

Lake Intro stays a standalone information-only message above — never
bundle it here, it is not a question.

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

If artifacts found: synthesize one-paragraph welcome-back briefing. If manifest exists: "harness is already set up. Manifest shows {project_type} project." → offer repair/upgrade/fresh.

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

Non-destructive detection. See `repo-census.md` (Claude-side at `plugin/skills/setup/repo-census.md` until v1.5 sub-file porting lands) for full detection bash, build/test command sniffing, and summary format.

## Phase 2: Interactive Configuration

### Phase 2.0: Project interview

Read `project-interview.md` and follow in full. Six forcing questions capture WHY before configuring HOW. Answers feed `doc/common/CLAUDE.md summary:`, `manifest.yaml` defaults, and `CONTRACTS.local.md` C-100+.

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

Interview output narrows Q1-Q3 below — check `doc/harness/.interview-answers.json` before asking each remaining question.

**Q1 + Q4 bundling.** Q1 (Project Type Confirmation) and Q4 (Quality Tooling) are independent objective multiple-choice questions, so they can ride one conversational ask. Q2 and Q3 stay solo (Q2 branches into free-text on B; Q3 branches by Q1's answer).

- Both ask-able (Q1 not census-decided AND Q4 not previously answered): ask both in one turn. Apply Q1 first (it gates Q3's branch), then ask Q2 and Q3, then apply Q4.
- Q1 census-decided (detected type is unambiguous): ask Q4 only.
- Q4 already configured: ask Q1 only.
- Both decided: skip Q1 + Q4 entirely.

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

When bundled with Q4 above, include both questions in the same turn; let the user reply with two letters (e.g. "A and A") or two short lines.

### Q2: Key Commands

Skip if build/test commands auto-detected.

Ask:
> I need build and test commands so harness can verify tasks.
> A) Auto-detected: `{build_cmd}` / `{test_cmd}` — looks right
> B) Let me specify

If B: follow up with two free-text asks (build, test) in subsequent turns.

### Q3: QA Strategy

Branch by project type. Check prerequisites before asking. If all met, auto-enable and inform.

**Web frontend (browser_qa_supported):** On Codex side, route MCP availability from current session tools or Codex/global runtime config. Keep project `.mcp.json` as user-owned configuration. Default to `browser_qa_supported: false` in manifest unless browser tools are already available and the user explicitly wants browser QA.

**Desktop / native GUI (desktop_qa_supported):** Same config ownership on Codex side: setup reports missing desktop MCP tools and preserves project `.mcp.json` as user-owned configuration. Default to `desktop_qa_supported: false` unless the required desktop tools are already available and the user explicitly wants desktop QA.

**API project:** Ask `A) Enable API QA (recommended). B) Skip — tests only`. curl/httpie assumed present.

**CLI/library:** Ask `A) Enable CLI QA (recommended). B) Skip — tests only`.

**Fullstack (frontend + API):** Enable qa-api for backend by default. Enable browser QA only when current session tools or Codex/global runtime config already provide the browser MCP surface and the user explicitly wants browser QA.

### Q4: Quality Tooling

Ask:
> harness can track project health (0-10 composite), benchmark perf regressions, and run categorized audits (security, a11y, etc.) across tasks. All optional, configurable later.
>
> RECOMMENDATION: Choose A. Near-zero setup cost — harness auto-detects test_command as the default health component.
> Completeness: A=8/10, B=5/10
>
> A) Enable health scoring (recommended — uses test_command as default, extend with health_components later)
> B) Skip for now — I'll configure manually

If A: manifest gets `health_components` uncommenting the default entry that wraps `test_command`. Benchmark and audit stay commented — user activates by uncommenting and filling in `benchmark_components` / `audit_categories`.

## Phase 2.5: Health Stack Auto-Detection

Run after project interview, before bootstrap. Idempotent: if `health_components:` key is already present in manifest.yaml (content or empty list), skip with log line and proceed.

```bash
# Idempotent check
_MANIFEST="$_ROOT/doc/harness/manifest.yaml"
if [ -f "$_MANIFEST" ] && grep -q "^health_components:" "$_MANIFEST" 2>/dev/null; then
  echo "health_components already set — skipping auto-detect"
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

If signals detected, ask (or auto-accept if `HARNESS_SPAWNED=1`):

> Detected health tooling. Write these to manifest `health_components`?
> RECOMMENDATION: Y — health scoring compounds across tasks with zero extra setup.
> A) Yes, write detected components (recommended)
> B) No, skip

If user accepts (or HARNESS_SPAWNED=1): write `health_components:` block to manifest with one entry per detected signal. If user declines or no signals detected: write `health_components: []` as explicit opt-out marker so this step is skipped on re-run.

## Phase 3: Bootstrap Core Structure

See `bootstrap.md` — directory creation, manifest.yaml (with smart-defaults table and setup-time MCP reporting), AGENTS.md (or CLAUDE.md fallback via `project_doc_fallback_filenames` in `~/.codex/config.toml`), critic playbooks, doc/harness/ directory + gitignore, non-destructive contracts installation (CONTRACTS.md + CONTRACTS.local.md + @import line + lint check). On Codex, project setup preserves project-root `.mcp.json` as user-owned configuration.

Codex-specific bootstrap steps (delta from Claude):
- Emit `[mcp_servers.harness]` block into `~/.codex/config.toml` via `plugin-codex/config.toml.example` (additive merge with timestamped backup).
- Emit `[hooks]` + `[hooks.state.<key>].trusted_hash` table (AC-006 emitter). Without trust state, Codex refuses to fire registered hooks.
- AGENTS.md is preferred over CLAUDE.md on Codex; the routing block goes into AGENTS.md.

## Phase 4: Verify & Report

See `verify-report.md` — file existence checks, QA infrastructure verification, completion report with action-required branches (Codex CLI version pin mismatch, missing manifest fields, hook trust state not registered) and optional smoke test.

Codex-specific verifications:
- `codex --version` against `plugin-codex/.codex-version` (minimum 0.130.0).
- `codex mcp test harness` returns "server reachable".
- (Best effort) Trigger a benign `codex exec` to fire PreToolUse + Stop hooks; verify no `gate-crash` entries in `learnings.jsonl`.

---

## Operational Self-Improvement

Before completing, log genuine operational discoveries (would save 5+ min in future):
```bash
mkdir -p doc/harness
echo '{"skill":"setup","runtime":"codex","type":"operational","key":"SHORT_KEY","insight":"DESCRIPTION","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> doc/harness/learnings.jsonl
```

---
