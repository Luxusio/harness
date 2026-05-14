#!/usr/bin/env python3
"""AC-005 sync engine MVP — canonical YAML → Claude SKILL.md + Codex SKILL.md.

Reads a canonical skill YAML/JSON (see canonical_schema.py for the schema),
emits two runtime-specific SKILL.md files atomically with content-hash headers.

Usage:
  python3 plugin/runtime-sync/transform_skill.py \\
    --canonical tests/runtime-sync/corpus/hello.skill.yaml --dry-run
  python3 plugin/runtime-sync/transform_skill.py \\
    --canonical tests/runtime-sync/corpus/hello.skill.yaml \\
    --emit-claude plugin/skills/hello/SKILL.md \\
    --emit-codex  plugin-codex/skills/hello/SKILL.md
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from canonical_schema import CanonicalSkill, TOOL_TOKEN_MAP  # type: ignore


def _load_canonical(path: Path) -> CanonicalSkill:
    """v1.5 MVP: parse JSON-form YAML (strict subset). PyYAML dep deferred."""
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        raise SystemExit(f"canonical at {path} is not valid JSON-form YAML: {e}")
    skill = CanonicalSkill.from_dict(data)
    errs = skill.validate()
    if errs:
        raise SystemExit(f"canonical validation failed: {errs}")
    return skill


def _substitute_tokens(body: str, runtime: str) -> str:
    """Replace {{TOKEN}} markers with runtime-appropriate strings."""
    def repl(m: re.Match) -> str:
        token = m.group(1)
        mapping = TOOL_TOKEN_MAP.get(token)
        if mapping is None:
            return m.group(0)
        return mapping[runtime]
    return re.sub(r"\{\{([A-Za-z_]+)\}\}", repl, body)


def _content_hash(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]


def _render_claude(skill: CanonicalSkill) -> str:
    out: list[str] = []
    body = _substitute_tokens(skill.body, "claude")
    fm_lines = ["---", f"name: {skill.name}", f"description: {skill.description}"]
    if skill.tools_used:
        tool_strs = [TOOL_TOKEN_MAP[t]["claude"] for t in skill.tools_used if t in TOOL_TOKEN_MAP]
        if tool_strs:
            fm_lines.append("allowed-tools: " + ", ".join(tool_strs))
    fm_lines.append("---")
    out.append("\n".join(fm_lines))
    out.append("")
    out.append("<!-- GENERATED — edit shared/skills/<name>.skill.yaml and re-emit; "
               "hand-edits are detected by plugin/runtime-sync/parity_check.py -->")
    out.append("")
    out.append(body.rstrip("\n"))
    if skill.sub_skills:
        out.append("")
        out.append("## Sub-skills (Skill chain)")
        out.append("")
        for sk in skill.sub_skills:
            out.append(f'- `Skill("harness:{sk}", "<task_id>")`')
    if skill.interactions:
        out.append("")
        out.append("## Interactions (AskUserQuestion call sites)")
        out.append("")
        for ix in skill.interactions:
            out.append(f"- Phase {ix.phase}: AskUserQuestion — {ix.content}")
            for opt in ix.options:
                marker = " (recommended)" if opt.lstrip().startswith(f"{ix.recommended})") else ""
                out.append(f"  - {opt}{marker}")
    if skill.voices_required == 2:
        out.append("")
        out.append("## Voices")
        out.append("")
        out.append("Dual-voice required — spawn Voice A and Voice B via `Agent(subagent_type=...)`.")
    body_render = "\n".join(out) + "\n"
    return f"<!-- canonical-hash: {_content_hash(body_render)} -->\n" + body_render


def _render_codex(skill: CanonicalSkill) -> str:
    out: list[str] = []
    body = _substitute_tokens(skill.body, "codex")
    fm_lines = ["---", f"name: {skill.name}", f"description: {skill.description}", "---"]
    out.append("\n".join(fm_lines))
    out.append("")
    out.append("<!-- GENERATED — edit shared/skills/<name>.skill.yaml and re-emit; "
               "hand-edits are detected by plugin/runtime-sync/parity_check.py -->")
    out.append("")
    if skill.runtime_notes_codex:
        out.append("> **Codex runtime notes** (delta from Claude):")
        out.append(">")
        for line in skill.runtime_notes_codex.splitlines():
            out.append(f"> {line}" if line else ">")
        out.append("")
    out.append(body.rstrip("\n"))
    if skill.sub_skills:
        out.append("")
        out.append("## Sub-skills (read inline)")
        out.append("")
        out.append("Codex has no `Skill()` primitive. For each sub-skill, read the SKILL.md and execute the methodology inline:")
        out.append("")
        for sk in skill.sub_skills:
            out.append(f"- `plugin-codex/skills/{sk}/SKILL.md` (Codex tree; falls back to `plugin/skills/{sk}/SKILL.md` if not ported yet)")
    if skill.interactions:
        out.append("")
        out.append("## Interactions (conversational asks)")
        out.append("")
        out.append("Codex has no structured AskUserQuestion. Emit the content + options to the user; read the reply from the next turn.")
        out.append("")
        for ix in skill.interactions:
            out.append(f"### Phase {ix.phase}")
            out.append("")
            out.append(f"Ask: {ix.content}")
            if ix.options:
                out.append("")
                for opt in ix.options:
                    marker = " (recommended)" if opt.lstrip().startswith(f"{ix.recommended})") else ""
                    out.append(f"- {opt}{marker}")
            out.append("")
    if skill.voices_required == 2:
        out.append("## Voices (degraded)")
        out.append("")
        out.append(
            "Source declares dual-voice; Codex has no `Agent` fan-out. v1.5 ships single-voice "
            "degraded variant. For dual-voice fidelity, run via Claude: `claude $/harness:" +
            skill.name + " <task>`."
        )
    body_render = "\n".join(out) + "\n"
    return f"<!-- canonical-hash: {_content_hash(body_render)} -->\n" + body_render


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main() -> int:
    p = argparse.ArgumentParser(description="Transform canonical skill YAML → Claude + Codex SKILL.md")
    p.add_argument("--canonical", required=True)
    p.add_argument("--emit-claude", default=None)
    p.add_argument("--emit-codex", default=None)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    canonical_path = Path(args.canonical)
    if not canonical_path.is_file():
        print(f"ERROR: canonical not found: {canonical_path}", file=sys.stderr)
        return 1
    skill = _load_canonical(canonical_path)
    claude_text = _render_claude(skill)
    codex_text = _render_codex(skill)
    if args.dry_run:
        sys.stdout.write("===== CLAUDE TARGET =====\n")
        sys.stdout.write(claude_text)
        sys.stdout.write("\n===== CODEX TARGET =====\n")
        sys.stdout.write(codex_text)
        return 0
    if args.emit_claude:
        _atomic_write(Path(args.emit_claude), claude_text)
        print(f"wrote {args.emit_claude}")
    if args.emit_codex:
        _atomic_write(Path(args.emit_codex), codex_text)
        print(f"wrote {args.emit_codex}")
    if not args.emit_claude and not args.emit_codex:
        print("ERROR: no --emit-claude or --emit-codex specified (use --dry-run for stdout)", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
