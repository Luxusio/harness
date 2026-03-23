#!/usr/bin/env python3
"""Deterministic query prefilter for the harness memory index.

Usage:
  python3 harness/scripts/query-memory.py \\
    --query "approval source of truth" \\
    [--paths "harness/policies/approvals.yaml"] \\
    [--domains "approval-gates"] \\
    [--top 8] \\
    [--include-overlay] \\
    [--format json|markdown]
"""
import argparse
import json
import os
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_DIR = REPO_ROOT / "harness" / "memory-index"
MANIFEST_PATH = INDEX_DIR / "manifest.json"
ACTIVE_DIR = INDEX_DIR / "active" / "by-subject"
VERSION_PATH = INDEX_DIR / "VERSION"
OVERLAY_PATH = REPO_ROOT / ".harness-cache" / "memory-overlay" / "records.jsonl"

AUTHORITY_SCORE = {
    "enforced": 4,
    "confirmed": 3,
    "observed": 2,
    "hypothesis": 1,
}


# ── Tokenizer ────────────────────────────────────────────────────────────────

def tokenize(text: str) -> set:
    """Lower-case word tokens from a string, filtering short noise words."""
    if not text:
        return set()
    import re
    tokens = re.findall(r"[a-z0-9_\-]+", text.lower())
    stopwords = {"a", "an", "the", "is", "in", "of", "to", "and", "or", "for",
                 "on", "at", "by", "with", "as", "be", "it", "its", "this",
                 "that", "are", "was", "from", "not"}
    return {t for t in tokens if len(t) > 1 and t not in stopwords}


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_record(record: dict, query_tokens: set, boost_paths: list, boost_domains: list) -> float:
    """Compute relevance score for a single record."""
    score = 0.0

    # Authority boost
    authority = record.get("authority", record.get("status", ""))
    score += AUTHORITY_SCORE.get(authority, 0)

    # Active records score higher than resolved
    if record.get("state", record.get("lifecycle_state", "")) == "active":
        score += 0.5

    # Token overlap: query tokens vs subject_key + statement + tags
    subject_key = record.get("subject_key", "")
    statement = record.get("statement", "")
    tags = " ".join(record.get("tags", []))
    record_text = f"{subject_key} {statement} {tags}"
    record_tokens = tokenize(record_text)
    overlap = len(query_tokens & record_tokens)
    score += overlap * 2.0

    # Path boost
    scope_paths = record.get("scope", {}).get("paths", [])
    if isinstance(scope_paths, list):
        for bp in boost_paths:
            if any(bp in sp or sp in bp for sp in scope_paths):
                score += 10.0
                break

    # Domain boost
    scope_domains = record.get("scope", {}).get("domains", [])
    if isinstance(scope_domains, list):
        for bd in boost_domains:
            if bd in scope_domains:
                score += 5.0
                break

    return score


# ── Loading ──────────────────────────────────────────────────────────────────

def load_index_records() -> list:
    """Load all active records from the index by-subject directory."""
    records = []
    if not ACTIVE_DIR.exists():
        return records
    for json_file in sorted(ACTIVE_DIR.glob("*.json")):
        try:
            with json_file.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # File may be a single record or a list of records
            if isinstance(data, list):
                records.extend(data)
            elif isinstance(data, dict):
                records.append(data)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"WARNING: Could not load {json_file}: {exc}", file=sys.stderr)
    return records


def load_overlay_records() -> list:
    """Load records from .harness-cache/memory-overlay/records.jsonl if present."""
    records = []
    if not OVERLAY_PATH.exists():
        return records
    try:
        with OVERLAY_PATH.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError as exc:
                    print(f"WARNING: Skipping malformed overlay line: {exc}", file=sys.stderr)
    except OSError as exc:
        print(f"WARNING: Could not read overlay: {exc}", file=sys.stderr)
    return records


def merge_overlay(index_records: list, overlay_records: list) -> list:
    """Overlay records override index records with the same subject_key."""
    merged = {r.get("subject_key", id(r)): r for r in index_records}
    for r in overlay_records:
        key = r.get("subject_key")
        if key:
            merged[key] = r
        else:
            merged[id(r)] = r
    return list(merged.values())


