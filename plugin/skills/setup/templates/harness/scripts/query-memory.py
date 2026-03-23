#!/usr/bin/env python3
"""Deterministic query prefilter for the harness memory index.

Usage:
  python3 harness/scripts/query-memory.py \\
    --query "approval source of truth" \\
    [--paths "harness/policies/approvals.yaml"] \\
    [--domains "approval-gates"] \\
    [--top 8] \\
    [--include-overlay] \\
    [--explain] \\
    [--format json|markdown|pack]
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path


# ── Constants ────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INDEX_DIR = REPO_ROOT / "harness" / "memory-index"
MANIFEST_PATH = INDEX_DIR / "manifest.json"
ACTIVE_DIR = INDEX_DIR / "active" / "by-subject"
DOMAIN_DIR = INDEX_DIR / "active" / "by-domain"
PATH_DIR = INDEX_DIR / "active" / "by-path"
TIMELINE_DIR = INDEX_DIR / "timeline"
SOURCE_DIR = INDEX_DIR / "active" / "by-source-path"
IDENTIFIER_DIR = INDEX_DIR / "active" / "by-identifier"
VERSION_PATH = INDEX_DIR / "VERSION"
OVERLAY_PATH = REPO_ROOT / ".harness-cache" / "memory-overlay" / "records.jsonl"

AUTHORITY_SCORE = {
    "enforced": 4,
    "confirmed": 3,
    "observed": 2,
    "hypothesis": 1,
}

# Temporal recency bonus weights
RECENCY_BONUS = 0.5  # per year of recency, applied to documented_at

# Operator tokens: used for temporal/structural query interpretation, NOT lexical scoring
OPERATOR_TOKENS = {"latest", "current", "changed", "still", "now", "before",
                   "after", "superseded", "valid", "what"}

# Keep for backward compatibility
TEMPORAL_TERMS = {"latest", "current", "changed", "still", "now", "before",
                  "after", "superseded", "valid"}

IDENTIFIER_RE = re.compile(r'\b(ADR-\d+|REQ-\d+)\b', re.IGNORECASE)
PATH_RE = re.compile(r'(?:^|\s)([a-zA-Z0-9_.-]+/[a-zA-Z0-9_./-]+)')


# ── Tokenizer ────────────────────────────────────────────────────────────────

def tokenize(text: str) -> set:
    """Lower-case word tokens from a string, filtering short noise words."""
    if not text:
        return set()
    tokens = re.findall(r"[a-z0-9_\-]+", text.lower())
    stopwords = {"a", "an", "the", "is", "in", "of", "to", "and", "or", "for",
                 "on", "at", "by", "with", "as", "be", "it", "its", "this",
                 "that", "are", "was", "from", "not"}
    return {t for t in tokens if len(t) > 1 and t not in stopwords}


# ── Structured Query Parser ───────────────────────────────────────────────────

def parse_query(raw_query: str) -> dict:
    """Parse query into structured features.

    Separates content tokens (used for lexical scoring) from operator tokens
    (used for temporal/structural interpretation) so operators like 'before'
    don't leak into lexical matching.
    """
    tokens = tokenize(raw_query)
    identifiers = [m.group(1).upper() for m in IDENTIFIER_RE.finditer(raw_query)]
    raw_paths = [m.group(1) for m in PATH_RE.finditer(raw_query)]
    operator_tokens = tokens & OPERATOR_TOKENS
    # KEY: operators excluded from lexical scoring to prevent "before" matching
    # "Always ask before" in approval rules
    content_tokens = tokens - OPERATOR_TOKENS
    return {
        "content_tokens": content_tokens,
        "operator_tokens": operator_tokens,
        "identifiers": identifiers,
        "raw_paths": raw_paths,
    }


# ── Path normalization ────────────────────────────────────────────────────────

def normalize_path_key(path: str) -> str:
    """Normalize a file path to a deterministic index key.

    Shared between build and query so shard filenames are predictable.
    """
    path = path.strip("/").replace("/", "__").replace(".", "_")
    return path[:80]


# ── Path matching ─────────────────────────────────────────────────────────────

def strict_path_match(query_path: str, scope_path: str) -> int:
    """Return path match score: 10 for exact, 5 for ancestor/descendant, 0 for no match."""
    qp = query_path.strip("/").split("/")
    sp = scope_path.strip("/").split("/")
    # Exact match
    if qp == sp:
        return 10
    # query is ancestor of scope
    if len(qp) < len(sp) and sp[:len(qp)] == qp:
        return 5
    # query is descendant of scope
    if len(sp) < len(qp) and qp[:len(sp)] == sp:
        return 5
    return 0


# ── Loading ──────────────────────────────────────────────────────────────────

def iter_records_from_json(data) -> list:
    """Flatten a JSON value into a flat list of record dicts.

    Handles three shapes:
    - {"records": [...], ...}  — wrapper object (by-subject format)
    - [...]                    — bare list of records
    - {...}                    — single record dict
    """
    if isinstance(data, dict):
        if "records" in data and isinstance(data["records"], list):
            return data["records"]
        return [data]
    if isinstance(data, list):
        return data
    return []


def load_json_file(path: Path) -> list:
    """Load records from a single JSON file, returning empty list on error."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return iter_records_from_json(data)
    except (json.JSONDecodeError, OSError) as exc:
        print(f"WARNING: Could not load {path}: {exc}", file=sys.stderr)
        return []


