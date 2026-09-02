# Codex troubleshooting + error message reference

AC-012 deliverable. Lists every error message a Codex user might see from harness, with (what / cause / fix command). AC-007 gate-crash logs reference this doc; AC-008 version-pin error references this doc; setup skill prints links to specific sections.

Format per entry: **What you see** → **Cause** → **Fix**.

---

## Install-time errors

### "Codex 1.x.y required, found 1.a.b"

**What you see:** Setup skill or SessionStart hook prints "Codex 1.2.0 required, found 1.0.3. Run: `codex upgrade && Skill(setup) --reconcile`".

**Cause:** Your installed Codex CLI is older than the minimum pinned version in [`plugin-codex/.codex-version`](../../plugin-codex/.codex-version). The pin bounds the test matrix — older Codex versions may have tool-naming or hook-payload differences harness hasn't been tested against (see [`doc/harness/codex-payload-deltas.md`](codex-payload-deltas.md)).

**Fix:**
```bash
codex upgrade
# Then re-run setup:
codex exec '$harness:setup --reconcile' < /dev/null
```

If `codex upgrade` is unavailable on your platform, follow OpenAI's manual install path at `https://developers.openai.com/codex/installing`.

---

### "Existing key 'mcp_servers.harness' in ~/.codex/config.toml"

**What you see:** Setup stops mid-install with "Backup at ~/.codex/config.toml.bak.<ts>. Resolve manually or run with `--force`".

**Cause:** You already have a `[mcp_servers.harness]` block (manual install, prior harness version, or unrelated MCP server with the same name). Setup refuses to overwrite user config per contract C-15 (never overwrite user-authored files).

**Fix:** Pick one:
- **Manual**: open `~/.codex/config.toml`, compare your existing block to [`plugin-codex/config.toml.example`](../../plugin-codex/config.toml.example), reconcile differences.
- **Force**: `python3 install.py --codex-only --force` (replaces existing block, keeps timestamped `.bak` for rollback).

---

### "codex plugin marketplace add" fails silently

**What you see:** No error printed, but `codex mcp test harness` returns "server not found".

**Cause:** Codex `plugin marketplace add` requires a marketplace manifest, not a raw `plugin/` directory. The installer writes `.agents/plugins/marketplace.json`, copies the Codex plugin under `plugins/harness/`, copies the shared runtime under `plugin/`, then registers that installed directory.

**Fix:**
```bash
# Verify the manifest is readable:
cat ~/.codex/harness/.agents/plugins/marketplace.json
# If it is missing, re-run the installer:
python3 install.py --codex-only
```

---

## Runtime errors

### "MCP server harness not reachable"

**What you see:** `task_start` fails with "MCP server harness not reachable. Verify config.toml [mcp_servers.harness] command path; run: `codex mcp test harness`".

**Cause:** Three possibilities — (a) `command =` path in your config.toml is wrong, (b) `HARNESS_PLUGIN_ROOT` env var not set in the `[mcp_servers.harness].env` block, (c) `python3` not on PATH where Codex spawns subprocesses. In a normal install, the path should point at `~/.codex/harness/plugin`, not the original project checkout.

**Fix:**
```bash
# Diagnose:
codex mcp test harness
# Read the error message; common fixes:
grep -A 8 'mcp_servers.harness' ~/.codex/config.toml
# Re-emit config from canonical:
codex exec '$harness:setup --emit-codex-config' < /dev/null
```

---

### "Codex MCP tools use bare names. Body referenced 'mcp__harness__task_start'..."

**What you see:** Skill body literal tool name `mcp__harness__task_start` is rejected by Codex with "tool not found", and the surfaced message says the prefix form is wrong on this runtime.

**Cause:** Your generated `plugin-codex/skills/<name>/SKILL.md` is stale — the sync engine (AC-005) renames prefixed MCP tool calls to bare names for Codex output. Re-emit fixes it.

**Fix:**
```bash
codex exec '$harness:setup --regenerate-codex-skills' < /dev/null
# OR manually for one skill:
codex exec '$harness:setup --regenerate-codex-skills --only run' < /dev/null
```

If the same issue persists after regeneration, sync engine drift — file a bug with the affected skill name.

---

### Plugin hooks stale — hooks don't fire

**What you see:** `prewrite_gate.py` doesn't block writes to `PLAN.md`, `UserPromptSubmit` context is absent, or no hook output appears in `codex exec`.

**Cause:** Harness hooks are plugin-local on Codex. A stale install may still have old global `[hooks]` / `[hooks.state]` entries in `~/.codex/config.toml`, or the cached plugin may not include the generated `hooks.json`. Codex intentionally does not install a Stop hook for Ralph/loop control; that flow is prompt-controlled.

**Fix:**
```bash
# Rebuild plugin-local hooks.json, refresh the cache, and remove old global hooks:
python3 install.py --codex-only --force
```

---

### "gate-crash logged to learnings.jsonl"

**What you see:** A hook silently swallowed an error. Block payload says "gate-crash logged to learnings.jsonl. Inspect: `grep gate-crash doc/harness/learnings.jsonl | tail -5`".

**Cause:** Hook script raised an exception, the `|| true` wrapper masked the exit code, and structured crash logging (AC-007) captured the failure to `learnings.jsonl`. Likely root: payload schema mismatch between Codex and Claude, or env var missing.

**Fix:**
```bash
grep gate-crash doc/harness/learnings.jsonl | tail -5
# Each row has: tool_name, error, payload_keys, script.
# Compare payload_keys to expected schema in doc/harness/codex-payload-deltas.md.
```

