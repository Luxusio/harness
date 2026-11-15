"""Record and check the exact Windows processes launched by test fixtures."""
from contextlib import contextmanager
import ctypes as C
from ctypes import wintypes as W
from pathlib import Path


WINDOWS_PROCESS_IMPORT = (
    f"import sys; sys.path.insert(0, {str(Path(__file__).resolve().parent)!r}); "
    "from windows_process_fixture import process_identity, process_exited\n"
)

_kernel = C.WinDLL("kernel32", use_last_error=True)
_kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
_kernel.OpenProcess.restype = W.HANDLE
_kernel.GetProcessTimes.argtypes = [W.HANDLE, *([C.POINTER(W.FILETIME)] * 4)]
_kernel.WaitForSingleObject.argtypes = [W.HANDLE, W.DWORD]
_kernel.WaitForSingleObject.restype = W.DWORD
_kernel.TerminateProcess.argtypes = [W.HANDLE, W.UINT]
_kernel.CloseHandle.argtypes = [W.HANDLE]

_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SYNCHRONIZE = 0x100000
_PROCESS_TERMINATE = 1
_PROCESS_NOT_FOUND = (87, 1168)
_WAIT_OBJECT_0 = 0
_WAIT_TIMEOUT = 258


@contextmanager
def _opened_process(pid, *, terminate=False):
    access = _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE
    if terminate:
        access |= _PROCESS_TERMINATE
    handle = _kernel.OpenProcess(access, False, pid)
    if not handle:
        error = C.get_last_error()
        if error not in _PROCESS_NOT_FOUND:
            raise C.WinError(error)
    try:
        yield handle
    finally:
        if handle:
            _kernel.CloseHandle(handle)


def _creation_time(handle):
    created, exited, kernel, user = (W.FILETIME() for _ in range(4))
    if not _kernel.GetProcessTimes(handle, C.byref(created), C.byref(exited), C.byref(kernel), C.byref(user)):
        raise C.WinError(C.get_last_error())
    return (created.dwHighDateTime << 32) | created.dwLowDateTime


def process_identity(pid):
    """Capture while the fixture process is live or its creator holds a handle."""
    with _opened_process(pid) as handle:
        if not handle:
            raise ProcessLookupError(pid)
        return pid, _creation_time(handle)


@contextmanager
def _matching_process(identity):
    pid, created = identity
    with _opened_process(pid) as handle:
        yield handle if handle and _creation_time(handle) == created else None


def _exited(handle, timeout_ms):
    status = _kernel.WaitForSingleObject(handle, timeout_ms)
    if status not in (_WAIT_OBJECT_0, _WAIT_TIMEOUT):
        raise C.WinError(C.get_last_error())
    return status == _WAIT_OBJECT_0


def process_exited(identity, *, timeout_ms=0):
    with _matching_process(identity) as handle:
        return not handle or _exited(handle, timeout_ms)


def terminate_fixture_process(identity):
    with _matching_process(identity) as original:
        if not original or _exited(original, 0):
            return
        # The original handle pins this process object and PID while we
        # acquire termination rights. A reused PID never gets this far.
        with _opened_process(identity[0], terminate=True) as handle:
            if not handle or _exited(handle, 0):
                return
            if not _kernel.TerminateProcess(handle, 1):
                error = C.get_last_error()
                if not _exited(handle, 0):
                    raise C.WinError(error)
            if not _exited(handle, 2000):
                raise TimeoutError(f"fixture process {identity[0]} did not exit after termination")
