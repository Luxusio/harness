#!/usr/bin/env python3
"""Create or update a durable REQ scaffold for observable behavior."""
from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from datetime import datetime, timezone

from req_detector import detect_req_need

ORIGINS = {"user_explicit", "user_confirmed", "derived", "assumption", "proposal", "unattributed"}
USER_ORIGINS = {"user_explicit", "user_confirmed"}


def _requirements(items, observable_behaviors, source, status):
    if status not in {"accepted", "candidate"}:
        raise ValueError("REQ status must be accepted or candidate")
    if items is not None and observable_behaviors.strip():
        raise ValueError("supply classified requirements or legacy behavior text, not two competing claim lists")
    if items is None:
        if status == "accepted":
            raise ValueError("accepted REQ requires classified per-item source evidence; free text is not user authorization")
        items = [dict(text=line.strip(" -\t"), origin="unattributed", source=source, evidence="")
                 for line in observable_behaviors.splitlines() if line.strip(" -\t")]
    if not isinstance(items, list) or not items:
        raise ValueError("REQ requires at least one outcome claim")
    rows = []
    for item in items:
        if not isinstance(item, dict) or set(item) - {"text", "origin", "source", "evidence", "confirmed_scope"}:
            raise ValueError("invalid requirement provenance fields")
        if any(not isinstance(value, str) for value in item.values()):
            raise ValueError("requirement provenance values must be strings")
        row = {key: item.get(key, "").strip() for key in ("text", "origin", "source", "evidence", "confirmed_scope")}
        if not row["text"] or row["origin"] not in ORIGINS:
            raise ValueError("each requirement needs a claim and a recognized origin")
        if row["origin"] in USER_ORIGINS | {"derived"} and (not row["source"] or not row["evidence"]):
            if status == "accepted" and row["origin"] in USER_ORIGINS:
                raise ValueError("accepted user claims require an identifiable source and relevant exact quote")
            row["origin"] = "unattributed"
        if row["origin"] == "user_confirmed" and not row["confirmed_scope"]:
            raise ValueError("user_confirmed needs the particular proposal/scope confirmed; blanket approval is insufficient")
        rows.append(row)
    if status == "accepted" and not any(row["origin"] in USER_ORIGINS for row in rows):
        raise ValueError("non-user claims cannot become accepted requirements; include at least one grounded user claim or publish candidate")
    return rows


def _cell(value):
    return value.replace("|", "\\|").replace("\r", "").replace("\n", "<br>")


