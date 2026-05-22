#!/usr/bin/env python3
"""Small runbook memory helper for harness projects.

Approved runbooks are concise, repo-local execution recipes that are safe to
surface in prompt context. Candidates are unapproved discoveries waiting for a
maintain review.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNBOOKS_REL = "doc/harness/runbooks.yaml"
CANDIDATES_REL = "doc/harness/runbook_candidates.yaml"
PROMPT_CAP = 1800
ITEM_CAP = 4
GOTCHA_CAP = 2

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SYSTEM_REMINDER_RE = re.compile(r"</?system-reminder[^>]*>", re.IGNORECASE)
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|secret|token|api[_-]?key|private[_-]?key|authorization)\b"
    r"\s*[:=]\s*\S+|bearer\s+[a-z0-9._~+/-]{12,}"
)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def find_repo_root(start: str | None = None) -> str:
    cur = Path(start or os.getcwd()).resolve()
    for candidate in (cur, *cur.parents):
        if (candidate / ".git").exists() or (candidate / "doc" / "harness").exists():
            return str(candidate)
    return str(cur)


def sanitize_text(value: Any, *, max_len: int = 180) -> str:
    text = str(value or "")
    text = _CONTROL_CHAR_RE.sub(" ", text)
    text = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", text)
    text = " ".join(text.split())
    if len(text) > max_len:
        text = text[: max_len - 1].rstrip() + "…"
    return text


def contains_secret(value: Any) -> bool:
    if isinstance(value, dict):
        return any(contains_secret(v) for v in value.values())
    if isinstance(value, list):
        return any(contains_secret(v) for v in value)
    return bool(_SECRET_RE.search(str(value or "")))


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _parse_scalar(raw: str) -> str:
    raw = raw.strip()
    if not raw:
        return ""
    if raw[0] in ("\"", "'"):
        try:
            return str(json.loads(raw))
        except Exception:
            return raw.strip("\"'")
    return raw


def _load_root_map(path: Path, root_key: str) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    if text.startswith("{"):
        data = json.loads(text)
        section = data.get(root_key, {})
        return section if isinstance(section, dict) else {}

    out: dict[str, dict[str, Any]] = {}
    current_id = ""
    current_list_key = ""
    in_root = False
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if not line.startswith(" "):
            in_root = line.strip() == f"{root_key}:"
            current_id = ""
            current_list_key = ""
            continue
        if not in_root:
            continue
        if line.startswith("  ") and not line.startswith("    ") and line.rstrip().endswith(":"):
            current_id = line.strip()[:-1]
            out.setdefault(current_id, {})
            current_list_key = ""
            continue
        if not current_id:
            continue
        if line.startswith("    ") and not line.startswith("      "):
            body = line.strip()
            if body.endswith(":"):
                current_list_key = body[:-1]
                out[current_id].setdefault(current_list_key, [])
            elif ":" in body:
                key, raw = body.split(":", 1)
                out[current_id][key.strip()] = _parse_scalar(raw)
                current_list_key = ""
            continue
        if line.startswith("      - ") and current_list_key:
            out[current_id].setdefault(current_list_key, []).append(_parse_scalar(line.strip()[2:]))
    return out


def _dump_root_map(root_key: str, items: dict[str, dict[str, Any]]) -> str:
    lines = [f"{root_key}:"]
    for item_id in sorted(items):
        item = items[item_id]
        lines.append(f"  {sanitize_text(item_id, max_len=80)}:")
        for key in ("description", "command", "source_task", "learned_at", "verified_at"):
            if item.get(key):
                lines.append(f"    {key}: {_quote(sanitize_text(item[key], max_len=500))}")
        gotchas = item.get("gotchas") or []
        if gotchas:
            lines.append("    gotchas:")
            for gotcha in gotchas[:10]:
                lines.append(f"      - {_quote(sanitize_text(gotcha, max_len=500))}")
    return "\n".join(lines) + "\n"


def load_runbooks(repo_root: str | None = None) -> dict[str, dict[str, Any]]:
    root = Path(repo_root or find_repo_root())
    return _load_root_map(root / RUNBOOKS_REL, "runbooks")


def load_candidates(repo_root: str | None = None) -> dict[str, dict[str, Any]]:
    root = Path(repo_root or find_repo_root())
    return _load_root_map(root / CANDIDATES_REL, "candidates")


def save_runbooks(repo_root: str, runbooks: dict[str, dict[str, Any]]) -> None:
    _atomic_write(Path(repo_root) / RUNBOOKS_REL, _dump_root_map("runbooks", runbooks))


def save_candidates(repo_root: str, candidates: dict[str, dict[str, Any]]) -> None:
    path = Path(repo_root) / CANDIDATES_REL
    if candidates:
        _atomic_write(path, _dump_root_map("candidates", candidates))
    elif path.exists():
        path.unlink()


def add_candidate(repo_root: str, item_id: str, description: str, command: str,
                  gotchas: list[str] | None = None, source_task: str = "") -> dict[str, Any]:
    item = {
        "description": sanitize_text(description, max_len=500),
        "command": sanitize_text(command, max_len=500),
        "source_task": sanitize_text(source_task, max_len=160),
        "learned_at": now_iso(),
        "gotchas": [sanitize_text(g, max_len=500) for g in (gotchas or []) if sanitize_text(g)],
    }
    if contains_secret(item):
        raise ValueError("candidate contains secret-like content; redact it before persisting")
    clean_id = sanitize_text(item_id, max_len=80)
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$", clean_id):
        raise ValueError("id must contain only letters, numbers, dot, underscore, or dash")
    candidates = load_candidates(repo_root)
    candidates[clean_id] = item
    save_candidates(repo_root, candidates)
    return item


def approve_candidate(repo_root: str, item_id: str) -> dict[str, Any]:
    candidates = load_candidates(repo_root)
    if item_id not in candidates:
        raise KeyError(f"candidate not found: {item_id}")
    item = candidates.pop(item_id)
    item["verified_at"] = now_iso()
    runbooks = load_runbooks(repo_root)
    runbooks[item_id] = item
    save_runbooks(repo_root, runbooks)
    save_candidates(repo_root, candidates)
    return item


def skip_candidate(repo_root: str, item_id: str) -> None:
    candidates = load_candidates(repo_root)
    if item_id not in candidates:
        raise KeyError(f"candidate not found: {item_id}")
    candidates.pop(item_id)
    save_candidates(repo_root, candidates)


def render_prompt_block(repo_root: str | None = None) -> str:
    root = repo_root or find_repo_root()
    runbooks = load_runbooks(root)
    candidates = load_candidates(root)
    if not runbooks and not candidates:
        return ""

    lines = ["<system-reminder>[harness-runbooks]"]
    if runbooks:
        lines.append("approved:")
        for item_id in sorted(runbooks)[:ITEM_CAP]:
            item = runbooks[item_id]
            desc = sanitize_text(item.get("description", ""), max_len=90)
            cmd = sanitize_text(item.get("command", ""), max_len=160)
            lines.append(f"  - {sanitize_text(item_id, max_len=60)}: {desc} | `{cmd}`")
            for gotcha in (item.get("gotchas") or [])[:GOTCHA_CAP]:
                lines.append(f"    gotcha: {sanitize_text(gotcha, max_len=120)}")
        if len(runbooks) > ITEM_CAP:
            lines.append(f"  ...and {len(runbooks) - ITEM_CAP} more approved runbook(s)")
    if candidates:
        sample = ", ".join(sanitize_text(k, max_len=40) for k in sorted(candidates)[:ITEM_CAP])
        suffix = f" (+{len(candidates) - ITEM_CAP} more)" if len(candidates) > ITEM_CAP else ""
        lines.append(f"pending candidates: {sample}{suffix}. Fold into the active/next harness task; approve/defer/skip through close-time Self-Healing Candidates.")
    lines.append("</system-reminder>")
    block = "\n".join(lines)
    if len(block) > PROMPT_CAP:
        block = block[: PROMPT_CAP - 22].rstrip() + "\n...truncated\n</system-reminder>"
    return block


def _print_list(repo_root: str) -> None:
    runbooks = load_runbooks(repo_root)
    candidates = load_candidates(repo_root)
    print(f"runbooks: {len(runbooks)}")
    for item_id, item in sorted(runbooks.items()):
        print(f"  - {item_id}: {item.get('description', '')}")
    print(f"candidates: {len(candidates)}")
    for item_id, item in sorted(candidates.items()):
        print(f"  - {item_id}: {item.get('description', '')}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage harness runbook memory")
    parser.add_argument("--repo-root", default=None)
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add-candidate")
    p_add.add_argument("--id", required=True)
    p_add.add_argument("--description", required=True)
    p_add.add_argument("--command", required=True)
    p_add.add_argument("--gotcha", action="append", default=[])
    p_add.add_argument("--source-task", default="")

    p_approve = sub.add_parser("approve")
    p_approve.add_argument("id")

    p_skip = sub.add_parser("skip")
    p_skip.add_argument("id")

    sub.add_parser("list")
    sub.add_parser("render")

    args = parser.parse_args(argv)
    repo_root = find_repo_root(args.repo_root)
    try:
        if args.cmd == "add-candidate":
            add_candidate(repo_root, args.id, args.description, args.command,
                          gotchas=args.gotcha, source_task=args.source_task)
            print(f"candidate added: {args.id}")
        elif args.cmd == "approve":
            approve_candidate(repo_root, args.id)
            print(f"candidate approved: {args.id}")
        elif args.cmd == "skip":
            skip_candidate(repo_root, args.id)
            print(f"candidate skipped: {args.id}")
        elif args.cmd == "list":
            _print_list(repo_root)
        elif args.cmd == "render":
            block = render_prompt_block(repo_root)
            if block:
                print(block)
    except (KeyError, ValueError) as exc:
        print(f"runbook_memory: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
