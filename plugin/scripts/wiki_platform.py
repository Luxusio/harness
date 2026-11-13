"""Small filesystem/process primitives shared by wiki runtime and hooks.

The Windows adapter uses native handles and security descriptors. POSIX keeps
directory descriptors open so changing a pathname cannot redirect an operation.
Failures are intentional: absence of a platform protection is not permission.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import threading
import time

if os.name == "nt":
    import _wiki_windows as _native
else:
    import fcntl


_guard = threading.RLock()
_active_locks = set()
_LOCK_LENGTH = (1 << 63) - 1


def _absolute(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Canonical absolute path required")
    return path


def _directory_info(info, *, private):
    if not stat.S_ISDIR(info.st_mode):
        raise ValueError("Directory required")
    if private and (info.st_uid not in (0, os.getuid()) or
                    info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX):
        raise ValueError("Unsafe private directory")


@contextmanager
def _parent(path, *, private=False, create=False):
    path = _absolute(path)
    fd = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, 0o700, dir_fd=fd)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
            _directory_info(os.fstat(fd), private=private)
        yield fd, path.name
    finally:
        os.close(fd)


def _regular(info, *, private=False):
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError("Single-link regular file required")
    if private and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("Owner-private file required")


def safe_read(path, limit, private=False):
    """Read at most limit bytes, rejecting links, special files and oversize."""
    if type(limit) is not int or limit < 0:
        raise ValueError("Nonnegative byte limit required")
    if os.name == "nt":
        return _native.safe_read(path, limit, private=private)
    with _parent(path, private=private) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            _regular(info, private=private)
            if info.st_size > limit:
                raise ValueError("File exceeds byte limit")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("File exceeds byte limit")
            return data


@contextmanager
def trusted_metadata_directory(path):
    """Hold the trusted notice directory while its transaction is consumed."""
    path = _absolute(path)
    if os.name == "nt":
        with _native.trusted_metadata_directory(path) as directory:
            yield directory
        return
    with _parent(path, private=True) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o022:
                raise ValueError("Unsafe metadata directory")
            yield path
        finally:
            os.close(fd)


def safe_metadata_read(path, limit):
    """Legacy snapshot files may be readable, but must never be foreign-writable."""
    if os.name == "nt":
        return _native.safe_metadata_read(path, limit)
    with _parent(path, private=True) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(fd)
            _regular(info)
            if info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_size > limit:
                raise ValueError("Unsafe metadata file")
            data = stream.read(limit + 1)
            if len(data) > limit:
                raise ValueError("Metadata exceeds byte limit")
            return data


def safe_metadata_line(path, limit):
    """Read a bounded first metadata line with native append-writer sharing."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata first-line reads require Windows")
    return _native.safe_metadata_line(path, limit)


def source_snapshot(path, limit, *, consume=None):
    """Read stable ordinary native inputs, optionally streaming to a consumer."""
    if os.name != "nt":
        raise NotImplementedError("Native ordinary source reads require Windows")
    return _native.source_snapshot(path, limit, consume=consume)


@contextmanager
def trusted_source_directory(path):
    """Retain ordinary native ancestry without requiring metadata ownership."""
    if os.name != "nt":
        raise NotImplementedError("Native ordinary source directories require Windows")
    with _native.trusted_source_directory(path) as directory:
        yield directory


def _metadata_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
            info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def _metadata_owner(info):
    _regular(info)
    if info.st_uid != os.getuid() or info.st_mode & 0o022:
        raise ValueError("Unsafe metadata ownership or permissions")


def safe_metadata_snapshot(path, limit):
    """Return bounded source bytes and their verified stable object identity."""
    if type(limit) is not int or limit < 0:
        raise ValueError("Nonnegative byte limit required")
    if os.name == "nt":
        return _native.safe_metadata_snapshot(path, limit)
    with _parent(path, private=True) as (directory, name):
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            _metadata_owner(before)
            identity = _metadata_identity(before)
            if before.st_size > limit:
                raise ValueError("Metadata exceeds byte limit")
            data = stream.read(limit + 1)
            after = os.fstat(stream.fileno())
            named = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if (len(data) > limit or len(data) != before.st_size
                    or _metadata_identity(after) != identity
                    or _metadata_identity(named) != identity):
                raise ValueError("Metadata source changed during read")
            return data, identity


