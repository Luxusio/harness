"""Narrow native Windows file/ACL/lock/process operations for wiki_platform.

All sensitive decisions use retained handles.  No POSIX mode bit is treated as
an access-control boundary on Windows.  This module is loaded only on Windows.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
import hashlib
import os
from pathlib import Path
import secrets
import shutil
import stat
import subprocess
import threading
import time


if os.name != "nt":
    raise ImportError("The native Windows adapter requires Windows")

K = C.WinDLL("kernel32", use_last_error=True)
A = C.WinDLL("advapi32", use_last_error=True)
P = C.c_void_p
DWORD = W.DWORD
HANDLE = W.HANDLE
SIZE_T = C.c_size_t
INVALID_HANDLE = C.c_void_p(-1).value
READ_CONTROL = 0x20000
DELETE = 0x10000
GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT = 0x10, 0x400
FILE_FLAG_BACKUP_SEMANTICS, FILE_FLAG_OPEN_REPARSE_POINT = 0x02000000, 0x00200000
FILE_FLAG_WRITE_THROUGH = 0x80000000
FILE_READ_ATTRIBUTES = 0x80
FILE_WRITE_ATTRIBUTES = 0x100
FILE_LIST_DIRECTORY = 1
FILE_SHARE_READ, FILE_SHARE_WRITE, FILE_SHARE_DELETE = 1, 2, 4
OPEN_EXISTING, OPEN_ALWAYS, CREATE_NEW = 3, 4, 1
MAX_LOCK_LOW, MAX_LOCK_HIGH = 0xFFFFFFFF, 0x7FFFFFFF
FileRenameInfoEx = 22
FILE_RENAME_FLAG_REPLACE_IF_EXISTS, FILE_RENAME_FLAG_POSIX_SEMANTICS = 1, 2


class SECURITY_ATTRIBUTES(C.Structure):
    _fields_ = [("nLength", DWORD), ("lpSecurityDescriptor", P), ("bInheritHandle", W.BOOL)]


class FILE_INFO(C.Structure):
    _fields_ = [("attributes", DWORD), ("creation", W.FILETIME), ("access", W.FILETIME),
                ("write", W.FILETIME), ("volume", DWORD), ("sizeHigh", DWORD),
                ("sizeLow", DWORD), ("links", DWORD), ("indexHigh", DWORD), ("indexLow", DWORD)]


class ACL(C.Structure):
    _fields_ = [("revision", W.BYTE), ("reserved", W.BYTE), ("size", W.WORD),
                ("count", W.WORD), ("reserved2", W.WORD)]


class SID_AND_ATTRIBUTES(C.Structure):
    _fields_ = [("sid", P), ("attributes", DWORD)]


class TOKEN_GROUPS(C.Structure):
    _fields_ = [("count", DWORD), ("groups", SID_AND_ATTRIBUTES * 1)]


class ACE_HEADER(C.Structure):
    _fields_ = [("kind", W.BYTE), ("flags", W.BYTE), ("size", W.WORD)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", SIZE_T), ("InternalHigh", SIZE_T), ("Offset", DWORD),
                ("OffsetHigh", DWORD), ("hEvent", HANDLE)]


class RENAME_INFO(C.Structure):
    # The first union is DWORD-sized; FileName starts at offset 20 on Win64.
    _fields_ = [("flags", DWORD), ("directory", HANDLE), ("length", DWORD), ("name", W.WCHAR * 1)]


class BASIC_INFO(C.Structure):
    _fields_ = [(name, C.c_longlong) for name in ("creation", "access", "write", "change")] + [
        ("attributes", DWORD)]


def _bind(dll, name, args, result=W.BOOL):
    fn = getattr(dll, name)
    fn.argtypes, fn.restype = args, result
    return fn


_CloseHandle = _bind(K, "CloseHandle", [HANDLE])
_CreateFile = _bind(K, "CreateFileW", [W.LPCWSTR, DWORD, DWORD, P, DWORD, DWORD, HANDLE], HANDLE)
_GetInfo = _bind(K, "GetFileInformationByHandle", [HANDLE, C.POINTER(FILE_INFO)])
_GetFileType = _bind(K, "GetFileType", [HANDLE], DWORD)
_ReadFile = _bind(K, "ReadFile", [HANDLE, P, DWORD, C.POINTER(DWORD), P])
_WriteFile = _bind(K, "WriteFile", [HANDLE, P, DWORD, C.POINTER(DWORD), P])
_CreateDirectory = _bind(K, "CreateDirectoryW", [W.LPCWSTR, C.POINTER(SECURITY_ATTRIBUTES)])
_FlushFileBuffers = _bind(K, "FlushFileBuffers", [HANDLE])
_SetFileInformation = _bind(K, "SetFileInformationByHandle", [HANDLE, C.c_int, P, DWORD])
_LockFileEx = _bind(K, "LockFileEx", [HANDLE, DWORD, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)])
_UnlockFileEx = _bind(K, "UnlockFileEx", [HANDLE, DWORD, DWORD, DWORD, C.POINTER(OVERLAPPED)])
_GetSecurityInfo = _bind(A, "GetSecurityInfo", [HANDLE, C.c_int, DWORD, C.POINTER(P), P,
                                             C.POINTER(P), P, C.POINTER(P)], DWORD)
_GetAce = _bind(A, "GetAce", [P, DWORD, C.POINTER(P)])
_ConvertSidToStringSid = _bind(A, "ConvertSidToStringSidW", [P, C.POINTER(W.LPWSTR)])
_ConvertStringSecurityDescriptor = _bind(A, "ConvertStringSecurityDescriptorToSecurityDescriptorW",
                                        [W.LPCWSTR, DWORD, C.POINTER(P), P])
_LocalFree = _bind(K, "LocalFree", [P], P)
_GetCurrentProcess = _bind(K, "GetCurrentProcess", [], HANDLE)
_OpenProcessToken = _bind(A, "OpenProcessToken", [HANDLE, DWORD, C.POINTER(HANDLE)])
_GetTokenInformation = _bind(A, "GetTokenInformation", [HANDLE, C.c_int, P, DWORD, C.POINTER(DWORD)])


def _error(code=None):
    code = C.get_last_error() if code is None else code
    message = "Native Windows operation failed or its security could not be verified"
    if code in (2, 3):
        return FileNotFoundError(code, message)
    if code in (80, 183):
        return FileExistsError(code, message)
    if code in (32, 33):
        return BlockingIOError(code, message)
    if code == 5:
        return PermissionError(code, message)
    return OSError(code, message)


def _check(ok):
    if not ok:
        raise _error()
    return ok


def _close(handle):
    if handle not in (None, 0, INVALID_HANDLE):
        _CloseHandle(handle)


def _sid_text(sid):
    text = W.LPWSTR()
    _check(_ConvertSidToStringSid(sid, C.byref(text)))
    try:
        return text.value
    finally:
        _LocalFree(C.cast(text, P))


def _token_information(token, kind):
    size = DWORD()
    _GetTokenInformation(token, kind, None, 0, C.byref(size))
    if not size.value or size.value > 65536:
        raise ValueError("Current Windows account identity unavailable")
    data = C.create_string_buffer(size.value)
    _check(_GetTokenInformation(token, kind, data, size, C.byref(size)))
    return data


def _user_sid():
    token = HANDLE()
    _check(_OpenProcessToken(_GetCurrentProcess(), 8, C.byref(token)))
    try:
        data = _token_information(token, 1)
        return _sid_text(C.cast(data, C.POINTER(P))[0])
    finally:
        _close(token)


USER_SID = _user_sid()
TRUSTED_SIDS = frozenset((USER_SID, "S-1-5-18", "S-1-5-32-544"))
# The system drive can be owned by the Windows Modules Installer service.
ANCESTOR_TRUSTED_SIDS = TRUSTED_SIDS | {
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
}


def _default_owner_state():
    token = HANDLE()
    _check(_OpenProcessToken(_GetCurrentProcess(), 8, C.byref(token)))
    try:
        owner = _token_information(token, 4)  # TOKEN_OWNER
        owner_sid = _sid_text(C.cast(owner, C.POINTER(P))[0])
        groups = _token_information(token, 2)  # TOKEN_GROUPS
        count = DWORD.from_buffer(groups).value
        offset, stride = TOKEN_GROUPS.groups.offset, C.sizeof(SID_AND_ATTRIBUTES)
        if count and offset + count * stride > len(groups):
            raise ValueError("Invalid Windows owner group information")
        values = []
        for index in range(count):
            group = SID_AND_ATTRIBUTES.from_buffer(groups, offset + index * stride)
            values.append((_sid_text(group.sid), group.attributes))
        return owner_sid, tuple(values)
    finally:
        _close(token)


def _current_default_owner(owner_sid):
    """Recognize the elevated process's actual native creator ownership.

    CPython 0700 directories can be owned by the token's default Administrators
    group. That group is already trusted by the private DACL policy, but it is
    an owner only when the current token actually selects and enables it as an
    owner group. Other default groups, SYSTEM and foreign users gain no trust.
    """
    if owner_sid != "S-1-5-32-544":
        return False
    default, groups = _default_owner_state()
    return default == owner_sid and any(
        sid == owner_sid and flags & 0x0C == 0x0C and not flags & 0x10
        for sid, flags in groups
    )


# Directory ancestors need integrity, not confidentiality.  Adding an unrelated
# child is harmless; replacing a retained component, changing its DACL, or
# turning it into a reparse point is not.  Leaf metadata also protects contents.
ANCESTOR_MUTATION = 0x10000000 | GENERIC_WRITE | DELETE | 0x40000 | 0x80000 | 0x100 | 0x10 | 0x40
METADATA_MUTATION = ANCESTOR_MUTATION | 0x2 | 0x4


def _security(handle, policy):
    owner, dacl, descriptor = P(), P(), P()
    result = _GetSecurityInfo(handle, 1, 1 | 4, C.byref(owner), None,
                              C.byref(dacl), None, C.byref(descriptor))
    if result:
        raise _error(result)
    try:
        if not owner.value or not dacl.value:
            raise ValueError("A verified Windows owner and explicit access list are required")
        owner_sid = _sid_text(owner)
        if not (owner_sid in ANCESTOR_TRUSTED_SIDS if policy == "ancestor" else
                owner_sid == USER_SID or _current_default_owner(owner_sid)):
            raise ValueError("Windows path has an untrusted owner")
        header = C.cast(dacl, C.POINTER(ACL)).contents
        if header.size < C.sizeof(ACL):
            raise ValueError("Invalid Windows access list")
        for index in range(header.count):
            pointer = P()
            _check(_GetAce(dacl, index, C.byref(pointer)))
            ace = C.cast(pointer, C.POINTER(ACE_HEADER)).contents
            if ace.flags & 8:  # INHERIT_ONLY_ACE does not grant this object rights.
                continue
            if ace.kind in (1, 6, 10, 12):  # deny variants cannot broaden access
                continue
            if ace.kind not in (0, 5):
                raise ValueError("Unsupported Windows access grant; refusing unsafe access")
            if ace.size < 12:
                raise ValueError("Invalid Windows access list")
            mask = DWORD.from_address(pointer.value + 4).value
            sid_offset = 8
            if ace.kind == 5:
                flags = DWORD.from_address(pointer.value + 8).value
                if flags & ~3:
                    raise ValueError("Unsupported Windows object grant")
                sid_offset = 12 + (16 if flags & 1 else 0) + (16 if flags & 2 else 0)
            if sid_offset + 8 > ace.size:
                raise ValueError("Invalid Windows account grant")
            trustee = _sid_text(pointer.value + sid_offset)
            if trustee == "S-1-3-4":  # OWNER_RIGHTS denotes this verified object's owner.
                trustee = owner_sid
            if trustee in (ANCESTOR_TRUSTED_SIDS if policy == "ancestor" else TRUSTED_SIDS):
                continue
            # Conservative evaluation: a foreign allow is refused even if an
            # earlier deny could counter it. No partial ACL evaluation guesses.
            if policy == "private" or mask & (ANCESTOR_MUTATION if policy == "ancestor" else
                                               ANCESTOR_MUTATION if policy == "lock" else METADATA_MUTATION):
                raise ValueError("Windows path grants unsafe access to another account")
        return owner_sid, hashlib.sha256(C.string_at(dacl, header.size)).hexdigest()
    finally:
        _LocalFree(descriptor)


@contextmanager
def _private_attributes(directory=False):
    inheritance = "OICI" if directory else ""
    sddl = "O:" + USER_SID + "D:P" + "".join(
        f"(A;{inheritance};FA;;;{sid})" for sid in (USER_SID, "SY", "BA"))
    descriptor = P()
    _check(_ConvertStringSecurityDescriptor(sddl, 1, C.byref(descriptor), None))
    attributes = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), descriptor, False)
    try:
        yield attributes
    finally:
        _LocalFree(descriptor)


def _path(value):
    path = Path(value)
    if (not path.is_absolute() or ".." in path.parts or "\x00" in str(path)
            or any(":" in part for part in path.parts[1:])):
        raise ValueError("An absolute Windows path without alternate streams is required")
    # Device paths can change Win32 name resolution; use ordinary absolute disk
    # paths and UNC paths only. Dots/spaces at component ends are aliases.
    if str(path).startswith(("\\\\?\\", "\\\\.\\")) or any(
            part.endswith((".", " ")) for part in path.parts[1:]):
        raise ValueError("Ambiguous Windows path is unsupported")
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
        prefix + digit for prefix in ("COM", "LPT") for digit in "123456789¹²³"
    }
    if any(part.split(".")[0].upper() in reserved or
           any(char in '<>"|?*' or ord(char) < 32 for char in part)
           for part in path.parts[1:]):
        raise ValueError("Reserved Windows path components are unsupported")
    return path


def _win32_path(value):
    """Render validated ordinary paths only at internal filesystem boundaries.

    Private temporary names must not make an otherwise valid destination hit
    the legacy MAX_PATH limit. Public inputs still pass the ordinary-path
    validator before the internal extended disk/UNC spelling is introduced.
    """
    path = str(_path(value))
    return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path


def _open(path, access, *, directory=False, create=OPEN_EXISTING, attributes=None, share=None,
          owner_neutral=False, allow_hardlinks=False, write_through=False):
    if share is None:
        share = FILE_SHARE_READ | FILE_SHARE_DELETE
    flags = FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    if write_through:
        flags |= FILE_FLAG_WRITE_THROUGH
    requested = access | FILE_READ_ATTRIBUTES | (0 if owner_neutral else READ_CONTROL)
    handle = _CreateFile(_win32_path(path), requested, share,
                         C.byref(attributes) if attributes is not None else None, create, flags, None)
    if handle == INVALID_HANDLE:
        raise _error()
    try:
        info = _info(handle)
        if (_GetFileType(handle) != 1 or bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
                or info.attributes & FILE_ATTRIBUTE_REPARSE_POINT
                or (not directory and not allow_hardlinks and info.links != 1)):
            raise ValueError("Windows path is not a regular, nonlinked disk object")
        return handle
    except BaseException:
        _close(handle)
        raise


def _info(handle):
    info = FILE_INFO()
    _check(_GetInfo(handle, C.byref(info)))
    return info


def _identity(handle):
    info = _info(handle)
    return info.volume, info.indexHigh, info.indexLow


@contextmanager
def _walk(value, *, create=False, secure=False, leaf="metadata", writing=False, owner_neutral=False):
    path = _path(value)
    handles = []
    try:
        current = Path(path.anchor)
        # Readers exclude WRITE sharing to prevent concurrent reparse conversion.
        # Writers allow it only after ACLs prove foreign accounts cannot perform
        # such a conversion. DELETE sharing is excluded for every ancestor.
        share = FILE_SHARE_READ | (FILE_SHARE_WRITE if writing else 0)
        for index, part in enumerate((None, *path.parts[1:])):
            if part is not None:
                current /= part
                if create:
                    with _private_attributes(directory=True) as attributes:
                        if not _CreateDirectory(_win32_path(current), C.byref(attributes)):
                            error = C.get_last_error()
                            if error != 183:
                                raise _error(error)
            # Attribute-only access is exempt from Windows sharing checks.
            # Request directory data access so withholding DELETE sharing
            # really retains each component's name until this context closes.
            handle = _open(current, FILE_LIST_DIRECTORY, directory=True, share=share,
                           owner_neutral=owner_neutral)
            handles.append(handle)
            if secure:
                _security(handle, leaf if index == len(path.parts) - 1 else "ancestor")
        yield path, handles[-1]
    finally:
        for handle in reversed(handles):
            _close(handle)


def _read(path, limit, policy):
    if type(limit) is not int or limit < 0:
        raise ValueError("A nonnegative byte limit is required")
    path = _path(path)
    with _walk(path.parent, secure=policy is not None) as (_, directory):
        handle = _open(path, GENERIC_READ)
        try:
            if policy is not None:
                _security(handle, policy)
            info = _info(handle)
            if (info.sizeHigh << 32) | info.sizeLow > limit:
                raise ValueError("File exceeds the supported byte limit")
            raw = bytearray()
            while len(raw) <= limit:
                size = min(65536, limit + 1 - len(raw))
                buffer, count = C.create_string_buffer(size), DWORD()
                _check(_ReadFile(handle, buffer, size, C.byref(count), None))
                if not count.value:
                    return bytes(raw)
                raw.extend(buffer.raw[:count.value])
            raise ValueError("File exceeds the supported byte limit")
        finally:
            _close(handle)


def safe_read(path, limit, private=False):
    return _read(path, limit, "private" if private else None)


def safe_metadata_read(path, limit):
    """Owner-owned, nonforeign-writable cache metadata; public read is allowed."""
    return _read(path, limit, "metadata")


def safe_metadata_line(path, limit):
    """Read one bounded metadata record while its owned log keeps appending."""
    if type(limit) is not int or limit < 0:
        raise ValueError("Nonnegative line limit required")
    path = _path(path)
    with _walk(path.parent, secure=True, writing=True):
        handle = _open(path, GENERIC_READ, share=FILE_SHARE_READ | FILE_SHARE_WRITE)
        try:
            before = _metadata_stamp(handle)
            data = bytearray()
            while len(data) <= limit:
                size = min(4096, limit + 1 - len(data))
                buffer, count = C.create_string_buffer(size), DWORD()
                _check(_ReadFile(handle, buffer, size, C.byref(count), None))
                if not count.value:
                    break
                data.extend(buffer.raw[:count.value])
                newline = data.find(b"\n")
                if newline >= 0:
                    del data[newline + 1:]
                    break
            if len(data) > limit:
                raise ValueError("Metadata first line exceeds byte limit")
            after = _metadata_stamp(handle)
            stable = lambda stamp: (stamp[:3], stamp[4:7], stamp[8])
            if stable(before) != stable(after) or after[3] < before[3]:
                raise ValueError("Metadata log changed during first-line read")
            named = _open(path, 0, share=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            try:
                if _identity(named) != before[:3]:
                    raise ValueError("Metadata log name changed during read")
            finally:
                _close(named)
            return bytes(data)
        finally:
            _close(handle)


def source_snapshot(path, limit, *, consume=None):
    """Read stable ordinary inputs without imposing private metadata policy.

    Ordinary hardlinks and publicly writable source permissions remain valid.
    A consumer receives bounded 64KiB chunks instead of a retained body.
    """
    if type(limit) is not int or limit < 0 or consume is not None and not callable(consume):
        raise ValueError("A nonnegative source limit and optional consumer are required")
    path = _path(path)
    with _walk(path.parent, owner_neutral=True):
        handle = _open(path, GENERIC_READ, share=FILE_SHARE_READ,
                       owner_neutral=True, allow_hardlinks=True)
        try:
            stamp = lambda: _metadata_free_stamp(handle)
            before = stamp()
            mode = os.lstat(_win32_path(path)).st_mode
            if not stat.S_ISREG(mode) or before[3] > limit:
                raise ValueError("Source exceeds byte limit or is not a regular file")
            data, size = bytearray() if consume is None else None, 0
            for chunk in _file_chunks(handle, limit, chunk_size=65536):
                size += len(chunk)
                if consume is None:
                    data.extend(chunk)
                else:
                    consume(chunk)
            if size != before[3] or stamp() != before or os.lstat(_win32_path(path)).st_mode != mode:
                raise ValueError("Source changed during read")
            named = _open(path, 0, owner_neutral=True, allow_hardlinks=True)
            try:
                if _identity(named) != before[:3]:
                    raise ValueError("Source name changed during read")
            finally:
                _close(named)
            return bytes(data) if data is not None else None, mode
        finally:
            _close(handle)


@contextmanager
def trusted_source_directory(path):
    """Retain owner-neutral, non-reparse ancestry while ordinary inputs run."""
    with _walk(path, owner_neutral=True) as (path, _):
        yield path


def _metadata_free_stamp(handle):
    info = _info(handle)
    times = tuple((value.dwHighDateTime << 32) | value.dwLowDateTime
                  for value in (info.creation, info.write))
    return (info.volume, info.indexHigh, info.indexLow,
            (info.sizeHigh << 32) | info.sizeLow, info.attributes, info.links, *times)


def _metadata_stamp(handle):
    return (*_metadata_free_stamp(handle), _security(handle, "metadata"))


def _file_chunks(handle, limit, *, chunk_size=1024 * 1024):
    total = 0
    while True:
        size = min(chunk_size, limit + 1 - total)
        buffer, count = C.create_string_buffer(size), DWORD()
        _check(_ReadFile(handle, buffer, size, C.byref(count), None))
        if not count.value:
            return
        total += count.value
        if total > limit:
            raise ValueError("Metadata exceeds byte limit")
        yield buffer.raw[:count.value]


def safe_metadata_snapshot(path, limit):
    """Return exact trusted source bytes and their opaque stable identity.

    Unlike a committed snapshot reader, a source-authentication read retains
    the file's name as well as its contents until the final identity check.
    """
    if type(limit) is not int or limit < 0:
        raise ValueError("Nonnegative byte limit required")
    path = _path(path)
    with _walk(path.parent, secure=True):
        handle = _open(path, GENERIC_READ, share=FILE_SHARE_READ)
        try:
            before = _metadata_stamp(handle)
            if before[3] > limit:
                raise ValueError("Metadata exceeds byte limit")
            data = b"".join(_file_chunks(handle, limit))
            if len(data) != before[3] or _metadata_stamp(handle) != before:
                raise ValueError("Metadata changed during source read")
            named = _open(path, 0)
            try:
                if _identity(named) != before[:3]:
                    raise ValueError("Metadata source name changed during read")
            finally:
                _close(named)
            return data, before
        finally:
            _close(handle)


def metadata_probe(path):
    """Inspect a retained, owned ordinary metadata object at any path depth."""
    path = _path(path)
    try:
        with _walk(path.parent, secure=True, writing=True):
            info = os.lstat(_win32_path(path))
            directory = bool(info.st_file_attributes & FILE_ATTRIBUTE_DIRECTORY)
            handle = _open(path, FILE_LIST_DIRECTORY if directory else GENERIC_READ,
                           directory=directory, share=FILE_SHARE_READ)
            try:
                stamp = _metadata_stamp(handle)
                return dict(kind="dir" if directory else "file", size=stamp[3], identity=stamp[:3])
            finally:
                _close(handle)
    except FileNotFoundError:
        return None


def metadata_names(path, *, max_entries):
    """List bounded names while native handles retain the private directory."""
    if type(max_entries) is not int or max_entries < 0:
        raise ValueError("A nonnegative directory entry limit is required")
    path = _path(path)
    with _walk(path, secure=True, writing=True):
        names = []
        with os.scandir(_win32_path(path)) as iterator:
            for entry in iterator:
                if len(names) >= max_entries:
                    raise ValueError("Metadata directory entry limit exceeded")
                names.append(entry.name)
        return names


def metadata_tree_inventory(root, *, max_entries, max_bytes, allow_control_lock=False):
    """Stream a bounded trusted tree while native handles retain every entry.

    No volatility exemption bypasses security or aggregate limits. Read-only
    attributes are reported as provenance, never interpreted as a DACL.
    """
    if any(type(value) is not int or value < 0 for value in (max_entries, max_bytes)):
        raise ValueError("Nonnegative inventory limits required")
    if type(allow_control_lock) is not bool:
        raise ValueError("Control-lock admission must be a boolean")
    root = _path(root)
    result, retained, stamps, control_names = {}, [], [], []
    used = discovered = 0

    def walk(path, prefix):
        nonlocal used, discovered
        with os.scandir(_win32_path(path)) as iterator:
            names = []
            for entry in iterator:
                discovered += 1
                if discovered > max_entries:
                    raise ValueError("Metadata entry limit exceeded")
                names.append(entry.name)
        names.sort()
        for name in names:
            if len(result) >= max_entries:
                raise ValueError("Metadata entry limit exceeded")
            child = _path(path / name)
            relative = prefix + name
            info = os.lstat(_win32_path(child))
            directory = bool(info.st_file_attributes & FILE_ATTRIBUTE_DIRECTORY)
            control_lock = allow_control_lock and relative == ".control.lock"
            if control_lock:
                handle = _open(child, 0, share=FILE_SHARE_READ | FILE_SHARE_WRITE)
            else:
                handle = _open(child, FILE_LIST_DIRECTORY if directory else GENERIC_READ,
                               directory=directory, share=FILE_SHARE_READ)
            retained.append(handle)
            before = _metadata_stamp(handle)
            stamps.append((handle, before))
            readonly = bool(before[4] & 1)
            if control_lock:
                if before[3] != 0:
                    raise ValueError("Native control lock must be empty")
                control_names.append((child, before))
                result[relative] = dict(kind="file", size=0, sha256=hashlib.sha256(b"").hexdigest(), readonly=readonly)
            elif directory:
                result[relative] = dict(kind="dir", size=0, sha256="", readonly=readonly)
                walk(child, relative + "/")
            else:
                if before[3] > max_bytes - used:
                    raise ValueError("Metadata byte limit exceeded")
                digest, size = hashlib.sha256(), 0
                for data in _file_chunks(handle, max_bytes - used):
                    size += len(data)
                    digest.update(data)
                if size != before[3]:
                    raise ValueError("Metadata file changed during inventory")
                used += size
                result[relative] = dict(kind="file", size=size, sha256=digest.hexdigest(), readonly=readonly)

    with _walk(root, secure=True, writing=True) as (_, directory):
        before_root = _metadata_stamp(directory)
        try:
            walk(root, "")
            if _metadata_stamp(directory) != before_root or any(
                    _metadata_stamp(handle) != before for handle, before in stamps):
                raise ValueError("Metadata tree changed during inventory")
            for path, before in control_names:
                named = _open(path, 0, share=FILE_SHARE_READ | FILE_SHARE_WRITE)
                try:
                    if _metadata_stamp(named) != before:
                        raise ValueError("Native control-lock name changed during inventory")
                finally:
                    _close(named)
            return result
        finally:
            for handle in reversed(retained):
                _close(handle)


@contextmanager
def trusted_metadata_directory(path):
    """Retain verified ancestry for an entire metadata read/commit transaction."""
    with _walk(path, secure=True, leaf="metadata", writing=True) as (path, _):
        yield path


def ensure_private_directory(path):
    with _walk(path, create=True, secure=True, leaf="private", writing=True) as (path, _):
        return path


_EXPECTED_ANY = object()


def _seal_readonly(handle, readonly):
    before = _metadata_stamp(handle)
    current = bool(before[4] & 1)
    if current:
        if not readonly:
            raise ValueError("Readonly metadata cannot be unsealed")
        return
    if not readonly:
        return
    basic = BASIC_INFO()
    basic.attributes = before[4] | 1
    _check(_SetFileInformation(handle, 0, C.byref(basic), C.sizeof(basic)))
    after = _metadata_stamp(handle)
    if (after[:4] != before[:4] or after[4] != before[4] | 1
            or after[5:7] != before[5:7] or after[8] != before[8]):
        raise ValueError("Metadata changed while sealing readonly attributes")


def _publish_metadata(path, chunks, *, expected=_EXPECTED_ANY, readonly=False, write_through=False):
    path = _path(path)
    with _walk(path.parent, create=True, secure=True, writing=True) as (_, directory):
        try:
            old = _open(path, 0, share=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        except FileNotFoundError:
            if isinstance(expected, bytes):
                raise
        else:
            try:
                _security(old, "metadata")
                if expected is None:
                    raise FileExistsError("Metadata destination already exists")
            finally:
                _close(old)
        temporary = path.parent / (".wiki-" + secrets.token_hex(16))
        with _private_attributes() as attributes:
            handle = _open(temporary, GENERIC_WRITE | DELETE, create=CREATE_NEW, attributes=attributes,
                           write_through=write_through)
        published = False
        try:
            _security(handle, "private")
            for data in chunks:
                offset = 0
                while offset < len(data):
                    chunk = data[offset:offset + (1024 * 1024 if write_through else 65536)]
                    count = DWORD()
                    _check(_WriteFile(handle, chunk, len(chunk), C.byref(count), None))
                    if not count.value:
                        raise OSError("Native Windows write made no progress")
                    offset += count.value
            _check(_FlushFileBuffers(handle))
            if readonly:
                _seal_readonly(handle, True)
                _check(_FlushFileBuffers(handle))
            # The owning migration/control lock serializes the expected-value
            # check with other authorized writers. This is not lock-free CAS.
            if isinstance(expected, bytes):
                current, _ = safe_metadata_snapshot(path, len(expected))
                if current != expected:
                    raise ValueError("Metadata changed before publication")
            # Use the full-path/NULL-root form, including the internal extended
            # spelling required for long destinations. Retained _walk handles
            # prevent ancestor replacement throughout this rename.
            encoded = _win32_path(path).encode("utf-16-le")
            buffer = C.create_string_buffer(max(C.sizeof(RENAME_INFO), RENAME_INFO.name.offset + len(encoded) + 2))
            rename = C.cast(buffer, C.POINTER(RENAME_INFO)).contents
            rename.flags = FILE_RENAME_FLAG_POSIX_SEMANTICS | (
                0 if expected is None else FILE_RENAME_FLAG_REPLACE_IF_EXISTS)
            rename.directory, rename.length = None, len(encoded)
            C.memmove(C.addressof(buffer) + RENAME_INFO.name.offset, encoded, len(encoded))
            if not _SetFileInformation(handle, FileRenameInfoEx, buffer, len(buffer)):
                code = C.get_last_error()  # Cleanup may overwrite last-error.
                error = _error(code)
                error._wiki_operation, error._wiki_native_code = "FileRenameInfoEx", code
                raise error
            published = True
        finally:
            if not published:
                # Delete our exact unpublished handle, never a substituted path.
                # A new archive may already be sealed when publication fails.
                # The owned DELETE handle can retire it without unsealing it;
                # keep the private wiki writer's existing disposition protocol.
                if readonly and write_through:
                    delete = DWORD(1 | 0x10)  # DELETE | IGNORE_READONLY_ATTRIBUTE
                    _SetFileInformation(handle, 21, C.byref(delete), C.sizeof(delete))
                else:
                    delete = W.BOOLEAN(True)
                    _SetFileInformation(handle, 4, C.byref(delete), C.sizeof(delete))
            _close(handle)


def atomic_write(path, data):
    if not isinstance(data, bytes):
        raise TypeError("Atomic data must be bytes")
    _publish_metadata(path, (data,))


def metadata_write(path, data, *, expected):
    """Publish a bounded control record under the caller's real owning lock."""
    if not isinstance(data, bytes) or expected is not None and not isinstance(expected, bytes):
        raise TypeError("Metadata records and expected contents must be bytes")
    if len(data) > 16 * 1024 * 1024 or expected is not None and len(expected) > 16 * 1024 * 1024:
        raise ValueError("Metadata manifest exceeds 16MiB limit")
    _publish_metadata(path, (data,), expected=expected, write_through=True)


