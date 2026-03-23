#!/usr/bin/env python3
"""Build a memory overlay from current session state files.

Reads:
  - harness/state/current-task.yaml
  - harness/state/last-session-summary.md

Writes:
  - .harness-cache/memory-overlay/records.jsonl
  - .harness-cache/memory-overlay/manifest.json

Records use the same schema as compiled index records but with:
  kind: overlay_context
  authority: observed
  index_status: active
  id: overlay:<source>:<hash>
"""
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path


# ── Paths ────────────────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
STATE_DIR = REPO_ROOT / "harness" / "state"
CURRENT_TASK_PATH = STATE_DIR / "current-task.yaml"
LAST_SESSION_PATH = STATE_DIR / "last-session-summary.md"
OVERLAY_DIR = REPO_ROOT / ".harness-cache" / "memory-overlay"
RECORDS_PATH = OVERLAY_DIR / "records.jsonl"
MANIFEST_PATH = OVERLAY_DIR / "manifest.json"


# ── Helpers ──────────────────────────────────────────────────────────────────

def short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:8]


def make_record(source: str, subject_key: str, statement: str,
                source_path: str = "", source_section: str = "",
                domains: list = None, paths: list = None) -> dict:
    uid = short_hash(f"{source}:{subject_key}:{statement}")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return {
        "authority": "observed",
        "id": f"overlay:{source}:{uid}",
        "index_status": "active",
        "kind": "overlay_context",
        "provenance": {
            "locator": source_section or source,
            "source_path": source_path or f"harness/state/{_source_filename(source)}",
            "source_section": source_section or source,
            "source_type": "state",
        },
        "relations": {
            "conflicts_with": [],
            "extends": [],
            "resolves": [],
            "supersedes": [],
        },
        "scope": {
            "api_surfaces": [],
            "domains": domains or [],
            "paths": paths or [],
        },
        "source_status": None,
        "statement": statement,
        "subject_key": subject_key,
        "tags": ["overlay", "session"],
        "temporal": {
            "documented_at": today,
            "effective_at": today,
            "last_verified_at": today,
        },
    }


def _source_filename(source: str) -> str:
    mapping = {
        "current-task": "current-task.yaml",
        "last-session": "last-session-summary.md",
    }
    return mapping.get(source, source)


# ── current-task.yaml parser ─────────────────────────────────────────────────

def _yaml_scalar(text: str, key: str) -> str:
    """Extract a simple scalar value for a key from minimal YAML."""
    for line in text.splitlines():
        m = re.match(rf'^{re.escape(key)}:\s*"?([^"#\n]*?)"?\s*(?:#.*)?$', line)
        if m:
            val = m.group(1).strip()
            return val
    return ""


def _yaml_list(text: str, key: str) -> list:
    """Extract an inline or block list for a key from minimal YAML."""
    # Try inline list: key: [a, b, c]
    for line in text.splitlines():
        m = re.match(rf'^{re.escape(key)}:\s*\[([^\]]*)\]', line)
        if m:
            items = [i.strip().strip('"').strip("'") for i in m.group(1).split(",") if i.strip()]
            return items

    # Try block list: lines starting with "- " after the key line
    lines = text.splitlines()
    in_key = False
    items = []
    for line in lines:
        if re.match(rf'^{re.escape(key)}:', line):
            in_key = True
            continue
        if in_key:
            if re.match(r'^\s*-\s+(.+)', line):
                m = re.match(r'^\s*-\s+"?([^"#\n]*?)"?\s*(?:#.*)?$', line)
                if m:
                    val = m.group(1).strip()
                    if val:
                        items.append(val)
            elif re.match(r'^[a-zA-Z]', line):
                break
    return items


def _classify_scope_items(scope_items: list) -> tuple:
    """Split scope list items into path-like entries and domain entries."""
    paths = []
    domains = []
    for item in scope_items:
        # Path-like: contains '/', starts with '.', or has a file extension
        if '/' in item or item.startswith('.') or re.search(r'\.\w{1,6}$', item):
            paths.append(item)
        else:
            domains.append(item)
    return paths, domains


