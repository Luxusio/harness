# 2026-05-08 — Local install moved from README to CONTRIBUTING; symlink replaced with marketplace-add-by-path

**Task:** TASK__readme-local-install-extract-to-contributing

User-feedback-driven doc restructure. README's local-development install instructions used a symlink-based approach (`ln -s "$(pwd)/plugin" ~/.claude/plugins/harness` then `/plugin install harness`). User confirmed via testing that the canonical Claude Code local-install flow is **`/plugin marketplace add <local-path>` + `/plugin install <name>`** — no symlinks, no manual copies. Local-dev guidance also belongs in CONTRIBUTING.md, not README.md (different audiences).

## What changed

**README.md (-34 net):**
- Removed `### Local development (symlink)` subsection (lines 17-35 in prior state).
- Removed `### Uninstall` subsection (lines 37-45).
- Removed stale `ln -s` reference inside `## Self-dogfooding` section (unplanned-but-necessary fix — once local-dev moved to CONTRIBUTING.md and adopted marketplace-add-by-path, the Self-dogfooding wording was broken).
- Added one-line pointer at end of `## Install`: `Contributors / local development → see [CONTRIBUTING.md](CONTRIBUTING.md).`
- Self-dogfooding section now says `After installing locally (see [CONTRIBUTING.md](CONTRIBUTING.md))` instead of inlining the install command.

**CONTRIBUTING.md (NEW, ~50 lines):**
- Header + scope statement: "this file documents local development setup; for end-user install see README.md".
- **Local development install:** `git clone` → `/plugin marketplace add "$(pwd)"` → `/plugin install harness`. Explicit note that `/plugin marketplace add` accepts an absolute filesystem path, no symlinks or manual copies into `~/.claude/plugins/`.
- **Validating the install:** `claude plugin validate ~/.claude/plugins/harness`.
- **Updating during development:** `/plugin marketplace update harness` or full reinstall.
- **Uninstall:** `/plugin uninstall harness` then `/plugin marketplace remove harness`.
- **Repo layout** + **Running the harness loop on the harness repo** sections orient new contributors to the dogfooding workflow.

## Excluded from scope

- Other README sections (Setup, Routing, Project layout, Quality scripts, etc.) unchanged.
- The marketplace install flow at the top of README unchanged.
- No code, scripts, hooks, or runtime behavior touched.

## Impact

End-users opening README.md see only the marketplace install they need. Contributors who would have hit the symlink-based local-dev flow are pointed to CONTRIBUTING.md and find the canonical marketplace-add-by-path flow that actually works (per user's testing). The Self-dogfooding section now correctly references the new flow rather than perpetuating the broken symlink approach.

## Premise gate

Satisfied via two prior AskUserQuestions in conversation — (1) user confirmed `/plugin marketplace add <local-path>` + `/plugin install harness` as the canonical local-install command form; (2) user confirmed extraction to CONTRIBUTING.md as a separate doc.

## References

- PLAN.md: `doc/harness/tasks/TASK__readme-local-install-extract-to-contributing/PLAN.md`
- HANDOFF.md: `doc/harness/tasks/TASK__readme-local-install-extract-to-contributing/HANDOFF.md`
- New: `CONTRIBUTING.md`
- Updated: `README.md`
