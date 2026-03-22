# PROJECT_PLAN — harness

## 1. Product definition

harness is a repo-local operating system plugin for Claude Code. Run `/harness:setup` once to install the control plane into any repository. After that, work in plain language — no workflow commands to memorize. Use `/harness:validate` when you want an explicit diagnostic on the current state.

## 2. Shipped source of truth

The runtime lives entirely under `plugin/`:

- `plugin/CLAUDE.md` — main agent instructions injected at session start
- `plugin/agents/` — specialist subagent prompts
- `plugin/skills/` — hidden workflow skills (setup, validate, and internal workflows)
- `plugin/hooks/` — session-start and stop hooks
- `plugin/scripts/` — helper scripts referenced by skills

Everything outside `plugin/` is project-level documentation and tooling for developing harness itself, not the shipped artifact.

## 3. Runtime model

```
user plain-language request
  -> orchestrator (harness-orchestrator)
  -> intent router -> workflow skill
  -> specialist agents (implementation, test, docs, brownfield-mapper, etc.)
  -> validation loop
  -> memory sync (recent-decisions, unknowns, domain docs)
  -> summary output
```

The orchestrator always loads `harness/manifest.yaml`, `harness/router.yaml`, and `harness/policies/approvals.yaml` before acting. Memory is repo-local — it persists across sessions because it lives in the repository, not in the conversation.

## 4. Command surface

| Command | Purpose |
|---|---|
| `/harness:setup` | Bootstrap the control plane into a new or existing repository |
| `/harness:validate` | Diagnostic: check that the control plane is consistent and complete |

All other workflows (feature, bugfix, tests, refactor, docs, decision-capture, brownfield-adoption) are hidden skills invoked automatically by the orchestrator from plain-language requests.

## 5. Current gaps

- **Monorepo / polyglot confidence is low.** The manifest infers language and framework but multi-root monorepos require manual verification after setup.
- **Browser tooling is an external dependency.** Browser-based runtime evidence (screenshots, console errors) requires the user to have browser MCP tools configured separately.
- **Memory promotion is manual.** Moving a finding from `unknowns.md` to `docs/constraints/` or `docs/decisions/` still requires an explicit user request or a triggered docs-sync workflow. There is no automated promotion hook yet.

## 6. Near-term priorities

1. **Single prompt tree.** Consolidate all agent and skill prompts so there is exactly one authoritative location per concern — eliminate any duplication between plugin and repo-level files.
2. **Stronger stop hook.** The session stop hook should reliably detect when validation was skipped or memory sync was not run, and surface a clear warning before the session closes.
3. **Clearer specialist output contracts.** Each specialist agent (implementation-engineer, test-engineer, docs-scribe, etc.) should declare its expected output format explicitly so the orchestrator can validate and route results without ambiguity.
