---
name: run
description: Orchestrate full development cycle — plan -> develop -> verify -> close.
---

# GENERATED-CANDIDATE — hand-ported v1.5 spike from plugin/skills/run/SKILL.md (171L source).
# Source canonical at plugin/skills/run/SKILL.md. v1.5 AC-005 sync engine will replace this
# hand-port with mechanical emission. Lives here only to measure porting friction for
# AC-002 of TASK__dual-runtime-v1.5-spike-and-sync.


Orchestrate the full harness development cycle for a task.

> **Codex runtime notes** (delta from Claude):
> - Claude's `Skill("harness:plan", task_id)` programmatic chain has no Codex equivalent — on Codex, the orchestrator reads each downstream skill's SKILL.md inline and executes its phases as part of the same conversation. Effect is identical (plan -> develop -> verify -> close), but the chain is sequential prose, not tool calls.
> - Claude's `Agent(subagent_type="oh-my-claudecode:executor", ...)` multi-spawn for QA has no Codex equivalent in v1.5. Codex side runs QA inline (the orchestrator itself does the verification, calls `write_critic_qa` MCP tool, reads the verdict). Parallel multi-lens QA (browser + api in one concurrent batch) is deferred to v2; browser QA itself is not deferred when browser tools are available in the current Codex session.
> - MCP tool names on Codex use bare form (`task_start`, `task_verify`, `task_close`, `write_critic_qa`) — NOT the Claude `mcp__plugin_harness_harness__*` prefix form. Where this skill mentions a prefixed name, read it as the bare form.
> - `${CLAUDE_PLUGIN_ROOT}` is not injected on Codex. Use `${HARNESS_PLUGIN_ROOT}` (set by the Codex plugin install).
> - AskUserQuestion (Phase 4 FAIL retry) is conversational prose on Codex — emit the question + options, read the reply from the next user turn.

## Sub-file

`self-improvement.md` — signal detection, auto-fix, tiered-learning promotion + pruning pipeline (runs after each task close). Not ported in v1.5 spike; the Codex orchestrator reads the Claude-side sub-file at `plugin/skills/run/self-improvement.md` if/when self-improvement runs.

## Voice

Direct, terse. Status updates, not narration. "Phase N done." not "I have completed Phase N."

## Flow

Execute phases in strict order. Each phase must complete before the next begins. On any phase failure: stop, report, ask how to proceed.

### Phase 1: Start task

```
task_start { slug: "<ARGUMENTS>" }
```

