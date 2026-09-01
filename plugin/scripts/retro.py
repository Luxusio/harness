#!/usr/bin/env python3
"""Weekly engineering retrospective from durable harness state.

Combines git log, verified task closures, and learnings.jsonl over a configurable
period (default 7 days) into a structured report.

Sections:
  1. Commits — count, authors, top changed files
  2. Tasks — receipt-backed task closures published in the period
  3. Learnings — new entries by type, key highlights
  4. Patterns — what repeated, what improved, what regressed

Output: stdout (markdown). Optionally append to doc/harness/retros/<date>.md.

Invocation:
  python3 retro.py                          # last 7 days, stdout
  python3 retro.py --days 14                # last 14 days
  python3 retro.py --save                   # also write to doc/harness/retros/
  python3 retro.py --count-closed-since 0   # verified close count only

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    canonical_task_dir,
    find_repo_root,
    read_task_control,
    receipt_stream_transaction,
    receipt_stream_transaction_fd,
    task_control_status,
)

LEARNINGS = "doc/harness/learnings.jsonl"
TASKS = "doc/harness/tasks"
RETROS = "doc/harness/retros"
RETRO_CLOSE_THRESHOLD = 3
MAX_LEARNINGS_BYTES = 16 * 1024 * 1024


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _cutoff(days: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(args: list[str], cwd: str) -> str:
    try:
        r = subprocess.run(
            ["git", *args], capture_output=True, text=True, cwd=cwd, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except (subprocess.SubprocessError, OSError):
        return ""


def _trusted_directory(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid in {os.getuid(), 0}
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


def _open_bound_chain(repo_root: str, *, include_retros: bool = False) -> list[int]:
    """Open repo/doc/harness[/retros] without following directory symlinks."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    fds: list[int] = []
    try:
        root_fd = os.open(repo_root, flags)
        fds.append(root_fd)
        if not _trusted_directory(os.fstat(root_fd)):
            raise RuntimeError("unsafe retro repository root")
        for name in ("doc", "harness"):
            child = os.open(name, flags, dir_fd=fds[-1])
            fds.append(child)
            if not _trusted_directory(os.fstat(child)):
                raise RuntimeError(f"unsafe retro directory: {name}")
        if include_retros:
            try:
                child = os.open("retros", flags, dir_fd=fds[-1])
            except FileNotFoundError:
                os.mkdir("retros", 0o755, dir_fd=fds[-1])
                child = os.open("retros", flags, dir_fd=fds[-1])
            fds.append(child)
            if not _trusted_directory(os.fstat(child)):
                raise RuntimeError("unsafe retro directory: retros")
        if not _bound_chain_matches(repo_root, fds):
            raise RuntimeError("retro directory identity mismatch")
        return fds
    except BaseException:
        for fd in reversed(fds):
            os.close(fd)
        raise


def _bound_chain_matches(repo_root: str, fds: list[int]) -> bool:
    try:
        root = os.lstat(repo_root)
        if stat.S_ISLNK(root.st_mode) or _control_identity(root) != _control_identity(os.fstat(fds[0])):
            return False
        for index, name in enumerate(("doc", "harness", "retros")[:len(fds) - 1]):
            current = os.stat(name, dir_fd=fds[index], follow_symlinks=False)
            opened = os.fstat(fds[index + 1])
            if stat.S_ISLNK(current.st_mode) or _control_identity(current) != _control_identity(opened):
                return False
        return all(_trusted_directory(os.fstat(fd)) for fd in fds)
    except OSError:
        return False


def _safe_label(value: object, maximum: int = 120) -> str | None:
    if not isinstance(value, str) or not value or len(value) > maximum:
        return None
    if not all(char.isascii() and (char.isalnum() or char in "._-") for char in value):
        return None
    return value


