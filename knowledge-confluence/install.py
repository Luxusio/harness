#!/usr/bin/env python3
"""Build and register a standalone document plugin through the host CLI."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile


ROOT = Path(__file__).resolve().parent
PAYLOAD_DIRS = (".claude-plugin", ".codex-plugin", "skills", "agents", "scripts")


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
        market.write_text(json.dumps({"name": "knowledge-documents", "owner": {"name": "knowledge-documents"},
            "plugins": [{"name": "knowledge-confluence", "source": "./plugin"}]}, indent=2) + "\n")
        destination = parent / version
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
    if args.prepare_only:
        print(destination)
        return 0
    runtimes = args.runtime or [name for name in ("codex", "claude") if shutil.which(name)]
    if not runtimes:
        parser.error("No supported host CLI found; use --prepare-only for a portable payload")
    failed = False
    for runtime in runtimes:
        try:
            executable = shutil.which(runtime)
            if not executable:
                raise ValueError(f"{runtime} CLI is unavailable")
            subprocess.run([executable, "plugin", "marketplace", "add", str(destination)], check=True)
            operation = "add" if runtime == "codex" else "install"
            subprocess.run([executable, "plugin", operation, "knowledge-confluence@knowledge-documents"], check=True)
            print(f"{runtime.title()}: SUCCESS")
        except (OSError, ValueError, subprocess.CalledProcessError) as error:
            failed = True
            print(f"{runtime.title()}: FAILED\n  Error: {error}")
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
