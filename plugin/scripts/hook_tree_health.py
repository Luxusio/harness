#!/usr/bin/env python3
"""Report whether the registered harness hook tree can record receipts.

Receipts are lifecycle-owned by contract (C-14). In Claude,
`SubagentStart`/`SubagentStop` run `background_hook.py`, the authorized writer
for that runtime; Codex instead uses `codex_lifecycle_watcher.py`. If a Claude
session's loaded hook tree does not contain its lifecycle machinery, no Claude
receipt can be written, `task_verify` can never reach PASS, and no standard
task can close.

That failure is silent by construction. On 2026-08-26 the session loaded hooks
from `~/.claude/plugins/cache/harness/harness/2.3.0` — registered in
`installed_plugins.json` on 2026-05-21 and never re-resolved after the
marketplace was repointed at `~/.claude/harness-dev`. That tree predates the
receipt subsystem entirely: no `background_hook.py`, no `subagent_lifecycle.py`,
and no `SubagentStart`/`SubagentStop` registration. Every *other* hook
(`prewrite_gate.py`, `stop_gate.py`, `prompt_memory.py`)
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

This module only reports. It does not disable hooks and does not mutate
`~/.claude` state. The defect was never that other hooks run, only that their
running was read as evidence that receipt lifecycle hooks were present.

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


def _runtime() -> str:
    """Return the explicit runtime, or infer Codex from its thread identity."""
    configured = str(os.environ.get("HARNESS_RUNTIME") or "").strip().lower()
    if configured:
        return configured
    if str(os.environ.get("CODEX_THREAD_ID") or "").strip():
        return "codex"
    return "claude"


def _codex_registration_present() -> bool:
    """Return whether this exact Codex root thread has a live registration."""
    try:
        from _lib import find_harness_root, read_session_hint  # type: ignore
        from codex_lifecycle_watcher import (  # type: ignore
            registration_host_live,
            registrations,
        )

        repo_root = find_harness_root(os.getcwd())
        if not repo_root:
            return False
        thread_id = str(
            os.environ.get("CODEX_THREAD_ID")
            or read_session_hint(repo_root)
            or ""
        ).strip()
        if not thread_id:
            return False
        registered = any(
            item.get("thread_id") == thread_id
            for item in registrations(repo_root)
        )
        return registered and registration_host_live(repo_root, thread_id)
    except Exception:
        return False


def _codex_capability_warning() -> str:
    if _codex_registration_present():
        return ""
    return (
        "Codex receipt watcher registration and live host are not positively confirmed for "
        "this root thread. Do not launch review or QA lenses: their verdicts "
        "may not be recorded. Repair or refresh the Codex hook registration, "
        "then rerun this check."
    )


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
        # Codex receipts are recorded by the MCP-hosted lifecycle watcher, not
        # Claude's registered SubagentStart/SubagentStop hook tree.  An explicit
        # config_dir still means "inspect this Claude registration" and keeps
        # the diagnostic helper useful from either runtime.
        if config_dir is None and _runtime() == "codex":
            return _codex_capability_warning()
        root = registered_hook_root(config_dir)
        if not root or not os.path.isdir(root):
            # Nothing registered, or a path we cannot see. Plenty of valid
            # setups look like this; do not cry wolf.
            return ""
        if _has_receipt_modules(root) and _registers_receipt_events(root):
            return ""
        # Hedged on purpose. This inspects the *registered* plugin path, not
        # whether receipts are actually being written, and the two come apart:
        # a session whose loaded hooks demonstrably write receipts still trips
        # this when the registry entry points at a stale cached tree. Stating
        # "no entry will be written" as fact was measurably false in exactly
        # that case, and sent the user to repair a problem they did not have.
        return (
            "harness hook tree may not be able to record receipts: "
            f"{root} is missing the SubagentStart/SubagentStop receipt subsystem. "
            "This checks which plugin path is registered, not whether receipts "
            "are being written, so it can fire in a session that is in fact "
            "recording — check RECEIPTS.jsonl for the current run before acting. "
            "If receipts really are missing, subagents will run and return "
            "verdicts that are never recorded, and task_close will refuse. "
            "Fix: update the harness plugin so it resolves against "
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
    elif _runtime() == "codex":
        print("receipt-capable runtime: Codex lifecycle watcher registration confirmed")
    else:
        root = registered_hook_root()
        print(f"receipt-capable hook tree: {root or '<unresolved>'}")
