"""Select installed mise base Python; never borrow the installer's interpreter.

The CLI is only the portable marketplace bootstrap. Canonical installations
embed the result of ``select_python`` directly in their existing launchers.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time

# -I deliberately omits the script directory, including for the raw bootstrap.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wiki_platform import run_bounded

SELECTION_TIMEOUT = 2.0
_STORAGE = {"MISE_DATA_DIR", "MISE_CACHE_DIR", "MISE_CONFIG_DIR", "MISE_STATE_DIR",
            "MISE_INSTALLS_DIR", "MISE_INSTALL_STORE_DIR"}
_LAYOUT = {"MISE_EXPERIMENTAL", "MISE_INSTALL_LAYOUT"}
_ENVIRONMENT = {"HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SystemRoot",
                "SYSTEMROOT", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "TMPDIR",
                "USER", "LOGNAME", "LANG", "LC_ALL", "XDG_DATA_HOME",
                "XDG_CACHE_HOME", "XDG_CONFIG_HOME", "XDG_STATE_HOME"}
_lock = threading.RLock()
_cache = {}
_PROBE = '''import json,sys
result = dict(executable=sys.executable, prefix=sys.prefix,
              base_prefix=sys.base_prefix, version=list(sys.version_info[:3]))
if len(sys.argv) > 1:
    import yaml
    result["yaml"] = hasattr(yaml, "SafeLoader")
print(json.dumps(result))
'''


class MisePythonError(ValueError):
    """A deliberately nonsecret prerequisite failure."""


def _context():
    env = {key: value for key, value in os.environ.items()
           if key in _ENVIRONMENT or key in _STORAGE or key in _LAYOUT}
    env["MISE_AUTO_INSTALL"] = "0"
    home = _absolute(str(Path.home()))
    if not home.is_dir():
        raise ValueError()
    if os.name == "nt":
        from wiki_platform import native_executable
        try:
            mise = native_executable("mise", cwd=home, env=env)
        except (OSError, ValueError):
            mise = None
    else:
        mise = shutil.which("mise", path=env.get("PATH", os.defpath))
    if not mise:
        raise MisePythonError("Mise is required; install mise and a managed Python 3.12 or later.")
    mise = _absolute(mise)
    default_data = (Path(env["XDG_DATA_HOME"]) / "mise" if env.get("XDG_DATA_HOME") else
                    Path(env["LOCALAPPDATA"]) / "mise" if os.name == "nt" and env.get("LOCALAPPDATA") else
                    home / ".local/share/mise")
    data = _absolute(env.get("MISE_DATA_DIR", str(default_data)))
    roots = [data]
    for name in ("MISE_INSTALLS_DIR", "MISE_INSTALL_STORE_DIR"):
        if env.get(name):
            roots.append(_absolute(env[name]))
    # Only execution/storage context enters this process-local cache; no auth.
    key = (str(home), str(mise), tuple(sorted(env.items())))
    return home, mise, roots, env, key


def _absolute(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or any(c in str(value) for c in "\r\n\x00"):
        raise ValueError()
    return path.resolve(strict=True)


def select_python(*, require_yaml=False, _requested_version=None) -> str:
    """Return one validated installed base executable for the current home.

    A lock gives concurrent Codex/Claude projections the same selection. A
    dependency probe upgrades that cached selection rather than choosing again.
    All subprocess output and causes stay private, even in verbose installers.
    """
    try:
        with _lock:
            home, mise, roots, env, key = _context()
            if _requested_version not in (None, "3.12"):
                raise ValueError()
            key = (key, _requested_version)
            cached = _cache.get(key)
            if cached and (not require_yaml or cached[1]):
                return cached[0]
            deadline = time.monotonic() + SELECTION_TIMEOUT

            def run(argv, limit=8192):
                result = run_bounded(argv, cwd=home, env=env,
                                     timeout=deadline - time.monotonic(), limit=limit)
                if result.returncode:
                    raise ValueError()
                return result.stdout.decode("utf-8", errors="strict")

            if cached:
                executable = Path(cached[0])
                installation = Path(cached[2])
                version = cached[3]
            else:
                # `which python` fails for an installed but globally inactive
                # Python. `where` discovers it without changing activation.
                tool = "python" if _requested_version is None else "python@" + _requested_version
                installation = _absolute(run([str(mise), "-C", str(home), "where", tool]).strip())
                if not any(installation != root and installation.is_relative_to(root) for root in roots):
                    raise ValueError()
                # Mise owns layout and version metadata, including identity
                # stores whose directory names are hashes rather than versions.
                rows = json.loads(run([str(mise), "-C", str(home), "ls", "--json", "--installed", "python"], limit=65536))
                if not isinstance(rows, list):
                    raise ValueError()
                versions = [row["version"] for row in rows if isinstance(row, dict)
                            and row.get("installed") is True
                            and _absolute(row["install_path"]) == installation]
                if len(versions) != 1 or not re.fullmatch(r"3\.\d+\.\d+", versions[0]):
                    raise ValueError()
                version = versions[0]
                if tuple(map(int, version.split("."))) < (3, 12, 0):
                    raise ValueError()
                if _requested_version and not version.startswith(_requested_version + "."):
                    raise ValueError()
                executable = _absolute(run([str(mise), "-C", str(home), "which", "python",
                                            "--tool", "python@" + version]).strip())
                if not executable.is_relative_to(installation) or not executable.is_file():
                    raise ValueError()
            output = json.loads(run([str(executable), "-I", "-B", "-c", _PROBE,
                                     *(["yaml"] if require_yaml else [])]))
            if (not isinstance(output, dict) or
                    _absolute(output["executable"]) != executable or
                    _absolute(output["prefix"]) != installation or
                    _absolute(output["base_prefix"]) != installation or
                    not isinstance(output["version"], list) or len(output["version"]) != 3 or
                    any(type(part) is not int for part in output["version"]) or
                    tuple(output["version"]) != tuple(map(int, version.split(".")))):
                raise ValueError()
            if require_yaml and output.get("yaml") is not True:
                raise ValueError()
            selected = str(executable)
            _cache[key] = (selected, require_yaml or bool(cached and cached[1]), str(installation), version)
            return selected
    except MisePythonError as error:
        raise MisePythonError(str(error)) from None
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError):
        message = ("Mise Python 3.12 or later with PyYAML is required; install PyYAML in the selected managed Python."
                   if require_yaml else
                   "A valid installed mise base Python 3.12 or later is required; check the managed installation.")
        raise MisePythonError(message) from None


_ENTRYPOINTS = {"mcp/harness_server.py", "scripts/hook_session_start.py",
                "scripts/verification_gap_check.py", "scripts/project_format_check.py",
                "scripts/drift_warn.py", "scripts/prewrite_gate.py", "scripts/prompt_memory.py",
                "scripts/tool_routing.py", "scripts/hook_post_tool_use.py",
                "scripts/wiki_change_notice.py", "scripts/launch.py"}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    try:
        if not args:
            raise MisePythonError("An owned plugin entry point is required.")
        root = Path(__file__).resolve().parent.parent
        entrypoint = _absolute(args[0])
        relative = entrypoint.relative_to(root).as_posix()
        if relative not in _ENTRYPOINTS or not entrypoint.is_file():
            raise ValueError()
        interpreter = select_python(require_yaml=relative == "scripts/launch.py", _requested_version="3.12")
        flags = ["-I", "-B"] if relative == "scripts/launch.py" else ["-E", "-s", "-B"]
        os.execv(interpreter, [interpreter, *flags, str(entrypoint), *args[1:]])
    except MisePythonError as error:
        print(str(error), file=sys.stderr)
    except (OSError, ValueError, RuntimeError):
        print("An owned plugin entry point and valid mise Python are required.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
