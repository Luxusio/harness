# Phase 4: Verify & Report

Sub-file for setup/SKILL.md. Verification is runtime-neutral and fail-closed:
do not report DONE or stamp a version until the shared finalizer passes.

## 4.1 Resolve runtime paths

```bash
_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
_PLUGIN_ROOT="${HARNESS_PLUGIN_ROOT:-${CLAUDE_PLUGIN_ROOT:-}}"
if [ -z "$_PLUGIN_ROOT" ]; then
  for _CANDIDATE in "$HOME/.codex/harness/plugins/harness" "$HOME/.claude/harness-dev/plugin"; do
    if [ -f "$_CANDIDATE/scripts/setup_finalize.py" ]; then _PLUGIN_ROOT="$_CANDIDATE"; break; fi
  done
fi
if [ -z "$_PLUGIN_ROOT" ]; then
  echo "BLOCKED: installed harness plugin root not found"
  return 1 2>/dev/null || exit 1
fi
_SETUP_RUNTIME="${HARNESS_RUNTIME:-}"
if [ -z "$_SETUP_RUNTIME" ]; then
  [ -f "$_PLUGIN_ROOT/.codex-plugin/plugin.json" ] && _SETUP_RUNTIME="codex" || _SETUP_RUNTIME="claude"
fi
if [ "$_SETUP_RUNTIME" = "codex" ]; then
  _PROJECT_DOC="AGENTS.md"
else
  _PROJECT_DOC="CLAUDE.md"
fi
```

`_PLUGIN_ROOT` must point at the installed plugin, not a development checkout.

## 4.2 Prepare the mechanical setup contract

```bash
python3 "${_PLUGIN_ROOT}/scripts/setup_finalize.py" \
  --repo "$_ROOT" --plugin-root "$_PLUGIN_ROOT" \
  --project-doc "$_PROJECT_DOC" --prepare
```

This command prepares the canonical manifest and operational ignores but does
not stamp a version. It also verifies:

1. Adds every operational harness artifact to `.gitignore` idempotently.
2. Requires manifest schema `version: 5`, top-level `name` and `type`, and
   nested `qa.browser_qa_supported`.
3. Requires the runtime project document and routing marker.
4. Requires contracts, the `@CONTRACTS.md` import, all three critic files,
   a verification command, and every packaged setup sub-file/template.

Any `SETUP_ERROR` is blocking. Fix it and rerun the command.

## 4.3 QA infrastructure verification

Read project type and nested QA flags from the canonical schema. A stdlib-only
probe avoids relying on optional YAML packages:

```bash
python3 - "$_ROOT/doc/harness/manifest.yaml" <<'PY'
import sys
from pathlib import Path

path = Path(sys.argv[1])
top = {}
qa = {}
section = None
for raw in path.read_text(encoding="utf-8").splitlines():
    if raw == "qa:":
        section = "qa"
        continue
    if raw and not raw[0].isspace() and not raw.startswith("#"):
        section = None
        if ":" in raw:
            key, value = raw.split(":", 1)
            top[key] = value.strip().strip('"').strip("'")
    elif section == "qa" and raw.startswith("  ") and ":" in raw:
        key, value = raw.strip().split(":", 1)
        qa[key] = value.strip().strip('"').strip("'")

project_type = top.get("type", "")
browser = qa.get("browser_qa_supported", "false").lower() == "true"
desktop = qa.get("desktop_qa_supported", "false").lower() == "true"
if browser:
    print("QA Strategy: browser")
elif desktop:
    print("QA Strategy: desktop")
elif project_type == "api":
    print("QA Strategy: API")
elif project_type in {"cli", "library"}:
    print("QA Strategy: CLI")
else:
    print("QA Strategy: tests only")
PY
```

For browser/desktop QA, verify required tools from the current session or
global runtime configuration. Setup reads MCP availability from global/runtime settings
and preserves project-root .mcp.json as user-owned configuration. Any missing
server is fixed in global/runtime MCP settings, not by editing the project file.
For pytest-based `test_command`, also run
`python3 -m pytest --version` and report a missing runner as blocking.

## 4.4 Runtime-specific checks

Codex:

- `codex --version` satisfies the installed `.codex-version` pin.
- Harness MCP is registered in global Codex configuration.
- Installed hooks have current trust hashes.
- `AGENTS.md` contains `<!-- harness:routing-injected -->`.
- `AGENTS.md` routes repository mutation to `$harness:run`.
- The installed plugin contains `skills/run/SKILL.md` and
  `skills/run/agents/openai.yaml`; the latter sets
  `policy.allow_implicit_invocation: true`.

Claude Code:

- Harness plugin is enabled.
- Required MCP tools are reachable when browser/desktop QA is enabled.
- `CLAUDE.md` contains `<!-- harness:routing-injected -->`.

## 4.5 Finalize after QA and runtime checks pass

Only after Sections 4.3 and 4.4 pass, run:

```bash
python3 "${_PLUGIN_ROOT}/scripts/setup_finalize.py" \
  --repo "$_ROOT" --plugin-root "$_PLUGIN_ROOT" \
  --project-doc "$_PROJECT_DOC" \
  --qa-verified --runtime-verified
```

This final validation writes `doc/harness/.version`. Never write the stamp
manually or run this command before QA/runtime prerequisites pass.

## 4.6 Completion report

```text
STATUS: DONE

harness is set up for {project}.

Verified:
  - manifest schema: v5 ({type})
  - operational artifacts: gitignored
  - setup resources: packaged
  - routing: {AGENTS.md|CLAUDE.md}
  - installed version: 2.3.0
  - QA strategy: {browser|desktop|api|cli|tests_only}
```

If any check fails, report `STATUS: BLOCKED`, the exact `SETUP_ERROR`, and the
next repair action. Do not downgrade a blocking setup failure to a warning.
