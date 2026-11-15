"""Literal host CLI argv shared by the two installers; never run npm shims."""
from __future__ import annotations

import os
from pathlib import Path
import shutil
import sys


_BOOTSTRAP = """import os,sys
p,count,entry=sys.argv[1:4]
p=os.path.normpath(p)
w=('\\\\\\\\?\\\\UNC\\\\'+p[2:]) if p.startswith('\\\\\\\\') else ('\\\\\\\\?\\\\'+p)
with open(w,'rb') as f: data=f.read(131073)
if len(data)>131072: raise ValueError('Native entry helper exceeds byte limit')
scope={'__name__':'_harness_native_entry','__file__':p}
exec(compile(data,p,'exec'),scope)
count=int(count)
scope['_run_owned_entry'](sys.argv[4:4+count],entry,sys.argv[4+count:])
"""


def owned_python_argv(interpreter, entrypoint, arguments=(), *, import_roots=()):
    """Launch one owned Python entry without exposing Win32 aliases publicly."""
    if os.name != "nt":
        entrypoint = Path(entrypoint).absolute()
        return [str(interpreter), "-E", "-s", "-B", str(entrypoint), *map(str, arguments)]
    from wiki_platform import _absolute
    if str(entrypoint).startswith("${CLAUDE_PLUGIN_ROOT}/"):
        root = "${CLAUDE_PLUGIN_ROOT}"
        helper = root + "/scripts/native_cli.py"
    else:
        entrypoint = _absolute(entrypoint)
        if str(entrypoint).startswith(("\\\\?\\", "\\\\.\\")):
            raise ValueError("Canonical owned entry point required")
        root = entrypoint.parent.parent
        helper = root / "scripts/native_cli.py"
    roots = [str(root), *(str(_absolute(path)) for path in import_roots)]
    return [str(interpreter), "-I", "-B", "-c", _BOOTSTRAP,
            str(helper), str(len(roots)), str(entrypoint), *roots, *map(str, arguments)]


def _run_owned_entry(import_roots, entrypoint, arguments):
    """Bridge only owned source imports; preserve normal source authority."""
    import importlib.machinery
    import importlib.util

    roots = tuple(Path(path) for path in import_roots)
    if not roots or any(not path.is_absolute() or ".." in path.parts
                        or str(path).startswith(("\\\\?\\", "\\\\.\\")) for path in roots):
        raise ValueError("Canonical owned import roots required")
    entrypoint = str(Path(entrypoint))
    if (not Path(entrypoint).is_absolute() or ".." in Path(entrypoint).parts
            or str(entrypoint).startswith(("\\\\?\\", "\\\\.\\"))
            or not Path(entrypoint).is_relative_to(roots[0])):
        raise ValueError("Entry point is outside its owned plugin")

    def wide(path):
        path = str(path)
        return "\\\\?\\UNC\\" + path[2:] if path.startswith("\\\\") else "\\\\?\\" + path

    snapshot = None

    class OwnedSourceLoader(importlib.machinery.SourceFileLoader):
        def get_data(self, path):
            if snapshot is not None:
                return snapshot(Path(path), 8 * 1024 * 1024)[0]
            # Bootstrap the existing platform primitives exactly as Python
            # normally imports them; subsequent reads use retained custody.
            if Path(path).name not in {"wiki_platform.py", "_wiki_windows.py"}:
                raise ImportError("Native source reader is not initialized")
            with open(wide(path), "rb") as stream:
                data = stream.read(8 * 1024 * 1024 + 1)
            if len(data) > 8 * 1024 * 1024:
                raise ImportError("Owned Python source exceeds byte limit")
            return data

        def get_code(self, fullname):
            return compile(self.get_data(self.path), self.path, "exec", dont_inherit=True)

    class OwnedSourceFinder:
        def find_spec(self, fullname, path=None, target=None):
            name = fullname.rsplit(".", 1)[-1]
            for directory in sys.path if path is None else path:
                parent = Path(directory)
                if not parent.is_absolute() or not any(parent.is_relative_to(root) for root in roots):
                    continue
                package = parent / name / "__init__.py"
                module = parent / (name + ".py")
                for candidate, locations in ((package, [str(package.parent)]), (module, None)):
                    if os.path.isfile(wide(candidate)):
                        loader = OwnedSourceLoader(fullname, str(candidate))
                        return importlib.util.spec_from_file_location(fullname, str(candidate), loader=loader,
                                                                      submodule_search_locations=locations)
            return None

    finder_index = next(i for i, finder in enumerate(sys.meta_path)
                        if finder is importlib.machinery.PathFinder)
    sys.meta_path.insert(finder_index, OwnedSourceFinder())
    # Explicit installed subjects precede the source-side smoke driver, so
    # platform primitives are imported from the tree actually being verified.
    selected = tuple(reversed(roots))
    sys.path[:0] = [*(str(root / "scripts") for root in selected), *map(str, selected), str(Path(entrypoint).parent)]
    # Imports execute owned code, so use its integrity/ownership policy rather
    # than the intentionally permissive reader for ordinary project inputs.
    from wiki_platform import safe_metadata_snapshot
    snapshot = safe_metadata_snapshot
    loader = OwnedSourceLoader("__main__", entrypoint)
    code = loader.get_code("__main__")
    namespace = sys.modules["__main__"].__dict__
    namespace.update(__name__="__main__", __file__=entrypoint, __spec__=None,
                     __package__=None, __loader__=loader, __cached__=None)
    sys.argv[:] = [entrypoint, *arguments]
    exec(code, namespace)