def metadata_set_readonly(path, readonly, *, directory=False):
    """Seal checked history, or validate existing attributes without unsealing."""
    if type(readonly) is not bool or type(directory) is not bool:
        raise ValueError("Readonly and directory selectors must be booleans")
    path = _path(path)
    with _walk(path.parent, secure=True, writing=True):
        access = (FILE_LIST_DIRECTORY if directory else GENERIC_READ) | FILE_WRITE_ATTRIBUTES
        handle = _open(path, access, directory=directory,
                       share=FILE_SHARE_READ, write_through=True)
        try:
            _seal_readonly(handle, readonly)
        finally:
            _close(handle)


def _expected_hash(value):
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError("An exact SHA256 digest is required")


def _checked_chunks(handle, *, max_bytes, expected_sha256, expected_size):
    before = _metadata_stamp(handle)
    if before[3] != expected_size or before[3] > max_bytes:
        raise ValueError("Metadata source size changed or exceeds budget")
    digest, size = hashlib.sha256(), 0
    for data in _file_chunks(handle, max_bytes):
        digest.update(data)
        size += len(data)
        yield data
    if (size != expected_size or digest.hexdigest() != expected_sha256
            or _metadata_stamp(handle) != before):
        raise ValueError("Metadata source changed during copy")


def metadata_copy(source, destination, *, max_bytes, expected_sha256, expected_size, readonly):
    """Create an authenticated archive copy, or validate an admitted retry.

    Existing matching destinations are reuse under the caller's authenticated
    prepared journal, not a fresh flush of arbitrary readonly files. New
    publication preflushes a retained writer and uses NTFS write-through rename.
    """
    _expected_hash(expected_sha256)
    if (type(max_bytes) is not int or max_bytes < 0 or type(expected_size) is not int
            or not 0 <= expected_size <= max_bytes or type(readonly) is not bool):
        raise ValueError("Invalid metadata copy bounds")
    source, destination = _path(source), _path(destination)
    with _walk(source.parent, secure=True), _walk(destination.parent, create=True, secure=True, writing=True):
        original = _open(source, GENERIC_READ, share=FILE_SHARE_READ)
        visible = None
        try:
            try:
                visible = _open(destination, GENERIC_READ, share=FILE_SHARE_READ)
            except FileNotFoundError:
                pass
            chunks = _checked_chunks(original, max_bytes=max_bytes, expected_sha256=expected_sha256,
                                     expected_size=expected_size)
            if visible is not None:
                for _ in chunks:
                    pass
                before = _metadata_stamp(visible)
                if bool(before[4] & 1) != readonly:
                    raise ValueError("Existing archive readonly state changed")
                for _ in _checked_chunks(visible, max_bytes=max_bytes, expected_sha256=expected_sha256,
                                         expected_size=expected_size):
                    pass
                return "reused"
            _publish_metadata(destination, chunks, expected=None, readonly=readonly, write_through=True)
            return "published"
        finally:
            _close(visible)
            _close(original)


