"""Native Harness acceptance against disposable NTFS fixtures, never host sessions.

Linux collection is useful, but skipped cases are not native Windows evidence.
"""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import threading
import time
from types import FunctionType

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

pytestmark = pytest.mark.skipif(os.name != "nt", reason="requires actual native Windows execution")
ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin/scripts"
SERVER = ROOT / "plugin/mcp/harness_server.py"
SID = "windows-core-runtime-fixture"
SOURCE_TEXT = "VALUE = 'ok'\n"
sys.path.insert(0, str(SCRIPTS))
if os.name == "nt":
    import _wiki_windows as win
    import wiki_platform as platform
    import wbs


@pytest.fixture
def private(tmp_path):
    return win.ensure_private_directory(tmp_path / "runtime fixture 한글")


def _acl(path, grants=""):
    """Change only this test's synthetic DACL; do not assume POSIX chmod ACLs."""
    descriptor = win.P()
    sddl = f"O:{win.USER_SID}D:P(A;;FA;;;{win.USER_SID})(A;;FA;;;SY)(A;;FA;;;BA){grants}"
    win._check(win._ConvertStringSecurityDescriptor(sddl, 1, C.byref(descriptor), None))
    setter = win._bind(win.A, "SetFileSecurityW", [W.LPCWSTR, W.DWORD, win.P])
    try:
        win._check(setter(str(path), 4 | 0x80000000, descriptor))
    finally:
        win._LocalFree(descriptor)


def _junction(link, target):
    # cmd emits the native console code page, independently of Python's UTF-8
    # protocol streams. Keep these fixture-only command diagnostics as bytes.
    made = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(link), str(target)],
                          capture_output=True, timeout=10)
    assert made.returncode == 0, made.stderr.decode("mbcs", errors="replace")


def _environment():
    env = {key: value for key, value in os.environ.items()
           if not key.startswith("GIT_") and key not in {
               "CODEX_THREAD_ID", "CODEX_SESSION_ID", "HARNESS_SKIP_PREWRITE",
               "HARNESS_DISABLE_SCOPE_LOCK"}}
    env.update(HARNESS_RUNTIME="claude", HARNESS_SESSION_ID=SID,
               CLAUDE_SESSION_ID="", PYTHONDONTWRITEBYTECODE="1")
    return env


