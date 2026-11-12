"""Actual native Windows platform invariants; skips are not Windows evidence."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import time

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires actual native Windows execution")
SCRIPTS = Path(__file__).resolve().parents[1] / "plugin/scripts"
if os.name == "nt":
    sys.path.insert(0, str(SCRIPTS))
    import _wiki_windows as win


@pytest.fixture
def private(tmp_path):
    return win.ensure_private_directory(tmp_path / "private Unicode 한글")


def _acl(path, grants):
    """Change only a synthetic fixture's DACL, using native Windows APIs."""
    descriptor = win.P()
    sddl = f"O:{win.USER_SID}D:P(A;;FA;;;{win.USER_SID})(A;;FA;;;SY)(A;;FA;;;BA){grants}"
    win._check(win._ConvertStringSecurityDescriptor(sddl, 1, C.byref(descriptor), None))
    setter = win._bind(win.A, "SetFileSecurityW", [W.LPCWSTR, W.DWORD, win.P])
    try:
        win._check(setter(str(path), 4 | 0x80000000, descriptor))
    finally:
        win._LocalFree(descriptor)


def _child_lock(path, shared=False):
    code = ("import sys; from pathlib import Path; sys.path.insert(0,sys.argv[1]); "
            "import _wiki_windows as w\n"
            "try:\n"
            " with w.file_lock(Path(sys.argv[2]),shared=sys.argv[3]=='1'): print('acquired')\n"
            "except BlockingIOError: print('busy')\n")
    return subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS), str(path), str(int(shared))],
                          capture_output=True, text=True, encoding="utf-8", timeout=10, check=True).stdout.strip()


