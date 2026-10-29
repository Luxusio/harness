#!/usr/bin/env python3
"""Harness minimal library — stdlib only, exact four-field TASK.json."""

import os
import re
import stat
import subprocess
import tempfile
import json
import hashlib
import importlib.machinery
import inspect
import sys
import secrets
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from types import CodeType

TASK_DIR = "doc/harness/tasks"
MANIFEST_PATH = "doc/harness/manifest.yaml"
TASK_CONTROL_NAME = "TASK.json"

CODEX_TASK_BINDING_TOOLS = frozenset({
    "wbs", "mcp__harness__wbs", "mcp__plugin_harness_harness__wbs",
})


def is_codex_task_binding_tool(tool_name, arguments=None):
    """Bind only successful public WBS open/context operations."""
    if not isinstance(tool_name, str) or tool_name.lower() not in CODEX_TASK_BINDING_TOOLS:
        return False
    try:
        arguments = json.loads(arguments) if isinstance(arguments, str) else arguments
    except (ValueError, TypeError, RecursionError):
        return False
    return (isinstance(arguments, dict) and isinstance(arguments.get("action"), str)
            and arguments["action"] in {"open", "context"})


def native_wbs_id(objective):
    """Stable root identity, without a parallel native-Goal planning ledger."""
    normalized = " ".join(str(objective or "").split())
    return "WBS__native-" + hashlib.sha256(normalized.encode()).hexdigest()[:20]


def native_wbs_is_synced(repo_root, objective):
    if not str(objective or "").strip():
        return False
    task_id = "TASK__" + native_wbs_id(objective)[5:]
    td = canonical_task_dir(task_id=task_id, repo_root=repo_root)
    return bool(read_task_control(td))

TASK_CONTROL_FIELDS = frozenset({
    "run_id", "execution_mode", "required_lenses", "closed",
})
LENS_ORDER = (
    "review-code", "review-security",
    "qa-api", "qa-browser", "qa-cli", "qa-desktop",
)
REVIEW_LENSES = frozenset(lens for lens in LENS_ORDER if lens.startswith("review-"))
QA_LENSES = frozenset(lens for lens in LENS_ORDER if lens.startswith("qa-"))
SUPPORTED_LENSES = frozenset(LENS_ORDER)
def plugin_root_env(default: str | None = None) -> str | None:
    """Return the configured runtime plugin root."""
    new = os.environ.get("HARNESS_PLUGIN_ROOT")
    if new:
        return new
    old = os.environ.get("CLAUDE_PLUGIN_ROOT")
    if old:
        return old
    return default


def plugin_root_env_pair(value: str) -> dict[str, str]:
    """Return plugin-root variables for child processes."""
    return {
        "HARNESS_PLUGIN_ROOT": value,
        "CLAUDE_PLUGIN_ROOT": value,  # deprecated; drop in v2.5
    }


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Hook I/O + gate signalling ───────────────────────────────────────────
#

import json as _json  # noqa: E402  (kept after module constants on purpose)
import sys as _sys    # noqa: E402


_STDIN_CAP_BYTES = 1 << 16  # 64 KiB read cap for hook payload

_LAST_HOOK_INPUT: dict = {}


def read_hook_input(max_chars=_STDIN_CAP_BYTES):
    """Read and cache a bounded hook JSON object, returning {} on failure."""
    global _LAST_HOOK_INPUT
    if not isinstance(max_chars, int) or max_chars <= 0:
        max_chars = _STDIN_CAP_BYTES
    try:
        raw = _sys.stdin.read(max_chars)
    except Exception:
        _LAST_HOOK_INPUT = {}
        return {}
    if not raw:
        _LAST_HOOK_INPUT = {}
        return {}
    try:
        data = _json.loads(raw)
        out = data if isinstance(data, dict) else {}
        _LAST_HOOK_INPUT = out
        return out
    except Exception:
        _LAST_HOOK_INPUT = {}
        return {}


def last_hook_input() -> dict:
    """Return the most recently parsed hook object."""
    return _LAST_HOOK_INPUT


def _validated_control_dir(repo_root: str, relative_dir: str, label: str) -> str:
    root = os.path.abspath(repo_root)
    resolved_root = os.path.realpath(root)
    current = root
    for part in relative_dir.split("/"):
        current = os.path.join(current, part)
        if not os.path.lexists(current):
            continue
        if os.path.islink(current) or not os.path.isdir(current):
            raise ValueError(f"invalid {label}; {relative_dir} must be a real directory inside the repository")
        try:
            if os.path.commonpath((os.path.realpath(current), resolved_root)) != resolved_root:
                raise ValueError(f"invalid {label}; {relative_dir} resolves outside the repository")
        except ValueError:
            raise ValueError(f"invalid {label}; {relative_dir} resolves outside the repository") from None
    return os.path.join(root, *relative_dir.split("/"))


def _read_regular_text_file(path: str, *, max_size: int = 1024 * 1024) -> str:
    flags = os.O_RDONLY
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    try:
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > max_size:
            return ""
        with os.fdopen(fd, encoding="utf-8") as f:
            fd = -1
            return f.read()
    except (OSError, UnicodeError):
        return ""
    finally:
        if fd >= 0:
            os.close(fd)


def _strict_regular_text_snapshot(
    path: str, *, max_size: int = 1024 * 1024, allow_symlink: bool = False,
):
    parent = os.path.abspath(os.path.dirname(path) or ".")
    leaf = os.path.basename(path)
    binding_fn = globals().get("_task_control_lock_binding")
    binding = binding_fn(parent) if callable(binding_fn) else None
    dir_fd = binding[1] if binding is not None else None
    if binding is not None:
        _validate_task_control_dir_binding(binding)

    def leaf_stat():
        if dir_fd is None:
            return os.lstat(path)
        return os.stat(leaf, dir_fd=dir_fd, follow_symlinks=False)

    try:
        before = leaf_stat()
    except FileNotFoundError:
        return {"exists": False, "kind": "absent", "text": ""}
    except OSError as exc:
        raise RuntimeError(f"snapshot unavailable for {path}: {exc}") from exc
    if stat.S_ISLNK(before.st_mode):
        if not allow_symlink:
            raise RuntimeError(f"snapshot requires a regular non-symlink file: {path}")
        target = os.readlink(leaf, dir_fd=dir_fd) if dir_fd is not None else os.readlink(path)
        after = leaf_stat()
        if (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"snapshot identity changed after read: {path}")
        return {"exists": True, "kind": "symlink", "target": target, "text": ""}
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"snapshot requires a regular non-symlink file: {path}")
    if before.st_size > max_size:
        raise RuntimeError(f"snapshot exceeds size limit: {path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK
    fd = os.open(leaf, flags, dir_fd=dir_fd) if dir_fd is not None else os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)
            or opened.st_size > max_size
        ):
            raise RuntimeError(f"snapshot identity changed before read: {path}")
        chunks = []
        remaining = max_size + 1
        while remaining:
            chunk = os.read(fd, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not remaining:
            raise RuntimeError(f"snapshot exceeds size limit: {path}")
        # Keep the original text reader's UTF-8 and universal-newline behavior.
        text = b"".join(chunks).decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(f"snapshot read unavailable for {path}: {exc}") from exc
    finally:
        if fd >= 0:
            os.close(fd)
    try:
        after = leaf_stat()
    except OSError as exc:
        raise RuntimeError(f"snapshot identity changed after read: {path}") from exc
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or (after.st_dev, after.st_ino) != (before.st_dev, before.st_ino)
    ):
        raise RuntimeError(f"snapshot identity changed after read: {path}")
    if after.st_size > max_size:
        raise RuntimeError(f"snapshot exceeds size limit: {path}")
    return {"exists": True, "kind": "regular", "text": text}


def _restore_text_snapshots(snapshots):
    first_error = None
    for path, snapshot in snapshots.items():
        try:
            if not snapshot["exists"]:
                parent = os.path.abspath(os.path.dirname(path) or ".")
                leaf = os.path.basename(path)
                binding = _task_control_lock_binding(parent) if "_task_control_lock_binding" in globals() else None
                try:
                    if binding is not None:
                        _validate_task_control_dir_binding(binding)
                        os.unlink(leaf, dir_fd=binding[1])
                    else:
                        os.unlink(path)
                except FileNotFoundError:
                    pass
            elif snapshot.get("kind") == "symlink":
                parent, leaf = os.path.abspath(os.path.dirname(path) or "."), os.path.basename(path)
                try:
                    os.unlink(path)
                except FileNotFoundError:
                    pass
                os.symlink(snapshot["target"], path)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                _atomic_text_write(path, snapshot["text"])
        except Exception as exc:
            if first_error is None:
                first_error = exc
    if first_error is not None:
        raise first_error


def _read_json_file(path: str, *, max_size: int = 1024 * 1024) -> dict:
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        before = os.lstat(path)
        fd = os.open(path, flags)
    except OSError:
        return {}
    try:
        opened = os.fstat(fd)
        identity = (opened.st_dev, opened.st_ino)
        if (
            not stat.S_ISREG(opened.st_mode)
            or identity != (before.st_dev, before.st_ino)
            or opened.st_uid != os.getuid()
            or opened.st_nlink != 1
            or opened.st_mode & 0o022
            or opened.st_size > max_size
        ):
            return {}
        with os.fdopen(fd, encoding="utf-8") as handle:
            fd = -1
            text = handle.read(max_size + 1)
        if len(text.encode("utf-8")) > max_size:
            return {}
        after = os.lstat(path)
        if (
            stat.S_ISLNK(after.st_mode)
            or (after.st_dev, after.st_ino) != identity
            or after.st_nlink != 1
            or after.st_mtime_ns != opened.st_mtime_ns
            or after.st_ctime_ns != opened.st_ctime_ns
            or after.st_size != opened.st_size
        ):
            return {}
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError(f"duplicate state key: {key}")
                result[key] = value
            return result
        data = json.loads(text, object_pairs_hook=unique_object)
    except (OSError, TypeError, UnicodeError, ValueError):
        return {}
    finally:
        if fd >= 0:
            os.close(fd)
    return data if isinstance(data, dict) else {}


