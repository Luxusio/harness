#!/usr/bin/env python3
"""Build and register a standalone document plugin through the host CLI."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent
PAYLOAD_DIRS = (".claude-plugin", ".codex-plugin", "skills", "agents", "scripts")


def marketplace_bytes() -> bytes:
    return (json.dumps({"name": "knowledge-documents", "owner": {"name": "knowledge-documents"},
        "plugins": [{"name": "knowledge-confluence", "source": "./plugin"}]}, indent=2) + "\n").encode()


@contextmanager
def trusted_directory(path: Path):
    """Walk/create private storage without following links or writable ancestry."""
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Plugin storage must be an absolute canonical path")
    descriptor = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:]:
            try:
                os.mkdir(part, 0o700, dir_fd=descriptor)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            sticky = bool(info.st_mode & stat.S_ISVTX)
            if info.st_uid not in (0, os.getuid()) or info.st_mode & 0o022 and not sticky:
                raise ValueError("Unsafe plugin storage directory; preserve it and choose private storage")
        yield descriptor
    finally:
        os.close(descriptor)


def prepare(data_dir: Path, jar: Path) -> Path:
    if not jar.is_file() or jar.is_symlink():
        raise ValueError("Build knowledge-confluence.jar before installation")
    parent = data_dir / "packages"
    # Never replace a pre-existing plugin tree; each attempt has private custody.
    with trusted_directory(parent) as descriptor:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or info.st_mode & 0o022:
            raise ValueError("Plugin package directory must be private and owned by the installer")
        return _prepare(parent, descriptor, jar)


def _prepare(parent: Path, parent_descriptor: int, jar: Path) -> Path:
    with tempfile.TemporaryDirectory(prefix=".install-", dir=parent) as temporary:
        stage = Path(temporary)
        stage_identity = stage.stat()
        plugin = stage / "plugin"
        plugin.mkdir()
        for name in PAYLOAD_DIRS:
            shutil.copytree(ROOT / name, plugin / name, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        for name in (".mcp.json", "PROVENANCE.md", "README.md"):
            shutil.copyfile(ROOT / name, plugin / name)
        (plugin / "lib").mkdir()
        shutil.copyfile(jar, plugin / "lib/knowledge-confluence.jar")
        # Frozen verification inputs are read-only; only our private copies change.
        for path in plugin.rglob("*"):
            if path.is_symlink():
                raise ValueError("Plugin payload cannot contain symbolic links")
            path.chmod(0o700 if path.is_dir() else 0o600)
        digest = hashlib.sha256()
        interpreter = str(Path(sys.executable).resolve())
        digest.update((interpreter + "\0" + str(parent) + "\0").encode())
        for path in sorted(plugin.rglob("*")):
            if path.is_symlink():
                raise ValueError("Plugin payload cannot contain symbolic links")
            if path.is_file():
                digest.update(str(path.relative_to(plugin)).encode() + b"\0")
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        digest.update(chunk)
        version = "0.1.0+h" + digest.hexdigest()[:16]
        for name in (".claude-plugin/plugin.json", ".codex-plugin/plugin.json"):
            path = plugin / name
            manifest = json.loads(path.read_text())
            manifest["version"] = version
            path.write_text(json.dumps(manifest, indent=2) + "\n")
        market = stage / ".claude-plugin/marketplace.json"
        market.parent.mkdir()
        market.write_bytes(marketplace_bytes())
        destination = parent / version
        # Resolve transport paths during installation. Some hosts expand plugin
        # variables in hooks but leave them literal in MCP argument arrays.
        transport_path = plugin / ".mcp.json"
        transport = json.loads(transport_path.read_text())
        transport["mcpServers"]["confluence"]["command"] = interpreter
        transport["mcpServers"]["confluence"]["args"] = [
            "-I", str(destination / "plugin/scripts/launch.py"),
        ]
        transport_path.write_text(json.dumps(transport, indent=2) + "\n")
        if os.path.lexists(destination):
            with trusted_directory(destination):
                pass
            # Validate existing bytes rather than trusting a hash-shaped pathname.
            expected = {str(p.relative_to(stage)): p.read_bytes() for p in stage.rglob("*") if p.is_file()}
            actual = {str(p.relative_to(destination)): p.read_bytes() for p in destination.rglob("*") if p.is_file() and not p.is_symlink()}
            if destination.is_symlink() or expected != actual or any(p.is_symlink() for p in destination.rglob("*")):
                raise ValueError("Existing versioned plugin payload differs; preserve it and inspect")
        else:
            before = os.stat(stage.name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(before.st_mode) or (before.st_dev, before.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                raise ValueError("Private plugin staging directory changed")
            os.rename(stage.name, destination.name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
            after = os.stat(destination.name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(after.st_mode) or (after.st_dev, after.st_ino) != (stage_identity.st_dev, stage_identity.st_ino):
                raise ValueError("Published plugin directory changed; registration refused")
        return destination


@contextmanager
def marketplace(payload: Path):
    """Keep host registration stable while retaining immutable runtime versions."""
    if payload.parent.name != "packages" or not re.fullmatch(r"0\.1\.0\+h[0-9a-f]{16}", payload.name) or not payload.is_dir():
        raise ValueError("Use a prepared immutable plugin payload")
    with trusted_directory(payload):
        pass
    root = payload.parent.parent / "marketplace"
    with trusted_directory(root) as directory:
        owner = os.fstat(directory)
        if owner.st_uid != os.getuid() or owner.st_mode & 0o022:
            raise ValueError("Marketplace directory must be private and owned by the installer")
        lock = os.open("install.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=directory)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or info.st_nlink != 1:
                raise ValueError("Unsafe marketplace installation lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with trusted_directory(root / ".claude-plugin") as metadata:
                try:
                    descriptor = os.open("marketplace.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=metadata)
                except FileNotFoundError:
                    name = ".manifest-" + secrets.token_hex(8)
                    descriptor = os.open(name, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=metadata)
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(marketplace_bytes())
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.replace(name, "marketplace.json", src_dir_fd=metadata, dst_dir_fd=metadata)
                else:
                    with os.fdopen(descriptor, "rb") as stream:
                        info = os.fstat(stream.fileno())
                        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o022 or stream.read(65537) != marketplace_bytes():
                            raise ValueError("Existing marketplace metadata differs; preserve it and inspect")
            try:
                selected = os.stat("plugin", dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISLNK(selected.st_mode) or selected.st_uid != os.getuid():
                    raise ValueError("Existing marketplace selection is not installer-owned")
                target = Path(os.readlink("plugin", dir_fd=directory))
                if target.parent.parent != payload.parent or target.name != "plugin" or not re.fullmatch(r"0\.1\.0\+h[0-9a-f]{16}", target.parent.name):
                    raise ValueError("Existing marketplace selection points outside managed packages")
            name = ".plugin-" + secrets.token_hex(8)
            os.symlink(str(payload / "plugin"), name, target_is_directory=True, dir_fd=directory)
            try:
                os.replace(name, "plugin", src_dir_fd=directory, dst_dir_fd=directory)
                if os.readlink("plugin", dir_fd=directory) != str(payload / "plugin"):
                    raise ValueError("Marketplace selection changed; registration refused")
                yield root
            finally:
                try:
                    os.unlink(name, dir_fd=directory)
                except FileNotFoundError:
                    pass
        finally:
            os.close(lock)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", choices=("codex", "claude"), action="append")
    parser.add_argument("--skip-build", action="store_true", help="Use the already-built local JAR")
    parser.add_argument("--prepare-only", action="store_true", help="Prepare a local marketplace without registering it")
    args = parser.parse_args()
    if not args.skip_build:
        gradle = shutil.which("gradle")
        if not gradle:
            parser.error("Gradle is required to build; use --skip-build only after a verified build")
        subprocess.run([gradle, "--no-daemon", "shadowJar"], cwd=ROOT, check=True)
    base = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share")))
    if not base.is_absolute():
        parser.error("XDG_DATA_HOME must be absolute")
    destination = prepare(base / "knowledge-confluence", ROOT / "build/libs/knowledge-confluence.jar")
    runtimes = args.runtime or [name for name in ("codex", "claude") if shutil.which(name)]
    if not runtimes and not args.prepare_only:
        parser.error("No supported host CLI found; use --prepare-only for a portable payload")
    failed = False
    with marketplace(destination) as source:
        if args.prepare_only:
            print(source)
            return 0
        for runtime in runtimes:
            try:
                executable = shutil.which(runtime)
                if not executable:
                    raise ValueError(f"{runtime} CLI is unavailable")
                subprocess.run([executable, "plugin", "marketplace", "add", str(source)], check=True, timeout=60,
                               capture_output=True, text=True)
                operation = "add" if runtime == "codex" else "install"
                subprocess.run([executable, "plugin", operation, "knowledge-confluence@knowledge-documents"], check=True, timeout=60,
                               capture_output=True, text=True)
                print(f"{runtime.title()}: SUCCESS")
            except (OSError, ValueError, subprocess.SubprocessError) as error:
                failed = True
                detail = getattr(error, "stderr", None) or getattr(error, "stdout", None) or str(error)
                print(f"{runtime.title()}: FAILED\n  Error: {detail.strip()}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
