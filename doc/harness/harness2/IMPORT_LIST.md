# harness2 Skill Import List

tags: [harness2, import, gstack]
status: draft
created: 2026-04-09
task_ref: TASK__harness2-architecture

This document records the import decision for each candidate gstack skill.
Each entry covers: import decision, rationale, and gstack-specific infrastructure to strip.

---

## Import Criteria

A skill is imported if it meets all three:

1. **No browser required** — harness operates with `browser_qa_supported: false`
2. **gstack infra is strippable** — the skill's value is in its logic, not gstack binaries
3. **Real value exchange with harness workflow** — adds capability harness doesn't already have

---

## Decision Table

| Skill | Decision | Value | gstack Infra to Strip |
|-------|----------|-------|----------------------|
| investigate | IMPORT | Core debugging methodology | preamble block, gstack-slug, telemetry, timeline-log, update-check, PROACTIVE/SKILL_PREFIX checks, YC lake intro, routing setup |
| health | IMPORT | Code quality dashboard harness lacks | same preamble block pattern as all skills |
| review | IMPORT | PR pre-landing review | same preamble block; gstack-repo-mode call |
| checkpoint | IMPORT | Session state save/restore across context breaks | same preamble block pattern |
| learn | IMPORT | Session learnings management | same preamble block; gstack-learnings-search binary calls |
| retro | IMPORT | Weekly engineering retrospective | same preamble block; team contribution breakdown usable without gstack |
| document-release | SKIP | Covered by harness DOC_SYNC + writer agent | redundant with existing harness doc infrastructure |
| office-hours | SKIP | YC-structured product ideation, gstack-specific framing | entire skill is gstack-flavored (YC plea, startup mode framing) |

---

## Per-Skill Detail

### investigate — IMPORT

**Source description:** Systematic debugging with root cause investigation. Four phases: investigate, analyze, hypothesize, implement. Iron Law: no fixes without root cause.

**Harness value:** Provides a structured debugging methodology. harness has no equivalent systematic investigation workflow. Activated by auto-routing on error patterns.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block — includes `gstack-update-check`, `gstack-sessions`, `gstack-config get proactive`, `gstack-repo-mode`, `gstack-slug`, `gstack-learnings-search`, `gstack-timeline-log`, telemetry analytics writes
- `PROACTIVE` flag check and conditional behavior
- `SKILL_PREFIX` flag check
- `UPGRADE_AVAILABLE` / `JUST_UPGRADED` handler
- `LAKE_INTRO` / Boil the Lake introduction
- `TEL_PROMPTED` telemetry dialog
- `PROACTIVE_PROMPTED` proactive behavior dialog
- `HAS_ROUTING` / `ROUTING_DECLINED` CLAUDE.md routing setup
- `VENDORED_GSTACK` vendoring deprecation warning

**Retained content:** Phase structure (investigate → analyze → hypothesize → implement), Iron Law (no fixes without root cause), freeze/scope boundary hooks (rewrite to harness hook style).

**Adaptation notes:** Replace `(gstack)` tag in description with `(harness2)`. Freeze hook commands reference gstack skill dir path — rewrite to harness skill path.

---

### health — IMPORT

**Source description:** Code quality dashboard. Wraps existing project tools (type checker, linter, test runner, dead code detector, shell linter), computes weighted composite 0-10 score, tracks trends over time.

**Harness value:** harness has no code quality dashboard. health provides a structured quality scan that complements the canonical loop's verify phase.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block (same pattern as all skills)
- All gstack-config, gstack-slug, gstack-telemetry, gstack-timeline-log references
- PROACTIVE/SKILL_PREFIX/LAKE_INTRO/TEL_PROMPTED/PROACTIVE_PROMPTED dialogs
- HAS_ROUTING/ROUTING_DECLINED setup

**Retained content:** Tool detection logic, weighted scoring model, trend tracking via local file, dashboard output format.

**Adaptation notes:** Trend history file path changes from `~/.gstack/projects/SLUG/health-history.jsonl` to harness task state directory or `.harness/health/` local path.

---

### review — IMPORT

**Source description:** Pre-landing PR review. Analyzes diff against base branch for SQL safety, LLM trust boundary violations, conditional side effects, and other structural issues.

**Harness value:** harness's critic-plan and runtime-critic cover task-level verification, but not ad-hoc diff review before merge. review fills that gap.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block
- `gstack-repo-mode` environment setup
- All gstack-config, gstack-slug, gstack-telemetry, gstack-timeline-log calls
- All preamble dialogs (LAKE_INTRO, TEL_PROMPTED, PROACTIVE_PROMPTED, HAS_ROUTING)

**Retained content:** Diff analysis heuristics, SQL safety checks, LLM trust boundary checks, conditional side effect detection, structured review output format.