def load_index_records() -> list:
    """Load all active records from the index by-subject directory (full scan)."""
    records = []
    if not ACTIVE_DIR.exists():
        return records
    for json_file in sorted(ACTIVE_DIR.glob("*.json")):
        records.extend(load_json_file(json_file))
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


# ── Query Planner ─────────────────────────────────────────────────────────────

def plan_query(args, parsed: dict) -> tuple:
    """Decide which index shards to load based on query hints.

    Uses structured parsed query features (content_tokens, operator_tokens,
    identifiers, raw_paths) for targeted loading instead of full scans.

    Returns (candidates: list, sources_used: list, explicit_path_hits: bool).
    """
    candidates = []
    sources_used = []
    seen = set()
    explicit_path_hits = False

    def add_records(records, source_label):
        added = 0
        for r in records:
            rid = r.get("id") or r.get("subject_key") or str(id(r))
            if rid not in seen:
                seen.add(rid)
                candidates.append(r)
                added += 1
        if added > 0:
            sources_used.append(source_label)
        return added

    # 1. Domain-targeted loading
    if args.domains:
        for domain in args.domains.split(","):
            domain = domain.strip()
            if not domain:
                continue
            path = DOMAIN_DIR / f"{domain}.json"
            if path.exists():
                add_records(load_json_file(path), f"by-domain/{domain}")

    # 2. Path-targeted loading (targeted, not full scan)
    # Combine explicit --paths arg with paths extracted from query text
    all_boost_paths = []
    if args.paths:
        all_boost_paths += [p.strip() for p in args.paths.split(",") if p.strip()]
    all_boost_paths += parsed.get("raw_paths", [])

    if all_boost_paths:
        if PATH_DIR.exists():
            for p in all_boost_paths:
                key = normalize_path_key(p)
                exact = PATH_DIR / f"{key}.json"
                if exact.exists():
                    n = add_records(load_json_file(exact), f"by-path/{key}")
                    if n > 0:
                        explicit_path_hits = True
                else:
                    # Try ancestor/descendant: only files whose stem matches structurally
                    for json_file in PATH_DIR.glob("*.json"):
                        reconstructed = json_file.stem.replace("__", "/").replace("_", ".")
                        if strict_path_match(p, reconstructed) > 0:
                            n = add_records(load_json_file(json_file), f"by-path/{json_file.stem}")
                            if n > 0:
                                explicit_path_hits = True

        # Also try by-source-path index if it exists
        if SOURCE_DIR.exists():
            for p in all_boost_paths:
                key = normalize_path_key(p)
                src_file = SOURCE_DIR / f"{key}.json"
                if src_file.exists():
                    n = add_records(load_json_file(src_file), f"by-source-path/{key}")
                    if n > 0:
                        explicit_path_hits = True

    # 3. Identifier-targeted loading
    identifiers = parsed.get("identifiers", [])
    if identifiers:
        if IDENTIFIER_DIR.exists():
            for ident in identifiers:
                ident_key = ident.lower().replace("-", ".")
                id_file = IDENTIFIER_DIR / f"{ident_key}.json"
                if id_file.exists():
                    add_records(load_json_file(id_file), f"by-identifier/{ident_key}")

    # 4. Targeted timeline loading — only for shortlisted subjects + explicit identifiers
    # NOT a full scan of all timeline files
    operator_tokens = parsed.get("operator_tokens", set())
    if operator_tokens or identifiers:
        if TIMELINE_DIR.exists():
            # Load timelines for explicit identifiers
            for ident in identifiers:
                ident_lower = ident.lower().replace("-", ".")
                for tf in TIMELINE_DIR.glob("*.json"):
                    if ident_lower in tf.stem:
                        add_records(load_json_file(tf), f"timeline/{tf.stem}")

            # Load timelines only for subjects already shortlisted (not a full scan)
            shortlisted_subjects = {r.get("subject_key", "") for r in candidates}
            for sk in list(shortlisted_subjects)[:20]:  # cap to prevent runaway scan
                if not sk:
                    continue
                tf = TIMELINE_DIR / f"{sk}.json"
                if tf.exists():
                    add_records(load_json_file(tf), f"timeline/{sk}")

    # 5. Quality-based rescue fallback (not just count < 3)
    content_tokens = parsed.get("content_tokens", set())
    max_content_overlap = 0
    for r in candidates:
        subject_key = r.get("subject_key", "")
        statement = r.get("statement", "")
        tags = " ".join(r.get("tags", []))
        rec_tokens = tokenize(f"{subject_key} {statement} {tags}")
        overlap = len(content_tokens & rec_tokens)
        if overlap > max_content_overlap:
            max_content_overlap = overlap

    needs_rescue = (
        len(candidates) < 3
        or (not identifiers and not explicit_path_hits)
        or max_content_overlap < 2
    )
    if needs_rescue:
        full = load_index_records()
        add_records(full, "by-subject/* (rescue)")

    return candidates, sources_used, explicit_path_hits