def metadata_remove(path, *, expected_sha256, max_bytes=1024 * 1024):
    """Retire only the checked, bounded control object through its own handle."""
    _expected_hash(expected_sha256)
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("A nonnegative metadata retirement limit is required")
    path = _path(path)
    with _walk(path.parent, secure=True, writing=True):
        handle = _open(path, GENERIC_READ | DELETE, share=FILE_SHARE_READ, write_through=True)
        try:
            before = _metadata_stamp(handle)
            for _ in _checked_chunks(handle, max_bytes=max_bytes, expected_sha256=expected_sha256,
                                     expected_size=before[3]):
                pass
            flags = DWORD(1 | 0x10)  # DELETE | IGNORE_READONLY_ATTRIBUTE
            _check(_SetFileInformation(handle, 21, C.byref(flags), C.sizeof(flags)))
        finally:
            _close(handle)


def metadata_remove_empty(path):
    """Remove the verified directory handle only if the filesystem finds it empty."""
    path = _path(path)
    with _walk(path.parent, secure=True, writing=True):
        handle = _open(path, FILE_LIST_DIRECTORY | DELETE, directory=True,
                       share=FILE_SHARE_READ, write_through=True)
        try:
            _security(handle, "metadata")
            with os.scandir(_win32_path(path)) as iterator:
                if next(iterator, None) is not None:
                    raise OSError("Metadata directory is not empty")
            flags = DWORD(1 | 0x10)
            _check(_SetFileInformation(handle, 21, C.byref(flags), C.sizeof(flags)))
        finally:
            _close(handle)