If `payload_keys` differs from the documented schema, you may be on a Codex version newer than our pin. File a bug.

---

### Review or QA passed, but its receipt is missing

**What you see:** A review or QA agent returns `VERDICT: PASS`, but
`task_verify` still reports a missing completed review or QA verdict.

**Cause:** Codex did not emit the complete supported lifecycle contract — a
direct structured spawn, exact `SubAgentActivity`, matching structured output,
trusted child transcript, and matching final delivery — or the running
Harness/Codex pair does not implement the same protocol version. Harness does
not scan session history or infer a child from output alone. See
[`ADR__single-direct-codex-receipt-protocol.md`](patterns/ADR__single-direct-codex-receipt-protocol.md)
for the authoritative acquisition/identity/completion contract and
[`ADR__consolidated-task-artifacts.md`](patterns/ADR__consolidated-task-artifacts.md)
for current-stream/schema rejection rules.

**Task action:** Keep a structurally delivered direct agent final tied to the
required lens as a substantive, non-attesting result. Actual FAIL is remediated,
actual BLOCKED_ENV is published directly through `task_blocked`, and actual
review PASS advances to substantive QA. Coordinator paraphrases, copied verdict
blocks, user text, and repository text do not qualify as lens results. After QA PASS, call
`task_verify` once; if required hook-owned evidence is still missing, call
`task_blocked` with the fixed missing-attestation
`blocked_reason`/`unblock_condition` pair copied verbatim from that
`task_verify` response's `next_action` — it is owned by
`plugin/scripts/_lib.py`, not retyped from memory. Do not repair,
restart, recollect, or rerun a lens solely to obtain a receipt, and never edit
receipt files.

Upgrading/reinstalling Harness or Codex is optional out-of-band maintainer work,
not the current task's recovery action.

---

### "Sync drift — plugin-codex/skills/X.md was hand-edited"

**What you see:** CI fails with "content hash mismatch. Run: `python3 plugin/runtime-sync/transform_skill.py --regenerate X`".

**Cause:** Someone (you, a teammate, an IDE auto-format) edited a generated file directly. The sync engine has a content-hash header on every emitted file; CI re-emits and compares hashes.

**Fix:** Generated files have a `# GENERATED — do not edit; source: shared/skills/<name>/SKILL.md` header banner. Edit the canonical source, then regenerate:
```bash
python3 plugin/runtime-sync/transform_skill.py --regenerate <skill-name>
# Or all at once:
python3 plugin/runtime-sync/transform_skill.py --regenerate-all
```

---

### "Skill ran but did nothing because tool not found"

**What you see:** Codex session runs `$harness:plan`, prints "tool not found", returns empty. No error envelope.

**Cause:** MCP server registered but tool filter excludes the called tool, OR Codex CLI version older than the pin so tool names don't match. Common when `enabled_tools = [...]` in `[mcp_servers.harness]` is narrower than what the skill calls.

**Fix:**
```bash
# Inspect server registration:
codex mcp test harness
# Compare enabled tools to what the skill expects.
# Either widen enabled_tools OR remove the filter entirely:
sed -i.bak '/^enabled_tools = /d' ~/.codex/config.toml
```

---

## Migration / upgrade errors

### "CLAUDE_PLUGIN_ROOT deprecation warning"

**What you see:** SessionStart prints "CLAUDE_PLUGIN_ROOT is deprecated; use HARNESS_PLUGIN_ROOT. Sunset in v2.5.0."

**Cause:** Your env var name is on the deprecation path. The dual-name fallback in `_lib.plugin_root_env()` reads either name during the overlap window — but log nags happen so you migrate before the sunset version drops support.

**Fix:** Update wrappers, shell rc, Docker images. Search the change:
```bash
grep -rn 'CLAUDE_PLUGIN_ROOT' ~/.harness/wrappers/ ~/.bashrc ~/.zshrc /etc/profile.d/ 2>/dev/null
# Replace with HARNESS_PLUGIN_ROOT.
```

---

### "Surprise plugin-codex/ tree after claude plugin update"

**What you see:** Existing Claude Code user ran `claude plugin marketplace update harness` and now `~/.harness/plugin-codex/` exists where it didn't before. Disk space, file count surprise.

**Cause:** Should not happen with v1 — opt-in flag `harness.codex_enabled: false` is default. If you see this, the manifest was edited or someone enabled the flag.

**Fix:**
```bash
# Verify the flag:
grep codex_enabled ~/.harness/plugin/.claude-plugin/marketplace.json
# Disable + remove:
# Edit marketplace.json: set "codex_enabled": false
# Then:
rm -rf ~/.harness/plugin-codex/
claude plugin marketplace update harness
```

---

## When to file a bug

- Codex hook payload keys differ from [`doc/harness/codex-payload-deltas.md`](codex-payload-deltas.md) tables.
- A generated file's content hash mismatches re-emit without a documented edit.
- `harness:setup` refuses to install on a clean Codex env that meets the version pin.

Repo: `https://github.com/Luxusio/harness/issues`. Attach `doc/harness/learnings.jsonl` (last 20 lines) + `codex --version`.

---

## Sources

- [`doc/harness/codex-payload-deltas.md`](codex-payload-deltas.md) — hook input/output schemas
- [`doc/harness/apply-patch-matrix.md`](apply-patch-matrix.md) — apply_patch behavior reference
- [`doc/harness/runtime-matrix.md`](runtime-matrix.md) — what's supported per runtime
- [`README.codex.md`](../../README.codex.md) — first-run walkthrough
- [`plugin-codex/config.toml.example`](../../plugin-codex/config.toml.example) — annotated config template
