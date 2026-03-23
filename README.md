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

> **Monorepos (workspaces, Nx, Turborepo, Lerna):** experimental / low-confidence support. Setup detects workspace structure but treats the project as a single root. Manual review of inferred commands is recommended. See [Known limitations](#known-limitations).

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

> **Note:** This uses Git-based marketplace add. The relative plugin source (`./plugin`) in `marketplace.json` works with Git-based installs but not with raw URL-based marketplace catalogs.

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

The orchestrator handles classification, routing, validation, and memory automatically. No slash commands are needed for normal work after setup. `/harness:validate` remains available as an optional diagnostic command.

## What setup creates

```
your-repo/
├── CLAUDE.md                           # AI operating manual (reference data)
└── harness/
    ├── manifest.yaml                   # Project shape, commands, risk zones (descriptive context)
    ├── router.yaml                     # Intent routing configuration
    ├── arch-rules.yaml                 # Architecture boundary rules (initially empty)
    ├── policies/
    │   ├── approvals.yaml              # Approval gates — ask-first rules for sensitive areas
    │   └── memory-policy.yaml          # Memory classification rules
    ├── state/
    │   ├── recent-decisions.md         # Chronological decision log
    │   ├── recent-decisions-archive.md # Archived older decisions
    │   ├── unknowns.md                # Open questions and hypotheses
    │   ├── current-task.yaml          # Runtime loop state (gitignored)
    │   └── last-session-summary.md    # Previous session summary (gitignored)
    ├── scripts/
    │   ├── validate.sh                 # Validation checks (manifest commands first, auto-detect fallback)
    │   ├── smoke.sh                    # Smoke tests (manifest commands first, auto-detect fallback)
    │   ├── arch-check.sh              # Architecture boundary checks
    │   └── check-approvals.sh         # Deterministic approval gate checker
    └── docs/
        ├── index.md                    # Documentation index
        ├── constraints/                # Confirmed project rules
        ├── decisions/                  # Architecture Decision Records
        ├── domains/                    # Domain knowledge
        ├── requirements/              # Requirement specs (REQ files)
        ├── architecture/               # Boundaries and patterns
        ├── runbooks/                   # Development procedures
        └── brownfield/                 # Legacy code maps (brownfield only)
```

## How it works after setup

You speak naturally. The orchestrator automatically:

1. **Classifies** your request (feature, bugfix, refactor, test, docs, decision...)
2. **Loads** only the relevant context from the control plane
3. **Checks risk** against approval rules before touching sensitive areas
4. **Routes** to the correct internal workflow procedure document
5. **Validates** with concrete evidence (tests, lint, build)
6. **Records** durable knowledge back into repo memory
7. **Summarizes** what changed, what was validated, and what needs follow-up

## Procedures

| Procedure | Triggers on | What it does |
|-----------|-------------|-------------|
| Feature | "build", "add", "create", "implement" | Scope → REQ capture → conflict check → brownfield check → risk gate → implement → test → validate → sync |
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
| `requirements-curator` | Scope clarification, acceptance criteria, requirement persistence, conflict checking |
| `brownfield-mapper` | Maps legacy code before risky edits |
| `implementation-engineer` | Code changes with small coherent diffs |
| `test-engineer` | Test writing and coverage |
| `refactor-engineer` | Behavior-preserving structural improvements |
| `docs-scribe` | Documentation and memory updates |
| `browser-validator` | Web UI validation (when tooling available) |

### OMC agent escalation

OMC escalation is optional. When the [oh-my-claudecode](https://github.com/anthropics/oh-my-claudecode) plugin is installed, harness automatically escalates to OMC agents for deeper work:
- Complex architecture → `oh-my-claudecode:architect`
- Code review → `oh-my-claudecode:code-reviewer`
- Security review → `oh-my-claudecode:security-reviewer`
- Deep debugging → `oh-my-claudecode:debugger` / `oh-my-claudecode:tracer`
- UI/UX design → `oh-my-claudecode:designer`

Without OMC installed, harness agents and the normal validation flow continue without interruption. OMC absence is not a failure — it is an optional enhancement.

## Diagnostic

Run `/harness:validate` anytime to check control plane health:
- Missing files
- Dangling references in approvals or index
- Stale `{{...}}` placeholders
- Script permissions

## Configuration

### Approval gates
Edit `harness/policies/approvals.yaml` to add or remove approval gates (ask-first rules). The orchestrator checks this before touching sensitive areas. Note: `harness/manifest.yaml` contains a `risk_zones` field that provides descriptive risk context about which paths are sensitive — this informs setup and human review but is not itself an enforcement gate.

### Architecture boundaries
When the architecture-guardrails procedure confirms a boundary rule, it appends to `harness/arch-rules.yaml`. Run `harness/scripts/arch-check.sh` to verify enforcement.

### Memory
- `recent-decisions.md` undergoes compaction policy during sync when the entry threshold is exceeded (archival to `recent-decisions-archive.md`)
- Resolved items in `unknowns.md` become pruning candidates during sync after 30 days
- Session summaries provide continuity between sessions
- Requirements track lifecycle: draft → accepted → implemented → verified

## Known limitations

1. **Monorepo/polyglot projects** — Experimental / low-confidence support. Setup can inventory multi-root workspace structure and read service-level docs, but the runtime control plane remains single-root. There is no per-service manifest schema yet. Inferred commands and risk zones should be reviewed manually before trusting them.
2. **Browser validation** — The `browser-validator` agent exists but requires external browser tooling (not included). Falls back to smoke checks.
3. **Memory promotion** — The hypothesis → confirmed → enforced ladder is manual. No automated promotion based on evidence accumulation.
4. **Prompt-driven** — The entire system runs via Claude Code's agent/skill infrastructure. Behavior depends on model capability and prompt adherence.
5. **Plugin required** — The harness plugin must remain installed for runtime operation. The `harness/` directory in your repo stores memory and configuration, but the orchestrator and skill procedures live in the plugin.

## Development

The shipped prompt system lives under `plugin/`. Root-level documentation exists to help develop this repository, not as a second runtime prompt tree.

The root `harness/` directory is the dogfood fixture for this repository — it is a real harness control plane used to develop harness itself. `scripts/check-dogfood-sync.sh` checks for static mirror file drift between the dogfood fixture and the setup templates.

### Key files
- `plugin/agents/` — shipped agent definitions
- `plugin/skills/` — shipped skills
- `plugin/hooks/` — shipped hooks
- `plugin/scripts/` — hook support scripts
- `plugin/skills/setup/templates/` — generated control plane templates
- `plugin/.claude-plugin/plugin.json` — plugin manifest
- Root `CLAUDE.md` — this repository's development manual
- Root `harness/` — dogfood harness control plane for this repo
- `scripts/check-dogfood-sync.sh` — checks static mirror file drift between dogfood fixture and setup templates

## License

MIT
