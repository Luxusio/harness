# 2026-05-24 — Codex internal skills hidden from user menu

Codex exposes every `plugin-codex/skills/*/SKILL.md` entry in its skill surface,
so `user-invocable: false` was not enough to hide internal harness prompts.

Codex now keeps only `setup`, `run`, and `plan` under `plugin-codex/skills/`.
Internal methodology prompts moved to `plugin-codex/internal-skills/` and remain
packaged for `run` and `plan` to read inline.

Verification covered the visible skill tree, install payload packaging,
Codex run/plan references, and install dry-run.
