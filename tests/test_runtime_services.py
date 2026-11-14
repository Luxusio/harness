import json
import os
import shlex
import importlib.util

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error
import socket
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin" / "scripts" / "runtime_services.py"


def _run(tmp_path, *args):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--manifest", str(tmp_path / "manifest.yaml"),
         "--runtime-dir", str(tmp_path / "runtime"), *args],
        text=True,
        capture_output=True,
        timeout=20,
    )


def _command(*argv):
    values = [str(arg) for arg in argv]
    return subprocess.list2cmdline(values) if os.name == "nt" else shlex.join(values)


def _exists(path):
    return _command(sys.executable, "-c", "import pathlib,sys;sys.exit(not pathlib.Path(sys.argv[1]).is_file())", path)


def _write_command(path, content=""):
    return _command(sys.executable, "-c", f"import pathlib,sys;pathlib.Path(sys.argv[1]).write_text({content!r})", path)


def _write_service_script(tmp_path: Path) -> Path:
    script = tmp_path / "service.py"
    script.write_text(
        "import pathlib, signal, sys, time\n"
        "ready = pathlib.Path(sys.argv[1])\n"
        "mode = sys.argv[2] if len(sys.argv) > 2 else 'normal'\n"
        "installed = pathlib.Path(sys.argv[3]) if len(sys.argv) > 3 else None\n"
        "if mode == 'needs-install' and (not installed or not installed.exists()):\n"
        "    sys.exit(7)\n"
        "if mode == 'needs-env' and not __import__('os').environ.get('SERVICE_TOKEN'):\n"
        "    print('missing env SERVICE_TOKEN', flush=True)\n"
        "    sys.exit(8)\n"
        "ready.write_text('ready')\n"
        "running = True\n"
        "def stop(*_):\n"
        "    global running\n"
        "    running = False\n"
        "signal.signal(signal.SIGTERM, stop)\n"
        "while running:\n"
        "    time.sleep(0.1)\n"
        "ready.unlink(missing_ok=True)\n",
        encoding="utf-8",
    )
    return script


def _write_failing_script(tmp_path: Path, message: str) -> Path:
    script = tmp_path / "fail.py"
    script.write_text(
        "import sys\n"
        f"print({message!r}, flush=True)\n"
        "sys.exit(9)\n",
        encoding="utf-8",
    )
    return script


def test_start_status_logs_stop_reuses_healthy_service(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: web\n"
        f"      command: {_command(sys.executable, service, ready)}\n"
        f"      cwd: {tmp_path}\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 5\n",
        encoding="utf-8",
    )

    first = _run(tmp_path, "start")
    assert first.returncode == 0, first.stderr + first.stdout
    assert "web: started" in first.stdout

    second = _run(tmp_path, "start")
    assert second.returncode == 0
    assert "web: running" in second.stdout

    status = _run(tmp_path, "status")
    assert status.returncode == 0
    assert "web: running" in status.stdout

    logs = _run(tmp_path, "logs", "web")
    assert logs.returncode == 0
    assert "starting:" in logs.stdout

    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["web"]["status"] == "running"
    assert Path(state["services"]["web"]["log"]).exists()

    stop = _run(tmp_path, "stop")
    assert stop.returncode == 0
    assert "web: stopped" in stop.stdout


