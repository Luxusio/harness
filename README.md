# harness

**Turn any repository into an AI operating system with one command.**

harness is a Claude Code plugin that gives your repository persistent memory, automatic workflow routing, validation loops, and brownfield-safe execution. Run `/harness:setup` once, then just talk — the system handles the rest.

## Supported project types

harness works with **any single-root project**. Setup auto-detects languages, frameworks, and tooling.

**Best inference support** (commands and tooling auto-detected):
- Node.js / TypeScript (npm, yarn, pnpm, ESLint, madge)
- Python (pytest, ruff, poetry, pip)
- Go (go test, go vet, go build)
- Rust (cargo test, cargo build, cargo clippy)
- Java / Kotlin (gradle, maven)

**Also works with** any other language — setup will ask for commands it can't infer.

> **Not yet supported:** Monorepos (workspaces, Nx, Turborepo, Lerna) and polyglot projects. See [Known limitations](#known-limitations).

## What harness does

- **Automatic workflow routing** — say "fix the login bug" and the system classifies your intent, loads the right context, and follows the bugfix procedure automatically
- **Repo-local memory** — constraints, decisions, findings, and unknowns are persisted in your repo, not lost between sessions
- **Validation loops** — every change goes through narrow-to-wide validation with concrete evidence before completion
- **Brownfield safety** — unfamiliar code areas are mapped before editing, with safety nets installed first
- **Architecture enforcement** — boundary rules are checked via `arch-check.sh` with auto-detection of ESLint, madge, ruff, cargo clippy, and go vet
- **Session continuity** — each session ends with a summary that the next session picks up automatically

## Quick start

### 1. Install

**Option A — Marketplace (recommended)**

```
/plugin marketplace add https://github.com/Luxusio/harness.git
/plugin install harness@harness
```

**Option B — Local development**

```bash
claude --plugin-dir ./plugin
```

### 2. Run setup

Open any project and run:
```
/harness:setup
```

Setup scans your project, infers commands and risk zones, asks a few questions, and generates the control plane. Takes about 1-2 minutes.

### 3. Start working

Just talk naturally:
- "Add input validation to the user endpoint"
- "Fix the authentication bypass"
- "Refactor the payment module"
- "From now on, all API responses must include a request_id"

The orchestrator handles classification, routing, validation, and memory automatically. No slash commands needed.

## What setup creates

```
your-repo/
├── CLAUDE.md                           # AI operating manual (reference data)
└── harness/
    ├── manifest.yaml                   # Project shape, commands, risk zones
    ├── router.yaml                     # Intent routing configuration
    ├── arch-rules.yaml                 # Architecture boundary rules (initially empty)
    ├── policies/
    │   ├── approvals.yaml              # Risk zone approval gates
    │   └── memory-policy.yaml          # Memory classification rules
    ├── state/
    │   ├── recent-decisions.md         # Chronological decision log
    │   ├── recent-decisions-archive.md # Archived older decisions
    │   ├── unknowns.md                # Open questions and hypotheses
    │   ├── current-task.yaml          # Runtime loop state (gitignored)
    │   └── last-session-summary.md    # Previous session summary (gitignored)
    ├── scripts/
    │   ├── validate.sh                 # Validation checks (auto-detects tools)
    │   ├── smoke.sh                    # Smoke tests
    │   └── arch-check.sh              # Architecture boundary checks
    └── docs/
        ├── index.md                    # Documentation index
        ├── constraints/                # Confirmed project rules
        ├── decisions/                  # Architecture Decision Records
        ├── domains/                    # Domain knowledge
        ├── architecture/               # Boundaries and patterns
        ├── runbooks/                   # Development procedures
        └── brownfield/                 # Legacy code maps (brownfield only)
```

## How it works after setup

You speak naturally. The orchestrator automatically:

1. **Classifies** your request (feature, bugfix, refactor, test, docs, decision...)
2. **Loads** only the relevant context from the control plane
3. **Checks risk** against approval rules before touching sensitive areas
4. **Routes** to the correct skill procedure
5. **Validates** with concrete evidence (tests, lint, build)
6. **Records** durable knowledge back into repo memory
7. **Summarizes** what changed, what was validated, and what needs follow-up

## Procedures

| Procedure | Triggers on | What it does |
|-----------|-------------|-------------|
| Feature | "build", "add", "create", "implement" | Scope → brownfield check → risk gate → implement → test → validate → sync |
| Bugfix | "fix", "broken", "error", "regression" | Reproduce → root cause → risk gate → fix → regression test → validate |
| Test expansion | "test", "coverage", "prove" | Assess gaps → prioritize → write tests → validate → report |
| Refactor | "refactor", "cleanup", "simplify" | Preservation contract → characterize → refactor in steps → validate |
| Docs sync | "document", "update docs" | Identify changes → update docs → update index → sync decisions |
| Decision capture | "from now on", "always", "never" | Classify → verify authority → record → encode as executable if possible |
| Brownfield adoption | "legacy", "unfamiliar", "map" | Inventory → protect flows → document → identify risks → install safety nets |
| Validation loop | After any code change | Lint → tests → smoke → runtime evidence → confidence assessment |
| Architecture guardrails | Structural changes | Load model → check boundaries → flag violations → record findings |
| Memory policy | Durable knowledge discovered | Classify → verify → record → promote → cleanup |

## Agents

| Agent | Role |
|-------|------|
| `harness-orchestrator` | Main runtime loop — classifies, routes, validates, syncs |
| `requirements-curator` | Scope clarification and acceptance criteria |
| `brownfield-mapper` | Maps legacy code before risky edits |
| `implementation-engineer` | Code changes with small coherent diffs |
| `test-engineer` | Test writing and coverage |
| `refactor-engineer` | Behavior-preserving structural improvements |
| `docs-scribe` | Documentation and memory updates |
| `browser-validator` | Web UI validation (when tooling available) |

## Diagnostic

Run `/harness:validate` anytime to check control plane health:
- Missing files
- Dangling references in approvals or index
- Stale `{{...}}` placeholders
- Script permissions

## Configuration

### Approval rules
Edit `harness/policies/approvals.yaml` to add or remove risk zones. The orchestrator checks this before touching sensitive areas.

### Architecture boundaries
When the architecture-guardrails procedure confirms a boundary rule, it appends to `harness/arch-rules.yaml`. Run `harness/scripts/arch-check.sh` to verify enforcement.

### Memory
- Decisions auto-archive after 50 entries
- Resolved unknowns are pruned after 30 days
- Session summaries provide continuity between sessions

## Known limitations

1. **Monorepo/polyglot projects** — Setup assumes a single project root. Workspaces, Nx, Turborepo, and Lerna configurations are detected but treated as low-confidence.
2. **Browser validation** — The `browser-validator` agent exists but requires external browser tooling (not included). Falls back to smoke checks.
3. **Memory promotion** — The hypothesis → confirmed → enforced ladder is manual. No automated promotion based on evidence accumulation.
4. **Prompt-driven** — The entire system runs via Claude Code's agent/skill infrastructure. Behavior depends on model capability and prompt adherence.
5. **Plugin required** — The harness plugin must remain installed for runtime operation. The `harness/` directory in your repo stores memory and configuration, but the orchestrator and skill procedures live in the plugin.

## Development

### Key files
- `agents/` — 8 agent definitions (orchestrator + 7 specialists)
- `skills/` — 12 skills (setup + validate + 10 hidden procedures)
- `hooks/` — SessionStart (context injection) and Stop (verification)
- `scripts/` — SessionStart hook script
- `skills/setup/templates/` — 22 template files for control plane generation
- `skills/setup/inference-application.md` — Inference confidence tiers and rules

## License

MIT
