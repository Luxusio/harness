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

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

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


def shared(root, artifact="built"):
    producer = job(root, "prepare", f"from pathlib import Path\nPath({artifact!r}).write_text('shared')\n", kind="producer", outputs=[artifact])
    consumers = [job(root, name, f"from pathlib import Path\nassert Path({artifact!r}).read_text() == 'shared'\nprint('consumed')\n", artifacts=[artifact]) for name in ("alpha", "beta")]
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


def test_reuse_only_never_runs_a_missing_or_stale_command(tmp_path):
    item = job(tmp_path, "check", "print('executed')\n")
    first = execute(tmp_path, [item], reuse_only=True)["commands"][0]
    assert first["state"] == "executor_error" and first["returncode"] is None
    assert "reuse-only" in first["error"] and "output" not in first
    assert execute(tmp_path, [item])["status"] == "PASS"
    assert execute(tmp_path, [item], "two", reuse_only=True)["commands"][0]["reused"]
    (tmp_path / "check.py").write_text("raise RuntimeError('must not execute')\n")
    stale = execute(tmp_path, [item], "three", reuse_only=True)["commands"][0]
    assert stale["returncode"] is None and "output" not in stale and "reuse-only" in stale["error"]


@pytest.mark.parametrize('boundary', ['deny', 'exclude'])
def test_required_input_cannot_be_omitted_before_execution_or_reuse(tmp_path, monkeypatch, boundary):
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('ran').touch()\n")
    required = tmp_path / 'required.py'
    required.write_text('required input')
    item['inputs']['source'].append(required.name)
    assert execute(tmp_path, [item])['status'] == 'PASS'
    (tmp_path / 'ran').unlink()
    required.write_text('changed required input')
    item['inputs'][boundary] = [required.name]
    original = os.open
    def opening(path, *args, **kwargs):
        assert os.fspath(path) not in (required.name, str(required)), 'forbidden input opened'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'open', opening)
    for reuse_only in (False, True):
        with pytest.raises(ValueError, match='required input is excluded or denied'):
            execute(tmp_path, [item], 'invalid', reuse_only=reuse_only)
    assert not (tmp_path / 'ran').exists()


@pytest.mark.parametrize('category', ['source', 'artifact'])
def test_streamed_input_budgets_refuse_commands_and_prior_success(tmp_path, monkeypatch, category):
    import wbs
    required = tmp_path / 'required.bin'
    required.write_bytes(b'input')
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('ran').touch()\n",
               artifacts=[required.name] if category == 'artifact' else [])
    if category == 'source':
        item['inputs']['source'].append(required.name)
        item['inputs']['source_byte_limit'] = (tmp_path / 'check.py').stat().st_size + required.stat().st_size
    assert execute(tmp_path, [item])['status'] == 'PASS'
    (tmp_path / 'ran').unlink()
    if category == 'artifact':
        monkeypatch.setattr(wbs, 'MAX_ARTIFACT_BYTES', required.stat().st_size)
    required.write_bytes(b'input changed')
    result = execute(tmp_path, [item], 'over-budget')['commands'][0]
    assert result['status'] == 'FAIL' and result['state'] == 'executor_error'
    assert result['returncode'] is None and not result['reused']
    assert category + ' bytes' in result['error'] and 'required.bin' in result['error']
    assert not (tmp_path / 'ran').exists()