def _atomic_text_write(path: str, text: str) -> None:
    parent = os.path.abspath(os.path.dirname(path) or ".")
    leaf = os.path.basename(path)
    binding_fn = globals().get("_task_control_lock_binding")
    binding = binding_fn(parent) if callable(binding_fn) else None
    if binding is not None:
        if binding is not None: _validate_task_control_dir_binding(binding)
        dir_fd = binding[1]
        tmp = f".text.{secrets.token_hex(8)}.tmp"
        flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
        try:
            payload = text.encode("utf-8")
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("atomic task artifact write made no progress")
                view = view[written:]
            os.fsync(fd)
            os.replace(tmp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
            os.fsync(dir_fd)
            if binding is not None: _validate_task_control_dir_binding(binding)
        finally:
            os.close(fd)
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except FileNotFoundError:
                pass
        return
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".text.", suffix=".tmp")
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


DIAGNOSTICS_MAX_BYTES = 64 * 1024


def read_json_diagnostics(path: str) -> dict:
    """Read a small advisory JSON dict without following a symlink.

    Diagnostics files sit beside protected artifacts but are not themselves
    protected, so their content is untrusted input. Reading through a planted
    symlink would let an unrelated file's contents be presented as harness
    state.
    """
    # O_NONBLOCK because O_NOFOLLOW does not reject a FIFO, and opening one with
    # no writer blocks forever — the S_ISREG check below never gets to run. The
    # MCP server has no timeout around this call.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(path, flags)
    except OSError:
        return {}
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > DIAGNOSTICS_MAX_BYTES:
            return {}
        raw = os.read(fd, DIAGNOSTICS_MAX_BYTES).decode("utf-8")
    except Exception:
        return {}
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
    try:
        loaded = json.loads(raw or "{}")
    except Exception:
        # Deliberately broad. `json.loads` on deeply nested input raises
        # RecursionError, which is not a ValueError, and letting it escape made
        # the Codex PreToolUse hook exit non-zero on a 19KB file any local
        # writer can plant — a C-12 fail-safe regression. This helper promises
        # never to break its caller, so it catches everything.
        return {}
    return loaded if isinstance(loaded, dict) else {}


def write_json_diagnostics(path: str, data: dict, confine_to: str = "") -> bool:
    """Atomically write a small advisory JSON file, refusing symlinked paths.

    A fixed ``<path>.tmp`` opened with plain ``open(..., "w")`` follows a
    symlink planted at that name, so an actor who may not touch RECEIPTS.jsonl
    or TASK.json could still redirect this write anywhere on the filesystem and
    clobber the target with attacker-chosen JSON keys. ``O_EXCL|O_NOFOLLOW`` on
    a random temp name closes the temp side; refusing a non-regular destination
    closes the replace side. Returns False rather than raising — diagnostics
    must never break the caller.
    """
    parent = os.path.abspath(os.path.dirname(path) or ".")
    leaf = os.path.basename(path)
    if not leaf:
        return False
    if confine_to:
        root = os.path.realpath(confine_to)
        resolved = os.path.realpath(parent)
        if resolved != root and not resolved.startswith(root + os.sep):
            return False
    try:
        os.makedirs(parent, exist_ok=True)
        info = os.lstat(os.path.join(parent, leaf))
    except FileNotFoundError:
        info = None
    except OSError:
        return False
    if info is not None and not stat.S_ISREG(info.st_mode):
        return False
    try:
        payload = (
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        ).encode("utf-8")
    except Exception:
        # The mirror of the read-side guard, and it must be here too: a planted
        # record that *parses* but nests deeply raises RecursionError inside the
        # encoder. Keep diagnostic writes bounded even for malformed input.
        # This also covers non-serializable values.
        return False
    if len(payload) > DIAGNOSTICS_MAX_BYTES:
        return False
    tmp = f".diag.{secrets.token_hex(8)}.tmp"
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    dir_fd = None
    try:
        dir_fd = os.open(parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        fd = os.open(tmp, flags, 0o600, dir_fd=dir_fd)
        try:
            view = memoryview(payload)
            while view:
                written = os.write(fd, view)
                if written <= 0:
                    raise OSError("diagnostics write made no progress")
                view = view[written:]
        finally:
            os.close(fd)
        os.replace(tmp, leaf, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        return True
    except BaseException as exc:
        if dir_fd is not None:
            try:
                os.unlink(tmp, dir_fd=dir_fd)
            except OSError:
                pass
        if isinstance(exc, OSError):
            return False
        raise
    finally:
        if dir_fd is not None:
            try:
                os.close(dir_fd)
            except OSError:
                pass


def _hook_payload_cwd():
    cwd = _LAST_HOOK_INPUT.get("cwd")
    if isinstance(cwd, str) and cwd:
        return cwd
    return None


def current_session_id():
    raw = (
        _LAST_HOOK_INPUT.get("session_id")
        or _LAST_HOOK_INPUT.get("sessionId")
        or os.environ.get("HARNESS_SESSION_ID")
        or os.environ.get("CODEX_SESSION_ID")
        or os.environ.get("CODEX_THREAD_ID")
        or os.environ.get("CLAUDE_SESSION_ID")
        or ""
    )
    return raw if isinstance(raw, str) and _usable_session_hint(raw) == raw else ""


def sanitize_session_id(value, default="default"):
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or default)).strip("._")
    return safe or default


def emit_permission_decision(decision, reason="", *, next_action_command="",
                             owner_skill="", docs=""):
    """Emit a deny decision; allow remains silent."""
    if decision != "deny":
        return
    full_reason = str(reason)
    tail_lines = []
    if next_action_command:
        tail_lines.append(f"↳ next action: {next_action_command}")
    if owner_skill:
        tail_lines.append(f"↳ owner: {owner_skill}")
    if docs:
        tail_lines.append(f"↳ docs: {docs}")
    if tail_lines:
        full_reason = full_reason + "\n\n" + "\n".join(tail_lines)
    envelope = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": full_reason[:2000],
        }
    }
    try:
        _sys.stdout.write(_json.dumps(envelope))
        _sys.stdout.flush()
    except Exception:
        pass


_ESCAPE_KEYS = {
    "prewrite": "HARNESS_SKIP_PREWRITE",
}


def _escape_hint(gate_name):
    key = _ESCAPE_KEYS.get(
        gate_name,
        "HARNESS_SKIP_" + str(gate_name or "").upper().replace("-", "_"),
    )
    return f"escape: {key}=1 <retry>"


def _log_gate_error(exc, source, repo_root=None):
    """Append a gate-exception entry to doc/harness/learnings.jsonl.

    Best-effort; any failure is swallowed. Used by gate scripts' outer
    try/except so silent fail-open doesn't decay into an invisible dead gate.

    `repo_root` is for a caller that already knows which repo the record
    belongs to. Resolving from the process cwd is right for a gate, which runs
    in the project, and wrong for a caller reached from a hook whose cwd may be
    elsewhere while it holds the task directory.
    """
    try:
        repo_root = repo_root or find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-error",
            "source": str(source or "gate"),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def log_gate_crash(exc, script, hook_input=None):
    """Structured gate-crash log (AC-007 of TASK__dual-runtime-plugin-claude-codex).

    Payload-aware upgrade over :func:`_log_gate_error`. Records the script
    name, tool name, payload keys, and exception. Used by gate scripts'
    top-level except so a `|| true` swallowed crash leaves a diagnostic
    breadcrumb. Critical for detecting Codex vs Claude payload key drift
    (e.g. `tool_input` vs `input`, `tool_name` vs `tool`) — when a gate
    crashes silently, this is the only post-hoc signal.

    Schema (one JSON line in `doc/harness/learnings.jsonl`):
      ts            ISO timestamp
      type          "gate-crash" (versus _log_gate_error's "gate-error" for legacy callers)
      script        the gate name (e.g. "prewrite_gate", "task_close")
      tool_name     hook_input["tool_name"] if present, truncated to 120 chars
      payload_keys  sorted top-level keys of hook_input (for drift detection)
      error         "<ExceptionName>: <message>" capped at 400 chars

    Best-effort; never raises. Safe in `|| true` outer wrapper.
    """
    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        record = {
            "ts": now_iso(),
            "type": "gate-crash",
            "script": str(script or "gate"),
            "error": f"{type(exc).__name__}: {str(exc)[:400]}",
        }
        if isinstance(hook_input, dict):
            tn = hook_input.get("tool_name")
            if tn:
                record["tool_name"] = str(tn)[:120]
            try:
                record["payload_keys"] = sorted(hook_input.keys())
            except Exception:
                pass
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(_json.dumps(record) + "\n")
    except Exception:
        pass


# Keyed by `_unbound_completion_cause`; each value is (what the row shows, what
# to do about it). Split by cause because one unconditional sentence was false
# on two of the three: a review reporting `VERDICT: PASS` beside `FIX_NOW=2`
# bound its verdict fine and was told to move that verdict to line 1, where it
# already was. This repo does not write false statements into diagnostics — see
# `doc/harness/REQ__gate-does-not-demand-impossible-evidence.md`, which exists
# because a fixed park reason asserted something untrue — and the read-side
# `nonparsing_completion_note` already separates these, so a write side that
# asserts one cause makes the two complementary paths disagree.
_UNBOUND_COMPLETION_CAUSES = {
    "verdict": (
        "its verdict did not bind",
        # Kept short: `_log_gate_error` caps the ledger line at 400 characters,
        # and a longer action lost its remedy clause for real task-dir names.
        "Re-deliver that lens final with exactly one verdict block on line 1; "
        "if it was an interim message from a lens waiting on its own work, "
        "spawn it fresh in the foreground.",
    ),
    "counts": (
        "only its verdict line bound — its FINDING_COUNTS line did not",
        "Re-deliver that lens final with FINDING_COUNTS on the line directly "
        "below the verdict.",
    ),
    "contradiction": (
        "its verdict line and its own FINDING_COUNTS line disagree",
        "Re-deliver that lens final with a verdict its counts support.",
    ),
}


