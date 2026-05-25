#!/usr/bin/env python3
"""harness MCP server — self-contained, 7-field TASK_STATE.

No plugin-legacy dependency. All operations are direct file I/O.
MCP tools: task_start, task_context, task_verify, task_close, task_blocked,
           write_critic_qa, write_critic_ux, write_critic_document,
           write_req_doc, write_handoff, write_doc_sync.
"""

from __future__ import annotations
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX fallback
    fcntl = None

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = PLUGIN_ROOT / "scripts"

SUPPORTED_PROTOCOLS = ("2025-11-25", "2025-06-18")
SERVER_INFO = {"name": "harness", "title": "harness Control Plane", "version": "2.0.0"}


def _runtime_from_initialize(params: dict) -> str:
    env_runtime = str(os.environ.get("HARNESS_RUNTIME") or "").strip().lower()
    if env_runtime in ("codex", "claude"):
        return env_runtime
    client = params.get("clientInfo") if isinstance(params, dict) else {}
    name = str(client.get("name") or "").lower() if isinstance(client, dict) else ""
    if "codex" in name:
        return "codex"
    if "claude" in name:
        return "claude"
    return "generic"


def _initialize_instructions(runtime: str) -> str:
    base = (
        "harness MCP — 14 tools, 7-field TASK_STATE. "
        "Protocol tool names are bare: task_start, task_verify, task_close, "
        "task_blocked, write_plan_artifact, write_critic_qa, "
        "write_critic_ux, write_critic_document, write_req_doc, "
        "write_handoff, write_doc_sync, "
        "record_ac_evidence, and record_attempt. "
        "write_plan_artifact is the canonical PLAN/CHECKS/AUDIT writer and "
        "replaces the legacy Python shim. "
    )
    if runtime == "codex":
        return (
            base
            + "Codex callers should use these bare tool names directly; do not "
            "use Claude display prefixes like mcp__plugin_harness_harness__*."
        )
    if runtime == "claude":
        return (
            base
            + "Claude Code may display callable tools with a runtime prefix such "
            "as mcp__plugin_harness_harness__write_critic_qa; that prefix is a "
            "Claude UI naming convention over the same shared MCP server."
        )
    return (
        base
        + "Clients may display these tools with runtime-specific prefixes; follow "
        "the active client's tool-call syntax while preserving the bare MCP names."
    )

sys.path.insert(0, str(SCRIPTS_DIR))
from _lib import (  # type: ignore
    now_iso, read_state, write_state, set_state_field,
    ensure_task_scaffold, emit_compact_context, sync_from_git_diff,
    artifact_exists, canonical_task_dir, canonical_task_id,
    find_repo_root, runtime_is_stale as _runtime_is_stale,
    write_active_marker, clear_active_marker, record_attempt,
)
try:
    from environment_snapshot import snapshot as _env_snapshot  # type: ignore
except Exception:
    _env_snapshot = None
try:
    from req_scaffold import write_req_doc as _write_req_doc_file  # type: ignore
except Exception:
    _write_req_doc_file = None


# ── Helpers ──────────────────────────────────────────────────────────────


def _ok(d: dict) -> dict:
    return {"content": [{"type": "text", "text": json.dumps(d, indent=2, ensure_ascii=False)}],
            "structuredContent": d}


def _err(m: str, data: dict | None = None) -> dict:
    p: dict[str, Any] = {"error": m}
    p.update(data or {})
    return {"content": [{"type": "text", "text": json.dumps(p, indent=2, ensure_ascii=False)}],
            "structuredContent": p, "isError": True}


def _req(args: dict, k: str) -> str:
    v = args.get(k)
    if not isinstance(v, str) or not v.strip():
        raise ValueError(f"{k} required")
    return v


def _opt(args: dict, k: str) -> str | None:
    v = args.get(k)
    return v.strip() if isinstance(v, str) and v.strip() else None


def _task_artifact_rel(td: str, fn: str) -> str:
    return f"doc/harness/tasks/{os.path.basename(td)}/{fn}" if artifact_exists(td, fn) else ""


AUDIT_HEADER = (
    "| # | phase | decision | classification | principle | rationale | rejected_option |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _atomic_write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    import tempfile
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".mcp.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _cleanup_orphan_index_lock(repo_root: str, max_age_secs: int = 0) -> bool:
    """Remove an orphan .git/index.lock left around task_start.

    The cleanup is intentionally narrow: only a 0-byte lock file is eligible,
    and on POSIX we also require a non-blocking exclusive flock to succeed.
    Non-empty locks are treated as active git state and left alone.
    """
    lock_path = os.path.join(repo_root, ".git", "index.lock")
    try:
        st = os.stat(lock_path)
    except (FileNotFoundError, OSError):
        return False
    if st.st_size != 0:
        return False
    if time.time() - st.st_mtime < max_age_secs:
        return False
    fd = None
    try:
        fd = os.open(lock_path, os.O_RDWR)
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, IOError):
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        return False
    try:
        if fcntl is not None and fd is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
    except (OSError, IOError):
        pass
    try:
        if fd is not None:
            os.close(fd)
    except OSError:
        pass
    try:
        os.unlink(lock_path)
    except OSError:
        return False
    return True


# ── PR2 close-gate helpers ──────────────────────────────────────────────
#
# `_runtime_is_stale` lives in `_lib.runtime_is_stale` so both the MCP
# server (close + verify) and `stop_gate.py` can reach it without
# cross-import from `mcp/` into `scripts/`. Imported at the top of this
# file. See `_lib.py` for the full helper + skip-list constants.