def test_start_blocks_when_healthcheck_never_passes(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    missing = tmp_path / "missing.txt"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: api\n"
        f"      command: {_command(sys.executable, service, ready)}\n"
        f"      healthcheck: {_exists(missing)}\n"
        "      ready_timeout_sec: 1\n"
        "      restart_on_fail: false\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 1
    assert "api: BLOCKED" in result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["api"]["status"] == "blocked"
    assert state["services"]["api"]["health"] == "fail"


def test_self_heal_command_runs_then_service_becomes_ready(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    installed = tmp_path / "installed.txt"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: api\n"
        f"      command: {_command(sys.executable, service, ready, "needs-install", installed)}\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 2\n"
        "      self_heal:\n"
        f"        - {_write_command(installed)}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "api: started" in result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    attempts = state["services"]["api"]["self_heal_attempts"]
    assert attempts[0]["command"] == _write_command(installed)
    assert state["services"]["api"]["health"] == "pass"

    _run(tmp_path, "stop")


def test_required_env_can_be_loaded_from_env_file(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    env_file = tmp_path / ".env.test"
    env_file.write_text("SERVICE_TOKEN=dev-token\n", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: api\n"
        f"      command: {_command(sys.executable, service, ready, "needs-env")}\n"
        f"      cwd: {tmp_path}\n"
        "      env_file: .env.test\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 2\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 0, result.stderr + result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["api"]["health"] == "pass"
    _run(tmp_path, "stop")


def test_missing_required_env_blocks_before_start(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: api\n"
        f"      command: {_command(sys.executable, service, ready, "needs-env")}\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 1\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 1
    assert "missing required env: SERVICE_TOKEN" in result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["api"]["failure_class"] == "missing_env"
    assert "env_file" in state["services"]["api"]["recommended_action"]


def test_env_setup_command_can_create_env_file_before_start(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    env_file = tmp_path / ".env.generated"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: api\n"
        f"      command: {_command(sys.executable, service, ready, "needs-env")}\n"
        f"      cwd: {tmp_path}\n"
        "      env_file: .env.generated\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      env_setup_command: {_write_command(env_file, chr(83) + 'ERVICE_TOKEN=generated' + chr(10))}\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 2\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 0, result.stderr + result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["api"]["self_heal_attempts"][0]["command"] == _write_command(env_file, "SERVICE_TOKEN=generated\n")
    assert state["services"]["api"]["health"] == "pass"
    _run(tmp_path, "stop")


def test_declared_port_conflict_blocks_before_start(tmp_path):
    service = _write_service_script(tmp_path)
    ready = tmp_path / "ready.txt"
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    port = sock.getsockname()[1]
    try:
        manifest = tmp_path / "manifest.yaml"
        manifest.write_text(
            "runtime:\n"
            "  services:\n"
            "    - name: web\n"
            f"      command: {_command(sys.executable, service, ready)}\n"
            f"      port: {port}\n"
            f"      healthcheck: {_exists(ready)}\n"
            "      ready_timeout_sec: 1\n",
            encoding="utf-8",
        )

        result = _run(tmp_path, "start")
        assert result.returncode == 1
        assert "already accepts connections" in result.stdout
        state = json.loads((tmp_path / "runtime" / "services.json").read_text())
        assert state["services"]["web"]["failure_class"] == "port_conflict"
        assert "127.0.0.1" in state["services"]["web"]["health_detail"]
    finally:
        sock.close()


def test_log_classifier_records_missing_dependency_failure(tmp_path):
    service = _write_failing_script(tmp_path, "Error: Cannot find module express")
    ready = tmp_path / "ready.txt"
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        "runtime:\n"
        "  services:\n"
        "    - name: web\n"
        f"      command: {_command(sys.executable, service)}\n"
        f"      healthcheck: {_exists(ready)}\n"
        "      ready_timeout_sec: 1\n"
        "      restart_on_fail: false\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 1
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    web = state["services"]["web"]
    assert web["failure_class"] == "missing_dependency"
    assert "install dependencies" in web["recommended_action"]
    assert "Cannot find module" in web["last_log_excerpt"]


def test_runtime_services_docs_and_qa_prompts_reference_live_startup():
    docs = (REPO / "doc" / "harness" / "runtime-services.md").read_text()
    manifest = (REPO / "doc" / "harness" / "manifest.yaml").read_text()
    runtime_smoke = (REPO / "plugin" / "skills" / "develop" / "runtime-smoke.md").read_text()

    assert "runtime.services" in docs
    assert "self_heal" in docs
    assert "required_env" in docs
    assert "failure_class" in docs
    assert "BLOCKED_ENV" in docs
    assert "runtime_services.py start" in manifest
    assert "Runtime Services Prelude" in runtime_smoke
    assert "runtime_services.py start" in runtime_smoke

    for rel in [
        "plugin/agents/qa-api.md",
        "plugin-codex/agents/qa-api.md",
        "plugin/agents/qa-browser.md",
        "plugin-codex/agents/qa-browser.md",
    ]:
        body = (REPO / rel).read_text()
        assert "runtime.services[]" in body
        assert "runtime_services.py start" in body
        assert "runtime_services.py logs <service>" in body
        assert "failure_class" in body
        assert "recommended_action" in body
        assert "last_log_excerpt" in body


def _runtime_module(monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("runtime_services_fixture", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_preserves_individually_quoted_command_paths(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    command = '"C:\\Program Files\\Python\\python.exe" "C:\\work tree\\service.py"'
    path = tmp_path / "manifest.yaml"
    path.write_text("runtime:\n  services:\n    - name: service\n      command: " + command + "\n")
    assert module._parse_manifest_services(path)[0]["command"] == command


def _native_process_alive(module, pid, created=None):
    win = module._win
    open_process = win._bind(win.K, "OpenProcess", [win.DWORD, win.W.BOOL, win.DWORD], win.HANDLE)
    handle = open_process(0x100000 | 0x1000, False, pid)
    if not handle:
        assert win.C.get_last_error() == 87
        return False
    try:
        if created is not None and module._NativeService._created(handle) != created:
            return False
        result = win._WaitForSingleObject(handle, 0)
        assert result in (0, 258)
        return result == 258
    finally:
        win._close(handle)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job/process ownership")
def test_native_persistent_job_reuses_descendants_after_leader_exit_and_preserves_foreign_pid(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    pid_path = tmp_path / "child.pid"
    launcher_pid_path = tmp_path / "launcher.pid"
    child = tmp_path / "child service.py"
    child.write_text("import os,pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(300)\n")
    launcher = tmp_path / "launcher.py"
    launcher.write_text("import os,pathlib,subprocess,sys\npathlib.Path(sys.argv[3]).write_text(str(os.getpid()))\n"
                        "subprocess.Popen([sys.executable,sys.argv[1],sys.argv[2]])\n")
    (tmp_path / "manifest.yaml").write_text(
        "runtime:\n  services:\n    - name: tree\n"
        f"      command: {_command(sys.executable, launcher, child, pid_path, launcher_pid_path)}\n"
        f"      healthcheck: {_exists(pid_path)}\n      ready_timeout_sec: 8\n")
    foreign = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"])
    original = None
    try:
        first = _run(tmp_path, "start")
        assert first.returncode == 0, first.stdout + first.stderr
        state = module._load_state(tmp_path / "runtime")
        original = dict(state["services"]["tree"])
        child_pid = int(pid_path.read_text())
        launcher_pid = int(launcher_pid_path.read_text())
        deadline = time.monotonic() + 5
        while _native_process_alive(module, launcher_pid):
            assert time.monotonic() < deadline, "fixture leader did not exit"
            time.sleep(0.05)
        assert _native_process_alive(module, original["pid"], original["windows_created"]), "execution owner must retain the Job after command-leader exit"
        assert module._entry_alive(original), "live descendant must retain the named Job"
        second = _run(tmp_path, "start")
        assert second.returncode == 0 and "running pid=" in second.stdout, second.stdout + second.stderr
        assert int(pid_path.read_text()) == child_pid
        # Simulate PID reuse by pointing the recorded leader at an unrelated
        # actual process. Job ownership, not that PID, defines termination.
        state["services"]["tree"]["pid"] = foreign.pid
        module._save_state(tmp_path / "runtime", state)
        stopped = _run(tmp_path, "stop")
        assert stopped.returncode == 0, stopped.stdout + stopped.stderr
        assert not _native_process_alive(module, child_pid)
        assert foreign.poll() is None, "unrelated/reused PID was terminated"
    finally:
        if original is not None:
            module._stop_entry(original)
        foreign.terminate()
        foreign.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="requires real suspended Windows creation and Job cleanup")
def test_native_failed_ownership_publication_never_executes_and_reaps_suspended_tree(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    marker = tmp_path / "must not execute"
    entry = {}
    def fail_publish():
        assert entry.get("windows_created") and entry.get("windows_job")
        raise OSError("publication fixture failed")
    with pytest.raises(OSError, match="publication fixture failed"):
        module._NativeService.start({"name": "fail", "command": _write_command(marker, "ran")},
                                    tmp_path / "runtime", entry, fail_publish)
    assert not marker.exists()
    assert not module._entry_alive(entry)
    assert not _native_process_alive(module, entry["pid"], entry["windows_created"])


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows process identity and private state")
def test_native_legacy_pid_cannot_be_adopted_or_terminated(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    foreign = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(300)"])
    try:
        entry = {"pid": foreign.pid}
        with pytest.raises(ValueError, match="Unowned legacy Windows service PID"):
            module._entry_alive(entry)
        with pytest.raises(ValueError, match="Unowned legacy Windows service PID"):
            module._stop_entry(entry)
        assert foreign.poll() is None
    finally:
        foreign.terminate()
        foreign.wait(timeout=10)


@pytest.mark.skipif(os.name != "nt", reason="requires actual Windows service-host death and Job kill-on-close")
def test_native_service_host_death_reaps_owned_descendants(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    pid_path = tmp_path / "service.pid"
    service = tmp_path / "service.py"
    service.write_text("import os,pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(300)\n")
    (tmp_path / "manifest.yaml").write_text(
        "runtime:\n  services:\n    - name: host\n"
        f"      command: {_command(sys.executable, service, pid_path)}\n"
        f"      healthcheck: {_exists(pid_path)}\n      ready_timeout_sec: 8\n")
    entry = None
    process = child = None
    win = module._win
    try:
        started = _run(tmp_path, "start")
        assert started.returncode == 0, started.stdout + started.stderr
        entry = module._load_state(tmp_path / "runtime")["services"]["host"]
        open_process = win._bind(win.K, "OpenProcess", [win.DWORD, win.W.BOOL, win.DWORD], win.HANDLE)
        process = open_process(1 | 0x1000 | 0x100000, False, entry["pid"])
        child = open_process(0x1000 | 0x100000, False, int(pid_path.read_text()))
        assert process and child
        assert module._NativeService._created(process) == entry["windows_created"]
        assert win._WaitForSingleObject(child, 0) == 258
        # No outside Job handle is retained. Losing this actual execution
        # owner's handle must terminate its child, rather than merely remove
        # the name and strand the still-running product process.
        win._check(win._TerminateProcess(process, 79))
        assert win._WaitForSingleObject(process, 5000) == 0
        assert win._WaitForSingleObject(child, 5000) == 0
        assert not module._entry_alive(entry)
    finally:
        win._close(child)
        win._close(process)
        if entry is not None:
            module._stop_entry(entry)


@pytest.mark.skipif(os.name != "nt", reason="requires real Windows Job creation-time validation")
def test_native_changed_creation_time_refuses_without_killing_original_tree(tmp_path, monkeypatch):
    module = _runtime_module(monkeypatch)
    marker = tmp_path / "ready"
    script = _write_service_script(tmp_path)
    entry = {}
    try:
        module._NativeService.start({"name": "identity", "command": _command(sys.executable, script, marker)},
                                    tmp_path / "runtime", entry, lambda: None)
        original = dict(entry)
        entry["windows_created"] += 1
        with pytest.raises(ValueError, match="leader identity changed"):
            module._stop_entry(entry)
        assert module._entry_alive(original)
    finally:
        if entry:
            entry["windows_created"] -= 1
            module._stop_entry(entry)


@pytest.mark.skipif(os.name != "nt", reason="requires actual controller death around Windows ResumeThread")
@pytest.mark.parametrize("crash_stage", ["before-resume", "after-resume", "after-ack"])
def test_native_unfinished_startup_is_not_running_and_recovers_owned_job(tmp_path, monkeypatch, crash_stage):
    module = _runtime_module(monkeypatch)
    marker = tmp_path / "executed.txt"
    service = tmp_path / "service.py"
    service.write_text(
        "import os,pathlib,sys,time\npathlib.Path(sys.argv[1]).write_text(str(os.getpid()))\ntime.sleep(300)\n")
    manifest = tmp_path / "manifest.yaml"
    # No healthcheck is intentional: default readiness must not turn a
    # controller's abandoned, possibly suspended Job into a running service.
    manifest.write_text(
        "runtime:\n  services:\n    - name: startup\n"
        f"      command: {_command(sys.executable, service, marker)}\n")
    controller = tmp_path / "crashing controller.py"
    controller.write_text(
        "import os,sys\n"
        "sys.path.insert(0,sys.argv[1])\n"
        "import runtime_services as runtime\n"
        "resume=runtime._win._ResumeThread\n"
        "def crash(thread):\n"
        "    if sys.argv[2]=='after-ack':\n"
        "        return resume(thread)\n"
        "    if sys.argv[2]=='after-resume':\n"
        "        assert resume(thread)!=0xFFFFFFFF\n"
        "    os._exit(91)\n"
        "runtime._win._ResumeThread=crash\n"
        "bind=runtime._win._bind\n"
        "def binding(dll,name,args,result=runtime._win.W.BOOL):\n"
        "    fn=bind(dll,name,args,result)\n"
        "    if name=='WaitForMultipleObjects' and sys.argv[2]=='after-ack':\n"
        "        def acknowledged(*values):\n"
        "            assert fn(*values)==0\n"
        "            os._exit(91)\n"
        "        return acknowledged\n"
        "    return fn\n"
        "runtime._win._bind=binding\n"
        "runtime.main(sys.argv[3:])\n")
    runtime_dir = tmp_path / "runtime"
    abandoned = recovered = None
    try:
        crashed = subprocess.run(
            [sys.executable, "-I", "-B", str(controller), str(SCRIPT.parent), crash_stage,
             "--manifest", str(manifest), "--runtime-dir", str(runtime_dir), "start"],
            text=True, capture_output=True, timeout=20)
        assert crashed.returncode == 91, crashed.stdout + crashed.stderr
        saved = module._load_state(runtime_dir)
        abandoned = dict(saved["services"]["startup"])
        assert abandoned["status"] == "starting" and abandoned["health"] == "pending"
        if crash_stage == "after-ack":
            assert module._entry_alive(abandoned), "acknowledged host must retain its actual Job handle"
        if crash_stage == "before-resume":
            deadline = time.monotonic() + 5
            while _native_process_alive(module, abandoned["pid"], abandoned["windows_created"]):
                assert time.monotonic() < deadline, "controller death did not reap the suspended owned host"
                time.sleep(0.01)
            assert not module._entry_alive(abandoned)
            assert not marker.exists(), "suspended command unexpectedly executed"
        before_status = (runtime_dir / "services.json").read_bytes()
        status = _run(tmp_path, "status")
        assert status.returncode == 1, status.stdout + status.stderr
        assert ("startup: starting" in status.stdout or "startup: unfinished" in status.stdout) and "health=fail" in status.stdout
        assert "startup not confirmed" in status.stdout
        assert (runtime_dir / "services.json").read_bytes() == before_status
        if crash_stage == "after-ack":
            assert module._entry_alive(abandoned), "status must preserve the unfinished owned Job"
        if crash_stage == "before-resume":
            assert not marker.exists()

        restarted = _run(tmp_path, "start")
        assert restarted.returncode == 0, restarted.stdout + restarted.stderr
        assert "startup: started" in restarted.stdout and "startup: running" not in restarted.stdout
        recovered = module._load_state(runtime_dir)["services"]["startup"]
        assert recovered["windows_job"] != abandoned["windows_job"]
        assert recovered["status"] == "running" and recovered["health"] == "pass"
        assert not module._entry_alive(abandoned), "recovery must retire the entire abandoned owned Job"
        assert module._entry_alive(recovered)
        deadline = time.monotonic() + 8
        job = module._NativeService._open(recovered)
        try:
            win = module._win
            open_process = win._bind(win.K, "OpenProcess", [win.DWORD, win.W.BOOL, win.DWORD], win.HANDLE)
            is_member = win._bind(win.K, "IsProcessInJob", [win.HANDLE, win.HANDLE, win.C.POINTER(win.W.BOOL)])
            while True:
                try:
                    child_pid = int(marker.read_text())
                except (FileNotFoundError, ValueError):
                    child_pid = 0
                child = open_process(0x1000, False, child_pid) if child_pid else None
                member = win.W.BOOL()
                try:
                    if child:
                        win._check(is_member(child, job, win.C.byref(member)))
                    if member.value:
                        break
                finally:
                    win._close(child)
                assert time.monotonic() < deadline, "recovered service command never executed in its new owned Job"
                time.sleep(0.05)
        finally:
            module._win._close(job)
    finally:
        if recovered is not None:
            module._stop_entry(recovered)
        if abandoned is not None:
            module._stop_entry(abandoned)