def log_gate_bypass(gate_name, path=""):
    """Append a gate-bypass entry when an escape-hatch env var short-circuits a gate."""
    try:
        repo_root = find_repo_root()
        if not is_harness_enabled_repo(repo_root):
            return
        learn_path = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
        os.makedirs(os.path.dirname(learn_path), exist_ok=True)
        entry = _json.dumps({
            "ts": now_iso(),
            "type": "gate-bypass",
            "source": str(gate_name or "gate"),
            "path": str(path or ""),
        })
        with open(learn_path, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass


# ── YAML helpers (simple key-value + block arrays, no pyyaml) ────────────


def yaml_field(field, path):
    """Read a scalar field from a flat YAML file."""
    if not os.path.isfile(path):
        return None
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith(prefix):
                val = line[len(prefix):].strip()
                if val in ("null", "~", "", "[]"):
                    return None
                return val.strip('"').strip("'")
    return None


def yaml_array(field, path):
    """Read a YAML array field (compact [] or block - item)."""
    if not os.path.isfile(path):
        return []
    prefix = field + ":"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            rest = line[len(prefix):].strip()
            if rest == "[]":
                return []
            items = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.*)", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def _yaml_fmt(val):
    """Format a value for YAML output."""
    if val is None:
        return "null"
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, list):
        if not val:
            return "[]"
        def _quote_item(v):
            s = str(v)
            return f'"{s}"' if ":" in s or s != s.strip() else s
        return "\n" + "\n".join(f"  - {_quote_item(v)}" for v in val)
    return str(val)


# ── Frontmatter public API (AC-001) ─────────────────────────────────────
#
# Promoted from note_freshness.py private helpers. These four functions form
# the canonical frontmatter read/write surface used by note_freshness.py
# (which re-imports them).


def split_frontmatter(text: str) -> "tuple[str | None, str, int]":
    """Return (frontmatter_content, body_after_closing_fence, closing_fence_line_index).

    Returns (None, text, -1) if no valid frontmatter found.
    Public alias for the parser promoted from note_freshness.py.
    """
    if not text.startswith("---"):
        return None, text, -1
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip() != "---":
        return None, text, -1
    for i in range(1, len(lines)):
        if lines[i].rstrip() == "---":
            fm = "".join(lines[1:i])
            body = "".join(lines[i + 1:])
            return fm, body, i
    return None, text, -1


def read_array_field(frontmatter: str, field: str) -> "list[str]":
    """Read a YAML array field from frontmatter string.

    Supports both compact ``[a, b]`` and block ``- item`` styles.
    Public alias promoted from note_freshness.py ``_read_array``.
    """
    lines = frontmatter.splitlines()
    prefix = field + ":"
    for i, ln in enumerate(lines):
        if ln.startswith(prefix):
            rest = ln[len(prefix):].strip()
            if rest.startswith("[") and rest.endswith("]"):
                inner = rest[1:-1].strip()
                if not inner:
                    return []
                return [x.strip().strip('"').strip("'") for x in inner.split(",")]
            items: list = []
            for j in range(i + 1, len(lines)):
                m = re.match(r"^\s+-\s+(.+?)\s*$", lines[j])
                if not m:
                    break
                items.append(m.group(1).strip().strip('"').strip("'"))
            return items
    return []


def read_scalar_field(frontmatter: str, field: str) -> "str | None":
    """Read a scalar field from frontmatter string.

    Public alias promoted from note_freshness.py ``_read_scalar``.
    """
    m = re.search(rf"^{re.escape(field)}:\s*(.*)$", frontmatter, re.MULTILINE)
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def set_scalar_field(frontmatter: str, field: str, value: str) -> str:
    """Set (or append) a scalar field in frontmatter string.

    Public alias promoted from note_freshness.py ``_set_scalar``.
    If the field exists it is replaced in-place; otherwise appended.
    """
    pattern = rf"^{re.escape(field)}:\s*.*$"
    replacement = f"{field}: {value}"
    new_fm, n = re.subn(pattern, replacement, frontmatter, count=1, flags=re.MULTILINE)
    if n:
        return new_fm
    new_fm = new_fm.rstrip("\n") + "\n"
    return new_fm + f"{field}: {value}\n"


# ── Task control read/write ──────────────────────────────────────────────


def task_control_file(task_dir):
    return os.path.join(task_dir, TASK_CONTROL_NAME)


def _task_control_lenses(value):
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in SUPPORTED_LENSES for item in value)
        or len(value) != len(set(value))
        or value != [lens for lens in LENS_ORDER if lens in value]
        or "review-code" not in value
        or not any(lens.startswith("qa-") for lens in value)
    ):
        return None
    return list(value)


def new_uuid7(timestamp_ms=None):
    """Generate a canonical RFC 9562 UUIDv7 using Python 3.12 stdlib."""
    if timestamp_ms is None:
        timestamp_ms = time.time_ns() // 1_000_000
    if not isinstance(timestamp_ms, int) or not 0 <= timestamp_ms < (1 << 48):
        raise ValueError("UUIDv7 timestamp must fit in 48 unsigned bits")
    random_bits = secrets.randbits(74)
    value = (
        (timestamp_ms << 80)
        | (0x7 << 76)
        | (((random_bits >> 62) & 0xFFF) << 64)
        | (0b10 << 62)
        | (random_bits & ((1 << 62) - 1))
    )
    return str(uuid.UUID(int=value))


def uuid7_timestamp_ms(value):
    """Validate canonical UUIDv7 text and return its Unix millisecond time."""
    if not isinstance(value, str) or not re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-7[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}",
        value,
    ):
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7")
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError) as exc:
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7") from exc
    if str(parsed) != value or parsed.version != 7 or parsed.variant != uuid.RFC_4122:
        raise ValueError("run_id must be a canonical lowercase RFC 9562 UUIDv7")
    return parsed.int >> 80


def task_run_started_at(control):
    """Return the TASK run cutoff as canonical UTC milliseconds."""
    milliseconds = uuid7_timestamp_ms((control or {}).get("run_id"))
    return datetime.fromtimestamp(milliseconds / 1000, timezone.utc).isoformat(
        timespec="milliseconds"
    ).replace("+00:00", "Z")


def _validate_task_control(data):
    if not isinstance(data, dict) or set(data) != TASK_CONTROL_FIELDS:
        return {}
    run_id = data.get("run_id")
    mode = data.get("execution_mode")
    closed = data.get("closed")
    try:
        uuid7_timestamp_ms(run_id)
    except ValueError:
        return {}
    lenses = _task_control_lenses(data.get("required_lenses"))
    if (
        mode not in {"standard", "micro"}
        or lenses is None
        or type(closed) is not bool
    ):
        return {}
    return {
        "run_id": run_id,
        "execution_mode": mode,
        "required_lenses": lenses,
        "closed": closed,
    }


