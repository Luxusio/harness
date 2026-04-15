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

## Sub-files

| File | Content |
|------|---------|
| `project-interview.md` | Phase 2.0: 6 forcing questions (office-hours style) |
| `repo-census.md` | Phase 1: project type detection, build/test command detection, summary |
| `bootstrap.md` | Phase 3: directory, manifest.yaml, CLAUDE.md, critics, contracts install |
| `verify-report.md` | Phase 4: file verification, QA infra verification, completion report |

Phase 2 (interactive Q1-Q3) stays inline below.

---

## Context (run first)

```bash
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
echo "harness2 setup | PROJECT: $_PROJECT | BRANCH: $_BRANCH"

[ -f doc/harness/manifest.yaml ] && echo "EXISTING_SETUP: yes" && head -20 doc/harness/manifest.yaml || echo "EXISTING_SETUP: no"
[ -f CLAUDE.md ] && echo "HAS_CLAUDE_MD: yes" || echo "HAS_CLAUDE_MD: no"
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

# Session timeline
_TIMELINE="$_ROOT/doc/harness/timeline.jsonl"
_SESSION_ID="$$-$(date +%s)"
_TEL_START=$(date +%s)
echo '{"skill":"setup","event":"started","branch":"'"$_BRANCH"'","session":"'"$_SESSION_ID"'","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> "$_TIMELINE" 2>/dev/null || true

# Repo mode
_CONTRIBUTORS=$(git log --oneline --format='%ae' 2>/dev/null | sort -u | wc -l | tr -d ' ')
[ "$_CONTRIBUTORS" -le 1 ] 2>/dev/null && _REPO_MODE="solo" || _REPO_MODE="collaborative"

# Version check
_HARNESS_VERSION="2.0.0"
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

If `SPAWNED_SESSION=true`: no AskUserQuestion (auto-choose recommended); no onboarding flows; focus on completing setup and reporting via prose; end with completion report.

### Upgrade path

`UPGRADE_AVAILABLE` shows version transition → AskUserQuestion: A) Upgrade now, B) Remind later, C) Skip version.

### Existing setup

`EXISTING_SETUP: yes` → AskUserQuestion:
- A) Repair — re-run, fix missing pieces
- B) Upgrade — update to latest conventions
- C) Fresh start — wipe and re-bootstrap

A/B: skip to Phase 3 preserving existing manifest values; first re-run Phase 4.2 QA infra checks against existing manifest. Repair matrix:

| Issue | Auto-fix? | Action |
|-------|-----------|--------|
| Chrome DevTools MCP missing from .mcp.json | Yes | Append to .mcp.json |
| dev_command missing from manifest | Yes | Detect from package.json, add |
| entry_url missing from manifest | Yes | Default from framework port table |
| Browser binary not installed | No | "Install Chromium — apt/brew" |
| Test command wrong in manifest | Yes | Re-detect and update |

C: delete `doc/harness/manifest.yaml` and `doc/harness/`, run full setup.

---

## Onboarding Flows (only when SPAWNED_SESSION=false)

### Lake Intro (once per project)

If `LAKE_INTRO=no`:
> harness2 follows the **Boil the Lake** principle — always do the complete thing when AI makes the marginal cost near-zero.

Then: `touch "$_MARKER_DIR/lake-intro-seen"`.

### Proactive Toggle (once)

If `PROACTIVE_PROMPTED=no` AND `LAKE_INTRO=yes`:
```
AskUserQuestion:
  "harness2 can proactively figure out when to invoke setup. Recommended: keep on."
  A) Keep proactive on → _harness_config_set proactive true
  B) Turn off → _harness_config_set proactive false
```
Then: `touch "$_MARKER_DIR/proactive-prompted"`.

### Routing Injection (once)

If `ROUTING_INJECTED=no` AND `ROUTING_DECLINED=false` AND `PROACTIVE_PROMPTED=yes`:
```
AskUserQuestion:
  "harness2 works best when CLAUDE.md includes skill routing rules. ~5 lines."
  A) Add routing rules to CLAUDE.md (recommended)
  B) No thanks
```
A → append routing section to CLAUDE.md; `touch "$_MARKER_DIR/routing-injected"`. B → `_harness_config_set routing_declined true`.

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
if [ -f "$_ROOT/doc/harness/timeline.jsonl" ]; then
  grep '"event":"completed"' "$_ROOT/doc/harness/timeline.jsonl" 2>/dev/null | tail -1
fi
```

If artifacts found: synthesize one-paragraph welcome-back briefing. If manifest exists: "harness2 is already set up. Manifest shows {project_type} project." → offer repair/upgrade/fresh.

## Prior Learnings

```bash
_LEARN_FILE="$_ROOT/doc/harness/learnings.jsonl"
[ -f "$_LEARN_FILE" ] && tail -5 "$_LEARN_FILE" && echo "TOTAL: $(wc -l < "$_LEARN_FILE" | tr -d ' ')"
```

