#!/usr/bin/env python3
"""Create or update a durable REQ scaffold for observable behavior."""
from __future__ import annotations

import argparse
import os
import re
import tempfile
from datetime import datetime, timezone

from req_detector import detect_req_need


def _slugify(value: str) -> str:
    words = re.findall(r"[a-z0-9]+", value.lower())
    return "-".join(words[:8]) or "observable-behavior"


def _bullet_lines(value: str) -> str:
    items = [line.strip(" -\t") for line in value.splitlines() if line.strip(" -\t")]
    if not items and value.strip():
        items = [value.strip()]
    return "\n".join(f"- {item}" for item in items) if items else "- TBD"


def render_req_doc(title: str, intent: str, observable_behaviors: str,
                   verification_cues: str, non_goals: str = "",
                   source: str = "", status: str = "accepted") -> str:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return (
        f"# REQ - {title}\n\n"
        f"status: {status.strip() or 'accepted'}\n\n"
        "## Intent\n"
        f"{intent.strip() or 'TBD'}\n\n"
        "## Observable Behavior\n"
        f"{_bullet_lines(observable_behaviors)}\n\n"
        "## Acceptance Signals\n"
        f"{_bullet_lines(observable_behaviors)}\n\n"
        "## Verification Cues\n"
        f"{_bullet_lines(verification_cues)}\n\n"
        "## Non-Goals\n"
        f"{_bullet_lines(non_goals) if non_goals.strip() else '- TBD'}\n\n"
        "## Source\n"
        f"- created: {today}\n"
        f"- source: {source.strip() or 'harness auto REQ scaffold'}\n"
    )


def create_req_doc(repo_root: str, area: str, slug: str, intent: str,
                   observable_behaviors: str, verification_cues: str,
                   non_goals: str = "", source: str = "",
                   append: bool = True, status: str = "accepted") -> str:
    area = _slugify(area)
    slug = _slugify(slug)
    rel = os.path.join("doc", area, f"REQ__{slug}.md")
    path = os.path.join(repo_root, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    title = slug.replace("-", " ").title()
    new_body = render_req_doc(title, intent, observable_behaviors, verification_cues, non_goals, source, status)

    if append and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read().rstrip()
        body = (
            existing
            + "\n\n## Auto-Scaffolded Update\n\n"
            + new_body.split("\n\n", 1)[1].rstrip()
            + "\n"
        )
    else:
        body = new_body

    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".REQ.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return rel.replace(os.sep, "/")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create or update a durable REQ scaffold.")
    parser.add_argument("--repo-root", default=os.getcwd())
    parser.add_argument("--task-id", default="")
    parser.add_argument("--area", default="")
    parser.add_argument("--slug", default="")
    parser.add_argument("--intent", required=True)
    parser.add_argument("--observable-behavior", required=True)
    parser.add_argument("--verification-cues", required=True)
    parser.add_argument("--non-goals", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--status", default="accepted",
                        help="REQ frontmatter status: accepted (default) | candidate. "
                             "critic-document retrospective writes use 'candidate'.")
    parser.add_argument("--path", action="append", default=[])
    args = parser.parse_args()

    detected = detect_req_need(texts=[args.intent, args.observable_behavior, args.verification_cues], paths=args.path)
    area = args.area or detected.get("suggested_area") or "ui"
    slug = args.slug or detected.get("suggested_slug") or "observable-behavior"
    source = args.source or (f"task: {args.task_id}" if args.task_id else "harness auto REQ scaffold")
    rel = create_req_doc(
        args.repo_root,
        area,
        slug,
        args.intent,
        args.observable_behavior,
        args.verification_cues,
        args.non_goals,
        source,
        status=args.status,
    )
    print(rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