def _claim_table(rows, status):
    if not rows:
        return "- None."
    lines = ["| Claim (writer interpretation) | Origin | Source | Exact quote / derivation basis | Confirmed proposal (writer interpretation) | Disposition |",
             "| --- | --- | --- | --- | --- | --- |"]
    for row in rows:
        if row["origin"] == "derived":
            disposition = "implementation-only; not an acceptance requirement"
        elif status == "accepted" and row["origin"] in USER_ORIGINS:
            disposition = "accepted"
        else:
            disposition = "candidate; non-binding"
        lines.append("| " + " | ".join(_cell(value) for value in
                     (row["text"], row["origin"], row["source"] or "unverified",
                      row["evidence"] or "unverified", row["confirmed_scope"] or "—", disposition)) + " |")
    return "\n".join(lines)


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
                   source: str = "", status: str = "candidate", *,
                   requirements: list[dict] | None = None) -> str:
    status = status.strip() or "candidate"
    rows = _requirements(requirements, observable_behaviors, source, status)
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    acceptance = ("Only accepted user-sourced rows are binding. Derived work and candidates do not add acceptance requirements; verification cues do not add requirements."
                  if status == "accepted" else
                  "None accepted by this scaffold. Candidate claims are not acceptance or close criteria.")
    return (
        f"# REQ - {title}\n\n"
        f"status: {status}\n\n"
        "## Intent\n"
        "Writer interpretation; the sourced claims below determine its scope.\n\n"
        f"{intent.strip() or 'TBD'}\n\n"
        "## Observable Behavior\n"
        "Source labels and quotes require semantic review against the actual source.\n\n"
        "### User-sourced claims\n"
        f"{_claim_table([r for r in rows if r['origin'] in USER_ORIGINS], status)}\n\n"
        "### Derived constraints (not user requests)\n"
        "Source identifies the existing requirement/constraint; basis explains necessary implementation work.\n"
        "Implement and check supported work to satisfy that outcome; do not introduce new product scope.\n\n"
        f"{_claim_table([r for r in rows if r['origin'] == 'derived'], status)}\n\n"
        "### Assumptions / proposals / unattributed claims (non-binding)\n"
        f"{_claim_table([r for r in rows if r['origin'] not in USER_ORIGINS | {'derived'}], status)}\n\n"
        "## Acceptance Signals\n"
        f"{acceptance}\n\n"
        "## Verification Cues\n"
        "Author-selected checks; these do not expand the sourced outcome.\n\n"
        f"{_bullet_lines(verification_cues)}\n\n"
        "## Non-Goals\n"
        "Author proposals unless included in a sourced claim above.\n\n"
        f"{_bullet_lines(non_goals) if non_goals.strip() else '- TBD'}\n\n"
        "## Source\n"
        f"- created: {today}\n"
        f"- source: {source.strip() or 'harness auto REQ scaffold'}\n"
    )


def create_req_doc(repo_root: str, area: str, slug: str, intent: str,
                   observable_behaviors: str, verification_cues: str,
                   non_goals: str = "", source: str = "",
                   append: bool = True, status: str = "candidate", *,
                   requirements: list[dict] | None = None) -> str:
    area = _slugify(area)
    slug = _slugify(slug)
    rel = os.path.join("doc", area, f"REQ__{slug}.md")
    path = os.path.join(repo_root, rel)
    title = slug.replace("-", " ").title()
    new_body = render_req_doc(title, intent, observable_behaviors, verification_cues, non_goals, source, status, requirements=requirements)
    os.makedirs(os.path.dirname(path), exist_ok=True)

    if append and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read().rstrip()
        body = (
            existing
            + ("\n\n## Candidate Update (non-binding; earlier acceptance does not apply)\n\n"
               if (status.strip() or "candidate") == "candidate" else "\n\n## Sourced Accepted Update\n\n")
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
    parser.add_argument("--observable-behavior", default="")
    parser.add_argument("--requirements-file", help="JSON array of per-outcome text/origin/source/evidence; confirmed claims also need confirmed_scope.")
    parser.add_argument("--verification-cues", required=True)
    parser.add_argument("--non-goals", default="")
    parser.add_argument("--source", default="")
    parser.add_argument("--status", default="candidate", choices=["accepted", "candidate"],
                        help="candidate by default; accepted requires grounded user claims and accepts only those rows, never derivations or candidates.")
    parser.add_argument("--path", action="append", default=[])
    args = parser.parse_args()
    requirements = None
    if args.requirements_file:
        try:
            with open(args.requirements_file, encoding="utf-8") as stream:
                requirements = json.load(stream)
        except (OSError, ValueError) as exc:
            parser.error(f"invalid requirements file: {exc}")

    detected = detect_req_need(texts=[args.intent, args.observable_behavior, args.verification_cues], paths=args.path)
    area = args.area or detected.get("suggested_area") or "ui"
    slug = args.slug or detected.get("suggested_slug") or "observable-behavior"
    source = args.source or (f"task: {args.task_id}" if args.task_id else "harness auto REQ scaffold")
    try:
        rel = create_req_doc(
            args.repo_root, area, slug, args.intent, args.observable_behavior,
            args.verification_cues, args.non_goals, source,
            status=args.status, requirements=requirements,
        )
    except ValueError as exc:
        parser.error(str(exc))
    print(rel)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
