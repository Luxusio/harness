#!/usr/bin/env python3
"""AC-001 spike: capture Codex hook payloads to tests/payload/.

Reads JSON envelope from stdin, writes to tests/payload/codex_<event>__<ts>__<session>.json.
Always exits 0 with empty output so it never blocks the codex session.

Spike-only logger — production hook crash-logging is AC-007's scope.
"""
from __future__ import annotations
import datetime as _dt
import json
import pathlib
import sys

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT_DIR = REPO_ROOT / "tests" / "payload"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def _read_envelope() -> dict:
    raw = sys.stdin.read()
    if not raw.strip():
        return {"_spike_note": "empty stdin"}
    try:
        return json.loads(raw)
    except json.JSONDecodeError as e:
        return {"_spike_parse_error": str(e), "_raw_head": raw[:500]}


def main() -> int:
    env = _read_envelope()
    event = env.get("hook_event_name") or env.get("hookEventName") or "unknown"
    session = (env.get("session_id") or env.get("sessionId") or "no-session")[:8]
    ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = OUT_DIR / f"codex_{event}__{ts}__{session}.json"
    record = {
        "_captured_at": ts,
        "_event_inferred": event,
        "_keys_at_top_level": sorted(env.keys()) if isinstance(env, dict) else "non-dict",
        "envelope": env,
    }
    out.write_text(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
