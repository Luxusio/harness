# 2026-05-24 — Plan hidden from user skill surface

The public harness command surface is now `setup` and `run`.

Claude keeps the plan prompt as an internal skill with `user-invocable: false`.
Codex keeps the plan prompt under `plugin-codex/internal-skills/plan/` because
Codex exposes every `plugin-codex/skills/*/SKILL.md` entry.

`run` still executes the plan phase internally; users no longer need to invoke a
separate plan command from the skill menu.