(On Codex MCP this is the bare tool name; Claude's prefix-form name is `mcp__plugin_harness_harness__task_start`.) Store the returned `task_dir` and `task_id` for all subsequent phases. Report: task created/resumed, task_dir path.

### Phase 2: Plan

Read `plugin-codex/skills/plan/SKILL.md` (the v1.5 hand-port; AC-003 spike target) and execute its phases inline, passing `task_id`. The plan skill writes PLAN.md to the task_dir. On BLOCKED: stop and report.

On Codex side the plan skill ships in degraded form — single-voice (no Voice A / Voice B Agent fan-out). The premise gate becomes a conversational ask. Plan output is functionally equivalent for v1.5 simple-scope tasks; complex dual-voice review remains Claude-only and the user should run `claude $/harness:plan <task>` for those.

### Phase 3: Develop

Read `plugin-codex/skills/develop/SKILL.md` and execute its phases inline, passing `task_id`. The develop skill on Codex is a hand-port of the Claude source (`plugin/skills/develop/SKILL.md`) under the MCP-only-sharing policy (spike-report §3.6) — same canonical-loop methodology, with `Agent` fan-out collapsed to sequential execution, `Skill()` chains rendered as inline-read sub-skill references, and `AskUserQuestion` gates rendered as conversational prose asks. Phase 0 through Phase 8.7 parity is preserved. Develop writes HANDOFF.md + DOC_SYNC.md to the task_dir. On BLOCKED: stop and report.

Multi-lens parallel QA (qa-browser + qa-api in one batch) is the one piece still deferred to v2. Browser MCP verification is availability-gated: if the current Codex session exposes browser tools (for example `chrome_devtools` or a future Playwright MCP), run the qa-browser methodology inline; if browser verification is required but no browser tool or reachable app exists, write a browser-lens `BLOCKED_ENV` verdict instead of silently falling back to CLI-only QA.

On completion: HANDOFF.md and DOC_SYNC.md exist in task_dir. If BLOCKED: stop, report, ask user.

### Phase 4: Verify (QA — inline on Codex)

Read `doc/harness/manifest.yaml` for project type. On Codex v1.5, run the appropriate QA inline (no Agent fan-out).

**Strategy selection:**
- **qa-browser** — required when `manifest.qa.browser_qa_supported: true` AND the diff contains frontend files (`.tsx/.jsx/.vue/.svelte/.html/.css/.scss` or `/components/`, `/pages/`, `/views/`, `/routes/` path fragments). On Codex, check the actual session tool surface first. If browser tools are available, read `plugin-codex/agents/qa-browser.md` and run that methodology inline, including real page navigation/interactions/screenshots where the tools support it. Call:
  ```
  write_critic_qa {
    task_id: "<task_id>",
    lens: "browser",
    verdict: "PASS" | "FAIL" | "BLOCKED_ENV",
    summary: "<one-line>",
    transcript: "<browser evidence>",
    manual_ux_verification: "<non-empty description of the pages, viewports, and interactions actually checked>"
  }
  ```
  If browser QA is required but no browser tool is available, the dev server cannot be reached, or a required browser setup is impossible, call the same browser lens with `verdict: "BLOCKED_ENV"` and a transcript naming the exact missing condition. Do not downgrade the task to qa-cli only.
- `desktop_qa_supported: true` → also Claude-only in v1.5. Same BLOCKED_ENV path.
- `type: api` or diff contains route/endpoint files → qa-api inline (orchestrator does the API verification using shell/curl).
- `type: cli` or `type: library` → qa-cli inline (orchestrator runs test suite + lint via shell tool).

Order: desktop branch before `type: cli` fallback so a desktop app declared as `type: cli` still routes to qa-desktop (and thus the v1.5 BLOCKED_ENV path).

QA inline pattern on Codex:

```
# Read qa-browser, qa-cli, or qa-api agent prompt from the Codex plugin tree
cat ${HARNESS_PLUGIN_ROOT}/agents/qa-cli.md   # or qa-api.md / qa-browser.md
# Follow the four-roles checklist inline (operation, intent, UX/design, runtime)
# Run the verification commands the agent prompt prescribes
# Call:
write_critic_qa { lens: "cli|api|browser", verdict: "PASS" | "FAIL" | "BLOCKED_ENV", summary, transcript, manual_ux_verification? }
```

After write_critic_qa, check the returned merged `runtime_verdict`:
- **PASS**: proceed to Phase 5.
- **FAIL**: report findings, then ask the user:
  > QA returned FAIL. Findings: <summary>
  > A) Send back to develop — fix the issues
  > B) Override — accept current state (justify in the next reply)
  > C) Abort task

  A → return to Phase 3 with QA findings as additional context. Retry limit: 3 cycles. After 3 FAILs: stop and report.

**Persist QA failure patterns** after each retry cycle:
```bash
_TS=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || echo "unknown")
echo '{"ts":"'"$_TS"'","type":"qa-failure-pattern","source":"run-retry","runtime":"codex","key":"FAILURE_TYPE","insight":"QA failed: <reason>, workaround: <fix>","task":"'"<task_id>"'"}' >> doc/harness/learnings.jsonl 2>/dev/null || true
```

### Phase 4.5: Health score snapshot

Before closing, capture the final project health score:

```bash
python3 ${HARNESS_PLUGIN_ROOT}/scripts/health.py 2>&1 || true
```

Store the printed score for inclusion in the completion report. The script auto-appends to `doc/harness/health-history.jsonl`.

### Phase 5: Close

```
task_close { task_id: "<task_id>" }
```

If blocked: report `missing_for_close`, fix the stated gate, retry.
If success: emit completion report, then run self-improvement pipeline (see `self-improvement.md` in the Claude tree).

## Completion Report