def parse_current_task(records: list) -> None:
    if not CURRENT_TASK_PATH.exists():
        return

    text = CURRENT_TASK_PATH.read_text(encoding="utf-8")

    intent = _yaml_scalar(text, "intent")
    scope = _yaml_list(text, "scope")
    risk_level = _yaml_scalar(text, "risk_level")
    status = _yaml_scalar(text, "status")

    source_path = f"harness/state/{_source_filename('current-task')}"
    scope_paths, scope_domains = _classify_scope_items(scope)

    if intent:
        records.append(make_record(
            "current-task",
            "current.task.intent",
            f"Current task intent: {intent}",
            source_path=source_path,
            source_section="intent",
            domains=["current-task"],
            paths=scope_paths,
        ))

    if scope:
        scope_str = ", ".join(scope)
        records.append(make_record(
            "current-task",
            "current.task.scope",
            f"Current task scope: {scope_str}",
            source_path=source_path,
            source_section="scope",
            domains=["current-task"] + scope_domains,
            paths=scope_paths,
        ))

    if risk_level:
        records.append(make_record(
            "current-task",
            "current.task.risk_level",
            f"Current task risk level: {risk_level}",
            source_path=source_path,
            source_section="risk_level",
            domains=["current-task"],
        ))

    if status:
        records.append(make_record(
            "current-task",
            "current.task.status",
            f"Current task status: {status}",
            source_path=source_path,
            source_section="status",
            domains=["current-task"],
        ))


# ── last-session-summary.md parser ───────────────────────────────────────────

# Sections we extract, mapped to subject_key prefixes
_SECTION_MAP = {
    "Changed": "last.session.changed",
    "Validated": "last.session.validated",
    "Recorded": "last.session.recorded",
    "Unknown": "last.session.unknown",
    "Follow-up": "last.session.followup",
}


def parse_last_session(records: list) -> None:
    if not LAST_SESSION_PATH.exists():
        return

    text = LAST_SESSION_PATH.read_text(encoding="utf-8")

    # Strip HTML comments
    text = re.sub(r'<!--.*?-->', '', text, flags=re.DOTALL)

    current_section = None
    bullets: dict[str, list] = {k: [] for k in _SECTION_MAP.values()}

    for line in text.splitlines():
        # Detect section headings (bold or heading markers)
        heading_match = re.match(
            r'^(?:#{1,6}\s+|\*{1,2}|\*\*)?(Changed|Validated|Recorded|Unknown|Follow-up):?\*{0,2}\s*$',
            line.strip()
        )
        if heading_match:
            current_section = _SECTION_MAP.get(heading_match.group(1))
            continue

        # Bullet point under a known section
        if current_section:
            bullet_match = re.match(r'^\s*[-*]\s+(.+)', line)
            if bullet_match:
                content = bullet_match.group(1).strip()
                if content:
                    bullets[current_section].append(content)
            elif line.strip() and not re.match(r'^\s*#', line):
                # Plain text continuation line (not a heading)
                if not re.match(r'^(?:#{1,6}|\*{1,2})', line.strip()):
                    pass  # ignore non-bullet content between sections

    source_path = f"harness/state/{_source_filename('last-session')}"
    for subject_key, items in bullets.items():
        if not items:
            continue
        section_name = next(k for k, v in _SECTION_MAP.items() if v == subject_key)
        combined = "; ".join(items)
        # Extract path-like entries from bullet text for scope.paths
        extracted_paths = []
        for item in items:
            # Find path-like tokens: contain '/', start with '.', or have file extensions
            tokens = re.findall(r'[\w./\-]+', item)
            for token in tokens:
                if '/' in token or token.startswith('.') or re.search(r'\.\w{1,6}$', token):
                    extracted_paths.append(token)
        records.append(make_record(
            "last-session",
            subject_key,
            f"{section_name}: {combined}",
            source_path=source_path,
            source_section=section_name,
            domains=["last-session"],
            paths=extracted_paths,
        ))


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> int:
    OVERLAY_DIR.mkdir(parents=True, exist_ok=True)

    records: list = []
    parse_current_task(records)
    parse_last_session(records)

    with RECORDS_PATH.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    manifest = {
        "record_count": len(records),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "sources": [
            str(CURRENT_TASK_PATH.relative_to(REPO_ROOT)),
            str(LAST_SESSION_PATH.relative_to(REPO_ROOT)),
        ],
    }
    with MANIFEST_PATH.open("w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Overlay built: {len(records)} records → {RECORDS_PATH.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