def test_real_cli_explicit_frozen_root_help_and_invalid(tmp_path):
    import verification_target as vt
    # A .git-free view nested inside a checkout must never walk to the main root.
    original = tmp_path / "original"
    original.mkdir()
    jobs = shared(original, "build/built")
    producer_script = original / "prepare.py"
    producer_script.write_text("#!" + sys.executable + "\nimport sys\nif '--version' in sys.argv:\n print('prepare-v1')\n raise SystemExit(0)\n" + producer_script.read_text())
    producer_script.chmod(0o755)
    jobs[0]["argv"] = ["./prepare.py"]
    view = tmp_path / "view"
    view.mkdir()
    vt._manifest(original, [p.name for p in original.glob("*.py")], destination=view)
    vt._seal_view(view, ["build"])
    assert view.stat().st_mode & 0o222 == 0
    assert all(p.stat().st_mode & 0o222 == 0 for p in view.glob("*.py"))
    declaration = tmp_path / "jobs.json"
    declaration.write_text(json.dumps(jobs))
    argv = [sys.executable, str(SCRIPTS / "verify_runner.py")]
    assert subprocess.run(argv + ["--help"], capture_output=True).returncode == 0
    args = ["--jobs", str(declaration), "--root", str(view), "--results", str(tmp_path / "group-results.json"), "--task-id", "task", "--run-id", "run", "--generation", "one"]
    proc = subprocess.run(argv + args, cwd=ROOT, capture_output=True, text=True, timeout=20)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["schema"] == runner.SCHEMA
    restored = tmp_path / "restored"
    restored.mkdir()
    for script in view.glob("*.py"):
        shutil.copyfile(script, restored / script.name)
        (restored / script.name).chmod(0o755 if script.stat().st_mode & 0o111 else 0o644)
        assert (restored / script.name).stat().st_mode & 0o200
    (restored / "build").mkdir()
    shutil.copyfile(view / "build/built", restored / "build/built")
    # External per-group results stay fixed while source/artifact roots relocate.
    args[args.index("--root") + 1] = str(restored)
    args[args.index("--generation") + 1] = "restored"
    again = subprocess.run(argv + args, capture_output=True, text=True, timeout=20)
    assert again.returncode == 0, again.stderr
    assert all(r["reused"] for r in json.loads(again.stdout)["commands"])
    (restored / "build/built").unlink()
    args[args.index("--generation") + 1] = "regenerated"
    regenerated = subprocess.run(argv + args, capture_output=True, text=True, timeout=20)
    assert regenerated.returncode == 0, regenerated.stderr
    assert [r["reused"] for r in json.loads(regenerated.stdout)["commands"]] == [False, True, True]
    declaration.write_text('{"invalid":true}')
    invalid = subprocess.run(argv + args, capture_output=True, text=True, timeout=10)
    assert invalid.returncode == 2 and "bounded jobs" in invalid.stderr


def test_producer_output_real_budget_blocks_consumer_and_reuse(tmp_path):
    item = job(tmp_path, "prepare", "from pathlib import Path\nwith Path('built').open('wb') as f: f.truncate(33554433)\n",
               kind="producer", outputs=["built"])
    consumer = job(tmp_path, "consume", "raise AssertionError('must not run')\n", artifacts=["built"])
    result = execute(tmp_path, [item, consumer])["commands"]
    assert result[0]["status"] == "FAIL" and result[0]["state"] == "executor_error"
    assert "artifact bytes" in result[0]["error"]
    assert result[1]["state"] == "blocked_dependency"
    again = execute(tmp_path, [item], "two", reuse_only=True)["commands"][0]
    assert again["status"] == "FAIL" and not again["reused"]


def test_producer_output_aggregate_budget_and_bad_old_output_repair(tmp_path, monkeypatch):
    import wbs
    monkeypatch.setattr(wbs, "MAX_ARTIFACT_BYTES", 8)
    item = job(tmp_path, "prepare", "from pathlib import Path\nPath('a').write_bytes(b'12345')\nPath('b').write_bytes(b'67890')\n",
               kind="producer", outputs=["a", "b"], resources=["output-budget:" + str(tmp_path)])
    failed = execute(tmp_path, [item])["commands"][0]
    assert failed["status"] == "FAIL" and "artifact bytes" in failed["error"]
    (tmp_path / "prepare.py").write_text("from pathlib import Path\nPath('a').write_bytes(b'12')\nPath('b').write_bytes(b'34')\n")
    repaired = execute(tmp_path, [item], "repaired")["commands"][0]
    assert repaired["status"] == "PASS" and not repaired["reused"]
    assert execute(tmp_path, [item], "reuse")["commands"][0]["reused"]
    # Result, output and named resource ownership all released after failure.
    (tmp_path / "a").write_bytes(b'oversized previous output')
    assert execute(tmp_path, [item], "repair-again")["status"] == "PASS"


