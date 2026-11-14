"""Selection and portable bootstrap prove runtime identity independently."""
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "plugin/scripts"))
import mise_python


@pytest.mark.skipif(os.name != "nt", reason="requires actual native Windows command lookup")
def test_native_context_does_not_select_a_caller_directory_mise_shadow(tmp_path, monkeypatch):
    from _wiki_windows import ensure_private_directory

    candidates = [Path(directory) / "mise.exe" for directory in os.environ.get("PATH", "").split(os.pathsep)
                  if Path(directory).is_absolute()]
    actual = next((candidate for candidate in candidates if candidate.is_file()), None)
    assert actual is not None, "actual mise.exe must be present in the Native fixture PATH"
    caller = ensure_private_directory(tmp_path / "ordinary checkout 한글 with spaces")
    shadow = caller / "mise.exe"
    # A benign known binary is sufficient: this test resolves context only,
    # and never executes either the shadow or a modeled mise provider.
    shutil.copyfile(sys.executable, shadow)
    monkeypatch.chdir(caller)
    monkeypatch.delenv("NoDefaultCurrentDirectoryInExePath", raising=False)
    _, resolved, _, _, _ = mise_python._context()
    assert Path(resolved).samefile(actual)
    assert not Path(resolved).samefile(shadow)


@pytest.fixture
def provider(tmp_path, monkeypatch):
    """Literal mise/probe process results; never a native runtime claim."""
    home = tmp_path / "home"
    install = home / ".local/share/mise/installs/python/3.12.15"
    install.mkdir(parents=True)
    executable = install / "bin/python"
    executable.parent.mkdir()
    executable.touch()
    mise = tmp_path / "mise"
    mise.touch()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("MISE_DATA_DIR", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(mise_python.shutil, "which", lambda *args, **kwargs: str(mise))
    mise_python._cache.clear()
    calls = []
    probe = dict(executable=str(executable), prefix=str(install), base_prefix=str(install),
                 version=[3, 12, 15], yaml=True)

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if "where" in argv:
            output = str(install)
        elif "ls" in argv:
            output = json.dumps([dict(version="3.12.15", install_path=str(install), installed=True)])
        elif "which" in argv:
            output = str(executable)
        else:
            output = json.dumps(probe)
        return subprocess.CompletedProcess(argv, 0, (output + "\n").encode())

    monkeypatch.setattr(mise_python, "run_bounded", run)
    return home, install, executable, calls, probe, run


def test_selection_is_shared_base_and_caches_dependency_upgrade(provider, monkeypatch):
    home, install, executable, calls, _, _ = provider
    monkeypatch.setenv("VIRTUAL_ENV", "/hostile/venv")
    monkeypatch.setenv("PYTHONPATH", "/hostile/modules")
    monkeypatch.setenv("PYTHONHOME", "/hostile/home")
    monkeypatch.setenv("MISE_PYTHON_VERSION", "3.1")
    monkeypatch.setenv("MISE_CONFIG_FILE", "/secret/config")
    monkeypatch.setenv("MISE_ENV", "secret-env")
    monkeypatch.setenv("ATLASSIAN_API_TOKEN", "do-not-probe-this")
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert set(pool.map(lambda _: mise_python.select_python(), range(8))) == {str(executable)}
    assert len(calls) == 4
    assert calls[2][0][-2:] == ["--tool", "python@3.12.15"]
    assert mise_python.select_python(require_yaml=True) == str(executable)
    assert len(calls) == 5 and calls[-1][0][-1] == "yaml"
    assert mise_python.select_python(require_yaml=True) == str(executable)
    assert len(calls) == 5
    for argv, options in calls:
        assert options["cwd"] == home
        env = options["env"]
        assert env["MISE_AUTO_INSTALL"] == "0"
        assert not any(key.startswith("PYTHON") for key in env)
        assert not any(key in env for key in ("MISE_PYTHON_VERSION", "MISE_CONFIG_FILE", "MISE_ENV", "VIRTUAL_ENV", "ATLASSIAN_API_TOKEN"))
        assert 0 < options["timeout"] <= mise_python.SELECTION_TIMEOUT < 3
        assert options["limit"] == (65536 if "ls" in argv else 8192)


@pytest.mark.parametrize("setting", ["HOME", "MISE_DATA_DIR", "MISE_CONFIG_DIR", "MISE_CACHE_DIR", "MISE_STATE_DIR"])
def test_cache_tracks_home_and_legitimate_storage(provider, monkeypatch, tmp_path, setting):
    home, install, executable, calls, _, _ = provider
    if setting == "HOME":
        monkeypatch.setenv("MISE_DATA_DIR", str(home / ".local/share/mise"))
    assert mise_python.select_python() == str(executable)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.setenv(setting, str(other))
    if setting == "MISE_DATA_DIR":
        with pytest.raises(mise_python.MisePythonError):
            mise_python.select_python()
    else:
        assert mise_python.select_python() == str(executable)
    assert len(calls) > 4
    assert calls[-1][1]["env"][setting] == str(other)


@pytest.mark.parametrize("failure", ["missing-mise", "tool", "relative", "outside", "venv", "old", "identity", "yaml", "malformed", "oversize", "timeout", "path-loop"])
def test_invalid_selection_is_fixed_redacted_and_never_cached(provider, monkeypatch, failure):
    _, install, executable, calls, probe, run = provider
    if failure == "missing-mise":
        monkeypatch.setattr(mise_python.shutil, "which", lambda *a, **k: None)
    if failure == "venv":
        probe["prefix"] = str(install.parent)
    if failure == "old":
        probe["version"] = [3, 11, 9]
    if failure == "identity":
        probe["executable"] = str(install / "wrong-python")
    if failure == "yaml":
        probe["yaml"] = False

    def bad(argv, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(["secret-token"], 2, output=b"secret-token")
        if failure == "oversize":
            raise ValueError("secret-token exceeded output")
        if failure == "path-loop":
            raise RuntimeError("secret-token path resolution loop")
        result = run(argv, **kwargs)
        if failure == "tool":
            result.returncode = 1
            result.stdout = b"secret-token"
        elif failure == "relative" and "which" in argv:
            result.stdout = b"relative/python"
        elif failure == "outside" and "which" in argv:
            result.stdout = str(Path(sys.executable)).encode()
        elif failure == "malformed" and "-c" in argv:
            result.stdout = b"secret-token"
        return result

    monkeypatch.setattr(mise_python, "run_bounded", bad)
    with pytest.raises(mise_python.MisePythonError) as captured:
        mise_python.select_python(require_yaml=failure == "yaml")
    assert "secret-token" not in str(captured.value)
    assert captured.value.__suppress_context__
    assert not mise_python._cache


def test_relative_home_cannot_select_using_the_consumer_directory(provider, monkeypatch):
    _, _, _, calls, _, _ = provider
    monkeypatch.setenv("HOME", "relative-consumer-home")
    with pytest.raises(mise_python.MisePythonError):
        mise_python.select_python()
    assert not calls


def test_raw_requires_installed_requested_minor_even_when_another_minor_exists(provider, monkeypatch, capsys):
    home, installation, _, calls, _, run = provider
    other = installation.parent / "3.14.1"
    other.mkdir()
    def other_minor(argv, **kwargs):
        if "where" in argv:
            calls.append((argv, kwargs))
            return subprocess.CompletedProcess(argv, 0, str(other).encode())
        if "ls" in argv:
            return subprocess.CompletedProcess(argv, 0, json.dumps([dict(version="3.14.1", install_path=str(other), installed=True)]).encode())
        return run(argv, **kwargs)
    monkeypatch.setattr(mise_python, "run_bounded", other_minor)
    monkeypatch.setattr(mise_python.os, "execv", lambda *a: pytest.fail("product executed with unrequested Python"))
    assert mise_python.main([str(ROOT / "plugin/scripts/wiki_change_notice.py"), "claude"]) == 1
    assert calls[0][0][-2:] == ["where", "python@3.12"]
    assert "mise base Python" in capsys.readouterr().err


@pytest.mark.parametrize("store", ["installs", "i", "explicit-outside-store"])
def test_mise_inventory_selects_hashed_layout_and_preserves_layout_context(provider, monkeypatch, tmp_path, store):
    home, installation, executable, calls, probe, run = provider
    parent = tmp_path / "explicit-store" if store == "explicit-outside-store" else home / ".local/share/mise" / store
    parent.mkdir(parents=True, exist_ok=True)
    if store == "explicit-outside-store":
        monkeypatch.setenv("MISE_INSTALL_STORE_DIR", str(parent))
    monkeypatch.setenv("MISE_EXPERIMENTAL", "true")
    monkeypatch.setenv("MISE_INSTALL_LAYOUT", "identity")
    hashed = parent / "python-abcdefgh"
    installation.rename(hashed)
    selected = hashed / executable.relative_to(installation)
    probe.update(executable=str(selected), prefix=str(hashed), base_prefix=str(hashed))
    def identity(argv, **kwargs):
        result = run(argv, **kwargs)
        if "where" in argv:
            result.stdout = str(hashed).encode()
        elif "ls" in argv:
            result.stdout = json.dumps([dict(version="3.12.15", install_path=str(hashed), installed=True)]).encode()
        elif "which" in argv:
            result.stdout = str(selected).encode()
        return result
    monkeypatch.setattr(mise_python, "run_bounded", identity)
    assert mise_python.select_python(require_yaml=True) == str(selected)
    assert calls[2][0][-2:] == ["--tool", "python@3.12.15"]
    assert calls[0][1]["env"]["MISE_INSTALL_LAYOUT"] == "identity"
    assert calls[0][1]["env"]["MISE_EXPERIMENTAL"] == "true"


@pytest.mark.parametrize("contradiction", ["missing", "duplicate", "not-installed", "wrong-version"])
def test_contradictory_native_inventory_cannot_authorize_an_executable(provider, monkeypatch, contradiction):
    _, installation, _, calls, _, run = provider
    row = dict(version="3.12.15", install_path=str(installation), installed=True)
    rows = [] if contradiction == "missing" else [row, dict(row)] if contradiction == "duplicate" else [row]
    if contradiction == "not-installed":
        row["installed"] = False
    if contradiction == "wrong-version":
        row["version"] = "system"
    def inventory(argv, **kwargs):
        result = run(argv, **kwargs)
        if "ls" in argv:
            result.stdout = json.dumps(rows).encode()
        return result
    monkeypatch.setattr(mise_python, "run_bounded", inventory)
    with pytest.raises(mise_python.MisePythonError):
        mise_python.select_python()
    assert not any("which" in argv for argv, _ in calls)


def test_cached_dependency_upgrade_reuses_validated_installation(provider):
    _, installation, executable, calls, probe, _ = provider
    nested = installation / "nested/bin/python"
    nested.parent.mkdir(parents=True)
    executable.rename(nested)
    probe["executable"] = str(nested)
    original = mise_python.run_bounded
    def nested_binary(argv, **kwargs):
        result = original(argv, **kwargs)
        if "which" in argv:
            result.stdout = str(nested).encode()
        return result
    mise_python.run_bounded = nested_binary
    try:
        assert mise_python.select_python() == str(nested)
        assert mise_python.select_python(require_yaml=True) == str(nested)
        assert len(calls) == 5
    finally:
        mise_python.run_bounded = original


def _raw_plugin(tmp_path):
    plugin = tmp_path / "plugin with spaces"
    scripts = plugin / "scripts"
    scripts.mkdir(parents=True)
    for name in ("mise_python.py", "wiki_platform.py", "_wiki_windows.py"):
        shutil.copyfile(ROOT / "plugin/scripts" / name, scripts / name)
    product = scripts / "wiki_change_notice.py"
    product.write_text("import os,sys,json\nprint(json.dumps(dict(executable=sys.executable,args=sys.argv[1:],cwd=os.getcwd(),value=os.environ['FORWARDED'],stdin=sys.stdin.read())))\n")
    return scripts / "mise_python.py", product


@pytest.mark.skipif(os.name == "nt", reason="POSIX fake executable; native Windows acceptance is separate")
def test_raw_missing_managed_python_cannot_run_product_on_path_fallback(tmp_path):
    bootstrap, product = _raw_plugin(tmp_path)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    mise = bin_dir / "mise"
    # Model mise's documented exec PATH fallback when auto-install is off.
    mise.write_text('#!/bin/sh\nif [ "$1" = exec ]; then shift; shift; shift; exec "$@"; fi\necho secret-token >&2\nexit 1\n')
    mise.chmod(0o700)
    fallback = bin_dir / "python"
    fallback.write_text('#!/bin/sh\nexec ' + shlex.quote(sys.executable) + ' "$@"\n')
    fallback.chmod(0o700)
    home = tmp_path / "home"
    home.mkdir()
    (home / ".local/share/mise").mkdir(parents=True)
    env = dict(os.environ, HOME=str(home), PATH=str(bin_dir), VIRTUAL_ENV=str(bin_dir), FORWARDED="value", MISE_AUTO_INSTALL="0")
    result = subprocess.run([str(mise), "exec", "python@3.12", "--", "python", "-I", "-B", str(bootstrap), str(product)],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert not result.stdout
    assert "secret-token" not in result.stderr
    assert "mise base Python" in result.stderr


def test_actual_managed_base_is_independent_of_caller():
    # An optional local integration probe, independent of the modeled provider.
    mise_python._cache.clear()
    try:
        selected = mise_python.select_python()
    except mise_python.MisePythonError:
        pytest.skip("No valid installed home mise Python; modeled refusal coverage remains active")
    identity = json.loads(subprocess.check_output([selected, "-I", "-B", "-c",
        "import json,sys; print(json.dumps([sys.executable,sys.prefix,sys.base_prefix]))"]))
    assert Path(identity[0]).resolve() == Path(selected)
    assert identity[1] == identity[2]


def test_actual_raw_uses_requested_minor_and_forwards_io(tmp_path):
    try:
        selected = mise_python.select_python(_requested_version="3.12")
    except mise_python.MisePythonError:
        pytest.skip("No installed mise Python 3.12; canonical selection has independent coverage")
    bootstrap, product = _raw_plugin(tmp_path)
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    env = dict(os.environ, VIRTUAL_ENV="/hostile/venv", PYTHONPATH="/hostile/modules", FORWARDED="preserved")
    result = subprocess.run([sys.executable, "-I", "-B", str(bootstrap), str(product), "argument with spaces", "--flag"],
                            input="stdin preserved", cwd=consumer, env=env,
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report == dict(executable=selected, args=["argument with spaces", "--flag"],
                          cwd=str(consumer), value="preserved", stdin="stdin preserved")


def test_raw_rejects_outside_entrypoint_before_selection(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(mise_python, "select_python", lambda **k: pytest.fail("selected before owned entrypoint check"))
    assert mise_python.main([str(tmp_path / "outside.py")]) == 1
    assert "owned plugin entry point" in capsys.readouterr().err


def _wiki_raw_layout(tmp_path, layout):
    root = tmp_path / ("wiki-atlassian" if layout == "source" else "prepared-plugin")
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    shutil.copyfile(ROOT / "wiki-atlassian/scripts/launch.py", scripts / "launch.py")
    helpers = tmp_path / "plugin/scripts" if layout == "source" else scripts
    helpers.mkdir(parents=True, exist_ok=True)
    for name in ("mise_python.py", "wiki_platform.py", "_wiki_windows.py"):
        shutil.copyfile(ROOT / "plugin/scripts" / name, helpers / name)
    if layout == "prepared":
        (helpers / "personal_feedback.py").write_text("# prepared helper layout marker\n")
    product = root / "wiki_atlassian"
    product.mkdir()
    (product / "__init__.py").touch()
    (product / "main.py").write_text('import sys,json\ndef main():\n print(json.dumps(dict(executable=sys.executable,args=sys.argv[1:])))\n return 0\n')
    return scripts / "launch.py"


@pytest.mark.parametrize("layout", ["source", "prepared"])
def test_wiki_raw_layout_refuses_before_importing_product_without_mise(tmp_path, layout):
    launcher = _wiki_raw_layout(tmp_path, layout)
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), PATH="", VIRTUAL_ENV="/hostile/venv")
    result = subprocess.run([sys.executable, "-I", "-B", str(launcher), "--mise"],
                            env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 1
    assert not result.stdout and "Mise is required" in result.stderr


@pytest.mark.parametrize("layout", ["source", "prepared"])
def test_wiki_raw_layout_executes_only_selected_base_and_removes_bootstrap_flag(tmp_path, layout):
    try:
        selected = mise_python.select_python(require_yaml=True, _requested_version="3.12")
    except mise_python.MisePythonError:
        pytest.skip("Installed mise Python 3.12 with YAML is unavailable")
    launcher = _wiki_raw_layout(tmp_path, layout)
    result = subprocess.run([sys.executable, "-I", "-B", str(launcher), "--mise", "argument with spaces"],
                            env=dict(os.environ, VIRTUAL_ENV="/hostile/venv"),
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == dict(executable=selected, args=["argument with spaces"])