def metadata_rename_directory(source, destination):
    """Publish a held private archive directory without replacement or copying."""
    source, destination = _path(source), _path(destination)
    with _walk(source.parent, secure=True, writing=True), _walk(destination.parent, secure=True, writing=True):
        handle = _open(source, FILE_LIST_DIRECTORY | DELETE, directory=True,
                       share=FILE_SHARE_READ, write_through=True)
        try:
            _security(handle, "metadata")
            encoded = _win32_path(destination).encode("utf-16-le")
            buffer = C.create_string_buffer(max(C.sizeof(RENAME_INFO), RENAME_INFO.name.offset + len(encoded) + 2))
            rename = C.cast(buffer, C.POINTER(RENAME_INFO)).contents
            rename.flags, rename.directory, rename.length = FILE_RENAME_FLAG_POSIX_SEMANTICS, None, len(encoded)
            C.memmove(C.addressof(buffer) + RENAME_INFO.name.offset, encoded, len(encoded))
            _check(_SetFileInformation(handle, FileRenameInfoEx, buffer, len(buffer)))
        finally:
            _close(handle)


@contextmanager
def file_lock(path, shared=False, blocking=False):
    path = _path(path)
    with _walk(path.parent, secure=True, writing=True) as (_, _directory):
        with _private_attributes() as attributes:
            handle = _open(path, GENERIC_READ | GENERIC_WRITE, create=OPEN_ALWAYS,
                            attributes=attributes, share=FILE_SHARE_READ | FILE_SHARE_WRITE)
        overlapped, locked = OVERLAPPED(), False
        try:
            _security(handle, "lock")
            flags = (0 if shared else 2) | (0 if blocking else 1)
            _check(_LockFileEx(handle, flags, 0, MAX_LOCK_LOW, MAX_LOCK_HIGH, C.byref(overlapped)))
            locked = True
            yield handle
        finally:
            if locked:
                _UnlockFileEx(handle, 0, MAX_LOCK_LOW, MAX_LOCK_HIGH, C.byref(overlapped))
            _close(handle)


