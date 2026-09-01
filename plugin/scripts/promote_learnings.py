#!/usr/bin/env python3
"""Validate Tier 3 learnings and report reviewed Tier 2 candidates.

Extracts the inline bash from self-improvement.md into a proper script.
Steps:
  1. Aggregate learnings.jsonl by key, count occurrences.
  2. Report keys with count >= threshold as candidates for a reviewed task.
  3. Leave learnings.jsonl and Tier 2 patterns unchanged.

Invocation:
  python3 promote_learnings.py                       # manual full pipeline
  python3 promote_learnings.py --dry-run             # report what would happen
  python3 promote_learnings.py --threshold 3         # require 3+ occurrences
  python3 promote_learnings.py --task TASK__x --task-run-id RUN_ID
                                                   # current-run automatic mode

Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
import sys
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (
    canonical_task_dir,
    find_repo_root,
    read_task_control,
    receipt_stream_transaction_fd,
    task_control_status,
)

LEARNINGS = "doc/harness/learnings.jsonl"
DEFAULT_THRESHOLD = 2
PROMOTABLE_TYPES = {
    "eureka",
    "feedback",
    "feedback-rule",
    "harness-improvement",
    "operational",
    "pitfall",
}
FEEDBACK_RULE_TYPES = {"feedback-rule"}
MAX_KEY_LENGTH = 120
MAX_INSIGHT_LENGTH = 4000
MAX_CONTEXT_LENGTH = 200
MAX_LEARNINGS_BYTES = 16 * 1024 * 1024
PROVENANCE_PREFIX = "**Promoted from learnings:**"

def _bounded_text(value: object, maximum: int, *, single_line: bool = False) -> bool:
    if not isinstance(value, str) or value != value.strip() or not value:
        return False
    if len(value) > maximum or not any(char.isalnum() for char in value):
        return False
    if single_line and ("\n" in value or "\r" in value):
        return False
    return not any(ord(char) < 32 and char not in "\n\r\t" for char in value)


def _atx_h2_name(line: str) -> str | None:
    """Return an ATX H2 payload after 0-3 spaces, or None for non-H2/code."""
    indent = 0
    while indent < len(line) and line[indent] == " ":
        indent += 1
    if indent > 3:
        return None
    candidate = line[indent:]
    if not candidate.startswith("##"):
        return None
    if len(candidate) == 2:
        return ""
    if candidate[2] not in " \t":
        return None
    payload = candidate[3:].strip(" \t")
    closing_start = len(payload)
    while closing_start > 0 and payload[closing_start - 1] == "#":
        closing_start -= 1
    if closing_start < len(payload) and (
        closing_start == 0 or payload[closing_start - 1] in " \t"
    ):
        payload = payload[:closing_start].rstrip(" \t")
    return payload


def _setext_h2_delimiter(line: str) -> bool:
    indent = 0
    while indent < len(line) and line[indent] == " ":
        indent += 1
    if indent > 3:
        return False
    candidate = line[indent:].rstrip(" \t")
    return bool(candidate) and all(char == "-" for char in candidate)


def _fence_delimiter(line: str) -> tuple[str, int, str] | None:
    indent = 0
    while indent < len(line) and line[indent] == " ":
        indent += 1
    if indent > 3 or indent >= len(line) or line[indent] not in "`~":
        return None
    marker = line[indent]
    end = indent
    while end < len(line) and line[end] == marker:
        end += 1
    if end - indent < 3:
        return None
    return marker, end - indent, line[end:]


def _has_unmatched_fence(lines: list[str]) -> bool:
    opened: tuple[str, int] | None = None
    for line in lines:
        delimiter = _fence_delimiter(line)
        if delimiter is None:
            continue
        marker, length, remainder = delimiter
        if opened is None:
            opened = (marker, length)
        elif marker == opened[0] and length >= opened[1] and not remainder.strip(" \t"):
            opened = None
    return opened is not None


def _raw_html_block_opener(line: str) -> bool:
    indent = 0
    while indent < len(line) and line[indent] == " ":
        indent += 1
    if indent > 3:
        return False
    return line[indent:].startswith("<")


def _safe_pattern_text(value: object, maximum: int) -> bool:
    if not _bounded_text(value, maximum):
        return False
    lines = value.splitlines()
    if _has_unmatched_fence(lines):
        return False
    for index, line in enumerate(lines):
        if _raw_html_block_opener(line):
            return False
        if _atx_h2_name(line) is not None:
            return False
        markdown = line.lstrip(" \t")
        if markdown.startswith(PROVENANCE_PREFIX):
            return False
        if index > 0 and lines[index - 1].strip() and _setext_h2_delimiter(line):
            return False
    return True


def _safe_pattern_key(value: object) -> bool:
    return bool(
        _bounded_text(value, MAX_KEY_LENGTH, single_line=True)
        and isinstance(value, str)
        and value[0].isalnum()
        and all(char.isascii() and (char.isalnum() or char in "._-") for char in value)
    )


def _canonical_timestamp(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError:
        return False
    return (
        parsed.strftime("%Y-%m-%dT%H:%M:%SZ") == value
        and parsed <= datetime.now(timezone.utc) + timedelta(minutes=5)
    )


def _trusted_directory(info: os.stat_result) -> bool:
    return bool(
        stat.S_ISDIR(info.st_mode)
        and info.st_uid in {os.getuid(), 0}
        and not stat.S_IMODE(info.st_mode) & 0o022
    )


class _SafeRepoStorage:
    """Descriptor-bound access to promotion state beneath a verified repo."""

    def __init__(self, repo_root: str):
        self.repo_root = os.path.abspath(repo_root)
        self._bindings: list[tuple[str, int, tuple[int, int]]] = []
        self.repo_fd = -1
        self.doc_fd = -1
        self.harness_fd = -1

    def __enter__(self):
        try:
            self.repo_fd = self._open_root(self.repo_root)
            self.doc_fd = self._open_child_dir(
                self.repo_fd, "doc", os.path.join(self.repo_root, "doc")
            )
            self.harness_fd = self._open_child_dir(
                self.doc_fd, "harness", os.path.join(self.repo_root, "doc", "harness")
            )
            return self
        except BaseException:
            for _, fd, _ in reversed(self._bindings):
                os.close(fd)
            self._bindings.clear()
            raise

    def __exit__(self, exc_type, exc, traceback):
        validation_error = None
        if exc_type is None:
            try:
                self._validate_bindings()
            except RuntimeError as error:
                validation_error = error
        for _, fd, _ in reversed(self._bindings):
            try:
                os.close(fd)
            except OSError:
                pass
        self._bindings.clear()
        if validation_error is not None:
            raise validation_error

    def _bind(self, path: str, fd: int) -> int:
        opened = os.fstat(fd)
        if not _trusted_directory(opened):
            os.close(fd)
            raise RuntimeError(f"unsafe promotion directory: {path}")
        try:
            current = os.lstat(path)
        except OSError as exc:
            os.close(fd)
            raise RuntimeError(f"promotion directory unavailable: {path}") from exc
        if (
            stat.S_ISLNK(current.st_mode)
            or (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            os.close(fd)
            raise RuntimeError(f"promotion directory identity mismatch: {path}")
        self._bindings.append((path, fd, (opened.st_dev, opened.st_ino)))
        return fd

    def _open_root(self, path: str) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(path, flags)
        except OSError as exc:
            raise RuntimeError("promotion repository root is unsafe") from exc
        return self._bind(path, fd)

    def _open_child_dir(self, parent_fd: int, name: str, path: str) -> int:
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise RuntimeError(f"unsafe promotion directory: {path}") from exc
        return self._bind(path, fd)

    def _validate_bindings(self) -> None:
        for path, fd, identity in self._bindings:
            try:
                opened = os.fstat(fd)
                current = os.lstat(path)
            except OSError as exc:
                raise RuntimeError("promotion storage identity changed") from exc
            if (
                not _trusted_directory(opened)
                or stat.S_ISLNK(current.st_mode)
                or (opened.st_dev, opened.st_ino) != identity
                or (current.st_dev, current.st_ino) != identity
            ):
                raise RuntimeError("promotion storage identity changed")

    @contextmanager
    def promotion_lock(self):
        self._validate_bindings()
        try:
            import fcntl
            fcntl.flock(self.harness_fd, fcntl.LOCK_EX)
        except (ImportError, OSError) as exc:
            raise RuntimeError("promotion lock unavailable") from exc
        try:
            self._validate_bindings()
            yield
            self._validate_bindings()
        finally:
            try:
                fcntl.flock(self.harness_fd, fcntl.LOCK_UN)
            except OSError:
                pass

    def _leaf_info(self, parent_fd: int, name: str) -> os.stat_result | None:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise RuntimeError(f"promotion file unavailable: {name}") from exc
        if (
            stat.S_ISLNK(info.st_mode)
            or not stat.S_ISREG(info.st_mode)
            or info.st_uid not in {os.getuid(), 0}
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) & 0o022
        ):
            raise RuntimeError(f"unsafe promotion file: {name}")
        return info

    def _read_leaf_text(self, parent_fd: int, name: str, *, max_size: int) -> str | None:
        self._validate_bindings()
        before = self._leaf_info(parent_fd, name)
        if before is None:
            return None
        if before.st_size > max_size:
            raise RuntimeError(f"promotion file exceeds size limit: {name}")
        flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
        try:
            fd = os.open(name, flags, dir_fd=parent_fd)
        except OSError as exc:
            raise RuntimeError(f"promotion file unavailable: {name}") from exc
        try:
            opened = os.fstat(fd)
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                raise RuntimeError(f"promotion file identity changed: {name}")
            chunks = []
            total = 0
            while True:
                chunk = os.read(fd, min(64 * 1024, max_size + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_size:
                    raise RuntimeError(f"promotion file exceeds size limit: {name}")
            after = os.fstat(fd)
            if (
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
            ):
                raise RuntimeError(f"promotion file changed during read: {name}")
            text = b"".join(chunks).decode("utf-8")
        except UnicodeError as exc:
            raise RuntimeError(f"promotion file is not UTF-8: {name}") from exc
        finally:
            os.close(fd)
        current = self._leaf_info(parent_fd, name)
        if current is None or (current.st_dev, current.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"promotion file identity changed: {name}")
        self._validate_bindings()
        return text

    def read_learnings(self) -> str | None:
        return self._read_leaf_text(
            self.harness_fd, os.path.basename(LEARNINGS), max_size=MAX_LEARNINGS_BYTES
        )

    @contextmanager
    def task_binding(self, task: str):
        initial_bindings = len(self._bindings)
        try:
            task_dir = canonical_task_dir(task_id=task, repo_root=self.repo_root)
            tasks_path = os.path.join(self.repo_root, "doc", "harness", "tasks")
            tasks_fd = self._open_child_dir(self.harness_fd, "tasks", tasks_path)
            self._open_child_dir(tasks_fd, task, task_dir)
            self._validate_bindings()
            yield task_dir, self._bindings[-1][1]
            self._validate_bindings()
        finally:
            for _, fd, _ in reversed(self._bindings[initial_bindings:]):
                os.close(fd)
            del self._bindings[initial_bindings:]


def _valid_learning_candidate(
    entry: object,
    *,
    task: str | None = None,
    task_run_id: str | None = None,
) -> bool:
    """Validate a promotable knowledge row, optionally against close context."""
    if not isinstance(entry, dict) or entry.get("type") not in PROMOTABLE_TYPES:
        return False
    if not _canonical_timestamp(entry.get("ts")):
        return False
    if not _safe_pattern_key(entry.get("key")):
        return False
    if not _safe_pattern_text(entry.get("insight"), MAX_INSIGHT_LENGTH):
        return False
    if not _bounded_text(entry.get("task"), MAX_CONTEXT_LENGTH, single_line=True):
        return False
    if not _bounded_text(entry.get("task_run_id"), MAX_CONTEXT_LENGTH, single_line=True):
        return False
    if entry.get("type") in FEEDBACK_RULE_TYPES:
        for field in ("trigger", "action", "verification"):
            if not _safe_pattern_text(entry.get(field), MAX_INSIGHT_LENGTH):
                return False
        reason = entry.get("reason")
        if reason is not None and not _safe_pattern_text(reason, MAX_INSIGHT_LENGTH):
            return False
    if task is not None and entry.get("task") != task:
        return False
    if task_run_id is not None and entry.get("task_run_id") != task_run_id:
        return False
    return True


def _load_entries(path: str) -> list[dict]:
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return []
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
        raise RuntimeError(f"unsafe learnings file: {path}")
    flags = os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | getattr(os, "O_NONBLOCK", 0)
    fd = os.open(path, flags)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise RuntimeError(f"learnings file identity changed: {path}")
        data = os.read(fd, MAX_LEARNINGS_BYTES + 1)
        if len(data) > MAX_LEARNINGS_BYTES or os.read(fd, 1):
            raise RuntimeError(f"learnings file exceeds size limit: {path}")
        text = data.decode("utf-8")
    finally:
        os.close(fd)
    return _parse_entries(text)


def _parse_entries(text: str | None) -> list[dict]:
    entries = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(entry, dict):
            entries.append(entry)
    return entries


def _repo_relative_regular_file(root_fd: int, rel: str) -> bool | None:
    """Return file existence without following any path component symlink."""
    parts = rel.split(os.sep)
    current_fd = os.dup(root_fd)
    try:
        for component in parts[:-1]:
            try:
                info = os.stat(component, dir_fd=current_fd, follow_symlinks=False)
            except FileNotFoundError:
                return False
            except OSError:
                return None
            if stat.S_ISLNK(info.st_mode) or not _trusted_directory(info):
                return None
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
            try:
                next_fd = os.open(component, flags, dir_fd=current_fd)
            except FileNotFoundError:
                return False
            except OSError:
                return None
            os.close(current_fd)
            current_fd = next_fd
        try:
            leaf = os.stat(parts[-1], dir_fd=current_fd, follow_symlinks=False)
        except FileNotFoundError:
            return False
        except OSError:
            return None
        if stat.S_ISLNK(leaf.st_mode):
            return None
        return stat.S_ISREG(leaf.st_mode)
    finally:
        os.close(current_fd)


def _audit_stale_files(entries: list[dict], repo_root: str) -> int:
    """Audit entries whose files[] contain paths that no longer exist.

    Warn-only (stderr). Never mutates learnings.jsonl.
    Deduplicates safe repository-relative paths and emits aggregate metadata.
    Returns count of unique missing paths flagged.
    """
    seen_paths: set[str] = set()
    warned = 0
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
    try:
        root_fd = os.open(repo_root, flags)
    except OSError:
        return 0
    try:
        if not _trusted_directory(os.fstat(root_fd)):
            return 0
        for e in entries:
            files = e.get("files") or []
            if isinstance(files, str):
                files = [files]
            elif not isinstance(files, list):
                continue
            for rel in files:
                if (
                    not isinstance(rel, str)
                    or not rel
                    or len(rel) > 4096
                    or os.path.isabs(rel)
                    or rel != os.path.normpath(rel)
                    or rel in {".", ".."}
                    or rel.startswith(".." + os.sep)
                    or any(
                        ord(char) < 32
                        or ord(char) == 127
                        or 0xD800 <= ord(char) <= 0xDFFF
                        for char in rel
                    )
                ):
                    continue
                if rel in seen_paths:
                    continue
                exists = _repo_relative_regular_file(root_fd, rel)
                if exists is False:
                    seen_paths.add(rel)
                    warned += 1
    finally:
        os.close(root_fd)
    if warned:
        print(
            f"[hygiene] classification=stale-file count={warned}",
            file=sys.stderr,
        )
    return warned


def _audit_contradictions(entries: list[dict]) -> int:
    """Audit entries with the same key that may contradict each other.

    Filter: pairs with ts difference < 30 days OR same source.
    Warn-only (stderr). Never mutates learnings.jsonl.
    Emits at most ONE warning per key (latest vs earliest qualifying prior).
    Returns count of keys flagged.
    """
    from collections import defaultdict
    by_key: dict[str, list[dict]] = defaultdict(list)
    for e in entries:
        k = e.get("key", "")
        if _safe_pattern_key(k) and _canonical_timestamp(e.get("ts")):
            by_key[k].append(e)

    cutoff_days = 30
    warned = 0
    for key, group in by_key.items():
        if len(group) < 2:
            continue
        # Sort by ts ascending
        def _ts_sort(e):
            return e.get("ts") or ""
        group_sorted = sorted(group, key=_ts_sort)
        latest = group_sorted[-1]
        # Find the first qualifying prior (one warning per key max)
        for prior in group_sorted[:-1]:
            same_source = (prior.get("source") or "") == (latest.get("source") or "") and (prior.get("source") or "") != ""
            recent = False
            try:
                from datetime import datetime, timezone
                fmt = "%Y-%m-%dT%H:%M:%SZ"
                t_prior = datetime.strptime(prior.get("ts", ""), fmt).replace(tzinfo=timezone.utc)
                t_latest = datetime.strptime(latest.get("ts", ""), fmt).replace(tzinfo=timezone.utc)
                if abs((t_latest - t_prior).days) < cutoff_days:
                    recent = True
            except Exception:
                pass
            if recent or same_source:
                reason = "recent-window" if recent else "same-source"
                safe_key = key if _safe_pattern_key(key) else "<invalid>"
                print(
                    f"[hygiene] contradiction: key={safe_key!r} "
                    f"classification={reason}",
                    file=sys.stderr,
                )
                warned += 1
                break  # one warning per key
    return warned


def _verified_closed_run(storage: _SafeRepoStorage, task: str, task_run_id: str) -> bool:
    try:
        with storage.task_binding(task) as (task_dir, task_fd):
            bound_task_dir = f"/proc/self/fd/{task_fd}"
            before = storage._leaf_info(task_fd, "TASK.json")
            if before is None:
                return False
            with receipt_stream_transaction_fd(task_fd):
                control = read_task_control(bound_task_dir)
                verified = bool(
                    control
                    and control.get("run_id") == task_run_id
                    and task_control_status(bound_task_dir, control) == "closed"
                )
            after = storage._leaf_info(task_fd, "TASK.json")
            if after is None or (
                after.st_dev,
                after.st_ino,
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ) != (
                before.st_dev,
                before.st_ino,
                before.st_size,
                before.st_mtime_ns,
                before.st_ctime_ns,
            ):
                return False
            return verified
    except (OSError, RuntimeError, ValueError):
        return False


def _run_with_storage(
    storage: _SafeRepoStorage,
    threshold: int,
    dry_run: bool,
    task: str | None,
    task_run_id: str | None,
) -> int:
    repo_root = storage.repo_root
    entries = _parse_entries(storage.read_learnings())
    automatic = task is not None or task_run_id is not None

    if automatic and not _verified_closed_run(storage, task or "", task_run_id or ""):
        print("automatic promotion requires the matching verified closed task run", file=sys.stderr)
        return 2

    if not entries:
        print("(no learnings to process)")
        return 0

    candidates = [entry for entry in entries if _valid_learning_candidate(entry)]
    verified_contexts: dict[tuple[str, str], bool] = {}
    unique_candidates: dict[tuple[str, str, str], dict] = {}
    for entry in candidates:
        context = (entry["task"], entry["task_run_id"])
        if context not in verified_contexts:
            verified_contexts[context] = _verified_closed_run(storage, *context)
        if not verified_contexts[context]:
            continue
        identity = (*context, entry["key"])
        if identity not in unique_candidates or entry["ts"] > unique_candidates[identity]["ts"]:
            unique_candidates[identity] = entry

    verified_entries = list(unique_candidates.values())
    current_keys: set[str] | None = None
    if automatic:
        for entry in verified_entries:
            if entry["task"] != task or entry["task_run_id"] != task_run_id:
                continue
            key = entry["key"]
            current_keys = current_keys or set()
            current_keys.add(key)
        current_keys = current_keys or set()
        if not current_keys:
            print("(no qualifying learning for current task run; promotion skipped)")
            _audit_stale_files(entries, repo_root)
            _audit_contradictions(entries)
            return 0

    counts: Counter[str] = Counter(entry["key"] for entry in verified_entries)

    promotable = {
        key for key, count in counts.items()
        if count >= threshold and (current_keys is None or key in current_keys)
    }
    print(
        f"learnings: {len(entries)} entries, {len(verified_entries)} verified unique, "
        f"{len(counts)} unique keys, {len(promotable)} promotable "
        f"(threshold={threshold})"
    )

    for key in sorted(promotable):
        mode = "would report" if dry_run else "candidate"
        print(
            f"  Tier 2 {mode}: {key} ({counts[key]} verified task runs); "
            "open a reviewed Harness task before changing durable patterns"
        )

    stale_warns = _audit_stale_files(entries, repo_root)
    contra_warns = _audit_contradictions(entries)
    if stale_warns + contra_warns:
        print(
            f"hygiene: {stale_warns} stale-file + {contra_warns} "
            "contradiction warnings (see stderr)",
            flush=True,
        )
    return 0


def run(
    repo_root: str,
    threshold: int,
    dry_run: bool,
    task: str | None = None,
    task_run_id: str | None = None,
) -> int:
    automatic = task is not None or task_run_id is not None
    if automatic and not (
        _bounded_text(task, MAX_CONTEXT_LENGTH, single_line=True)
        and _bounded_text(task_run_id, MAX_CONTEXT_LENGTH, single_line=True)
    ):
        print("automatic mode requires valid --task and --task-run-id values", file=sys.stderr)
        return 2
    try:
        with _SafeRepoStorage(repo_root) as storage:
            with storage.promotion_lock():
                return _run_with_storage(
                    storage, threshold, dry_run, task=task, task_run_id=task_run_id
                )
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"promotion skipped: {exc}", file=sys.stderr)
        return 2


def main() -> int:
    p = argparse.ArgumentParser(description="Report validated Tier 2 learning candidates")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--threshold", type=int, default=DEFAULT_THRESHOLD,
                   help=f"Min occurrences for promotion (default {DEFAULT_THRESHOLD})")
    p.add_argument("--task", help="Current task id; with --task-run-id enables automatic mode")
    p.add_argument("--task-run-id", help="Current task run id; with --task enables automatic mode")
    args = p.parse_args()
    return run(
        find_repo_root(), args.threshold, args.dry_run,
        task=args.task, task_run_id=args.task_run_id,
    )


if __name__ == "__main__":
    sys.exit(main())