def _read_task_control_text(path):
    flags = (os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
             | getattr(os, "O_NONBLOCK", 0))
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > 16 * 1024
        ):
            return ""
        fd = os.open(path, flags)
    except OSError:
        return ""
    try:
        opened = os.fstat(fd)
        identity = (
            opened.st_dev, opened.st_ino, opened.st_size, opened.st_mode,
            opened.st_uid, opened.st_nlink, opened.st_mtime_ns, opened.st_ctime_ns,
        )
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino) or opened.st_size > 16 * 1024:
            return ""
        chunks = []
        remaining = 16 * 1024 + 1
        while remaining:
            chunk = os.read(fd, min(4096, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        if not remaining:
            return ""
        after = os.fstat(fd)
        if identity != (
            after.st_dev, after.st_ino, after.st_size, after.st_mode,
            after.st_uid, after.st_nlink, after.st_mtime_ns, after.st_ctime_ns,
        ):
            return ""
        return b"".join(chunks).decode("utf-8")
    except (OSError, UnicodeError):
        return ""
    finally:
        os.close(fd)


def read_task_control(task_dir):
    """Read the one exact, owner-controlled task authority or fail closed."""
    text = _read_task_control_text(task_control_file(task_dir))
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate TASK.json key: {key}")
            result[key] = value
        return result
    try:
        data = json.loads(text, object_pairs_hook=unique_object) if text else {}
    except (TypeError, ValueError):
        return {}
    return _validate_task_control(data)


def _make_control_writer_authority():
    allowed = {
        "harness_server": {
            "handle_task_start", "handle_task_context", "handle_task_close",
            "handle_task_blocked", "handle_write_plan", "handle_wbs_migrate",
            "handle_task_wbs", "handle_verification_target",
        },
        "codex_hook_registration": {
            "register_native_task", "register_task_result",
        },
    }
    canonical_paths = {
        "harness_server": os.path.realpath(os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "mcp", "harness_server.py",
        )),
        "codex_hook_registration": os.path.realpath(os.path.join(
            os.path.dirname(__file__), "codex_hook_registration.py",
        )),
    }
    bindings = {}

    def module_codes(module):
        found = set()
        pending = []
        for value in vars(module).values():
            if inspect.isfunction(value) and value.__globals__ is vars(module):
                pending.append(value.__code__)
            elif inspect.isclass(value) and value.__module__ == module.__name__:
                pending.extend(
                    item.__code__ for item in vars(value).values()
                    if inspect.isfunction(item) and item.__globals__ is vars(module)
                )
        while pending:
            code = pending.pop()
            if code in found:
                continue
            found.add(code)
            pending.extend(item for item in code.co_consts if isinstance(item, CodeType))
        return found

    protected_dependencies = {
        "write_task_control", "begin_task_run", "restore_task_control",
        "write_active_marker", "clear_active_marker",
        "restore_active_marker_snapshot", "publish_task_close",
    }
    def bind(function):
        module_name = function.__module__
        function_path = os.path.realpath(function.__code__.co_filename)
        roles = [role for role, path in canonical_paths.items() if path == function_path]
        role = roles[0] if len(roles) == 1 else ""
        if function.__qualname__ not in allowed.get(role, set()):
            raise PermissionError("unsupported task-control writer")
        module = sys.modules.get(module_name)
        expected_path = canonical_paths[role]
        caller = inspect.currentframe().f_back
        module_path = os.path.realpath(str(getattr(module, "__file__", "") or ""))
        if module_name == "__main__":
            canonical_loader = (
                os.path.realpath(sys.argv[0]) == expected_path
                and getattr(module, "__spec__", None) is None
            )
        else:
            spec = getattr(module, "__spec__", None)
            loader = getattr(spec, "loader", None)
            canonical_loader = (
                module_name == role
                and isinstance(loader, importlib.machinery.SourceFileLoader)
                and os.path.realpath(str(getattr(spec, "origin", "") or "")) == expected_path
                and os.path.realpath(loader.get_filename(module_name)) == expected_path
            )
        owner = module
        for part in function.__qualname__.split("."):
            owner = getattr(owner, part, None)
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        try:
            before = os.lstat(expected_path)
            fd = os.open(expected_path, flags)
            try:
                opened = os.fstat(fd)
                chunks = []
                while True:
                    chunk = os.read(fd, 64 * 1024)
                    if not chunk:
                        break
                    chunks.append(chunk)
                after = os.fstat(fd)
            finally:
                os.close(fd)
            final_path = os.lstat(expected_path)
        except OSError as exc:
            raise PermissionError("task-control writer source is unsafe") from exc
        source_identity = lambda value: (
            value.st_dev, value.st_ino, value.st_size, value.st_mode,
            value.st_uid, value.st_nlink, value.st_mtime_ns, value.st_ctime_ns,
        )
        canonical_root = compile(b"".join(chunks), expected_path, "exec")
        candidates = []
        pending = [canonical_root]
        while pending:
            candidate = pending.pop()
            if candidate.co_qualname == function.__qualname__:
                candidates.append(candidate)
            pending.extend(value for value in candidate.co_consts if isinstance(value, CodeType))
        if (
            caller is None
            or caller.f_code.co_name != "<module>"
            or caller.f_code != canonical_root
            or module is None
            or caller.f_globals is not vars(module)
            or function.__globals__ is not vars(module)
            or module_path != expected_path
            or not canonical_loader
            or owner is not function
            or len(candidates) != 1
            or candidates[0] != function.__code__
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or before.st_mode & 0o022
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
            or source_identity(opened) != source_identity(after)
            or (final_path.st_dev, final_path.st_ino) != (before.st_dev, before.st_ino)
        ):
            raise PermissionError("task-control writer requires its canonical module import")
        dependencies = tuple(
            (name, function.__globals__[name]) for name in function.__code__.co_names
            if name in protected_dependencies and name in function.__globals__
        )
        library = sys.modules[__name__]
        allowed_frames = {
            **{code: vars(module) for code in module_codes(module)},
            **{code: vars(library) for code in module_codes(library)},
        }
        identity = (role, function.__qualname__)
        binding = (
            function.__code__, function.__globals__, dependencies, role, allowed_frames,
        )
        existing = bindings.get(identity)
        if existing is not None and existing != binding:
            raise PermissionError("task-control writer is already bound")
        bindings[identity] = binding

    def authorized(marker=False):
        frame = inspect.currentframe()
        try:
            caller = frame.f_back.f_back if frame and frame.f_back else None
            while caller is not None:
                binding = next((
                    value for (role, qualname), value in bindings.items()
                    if qualname == caller.f_code.co_qualname
                    and (marker or role == "harness_server")
                ), None)
                if binding is not None:
                    return bool(
                        caller.f_code is binding[0]
                        and caller.f_globals is binding[1]
                        and all(caller.f_globals.get(name) is value for name, value in binding[2])
                    )
                if not any(
                    value[4].get(caller.f_code) is caller.f_globals
                    for (role, _), value in bindings.items()
                    if marker or role == "harness_server"
                ):
                    return False
                caller = caller.f_back
            return False
        finally:
            del caller
            del frame

    def refusal_category(marker=False):
        """Name which *kind* of refusal happened. Never why, in detail.

        `authorized()` returns a single False for at least four distinct
        situations, and two of them have opposite remedies:

        - nothing is bound at all — `harness_server` was never imported, or
          `_lib` was reloaded in-process, which re-executes this factory in the
          same module dict and empties `bindings`. Nothing the caller does at
          the call site can fix it.
        - something is bound but this caller is not it — an ordinary
          wrong-writer refusal, and the caller should route through the MCP.

        Diagnosing the first from the message alone was impossible, and in one
        2026-09-03 session that cost hours: `importlib.reload(_lib)` in a test
        setUp emptied the closure and every subsequent write in that process
        raised the wrong-writer text.

        This deliberately reports the *category* only. It reads no frame and
        compares no value, so it cannot leak inode, uid, path, or which
        identity check failed — telling an attacker which of the checks they
        tripped is exactly how a guard becomes a puzzle to be solved. It is
        also computed after the decision, never as part of it.
        """
        # `marker` selects the same binding subset `authorized()` consults, so
        # the category answers the question the caller actually asked. Without
        # it, a process that bound only the Codex hook registration reported
        # "not the bound writer" for a task-control write when in truth no
        # task-control writer was bound at all — the exact confusion AC-3 names.
        if not any(marker or role == "harness_server" for role, _ in bindings):
            return (
                "no task-control writer is bound in this process — "
                "import the canonical MCP module, and note that reloading _lib "
                "empties the binding"
            )
        return "the calling frame is not the bound task-control writer"

    return authorized, bind, refusal_category


(
    _trusted_control_writer,
    _bind_control_writer,
    _control_writer_refusal,
) = _make_control_writer_authority()
del _make_control_writer_authority


def _control_writer_error(action: str, *, marker: bool = False) -> PermissionError:
    """Build a refusal that names the action *and* the category of failure."""
    return PermissionError(f"{action}: {_control_writer_refusal(marker=marker)}")


def write_task_control(task_dir, control):
    """Atomically publish one exact TASK.json value."""
    validated = _validate_task_control(control)
    if not validated:
        raise ValueError("invalid exact TASK.json control value")
    path = task_control_file(task_dir)
    if not _trusted_control_writer():
        raise _control_writer_error("TASK.json mutation requires the task-control MCP")
    os.makedirs(task_dir, exist_ok=True)
    _revalidate_task_control_transaction(task_dir)
    if os.path.lexists(path) and not read_task_control(task_dir):
        raise RuntimeError("existing TASK.json is unsafe or invalid")
    snapshot = {path: _strict_regular_text_snapshot(path, max_size=16 * 1024)}
    try:
        _atomic_text_write(path, json.dumps(validated, indent=2, sort_keys=True) + "\n")
        _revalidate_task_control_transaction(task_dir)
    except BaseException:
        _restore_text_snapshots(snapshot)
        raise
    return True


def _new_task_control(*, execution_mode="standard"):
    return {
        "run_id": new_uuid7(),
        "execution_mode": execution_mode,
        "required_lenses": ["review-code", "qa-cli"],
        "closed": False,
    }


def begin_task_run(task_dir):
    """Rotate TASK.json run identity and clear terminal authority."""
    if not _trusted_control_writer():
        raise _control_writer_error("task run rotation requires the task-control MCP")
    path = task_control_file(task_dir)
    snapshot = {path: _strict_regular_text_snapshot(path, max_size=16 * 1024)}
    current = read_task_control(task_dir)
    if not current:
        raise RuntimeError("valid TASK.json required to rotate task run")
    payload = dict(current)
    payload.update({
        "run_id": new_uuid7(),
        "closed": False,
    })
    write_task_control(task_dir, payload)
    return payload, snapshot


def restore_task_control(snapshot):
    if not _trusted_control_writer():
        raise _control_writer_error("TASK.json restoration requires the task-control MCP")
    _restore_text_snapshots(snapshot)


# ── Path resolution ──────────────────────────────────────────────────────


def find_repo_root(start_dir=None):
    """Find git repo root."""
    # Codex plugin-local hooks may execute from the installed plugin directory
    # while the hook payload still carries the project cwd. Prefer that payload
    # cwd so gates read the user's repo, not ~/.codex/harness/plugins/harness.
    d = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    while d != "/":
        git_path = os.path.join(d, ".git")
        if os.path.isdir(git_path) or os.path.isfile(git_path):
            return d
        d = os.path.dirname(d)
    return os.path.abspath(start_dir or os.getcwd())


class GitBindingError(RuntimeError):
    """Actionable, fail-closed error at an explicit Git trust boundary."""

    def __init__(self, code, message, *, path="", invariant="", next_action=""):
        self.code = code
        self.path = path
        self.invariant = invariant
        self.next_action = next_action
        super().__init__(f"[{code}] {message}")


def _trusted_git_env():
    """Return an environment without ambient Git repository/config overrides."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith("GIT_")
    }


def _canonical_git_relpath(value):
    """Normalize an explicitly returned Git path without resolving it."""
    rel = str(value or "")
    if os.sep == "\\":
        rel = rel.replace("\\", "/")
    while rel.startswith("./"):
        rel = rel[2:]
    return rel


def _direct_gitlink_index_entries(repo_root, *, git_dir=None):
    """Return direct stage-0 gitlinks without traversing their worktrees."""
    try:
        command = ["git"]
        if git_dir:
            command.extend([f"--git-dir={git_dir}", f"--work-tree={repo_root}"])
        command.extend(["ls-files", "--stage", "-z"])
        result = subprocess.run(
            command,
            capture_output=True,
            cwd=repo_root,
            env=_trusted_git_env(),
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: direct gitlink index enumeration in {repo_root}"
        ) from exc
    if result.returncode != 0:
        raise RuntimeError(
            f"Git submodule snapshot unavailable: direct gitlink index enumeration failed in {repo_root}"
        )
    found = {}
    records = result.stdout.split(b"\0") if isinstance(result.stdout, bytes) else str(result.stdout or "").split("\0")
    for record in records:
        if not record:
            continue
        tab = b"\t" if isinstance(record, bytes) else "\t"
        metadata, separator, raw_path = record.partition(tab)
        fields = metadata.split()
        if not separator or not fields or fields[0] not in (b"160000", "160000"):
            continue
        if len(fields) != 3 or fields[2] not in (b"0", "0"):
            raise RuntimeError("Git submodule snapshot unavailable")
        path = _canonical_git_relpath(
            os.fsdecode(raw_path) if isinstance(raw_path, bytes) else raw_path
        ).rstrip("/")
        oid = os.fsdecode(fields[1]) if isinstance(fields[1], bytes) else fields[1]
        if (
            not path
            or os.path.isabs(path)
            or path == ".."
            or path.startswith("../")
            or not re.fullmatch(r"[0-9a-fA-F]{40,64}", oid)
        ):
            raise RuntimeError("Git submodule snapshot unavailable")
        found[path] = oid.lower()
    return found


def _registered_source_metadata_binding(control_root, source_root, relpath):
    """Resolve Git metadata for an explicitly trusted registered source root."""
    git_path = os.path.join(source_root, ".git")
    try:
        git_info = os.lstat(git_path)
    except OSError as exc:
        raise GitBindingError(
            "REGISTERED_SOURCE_UNINITIALIZED",
            f"registered source '{relpath}' is not initialized",
            path=relpath,
            invariant="initialized_checkout",
            next_action="Restore the checkout at the registered path, then retry.",
        ) from exc
    if stat.S_ISDIR(git_info.st_mode):
        return git_path
    if stat.S_ISLNK(git_info.st_mode) or not stat.S_ISREG(git_info.st_mode):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has invalid .git metadata",
            path=relpath,
            invariant="gitfile_regular",
            next_action="Repair the checkout and retry.",
        )

    try:
        with open(git_path, "r", encoding="utf-8") as handle:
            line = handle.read(4097).strip()
    except (OSError, UnicodeError) as exc:
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has unreadable .git metadata",
            path=relpath,
            invariant="gitdir_pointer",
            next_action="Repair the checkout and retry.",
        ) from exc
    if not line.startswith("gitdir: ") or not line[len("gitdir: "):].strip():
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' has malformed .git metadata",
            path=relpath,
            invariant="gitdir_pointer",
            next_action="Repair the Git worktree binding and retry.",
        )
    target = line[len("gitdir: "):].strip()
    target = os.path.abspath(target if os.path.isabs(target) else os.path.join(source_root, target))
    if not os.path.isdir(target):
        raise GitBindingError(
            "REGISTERED_WORKTREE_BINDING_MISMATCH",
            f"registered source '{relpath}' points to missing Git metadata",
            path=relpath,
            invariant="gitdir_path",
            next_action="Repair the checkout and retry.",
        )
    return target


def _registered_worktree_root(control_root, workspace):
    """Resolve same-repository Git registration without admitting Harness use.

    Pure file checks, re-run on every call: canonical absolute path, a regular
    gitfile whose target sits directly under ``<control>/.git/worktrees``, a
    matching back-pointer. The caller separately validates Harness configuration.
    A workspace equal to the control root returns "" (treated as omitted).
    """
    control = os.path.realpath(control_root)
    ws = str(workspace or "")

    def refuse(message, invariant):
        raise GitBindingError(
            "WORKSPACE_NOT_REGISTERED_WORKTREE",
            message,
            path=ws,
            invariant=invariant,
            next_action=(
                "Pass the realpath of a linked worktree of this repository "
                "(git worktree add / Claude isolation: worktree), or omit workspace."
            ),
        )

    if not ws or not os.path.isabs(ws) or os.path.realpath(ws) != ws:
        refuse("workspace must be an absolute canonical path", "canonical_path")
    if ws == control:
        return ""
    try:
        target = _registered_source_metadata_binding(control, ws, ws)
    except GitBindingError as exc:
        refuse(f"workspace has no usable gitfile: {exc}", exc.invariant or "gitfile")
    if not os.path.isfile(os.path.join(ws, ".git")):
        refuse("workspace is a main checkout, not a linked worktree", "gitfile_regular")
    worktrees_dir = os.path.realpath(os.path.join(control, ".git", "worktrees"))
    target = os.path.realpath(target)
    if os.path.dirname(target) != worktrees_dir:
        refuse("workspace gitdir is not registered under this repository", "gitdir_registered")
    try:
        back = _read_regular_text_file(os.path.join(target, "gitdir"), max_size=4096).strip()
    except (OSError, ValueError):
        back = ""
    if not back:
        refuse("registered worktree has no gitdir back-pointer", "gitdir_backpointer")
    if not os.path.isabs(back):
        back = os.path.join(target, back)
    if os.path.realpath(back) != os.path.join(ws, ".git"):
        refuse("registered worktree back-pointer names another checkout", "gitdir_backpointer")
    return ws


def resolve_registered_worktree(control_root, workspace):
    """Admit only a registered linked checkout with its own valid manifest."""
    ws = _registered_worktree_root(control_root, workspace)
    if not ws:
        return ""
    root, error = harness_root_resolution(ws)
    if error or root != ws:
        raise GitBindingError(
            "WORKSPACE_NOT_REGISTERED_WORKTREE",
            "workspace does not carry its own Harness manifest",
            path=ws, invariant="harness_manifest",
            next_action="Restore the worktree's own valid Harness manifest, then retry.",
        )
    return ws


def harness_root_resolution(start_dir=None):
    """Return ``(root, error)`` for valid/none/invalid Harness ancestry.

    A trusted ancestor manifest owns nested repositories only while that
    control root has an explicit active task marker. This lets a delegated
    agent finish from an ignored child repository without causing unrelated
    nested repositories to inherit an outer Harness installation.
    """
    start = os.path.realpath(start_dir or _hook_payload_cwd() or os.getcwd())
    current = start
    nearest_git = ""
    while True:
        manifest_path = os.path.join(current, MANIFEST_PATH)
        if os.path.lexists(manifest_path):
            try:
                manifest_info = None
                probe = current
                components = MANIFEST_PATH.split("/")
                for index, component in enumerate(components):
                    probe = os.path.join(probe, component)
                    info = os.lstat(probe)
                    if stat.S_ISLNK(info.st_mode):
                        if index == len(components) - 1:
                            return current, (
                                "Harness manifest must be a regular non-symlink file"
                            )
                        return current, (
                            "Harness manifest path components must not be symlinks"
                        )
                    manifest_info = info
            except OSError as exc:
                return current, f"Harness manifest is unreadable: {exc}"
            if (
                manifest_info is None
                or not stat.S_ISREG(manifest_info.st_mode)
            ):
                return current, "Harness manifest must be a regular non-symlink file"
            if nearest_git and nearest_git != current:
                try:
                    sid = current_session_id()
                    binding = resolve_session_task_binding(current, sid)
                    if not binding:
                        return "", ""
                except (OSError, RuntimeError, ValueError):
                    return "", ""
            try:
                if os.path.commonpath((current, start)) == current:
                    return current, ""
            except ValueError:
                return "", ""
        if not nearest_git and os.path.lexists(os.path.join(current, ".git")):
            nearest_git = current
        parent = os.path.dirname(current)
        if parent == current:
            return "", ""
        current = parent


def find_harness_root(start_dir=None):
    """Find a valid Harness control root; invalid ancestry is not valid."""
    root, error = harness_root_resolution(start_dir)
    return "" if error else root


def is_harness_enabled_repo(repo_root=None):
    """Return True when a repo has completed harness setup.

    Claude hooks may be installed globally and can run from arbitrary project
    directories. A git root alone is not enough permission to create
    ``doc/harness`` runtime files; setup creates ``doc/harness/manifest.yaml``.
    """
    root = repo_root or find_harness_root() or find_repo_root()
    return os.path.isfile(os.path.join(root, MANIFEST_PATH))


_TASK_ID_RE = re.compile(r"TASK__[A-Za-z0-9_.-]{1,180}\Z")


def _normalize_task_id(task_id=None, slug=None, task_dir=None):
    """Derive canonical TASK__<id> from arguments."""
    if task_id:
        value = str(task_id)
        tid = value if value.startswith("TASK__") else f"TASK__{value}"
        field = "task_id"
    if slug:
        value = str(slug)
        tid = value if value.startswith("TASK__") else f"TASK__{value}"
        field = "slug"
    if task_dir and not task_id and not slug:
        name = os.path.basename(os.path.normpath(task_dir))
        tid = name if name.startswith("TASK__") else f"TASK__{name}"
        field = "task_dir"
    if not (task_id or slug or task_dir):
        return None
    if not _TASK_ID_RE.fullmatch(tid):
        raise ValueError(
            f"invalid {field}; expected canonical TASK__<safe-id> using only "
            "letters, digits, dot, underscore, or hyphen"
        )
    return tid


def canonical_task_dir(task_id=None, slug=None, task_dir=None,
                       tasks_dir=TASK_DIR, repo_root=None):
    """Resolve a task selector to an immediate child of the repo task root."""
    repo_root = repo_root or find_repo_root()
    selectors = [
        _normalize_task_id(task_id=task_id) if task_id else None,
        _normalize_task_id(slug=slug) if slug else None,
        _normalize_task_id(task_dir=task_dir) if task_dir else None,
    ]
    selected = {item for item in selectors if item}
    if len(selected) > 1:
        raise ValueError(
            "task selectors disagree; task_id, slug, and task_dir must name the same canonical TASK__<safe-id>"
        )
    tid = next(iter(selected), None)
    if not tid:
        return ""
    tasks_root = _validated_control_dir(repo_root, tasks_dir, "canonical task root")
    canonical = os.path.join(tasks_root, tid)
    if task_dir:
        raw = str(task_dir)
        if any(ord(ch) < 32 for ch in raw):
            raise ValueError("invalid task_dir; control characters are not allowed")
        candidate = os.path.abspath(raw if os.path.isabs(raw) else os.path.join(repo_root, raw))
        expected_form = canonical if os.path.isabs(raw) else os.path.relpath(canonical, repo_root)
        if raw != expected_form:
            raise ValueError(
                f"invalid task_dir; expected exact canonical path {os.path.relpath(canonical, repo_root)} "
                "or its absolute path without traversal or aliases"
            )
        if candidate != canonical or os.path.dirname(candidate) != tasks_root:
            raise ValueError(
                f"invalid task_dir; expected canonical {os.path.relpath(canonical, repo_root)} "
                "or its absolute path inside this repository"
            )
    expected_real = os.path.join(os.path.realpath(tasks_root), tid)
    if os.path.lexists(canonical):
        if os.path.islink(canonical) or not os.path.isdir(canonical):
            raise ValueError("invalid task selector; canonical task directory must be a real directory")
        if os.path.realpath(canonical) != expected_real:
            raise ValueError("invalid task selector; canonical task directory resolves outside this repository")
    return canonical


def canonical_task_id(task_id=None, slug=None, task_dir=None,
                      tasks_dir=TASK_DIR, repo_root=None):
    """Derive canonical task id string."""
    resolved = canonical_task_dir(
        task_id=task_id,
        slug=slug,
        task_dir=task_dir,
        tasks_dir=tasks_dir,
        repo_root=repo_root,
    )
    return os.path.basename(resolved) if resolved else ""


# ── Active task markers ─────────────────────────────────────────────────


ACTIVE_SESSIONS_DIRNAME = ".active_sessions"


def _active_sessions_dir(repo_root):
    return _validated_control_dir(
        repo_root,
        f"{TASK_DIR}/{ACTIVE_SESSIONS_DIRNAME}",
        "active session marker root",
    )


SESSION_HINT_NAME = ".session-hint"


def _session_hint_path(repo_root):
    return os.path.join(_active_sessions_dir(repo_root), SESSION_HINT_NAME)


def _usable_session_hint(value):
    """Return a session id safe to bind a marker to, or '' when unusable."""
    sid = str(value or "").strip()
    if not sid or sid == "default" or sanitize_session_id(sid) != sid:
        return ""
    return sid


def write_session_hint(repo_root, session_id):
    """Record an advisory hook hint; never use it as session authority.

    Multiple sessions share this file, so it cannot authenticate an MCP caller.
    Hooks publish exact task focus from their trusted successful tool results.
    """
    sid = _usable_session_hint(session_id)
    if not sid:
        return False
    try:
        os.makedirs(_active_sessions_dir(repo_root), exist_ok=True)
        _atomic_text_write(_session_hint_path(repo_root), sid + "\n")
    except (OSError, ValueError):
        return False
    return True


def read_session_hint(repo_root):
    """Return the recorded runtime session id, or '' when absent/unusable."""
    try:
        raw = _read_regular_text_file(_session_hint_path(repo_root), max_size=4096)
    except (OSError, ValueError):
        return ""
    first = (str(raw or "").strip().splitlines() or [""])[0]
    return _usable_session_hint(first)


def _session_active_path(repo_root, session_id=None):
    sid = current_session_id() if session_id is None else session_id
    if not isinstance(sid, str) or not sid or sid == "default" or sanitize_session_id(sid) != sid:
        raise ValueError("invalid exact session id")
    return os.path.join(_active_sessions_dir(repo_root), sid + ".json")


def read_active_session_marker(repo_root, session_id, *, strict=False):
    """Read one exact marker, including a non-authoritative recovery fence."""
    sid = session_id
    if not isinstance(sid, str) or not sid or sid == "default" or sanitize_session_id(sid) != sid:
        return {}
    return _read_session_marker(_session_active_path(repo_root, sid), sid, strict=strict)


def _publish_session_marker(repo_root, sid, payload):
    fd, tmp = tempfile.mkstemp(dir=_active_sessions_dir(repo_root), prefix=".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, _session_active_path(repo_root, sid))
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


@contextmanager
def active_session_transaction(repo_root):
    """Serialize exact-session marker decisions within one repository.

    PostToolUse callbacks can arrive out of order. Locking the marker directory
    lets a caller compare the current exact binding and publish its replacement
    as one decision, so a delayed result cannot race a newer task binding.
    """
    root = os.path.abspath(repo_root)
    sessions = _active_sessions_dir(root)
    os.makedirs(sessions, mode=0o700, exist_ok=True)
    relative = os.path.relpath(sessions, root)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    component_fds = []
    entered = False
    try:
        fd = os.open(root, flags)
        component_fds.append(fd)
        for part in relative.split(os.sep):
            fd = os.open(part, flags, dir_fd=fd)
            component_fds.append(fd)
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RuntimeError("active session storage integrity unavailable")
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("active session storage integrity unavailable") from exc
        entered = True
        yield
        after = os.lstat(sessions)
        if (
            stat.S_ISLNK(after.st_mode)
            or not stat.S_ISDIR(after.st_mode)
            or (after.st_dev, after.st_ino) != (info.st_dev, info.st_ino)
        ):
            raise RuntimeError("active session storage identity changed")
    except OSError as exc:
        if entered:
            raise
        raise RuntimeError("active session storage integrity unavailable") from exc
    finally:
        for component_fd in reversed(component_fds):
            os.close(component_fd)


def write_active_marker(repo_root, task_dir, session_id=None):
    """Publish only one exact session's current task generation."""
    if not _trusted_control_writer(marker=True):
        raise _control_writer_error("active task binding requires the task-control runtime", marker=True)
    sid = current_session_id() if session_id is None else session_id
    _session_active_path(repo_root, sid)  # Validate before creating any storage.
    task_run = read_task_control(task_dir)
    if not task_run:
        raise ValueError("invalid task control for exact session binding")
    os.makedirs(_active_sessions_dir(repo_root), exist_ok=True)
    if _has_binding_conflict_fence(read_active_session_marker(repo_root, sid)):
        raise RuntimeError("exact task result recovery must acknowledge the conflict fence first")
    _publish_session_marker(repo_root, sid, {
        "session_id": sid,
        "task_dir": task_dir,
        "task_id": os.path.basename(os.path.normpath(task_dir)),
        "run_id": task_run["run_id"],
        "updated": now_iso(),
    })


MAX_BINDING_CONFLICTS = 257
MAX_CONFLICT_BYTES = 128 * 1024


def _binding_conflicts_overflow(marker):
    raw = marker.get("conflicts")
    return marker.get("conflicts_overflow") is True or (
        isinstance(raw, list) and (
            len(raw) > MAX_BINDING_CONFLICTS
            or len(json.dumps(raw, ensure_ascii=True).encode("utf-8")) > MAX_CONFLICT_BYTES
        )
    )


def _has_binding_conflict_fence(marker):
    return "conflicts" in marker or marker.get("conflicts_overflow") is True


class _BindingConflictOverflow(RuntimeError):
    pass


def _live_binding_conflicts(marker, workspace_roots):
    """Read only live canonical generations in already validated workspaces."""
    if _binding_conflicts_overflow(marker):
        raise _BindingConflictOverflow("coordinator conflict fence exceeds its bound")
    raw = marker.get("conflicts")
    if not isinstance(raw, list) or len(raw) < 2:
        return []
    tasks_roots = {os.path.realpath(os.path.join(root, "doc", "harness", "tasks"))
                   for root in workspace_roots}
    found = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        task_dir = os.path.realpath(str(item.get("task_dir") or ""))
        task_id = str(item.get("task_id") or "")
        run_id = str(item.get("run_id") or "")
        if (not re.fullmatch(r"TASK__[A-Za-z0-9_.-]{1,180}", task_id)
            or os.path.dirname(task_dir) not in tasks_roots
            or os.path.basename(task_dir) != task_id):
            continue
        control = read_task_control(task_dir)
        if task_control_status(task_dir, control) == "open" and control.get("run_id") == run_id:
            found.append({"task_dir": task_dir, "task_id": task_id, "run_id": run_id})
    return found


def write_binding_conflict_fence(repo_root, session_id, conflicts, *, overflow=False):
    """Replace task authority with the exact conflicting task generations."""
    if not _trusted_control_writer(marker=True):
        raise _control_writer_error("binding recovery requires the task-control runtime", marker=True)
    sid = sanitize_session_id(session_id)
    if sid != session_id or sid == "default":
        raise ValueError("invalid exact session id")
    overflow = overflow or _binding_conflicts_overflow({"conflicts": conflicts})
    conflicts = [] if overflow else conflicts
    os.makedirs(_active_sessions_dir(repo_root), exist_ok=True)
    _publish_session_marker(repo_root, sid, {
        "session_id": sid,
        "conflicts": conflicts,
        **({"conflicts_overflow": True} if overflow else {}),
        "updated": now_iso(),
    })


def clear_binding_conflict_fence(repo_root, session_id):
    """Acknowledge a fence only after the caller revokes all registrations."""
    if not _trusted_control_writer(marker=True):
        raise _control_writer_error("binding recovery requires the task-control runtime", marker=True)
    sid = sanitize_session_id(session_id)
    if sid != session_id or sid == "default":
        raise ValueError("invalid exact session id")
    marker = read_active_session_marker(repo_root, sid)
    if _binding_conflicts_overflow(marker):
        raise _BindingConflictOverflow("overflow requires a new coordinator")
    if _has_binding_conflict_fence(marker):
        _publish_session_marker(repo_root, sid, {"session_id": sid, "updated": now_iso()})


def active_task_binding_matches(repo_root, task_dir, control=None, session_id=None):
    """Require an unfenced exact marker for the current open TASK generation."""
    control = control or read_task_control(task_dir)
    sid = current_session_id() if session_id is None else session_id
    binding = resolve_session_task_binding(repo_root, sid)
    return bool(binding and control and task_control_status(task_dir, control) == "open"
                and os.path.realpath(binding["task_dir"]) == os.path.realpath(task_dir)
                and binding["run_id"] == control.get("run_id"))


def active_marker_snapshot(repo_root, session_id=None):
    """Capture the exact session marker for rollback, or nothing when deferred."""
    sid = current_session_id() if session_id is None else session_id
    if not sid:
        return {}
    path = _session_active_path(repo_root, sid)
    return {path: _strict_regular_text_snapshot(
        path, max_size=256 * 1024, allow_symlink=True,
    )}


def restore_active_marker_snapshot(snapshot):
    """Restore an exact marker snapshot captured by active_marker_snapshot."""
    if not _trusted_control_writer(marker=True):
        raise _control_writer_error("active task restoration requires the task-control runtime", marker=True)
    # Ordinary rollback cannot acknowledge a fence published after its snapshot.
    # Callers hold the session transaction before their task transaction.
    restorable = {}
    for path, value in snapshot.items():
        if os.path.basename(os.path.dirname(path)) == ACTIVE_SESSIONS_DIRNAME:
            sid = os.path.basename(path).removesuffix(".json")
            if _has_binding_conflict_fence(_read_session_marker(path, sid)):
                continue
        restorable[path] = value
    _restore_text_snapshots(restorable)


def _read_regular_marker(path, *, max_size=256 * 1024):
    return _read_regular_text_file(path, max_size=max_size)


def _read_session_marker(path, expected_session_id, *, strict=False):
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"duplicate active marker key: {key}")
            result[key] = value
        return result
    if strict:
        snapshot = _strict_regular_text_snapshot(path, max_size=256 * 1024)
        if not snapshot["exists"]:
            return {}
        text = snapshot["text"]
    else:
        text = _read_regular_marker(path)
    try:
        data = json.loads(text, object_pairs_hook=unique_object)
    except (TypeError, ValueError):
        if strict:
            raise RuntimeError("active session marker is invalid") from None
        return {}
    if not isinstance(data, dict) or data.get("session_id") != expected_session_id:
        if strict:
            raise RuntimeError("active session marker identity is invalid")
        return {}
    return data


