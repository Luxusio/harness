# Runtime capability matrix — Claude Code vs Codex CLI

AC-009 deliverable. Single source of truth for which harness features work on which runtime, with the rationale anchoring each row.

Read this BEFORE adopting harness on Codex. If you're a Claude Code user already running v1, this also tells you what's about to ship if you opt-in to `harness.codex_enabled: true`.

**Linked from:** root `README.md` "Runtime support" section, `README.codex.md` install banner, `plugin-codex/README.md` capability caveats.

---

## Feature support matrix

Legend:
- ✅ Full — works identically on both runtimes
- 🟡 Partial — works with caveats / reduced fidelity
- 🚧 v2 — reachable but deferred
- ❌ Runtime-bound — fundamentally Claude- or Codex-specific in v1

| Feature | Claude Code | Codex CLI | Rationale / evidence |
|---|---|---|---|
| **MCP server (`plugin/mcp/harness_server.py`)** | ✅ | ✅ | Standard MCP wire protocol; both consume same server. Codex: `~/.codex/config.toml [mcp_servers.harness]`. Claude: `settings.json mcpServers.harness`. Negotiates protocol 2025-11-25 / 2025-06-18 (`harness_server.py:20`). |
| **Python scripts (`plugin/scripts/*.py`)** | ✅ | ✅ | Stdlib-only, env-var-driven via `HARNESS_PLUGIN_ROOT` (AC-006 rename, dual-name fallback during deprecation). |
| **Hook event names** | ✅ | ✅ | Identical: `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, `UserPromptSubmit`, `PermissionRequest`. Plus Codex-only: `PostCompact`. See `doc/harness/codex-payload-deltas.md`. |
| **`hooks.json` schema** | ✅ | ✅ | Codex's `ClaudeHooksEngine` is an explicit port (`codex-rs/hooks/src/engine/mod.rs:98`). `{ hooks: { Event: [{ matcher, hooks: [{ type, command }] }] } }` byte-identical. Figma plugin confirms. |
| **Hook payload keys (input)** | ✅ | ✅ | Snake_case in both. See payload deltas doc for full schema. |
| **Hook output JSON (`hookSpecificOutput`, `permissionDecision`)** | ✅ | 🟡 | `deny` clean on both. Codex `allow`/`ask` parser caveats per CODEX_REVIEW finding 6 — needs golden-replay test. |
| **Hook trust / activation** | ✅ implicit-on-install | 🟡 explicit | Codex requires per-hook `[hooks.state.<key>].trusted_hash` table; setup skill (AC-005 expanded) emits this. |
| **Plugin manifest** | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` | Same shape (Figma plugin confirms keys `name`/`version`/`description`/`skills`/`apps`/`interface`). One-rename diff. |
| **Skill loading** | ✅ `plugin/skills/<name>/SKILL.md` | ✅ `<plugin>/skills/<name>/SKILL.md` | Same convention; per-skill content portability varies (next section). |
| **Slash command invocation** | `/<plugin>:<skill>` | `$<plugin>:<skill>` or `/skills` | Cosmetic difference; README documents both forms. |
| **`Read`/`Edit`/`Write`/`Bash` tool names in skill prose** | native | 🟡 rewrite | Codex: `read_file`/`apply_patch`/`apply_patch`/`shell`. Sync engine rewrites code-block identifiers (AC-005). Prose text mentioning the tools also gets transformed. |
| **`apply_patch` vs `Edit` semantics** | `Edit` operation-oriented | `apply_patch` envelope-oriented | 13-pattern matrix in `doc/harness/apply-patch-matrix.md`. Patterns 1-4 direct; 7-12 caveats; 13 no-port. |
| **`mcp__server__tool` prefix** | native | bare | Codex strips the prefix; tools exposed by short name (e.g. `task_start`). Sync engine rewrites all `mcp__harness__X` → `X` in skill bodies. |
| **AskUserQuestion** | ✅ native | ❌ Claude-only in v1 | 195 call sites in harness skills. Codex has no equivalent native primitive. v2 may add a Codex-native prompt mechanism per [codex skills doc]. |
| **`Agent(subagent_type=...)` fan-out** | ✅ native | ❌ Claude-only in v1 | Control-flow primitive; Codex has `multi_agent` flag but spawn-pattern differs. Sequential degraded variant of develop/plan possible in v2. |
| **`Skill(...)` chaining** | ✅ native | ❌ Claude-only in v1 | Codex skills invoked via `$<name>` or `/skills`; no programmatic chaining. Sync engine flags these call sites for human review on transform. |
| **Setup skill** | ✅ | 🟡 partial | `setup` ports as one of the AC-003 3-skill spike. ~80% portable (mostly prose); AskUserQuestion sites lose interactivity on Codex. |
| **Maintain skill** | ✅ | 🟡 partial | `maintain` ~70% portable; depends on AC-003 outcome. |
| **Run skill (orchestrator)** | ✅ native (Skill chaining) | 🟡 sequential degraded | Codex lacks `Skill()` primitive; v1 ports the public task-start→plan→develop→QA→close lifecycle sequentially, with review and verification retained as internal close gates. |
| **Develop skill** | ✅ native (Agent fan-out) | 🟡 sequential degraded | qa-* parallelization is Claude-only; Codex variant runs single QA lens at a time. |
| **Plan skill (premise gate, scope confirmation)** | ✅ native (AskUserQuestion) | 🟡 sequential degraded | Premise gate uses AskUserQuestion; Codex variant prints premises and reads bare stdin OR uses Codex's `permissionDecision` plumbing. AC-003 spike validates. |
| **Plan-* review skills (CEO/Eng/Design/DevEx, 4014L total)** | ✅ native (dual-voice Agent) | ❌ Claude-only in v1 | Each is structurally a dual-voice review. 72% of skill mass. Single-voice degraded variant is structurally different code, not a port. v2 decides whether to write Codex variants. |
| **qa-cli agent** | ✅ | ✅ (v2 port) | Text-only agent, no MCP coupling beyond shell. Portable. v2. |
| **qa-api agent** | ✅ | ✅ (v2 port) | Same as qa-cli. Portable. v2. |
| **qa-desktop agent** | ✅ | 🚧 v2 | Codex's MCP can register desktop-driving servers (xdotool / AT-SPI MCPs); not v1 priority. |
| **qa-browser agent** | ✅ | 🚧 v2 | NOT Claude-only-by-capability as initially modeled. Codex CAN register `chrome-devtools`/`playwright`/`puppeteer` MCP servers (CODEX_REVIEW finding 5; figma plugin precedent). v1 deferred only because (a) agent prompt hard-codes 14 `mcp__chrome-devtools__*` tool names that need re-templating, (b) browser MCP installs differ per runtime, (c) AC-003 spike doesn't cover. v2 candidate. |
| **dogfooder agent** | ✅ | ✅ (v2 port) | Text-only, AC-003 spike candidate for v2. |
| **stop-judge compatibility path** | removed | removed | Compatibility window closed; the stub agent file is deleted from both trees. Qualified blockers use direct `task_blocked`. |
| **developer agent** | ✅ | ✅ (v2 port) | Text-only, portable. |
| **Subprocess fan-out (`codex exec` spawned for parallel qa-* / dogfooder)** | N/A (Agent-based) | 🚧 v2 | v1 ships sequential Codex executor only. v2 blocker: hook-lifecycle collision with codex exec duration vs `hooks.json:50` timeout=10s. Orphan-PID concern downgraded — `codex exec --json` streaming + process-group ownership manages this (CODEX_REVIEW finding 3). |
| **`HARNESS_PLUGIN_ROOT` env var** | ✅ (AC-006) | ✅ (AC-006) | Renamed from `CLAUDE_PLUGIN_ROOT` with one-version overlap. Both names readable in `_lib.plugin_root_env()` during deprecation window. Sunset version pinned in `CHANGELOG.md`. |
| **Structured gate-crash logging** | ✅ (AC-007) | ✅ (AC-007) | Same JSON shape on both: `{type:"gate-crash", script, tool_name, error, payload_keys}` in `learnings.jsonl`. Codex hook output `permissionDecision` writes match Claude. |
| **Codex CLI version pin** | N/A | ✅ (AC-008) | `plugin-codex/.codex-version` minimum version; setup refuses registration if installed Codex < pin. Bounds the test matrix. |
| **Opt-in / opt-out** | always-on | ✅ (AC-010) opt-in | Manifest flag `harness.codex_enabled: false` default. `plugin-codex/` materializes only when true. Prevents surprise for existing Claude users on `claude plugin update`. |
| **`AGENTS.md` import / `project_doc_fallback_filenames`** | N/A | 🟡 user-config | Codex supports `project_doc_fallback_filenames = ["CLAUDE.md"]` in `~/.codex/config.toml`. User-opt-in bridge during migration. Documented in `README.codex.md`. |
| **Cross-runtime task handoff (Claude start, Codex resume)** | N/A | 🚧 v3 | Current runtimes share atomic `TASK.json` publication and receipt locking, but concurrent orchestration policy remains single-active-session. v3 may support concurrent ownership. |

