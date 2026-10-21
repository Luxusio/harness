#!/usr/bin/env python3
"""Harvest one lead's task evidence from a linked worktree before removal.

`doc/harness/tasks/` and `doc/harness/learnings.jsonl` are gitignored, so a
batch lead's task evidence and learnings only exist inside its own linked
worktree. Before the coordinator runs `git worktree remove` it must copy that
evidence into the main checkout, or it is lost.

Usage:
  python3 plugin/scripts/batch_harvest.py --worktree <abs path> \
      --task-id <TASK__x> [--repo <main checkout>]

Refuses (non-zero exit, nothing written) unless:
  - `<worktree>` is a registered linked worktree of `--repo` (a regular,
    non-symlink `.git` gitfile whose `gitdir:` target sits directly under
    `<repo>/.git/worktrees/`, with a matching `gitdir` back-pointer); and
  - the worktree's HEAD is on a branch (a rebase stopped midway detaches it;
    stopped at its first commit, it sits at the main HEAD itself, which would
    otherwise pass the next check); and
  - the worktree's current HEAD commit is an ancestor of the main checkout's
    HEAD (i.e. already integrated: rebased and fast-forwarded).

Idempotent: re-running after a successful harvest is a no-op for the archive
copy (byte-identical tree) and appends zero learnings rows. An existing archive
with different content for the same task id is refused, never replaced. Never removes the
worktree, branch, or any git state — that stays the coordinator's job with
plain `git worktree remove` / `git branch -d`. The internal S1 removal
entrypoint below is the sole guarded exception for state-managed submodules.

Stdlib only.
"""
from __future__ import annotations

import argparse
from collections import deque
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import (  # type: ignore  # noqa: E402
    GitBindingError,
    _trusted_git_env,
    resolve_registered_worktree,
    find_repo_root,
)

TASK_ID_RE = re.compile(r"^TASK__[A-Za-z0-9._-]+$")


class HarvestError(RuntimeError):
    """Refusal to harvest — nothing has been written when this is raised."""


def _verify_registered_worktree(repo_root: str, worktree: str) -> None:
    """Refuse unless `worktree` is a registered linked worktree of `repo_root`."""
    if not os.path.isabs(worktree):
        raise HarvestError(f"--worktree must be an absolute path: {worktree}")
    try:
        resolved = resolve_registered_worktree(repo_root, worktree)
    except GitBindingError as exc:
        raise HarvestError(f"worktree is not a registered git worktree: {exc}") from exc
    if not resolved:
        raise HarvestError("--worktree names the main checkout, not a linked worktree")


def _worktree_head_sha(worktree: str) -> str:
    result = subprocess.run(
        ["git", "-C", worktree, "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False, env=_trusted_git_env(),
    )
    if result.returncode != 0:
        raise HarvestError(f"cannot resolve worktree HEAD: {result.stderr.strip()}")
    sha = result.stdout.strip()
    if not sha:
        raise HarvestError("worktree HEAD is empty")
    return sha


def _verify_merged(repo_root: str, worktree: str) -> None:
    on_branch = subprocess.run(
        ["git", "-C", worktree, "symbolic-ref", "-q", "HEAD"],
        capture_output=True, text=True, check=False, env=_trusted_git_env(),
    )
    if on_branch.returncode != 0:
        raise HarvestError(
            "worktree HEAD is detached (a rebase may be in progress); "
            "finish or abort it before harvesting"
        )
    sha = _worktree_head_sha(worktree)
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", sha, "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=False,
        env=_trusted_git_env(),
    )
    if result.returncode != 0:
        raise HarvestError(
            f"worktree HEAD {sha} is not merged into the main checkout's HEAD"
        )


def _refuse_links_below(worktree: str, *parts: str) -> str:
    """Return ``<worktree>/<parts...>`` after refusing a symlink at any level."""
    path = worktree
    for part in parts:
        path = os.path.join(path, part)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            return path
        if stat.S_ISLNK(info.st_mode):
            raise HarvestError(f"refusing symlink in worktree evidence path: {path}")
    return path


def _refuse_non_regular_tree(root: str) -> None:
    """Refuse symlinks, FIFOs, sockets, and devices anywhere under ``root``."""
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        for name in dirnames + filenames:
            full = os.path.join(dirpath, name)
            mode = os.lstat(full).st_mode
            if not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
                raise HarvestError(
                    f"refusing non-regular entry in task evidence: {full}"
                )


def _hash_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _tree_entries(root: str) -> dict:
    """Map every relative path under `root` to a file hash, or None for a dir."""
    entries: dict = {}

    def scan_error(error):
        raise error

    with ThreadPoolExecutor(max_workers=4) as executor:
        pending = deque()
        for dirpath, dirnames, filenames in os.walk(root, onerror=scan_error):
            for name in dirnames:
                rel = os.path.relpath(os.path.join(dirpath, name), root)
                entries[rel] = None
            for name in filenames:
                full = os.path.join(dirpath, name)
                rel = os.path.relpath(full, root)
                # Reserve traversal order before asynchronous completion.
                entries[rel] = None
                pending.append((rel, executor.submit(_hash_file, full)))
                if len(pending) == 4:
                    key, future = pending.popleft()
                    entries[key] = future.result()
        for key, future in pending:
            entries[key] = future.result()
    return entries