def same_file(left, right):
    handles = []
    try:
        left, right = _path(left), _path(right)
        with _walk(left.parent), _walk(right.parent):
            for path in (left, right):
                handle = _open(path, 0)
                handles.append(handle)
            return _identity(handles[0]) == _identity(handles[1])
    except (OSError, ValueError, TypeError):
        return False
    finally:
        for handle in handles:
            _close(handle)


class STARTUPINFO(C.Structure):
    _fields_ = [("cb", DWORD), ("lpReserved", W.LPWSTR), ("lpDesktop", W.LPWSTR),
                ("lpTitle", W.LPWSTR), ("dwX", DWORD), ("dwY", DWORD), ("dwXSize", DWORD),
                ("dwYSize", DWORD), ("dwXCountChars", DWORD), ("dwYCountChars", DWORD),
                ("dwFillAttribute", DWORD), ("dwFlags", DWORD), ("wShowWindow", W.WORD),
                ("cbReserved2", W.WORD), ("lpReserved2", P), ("hStdInput", HANDLE),
                ("hStdOutput", HANDLE), ("hStdError", HANDLE)]


class STARTUPINFOEX(C.Structure):
    _fields_ = [("StartupInfo", STARTUPINFO), ("lpAttributeList", P)]


class PROCESS_INFORMATION(C.Structure):
    _fields_ = [("process", HANDLE), ("thread", HANDLE), ("processId", DWORD), ("threadId", DWORD)]