```
DONE

Task:    <task_id>
Status:  closed
Dir:     <task_dir>
Runtime: codex

Phases completed: plan, develop, verify, close
Runtime verdict:  PASS
Health score:     <score>/10
Files changed:    <count>
Doc:              doc/changes/<date>-<slug>.md
```

## Retry Tracking

Phase 3 (develop): max 3 retries after runtime FAIL. After max: stop, emit DONE_WITH_CONCERNS.

## Error Handling

On any phase error or MCP timeout:
1. Report what happened
2. Check state via `task_context`
3. Ask user: retry / skip / abort

Never silently continue past a failure.

## Self-Improvement (post-close)

After every task close, run the pipeline in `self-improvement.md` (Claude tree):
- Detect friction signals (wrong verify strategy, stale manifest, repeated failures, new project patterns)
- Log harness-improvement entries to `learnings.jsonl`
- Auto-fix safe manifest updates (reported to user before write)
- Promote learnings: Tier 3 (jsonl) -> Tier 2 (patterns/*.md) -> Tier 1 (CLAUDE.md or AGENTS.md)
- Prune promoted entries and stale (>90 day) non-eureka entries

Pipeline is housekeeping, not a gate. On failure: log warning and continue.

---

# v1.5 spike measurements (this port — captured during hand-port for spike-report.md)

| Category | Source lines | Result | % |
|---|---|---|---|
| As-is portable (voice rules, completion report shape, retry/error narration, health snapshot, self-improvement bullets, telemetry) | ~70 | reused verbatim | 41% |
| Trivial rewrite (`${CLAUDE_PLUGIN_ROOT}` -> `${HARNESS_PLUGIN_ROOT}`, MCP tool name de-prefix, frontmatter prune from 7 to 3 lines, JSONL `runtime` field, AGENTS.md alongside CLAUDE.md) | ~25 | line-for-line rewrite | 15% |
| Significant restructure (Phase 2 Skill() -> "read sub-skill SKILL.md and execute inline"; Phase 3 develop gap-surface; Phase 4 Agent() -> "inline on Codex"; QA strategy reduced to qa-cli/qa-api only) | ~50 | semantically equivalent but structurally different | 29% |
| Dropped (Phase 4 multi-lens parallel Agent spawn block + MCP-reload note; Phase 4 chrome-devtools/desktop QA branches collapsed to BLOCKED_ENV) | ~25 | not represented or replaced with v1.5 gap-prose | 15% |
| Codex-additive (runtime notes header, sequential-degraded develop fallback prose, "inline on Codex" QA pattern, BLOCKED_ENV gap prose for browser/desktop) | ~35 | new content | 20% |
| Total source | 171 | ~200 emitted | — |

Key port observations:
- **3 `Skill()` call sites** are the load-bearing porting friction. Codex has no Skill() tool. Resolution: prose direction "read and execute inline" — works but expands wordcount. The model's natural read+follow capability covers the gap.
- **1 `Agent()` multi-spawn block** at Phase 4 (QA fan-out) — explicitly degraded to inline on Codex. Acceptable for v1.5 because qa-cli/qa-api are text-only roles the model can fulfill itself.
- **Frontmatter `allowed-tools` list** included `Agent` + `Skill` — both nonexistent on Codex. Stripped entirely (Codex ignores allowed-tools).
- **MCP tool names** changed from `mcp__harness__task_*` to bare `task_*`. Could be automated by a regex pass in the sync engine.
- **Sequential degradation** of multi-lens QA: cost is wall-clock (no parallelism) and structural (one verdict at a time). Functional but slower.
- **Develop phase is Claude-only in v1.5** — surfaced as a gap, not silently dropped.

Conclusion for AC-004: run port is **56% reuse (as-is + trivial), 29% restructure, 15% drop**. Higher restructure% than setup because of Skill()/Agent() control-flow primitives. Argues for **YAML/JSON intermediate** canonical form with declarative `chain:` and `qa_lenses:` fields that the sync engine renders as Claude-Skill-calls vs Codex-inline-prose. Pure AST text substitution would NOT handle this cleanly (Voice B of Phase 3 v1 Eng review was right).