def _archive_task(repo_root: str, worktree: str, task_id: str) -> str:
    if not TASK_ID_RE.match(task_id):
        raise HarvestError(f"invalid task_id: {task_id}")
    source_dir = _refuse_links_below(worktree, "doc", "harness", "tasks", task_id)
    if not os.path.isdir(source_dir):
        raise HarvestError(f"task directory not found in worktree: {source_dir}")
    # A lead's worktree is untrusted input: copy only plain files and dirs so a
    # planted link or FIFO can neither pull outside files in nor hang the copy.
    _refuse_non_regular_tree(source_dir)

    dest_root = os.path.join(repo_root, "doc", "harness", "archive", "batch")
    dest_dir = os.path.join(dest_root, task_id)
    os.makedirs(dest_root, exist_ok=True)

    if os.path.lexists(dest_dir):
        if os.path.islink(dest_dir) or not os.path.isdir(dest_dir):
            raise HarvestError(
                f"archive destination exists and is not a plain directory: {dest_dir}"
            )
        if _tree_entries(source_dir) == _tree_entries(dest_dir):
            return dest_dir  # already archived, identical content: no-op
        # A different tree under the same id is an earlier batch's evidence
        # (e.g. a reused slug); never replace it.
        raise HarvestError(
            f"archive already holds different evidence for {task_id}: {dest_dir}; "
            "refusing to overwrite it"
        )

    tmp_dir = tempfile.mkdtemp(dir=dest_root, prefix=f".{task_id}.tmp-")
    try:
        # mkdtemp already created tmp_dir; copytree requires the destination to
        # not exist, so drop the empty placeholder before copying into it.
        os.rmdir(tmp_dir)
        shutil.copytree(source_dir, tmp_dir, symlinks=True)
        os.rename(tmp_dir, dest_dir)
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return dest_dir


