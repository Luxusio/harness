"""Explicit, one-time import of retired Goal records; never an execution path.

Only apply writes. Its durable manifest is the commit point: original bytes are
archived first, destinations follow, and source retirement happens last.
"""
from __future__ import annotations

import base64
import copy
import ctypes
import errno
from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from functools import wraps
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

LIMIT = 1024 * 1024
MAX_RECORDS = 256
ARCHIVE = "doc/harness/archive/goal-migration"
MANIFEST = ARCHIVE + "/manifest.json"
GOALS = "doc/harness/goals"
IDENT = re.compile(r"(?:GOAL|TASK)__[A-Za-z0-9_.-]{1,180}")


def _require(ok, message):
    if not ok:
        raise ValueError("Goal migration: " + message)


def _hash(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def _json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    try:
        def constant(_):
            raise ValueError("non-finite JSON constant")
        return json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Goal migration: invalid bounded JSON") from exc


def _encode(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


_UNSPECIFIED = object()

_ROOT = ContextVar("goal_migration_root", default=None)
_DIR_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _safe_dir(fd):
    info = os.fstat(fd)
    _require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.getuid()
             and not stat.S_IMODE(info.st_mode) & 0o022, "unsafe storage directory")
    return info


def _same(left, right):
    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _parts(relative):
    _require(isinstance(relative, str) and relative and not relative.startswith("/")
             and all(p not in ("", ".", "..") for p in relative.split("/")), "invalid relative path")
    return relative.split("/")


def _root_fd(repo):
    binding = _ROOT.get()
    if binding is not None:
        _require(str(repo) == binding[0], "repository binding changed")
        fd = os.dup(binding[1])
    else:
        fd = os.open(repo, _DIR_FLAGS)
    try:
        info = _safe_dir(fd)
        _require(_same(info, os.stat(repo, follow_symlinks=False)), "repository path rebound")
        return fd
    except BaseException:
        os.close(fd)
        raise


def _pinned(function):
    @wraps(function)
    def call(repo_root):
        if function.__name__ == "apply":
            from _lib import _trusted_control_writer
            _require(_trusted_control_writer(), "apply requires the WBS MCP owner")
        repo = Path(repo_root).absolute()
        fd = _root_fd(repo)
        token = _ROOT.set((str(repo), fd))
        try:
            return function(repo)
        finally:
            _ROOT.reset(token)
            os.close(fd)
    return call


@contextmanager
def _directory(repo, parts, *, create=False):
    root = _root_fd(repo)
    chain, opened = [], [root]
    def bound():
        try:
            _require(_same(_safe_dir(root), os.stat(repo, follow_symlinks=False)), "repository path rebound")
            for parent, name, child in chain:
                _require(_same(_safe_dir(child), os.stat(name, dir_fd=parent, follow_symlinks=False)),
                         "directory binding changed")
        except FileNotFoundError as exc:
            raise ValueError("Goal migration: directory binding disappeared") from exc
    try:
        for name in parts:
            bound()
            parent = opened[-1]
            try:
                child = os.open(name, _DIR_FLAGS, dir_fd=parent)
            except FileNotFoundError:
                if not create:
                    raise
                os.mkdir(name, 0o700, dir_fd=parent)
                os.fsync(parent)
                child = os.open(name, _DIR_FLAGS, dir_fd=parent)
            opened.append(child)
            chain.append((parent, name, child))
            bound()
        yield opened[-1], bound
        bound()
    except OSError as exc:
        if isinstance(exc, FileNotFoundError):
            raise
        raise ValueError("Goal migration: unsafe storage path") from exc
    finally:
        for fd in reversed(opened):
            os.close(fd)


def _path(repo, relative):
    # Path is only a selector for non-I/O callers. All storage operations below
    # independently use no-follow descriptors, never this returned path.
    parts = _parts(relative)
    try:
        with _directory(repo, parts[:-1]) as (parent, bound):
            try:
                info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
                _require(not stat.S_ISLNK(info.st_mode), "symlink path refused: " + relative)
            except FileNotFoundError:
                pass
            bound()
    except FileNotFoundError:
        pass
    return repo.joinpath(*parts)


def _publication_stage(name):
    return ".migration-" + hashlib.sha256(os.fsencode(name)).hexdigest()[:32]


def _read_at(parent, name, relative, limit, *, publication=False):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=parent)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(fd)
        paired = publication and info.st_nlink == 2
        if paired:
            try:
                alias = os.stat(_publication_stage(name), dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ValueError("Goal migration: unaccounted publication links: " + relative) from exc
            _require(_same(info, alias) and stat.S_ISREG(alias.st_mode),
                     "unaccounted publication links: " + relative)
        _require(stat.S_ISREG(info.st_mode) and (info.st_nlink == 1 or paired) and info.st_uid == os.getuid()
                 and not stat.S_IMODE(info.st_mode) & 0o022 and info.st_size <= limit,
                 "unsafe or oversized file: " + relative)
        data = b""
        while len(data) <= limit:
            block = os.read(fd, min(65536, limit + 1 - len(data)))
            if not block:
                break
            data += block
        after = os.fstat(fd)
        _require(len(data) <= limit and (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns, info.st_nlink)
                 == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink), "file changed while reading")
        _require(_same(info, os.stat(name, dir_fd=parent, follow_symlinks=False)), "file replaced while reading")
        if paired:
            try:
                alias_after = os.stat(_publication_stage(name), dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError as exc:
                raise ValueError("Goal migration: publication alias disappeared") from exc
            _require(_same(after, alias_after) and alias_after.st_nlink == 2,
                     "publication alias changed while reading")
        return data
    finally:
        os.close(fd)


def _read(repo, relative, limit=LIMIT, *, publication=False):
    parts = _parts(relative)
    try:
        with _directory(repo, parts[:-1]) as (parent, bound):
            data = _read_at(parent, parts[-1], relative, limit, publication=publication)
            bound()
            return data
    except FileNotFoundError:
        return None


def _read_publication(repo, relative, limit=LIMIT):
    return _read(repo, relative, limit, publication=True)


def _normalize_at(parent, name, relative, expected, *, stage_expected=_UNSPECIFIED):
    """Remove only the exact validated publication alias, never unknown links."""
    current = _read_at(parent, name, relative, 16 * LIMIT, publication=True)
    _require(current == expected, "publication bytes changed: " + relative)
    stage = _publication_stage(name)
    try:
        alias = os.stat(stage, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        # A previous process may have removed its alias but died before the
        # directory sync. Observed bytes alone do not make publication durable.
        if current is not None:
            os.fsync(parent)
        return
    if alias.st_nlink == 2:
        target = os.stat(name, dir_fd=parent, follow_symlinks=False)
        _require(_same(alias, target) and target.st_nlink == 2,
                 "unaccounted publication links: " + relative)
    else:
        # A pre-link crash leaves one private stage. Its requested bytes must
        # agree before it can be removed and recreated; sources stay strict.
        staged = _read_at(parent, stage, relative, 16 * LIMIT)
        _require(staged == (expected if stage_expected is _UNSPECIFIED else stage_expected),
                 "conflicting publication stage: " + relative)
        _require(current is None or current != staged,
                 "unrelated publication stage inode: " + relative)
    alias_after = os.stat(stage, dir_fd=parent, follow_symlinks=False)
    _require(_same(alias, alias_after) and
             (alias.st_nlink, alias.st_size, alias.st_mtime_ns, alias.st_ctime_ns) ==
             (alias_after.st_nlink, alias_after.st_size, alias_after.st_mtime_ns, alias_after.st_ctime_ns),
             "publication alias replaced")
    os.unlink(stage, dir_fd=parent)
    os.fsync(parent)


def _normalize_publication(repo, relative, expected, *, stage_expected=_UNSPECIFIED):
    parts = _parts(relative)
    with _directory(repo, parts[:-1]) as (parent, bound):
        bound()
        _normalize_at(parent, parts[-1], relative, expected, stage_expected=stage_expected)
        bound()



def _check_originals(repo, sources, destinations):
    """Validate interrupted pre-manifest archives without changing preview."""
    originals = dict(sources)
    originals.update({p: before for p, (before, _) in destinations.items() if before is not None})
    for relative, expected in originals.items():
        archive = ARCHIVE + "/original/" + relative
        parts = _parts(archive)
        try:
            with _directory(repo, parts[:-1]) as (parent, bound):
                current = _read_at(parent, parts[-1], archive, LIMIT, publication=True)
                _require(current is None or current == expected, "conflicting original archive")
                stage = _publication_stage(parts[-1])
                try:
                    alias = os.stat(stage, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    alias = None
                if alias is not None and alias.st_nlink != 2:
                    _require(current is None, "unrelated publication stage inode: " + archive)
                    _require(_read_at(parent, stage, archive, LIMIT) == expected,
                             "conflicting publication stage: " + archive)
                elif alias is not None:
                    _require(current is not None and _same(alias, os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)),
                             "unaccounted publication links: " + archive)
                bound()
        except FileNotFoundError:
            pass


def _rename_new(parent, temporary, destination):
    """Atomic no-replace rename on the supported Linux/macOS runtimes."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = getattr(libc, "renameat2", None)
    flag = 1  # RENAME_NOREPLACE
    if rename is None:
        rename = getattr(libc, "renameatx_np", None)
        flag = 4  # RENAME_EXCL on macOS
    if rename is None:
        _link_new(parent, temporary, destination)
        return
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(parent, os.fsencode(temporary), parent, os.fsencode(destination), flag) != 0:
        error = ctypes.get_errno()
        if error == errno.EEXIST:
            raise FileExistsError(error, os.strerror(error), destination)
        if error in {errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP}:
            _link_new(parent, temporary, destination)
            return
        raise OSError(error, os.strerror(error), destination)


def _link_new(parent, temporary, destination):
    try:
        os.link(temporary, destination, src_dir_fd=parent, dst_dir_fd=parent,
                follow_symlinks=False)
    except FileExistsError:
        raise
    except OSError as exc:
        raise ValueError("Goal migration: atomic no-replace hardlink publication failed: "
                         + str(exc)) from exc
    # A crash before unlink is recognized solely by this exact same-parent pair.
    os.unlink(temporary, dir_fd=parent)


def _write(repo, relative, data, *, expected=_UNSPECIFIED):
    parts = _parts(relative)
    with _directory(repo, parts[:-1], create=True) as (parent, bound):
        before = _read_at(parent, parts[-1], relative, 16 * LIMIT, publication=True)
        if expected is not _UNSPECIFIED:
            _require(_hash(before) == expected, "destination changed after preparation")
        temporary = _publication_stage(parts[-1])
        # Existing visible bytes may be the result of an interrupted link.
        _normalize_at(parent, parts[-1], relative, before, stage_expected=data)
        fd = None
        try:
            bound()
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=parent)
            view = memoryview(data)
            while view:
                written = os.write(fd, view)
                _require(written > 0, "short migration write")
                view = view[written:]
            os.fsync(fd)
            bound()
            _require(_read_at(parent, parts[-1], relative, 16 * LIMIT) == before,
                     "destination changed before publication")
            if before is None:
                try:
                    _rename_new(parent, temporary, parts[-1])
                except FileExistsError as exc:
                    raise ValueError("Goal migration: destination changed before publication") from exc
            else:
                os.replace(temporary, parts[-1], src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
            bound()
        finally:
            if fd is not None:
                try:
                    try:
                        alias = os.stat(temporary, dir_fd=parent, follow_symlinks=False)
                    except FileNotFoundError:
                        alias = None
                    # v9fs can resolve fstat through the vanished staging name
                    # after publication. No cleanup or inode proof is needed
                    # when that name is already absent.
                    if alias is not None:
                        owned = os.fstat(fd)
                        if owned.st_nlink == 1 and _same(owned, alias):
                            os.unlink(temporary, dir_fd=parent)
                finally:
                    os.close(fd)


def _retire(repo, relative, expected):
    if _read(repo, relative) is None:
        return
    parts = _parts(relative)
    with _directory(repo, parts[:-1]) as (parent, bound):
        current = _read_at(parent, parts[-1], relative, LIMIT)
        if current is not None:
            _require(_hash(current) == expected, "source changed before retirement")
            bound()
            os.unlink(parts[-1], dir_fd=parent)
            os.fsync(parent)
            bound()


def _remove_empty(repo, relative):
    parts = _parts(relative)
    with _directory(repo, parts[:-1]) as (parent, bound):
        try:
            child = os.open(parts[-1], _DIR_FLAGS, dir_fd=parent)
        except FileNotFoundError:
            return
        try:
            info = _safe_dir(child)
            _require(not os.listdir(child), "legacy directory acquired new files")
            bound()
            _require(_same(info, os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)),
                     "legacy directory binding changed")
            os.rmdir(parts[-1], dir_fd=parent)
            os.fsync(parent)
            bound()
        finally:
            os.close(child)


def _entries(repo, relative):
    """Bounded no-follow directory enumeration; absent directory returns None."""
    try:
        with _directory(repo, _parts(relative)) as (directory, bound):
            names = os.listdir(directory)
            _require(len(names) <= 4096, "too many directory entries")
            entries = [(name, os.stat(name, dir_fd=directory, follow_symlinks=False)) for name in sorted(names)]
            bound()
            return entries
    except FileNotFoundError:
        return None


def _control(repo, relative):
    from _lib import _validate_task_control
    raw = _read(repo, relative)
    return _migration_control(_json(raw)) if raw is not None else {}


def _open_lock(repo, relative, *, directory=False):
    parts = _parts(relative)
    fd = None
    try:
        with _directory(repo, parts[:-1], create=not directory) as (parent, bound):
            flags = _DIR_FLAGS if directory else os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
            fd = os.open(parts[-1], flags, 0o600, dir_fd=parent)
            info = os.fstat(fd)
            _require(info.st_uid == os.getuid() and not stat.S_IMODE(info.st_mode) & 0o022
                     and (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode) and info.st_nlink == 1),
                     "unsafe migration lock")
            bound()
            _require(_same(info, os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)), "lock binding changed")
        return fd
    except BaseException:
        if fd is not None:
            os.close(fd)
        raise


def _legacy_spec(goal_id, task_id, value):
    import batch_state as batch
    _require(isinstance(value, dict) and set(value) == {"batch_id", "requests"}, "invalid legacy pack")
    requests = value["requests"]
    try:
        batch.requests_valid(requests)
    except batch.Refusal as exc:
        raise ValueError("Goal migration: invalid legacy intake: " + str(exc)) from exc
    _require(len(requests) >= 2 and all(set(item) <= {"slug", "request", "scopes", "depends_on", "submodules"}
             for item in requests), "invalid legacy intake")
    normalized = copy.deepcopy(batch.normalize_requests(requests))
    identity = json.dumps([goal_id, task_id], separators=(",", ":")).encode()
    expected = {"batch_id": "goal-" + hashlib.sha256(identity).hexdigest(), "requests": normalized}
    _require(value == expected and all("TASK__" + r["slug"] != task_id for r in normalized),
             "legacy pack identity or intake mismatch")
    return dict(task_id=task_id, **expected)


def _binding(repo, goal_id, task_id, spec, recoveries, destination, *, require_pool):
    import work_pack
    _require(isinstance(goal_id, str) and IDENT.fullmatch(goal_id) and goal_id.startswith("GOAL__"),
             "invalid legacy Goal identity")
    result = _legacy_spec(goal_id, task_id, spec)
    import batch_state as batch
    relative = "doc/harness/runtime/batches/" + result["batch_id"] + ".json"
    raw = _read(repo, relative)
    if raw is None:
        route = dict(batch_state="missing", next_action="Initialize this exact intake through batch_state with --owner-task-id " + task_id + ".")
    else:
        data = batch.validate(_json(raw), str(repo), result["batch_id"])
        _require(data.get("owner_task_id") in (None, task_id), "conflicting pool owner")
        staged = dict(data, owner_task_id=task_id)
        try:
            route = work_pack.inspect_pool(str(repo), task_id, result, staged)
        except batch.Refusal as exc:
            raise ValueError("Goal migration: invalid pool proof: " + str(exc)) from exc
        if staged != data:
            destination(relative, raw, _encode(staged))
    recovery = dict(wbs_id="WBS__" + task_id[6:], **result,
                    next_action=route.get("next_action", "Resume the retained pool through batch_state."))
    if route.get("batch_state") == "missing" and require_pool:
        recovery["blocker"] = "Initialize this exact batch_id and requests through batch_state, then retry migration."
    recoveries.append(recovery)
    return result


def _collect(repo):
    from _lib import native_wbs_id
    sources, destinations, recoveries = {}, {}, []
    goal_entries = _entries(repo, GOALS)
    rows = {}
    if goal_entries is not None:
        names = [name for name, _ in goal_entries]
        _require(len(names) <= MAX_RECORDS + 2, "too many Goal records")
        for name in names:
            _require(name == ".lock" or name == "current.json" or re.fullmatch(r"GOAL__[A-Za-z0-9_.-]{1,180}\.json", name),
                     "unknown Goal directory entry: " + name)
            relative = GOALS + "/" + name
            raw = _read(repo, relative)
            _require(raw is not None, "Goal record disappeared")
            if name == ".lock":
                _require(not raw, "unexpected Goal lock contents")
                sources[relative] = raw
                continue
            data = _json(raw)
            _require(isinstance(data, dict) and isinstance(data.get("goal_id"), str)
                     and IDENT.fullmatch(data["goal_id"]) and data["goal_id"].startswith("GOAL__"), "invalid Goal record")
            _require(isinstance(data.get("objective"), str) and data["objective"].strip()
                     and isinstance(data.get("tasks"), list), "invalid Goal objective/intake")
            _require(name == "current.json" or name == data["goal_id"] + ".json", "Goal filename identity mismatch")
            sources[relative] = raw
            if name != "current.json":
                rows[data["goal_id"]] = data
        current = sources.get(GOALS + "/current.json")
        if current is not None:
            value = _json(current)
            _require(sources.get(GOALS + "/" + value["goal_id"] + ".json") == current, "current/named Goal conflict")
    reserved = {}
    notes = {}
    def destination(relative, before, after):
        _require(len(after) <= LIMIT, "migration destination exceeds storage budget")
        _require(relative not in destinations or destinations[relative] == (before, after), "conflicting migration target")
        destinations[relative] = (before, after)
    for gid, row in sorted(rows.items()):
        wbs_id = native_wbs_id(row["objective"])
        target = "doc/harness/tasks/TASK__" + wbs_id[5:] + "/MIGRATION.md"
        notes.setdefault(target, []).append(row)
        children = set()
        for child in row["tasks"]:
            _require(isinstance(child, dict) and isinstance(child.get("task_id"), str)
                     and IDENT.fullmatch(child["task_id"]) and child["task_id"].startswith("TASK__"), "invalid child identity")
            tid = child["task_id"]
            _require(tid not in children, "duplicate child identity")
            children.add(tid)
            directory = "doc/harness/tasks/" + tid
            _require(not child.get("task_dir") or child["task_dir"] in (directory, str(repo / directory)), "noncanonical child directory")
            _path(repo, directory)
            if "batch" not in child:
                continue
            spec = _binding(repo, gid, tid, child["batch"], recoveries, destination, require_pool=True)
            for identity in [tid] + ["TASK__" + item["slug"] for item in spec["requests"]]:
                _require(identity not in reserved, "duplicate legacy pack reservation")
                reserved[identity] = gid
            relative = directory + "/WBS.json"
            raw = _read(repo, relative)
            if raw is None:
                control = _control(repo, directory + "/TASK.json")
                _require(_read(repo, directory + "/TASK.json") is None or bool(control), "invalid existing task control")
                value = dict(version=1, nodes=[], checks={}, run_id=(control or {}).get("run_id", ""),
                             started_at=0, last_progress_at=0, pack_only=True, batch=spec)
                destination(relative, None, _encode(value))
            else:
                value = _json(raw)
                _require(value.get("batch") == child["batch"] and value.get("batch_goal_id") == gid,
                         "existing WBS conflicts with legacy pack")
    for relative, records in sorted(notes.items()):
        before = _read(repo, relative)
        _require(before is None, "migration notes target already exists")
        text = ("# Imported historical Goal intake\n\n"
                "These records are unverified historical intent, not confirmed user requirements or completion evidence. "
                "They do not create executable work, a WBS baseline, or measured progress.\n\n```json\n"
                + json.dumps(records, ensure_ascii=False, sort_keys=True, indent=2) + "\n```\n")
        destination(relative, before, text.encode())
    entries = _entries(repo, "doc/harness/tasks")
    if entries is not None:
        for name, info in entries:
            if not name.startswith("TASK__"):
                continue
            _require(IDENT.fullmatch(name), "invalid task directory")
            if stat.S_ISLNK(info.st_mode):
                continue  # Unrelated symlink directories never enter migration ownership.
            if not stat.S_ISDIR(info.st_mode):
                continue
            relative = "doc/harness/tasks/" + name + "/WBS.json"
            raw = _read(repo, relative)
            if raw is None:
                continue
            value = _json(raw)
            _require(isinstance(value, dict), "invalid WBS state")
            from wbs import validate_state
            validate_state(value)
            changed = False
            _require(isinstance(value.get("batch_history", []), list), "invalid WBS batch history")
            containers = [value] + value.get("batch_history", [])
            _require(len(containers) <= 33,
                     "invalid WBS batch history")
            for container in containers:
                _require(isinstance(container, dict), "invalid WBS pack binding")
                if "batch_goal_id" in container:
                    container["batch"] = _binding(repo, container["batch_goal_id"], name,
                                                 container.get("batch"), recoveries, destination, require_pool=False)
                    del container["batch_goal_id"]
                    changed = True
            if changed:
                destination(relative, raw, _encode(value))
    _require(len(sources) + len(destinations) <= MAX_RECORDS * 4, "migration exceeds record budget")
    return sources, destinations, recoveries


def _report(sources, destinations, recoveries, status):
    return dict(status=status, sources=sorted(sources), destinations=sorted(destinations),
                recoveries=recoveries, blockers=[r for r in recoveries if "blocker" in r],
                next_action="Initialize blocked exact intakes, then retry migration." if any("blocker" in r for r in recoveries)
                else "Apply this explicit migration." if status == "preview" else "Use the WBS control plane.")


def _manifest(repo):
    raw = _read_publication(repo, MANIFEST, 16 * LIMIT)
    if raw is None:
        return None
    data = _json(raw)
    _require(isinstance(data, dict) and data.get("version") == 1 and data.get("status") in {"prepared", "complete"}
             and isinstance(data.get("sources"), dict) and isinstance(data.get("destinations"), dict)
             and isinstance(data.get("recoveries"), list), "invalid migration manifest")
    _require(len(data["sources"]) + len(data["destinations"]) <= MAX_RECORDS * 4, "oversized migration manifest")
    for relative, record in data["sources"].items():
        _require(relative.startswith(GOALS + "/"), "invalid archived source")
        _require(isinstance(record, dict) and set(record) == {"sha256"}
                 and isinstance(record["sha256"], str) and re.fullmatch(r"[0-9a-f]{64}", record["sha256"]), "invalid source manifest")
        original = _read_publication(repo, ARCHIVE + "/original/" + relative)
        _require(original is not None and _hash(original) == record["sha256"], "archive changed or missing")
        current = _read(repo, relative)
        _require(current is None or _hash(current) == record["sha256"], "legacy source changed after preparation")
        if data["status"] == "complete":
            _require(current is None, "retired source recreated; explicit reconciliation required")
    for relative, record in data["destinations"].items():
        _require(re.fullmatch(r"(?:doc/harness/tasks/TASK__[A-Za-z0-9_.-]{1,180}/(?:WBS.json|MIGRATION.md)|doc/harness/runtime/batches/[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\.json)", relative),
                 "invalid migration destination")
        _require(isinstance(record, dict) and set(record) == {"before", "after", "data"}
                 and isinstance(record["after"], str) and re.fullmatch(r"[0-9a-f]{64}", record["after"])
                 and (record["before"] is None or isinstance(record["before"], str)
                      and re.fullmatch(r"[0-9a-f]{64}", record["before"])), "invalid destination manifest")
        try:
            after = base64.b64decode(record["data"], validate=True)
        except (ValueError, TypeError) as exc:
            raise ValueError("Goal migration: invalid archived destination") from exc
        _require(len(after) <= LIMIT and _hash(after) == record["after"], "destination archive changed")
        _require(_hash(_read_publication(repo, relative)) in ({record["after"]} if data["status"] == "complete" else {record["before"], record["after"]}), "destination changed after preparation")
        original = _read_publication(repo, ARCHIVE + "/original/" + relative)
        _require(_hash(original) == record["before"], "destination original archive changed")
    entries = _entries(repo, GOALS)
    if entries is not None:
        _require({GOALS + "/" + name for name, _ in entries} <= data["sources"].keys(),
                 "new legacy source after preparation")
    return data


@_pinned
def _preview_goals(repo_root):
    repo = Path(repo_root).resolve(strict=True)
    journal = _manifest(repo)
    if journal:
        return _report(journal["sources"], journal["destinations"], journal["recoveries"], journal["status"])
    sources, destinations, recoveries = _collect(repo)
    _check_originals(repo, sources, destinations)
    return _report(sources, destinations, recoveries, "preview")


@_pinned
def _apply_goals(repo_root):
    repo = Path(repo_root).resolve(strict=True)
    # Refuse all bad inputs before creating archive/lock state.
    initial = _manifest(repo)
    if initial is None:
        sources, destinations, recoveries = _collect(repo)
        _check_originals(repo, sources, destinations)
        report = _report(sources, destinations, recoveries, "preview")
        _require(not report["blockers"], "missing legacy pool; preview returns exact initialization intake")
        if not sources and not destinations:
            return dict(report, status="complete")
    fd = _open_lock(repo, ARCHIVE + "/.lock")
    goal_fd = None
    batch_fd = None
    task_locks = ExitStack()
    try:
        _require(stat.S_ISREG(os.fstat(fd).st_mode) and os.fstat(fd).st_nlink == 1, "unsafe migration lock")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            goal_fd = _open_lock(repo, GOALS, directory=True)
        except FileNotFoundError:
            pass
        if goal_fd is not None:
            try:
                fcntl.flock(goal_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Goal migration: legacy writer active; retry after it exits") from exc
        planned_destinations = initial["destinations"] if initial is not None else destinations
        if any(p.startswith("doc/harness/runtime/batches/") for p in planned_destinations):
            batch_fd = _open_lock(repo, "doc/harness/runtime/batches/.lock")
            try:
                fcntl.flock(batch_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ValueError("Goal migration: batch writer active; retry after it exits") from exc
        # WBS writers use the same task-directory flock for their task-state
        # transaction. Hold it across hash recheck and metadata replacement.
        for relative in sorted(planned_destinations):
            if relative.endswith("/WBS.json"):
                task_fd, _ = task_locks.enter_context(_directory(repo, _parts(relative)[:-1], create=True))
                try:
                    fcntl.flock(task_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise ValueError("Goal migration: WBS writer active; retry after it exits") from exc
        journal = _manifest(repo)
        if journal is None:
            current = _collect(repo)
            _require(current == (sources, destinations, recoveries), "inputs changed before preparation")
            for relative, raw in sources.items():
                archive = ARCHIVE + "/original/" + relative
                existing = _read_publication(repo, archive)
                _require(existing is None or existing == raw, "conflicting original archive")
                if existing is None:
                    _write(repo, archive, raw)
                else:
                    _normalize_publication(repo, archive, raw)
            for relative, (before, _) in destinations.items():
                archive = ARCHIVE + "/original/" + relative
                existing = _read_publication(repo, archive)
                _require(existing is None or existing == before, "conflicting destination archive")
                if before is not None:
                    if existing is None:
                        _write(repo, archive, before)
                    else:
                        _normalize_publication(repo, archive, before)
            journal = dict(version=1, status="prepared", recoveries=recoveries,
                           sources={p: {"sha256": _hash(raw)} for p, raw in sources.items()},
                           destinations={p: dict(before=_hash(before), after=_hash(after),
                                                  data=base64.b64encode(after).decode())
                                         for p, (before, after) in destinations.items()})
            _require(len(_encode(journal)) <= 16 * LIMIT, "migration manifest exceeds budget")
            _write(repo, MANIFEST, _encode(journal))
        # _manifest has validated semantics and archive hashes. Normalize all
        # exact publication pairs before either completion or source retirement.
        for relative in journal["sources"]:
            archive = ARCHIVE + "/original/" + relative
            original = _read_publication(repo, archive)
            _require(_hash(original) == journal["sources"][relative]["sha256"], "archive changed")
            _normalize_publication(repo, archive, original)
        for relative, record in journal["destinations"].items():
            archive = ARCHIVE + "/original/" + relative
            original = _read_publication(repo, archive)
            _require(_hash(original) == record["before"], "destination original archive changed")
            if original is not None:
                _normalize_publication(repo, archive, original)
            current = _read_publication(repo, relative)
            _require(_hash(current) in ({record["after"]} if journal["status"] == "complete"
                                       else {record["before"], record["after"]}), "destination changed")
            if current is not None:
                _normalize_publication(repo, relative, current,
                                       stage_expected=base64.b64decode(record["data"], validate=True))
        manifest_raw = _read_publication(repo, MANIFEST, 16 * LIMIT)
        _require(_json(manifest_raw) == journal, "manifest changed")
        _normalize_publication(repo, MANIFEST, manifest_raw,
                               stage_expected=_encode(dict(journal, status="complete")))
        if journal["status"] == "complete":
            return _report(journal["sources"], journal["destinations"], journal["recoveries"], "complete")
        # Recheck the whole transaction before each stage; replay accepts only
        # recorded before/after hashes, never overwrites subsequent edits.
        _manifest(repo)
        for relative, record in journal["destinations"].items():
            _require(_hash(_read(repo, relative)) in {record["before"], record["after"]}, "destination changed")
            if _hash(_read(repo, relative)) != record["after"]:
                _write(repo, relative, base64.b64decode(record["data"], validate=True), expected=record["before"])
        _manifest(repo)
        for relative, record in journal["sources"].items():
            _retire(repo, relative, record["sha256"])
        _remove_empty(repo, GOALS)
        journal["status"] = "complete"
        _write(repo, MANIFEST, _encode(journal))
        return _report(journal["sources"], journal["destinations"], journal["recoveries"], "complete")
    finally:
        try:
            task_locks.close()
        finally:
            if batch_fd is not None:
                os.close(batch_fd)
            if goal_fd is not None:
                os.close(goal_fd)
            os.close(fd)


CONTROL_ARCHIVE = "doc/harness/archive/task-control-migration"
CONTROL_MANIFEST = CONTROL_ARCHIVE + "/manifest.json"


def _migration_control(value):
    """Only explicit migration accepts the retired control schema."""
    from _lib import _validate_task_control
    current = _validate_task_control(value)
    if current:
        return current
    if not isinstance(value, dict) or set(value) != {"run_id", "execution_mode", "required_lenses", "close_receipt_fingerprint"}:
        return {}
    fingerprint = value["close_receipt_fingerprint"]
    if fingerprint is not None and (not isinstance(fingerprint, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint)):
        return {}
    return _validate_task_control({key: val for key, val in value.items() if key != "close_receipt_fingerprint"}
                                  | {"closed": fingerprint is not None})


def _historical_control(value):
    """Recognize pre-UUIDv7 history without granting it lifecycle authority."""
    from datetime import datetime
    from _lib import _task_control_lenses
    fields = {"task_run_id", "started_at", "execution_mode", "review_lenses",
              "qa_lenses", "close_receipt_fingerprint"}
    if not isinstance(value, dict) or set(value) != fields:
        return False
    if (not isinstance(value["task_run_id"], str)
            or not re.fullmatch(r"[0-9a-f]{32}", value["task_run_id"])
            or value["execution_mode"] not in ("standard", "micro")):
        return False
    started = value["started_at"]
    if not isinstance(started, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z", started):
        return False
    try:
        datetime.fromisoformat(started)
    except ValueError:
        return False
    review, qa = value["review_lenses"], value["qa_lenses"]
    if (not isinstance(review, list) or not isinstance(qa, list)
            or any(not isinstance(lens, str) or not lens.startswith("review-") for lens in review)
            or any(not isinstance(lens, str) or not lens.startswith("qa-") for lens in qa)
            or _task_control_lenses(review + qa) is None):
        return False
    fingerprint = value["close_receipt_fingerprint"]
    return fingerprint is None or isinstance(fingerprint, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint) is not None


def _control_json(raw, path):
    try:
        return _json(raw)
    except ValueError as exc:
        raise ValueError(str(exc) + ": " + str(path)) from exc


def _control_bindings(repo):
    """Inspect raw bindings: normal readers deliberately reject old runs."""
    references = set()
    legacy = _read(repo, "doc/harness/tasks/.active", 4096)
    if legacy and legacy.strip():
        first = legacy.decode("utf-8").strip().splitlines()[0]
        references.add(Path(first.rstrip("/")).name)
    for name, _ in _entries(repo, "doc/harness/tasks/.active_sessions") or []:
        if not name.endswith(".json"):
            continue
        value = _json(_read(repo, "doc/harness/tasks/.active_sessions/" + name))
        _require(isinstance(value, dict), "invalid active session binding: " + name)
        # Conflict fences also retain task identities and must not be ignored.
        conflicts = value.get("conflicts", [])
        _require(isinstance(conflicts, list) and not value.get("conflicts_overflow"),
                 "unresolved active session conflict: " + name)
        for binding in [value] + conflicts:
            _require(isinstance(binding, dict), "invalid active session conflict: " + name)
            for key in ("task_id", "task_dir"):
                if isinstance(binding.get(key), str):
                    references.add(Path(binding[key].rstrip("/")).name)
    return references


def _without_close_fingerprint(data):
    """Convert only retired pool fields, preserving run and Git/archive proof."""
    value = copy.deepcopy(data)
    for item in value.get("requests", []):
        if "close_fingerprint" in item:
            fingerprint = item.pop("close_fingerprint")
            _require(fingerprint is None or isinstance(fingerprint, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint), "invalid retired pool close fingerprint")
            _require("closed" not in item or item["closed"] == (fingerprint is not None), "conflicting pool closure")
            item["closed"] = fingerprint is not None
        checkpoint = item.get("checkpoint")
        if isinstance(checkpoint, dict) and "close_fingerprint" in checkpoint:
            fingerprint = checkpoint.pop("close_fingerprint")
            _require(isinstance(fingerprint, str) and re.fullmatch(r"sha256:[0-9a-f]{64}", fingerprint), "invalid retired checkpoint close fingerprint")
            checkpoint["closed"] = True
    return value


@contextmanager
def _control_workspace(repo, root):
    """Use the existing descriptor-bound migration I/O for registered worktrees."""
    from _lib import resolve_registered_worktree
    root = Path(root)
    if root != repo:
        _require(resolve_registered_worktree(str(repo), str(root)) == str(root), "unregistered migration worktree")
    if root == repo:
        yield root
        return
    token = _ROOT.set(None)
    fd = None
    try:
        fd = _root_fd(root)
        bound = _ROOT.set((str(root), fd))
        try:
            yield root
        finally:
            _ROOT.reset(bound)
    finally:
        if fd is not None:
            os.close(fd)
        _ROOT.reset(token)


def _control_read(repo, entry):
    with _control_workspace(repo, entry["root"]) as root:
        return _read_publication(root, entry["path"], 16 * LIMIT)


def _control_write(repo, entry, data, expected):
    with _control_workspace(repo, entry["root"]) as root:
        _write(root, entry["path"], data, expected=expected)


def _control_changes(repo):
    """Prevalidate whole archive custody before planning any converted byte."""
    import batch_state as batch
    changes, archives, pools, retained = {}, {}, [], []
    pool_tasks = set()
    roots = {str(repo)}
    for name, info in _entries(repo, "doc/harness/runtime/batches") or []:
        if name == ".lock":
            continue
        _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\.json", name) and stat.S_ISREG(info.st_mode), "unsafe pool migration input")
        path = "doc/harness/runtime/batches/" + name
        raw = _read(repo, path)
        original = _json(raw)
        value = _without_close_fingerprint(original)
        batch.validate(value, str(repo), name[:-5])
        pools.append((path, raw, value))
        for item in value["requests"]:
            pool_tasks.add(item["task_id"])
            worktree = item.get("worktree")
            if worktree and os.path.isdir(worktree):
                from _lib import resolve_registered_worktree
                _require(resolve_registered_worktree(str(repo), worktree) == worktree, "unregistered pool worktree")
                roots.add(worktree)
            checkpoint = item.get("checkpoint") or {}
            if checkpoint.get("archive_fingerprint"):
                archive = str(repo / ("doc/harness/archive/batch/" + item["task_id"]))
                _require((checkpoint.get("harvest") or {}).get("archived") == archive, "archive identity mismatch")
                # This generic custody check includes historical RECEIPTS/REVIEWS bytes.
                expected = checkpoint["archive_fingerprint"]
                if archive in archives:
                    _require(archives[archive][0] == expected, "conflicting archive proof")
                else:
                    entries = batch._fingerprint_entries(archive)
                    observed = "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
                    _require(observed == expected, "preexisting archive fingerprint drift")
                    archives[archive] = (expected, entries)
    def add(root, path, before, after):
        if before != after:
            changes[(str(root), path)] = dict(root=str(root), path=path,
                before=base64.b64encode(before).decode(), after=base64.b64encode(after).decode())
    for root in sorted(roots):
        with _control_workspace(repo, root) as source:
            references = pool_tasks | _control_bindings(source)
            for name, info in _entries(source, "doc/harness/tasks") or []:
                if not name.startswith("TASK__") or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    continue
                _require(IDENT.fullmatch(name), "invalid task identity")
                path = "doc/harness/tasks/" + name + "/TASK.json"
                raw = _read(source, path)
                if raw is None:
                    continue
                original = _control_json(raw, source / path)
                value = _migration_control(original)
                if not value and _historical_control(original) and name not in references:
                    retained.append(dict(path=str(source / path), reason="pre-UUIDv7 historical control retained; unavailable to current lifecycle"))
                    continue
                _require(value, "invalid or referenced historical task control during explicit migration: " + str(source / path))
                if "close_receipt_fingerprint" in original:
                    add(source, path, raw, _encode(value))
    # Archived controls are format consumers too; preserve every other archived byte.
    for name, info in _entries(repo, "doc/harness/archive/batch") or []:
        if not name.startswith("TASK__") or stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            continue
        path = "doc/harness/archive/batch/" + name + "/TASK.json"
        raw = _read(repo, path)
        if raw is None:
            continue
        value = _migration_control(_control_json(raw, repo / path))
        _require(value, "invalid archived task control: " + str(repo / path))
        if "close_receipt_fingerprint" in _json(raw):
            after = _encode(value)
            add(repo, path, raw, after)
            archive = str(repo / ("doc/harness/archive/batch/" + name))
            if archive in archives:
                archives[archive][1]["TASK.json"] = _hash(after)
    archive_rows = []
    for archive, (before, entries) in sorted(archives.items()):
        after = "sha256:" + hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        archive_rows.append(dict(path=archive, before=before, after=after))
    hashes = {row["path"]: row["after"] for row in archive_rows}
    for path, raw, value in pools:
        for item in value["requests"]:
            checkpoint = item.get("checkpoint") or {}
            if checkpoint.get("archive_fingerprint"):
                checkpoint["archive_fingerprint"] = hashes[checkpoint["harvest"]["archived"]]
        if _json(raw) != value:
            add(repo, path, raw, _encode(value))
    # Task controls precede pools so a retry must understand the mixed journal state.
    result = dict(version=1, status="prepared", destinations=sorted(changes.values(), key=lambda row: (row["path"].endswith(".json") and "/runtime/batches/" in row["path"], row["root"], row["path"])), archives=archive_rows)
    if retained:
        result["retained_historical_controls"] = retained
    return result


def _control_journal(repo):
    raw = _read_publication(repo, CONTROL_MANIFEST, 16 * LIMIT)
    if raw is None:
        return None
    value = _json(raw)
    _require(isinstance(value, dict) and set(value) - {"retained_historical_controls"} == {"version", "status", "destinations", "archives"}
             and value["version"] == 1 and value["status"] in {"prepared", "complete"}
             and isinstance(value["destinations"], list) and len(value["destinations"]) <= MAX_RECORDS * 4
             and isinstance(value["archives"], list), "invalid task-control migration journal")
    retained = value.get("retained_historical_controls", [])
    _require(isinstance(retained, list) and len(retained) <= MAX_RECORDS * 4
             and all(isinstance(row, dict) and set(row) == {"path", "reason"}
                     and isinstance(row["path"], str) and os.path.isabs(row["path"])
                     and str(Path(row["path"])) == row["path"] and ".." not in Path(row["path"]).parts
                     and re.fullmatch(r"TASK__[A-Za-z0-9_.-]{1,180}", Path(row["path"]).parent.name)
                     and Path(row["path"]).name == "TASK.json"
                     and Path(row["path"]).parent.parent.parts[-3:] == ("doc", "harness", "tasks")
                     and row["reason"] == "pre-UUIDv7 historical control retained; unavailable to current lifecycle"
                     for row in retained), "invalid historical control report")
    keys = set()
    for entry in value["destinations"]:
        _require(isinstance(entry, dict) and set(entry) == {"root", "path", "before", "after"}, "invalid control migration entry")
        _require(isinstance(entry["root"], str) and os.path.isabs(entry["root"]), "invalid migration root")
        _require(re.fullmatch(r"doc/harness/(?:tasks/TASK__[A-Za-z0-9_.-]{1,180}/TASK.json|archive/batch/TASK__[A-Za-z0-9_.-]{1,180}/TASK.json|runtime/batches/[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\.json)", entry["path"]), "invalid control migration destination")
        _require((entry["root"], entry["path"]) not in keys, "duplicate migration destination")
        keys.add((entry["root"], entry["path"]))
        decoded = {}
        for field in ("before", "after"):
            try:
                data = base64.b64decode(entry[field], validate=True)
            except (ValueError, TypeError) as exc:
                raise ValueError("Goal migration: invalid task-control journal bytes") from exc
            _require(len(data) <= LIMIT, "oversized migration entry")
            decoded[field] = _json(data)
        before, after = decoded["before"], decoded["after"]
        if entry["path"].endswith("/TASK.json"):
            _require("close_receipt_fingerprint" in before and _migration_control(before) == after,
                     "journal changes task identity or closure")
        else:
            import batch_state as batch
            expected = _without_close_fingerprint(before)
            # Archive hashes may change only by the declared TASK conversion.
            archive_map = {row.get("path"): row for row in value["archives"] if isinstance(row, dict)}
            for item in expected.get("requests", []):
                checkpoint = item.get("checkpoint") or {}
                if checkpoint.get("archive_fingerprint"):
                    archive = archive_map.get((checkpoint.get("harvest") or {}).get("archived"), {})
                    _require(checkpoint["archive_fingerprint"] == archive.get("before"), "journal archive source mismatch")
                    checkpoint["archive_fingerprint"] = archive.get("after")
            _require(expected == after, "journal changes unrelated pool state")
            batch.validate(after, str(repo), Path(entry["path"]).stem)
    for archive in value["archives"]:
        _require(isinstance(archive, dict) and set(archive) == {"path", "before", "after"}
                 and isinstance(archive["path"], str)
                 and Path(archive["path"]).parent == repo / "doc/harness/archive/batch"
                 and IDENT.fullmatch(Path(archive["path"]).name)
                 and all(isinstance(archive[key], str) and re.fullmatch(r"sha256:[0-9a-f]{64}", archive[key]) for key in ("before", "after")),
                 "invalid migration archive proof")
    return value


def _control_report(journal):
    report = dict(status=journal["status"], destinations=[str(Path(row["root"]) / row["path"]) for row in journal["destinations"]])
    if journal.get("retained_historical_controls"):
        report["retained_historical_controls"] = journal["retained_historical_controls"]
    return report


def _check_retained_controls(repo, journal):
    """A prepared journal cannot exempt history that acquired a live reference."""
    retained = journal.get("retained_historical_controls", [])
    if not retained or journal["status"] == "complete":
        return
    import batch_state as batch
    pool_tasks = set()
    for name, _ in _entries(repo, "doc/harness/runtime/batches") or []:
        if name == ".lock":
            continue
        _require(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,179}\.json", name), "unsafe pool migration input")
        value = _without_close_fingerprint(_json(_read(repo, "doc/harness/runtime/batches/" + name)))
        batch.validate(value, str(repo), name[:-5])
        pool_tasks.update(item["task_id"] for item in value["requests"])
    bindings = {}
    for row in retained:
        path = Path(row["path"])
        root = path.parents[4]
        with _control_workspace(repo, root) as source:
            if root not in bindings:
                bindings[root] = _control_bindings(source) | pool_tasks
            raw = _read(source, str(path.relative_to(source)))
            _require(raw is not None and _historical_control(_json(raw))
                     and path.parent.name not in bindings[root],
                     "invalid or referenced historical task control during explicit migration: " + str(path))


def _control_preview(repo):
    journal = _control_journal(repo)
    if journal is not None:
        _check_retained_controls(repo, journal)
        return _control_report(journal)
    value = _control_changes(repo)
    return dict(_control_report(value), status="preview" if value["destinations"] else "complete")


def _control_apply(repo):
    from _lib import active_session_transaction
    # Session focus locks precede task state locks, including migration.
    journal = _control_journal(repo)
    if journal is None:
        journal = _control_changes(repo)
    if journal["status"] == "complete" or not journal["destinations"]:
        return dict(_control_report(journal), status="complete")
    roots = [str(repo)] + sorted({row["root"] for row in journal["destinations"]} - {str(repo)})
    from _lib import resolve_registered_worktree
    for root in roots:
        _require(root == str(repo) or resolve_registered_worktree(str(repo), root) == root, "unregistered migration worktree")
    with ExitStack() as stack:
        for root in roots:
            stack.enter_context(active_session_transaction(root))
        lock = _open_lock(repo, CONTROL_ARCHIVE + "/.lock")
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        batch_lock = _open_lock(repo, "doc/harness/runtime/batches/.lock")
        stack.callback(os.close, batch_lock)
        fcntl.flock(batch_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        for entry in journal["destinations"]:
            if entry["path"].endswith("/TASK.json"):
                with _control_workspace(repo, entry["root"]) as root:
                    fd, _ = stack.enter_context(_directory(root, _parts(entry["path"])[:-1]))
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = _control_journal(repo)
        if current is None:
            _require(journal == _control_changes(repo), "migration inputs changed before preparation")
            _require(len(journal["destinations"]) <= MAX_RECORDS * 4 and len(_encode(journal)) <= 16 * LIMIT,
                     "task-control migration exceeds record/storage budget")
            _write(repo, CONTROL_MANIFEST, _encode(journal))
        else:
            _require(current == journal, "migration journal changed")
        _check_retained_controls(repo, journal)
        # Validate all before/after pairs before any new publication. A prepared
        # journal permits exactly its own intermediate formats, never unrelated drift.
        for entry in journal["destinations"]:
            before = base64.b64decode(entry["before"], validate=True)
            after = base64.b64decode(entry["after"], validate=True)
            observed = _control_read(repo, entry)
            if journal["status"] == "complete":
                # Tasks/pools legitimately evolve after completed migration.
                continue
            _require(observed in (before, after), "task-control migration destination drift")
        if journal["status"] == "complete":
            return _control_report(journal)
        # Archive integrity on retry allows precisely the journal's own TASK
        # conversion, so interruption before pool fingerprint update is safe.
        import batch_state as batch
        for archive in journal["archives"]:
            _require(isinstance(archive, dict) and set(archive) == {"path", "before", "after"}, "invalid migration archive proof")
            observed = batch.fingerprint(archive["path"])
            _require(observed in {archive["before"], archive["after"]}, "archive drift during task-control migration")
        for entry in journal["destinations"]:
            before = base64.b64decode(entry["before"], validate=True)
            after = base64.b64decode(entry["after"], validate=True)
            observed = _control_read(repo, entry)
            if observed == before:
                _control_write(repo, entry, after, _hash(before))
        for archive in journal["archives"]:
            _require(batch.fingerprint(archive["path"]) == archive["after"], "archive changed during task-control migration")
        journal["status"] = "complete"
        _write(repo, CONTROL_MANIFEST, _encode(journal))
        return _control_report(journal)


@_pinned
def preview(repo_root):
    repo = Path(repo_root)
    controls = _control_preview(repo)
    # Legacy Goal inspection may rely on the controls being converted; its own
    # importer accepts both exact forms only within this explicit migration.
    legacy = _preview_goals(repo)
    if legacy["status"] == "complete" and controls["status"] != "complete":
        legacy = dict(legacy, status=controls["status"], next_action="Apply this explicit task-control migration.")
    return dict(legacy, task_control_migration=controls)


@_pinned
def apply(repo_root):
    repo = Path(repo_root)
    controls = _control_apply(repo)
    legacy = _apply_goals(repo)
    return dict(legacy, task_control_migration=controls)