def metadata_tree_inventory(root, *, max_entries, max_bytes, allow_control_lock=False):
    """Inspect a native Windows tree with retained file and directory custody."""
    if os.name != "nt":
        raise NotImplementedError("Trusted metadata tree inventory requires native Windows")
    return _native.metadata_tree_inventory(root, max_entries=max_entries, max_bytes=max_bytes,
                                            allow_control_lock=allow_control_lock)


def ensure_private_directory(path):
    """Create missing private storage; never chmod an existing user's tree."""
    path = _absolute(path)
    if os.name == "nt":
        return _native.ensure_private_directory(path)
    with _parent(path, private=True, create=True) as (directory, name):
        try:
            os.mkdir(name, 0o700, dir_fd=directory)
        except FileExistsError:
            pass
        fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError("Owner-private directory required")
        finally:
            os.close(fd)
    return path


def atomic_write(path, data):
    """Publish private bytes through a new file in the verified destination dir."""
    if not isinstance(data, bytes):
        raise TypeError("Bytes required")
    if os.name == "nt":
        return _native.atomic_write(path, data)
    with _parent(path, private=True) as (directory, name):
        owner = os.fstat(directory)
        if owner.st_uid != os.getuid() or owner.st_mode & 0o022:
            raise ValueError("Private writable directory required")
        try:
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            _regular(before)
            if before.st_uid != os.getuid() or before.st_mode & 0o022:
                raise ValueError("Unsafe existing metadata")
        temporary = ".wiki-" + os.urandom(16).hex()
        fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def metadata_write(path, data, *, expected):
    """Publish a native metadata record under the caller's owning lock."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata publication requires Windows")
    return _native.metadata_write(path, data, expected=expected)


def metadata_copy(source, destination, *, max_bytes, expected_sha256, expected_size, readonly):
    """Create or validate reuse of a journal-admitted native archive file."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata copying requires Windows")
    return _native.metadata_copy(source, destination, max_bytes=max_bytes,
                                  expected_sha256=expected_sha256, expected_size=expected_size,
                                  readonly=readonly)


def metadata_remove(path, *, expected_sha256, max_bytes=1024 * 1024):
    """Retire one exact native metadata object after hash validation."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata retirement requires Windows")
    return _native.metadata_remove(path, expected_sha256=expected_sha256, max_bytes=max_bytes)


def metadata_remove_empty(path):
    """Remove a held native metadata directory only when it is empty."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata directory removal requires Windows")
    return _native.metadata_remove_empty(path)