def resolve_active_task_dir(repo_root=None, session_id=None):
    """Resolve only this exact session's current, open, unfenced task."""
    repo_root = repo_root or find_repo_root()
    sid = current_session_id() if session_id is None else session_id
    return resolve_session_task_binding(repo_root, sid).get("task_dir", "")


def resolve_session_task_binding(repo_root, session_id, *, strict=False):
    """Resolve only an exact session marker and its current open run."""
    if not isinstance(session_id, str) or not session_id or session_id == "default":
        return {}
    if sanitize_session_id(session_id) != session_id:
        return {}
    try:
        marker = _read_session_marker(
            _session_active_path(repo_root, session_id), session_id, strict=strict,
        )
    except ValueError:
        if strict:
            raise RuntimeError("active session storage unavailable") from None
        return {}
    if _has_binding_conflict_fence(marker):
        return {}
    binding_fields = ("task_dir", "task_id", "run_id")
    if strict and any(key in marker for key in binding_fields) and any(
        not isinstance(marker.get(key), str) or not marker[key]
        for key in binding_fields
    ):
        raise RuntimeError("active session binding malformed")
    value = marker.get("task_dir")
    if not isinstance(value, str) or not value:
        return {}
    try:
        task_dir = canonical_task_dir(task_dir=value, repo_root=repo_root)
    except ValueError:
        if strict:
            raise RuntimeError("active task directory unavailable") from None
        return {}
    if not task_dir or marker.get("task_id") != os.path.basename(task_dir):
        if strict:
            raise RuntimeError("active session binding malformed")
        return {}
    control = read_task_control(task_dir)
    if strict and not control:
        raise RuntimeError("active task authority unavailable")
    if (
        not control
        or task_control_status(task_dir, control) != "open"
        or marker.get("run_id") != control.get("run_id")
    ):
        return {}
    return {"task_dir": task_dir, "run_id": control["run_id"]}


