#!/usr/bin/env python3
"""Create an auto-runnable follow-up task for pending doc hygiene.

The prompt hook may surface pending hygiene while another task is active, but
agents should not mix cleanup into that task. This helper turns the ambient
queue into one dedicated task after the primary task closes.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (  # type: ignore
    TASK_DIR,
    ensure_task_scaffold,
    find_repo_root,
    now_iso,
    read_json_state,
)


PENDING_JSON = "doc/harness/.hygiene-pending.json"
LEGACY_PENDING_JSON = "doc/harness/.maintain-pending.json"
FOLLOWUP_TASK_ID = "TASK__hygiene-review-pending-docs"
AUTO_RUN_MAX_ITEMS = 25


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _read_pending(repo_root: str) -> list[dict[str, Any]]:
    pending = read_json_state(os.path.join(repo_root, PENDING_JSON))
    if not isinstance(pending, list):
        pending = read_json_state(os.path.join(repo_root, LEGACY_PENDING_JSON))
    if not isinstance(pending, list):
        return []
    return [item for item in pending if isinstance(item, dict) and item.get("path")]


def _sanitize_path(raw: Any) -> str:
    text = str(raw or "").replace("\n", " ").replace("\r", " ")
    text = re.sub(r"[\x00-\x1f\x7f]", "", text).strip()
    return text[:240]


def _item_line(item: dict[str, Any]) -> str:
    signals = item.get("signals") if isinstance(item.get("signals"), dict) else {}
    refs = signals.get("reference_count", "?")
    freshness = signals.get("freshness", "?")
    path = _sanitize_path(item.get("path"))
    return f"- `{path}` ({item.get('kind', 'review')}; refs={refs}; freshness={freshness})"


def _request_text(items: list[dict[str, Any]]) -> str:
    listed = "\n".join(_item_line(item) for item in items)
    return (
        "# Hygiene Follow-up Request\n\n"
        "Review the pending doc hygiene queue as its own task. Do not combine "
        "this cleanup with unrelated feature work.\n\n"
        "For each item, inspect the document and choose exactly one disposition: "
        "keep/update/archive/defer/reject. Apply low-risk documentation fixes "
        "directly; defer ambiguous product or historical decisions with a clear "
        "reason in the plan/task rationale.\n\n"
        "Pending items at task creation:\n"
        f"{listed}\n"
    )


def _plan_text(items: list[dict[str, Any]]) -> str:
    listed = "\n".join(_item_line(item) for item in items)
    return (
        "# PLAN\n\n"
        f"Task: {FOLLOWUP_TASK_ID}\n\n"
        "## Objective\n"
        "Resolve the pending doc hygiene review queue as a standalone cleanup task, "
        "keeping the attention scope separate from feature development.\n\n"
        "## Pending Items\n"
        f"{listed}\n\n"
        "## Approach\n"
        "1. Read `doc/harness/.hygiene-pending.json` and each listed document.\n"
        "2. For each item, classify it as keep, update, archive, defer, or reject.\n"
        "3. Apply only low-risk documentation hygiene changes in this task.\n"
        "4. Record every disposition in the plan/task rationale and clear or shrink the pending queue "
        "only for items that were actually resolved.\n"
        "5. Verify with the focused hygiene tests and a final queue check.\n\n"
        "## Verification\n"
        "- `uv run pytest tests/test_hygiene_contracts.py tests/test_prompt_memory.py -x --tb=short`\n"
        "- Inspect `doc/harness/.hygiene-pending.json` and recorded dispositions.\n"
    )


def _write_if_absent(path: Path, text: str) -> bool:
    if path.exists():
        return False
    _atomic_write_text(path, text)
    return True


def create_followup(repo_root: str | None = None) -> dict[str, Any]:
    root = repo_root or find_repo_root()
    items = _read_pending(root)
    if not items:
        return {
            "action": "none",
            "reason": "no pending hygiene items",
            "pending_count": 0,
        }

    task_dir = Path(root) / TASK_DIR / FOLLOWUP_TASK_ID
    scaffold = ensure_task_scaffold(str(task_dir), FOLLOWUP_TASK_ID, _request_text(items))
    written = [str(Path(path).relative_to(root)) for path in scaffold.get("created", []) if Path(path).is_absolute()]
    written += [path for path in scaffold.get("created", []) if not Path(path).is_absolute()]

    if _write_if_absent(task_dir / "PLAN.md", _plan_text(items)):
        written.append(str((task_dir / "PLAN.md").relative_to(root)))
    if _write_if_absent(
        task_dir / "FOLLOWUP.meta.json",
        json.dumps(
            {
                "created_at": now_iso(),
                "source": PENDING_JSON,
                "mode": "separate_task_by_default",
                "current_task_action": "triage_only",
                "auto_run": len(items) <= AUTO_RUN_MAX_ITEMS,
                "pending_count": len(items),
            },
            indent=2,
        )
        + "\n",
    ):
        written.append(str((task_dir / "FOLLOWUP.meta.json").relative_to(root)))

    auto_run = len(items) <= AUTO_RUN_MAX_ITEMS
    return {
        "action": "run_followup" if auto_run else "queued",
        "task_id": FOLLOWUP_TASK_ID,
        "task_dir": str(task_dir),
        "pending_count": len(items),
        "auto_run": auto_run,
        "written": sorted(set(written)),
        "next_action": (
            f"Run harness on {FOLLOWUP_TASK_ID} now, as the next standalone task."
            if auto_run
            else f"Queue {FOLLOWUP_TASK_ID}; pending hygiene count exceeds auto-run cap."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Schedule pending hygiene as a follow-up task")
    parser.add_argument("--repo-root", default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    result = create_followup(args.repo_root)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{result.get('action')}: {result.get('next_action') or result.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