def _public(repo, action, **extra):
    requests = [
        dict(jsonrpc="2.0", id=1, method="initialize", params=dict(
            protocolVersion="2025-06-18", capabilities={},
            clientInfo=dict(name="native-windows-fixture", version="1"))),
        dict(jsonrpc="2.0", id=2, method="tools/call", params=dict(
            name="wbs", arguments=dict(action=action,
                **({} if action == "migrate" else dict(wbs_id="native")), **extra))),
    ]
    completed = subprocess.run(
        [sys.executable, "-B", str(SERVER)], cwd=repo, env=_environment(),
        input="".join(json.dumps(row) + "\n" for row in requests),
        capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    replies = [json.loads(line) for line in completed.stdout.splitlines() if line.startswith("{")]
    assert any(row.get("id") == 1 and "result" in row for row in replies), completed
    response = next(row for row in replies if row.get("id") == 2)
    assert "error" not in response, response
    return response["result"]


def _plan(command=None):
    command = command or [sys.executable, "-B", "-c",
                          f"from pathlib import Path; assert Path('source.py').read_text() == {SOURCE_TEXT!r}; print('native checked')"]
    return ("# Native fixture\n```wbs\n" + json.dumps([
        dict(id="A", title="Native protected runtime checks source", weight=1, check=command)
    ]) + "\n```\n```wbs-inputs\n" + json.dumps(dict(source=["source.py"])) + "\n```\n")


def _project(private):
    repo = win.ensure_private_directory(private / "checkout 中文")
    subprocess.run(["git", "init", "-q", str(repo)], check=True, capture_output=True, timeout=10)
    manifest = repo / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: library\nstrict_compliance_requires_delegation: true\n", encoding="utf-8")
    (repo / ".gitignore").write_text("doc/harness/wbs/\n", encoding="utf-8")
    (repo / "source.py").write_text(SOURCE_TEXT, encoding="utf-8")
    return repo


def _gate_process(repo, target):
    env = _environment()
    env["HARNESS_RUNTIME"] = "codex"
    payload = dict(cwd=str(repo), session_id=SID, tool_name="Write",
                   tool_input=dict(file_path=str(target), content="x = 1\n"))
    completed = subprocess.run(
        [sys.executable, "-E", "-s", "-B", str(SCRIPTS / "prewrite_gate.py")], cwd=repo,
        env=env, input=json.dumps(payload, ensure_ascii=False), capture_output=True, text=True,
        encoding="utf-8", timeout=10,
    )
    assert completed.returncode == 0 and not completed.stderr, completed
    return completed


def _gate(repo, target):
    completed = _gate_process(repo, target)
    return json.loads(completed.stdout)["hookSpecificOutput"] if completed.stdout.strip() else {}


def test_native_uninstalled_hooks_are_quiet_outside_git_and_at_nested_git_boundary(private, monkeypatch):
    home = win.ensure_private_directory(private / "isolated home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    outside = win.ensure_private_directory(private / "outside Git")
    initialized_parent = _project(private)
    nested = win.ensure_private_directory(initialized_parent / "independent checkout")
    subprocess.run(["git", "init", "-q", str(nested)], check=True,
                   capture_output=True, env=_environment(), timeout=10)
    before_entries = {path.relative_to(private) for path in private.rglob("*")}
    before_files = {path.relative_to(private): path.read_bytes()
                    for path in private.rglob("*") if path.is_file()}
    for cwd in (outside, nested):
        started = time.monotonic()
        completed = _gate_process(cwd, cwd / "source.py")
        assert time.monotonic() - started < 5
        assert completed.stdout == "" and completed.stderr == "", completed
    assert {path.relative_to(private) for path in private.rglob("*")} == before_entries
    assert {path.relative_to(private): path.read_bytes()
            for path in private.rglob("*") if path.is_file()} == before_files


def test_native_stdio_open_plan_check_assignment_and_stale_authority(private):
    from test_managed_wbs import host_snapshot

    repo = _project(private)
    opened = _public(repo, "open")
    assert not opened.get("isError"), opened
    run = opened["structuredContent"]["run_id"]
    directory = repo / "doc/harness/wbs/WBS__native"
    assert not _public(repo, "plan", plan=_plan()).get("isError")
    assert _public(repo, "open")["structuredContent"]["run_id"] == run
    assert _gate(repo, repo / "source.py").get("permissionDecision") == "deny"
    dispatched = _public(repo, "dispatch", ownership={"A": ["source.py"]}, host=host_snapshot())
    assert not dispatched.get("isError"), dispatched
    assert _gate(repo, repo / "source.py") == {}
    assert _gate(repo, repo / "outside.py").get("permissionDecision") == "deny"
    for name in ("WBS.control.json", "PLAN.md", "WBS.json"):
        assert _gate(repo, directory / name).get("permissionDecision") == "deny"
    state_path = directory / "WBS.json"
    original = state_path.read_bytes()
    state = json.loads(original)
    state["execution_admission"]["run_id"] = "00000000-0000-7000-8000-000000000000"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert _gate(repo, repo / "source.py").get("permissionDecision") == "deny"
    state_path.write_bytes(original)
    assert _gate(repo, repo / "source.py") == {}
    checked = _public(repo, "check", node_id="A")
    assert not checked.get("isError"), checked
    assert checked["structuredContent"]["passed"], checked
    assert "native checked" in checked["structuredContent"]["output"]


def test_native_oversized_plan_refuses_context_and_check_without_executing(private):
    repo = _project(private)
    marker = private / "verification command ran"
    command = [sys.executable, "-I", "-B", "-c",
               f"from pathlib import Path; Path({str(marker)!r}).write_bytes(b'checked')"]
    opened = _public(repo, "open")
    assert not opened.get("isError"), opened
    run = opened["structuredContent"]["run_id"]
    assert not _public(repo, "plan", plan=_plan(command)).get("isError")
    initial = _public(repo, "check", node_id="A")
    assert not initial.get("isError") and initial["structuredContent"]["passed"], initial
    assert marker.read_bytes() == b"checked"
    marker.unlink()
    directory = repo / "doc/harness/wbs/WBS__native"
    before = {name: (directory / name).read_bytes()
              for name in ("WBS.json", "WBS.control.json")}
    source_before = (repo / "source.py").read_bytes()
    target = directory / "PLAN.md"
    target.write_bytes(target.read_bytes() + b" " * (1024 * 1024))
    assert stat.S_ISREG(target.lstat().st_mode) and target.stat().st_size > 1024 * 1024

    observed = _public(repo, "context")
    assert observed.get("isError"), observed
    context = observed["structuredContent"]
    assert context.get("error"), context
    assert len(json.dumps(observed).encode("utf-8")) < 65536
    assert context["progress"]["status"] == "invalid", context
    assert context["progress"]["percentage"] is None, context
    assert context["source_write_allowed"] is False, context
    assert context["runtime_verdict"] != "PASS" and context["missing_for_close"], context
    refused = _public(repo, "check", node_id="A")
    assert refused.get("isError"), refused
    assert refused["structuredContent"].get("passed") is not True, refused
    assert _public(repo, "close").get("isError")
    assert not marker.exists()
    assert {name: (directory / name).read_bytes() for name in before} == before
    assert json.loads(before["WBS.control.json"])["run_id"] == run
    assert (repo / "source.py").read_bytes() == source_before


def test_native_future_case_variant_control_names_require_the_protected_writer(private):
    repo = _project(private)
    opened = _public(repo, "open")
    assert not opened.get("isError"), opened
    directory = repo / "doc/harness/wbs/WBS__native"
    assert not (directory / "PLAN.md").exists()
    assert not (directory / "WBS.json").exists()
    control_before = (directory / "WBS.control.json").read_bytes()
    targets = [directory / "PLAN.MD", directory / "WBS.JSON",
               repo / "doc/harness/wbs/.ACTIVE_SESSIONS/future-owner.JSON"]
    for target in targets:
        assert not target.exists(), "the Native alias probe must target an absent file"
        denied = _gate(repo, target)
        assert denied.get("permissionDecision") == "deny", denied
        assert "C-05-protected-artifact" in denied["permissionDecisionReason"], denied
        assert not target.exists()
    assert (directory / "WBS.control.json").read_bytes() == control_before


def test_native_ordinary_writable_and_hardlinked_source_can_be_measured(private):
    repo = _project(private)
    source = repo / "source.py"
    alias = repo / "ordinary alias.txt"
    os.link(source, alias)
    _acl(source, "(A;;GRGW;;;WD)")
    # Ordinary checkout bytes have no private metadata owner/DACL requirement.
    assert wbs.fingerprint(repo, dict(source=[source.name, alias.name]))
    assert not _public(repo, "open").get("isError")
    assert not _public(repo, "plan", plan=_plan()).get("isError")
    result = _public(repo, "check", node_id="A")
    assert not result.get("isError"), result
    assert result["structuredContent"]["passed"], result
    before = wbs.fingerprint(repo, dict(source=[source.name]))
    alias.write_text("changed through ordinary alias", encoding="utf-8")
    assert wbs.fingerprint(repo, dict(source=[source.name])) != before


def test_native_source_snapshot_streams_and_rejects_a_concurrent_change(private):
    source = private / "streamed source.txt"
    content = b"streaming source bytes" * 10000
    source.write_bytes(content)
    observed = []
    data, mode = platform.source_snapshot(source, len(content), consume=observed.append)
    assert data is None and isinstance(mode, int)
    assert b"".join(observed) == content and len(observed) > 1
    assert all(0 < len(chunk) <= 65536 for chunk in observed)
    with pytest.raises(ValueError):
        platform.source_snapshot(source, len(content) - 1, consume=lambda chunk: None)

    def fail_consumer(chunk):
        raise RuntimeError("test consumer budget exhausted")

    with pytest.raises(RuntimeError, match="test consumer budget exhausted"):
        platform.source_snapshot(source, len(content), consume=fail_consumer)
    # All retained handles must unwind even when the consumer rejects a chunk.
    moved = source.with_name("moved after consumer failure.txt")
    source.rename(moved)
    moved.rename(source)

    changed = False

    def change_during_read(chunk):
        nonlocal changed
        if not changed:
            changed = True
            with source.open("r+b") as writer:
                writer.write(b"changed source")

    with pytest.raises((ValueError, OSError, RuntimeError)):
        platform.source_snapshot(source, len(content), consume=change_during_read)
    assert changed


def test_native_unicode_hook_input_uses_actual_utf8_bytes(private):
    repo = _project(private)
    decision = _gate(repo, repo / "source 한글 中文.py")
    assert decision.get("permissionDecision") == "deny", decision
    assert "No active WBS" in decision["permissionDecisionReason"]


@pytest.mark.parametrize("unsafe", ["hardlink", "foreign-write", "duplicate-key"])
def test_native_public_control_refuses_unsafe_authority_without_repair(private, unsafe):
    repo = _project(private)
    assert not _public(repo, "open").get("isError")
    directory = repo / "doc/harness/wbs/WBS__native"
    target = directory / "WBS.control.json"
    if unsafe == "hardlink":
        os.link(target, private / "control alias")
    elif unsafe == "foreign-write":
        _acl(target, "(A;;GW;;;WD)")
    else:
        raw = target.read_text(encoding="utf-8")
        target.write_text(raw.rstrip().removesuffix("}") + ',"closed":true}\n', encoding="utf-8")
    before = target.read_bytes()
    denied = _public(repo, "context")
    assert denied.get("isError"), denied
    assert target.read_bytes() == before
    assert _gate(repo, repo / "source.py").get("permissionDecision") == "deny"


def test_native_readonly_metadata_copy_failure_disposes_owned_stage_and_preserves_error(private, monkeypatch):
    source = private / "closed source.bin"
    data = b"closed source bytes must survive failed readonly publication\x00\xff"
    source.write_bytes(data)
    destination = private / "unpublished readonly archive.bin"
    unrelated = private / ".wiki-unrelated"
    unrelated.write_bytes(b"preserve unrelated fixture")
    real_set = win._SetFileInformation

    def refuse_rename(handle, kind, buffer, size):
        if kind == win.FileRenameInfoEx:
            C.set_last_error(5)
            return False
        return real_set(handle, kind, buffer, size)

    monkeypatch.setattr(win, "_SetFileInformation", refuse_rename)
    with pytest.raises(PermissionError) as error:
        platform.metadata_copy(source, destination, max_bytes=len(data),
                               expected_sha256=hashlib.sha256(data).hexdigest(),
                               expected_size=len(data), readonly=True)
    assert error.value._wiki_operation == "FileRenameInfoEx"
    assert error.value._wiki_native_code == 5
    assert source.read_bytes() == data
    assert unrelated.read_bytes() == b"preserve unrelated fixture"
    assert not destination.exists()
    assert list(private.glob(".wiki-*")) == [unrelated]
    assert set(private.iterdir()) == {source, unrelated}


def test_native_canonical_control_writer_rejects_foreign_globals(private, monkeypatch):
    from test_harness_mcp_server import harness_lib, harness_server

    repo = _project(private)
    monkeypatch.chdir(repo)
    owner = harness_server.handle_wbs_open
    clone = FunctionType(owner.__code__, dict(owner.__globals__), owner.__name__, owner.__defaults__, owner.__closure__)
    clone.__kwdefaults__ = owner.__kwdefaults__
    with pytest.raises(PermissionError, match="WBS-control"):
        clone(dict(wbs_id="WBS__forged-native-owner"))
    assert not (repo / "doc/harness/wbs/WBS__forged-native-owner/WBS.control.json").exists()
    target = private / "not-authorized"
    with pytest.raises(PermissionError, match="WBS-control"):
        harness_lib.begin_wbs_run(str(target))
    assert not target.exists()


def test_native_reparse_wbs_parent_and_source_fail_closed(private):
    repo = _project(private)
    external = win.ensure_private_directory(private / "external target")
    (external / "precious.txt").write_bytes(b"preserve external bytes")
    junction = repo / "alias"
    _junction(junction, external)
    try:
        with pytest.raises((ValueError, OSError, RuntimeError)):
            wbs.fingerprint(repo, dict(source=["alias/precious.txt"]))
        wbs_parent = repo / "doc/harness/wbs"
        _junction(wbs_parent, external)
        try:
            assert _public(repo, "open").get("isError")
            assert sorted(path.name for path in external.iterdir()) == ["precious.txt"]
        finally:
            os.rmdir(wbs_parent)
        assert (external / "precious.txt").read_bytes() == b"preserve external bytes"
    finally:
        os.rmdir(junction)


def _legacy_migration_fixture(repo):
    import _lib

    old = repo / "doc/harness/tasks/TASK__native"
    old.mkdir(parents=True)
    control = dict(run_id=_lib.new_uuid7(), execution_mode="standard",
                   required_lenses=["review-code", "qa-cli"], closed=False)
    original_control = json.dumps(control).encode()
    (old / "TASK.json").write_bytes(original_control)
    state = dict(version=1, run_id=control["run_id"],
                 nodes=[dict(id="A", title="Preserved historical outcome", weight=1)],
                 checks={}, started_at=1, last_progress_at=1)
    (old / "WBS.json").write_text(json.dumps(state), encoding="utf-8")
    (old / "PLAN.md").write_text("# Historical native fixture\n", encoding="utf-8")
    return old, control, original_control, (old / "WBS.json").read_bytes()


def test_native_public_migration_preserves_history_after_collision_refusal(private):
    import wbs_namespace_migration as migration

    repo = _project(private)
    old, control, original_control, original_state = _legacy_migration_fixture(repo)
    historical = {name: b"opaque historical bytes\x00\xff " + name.encode()
                  for name in ("RECEIPTS.jsonl", "REVIEWS.jsonl", "artifact.bin")}
    for name, data in historical.items():
        (old / name).write_bytes(data)
    readonly_parent = old / "work/source"
    readonly_parent.mkdir(parents=True)
    readonly = readonly_parent / "readonly historical artifact.bin"
    readonly.write_bytes(b"preserve read-only source bytes\x00\xff")
    readonly.chmod(0o444)
    readonly_parent.chmod(0o555)
    assert readonly.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    assert readonly_parent.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    nested_lock = old / "work/.control.lock"
    nested_lock.write_bytes(b"nested literal historical lock evidence\x00\xff")
    preview = _public(repo, "migrate")
    assert not preview.get("isError"), preview
    assert (old / "TASK.json").read_bytes() == original_control
    assert not (repo / migration.MANIFEST).exists()

    new = repo / "doc/harness/wbs/WBS__native"
    new.mkdir(parents=True)
    collision = new / "precious-user-file"
    collision.write_bytes(b"preserve unrelated destination")
    refused = _public(repo, "migrate", apply=True)
    assert refused.get("isError"), refused
    assert collision.read_bytes() == b"preserve unrelated destination"
    assert (old / "TASK.json").read_bytes() == original_control
    assert (old / "WBS.json").read_bytes() == original_state
    assert not (repo / migration.MANIFEST).exists()
    collision.unlink()
    new.rmdir()

    applied = _public(repo, "migrate", apply=True)
    assert not applied.get("isError"), applied
    assert json.loads((new / "WBS.control.json").read_bytes()) == control
    assert (new / "WBS.json").read_bytes() == original_state
    for name, data in historical.items():
        assert (old / name).read_bytes() == data == (new / name).read_bytes()
    copied_readonly = new / readonly.relative_to(old)
    assert copied_readonly.read_bytes() == readonly.read_bytes() == b"preserve read-only source bytes\x00\xff"
    assert copied_readonly.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    assert copied_readonly.parent.stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY
    assert (new / "work/.control.lock").read_bytes() == nested_lock.read_bytes()
    archive = repo / migration.ARCHIVE / "original/doc/harness/tasks/TASK__native/TASK.json"
    assert archive.read_bytes() == original_control
    assert not (old / "TASK.json").exists()
    journal = (repo / migration.MANIFEST).read_bytes()
    published_control = (new / "WBS.control.json").read_bytes()
    repeated = _public(repo, "migrate", apply=True)
    assert not repeated.get("isError"), repeated
    assert (repo / migration.MANIFEST).read_bytes() == journal
    assert (new / "WBS.control.json").read_bytes() == published_control


@pytest.mark.parametrize("unsafe", ["nonempty", "hardlink", "junction"])
def test_native_public_migration_rejects_unsafe_root_control_lock(private, unsafe):
    import wbs_namespace_migration as migration

    repo = _project(private)
    old, _, original_control, original_state = _legacy_migration_fixture(repo)
    lock = old / ".control.lock"
    external = win.ensure_private_directory(private / "external lock evidence")
    sentinel = external / "preserve.txt"
    sentinel.write_bytes(b"untouched external lock evidence")
    if unsafe == "junction":
        _junction(lock, external)
    else:
        lock.write_bytes(b"nonempty lock evidence" if unsafe == "nonempty" else b"")
        if unsafe == "hardlink":
            os.link(lock, external / "lock alias")
    try:
        denied = _public(repo, "migrate", apply=True)
        assert denied.get("isError"), denied
        assert (old / "TASK.json").read_bytes() == original_control
        assert (old / "WBS.json").read_bytes() == original_state
        assert not (repo / migration.MANIFEST).exists()
        assert sentinel.read_bytes() == b"untouched external lock evidence"
        if unsafe != "junction":
            assert lock.read_bytes() == (b"nonempty lock evidence" if unsafe == "nonempty" else b"")
    finally:
        if unsafe == "junction":
            os.rmdir(lock)


@pytest.mark.parametrize("retained_history", [False, True])
def test_native_old_control_migration_retries_prepared_publication_without_retargeting(
        private, monkeypatch, retained_history):
    import _lib
    import goal_migration
    from test_harness_mcp_server import harness_server
    from test_receipt_free_migration import historical_control, legacy_control

    repo = _project(private)
    opened = _public(repo, "open")
    assert not opened.get("isError"), opened
    assert not _public(repo, "plan", plan=_plan()).get("isError")
    checked = _public(repo, "check", node_id="A")
    assert not checked.get("isError"), checked
    directory = repo / "doc/harness/wbs/WBS__native"
    current = json.loads((directory / "WBS.control.json").read_bytes())
    measured = {name: (directory / name).read_bytes() for name in ("PLAN.md", "WBS.json")}
    opaque = directory / "RECEIPTS.jsonl"
    opaque.write_bytes(b"opaque current-run history\x00\xff\n")
    legacy = legacy_control(directory)
    before = (directory / "WBS.control.json").read_bytes()
    historical = repo / "doc/harness/archive/batch/WBS__historical"
    historical_bytes = {}
    if retained_history:
        historical_control(historical)
        (historical / "RECEIPTS.jsonl").write_bytes(b"opaque pre-UUIDv7 history\x00\xff\n")
        historical_bytes = {name: (historical / name).read_bytes()
                            for name in ("WBS.control.json", "RECEIPTS.jsonl")}
    preview = _public(repo, "migrate")
    assert not preview.get("isError"), preview
    journal = repo / goal_migration.CONTROL_MANIFEST
    assert not journal.exists()
    interrupted = False

    def interrupt(*args, **kwargs):
        nonlocal interrupted
        interrupted = True
        raise OSError("fixture interruption before old-control conversion")

    # Enter the canonical public handler in the real disposable checkout;
    # only the publication fault is injected, with no authority/root bypass.
    with monkeypatch.context() as fault:
        fault.chdir(repo)
        for key in ("HARNESS_RUNTIME", "HARNESS_SESSION_ID", "CLAUDE_SESSION_ID"):
            fault.setenv(key, _environment()[key])
        fault.setattr(goal_migration, "_control_write", interrupt)
        result = harness_server.call_tool("wbs", dict(action="migrate", apply=True))
    assert result.get("isError"), result
    assert interrupted, result
    prepared = journal.read_bytes()
    assert json.loads(prepared)["status"] == "prepared"
    assert (directory / "WBS.control.json").read_bytes() == before

    # A prepared publication cannot be redirected to another run. Restore only
    # this disposable fixture's exact admitted bytes before the genuine retry.
    retargeted = dict(legacy, run_id=_lib.new_uuid7())
    (directory / "WBS.control.json").write_bytes(json.dumps(retargeted).encode("utf-8"))
    denied = _public(repo, "migrate", apply=True)
    assert denied.get("isError"), denied
    assert json.loads((directory / "WBS.control.json").read_bytes()) == retargeted
    assert journal.read_bytes() == prepared
    (directory / "WBS.control.json").write_bytes(before)

    for apply in (False, True, True):
        result = _public(repo, "migrate", apply=apply)
        assert not result.get("isError"), result
        report = result["structuredContent"]["wbs_control_migration"]
        retained = report.get("retained_historical_controls", [])
        if retained_history:
            assert retained == [dict(path=str(historical / "WBS.control.json"),
                reason="pre-UUIDv7 historical control retained; unavailable to current lifecycle")]
            for name, raw in historical_bytes.items():
                assert (historical / name).read_bytes() == raw
        else:
            assert retained == []
        for name, raw in measured.items():
            assert (directory / name).read_bytes() == raw
        assert opaque.read_bytes() == b"opaque current-run history\x00\xff\n"
    assert json.loads((directory / "WBS.control.json").read_bytes()) == current
    assert json.loads(journal.read_bytes())["status"] == "complete"
    assert not _public(repo, "context").get("isError")


def _native_closed_batch(private, monkeypatch):
    from test_batch_state import Pool, complete

    base = win.ensure_private_directory(private / "b")
    # Pool isolates HOME; Windows Path.home additionally consumes USERPROFILE.
    monkeypatch.setenv("USERPROFILE", str(base / "home"))
    pool = Pool(base, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    (directory / "RECEIPTS.jsonl").write_bytes(b"opaque historical receipt bytes\x00\xff")
    (directory / "evidence.bin").write_bytes(b"exact closed source evidence\x00\xff")
    readonly = directory / "readonly.bin"
    readonly.write_bytes(b"read-only closed evidence")
    readonly.chmod(0o444)
    originals = {name: (directory / name).read_bytes() for name in
                 ("WBS.control.json", "PLAN.md", "RECEIPTS.jsonl", "evidence.bin", "readonly.bin")}
    return pool, worktree, branch, directory, originals


@pytest.mark.parametrize("resume", ["same-run", "mismatched-archive"])
def test_native_batch_archive_pending_intent_survives_interruption_and_controls_retry(
        private, monkeypatch, capsys, resume):
    import batch_finish
    from test_batch_state import load_state_module

    pool, worktree, branch, directory, originals = _native_closed_batch(private, monkeypatch)
    owner = load_state_module()
    real_finish = batch_finish.finish
    interrupted_after_publication = False

    def interrupted(repo, args, checkpoint=None, **kwargs):
        assert checkpoint is not None, "saved batch owner must supply its checkpoint callback"

        def callback(stage, result):
            nonlocal interrupted_after_publication
            if stage == "harvested":
                interrupted_after_publication = True
                raise RuntimeError("fixture interruption after archive publication before harvested checkpoint")
            return checkpoint(stage, result)

        return real_finish(repo, args, checkpoint=callback, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(batch_finish, "finish", interrupted)
        assert owner.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    interrupted_output = capsys.readouterr()
    assert interrupted_after_publication, interrupted_output.out + interrupted_output.err
    saved = json.loads(pool.state.read_bytes())["requests"][0]
    checkpoint = saved["checkpoint"]
    assert checkpoint["stage"] == "integrated"
    assert checkpoint["closed"] is True
    assert checkpoint["run_id"] == json.loads(originals["WBS.control.json"])["run_id"]
    pending = checkpoint["archive_publication"]
    assert set(pending) == {"protocol", "source", "archived", "fingerprint"}
    # Windows accepts both native separators and the repository-relative
    # separator constants used by the owner. Keep exact path components and
    # require the actual source identity rather than a literal slash spelling.
    assert Path(pending["source"]) == directory
    assert Path(pending["source"]).samefile(directory)
    archive = pool.repo / "doc/harness/archive/batch/WBS__a"
    assert worktree.exists() and archive.is_dir()
    assert Path(pending["archived"]) == archive
    assert Path(pending["archived"]).samefile(archive)
    assert pending["protocol"] and pending["fingerprint"].startswith("sha256:")
    for name, expected in originals.items():
        assert (directory / name).read_bytes() == expected == (archive / name).read_bytes()
    assert (archive / "readonly.bin").stat().st_file_attributes & stat.FILE_ATTRIBUTE_READONLY

    if resume == "mismatched-archive":
        (archive / "evidence.bin").write_bytes(b"mismatched preexisting archive bytes")
        pool.cli("finish", "--slug", "a", "--resume", ok=False)
        assert worktree.exists()
        for name, expected in originals.items():
            assert (directory / name).read_bytes() == expected
        assert (archive / "evidence.bin").read_bytes() == b"mismatched preexisting archive bytes"
        retained = json.loads(pool.state.read_bytes())["requests"][0]["checkpoint"]
        assert retained["archive_publication"] == pending
        assert retained["stage"] == "integrated" and retained["run_id"] == checkpoint["run_id"]
    else:
        pool.cli("finish", "--slug", "a", "--resume")
        completed = json.loads(pool.state.read_bytes())["requests"][0]
        assert completed["status"] == "integrated"
        assert completed["checkpoint"]["stage"] == "harvested"
        assert completed["checkpoint"]["run_id"] == checkpoint["run_id"]
        assert "archive_publication" not in completed["checkpoint"]
        assert Path(completed["checkpoint"]["harvest"]["archived"]) == archive
        assert Path(completed["checkpoint"]["harvest"]["archived"]).samefile(archive)
        assert not worktree.exists()
        for name, expected in originals.items():
            assert (archive / name).read_bytes() == expected
        from test_batch_state import _git
        assert not _git("branch", "--list", branch, cwd=pool.repo)


def test_native_partial_residue_uses_saved_archive_proof_and_preserves_unknown_bytes(
        private, monkeypatch, capsys):
    import batch_finish
    import batch_harvest
    from test_batch_state import _git, load_state_module

    pool, worktree, _, directory, originals = _native_closed_batch(private, monkeypatch)
    # Capture the actual checkout representation; Git's native conversion
    # policy may make these working-tree bytes differ from the stored blob.
    tracked = worktree / "README.md"
    remainder = {tracked.relative_to(worktree): tracked.read_bytes(),
                 (directory / "evidence.bin").relative_to(worktree): originals["evidence.bin"]}
    owner = load_state_module()
    real_finish = batch_finish.finish
    saved_harvested = False

    def interrupted(repo, args, checkpoint=None, **kwargs):
        assert checkpoint is not None

        def callback(stage, result):
            nonlocal saved_harvested
            saved = checkpoint(stage, result)
            if stage == "harvested":
                saved_harvested = True
                raise RuntimeError("fixture interruption after durable harvested checkpoint")
            return saved

        return real_finish(repo, args, checkpoint=callback, **kwargs)

    with monkeypatch.context() as fault:
        fault.setattr(batch_finish, "finish", interrupted)
        assert owner.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    diagnostics = capsys.readouterr()
    assert saved_harvested, diagnostics.out + diagnostics.err
    checkpoint = json.loads(pool.state.read_bytes())["requests"][0]["checkpoint"]
    assert checkpoint["stage"] == "harvested" and checkpoint["closed"] is True
    assert checkpoint["run_id"] == json.loads(originals["WBS.control.json"])["run_id"]
    assert "archive_publication" not in checkpoint
    archive = Path(checkpoint["harvest"]["archived"])
    archive_before = {name: (archive / name).read_bytes() for name in originals}
    assert archive_before == originals
    batch_harvest.ordinary_removal_proof(str(pool.repo), str(worktree), directory.name,
                                       checkpoint=checkpoint)
    # Real Git removes both registration and administrative back-pointers.
    # Recreate only captured, proven bytes to model interruption mid-removal.
    _git("worktree", "remove", "--force", "--force", str(worktree), cwd=pool.repo)
    assert not worktree.exists()
    for relative, raw in remainder.items():
        destination = worktree / relative
        win.ensure_private_directory(destination.parent)
        destination.write_bytes(raw)
    assert batch_harvest.ordinary_unregistered(str(pool.repo), str(worktree))
    precious = worktree / directory.relative_to(worktree) / "precious.bin"
    precious.write_bytes(b"unique unpreserved user bytes\x00\xff")
    saved_state = pool.state.read_bytes()
    with pytest.raises(batch_harvest.HarvestError, match="unpreserved"):
        batch_harvest.remove_ordinary_residue(str(pool.repo), str(worktree), directory.name,
                                            checkpoint["integrated_tip"], checkpoint)
    assert precious.read_bytes() == b"unique unpreserved user bytes\x00\xff"
    for relative, raw in remainder.items():
        assert (worktree / relative).read_bytes() == raw
    assert pool.state.read_bytes() == saved_state
    for name, raw in archive_before.items():
        assert (archive / name).read_bytes() == raw
    precious.unlink()
    batch_harvest.remove_ordinary_residue(str(pool.repo), str(worktree), directory.name,
                                        checkpoint["integrated_tip"], checkpoint)
    assert not worktree.exists()
    assert pool.state.read_bytes() == saved_state
    for name, raw in archive_before.items():
        assert (archive / name).read_bytes() == raw


def test_native_batch_refuses_identical_archive_without_saved_publication_intent(private, monkeypatch, capsys):
    from test_batch_state import load_state_module

    pool, worktree, _, directory, originals = _native_closed_batch(private, monkeypatch)
    archive = pool.repo / "doc/harness/archive/batch/WBS__a"
    # This is a disposable caller-created copy, deliberately lacking owner
    # admission. Byte equality cannot establish its publication provenance.
    shutil.copytree(directory, archive, copy_function=shutil.copy2)
    owner = load_state_module()
    assert owner.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    refusal = capsys.readouterr().out.lower()
    assert "archive" in refusal and "intent" in refusal
    assert worktree.exists()
    for name, expected in originals.items():
        assert (directory / name).read_bytes() == expected == (archive / name).read_bytes()
    checkpoint = json.loads(pool.state.read_bytes())["requests"][0]["checkpoint"]
    assert checkpoint["stage"] == "integrated"
    assert "archive_publication" not in checkpoint


def test_native_standalone_finish_cli_uses_exact_saved_owner_and_preserves_unowned_source(private, monkeypatch):
    from test_batch_state import _git, _head, task, unbound_worktree

    pool, worktree, branch, directory, originals = _native_closed_batch(private, monkeypatch)
    commit = _head(worktree)
    mismatched_commit = _head(pool.repo)
    assert mismatched_commit != commit
    foreign, foreign_branch = unbound_worktree(pool, "unowned")
    foreign_task = task(foreign, "unowned", closed=True)
    precious = foreign_task / "user.bin"
    precious.write_bytes(b"preserve unowned source")
    foreign_control = (foreign_task / "WBS.control.json").read_bytes()

    def invoke(tree, selected_branch, selected_wbs, selected_commit):
        return subprocess.run(
            [sys.executable, "-B", str(SCRIPTS / "batch_finish.py"), "--repo", str(pool.repo),
             "--worktree", str(tree), "--branch", selected_branch, "--wbs-id", selected_wbs,
             "--commit", selected_commit], cwd=pool.repo, env=_environment(),
            capture_output=True, text=True, encoding="utf-8", timeout=30,
        )

    before = pool.state.read_bytes()
    for target, selected_branch, selected_wbs, selected_commit in (
            (worktree, branch, "WBS__a", mismatched_commit),
            (foreign, foreign_branch, "WBS__unowned", _head(foreign))):
        refused = invoke(target, selected_branch, selected_wbs, selected_commit)
        assert refused.returncode != 0, refused.stdout + refused.stderr
        assert pool.state.read_bytes() == before
        assert worktree.exists() and foreign.exists()
        assert not (pool.repo / "doc/harness/archive/batch/WBS__a").exists()
        for name, expected in originals.items():
            assert (directory / name).read_bytes() == expected
    completed = invoke(worktree, branch, "WBS__a", commit)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert json.loads(completed.stdout)["status"] == "integrated"
    actual = json.loads(pool.state.read_bytes())["requests"][0]
    assert actual["status"] == "integrated" and actual["checkpoint"]["stage"] == "harvested"
    assert "archive_publication" not in actual["checkpoint"]
    assert not worktree.exists()
    archive = pool.repo / "doc/harness/archive/batch/WBS__a"
    for name, expected in originals.items():
        assert (archive / name).read_bytes() == expected
    assert foreign.exists() and precious.read_bytes() == b"preserve unowned source"
    assert (foreign_task / "WBS.control.json").read_bytes() == foreign_control
    assert _git("branch", "--list", foreign_branch, cwd=pool.repo)


@pytest.mark.parametrize("resume", ["same-run", "foreign-stage"])
def test_native_saved_pool_prepares_selected_submodule_after_interruption_without_adopting_foreign_stage(
        private, monkeypatch, capsys, resume):
    from test_batch_state import _git, _s1_pool, _s1_request, load_state_module, unbound_worktree
    import batch_submodules

    base = win.ensure_private_directory(private / "s")
    monkeypatch.setenv("USERPROFILE", str(base / "home"))
    pool = _s1_pool(base, monkeypatch)

    def diagnostic_cli(*arguments, ok=True):
        completed = subprocess.run(pool.argv(*arguments), capture_output=True)
        stdout = completed.stdout.decode("utf-8", errors="replace")
        stderr = completed.stderr.decode("utf-8", errors="replace")
        if (ok and completed.returncode != 0) or (not ok and completed.returncode == 0):
            (base / "s1-command.stdout").write_bytes(completed.stdout)
            (base / "s1-command.stderr").write_bytes(completed.stderr)
            pytest.fail(f"Native S1 batch command {arguments!r} exited {completed.returncode}\n"
                        f"stdout:\n{stdout}\nstderr:\n{stderr}", pytrace=False)
        if completed.returncode == 2:
            return {}
        body = json.loads(stdout)
        assert isinstance(body, dict) and "status" in body, body
        return body

    # Only this Native saved-pool fixture gets complete command diagnostics.
    monkeypatch.setattr(pool, "cli", diagnostic_cli)
    source_module = pool.repo / "libs/module0"
    source_gitdir = Path(_git("rev-parse", "--absolute-git-dir", cwd=source_module))
    configs = (pool.repo / ".git/config", source_gitdir / "config")
    original_configs = {path: path.read_bytes() for path in configs}
    original_source = (source_module / "code.txt").read_bytes()
    original_tip = _git("rev-parse", "HEAD", cwd=source_module)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = unbound_worktree(pool)
    owner = load_state_module()
    args = pool.argv("bind", "--slug", "a", "--worker-id", "worker-a",
                     "--worktree", worktree, "--branch", branch)[2:]
    real_rename = platform.metadata_rename_directory
    interrupted_after_git_publish = False

    def interrupt_after_git_publish(source, destination, *extra, **kwargs):
        nonlocal interrupted_after_git_publish
        result = real_rename(source, destination, *extra, **kwargs)
        item = json.loads(pool.state.read_bytes())["requests"][0]
        selected = item["submodule_manifest"]["modules"][0]
        if Path(destination) == Path(selected["private_gitdir"]):
            interrupted_after_git_publish = True
            raise OSError("fixture interruption after selected private Git publication")
        return result

    with monkeypatch.context() as fault:
        fault.setattr(platform, "metadata_rename_directory", interrupt_after_git_publish)
        assert owner.main(args) != 0
    output = capsys.readouterr()
    assert interrupted_after_git_publish, output.out + output.err
    saved = json.loads(pool.state.read_bytes())["requests"][0]
    selected = saved["submodule_manifest"]["modules"][0]
    assert saved["status"] == "reserved" and saved["worker_id"] == "worker-a"
    assert Path(saved["worktree"]) == worktree
    assert selected["phase"] == "owned"
    private_gitdir = Path(selected["private_gitdir"])
    assert private_gitdir.is_dir()
    identity = (private_gitdir.stat().st_dev, private_gitdir.stat().st_ino)
    assert not (worktree / "libs/module0/.git").exists()

    if resume == "foreign-stage":
        stage = private_gitdir.parent / "publish-worktree"
        stage.mkdir(exist_ok=True)
        precious = stage / "precious-user-file.bin"
        precious.write_bytes(b"retain foreign Native staging bytes\x00\xff")
        pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
                 "--worktree", worktree, "--branch", branch, ok=False)
        assert precious.read_bytes() == b"retain foreign Native staging bytes\x00\xff"
        retained = json.loads(pool.state.read_bytes())["requests"][0]
        assert retained["status"] == "reserved" and retained["worker_id"] == "worker-a"
        assert retained["submodule_manifest"]["modules"][0]["phase"] == "owned"
        assert not (worktree / "libs/module0/.git").exists()
    else:
        pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
                 "--worktree", worktree, "--branch", branch)
        completed = json.loads(pool.state.read_bytes())["requests"][0]
        assert completed["status"] == "running"
        assert completed["submodule_manifest"]["modules"][0]["phase"] == "prepared"
        module = worktree / "libs/module0"
        assert _git("rev-parse", "HEAD", cwd=module) == original_tip
        assert _git("symbolic-ref", "--short", "HEAD", cwd=module) == selected["branch"]
        assert Path(_git("rev-parse", "--absolute-git-dir", cwd=module)) == private_gitdir
        assert not (private_gitdir / "objects/info/alternates").exists()
        batch_submodules.validate(str(pool.repo), str(worktree), completed["submodule_manifest"])
    assert (private_gitdir.stat().st_dev, private_gitdir.stat().st_ino) == identity
    assert {path: path.read_bytes() for path in configs} == original_configs
    assert (source_module / "code.txt").read_bytes() == original_source
    assert _git("rev-parse", "HEAD", cwd=source_module) == original_tip


def _synthetic_native_session(private, monkeypatch):
    """Test-owned metadata follows the existing fixture contract, not a host identity."""
    import _lib
    import codex_hook_registration as registration

    cwd = _project(private)
    sid = _lib.new_uuid7()
    created = datetime.fromtimestamp(_lib.uuid7_timestamp_ms(sid) / 1000).astimezone()
    home = win.ensure_private_directory(private / "synthetic codex home")
    parent = home / "sessions" / f"{created:%Y/%m/%d}"
    parent.mkdir(parents=True)
    path = parent / f"rollout-fixture-{sid}.jsonl"
    metadata = (json.dumps(dict(type="session_meta", payload=dict(id=sid, cwd=str(cwd)))) + "\n").encode()
    path.write_bytes(metadata)
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("CODEX_THREAD_ID", sid)
    return registration, cwd, sid, path, metadata


def test_native_codex_first_metadata_authenticates_while_append_handle_is_open(private, monkeypatch):
    registration, cwd, sid, path, metadata = _synthetic_native_session(private, monkeypatch)
    # Real Codex grows the log; its entire size is not the first-line budget.
    with path.open("ab") as writer:
        event = b'{"type":"event_msg"}\n'
        writer.write(event * (registration.NATIVE_METADATA_MAX_BYTES // len(event) + 1))
        writer.flush()
        assert path.stat().st_size > registration.NATIVE_METADATA_MAX_BYTES
        assert registration.authenticate_native_session(sid, str(cwd)) == str(cwd)
        writer.write(b'{"type":"event_msg","payload":"later"}\n')
        writer.flush()
        assert registration.authenticate_native_session(sid, str(cwd)) == str(cwd)
    assert path.read_bytes().startswith(metadata)


def test_native_interrupted_cross_workspace_fence_denies_both_sources_without_rewriting_old_binding(
        private, monkeypatch):
    import _lib
    import codex_hook_registration as registration
    from test_codex_worktree_prewrite import setup
    from test_exact_session_focus import _gate as exact_gate
    from test_managed_wbs import host_snapshot
    from test_worktree_workspace import _native_session

    home = win.ensure_private_directory(private / "fence home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    main, linked = setup(private)
    text = "# Exact fence fixture\n```wbs\n" + json.dumps([
        dict(id="A", title="Exact source ownership", weight=1)]) + "\n```\n"
    directories = []
    for root in (main, linked):
        workspace = {} if root == main else dict(workspace=str(linked))
        assert not _public(main, "open", **workspace).get("isError")
        assert not _public(main, "plan", plan=text, **workspace).get("isError")
        assigned = _public(main, "dispatch", ownership={"A": ["source.py"]},
                           host=host_snapshot(), **workspace)
        assert not assigned.get("isError"), assigned
        directories.append(root / "doc/harness/wbs/WBS__native")
    main_task, linked_task = directories
    controls = {path: (path / "WBS.control.json").read_bytes() for path in directories}

    # Existing fixture helper creates owned synthetic session metadata only;
    # this does not use or claim integration with an official host session.
    sid = _native_session(private, monkeypatch, main)
    status = {}
    assert registration.register_native_wbs(
        str(main), sid, str(linked), str(linked_task), linked_task.name,
        json.loads(controls[linked_task])["run_id"], status_out=status,
    ), status
    assert _lib.resolve_session_wbs_binding(str(linked), sid)["wbs_dir"] == str(linked_task)
    assert exact_gate(main, linked / "source.py", sid) == {}
    old_marker = linked / "doc/harness/wbs/.active_sessions" / (sid + ".json")
    original_marker = old_marker.read_bytes()
    # A real NTFS reader permits reads/writes but denies DELETE sharing. The
    # first main fence can publish; atomic replacement of this old marker fails.
    reader = win._open(old_marker, win.GENERIC_READ,
                       share=win.FILE_SHARE_READ | win.FILE_SHARE_WRITE)
    try:
        assert not registration.register_native_wbs(
            str(main), sid, str(main), str(main_task), main_task.name,
            json.loads(controls[main_task])["run_id"], status_out=status,
        )
        assert len(_lib.read_active_session_marker(str(main), sid)["conflicts"]) == 2
        assert old_marker.read_bytes() == original_marker
        for root in (main, linked):
            assert not _lib.resolve_session_wbs_binding(str(root), sid)
            denied = exact_gate(main, root / "source.py", sid)
            assert denied.get("permissionDecision") == "deny", denied
        assert {path: (path / "WBS.control.json").read_bytes() for path in directories} == controls
    finally:
        win._close(reader)
    assert old_marker.read_bytes() == original_marker


@pytest.mark.parametrize("unsafe", ["wrong-id", "wrong-cwd", "missing-newline", "oversize", "hardlink", "foreign-write"])
def test_native_codex_metadata_never_authenticates_invalid_authority(private, monkeypatch, unsafe):
    registration, cwd, sid, path, metadata = _synthetic_native_session(private, monkeypatch)
    if unsafe == "wrong-id":
        path.write_bytes(metadata.replace(sid.encode(), b"00000000-0000-7000-8000-000000000000"))
    elif unsafe == "wrong-cwd":
        path.write_text(json.dumps(dict(type="session_meta", payload=dict(id=sid, cwd=str(private)))) + "\n", encoding="utf-8")
    elif unsafe == "missing-newline":
        path.write_bytes(metadata.rstrip(b"\n"))
    elif unsafe == "oversize":
        path.write_bytes(b" " * (registration.NATIVE_METADATA_MAX_BYTES + 1) + metadata)
    elif unsafe == "hardlink":
        os.link(path, private / "session alias")
    else:
        _acl(path, "(A;;GW;;;WD)")
    before = path.read_bytes()
    assert not registration.authenticate_native_session(sid, str(cwd))
    assert path.read_bytes() == before


def _dead(pid):
    opener = win._bind(win.K, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
    handle = opener(0x1000 | 0x100000, False, pid)
    if not handle:
        assert C.get_last_error() in (87, 1168), "could not inspect synthetic descendant"
        return
    try:
        assert win._WaitForSingleObject(handle, 2000) == 0, "assertion descendant survived cleanup"
    finally:
        win._close(handle)


@pytest.mark.parametrize("finish", ["success", "timeout", "overflow"])
def test_native_assertion_retains_bounded_merged_diagnostics_and_reaps_tree(private, finish):
    pidfile = private / "descendant.pid"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"Path({str(pidfile)!r}).write_text(str(p.pid)); "
        "print('native stdout',flush=True); print('native stderr',file=sys.stderr,flush=True); "
    )
    script += {"success": "pass", "timeout": "time.sleep(30)",
               "overflow": "sys.stdout.write('x'*65536); sys.stdout.flush(); time.sleep(30)"}[finish]
    started = time.monotonic()
    result = wbs._execute([sys.executable, "-B", "-c", script], str(private),
                          2 if finish == "timeout" else 10, output_limit=256, retain=256)
    assert time.monotonic() - started < 12
    assert "native stdout" in result["output"] and "native stderr" in result["output"]
    assert len(result["output"].encode()) <= 256
    assert result["error"] == {"success": None, "timeout": "timeout", "overflow": "output limit exceeded"}[finish]
    if finish == "success":
        assert result["returncode"] == 0
    assert pidfile.exists(), result
    _dead(int(pidfile.read_text()))


def test_native_assertion_cancellation_preserves_partial_output_and_reaps_tree(private):
    pidfile = private / "cancelled-descendant.pid"
    ready = private / "cancellation ready"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        "print('before cancellation',file=sys.stderr,flush=True); "
        f"Path({str(pidfile)!r}).write_text(str(p.pid)); Path({str(ready)!r}).touch(); time.sleep(30)"
    )
    started = time.monotonic()
    result = platform.execute_bounded(
        [sys.executable, "-B", "-c", script], cwd=private, env=_environment(),
        timeout=10, limit=1024, cancelled=ready.exists,
    )
    assert time.monotonic() - started < 5
    assert result["error"] and "before cancellation" in result["output"].decode()
    assert pidfile.exists(), result
    _dead(int(pidfile.read_text()))


def test_native_bounded_stdin_roundtrips_unicode_json_without_request_files(private):
    value = dict(message="한글 中文 😀", checkout=str(private), values=[1, False, None])
    encoded = json.dumps(value, ensure_ascii=False).encode("utf-8")
    script = ("import json,sys; value=json.load(sys.stdin.buffer); "
              "sys.stderr.buffer.write(b'stdin transport stderr\\n'); sys.stderr.buffer.flush(); "
              "sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))")
    before = set(private.iterdir())
    before_threads = set(threading.enumerate())
    result = platform.execute_bounded(
        [sys.executable, "-B", "-c", script], cwd=private, env=_environment(),
        timeout=10, limit=4096, input_bytes=encoded,
    )
    assert result["returncode"] == 0 and not result["error"], result
    assert result["output"] == b"stdin transport stderr\n" + encoded
    assert not [thread for thread in threading.enumerate()
                if thread not in before_threads and thread.is_alive()], "stdin writer outlived normal exit"
    null_stdin = platform.run_bounded(
        [sys.executable, "-B", "-c", "import sys; assert sys.stdin.buffer.read()==b''; sys.stdout.buffer.write(b'private stdin NUL')"],
        cwd=private, env=_environment(), timeout=10, limit=128,
    )
    assert null_stdin.returncode == 0 and null_stdin.stdout == b"private stdin NUL"
    assert set(private.iterdir()) == before


@pytest.mark.parametrize("invalid", ["text", bytearray(b"x"), memoryview(b"x"), 1, True, "oversized"])
def test_native_bounded_stdin_rejects_invalid_inputs_before_spawn(private, invalid):
    if invalid == "oversized":
        invalid = b"x" * (2 * 1024 * 1024 + 1)
    sentinel = private / "unexpected input consumer"
    script = "from pathlib import Path; Path(" + repr(str(sentinel)) + ").touch()"
    with pytest.raises((TypeError, ValueError)):
        platform.execute_bounded(
            [sys.executable, "-B", "-c", script], cwd=private, env=_environment(),
            timeout=2, limit=128, input_bytes=invalid,
        )
    assert not sentinel.exists()


@pytest.mark.parametrize("finish", ["timeout", "cancel"])
def test_native_bounded_stdin_backpressure_returns_and_cleans_writer_and_processes(private, finish):
    pidfile = private / "stdin consumer pids.json"
    ready = private / "stdin consumer ready"
    script = (
        "import json,os,subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
        "stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"Path({str(pidfile)!r}).write_text(json.dumps([os.getpid(),p.pid])); "
        "print('input consumer deliberately never reads',file=sys.stderr,flush=True); "
        f"Path({str(ready)!r}).touch(); time.sleep(30)"
    )
    before_threads = set(threading.enumerate())
    started = time.monotonic()
    try:
        result = platform.execute_bounded(
            [sys.executable, "-B", "-c", script], cwd=private, env=_environment(),
            timeout=2 if finish == "timeout" else 10, limit=1024,
            cancelled=ready.exists if finish == "cancel" else None,
            input_bytes=b"x" * (2 * 1024 * 1024),
        )
        assert time.monotonic() - started < 6
        assert ready.exists(), result
        assert result["error"], result
        if finish == "timeout":
            assert result["error"] == "timeout"
        assert b"input consumer deliberately never reads" in result["output"]
        assert not [thread for thread in threading.enumerate()
                    if thread not in before_threads and thread.is_alive()], "stdin writer outlived its operation"
        for pid in json.loads(pidfile.read_text()):
            _dead(pid)
    finally:
        if ready.exists():
            for pid in json.loads(pidfile.read_text()):
                opener = win._bind(win.K, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
                handle = opener(1 | 0x100000, False, pid)
                if handle:
                    try:
                        if win._WaitForSingleObject(handle, 0) != 0:
                            win._TerminateProcess(handle, 1)
                            win._WaitForSingleObject(handle, 2000)
                    finally:
                        win._close(handle)


def test_native_bare_git_uses_explicit_path_without_implicit_repository_shadow(private, monkeypatch):
    import verify_runner

    actual_git = shutil.which("git")
    assert actual_git and Path(actual_git).suffix.lower() == ".exe", "native Git fixture is required"
    home = win.ensure_private_directory(private / "shadow lease home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = _project(private)
    shadow = repo / "git.exe"
    # A known Python binary is a benign, observable substitute, never user code.
    shutil.copyfile(sys.executable, shadow)
    env = _environment()
    env["PATH"] = os.pathsep.join([str(Path(actual_git).parent),
                                  str(Path(sys.executable).parent), env.get("PATH", "")])
    env["PYTHONHOME"] = sys.base_prefix
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    env = {key: value for key, value in env.items()
           if key.casefold() != "nodefaultcurrentdirectoryinexepath"}
    known_shadow = subprocess.run([str(shadow), "--version"], cwd=repo, env=env,
                                  capture_output=True, timeout=10)
    assert known_shadow.returncode == 0 and known_shadow.stdout.startswith(b"Python "), known_shadow
    expected = subprocess.run([actual_git, "--version"], cwd=repo, env=env,
                              capture_output=True, timeout=10, check=True)
    assert expected.stdout.startswith(b"git version ") and not expected.stderr
    # This is the actual MCP/assertion caller layout: process cwd is the checkout.
    monkeypatch.chdir(repo)
    result = platform.execute_bounded(["git", "--version"], cwd=repo, env=env,
                                      timeout=10, limit=4096)
    assert result["returncode"] == 0 and not result["error"], result
    assert result["output"] == expected.stdout, result
    # The public scheduler resolves commands before handing an absolute path to
    # _execute, so the already-tested process facade cannot repair its choice.
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("PYTHONHOME", env["PYTHONHOME"])
    item = dict(job_id="path-git", outcome_id="A", kind="consumer", argv=["git", "--version"],
                inputs=dict(source=["source.py"]), outputs=[], resources=[], requires=["executable:git"],
                environment=["PATH", "PYTHONHOME", "HOME", "USERPROFILE", "SYSTEMROOT", "PYTHONDONTWRITEBYTECODE"])
    scheduled = verify_runner.run_jobs(
        [item], repo, wbs_id="WBS__shadow-fixture", run_id="shadow-fixture-run",
        execution_generation="one", results_path=repo / "shadow-results.json", max_workers=1, timeout=20,
    )
    assert scheduled["status"] == "PASS", scheduled
    record = scheduled["commands"][0]
    assert record["returncode"] == 0 and not record["error"], record
    assert record["output"].strip() == expected.stdout.decode("utf-8").strip(), record


@pytest.mark.parametrize("channel", ["declared-pythonpath", "cwd-stdlib"])
def test_native_public_runner_ignores_declared_python_startup_code_in_trusted_helpers(private, monkeypatch, channel):
    import verify_runner

    actual_git = shutil.which("git")
    assert actual_git and Path(actual_git).suffix.lower() == ".exe", "native Git fixture is required"
    home = win.ensure_private_directory(private / "startup lease home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = _project(private)
    marker = private / "untrusted startup executed"
    if channel == "declared-pythonpath":
        startup = win.ensure_private_directory(private / "declared Python path")
        (startup / "sitecustomize.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_bytes(b'untrusted startup ran')\n", encoding="utf-8")
        # The actual user command retains its declared environment; trusted
        # Python helpers must independently isolate their interpreter startup.
        monkeypatch.setenv("PYTHONPATH", os.pathsep.join([str(startup), str(SCRIPTS)]))
    else:
        monkeypatch.delenv("PYTHONPATH", raising=False)
        # -E/-s alone still expose cwd to a -c helper's stdlib imports.
        (repo / "json.py").write_text(
            "from pathlib import Path\n"
            f"Path({str(marker)!r}).write_bytes(b'untrusted cwd json imported')\n"
            "raise RuntimeError('fixture cwd stdlib canary imported')\n", encoding="utf-8")
    argv = [actual_git, "--definitely-invalid-native-startup-fixture"]
    baseline = subprocess.run(argv, cwd=repo, env=_environment(), capture_output=True, timeout=10)
    assert baseline.returncode == 129 and not marker.exists(), baseline
    item = dict(job_id="startup-git", outcome_id="A", kind="consumer", argv=argv,
                inputs=dict(source=["source.py"]), outputs=[], resources=[], requires=["executable:git"],
                environment=["PATH", "PYTHONPATH", "HOME", "USERPROFILE", "SYSTEMROOT", "PYTHONDONTWRITEBYTECODE"])
    scheduled = verify_runner.run_jobs(
        [item], repo, wbs_id="WBS__startup-fixture", run_id="startup-fixture-run",
        execution_generation="one", results_path=repo / "startup-results.json", max_workers=1, timeout=20,
    )
    record = scheduled["commands"][0]
    assert scheduled["status"] == "FAIL", scheduled
    assert record["returncode"] == 129, record
    assert "forged helper result" not in record["output"], record
    assert not marker.exists(), f"trusted helper executed {channel} canary"


def test_native_retro_reads_real_utf8_git_commits_under_default_python_encoding(private, monkeypatch):
    home = win.ensure_private_directory(private / "retro home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    repo = _project(private)
    message = "Native 회고 소식 😀"
    message_file = private / "commit-message.txt"
    message_file.write_bytes((message + "\n").encode("utf-8"))
    subprocess.run(["git", "add", "."], cwd=repo, env=_environment(),
                   capture_output=True, check=True, timeout=10)
    committed = subprocess.run(
        ["git", "-c", "user.name=Native Fixture", "-c", "user.email=native@example.test",
         "-c", "i18n.commitEncoding=UTF-8", "commit", "-q", "--file", str(message_file)],
        cwd=repo, env=_environment(), capture_output=True, timeout=10,
    )
    assert committed.returncode == 0, committed.stderr
    code = (
        "import json,locale,sys; sys.path.insert(0,sys.argv[1]); import retro; "
        "value=dict(section=retro._section_commits(sys.argv[2],1),"
        "message=retro._git(['log','-1','--format=%s'],sys.argv[2]),"
        "encoding=locale.getencoding(),utf8_mode=sys.flags.utf8_mode); "
        "sys.stdout.buffer.write(json.dumps(value,ensure_ascii=False).encode('utf-8'))"
    )
    env = {key: value for key, value in _environment().items()
           if key.upper() not in {"PYTHONUTF8", "PYTHONIOENCODING"}}
    completed = subprocess.run([sys.executable, "-E", "-s", "-B", "-c", code, str(SCRIPTS), str(repo)],
                               cwd=repo, env=env, capture_output=True, timeout=20)
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    actual = json.loads(completed.stdout.decode("utf-8"))
    assert actual["message"] == message, actual
    assert "**1** commits" in actual["section"], actual


def test_native_scoped_verification_cancellation_reaps_nested_job(private):
    import verify_runner

    pidfile = private / "scoped descendant.pid"
    ready = private / "scoped ready"
    script = (
        "import subprocess,sys,time; from pathlib import Path; "
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
        f"Path({str(pidfile)!r}).write_text(str(p.pid)); Path({str(ready)!r}).touch(); time.sleep(30)"
    )
    cancelled = threading.Event()
    started = time.monotonic()

    def cancel_after_spawn():
        deadline = time.monotonic() + 10
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        cancelled.set()

    cancellation = threading.Thread(target=cancel_after_spawn)
    cancellation.start()
    try:
        with pytest.raises(ValueError, match="cancel|scoped execution"):
            value = dict(argv=[sys.executable, "-B", "-c", script], timeout=20,
                         log_path=str(private / "scoped execution.log"))
            verify_runner._scoped_call(verify_runner._SCOPED_EXECUTE_CODE, value,
                                      private, _environment(), cancelled=cancelled)
        assert ready.exists(), "scoped command never reached the cancellation boundary"
        assert time.monotonic() - started < 12
        _dead(int(pidfile.read_text()))
    finally:
        cancelled.set()
        cancellation.join(11)
    assert not cancellation.is_alive()


def test_native_shared_file_lease_release_retains_other_reader_and_case_alias_exclusion(private, monkeypatch):
    import verify_runner

    home = win.ensure_private_directory(private / "lease home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    outputs = win.ensure_private_directory(private / "Outputs")
    target = outputs / "FutureResult.bin"
    alias = Path(str(target).swapcase())
    assert not target.exists() and target.name != alias.name
    first_keys, alias_keys, writer_keys = {}, {}, {}
    verify_runner._path_leases(first_keys, target, False)
    verify_runner._path_leases(alias_keys, alias, False)
    verify_runner._path_leases(writer_keys, target, True)

    def child_claim():
        code = ("import json,sys; sys.path.insert(0,sys.argv[1]); import verify_runner as r; "
                "held=r._lease(json.loads(sys.argv[2])); print('busy' if held is None else 'acquired'); "
                "r._close_leases(held) if held is not None else None")
        result = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS), json.dumps(writer_keys)],
                                env=_environment(), capture_output=True, text=True, encoding="utf-8", timeout=10)
        assert result.returncode == 0 and not result.stderr, result
        return result.stdout.strip()

    first = second = writer = None
    try:
        first = verify_runner._lease(first_keys)
        second = verify_runner._lease(alias_keys)
        assert first is not None and second is not None
        assert child_claim() == "busy"
        verify_runner._close_leases(first)
        first = None
        # One compatible reader's release must not drop the other reader's
        # actual interprocess lock, including a differently cased future path.
        assert child_claim() == "busy"
        verify_runner._close_leases(second)
        second = None
        assert child_claim() == "acquired"
        writer = verify_runner._lease(writer_keys)
        assert writer is not None
        unexpected = verify_runner._lease(alias_keys)
        try:
            assert unexpected is None, "case alias escaped the exclusive file lease"
        finally:
            if unexpected is not None:
                verify_runner._close_leases(unexpected)
    finally:
        for held in (first, second, writer):
            if held is not None:
                verify_runner._close_leases(held)


def test_native_verify_runner_parent_death_releases_work_only_after_cleanup(private, monkeypatch):
    # Keep all host-wide lease files in this test's disposable profile.
    home = win.ensure_private_directory(private / "runner home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    resource = "native-fixture:" + str(private)
    code = (
        "from pathlib import Path\nimport json,os,subprocess,sys,time\n"
        "p=subprocess.Popen([sys.executable,'-B','-c','import time; time.sleep(30)'],"
        "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        "Path('runner-pids.json').write_text(json.dumps([os.getpid(),p.pid]))\n"
        "Path('runner-ready').touch()\n"
        "while not Path('release-old-command').exists(): time.sleep(.01)\n"
        "Path('old-command-finished').touch()\n"
    )
    script = private / "runner-owner.py"
    script.write_text(code, encoding="utf-8")
    item = dict(job_id="owner", outcome_id="A", kind="producer",
                argv=[sys.executable, "-B", script.name], inputs=dict(source=[script.name]),
                outputs=["old-command-finished"], resources=[resource])

    def argv(job, label):
        declaration = private / (label + "-jobs.json")
        declaration.write_text(json.dumps([job]), encoding="utf-8")
        return [sys.executable, "-B", str(SCRIPTS / "verify_runner.py"),
                "--root", str(private), "--jobs", str(declaration),
                "--results", str(private / (label + "-result.json")),
                "--wbs-id", "WBS__native-fixture", "--run-id", "native-fixture-run",
                "--generation", label, "--timeout", "20"]

    owner = subprocess.Popen(argv(item, "owner"), cwd=private, env=_environment(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    owner_output = None

    def collect_owner_diagnostics():
        nonlocal owner_output
        if owner.poll() is None:
            owner.kill()
        if owner_output is None:
            owner_output = owner.communicate(timeout=10)
        (private / "owner-process.stdout").write_bytes(owner_output[0])
        (private / "owner-process.stderr").write_bytes(owner_output[1])
        advisory = private / "owner-result.json"
        return ("stdout:\n" + owner_output[0].decode("utf-8", errors="replace")
                + "\nstderr:\n" + owner_output[1].decode("utf-8", errors="replace")
                + "\nadvisory result:\n" + (advisory.read_bytes().decode("utf-8", errors="replace") if advisory.exists() else "not published"))

    pids = []
    try:
        deadline = time.monotonic() + 15
        while not (private / "runner-ready").exists():
            if owner.poll() is not None:
                pytest.fail("native runner exited before command readiness; " + collect_owner_diagnostics())
            if time.monotonic() >= deadline:
                pytest.fail("native job failed to start within its fixture budget; " + collect_owner_diagnostics())
            time.sleep(0.01)
        pids = json.loads((private / "runner-pids.json").read_text())
        collect_owner_diagnostics()
        # A new public CLI claimant uses the same resource. At command admission
        # both the old writer and its redirected descendant must already be dead.
        probe = private / "runner-next-owner.py"
        probe.write_text(
            "import ctypes as C,json\nfrom ctypes import wintypes as W\n"
            "from pathlib import Path\nk=C.WinDLL('kernel32',use_last_error=True)\n"
            "k.OpenProcess.argtypes=[W.DWORD,W.BOOL,W.DWORD];k.OpenProcess.restype=W.HANDLE\n"
            "k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD]\n"
            "k.CloseHandle.argtypes=[W.HANDLE]\n"
            "for pid in json.loads(Path('runner-pids.json').read_text()):\n"
            " h=k.OpenProcess(0x1000|0x100000,False,pid)\n"
            " if h:\n"
            "  try: assert k.WaitForSingleObject(h,0)==0, 'prior writer survived new resource admission'\n"
            "  finally: k.CloseHandle(h)\n"
            " else: assert C.get_last_error() in (87,1168)\n"
            "print('new resource owner admitted after cleanup')\n", encoding="utf-8")
        claimant = dict(item, job_id="next-owner", kind="consumer",
                        argv=[sys.executable, "-B", probe.name],
                        inputs=dict(source=[probe.name]), outputs=[])
        completed = subprocess.run(argv(claimant, "next"), cwd=private, env=_environment(),
                                   capture_output=True, text=True, encoding="utf-8", timeout=30)
        assert completed.returncode == 0, completed.stdout + completed.stderr
        assert json.loads(completed.stdout)["status"] == "PASS"
        for pid in pids:
            _dead(pid)
        (private / "release-old-command").touch()
        assert not (private / "old-command-finished").exists()
        interrupted = json.loads((private / "owner-result.json").read_text())
        assert interrupted["status"] == "FAIL" and interrupted["successful"] == []
        assert interrupted["commands"][0]["returncode"] is None
    finally:
        collect_owner_diagnostics()
        # On a product failure, dispose only the exact synthetic processes whose
        # identities this fixture recorded; do not leave a thirty-second writer.
        for pid in pids:
            opener = win._bind(win.K, "OpenProcess", [W.DWORD, W.BOOL, W.DWORD], W.HANDLE)
            handle = opener(1 | 0x100000, False, pid)
            if handle:
                try:
                    if win._WaitForSingleObject(handle, 0) != 0:
                        win._TerminateProcess(handle, 1)
                        win._WaitForSingleObject(handle, 2000)
                finally:
                    win._close(handle)