class JOB_BASIC_LIMIT(C.Structure):
    _fields_ = [("PerProcessUserTimeLimit", C.c_longlong), ("PerJobUserTimeLimit", C.c_longlong),
                ("LimitFlags", DWORD), ("MinimumWorkingSetSize", SIZE_T), ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", DWORD), ("Affinity", SIZE_T), ("PriorityClass", DWORD),
                ("SchedulingClass", DWORD)]


class IO_COUNTERS(C.Structure):
    _fields_ = [(name, C.c_ulonglong) for name in ("ReadOperationCount", "WriteOperationCount",
                "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]


class JOB_EXTENDED_LIMIT(C.Structure):
    _fields_ = [("BasicLimitInformation", JOB_BASIC_LIMIT), ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", SIZE_T), ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T), ("PeakJobMemoryUsed", SIZE_T)]


class JOB_ACCOUNTING(C.Structure):
    _fields_ = [(name, C.c_longlong) for name in ("user", "kernel", "period_user", "period_kernel")] + [
        (name, DWORD) for name in ("faults", "total", "active", "terminated")]


_CreatePipe = _bind(K, "CreatePipe", [C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(SECURITY_ATTRIBUTES), DWORD])
_SetHandleInformation = _bind(K, "SetHandleInformation", [HANDLE, DWORD, DWORD])
_CreateJobObject = _bind(K, "CreateJobObjectW", [P, W.LPCWSTR], HANDLE)
_SetInformationJobObject = _bind(K, "SetInformationJobObject", [HANDLE, C.c_int, P, DWORD])
_AssignProcessToJobObject = _bind(K, "AssignProcessToJobObject", [HANDLE, HANDLE])
_TerminateJobObject = _bind(K, "TerminateJobObject", [HANDLE, W.UINT])
_QueryJob = _bind(K, "QueryInformationJobObject", [HANDLE, C.c_int, P, DWORD, P])
_InitializeAttributes = _bind(K, "InitializeProcThreadAttributeList", [P, DWORD, DWORD, C.POINTER(SIZE_T)])
_UpdateAttribute = _bind(K, "UpdateProcThreadAttribute", [P, DWORD, SIZE_T, P, SIZE_T, P, P])
_DeleteAttributes = _bind(K, "DeleteProcThreadAttributeList", [P], None)
_CreateProcess = _bind(K, "CreateProcessW", [W.LPCWSTR, W.LPWSTR, P, P, W.BOOL, DWORD, P,
                                           W.LPCWSTR, P, C.POINTER(PROCESS_INFORMATION)])
_ResumeThread = _bind(K, "ResumeThread", [HANDLE], DWORD)
_TerminateProcess = _bind(K, "TerminateProcess", [HANDLE, W.UINT])
_WaitForSingleObject = _bind(K, "WaitForSingleObject", [HANDLE, DWORD], DWORD)
_GetExitCodeProcess = _bind(K, "GetExitCodeProcess", [HANDLE, C.POINTER(DWORD)])
_PeekNamedPipe = _bind(K, "PeekNamedPipe", [HANDLE, P, DWORD, P, C.POINTER(DWORD), P])
_OpenThread = _bind(K, "OpenThread", [DWORD, W.BOOL, DWORD], HANDLE)
_CancelSynchronousIo = _bind(K, "CancelSynchronousIo", [HANDLE])


def _stop_job(job):
    """Reap every owned process before releasing a verification resource."""
    _check(_TerminateJobObject(job, 1))
    deadline = time.monotonic() + 5
    while True:
        accounting = JOB_ACCOUNTING()
        _check(_QueryJob(job, 1, C.byref(accounting), C.sizeof(accounting), None))
        if accounting.active == 0:
            return
        if time.monotonic() >= deadline:
            raise OSError("Native job cleanup could not be confirmed")
        time.sleep(0.005)


def _feed_input(handle, data, state):
    try:
        state["thread"] = _OpenThread(0x100001, False, threading.get_native_id())
        if not state["thread"]:
            raise _error()
        offset = 0
        while offset < len(data):
            chunk = data[offset:offset + 65536]
            count = DWORD()
            _check(_WriteFile(handle, chunk, len(chunk), C.byref(count), None))
            if not count.value:
                raise OSError("Native stdin write made no progress")
            offset += count.value
    except OSError as error:
        state["error"] = error
    finally:
        _close(handle)


def _join_input(writer, state):
    writer.join(0.1)
    deadline = time.monotonic() + 2
    while writer.is_alive() and time.monotonic() < deadline:
        handle = state.get("thread")
        if handle:
            # Only this owned writer can have synchronous I/O cancelled.
            if not _CancelSynchronousIo(handle) and C.get_last_error() != 1168:
                raise _error()
        writer.join(0.02)
    if writer.is_alive():
        raise OSError("Native stdin writer cleanup could not be confirmed")


def _process(argv, *, cwd, env, timeout, limit, merge_stderr=False, cancelled=None, input_bytes=None,
             capture_stderr=False):
    """Spawn suspended, assign a kill-on-close Job, then bound captured bytes.

    The explicit inherited-handle list keeps unrelated private process handles
    out of the child. A Job is closed even after normal leader exit, so mise
    descendants retaining a stdout pipe cannot outlive this operation.
    """
    if (not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(a, str) or "\x00" in a for a in argv)
            or type(limit) is not int or limit < 0 or timeout <= 0):
        raise ValueError("Invalid bounded process request")
    if input_bytes is not None and (not isinstance(input_bytes, bytes) or len(input_bytes) > 2 * 1024 * 1024):
        raise ValueError("Native stdin must be at most 2MiB of bytes")
    if type(capture_stderr) is not bool or capture_stderr and merge_stderr:
        raise ValueError("Separate stderr capture cannot be combined with merged stderr")
    executable = shutil.which(argv[0], path=env.get("PATH"))
    if not executable or Path(executable).suffix.lower() not in (".exe", ".com"):
        raise ValueError("A native Windows executable is required; shell scripts are unsupported")
    if any(not isinstance(k, str) or not isinstance(v, str) or not k or "=" in k or "\x00" in k + v
           for k, v in env.items()):
        raise ValueError("Invalid process environment")
    if cancelled is not None and cancelled():
        result = dict(returncode=None, error="parent execution ended", output=b"", start_failed=True)
        if capture_stderr:
            result["stderr"] = b""
        return result
    environment = C.create_unicode_buffer("\x00".join(f"{k}={v}" for k, v in
                                sorted(env.items(), key=lambda item: item[0].upper())) + "\x00\x00")
    command = C.create_unicode_buffer(subprocess.list2cmdline([executable, *argv[1:]]))
    read, write, input_read, input_write = HANDLE(), HANDLE(), HANDLE(), HANDLE()
    error_read, error_write = HANDLE(), HANDLE()
    null, job, attribute_list = None, None, None
    process = PROCESS_INFORMATION()
    inherited = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), None, True)
    assigned = attributes_initialized = stopped = writer_started = writer_joined = False
    writer, input_state = None, {}
    try:
        _check(_CreatePipe(C.byref(read), C.byref(write), C.byref(inherited), 65536))
        _check(_SetHandleInformation(read, 1, 0))
        if capture_stderr:
            _check(_CreatePipe(C.byref(error_read), C.byref(error_write), C.byref(inherited), 65536))
            _check(_SetHandleInformation(error_read, 1, 0))
        if input_bytes is not None:
            _check(_CreatePipe(C.byref(input_read), C.byref(input_write), C.byref(inherited), 65536))
            _check(_SetHandleInformation(input_write, 1, 0))
        null = _CreateFile("NUL", GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE,
                            C.byref(inherited), OPEN_EXISTING, 0, None)
        if null == INVALID_HANDLE:
            raise _error()
        job = _CreateJobObject(None, None)
        if not job:
            raise _error()
        limits = JOB_EXTENDED_LIMIT()
        limits.BasicLimitInformation.LimitFlags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        _check(_SetInformationJobObject(job, 9, C.byref(limits), C.sizeof(limits)))
        size = SIZE_T()
        _InitializeAttributes(None, 1, 0, C.byref(size))
        if not size.value or size.value > 65536:
            raise ValueError("Native process handle isolation unavailable")
        attribute_buffer = C.create_string_buffer(size.value)
        attribute_list = C.cast(attribute_buffer, P)
        _check(_InitializeAttributes(attribute_list, 1, 0, C.byref(size)))
        attributes_initialized = True
        stdin = input_read.value if input_bytes is not None else null
        stderr = error_write.value if capture_stderr else write.value if merge_stderr else null
        values = list(dict.fromkeys((write.value, stdin, stderr)))
        handles = (HANDLE * len(values))(*values)
        _check(_UpdateAttribute(attribute_list, 0, 0x20002, handles, C.sizeof(handles), None, None))
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput = stdin
        startup.StartupInfo.hStdOutput = write.value
        startup.StartupInfo.hStdError = stderr
        startup.lpAttributeList = attribute_list
        flags = 4 | 0x400 | 0x80000 | 0x08000000
        _check(_CreateProcess(executable, command, None, None, True, flags, environment,
                              str(_path(cwd)), C.byref(startup), C.byref(process)))
        _check(_AssignProcessToJobObject(job, process.process))
        assigned = True
        if _ResumeThread(process.thread) == 0xFFFFFFFF:
            raise _error()
        _close(process.thread)
        process.thread = None
        _close(write.value)
        write = HANDLE()
        _close(error_write.value)
        error_write = HANDLE()
        _close(null)
        null = None
        _close(input_read.value)
        input_read = HANDLE()
        if input_bytes is not None:
            writer = threading.Thread(target=_feed_input, args=(input_write.value, input_bytes, input_state),
                                      name="harness-native-stdin", daemon=False)
            writer.start()
            writer_started = True
        output, error_output = bytearray(), bytearray()
        streams = [(read, output)]
        if capture_stderr:
            streams.append((error_read, error_output))
        deadline, exited = time.monotonic() + timeout, False
        failure = None
        while streams or not exited:
            ready = False
            for stream, target in tuple(streams):
                available = DWORD()
                if not _PeekNamedPipe(stream, None, 0, None, C.byref(available), None):
                    error = C.get_last_error()
                    if error == 109:
                        streams.remove((stream, target))
                        continue
                    raise _error(error)
                if available.value:
                    ready = True
                    remaining = limit - len(output) - len(error_output)
                    size = min(65536, available.value, remaining + 1)
                    buffer, count = C.create_string_buffer(size), DWORD()
                    _check(_ReadFile(stream, buffer, size, C.byref(count), None))
                    target.extend(buffer.raw[:min(count.value, remaining)])
                    if count.value > remaining:
                        failure = "output limit exceeded"
                        break
            if not exited:
                wait = _WaitForSingleObject(process.process, 0)
                if wait == 0xFFFFFFFF:
                    raise _error()
                if wait == 0:
                    exited = True
                    _stop_job(job)
                    stopped = True
            if not failure and cancelled is not None and cancelled():
                failure = "parent execution ended"
            if not failure and time.monotonic() >= deadline:
                failure = "timeout"
            if failure:
                if not stopped:
                    _stop_job(job)
                    stopped = True
                if _WaitForSingleObject(process.process, 5000) != 0:
                    raise OSError("Native process termination could not be confirmed")
                break
            if not ready:
                time.sleep(0.005)
        code = DWORD()
        _check(_GetExitCodeProcess(process.process, C.byref(code)))
        if writer_started:
            _join_input(writer, input_state)
            writer_joined = True
            if input_state.get("error") is not None and failure is None:
                failure = "stdin delivery failed"
        result = dict(returncode=code.value, error=failure, output=bytes(output))
        if capture_stderr:
            result["stderr"] = bytes(error_output)
        return result
    finally:
        try:
            if assigned and not stopped:
                _stop_job(job)
        finally:
            try:
                _close(job)
                if process.process:
                    if not assigned or _WaitForSingleObject(process.process, 0) != 0:
                        _TerminateProcess(process.process, 1)
                    _WaitForSingleObject(process.process, 5000)
                if writer_started and not writer_joined:
                    _join_input(writer, input_state)
            finally:
                _close(input_state.get("thread"))
                if not writer_started:
                    _close(input_write.value)
                _close(input_read.value)
                _close(process.thread)
                _close(process.process)
                _close(read.value)
                _close(write.value)
                _close(error_read.value)
                _close(error_write.value)
                _close(null)
                if attributes_initialized:
                    _DeleteAttributes(attribute_list)


