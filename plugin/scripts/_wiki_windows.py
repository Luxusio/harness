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
import subprocess
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
FILE_READ_ATTRIBUTES = 0x80
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


class ACE_HEADER(C.Structure):
    _fields_ = [("kind", W.BYTE), ("flags", W.BYTE), ("size", W.WORD)]


class OVERLAPPED(C.Structure):
    _fields_ = [("Internal", SIZE_T), ("InternalHigh", SIZE_T), ("Offset", DWORD),
                ("OffsetHigh", DWORD), ("hEvent", HANDLE)]


class RENAME_INFO(C.Structure):
    # The first union is DWORD-sized; FileName starts at offset 20 on Win64.
    _fields_ = [("flags", DWORD), ("directory", HANDLE), ("length", DWORD), ("name", W.WCHAR * 1)]


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


def _user_sid():
    token = HANDLE()
    _check(_OpenProcessToken(_GetCurrentProcess(), 8, C.byref(token)))
    try:
        size = DWORD()
        _GetTokenInformation(token, 1, None, 0, C.byref(size))
        if not size.value or size.value > 65536:
            raise ValueError("Current Windows account identity unavailable")
        data = C.create_string_buffer(size.value)
        _check(_GetTokenInformation(token, 1, data, size, C.byref(size)))
        return _sid_text(C.cast(data, C.POINTER(P))[0])
    finally:
        _close(token)


USER_SID = _user_sid()
TRUSTED_SIDS = frozenset((USER_SID, "S-1-5-18", "S-1-5-32-544"))
# The system drive can be owned by the Windows Modules Installer service.
ANCESTOR_TRUSTED_SIDS = TRUSTED_SIDS | {
    "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464",
}
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
        if owner_sid not in (ANCESTOR_TRUSTED_SIDS if policy == "ancestor" else (USER_SID,)):
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


def _open(path, access, *, directory=False, create=OPEN_EXISTING, attributes=None, share=None):
    if share is None:
        share = FILE_SHARE_READ | FILE_SHARE_DELETE
    flags = FILE_FLAG_OPEN_REPARSE_POINT | (FILE_FLAG_BACKUP_SEMANTICS if directory else 0)
    handle = _CreateFile(str(path), access | FILE_READ_ATTRIBUTES | READ_CONTROL, share,
                         C.byref(attributes) if attributes is not None else None, create, flags, None)
    if handle == INVALID_HANDLE:
        raise _error()
    try:
        info = _info(handle)
        if (_GetFileType(handle) != 1 or bool(info.attributes & FILE_ATTRIBUTE_DIRECTORY) != directory
                or info.attributes & FILE_ATTRIBUTE_REPARSE_POINT or (not directory and info.links != 1)):
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
def _walk(value, *, create=False, secure=False, leaf="metadata", writing=False):
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
                        if not _CreateDirectory(str(current), C.byref(attributes)):
                            error = C.get_last_error()
                            if error != 183:
                                raise _error(error)
            # Attribute-only access is exempt from Windows sharing checks.
            # Request directory data access so withholding DELETE sharing
            # really retains each component's name until this context closes.
            handle = _open(current, FILE_LIST_DIRECTORY, directory=True, share=share)
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


def _metadata_stamp(handle):
    info = _info(handle)
    times = tuple((value.dwHighDateTime << 32) | value.dwLowDateTime
                  for value in (info.creation, info.write))
    return (info.volume, info.indexHigh, info.indexLow,
            (info.sizeHigh << 32) | info.sizeLow, info.attributes, info.links,
            *times, _security(handle, "metadata"))


def _file_chunks(handle, limit):
    total = 0
    while True:
        size = min(1024 * 1024, limit + 1 - total)
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


def metadata_tree_inventory(root, *, max_entries, max_bytes):
    """Stream a bounded trusted tree while native handles retain every entry.

    No volatility exemption bypasses security or aggregate limits. Read-only
    attributes are reported as provenance, never interpreted as a DACL.
    """
    if any(type(value) is not int or value < 0 for value in (max_entries, max_bytes)):
        raise ValueError("Nonnegative inventory limits required")
    root = _path(root)
    result, retained, stamps = {}, [], []
    used = discovered = 0

    def walk(path, prefix):
        nonlocal used, discovered
        with os.scandir(path) as iterator:
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
            info = child.lstat()
            directory = bool(info.st_file_attributes & FILE_ATTRIBUTE_DIRECTORY)
            handle = _open(child, FILE_LIST_DIRECTORY if directory else GENERIC_READ,
                           directory=directory, share=FILE_SHARE_READ)
            retained.append(handle)
            before = _metadata_stamp(handle)
            stamps.append((handle, before))
            readonly = bool(before[4] & 1)
            if directory:
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