# ── Admission Gate ────────────────────────────────────────────────────────────

def has_match_signal(record: dict, content_tokens: set,
                     boost_paths: list, boost_domains: list) -> bool:
    """Return True if record has at least one genuine match signal (not just authority).

    Uses content_tokens (operator-stripped) for lexical matching.
    """
    # Lexical overlap with subject/statement/tags using content tokens only
    subject_key = record.get("subject_key", "")
    statement = record.get("statement", "")
    tags = " ".join(record.get("tags", []))
    text = f"{subject_key} {statement} {tags}"
    if content_tokens & tokenize(text):
        return True

    # Domain match
    scope_domains = record.get("scope", {}).get("domains", [])
    if isinstance(scope_domains, list):
        if any(d in scope_domains for d in boost_domains):
            return True

    # Strict path match
    scope_paths = record.get("scope", {}).get("paths", [])
    if isinstance(scope_paths, list):
        for bp in boost_paths:
            for sp in scope_paths:
                if strict_path_match(bp, sp) > 0:
                    return True

    # Provenance source_path match
    provenance = record.get("provenance", {})
    if isinstance(provenance, dict):
        src = provenance.get("source_path", "")
        if src:
            for bp in boost_paths:
                if src == bp or bp.endswith(src) or src.endswith(bp):
                    return True

    return False


# ── Scoring ──────────────────────────────────────────────────────────────────

def score_record(record: dict, content_tokens: set, boost_paths: list, boost_domains: list,
                 reference_date: str = "") -> tuple:
    """Compute relevance score for a single record.

    Uses content_tokens (operator-stripped) for lexical scoring to prevent
    operator words like 'before' from boosting irrelevant records.

    Returns (score: float, admission_reasons: list, score_breakdown: dict).
    """
    import datetime
    score = 0.0
    admission_reasons = []
    score_breakdown = {}

    # Lexical overlap (primary signal) — uses content_tokens only
    subject_key = record.get("subject_key", "")
    statement = record.get("statement", "")
    tags = " ".join(record.get("tags", []))
    record_text = f"{subject_key} {statement} {tags}"
    record_tokens = tokenize(record_text)
    overlap = len(content_tokens & record_tokens)
    if overlap > 0:
        lexical_score = overlap * 2.0
        score += lexical_score
        admission_reasons.append(f"lexical:{overlap}")
        score_breakdown["lexical"] = lexical_score

    # Path boost (strict segment-based matching)
    scope_paths = record.get("scope", {}).get("paths", [])
    best_path_score = 0
    if isinstance(scope_paths, list):
        for bp in boost_paths:
            for sp in scope_paths:
                ps = strict_path_match(bp, sp)
                if ps > best_path_score:
                    best_path_score = ps
    if best_path_score > 0:
        score += best_path_score
        admission_reasons.append(f"path:{best_path_score}")
        score_breakdown["path"] = best_path_score

    # Domain boost
    scope_domains = record.get("scope", {}).get("domains", [])
    domain_hit = False
    if isinstance(scope_domains, list):
        for bd in boost_domains:
            if bd in scope_domains:
                domain_hit = True
                break
    if domain_hit:
        score += 5.0
        admission_reasons.append("domain")
        score_breakdown["domain"] = 5.0

    # Provenance source_path boost — strong signal when --paths matches source file
    provenance = record.get("provenance", {})
    if isinstance(provenance, dict):
        src = provenance.get("source_path", "")
        if src:
            for bp in boost_paths:
                if src == bp or bp.endswith(src) or src.endswith(bp):
                    score += 8.0  # strong provenance boost
                    admission_reasons.append(f"provenance:{bp}")
                    score_breakdown["provenance"] = 8.0
                    break

    # Authority as rank modifier only (0.5x weight, not admission ticket)
    authority = record.get("authority", record.get("status", ""))
    authority_bonus = AUTHORITY_SCORE.get(authority, 0) * 0.5
    if authority_bonus > 0:
        score += authority_bonus
        score_breakdown["authority_mod"] = authority_bonus

    # Active records score higher than resolved
    status_bonus = 0.0
    index_status = record.get("index_status", "")
    if index_status == "active":
        status_bonus = 0.3
    elif index_status == "resolved":
        status_bonus = 0.1
    if status_bonus > 0:
        score += status_bonus
        score_breakdown["status"] = status_bonus

    # Temporal recency bonus
    temporal = record.get("temporal", {})
    documented_at = temporal.get("documented_at") or temporal.get("last_verified_at")
    if documented_at and reference_date:
        try:
            doc_date = datetime.date.fromisoformat(documented_at)
            ref_date = datetime.date.fromisoformat(reference_date)
            delta_years = (ref_date - doc_date).days / 365.25
            if delta_years >= 0:
                recency = max(0.0, RECENCY_BONUS * (1.0 - delta_years))
                if recency > 0:
                    score += recency
                    score_breakdown["recency"] = round(recency, 3)
        except (ValueError, TypeError):
            pass

    return score, admission_reasons, score_breakdown