def iter_active_task_dirs(repo_root=None):
    """Yield unique current open task generations from exact session markers."""
    repo_root = repo_root or find_repo_root()
    seen = set()
    try:
        sessions = _active_sessions_dir(repo_root)
    except ValueError:
        return
    if os.path.isdir(sessions):
        for name in os.listdir(sessions):
            if not name.endswith(".json"):
                continue
            binding = resolve_session_task_binding(repo_root, name[:-5])
            td = binding.get("task_dir")
            if td and td not in seen:
                seen.add(td)
                yield td


def clear_active_marker(repo_root, task_dir=None, session_id=None, *, strict=False):
    """Clear only matching exact focus under the session lock, preserving fences."""
    if not _trusted_control_writer(marker=True):
        raise _control_writer_error("active task cleanup requires the task-control runtime", marker=True)
    sid = current_session_id() if session_id is None else session_id
    if not sid:
        return
    try:
        path = _session_active_path(repo_root, sid)
        marker = _read_session_marker(path, sid, strict=strict)
        if _has_binding_conflict_fence(marker):
            return
        if task_dir is not None and os.path.normpath(str(marker.get("task_dir") or "")) != os.path.normpath(task_dir):
            return
        os.unlink(path)
        if strict and os.path.lexists(path):
            raise RuntimeError("active session marker cleanup unavailable")
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        if strict:
            raise