def _read_learnings_rows(src: str) -> list:
    """Return the JSON-object rows of a worktree learnings file, no links followed."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    try:
        fd = os.open(src, flags)
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise HarvestError(f"refusing unreadable or symlinked learnings file: {src}") from exc
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            raise HarvestError(f"refusing non-regular learnings file: {src}")
        rows = []
        for line in handle:
            stripped = line.rstrip("\n")
            try:
                if isinstance(json.loads(stripped), dict):
                    rows.append(stripped)
            except ValueError:
                continue
        return rows


def _append_learnings(repo_root: str, rows: list) -> int:
    if not rows:
        return 0
    dest = os.path.join(repo_root, "doc", "harness", "learnings.jsonl")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "a+", encoding="utf-8") as handle:
        # Read-dedupe-append under one exclusive lock so a retried harvest
        # cannot interleave with a still-running one.
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        existing = {line.rstrip("\n") for line in handle if line.strip()}
        new_rows = []
        for row in rows:
            if row not in existing:
                new_rows.append(row)
                existing.add(row)
        for row in new_rows:
            handle.write(row + "\n")
    return len(new_rows)


def harvest(repo_root: str, worktree: str, task_id: str) -> dict:
    repo_root = os.path.realpath(repo_root)
    if not os.path.isabs(worktree):
        raise HarvestError(f"--worktree must be an absolute path: {worktree}")
    # Validate and operate on one canonical path, never the raw argument.
    worktree = os.path.realpath(worktree)
    _verify_registered_worktree(repo_root, worktree)
    _verify_merged(repo_root, worktree)
    # Read and validate every input before the first write, so a refusal
    # leaves the main checkout untouched.
    rows = _read_learnings_rows(
        _refuse_links_below(worktree, "doc", "harness", "learnings.jsonl")
    )
    dest_dir = _archive_task(repo_root, worktree, task_id)
    appended = _append_learnings(repo_root, rows)
    return {"archived": dest_dir, "learnings_appended": appended}


def _s1_require(condition, reason):
    if not condition:
        raise HarvestError(reason)


def _s1_path(path, directory=False):
    """Check every existing ancestor, including before bounded state reads."""
    absolute = os.path.abspath(path)
    _s1_require(os.path.realpath(absolute) == absolute, "unsafe symlink in S1 proof path")
    current = os.path.sep
    parts = absolute.split(os.path.sep)[1:]
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        info = os.lstat(current)
        is_directory = index < len(parts) - 1 or directory
        _s1_require(stat.S_ISDIR(info.st_mode) if is_directory else stat.S_ISREG(info.st_mode),
                    "unsafe S1 proof path type")
    return absolute


def _s1_git(root, *args):
    env = _trusted_git_env()
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT='0', GIT_NO_LAZY_FETCH='1', GIT_NO_REPLACE_OBJECTS='1', LC_ALL='C')
    return subprocess.run(
        ['git', '--no-optional-locks', '-c', 'core.fsmonitor=false',
         '-c', 'core.hooksPath=' + os.devnull, '-c', 'submodule.recurse=false',
         '-c', 'protocol.allow=never', '-c', 'status.showUntrackedFiles=all', *args],
        cwd=root, env=env, capture_output=True, text=True, timeout=120,
    )


def _s1_git_text(root, *args):
    result = _s1_git(root, *args)
    _s1_require(result.returncode == 0 and len(result.stdout) <= 4 * 1024 * 1024,
                "cannot inspect S1 Git proof: " + args[0])
    _s1_require(not any(x in result.stderr for x in ('Permission denied', 'could not open directory')),
                "cannot inspect all S1 checkout content")
    return result.stdout.rstrip('\n')


def _s1_content(worktree, task_id, source_entries, manifest, repo, *, ordinary_allowed=(), ordinary_caches=False):
    """Every outer file must be tracked or have an exact preserved counterpart."""
    from batch_submodules import reject_hidden_index_flags

    reject_hidden_index_flags(worktree)
    _s1_require(not _s1_git_text(worktree, 'diff', '--cached', '--name-only', 'HEAD', '--ignore-submodules=none')
                and not _s1_git_text(worktree, 'diff', '--name-only', '--ignore-submodules=none'),
                "tracked or staged S1 worktree content changed")
    tracked = set(filter(None, _s1_git_text(worktree, 'ls-files', '-z').split('\0')))
    selected = {m['path'] for m in manifest['modules']}
    task_rel = 'doc/harness/tasks/' + task_id
    allowed = set(tracked) | {task_rel + '/' + rel for rel in source_entries} | set(ordinary_allowed)
    allowed.add(task_rel)
    learning_rel = 'doc/harness/learnings.jsonl'
    learning = os.path.join(worktree, learning_rel)
    if os.path.lexists(learning):
        _s1_path(learning)
        destination = _s1_path(os.path.join(repo, learning_rel))
        _s1_require(os.path.getsize(learning) <= 1024 * 1024 and os.path.getsize(destination) <= 1024 * 1024,
                    "learning content exceeds removal proof bound")
        with open(learning, 'rb') as handle:
            source_lines = handle.read().splitlines()
        with open(destination, 'rb') as handle:
            dest_lines = set(handle.read().splitlines())
        # Unlike ordinary harvest's permissive parser, disposal cannot lose
        # malformed rows or unique bytes that were not appended to the ledger.
        _s1_require(all(line in dest_lines for line in source_lines), "learning content not preserved")
        allowed.add(learning_rel)
    directories = set(ordinary_allowed)
    for path in allowed | selected:
        parent = os.path.dirname(path)
        while parent:
            directories.add(parent)
            parent = os.path.dirname(parent)
    directories.update(task_rel + '/' + p for p, value in source_entries.items() if value is None)
    directories.add(task_rel)
    def unreadable(exc):
        raise HarvestError('cannot inspect every worktree entry') from exc
    count = 0
    for current, dirs, files in os.walk(worktree, followlinks=False, onerror=unreadable):
        relroot = os.path.relpath(current, worktree)
        relroot = '' if relroot == '.' else relroot
        keep = []
        for name in dirs:
            rel = '/'.join(filter(None, (relroot, name)))
            full = os.path.join(worktree, rel)
            _s1_require(name != '.git', 'unexpected nested repository')
            if os.path.islink(full):
                _s1_require(rel in tracked, 'unknown symlink directory in worktree removal proof')
                continue  # tracked link bytes are integrated; never follow its target
            if rel in selected:
                continue  # shared helper independently proves all module content
            if ordinary_caches and name in ORDINARY_CACHES:
                count += _ordinary_cache(full)
                _s1_require(count <= 100000, 'worktree content inventory exceeds bound')
                continue
            _s1_require(rel in directories or rel in tracked, 'unknown ignored or untracked directory: ' + rel)
            keep.append(name)
        dirs[:] = keep
        for name in files:
            rel = '/'.join(filter(None, (relroot, name)))
            if rel == '.git':
                continue  # registered worktree gitfile proved separately
            _s1_require(name != '.git', 'unexpected nested repository')
            _s1_require(rel in allowed, 'unknown ignored or untracked content: ' + rel)
            mode = os.lstat(os.path.join(worktree, rel)).st_mode
            _s1_require(stat.S_ISREG(mode) or rel in tracked and stat.S_ISLNK(mode), 'nonregular worktree content')
        count += len(dirs) + len(files)
        _s1_require(count <= 100000, 'worktree content inventory exceeds bound')



# Only these actual directory trees are reproducible disposal exceptions.
# A regular file or symlink with the same name is never a cache exception.
ORDINARY_CACHES = {'.venv', 'node_modules', '__pycache__', '.pytest_cache', '.mypy_cache', '.ruff_cache'}


def _ordinary_cache(root):
    def unreadable(exc):
        raise HarvestError('cannot inspect reproducible cache') from exc
    count = 0
    for current, dirs, files in os.walk(root, followlinks=False, onerror=unreadable):
        for name in dirs + files:
            full = os.path.join(current, name)
            _s1_require(name != '.git', 'nested repository inside cache: ' + full)
            mode = os.lstat(full).st_mode
            _s1_require(stat.S_ISREG(mode) or stat.S_ISDIR(mode) or stat.S_ISLNK(mode),
                        'nonregular cache content: ' + full)
            count += 1
            _s1_require(count <= 100000, 'cache content inventory exceeds bound')
    return count


def _ordinary_bytes(path, limit=65536):
    _s1_path(path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as handle:
        info = os.fstat(handle.fileno())
        _s1_require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
                    and info.st_nlink == 1 and not info.st_mode & 0o022,
                    'unsafe disposable control metadata: ' + path)
        raw = handle.read(limit + 1)
    _s1_require(len(raw) <= limit, 'disposable control metadata exceeds bound')
    return raw


def _ordinary_markers(worktree, task_id):
    from _lib import read_task_control, task_control_status, sanitize_session_id
    task_rel = 'doc/harness/tasks/' + task_id
    source = os.path.join(worktree, task_rel)
    control = read_task_control(source)
    if not control or task_control_status(source, control) != 'closed':
        return set()  # standalone legacy evidence can be archived, never infer marker ownership
    allowed = set()
    tasks = os.path.dirname(source)
    for name in os.listdir(tasks):
        full = os.path.join(tasks, name)
        if re.fullmatch(r'\.active(?:\.[A-Za-z0-9_.-]+)?\.lock', name):
            _s1_require(_ordinary_bytes(full, 0) == b'', 'nonempty Harness operational lock')
            allowed.add('doc/harness/tasks/' + name)
        elif name == '.active':
            _s1_require(_ordinary_bytes(full, 4096).decode('utf-8').strip() == source,
                        'legacy active marker belongs to another task')
            allowed.add('doc/harness/tasks/' + name)
    sessions = os.path.join(tasks, '.active_sessions')
    owned_sessions = set()
    if os.path.lexists(sessions):
        _s1_path(sessions, True)
        allowed.add('doc/harness/tasks/.active_sessions')
        for name in os.listdir(sessions):
            if not name.endswith('.json'):
                continue  # unknown entries are rejected by the full inventory
            raw = _ordinary_bytes(os.path.join(sessions, name))
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    _s1_require(key not in result, 'duplicate session marker field')
                    result[key] = value
                return result
            marker = json.loads(raw, object_pairs_hook=unique)
            sid = name[:-5]
            _s1_require(isinstance(marker, dict) and set(marker) == {'session_id', 'task_dir', 'task_id', 'run_id', 'updated'}
                        and all(isinstance(v, str) and v for v in marker.values())
                        and marker['session_id'] == sid and sanitize_session_id(sid) == sid
                        and marker['task_dir'] == source and marker['task_id'] == task_id
                        and marker['run_id'] == control['run_id'], 'session marker does not belong to exact closed task')
            owned_sessions.add(sid)
            allowed.add('doc/harness/tasks/.active_sessions/' + name)
        hint = os.path.join(sessions, '.session-hint')
        if os.path.lexists(hint):
            sid = _ordinary_bytes(hint, 4096).decode('utf-8').strip()
            _s1_require(sid in owned_sessions, 'session hint lacks exact closed-task marker')
            allowed.add('doc/harness/tasks/.active_sessions/.session-hint')
    return allowed


def _ordinary_learning_proof(source, destination):
    """Preserve exact raw rows while permitting unrelated destination history."""
    raw = _ordinary_bytes(source, 1024 * 1024)
    for line in raw.splitlines():
        _s1_require(isinstance(json.loads(line), dict), 'malformed learning content cannot be discarded')
    preserved = _ordinary_bytes(destination, 1024 * 1024)
    rows = raw.split(b'\n')
    if raw.endswith(b'\n'):
        rows.pop()
    preserved_rows = set(preserved.split(b'\n'))
    _s1_require(all(row in preserved_rows for row in rows),
                'learning bytes not preserved outside worktree')



def _ordinary_ancillary(repo, worktree, task_id, *, control_dir=None, preserve=True):
    """Preserve exact typed runtime snapshots outside the immutable task archive.

    These files are advisory, never review/QA evidence. The checkpoint producer
    has no run identity: its exact task header identifies a historical snapshot,
    not the current generation. Diagnostics identify a session but not task/run;
    their sidecar location records disposal context, never generation ownership.
    Closing clears session markers, so their absence is expected here.
    """
    from _lib import read_task_control, task_control_status, sanitize_session_id

    task = control_dir or os.path.join(worktree, 'doc/harness/tasks', task_id)
    control = read_task_control(task)
    candidates = [('doc/harness/.watcher-diagnostics.json', 'watcher-diagnostics.json'),
                  ('doc/harness/checkpoints/' + task_id + '.md', 'checkpoint.md')]
    present = [(rel, name) for rel, name in candidates if os.path.lexists(os.path.join(worktree, rel))]
    if not present and preserve:
        return set()
    _s1_require(control and task_control_status(task, control) == 'closed',
                'ancillary snapshots require exact closed task')
    run = control.get('run_id')
    _s1_require(isinstance(run, str) and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run),
                'unsafe ancillary snapshot run identity')
    root = os.path.join(repo, 'doc/harness/archive/worktree-recovery', task_id, run)
    if not preserve:
        present = [(rel, name) for rel, name in candidates
                   if os.path.lexists(os.path.join(worktree, rel)) or os.path.lexists(os.path.join(root, name))]
    if not present:
        return set()
    snapshots = []
    for rel, name in present:
        source = os.path.join(worktree, rel)
        raw = _ordinary_bytes(source if os.path.lexists(source) else os.path.join(root, name))
        if name == 'watcher-diagnostics.json':
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    _s1_require(key not in result, 'duplicate watcher diagnostic field')
                    result[key] = value
                return result
            value = json.loads(raw, object_pairs_hook=unique)
            required = {'session_id', 'updated', 'registration_present'}
            optional = {'last_registration_error', 'last_registration_note'}
            _s1_require(isinstance(value, dict) and required <= set(value) <= required | optional
                        and isinstance(value['session_id'], str) and value['session_id']
                        and isinstance(value['updated'], str) and value['updated']
                        and (value['registration_present'] is None or type(value['registration_present']) is bool)
                        and all(isinstance(value[key], str) for key in optional & set(value)),
                        'unknown watcher diagnostics schema')
            sid = value['session_id']
            _s1_require(sid != 'default' and sanitize_session_id(sid) == sid,
                        'watcher diagnostics have unsafe session identity')
        else:
            text = raw.decode('utf-8')
            _s1_require(text.startswith('# Checkpoint — ' + task_id + '\n\n- written: ')
                        and all(section in text for section in
                                ('\n## Task state\n', '\n## Progress\n', '\n## Next action\n')),
                        'unknown or foreign task checkpoint')
        snapshots.append((rel, name, raw))
    # No mutable manifest and no mutation of closed task evidence. Exact bytes
    # themselves are the preservation proof; an existing different copy refuses.
    for directory in (os.path.join(repo, 'doc/harness/archive'),
                      os.path.join(repo, 'doc/harness/archive/worktree-recovery'),
                      os.path.dirname(root), root):
        if preserve:
            try:
                os.mkdir(directory, 0o700)
            except FileExistsError:
                pass
        _s1_path(directory, True)
        info = os.lstat(directory)
        _s1_require(info.st_uid == os.getuid() and not info.st_mode & 0o022,
                    'unsafe ancillary snapshot directory: ' + directory)
    preserved = set()
    for rel, name, raw in snapshots:
        destination = os.path.join(root, name)
        if not os.path.lexists(destination):
            _s1_require(preserve, 'ancillary snapshot was not preserved before partial removal: ' + rel)
            try:
                fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            except FileExistsError:
                pass  # concurrent preservation must still match exact bytes
            else:
                with os.fdopen(fd, 'wb') as handle:
                    handle.write(raw)
                    handle.flush()
                    os.fsync(handle.fileno())
        _s1_require(_ordinary_bytes(destination) == raw,
                    'ancillary snapshot collision; refusing overwrite: ' + destination)
        if os.path.lexists(os.path.join(worktree, rel)):
            _s1_require(_ordinary_bytes(os.path.join(worktree, rel)) == raw,
                        'ancillary snapshot changed during preservation')
        preserved.add(rel)
    # Publish every newly created directory entry durably before W can go.
    # Syncing only the run directory does not persist its name in its parent.
    current = root
    while preserve and current != os.path.dirname(repo):
        _s1_path(current, True)
        directory_fd = os.open(current, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        current = os.path.dirname(current)
    return preserved


def ordinary_removal_proof(repo, worktree, task_id, retention=None, checkpoint=None):
    """Refuse ordinary disposal until every noncache byte is accounted for.

    This is a removal guard, not an additional archive or task authority. Its
    inventory runs after integration/harvest, immediately before unlocking.
    """
    try:
        _verify_registered_worktree(repo, worktree)
        source = _s1_path(os.path.join(worktree, 'doc/harness/tasks', task_id), True)
        archive = _s1_path(os.path.join(repo, 'doc/harness/archive/batch', task_id), True)
        for directory in (source, archive):
            _refuse_non_regular_tree(directory)
        entries = _tree_entries(source)
        _s1_require(entries == _tree_entries(archive), 'task evidence differs from preserved archive')
        if checkpoint is not None:
            fingerprint = 'sha256:' + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
            _s1_require(checkpoint.get('stage') == 'harvested'
                        and checkpoint.get('harvest', {}).get('archived') == archive
                        and checkpoint.get('archive_fingerprint') == fingerprint,
                        'task archive differs from durable removal checkpoint')
        allowed = _ordinary_markers(worktree, task_id)
        ancillary = _ordinary_ancillary(repo, worktree, task_id)
        allowed.update(ancillary)
        learning = os.path.join(worktree, 'doc/harness/learnings.jsonl')
        if os.path.lexists(learning):
            _ordinary_learning_proof(learning, os.path.join(repo, 'doc/harness/learnings.jsonl'))
        if retention is not None:
            from batch_finish import RETENTION_MARKER, validate_retention
            validate_retention(worktree, retention)
            allowed.add(RETENTION_MARKER)
        _s1_content(worktree, task_id, entries, {'modules': []}, repo,
                    ordinary_allowed=allowed, ordinary_caches=True)
        _s1_require(_ordinary_ancillary(repo, worktree, task_id) == ancillary,
                    'ancillary snapshot inventory changed')
        _s1_require(_tree_entries(source) == entries and _tree_entries(archive) == entries,
                    'task evidence changed during removal inventory')
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HarvestError('ordinary removal proof refused: ' + str(exc)) from exc


def thaw_verification_sources(repo, worktree, task_id, retention, checkpoint):
    """Only after full ordinary proof and durable archive, unseal typed copies."""
    ordinary_removal_proof(repo, worktree, task_id, retention, checkpoint)
    if checkpoint is None:
        return
    from verification_target import _spec
    task = os.path.join(worktree, 'doc/harness/tasks', task_id)
    store = os.path.join(task, 'work/verification-targets')
    if not os.path.isdir(store):
        return
    directories = []
    for token in os.listdir(store):
        if not re.fullmatch(r'[0-9a-f]{16}', token):
            continue
        # Disposal preserves history, including copies issued before fresh_run.
        # It does not certify these historical inputs for current acceptance.
        relative = os.path.join('work/verification-targets', token, 'target.json')
        metadata = _ordinary_bytes(os.path.join(task, relative), 1024 * 1024)
        archive_task = checkpoint['harvest']['archived']
        _s1_require(metadata == _ordinary_bytes(os.path.join(archive_task, relative), 1024 * 1024),
                    'verification metadata differs from preserved archive')
        record = json.loads(metadata)
        _s1_require(isinstance(record, dict) and record.get('token') == token
                    and isinstance(record.get('run_id'), str) and bool(record['run_id'])
                    and isinstance(record.get('manifest'), list), 'invalid historical verification metadata')
        _spec(record.get('spec'))
        for name in os.listdir(os.path.join(store, token)):
            if name != 'source' and not re.fullmatch(r'source-restored-[0-9a-f]{16}', name):
                continue
            root = os.path.join(store, token, name)
            _s1_path(root, True)
            for current, dirs, files in os.walk(root):
                _s1_path(current, True)
                info = os.lstat(current)
                directories.append((current, info.st_dev, info.st_ino, info.st_mode))
    # Inventory/proof must finish before any permission change.
    ordinary_removal_proof(repo, worktree, task_id, retention, checkpoint)
    for path, dev, ino, mode in directories:
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            _s1_require((info.st_dev, info.st_ino, info.st_mode) == (dev, ino, mode),
                        'verification directory changed before disposal')
            os.fchmod(fd, stat.S_IMODE(mode) | stat.S_IWUSR)
        finally:
            os.close(fd)


def ordinary_unregistered(repo, worktree):
    """Absence of both registration and administrative back-pointers."""
    worktree = os.path.abspath(worktree)
    listing = _s1_git_text(repo, 'worktree', 'list', '--porcelain', '-z')
    if any(field == 'worktree ' + worktree for field in listing.split('\0')):
        return False
    _s1_require(not os.path.lexists(os.path.join(worktree, '.git')), 'residue retains .git metadata')
    common = _s1_git_text(repo, 'rev-parse', '--git-common-dir')
    if not os.path.isabs(common):
        common = os.path.join(repo, common)
    admins = os.path.join(common, 'worktrees')
    if os.path.lexists(admins):
        _s1_path(admins, True)
        for name in os.listdir(admins):
            pointer = os.path.join(admins, name, 'gitdir')
            if os.path.lexists(pointer):
                _s1_path(pointer)
                with open(pointer, encoding='utf-8') as handle:
                    target = handle.read(65537).strip()
                _s1_require(len(target) <= 65536, 'oversized worktree administrative pointer')
                if not os.path.isabs(target):
                    target = os.path.join(os.path.dirname(pointer), target)
                _s1_require(os.path.realpath(target) != os.path.join(worktree, '.git'),
                            'residue retains administrative registration')
    return True


def remove_ordinary_residue(repo, worktree, task_id, tip, checkpoint):
    """Dispose only a proven remaining subset; caller owns closed-run authority."""
    _s1_require(ordinary_unregistered(repo, worktree), 'residue is still registered')
    _s1_path(worktree, True)
    _s1_require(os.path.abspath(worktree) == os.path.realpath(worktree)
                and os.path.commonpath([repo, worktree]) != worktree,
                'unsafe residue root')
    archive = _s1_path(os.path.join(repo, 'doc/harness/archive/batch', task_id), True)
    _s1_require(os.path.commonpath([archive, worktree]) != worktree, 'archive lies inside residue')
    _refuse_non_regular_tree(archive)
    archived = _tree_entries(archive)
    fingerprint = 'sha256:' + hashlib.sha256(json.dumps(archived, sort_keys=True).encode()).hexdigest()
    _s1_require(checkpoint.get('archive_fingerprint') == fingerprint
                and checkpoint.get('harvest', {}).get('archived') == archive
                and checkpoint.get('integrated_tip') == tip and checkpoint.get('stage') == 'harvested',
                'residue archive/integration proof changed')
    tree = _s1_git_text(repo, 'ls-tree', '-r', '-t', '-z', tip)
    tracked, directories, gitlinks = {}, set(), set()
    for entry in filter(None, tree.split('\0')):
        header, path = entry.split('\t', 1)
        mode, kind, oid = header.split()
        if kind == 'tree':
            directories.add(path)
        elif kind == 'blob' and mode in {'100644', '100755'}:
            tracked[path] = (oid, mode == '100755')
        elif kind == 'commit' and mode == '160000':
            gitlinks.add(path)
        # Tracked symlinks remain unsupported residue.
    directories.update(gitlinks)
    ancillary = _ordinary_ancillary(repo, worktree, task_id, control_dir=archive, preserve=False)
    ancillary_entries = {rel: hashlib.sha256(_ordinary_bytes(os.path.join(worktree, rel))).hexdigest()
                         for rel in ancillary if os.path.lexists(os.path.join(worktree, rel))}
    task_rel = 'doc/harness/tasks/' + task_id
    preserved = {task_rel + '/' + rel: digest for rel, digest in archived.items() if digest is not None}
    directories.add(task_rel)
    directories.update(task_rel + '/' + rel for rel, digest in archived.items() if digest is None)
    for path in list(directories) + list(preserved) + list(ancillary):
        while '/' in path:
            path = path.rsplit('/', 1)[0]
            directories.add(path)
    algorithm = _s1_git_text(repo, 'rev-parse', '--show-object-format')
    _s1_require(algorithm in {'sha1', 'sha256'}, 'unsupported Git object format')
    def inventory():
        found, cache_dirs = {}, set()
        def unreadable(exc):
            raise HarvestError('cannot inspect residue') from exc
        for current, dirs, files in os.walk(worktree, followlinks=False, onerror=unreadable):
            for path in [current] + [os.path.join(current, n) for n in files + dirs]:
                rel = os.path.relpath(path, worktree)
                _s1_require(len(found) < 100000, 'residue inventory exceeds bound')
                info = os.lstat(path)
                identity = (info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns)
                _s1_require(info.st_uid == os.getuid() and info.st_nlink == 1 or stat.S_ISDIR(info.st_mode)
                            and info.st_uid == os.getuid(), 'unsafe residue ownership/link count')
                parent = os.path.dirname(rel) or '.'
                in_cache = parent in cache_dirs or rel in cache_dirs
                if in_cache:
                    _s1_require(os.path.basename(rel) != '.git', 'nested repository inside residue cache: ' + rel)
                if stat.S_ISDIR(info.st_mode):
                    if rel in gitlinks:
                        # An uninitialized placeholder owns no module bytes.
                        # Observe only whether an entry exists; never descend.
                        with os.scandir(path) as children:
                            _s1_require(next(children, None) is None, 'populated gitlink residue: ' + rel)
                    elif (in_cache or os.path.basename(rel) in ORDINARY_CACHES
                          and (parent == '.' or parent in directories)):
                        cache_dirs.add(rel)
                    else:
                        _s1_require(rel == '.' or rel in directories, 'unknown residue directory: ' + rel)
                    found[rel] = identity
                    continue
                _s1_require(stat.S_ISREG(info.st_mode), 'nonregular residue: ' + rel)
                if in_cache:
                    found[rel] = identity
                    continue
                sha = hashlib.sha256()
                blob = hashlib.new(algorithm)
                blob.update(('blob ' + str(info.st_size) + '\0').encode())
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as handle:
                    before = os.fstat(handle.fileno())
                    _s1_require((before.st_dev, before.st_ino, before.st_mode, before.st_size, before.st_mtime_ns) == identity,
                                'residue changed during open')
                    for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                        sha.update(chunk)
                        blob.update(chunk)
                    after = os.fstat(handle.fileno())
                    _s1_require((after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns) == identity,
                                'residue changed during read')
                archived_match = (preserved.get(rel) == sha.hexdigest()
                                  and bool(info.st_mode & 0o111) == bool(os.lstat(os.path.join(archive, rel[len(task_rel) + 1:])).st_mode & 0o111))
                matches = (archived_match or ancillary_entries.get(rel) == sha.hexdigest()
                           or tracked.get(rel) == (blob.hexdigest(), bool(info.st_mode & 0o111)))
                if rel == 'doc/harness/learnings.jsonl':
                    _ordinary_learning_proof(path, os.path.join(repo, rel))
                    matches = True
                _s1_require(matches, 'unpreserved or changed residue: ' + rel)
                found[rel] = identity
            dirs[:] = [name for name in dirs
                       if os.path.relpath(os.path.join(current, name), worktree) not in gitlinks]
        return found
    expected = inventory()
    _s1_require(inventory() == expected, 'residue inventory changed')
    _s1_require(ordinary_unregistered(repo, worktree), 'residue registered during proof')
    _s1_require(_tree_entries(archive) == archived, 'archive changed during residue proof')
    _s1_require(_ordinary_ancillary(repo, worktree, task_id, control_dir=archive, preserve=False) == ancillary,
                'ancillary preservation changed during residue proof')
    # Keep verified directory descriptors through deletion; never traverse a
    # newly replaced parent by pathname while removing its children.
    handles = {}
    parent_fd = os.open(os.path.dirname(worktree), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for rel in sorted(expected, key=lambda p: (p.count('/'), p)):
            identity = expected[rel]
            if stat.S_ISDIR(identity[2]):
                if rel == '.':
                    owner, name = parent_fd, os.path.basename(worktree)
                else:
                    parent, name = os.path.split(rel)
                    owner = handles[parent or '.']
                fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=owner)
                handles[rel] = fd
                info = os.fstat(fd)
                _s1_require((info.st_dev, info.st_ino, info.st_mode, info.st_size, info.st_mtime_ns) == identity,
                            'residue directory changed before disposal')
        # No permission changes until all directory identities are checked.
        for fd in handles.values():
            info = os.fstat(fd)
            os.fchmod(fd, stat.S_IMODE(info.st_mode) | stat.S_IWUSR)
        for rel in sorted((p for p in expected if p != '.'), key=lambda p: (p.count('/'), p), reverse=True):
            parent, name = os.path.split(rel)
            owner = handles[parent or '.']
            info = os.stat(name, dir_fd=owner, follow_symlinks=False)
            original = expected[rel]
            _s1_require((info.st_dev, info.st_ino) == original[:2], 'residue replaced before deletion')
            if stat.S_ISDIR(original[2]):
                os.rmdir(name, dir_fd=owner)
            else:
                _s1_require((info.st_mode, info.st_size, info.st_mtime_ns) == original[2:], 'residue changed before deletion')
                os.unlink(name, dir_fd=owner)
        info = os.stat(os.path.basename(worktree), dir_fd=parent_fd, follow_symlinks=False)
        _s1_require((info.st_dev, info.st_ino) == expected['.'][:2], 'residue root replaced')
        os.rmdir(os.path.basename(worktree), dir_fd=parent_fd)
    finally:
        for fd in handles.values():
            os.close(fd)
        os.close(parent_fd)


def remove_s1_worktree(repo, worktree, task_id, manifest, checkpoint, git_runner=None):
    """Single-force disposal after durable state, evidence and module proofs.

    Finish owns lock/retention restoration and ordinary branch deletion. This
    callable grants no permission to standalone harvest or ordinary finish.
    """
    from _lib import _read_json_file, read_task_control, task_control_status
    import batch_submodules

    try:
        repo, worktree = os.fspath(repo), os.fspath(worktree)
        _s1_path(repo, True)
        _s1_path(worktree, True)
        batch_submodules.validate_manifest(manifest)
        _s1_require(manifest['repo'] == repo and manifest['worktree'] == worktree
                    and task_id == 'TASK__' + manifest['slug'], 'S1 removal identity mismatch')
        _verify_registered_worktree(repo, worktree)
        state_path = _s1_path(os.path.join(repo, 'doc/harness/runtime/batches', manifest['batch_id'] + '.json'))
        data = _read_json_file(state_path)
        _s1_require(type(data.get('schema_version')) is int and data['schema_version'] == 2
                    and data.get('repo') == repo and data.get('batch_id') == manifest['batch_id'], 'missing or incompatible durable S1 state')
        requests = data.get('requests')
        _s1_require(isinstance(requests, list), 'invalid durable request list')
        matching = [item for item in requests if isinstance(item, dict) and item.get('slug') == manifest['slug']]
        _s1_require(len(matching) == 1, 'missing or ambiguous durable S1 request')
        item = matching[0]
        _s1_require(item.get('status') == 'integrating' and item.get('task_id') == task_id
                    and item.get('worktree') == worktree and item.get('spawn_head') == manifest['spawn_head']
                    and item.get('submodule_manifest') == manifest and item.get('checkpoint') == checkpoint,
                    'durable S1 removal proof does not match request')
        _s1_require(isinstance(checkpoint, dict) and checkpoint.get('stage') == 'harvested'
                    and checkpoint.get('destination_ref') == data.get('destination_ref')
                    and checkpoint.get('run_id') == item.get('run_id')
                    and checkpoint.get('close_fingerprint') == item.get('close_fingerprint'), 'missing harvested generation checkpoint')
        tip = checkpoint.get('integrated_tip')
        _s1_require(isinstance(tip, str) and re.fullmatch(r'[0-9a-f]{40}|[0-9a-f]{64}', tip)
                    and checkpoint.get('branch_tip') == tip, 'invalid integrated tip')
        _s1_require(_s1_git_text(repo, 'symbolic-ref', '-q', 'HEAD') == data['destination_ref']
                    and _s1_git_text(repo, 'rev-parse', 'HEAD') == tip
                    and _s1_git_text(worktree, 'rev-parse', 'HEAD') == tip
                    and isinstance(item.get('branch'), str)
                    and _s1_git_text(worktree, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + item['branch'],
                    'integrated branch or destination changed')
        source = _s1_path(os.path.join(worktree, 'doc/harness/tasks', task_id), True)
        archive = _s1_path(os.path.join(repo, 'doc/harness/archive/batch', task_id), True)
        _s1_require(isinstance(checkpoint.get('harvest'), dict) and checkpoint['harvest'].get('archived') == archive,
                    'checkpoint archive path mismatch')
        for directory in (source, archive):
            _refuse_non_regular_tree(directory)
            control = read_task_control(directory)
            _s1_require(control and task_control_status(directory, control) == 'closed'
                        and control.get('run_id') == item.get('run_id')
                        and control.get('close_receipt_fingerprint') == item.get('close_fingerprint'),
                        'task source/archive is not the exact closed generation')
        source_entries, archive_entries = _tree_entries(source), _tree_entries(archive)
        fingerprint = 'sha256:' + hashlib.sha256(json.dumps(archive_entries, sort_keys=True).encode()).hexdigest()
        _s1_require(source_entries == archive_entries and checkpoint.get('archive_fingerprint') == fingerprint,
                    'task evidence differs from durable archive')
        batch_submodules.removal_proof(repo, worktree, manifest)
        _s1_content(worktree, task_id, source_entries, manifest, repo)
        # Re-read authoritative state after potentially expensive inventories, and
        # repeat module proof immediately before the one destructive operation.
        _s1_require(_read_json_file(_s1_path(state_path)) == data, 'durable state changed during removal proof')
        batch_submodules.removal_proof(repo, worktree, manifest)
        for directory in (source, archive):
            _s1_path(directory, True)
            _refuse_non_regular_tree(directory)
        _s1_require(_tree_entries(source) == source_entries and _tree_entries(archive) == archive_entries,
                    'task evidence changed during removal proof')
        _s1_content(worktree, task_id, source_entries, manifest, repo)
        _s1_require(_s1_git_text(repo, 'rev-parse', 'HEAD') == tip
                    and _s1_git_text(worktree, 'rev-parse', 'HEAD') == tip
                    and _s1_git_text(repo, 'symbolic-ref', '-q', 'HEAD') == data['destination_ref']
                    and _s1_git_text(worktree, 'symbolic-ref', '-q', 'HEAD') == 'refs/heads/' + item['branch'],
                    'branch identity changed during removal proof')
    except (batch_submodules.SubmoduleError, OSError, ValueError, subprocess.SubprocessError) as exc:
        raise HarvestError('S1 removal proof refused: ' + str(exc)) from exc
    return (git_runner or _s1_git)(repo, 'worktree', 'remove', '--force', worktree)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worktree", required=True, help="absolute path to the linked worktree")
    parser.add_argument("--task-id", required=True, help="TASK__... id to harvest")
    parser.add_argument(
        "--repo", default=None,
        help="main checkout root (default: discover from cwd)",
    )
    args = parser.parse_args()

    repo_root = args.repo or find_repo_root()
    try:
        summary = harvest(repo_root, args.worktree, args.task_id)
    except HarvestError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
