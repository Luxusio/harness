#!/usr/bin/env python3
"""UserPromptSubmit hook — inject compact harness state on every user prompt.

Emits a short ``[harness-context]`` block on stdout when a harness task is
active, so agents don't burn a turn re-reading ``TASK_STATE.yaml`` /
``CHECKS.yaml`` to orient themselves in fix rounds.

Pending hygiene is intentionally not injected here. The post-close
`hygiene_followup.py` scheduler turns that queue into a separate task so prompt
hooks do not dilute the active task's attention scope.

Output is silent when no active task exists. Total length is hard-capped at
400 chars for harness-context; excess truncates with ``…``. The ``|| true``
wrapper in ``hooks.json`` (C-12 fail-safe) keeps the session healthy on any crash.
"""
from __future__ import annotations

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from _lib import (  # type: ignore
        read_hook_input,
        read_state,
        find_repo_root,
        TASK_DIR,
        _log_gate_error,
        resolve_active_task_dir,
        runtime_is_stale,
    )
except Exception:
    sys.exit(0)

try:
    from runbook_memory import render_prompt_block as _render_runbook_block  # type: ignore
except Exception:
    _render_runbook_block = None


MAX_BLOCK_CHARS = 400
PREFIX = "[harness-context]"
RESTORE_INJECT_CAP = 1400
RESTORE_TOUCHED_CAP = 5
RESTORE_COMMIT_CAP = 3
RESTORE_ARTIFACTS = ("HANDOFF.md", "CRITIC__qa.md", "DOC_SYNC.md", "BLOCKED.md")

_STALE_SKIP_SUFFIXES = (".pyc", ".pyo", ".pyd")
_STALE_SKIP_FRAGMENTS = ("__pycache__/", "/.DS_Store", ".swp", ".swo")
_STALE_PATH_CAP = 50   # bound mtime scan cost on the hook hot path
_AC_CAP = 3
_NOTE_CAP = 2
_DOC_FILE_CAP = 100    # cap frontmatter scan so a huge doc/ can't blow timeout
_DOC_DEPTH_CAP = 2     # walk doc/ root + 2 subdirs only
_AC_TERMINAL = {"passed", "deferred"}
_TITLE_MAX = 24


def _stale_skip(rel: str) -> bool:
    if not rel:
        return True
    for suf in _STALE_SKIP_SUFFIXES:
        if rel.endswith(suf):
            return True
    for frag in _STALE_SKIP_FRAGMENTS:
        if frag in rel:
            return True
    return False


def _find_active_task_dir(repo_root: str) -> str:
    td = resolve_active_task_dir(repo_root)
    return td if td and os.path.isdir(td) else ""


def _runtime_is_stale(task_dir: str, touched: list, repo_root: str) -> bool:
    return runtime_is_stale(task_dir)[0]


_CHECKS_ID_RE = re.compile(r"^-\s+id:\s*(\S+)", re.MULTILINE)
_CHECKS_BLOCK_RE = re.compile(r"^-\s+id:\s*", re.MULTILINE)