# ── Scaffold ─────────────────────────────────────────────────────────────


def ensure_task_scaffold(
    task_dir, task_id, request_text="", repo_root=None, execution_mode="standard",
):
    """Create a new exact TASK.json; existing valid controls are resumed."""
    os.makedirs(task_dir, exist_ok=True)
    expected_tid = _normalize_task_id(task_id, task_dir=task_dir) or task_id
    path = task_control_file(task_dir)
    if os.path.lexists(path):
        if not read_task_control(task_dir):
            raise ValueError("existing TASK.json must be an exact safe task control")
        created = [path]
        return {"created": created, "task_dir": task_dir, "task_id": expected_tid}
    created = []
    try:
        write_task_control(task_dir, _new_task_control(execution_mode=execution_mode))
        created.append(path)
        if request_text:
            req_path = os.path.join(task_dir, "REQUEST.md")
            if not os.path.isfile(req_path) or os.path.islink(req_path):
                _atomic_text_write(req_path, request_text)
                created.append(req_path)
    except Exception:
        for artifact in created:
            try:
                os.unlink(artifact)
            except FileNotFoundError:
                pass
        raise
    return {"created": created, "task_dir": task_dir, "task_id": expected_tid}


# ── Manifest ─────────────────────────────────────────────────────────────


def read_manifest_field(field, repo_root=None):
    repo_root = repo_root or find_harness_root() or find_repo_root()
    return yaml_field(field, os.path.join(repo_root, MANIFEST_PATH))


def is_maintenance_task(task_dir, repo_root=None):
    if os.path.isfile(os.path.join(task_dir, "MAINTENANCE")):
        return True
    return str(read_manifest_field("maintenance_default", repo_root) or "").lower() == "true"


# ── Routing (on-the-fly, never stored) ───────────────────────────────────


def compile_routing(task_dir, repo_root=None):
    # A task in a linked worktree routes by that worktree's manifest, not the
    # process cwd (the MCP server's cwd is always the main checkout).
    repo_root = repo_root or find_harness_root(task_dir) or find_repo_root()
    maintenance = is_maintenance_task(task_dir, repo_root)
    control = read_task_control(task_dir)
    micro_loop = _is_micro_loop_state(control)
    from wbs import read_state, admission_definition
    try:
        state = read_state(task_dir)
        assignment = (state or {}).get('execution_admission', {})
        mode = (assignment.get('mode') if state and assignment.get('run_id') == control.get('run_id')
                and assignment.get('definition') == admission_definition(state, assignment.get('ownership'), assignment.get('selected'))
                else 'unassigned')
    except (OSError, ValueError, RuntimeError):
        mode = 'unassigned'
    return {
        "maintenance_task": maintenance,
        "workflow_locked": not maintenance,
        "risk_level": "high" if maintenance else "medium",
        "execution_mode": "micro" if micro_loop else "standard",
        "orchestration_mode": mode,
        "planning_mode": "skipped" if micro_loop else "standard",
    }


def _is_micro_loop_state(control):
    return (control or {}).get("execution_mode") == "micro"


def _atomic_json_write(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path) or ".", prefix=".json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_TASK_CONTROL_LOCK_HELD = ContextVar("harness_task_control_lock_held", default=())


def _validated_task_control_dir(task_dir):
    task_dir = os.path.abspath(os.fspath(task_dir))
    current = task_dir
    for _ in range(4):
        try:
            info = os.lstat(current)
        except OSError as exc:
            raise RuntimeError("task storage integrity unavailable") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("task storage integrity unavailable")
        current = os.path.dirname(current)
    task_info = os.lstat(task_dir)
    if task_info.st_uid != os.getuid() or stat.S_IMODE(task_info.st_mode) & 0o022:
        raise RuntimeError("task storage integrity unavailable")
    return task_dir


def _task_control_lock_binding(task_dir):
    task_dir = os.path.abspath(os.fspath(task_dir))
    for binding in reversed(_TASK_CONTROL_LOCK_HELD.get()):
        if binding[0] == task_dir:
            return binding
    return None


def _validate_task_control_dir_binding(binding):
    task_dir, fd, identity, components = binding
    for path, component_fd, component_identity in components:
        try:
            opened = os.fstat(component_fd)
            current = os.lstat(path)
        except OSError as exc:
            raise RuntimeError("task storage integrity unavailable") from exc
        if (
            not stat.S_ISDIR(opened.st_mode)
            or (opened.st_dev, opened.st_ino) != component_identity
            or stat.S_ISLNK(current.st_mode)
            or not stat.S_ISDIR(current.st_mode)
            or (current.st_dev, current.st_ino) != component_identity
        ):
            raise RuntimeError("task storage integrity unavailable")
    task_opened = os.fstat(fd)
    if (
        (task_opened.st_dev, task_opened.st_ino) != identity
        or task_opened.st_uid != os.getuid()
        or stat.S_IMODE(task_opened.st_mode) & 0o022
    ):
        raise RuntimeError("task storage integrity unavailable")


def _revalidate_task_control_transaction(task_dir):
    """Fail if a task directory changes during an enclosing task transaction."""
    binding = _task_control_lock_binding(task_dir)
    if binding is not None:
        _validate_task_control_dir_binding(binding)