# ── Output formatters ────────────────────────────────────────────────────────

def format_markdown(results: list, explain: bool = False,
                    sources_used: list = None) -> str:
    """Render scored records as a human-readable markdown summary."""
    if not results:
        return "_No matching memory records found._\n"

    lines = ["## Memory Query Results\n"]
    for i, entry in enumerate(results, 1):
        record = entry["record"]
        score = entry["score"]
        admission_reasons = entry["admission_reasons"]
        score_breakdown = entry["score_breakdown"]
        source_shard = entry.get("source_shard", "")

        subject = record.get("subject_key", "(no subject)")
        statement = record.get("statement", "")
        kind = record.get("kind", record.get("type", ""))
        authority = record.get("authority", record.get("status", ""))
        index_status = record.get("index_status", "")
        domains = record.get("scope", {}).get("domains", [])
        domain_str = ", ".join(domains) if domains else ""

        meta_parts = []
        if kind:
            meta_parts.append(f"kind:{kind}")
        if authority:
            meta_parts.append(f"authority:{authority}")
        if domain_str:
            meta_parts.append(f"domains:{domain_str}")
        if index_status:
            meta_parts.append(f"status:{index_status}")
        meta_parts.append(f"score:{score:.1f}")
        meta = "  |  ".join(meta_parts)

        lines.append(f"### {i}. {subject}")
        lines.append(f"_{meta}_\n")
        if statement:
            lines.append(statement)
            lines.append("")

        if explain:
            reason_str = ", ".join(admission_reasons) if admission_reasons else "none"
            breakdown_parts = [f"{k}:{v}" for k, v in score_breakdown.items()]
            breakdown_str = " | ".join(breakdown_parts)
            source_str = f" | source: {source_shard}" if source_shard else ""
            lines.append(f"**[explain]** admission: {reason_str} | {breakdown_str}{source_str}\n")

        # Source provenance
        provenance = record.get("provenance", {})
        if isinstance(provenance, dict):
            src_path = provenance.get("source_path", "")
            if src_path:
                lines.append(f"**Source:** `{src_path}`\n")

    if sources_used:
        lines.append("\n---")
        lines.append(f"_Shards loaded: {', '.join(sources_used)}_")

    return "\n".join(lines)


def format_json(results: list) -> str:
    """Render scored records as a JSON array."""
    output = []
    for entry in results:
        record = entry["record"]
        out_record = dict(record)
        out_record["_score"] = entry["score"]
        out_record["_admission_reasons"] = entry["admission_reasons"]
        output.append(out_record)
    return json.dumps(output, indent=2, ensure_ascii=False)