@pytest.mark.parametrize("reuse", [False, True])
def test_output_observation_deadline_failure_releases_leases(tmp_path, monkeypatch, reuse):
    import verification_target as vt
    import install_verified
    item = job(tmp_path, "prepare", "from pathlib import Path\nPath('built').write_text('valid')\n",
               kind="producer", outputs=["built"], resources=["output-deadline:" + str(tmp_path)])
    if reuse:
        assert execute(tmp_path, [item])["status"] == "PASS"
    original_manifest, original_read = vt._manifest, install_verified._read_payload_file
    expired = []
    def manifest(root, paths, **kwargs):
        if kwargs.get('policy', {}).get('artifacts') == ['built']:
            assert kwargs.get('_deadline') is not None
            expired[:] = [kwargs['_deadline'] + 1]
        return original_manifest(root, paths, **kwargs)
    def read(root, name, **kwargs):
        if name == 'built':
            clock = vt.time.monotonic
            try:
                monkeypatch.setattr(vt.time, 'monotonic', lambda: expired[0])
                return original_read(root, name, **kwargs)
            finally:
                monkeypatch.setattr(vt.time, 'monotonic', clock)
        return original_read(root, name, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(vt, '_manifest', manifest)
        patch.setattr(install_verified, '_read_payload_file', read)
        failed = execute(tmp_path, [item], "expired")["commands"][0]
    assert failed['status'] == 'FAIL' and not failed['reused']
    assert 'time budget exceeded' in failed['error']
    assert execute(tmp_path, [item], "recovered")['status'] == 'PASS'


def test_producer_output_change_during_reuse_is_not_pass(tmp_path, monkeypatch):
    import verification_target as vt
    item = job(tmp_path, "prepare", "from pathlib import Path\nPath('built').write_text('original')\n",
               kind="producer", outputs=["built"])
    assert execute(tmp_path, [item])["status"] == "PASS"
    original, observations = vt._manifest, []
    def manifest(root, paths, **kwargs):
        if kwargs.get('policy', {}).get('artifacts') == ['built']:
            observations.append(True)
            if len(observations) == 2:
                (tmp_path / 'built').write_text('changed')
        return original(root, paths, **kwargs)
    monkeypatch.setattr(vt, '_manifest', manifest)
    failed = execute(tmp_path, [item], "changed")["commands"][0]
    assert failed['status'] == 'FAIL' and not failed['reused']
    assert 'outputs changed during reuse validation' in failed['error']


def test_producer_output_fifo_fails_without_blocking_or_retaining_leases(tmp_path):
    item = job(tmp_path, 'prepare', "import os\nos.mkfifo('built')\n",
               kind='producer', outputs=['built'], resources=['fifo:' + str(tmp_path)])
    declaration = tmp_path / 'fifo-job.json'
    declaration.write_text(json.dumps([item]))
    argv = [sys.executable, str(SCRIPTS / 'verify_runner.py'), '--root', str(tmp_path),
            '--jobs', str(declaration), '--results', str(tmp_path / 'results.json'),
            '--task-id', 'task', '--run-id', 'run', '--generation', 'fifo', '--timeout', '5']
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 1, proc.stderr
    failed = json.loads(proc.stdout)['commands'][0]
    assert failed['state'] == 'executor_error' and failed['status'] == 'FAIL'
    assert 'regular file' in failed['error']
    (tmp_path / 'built').unlink()
    (tmp_path / 'prepare.py').write_text("from pathlib import Path\nPath('built').write_text('repaired')\n")
    assert execute(tmp_path, [item], 'repaired')['status'] == 'PASS'


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
        interrupted = json.loads((tmp_path / "result.json").read_text())
        assert interrupted["status"] == "FAIL" and interrupted["successful"] == []
        assert interrupted["commands"][0]["execution_pending"]
        assert interrupted["commands"][0]["returncode"] is None
    finally:
        (tmp_path / "release").touch()
        if proc.poll() is None:
            proc.kill()
        proc.communicate(timeout=10)


def test_real_cli_consumers_enter_in_parallel_after_shared_preparation(tmp_path):
    from contextlib import ExitStack

    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        jobs = shared(tmp_path)
        for item in jobs[1:]:
            script = tmp_path / (item["job_id"] + ".py")
            script.write_text(script.read_text() + f"import socket\nwith socket.create_connection(('127.0.0.1', {server.getsockname()[1]}), timeout=30) as s:\n s.sendall({item['job_id'].encode()!r} + b'\\n')\n assert s.recv(2) == b'ok'\n")
        declaration = tmp_path / "jobs.json"
        declaration.write_text(json.dumps(jobs))
        arrivals, accepted, barrier_error = [], 0, None
        with subprocess.Popen([sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(tmp_path / "result.json"), "--task-id", "t", "--run-id", "r", "--generation", "g", "--max-workers", "2", "--timeout", "45"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
            try:
                with ExitStack() as sockets:
                    connections = []
                    # Imports, fingerprints and preparation precede the first
                    # arrival; this is a startup bound, not an overlap measure.
                    server.settimeout(30)
                    for _ in range(2):
                        connection = sockets.enter_context(server.accept()[0])
                        accepted += 1
                        connection.settimeout(15)
                        connections.append(connection)
                        with connection.makefile("rb") as stream:
                            arrivals.append(stream.readline(16).decode().strip())
                        server.settimeout(15)
                    assert sorted(arrivals) == ["alpha", "beta"]
                    # Neither consumer can finish before both distinct clients
                    # arrive. A serialized executor cannot satisfy this barrier.
                    for connection in connections:
                        connection.sendall(b"ok")
            except Exception as exc:
                barrier_error = repr(exc)
            try:
                stdout, stderr = proc.communicate(timeout=90)
            except subprocess.TimeoutExpired as exc:
                proc.kill()
                stdout, stderr = proc.communicate(timeout=10)
                barrier_error = f"{barrier_error}; CLI timeout: {exc}"
        diagnostic = f"accepted={accepted}, arrivals={arrivals}, barrier_error={barrier_error}\nstdout={stdout}\nstderr={stderr}"
        assert proc.returncode == 0 and barrier_error is None, diagnostic
        assert len(json.loads(stdout)["commands"]) == 3, diagnostic


def test_repeated_reuse_and_reuse_only_misses_preserve_origin_and_diagnostics(tmp_path):
    original = "print('EARLY diagnostic')\nprint('x' * 9000)\n"
    item = job(tmp_path, 'check', original)
    first = execute(tmp_path, [item])['commands'][0]
    reference = first['output_log']
    log = Path(reference['path'])
    assert log.read_text().startswith('EARLY diagnostic\n')
    assert 'EARLY' not in first['output']
    for generation in ('two', 'three'):
        current = execute(tmp_path, [item], generation, reuse_only=True)['commands'][0]
        assert current['reused'] and current['origin_generation'] == 'one'
        assert current['output'] == first['output'] and current['output_log'] == reference
    (tmp_path / 'check.py').write_text("raise AssertionError('must not execute B')\n")
    missed = execute(tmp_path, [item], 'B', reuse_only=True)
    assert missed['status'] == 'FAIL' and missed['commands'][0]['returncode'] is None
    assert missed['successful'][0]['origin_generation'] == 'one'
    assert log.read_text().startswith('EARLY diagnostic\n')
    (tmp_path / 'check.py').write_text(original)
    restored = execute(tmp_path, [item], 'A', reuse_only=True)['commands'][0]
    assert restored['reused'] and restored['output_log'] == reference
    log.unlink()
    missing = execute(tmp_path, [item], 'missing-log', reuse_only=True)['commands'][0]
    assert missing['reused'] and missing['output'] == first['output']
    assert missing['output_log']['available'] is False


def test_actual_failure_invalidates_matching_prior_success(tmp_path):
    # Undeclared marker intentionally controls this trusted fixture's outcome;
    # declaration argv/source remain identical across the actual attempts.
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('built').write_text('same artifact')\nprint('actual attempt')\nraise SystemExit(7 if Path('fail').exists() else 0)\n", kind="producer", outputs=["built"])
    assert execute(tmp_path, [item])['status'] == 'PASS'
    (tmp_path / 'fail').touch()
    # Lost artifact forces actual execution with identical input/context; the
    # failing attempt recreates the original output bytes before returning 7.
    (tmp_path / "built").unlink()
    failed = execute(tmp_path, [item], 'failed')
    assert failed['commands'][0]['state'] == 'command_failure' and failed['successful'] == []
    assert 'actual attempt' in Path(failed['commands'][0]['output_log']['path']).read_text()
    missed = execute(tmp_path, [item], 'later', reuse_only=True)
    assert missed['status'] == 'FAIL' and not missed['commands'][0]['reused']
    evidence = missed['commands'][0]['last_execution']
    assert evidence['returncode'] == 7 and evidence['execution_generation'] == 'failed'
    assert Path(evidence['output_log']['path']).read_text().startswith('actual attempt')
    again = execute(tmp_path, [item], 'later-again', reuse_only=True)
    assert again['commands'][0]['last_execution'] == evidence


@pytest.mark.parametrize('failure', ['exit', 'timeout', 'overflow'])
def test_full_diagnostics_preserve_early_failure_output_and_bounds(tmp_path, failure):
    code = "import sys,time\nprint('EARLY failure evidence', flush=True)\nprint('z' * 9000, flush=True)\n"
    code += {'exit': 'raise SystemExit(9)\n', 'timeout': 'time.sleep(5)\n',
             'overflow': "sys.stdout.write('z' * 1100000)\nsys.stdout.flush()\n"}[failure]
    item = job(tmp_path, 'check', code)
    record = execute(tmp_path, [item], timeout=0.3 if failure == 'timeout' else 5)['commands'][0]
    assert record['status'] == 'FAIL'
    reference = record['output_log']
    data = Path(reference['path']).read_bytes()
    assert data.startswith(b'EARLY failure evidence\n') and len(data) <= 1048576
    assert reference['bytes'] == len(data) and reference['available']
    assert reference['truncated'] == (failure == 'overflow')
    if failure == 'overflow':
        assert record['error'] == 'output limit exceeded'


@pytest.mark.parametrize('unsafe', ['symlink', 'source', 'output'])
def test_diagnostic_destinations_reject_before_launch(tmp_path, unsafe):
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('ran').touch()\n")
    directory = tmp_path / 'results.json.logs'
    if unsafe == 'symlink':
        elsewhere = tmp_path / 'elsewhere'
        elsewhere.mkdir()
        directory.symlink_to(elsewhere, target_is_directory=True)
    elif unsafe == 'source':
        directory.mkdir()
        (directory / 'input').write_text('selected')
        item['inputs']['source'].append(directory.name)
    else:
        item['kind'] = 'producer'
        item['outputs'] = [directory.name]
    with pytest.raises(ValueError, match='diagnostic|symlink'):
        execute(tmp_path, [item])
    assert not (tmp_path / 'ran').exists()


def test_prior_log_cannot_become_selected_source_or_artifact(tmp_path):
    first = execute(tmp_path, [job(tmp_path, 'old', "print('old log')\n")])['commands'][0]
    path = Path(first['output_log']['path'])
    for category in ('source', 'artifacts'):
        item = job(tmp_path, 'new', "from pathlib import Path\nPath('ran').touch()\n")
        item['inputs'].setdefault(category, []).append(path.relative_to(tmp_path).as_posix())
        with pytest.raises(ValueError, match='diagnostic path overlaps'):
            execute(tmp_path, [item], 'selected-log')
        assert path.read_text() == 'old log\n' and not (tmp_path / 'ran').exists()


def test_failed_diagnostics_survive_blocking_and_refresh_missing_log(tmp_path):
    consumer = job(tmp_path, 'consumer', "print('consumer failure')\nraise SystemExit(7)\n")
    failed = execute(tmp_path, [consumer])['commands'][0]
    path = Path(failed['output_log']['path'])
    producer = job(tmp_path, 'producer', 'raise SystemExit(8)\n', kind='producer')
    blocked = execute(tmp_path, [producer, consumer], 'blocked')['commands'][1]
    assert blocked['state'] == 'blocked_dependency' and blocked['returncode'] is None
    assert blocked['last_execution']['returncode'] == 7
    assert path.read_text() == 'consumer failure\n'
    path.unlink()
    missed = execute(tmp_path, [consumer], 'miss', reuse_only=True)['commands'][0]
    assert missed['last_execution']['output'] == 'consumer failure\n'
    assert missed['last_execution']['output_log']['available'] is False
    assert missed['status'] == 'FAIL' and missed['returncode'] is None


def test_log_slots_remain_bounded_across_jobs_and_runs(tmp_path):
    for index in range(4):
        item = job(tmp_path, 'job' + str(index), "print('bounded')\n")
        result = execute(tmp_path, [item], str(index), run_id='run' + str(index))
        current = Path(result['commands'][0]['output_log']['path'])
        assert current.read_text() == 'bounded\n'
        assert list(current.parent.glob('*.log')) == [current]
    # A leftover managed slot from an interrupted publication is reclaimed;
    # unrelated files in that directory are outside the managed naming scheme.
    leftover = current.parent / ('0' * 64 + '-attempt.log')
    leftover.write_text('interrupted')
    unrelated = current.parent / 'notes.txt'
    unrelated.write_text('foreign')
    execute(tmp_path, [item], 'last', run_id='run3')
    assert not leftover.exists() and unrelated.read_text() == 'foreign'


@pytest.mark.parametrize('corruption', ['returncode', 'commands', 'empty', 'utf8', 'oversized'])
def test_invalid_current_results_cannot_be_repaired_by_success_copy(tmp_path, corruption):
    item = job(tmp_path, 'check', "print('valid execution')\n")
    assert execute(tmp_path, [item])['status'] == 'PASS'
    path = tmp_path / 'results.json'
    if corruption in ('returncode', 'commands', 'empty'):
        payload = json.loads(path.read_text())
        if corruption == 'returncode':
            payload['commands'][0]['returncode'] = 7
        else:
            payload['commands'] = [] if corruption == 'empty' else None
        path.write_text(json.dumps(payload))
    elif corruption == 'utf8':
        path.write_bytes(b'\xff')
    else:
        with path.open('wb') as stream:
            stream.truncate(16 * 1024 * 1024 + 1)
    result = execute(tmp_path, [item], 'unavailable', reuse_only=True)
    assert result['status'] == 'FAIL' and not result['commands'][0]['reused']
    assert execute(tmp_path, [item], 'regenerate')['status'] == 'PASS'


def test_retained_success_diagnostic_corruption_or_loss_stays_unavailable(tmp_path):
    original = "print('bounded summary')\n"
    item = job(tmp_path, 'check', original)
    record = execute(tmp_path, [item])['commands'][0]
    Path(record['output_log']['path']).unlink()
    (tmp_path / 'check.py').write_text("print('changed')\n")
    missed = execute(tmp_path, [item], 'miss', reuse_only=True)
    assert missed['status'] == 'FAIL' and not missed['successful'][0]['output_log']['available']
    payload = json.loads((tmp_path / 'results.json').read_text())
    payload['successful'][0]['output_log'] = []
    (tmp_path / 'results.json').write_text(json.dumps(payload))
    corrupted = execute(tmp_path, [item], 'malformed-log', reuse_only=True)
    assert corrupted['status'] == 'FAIL' and corrupted['successful'][0]['output_log'] == {'available': False}
    (tmp_path / 'check.py').write_text(original)
    reused = execute(tmp_path, [item], 'restored', reuse_only=True)['commands'][0]
    assert reused['reused'] and reused['output'] == 'bounded summary\n'
    assert reused['output_log'] == {'available': False}


def test_killed_rerun_cannot_resurrect_success_and_keeps_unverified_log(tmp_path):
    code = "from pathlib import Path\nimport time\nPath('built').write_text('same')\nif Path('rerun').exists():\n Path('ready').touch()\n while not Path('release').exists(): time.sleep(.01)\n print('failed rerun evidence', flush=True)\n raise SystemExit(7)\nprint('first success')\n"
    item = job(tmp_path, 'owner', code, kind='producer', outputs=['built'])
    first = execute(tmp_path, [item])['commands'][0]
    assert first['status'] == 'PASS'
    (tmp_path / 'built').unlink()
    (tmp_path / 'rerun').touch()
    declaration = tmp_path / 'jobs.json'
    declaration.write_text(json.dumps([item]))
    argv = [sys.executable, '-B', str(SCRIPTS / 'verify_runner.py'), '--root', str(tmp_path),
            '--jobs', str(declaration), '--results', str(tmp_path / 'results.json'),
            '--task-id', 'task', '--run-id', 'run', '--generation', 'interrupted', '--timeout', '8']
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / 'ready').exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        pending = json.loads((tmp_path / 'results.json').read_text())
        assert pending['status'] == 'FAIL' and pending['successful'] == []
        assert pending['commands'][0]['execution_pending']
        proc.kill()
        proc.communicate(timeout=5)
    finally:
        (tmp_path / 'release').touch()
        if proc.poll() is None:
            proc.kill()
            proc.communicate(timeout=5)
    result = execute(tmp_path, [item], 'later', reuse_only=True)
    record = result['commands'][0]
    assert result['status'] == 'FAIL' and not record['reused'] and result['successful'] == []
    evidence = record['last_execution']
    assert evidence['returncode'] is None and evidence['execution_pending']
    assert not evidence['output_log']['available']
    assert Path(evidence['output_log']['path']).read_text() == 'failed rerun evidence\n'


def test_malformed_unicode_log_reference_does_not_repeat_valid_command(tmp_path):
    item = job(tmp_path, 'check', "print('valid summary')\n")
    assert execute(tmp_path, [item])['status'] == 'PASS'
    path = tmp_path / 'results.json'
    payload = json.loads(path.read_text())
    payload['successful'][0]['output_log']['path'] = '\ud800'
    path.write_text(json.dumps(payload))
    record = execute(tmp_path, [item], 'malformed-unicode', reuse_only=True)['commands'][0]
    assert record['status'] == 'PASS' and record['reused']
    assert record['output'] == 'valid summary\n' and record['output_log'] == {'available': False}