class HostUnavailableError(ValueError):
    """No supported host command was found."""


class HostUnverifiedError(ValueError):
    """No host command was found and a candidate could not be verified."""


def _npm_host_command(runtime, directory, env, executable, read, search_dirs):
    package = directory / "node_modules" / ("@openai/codex" if runtime == "codex" else "@anthropic-ai/claude-code")
    if runtime == "claude":
        candidates = [package / "bin/claude.exe"]
    else:
        architecture = env.get("PROCESSOR_ARCHITECTURE", "").upper()
        targets = {"AMD64": ("x64", "x86_64-pc-windows-msvc"), "ARM64": ("arm64", "aarch64-pc-windows-msvc")}
        arch = targets.get(architecture)
        candidates = []
        if arch:
            name, triple = arch
            roots = [package, package / "node_modules" / ("@openai/codex-win32-" + name),
                     directory / "node_modules" / ("@openai/codex-win32-" + name)]
            candidates = [root / "vendor" / triple / relative for root in roots
                          for relative in ("bin/codex.exe", "codex/codex.exe")]
    for candidate in candidates:
        resolved = executable(candidate)
        if resolved is not None:
            return [resolved]
    # Older official packages expose a JavaScript entrypoint, not a batch file.
    script = package / ("cli.js" if runtime == "claude" else "bin/codex.js")
    if read(script, 64 * 1024 * 1024) is not None:
        for node_dir in search_dirs:
            node = executable(node_dir / "node.exe")
            if node is not None:
                return [node, str(script)]
    return None


def host_command(runtime, *, cwd=None, env=None):
    """Return a launch argv, or distinguish absence from an incomplete search."""
    from wiki_platform import native_executable_at, safe_read
    if runtime not in ("codex", "claude"):
        raise ValueError("Unsupported host CLI")
    env = dict(os.environ) if env is None else env
    title = runtime.title()
    if os.name != "nt":
        executable = shutil.which(runtime, path=env.get("PATH", os.defpath))
        if executable:
            return [executable]
        raise HostUnavailableError(title + " CLI is unavailable")

    cwd = Path(cwd or os.getcwd()).absolute()
    first_problem = None

    def probe(operation, candidate, *args, **kwargs):
        nonlocal first_problem
        try:
            return operation(candidate, *args, **kwargs)
        except FileNotFoundError:
            return None
        except (OSError, ValueError) as error:
            if first_problem is None:
                first_problem = (candidate, error)
            return None

    def executable(candidate):
        return probe(native_executable_at, candidate)

    def read(candidate, limit):
        return probe(safe_read, candidate, limit)

    search_dirs = [Path(os.path.normpath(cwd / entry)) for entry in env.get("PATH", os.defpath).split(os.pathsep)]
    for directory in search_dirs:
        resolved = executable(directory / (runtime + ".exe"))
        if resolved is not None:
            return [resolved]

    for directory in search_dirs:
        if not any(read(directory / (runtime + suffix), 65536) is not None for suffix in (".cmd", ".bat")):
            continue
        command = _npm_host_command(runtime, directory, env, executable, read, search_dirs)
        if command is not None:
            return command

    if first_problem is not None:
        candidate, error = first_problem
        raise HostUnverifiedError(
            f"{title} CLI could not be verified. Could not inspect {candidate}: {error}. "
            f"Ensure a supported {title} installation is accessible on PATH."
        ) from error
    raise HostUnavailableError(f"{title} CLI is unavailable; install the native CLI or a supported npm layout")
