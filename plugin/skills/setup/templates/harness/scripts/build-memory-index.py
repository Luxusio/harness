#!/usr/bin/env python3
"""
Deterministic memory index compiler for harness.

Compiles harness/docs/*, harness/state/*, harness/policies/* into
structured JSON under harness/memory-index/.

Rules:
- Python 3 stdlib only
- Same input → same output (idempotent)
- JSON keys sorted alphabetically
- Records stable-sorted by id
- No build timestamps or commit hashes
- Record id = "mem:<kind>:<subject_key>:<sha256[:8]>"
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def make_id(kind: str, subject_key: str, statement: str) -> str:
    digest = hashlib.sha256((kind + subject_key + statement).encode()).hexdigest()[:8]
    return f"mem:{kind}:{subject_key}:{digest}"


def empty_record(kind: str, subject_key: str, statement: str, source_path: str,
                 source_section: str, source_type: str) -> dict:
    return {
        "authority": "observed",
        "id": make_id(kind, subject_key, statement),
        "index_status": "active",
        "kind": kind,
        "provenance": {
            "locator": source_section,
            "source_path": source_path,
            "source_section": source_section,
            "source_type": source_type,
        },
        "relations": {
            "conflicts_with": [],
            "extends": [],
            "resolves": [],
            "supersedes": [],
        },
        "scope": {
            "api_surfaces": [],
            "domains": [],
            "paths": [],
        },
        "source_status": None,
        "statement": statement,
        "subject_key": subject_key,
        "tags": [],
        "temporal": {
            "documented_at": None,
            "effective_at": None,
            "last_verified_at": None,
        },
    }


def slugify(text: str) -> str:
    """Turn arbitrary text into a dot-separated subject key."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", ".", text)
    text = text.strip(".")
    return text[:80]  # cap length


def write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    content = json.dumps(obj, indent=2, sort_keys=True) + "\n"
    path.write_text(content, encoding="utf-8")


PATH_LIKE_RE = re.compile(r'`([a-zA-Z0-9_./-]+/[a-zA-Z0-9_./-]*)`')


def extract_paths_from_text(text: str) -> list:
    """Extract path-like references from text (backtick-quoted paths with /)."""
    paths = []
    for m in PATH_LIKE_RE.finditer(text):
        p = m.group(1).rstrip("/")
        # Filter out very short or unlikely paths
        if len(p) > 3 and not p.startswith("http"):
            paths.append(p)
    return sorted(set(paths))


def default_scope_for_source(source_path: str) -> dict:
    """Derive default scope from source file path."""
    domains = []
    paths = [source_path] if source_path else []
    if "policies/approvals" in source_path:
        domains = ["approval-gates"]
    elif "docs/architecture" in source_path:
        domains = ["architecture"]
    elif "docs/constraints" in source_path:
        domains = ["constraints"]
    elif "docs/runbooks" in source_path:
        domains = ["runbooks"]
    elif "docs/decisions" in source_path:
        domains = ["decisions"]
    elif "docs/requirements" in source_path:
        domains = ["requirements"]
    elif "docs/domains" in source_path:
        # domain = filename stem
        m = re.search(r"docs/domains/([^/]+)\.md", source_path)
        if m:
            stem = m.group(1)
            domains = [stem] if stem.lower() != "readme" else ["domains"]
        else:
            domains = ["domains"]
    elif "state/recent-decisions" in source_path:
        domains = ["recent-context"]
    elif "state/unknowns" in source_path:
        domains = ["unknowns"]
    elif "policies/memory-policy" in source_path:
        domains = ["memory-policy"]
    return {"domains": domains, "paths": paths, "api_surfaces": []}


def apply_default_scope(rec: dict) -> None:
    """Fill empty scope from source path, and extract paths from statement."""
    # Always try to extract paths from statement text
    if not rec["scope"]["paths"]:
        extracted = extract_paths_from_text(rec.get("statement", ""))
        if extracted:
            rec["scope"]["paths"] = extracted
    # Fill domain/paths fallback from source path
    if not rec["scope"]["domains"] and not rec["scope"]["paths"]:
        default = default_scope_for_source(rec["provenance"]["source_path"])
        rec["scope"]["domains"] = default["domains"]
        rec["scope"]["paths"] = default["paths"]
        rec["scope"]["api_surfaces"] = default["api_surfaces"]
    elif not rec["scope"]["domains"]:
        default = default_scope_for_source(rec["provenance"]["source_path"])
        rec["scope"]["domains"] = default["domains"]
    elif not rec["scope"]["paths"]:
        # Add source path as minimal path reference
        src = rec["provenance"].get("source_path", "")
        if src:
            rec["scope"]["paths"] = [src]


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------