def run_bounded(argv, *, cwd, env, timeout=20, limit=1048576, capture_stderr=False, input_bytes=None):
    """Capture optional stderr with the private helper's exception protocol."""
    result = _process(argv, cwd=cwd, env=env, timeout=timeout, limit=limit,
                      capture_stderr=capture_stderr, input_bytes=input_bytes)
    if result["error"] == "timeout":
        raise subprocess.TimeoutExpired(argv, timeout)
    if result["error"] == "output limit exceeded":
        raise ValueError("Process stdout exceeds the supported byte limit")
    if result["error"]:
        error = subprocess.SubprocessError("Native private helper failed: " + result["error"])
        error.stdout, error.stderr = result["output"], result.get("stderr", b"")
        raise error
    return subprocess.CompletedProcess(argv, result["returncode"], result["output"], result.get("stderr", b""))


def native_executable(command, *, cwd, env):
    """Resolve a native command from explicit paths, without an implicit cwd."""
    env = dict(os.environ) if env is None else env
    if not isinstance(command, str) or "\x00" in command:
        raise ValueError("Invalid native command")
    if os.path.dirname(command):
        candidate = Path(command)
        if not candidate.is_absolute():
            candidate = _path(cwd) / candidate
        executable = shutil.which(str(candidate))
    else:
        # Windows which() otherwise prepends the ambient process directory.
        # Verification commands honor only their supplied PATH; an explicitly
        # listed relative/empty entry is relative to the requested child cwd.
        executable = None
        for entry in env.get("PATH", os.environ.get("PATH", os.defpath)).split(os.pathsep):
            directory = Path(entry)
            if not directory.is_absolute():
                directory = _path(cwd) / directory
            executable = shutil.which(str(directory / command))
            if executable is not None:
                break
    if executable is None:
        raise FileNotFoundError("Native verification executable is unavailable")
    if Path(executable).suffix.lower() not in (".exe", ".com"):
        raise OSError("Verification needs a native executable or an explicit native shell")
    return executable


def execute_bounded(argv, *, cwd, env, timeout, limit, cancelled=None, input_bytes=None, capture_stderr=False):
    """Run one native verification command, retaining bounded partial logs."""
    if type(capture_stderr) is not bool:
        raise ValueError("Separate stderr capture must be a boolean")
    if cancelled is not None and not callable(cancelled):
        raise ValueError("Cancellation must be a callback")
    if cancelled is not None and cancelled():
        result = dict(returncode=None, error="parent execution ended", output=b"", start_failed=True)
        if capture_stderr:
            result["stderr"] = b""
        return result
    env = dict(os.environ) if env is None else env
    if not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(a, str) or "\x00" in a for a in argv):
        raise ValueError("Invalid verification command")
    executable = native_executable(argv[0], cwd=cwd, env=env)
    with trusted_source_directory(cwd):
        return _process([executable, *argv[1:]], cwd=cwd, env=env, timeout=timeout,
                        limit=limit, merge_stderr=not capture_stderr, cancelled=cancelled,
                        input_bytes=input_bytes, capture_stderr=capture_stderr)