def _parse_checks_yaml(td: str) -> list[dict] | None:
    """Parse CHECKS.yaml into [{id, status, title}, ...].

    Returns ``None`` when the file is missing (pre-PR2 task compatibility);
    caller warn-logs and proceeds. Returns ``[]`` when the file is present
    but empty or unparseable — treat as same as missing after logging. Uses
    block-scanning so we don't pull in PyYAML; matches the
    ``update_checks.py`` parser shape.
    """
    checks_path = os.path.join(td, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return None
    try:
        with open(checks_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []

    import re
    blocks: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*-\s+id:\s*", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    items: list[dict] = []
    for block in blocks:
        m_id = re.match(r"^\s*-\s+id:\s*(\S+)", block)
        m_status = re.search(r"^\s+status:\s*(\S+)", block, re.MULTILINE)
        m_title = re.search(r'^\s+title:\s*"?(.*?)"?\s*$', block, re.MULTILINE)
        if not m_id:
            continue
        title = (m_title.group(1) if m_title else "").strip().strip('"').strip("'")
        if len(title) > 120:
            title = title[:117] + "..."
        items.append({
            "id": m_id.group(1),
            "status": (m_status.group(1) if m_status else "open").strip(),
            "title": title,
        })
    return items


_CHECKS_GATE_TERMINAL = {"passed", "deferred"}
_AC_AUTO_PROMOTE_STATUSES = {"open"}


def _split_checks_blocks(text: str) -> list[str]:
    lines = text.splitlines(keepends=True)
    blocks: list[list[str]] = [[]]
    in_ac = False
    for line in lines:
        if re.match(r"^\s*-\s+id:\s*", line):
            blocks.append([line])
            in_ac = True
        elif in_ac:
            blocks[-1].append(line)
        else:
            blocks[-1].append(line)
    return ["".join(block) for block in blocks]


def _yaml_quote(value: str) -> str:
    escaped = str(value or "").replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _append_ac_evidence(td: str, ac_id: str, evidence: str, source: str = "verification") -> dict:
    checks_path = os.path.join(td, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return {"ok": False, "error": "CHECKS.yaml not found"}
    try:
        text = Path(checks_path).read_text(encoding="utf-8")
    except OSError as exc:
        return {"ok": False, "error": str(exc)}

    blocks = _split_checks_blocks(text)
    if len(blocks) <= 1:
        return {"ok": False, "error": "CHECKS.yaml has no AC blocks"}

    entry = f"{now_iso()} | {source or 'verification'} | {(evidence or '').replace(chr(10), ' ').strip()}"
    changed = False
    for idx in range(1, len(blocks)):
        block = blocks[idx]
        m_id = re.match(r"^\s*-\s+id:\s*(\S+)", block)
        if not m_id or m_id.group(1).strip().strip('"').strip("'") != ac_id:
            continue

        if re.search(r"^\s+evidence_log:\s*$", block, flags=re.MULTILINE):
            lines = block.splitlines(keepends=True)
            insert_at = len(lines)
            in_log = False
            for line_idx, line in enumerate(lines):
                if re.match(r"^\s+evidence_log:\s*$", line):
                    in_log = True
                    insert_at = line_idx + 1
                    continue
                if in_log and re.match(r"^\s{2}\w+:", line):
                    insert_at = line_idx
                    break
                if in_log and re.match(r"^\s{4}-\s+", line):
                    insert_at = line_idx + 1
            lines.insert(insert_at, f"    - {_yaml_quote(entry)}\n")
            blocks[idx] = "".join(lines)
        else:
            suffix = "\n" if block.endswith("\n") else ""
            blocks[idx] = block.rstrip("\n") + f"\n  evidence_log:\n    - {_yaml_quote(entry)}" + suffix
        changed = True
        break

    if not changed:
        return {"ok": False, "error": f"AC not found: {ac_id}"}
    _atomic_write_text(checks_path, "".join(blocks))
    return {"ok": True, "ac_id": ac_id, "evidence": entry}


def handle_record_ac_evidence(args: dict) -> dict:
    ti = _req(args, "task_id")
    ac_id = _req(args, "ac_id")
    evidence = _req(args, "evidence")
    source = _opt(args, "source") or "verification"
    td = canonical_task_dir(task_id=ti)
    result = _append_ac_evidence(td, ac_id, evidence, source)
    if not result.get("ok"):
        return _err("record_ac_evidence failed", data={"task_dir": td, **result})
    return _ok({"task_dir": td, **result})


def handle_record_attempt(args: dict) -> dict:
    ti = _req(args, "task_id")
    kind = _opt(args, "kind") or "retry"
    verdict = _opt(args, "verdict") or "unknown"
    summary = _req(args, "summary")
    transcript = _opt(args, "transcript") or ""
    td = canonical_task_dir(task_id=ti)
    meta = record_attempt(td, kind, verdict, summary, transcript)
    return _ok({"task_dir": td, "attempt": meta})


def _run_verify_runner(td: str, parallel: bool = True, max_workers: int | None = None) -> dict:
    script = SCRIPTS_DIR / "verify_runner.py"
    cmd = [sys.executable, str(script), "--json"]
    if parallel:
        cmd.append("--parallel")
    if max_workers:
        cmd.extend(["--max-workers", str(max_workers)])
    proc = subprocess.run(
        cmd,
        cwd=find_repo_root(td),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=None,
    )
    try:
        payload = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        payload = {"commands": [], "stdout": proc.stdout}
    payload["returncode"] = proc.returncode
    if proc.stderr:
        payload["stderr"] = proc.stderr[-4000:]
    return payload


def _checks_gate_status(td: str) -> tuple[str, list[dict]]:
    """Return (``"ok"``|``"blocked"``|``"absent"``, blocking_acs).

    - ``ok``: CHECKS.yaml present, every AC in {passed, deferred}.
    - ``blocked``: CHECKS.yaml present, at least one AC not terminal.
      ``blocking_acs`` is the non-terminal subset (id, status, title).
    - ``absent``: CHECKS.yaml missing — caller warn-logs and proceeds.
    """
    items = _parse_checks_yaml(td)
    if items is None:
        return "absent", []
    if not items:
        return "absent", []
    blocking = [ac for ac in items if ac["status"] not in _CHECKS_GATE_TERMINAL]
    return ("blocked" if blocking else "ok"), blocking


def _truthy(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"1", "true", "yes", "y", "on"}
    return False


def _set_block_field(block: str, field: str, value: str) -> str:
    pattern = rf"^(\s+{re.escape(field)}:\s*).*$"
    replacement = rf"\1{value}"
    new, count = re.subn(pattern, replacement, block, count=1, flags=re.MULTILINE)
    if count:
        return new
    suffix = "\n" if block.endswith("\n") else ""
    return block.rstrip("\n") + f"\n  {field}: {value}" + suffix


def _auto_promote_open_acs(td: str, evidence: str) -> list[str]:
    """Promote open CHECKS.yaml ACs to passed after an explicit QA PASS.

    Only ``status: open`` is eligible. Failed/deferred/in-progress statuses are
    left for explicit update_checks calls so a broad QA PASS cannot erase known
    exceptions or previous failures.
    """
    checks_path = os.path.join(td, "CHECKS.yaml")
    if not os.path.isfile(checks_path):
        return []
    try:
        with open(checks_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return []
    if not text.strip():
        return []

    blocks: list[str] = []
    current: list[str] = []
    prefix_lines: list[str] = []
    for line in text.splitlines():
        if re.match(r"^\s*-\s+id:\s*", line):
            if current:
                blocks.append("\n".join(current))
            current = [line]
        elif current:
            current.append(line)
        else:
            prefix_lines.append(line)
    if current:
        blocks.append("\n".join(current))
    if not blocks:
        return []

    promoted: list[str] = []
    new_blocks: list[str] = []
    safe_evidence = (evidence or "CRITIC__qa.md").replace("\n", " ").strip()
    if len(safe_evidence) > 240:
        safe_evidence = safe_evidence[:237].rstrip() + "..."
    for block in blocks:
        m_id = re.match(r"^\s*-\s+id:\s*(\S+)", block)
        m_status = re.search(r"^\s+status:\s*(\S+)", block, re.MULTILINE)
        status = (m_status.group(1) if m_status else "open").strip()
        if m_id and status in _AC_AUTO_PROMOTE_STATUSES:
            block = _set_block_field(block, "status", "passed")
            block = _set_block_field(block, "last_updated", now_iso())
            block = _set_block_field(block, "evidence", safe_evidence)
            promoted.append(m_id.group(1))
        new_blocks.append(block)
    if not promoted:
        return []
    new_text = "\n".join([p for p in prefix_lines if p] + new_blocks) + "\n"
    try:
        _atomic_write_text(checks_path, new_text)
    except OSError:
        return []
    return promoted


def _critic_qa_has_pass(td: str) -> bool:
    """Return true when CRITIC__qa.md contains current PASS evidence."""
    path = os.path.join(td, "CRITIC__qa.md")
    if not os.path.isfile(path):
        return False
    try:
        content = Path(path).read_text(encoding="utf-8")
    except OSError:
        return False
    if re.search(r"^## qa-[^\n]+ verdict: PASS\s*$", content, flags=re.MULTILINE):
        return True
    return bool(re.search(r"^## Verdict\s*\nPASS\s*$", content, flags=re.MULTILINE))


def _reconcile_acs_from_qa(td: str) -> dict:
    """Promote open ACs from fresh QA PASS evidence during task_verify."""
    st = read_state(td)
    runtime_verdict = (st.get("runtime_verdict") or "pending").upper()
    if runtime_verdict != "PASS":
        return {
            "promoted_acs": [],
            "reason": f"runtime_verdict is {runtime_verdict}, not PASS",
        }
    if not _critic_qa_has_pass(td):
        return {
            "promoted_acs": [],
            "reason": "CRITIC__qa.md has no PASS evidence",
        }
    promoted = _auto_promote_open_acs(td, "CRITIC__qa.md task_verify PASS")
    return {
        "promoted_acs": promoted,
        "reason": "promoted open ACs from QA PASS evidence" if promoted else "no open ACs to promote",
    }


def _log_gate_warn(task_id: str, key: str, insight: str) -> None:
    """Append a one-line gate-warn entry to doc/harness/learnings.jsonl."""
    try:
        import json as _json
        repo_root = find_repo_root()
        learn = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-warn",
            "source": "task_close",
            "key": key,
            "insight": insight,
            "task_id": task_id,
        })
        with open(learn, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _resolve_td(args: dict) -> str:
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id")
    if ti:
        return canonical_task_dir(task_id=ti)
    if td:
        return td
    raise ValueError("task_id or task_dir required")


# ── Tool handlers ────────────────────────────────────────────────────────


def handle_task_start(args: dict) -> dict:
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id")
    sl = _opt(args, "slug")
    rf = _opt(args, "request_file")
    if not td and not ti and not sl:
        raise ValueError("task_start requires task_dir, task_id, or slug")

    repo_root = find_repo_root()
    _cleanup_orphan_index_lock(repo_root)
    task_dir = td or canonical_task_dir(task_id=ti, slug=sl, repo_root=repo_root)
    tid = canonical_task_id(task_id=ti, slug=sl, task_dir=task_dir)

    request_text = ""
    if rf:
        rp = rf if os.path.isabs(rf) else os.path.join(repo_root, rf)
        if os.path.isfile(rp):
            try:
                with open(rp, "r", encoding="utf-8") as f:
                    request_text = f.read()
            except OSError:
                pass

    ensure_task_scaffold(task_dir, tid, request_text=request_text)
    execution_mode = _opt(args, "execution_mode")
    if execution_mode:
        mode = execution_mode.strip().lower()
        if mode not in {"standard", "micro"}:
            raise ValueError("execution_mode must be standard or micro")
        if mode == "micro":
            set_state_field(task_dir, "plan_session_state", "micro_loop")

    # Write session-scoped active marker so multiple sessions can work in one repo.
    write_active_marker(repo_root, task_dir)

    # Best-effort environment snapshot: probe failure must never block task_start.
    snapshot_path = ""
    if _env_snapshot is not None:
        try:
            snapshot_path = _env_snapshot(task_dir, repo_root) or ""
        except Exception:
            snapshot_path = ""

    ctx = emit_compact_context(task_dir)
    _cleanup_orphan_index_lock(repo_root)
    if "error" in ctx:
        return _err("task_start failed", data={"task_dir": task_dir})
    return _ok({
        "task_dir": task_dir, "task_id": tid, "task_context": ctx,
        "environment_snapshot": snapshot_path,
    })


def handle_task_context(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    ctx = emit_compact_context(td)
    if "error" in ctx:
        return _err("task_context failed", data=ctx)
    return _ok({"task_dir": td, "task_context": ctx})


def handle_task_verify(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    sync_from_git_diff(td)
    verify_run = None
    if _truthy(args.get("run_commands")):
        max_workers_raw = args.get("max_workers")
        max_workers = int(max_workers_raw) if isinstance(max_workers_raw, int) and max_workers_raw > 0 else None
        verify_run = _run_verify_runner(
            td,
            parallel=_truthy(args.get("parallel")) or args.get("parallel") is None,
            max_workers=max_workers,
        )

    # Stale check: if any touched path is newer than CRITIC__qa.md,
    # revert the stored verdict to pending so task_close won't accept a
    # frozen PASS. task_verify is the natural place to clear the bit —
    # re-running QA re-writes CRITIC__qa.md with a fresh mtime.
    stale, stale_path = _runtime_is_stale(td)
    if stale:
        st = read_state(td)
        if (st.get("runtime_verdict") or "").upper() == "PASS":
            set_state_field(td, "runtime_verdict", "pending")

    ac_reconcile = {"promoted_acs": [], "reason": "not requested"}
    if _truthy(args.get("reconcile_acs")):
        if stale:
            ac_reconcile = {
                "promoted_acs": [],
                "reason": "runtime_verdict is stale; rerun QA before AC reconciliation",
            }
        else:
            ac_reconcile = _reconcile_acs_from_qa(td)

    st = read_state(td)
    rv = (st.get("runtime_verdict") or "pending").upper()
    ctx = emit_compact_context(td)
    payload = {
        "task_dir": td, "runtime_verdict": rv,
        "touched_paths": st.get("touched_paths") or [],
        "next_action": ctx.get("next_action", ""),
        "missing_for_close": ctx.get("missing_for_close", []),
        "report_path": _task_artifact_rel(td, "CRITIC__qa.md"),
        "stale": stale,
        "stale_path": stale_path,
        "ac_reconcile": ac_reconcile,
    }
    if verify_run is not None:
        payload["verify_run"] = verify_run
    return _ok(payload)


def handle_task_close(args: dict) -> dict:
    ti = _req(args, "task_id")
    td = canonical_task_dir(task_id=ti)
    sync_from_git_diff(td)
    ctx = emit_compact_context(td)
    missing = ctx.get("missing_for_close") or []
    stale, stale_path = _runtime_is_stale(td)
    checks_status, blocking = _checks_gate_status(td)
    if missing:
        data = {
            "task_dir": td, "missing_for_close": missing, "task_context": ctx,
            "stale": stale, "stale_path": stale_path,
        }
        if checks_status == "blocked":
            data["blocking_acs"] = blocking
        return _err("task_close blocked", data=data)

    # PR2 runtime-stale gate: refuse close when a touched path is newer
    # than CRITIC__qa.md. Caller must re-run task_verify so QA can
    # re-issue a fresh PASS.
    if stale:
        return _err("task_close blocked: runtime_verdict stale — re-run task_verify", data={
            "task_dir": td, "stale_path": stale_path,
        })

    # PR2 CHECKS gate: refuse close when any AC is non-terminal.
    # Absent CHECKS.yaml → warn-log + proceed (pre-PR2 tasks).
    if checks_status == "blocked":
        return _err("task_close blocked: CHECKS gate", data={
            "task_dir": td, "blocking_acs": blocking,
        })
    if checks_status == "absent":
        # CHECKS.yaml absent → proceed silently for pre-PR2 task compatibility.
        # Do not pollute learnings.jsonl — runtime alerts are not learnings.
        pass

    st = read_state(td)
    st["status"] = "closed"
    st["closed_at"] = now_iso()
    st["updated"] = now_iso()
    write_state(td, st)

    clear_active_marker(find_repo_root(), td)
    st = read_state(td)
    return _ok({
        "task_dir": td, "closed": True, "status": st.get("status"),
        "gate_artifact": _task_artifact_rel(td, "HANDOFF.md"),
    })


def _write_artifact(args: dict, filename: str, verdict_field: str | None = None,
                    verdict_value: str | None = None) -> dict:
    """Common artifact write: create file, optionally update verdict. Atomic.

    AC-006 (2026-05-12 retro): when called for CRITIC__qa.md, append a
    `## Manual UX verification` section after the standard content. The section
    content is taken from ``args["manual_ux_verification"]``; an empty value
    renders a `_NOT SUPPLIED_` placeholder (handled by handle_write_critic_qa
    so the placeholder text is lens-aware).
    """
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti:
        return _err("task_id or task_dir required")
    td = td or canonical_task_dir(task_id=ti)
    content_parts = [f"# {filename.replace('.md', '').replace('__', ' — ')}\n"]
    for key in ("verdict", "summary", "verification", "transcript"):
        val = _opt(args, key)
        if val:
            content_parts.append(f"\n## {key.title()}\n{val}\n")
    manual_ux = args.get("_rendered_manual_ux_section")
    if manual_ux and filename == "CRITIC__qa.md":
        content_parts.append(manual_ux)
    path = os.path.join(td, filename)
    os.makedirs(td, exist_ok=True)
    import tempfile
    text = "\n".join(content_parts)
    fd, tmp = tempfile.mkstemp(dir=td, prefix=f".{filename}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    result = {"artifact": filename, "task_dir": td}
    if verdict_field:
        verdict = verdict_value or _opt(args, "verdict") or "PASS"
        set_state_field(td, verdict_field, verdict)
        result["verdict"] = verdict
    return _ok(result)


_QA_SEVERITY = {"PENDING": 0, "PASS": 1, "BLOCKED_ENV": 2, "FAIL": 3}


def _worst_verdict(current: str, new: str) -> str:
    """Severity ordering: PENDING < PASS < BLOCKED_ENV < FAIL. Returns the worst.

    Both inputs are normalized to uppercase canonical form before comparison;
    the returned value is also uppercase. The state file may previously have
    stored ``pending`` lowercase — we normalize on read to avoid same-severity
    case-only differences being treated as "no change".
    """
    cur_up = (current or "PENDING").upper()
    new_up = (new or "PENDING").upper()
    return new_up if _QA_SEVERITY.get(new_up, 0) >= _QA_SEVERITY.get(cur_up, 0) else cur_up


def _render_manual_ux_section(lens: str, manual_ux: str) -> tuple[str, str]:
    """Compute (section_markdown, effective_verdict_override).

    AC-006 (2026-05-12 retro): the CRITIC__qa.md template always includes a
    `## Manual UX verification` section. Behavior depends on lens + arg presence:

      - arg provided + non-empty → render content verbatim. No verdict override.
      - lens == 'browser' + arg empty → render placeholder + force PENDING.
      - lens != 'browser' + arg empty → render `_n/a — non-browser lens_`.
        No verdict override.

    Returns the section markdown (including the heading and trailing newline)
    and the effective verdict override ('' = no override; 'PENDING' = downgrade).
    """
    body = (manual_ux or "").strip()
    if body:
        return (f"\n## Manual UX verification\n{body}\n", "")
    if lens == "browser":
        return (
            "\n## Manual UX verification\n"
            "_NOT SUPPLIED — agent must supply manual_ux_verification arg. "
            "runtime_verdict downgraded to PENDING until provided._\n",
            "PENDING",
        )
    return ("\n## Manual UX verification\n_n/a — non-browser lens_\n", "")


def _lens_merge_critic_qa(td: str, lens: str, verdict: str,
                          summary: str, transcript: str,
                          manual_ux: str = "",
                          auto_promote_open_acs: bool = False) -> dict:
    """Lens-aware merge for CRITIC__qa.md. Worst-wins runtime_verdict.

    First lens writer creates the file with a global header and one section.
    Subsequent writes replace that lens's previous section. runtime_verdict is
    recomputed from the latest section per lens so stale FAILs cannot poison a
    later PASS from the same verifier.

    AC-006: the Manual UX verification section is rendered per-lens; the
    browser lens with empty manual_ux forces PENDING (worst-wins still applies).
    """
    os.makedirs(td, exist_ok=True)
    path = os.path.join(td, "CRITIC__qa.md")
    manual_ux_md, verdict_override = _render_manual_ux_section(lens, manual_ux)
    if verdict_override:
        verdict = verdict_override
    section = (
        f"\n## qa-{lens} verdict: {verdict}\n\n"
        f"### summary\n{summary}\n\n"
        f"### transcript\n{transcript}\n"
        f"{manual_ux_md}"
    )
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
        pattern = (
            rf"\n## qa-{re.escape(lens)} verdict: "
            r"(?:PASS|FAIL|BLOCKED_ENV|PENDING)\n.*?(?=\n## qa-[^\n]+ verdict: |\Z)"
        )
        content = re.sub(pattern, "", existing, flags=re.DOTALL)
        if not content.strip():
            content = "# CRITIC — qa\n"
        if "# CRITIC" not in content.splitlines()[0]:
            content = "# CRITIC — qa\n" + content
        content = content.rstrip() + section
    else:
        content = f"# CRITIC — qa\n{section}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    verdicts = re.findall(r"^## qa-[^\n]+ verdict: (PASS|FAIL|BLOCKED_ENV|PENDING)\s*$",
                          content, flags=re.MULTILINE)
    current = "PENDING"
    for v in verdicts:
        current = _worst_verdict(current, v)
    # Compatibility for older stop-judge prompts: stop-judge lens has
    # OVERRIDE authority — it is the only authorized writer of blocker
    # transitions per CONTRACTS § C-17, which means it must also be able to
    # CLEAR a prior BLOCKED_ENV when the blocker condition resolves. Without
    # this exemption, a stale stop-judge BLOCKED_ENV from an earlier session
    # phase permanently traps the task at BLOCKED_ENV via worst-wins merge,
    # blocking task_close even after the situation that produced the verdict
    # has resolved. The Stop hook got a staleness check on 2026-05-14; this
    # mirrors that fix at the lens-merge layer for the task_close gate.
    if lens == "stop-judge":
        final = (verdict or "PENDING").upper()
    else:
        final = current
    set_state_field(td, "runtime_verdict", final)
    attempt = None
    if final in {"FAIL", "BLOCKED_ENV"}:
        attempt = record_attempt(td, f"qa-{lens}", final, summary, transcript)
    legacy_reconcile_note = ""
    if auto_promote_open_acs:
        legacy_reconcile_note = (
            "auto_promote_open_acs is deprecated and no longer mutates CHECKS.yaml; "
            "run task_verify with reconcile_acs=true after QA PASS."
        )
    return _ok({"artifact": "CRITIC__qa.md", "task_dir": td,
                "lens": lens, "verdict": final, "merged": True,
                "promoted_acs": [],
                "ac_reconcile_next_action": legacy_reconcile_note,
                "attempt": attempt or {}})


def _lens_merge_critic_ux(td: str, lens: str, verdict: str,
                          summary: str, transcript: str) -> dict:
    """Lens-aware merge for CRITIC__ux.md.

    UX verdicts gate close for applicable user-facing surfaces, but they do not
    update TASK_STATE.runtime_verdict and never auto-promote functional ACs.
    """
    lens = lens.strip().lower()
    if lens not in {"cli", "api", "browser", "desktop"}:
        return _err("invalid lens — must be cli, api, browser, or desktop")
    os.makedirs(td, exist_ok=True)
    path = os.path.join(td, "CRITIC__ux.md")
    section = (
        f"\n## ux-{lens} verdict: {verdict}\n\n"
        f"### summary\n{summary}\n\n"
        f"### transcript\n{transcript}\n"
    )
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            existing = f.read()
        pattern = (
            rf"\n## ux-{re.escape(lens)} verdict: "
            r"(?:PASS|FAIL|BLOCKED_ENV|PENDING)\n.*?(?=\n## ux-[^\n]+ verdict: |\Z)"
        )
        content = re.sub(pattern, "", existing, flags=re.DOTALL)
        if not content.strip():
            content = "# CRITIC — ux\n"
        if "# CRITIC" not in content.splitlines()[0]:
            content = "# CRITIC — ux\n" + content
        content = content.rstrip() + section
    else:
        content = f"# CRITIC — ux\n{section}"
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    verdicts = re.findall(r"^## ux-[^\n]+ verdict: (PASS|FAIL|BLOCKED_ENV|PENDING)\s*$",
                          content, flags=re.MULTILINE)
    merged = "PENDING"
    for v in verdicts:
        merged = _worst_verdict(merged, v)
    attempt = None
    if verdict in {"FAIL", "BLOCKED_ENV"}:
        attempt = record_attempt(td, f"ux-{lens}", verdict, summary, transcript)
    return _ok({"artifact": "CRITIC__ux.md", "task_dir": td,
                "lens": lens, "verdict": verdict, "ux_verdict": merged,
                "merged": True, "attempt": attempt or {}})


def handle_write_critic_qa(args: dict) -> dict:
    verdict = _req(args, "verdict")
    if verdict not in ("PASS", "FAIL", "BLOCKED_ENV"):
        return _err(f"invalid verdict '{verdict}' — must be PASS, FAIL, or BLOCKED_ENV")
    lens = _opt(args, "lens")
    manual_ux = _opt(args, "manual_ux_verification") or ""
    auto_promote = _truthy(args.get("auto_promote_open_acs"))
    if lens:
        td = _opt(args, "task_dir")
        ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
        if not ti:
            return _err("task_id or task_dir required")
        td = td or canonical_task_dir(task_id=ti)
        summary = _opt(args, "summary") or ""
        transcript = _opt(args, "transcript") or ""
        return _lens_merge_critic_qa(
            td, lens, verdict, summary, transcript, manual_ux,
            auto_promote_open_acs=auto_promote,
        )
    # Legacy single-lens (no lens arg). Treat as non-browser by default for the
    # Manual UX rendering — the agent's lens identity is unknown, so we err on
    # the side of not forcing PENDING.
    manual_ux_md, verdict_override = _render_manual_ux_section("", manual_ux)
    if verdict_override:
        verdict = verdict_override
    args = dict(args)  # don't mutate caller's dict
    args["_rendered_manual_ux_section"] = manual_ux_md
    result = _write_artifact(args, "CRITIC__qa.md", "runtime_verdict", verdict_value=verdict)
    if verdict in {"FAIL", "BLOCKED_ENV"}:
        td = _opt(args, "task_dir")
        ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
        if ti and isinstance(result.get("structuredContent"), dict):
            td = td or canonical_task_dir(task_id=ti)
            result["structuredContent"]["attempt"] = record_attempt(
                td,
                "qa",
                verdict,
                _opt(args, "summary") or "",
                _opt(args, "transcript") or "",
            )
    if isinstance(result.get("structuredContent"), dict):
        result["structuredContent"]["promoted_acs"] = []
        if auto_promote:
            result["structuredContent"]["ac_reconcile_next_action"] = (
                "auto_promote_open_acs is deprecated and no longer mutates CHECKS.yaml; "
                "run task_verify with reconcile_acs=true after QA PASS."
            )
        try:
            result["content"][0]["text"] = json.dumps(result["structuredContent"], indent=2, ensure_ascii=False)
        except Exception:
            pass
    return result


def handle_write_critic_ux(args: dict) -> dict:
    verdict = _req(args, "verdict")
    if verdict not in ("PASS", "FAIL", "BLOCKED_ENV"):
        return _err(f"invalid verdict '{verdict}' — must be PASS, FAIL, or BLOCKED_ENV")
    lens = _req(args, "lens").lower()
    td = _opt(args, "task_dir")
    ti = _opt(args, "task_id") or (os.path.basename(td.rstrip("/")) if td else None)
    if not ti:
        return _err("task_id or task_dir required")
    td = td or canonical_task_dir(task_id=ti)
    summary = _opt(args, "summary") or ""
    transcript = _opt(args, "transcript") or ""
    return _lens_merge_critic_ux(td, lens, verdict, summary, transcript)


def handle_write_critic_document(args: dict) -> dict:
    verdict = _req(args, "verdict")
    if verdict not in ("PASS", "FAIL"):
        return _err(f"invalid verdict '{verdict}' — must be PASS or FAIL")
    return _write_artifact(args, "CRITIC__document.md")


def handle_write_req_doc(args: dict) -> dict:
    if _write_req_doc_file is None:
        return _err("write_req_doc unavailable: req_scaffold.py import failed")
    ti = _req(args, "task_id")
    area = _opt(args, "area") or "ui"
    slug = _req(args, "slug")
    intent = _req(args, "intent")
    observable = _req(args, "observable_behaviors")
    verification = _req(args, "verification_cues")
    non_goals = _opt(args, "non_goals") or ""
    source = _opt(args, "source") or f"task: {ti}"
    repo_root = find_repo_root()
    rel = _write_req_doc_file(
        repo_root,
        area,
        slug,
        intent,
        observable,
        verification,
        non_goals,
        source,
    )
    return _ok({
        "artifact": rel,
        "task_id": ti,
        "task_dir": canonical_task_dir(task_id=ti),
        "req_path": rel,
    })


def handle_write_handoff(args: dict) -> dict:
    return _write_artifact(args, "HANDOFF.md")


def handle_write_doc_sync(args: dict) -> dict:
    return _write_artifact(args, "DOC_SYNC.md")


def handle_task_blocked(args: dict) -> dict:
    ti = _req(args, "task_id")
    reason = _req(args, "blocked_reason")
    unblock = _req(args, "unblock_condition")
    td = canonical_task_dir(task_id=ti)
    os.makedirs(td, exist_ok=True)
    blocked_md = (
        "# BLOCKED\n\n"
        f"## Blocked Reason\n{reason}\n\n"
        f"## Unblock Condition\n{unblock}\n\n"
        f"## Blocked At\n{now_iso()}\n"
    )
    with open(os.path.join(td, "BLOCKED.md"), "w", encoding="utf-8") as f:
        f.write(blocked_md)
    st = read_state(td)
    if not st:
        return _err("task_blocked failed: missing TASK_STATE.yaml", data={"task_dir": td})
    st["status"] = "blocked"
    st["runtime_verdict"] = "BLOCKED_ENV"
    st["updated"] = now_iso()
    write_state(td, st)
    clear_active_marker(find_repo_root(), td)
    return _ok({
        "task_dir": td,
        "status": "blocked",
        "runtime_verdict": "BLOCKED_ENV",
        "blocked_artifact": _task_artifact_rel(td, "BLOCKED.md"),
    })


def _plan_meta_dict(td: str, artifact: str, meta: dict | None = None) -> dict:
    out: dict[str, Any] = {
        "artifact": artifact,
        "task_id": os.path.basename(os.path.abspath(td)),
        "author_role": "plan-skill",
        "written_at": now_iso(),
    }
    if meta:
        out["plan_meta"] = meta
    return out


def _coerce_meta(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}
    return {}


def _nonempty_artifact_content(value: str, *, artifact: str, filename: str) -> str | dict:
    if not value.strip():
        return _err(
            f"write_plan_artifact refused to write empty {filename}",
            data={
                "artifact": artifact,
                "filename": filename,
                "next_action": "Pass non-empty content, or omit the bundled artifact.",
            },
        )
    return value


def _record_write(path: str, text: str, written: list[str], bytes_written: dict[str, int]) -> None:
    _atomic_write_text(path, text)
    name = os.path.basename(path)
    written.append(name)
    bytes_written[name] = len(text.encode("utf-8"))


def handle_write_plan_artifact(args: dict) -> dict:
    """MCP replacement for scripts/write_plan_artifact.py.

    The MCP call is the ownership boundary, so it intentionally does not require
    PLAN_SESSION.json token choreography.
    """
    artifact = _req(args, "artifact")
    if artifact not in ("plan", "plan-meta", "checks", "audit"):
        return _err("invalid artifact — must be plan, plan-meta, checks, or audit")
    td = _resolve_td(args)
    if not os.path.isfile(os.path.join(td, "TASK_STATE.yaml")):
        return _err("write_plan_artifact failed: missing TASK_STATE.yaml", data={"task_dir": td})
    raw_content = args.get("content")
    content = raw_content if isinstance(raw_content, str) else ""
    meta = _coerce_meta(args.get("meta"))
    written: list[str] = []
    bytes_written: dict[str, int] = {}
    raw_checks_content = args.get("checks_content")
    has_checks_content = isinstance(raw_checks_content, str)
    checks_content = raw_checks_content if has_checks_content else None

    if has_checks_content and artifact not in ("plan", "plan-meta"):
        return _err(
            "checks_content is only valid when bundled with artifact=plan or artifact=plan-meta",
            data={
                "artifact": artifact,
                "next_action": "For artifact=checks, use content for the CHECKS.yaml body.",
            },
        )

    if artifact == "plan":
        checked = _nonempty_artifact_content(content, artifact=artifact, filename="PLAN.md")
        if isinstance(checked, dict):
            return checked
        _record_write(os.path.join(td, "PLAN.md"), checked, written, bytes_written)
        plan_meta = json.dumps(_plan_meta_dict(td, "PLAN.md", meta), indent=2, ensure_ascii=False) + "\n"
        _record_write(os.path.join(td, "PLAN.meta.json"), plan_meta, written, bytes_written)
        if checks_content is not None:
            checked_checks = _nonempty_artifact_content(checks_content, artifact=artifact, filename="CHECKS.yaml")
            if isinstance(checked_checks, dict):
                return checked_checks
            _record_write(os.path.join(td, "CHECKS.yaml"), checked_checks, written, bytes_written)
    elif artifact == "plan-meta":
        content_meta = _coerce_meta(content)
        content_meta.update(meta)
        plan_meta = json.dumps(_plan_meta_dict(td, "plan-meta", content_meta), indent=2, ensure_ascii=False) + "\n"
        _record_write(os.path.join(td, "PLAN.meta.json"), plan_meta, written, bytes_written)
        if checks_content is not None:
            checked_checks = _nonempty_artifact_content(checks_content, artifact=artifact, filename="CHECKS.yaml")
            if isinstance(checked_checks, dict):
                return checked_checks
            _record_write(os.path.join(td, "CHECKS.yaml"), checked_checks, written, bytes_written)
    elif artifact == "checks":
        checked = _nonempty_artifact_content(content, artifact=artifact, filename="CHECKS.yaml")
        if isinstance(checked, dict):
            return checked
        _record_write(os.path.join(td, "CHECKS.yaml"), checked, written, bytes_written)
    elif artifact == "audit":
        checked = _nonempty_artifact_content(content, artifact=artifact, filename="AUDIT_TRAIL.md")
        if isinstance(checked, dict):
            return checked
        path = os.path.join(td, "AUDIT_TRAIL.md")
        try:
            existing = open(path, encoding="utf-8").read() if os.path.isfile(path) else ""
        except OSError:
            existing = ""
        first_line = existing.lstrip("\n").split("\n")[0] if existing.strip() else ""
        has_header = first_line.startswith("| # |")
        if not existing.strip():
            new_content = AUDIT_HEADER + checked.rstrip("\n") + "\n"
        elif has_header:
            new_content = existing.rstrip("\n") + "\n" + checked.rstrip("\n") + "\n"
        else:
            new_content = existing.rstrip("\n") + "\n\n" + AUDIT_HEADER + checked.rstrip("\n") + "\n"
        _record_write(path, new_content, written, bytes_written)

    return _ok({
        "artifact": artifact,
        "task_dir": td,
        "written": written,
        "bytes_written": bytes_written,
    })


# ── Tool definitions ─────────────────────────────────────────────────────

TOOL_DEFS: list[dict[str, Any]] = [
    {"name": "task_start", "title": "Create or resume a task",
     "description": "Create task scaffolding (7-field TASK_STATE) and return fresh context. Pass execution_mode='micro' for explicit no-plan develop->verify->close mode; verification remains mandatory.",
     "inputSchema": {"type": "object", "properties": {
         "task_dir": {"type": "string"}, "task_id": {"type": "string"},
         "slug": {"type": "string"}, "request_file": {"type": "string"},
         "execution_mode": {"type": "string", "enum": ["standard", "micro"]}},
         "additionalProperties": False},
     "handler": handle_task_start},
    {"name": "task_context", "title": "Read the task pack",
     "description": "Return compact task context with on-the-fly routing.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_context},
    {"name": "task_verify", "title": "Run task verification",
     "description": "Sync changed paths and check verification state. Optional run_commands executes manifest verify_commands through the parallel verify runner. Optional reconcile_acs promotes open CHECKS.yaml ACs from fresh QA PASS evidence.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "run_commands": {"type": "boolean"},
         "reconcile_acs": {"type": "boolean", "description": "Optional. When true, promote open CHECKS.yaml ACs using fresh CRITIC__qa.md PASS evidence. Failed/deferred ACs are never promoted."},
         "parallel": {"type": "boolean"},
         "max_workers": {"type": "integer"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_verify},
    {"name": "task_close", "title": "Run the completion gate",
     "description": "Check all verdicts PASS, then close the task.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}},
         "required": ["task_id"], "additionalProperties": False},
     "handler": handle_task_close},
    {"name": "task_blocked", "title": "Park a task on a real environment blocker",
     "description": "Record BLOCKED_ENV, write BLOCKED.md, set status=blocked, and clear this session's active marker. This is not completion.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "blocked_reason": {"type": "string"},
         "unblock_condition": {"type": "string"}},
         "required": ["task_id", "blocked_reason", "unblock_condition"],
         "additionalProperties": False},
     "handler": handle_task_blocked},
    {"name": "record_ac_evidence", "title": "Append incremental AC evidence",
     "description": "Append timestamped evidence to one CHECKS.yaml AC without changing its status. Use during develop or focused verification before final QA PASS.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "ac_id": {"type": "string"},
         "evidence": {"type": "string"},
         "source": {"type": "string"}},
         "required": ["task_id", "ac_id", "evidence"],
         "additionalProperties": False},
     "handler": handle_record_ac_evidence},
    {"name": "record_attempt", "title": "Record retry attempt evidence",
     "description": "Create attempts/attempt-NNN with summary metadata and optional transcript for failed develop/verify retries.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "kind": {"type": "string"},
         "verdict": {"type": "string"},
         "summary": {"type": "string"},
         "transcript": {"type": "string"}},
         "required": ["task_id", "summary"],
         "additionalProperties": False},
     "handler": handle_record_attempt},
    {"name": "write_critic_qa", "title": "Write runtime verdict — QA agents only",
     "description": "Write CRITIC__qa.md and set runtime_verdict. Called by qa-browser, qa-api, qa-cli, or qa-desktop. Pass `lens` (e.g. \"cli\", \"browser\") when multiple QA agents run in parallel — the handler keeps the latest section per lens and computes worst-wins runtime_verdict across current lens verdicts. Without `lens`, legacy full-overwrite behavior is preserved. Pass `manual_ux_verification` with a non-empty description of the manual UX verification performed; when lens='browser' and this field is empty, runtime_verdict is forced to PENDING (AC-006).",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]},
         "summary": {"type": "string"}, "transcript": {"type": "string"},
         "lens": {"type": "string", "description": "Optional QA lens identifier (cli, api, browser, desktop). When set, replaces that lens's prior section + worst-wins merges current lens verdicts."},
         "manual_ux_verification": {"type": "string", "description": "Optional. Description of manual UX checks performed (browser lens). Empty + lens=browser → verdict forced to PENDING (AC-006)."},
         "auto_promote_open_acs": {"type": "boolean", "description": "Deprecated compatibility no-op. QA tools only write evidence/runtime verdict; run task_verify with reconcile_acs=true to update CHECKS.yaml from QA PASS evidence."}},
         "required": ["task_id", "verdict", "summary", "transcript"],
         "additionalProperties": False},
     "handler": handle_write_critic_qa},
    {"name": "write_critic_ux", "title": "Write UX review verdict — UX agents only",
     "description": "Write lens-aware CRITIC__ux.md. Called by ux-cli, ux-api, ux-browser, or ux-desktop. Does not update runtime_verdict and does not auto-promote CHECKS functional ACs.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "verdict": {"type": "string", "enum": ["PASS", "FAIL", "BLOCKED_ENV"]},
         "summary": {"type": "string"}, "transcript": {"type": "string"},
         "lens": {"type": "string", "enum": ["cli", "api", "browser", "desktop"],
                  "description": "UX lens identifier. Replaces that lens's prior section and recomputes the merged UX verdict."}},
         "required": ["task_id", "verdict", "summary", "transcript", "lens"],
         "additionalProperties": False},
     "handler": handle_write_critic_ux},
    {"name": "write_plan_artifact", "title": "Write plan-owned artifacts",
     "description": "Write PLAN.md, PLAN.meta.json, CHECKS.yaml, or AUDIT_TRAIL.md through MCP. Replaces scripts/write_plan_artifact.py and does not require PLAN_SESSION.json handshakes.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "artifact": {"type": "string", "enum": ["plan", "plan-meta", "checks", "audit"]},
         "content": {"type": "string"},
         "checks_content": {"type": "string", "description": "Optional bundled CHECKS.yaml body. Valid only with artifact=plan or artifact=plan-meta; use content for artifact=checks."},
         "meta": {"type": ["object", "string"]}},
         "required": ["artifact"],
         "additionalProperties": False},
     "handler": handle_write_plan_artifact},
    {"name": "write_critic_document", "title": "Write document critic verdict",
     "description": "Write CRITIC__document.md after critic-document reviews DOC_SYNC and durable doc quality.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "verdict": {"type": "string", "enum": ["PASS", "FAIL"]},
         "summary": {"type": "string"}, "transcript": {"type": "string"}},
         "required": ["task_id", "verdict", "summary", "transcript"],
         "additionalProperties": False},
     "handler": handle_write_critic_document},
    {"name": "write_req_doc", "title": "Create or update durable REQ doc",
     "description": "Auto-author a doc/<area>/REQ__<slug>.md scaffold before observable source work. The scaffold is reviewed by critic-document when durable docs change.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"},
         "area": {"type": "string"},
         "slug": {"type": "string"},
         "intent": {"type": "string"},
         "observable_behaviors": {"type": "string"},
         "verification_cues": {"type": "string"},
         "non_goals": {"type": "string"},
         "source": {"type": "string"}},
         "required": ["task_id", "slug", "intent", "observable_behaviors", "verification_cues"],
         "additionalProperties": False},
     "handler": handle_write_req_doc},
    {"name": "write_handoff", "title": "Write developer handoff — developer only",
     "description": "Write HANDOFF.md. This is a full rewrite; when updating an existing handoff, preserve existing content and include the Commit-backed Learnings classification.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "summary": {"type": "string"}, "verification": {"type": "string"}},
         "required": ["task_id", "summary", "verification"], "additionalProperties": False},
     "handler": handle_write_handoff},
    {"name": "write_doc_sync", "title": "Write DOC_SYNC — developer only",
     "description": "Write DOC_SYNC.md.",
     "inputSchema": {"type": "object", "properties": {
         "task_id": {"type": "string"}, "task_dir": {"type": "string"},
         "summary": {"type": "string"}},
         "required": ["task_id", "summary"], "additionalProperties": False},
     "handler": handle_write_doc_sync},
]