def _open_acs(task_dir: str) -> "tuple[list[tuple[str, str]], int]":
    """Return ``(non_terminal_acs, reopen_total)``."""
    checks_path = os.path.join(task_dir, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return [], 0
    try:
        text = open(checks_path, encoding="utf-8").read()
    except OSError:
        return [], 0
    blocks: list = []
    current: list = []
    for line in text.splitlines():
        if _CHECKS_BLOCK_RE.match(line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    out: list = []
    reopen_total = 0
    for block in blocks:
        m_id = _CHECKS_ID_RE.match(block)
        if not m_id:
            continue
        m_status = re.search(r"^\s+status:\s*(\S+)", block, re.MULTILINE)
        status = (m_status.group(1) if m_status else "open").strip()
        if status in _AC_TERMINAL:
            continue
        m_title = re.search(r'^\s+title:\s*"?(.*?)"?\s*$', block, re.MULTILINE)
        title = (m_title.group(1) if m_title else "").strip().strip('"').strip("'")
        if len(title) > _TITLE_MAX:
            title = title[: _TITLE_MAX - 1] + "…"
        m_reopen = re.search(r"^\s+reopen_count:\s*(\d+)", block, re.MULTILINE)
        if m_reopen:
            try:
                reopen_total += int(m_reopen.group(1))
            except ValueError:
                pass
        out.append((m_id.group(1), title))
        if len(out) >= _AC_CAP:
            break
    return out, reopen_total


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)^---\s*\n", re.MULTILINE | re.DOTALL)
_FRESHNESS_RE = re.compile(r"^\s*freshness:\s*(\S+)", re.MULTILINE)


def _suspect_notes(repo_root: str) -> list:
    """Walk doc/ root + capped depth/files, list up to _NOTE_CAP suspect notes."""
    doc_root = os.path.join(repo_root, "doc")
    if not os.path.isdir(doc_root):
        return []
    out: list = []
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(doc_root):
        rel_dir = os.path.relpath(dirpath, doc_root)
        depth = 0 if rel_dir == "." else rel_dir.count(os.sep) + 1
        if depth > _DOC_DEPTH_CAP:
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith(".md"):
                continue
            if scanned >= _DOC_FILE_CAP:
                return out
            scanned += 1
            path = os.path.join(dirpath, fn)
            try:
                with open(path, encoding="utf-8") as f:
                    head = f.read(600)
            except OSError:
                continue
            m = _FRONTMATTER_RE.match(head)
            if not m:
                continue
            m_fresh = _FRESHNESS_RE.search(m.group(1))
            if not m_fresh:
                continue
            if m_fresh.group(1).strip() == "suspect":
                out.append(os.path.relpath(path, repo_root))
                if len(out) >= _NOTE_CAP:
                    return out
    return out


def _truncate(block: str) -> str:
    if len(block) <= MAX_BLOCK_CHARS:
        return block
    return block[: MAX_BLOCK_CHARS - 2].rstrip() + " …"


def _build_block(task_dir: str, repo_root: str) -> str:
    st = read_state(task_dir)
    if not st:
        return ""
    task_id = st.get("task_id") or os.path.basename(task_dir)
    status = st.get("status") or "unknown"
    verdict = (st.get("runtime_verdict") or "pending").upper()
    touched = st.get("touched_paths") or []
    stale = _runtime_is_stale(task_dir, touched, repo_root) and verdict == "PASS"

    pieces: list = [PREFIX, f"task={task_id}", f"status={status}"]
    verdict_piece = f"verdict={verdict}"
    if stale:
        verdict_piece += " stale"
    pieces.append(verdict_piece)

    acs, reopen_total = _open_acs(task_dir)
    if acs:
        ac_strs = [f"{ac_id}:{title}" if title else ac_id for ac_id, title in acs]
        summary = "open=" + ",".join(ac_strs)
        if reopen_total > 0:
            summary += f" ⚠reopened={reopen_total}"
        pieces.append(summary)

    suspects = _suspect_notes(repo_root)
    if suspects:
        pieces.append("suspect=" + ",".join(suspects))

    block = " ".join(pieces)
    return _truncate(block)


# ── AC-014: hygiene-review injection ─────────────────────────────────────

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")
_SYSTEM_REMINDER_RE = re.compile(r"</?system-reminder[^>]*>", re.IGNORECASE)


def _sanitize_path(path: str) -> str:
    """Sanitize a path for injection into system-reminder output.

    Removes control chars, newlines, and system-reminder tag fragments.
    """
    # Strip control chars and newlines
    cleaned = _CONTROL_CHAR_RE.sub("", path)
    # Escape system-reminder tags
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return cleaned.strip()


def _sanitize_prompt_text(text: str) -> str:
    cleaned = _CONTROL_CHAR_RE.sub(" ", str(text or ""))
    cleaned = _SYSTEM_REMINDER_RE.sub("[SANITIZED]", cleaned)
    return " ".join(cleaned.split()).strip()


def _first_meaningful_line(path: str, cap: int = 180) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = _sanitize_prompt_text(line)
                if not line or line.startswith("#"):
                    continue
                return line[:cap]
    except OSError:
        return ""
    return ""


def _recent_commits(repo_root: str) -> list[str]:
    import subprocess
    try:
        r = subprocess.run(
            ["git", "log", f"-{RESTORE_COMMIT_CAP}", "--oneline"],
            cwd=repo_root, capture_output=True, text=True, timeout=2,
        )
    except Exception:
        return []
    if r.returncode != 0:
        return []
    return [_sanitize_prompt_text(line)[:120] for line in r.stdout.splitlines() if line.strip()]


def _build_restore_block(task_dir: str, repo_root: str) -> str:
    """Build a capped resume digest for the active task."""
    st = read_state(task_dir)
    if not st:
        return ""
    lines = ["<system-reminder>[harness-restore]"]
    touched = [
        _sanitize_path(str(p)) for p in (st.get("touched_paths") or [])[:RESTORE_TOUCHED_CAP]
        if str(p).strip()
    ]
    if touched:
        lines.append("recent touched: " + ", ".join(touched))

    artifact_lines: list[str] = []
    for name in RESTORE_ARTIFACTS:
        snippet = _first_meaningful_line(os.path.join(task_dir, name))
        if snippet:
            artifact_lines.append(f"{name}: {snippet}")
    if artifact_lines:
        lines.append("latest artifacts:")
        lines.extend(f"  - {line}" for line in artifact_lines[:3])

    commits = _recent_commits(repo_root)
    if commits:
        lines.append("recent commits:")
        lines.extend(f"  - {line}" for line in commits)

    if len(lines) == 1:
        return ""
    lines.append("</system-reminder>")
    block = "\n".join(lines)
    if len(block) > RESTORE_INJECT_CAP:
        block = block[: RESTORE_INJECT_CAP - 22].rstrip() + "\n...truncated\n</system-reminder>"
    return block


def main() -> int:
    read_hook_input()
    repo_root = find_repo_root()
    task_dir = _find_active_task_dir(repo_root)

    output_parts = []

    # Harness context block (existing behavior)
    if task_dir:
        block = _build_block(task_dir, repo_root)
        if block:
            output_parts.append(block)
        restore_block = _build_restore_block(task_dir, repo_root)
        if restore_block:
            output_parts.append(restore_block)

    # Approved runbooks + pending candidates are repo-local execution memory.
    # They are advisory and capped inside runbook_memory.py.
    if _render_runbook_block is not None:
        runbook_block = _render_runbook_block(repo_root)
        if runbook_block:
            output_parts.append(runbook_block)

    if output_parts:
        sys.stdout.write("\n".join(output_parts))
        sys.stdout.flush()

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except Exception as exc:
        try:
            _log_gate_error(exc, "prompt_memory")
        except Exception:
            pass
        sys.exit(0)