# ── Output formatters ────────────────────────────────────────────────────────

def format_markdown(results: list) -> str:
    """Render scored records as a human-readable markdown summary."""
    if not results:
        return "_No matching memory records found._\n"

    lines = ["## Memory Query Results\n"]
    for i, (record, score) in enumerate(results, 1):
        subject = record.get("subject_key", "(no subject)")
        statement = record.get("statement", "")
        kind = record.get("kind", record.get("type", ""))
        authority = record.get("authority", record.get("status", ""))
        domain = record.get("domain", "")
        state = record.get("state", record.get("lifecycle_state", ""))

        meta_parts = []
        if kind:
            meta_parts.append(f"kind:{kind}")
        if authority:
            meta_parts.append(f"authority:{authority}")
        if domain:
            meta_parts.append(f"domain:{domain}")
        if state:
            meta_parts.append(f"state:{state}")
        meta_parts.append(f"score:{score:.1f}")
        meta = "  |  ".join(meta_parts)

        lines.append(f"### {i}. {subject}")
        lines.append(f"_{meta}_\n")
        if statement:
            lines.append(statement)
            lines.append("")

        # Additional fields of interest
        rationale = record.get("rationale", "")
        if rationale:
            lines.append(f"**Rationale:** {rationale}\n")

        source = record.get("source", {})
        if source and isinstance(source, dict):
            src_file = source.get("file", "")
            if src_file:
                lines.append(f"**Source:** `{src_file}`\n")

    return "\n".join(lines)


def format_json(results: list) -> str:
    """Render scored records as a JSON array."""
    output = []
    for record, score in results:
        entry = dict(record)
        entry["_score"] = score
        output.append(entry)
    return json.dumps(output, indent=2, ensure_ascii=False)


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_args():
    parser = argparse.ArgumentParser(
        description="Query the harness memory index with deterministic scoring."
    )
    parser.add_argument("--query", required=True, help="Natural language query string")
    parser.add_argument("--paths", default="", help="Comma-separated paths to boost")
    parser.add_argument("--domains", default="", help="Comma-separated domains to boost")
    parser.add_argument("--top", type=int, default=8, help="Max results to return (default: 8)")
    parser.add_argument("--include-overlay", action="store_true",
                        help="Merge .harness-cache/memory-overlay/ records if present")
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown",
                        help="Output format (default: markdown)")
    return parser.parse_args()


def main():
    args = parse_args()

    # Verify index exists
    if not MANIFEST_PATH.exists():
        print(
            "WARNING: Memory index not found. Run build-memory-index.sh first.",
            file=sys.stderr,
        )
        if args.format == "json":
            print("[]")
        else:
            print("_Memory index not built yet._")
        sys.exit(0)

    # Load manifest (basic integrity check)
    try:
        with MANIFEST_PATH.open("r", encoding="utf-8") as f:
            manifest = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: Memory index manifest is corrupt: {exc}", file=sys.stderr)
        if args.format == "json":
            print("[]")
        else:
            print("_Memory index is corrupt. Try rebuilding with build-memory-index.sh._")
        sys.exit(0)

    # Load records
    records = load_index_records()

    if args.include_overlay:
        overlay = load_overlay_records()
        if overlay:
            records = merge_overlay(records, overlay)

    # Exclude superseded records
    records = [r for r in records if not r.get("superseded", False)]

    # Parse boost lists
    boost_paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    boost_domains = [d.strip() for d in args.domains.split(",") if d.strip()]
    query_tokens = tokenize(args.query)

    # Score and rank
    scored = [(r, score_record(r, query_tokens, boost_paths, boost_domains)) for r in records]
    scored.sort(key=lambda x: x[1], reverse=True)

    # Take top N (only include records with non-zero score if query was provided)
    if query_tokens:
        scored = [(r, s) for r, s in scored if s > 0]
    top_results = scored[: args.top]

    # Output
    if args.format == "json":
        print(format_json(top_results))
    else:
        print(format_markdown(top_results))


if __name__ == "__main__":
    main()