TOOLS = {t["name"]: t for t in TOOL_DEFS}


def list_tools() -> list[dict]:
    return [{k: v for k, v in t.items() if k != "handler"} for t in TOOL_DEFS]


def call_tool(name: str, args: dict | None) -> dict:
    if name not in TOOLS:
        return _err(f"Unknown tool: {name}")
    try:
        return TOOLS[name]["handler"](args or {})
    except ValueError as e:
        return _err(str(e))
    except Exception as e:
        return _err(f"{name} failed: {e}")


# ── MCP protocol ─────────────────────────────────────────────────────────


class McpServer:
    def __init__(self) -> None:
        self.initialized = False
        self.protocol_version = SUPPORTED_PROTOCOLS[0]
        self.framed_stdio = False

    def _read(self) -> dict | None:
        """Read either MCP stdio frames or newline-delimited JSON.

        Codex speaks the standard MCP stdio transport, where every JSON-RPC
        message is framed with HTTP-like headers, most importantly
        ``Content-Length``. Older harness smoke tests used one JSON object per
        line, so this reader accepts both forms.
        """
        first = sys.stdin.buffer.readline()
        if not first:
            return None
        if first.strip().startswith(b"{"):
            self.framed_stdio = False
            return json.loads(first.strip().decode())

        self.framed_stdio = True
        headers: dict[str, str] = {}
        line = first
        while line and line.strip():
            key, sep, value = line.decode(errors="replace").partition(":")
            if sep:
                headers[key.strip().lower()] = value.strip()
            line = sys.stdin.buffer.readline()

        length_raw = headers.get("content-length")
        if not length_raw:
            return None
        body = sys.stdin.buffer.read(int(length_raw))
        if not body:
            return None
        return json.loads(body.decode())

    def _write(self, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
        if self.framed_stdio:
            header = f"Content-Length: {len(body)}\r\n\r\n".encode()
            sys.stdout.buffer.write(header + body)
        else:
            sys.stdout.buffer.write(body + b"\n")
        sys.stdout.buffer.flush()

    def _reply(self, msg_id: Any, result: Any) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "result": result})

    def _error(self, msg_id: Any, code: int, message: str) -> None:
        self._write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})

    def handle_request(self, req: dict) -> None:
        method = req.get("method")
        msg_id = req.get("id")
        params = req.get("params") or {}

        if method == "initialize":
            pv = params.get("protocolVersion")
            self.protocol_version = pv if isinstance(pv, str) and pv in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
            runtime = _runtime_from_initialize(params)
            self._reply(msg_id, {
                "protocolVersion": self.protocol_version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": SERVER_INFO,
                "instructions": _initialize_instructions(runtime),
            })
        elif method == "notifications/initialized":
            self.initialized = True
        elif method == "ping":
            self._reply(msg_id, {})
        elif method == "tools/list":
            self._reply(msg_id, {"tools": list_tools()})
        elif method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str):
                self._error(msg_id, -32602, "Tool name must be a string")
                return
            self._reply(msg_id, call_tool(name, arguments))
        else:
            self._error(msg_id, -32601, f"Method not found: {method}")

    def serve_forever(self) -> None:
        while True:
            req = self._read()
            if req is None:
                return
            self.handle_request(req)


def main() -> int:
    McpServer().serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
