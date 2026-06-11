#!/usr/bin/env python3
"""Ordered task-pack state for multi-step harness requests.

This runner is deliberately smaller than goal_queue_runner.py. It does not pick
product scope or execute commands. It records the user's known ordered work,
lets the orchestrator claim one task at a time, and makes the next task
deterministic after each close.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


DEFAULT_STATE = Path("doc/harness/task-packs/current.json")
TERMINAL_STATUSES = {"closed", "blocked", "skipped"}


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def slugify(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return slug or "task"


def task_id_for_slug(slug: str) -> str:
    return f"TASK__{slugify(slug)}"


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
        Path(tmp).replace(path)
    except BaseException:
        try:
            Path(tmp).unlink()
        except OSError:
            pass
        raise


def save_state(path: Path, state: dict[str, Any]) -> None:
    state["updated_at"] = now_iso()
    atomic_write(path, json.dumps(state, indent=2, sort_keys=True) + "\n")


def load_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise SystemExit(f"task pack state not found: {path}")
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"task pack state must be JSON: {exc}") from exc
    if not isinstance(state, dict) or not isinstance(state.get("tasks"), list):
        raise SystemExit("task pack state missing tasks[]")
    return state


def event_path(state_path: Path) -> Path:
    return state_path.parent / "task-pack-events.jsonl"


def append_event(state_path: Path, event_type: str, **fields: Any) -> None:
    event = {"ts": now_iso(), "type": event_type}
    event.update({k: v for k, v in fields.items() if v not in (None, "")})
    path = event_path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, sort_keys=True) + "\n")


def parse_task(raw: str, index: int) -> dict[str, Any]:
    if ":" in raw:
        raw_slug, title = raw.split(":", 1)
        slug = slugify(raw_slug)
        title = title.strip()
    else:
        title = raw.strip()
        slug = f"task-{index:03d}-{slugify(title)[:36].strip('-')}"
    if not title:
        raise SystemExit("--task must be 'slug:title' or a non-empty title")
    return {
        "id": slug,
        "slug": slug,
        "title": title,
        "task_id": task_id_for_slug(slug),
        "status": "queued",
        "order": index,
        "reason": "",
        "updated_at": now_iso(),
    }


def refresh_status(state: dict[str, Any]) -> None:
    tasks = [item for item in state.get("tasks", []) if isinstance(item, dict)]
    statuses = [str(item.get("status") or "queued") for item in tasks]
    if statuses and all(status in TERMINAL_STATUSES for status in statuses):
        if any(status == "blocked" for status in statuses):
            state["status"] = "blocked"
        else:
            state["status"] = "done"
    elif state.get("status") != "stopped":
        state["status"] = "active"


def next_task(state: dict[str, Any]) -> dict[str, Any] | None:
    tasks = sorted(
        [item for item in state.get("tasks", []) if isinstance(item, dict)],
        key=lambda item: int(item.get("order") or 0),
    )
    for item in tasks:
        if str(item.get("status") or "queued") == "active":
            return item
    for item in tasks:
        if str(item.get("status") or "queued") == "queued":
            return item
    return None


def find_task(state: dict[str, Any], task_ref: str) -> dict[str, Any] | None:
    ref = slugify(task_ref)
    for item in state.get("tasks", []):
        if not isinstance(item, dict):
            continue
        values = {
            str(item.get("id") or ""),
            str(item.get("slug") or ""),
            str(item.get("task_id") or ""),
        }
        if task_ref in values or ref in {slugify(value) for value in values if value}:
            return item
    return None


def init(args: argparse.Namespace) -> int:
    if args.state.exists() and not args.force:
        raise SystemExit(f"refusing to overwrite existing task pack: {args.state}")
    if not args.task:
        raise SystemExit("at least one --task is required")
    state = {
        "version": 1,
        "status": "active",
        "pack_id": args.pack_id or slugify(args.goal or "task-pack"),
        "goal": args.goal,
        "created_at": now_iso(),
        "updated_at": now_iso(),
        "tasks": [parse_task(raw, index) for index, raw in enumerate(args.task, start=1)],
    }
    save_state(args.state, state)
    append_event(args.state, "initialized", pack_id=state["pack_id"], tasks=len(state["tasks"]))
    print(f"task pack initialized: {args.state} ({len(state['tasks'])} tasks)")
    return 0


def print_status(state: dict[str, Any]) -> None:
    refresh_status(state)
    counts: dict[str, int] = {}
    for item in state.get("tasks", []):
        status = str(item.get("status") or "queued")
        counts[status] = counts.get(status, 0) + 1
    print(f"status: {state.get('status')}")
    print(f"pack_id: {state.get('pack_id')}")
    print(f"goal: {state.get('goal')}")
    print("tasks: " + ", ".join(f"{key}={counts[key]}" for key in sorted(counts)))
    item = next_task(state)
    if item:
        print(f"next: {item['task_id']} - {item['title']} ({item['status']})")
    else:
        print("next: none")


def status(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    print_status(state)
    return 0


def next_cmd(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    refresh_status(state)
    item = next_task(state)
    if not item:
        print("next: none")
        return 2 if state.get("status") != "done" else 0
    print(f"next: {item['task_id']} - {item['title']}")
    print(f"slug: {item['slug']}")
    print(
        "prompt: "
        f"/goal task-pack {state.get('pack_id')} "
        f"task {item['slug']}: {item['title']}"
    )
    return 0


def claim_next(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    item = next_task(state)
    if not item:
        print("claim-next: none")
        return 2
    if str(item.get("status") or "") != "active":
        item["status"] = "active"
        item["started_at"] = now_iso()
        item["updated_at"] = now_iso()
    save_state(args.state, state)
    append_event(args.state, "task_claimed", pack_id=state.get("pack_id"), task_id=item.get("task_id"))
    print(f"claimed: {item['task_id']} - {item['title']}")
    return 0


def close(args: argparse.Namespace) -> int:
    state = load_state(args.state)
    item = find_task(state, args.task)
    if item is None:
        raise SystemExit(f"unknown task in pack: {args.task}")
    item["status"] = args.result
    item["closed_at"] = now_iso()
    item["updated_at"] = now_iso()
    if args.reason:
        item["reason"] = args.reason
    refresh_status(state)
    save_state(args.state, state)
    append_event(
        args.state,
        "task_closed",
        pack_id=state.get("pack_id"),
        task_id=item.get("task_id"),
        result=args.result,
    )
    print(f"closed: {item['task_id']} {args.result}")
    nxt = next_task(state)
    if nxt:
        print(f"next: {nxt['task_id']} - {nxt['title']}")
    else:
        print("next: none")
    return 0 if args.result == "closed" else 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Ordered harness task-pack runner")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init")
    p_init.add_argument("--goal", required=True)
    p_init.add_argument("--pack-id", default="")
    p_init.add_argument("--task", action="append", default=[])
    p_init.add_argument("--force", action="store_true")
    p_init.set_defaults(func=init)

    p_status = sub.add_parser("status")
    p_status.set_defaults(func=status)

    p_next = sub.add_parser("next")
    p_next.set_defaults(func=next_cmd)

    p_claim = sub.add_parser("claim-next")
    p_claim.set_defaults(func=claim_next)

    p_close = sub.add_parser("close")
    p_close.add_argument("--task", required=True)
    p_close.add_argument("--result", choices=["closed", "blocked", "skipped"], default="closed")
    p_close.add_argument("--reason", default="")
    p_close.set_defaults(func=close)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