def atomic_write(path, data):
    if not isinstance(data, bytes):
        raise TypeError("Atomic data must be bytes")
    path = _path(path)
    with _walk(path.parent, create=True, secure=True, writing=True) as (_, directory):
        try:
            old = _open(path, 0, share=FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
        except FileNotFoundError:
            pass
        else:
            try:
                _security(old, "metadata")
            finally:
                _close(old)
        temporary = path.parent / (".wiki-" + secrets.token_hex(16))
        with _private_attributes() as attributes:
            handle = _open(temporary, GENERIC_WRITE | DELETE, create=CREATE_NEW, attributes=attributes)
        published = False
        try:
            _security(handle, "private")
            offset = 0
            while offset < len(data):
                chunk = data[offset:offset + 65536]
                count = DWORD()
                _check(_WriteFile(handle, chunk, len(chunk), C.byref(count), None))
                if not count.value:
                    raise OSError("Native Windows write made no progress")
                offset += count.value
            _check(_FlushFileBuffers(handle))
            # Use the documented full-path/NULL-root form. Retained _walk
            # handles still prevent ancestor replacement during this rename.
            encoded = str(path).encode("utf-16-le")
            buffer = C.create_string_buffer(max(C.sizeof(RENAME_INFO), RENAME_INFO.name.offset + len(encoded) + 2))
            rename = C.cast(buffer, C.POINTER(RENAME_INFO)).contents
            rename.flags = FILE_RENAME_FLAG_REPLACE_IF_EXISTS | FILE_RENAME_FLAG_POSIX_SEMANTICS
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
                delete = W.BOOLEAN(True)
                # Delete our exact unpublished handle, never a substituted path.
                _SetFileInformation(handle, 4, C.byref(delete), C.sizeof(delete))
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


_CreatePipe = _bind(K, "CreatePipe", [C.POINTER(HANDLE), C.POINTER(HANDLE), C.POINTER(SECURITY_ATTRIBUTES), DWORD])
_SetHandleInformation = _bind(K, "SetHandleInformation", [HANDLE, DWORD, DWORD])
_CreateJobObject = _bind(K, "CreateJobObjectW", [P, W.LPCWSTR], HANDLE)
_SetInformationJobObject = _bind(K, "SetInformationJobObject", [HANDLE, C.c_int, P, DWORD])
_AssignProcessToJobObject = _bind(K, "AssignProcessToJobObject", [HANDLE, HANDLE])
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


def run_bounded(argv, *, cwd, env, timeout=20, limit=1048576):
    """Spawn suspended, assign a kill-on-close Job, then resume and bound stdout.

    The explicit inherited-handle list keeps unrelated private process handles
    out of the child. A Job is closed even after normal leader exit, so mise
    descendants retaining a stdout pipe cannot outlive this operation.
    """
    if (not isinstance(argv, (list, tuple)) or not argv or any(not isinstance(a, str) or "\x00" in a for a in argv)
            or type(limit) is not int or limit < 0 or timeout <= 0):
        raise ValueError("Invalid bounded process request")
    executable = shutil.which(argv[0], path=env.get("PATH"))
    if not executable or Path(executable).suffix.lower() not in (".exe", ".com"):
        raise ValueError("A native Windows executable is required; shell scripts are unsupported")
    if any(not isinstance(k, str) or not isinstance(v, str) or not k or "=" in k or "\x00" in k + v
           for k, v in env.items()):
        raise ValueError("Invalid process environment")
    environment = C.create_unicode_buffer("\x00".join(f"{k}={v}" for k, v in
                                sorted(env.items(), key=lambda item: item[0].upper())) + "\x00\x00")
    command = C.create_unicode_buffer(subprocess.list2cmdline([executable, *argv[1:]]))
    read, write = HANDLE(), HANDLE()
    null, job, attribute_list = None, None, None
    process = PROCESS_INFORMATION()
    inherited = SECURITY_ATTRIBUTES(C.sizeof(SECURITY_ATTRIBUTES), None, True)
    assigned = attributes_initialized = False
    try:
        _check(_CreatePipe(C.byref(read), C.byref(write), C.byref(inherited), 65536))
        _check(_SetHandleInformation(read, 1, 0))
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
        handles = (HANDLE * 2)(write.value, null)
        _check(_UpdateAttribute(attribute_list, 0, 0x20002, handles, C.sizeof(handles), None, None))
        startup = STARTUPINFOEX()
        startup.StartupInfo.cb = C.sizeof(startup)
        startup.StartupInfo.dwFlags = 0x100
        startup.StartupInfo.hStdInput, startup.StartupInfo.hStdOutput, startup.StartupInfo.hStdError = null, write.value, null
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
        _close(null)
        null = None
        output = bytearray()
        deadline, exited = time.monotonic() + timeout, False
        while True:
            available = DWORD()
            if not _PeekNamedPipe(read, None, 0, None, C.byref(available), None):
                error = C.get_last_error()
                if error == 109:  # All job-owned writers have closed the pipe.
                    break
                raise _error(error)
            if available.value:
                size = min(65536, available.value, limit + 1 - len(output))
                buffer, count = C.create_string_buffer(size), DWORD()
                _check(_ReadFile(read, buffer, size, C.byref(count), None))
                output.extend(buffer.raw[:count.value])
                if len(output) > limit:
                    raise ValueError("Process stdout exceeds the supported byte limit")
            if not exited:
                wait = _WaitForSingleObject(process.process, 0)
                if wait == 0xFFFFFFFF:
                    raise _error()
                if wait == 0:
                    exited = True
                    _close(job)  # Kill descendants on ordinary parent completion too.
                    job = None
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(argv, timeout)
            if not available.value:
                time.sleep(0.005)
        remaining = max(0, min(0xFFFFFFFE, int((deadline - time.monotonic()) * 1000)))
        if _WaitForSingleObject(process.process, remaining) != 0:
            raise subprocess.TimeoutExpired(argv, timeout)
        code = DWORD()
        _check(_GetExitCodeProcess(process.process, C.byref(code)))
        return subprocess.CompletedProcess(argv, code.value, bytes(output), b"")
    finally:
        _close(job)
        if process.process:
            if not assigned or _WaitForSingleObject(process.process, 0) != 0:
                _TerminateProcess(process.process, 1)
            _WaitForSingleObject(process.process, 5000)
        _close(process.thread)
        _close(process.process)
        _close(read.value)
        _close(write.value)
        _close(null)
        if attributes_initialized:
            _DeleteAttributes(attribute_list)
