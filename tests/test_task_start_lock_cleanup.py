from pathlib import Path
import importlib.util
import sys


REPO = Path(__file__).resolve().parents[1]
SERVER = REPO / "plugin" / "mcp" / "harness_server.py"

spec = importlib.util.spec_from_file_location("harness_server", SERVER)
harness_server = importlib.util.module_from_spec(spec)
sys.modules["harness_server"] = harness_server
spec.loader.exec_module(harness_server)


def _repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    return root


def test_cleanup_orphan_index_lock_removes_zero_byte_lock(tmp_path):
    root = _repo(tmp_path)
    lock = root / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")

    assert harness_server._cleanup_orphan_index_lock(str(root)) is True
    assert not lock.exists()


def test_cleanup_orphan_index_lock_keeps_non_empty_lock(tmp_path):
    root = _repo(tmp_path)
    lock = root / ".git" / "index.lock"
    lock.write_text("active", encoding="utf-8")

    assert harness_server._cleanup_orphan_index_lock(str(root)) is False
    assert lock.exists()


def test_cleanup_orphan_index_lock_keeps_held_lock(tmp_path, monkeypatch):
    root = _repo(tmp_path)
    lock = root / ".git" / "index.lock"
    lock.write_text("", encoding="utf-8")

    class FakeFcntl:
        LOCK_EX = 1
        LOCK_NB = 2
        LOCK_UN = 4

        @staticmethod
        def flock(fd, flags):
            raise OSError("held")

    monkeypatch.setattr(harness_server, "fcntl", FakeFcntl)

    assert harness_server._cleanup_orphan_index_lock(str(root)) is False
    assert lock.exists()