**Adaptation notes:** `allowed-tools` includes `Agent` — keep. WebSearch — keep. No harness-specific adaptation needed beyond stripping preamble.

---

### checkpoint — IMPORT

**Source description:** Save and resume working state checkpoints. Captures git state, decisions made, and remaining work so you can pick up exactly where you left off.

**Harness value:** harness has no native session continuity mechanism. checkpoint provides save/restore across context breaks, especially valuable in long multi-task sessions.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block
- All gstack-config, gstack-slug, gstack-sessions, gstack-timeline-log calls
- Conductor/workspace handoff references (Conductor is gstack-specific orchestrator)
- All preamble dialogs

**Retained content:** Checkpoint file schema, git state capture, decision log capture, "where was I" resume logic, remaining work enumeration.

**Adaptation notes:** Checkpoint storage path changes from `~/.gstack/projects/SLUG/checkpoints/` to `.harness/checkpoints/` in project root (or harness task state dir). Remove Conductor-specific workspace handoff language.

---

### learn — IMPORT

**Source description:** Manage project learnings. Review, search, prune, and export what the system has learned across sessions.

**Harness value:** harness has no cross-session learning capture. learn provides durable knowledge accumulation that can be retrieved during future sessions.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block
- `gstack-slug` for project identifier
- `gstack-learnings-search` binary — replace with harness-native file search
- All gstack-config, gstack-telemetry, gstack-timeline-log calls
- All preamble dialogs

**Retained content:** Learning capture schema, search/prune/export operations, "didn't we fix this before?" retrieval pattern.

**Adaptation notes:** Storage path changes from `~/.gstack/projects/SLUG/learnings.jsonl` to `.harness/learnings.jsonl` in project root. `gstack-learnings-search` binary replaced with direct grep/jq over the local file. Project slug derived from `git rev-parse --show-toplevel` basename rather than gstack-slug binary.

---

### retro — IMPORT

**Source description:** Weekly engineering retrospective. Analyzes commit history, work patterns, and code quality metrics with persistent history and trend tracking. Team-aware: breaks down per-person contributions.

**Harness value:** harness has no retrospective capability. retro provides periodic engineering health review using only git history and local metrics — no external dependency.

**gstack infra to strip:**
- Entire `## Preamble (run first)` bash block
- All gstack-config, gstack-slug, gstack-telemetry, gstack-timeline-log calls
- All preamble dialogs

**Retained content:** Commit history analysis, per-person contribution breakdown, code quality metric collection, trend history, structured retro output (what shipped, what was hard, patterns to improve).

**Adaptation notes:** Retro history file path changes from `~/.gstack/projects/SLUG/retro-history.jsonl` to `.harness/retro-history.jsonl`. No binary dependencies beyond standard git and shell tools.

---

### document-release — SKIP

**Rationale:** harness already has a complete documentation infrastructure: DOC_SYNC.md, writer agent, and critic-document. document-release duplicates this with a gstack-flavored approach. Importing it would create a redundant parallel path and potential conflict with harness's structured doc loop.

**What to keep instead:** The concept of cross-referencing diff vs. docs is already embedded in harness's writer agent and DOC_SYNC.md format. No import needed.

---

### office-hours — SKIP

**Rationale:** office-hours is deeply gstack-specific in framing. Its two modes (Startup mode with YC-style forcing questions, Builder mode for side projects) are oriented around YC startup methodology. The YC plea, "is this worth building" framing, and plan-ceo-review integration are not relevant to harness's engineering workflow focus.

**What to keep instead:** The brainstorming/ideation use case can be handled by harness's plan-skill with a lightweight ideation mode, without importing gstack's YC framing.

---

## Stripping Template (common to all imported skills)

Every imported SKILL.md has its preamble bash block replaced with a minimal harness-native context block:

```bash
# harness2 context
_BRANCH=$(git branch --show-current 2>/dev/null || echo "unknown")
echo "BRANCH: $_BRANCH"
_PROJECT=$(basename "$(git rev-parse --show-toplevel 2>/dev/null)" 2>/dev/null || echo "unknown")
echo "PROJECT: $_PROJECT"
```

All references to the following are removed:
- `gstack-update-check`
- `gstack-config`
- `gstack-slug`
- `gstack-telemetry-log`
- `gstack-timeline-log`
- `gstack-repo-mode`
- `gstack-learnings-search`
- `~/.gstack/` paths
- `PROACTIVE`, `SKILL_PREFIX`, `LAKE_INTRO`, `TEL_PROMPTED`, `PROACTIVE_PROMPTED`
- `HAS_ROUTING`, `ROUTING_DECLINED`, `VENDORED_GSTACK`
- YC / Boil the Lake references
- `(gstack)` tag in descriptions → replaced with `(harness2)`
- `AUTO-GENERATED from SKILL.md.tmpl` comment → remove
- `Regenerate: bun run gen:skill-docs` comment → remove
