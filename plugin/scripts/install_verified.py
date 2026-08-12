#!/usr/bin/env python3
"""Install a stable, freshly reviewed Harness payload without persistent state."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

try:
    import fcntl
except ImportError:  # pragma: no cover - harness development is POSIX-first
    fcntl = None

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from _lib import (  # type: ignore  # noqa: E402
    _git_changed_paths,
    active_task_binding_matches,
    find_repo_root,
    read_task_control,
    receipt_snapshot,
    receipt_review_verdict,
    receipt_runtime_verdict,
    receipt_stream_fingerprint,
    receipt_stream_transaction,
)

CANONICAL_REMOTE = "https://github.com/Luxusio/harness"
PAYLOAD_ROOTS = (".claude-plugin", "plugin", "plugin-codex")
PAYLOAD_FILES = ("install.py", ".codex-version")
def _normalized_remote(value: str) -> str:
    return value.strip().removesuffix(".git").rstrip("/")


def _trusted_harness_repo(repo_root: Path) -> tuple[bool, str]:
    try:
        codex = json.loads((repo_root / "plugin-codex/.codex-plugin/plugin.json").read_text())
        claude = json.loads((repo_root / "plugin/.claude-plugin/plugin.json").read_text())
    except Exception as exc:
        return False, f"manifest unreadable: {exc}"
    if codex.get("name") != "harness" or claude.get("name") != "harness":
        return False, "plugin manifest name is not harness"
    if _normalized_remote(str(codex.get("repository") or "")) != CANONICAL_REMOTE:
        return False, "Codex manifest repository is not canonical harness"
    result = subprocess.run(
        ["git", "config", "--get", "remote.origin.url"],
        cwd=repo_root, capture_output=True, text=True, timeout=3,
    )
    remote = _normalized_remote(result.stdout) if result.returncode == 0 else ""
    if remote != CANONICAL_REMOTE:
        return False, f"origin is not canonical harness: {remote or '<missing>'}"
    if not (repo_root / "install.py").is_file():
        return False, "root install.py missing"
    return True, ""


def _verification_state(task_dir: Path) -> tuple[bool, str, str]:
    state = read_task_control(str(task_dir))
    snapshot = receipt_snapshot(str(task_dir))
    fingerprint = receipt_stream_fingerprint(str(task_dir), snapshot)
    if receipt_review_verdict(str(task_dir), state, snapshot) != "PASS":
        return False, "fresh review PASS required", fingerprint
    if receipt_runtime_verdict(str(task_dir), state, snapshot) != "PASS":
        return False, "fresh QA PASS after review required", fingerprint
    return True, "", fingerprint


def _is_install_payload_path(path: str) -> bool:
    rel = _normalize_payload_path(path)
    if not rel:
        return False
    return rel in PAYLOAD_FILES or any(rel == root or rel.startswith(root + "/") for root in PAYLOAD_ROOTS)


def _normalize_payload_path(path: str) -> str:
    """Return one exact repository-relative spelling or fail closed."""
    raw = str(path or "").replace("\\", "/")
    if raw.startswith("./"):
        raw = raw[2:]
    candidate = PurePosixPath(raw)
    parts = candidate.parts
    if (
        not raw
        or raw.startswith("/")
        or not parts
        or any(part in {"", ".", ".."} for part in parts)
        or ":" in parts[0]
        or "/".join(parts) != raw
    ):
        return ""
    return raw


def _read_payload_file(repo_root: Path, path: str) -> tuple[bytes, os.stat_result] | None:
    """Read one stable regular file through descriptor-relative no-follow opens."""
    rel = _normalize_payload_path(path)
    if not rel:
        return None
    parts = PurePosixPath(rel).parts
    directory_fd = -1
    file_fd = -1
    try:
        directory_fd = os.open(
            repo_root,
            os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
        )
        if not _trusted_payload_directory(directory_fd):
            return None
        for part in parts[:-1]:
            next_fd = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = next_fd
            if not _trusted_payload_directory(directory_fd):
                return None
        file_fd = os.open(
            parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW,
            dir_fd=directory_fd,
        )
        before = os.fstat(file_fd)
        mode = stat.S_IMODE(before.st_mode)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or mode not in {0o644, 0o755}
        ):
            return None
        chunks = []
        while True:
            chunk = os.read(file_fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        identity_before = (
            before.st_dev, before.st_ino, before.st_size, before.st_mode,
            before.st_mtime_ns, before.st_ctime_ns,
        )
        identity_after = (
            after.st_dev, after.st_ino, after.st_size, after.st_mode,
            after.st_mtime_ns, after.st_ctime_ns,
        )
        if identity_before != identity_after:
            return None
        return b"".join(chunks), after
    except OSError:
        return None
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        if directory_fd >= 0:
            os.close(directory_fd)


def _trusted_payload_directory(directory_fd: int) -> bool:
    """Require each descriptor-bound source directory to be owner-controlled."""
    try:
        current = os.fstat(directory_fd)
    except OSError:
        return False
    return bool(
        stat.S_ISDIR(current.st_mode)
        and current.st_uid in {os.getuid(), 0}
        and not stat.S_IMODE(current.st_mode) & 0o022
    )


def _payload_lstat(repo_root: Path, path: str) -> os.stat_result | None:
    record = _read_payload_file(repo_root, path)
    return record[1] if record else None


def _git_index_modes(repo_root: Path, paths: set[str]) -> dict[str, str] | None:
    if not paths:
        return {}
    result = subprocess.run(
        ["git", "ls-files", "--stage", "-z", "--", *sorted(paths)],
        cwd=repo_root, capture_output=True, timeout=5,
    )
    if result.returncode != 0:
        return None
    modes: dict[str, str] = {}
    for record in result.stdout.split(b"\0"):
        if not record or b"\t" not in record:
            continue
        meta, raw_path = record.split(b"\t", 1)
        fields = meta.split()
        if len(fields) >= 3 and fields[2] == b"0":
            modes[raw_path.decode("utf-8", errors="replace")] = fields[0].decode()
    return modes


def _payload_modes_match_index(repo_root: Path, paths: set[str]) -> bool:
    modes = _git_index_modes(repo_root, paths)
    if modes is None:
        return False
    for rel in paths:
        current = _payload_lstat(repo_root, rel)
        if current is None:
            return False
        expected = modes.get(rel)
        actual = stat.S_IMODE(current.st_mode)
        if expected == "100755":
            if actual != 0o755:
                return False
        elif expected in {"100644", None}:
            if actual != 0o644:
                return False
        else:
            return False
    return True


def _tracked_install_payload(repo_root: Path) -> set[str]:
    result = subprocess.run(
        ["git", "ls-files", "-z", "--", *PAYLOAD_FILES, *PAYLOAD_ROOTS],
        cwd=repo_root, capture_output=True, timeout=5,
    )
    if result.returncode != 0:
        return set()
    paths = {
        _normalize_payload_path(path.decode("utf-8", errors="replace"))
        for path in result.stdout.split(b"\0") if path
    }
    return {path for path in paths if path}


def _snapshot_paths(repo_root: Path, task_dir: Path) -> set[str]:
    candidates = _tracked_install_payload(repo_root) | _dirty_install_payload(repo_root)
    # A deletion remains absent from the isolated source snapshot; install.py
    # then prunes the stale target copy.
    return {
        path for path in candidates
        if os.path.lexists(repo_root / path)
    }


def _payload_fingerprint(repo_root: Path, paths: set[str]) -> str:
    digest = hashlib.sha256()
    for rel in sorted(paths):
        record = _read_payload_file(repo_root, rel)
        if record is None:
            return ""
        data, current = record
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(str(stat.S_IFMT(current.st_mode)).encode("ascii"))
        digest.update(b":")
        digest.update(str(stat.S_IMODE(current.st_mode)).encode("ascii"))
        digest.update(b"\0")
        digest.update(hashlib.sha256(data).digest())
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def _dirty_install_payload(repo_root: Path) -> set[str]:
    paths = {
        _normalize_payload_path(path)
        for path in _git_changed_paths(str(repo_root))
        if _is_install_payload_path(path)
    }
    return {path for path in paths if path}


def _validate_task_dir(repo_root: Path, task_dir: Path) -> tuple[bool, str]:
    expected_parent = (repo_root / "doc/harness/tasks").resolve()
    if task_dir.parent != expected_parent or not task_dir.name.startswith("TASK__"):
        return False, "task directory is not canonical"
    if not read_task_control(str(task_dir)):
        return False, "missing or invalid TASK.json"
    control = read_task_control(str(task_dir))
    if not active_task_binding_matches(str(repo_root), str(task_dir), control):
        return False, "task is not the open active TASK.json generation"
    return True, ""


def _copy_payload_snapshot(repo_root: Path, snapshot_root: Path, paths: set[str]) -> None:
    for rel in sorted(paths):
        record = _read_payload_file(repo_root, rel)
        if record is None:
            raise OSError(f"unsafe install payload path: {rel}")
        data, current = record
        target = snapshot_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        target.chmod(stat.S_IMODE(current.st_mode))


def _global_lock_path() -> Path:
    return Path.home() / ".cache/harness/install.lock"


def install_verified(task_dir: Path) -> int:
    task_dir = task_dir.resolve()
    repo_root = Path(find_repo_root(str(task_dir))).resolve()
    lock_path = _global_lock_path()
    task_dir.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+", encoding="utf-8") as lock:
        if fcntl is not None:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        trusted, reason = _trusted_harness_repo(repo_root)
        if not trusted:
            print(f"ERROR: automatic install refused: {reason}", file=sys.stderr)
            return 2
        with receipt_stream_transaction(str(task_dir)):
            valid_task, reason = _validate_task_dir(repo_root, task_dir)
            if not valid_task:
                print(f"ERROR: automatic install refused: {reason}", file=sys.stderr)
                return 2
            snapshot_paths = _snapshot_paths(repo_root, task_dir)
            verified, reason, fingerprint = _verification_state(task_dir)
            if not verified:
                print(f"ERROR: automatic install refused: {reason}", file=sys.stderr)
                return 3
            if not _payload_modes_match_index(repo_root, snapshot_paths):
                print("ERROR: install payload mode or file type does not match Git", file=sys.stderr)
                return 5
            payload_fingerprint = _payload_fingerprint(repo_root, snapshot_paths)
            if not payload_fingerprint:
                print("ERROR: unsafe or unreadable install payload", file=sys.stderr)
                return 5
            with tempfile.TemporaryDirectory(prefix="harness-install-snapshot-") as tmp:
                snapshot_root = Path(tmp)
                try:
                    _copy_payload_snapshot(repo_root, snapshot_root, snapshot_paths)
                except OSError as exc:
                    print(f"ERROR: payload changed while snapshot was captured: {exc}", file=sys.stderr)
                    return 5
                if _payload_fingerprint(snapshot_root, snapshot_paths) != payload_fingerprint:
                    print("ERROR: payload changed while snapshot was captured", file=sys.stderr)
                    return 5
                verified_before, reason_before, fingerprint_before = _verification_state(task_dir)
                payload_before = _payload_fingerprint(repo_root, snapshot_paths)
                if (
                    not verified_before
                    or fingerprint_before != fingerprint
                    or payload_before != payload_fingerprint
                    or not _payload_modes_match_index(repo_root, snapshot_paths)
                ):
                    print(
                        "ERROR: source or verification changed before install"
                        + (f": {reason_before}" if reason_before else ""),
                        file=sys.stderr,
                    )
                    return 5
                command = [sys.executable, str(snapshot_root / "install.py"), "--force"]
                result = subprocess.run(command, cwd=snapshot_root)
            if result.returncode != 0:
                print(f"ERROR: installer exited {result.returncode}", file=sys.stderr)
                return result.returncode
            verified_after, reason_after, fingerprint_after = _verification_state(task_dir)
            valid_after, task_reason_after = _validate_task_dir(repo_root, task_dir)
            snapshot_paths_after = _snapshot_paths(repo_root, task_dir)
            payload_after = _payload_fingerprint(repo_root, snapshot_paths_after)
            if (
                not verified_after
                or not valid_after
                or fingerprint_after != fingerprint
                or snapshot_paths_after != snapshot_paths
                or payload_after != payload_fingerprint
                or not _payload_modes_match_index(repo_root, snapshot_paths_after)
            ):
                print(
                    "ERROR: source or verification changed during install; install aborted"
                    + (f": {reason_after or task_reason_after}" if reason_after or task_reason_after else ""),
                    file=sys.stderr,
                )
                return 5
    print(f"automatic install PASS for {fingerprint}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", required=True)
    args = parser.parse_args()
    return install_verified(Path(args.task_dir))


if __name__ == "__main__":
    raise SystemExit(main())
