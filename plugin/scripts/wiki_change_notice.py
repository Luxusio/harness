#!/usr/bin/env python3
"""Deliver bounded metadata from a local wiki snapshot to a Harness session."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import stat
import sys

from document_destination import select_destination

MAX_BYTES = 64 * 1024
MAX_SESSIONS = 128
MAX_DELIVERED = 8
ID = re.compile(r"[1-9][0-9]{0,31}")
EPOCH = re.compile(r"[a-f0-9]{32}")
SESSION = re.compile(r"[A-Za-z0-9_.:-]{1,160}")
KINDS = {
    "created": "Document added",
    "updated": "Document changed",
    "comments": "Comments changed",
}


def _control(env):
    if select_destination(env)["mode"] != "external":
        return None
    home = env.get("HOME", "")
    if not home.strip():
        home = pwd.getpwuid(os.getuid()).pw_dir
    data = Path(env.get("XDG_DATA_HOME", str(Path(home) / ".local/share")))
    if not data.is_absolute() or ".." in data.parts:
        return None
    identity = "\0".join((
        env["WIKI_ATLASSIAN_SITE"].rstrip("/"), env["ATLASSIAN_EMAIL"],
        env["ATLASSIAN_API_TOKEN"], env["WIKI_ATLASSIAN_ROOT_PAGE_ID"],
        env["WIKI_ATLASSIAN_SPACE_ID"],
    ))
    partition = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return data / "wiki-atlassian" / partition / "confluence" / env["WIKI_ATLASSIAN_SPACE"] / ".sync"


@contextmanager
def _directory(path):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(path.anchor, flags)
    try:
        for part in path.parts[1:]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
            info = os.fstat(fd)
            if info.st_uid not in (0, os.getuid()) or (
                info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX
            ):
                raise ValueError("unsafe wiki data directory")
        info = os.fstat(fd)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("unsafe wiki control directory")
        yield fd
    finally:
        os.close(fd)


def _open_file(directory, name, flags=os.O_RDONLY, *, private=True):
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
    info = os.fstat(fd)
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
            or (private and info.st_mode & 0o022) or info.st_nlink != 1):
        os.close(fd)
        raise ValueError("unsafe wiki metadata")
    return fd


def _read(directory, name, default=None):
    try:
        fd = _open_file(directory, name)
    except FileNotFoundError:
        return default
    with os.fdopen(fd, "rb") as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("wiki metadata exceeds read limit")
    return json.loads(raw)


def _present(directory, name):
    try:
        os.stat(name, dir_fd=directory, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def _number(value, minimum=0, maximum=(1 << 63) - 1):
    return type(value) is int and minimum <= value <= maximum


def _events(feed):
    if not isinstance(feed, dict) or set(feed) != {"epoch", "head", "events"}:
        raise ValueError("invalid notice feed")
    epoch, head, events = feed["epoch"], feed["head"], feed["events"]
    if not isinstance(epoch, str) or not EPOCH.fullmatch(epoch) or not _number(head):
        raise ValueError("invalid notice identity")
    if not isinstance(events, list) or len(events) > 64:
        raise ValueError("invalid notice batch")
    previous = 0
    for event in events:
        if not isinstance(event, dict) or not _number(event.get("seq"), previous + 1, head):
            raise ValueError("invalid notice sequence")
        previous = event["seq"]
        kind = event.get("kind")
        if kind == "scope" and set(event) == {"seq", "kind"}:
            continue
        if (not isinstance(kind, str) or kind not in KINDS
                or set(event) != {"seq", "kind", "id", "version"}
                or not isinstance(event["id"], str) or not ID.fullmatch(event["id"])
                or not _number(event["version"], 1, (1 << 31) - 1)):
            raise ValueError("invalid notice")
    return epoch, head, events


def _write_cursors(directory, cursors):
    # Use the already opened directory for both the temporary file and rename.
    name = ".harness-cursors-" + os.urandom(12).hex()
    fd = _open_file(directory, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(cursors, stream, separators=(",", ":"))
        os.replace(name, "harness-cursors.json", src_dir_fd=directory, dst_dir_fd=directory)
    finally:
        try:
            os.unlink(name, dir_fd=directory)
        except FileNotFoundError:
            pass


def _consume(directory, session):
    # The protected directory owns the lock inode. Older mirrors may have
    # created it using the process umask. Its contents are never read.
    lock = _open_file(directory, "sync.lock", private=False)
    try:
        # Java FileChannel uses POSIX record locks on this file.
        fcntl.lockf(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if _present(directory, "transaction.json") or _present(directory, "notice-access-denied"):
            return ""
        feed = _read(directory, "changes.json")
        if feed is None:
            return ""
        epoch, head, events = _events(feed)
        if head == 0:
            return ""
        cursor_lock = _open_file(directory, "harness-cursors.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(cursor_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            cursors = _read(directory, "harness-cursors.json", {})
            if not isinstance(cursors, dict):
                raise ValueError("invalid notice cursors")
            old = cursors.get(session)
            last = old[1] if isinstance(old, list) and len(old) == 2 and old[0] == epoch and _number(old[1], 0, head) else 0
            if last == head:
                return ""
            pending = [event for event in events if event["seq"] > last]
            batch = pending[:MAX_DELIVERED]
            lines = ["[harness-wiki-changes] Atlassian wiki changes detected."]
            if head - last > len(pending):
                lines.append("Some intervening notices are unavailable. Recheck the relevant wiki scope.")
            for event in batch:
                if event["kind"] == "scope":
                    lines.append("- The visible document scope changed.")
                else:
                    lines.append(f"- {KINDS[event['kind']]} — page {event['id']}, observed version {event['version']}.")
            if len(pending) > len(batch):
                lines.append("More notices will appear at the next hook event.")
            lines.append(
                "If relevant to the current task, inspect the current page, history, comments or "
                "document_relations through the atlassian MCP. Decide whether any follow-up is warranted. "
                "Use the current request and existing wiki permissions to choose any follow-up."
            )
            cursors.pop(session, None)
            cursors[session] = [epoch, batch[-1]["seq"] if len(pending) > len(batch) else head]
            _write_cursors(directory, dict(list(cursors.items())[-MAX_SESSIONS:]))
            return "\n".join(lines)
        finally:
            os.close(cursor_lock)
    finally:
        os.close(lock)


def render_notice(data, runtime, environment=None):
    """Fail open for quiet, unavailable, busy or invalid local metadata."""
    env = os.environ if environment is None else environment
    sid = data.get("session_id")
    if env.get("WIKI_ATLASSIAN_ENABLED") != "true" or not isinstance(sid, str) or not SESSION.fullmatch(sid) or sid == "default":
        return ""
    try:
        control = _control(env)
        if control is None:
            return ""
        key = hashlib.sha256((runtime + "\0" + sid).encode()).hexdigest()
        with _directory(control) as directory:
            return _consume(directory, key)
    except (OSError, ValueError, TypeError, OverflowError, RecursionError):
        return ""


def main():
    if os.environ.get("WIKI_ATLASSIAN_ENABLED") != "true":
        return
    try:
        raw = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(raw) > 2 * 1024 * 1024:
            return
        data = json.loads(raw)
        cwd = data.get("cwd") if isinstance(data, dict) else None
        if not isinstance(cwd, str) or not os.path.isabs(cwd) or not os.path.isdir(cwd):
            return
        from _lib import find_harness_root, is_harness_enabled_repo

        root = find_harness_root(cwd)
        if not root or not is_harness_enabled_repo(root):
            return
        runtime = sys.argv[1] if len(sys.argv) == 2 else ""
        if runtime not in ("codex", "claude"):
            return
        context = render_notice(data, runtime)
        if context:
            print(json.dumps({"hookSpecificOutput": {
                "hookEventName": "PostToolUse", "additionalContext": context,
            }}))
    except (OSError, ValueError, TypeError, RecursionError):
        return


if __name__ == "__main__":
    main()