def _load_jsonl_since(repo_root: str, since: str) -> list[dict]:
    """Read safe learning metadata from a bound, owner-controlled ledger."""
    try:
        fds = _open_bound_chain(repo_root)
    except (OSError, RuntimeError):
        return []
    try:
        harness_fd = fds[-1]
        try:
            before = os.stat("learnings.jsonl", dir_fd=harness_fd, follow_symlinks=False)
        except FileNotFoundError:
            return []
        if (
            stat.S_ISLNK(before.st_mode)
            or not stat.S_ISREG(before.st_mode)
            or before.st_uid not in {os.getuid(), 0}
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o022
            or before.st_size > MAX_LEARNINGS_BYTES
        ):
            return []
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open("learnings.jsonl", flags, dir_fd=harness_fd)
        except OSError:
            return []
        try:
            opened = os.fstat(fd)
            if _control_identity(opened) != _control_identity(before):
                return []
            data = os.read(fd, MAX_LEARNINGS_BYTES + 1)
            if len(data) > MAX_LEARNINGS_BYTES or os.read(fd, 1):
                return []
            after = os.fstat(fd)
            if _control_identity(after) != _control_identity(opened):
                return []
        finally:
            os.close(fd)
        current = os.stat("learnings.jsonl", dir_fd=harness_fd, follow_symlinks=False)
        if _control_identity(current) != _control_identity(before) or not _bound_chain_matches(repo_root, fds):
            return []
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            return []
    except OSError:
        return []
    finally:
        for bound_fd in reversed(fds):
            os.close(bound_fd)

    results = []
    for line in text.splitlines():
        try:
            entry = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(entry, dict):
            continue
        ts = entry.get("ts")
        kind = _safe_label(entry.get("type"))
        key = _safe_label(entry.get("key"))
        if not isinstance(ts, str) or len(ts) != 20 or ts < since or kind is None:
            continue
        try:
            datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ")
        except ValueError:
            continue
        results.append({"ts": ts, "type": kind, "key": key or ""})
    return results


def _section_commits(repo_root: str, days: int) -> str:
    since = f"{days} days ago"
    log = _git(["log", f"--since={since}", "--oneline", "--no-merges"], repo_root)
    lines = [ln for ln in log.splitlines() if ln.strip()] if log else []
    if not lines:
        return "## Commits\n\n(none in this period)\n"

    authors_raw = _git(
        ["log", f"--since={since}", "--format=%aN", "--no-merges"], repo_root
    )
    authors = Counter(
        author for raw in authors_raw.splitlines()
        if (author := _safe_git_author(raw.strip())) is not None
    )

    files_raw = _git(
        ["log", f"--since={since}", "--name-only", "--format=", "--no-merges"], repo_root
    )
    files = Counter(
        path for raw in files_raw.splitlines()
        if (path := _safe_git_path(raw.strip())) is not None
    )
    top_files = files.most_common(5)

    parts = [f"## Commits\n\n- **{len(lines)}** commits"]
    if authors:
        parts.append(f"- Authors: {', '.join(f'{a} ({c})' for a, c in authors.most_common(5))}")
    if top_files:
        parts.append("- Most changed files:")
        for fp, cnt in top_files:
            parts.append(f"  - `{fp}` ({cnt})")
    return "\n".join(parts) + "\n"


def _safe_git_author(value: str) -> str | None:
    if not value or len(value) > 120:
        return None
    allowed = " ._@+-"
    return value if all(
        char.isascii() and (char.isalnum() or char in allowed) for char in value
    ) else None


def _safe_git_path(value: str) -> str | None:
    if not value or len(value) > 512:
        return None
    allowed = "/._@+-"
    return value if all(
        char.isascii() and (char.isalnum() or char in allowed) for char in value
    ) else None


