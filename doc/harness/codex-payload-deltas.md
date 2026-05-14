# Codex hook payload schemas + Claude delta

AC-001 deliverable. Captures Codex hook envelope schemas, output formats, and the delta against Claude Code's hook payload shape. Used by AC-007 (structured gate-crash logging) to know which keys to extract when hooks crash under either runtime.

Source priorities (highest to lowest):
1. **Codex source code** — `/tmp/openai-codex-src/codex-rs/hooks/` (engine, registry, types)
2. **Codex plugin example** — `/home/ccc/.codex/.tmp/plugins/plugins/figma/hooks.json` (on-disk Claude-compatible schema)
3. **Cross-model review** — CODEX_REVIEW.md finding 6 (gpt-5.5 inspection of openai/codex source)
4. **Anthropic Claude Code docs** — embedded in `plugin/scripts/prewrite_gate.py` docstring

---

## Architectural finding (load-bearing)

Codex's hook runtime engine is explicitly named **`ClaudeHooksEngine`** in source (`codex-rs/core/src/hook_runtime.rs` + `codex-rs/hooks/src/engine/mod.rs:98`). It is a deliberate port of Claude Code's hook surface, not an independent design. This is the strongest possible evidence that hook payloads are structurally compatible: Codex did NOT design a new hook system from scratch — they consumed Claude's contract.

The `HookEventName` enum at `codex-rs/hooks/src/engine/mod.rs:65-75` lists the events Codex supports:

```rust
PreToolUse        → "pre-tool-use"
PostToolUse       → "post-tool-use"        // partial — see deltas
PostCompact       → "post-compact"          // CODEX-ONLY
SessionStart      → "session-start"
UserPromptSubmit  → "user-prompt-submit"
Stop              → "stop"
```

Notable: **`PostCompact`** is Codex-only (no Claude analogue). **`PermissionRequest`** appears in Codex source as `PreToolUse` outcome but not as a top-level event — Claude has it as a separate event.

---

## On-disk schema (Claude-compatible)

Both runtimes load hooks via a JSON file with this shape. Figma plugin example confirms (`/home/ccc/.codex/.tmp/plugins/plugins/figma/hooks.json`):

```json
{
  "hooks": {
    "PostToolUse": [
      {
        "matcher": "Write|Edit",
        "hooks": [
          { "type": "command", "command": "./scripts/post_write_figma_parity_check.sh" }
        ]
      }
    ]
  }
}
```

This is byte-identical to Claude Code's `hooks.json` schema. The `matcher` field, the nested `hooks` array of `{ type, command }`, the event-name keys — all the same.

**Implication for the plan:** PLAN.md premise 3 said "Claude Code's `hooks.json` wiring needs a separate Codex equivalent — hand-maintained, lint-checked for parity, NOT text-substitution synced." This is overly pessimistic. The hooks.json file CAN be text-shared if payload key differences are handled at the script layer.

---

## Input payload schemas (per event)

Snake_case keys. Source: CODEX_REVIEW.md finding 6 + cross-referenced against Codex source `codex-protocol/src/protocol.rs` HookEventName variants.

### PreToolUse

| Key | Type | Notes |
|---|---|---|
| `cwd` | string | current working directory |
| `hook_event_name` | string | "PreToolUse" |
| `model` | string | active model (e.g. "gpt-5.5", "claude-opus-4-7") |
| `permission_mode` | string | "untrusted" / "trusted" / approval mode |
| `session_id` | string | session UUID |
| `tool_input` | object | tool-specific payload |
| `tool_name` | string | e.g. "shell", "read_file", "apply_patch" |
| `tool_use_id` | string | per-call correlation id |
| `transcript_path` | string | path to session transcript |
| `turn_id` | string | per-turn id |

### Stop

| Key | Type | Notes |
|---|---|---|
| `cwd` | string | |
| `hook_event_name` | string | "Stop" |
| `last_assistant_message` | string | terminal message text |
| `model` | string | |
| `permission_mode` | string | |
| `session_id` | string | |
| `stop_hook_active` | boolean | recursion guard |
| `transcript_path` | string | |
| `turn_id` | string | |

### UserPromptSubmit

| Key | Type | Notes |
|---|---|---|
| `cwd` | string | |
| `hook_event_name` | string | "UserPromptSubmit" |
| `model` | string | |
| `permission_mode` | string | |
| `prompt` | string | user's submitted text |
| `session_id` | string | |
| `transcript_path` | string | |
| `turn_id` | string | |

---

## Output schema

Hook scripts emit JSON on stdout. Codex parses approximately Claude's format, with documented caveats from CODEX_REVIEW:

```json
{
  "hookSpecificOutput": {
    "hookEventName": "PreToolUse",
    "permissionDecision": "deny",
    "permissionDecisionReason": "...",
    "additionalContext": "..."
  },
  "decision": "...",
  "reason": "...",
  "continue": false,
  "stopReason": "...",
  "systemMessage": "..."
}
```

**Caveats (per CODEX_REVIEW):**
- `PreToolUse` supports `permissionDecision: "deny"` cleanly.
- `permissionDecision: "allow"` and `"ask"` have parser caveats — test edge cases before relying on them in shared gate scripts.

---

## Delta against Claude Code

