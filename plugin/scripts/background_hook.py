#!/usr/bin/env python3
"""Claude SubagentStart/SubagentStop adapter for receipt-backed lifecycle."""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _harness_root_above(start: str) -> str:
    """First ancestor of ``start`` (inclusive) that holds ``doc/harness``."""
    root = start
    while root:
        if os.path.isdir(os.path.join(root, "doc", "harness")):
            return root
        parent = os.path.dirname(root)
        if parent == root:
            return ""
        root = parent
    return ""


def _payload_harness_root() -> str:
    """Resolve the repo from the hook payload's ``cwd``, or '' when unavailable.

    Only reachable from the import-failure path, which exits immediately after,
    so consuming stdin here cannot starve ``read_hook_input`` on the healthy
    path. ``isatty`` keeps an interactive invocation from blocking on a read
    that will never return.
    """
    try:
        import json

        stdin = sys.stdin
        if stdin is None or stdin.isatty():
            return ""
        payload = json.loads(stdin.read() or "{}")
        cwd = str((payload or {}).get("cwd") or "").strip()
    except Exception:
        return ""
    return _harness_root_above(os.path.abspath(cwd)) if cwd else ""


CAPABILITY_MARKER_RELPATH = os.path.join("doc", "harness", ".receipt-capability-broken")


def _capability_marker_path(root: str) -> str:
    return os.path.join(root, CAPABILITY_MARKER_RELPATH)


def _clear_capability_marker(root: str) -> None:
    """Drop the broken-capability marker once the import path works again.

    A marker that outlived its cause would make the MCP report a dead receipt
    subsystem for a session whose hooks are demonstrably importing, and a false
    `receipts_recordable: false` is its own outage.

    `root` is passed in rather than resolved here, and this is called from
    `main()` rather than at import time, for one specific reason:
    `_payload_harness_root` consumes stdin. Calling it on the healthy path
    starves `read_hook_input`, the payload arrives empty, and no receipt is
    written — the exact outage this file exists to prevent, reintroduced by the
    fix for it. The installed-runtime smoke test in `install.py` catches this,
    and did. Never raises.
    """
    try:
        if not root:
            return
        os.unlink(_capability_marker_path(root))
    except Exception:
        pass


def _write_capability_marker(root: str, exc: BaseException, remedy: str) -> None:
    """Record that this hook could not import, for the MCP to read back.

    The MCP cannot otherwise tell a live receipt subsystem from a dead one: it
    runs in its own process, often from a different tree than the one hooks
    load, which is precisely why `receipt_capability_warning` is documented as a
    suspicion rather than an observation. This marker is an observation — the
    hook that failed wrote it — so the MCP can move from `None` to a definite
    `False` without guessing. Stdlib only; never raises.
    """
    try:
        import json
        import stat

        from datetime import datetime, timezone

        path = _capability_marker_path(root)
        parent = os.path.dirname(path)
        # `O_NOFOLLOW` below guards only the final component. A symlinked
        # `doc/harness` — survivable through checkout, since Git tracks
        # symlinks — would otherwise redirect the write outside the repo.
        # Mirrors `_lib.write_json_diagnostics`'s confinement check, which
        # cannot be called here because `_lib` may be the broken import.
        real_root = os.path.realpath(root)
        if not os.path.realpath(parent).startswith(real_root + os.sep):
            return
        # Created only after confinement passes. Creating it first let a
        # symlinked `doc`/`doc/harness` have one empty directory materialized at
        # the link target before the check refused the write — small, but there
        # is no reason to do it on a path already known to be rejected.
        os.makedirs(parent, exist_ok=True)
        # `O_NOFOLLOW`, not plain `open(..., "w")`. A symlink planted at this
        # fixed name — committable on a hostile branch, since `.gitignore` does
        # not stop checkout of a tracked path — would otherwise be followed and
        # the target truncated, by a writer that runs whenever a hook import
        # fails. `_lib.write_json_diagnostics` already exists for exactly this
        # hazard on the sibling diagnostics file; `_lib` is unavailable here by
        # design, so the same protection is applied inline.
        #
        # `O_NONBLOCK` and the `fstat` are the other half of that protection:
        # `O_NOFOLLOW` rejects a symlink but happily opens a FIFO, and a FIFO
        # planted at this fixed, gitignored, predictable name blocks the open
        # until a reader appears — stalling the hook until its 3s timeout kills
        # it. That is a C-12 fail-safe break, so the same `S_ISREG` gate
        # `_lib.read_json_diagnostics` uses on the read side applies here.
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NONBLOCK", 0)
        fd = os.open(path, flags, 0o600)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "error": f"{type(exc).__name__}: {exc}",
                    "remedy": remedy,
                    "source": "background_hook:import",
                },
                handle,
                ensure_ascii=False,
            )
    except Exception:
        pass


