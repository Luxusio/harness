#!/usr/bin/env python3
"""Report whether the registered harness hook tree can record receipts.

Receipts are hook-owned by contract (C-14): `SubagentStart`/`SubagentStop` run
`background_hook.py`, which is the only authorized writer of `RECEIPTS.jsonl`.
If the tree the session loaded its hooks from does not contain that machinery,
no receipt can ever be written, `task_verify` can never reach PASS, and no
standard task can close.

That failure is silent by construction. On 2026-08-26 the session loaded hooks
from `~/.claude/plugins/cache/harness/harness/2.3.0` — registered in
`installed_plugins.json` on 2026-05-21 and never re-resolved after the
marketplace was repointed at `~/.claude/harness-dev`. That tree predates the
receipt subsystem entirely: no `background_hook.py`, no `subagent_lifecycle.py`,
and no `SubagentStart`/`SubagentStop` registration. Every *other* gate
(`prewrite_gate.py`, `mcp_bash_guard.py`, `stop_gate.py`, `prompt_memory.py`)
does exist there and fired normally all session, so the harness presented as
fully healthy. The only symptom was an absence: no receipts, and — because
`background_hook.py` was never on disk to run — not even a `binding-miss`
breadcrumb to point at.

Compounding it, MCP and hooks can resolve to *different* trees. That session's
MCP server ran current code from `harness-dev` (it returned `required_lenses`
and `close_receipt_fingerprint`, strings absent from the 2.3.0 server) while
hooks ran from the May cache. That split is what makes this module useful and
also what dictates where it is called from: a SessionStart hook would be loaded
from the same stale tree it needs to indict, so in the failure mode it does not
exist and cannot run. The MCP server is the surface proven to execute current
code here, so `task_start` is the caller.

This module only reports. It does not disable gates and does not mutate
`~/.claude` state. Silencing the gates when receipts are unavailable would strip
protected-artifact enforcement exactly when the harness is least trustworthy;
the defect was never that the gates run, only that their running was read as
evidence of health.

Every failure path returns "no finding". A missed warning is a regression; an
exception raised into `task_start` would be an outage.
"""
from __future__ import annotations

import json
import os

PLUGIN_KEY = "harness@harness"
# Modules without which no receipt can be written, whatever else is present.
RECEIPT_MODULES = ("background_hook.py", "subagent_lifecycle.py")
RECEIPT_EVENTS = ("SubagentStart", "SubagentStop")


def _config_dir() -> str:
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(
        os.path.expanduser("~"), ".claude"
    )


def registered_hook_root(config_dir: str | None = None) -> str:
    """Return the install path registered for the harness plugin, or ''.

    Read-only. Returns '' rather than raising when the file is absent,
    unreadable, not JSON, or shaped differently than expected — an unknown
    layout must not be reported as a broken one.
    """
    path = os.path.join(config_dir or _config_dir(), "plugins", "installed_plugins.json")
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return ""
    if not isinstance(data, dict):
        return ""
    entries = data.get("plugins")
    if not isinstance(entries, dict):
        return ""
    records = entries.get(PLUGIN_KEY)
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        return ""
    for record in records:
        if not isinstance(record, dict):
            continue
        install_path = record.get("installPath")
        if isinstance(install_path, str) and install_path.strip():
            return install_path.strip()
    return ""


def _scripts_dirs(root: str) -> tuple[str, ...]:
    # Layout moved between releases: 2.3.0 kept scripts/ at the tree root,
    # current builds nest them under plugin/. Accept either so a healthy tree
    # of any vintage is never reported as broken.
    return (os.path.join(root, "plugin", "scripts"), os.path.join(root, "scripts"))


def _hooks_files(root: str) -> tuple[str, ...]:
    return (
        os.path.join(root, "plugin", "hooks", "hooks.json"),
        os.path.join(root, "hooks", "hooks.json"),
    )


def _has_receipt_modules(root: str) -> bool:
    for scripts in _scripts_dirs(root):
        if all(os.path.isfile(os.path.join(scripts, name)) for name in RECEIPT_MODULES):
            return True
    return False


def _registers_receipt_events(root: str) -> bool:
    """True when a readable hooks.json registers both subagent events.

    An unreadable, absent, or unparseable hooks.json returns False and is
    therefore sufficient on its own to indict the tree: the caller requires
    modules AND registration. That is deliberate. A hooks.json we cannot parse
    is a hooks.json whose registration we cannot vouch for, and the cost of
    being wrong is asymmetric — a false warning is an advisory telling a healthy
    user to update and restart, while a false silence reproduces the outage this
    module exists to end. Relaxing to "modules present is enough" would restore
    that silence for any tree whose hooks.json drifts to a format we cannot
    read.
    """
    for path in _hooks_files(root):
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if not isinstance(data, dict):
            continue
        hooks = data.get("hooks")
        hooks = hooks if isinstance(hooks, dict) else data
        if all(event in hooks for event in RECEIPT_EVENTS):
            return True
    return False


def receipt_capability_warning(config_dir: str | None = None) -> str:
    """Return a warning when the registered hook tree cannot record receipts.

    Returns '' when the tree looks capable, when the registration cannot be
    resolved, or on any error. Never raises.
    """
    try:
        root = registered_hook_root(config_dir)
        if not root or not os.path.isdir(root):
            # Nothing registered, or a path we cannot see. Plenty of valid
            # setups look like this; do not cry wolf.
            return ""
        if _has_receipt_modules(root) and _registers_receipt_events(root):
            return ""
        return (
            "harness hook tree cannot record receipts: "
            f"{root} is missing the SubagentStart/SubagentStop receipt subsystem. "
            "Subagents will run and return verdicts, but no RECEIPTS.jsonl entry "
            "will be written, so task_verify cannot reach PASS and task_close "
            "will refuse. Fix: update the harness plugin so it resolves against "
            "your marketplace tree (e.g. `/plugin update harness@harness`), then "
            "restart the session — hook registration is only read at session "
            "start. Planning and implementation still work in this session."
        )
    except Exception:
        return ""


if __name__ == "__main__":
    message = receipt_capability_warning()
    if message:
        print(message)
    else:
        root = registered_hook_root()
        print(f"receipt-capable hook tree: {root or '<unresolved>'}")
