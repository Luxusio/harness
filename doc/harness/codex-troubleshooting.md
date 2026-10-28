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

Older installers stopped here on every re-run unless given `--force`. Since
2026-09-27 a plain `python3 install.py` replaces the harness-owned sections
(`[plugins."harness@harness"]`, `[marketplaces.harness]`, `[mcp_servers.harness]`)
whenever it refreshes a stale runtime, and keeps the previous file as
`config.toml.bak.<timestamp>`. Every other section is left as it was. To
rewrite the block while the payload is already synchronized, run
`python3 install.py --codex-only --force`.

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

### "unsafe payload path component: … (mode 0777, uid 0)"

**Symptom:** `python3 install.py` (and so
`plugin/scripts/install_verified.py`) fails with `unsafe payload path
component: /home/<you>/.codex (mode 0777, uid 0) while inspecting …`, every
run.

**Cause:** a directory above the installed payload is writable by other users.
The installer refuses to trust a payload another local user could swap.
`--force` does not change directories above the payload, so it cannot clear
this.

**Fix:** when the line after `repair:` (also the `; fix: …` end of the error
line) is a command, run it, then re-run `python3 install.py`. When it
names another owner or asks you to choose another install root, the directory
is shared or not yours: install under your home instead of changing it (Claude:
`HARNESS_DEST`; Codex: `--config-path <dir under your home>/config.toml`). A
writable directory *above* your home cannot be avoided; an administrator has to
fix it. For a
root-owned config directory in your home that is
`sudo chown "$(id -u):$(id -g)" ~/.codex && chmod go-w ~/.codex` (and the same
for `~/.claude`). `chmod +t` is not enough there: see
`doc/harness/REQ__installed-tree-modes-are-installer-owned.md`.

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

**Cause:** Codex skill prose still names a tool the Claude way. Nothing
rewrites it: the Codex skill files are hand-maintained, and the sync engine that
once renamed prefixed MCP calls was removed on 2026-05-14.

**Fix:** find where the prefixed name comes from and use the bare name there
(`task_start`, not `mcp__harness__task_start`). A Codex `SKILL.md` is edited in
`plugin-codex/`. A Markdown sub-file that `install.py` `_build_codex_payload`
copies from `plugin/skills/` is fixed in `plugin/skills/` in a runtime-neutral
way, then re-run
`python3 install.py --codex-only`.

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

### Review or QA passed, but verification is not PASS

**Cause:** machine verification measures current required WBS assertions,
blocked state and source applicability. It does not attest agent results.

**Task action:** await actual independent review PASS before actual QA. Fix real
failures and publish real environmental blockers. After actual QA PASS, run
`wbs(action="verify")` and resolve the stated WBS checks. Never rerun successful
work, repair recording or park a task for missing recording. See
[the current requirement](REQ__receipt-free-execution-and-completion.md).

---

### Claude and Codex skill trees disagree

**What you see:** a skill behaves differently under Claude Code and Codex, or
a fix landed in `plugin/skills/<name>/` but not in `plugin-codex/`.

**Cause:** the `SKILL.md` files (and `plugin-codex/skills/run/agents/openai.yaml`)
are maintained per runtime. The YAML sync
engine (`plugin/runtime-sync/transform_skill.py` and its generated-file
banners; the planned CI parity check was never built) was removed on
2026-05-14 (`doc/harness/spike-report.md` §3.6), so nothing regenerates one
tree from the other.

**Fix:** edit a skill's `SKILL.md` in both `plugin/skills/` and `plugin-codex/`
in the same change. The Markdown sub-files listed in `install.py`
`_build_codex_payload` (its `shared_skill_files` table plus the setup
sub-files) and `templates/` are copied from `plugin/skills/`, and the copy
overwrites anything with the same name under `plugin-codex/` — edit those only
in `plugin/skills/`.

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

**Cause:** Claude's marketplace source is `./plugin`, which excludes the
sibling `plugin-codex/` tree. The source installer instead installs each
runtime whose CLI is on PATH, unless `--claude-only` or `--codex-only` selects
one. The reserved `features.codex_enabled` metadata does not control this.

**Fix:** Check whether the source installer ran and inspect the registered
marketplace source. Use `python3 install.py --claude-only` for future
Claude-only installs. This does not uninstall an existing Codex registration;
follow the removal instructions in `README.codex.md` if that is intended.

---

## When to file a bug

- Codex hook payload keys differ from [`doc/harness/codex-payload-deltas.md`](codex-payload-deltas.md) tables.
- `harness:setup` refuses to install on a clean Codex env that meets the version pin.

Repo: `https://github.com/Luxusio/harness/issues`. Attach `doc/harness/learnings.jsonl` (last 20 lines) + `codex --version`.

---

## Sources

- [`doc/harness/codex-payload-deltas.md`](codex-payload-deltas.md) — hook input/output schemas
- [`doc/harness/apply-patch-matrix.md`](apply-patch-matrix.md) — apply_patch behavior reference
- [`doc/harness/runtime-matrix.md`](runtime-matrix.md) — what's supported per runtime
- [`README.codex.md`](../../README.codex.md) — first-run walkthrough
- [`plugin-codex/config.toml.example`](../../plugin-codex/config.toml.example) — annotated config template