When detection matches a prior learning, surface:
> **Prior learning applied: {key} (confidence {N}/10, from {date})**

Compounding visibility. No file → proceed silently.

---

## AskUserQuestion Format

1. **Re-ground** — project name, branch, step (1 sentence).
2. **Simplify** — plain language, no jargon.
3. **Recommend** — `RECOMMENDATION: Choose [X] because [reason]`. Include `Completeness: X/10` per option (10=complete, 7=happy path, 3=shortcut). When effort-heavy, show `(human: ~X / CC: ~Y)`.
4. **Options** — lettered with clear descriptions.

Assume the user hasn't looked at this window in 20 min. If you'd need to read source to understand your own question, it's too complex.

## Completeness — Boil the Lake

AI makes completeness near-free. Always recommend complete setup over shortcuts. A "lake" (full config, critic playbooks, gitignore, CLAUDE.md) is boilable; an "ocean" (full codebase migration) is not.

| Task type     | Human team | CC+harness2 | Compression |
|---------------|-----------|-------------|-------------|
| Boilerplate   | 2 days    | 15 min      | ~100× |
| Tests setup   | 1 day     | 15 min      | ~50× |
| Feature impl  | 1 week    | 30 min      | ~30× |
| Bug fix       | 4 hours   | 15 min      | ~20× |

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

Non-destructive detection. See `repo-census.md` for full detection bash, build/test command sniffing, and summary format.

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

If any condition matched: skip Phase 2.0. The maintain skill can re-open
the interview later when drift is suspected.

Interview output narrows Q1-Q3 below — check `doc/harness/.interview-answers.json` before asking each remaining question.

### Q1: Project Type Confirmation

Skip if census determined type clearly.
```
AskUserQuestion:
  "Setting up harness2 for {project} on {branch}. Detected as {detected_type}. Right?"
  RECOMMENDATION: Choose detected unless wrong.
  A) {detected_type} (detected)
  B) Web frontend — browser-rendered UI (React/Vue/Next.js/…)
  C) API / backend — server-side only
  D) CLI / library — no server, no UI
```

### Q2: Key Commands

Skip if build/test commands auto-detected.
```
AskUserQuestion:
  "I need build and test commands so harness2 can verify tasks."
  A) Auto-detected: `{build_cmd}` / `{test_cmd}` — looks right
  B) Let me specify
```
If B: follow up with two free-text asks (build, test).

### Q3: QA Strategy

Branch by project type. Check prerequisites before asking. If all met, auto-enable and inform.

**Web frontend (browser_qa_supported):** Show prereq check (Browser / Chrome DevTools MCP / Dev command). If all met:
```
A) Enable browser QA (recommended)
B) Skip — tests only
```

If browser missing: `A) Install and come back | B) Skip | C) Install now via apt/brew`.
If Chrome MCP missing: `A) Add to .mcp.json (recommended) | B) Already configured globally | C) Skip`.
If dev_command missing: `A) Specify command | B) Skip | C) Auto-detect later`.

**API project:** `A) Enable API QA (recommended) | B) Skip — tests only`. curl assumed present.

**CLI/library:** `A) Enable CLI QA (recommended) | B) Skip — tests only`.

**Fullstack (frontend + API):** `A) Both browser + API QA (recommended) | B) Browser only | C) API only | D) Skip`.

## Phase 3: Bootstrap Core Structure

See `bootstrap.md` — directory creation, manifest.yaml (with smart-defaults table and MCP config), CLAUDE.md, critic playbooks, doc/harness/ directory + gitignore, non-destructive contracts installation (CONTRACTS.md + CONTRACTS.local.md + @import line + lint check).

## Phase 4: Verify & Report

See `verify-report.md` — file existence checks, QA infrastructure verification, completion report with action-required branches (MCP restart, browser install, missing manifest fields) and optional smoke test.

---

## Plan Mode Safe Operations

Always allowed in plan mode: detection bash (read-only), writes to `doc/harness/` (local.yaml, .markers/, timeline.jsonl), AskUserQuestion.

**PLAN MODE EXCEPTION — always run:** timeline logging, marker file creation.

## Operational Self-Improvement

Before completing, log genuine operational discoveries (would save 5+ min in future):
```bash
mkdir -p doc/harness
echo '{"skill":"setup","type":"operational","key":"SHORT_KEY","insight":"DESCRIPTION","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> doc/harness/learnings.jsonl
```

## Telemetry (always runs, including plan mode)

```bash
_TEL_END=$(date +%s)
_TEL_DUR=$(( _TEL_END - _TEL_START ))
echo '{"skill":"setup","event":"completed","branch":"'"$_BRANCH"'","outcome":"OUTCOME","duration_s":"'"$_TEL_DUR"'","session":"'"$_SESSION_ID"'","ts":"'$(date -u +%Y-%m-%dT%H:%M:%SZ)'"}' >> "$_TIMELINE" 2>/dev/null || true
```
Replace `OUTCOME` with success/error/abort.
