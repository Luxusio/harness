"""Linux CLI contracts for shared preparation, reuse and cooperative ownership."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import socket
from pathlib import Path
import subprocess
import sys
import time
import threading

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "plugin/scripts"
sys.path.insert(0, str(SCRIPTS))
spec = importlib.util.spec_from_file_location("job_runner", SCRIPTS / "verify_runner.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def job(root, name, code, *, kind="consumer", artifacts=(), outputs=(), resources=()):
    script = root / (name + ".py")
    script.write_text(code)
    return dict(job_id=name, outcome_id=name, kind=kind, argv=[sys.executable, script.name],
                inputs=dict(source=[script.name], artifacts=list(artifacts)), outputs=list(outputs), resources=list(resources))


def execute(root, jobs, generation="one", **kwargs):
    return runner.run_jobs(jobs, root, task_id=kwargs.pop("task_id", "task"), run_id=kwargs.pop("run_id", "run"),
                           execution_generation=generation, results_path=root / "results.json",
                           max_workers=2, timeout=kwargs.pop("timeout", 5), **kwargs)


def shared(root):
    producer = job(root, "prepare", "from pathlib import Path\nPath('built').write_text('shared')\n", kind="producer", outputs=["built"])
    consumers = [job(root, name, "from pathlib import Path\nassert Path('built').read_text() == 'shared'\nprint('consumed')\n", artifacts=["built"]) for name in ("alpha", "beta")]
    return [producer, *consumers]


def test_shared_producer_reuse_selective_changes_and_lost_artifact(tmp_path):
    jobs = shared(tmp_path)
    first = execute(tmp_path, jobs)
    assert first["status"] == "PASS"
    assert [r["job_id"] for r in first["commands"]] == ["prepare", "alpha", "beta"]
    assert all(not r["reused"] for r in first["commands"])
    assert all(r["reused"] for r in execute(tmp_path, jobs, "two")["commands"])
    (tmp_path / "built").unlink()
    regenerated = execute(tmp_path, jobs, "three")["commands"]
    assert [r["reused"] for r in regenerated] == [False, True, True]
    (tmp_path / "alpha.py").write_text((tmp_path / "alpha.py").read_text() + "print('changed')\n")
    assert [r["reused"] for r in execute(tmp_path, jobs, "four")["commands"]] == [True, False, True]
    assert all(not r["reused"] for r in execute(tmp_path, jobs, "five", run_id="new-run")["commands"])


def test_real_cli_explicit_frozen_root_help_and_invalid(tmp_path):
    # A .git-free view nested inside a checkout must never walk to the main root.
    view = tmp_path / "view"
    view.mkdir()
    jobs = shared(view)
    declaration = tmp_path / "jobs.json"
    declaration.write_text(json.dumps(jobs))
    argv = [sys.executable, str(SCRIPTS / "verify_runner.py")]
    assert subprocess.run(argv + ["--help"], capture_output=True).returncode == 0
    args = ["--jobs", str(declaration), "--root", str(view), "--results", str(view / "result.json"), "--task-id", "task", "--run-id", "run", "--generation", "one"]
    proc = subprocess.run(argv + args, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["schema"] == runner.SCHEMA
    restored = tmp_path / "restored"
    restored.mkdir()
    for script in view.glob("*.py"):
        shutil.copyfile(script, restored / script.name)
    # External per-group results stay fixed while source/artifact roots relocate.
    args[args.index("--root") + 1] = str(restored)
    args[args.index("--generation") + 1] = "restored"
    again = subprocess.run(argv + args, capture_output=True, text=True, timeout=20)
    assert again.returncode == 0, again.stderr
    assert [r["reused"] for r in json.loads(again.stdout)["commands"]] == [False, True, True]
    declaration.write_text('{"invalid":true}')
    invalid = subprocess.run(argv + args, capture_output=True, text=True, timeout=10)
    assert invalid.returncode == 2 and "bounded jobs" in invalid.stderr


@pytest.mark.parametrize("mutation", ["duplicate", "two-producers", "traversal", "absolute", "source-overlap", "consumer-output", "unknown", "symlink", "source-symlink", "result-overlap"])
def test_declarations_reject_before_execution(tmp_path, mutation):
    jobs = shared(tmp_path)
    if mutation == "duplicate":
        jobs[1]["job_id"] = jobs[0]["job_id"]
    elif mutation == "two-producers":
        jobs[1]["kind"] = "producer"
    elif mutation == "traversal":
        jobs[0]["outputs"] = ["../escape"]
    elif mutation == "absolute":
        jobs[0]["outputs"] = [str(tmp_path / "escape")]
    elif mutation == "source-overlap":
        jobs[0]["outputs"] = ["prepare.py"]
    elif mutation == "consumer-output":
        jobs[1]["outputs"] = ["wrong"]
    elif mutation == "unknown":
        jobs[0]["depends_on"] = ["alpha"]
    elif mutation == "symlink":
        (tmp_path / "built").symlink_to(tmp_path / "prepare.py")
    elif mutation == "source-symlink":
        (tmp_path / "alpha.py").unlink()
        (tmp_path / "alpha.py").symlink_to(tmp_path / "beta.py")
    elif mutation == "result-overlap":
        jobs[0]["outputs"] = ["results.json"]
    with pytest.raises(ValueError):
        execute(tmp_path, jobs)
    assert not (tmp_path / "built").is_file() or mutation == "symlink"


def test_capability_launch_command_and_executor_states(tmp_path):
    item = job(tmp_path, "check", "raise SystemExit(7)\n")
    assert execute(tmp_path, [item])["commands"][0]["state"] == "command_failure"
    item["requires"] = ["agent:unavailable"]
    assert execute(tmp_path, [item])["commands"][0]["state"] == "capability_unavailable"
    item["requires"] = []
    item["argv"][0] = "definitely-no-harness-executable"
    assert execute(tmp_path, [item])["commands"][0]["state"] == "start_failed"
    item["argv"][0] = sys.executable
    (tmp_path / "check.py").write_text("from pathlib import Path\nPath('check.py').write_text('changed')\n")
    assert execute(tmp_path, [item])["commands"][0]["state"] == "executor_error"


def test_failed_partial_producer_blocks_consumers_and_new_bytes_invalidate(tmp_path):
    jobs = shared(tmp_path)
    assert execute(tmp_path, jobs)["status"] == "PASS"
    (tmp_path / "prepare.py").write_text("from pathlib import Path\nPath('built').write_text('partial')\nraise SystemExit(4)\n")
    failed = execute(tmp_path, jobs)["commands"]
    assert [r["state"] for r in failed] == ["command_failure", "blocked_dependency", "blocked_dependency"]
    (tmp_path / "prepare.py").write_text("from pathlib import Path\nPath('built').write_text('new')\n")
    changed = execute(tmp_path, jobs)["commands"]
    assert all(not r["reused"] for r in changed)
    assert [r["state"] for r in changed[1:]] == ["command_failure", "command_failure"]


def test_environment_argv_outcome_and_corrupt_records(tmp_path, monkeypatch):
    item = job(tmp_path, "check", "import os\nassert 'UNDECLARED' not in os.environ\nprint(os.environ.get('DECLARED'))\n")
    item["environment"] = ["DECLARED"]
    monkeypatch.setenv("UNDECLARED", "hidden")
    monkeypatch.setenv("DECLARED", "first")
    assert execute(tmp_path, [item])["status"] == "PASS"
    monkeypatch.setenv("UNDECLARED", "other")
    assert execute(tmp_path, [item])["commands"][0]["reused"]
    monkeypatch.setenv("DECLARED", "second")
    assert not execute(tmp_path, [item])["commands"][0]["reused"]
    item["argv"].append("extra")
    assert not execute(tmp_path, [item])["commands"][0]["reused"]
    item["outcome_id"] = "new-outcome"
    assert not execute(tmp_path, [item])["commands"][0]["reused"]
    for corrupt in ("{", '{"schema":"other"}', '{"commands":null}'):
        (tmp_path / "results.json").write_text(corrupt)
        assert execute(tmp_path, [item])["status"] == "PASS"


@pytest.mark.parametrize("timeout", [False, True])
def test_descendants_die_before_lease_release(tmp_path, timeout):
    code = "import subprocess,sys,time\nsubprocess.Popen([sys.executable,'-c',\"import time;from pathlib import Path;time.sleep(0.8);Path('escaped').write_text('bad')\"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
    if timeout:
        code += "time.sleep(10)\n"
    item = job(tmp_path, "check", code)
    record = execute(tmp_path, [item], timeout=0.3 if timeout else 5)["commands"][0]
    assert record["state"] == ("timeout" if timeout else "completed")
    time.sleep(1)
    assert not (tmp_path / "escaped").exists()


@pytest.mark.parametrize("ownership", ["resource", "output"])
def test_cross_process_named_leases_skip_waiters_and_release(tmp_path, ownership):
    # The holder is a separate process, with a different task/results file.
    holder = job(tmp_path, "holder", "from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists(): time.sleep(.01)\n", resources=["test-display:" + str(tmp_path)])
    if ownership == "output":
        holder.update(kind="producer", outputs=["built"], resources=[])
        (tmp_path / "holder.py").write_text("from pathlib import Path\nimport time\nPath('built').write_text('partial')\nPath('ready').touch()\nwhile not Path('release').exists(): time.sleep(.01)\nPath('built').write_text('final')\n")
    declaration = tmp_path / "holder.json"
    declaration.write_text(json.dumps([holder]))
    args = [sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(tmp_path / "holder-result.json"), "--task-id", "other-task", "--run-id", "other-run", "--generation", "one", "--timeout", "10"]
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        deadline = time.monotonic() + 8
        while not (tmp_path / "ready").exists():
            assert proc.poll() is None
            assert time.monotonic() < deadline
            time.sleep(.01)
        waiters = [job(tmp_path, name, "from pathlib import Path\nassert Path('release').exists()\n", resources=holder["resources"]) for name in ("wait_a", "wait_b")]
        if ownership == "output":
            for waiter in waiters:
                waiter["inputs"]["artifacts"] = ["built"]
                (tmp_path / (waiter["job_id"] + ".py")).write_text("from pathlib import Path\nassert Path('built').read_text() == 'final'\n")
        free = job(tmp_path, "free", "from pathlib import Path\nPath('release').touch()\n")
        # Free's marker is test synchronization, not an artifact consumer.
        result = execute(tmp_path, [*waiters, free])
        assert result["status"] == "PASS"
        out, err = proc.communicate(timeout=10)
        assert proc.returncode == 0, err
        assert json.loads(out)["status"] == "PASS"
    finally:
        if proc.poll() is None:
            proc.kill()
            proc.communicate()


def test_output_leases_conflict_only_at_ancestor_or_same_leaf(tmp_path):
    first, second, shared = {}, {}, {}
    runner._path_leases(first, tmp_path / "build" / "a", True)
    runner._path_leases(second, tmp_path / "build" / "b", True)
    runner._path_leases(shared, tmp_path / "build", True)
    held = runner._lease(first)
    try:
        independent = runner._lease(second)
        assert independent is not None
        for fd in independent:
            os.close(fd)
        assert runner._lease(shared) is None
        reader = {}
        runner._path_leases(reader, tmp_path / "build" / "a", False)
        assert runner._lease(reader) is None
    finally:
        for fd in held:
            os.close(fd)


def test_scoped_toolchain_probe_and_malformed_success_are_not_false_reuse(tmp_path, monkeypatch):
    tool = tmp_path / "proxy"
    tool.write_text("#!" + sys.executable + "\nimport os\nprint(os.environ.get('UNDECLARED', 'scoped'))\n")
    tool.chmod(0o755)
    item = dict(job_id="tool", outcome_id="tool", kind="consumer", argv=["proxy"], inputs=dict(source=["proxy"]), toolchains=["proxy"])
    monkeypatch.setenv("PATH", ".")
    monkeypatch.setenv("UNDECLARED", "ambient-one")
    assert execute(tmp_path, [item])["status"] == "PASS"
    monkeypatch.setenv("UNDECLARED", "ambient-two")
    assert execute(tmp_path, [item])["commands"][0]["reused"]
    for target in ("envelope", "record"):
        payload = json.loads((tmp_path / "results.json").read_text())
        if target == "envelope":
            payload.pop("execution_generation")
        else:
            payload["commands"][0]["execution_generation"] = None
        (tmp_path / "results.json").write_text(json.dumps(payload))
        assert not execute(tmp_path, [item])["commands"][0]["reused"]


def test_producer_replacement_before_consumer_admission_is_not_accepted(tmp_path):
    jobs = shared(tmp_path)
    jobs = jobs[:2]
    jobs[1]["resources"] = ["consumer-gate:" + str(tmp_path)]
    held = runner._lease({"resource:" + jobs[1]["resources"][0]: True})
    result = []
    thread = threading.Thread(target=lambda: result.append(execute(tmp_path, jobs)))
    thread.start()
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                records = json.loads((tmp_path / "results.json").read_text())["commands"]
                if records and records[0]["status"] == "PASS":
                    break
            except (OSError, ValueError):
                pass
            assert time.monotonic() < deadline
            time.sleep(.01)
        other = job(tmp_path, "replace", "from pathlib import Path\nPath('built').write_text('replacement')\n", kind="producer", outputs=["built"])
        assert runner.run_jobs([other], tmp_path, task_id="other", run_id="run", execution_generation="one", results_path=tmp_path / "other-results.json", timeout=5)["status"] == "PASS"
    finally:
        for fd in held:
            os.close(fd)
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result[0]["commands"][1]["state"] == "executor_error"
    assert "prepared artifact changed" in result[0]["commands"][1]["error"]


def test_killed_runner_keeps_leases_until_supervisor_cleans_command(tmp_path):
    name = "death-test:" + str(tmp_path)
    item = job(tmp_path, "owner", "from pathlib import Path\nimport time\nPath('ready').touch()\nwhile not Path('release').exists(): time.sleep(.01)\nPath('finished').touch()\n", kind="producer", outputs=["finished"], resources=[name])
    declaration = tmp_path / "jobs.json"
    declaration.write_text(json.dumps([item]))
    proc = subprocess.Popen([sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(tmp_path / "result.json"), "--task-id", "t", "--run-id", "r", "--generation", "g", "--timeout", "8"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / "ready").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        proc.kill()
        proc.wait(timeout=5)
        assert runner._lease({"resource:" + name: True}) is None
        (tmp_path / "release").touch()
        while (held := runner._lease({"resource:" + name: True})) is None:
            assert time.monotonic() < deadline
            time.sleep(.01)
        for fd in held:
            os.close(fd)
        assert (tmp_path / "finished").exists()
        assert not (tmp_path / "result.json").exists()
    finally:
        (tmp_path / "release").touch()
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)


def test_real_cli_consumers_enter_in_parallel_after_shared_preparation(tmp_path):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(2)
    server.settimeout(8)
    errors = []
    def release_both():
        connections = []
        try:
            connections = [server.accept()[0], server.accept()[0]]
            for connection in connections:
                connection.sendall(b"ok")
        except Exception as exc:
            errors.append(exc)
        finally:
            for connection in connections:
                connection.close()
            server.close()
    worker = threading.Thread(target=release_both)
    worker.start()
    jobs = shared(tmp_path)
    for item in jobs[1:]:
        script = tmp_path / (item["job_id"] + ".py")
        script.write_text(script.read_text() + f"import socket\ns = socket.create_connection(('127.0.0.1', {server.getsockname()[1]}), timeout=6)\nassert s.recv(2) == b'ok'\n")
    declaration = tmp_path / "jobs.json"
    declaration.write_text(json.dumps(jobs))
    proc = subprocess.run([sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(tmp_path / "result.json"), "--task-id", "t", "--run-id", "r", "--generation", "g", "--max-workers", "2", "--timeout", "8"], capture_output=True, text=True, timeout=25)
    worker.join(timeout=10)
    assert not worker.is_alive() and not errors
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert len(json.loads(proc.stdout)["commands"]) == 3
