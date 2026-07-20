#!/usr/bin/env python3
"""Migrate pre-native Goal queue artifacts in an existing repository."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any


LEGACY_STATE = Path("doc/harness/autopilot.yaml")
GOAL_QUEUE_STATE = Path("doc/harness/goal-queue.json")
ROUTING_MARKER = "<!-- harness:routing-injected -->"
DEFAULT_AGENT_RE = re.compile(r"^- Default (agent|operating agent) is harness\s*$")

ROUTING_BLOCK = """## Harness routing
<!-- harness:routing-injected -->
- Run the full cycle (plan -> develop -> verify -> close) -> native `/goal` for explicit goals, or let the agent open/resume a harness task for plain repo-mutating requests
- Bootstrap harness in a new project / repair existing -> `Skill(harness:setup)`
- Plan-only requests -> sync/create Goal and stop after the internal plan phase if the user explicitly asks not to implement
- Implement an approved PLAN.md / develop only -> resume the active Goal child task through the internal develop path
- Contract drift / post-upgrade cleanup -> continuous maintenance flow in the active/next Goal child task
- Read-only question or explanation -> answer directly, no skill

### Durable Decision Documentation Gate

A user-stated durable decision is not handled until it is documented under `doc/`.
If the user establishes, corrects, or confirms a lasting product, design,
architecture, domain, workflow, or implementation rule, update the matching
`doc/` file before finalizing. Conversation history is not durable memory. If
no matching document exists, create one under the appropriate `doc/` area; if no
doc is needed, keep the no-doc rationale in the plan/task rationale.
"""


def now_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


def atomic_write(path: Path, text: str) -> None:
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


def read_json_compatible(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"legacy goal queue state must be JSON-compatible YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit("legacy goal queue state must be an object")
    return data


def legacy_task_id_target(repo_root: Path, task_id: str) -> tuple[str, str]:
    if not task_id.startswith("TASK__autopilot-"):
        return task_id, "unchanged"
    task_dir = repo_root / "doc" / "harness" / "tasks" / task_id
    if task_dir.exists():
        return task_id, "preserved_existing_task_dir"
    return task_id.replace("TASK__autopilot-", "TASK__goal-queue-"), "rewritten_missing_task_dir"


def rewrite_legacy_task_ids(value: Any, repo_root: Path, policy_counts: dict[str, int]) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            str_key = str(key)
            if str_key == "task_id" and isinstance(item, str):
                rewritten, policy = legacy_task_id_target(repo_root, item)
                out[str_key] = rewritten
                if policy != "unchanged":
                    out.setdefault("legacy_task_id", item)
                    policy_counts[policy] = policy_counts.get(policy, 0) + 1
                continue
            out[str_key] = rewrite_legacy_task_ids(item, repo_root, policy_counts)
        return out
    if isinstance(value, list):
        return [rewrite_legacy_task_ids(item, repo_root, policy_counts) for item in value]
    if isinstance(value, str):
        return value.replace("TASK__autopilot-", "TASK__goal-queue-")
    return value


def migrate_state(repo_root: Path, *, force: bool = False, archive: bool = True) -> str:
    legacy = repo_root / LEGACY_STATE
    target = repo_root / GOAL_QUEUE_STATE
    if not legacy.exists():
        return "state: no legacy file"
    if target.exists() and not force:
        return "state: goal-queue state already exists"

    policy_counts: dict[str, int] = {}
    state = rewrite_legacy_task_ids(read_json_compatible(legacy), repo_root, policy_counts)
    if not isinstance(state, dict):
        raise SystemExit("legacy goal queue state must migrate to an object")
    state.setdefault("version", 1)
    state["migrated_from"] = str(LEGACY_STATE)
    state["migrated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if policy_counts:
        state["task_id_migration"] = policy_counts
    atomic_write(target, json.dumps(state, indent=2, sort_keys=True) + "\n")

    if archive:
        archive_dir = legacy.parent / "legacy"
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = archive_dir / f"goal-queue-pre-native-state.{now_stamp()}.json"
        os.replace(legacy, archive_path)
        return f"state: migrated to {GOAL_QUEUE_STATE}; archived legacy at {archive_path.relative_to(repo_root)}"
    return f"state: migrated to {GOAL_QUEUE_STATE}; legacy left in place"


def routing_block_bounds(lines: list[str]) -> tuple[int, int] | None:
    start = None
    for idx, line in enumerate(lines):
        if line.strip() == "## Harness routing" or line.strip() == ROUTING_MARKER:
            start = idx
            if line.strip() == ROUTING_MARKER and idx > 0 and lines[idx - 1].strip() == "## Harness routing":
                start = idx - 1
            break
    if start is None:
        return None
    end = len(lines)
    for idx in range(start + 1, len(lines)):
        if lines[idx].startswith("## ") and lines[idx].strip() != "## Harness routing":
            end = idx
            break
    return start, end


def migrate_project_routing(repo_root: Path, project_doc: str = "CLAUDE.md") -> str:
    path = repo_root / project_doc
    if not path.exists():
        return f"routing: {project_doc} absent"
    original = path.read_text(encoding="utf-8")
    lines = [
        line
        for line in original.splitlines()
        if not DEFAULT_AGENT_RE.match(line.strip())
    ]
    bounds = routing_block_bounds(lines)
    replacement = ROUTING_BLOCK.rstrip("\n").splitlines()
    if bounds is None:
        if lines and lines[-1].strip():
            lines.extend([""])
        lines.extend(replacement)
    else:
        start, end = bounds
        prefix = lines[:start]
        suffix = lines[end:]
        while prefix and not prefix[-1].strip():
            prefix.pop()
        while suffix and not suffix[0].strip():
            suffix.pop(0)
        lines = prefix + [""] + replacement + ([""] + suffix if suffix else [])
    migrated = "\n".join(lines).rstrip() + "\n"
    if migrated == original:
        return "routing: already current"
    atomic_write(path, migrated)
    return f"routing: updated {project_doc}"


def migrate_claude_routing(repo_root: Path) -> str:
    """Backward-compatible wrapper for callers that target Claude Code."""
    return migrate_project_routing(repo_root, "CLAUDE.md")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy Goal queue artifacts")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--keep-legacy-state", action="store_true")
    parser.add_argument("--state-only", action="store_true")
    parser.add_argument("--routing-only", action="store_true")
    parser.add_argument("--project-doc", choices=("CLAUDE.md", "AGENTS.md"), default="CLAUDE.md")
    args = parser.parse_args(argv)

    repo = args.repo.resolve()
    messages: list[str] = []
    if not args.routing_only:
        messages.append(
            migrate_state(repo, force=args.force, archive=not args.keep_legacy_state)
        )
    if not args.state_only:
        messages.append(migrate_project_routing(repo, args.project_doc))
    for message in messages:
        print(message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