def _task_control_dir_fd(task_dir):
    binding = _task_control_lock_binding(task_dir)
    if binding is None:
        raise RuntimeError("task transaction required")
    _validate_task_control_dir_binding(binding)
    return binding[1]


@contextmanager
def _task_control_lock(task_dir):
    task_dir = os.path.abspath(os.fspath(task_dir))
    nested = _task_control_lock_binding(task_dir)
    if nested is not None:
        _validate_task_control_dir_binding(nested)
        yield
        _validate_task_control_dir_binding(nested)
        return
    task_dir = _validated_task_control_dir(task_dir)
    component_paths = []
    current = task_dir
    for _ in range(4):
        component_paths.append(current)
        current = os.path.dirname(current)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    components = []
    try:
        for path in reversed(component_paths):
            component_fd = os.open(path, flags)
            # Admission can be interrupted by the registration hard deadline.
            # Own the descriptor before any subsequent metadata operation.
            components.append((path, component_fd, None))
            component_info = os.fstat(component_fd)
            current_info = os.lstat(path)
            if (
                not stat.S_ISDIR(component_info.st_mode)
                or stat.S_ISLNK(current_info.st_mode)
                or not stat.S_ISDIR(current_info.st_mode)
                or (component_info.st_dev, component_info.st_ino)
                != (current_info.st_dev, current_info.st_ino)
            ):
                raise RuntimeError("task storage integrity unavailable")
            components[-1] = (
                path, component_fd, (component_info.st_dev, component_info.st_ino),
            )
    except BaseException as exc:
        for _, component_fd, _ in components:
            os.close(component_fd)
        if isinstance(exc, OSError):
            raise RuntimeError("task storage integrity unavailable") from exc
        raise
    fd = components[-1][1]
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RuntimeError("task storage integrity unavailable")
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("task storage integrity unavailable") from exc
        binding = (
            task_dir, fd, (info.st_dev, info.st_ino), tuple(components),
        )
        _validate_task_control_dir_binding(binding)
        token = _TASK_CONTROL_LOCK_HELD.set(_TASK_CONTROL_LOCK_HELD.get() + (binding,))
        try:
            yield
            _validate_task_control_dir_binding(binding)
        finally:
            _TASK_CONTROL_LOCK_HELD.reset(token)
    finally:
        for _, component_fd, _ in reversed(components):
            os.close(component_fd)


@contextmanager
def task_control_transaction(task_dir):
    """Hold the task directory stable across state publication."""
    with _task_control_lock(task_dir):
        yield


@contextmanager
def task_control_transaction_fd(task_fd):
    """Hold a task transaction on an already-bound task directory fd."""
    locked_fd = os.dup(task_fd)
    task_dir = os.path.abspath(f"/proc/self/fd/{task_fd}")
    try:
        info = os.fstat(locked_fd)
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RuntimeError("task storage integrity unavailable")
        try:
            import fcntl
            fcntl.flock(locked_fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("task storage integrity unavailable") from exc
        binding = (
            task_dir, locked_fd, (info.st_dev, info.st_ino), (),
        )
        _validate_task_control_dir_binding(binding)
        token = _TASK_CONTROL_LOCK_HELD.set(_TASK_CONTROL_LOCK_HELD.get() + (binding,))
        try:
            yield
            _validate_task_control_dir_binding(binding)
        finally:
            _TASK_CONTROL_LOCK_HELD.reset(token)
    finally:
        os.close(locked_fd)


def _declared_lenses(task_dir, prefix, *, control=None):
    value = (control or read_task_control(task_dir)).get("required_lenses")
    if not isinstance(value, list):
        return []
    return [lens for lens in value if lens.startswith(prefix)]


def required_review_lenses(task_dir, state=None):
    """Return task-declared review lenses without inspecting source state."""
    return _declared_lenses(task_dir, "review-", control=state)


def _blocked_artifact_valid(task_dir):
    path = os.path.join(task_dir, "BLOCKED.md")
    try:
        info = os.lstat(path)
    except OSError:
        return False
    return bool(
        stat.S_ISREG(info.st_mode)
        and info.st_uid == os.getuid()
        and info.st_nlink == 1
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def task_control_status(task_dir, control=None):
    """Derive state from exact task control and its safe parking artifact."""
    control = control or read_task_control(task_dir)
    if not control:
        return "invalid"
    if os.path.lexists(os.path.join(task_dir, "BLOCKED.md")):
        if not _blocked_artifact_valid(task_dir) or control["closed"]:
            return "invalid"
        return "blocked"
    return "closed" if control["closed"] else "open"


def publish_task_close(task_dir, control):
    """Publish closure through the task-control owner."""
    if not _trusted_control_writer():
        raise _control_writer_error("task close publication requires the task-control MCP")
    updated = dict(control, closed=True)
    write_task_control(task_dir, updated)
    return updated


def _required_qa_lenses(task_dir, state=None):
    """Return workflow declarations, not machine-tracked role results."""
    return _declared_lenses(task_dir, "qa-", control=state)


def emit_compact_context(task_dir, *, measure_wbs=False, measured_progress=None, measure_targets=False, measured_target_inputs=None):
    """Report WBS check readiness; independent role results remain workflow-owned."""
    st = read_task_control(task_dir)
    if not st:
        return {"error": "missing or invalid exact four-field TASK.json", "task_dir": task_dir,
                "next_action": "Use wbs(action='migrate') preview/apply for old controls, or open a new task."}
    from wbs import task_progress, summary
    from verification_target import applicability
    repo = find_repo_root(task_dir)
    progress = measured_progress if measured_progress is not None else task_progress(
        task_dir, repo, st["run_id"], measure=measure_wbs)
    targets = applicability(task_dir, repo, measure=measure_wbs or measure_targets,
                            measured_inputs=measured_target_inputs)
    status = task_control_status(task_dir, st)
    routing = compile_routing(task_dir)
    has_plan = artifact_exists(task_dir, "PLAN.md")
    source_allowed = status == "open" and has_plan and routing["orchestration_mode"] == "direct"
    why = "" if source_allowed else "Create PLAN.md before source writes." if not has_plan else "Fresh execution assignment required: dispatch prepare with current host inventory and ownership"
    if status == "open" and has_plan and routing["orchestration_mode"] == "unassigned" and os.path.isfile(os.path.join(repo, ".git")):
        source_allowed = None
        why = "Status does not authenticate managed lead ownership; prewrite validates its existing pool binding."
    missing = []
    if not has_plan:
        missing.append("PLAN.md")
    complete = progress.get("status") == "measured" and bool(progress.get("outcomes")) and all(row["verified"] for row in progress["outcomes"])
    if not complete:
        missing.append("verified WBS outcomes")
    integration = progress.get("integration")
    if integration and integration["route"] != "integration":
        missing.append("integrated WBS work pack")
        source_allowed = False
        why = "WBS work pack owns source writes; complete isolated leads first."
    if targets.get("enforced") and targets.get("status") != "applicable":
        missing.append("applicable verification target inputs")
    if status == "blocked":
        missing.append("task is blocked")
    if status == "invalid":
        missing.append("valid task state")
    verdict = "BLOCKED_ENV" if status == "blocked" else "PASS" if not missing else "PENDING"
    next_action = "Run wbs(action='close') after awaited independent review and actual QA succeed." if not missing else "Run the declared WBS checks against current inputs; resolve the listed blockers."
    if not has_plan:
        next_action = "Create PLAN.md via wbs(action='plan') before source writes."
    elif status == "blocked":
        next_action = "Resolve the blocker, then resume this same task with wbs(action='open')."
    elif integration and integration["route"] != "integration":
        next_action = "Finish integration and disposal of this exact WBS work pack before resuming."
    context = dict(task_id=os.path.basename(task_dir), task_dir=task_dir, status=status,
                   routing=routing, runtime_verdict=verdict, verification_basis="current_wbs_checks",
                   workflow_verification="Independent review and QA results are awaited by the workflow and are not machine-tracked.",
                   progress=progress, verification_targets=targets,
                   source_write_allowed=source_allowed, why_source_write_blocked=why,
                   required_review_lenses=required_review_lenses(task_dir, st),
                   required_qa_lenses=_required_qa_lenses(task_dir, st),
                   missing_for_close=missing, next_action=next_action,
                   effective_close_gate="micro" if _is_micro_loop_state(st) else "standard")
    context["summary"] = summary(context)
    return context


# ── Explicit installer Git payload helper ──────────────────────────────
def _git_changed_paths(repo_root):
    """Return dirty paths for the explicit verified-install payload."""
    if not os.path.lexists(os.path.join(repo_root, ".git")):
        return set()
    base = ["git", "-c", f"safe.directory={repo_root}"]
    commands = (
        base + ["diff", "--name-only", "-z", "HEAD"],
        base + ["diff", "--cached", "--name-only", "-z", "HEAD"],
        base + ["ls-files", "--others", "--exclude-standard", "-z"],
    )
    changed = set()
    for command in commands:
        try:
            result = subprocess.run(
                command,
                cwd=repo_root,
                capture_output=True,
                timeout=5,
                env=_trusted_git_env(),
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError(
                f"Git changed-path query unavailable in {repo_root}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"Git changed-path query failed in {repo_root}"
            )
        output = result.stdout if isinstance(result.stdout, bytes) else os.fsencode(result.stdout or "")
        changed.update(os.fsdecode(path) for path in output.split(b"\0") if path)
    return changed


# ── Artifact helpers ─────────────────────────────────────────────────────


def artifact_exists(task_dir, filename):
    return os.path.isfile(os.path.join(task_dir, filename))


# ── Atomic JSON state helpers ─────────────────────────────────────────────


def read_json_state(path: str):
    """Read JSON state file. Returns None on missing/corrupt file."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return _json.load(f)
    except Exception:
        return None


def write_json_state(path: str, data) -> bool:
    """Atomically write JSON state file. Returns True on success."""
    d = os.path.dirname(path) or "."
    os.makedirs(d, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=d, prefix=".json.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            _json.dump(data, f, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return True
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return False