def format_pack(results: list, query: str, sources_used: list,
                total_candidates: int, admitted: int, filtered: int) -> str:
    """Render results as a structured memory pack JSON."""
    facts = []
    source_files = {}  # path -> score for capping/sorting
    for entry in results:
        record = entry["record"]
        # Rich fact with full record fields (PR3)
        fact = {
            "subject_key": record.get("subject_key", ""),
            "statement": record.get("statement", ""),
            "kind": record.get("kind", record.get("type", "")),
            "authority": record.get("authority", record.get("status", "")),
            "index_status": record.get("index_status", ""),
            "source_status": record.get("source_status"),
            "scope": record.get("scope", {}),
            "temporal": record.get("temporal", {}),
            "relations": record.get("relations", {}),
            "provenance": record.get("provenance", {}),
            "tags": record.get("tags", []),
            "score": entry["score"],
            "admission_reasons": entry["admission_reasons"],
        }
        provenance = record.get("provenance", {})
        if isinstance(provenance, dict):
            src = provenance.get("source_path", "")
            if src:
                # Track max score per source file for capping
                if src not in source_files or entry["score"] > source_files[src]:
                    source_files[src] = entry["score"]
        facts.append(fact)

    # Detect conflicts: same canonical subject with multiple active records
    # at different authorities
    subject_groups: dict = {}
    for entry in results:
        sk = entry["record"].get("subject_key", "")
        subject_groups.setdefault(sk, []).append(entry)
    conflicts = []
    for sk, entries in subject_groups.items():
        if len(entries) > 1:
            authorities = set(e["record"].get("authority", "") for e in entries)
            if len(authorities) > 1:
                conflicts.append({"subject_key": sk, "authorities": sorted(authorities)})

    # Cap source_files_to_verify at 4, sorted by score descending
    top_sources = sorted(source_files.items(), key=lambda x: x[1], reverse=True)[:4]
    source_files_list = [s for s, _ in top_sources]

    pack = {
        "query": query,
        "sources_loaded": sources_used,
        "facts": facts,
        "source_files_to_verify": source_files_list,
        "unresolved_conflicts": conflicts,
        "admission_summary": {
            "total_candidates": total_candidates,
            "admitted": admitted,
            "filtered": filtered,
        },
    }
    return json.dumps(pack, indent=2, ensure_ascii=False)


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
    parser.add_argument("--explain", action="store_true",
                        help="Show admission reasons and score breakdown per result")
    parser.add_argument("--format", choices=["markdown", "json", "pack"], default="markdown",
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
        elif args.format == "pack":
            print(json.dumps({"query": args.query, "facts": [], "error": "index not built"}))
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
        elif args.format == "pack":
            print(json.dumps({"query": args.query, "facts": [], "error": "index corrupt"}))
        else:
            print("_Memory index is corrupt. Try rebuilding with build-memory-index.sh._")
        sys.exit(0)

    # Parse boost lists
    boost_paths = [p.strip() for p in args.paths.split(",") if p.strip()]
    boost_domains = [d.strip() for d in args.domains.split(",") if d.strip()]

    # Structured query parsing — separates content from operator tokens
    parsed = parse_query(args.query)
    content_tokens = parsed["content_tokens"]

    # Query planner: load targeted shards first, quality-based rescue fallback
    records, sources_used, explicit_path_hits = plan_query(args, parsed)

    if args.include_overlay:
        overlay = load_overlay_records()
        if overlay:
            records = merge_overlay(records, overlay)

    # Exclude superseded records
    records = [r for r in records if r.get("index_status") != "superseded"]

    total_candidates = len(records)

    # Admission gate: every record must have at least one genuine match signal
    # Uses content_tokens (operator-stripped) for lexical matching
    if content_tokens or boost_paths or boost_domains:
        admitted_records = [
            r for r in records
            if has_match_signal(r, content_tokens, boost_paths, boost_domains)
        ]
    else:
        admitted_records = records

    filtered_count = total_candidates - len(admitted_records)

    # Score admitted records using content_tokens (not raw query tokens)
    import datetime
    today = datetime.date.today().isoformat()
    scored = []
    for r in admitted_records:
        sc, reasons, breakdown = score_record(
            r, content_tokens, boost_paths, boost_domains, today
        )
        src_path = (r.get("provenance", {}).get("source_path", "")
                    if isinstance(r.get("provenance"), dict) else "")
        scored.append({
            "record": r,
            "score": sc,
            "admission_reasons": reasons,
            "score_breakdown": breakdown,
            "source_shard": src_path,
        })

    # Apply provenance boost for --paths matches AFTER initial scoring
    # (already done in score_record via boost_paths, this ensures all paths are covered)

    scored.sort(key=lambda x: x["score"], reverse=True)
    top_results = scored[:args.top]

    # Output
    if args.format == "json":
        print(format_json(top_results))
    elif args.format == "pack":
        print(format_pack(
            top_results, args.query, sources_used,
            total_candidates, len(admitted_records), filtered_count
        ))
    else:
        print(format_markdown(
            top_results,
            explain=args.explain,
            sources_used=sources_used if args.explain else None,
        ))


if __name__ == "__main__":
    main()