def _is_stale_bytecode_failure(exc: BaseException) -> bool:
    """Is this the recoverable cache shape rather than a compromised module?

    Matched by name and message, never by `isinstance`. The class that names
    this condition lives in `_lib`, which is one of the modules a poisoned cache
    can corrupt — importing it to ask the question is exactly the import that
    just failed. A stale `_lib` can even raise `NameError` for the class it no
    longer defines, so the message is checked too and that case still matches.
    """
    for error in (exc, getattr(exc, "__cause__", None), getattr(exc, "__context__", None)):
        if error is None:
            continue
        if type(error).__name__ == "StaleBytecodeCacheError":
            return True
        if "stale bytecode cache" in str(error) or "StaleBytecodeCacheError" in str(error):
            return True
    return False


def _prune_sibling_bytecode_cache() -> str:
    """Delete the `__pycache__` beside this script so the next run recovers.

    The failing modules (`_lib`, `subagent_lifecycle`) are siblings of this
    file, so their cache is the one directory that has to go. Bounded the same
    way `install.py:_prune_bytecode_caches` is bounded: one `__pycache__`, only
    inside the tree this script already lives in, and only a real directory —
    `islink` rejects a symlink before `rmtree` is reached, so nothing outside
    the tree is reachable. Regeneration is automatic.

    Returns a short outcome string for the breadcrumb. Never raises.
    """
    cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
    try:
        if os.path.islink(cache) or not os.path.isdir(cache):
            return f"no bytecode cache to prune at {cache}"
        import shutil

        shutil.rmtree(cache)
        return f"pruned bytecode cache {cache}; next invocation recompiles"
    except Exception as exc:  # pragma: no cover - reported, never raised
        return f"could not prune bytecode cache {cache}: {type(exc).__name__}: {exc}"


