import json
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
        f"      command: {sys.executable} {service} {ready}\n"
        f"      cwd: {tmp_path}\n"
        f"      healthcheck: test -f {ready}\n"
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
        f"      command: {sys.executable} {service} {ready}\n"
        f"      healthcheck: test -f {missing}\n"
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
        f"      command: {sys.executable} {service} {ready} needs-install {installed}\n"
        f"      healthcheck: test -f {ready}\n"
        "      ready_timeout_sec: 2\n"
        "      self_heal:\n"
        f"        - touch {installed}\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 0, result.stderr + result.stdout
    assert "api: started" in result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    attempts = state["services"]["api"]["self_heal_attempts"]
    assert attempts[0]["command"] == f"touch {installed}"
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
        f"      command: {sys.executable} {service} {ready} needs-env\n"
        f"      cwd: {tmp_path}\n"
        "      env_file: .env.test\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      healthcheck: test -f {ready}\n"
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
        f"      command: {sys.executable} {service} {ready} needs-env\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      healthcheck: test -f {ready}\n"
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
        f"      command: {sys.executable} {service} {ready} needs-env\n"
        f"      cwd: {tmp_path}\n"
        "      env_file: .env.generated\n"
        "      required_env: [SERVICE_TOKEN]\n"
        f"      env_setup_command: printf 'SERVICE_TOKEN=generated\\n' > {env_file}\n"
        f"      healthcheck: test -f {ready}\n"
        "      ready_timeout_sec: 2\n",
        encoding="utf-8",
    )

    result = _run(tmp_path, "start")
    assert result.returncode == 0, result.stderr + result.stdout
    state = json.loads((tmp_path / "runtime" / "services.json").read_text())
    assert state["services"]["api"]["self_heal_attempts"][0]["command"].startswith("printf")
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
            f"      command: {sys.executable} {service} {ready}\n"
            f"      port: {port}\n"
            f"      healthcheck: test -f {ready}\n"
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
        f"      command: {sys.executable} {service}\n"
        f"      healthcheck: test -f {ready}\n"
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
