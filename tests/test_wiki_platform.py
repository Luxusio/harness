"""Real filesystem/process invariants; native-only details have their own suite."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import time

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required") from error

SCRIPTS = Path(__file__).resolve().parents[1] / "plugin/scripts"
sys.path.insert(0, str(SCRIPTS))
import wiki_platform as platform


@pytest.mark.skipif(os.name == "nt", reason="POSIX private stdin pump; Native Job stdin has its own regressions")
def test_private_helper_stdin_pumps_while_stdout_is_backpressured(tmp_path):
    payload = "한글😀".encode() * 100000
    code = ("import hashlib,sys;sys.stdout.buffer.write(b'x'*262144);sys.stdout.buffer.flush();"
            "data=sys.stdin.buffer.read();sys.stdout.buffer.write(hashlib.sha256(data).hexdigest().encode())")
    result = platform.run_bounded([sys.executable, "-I", "-B", "-c", code], cwd=tmp_path,
        env=dict(os.environ), input_bytes=payload, timeout=5, limit=262144 + 64)
    assert result.returncode == 0
    assert result.stdout == b"x" * 262144 + hashlib.sha256(payload).hexdigest().encode()
    empty = platform.run_bounded([sys.executable, "-I", "-B", "-c", "import sys;print(len(sys.stdin.buffer.read()))"],
        cwd=tmp_path, env=dict(os.environ), input_bytes=b"", timeout=5)
    assert empty.stdout == b"0\n"


@pytest.mark.skipif(os.name == "nt", reason="POSIX pipe delivery error; Native Job stdin has its own regressions")
def test_private_helper_rejects_undelivered_input_and_reaps_timeout(tmp_path):
    with pytest.raises(BrokenPipeError):
        platform.run_bounded([sys.executable, "-I", "-B", "-c", "import os,time;os.close(0);time.sleep(60)"],
            cwd=tmp_path, env=dict(os.environ), input_bytes=b"x" * 1048576, timeout=5)
    with pytest.raises(subprocess.TimeoutExpired):
        platform.run_bounded([sys.executable, "-I", "-B", "-c", "import time;time.sleep(60)"],
            cwd=tmp_path, env=dict(os.environ), input_bytes=b"x" * 1048576, timeout=.2)


def test_metadata_snapshot_binds_exact_bytes_and_file_identity(tmp_path):
    folder = platform.ensure_private_directory(tmp_path / "source Unicode 한글")
    target = folder / "source.py"
    raw = "# 원문\nvalue = 1\n".encode("utf-8")
    platform.atomic_write(target, raw)
    data, identity = platform.safe_metadata_snapshot(target, len(raw))
    assert data == raw and identity
    assert platform.safe_metadata_snapshot(target, len(raw)) == (data, identity)
    with pytest.raises(ValueError):
        platform.safe_metadata_snapshot(target, len(raw) - 1)
    with pytest.raises(ValueError):
        platform.safe_metadata_snapshot(target, -1)
    platform.atomic_write(target, raw)
    replaced, new_identity = platform.safe_metadata_snapshot(target, len(raw))
    assert replaced == raw and new_identity != identity


@pytest.mark.skipif(os.name != "nt", reason="retained installer inventory uses native Windows handles")
def test_metadata_inventory_hashes_nested_files_and_enforces_aggregate_budgets(tmp_path):
    root = platform.ensure_private_directory(tmp_path / "inventory 한글")
    child = platform.ensure_private_directory(root / "nested")
    platform.atomic_write(root / "empty", b"")
    raw = "source 한글".encode("utf-8")
    target = child / "source.py"
    platform.atomic_write(target, raw)
    entries = platform.metadata_tree_inventory(root, max_entries=3, max_bytes=len(raw))
    assert set(entries) == {"empty", "nested", "nested/source.py"}
    assert entries["nested"]["kind"] == "dir"
    assert entries["nested"]["size"] == 0 and entries["nested"]["sha256"] == ""
    assert entries["empty"]["kind"] == "file" and entries["empty"]["size"] == 0
    assert entries["empty"]["sha256"] == hashlib.sha256(b"").hexdigest()
    assert entries["nested/source.py"] == {
        "kind": "file", "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest(), "readonly": False,
    }
    with pytest.raises(ValueError):
        platform.metadata_tree_inventory(root, max_entries=2, max_bytes=len(raw))
    with pytest.raises(ValueError):
        platform.metadata_tree_inventory(root, max_entries=3, max_bytes=len(raw) - 1)
    target.chmod(0o400)
    try:
        assert platform.metadata_tree_inventory(root, max_entries=3, max_bytes=len(raw))["nested/source.py"]["readonly"]
    finally:
        target.chmod(0o600)


@pytest.mark.skipif(os.name == "nt", reason="POSIX link and foreign-writable mode fixtures; native ACL cases are separate")
def test_metadata_snapshot_rejects_unsafe_children(tmp_path):
    root = platform.ensure_private_directory(tmp_path / "private source")
    target = root / "source.py"
    platform.atomic_write(target, b"source")
    outside = platform.ensure_private_directory(tmp_path / "outside")
    platform.atomic_write(outside / "other.py", b"unrelated")
    hardlink = root / "hardlink"
    os.link(target, hardlink)
    with pytest.raises(ValueError):
        platform.safe_metadata_snapshot(target, 100)
    hardlink.unlink()
    source_alias = root / "source-alias.py"
    source_alias.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        platform.safe_metadata_snapshot(source_alias, 100)
    source_alias.unlink()
    alias = root / "alias"
    alias.symlink_to(outside, target_is_directory=True)
    with pytest.raises((ValueError, OSError)):
        platform.safe_metadata_snapshot(alias / "other.py", 100)
    alias.unlink()
    target.chmod(0o666)
    with pytest.raises(ValueError):
        platform.safe_metadata_snapshot(target, 100)
    assert target.read_bytes() == b"source" and (outside / "other.py").read_bytes() == b"unrelated"


def test_private_atomic_roundtrip_bounds_and_unicode(tmp_path):
    folder = platform.ensure_private_directory(tmp_path / "테스트 space")
    target = folder / "payload.json"
    platform.atomic_write(target, "첫 snapshot".encode())
    old = platform.safe_read(target, 128, private=True)
    with pytest.raises(ValueError):
        platform.safe_read(target, 1, private=True)
    platform.atomic_write(target, b"replacement")
    assert old.decode() == "첫 snapshot"
    assert platform.safe_read(target, 128, private=True) == b"replacement"
    assert not list(folder.glob(".wiki-*"))


@pytest.mark.skipif(os.name == "nt", reason="POSIX mode/descriptor invariants")
def test_posix_private_read_rejects_modes_hardlinks_symlinks_fifo(tmp_path):
    folder = platform.ensure_private_directory(tmp_path / "private")
    target = folder / "private.txt"
    platform.atomic_write(target, b"safe")
    target.chmod(0o644)
    with pytest.raises(ValueError):
        platform.safe_read(target, 128, private=True)
    target.chmod(0o600)
    os.link(target, folder / "hardlink")
    with pytest.raises(ValueError):
        platform.safe_read(target, 128)
    (folder / "hardlink").unlink()
    (folder / "alias").symlink_to(target)
    with pytest.raises(OSError):
        platform.safe_read(folder / "alias", 128)
    alias = tmp_path / "directory-alias"
    alias.symlink_to(folder, target_is_directory=True)
    with pytest.raises(OSError):
        platform.safe_read(alias / target.name, 128)
    os.mkfifo(folder / "fifo")
    started = time.monotonic()
    with pytest.raises(ValueError):
        platform.safe_read(folder / "fifo", 128)
    assert time.monotonic() - started < 1


@pytest.mark.skipif(os.name == "nt", reason="POSIX existing unsafe mode")
def test_directory_creation_never_repairs_existing_unsafe_tree(tmp_path):
    directory = tmp_path / "shared"
    directory.mkdir(mode=0o777)
    directory.chmod(0o777)
    with pytest.raises(ValueError):
        platform.ensure_private_directory(directory)
    assert directory.stat().st_mode & 0o777 == 0o777


def test_atomic_write_preserves_previous_bytes_on_failed_replace(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("Native handle rename interruption tested separately")
    folder = platform.ensure_private_directory(tmp_path / "private")
    target = folder / "state.json"
    platform.atomic_write(target, b"old")
    def interrupted(*args, **kwargs):
        raise OSError("synthetic interruption")
    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        platform.atomic_write(target, b"new")
    assert platform.safe_read(target, 128, private=True) == b"old"
    assert list(folder.iterdir()) == [target]


def _lock_probe(path):
    code = "import sys; sys.path.insert(0,sys.argv[1]); from wiki_platform import file_lock\ntry:\n with file_lock(sys.argv[2]): pass\nexcept BlockingIOError: sys.exit(7)"
    return subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPTS), str(path)],
                          capture_output=True, timeout=10).returncode


def test_lock_excludes_other_process_and_same_process_alias(tmp_path):
    folder = platform.ensure_private_directory(tmp_path / "private")
    lock = folder / "sync.lock"
    with platform.file_lock(lock):
        assert _lock_probe(lock) == 7
        with pytest.raises(BlockingIOError):
            with platform.file_lock(lock):
                pass
        assert _lock_probe(lock) == 7, "Rejected second acquisition must not release first record lock"
    assert _lock_probe(lock) == 0


@pytest.mark.skipif(os.name == "nt", reason="Existing FileChannel/hook POSIX record-lock interop")
def test_lock_interoperates_with_direct_posix_lockf(tmp_path):
    folder = platform.ensure_private_directory(tmp_path / "private")
    lock = folder / "sync.lock"
    lock.touch(mode=0o600)
    code = "import fcntl,sys; f=open(sys.argv[1],'r+'); fcntl.lockf(f,fcntl.LOCK_EX|fcntl.LOCK_NB)"
    with platform.file_lock(lock, shared=True):
        assert subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True).returncode != 0
    assert subprocess.run([sys.executable, "-c", code, str(lock)], capture_output=True).returncode == 0


def test_source_identity_is_file_identity_not_spelling(tmp_path):
    target = tmp_path / "source"
    target.write_bytes(b"source")
    assert platform.same_file(target, target.parent / "." / target.name)
    assert not platform.same_file(target, tmp_path / "missing")
    if os.name != "nt":
        alias = tmp_path / "alias"
        alias.symlink_to(target)
        assert not platform.same_file(target, alias)


def test_bounded_process_stdout_timeout_and_utf8(tmp_path):
    result = platform.run_bounded([sys.executable, "-c", "import sys;sys.stdout.buffer.write('한글'.encode())"],
                                  cwd=tmp_path, env=dict(os.environ))
    assert result.returncode == 0 and result.stdout.decode() == "한글"
    with pytest.raises(ValueError):
        platform.run_bounded([sys.executable, "-c", "import sys;sys.stdout.buffer.write(b'x'*1000000)"],
                             cwd=tmp_path, env=dict(os.environ), limit=1024)
    with pytest.raises(subprocess.TimeoutExpired):
        platform.run_bounded([sys.executable, "-c", "import time;time.sleep(60)"],
                             cwd=tmp_path, env=dict(os.environ), timeout=0.2)


@pytest.mark.skipif(os.name == "nt", reason="POSIX process group cleanup")
@pytest.mark.parametrize("inherited_stdout", [False, True])
def test_normal_exit_still_cleans_owned_descendants(tmp_path, inherited_stdout):
    child = "import time;time.sleep(60)"
    parent = "import subprocess,sys; p=subprocess.Popen([sys.executable,'-c',sys.argv[1]],stdout=subprocess.DEVNULL); print(p.pid)"
    if inherited_stdout:
        parent = parent.replace(",stdout=subprocess.DEVNULL", "")
    result = platform.run_bounded([sys.executable, "-c", parent, child], cwd=tmp_path, env=dict(os.environ), timeout=3)
    pid = int(result.stdout)
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        try:
            status = Path(f"/proc/{pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            return
        if status == "Z":
            return
        time.sleep(0.02)
    pytest.fail("Owned descendant survived helper completion")