def _report_import_failure(exc: BaseException) -> None:
    """Leave a breadcrumb when this hook cannot import its own dependencies.

    Without it the receipt subsystem fails completely silently: the hook runs,
    exits 0, writes no receipt, and logs nothing — because every logger it would
    use lives in the import that just failed. On 2026-08-26 a stale
    `__pycache__` entry made `subagent_lifecycle` raise PermissionError from the
    receipt-adapter binding, disabling receipts entirely. Three sessions
    diagnosed it as three different causes because there was no signal at all.

    The repository is resolved from the hook **payload**, not from this file:
    hooks execute from the installed tree (`~/.claude/.../plugin/scripts`),
    which has no `doc/harness` above it, so the script-relative walk found
    nothing and the guard added in response to 2026-08-26 stayed silent through
    a month-long recurrence. The walk is kept only as a fallback for callers
    that pass no payload. See
    `doc/harness/REQ__receipt-subsystem-failures-are-observable.md`.

    Stdlib only, and deliberately not `_lib`: the point is to work when `_lib`
    is exactly what is broken. Never raises into the hook.
    """
    try:
        import json
        import traceback
        from datetime import datetime, timezone

        # Self-heal before the breadcrumb is composed, so the entry records what
        # actually happened rather than an intention. Ordered this way on
        # purpose: the repo may be unresolvable (no payload, no `doc/harness`
        # above an installed tree), and recovery must not depend on whether a
        # log line can be written.
        # Prune on ANY import failure, not only the named shape. Gating on
        # `_is_stale_bytecode_failure` left a hole: a poisoned `_lib` cache
        # surfaces as `ImportError: cannot import name 'find_harness_root'`,
        # which never reaches the guard that raises `StaleBytecodeCacheError`
        # and so matched nothing — the receipt subsystem would stay dead. The
        # asymmetry decides it: a needless prune costs one recompile of a
        # directory Python regenerates on its own, while a missed prune costs
        # every receipt until someone notices. The detection result is still
        # recorded, because which shape it was is the diagnostic.
        remedy = _prune_sibling_bytecode_cache()
        if not _is_stale_bytecode_failure(exc):
            remedy += " (import failure did not name a stale cache)"
        root = _payload_harness_root() or _harness_root_above(
            os.path.dirname(os.path.abspath(__file__))
        )
        if not root:
            return
        _write_capability_marker(root, exc, remedy)
        entry = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "type": "gate-crash",
            "source": "background_hook:import",
            "key": "receipt-subsystem-unavailable",
            "error": f"{type(exc).__name__}: {exc}",
            "insight": (
                "background_hook could not import its dependencies, so NO receipt "
                "can be written and task_close will refuse. A stale __pycache__ "
                "in the loaded plugin tree is a known cause; clearing it is safe."
            ),
            "remedy": remedy,
            "traceback_tail": traceback.format_exc().strip().splitlines()[-3:],
        }
        with open(
            os.path.join(root, "doc", "harness", "learnings.jsonl"), "a", encoding="utf-8",
        ) as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


try:
    from _lib import (  # type: ignore
        _REVIEW_DETAIL_MAX_BYTES,
        find_repo_root,
        find_harness_root,
        harness_root_resolution,
        is_harness_enabled_repo,
        last_hook_input,
        log_gate_crash,
        read_hook_input,
        resolve_active_task_dir,
        _log_gate_error,
    )
    import subagent_lifecycle  # type: ignore
except Exception as exc:
    _report_import_failure(exc)
    sys.exit(0)


def _receipt_was_expected(diagnostics: dict, payload: dict) -> bool:
    """Was a completion receipt actually owed for this stop?

    Not every SubagentStop belongs to a lifecycle-tracked lens agent. Other
    agent classes stop without ever writing a subagent transcript and without a
    `started` receipt, so they owe no completion — logging them as misses buried
    the real failures under ~25 noise entries during the 2026-08-25 diagnosis.

    A receipt is owed when a matching `started` receipt exists for this run, or
    when the payload names an agent type (a lens agent whose start should have
    been recorded). Absent both, silence is correct. Errs toward logging: an
    unknown shape is reported, not swallowed.

    `receipt_not_owed` is the one signal that overrides even a present agent
    type. The lifecycle sets it for a spawn that carries no lens and whose id
    has the unnamed shape — a `harness:developer` or `oh-my-claudecode:critic`
    that legitimately owes nothing. Those have an agent type, so the fallback
    below would log every one of them and simply trade `gate-crash` noise for
    binding-miss noise. A *named* lens-less spawn deliberately does not set it:
    that case is the shadowed-agent-type defect and must stay visible.
    """
    if diagnostics.get("receipt_not_owed"):
        return False
    if diagnostics.get("expected_receipt"):
        return True
    # Use the lifecycle's alias-normalizing accessor, not the raw key: the
    # runtime may supply agentType / subagent_type / a nested dict. Reading
    # `agent_type` directly would silence a genuine lens miss on a build that
    # renames the field — the precise failure this breadcrumb exists to catch.
    try:
        return bool(subagent_lifecycle._agent_type(payload))
    except Exception:
        return bool(str(payload.get("agent_type") or "").strip())


