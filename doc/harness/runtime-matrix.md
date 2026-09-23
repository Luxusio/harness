# Runtime capability matrix — Claude Code vs Codex CLI

Current operational summary of which harness features work on each runtime,
with the rationale anchoring each row. Historical v1 spike estimates are not
runtime contracts; the installed skills and agent capability surface govern.

Read this before adopting harness on Codex or comparing its orchestration with
Claude Code.

**Linked from:** root `README.md` "Runtime support" section, `README.codex.md` install banner, `plugin-codex/README.md` capability caveats.

---

## Feature support matrix

Legend:
- ✅ Full — works identically on both runtimes
- 🟡 Partial — works with caveats / reduced fidelity
- 🚧 Deferred — reachable but not currently wired
- ❌ Runtime-bound — fundamentally Claude- or Codex-specific

| Feature | Claude Code | Codex CLI | Rationale / evidence |
|---|---|---|---|
| **MCP server (`plugin/mcp/harness_server.py`)** | ✅ | ✅ | Standard MCP wire protocol; both consume same server. Codex: `~/.codex/config.toml [mcp_servers.harness]`. Claude: `settings.json mcpServers.harness`. Negotiates protocol 2025-11-25 / 2025-06-18 (`harness_server.py:20`). |
| **Python scripts (`plugin/scripts/*.py`)** | ✅ | ✅ | Stdlib-only, env-var-driven via `HARNESS_PLUGIN_ROOT` (AC-006 rename, dual-name fallback during deprecation). |
| **Hook event names** | ✅ | ✅ | Identical schema support: `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, `UserPromptSubmit`, `PermissionRequest`. Plus Codex-only: `PostCompact`. See `doc/harness/codex-payload-deltas.md`. `Stop` is schema-supported on both but neither runtime currently registers a live Stop hook (`stop_gate.py` removed from `plugin/hooks/hooks.json` 2026-09-23; `hook_stop.py` was never wired by `install.py`) — turn-end continuation is native `/goal` (`doc/harness/patterns/auto-loop.md`). |
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
| **Structured user questions** | ✅ native | 🟡 capability-dependent | Codex uses structured input when exposed by the active mode and conversational input otherwise. |
| **Independent-agent fan-out** | ✅ `Agent(subagent_type=...)` | ✅ `spawn_agent` | Syntax differs, but current plan/develop workflows capability-route fresh independent contexts on both runtimes. |
| **`Skill(...)` chaining** | ✅ native | ❌ runtime-bound | Codex invokes public skills directly and the run skill owns its internal workflow instead of calling a `Skill(...)` primitive. |
| **Setup skill** | ✅ | ✅ | A dedicated Codex setup skill installs and configures the runtime-specific payload. |
| **Maintain skill** | ✅ | 🟡 no dedicated Codex skill | Maintenance work still routes through the public run workflow. |
| **Run skill (orchestrator)** | ✅ native | ✅ capability-routed | Both retain the public task-start→plan→develop→QA→close lifecycle, with review and verification as internal close gates. |
| **Develop skill** | ✅ native fan-out | ✅ capability-routed fan-out | Both deterministically select ephemeral LIGHT (0 hunters), STANDARD (1 selected hunter), or DEEP (2 hunters), then always run one fresh full-sweep formal code reviewer. Security remains separately conditional. Depth is not authoritative lifecycle state or a dedicated task/receipt/review-detail field, although it may appear in stored non-authoritative formal-review narrative. |
| **Clean-rebase review proof** | ✅ | ✅ | Rebase-LIGHT requires exact old/new base and tip endpoints, conflict-free one-to-one patch equivalence, affirmative semantic non-overlap, `HEAD` at the new tip, and a clean accounted-for index/worktree. Missing proof rejects LIGHT; evidence loss that may hide conflict, drift, or overlap selects DEEP. |
| **Plan skill (consolidated material-decision gate)** | ✅ native | ✅ capability-routed | Both reuse explicit request/clarification authority and ask once after review only for unresolved material decisions. Codex uses structured input when available and conversational fallback otherwise. |
| **Plan-* review skills (CEO/Eng/Design/DevEx)** | ✅ native | ✅ capability-routed | Codex carries runtime-specific internal skill variants and uses fresh agents when the collaboration surface is available. |
| **qa-cli agent** | ✅ | ✅ | Text-only agent with shell-based verification. |
| **qa-api agent** | ✅ | ✅ | Text-only API verification role. |
| **qa-desktop agent** | ✅ | 🚧 deferred | The role is installed, but useful execution depends on a configured desktop-driving capability. |
| **qa-browser agent** | ✅ | 🚧 deferred | The role is installed, but useful execution depends on a configured browser-driving capability. |
| **dogfooder agent** | ✅ | ✅ | Text-only user-facing-experience pass. |
| **stop-judge compatibility path** | removed | removed | Compatibility window closed; the stub agent file is deleted from both trees. Qualified blockers use direct `task_blocked`. |
| **developer agent** | ✅ | ✅ | Text-only implementation role. |
| **Subprocess fan-out (`codex exec`)** | N/A | not used | Current Codex orchestration uses the native collaboration surface instead of subprocess lifecycle emulation. |
| **`HARNESS_PLUGIN_ROOT` env var** | ✅ (AC-006) | ✅ (AC-006) | Renamed from `CLAUDE_PLUGIN_ROOT` with one-version overlap. Both names readable in `_lib.plugin_root_env()` during deprecation window. Sunset version pinned in `CHANGELOG.md`. |
| **Structured gate-crash logging** | ✅ (AC-007) | ✅ (AC-007) | Same JSON shape on both: `{type:"gate-crash", script, tool_name, error, payload_keys}` in `learnings.jsonl`. Codex hook output `permissionDecision` writes match Claude. |
| **Codex CLI version pin** | N/A | ✅ (AC-008) | `plugin-codex/.codex-version` minimum version; setup refuses registration if installed Codex < pin. Bounds the test matrix. |
| **Opt-in / opt-out** | always-on | ✅ (AC-010) opt-in | Manifest flag `harness.codex_enabled: false` default. `plugin-codex/` materializes only when true. Prevents surprise for existing Claude users on `claude plugin update`. |
| **`AGENTS.md` import / `project_doc_fallback_filenames`** | N/A | 🟡 user-config | Codex supports `project_doc_fallback_filenames = ["CLAUDE.md"]` in `~/.codex/config.toml`. User-opt-in bridge during migration. Documented in `README.codex.md`. |
| **Cross-runtime task handoff (Claude start, Codex resume)** | N/A | 🚧 v3 | Current runtimes share atomic `TASK.json` publication and receipt locking, but concurrent orchestration policy remains single-active-session. v3 may support concurrent ownership. |

---

## Current Codex skill availability

| Skill | Status | Notes |
|---|---|---|
| setup | current | Dedicated Codex installation and configuration path. |
| run | current | Public router plus a Codex-native internal workflow. |
| plan | current | Capability-routed premise and plan review. |
| develop | current | Capability-routed implementation; deterministic LIGHT/STANDARD/DEEP discovery; mandatory fresh full formal review; separate conditional security; QA and dogfood. |
| plan-ceo-review | current | Installed Codex internal variant. |
| plan-eng-review | current | Installed Codex internal variant. |
| plan-design-review | current | Installed Codex internal variant. |
| plan-devex-review | current | Installed Codex internal variant. |

---

## What this matrix is NOT

- **NOT a feature roadmap.** v2 and later capabilities are noted but not committed.
- **NOT a substitute for runtime checks.** Capability availability still depends
  on the installed Codex version and configured MCP/app surfaces.
- **NOT a parity claim.** Runtime-specific syntax and interaction capabilities
  remain even where the workflow outcome is equivalent.

---

## Sources

- `README.codex.md`
- `plugin-codex/README.md`
- `plugin-codex/internal-skills/run/SKILL.md`
- `plugin-codex/internal-skills/develop/SKILL.md`
- `doc/harness/codex-payload-deltas.md`
- `doc/harness/apply-patch-matrix.md`