---

## Per-skill portability (from AC-003 spike target list)

| Skill | LOC | Est. portability | v1 status | Notes |
|---|---|---|---|---|
| setup | 469 | ~80% | AC-003 target | Mostly prose; AskUserQuestion sites lose interactivity on Codex. |
| maintain | 123 | ~70% | AC-003 candidate | Simple flow. |
| run | 171 | ~50% | AC-003 target | Skill() chaining → sequential rewrite. |
| plan | 298 | ~40% | AC-003 target | AskUserQuestion premise gate is the load-bearing differentiator. |
| develop | 500 | ~40% | not v1 | Heavy Agent fan-out for parallel qa-* + dogfooder. |
| plan-ceo-review | 1293 | ~15% | not v1 | Dual-voice review = Claude-only structurally. |
| plan-eng-review | 846 | ~15% | not v1 | Same. |
| plan-design-review | 853 | ~15% | not v1 | Same. |
| plan-devex-review | 1022 | ~15% | not v1 | Same. |

Total skill mass: 5575 LOC. Estimated v1 Codex coverage: setup + maintain + run + plan (AC-003 spike) = 1061 LOC = **19% of skill content**.

The optimistic CODEX_REVIEW finding 1 noted "Codex CLI 0.130.0 has stable plugins/hooks/multi_agent/tool_search/skill support" — that may shift the mechanical-rewrite bucket up and Claude-only down. AC-003 spike measures empirically.

---

## What this matrix is NOT

- **NOT a feature roadmap.** v2 and later capabilities are noted but not committed.
- **NOT a substitute for the spike.** AC-003 measures actual portability; this matrix codifies the architectural decisions, not empirical results.
- **NOT a parity claim.** Codex side intentionally ships a degraded variant for several features. The matrix is honest about what works and what doesn't.

---

## Sources

- `/project/harness-e14968053086/doc/harness/codex-payload-deltas.md` (AC-001)
- `/project/harness-e14968053086/doc/harness/apply-patch-matrix.md` (AC-002)
- `/project/harness-e14968053086/doc/harness/tasks/TASK__dual-runtime-plugin-claude-codex/CODEX_REVIEW.md` (all 7 findings)
- `/project/harness-e14968053086/plugin/CLAUDE.md` (Claude Code harness rules)
- `/project/harness-e14968053086/plugin/mcp/harness_server.py` (MCP wire protocol versions)
- `/tmp/openai-codex-src/codex-rs/hooks/src/engine/mod.rs` (ClaudeHooksEngine + HookEventName)
- `/home/ccc/.codex/.tmp/plugins/plugins/figma/` (Codex plugin layout reference)