def _control_identity(info: os.stat_result) -> tuple[int, ...]:
    return (
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mode,
        info.st_uid,
        info.st_nlink,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _verified_close_publication_fd(task_fd: int) -> float | None:
    """Verify one close generation relative to its bound task directory."""
    task_dir = f"/proc/self/fd/{task_fd}"
    try:
        with receipt_stream_transaction_fd(task_fd):
            before = os.stat("TASK.json", dir_fd=task_fd, follow_symlinks=False)
            if (
                not stat.S_ISREG(before.st_mode)
                or before.st_uid != os.getuid()
                or before.st_nlink != 1
                or stat.S_IMODE(before.st_mode) & 0o022
            ):
                return None

            control = read_task_control(task_dir)
            if not control or task_control_status(task_dir, control) != "closed":
                return None

            after = os.stat("TASK.json", dir_fd=task_fd, follow_symlinks=False)
            if _control_identity(before) != _control_identity(after):
                return None
            return before.st_mtime
    except (OSError, RuntimeError):
        return None


def _verified_close_publication(task_dir: str) -> float | None:
    """Path-compatible wrapper that binds the task leaf before verification."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        task_fd = os.open(task_dir, flags)
    except OSError:
        return None
    try:
        opened = os.fstat(task_fd)
        current = os.lstat(task_dir)
        if (
            not _trusted_directory(opened)
            or stat.S_ISLNK(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            return None
        return _verified_close_publication_fd(task_fd)
    finally:
        os.close(task_fd)


def _safe_tasks_root(root: str) -> tuple[int, tuple[int, ...]] | None:
    """Open and bind an owner-controlled, non-symlinked tasks directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = -1
    try:
        before = os.lstat(root)
        fd = os.open(root, flags)
        opened = os.fstat(fd)
    except OSError:
        if fd >= 0:
            os.close(fd)
        return None
    identity = _control_identity(opened)
    if (
        not stat.S_ISDIR(before.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or before.st_uid != os.getuid()
        or opened.st_uid != os.getuid()
        or stat.S_IMODE(before.st_mode) & 0o022
        or stat.S_IMODE(opened.st_mode) & 0o022
        or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
    ):
        os.close(fd)
        return None
    return fd, identity


def _tasks_root_still_bound(root: str, fd: int, identity: tuple[int, ...]) -> bool:
    try:
        current = os.lstat(root)
        opened = os.fstat(fd)
    except OSError:
        return False
    return (
        not stat.S_ISLNK(current.st_mode)
        and _control_identity(current) == identity
        and _control_identity(opened) == identity
    )


def _canonical_bound_task_dir(
    repo_root: str, root: str, root_fd: int, name: str,
) -> int | None:
    """Resolve a canonical task ID and bind it to the enumerated directory entry."""
    task_fd = -1
    try:
        canonical = canonical_task_dir(task_id=name, repo_root=repo_root)
        expected = os.path.join(root, name)
        entry_info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
        path_info = os.lstat(canonical)
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        task_fd = os.open(name, flags, dir_fd=root_fd)
        opened = os.fstat(task_fd)
    except (OSError, ValueError):
        if task_fd >= 0:
            os.close(task_fd)
        return None
    if (
        canonical != expected
        or not stat.S_ISDIR(entry_info.st_mode)
        or _control_identity(entry_info) != _control_identity(path_info)
        or _control_identity(entry_info) != _control_identity(opened)
        or not _trusted_directory(opened)
    ):
        os.close(task_fd)
        return None
    return task_fd


def _markdown_task_name(name: str) -> str:
    """Render only the canonical task-ID alphabet inside a Markdown code span."""
    safe = "".join(
        char for char in name
        if char.isascii() and (char.isalnum() or char in "._-")
    )
    return safe if safe == name else "[invalid-task]"


def verified_closed_tasks_since(
    repo_root: str, cutoff_ts: float,
) -> list[tuple[float, str]]:
    """List receipt-verified task closures published after ``cutoff_ts``."""
    root = os.path.join(repo_root, TASKS)
    opened_root = _safe_tasks_root(root)
    if opened_root is None:
        return []
    root_fd, root_identity = opened_root
    tasks: list[tuple[float, str]] = []
    try:
        entries = os.scandir(root_fd)
    except OSError:
        os.close(root_fd)
        return []
    try:
        with entries:
            for entry in entries:
                if not entry.name.startswith("TASK__"):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                task_fd = _canonical_bound_task_dir(
                    repo_root, root, root_fd, entry.name
                )
                if task_fd is None:
                    continue
                try:
                    published = _verified_close_publication_fd(task_fd)
                finally:
                    os.close(task_fd)
                if published is not None and published > cutoff_ts:
                    tasks.append((published, entry.name))
        if not _tasks_root_still_bound(root, root_fd, root_identity):
            return []
    finally:
        os.close(root_fd)
    tasks.sort()
    return tasks


def count_verified_closes_since(repo_root: str, cutoff_ts: float) -> int:
    """Count verified task closes for the shared three-close retro cadence."""
    return len(verified_closed_tasks_since(repo_root, cutoff_ts))


def _section_tasks(repo_root: str, days: int) -> str:
    cutoff_ts = datetime.now(timezone.utc).timestamp() - (days * 86400)
    tasks = verified_closed_tasks_since(repo_root, cutoff_ts)
    if not tasks:
        return "## Tasks\n\n(no verified task closures in this period)\n"

    parts = [f"## Tasks\n\n- **{len(tasks)}** verified task closures"]
    for _, name in tasks[-5:]:
        parts.append(f"  - `{_markdown_task_name(name)}`")
    return "\n".join(parts) + "\n"


def _section_learnings(entries: list[dict]) -> str:
    if not entries:
        return "## Learnings\n\n(none in this period)\n"

    by_type = Counter(e.get("type", "unknown") for e in entries)
    parts = [f"## Learnings\n\n- **{len(entries)}** new entries"]
    parts.append(f"- By type: {', '.join(f'{t} ({c})' for t, c in by_type.most_common())}")

    key_counts = Counter(e.get("key", "") for e in entries if e.get("key"))
    repeated = [(k, c) for k, c in key_counts.most_common(5) if c >= 2]
    if repeated:
        parts.append("- Repeated keys (promotion candidates):")
        for k, c in repeated:
            parts.append(f"  - `{k}` ({c}x)")

    return "\n".join(parts) + "\n"


def generate(repo_root: str, days: int) -> str:
    since = _cutoff(days)
    learnings = _load_jsonl_since(repo_root, since)

    header = f"# Retro — {datetime.now(timezone.utc).strftime('%Y-%m-%d')} (last {days} days)\n"
    sections = [
        header,
        _section_commits(repo_root, days),
        _section_tasks(repo_root, days),
        _section_learnings(learnings),
    ]
    return "\n".join(sections)


def _write_all(fd: int, payload: bytes) -> None:
    offset = 0
    while offset < len(payload):
        written = os.write(fd, payload[offset:])
        if written <= 0:
            raise RuntimeError("retro report write was incomplete")
        offset += written


def _save_report(repo_root: str, report: str, date_str: str) -> str:
    """Atomically publish a report beneath a bound, no-follow retros directory."""
    filename = f"{date_str}.md"
    if datetime.strptime(date_str, "%Y-%m-%d").strftime("%Y-%m-%d") != date_str:
        raise ValueError("invalid retro date")
    fds = _open_bound_chain(repo_root, include_retros=True)
    temp_name = f".{date_str}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temp_created = False
    try:
        retros_fd = fds[-1]
        try:
            existing = os.stat(filename, dir_fd=retros_fd, follow_symlinks=False)
        except FileNotFoundError:
            existing = None
        if existing is not None and (
            stat.S_ISLNK(existing.st_mode)
            or not stat.S_ISREG(existing.st_mode)
            or existing.st_uid not in {os.getuid(), 0}
            or existing.st_nlink != 1
            or stat.S_IMODE(existing.st_mode) & 0o022
        ):
            raise RuntimeError("unsafe existing retro report")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW
        temp_fd = os.open(temp_name, flags, 0o600, dir_fd=retros_fd)
        temp_created = True
        try:
            _write_all(temp_fd, report.encode("utf-8"))
            os.fchmod(temp_fd, 0o644)
            os.fsync(temp_fd)
        finally:
            os.close(temp_fd)
        if not _bound_chain_matches(repo_root, fds):
            raise RuntimeError("retro directory identity changed before publication")
        os.replace(temp_name, filename, src_dir_fd=retros_fd, dst_dir_fd=retros_fd)
        temp_created = False
        os.fsync(retros_fd)
        if not _bound_chain_matches(repo_root, fds):
            raise RuntimeError("retro directory identity changed after publication")
    finally:
        if temp_created:
            try:
                os.unlink(temp_name, dir_fd=fds[-1])
            except OSError:
                pass
        for fd in reversed(fds):
            os.close(fd)
    return os.path.join(repo_root, RETROS, filename)


def main() -> int:
    p = argparse.ArgumentParser(description="Weekly engineering retrospective")
    p.add_argument("--days", type=int, default=7)
    p.add_argument("--save", action="store_true", help="Also write to doc/harness/retros/")
    p.add_argument(
        "--count-closed-since",
        type=float,
        metavar="EPOCH",
        help="Print verified closes newer than this Unix timestamp and exit",
    )
    args = p.parse_args()

    repo_root = find_repo_root()
    if args.count_closed_since is not None:
        print(count_verified_closes_since(repo_root, args.count_closed_since))
        return 0
    report = generate(repo_root, args.days)
    print(report)

    if args.save:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = _save_report(repo_root, report, date_str)
        print(f"\nsaved: {path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