def _log_binding_miss(repo_root: str, payload: dict, event: str, reason: str = "") -> None:
    """Leave a breadcrumb when a subagent ran but produced no receipt.

    An empty lifecycle result means the session/task binding did not resolve,
    so no receipt was written. Without this signal that failure is completely
    invisible: the subagent completes normally, receipts stay empty, and
    task_close blocks with no indication of why. Only logged when an active
    task exists, i.e. when a receipt was actually expected.

    Best-effort: never raises into the hook.
    """
    try:
        if not resolve_active_task_dir(repo_root):
            return
        # Record which payload fields were present, not their values. An empty
        # result has several causes (unresolved binding, missing transcript
        # path, missing final text, failed provenance) and the bare message
        # cannot tell them apart — that ambiguity is what made the 2026-08-25
        # outage expensive to diagnose. Keys only: transcripts and assistant
        # text must not be copied into learnings.jsonl.
        present = sorted(
            key for key in (
                "session_id", "agent_id", "agent_type",
                "agent_transcript_path", "last_assistant_message",
            ) if payload.get(key)
        )
        # Whether the runtime's transcript path resolves at hook time, and its
        # last two components for shape comparison. Home-directory prefixes are
        # deliberately not recorded.
        raw_path = str(payload.get("agent_transcript_path") or "")
        transcript_tail = "/".join(raw_path.split(os.sep)[-2:]) if raw_path else ""
        transcript_exists = bool(raw_path) and os.path.exists(raw_path)
        _log_gate_error(
            RuntimeError(
                "subagent lifecycle produced no receipt "
                f"(event={event}, "
                f"session_id={str(payload.get('session_id') or '')!r}, "
                f"provenance_reason={reason or 'n/a'}, "
                f"transcript_exists={transcript_exists}, "
                f"transcript_tail={transcript_tail!r}, "
                f"payload_present={present}, "
                f"payload_keys={sorted(str(key) for key in payload)[:20]})"
            ),
            "background_hook:binding-miss",
        )
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Record Claude subagent lifecycle")
    parser.add_argument("--event", choices=["start", "stop"], default="")
    args = parser.parse_args()
    try:
        # A maximum-size UTF-8 review final may expand sixfold as a JSON string.
        # Other hooks retain the small default; only the lifecycle stop adapter
        # needs enough framing room to deliver the shared 2 MiB detail bound.
        payload = read_hook_input(8 * _REVIEW_DETAIL_MAX_BYTES)
        payload_cwd = str(payload.get("cwd") or "").strip()
        hook_cwd = os.path.realpath(payload_cwd or os.getcwd())
        if payload_cwd:
            harness_root, _harness_error = harness_root_resolution(hook_cwd)
            if _harness_error:
                return 0
            repo_root = harness_root or find_repo_root(hook_cwd)
        else:
            candidate_root = find_repo_root()
            harness_root, _harness_error = harness_root_resolution(candidate_root)
            if _harness_error:
                return 0
            repo_root = harness_root or candidate_root
        if not is_harness_enabled_repo(repo_root):
            return 0
        # Reaching here proves the import path works in the tree that hooks
        # actually load, which is the only evidence that can retire the marker.
        _clear_capability_marker(repo_root)
        diagnostics: dict = {}
        result = subagent_lifecycle.handle_subagent_hook(
            repo_root, payload, forced_event=args.event, diagnostics=diagnostics
        )
        if not result and _receipt_was_expected(diagnostics, payload):
            _log_binding_miss(
                repo_root, payload, args.event,
                str(diagnostics.get("provenance_reason") or ""),
            )
    except Exception as exc:
        try:
            log_gate_crash(exc, "background_hook", last_hook_input())
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
