"""AC-005 canonical schema for runtime-sync.

A skill in canonical form is a YAML file with these fields:

  name              str           required — skill identifier
  description       str           required — one-line summary for both frontmatters
  voices_required   int           default 1 — 1 = single voice, 2 = dual voice (Agent fan-out)
  sub_skills        list[str]     default [] — skill names invoked via Skill() on Claude,
                                              rendered as "read inline" prose on Codex
  runtime_notes_codex str         optional — markdown block rendered into the "Codex runtime
                                              notes" header on the Codex SKILL.md output. Not
                                              rendered on Claude output.
  interactions      list[dict]    optional — AskUserQuestion-style structured asks; rendered
                                              as `AskUserQuestion` blocks on Claude, as
                                              conversational prose on Codex. Each entry has
                                              `phase`, `content`, `options` (list of strings).
  tools_used        list[str]     optional — pseudo-tokens like Read / Edit / Bash referenced
                                              in body. Sync engine rewrites for Codex
                                              (read_file / apply_patch / shell) where the
                                              body uses {{TOKEN}} substitution.
  body              str           required — the markdown body (post-frontmatter). May
                                              contain {{TOKEN}} substitutions matching
                                              tools_used; SKILL.md sections "## Voice", "## Phase 1"
                                              etc. live here. Pure prose by default.

The transform engine (`transform_skill.py`) reads the YAML, applies runtime-specific rendering,
and writes:
  - Claude target: plugin/skills/<name>/SKILL.md   — full Claude-flavored output
  - Codex target:  plugin-codex/skills/<name>/SKILL.md — Codex-flavored output

This module defines the schema as a plain dataclass (stdlib only, no Pydantic dependency).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Interaction:
    """One AskUserQuestion-style ask in the skill."""
    phase: str
    content: str
    options: list[str] = field(default_factory=list)
    recommended: str = ""  # letter of recommended option (e.g. "A")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Interaction":
        return cls(
            phase=str(d.get("phase", "")),
            content=str(d.get("content", "")),
            options=list(d.get("options") or []),
            recommended=str(d.get("recommended", "")),
        )


@dataclass
class CanonicalSkill:
    """The canonical YAML representation of a skill."""
    name: str
    description: str
    body: str
    voices_required: int = 1
    sub_skills: list[str] = field(default_factory=list)
    runtime_notes_codex: str = ""
    interactions: list[Interaction] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "CanonicalSkill":
        return cls(
            name=str(d["name"]),
            description=str(d["description"]),
            body=str(d.get("body", "")),
            voices_required=int(d.get("voices_required", 1)),
            sub_skills=list(d.get("sub_skills") or []),
            runtime_notes_codex=str(d.get("runtime_notes_codex", "")),
            interactions=[Interaction.from_dict(x) for x in (d.get("interactions") or [])],
            tools_used=list(d.get("tools_used") or []),
        )

    def validate(self) -> list[str]:
        """Return a list of validation error strings (empty list = valid)."""
        errs: list[str] = []
        if not self.name:
            errs.append("name is required")
        if not self.description:
            errs.append("description is required")
        if not self.body:
            errs.append("body is required")
        if self.voices_required not in (1, 2):
            errs.append(f"voices_required must be 1 or 2, got {self.voices_required}")
        for i, ix in enumerate(self.interactions):
            if not ix.phase:
                errs.append(f"interactions[{i}].phase is required")
            if not ix.content:
                errs.append(f"interactions[{i}].content is required")
        return errs


# Pseudo-token table: source body uses {{TOKEN}}; sync engine rewrites runtime-appropriate.
TOOL_TOKEN_MAP = {
    "Read":      {"claude": "Read",     "codex": "read_file"},
    "Write":     {"claude": "Write",    "codex": "apply_patch"},
    "Edit":      {"claude": "Edit",     "codex": "apply_patch"},
    "MultiEdit": {"claude": "MultiEdit","codex": "apply_patch"},
    "Bash":      {"claude": "Bash",     "codex": "shell"},
    "Grep":      {"claude": "Grep",     "codex": "file_search"},
    "Glob":      {"claude": "Glob",     "codex": "file_search"},
    # MCP harness prefix — Claude long form vs Codex bare
    "TaskStart":   {"claude": "mcp__harness__task_start",   "codex": "task_start"},
    "TaskContext": {"claude": "mcp__harness__task_context", "codex": "task_context"},
    "TaskVerify":  {"claude": "mcp__harness__task_verify",  "codex": "task_verify"},
    "TaskClose":   {"claude": "mcp__harness__task_close",   "codex": "task_close"},
    "WriteHandoff":   {"claude": "mcp__harness__write_handoff",  "codex": "write_handoff"},
    "WriteDocSync":   {"claude": "mcp__harness__write_doc_sync", "codex": "write_doc_sync"},
    "WriteCriticQA":  {"claude": "mcp__harness__write_critic_qa","codex": "write_critic_qa"},
    # Env var rename
    "PluginRoot": {"claude": "CLAUDE_PLUGIN_ROOT", "codex": "HARNESS_PLUGIN_ROOT"},
}
