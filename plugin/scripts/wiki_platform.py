"""Small filesystem/process primitives shared by wiki runtime and hooks.

The Windows adapter uses native handles and security descriptors. POSIX keeps
directory descriptors open so changing a pathname cannot redirect an operation.
Failures are intentional: absence of a platform protection is not permission.
"""
from __future__ import annotations

from contextlib import contextmanager
import errno
import hashlib
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


def _metadata_identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mode, info.st_uid,
            info.st_nlink, info.st_mtime_ns, info.st_ctime_ns)


def _metadata_owner(info, *, directory=False):
    if directory:
        if not stat.S_ISDIR(info.st_mode):
            raise ValueError("Metadata directory required")
    else:
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


def metadata_tree_inventory(root, *, max_entries, max_bytes):
    """Stream a no-follow trusted tree within aggregate entry and byte limits."""
    if any(type(value) is not int or value < 0 for value in (max_entries, max_bytes)):
        raise ValueError("Nonnegative inventory limits required")
    if os.name == "nt":
        return _native.metadata_tree_inventory(root, max_entries=max_entries, max_bytes=max_bytes)
    root = _absolute(root)
    result = {}
    used = discovered = 0

    def walk(directory, prefix):
        nonlocal used, discovered
        before_directory = os.fstat(directory)
        _metadata_owner(before_directory, directory=True)
        names = []
        with os.scandir(directory) as iterator:
            for entry in iterator:
                discovered += 1
                if discovered > max_entries:
                    raise ValueError("Metadata entry limit exceeded")
                names.append(entry.name)
        for name in sorted(names):
            relative = prefix + name
            before = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if stat.S_ISDIR(before.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
                try:
                    opened = os.fstat(child)
                    if _metadata_identity(opened) != _metadata_identity(before):
                        raise ValueError("Metadata directory changed before inventory")
                    _metadata_owner(opened, directory=True)
                    result[relative] = dict(kind="dir", size=0, sha256="",
                                            readonly=not bool(opened.st_mode & stat.S_IWUSR))
                    walk(child, relative + "/")
                finally:
                    os.close(child)
            else:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
                with os.fdopen(fd, "rb") as stream:
                    opened = os.fstat(stream.fileno())
                    _metadata_owner(opened)
                    if _metadata_identity(opened) != _metadata_identity(before):
                        raise ValueError("Metadata file changed before inventory")
                    if opened.st_size > max_bytes - used:
                        raise ValueError("Metadata byte limit exceeded")
                    digest, size = hashlib.sha256(), 0
                    while data := stream.read(min(1024 * 1024, max_bytes - used - size + 1)):
                        size += len(data)
                        if used + size > max_bytes:
                            raise ValueError("Metadata byte limit exceeded")
                        digest.update(data)
                    if (size != opened.st_size
                            or _metadata_identity(os.fstat(stream.fileno())) != _metadata_identity(opened)):
                        raise ValueError("Metadata file changed during inventory")
                    used += size
                    result[relative] = dict(kind="file", size=size, sha256=digest.hexdigest(),
                                            readonly=not bool(opened.st_mode & stat.S_IWUSR))
            if _metadata_identity(os.stat(name, dir_fd=directory, follow_symlinks=False)) != _metadata_identity(before):
                raise ValueError("Metadata name changed during inventory")
        if _metadata_identity(os.fstat(directory)) != _metadata_identity(before_directory):
            raise ValueError("Metadata directory changed during inventory")

    with _parent(root, private=True) as (parent, name):
        directory = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            walk(directory, "")
        finally:
            os.close(directory)
    return result


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