def _dead(pid):
    opener = win._bind(win.K, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
    handle = opener(0x1000 | 0x100000, False, pid)
    if not handle:
        assert C.get_last_error() in (87, 1168), "could not verify synthetic descendant identity"
        return
    try:
        assert win._WaitForSingleObject(handle, 2000) == 0, "owned synthetic descendant survived cleanup"
    finally:
        win._close(handle)


def test_native_private_acl_unicode_atomic_snapshot_and_bound(private):
    path = private / "config 中文.json"
    win.atomic_write(path, "first 한글".encode("utf-8"))
    assert win.safe_read(path, 128, private=True) == "first 한글".encode("utf-8")
    old = win._open(path, win.GENERIC_READ)
    try:
        win.atomic_write(path, b"second")
        # A retained old handle remains the exact prior file through replacement.
        buffer, count = C.create_string_buffer(128), W.DWORD()
        win._check(win._ReadFile(old, buffer, 128, C.byref(count), None))
        assert buffer.raw[:count.value] == "first 한글".encode("utf-8")
    finally:
        win._close(old)
    assert win.safe_read(path, 6, private=True) == b"second"
    with pytest.raises(ValueError):
        win.safe_read(path, 5, private=True)
    win.atomic_write(private / "empty", b"")
    assert win.safe_read(private / "empty", 0, private=True) == b""
    assert not list(private.glob(".wiki-*"))


def test_native_permissive_acl_is_not_mistaken_for_private_mode(private):
    path = private / "settings"
    win.atomic_write(path, b"synthetic secret")
    _acl(path, "(A;;GR;;;WD)")
    assert win.safe_metadata_read(path, 100) == b"synthetic secret"
    with pytest.raises(ValueError):
        win.safe_read(path, 100, private=True)
    _acl(path, "(A;;GW;;;WD)")
    with pytest.raises(ValueError):
        win.safe_metadata_read(path, 100)
    # An ordinary project manifest deliberately has no confidentiality policy.
    assert win.safe_read(path, 100) == b"synthetic secret"


def test_native_null_dacl_is_rejected(private):
    path = private / "null-acl"
    win.atomic_write(path, b"fixture")
    descriptor = win.P()
    setter = win._bind(win.A, "SetFileSecurityW", [W.LPCWSTR, W.DWORD, win.P])
    win._check(win._ConvertStringSecurityDescriptor("D:NO_ACCESS_CONTROL", 1, C.byref(descriptor), None))
    try:
        win._check(setter(str(path), 4 | 0x80000000, descriptor))
    finally:
        win._LocalFree(descriptor)
    with pytest.raises(ValueError):
        win.safe_read(path, 100, private=True)


def test_native_hardlinks_and_unsafe_directory_acl_are_rejected(private):
    path, alias = private / "file", private / "hardlink"
    win.atomic_write(path, b"fixture")
    os.link(path, alias)
    with pytest.raises(ValueError):
        win.safe_read(path, 100, private=True)
    assert not win.same_file(path, alias)
    alias.unlink()
    _acl(private, "(A;;GW;;;WD)")
    with pytest.raises(ValueError):
        win.safe_read(path, 100, private=True)
    with pytest.raises(ValueError):
        win.atomic_write(path, b"changed")
    _acl(private, "")
    assert win.safe_read(path, 100, private=True) == b"fixture"


def test_native_junction_components_are_rejected_without_symlink_privilege(private):
    target = win.ensure_private_directory(private / "target")
    win.atomic_write(target / "data", b"target intact")
    junction = private / "junction"
    junction.mkdir()
    substitute, printed = ("\\??\\" + str(target)).encode("utf-16-le"), str(target).encode("utf-16-le")
    paths = substitute + b"\0\0" + printed + b"\0\0"
    raw = struct.pack("<LHHHHHH", 0xA0000003, len(paths) + 8, 0, 0, len(substitute),
                      len(substitute) + 2, len(printed)) + paths
    device_io = win._bind(win.K, "DeviceIoControl",
                          [W.HANDLE, W.DWORD, win.P, W.DWORD, win.P, W.DWORD, C.POINTER(W.DWORD), win.P])
    handle = win._open(junction, win.GENERIC_WRITE, directory=True)
    try:
        count = W.DWORD()
        win._check(device_io(handle, 0x900A4, raw, len(raw), None, 0, C.byref(count), None))
    finally:
        win._close(handle)
    try:
        with pytest.raises((ValueError, OSError)):
            win.safe_read(junction / "data", 100)
        with pytest.raises((ValueError, OSError)):
            win.ensure_private_directory(junction / "new")
        assert not win.same_file(target / "data", junction / "data")
    finally:
        junction.rmdir()
    assert win.safe_read(target / "data", 100, private=True) == b"target intact"
    assert not (target / "new").exists()


def test_native_lock_range_shared_and_exclusive_are_interprocess(private):
    lock = private / "sync.lock"
    with win.file_lock(lock):
        assert _child_lock(lock) == "busy"
        assert _child_lock(lock, shared=True) == "busy"
    with win.file_lock(lock, shared=True):
        assert _child_lock(lock) == "busy"
        assert _child_lock(lock, shared=True) == "acquired"
    assert _child_lock(lock) == "acquired"



def test_native_foreign_generic_all_cannot_control_a_lock(private):
    lock = private / "sync.lock"
    with win.file_lock(lock):
        pass
    _acl(lock, "(A;;GA;;;WD)")
    with pytest.raises(ValueError):
        with win.file_lock(lock):
            pytest.fail("foreign generic-all grant accepted")


def test_native_retained_directory_handle_prevents_component_substitution(private):
    moved = private.with_name(private.name + " moved")
    with win.trusted_metadata_directory(private) as observed:
        assert observed == private
        with pytest.raises(OSError):
            private.rename(moved)
    assert private.is_dir() and not moved.exists()


def test_native_source_identity_accepts_case_alias_and_rejects_other_file(private):
    path, other = private / "Source.toml", private / "other.toml"
    path.write_bytes(b"ordinary inherited file")
    other.write_bytes(b"ordinary inherited file")
    assert win.same_file(path, path.parent / "SOURCE.TOML")
    assert not win.same_file(path, other)
    assert not win.same_file(path, private / "missing")


def test_native_atomic_failure_preserves_old_bytes_and_removes_only_owned_temp(private, monkeypatch):
    path = private / "snapshot"
    win.atomic_write(path, b"old snapshot")
    original = win._SetFileInformation
    def fail_rename(handle, kind, *args):
        if kind == 3:
            C.set_last_error(5)
            return False
        return original(handle, kind, *args)
    monkeypatch.setattr(win, "_SetFileInformation", fail_rename)
    with pytest.raises(PermissionError):
        win.atomic_write(path, b"unpublished snapshot")
    assert win.safe_read(path, 100, private=True) == b"old snapshot"
    assert not list(private.glob(".wiki-*"))


def test_native_abi_layouts_match_documented_win32_structures():
    assert C.sizeof(W.DWORD) == 4 and C.sizeof(W.WCHAR) == 2
    pointer = C.sizeof(C.c_void_p)
    assert C.sizeof(win.SECURITY_ATTRIBUTES) == (24 if pointer == 8 else 12)
    assert win.RENAME_INFO.name.offset == (20 if pointer == 8 else 12)
    assert C.sizeof(win.OVERLAPPED) == (32 if pointer == 8 else 20)
    assert C.sizeof(win.STARTUPINFO) == (104 if pointer == 8 else 68)
    assert C.sizeof(win.STARTUPINFOEX) == (112 if pointer == 8 else 72)
    assert C.sizeof(win.JOB_EXTENDED_LIMIT) == (144 if pointer == 8 else 112)


def test_native_job_closes_descendants_even_after_leader_exit(private):
    code = ("import subprocess,sys; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "print(p.pid,flush=True)")
    start = time.monotonic()
    result = win.run_bounded([sys.executable, "-B", "-c", code], cwd=private,
                              env=dict(os.environ), timeout=5, limit=100)
    assert result.returncode == 0 and time.monotonic() - start < 5
    _dead(int(result.stdout))


@pytest.mark.parametrize("failure", ["timeout", "overflow"])
def test_native_job_cleanup_on_timeout_and_output_overflow(private, failure):
    pidfile = private / "descendant.pid"
    code = ("import pathlib,subprocess,sys,time; "
            "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(p.pid),encoding='ascii'); " +
            ("print('x'*8192,flush=True); " if failure == "overflow" else "") + "time.sleep(60)")
    error = subprocess.TimeoutExpired if failure == "timeout" else ValueError
    with pytest.raises(error):
        win.run_bounded([sys.executable, "-B", "-c", code, str(pidfile)], cwd=private,
                         env=dict(os.environ), timeout=1 if failure == "timeout" else 5, limit=256)
    assert pidfile.is_file(), "synthetic child must start before testing cleanup"
    _dead(int(pidfile.read_text(encoding="ascii")))


def test_native_subprocess_utf8_bytes_and_shell_script_rejection(private):
    expected = "café 中文 한글".encode("utf-8")
    result = win.run_bounded([sys.executable, "-B", "-c", f"import os; os.write(1,{expected!r})"],
                              cwd=private, env=dict(os.environ), timeout=5, limit=100)
    assert result.returncode == 0 and result.stdout == expected and result.stderr == b""
    script = private / "unsupported.cmd"
    script.write_text("exit /b 0", encoding="ascii")
    with pytest.raises(ValueError):
        win.run_bounded([str(script)], cwd=private, env=dict(os.environ))


def test_native_early_stdout_close_keeps_requested_execution_deadline(private):
    result = win.run_bounded([sys.executable, "-B", "-c", "import os,time;os.close(1);time.sleep(5.3)"],
                             cwd=private, env=dict(os.environ), timeout=9)
    assert result.returncode == 0 and result.stdout == b""


def _consumer_fixture(private):
    project = private / "consumer"
    win.ensure_private_directory(project / ".git")
    win.ensure_private_directory(project / "doc/harness")
    selected = {"backend": "atlassian", "enabled": True, "site": "https://fixture.invalid",
                "spaces": [{"key": "TEAM", "rootPageId": "456"}]}
    win.atomic_write(project / "doc/harness/manifest.yaml", json.dumps({"type": "library", "wiki": selected}).encode())
    env = dict(HOME=str(private), ATLASSIAN_BASE_URL="https://fixture.invalid",
               ATLASSIAN_EMAIL="fixture@example.invalid", ATLASSIAN_API_TOKEN="synthetic-native-only")
    identity = [selected["site"], env["ATLASSIAN_EMAIL"], "456", "TEAM"]
    partition = hashlib.sha256(json.dumps(identity, separators=(",", ":")).encode()).hexdigest()
    control = private / ".local/share/wiki-atlassian" / partition / "confluence/TEAM/.sync"
    win.ensure_private_directory(control)
    binding = hashlib.sha256(json.dumps([selected["site"], env["ATLASSIAN_EMAIL"], env["ATLASSIAN_API_TOKEN"]],
                                       separators=(",", ":")).encode()).hexdigest()
    win.atomic_write(control / "sync.lock", b"")
    win.atomic_write(control / "state.json", json.dumps({"credentialBinding": binding}).encode())
    win.atomic_write(control / "changes.json", json.dumps({"epoch": "1" * 32, "head": 1,
                     "events": [{"seq": 1, "kind": "updated", "id": "101", "version": 2}]}).encode())
    return project, env, control


def test_native_project_selector_and_private_feedback_sources_are_independent(private, monkeypatch):
    import document_destination as destination
    import personal_feedback as feedback

    project, env, _ = _consumer_fixture(private)
    assert destination.select_destination(env, cwd=project)["mode"] == "external"
    assert destination.select_destination(dict(env, ATLASSIAN_BASE_URL="https://other.invalid"), cwd=project)["mode"] == "unavailable"
    win.ensure_private_directory(private / ".config/harness")
    win.atomic_write(private / ".config/harness/feedback.yaml", json.dumps({"backend": "atlassian",
                     "page": "https://private.invalid/wiki/spaces/TEAM/pages/123"}).encode())
    def forbidden(*args, **kwargs):
        raise AssertionError("status must not authenticate or contact any service")
    monkeypatch.setattr(feedback, "_credentials", forbidden)
    monkeypatch.setattr(feedback, "_request", forbidden)
    status = feedback.perform("status", home=private, environment=env)
    assert status["status"] == "configured" and status["site"] == "https://private.invalid"


@pytest.mark.parametrize("fence", ["transaction.json", "notice-access-denied", "credential"])
def test_native_notice_transaction_denial_credentials_and_cursor(private, fence):
    import wiki_change_notice as notice

    project, env, control = _consumer_fixture(private)
    request = {"session_id": "native-session", "cwd": str(project)}
    if fence == "credential":
        blocked = dict(env, ATLASSIAN_API_TOKEN="different-synthetic-token")
    else:
        win.atomic_write(control / fence, b"")
        blocked = env
    assert notice.render_notice(request, "codex", blocked) == ""
    assert not (control / "harness-cursors.json").exists()
    if fence != "credential":
        (control / fence).unlink()
    assert "page 101" in notice.render_notice(request, "codex", env)
    assert (control / "harness-cursors.json").is_file()
    assert notice.render_notice(request, "codex", env) == ""


def test_native_notice_observes_other_process_snapshot_lock(private):
    import wiki_change_notice as notice

    project, env, control = _consumer_fixture(private)
    code = ("import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);import _wiki_windows as w\n"
            "with w.file_lock(Path(sys.argv[2])):\n print('locked',flush=True);sys.stdin.read()")
    child = subprocess.Popen([sys.executable, "-B", "-c", code, str(SCRIPTS), str(control / "sync.lock")],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    request = {"session_id": "native-lock-session", "cwd": str(project)}
    try:
        assert child.stdout.readline() == b"locked\r\n"
        assert notice.render_notice(request, "codex", env) == ""
        assert not (control / "harness-cursors.json").exists()
    finally:
        child.communicate(timeout=5)
    assert "page 101" in notice.render_notice(request, "codex", env)
