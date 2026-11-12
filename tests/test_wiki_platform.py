"""Real filesystem/process invariants; native-only details have their own suite."""
from pathlib import Path
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