def metadata_set_readonly(path, readonly, *, directory=False):
    """Seal native metadata attributes without unsealing existing history."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata attribute sealing requires Windows")
    return _native.metadata_set_readonly(path, readonly, directory=directory)


def metadata_rename_directory(source, destination):
    """Publish a checked native directory without replacing its destination."""
    if os.name != "nt":
        raise NotImplementedError("Native metadata directory publication requires Windows")
    return _native.metadata_rename_directory(source, destination)


@contextmanager
def file_lock_at(directory, name, shared=False, blocking=False):
    """Lock a child of an already retained POSIX directory, without reopening it."""
    if os.name == "nt":
        raise ValueError("Windows locks use retained native handles through file_lock")
    if not isinstance(name, str) or not name or name in (".", "..") or "/" in name or "\\" in name:
        raise ValueError("One lock filename required")
    _directory_info(os.fstat(directory), private=True)
    inode_key = None
    inode_registered = False
    fd = None
    try:
        # Checking the inode before opening is essential: closing a second FD
        # would release another lockf owner in this process, even on rejection.
        with _guard:
            try:
                before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None:
                _regular(before)
                inode_key = ("inode", before.st_dev, before.st_ino)
                if inode_key in _active_locks:
                    raise BlockingIOError("Lock already owned by this process")
            fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                         0o600, dir_fd=directory)
            info = os.fstat(fd)
            _regular(info)
            if info.st_uid != os.getuid():
                raise ValueError("Lock is owned by another user")
            if before is not None and (info.st_dev, info.st_ino) != (before.st_dev, before.st_ino):
                raise ValueError("Lock changed while opening")
            inode_key = ("inode", info.st_dev, info.st_ino)
            _active_locks.add(inode_key)
            inode_registered = True
        flags = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        if not blocking:
            flags |= fcntl.LOCK_NB
        try:
            fcntl.lockf(fd, flags, _LOCK_LENGTH, 0, os.SEEK_SET)
        except OSError as error:
            if error.errno in (errno.EACCES, errno.EAGAIN):
                raise BlockingIOError("File lock is busy") from None
            raise
        yield
    finally:
        with _guard:
            if fd is not None:
                os.close(fd)
            if inode_registered:
                _active_locks.discard(inode_key)


@contextmanager
def file_lock(path, shared=False, blocking=False):
    """One record-lock owner per inode/process, interoperable with FileChannel."""
    path = _absolute(path)
    if os.name != "nt":
        with _parent(path, private=True) as (directory, name):
            with file_lock_at(directory, name, shared=shared, blocking=blocking):
                yield
        return
    path_key = ("path", os.path.normcase(str(path)))
    with _guard:
        if path_key in _active_locks:
            raise BlockingIOError("Lock already owned by this process")
        _active_locks.add(path_key)
    try:
        with _native.file_lock(path, shared=shared, blocking=blocking):
            yield
    finally:
        with _guard:
            _active_locks.discard(path_key)


def same_file(left, right):
    """Compare existing nonlink source paths without accepting a symlink alias."""
    try:
        if os.name == "nt":
            return _native.same_file(left, right)
        values = []
        for path in (left, right):
            with _parent(path) as (directory, name):
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                _regular(info)
                values.append((info.st_dev, info.st_ino))
        return values[0] == values[1]
    except (OSError, ValueError, TypeError):
        return False


def run_bounded(argv, *, cwd, env, timeout=20, limit=1048576, graceful=False):
    """Run a private helper with bounded bytes and complete owned-tree cleanup."""
    if timeout <= 0 or type(limit) is not int or limit < 0:
        raise ValueError("Positive timeout and nonnegative byte limit required")
    if os.name == "nt":
        return _native.run_bounded(argv, cwd=cwd, env=env, timeout=timeout, limit=limit)
    process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               start_new_session=True)
    try:
        deadline = time.monotonic() + timeout
        output = bytearray()
        leader_exited = False
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while selector.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(argv, timeout)
                if not leader_exited and process.poll() is not None:
                    leader_exited = True
                    try:
                        os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                for key, _ in selector.select(min(remaining, 0.05)):
                    chunk = os.read(key.fileobj.fileno(), min(65536, limit + 1 - len(output)))
                    if not chunk:
                        selector.unregister(key.fileobj)
                    else:
                        output.extend(chunk)
                        if len(output) > limit:
                            raise ValueError("Private helper output exceeds byte limit")
            process.wait(timeout=max(0.001, deadline - time.monotonic()))
        return subprocess.CompletedProcess(argv, process.returncode, bytes(output), b"")
    finally:
        if graceful:
            # A trusted Python owner may have nested helper groups to reap.
            # Let its termination handler unwind them before the hard ceiling.
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=1)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                pass
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        process.stdout.close()


def native_executable(command, *, cwd, env):
    """Resolve an explicitly selected native Windows command."""
    if os.name != "nt":
        raise NotImplementedError("Native command resolution requires Windows")
    return _native.native_executable(command, cwd=cwd, env=env)


def execute_bounded(argv, *, cwd, env, timeout, limit, cancelled=None, input_bytes=None):
    """Run a native verification command with bounded merged partial output."""
    if os.name != "nt":
        raise NotImplementedError("Native verification execution requires Windows")
    return _native.execute_bounded(argv, cwd=cwd, env=env, timeout=timeout,
                                   limit=limit, cancelled=cancelled, input_bytes=input_bytes)