DATE_RE = re.compile(r"\[(\d{4}-\d{2}-\d{2})\]")


def parse_constraints(src: str, rel_path: str) -> list:
    records = []
    current_section = "Project constraints"
    for line in src.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            current_section = h.group(1).strip()
            continue
        # Match bullet items: - [date] text  OR  - text  OR  1. text
        m = re.match(r"^[-*]\s+(.*)", line) or re.match(r"^\d+\.\s+(.*)", line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text or text.startswith("<!--"):
            continue
        # Extract date if present
        dm = DATE_RE.search(text)
        documented_at = dm.group(1) if dm else None
        # Strip date bracket from statement
        statement = DATE_RE.sub("", text).strip().lstrip("]").strip()
        if not statement:
            continue
        section_slug = slugify(current_section)[:20]
        item_slug = slugify(statement)[:30]
        subject_key = f"constraint.{section_slug}.{item_slug}"
        rec = empty_record("constraint", subject_key, statement, rel_path,
                           current_section, "doc")
        rec["authority"] = "confirmed"
        rec["temporal"]["documented_at"] = documented_at
        apply_default_scope(rec)
        records.append(rec)
    return records


def parse_adr(src: str, rel_path: str, filename: str) -> list:
    """One record per ADR file representing the core decision."""
    lines = src.splitlines()
    title = ""
    status = "active"
    date = None
    decision_text_lines = []
    in_decision = False
    superseded_by_adr = None

    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
        sm = re.match(r"^\*\*Status:\*\*\s*(.*)", line)
        if sm:
            status_raw = sm.group(1).strip()
            status_lower = status_raw.lower()
            sup_match = re.match(r"superseded\s+by\s+(ADR-\d+)", status_raw, re.IGNORECASE)
            if sup_match:
                status = "superseded"
                superseded_by_adr = sup_match.group(1).upper()
            elif status_lower == "superseded":
                status = "superseded"
            elif status_lower in ("accepted", "approved", "done"):
                status = "active"
        dm = re.match(r"^\*\*Date:\*\*\s*(\d{4}-\d{2}-\d{2})", line)
        if dm:
            date = dm.group(1)
        if re.match(r"^## Decision", line):
            in_decision = True
            continue
        if in_decision:
            if re.match(r"^## ", line):
                in_decision = False
            else:
                if line.strip() and not line.strip().startswith("<!--"):
                    decision_text_lines.append(line.strip("- ").strip())

    decision_text = " ".join(l for l in decision_text_lines if l).strip()
    if not decision_text:
        decision_text = title

    adr_id_match = re.match(r"ADR-(\d+)", filename, re.IGNORECASE)
    adr_num = adr_id_match.group(1) if adr_id_match else "0000"
    subject_key = f"adr.{adr_num}.{slugify(title)}"

    rec = empty_record("decision", subject_key, decision_text, rel_path, title, "doc")
    rec["authority"] = "confirmed"
    rec["index_status"] = status
    rec["temporal"]["documented_at"] = date
    rec["temporal"]["effective_at"] = date
    # Stash superseded_by for second pass resolution
    if superseded_by_adr:
        rec["_superseded_by_adr"] = superseded_by_adr
    apply_default_scope(rec)
    return [rec]


def parse_requirements(src: str, rel_path: str, filename: str) -> list:
    """One record per REQ file."""
    lines = src.splitlines()
    title = ""
    status = None
    summary_lines = []
    in_summary = False

    for line in lines:
        if line.startswith("# "):
            title = line[2:].strip()
        sm = re.match(r"^\*\*Status:\*\*\s*(.*)", line)
        if sm:
            status = sm.group(1).strip().lower()
        if re.match(r"^## Summary", line, re.IGNORECASE):
            in_summary = True
            continue
        if in_summary:
            if re.match(r"^## ", line):
                in_summary = False
            else:
                if line.strip():
                    summary_lines.append(line.strip("- ").strip())

    statement = " ".join(l for l in summary_lines if l).strip()
    if not statement:
        statement = title

    req_match = re.match(r"REQ-(\d+)", filename, re.IGNORECASE)
    req_num = req_match.group(1) if req_match else "0000"
    subject_key = f"req.{req_num}.{slugify(title)}"

    rec = empty_record("requirement", subject_key, statement, rel_path, title, "doc")
    rec["authority"] = "confirmed"
    rec["source_status"] = status
    apply_default_scope(rec)
    return [rec]


def parse_architecture(src: str, rel_path: str) -> list:
    records = []
    current_section = "Architecture"
    for line in src.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            current_section = h.group(1).strip()
            continue
        # Match bullet items
        m = re.match(r"^[-*]\s+(.*)", line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text or text.startswith("<!--"):
            continue

        # Detect authority marker at end: — confirmed / — inferred / — hypothesis
        authority = "observed"
        kind = "observed_fact"
        if re.search(r"—\s*confirmed\s*$", text, re.IGNORECASE):
            authority = "confirmed"
            kind = "constraint"
            text = re.sub(r"\s*—\s*confirmed\s*$", "", text, flags=re.IGNORECASE).strip()
        elif re.search(r"—\s*inferred\s*$", text, re.IGNORECASE):
            authority = "observed"
            kind = "observed_fact"
            text = re.sub(r"\s*—\s*inferred\s*$", "", text, flags=re.IGNORECASE).strip()
        elif re.search(r"—\s*hypothesis\s*$", text, re.IGNORECASE):
            authority = "hypothesis"
            kind = "observed_fact"
            text = re.sub(r"\s*—\s*hypothesis\s*$", "", text, flags=re.IGNORECASE).strip()

        # Extract date if present
        dm = DATE_RE.search(text)
        documented_at = dm.group(1) if dm else None
        statement = DATE_RE.sub("", text).strip().lstrip("]").strip()
        if not statement:
            continue

        # Derive scope from section name
        section_lower = current_section.lower()
        domains = ["architecture"]
        paths = []
        if "validation" in section_lower:
            domains = ["validation"]
        elif "system boundaries" in section_lower or "boundaries" in section_lower:
            domains = ["architecture"]
            # Extract path-like strings from statement
            paths = re.findall(r"(?:^|[\s,])(\w[\w/.-]+/)", statement)
            paths = [p.strip() for p in paths if len(p.strip()) > 2]

        section_slug = slugify(current_section)[:20]
        item_slug = slugify(statement)[:30]
        subject_key = f"arch.{section_slug}.{item_slug}"
        rec = empty_record(kind, subject_key, statement, rel_path,
                           current_section, "doc")
        rec["authority"] = authority
        rec["temporal"]["documented_at"] = documented_at
        rec["scope"]["domains"] = domains
        rec["scope"]["paths"] = paths
        records.append(rec)
    return records


def parse_runbook(src: str, rel_path: str) -> list:
    records = []
    current_section = "Runbook"
    for line in src.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            current_section = h.group(1).strip()
            continue
        m = re.match(r"^[-*]\s+(.*)", line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text or text.startswith("<!--") or text.startswith("**") and text.endswith("**"):
            continue
        # Skip pure command lines (start with backtick) — these are code examples
        if text.startswith("`") and text.endswith("`"):
            continue
        # Skip bold-only headers like "**Dev server:**"
        if re.match(r"^\*\*[^*]+:\*\*\s*`", text):
            # Extract command description
            desc_m = re.match(r"^\*\*([^*]+):\*\*\s*(.*)", text)
            if desc_m:
                statement = f"{desc_m.group(1).strip()}: {desc_m.group(2).strip()}"
            else:
                statement = text
        else:
            statement = text

        if not statement.strip():
            continue

        section_slug = slugify(current_section)[:20]
        item_slug = slugify(statement)[:30]
        subject_key = f"runbook.{section_slug}.{item_slug}"
        rec = empty_record("runbook_note", subject_key, statement, rel_path,
                           current_section, "doc")
        rec["authority"] = "confirmed"
        apply_default_scope(rec)
        records.append(rec)
    return records


def parse_approvals(src: str, rel_path: str) -> list:
    records = []

    # Parse always_ask_before rules
    kind_re = re.compile(r"-\s+kind:\s+(\S+)")
    reason_re = re.compile(r"reason:\s+[\"']?([^\"'\n]+)[\"']?")
    min_files_re = re.compile(r"min_files:\s+(\d+)")
    paths_re = re.compile(r"paths:\s*\n((?:[ \t]+-[^\n]*\n)*)", re.MULTILINE)

    in_always = False
    current_kind = None
    current_reason = None
    current_min_files = None

    for line in src.splitlines():
        if line.strip().startswith("always_ask_before:"):
            in_always = True
            continue
        if in_always:
            if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                # Top-level key ended
                if current_kind:
                    statement = f"Always ask before: {current_kind}"
                    if current_reason:
                        statement += f". Reason: {current_reason}"
                    if current_min_files:
                        statement += f". Min files: {current_min_files}"
                    subject_key = f"approval.always.{slugify(current_kind)}"
                    rec = empty_record("approval_rule", subject_key, statement,
                                      rel_path, "always_ask_before", "policy")
                    rec["authority"] = "enforced"
                    rec["scope"]["domains"] = ["approval-gates"]
                    records.append(rec)
                in_always = False
                current_kind = None
            km = kind_re.search(line)
            if km:
                # Save previous if any
                if current_kind:
                    statement = f"Always ask before: {current_kind}"
                    if current_reason:
                        statement += f". Reason: {current_reason}"
                    if current_min_files:
                        statement += f". Min files: {current_min_files}"
                    subject_key = f"approval.always.{slugify(current_kind)}"
                    rec = empty_record("approval_rule", subject_key, statement,
                                      rel_path, "always_ask_before", "policy")
                    rec["authority"] = "enforced"
                    rec["scope"]["domains"] = ["approval-gates"]
                    records.append(rec)
                current_kind = km.group(1)
                current_reason = None
                current_min_files = None
            rm = reason_re.search(line)
            if rm:
                current_reason = rm.group(1).strip()
            mfm = min_files_re.search(line)
            if mfm:
                current_min_files = mfm.group(1)

    if current_kind:
        statement = f"Always ask before: {current_kind}"
        if current_reason:
            statement += f". Reason: {current_reason}"
        if current_min_files:
            statement += f". Min files: {current_min_files}"
        subject_key = f"approval.always.{slugify(current_kind)}"
        rec = empty_record("approval_rule", subject_key, statement,
                           rel_path, "always_ask_before", "policy")
        rec["authority"] = "enforced"
        rec["scope"]["domains"] = ["approval-gates"]
        records.append(rec)

    # Parse ask_when flags
    ask_when_re = re.compile(r"^\s+(\w+):\s+true\s*$")
    in_ask_when = False
    for line in src.splitlines():
        if line.strip().startswith("ask_when:"):
            in_ask_when = True
            continue
        if in_ask_when:
            if line.strip() and not line.startswith(" ") and not line.startswith("\t"):
                in_ask_when = False
                continue
            m = ask_when_re.match(line)
            if m:
                flag = m.group(1)
                statement = f"Ask when: {flag}"
                subject_key = f"approval.ask_when.{slugify(flag)}"
                rec = empty_record("approval_rule", subject_key, statement,
                                   rel_path, "ask_when", "policy")
                rec["authority"] = "enforced"
                rec["scope"]["domains"] = ["approval-gates"]
                records.append(rec)

    # Parse path-based rules and extract scope.paths
    for m in paths_re.finditer(src):
        path_block = m.group(1)
        extracted = re.findall(r"-\s+(.+)", path_block)
        extracted = [p.strip().strip("'\"") for p in extracted if p.strip()]
        if extracted:
            statement = f"Approval gate paths: {', '.join(extracted)}"
            subject_key = f"approval.paths.{slugify(','.join(extracted)[:60])}"
            rec = empty_record("approval_rule", subject_key, statement,
                               rel_path, "paths", "policy")
            rec["authority"] = "enforced"
            rec["scope"]["domains"] = ["approval-gates"]
            rec["scope"]["paths"] = extracted
            records.append(rec)

    return records


RECENT_LINE_RE = re.compile(r"^-\s+\[(\d{4}-\d{2}-\d{2})\]\s+(\w+):\s+(.*)")


def parse_recent_decisions(src: str, rel_path: str) -> list:
    records = []
    for line in src.splitlines():
        m = RECENT_LINE_RE.match(line)
        if not m:
            continue
        date, rtype, desc = m.group(1), m.group(2), m.group(3).strip()
        kind_map = {
            "decision": "decision",
            "constraint": "constraint",
            "observed_fact": "observed_fact",
            "approval_rule": "approval_rule",
            "architecture": "observed_fact",
            "risk_zone": "observed_fact",
        }
        kind = kind_map.get(rtype, "observed_fact")
        # Stable subject key using date + type + slug of description
        subject_key = f"recent.{date}.{rtype}.{slugify(desc[:40])}"
        rec = empty_record(kind, subject_key, desc, rel_path,
                           "Recent decisions", "state")
        rec["authority"] = "observed"  # lower authority — state file
        rec["temporal"]["documented_at"] = date
        apply_default_scope(rec)
        records.append(rec)
    return records


UNKNOWNS_SECTION_RE = re.compile(r"^##\s+(.*)")
UNKNOWNS_LINE_RE = re.compile(r"^-\s+\[(\d{4}-\d{2}-\d{2})\]\s+\[([^\]]+)\]\s+(.*?)(?:\s+—\s+(.*))?$")


def parse_unknowns(src: str, rel_path: str) -> list:
    records = []
    current_section = "Unknowns"
    for line in src.splitlines():
        sm = UNKNOWNS_SECTION_RE.match(line)
        if sm:
            current_section = sm.group(1).strip()
            continue
        m = UNKNOWNS_LINE_RE.match(line)
        if not m:
            continue
        date, scope, text, extra = m.group(1), m.group(2), m.group(3).strip(), m.group(4)

        section_lower = current_section.lower()
        if "resolved" in section_lower:
            kind = "open_question"
            index_status = "resolved"
            authority = "observed"
        elif "hypothesis" in section_lower:
            kind = "hypothesis"
            index_status = "active"
            authority = "hypothesis"
        else:
            kind = "open_question"
            index_status = "active"
            authority = "hypothesis"

        # Try to parse outcome date from extra
        resolved_date = None
        resolves_adr = None
        if extra:
            rd = re.search(r"resolved:\s*(\d{4}-\d{2}-\d{2})", extra)
            if rd:
                resolved_date = rd.group(1)
            # Check if extra mentions an ADR resolution
            adr_ref = re.search(r"(ADR-\d+)", extra, re.IGNORECASE)
            if adr_ref:
                resolves_adr = adr_ref.group(1).upper()

        statement = text
        # Stable subject key: unknown.<scope>.<slug-of-text[:40]>
        subject_key = f"unknown.{slugify(scope)}.{slugify(text[:40])}"
        rec = empty_record(kind, subject_key, statement, rel_path,
                           current_section, "state")
        rec["authority"] = authority
        rec["index_status"] = index_status
        rec["temporal"]["documented_at"] = date
        if resolved_date:
            rec["temporal"]["last_verified_at"] = resolved_date

        # Derive scope from [scope] tag
        if re.search(r"[/\\]", scope) or scope.endswith("/"):
            rec["scope"]["paths"] = [scope]
            rec["scope"]["domains"] = ["unknowns"]
        else:
            rec["scope"]["domains"] = [scope, "unknowns"]

        # Stash ADR reference for second pass
        if resolves_adr:
            rec["_resolves_adr"] = resolves_adr

        records.append(rec)
    return records


def parse_memory_policy(src: str, rel_path: str) -> list:
    """Parse memory-policy.yaml into constraint records."""
    records = []

    # Parse persist.auto items
    auto_block_re = re.compile(r"persist:\s*\n\s+auto:\s*\n((?:\s+-[^\n]*\n)+)", re.MULTILINE)
    ask_block_re = re.compile(r"ask_first:\s*\n((?:\s+-[^\n]*\n)+)", re.MULTILINE)
    never_block_re = re.compile(r"never:\s*\n((?:\s+-[^\n]*\n)+)", re.MULTILINE)
    promotion_block_re = re.compile(
        r"-\s+from:\s+(\S+)\s*\n\s+to:\s+(\S+)\s*\n\s+when:\s*\n((?:\s+-[^\n]*\n)+)",
        re.MULTILINE
    )

    item_re = re.compile(r"-\s+(\S+)")

    auto_match = auto_block_re.search(src)
    if auto_match:
        for item_m in item_re.finditer(auto_match.group(1)):
            item = item_m.group(1)
            statement = f"auto-record: {item}"
            subject_key = f"memory-policy.persist.auto.{slugify(item)[:30]}"
            rec = empty_record("constraint", subject_key, statement, rel_path,
                               "persist.auto", "policy")
            rec["authority"] = "enforced"
            rec["scope"]["domains"] = ["memory-policy"]
            records.append(rec)

    ask_match = ask_block_re.search(src)
    if ask_match:
        for item_m in item_re.finditer(ask_match.group(1)):
            item = item_m.group(1)
            statement = f"ask-first before recording: {item}"
            subject_key = f"memory-policy.persist.ask.{slugify(item)[:30]}"
            rec = empty_record("constraint", subject_key, statement, rel_path,
                               "persist.ask_first", "policy")
            rec["authority"] = "enforced"
            rec["scope"]["domains"] = ["memory-policy"]
            records.append(rec)

    never_match = never_block_re.search(src)
    if never_match:
        for item_m in item_re.finditer(never_match.group(1)):
            item = item_m.group(1)
            statement = f"never record: {item}"
            subject_key = f"memory-policy.persist.never.{slugify(item)[:30]}"
            rec = empty_record("constraint", subject_key, statement, rel_path,
                               "persist.never", "policy")
            rec["authority"] = "enforced"
            rec["scope"]["domains"] = ["memory-policy"]
            records.append(rec)

    for prom_m in promotion_block_re.finditer(src):
        from_status = prom_m.group(1)
        to_status = prom_m.group(2)
        conditions = [c.strip() for c in item_re.findall(prom_m.group(3))]
        cond_str = ", ".join(conditions)
        statement = f"promotion: {from_status}→{to_status} requires {cond_str}"
        subject_key = f"memory-policy.promotion.{slugify(from_status)[:15]}.to.{slugify(to_status)[:15]}"
        rec = empty_record("constraint", subject_key, statement, rel_path,
                           "promotion", "policy")
        rec["authority"] = "enforced"
        rec["scope"]["domains"] = ["memory-policy"]
        records.append(rec)

    return records


def parse_domain_doc(src: str, rel_path: str, filename: str) -> list:
    """Parse a domains/*.md file into observed_fact records."""
    records = []
    # Determine domain from filename
    stem = filename.lower()
    if stem == "readme":
        domain = "domains"
    else:
        domain = stem

    current_section = domain
    for line in src.splitlines():
        h = re.match(r"^#{1,6}\s+(.*)", line)
        if h:
            current_section = h.group(1).strip()
            continue
        m = re.match(r"^[-*]\s+(.*)", line)
        if not m:
            continue
        text = m.group(1).strip()
        if not text or text.startswith("<!--") or text.startswith("<!--"):
            continue
        # Skip pure meta/template lines
        if re.match(r"^`[^`]+`\s+—", text):
            # these are file reference lines like "`plugin-system.md` — desc"
            statement = text
        else:
            statement = text

        if not statement.strip():
            continue

        dm = DATE_RE.search(statement)
        documented_at = dm.group(1) if dm else None
        statement = DATE_RE.sub("", statement).strip().lstrip("]").strip()
        if not statement:
            continue

        section_slug = slugify(current_section)[:20]
        item_slug = slugify(statement)[:30]
        subject_key = f"domain.{domain}.{section_slug}.{item_slug}"
        rec = empty_record("observed_fact", subject_key, statement, rel_path,
                           current_section, "doc")
        rec["authority"] = "observed"
        rec["temporal"]["documented_at"] = documented_at
        rec["scope"]["domains"] = [domain]
        records.append(rec)
    return records


# ---------------------------------------------------------------------------
# Second pass: relation resolution
# ---------------------------------------------------------------------------

def resolve_relations(all_records: list) -> None:
    """Second pass: resolve ADR cross-references and unknown resolutions."""
    # Build lookup: ADR-NNNN -> record id
    by_adr_ref = {}
    for rec in all_records:
        m = re.match(r"adr\.(\d+)\.", rec.get("subject_key", ""))
        if m:
            adr_ref = f"ADR-{m.group(1).zfill(4)}"
            by_adr_ref[adr_ref] = rec["id"]

    # Build lookup: id -> record
    by_id = {rec["id"]: rec for rec in all_records}

    for rec in all_records:
        # Handle superseded-by ADR on the OLD record
        superseded_by = rec.pop("_superseded_by_adr", None)
        if superseded_by and superseded_by in by_adr_ref:
            # The NEW ADR supersedes this (old) record
            new_rec_id = by_adr_ref[superseded_by]
            new_rec = by_id.get(new_rec_id)
            if new_rec:
                if rec["id"] not in new_rec["relations"]["supersedes"]:
                    new_rec["relations"]["supersedes"].append(rec["id"])

        # Handle resolves_adr on unknown records
        resolves_adr = rec.pop("_resolves_adr", None)
        if resolves_adr and resolves_adr in by_adr_ref:
            adr_id = by_adr_ref[resolves_adr]
            if adr_id not in rec["relations"]["resolves"]:
                rec["relations"]["resolves"].append(adr_id)


def token_overlap(a: str, b: str) -> float:
    """Return Jaccard token overlap between two statements (0.0–1.0)."""
    ta = set(re.findall(r"[a-z0-9]+", a.lower()))
    tb = set(re.findall(r"[a-z0-9]+", b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


# Authority ranking: higher = more authoritative
AUTHORITY_RANK = {
    "enforced": 4,
    "confirmed": 3,
    "observed": 2,
    "hypothesis": 1,
}


def deduplicate_cross_source(all_records: list) -> None:
    """Detect near-duplicate records from different sources and link via extends."""
    # Only consider active records
    active = [r for r in all_records if r["index_status"] == "active"]

    for i, rec_a in enumerate(active):
        for rec_b in active[i + 1:]:
            # Must be different sources
            src_a = rec_a["provenance"]["source_path"]
            src_b = rec_b["provenance"]["source_path"]
            if src_a == src_b:
                continue
            # Must have same kind
            if rec_a["kind"] != rec_b["kind"]:
                continue
            # Check token overlap threshold
            overlap = token_overlap(rec_a["statement"], rec_b["statement"])
            if overlap < 0.80:
                continue
            # Determine which is higher authority
            rank_a = AUTHORITY_RANK.get(rec_a["authority"], 0)
            rank_b = AUTHORITY_RANK.get(rec_b["authority"], 0)
            if rank_a >= rank_b:
                higher, lower = rec_a, rec_b
            else:
                higher, lower = rec_b, rec_a
            # Lower-authority record extends higher-authority record
            if higher["id"] not in lower["relations"]["extends"]:
                lower["relations"]["extends"].append(higher["id"])


# ---------------------------------------------------------------------------
# Clean rebuild
# ---------------------------------------------------------------------------

def clean_generated(output_dir: Path) -> None:
    """Remove generated subtrees, preserve README.md and VERSION."""
    for subdir in ["source-shards", "active", "timeline"]:
        target = output_dir / subdir
        if target.exists():
            shutil.rmtree(target)
    manifest = output_dir / "manifest.json"
    if manifest.exists():
        manifest.unlink()


# ---------------------------------------------------------------------------
# Main compiler
# ---------------------------------------------------------------------------

def collect_records(repo_root: Path) -> dict:
    """Returns dict: rel_path -> list of records."""
    all_shards = {}

    def add(rel_path: str, recs: list):
        all_shards[rel_path] = recs

    # constraints
    p = repo_root / "harness/docs/constraints/project-constraints.md"
    if p.exists():
        add("docs/constraints/project-constraints", parse_constraints(p.read_text(), str(p.relative_to(repo_root))))

    # ADRs
    adr_dir = repo_root / "harness/docs/decisions"
    if adr_dir.exists():
        for f in sorted(adr_dir.glob("ADR-*.md")):
            rel = str(f.relative_to(repo_root))
            add(f"docs/decisions/{f.stem}", parse_adr(f.read_text(), rel, f.stem))

    # Requirements
    req_dir = repo_root / "harness/docs/requirements"
    if req_dir.exists():
        for f in sorted(req_dir.glob("REQ-*.md")):
            rel = str(f.relative_to(repo_root))
            add(f"docs/requirements/{f.stem}", parse_requirements(f.read_text(), rel, f.stem))

    # Architecture
    arch_dir = repo_root / "harness/docs/architecture"
    if arch_dir.exists():
        for f in sorted(arch_dir.glob("*.md")):
            rel = str(f.relative_to(repo_root))
            add(f"docs/architecture/{f.stem}", parse_architecture(f.read_text(), rel))

    # Runbooks
    rb_dir = repo_root / "harness/docs/runbooks"
    if rb_dir.exists():
        for f in sorted(rb_dir.glob("*.md")):
            rel = str(f.relative_to(repo_root))
            add(f"docs/runbooks/{f.stem}", parse_runbook(f.read_text(), rel))

    # Approvals policy
    p = repo_root / "harness/policies/approvals.yaml"
    if p.exists():
        add("policies/approvals", parse_approvals(p.read_text(), str(p.relative_to(repo_root))))

    # Memory policy
    p = repo_root / "harness/policies/memory-policy.yaml"
    if p.exists():
        add("policies/memory-policy", parse_memory_policy(p.read_text(), str(p.relative_to(repo_root))))

    # Domain docs
    domains_dir = repo_root / "harness/docs/domains"
    if domains_dir.exists():
        for f in sorted(domains_dir.glob("*.md")):
            rel = str(f.relative_to(repo_root))
            add(f"docs/domains/{f.stem}", parse_domain_doc(f.read_text(), rel, f.stem))

    # State: recent decisions
    p = repo_root / "harness/state/recent-decisions.md"
    if p.exists():
        add("state/recent-decisions", parse_recent_decisions(p.read_text(), str(p.relative_to(repo_root))))

    # State: unknowns
    p = repo_root / "harness/state/unknowns.md"
    if p.exists():
        add("state/unknowns", parse_unknowns(p.read_text(), str(p.relative_to(repo_root))))

    return all_shards


def build_index(repo_root: Path, output_dir: Path) -> None:
    # Clean before rebuild
    clean_generated(output_dir)

    all_shards = collect_records(repo_root)

    # Flatten all records
    all_records = []
    for recs in all_shards.values():
        all_records.extend(recs)

    # Second pass: resolve cross-references
    resolve_relations(all_records)

    # Third pass: cross-source deduplication (near-duplicates link via extends)
    deduplicate_cross_source(all_records)

    # Stable sort by id
    all_records.sort(key=lambda r: r["id"])

    # Write source shards
    for shard_key, recs in sorted(all_shards.items()):
        shard_recs = sorted(recs, key=lambda r: r["id"])
        shard_obj = {
            "records": shard_recs,
            "source_shard": shard_key,
        }
        write_json(output_dir / "source-shards" / f"{shard_key}.json", shard_obj)

    # Build active/ indexes
    by_subject: dict = {}
    by_domain: dict = {}
    by_path: dict = {}

    for rec in all_records:
        if rec["index_status"] != "active":
            continue
        sk = rec["subject_key"]
        by_subject.setdefault(sk, []).append(rec)

        for domain in rec["scope"]["domains"]:
            by_domain.setdefault(domain, []).append(rec)

        for path in rec["scope"]["paths"]:
            path_key = slugify(path)
            by_path.setdefault(path_key, []).append(rec)

    for sk, recs in sorted(by_subject.items()):
        write_json(output_dir / "active" / "by-subject" / f"{sk}.json",
                   {"records": sorted(recs, key=lambda r: r["id"]), "subject_key": sk})

    for domain, recs in sorted(by_domain.items()):
        write_json(output_dir / "active" / "by-domain" / f"{domain}.json",
                   {"domain": domain, "records": sorted(recs, key=lambda r: r["id"])})

    for path_key, recs in sorted(by_path.items()):
        write_json(output_dir / "active" / "by-path" / f"{path_key}.json",
                   {"path_key": path_key, "records": sorted(recs, key=lambda r: r["id"])})

    # Build timeline/ — group all records by subject_key across time
    timeline: dict = {}
    for rec in all_records:
        sk = rec["subject_key"]
        timeline.setdefault(sk, []).append(rec)

    for sk, recs in sorted(timeline.items()):
        write_json(output_dir / "timeline" / f"{sk}.json",
                   {"records": sorted(recs, key=lambda r: r["id"]), "subject_key": sk})

    # Count multi-record subjects (same subject_key, 2+ records across ALL records including superseded)
    subject_count: dict = {}
    for rec in all_records:
        sk = rec["subject_key"]
        subject_count[sk] = subject_count.get(sk, 0) + 1
    multi_record_subjects = sum(1 for c in subject_count.values() if c >= 2)
    # Also count subjects that have a supersedes relation (old + new = 2+ logical records)
    superseded_subjects = sum(
        1 for rec in all_records if rec["relations"]["supersedes"]
    )

    # Write manifest
    sources = sorted(all_shards.keys())
    manifest = {
        "multi_record_subjects": multi_record_subjects,
        "record_count": len(all_records),
        "schema_version": 1,
        "sources": sources,
        "superseded_subjects": superseded_subjects,
        "version": 1,
    }
    write_json(output_dir / "manifest.json", manifest)

    # Write VERSION
    (output_dir / "VERSION").write_text("1\n", encoding="utf-8")

    print(f"Built memory index: {len(all_records)} records, {len(sources)} sources → {output_dir}")


def main():
    parser = argparse.ArgumentParser(description="Build deterministic harness memory index")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: harness/memory-index/ relative to repo root)",
    )
    parser.add_argument(
        "--repo-root",
        default=None,
        help="Repository root (default: auto-detected from script location)",
    )
    args = parser.parse_args()

    # Resolve repo root
    if args.repo_root:
        repo_root = Path(args.repo_root).resolve()
    else:
        # Script lives in harness/scripts/, repo root is two levels up
        repo_root = Path(__file__).resolve().parent.parent.parent

    # Resolve output dir
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = repo_root / "harness" / "memory-index"

    build_index(repo_root, output_dir)


if __name__ == "__main__":
    main()