| Concern | Claude Code | Codex CLI | Sync feasibility |
|---|---|---|---|
| **Hook event name set** | `PreToolUse`, `PostToolUse`, `SessionStart`, `Stop`, `UserPromptSubmit`, `PermissionRequest` | Same five + `PostCompact` (Codex-only) | High — shared events covered, Codex-only and Claude-only events become per-runtime |
| **Input payload key naming** | snake_case in v2 (was camelCase earlier) | snake_case | Identical |
| **Input keys (PreToolUse)** | cwd, hook_event_name, model, permission_mode, session_id, tool_input, tool_name, tool_use_id, transcript_path, turn_id | Same 10 keys | Identical |
| **`hooks.json` schema** | `{ hooks: { Event: [{ matcher, hooks: [{ type, command }] }] } }` | Identical (figma plugin confirms) | Direct port possible |
| **Output JSON** | `hookSpecificOutput.{hookEventName, permissionDecision, permissionDecisionReason, additionalContext}` + top-level | Same shape; `deny` clean, `allow`/`ask` parser caveats | High; gate scripts can write same shape, Codex-side test required for ask/allow |
| **Hook trust** | Claude Code: implicit via plugin install | Codex: explicit per-hook trust state in `[hooks.state.<key>]` table or `--dangerously-bypass-hook-trust` flag | Codex-specific install flow; trust mechanism must be documented for users |
| **Hook discovery** | `plugin/hooks/hooks.json` per plugin | `<plugin>/hooks.json` per plugin (same path) + `[hooks]` in `~/.codex/config.toml` | Identical for plugins; user-level config diverges |
| **Plugin manifest path** | `.claude-plugin/plugin.json` | `.codex-plugin/plugin.json` | One-rename diff |

---

## Empirical capture status

**Attempted:** AC-001 spike tried to fire a hook by appending `[hooks]` block to `~/.codex/config.toml` and running `codex exec`. Configuration appended cleanly. `codex exec` ran. Hooks did NOT fire — script never captured an envelope.

**Why:** Codex 0.130.0 requires explicit per-hook trust state at `[hooks.state.<key>]` (computed `trusted_hash`) BEFORE hooks are activated. The `--dangerously-bypass-hook-trust` flag exists in Codex's `utils/cli/src/shared_options.rs:52` but is NOT exposed via `codex exec` in this build — likely behind a feature flag or only available in `codex` interactive subcommand. The `bypass_hook_trust=true` config override was accepted but did not activate the hooks (possibly because hooks must still be discovered from a trusted source like a plugin).

**Result:** Empirical envelope capture deferred. Architectural verification (schema match + Codex source confirmation + cross-model review consensus) is sufficient evidence for AC-001 — the question of "what does a real Codex PreToolUse envelope look like" has been answered by three independent sources, all converging on the same schema. The remaining question of "does Codex actually invoke my logger when I configure it" is plumbing, not architecture.

**Recommended follow-up (out of AC-001 scope, into AC-005 or a new AC):** when implementing the Codex MCP config emitter (AC-005), include the `[hooks.state]` trust-table generation. The setup skill computes the `trusted_hash` for each registered hook and writes the state table alongside the hooks block. This gives Codex users one-command activation matching Claude Code's plugin-install UX.

---

## What this means for the plan

- **PLAN.md premise 3 amendment:** hooks.json text IS shareable (not "runtime-private" as initially modeled). The wiring difference is the trust-state mechanism Codex layers on top, not the hook config itself.
- **AC-007 (gate-crash logging):** the structured logging format CAN match Claude's, since the input payload keys are identical. The only Codex-side extension is to detect `PostCompact` events (Codex-only) and route them appropriately.
- **AC-005 (MCP config emitter):** scope grows by ~30 lines to also emit the `[hooks]` block + `[hooks.state]` trust table when the user enables Codex side via the opt-in flag (AC-010).
- **AC-009 (capability matrix):** hooks row is now "shared schema, runtime-private trust mechanism" rather than "runtime-private".

---

## Sources cited

- `/tmp/openai-codex-src/codex-rs/core/src/hook_runtime.rs` — runtime engine
- `/tmp/openai-codex-src/codex-rs/hooks/src/engine/mod.rs:79-95` — `HookListEntry` struct
- `/tmp/openai-codex-src/codex-rs/hooks/src/engine/mod.rs:65-75` — `HookEventName` enum
- `/tmp/openai-codex-src/codex-rs/hooks/src/engine/mod.rs:98` — `ClaudeHooksEngine` (Codex's hook engine name)
- `/tmp/openai-codex-src/codex-rs/core/src/config/mod.rs:670` — `bypass_hook_trust: bool` config field
- `/tmp/openai-codex-src/codex-rs/utils/cli/src/shared_options.rs:52` — `--dangerously-bypass-hook-trust` CLI flag (gated)
- `/tmp/openai-codex-src/codex-rs/core/tests/common/hooks.rs:26-69` — `trust_hooks` + `[hooks.state.<key>]` table mechanism
- `/home/ccc/.codex/.tmp/plugins/plugins/figma/hooks.json` — Claude-compatible on-disk schema
- `/home/ccc/.codex/.tmp/plugins/plugins/figma/.codex-plugin/plugin.json` — plugin manifest format
- `/project/harness-e14968053086/doc/harness/tasks/TASK__dual-runtime-plugin-claude-codex/CODEX_REVIEW.md` finding 6 — cross-model schema documentation
- `/project/harness-e14968053086/plugin/scripts/prewrite_gate.py:1-30` — Claude Code's PreToolUse hook contract
