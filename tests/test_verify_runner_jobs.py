"""CLI contracts for shared preparation, reuse and cooperative ownership."""
from __future__ import annotations

import importlib.util
import hashlib
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


@pytest.fixture(autouse=True)
def native_lease_home(tmp_path, monkeypatch):
    if os.name == "nt":
        from wiki_platform import ensure_private_directory
        home = ensure_private_directory(tmp_path / "native-home")
        monkeypatch.setenv("HOME", str(home))
        monkeypatch.setenv("USERPROFILE", str(home))


def job(root, name, code, *, kind="consumer", artifacts=(), outputs=(), resources=()):
    script = root / (name + ".py")
    script.write_text(code)
    return dict(job_id=name, outcome_id=name, kind=kind, argv=[sys.executable, script.name],
                inputs=dict(source=[script.name], artifacts=list(artifacts)), outputs=list(outputs), resources=list(resources))


def execute(root, jobs, generation="one", **kwargs):
    return runner.run_jobs(jobs, root, wbs_id=kwargs.pop("wbs_id", "task"), run_id=kwargs.pop("run_id", "run"),
                           execution_generation=generation, results_path=result_path(root),
                           max_workers=2, timeout=kwargs.pop("timeout", 5), **kwargs)


def result_path(root, name="results.json"):
    directory = root / "verification"
    directory.mkdir(exist_ok=True)
    return directory / name


def native_owner_fault(monkeypatch, root, setup):
    """Inject a test fault in the genuine owned child entry, never its caller."""
    import native_cli
    marker = result_path(root).with_name("native-owner-fault-fired")
    prefix = f"""import sys
def _fixture_owner_trace(frame, event, argument):
 if event == 'call' and frame.f_code.co_name == '_native_owner_main' and frame.f_code.co_filename.casefold() == {str(SCRIPTS / 'verify_runner.py')!r}.casefold():
  assert frame.f_globals is vars(sys.modules['__main__'])
  assert frame.f_code is frame.f_globals['_native_owner_main'].__code__
  assert sys.argv[1:] == ['--native-job-owner']
  sys.settrace(None)
  frame.f_globals['_fixture_fault_marker'] = {str(marker)!r}
  exec({setup!r}, frame.f_globals)
 return _fixture_owner_trace
sys.settrace(_fixture_owner_trace)
"""
    monkeypatch.setattr(native_cli, "_BOOTSTRAP", prefix + native_cli._BOOTSTRAP)
    return marker


def shared(root, artifact="built"):
    producer = job(root, "prepare", f"from pathlib import Path\nPath({artifact!r}).write_text('shared')\n", kind="producer", outputs=[artifact])
    consumers = [job(root, name, f"from pathlib import Path\nassert Path({artifact!r}).read_text() == 'shared'\nprint('consumed')\n", artifacts=[artifact]) for name in ("alpha", "beta")]
    return [producer, *consumers]


@pytest.mark.skipif(os.name != "nt", reason="requires actual Native source-custody placement validation")
@pytest.mark.parametrize("placement", ["source-root", "ancestor"])
def test_native_results_require_dedicated_directory_before_execution(tmp_path, monkeypatch, placement):
    from wiki_platform import ensure_private_directory
    source = ensure_private_directory(tmp_path / "source" / "nested")
    home = ensure_private_directory(tmp_path / "isolated-home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    item = job(source, "check", "from pathlib import Path\nPath('ran').touch()\n")
    target = (source if placement == "source-root" else source.parent) / "results.json"
    target.write_bytes(b"preserved prior result bytes")
    identity = (target.stat().st_dev, target.stat().st_ino)
    with pytest.raises(ValueError, match="dedicated --results directory"):
        runner.run_jobs([item], source, wbs_id="task", run_id="run", execution_generation="one",
                        results_path=target)
    assert target.read_bytes() == b"preserved prior result bytes"
    assert (target.stat().st_dev, target.stat().st_ino) == identity
    assert not (source / "ran").exists()
    assert not target.with_name(target.name + ".logs").exists()
    assert not (home / ".cache" / "harness-verification-leases").exists()


@pytest.mark.skipif(os.name == "nt", reason="Native placement is covered by dedicated-directory validation")
def test_posix_root_result_location_remains_supported(tmp_path):
    item = job(tmp_path, "check", "print('root result remains supported')\n")
    target = tmp_path / "results.json"
    result = runner.run_jobs([item], tmp_path, wbs_id="task", run_id="run", execution_generation="one",
                             results_path=target)
    assert result["status"] == "PASS" and target.is_file()


@pytest.mark.skipif(os.name != "nt", reason="requires actual Native base environment and scoped reuse")
def test_native_systemroot_is_implicit_measured_and_other_environment_stays_scoped(tmp_path, monkeypatch):
    systemroot = os.environ["SYSTEMROOT"]
    assert Path(systemroot).is_dir()
    monkeypatch.setenv("NATIVE_UNDECLARED_SECRET", "first private fixture value")
    code = ("import json,os\nprint(json.dumps({name: os.environ.get(name) for name in "
            "('SYSTEMROOT', 'PATH', 'NATIVE_UNDECLARED_SECRET', 'HOME', 'USERPROFILE', 'TEMP', 'TMP')}))\n")
    item = job(tmp_path, "environment", code)
    item["requires"] = ["executable:" + sys.executable]
    first = execute(tmp_path, [item])["commands"][0]
    assert first["status"] == "PASS", first
    observed = json.loads(first["output"])
    assert observed["SYSTEMROOT"] == systemroot and observed["PATH"] == os.environ["PATH"]
    assert all(observed[name] is None for name in ('NATIVE_UNDECLARED_SECRET', 'HOME', 'USERPROFILE', 'TEMP', 'TMP'))
    monkeypatch.setenv("NATIVE_UNDECLARED_SECRET", "changed private fixture value")
    assert execute(tmp_path, [item], "undeclared-change")["commands"][0]["reused"]
    # A real, valid case alias changes the measured environment without
    # modifying the OS directory or executing a different toolchain image.
    changed = systemroot.swapcase()
    assert changed != systemroot and Path(changed).is_dir()
    monkeypatch.setenv("SYSTEMROOT", changed)
    refreshed = execute(tmp_path, [item], "base-environment-change")["commands"][0]
    assert refreshed["status"] == "PASS" and not refreshed["reused"], refreshed
    assert refreshed["context_fingerprint"] != first["context_fingerprint"]
    assert json.loads(refreshed["output"])["SYSTEMROOT"] == changed


@pytest.mark.skipif(os.name != "nt", reason="requires actual Native context child environment validation")
@pytest.mark.parametrize("count", [31, 32])
def test_native_implicit_os_environment_preserves_declared_name_budget(tmp_path, monkeypatch, count):
    names = ["HARNESS_TEST_ENV_%02d" % index for index in range(count)]
    for name in names:
        monkeypatch.setenv(name, "harmless boundary value")
    item = job(tmp_path, "environment_budget",
               f"import json,os\nprint(json.dumps({{name: os.environ[name] for name in {names!r}}}))\n")
    item.update(environment=names, requires=["executable:" + sys.executable])
    first = execute(tmp_path, [item])["commands"][0]
    assert first["status"] == "PASS", first
    assert json.loads(first["output"]) == dict.fromkeys(names, "harmless boundary value")
    assert execute(tmp_path, [item], "reuse")["commands"][0]["reused"]
    monkeypatch.setenv(names[-1], "changed harmless value")
    refreshed = execute(tmp_path, [item], "declared-change")["commands"][0]
    assert refreshed["status"] == "PASS" and not refreshed["reused"], refreshed
    assert refreshed["context_fingerprint"] != first["context_fingerprint"]
    assert json.loads(refreshed["output"])[names[-1]] == "changed harmless value"


def test_declared_environment_over_budget_is_rejected_before_execution(tmp_path):
    item = job(tmp_path, "environment_over_budget", "from pathlib import Path\nPath('command-started').touch()\n")
    item["environment"] = ["HARNESS_TEST_ENV_%02d" % index for index in range(33)]
    with pytest.raises(ValueError, match="invalid environment"):
        execute(tmp_path, [item])
    assert not (tmp_path / "command-started").exists()
    assert not result_path(tmp_path).exists()


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
    if category == 'artifact' and os.name != "nt":
        monkeypatch.setattr(wbs, 'MAX_ARTIFACT_BYTES', required.stat().st_size)
    if category == 'artifact' and os.name == "nt":
        # The Native owner is another process: exercise its real byte bound.
        with required.open('wb') as stream:
            stream.truncate(wbs.MAX_ARTIFACT_BYTES + 1)
    else:
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
    jobs[0]["argv"] = [sys.executable, "prepare.py"] if os.name == "nt" else ["./prepare.py"]
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
    args = ["--jobs", str(declaration), "--root", str(view), "--results", str(result_path(tmp_path, "group-results.json")), "--wbs-id", "task", "--run-id", "run", "--generation", "one"]
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
    if os.name != "nt":
        monkeypatch.setattr(wbs, "MAX_ARTIFACT_BYTES", 8)
    size = wbs.MAX_ARTIFACT_BYTES // 2 + 1
    item = job(tmp_path, "prepare", f"from pathlib import Path\nfor name in ('a', 'b'):\n with Path(name).open('wb') as stream: stream.truncate({size})\n",
               kind="producer", outputs=["a", "b"], resources=["output-budget:" + str(tmp_path)])
    failed = execute(tmp_path, [item])["commands"][0]
    assert failed["status"] == "FAIL" and "artifact bytes" in failed["error"]
    (tmp_path / "prepare.py").write_text("from pathlib import Path\nPath('a').write_bytes(b'12')\nPath('b').write_bytes(b'34')\n")
    repaired = execute(tmp_path, [item], "repaired")["commands"][0]
    assert repaired["status"] == "PASS" and not repaired["reused"]
    assert execute(tmp_path, [item], "reuse")["commands"][0]["reused"]
    # Result, output and named resource ownership all released after failure.
    with (tmp_path / "a").open('wb') as stream:
        stream.truncate(wbs.MAX_ARTIFACT_BYTES + 1)
    assert execute(tmp_path, [item], "repair-again")["status"] == "PASS"


@pytest.mark.parametrize("reuse", [False, True])
def test_output_observation_deadline_failure_releases_leases(tmp_path, monkeypatch, reuse):
    import verification_target as vt
    import install_verified
    item = job(tmp_path, "prepare", "from pathlib import Path\nPath('built').write_text('valid')\n",
               kind="producer", outputs=["built"], resources=["output-deadline:" + str(tmp_path)])
    if reuse:
        assert execute(tmp_path, [item])["status"] == "PASS"
    if os.name == "nt":
        with monkeypatch.context() as patch:
            marker = native_owner_fault(patch, tmp_path, """import verification_target as vt
import install_verified
_fixture_manifest, _fixture_read = vt._manifest, install_verified._read_payload_file
_fixture_expired = []
def _fixture_output_manifest(root, paths, **kwargs):
 if kwargs.get('policy', {}).get('artifacts') == ['built']:
  assert kwargs.get('_deadline') is not None
  _fixture_expired[:] = [kwargs['_deadline'] + 1]
 return _fixture_manifest(root, paths, **kwargs)
def _fixture_output_read(root, name, **kwargs):
 if name == 'built':
  clock = vt.time.monotonic
  try:
   vt.time.monotonic = lambda: _fixture_expired[0]
   Path(_fixture_fault_marker).write_text('output read deadline expired in child')
   return _fixture_read(root, name, **kwargs)
  finally:
   vt.time.monotonic = clock
 return _fixture_read(root, name, **kwargs)
vt._manifest = _fixture_output_manifest
install_verified._read_payload_file = _fixture_output_read
""")
            failed = execute(tmp_path, [item], "expired")["commands"][0]
        assert marker.read_text() == 'output read deadline expired in child'
        assert failed['status'] == 'FAIL' and not failed['reused']
        assert 'time budget exceeded' in failed['error']
        assert (tmp_path / "built").read_text() == "valid"
        assert execute(tmp_path, [item], "recovered")["status"] == "PASS"
        return
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
    if os.name == "nt":
        marker = native_owner_fault(monkeypatch, tmp_path, """import verification_target as vt
_fixture_manifest, _fixture_observations = vt._manifest, []
def _fixture_changed_output(root, paths, **kwargs):
 if kwargs.get('policy', {}).get('artifacts') == ['built']:
  _fixture_observations.append(True)
  if len(_fixture_observations) == 2:
   (root / 'built').write_text('changed')
   Path(_fixture_fault_marker).write_text('artifact changed in actual child')
 return _fixture_manifest(root, paths, **kwargs)
vt._manifest = _fixture_changed_output
""")
        failed = execute(tmp_path, [item], "changed")["commands"][0]
        assert marker.read_text() == "artifact changed in actual child"
        assert failed['status'] == 'FAIL' and not failed['reused']
        assert 'outputs changed during reuse validation' in failed['error']
        return
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
    # Windows has no filesystem FIFO. A directory exercises the same required
    # regular-file output boundary without inventing an unsupported OS call.
    code = "from pathlib import Path\nPath('built').mkdir()\n" if os.name == "nt" else "import os\nos.mkfifo('built')\n"
    item = job(tmp_path, 'prepare', code,
               kind='producer', outputs=['built'], resources=['fifo:' + str(tmp_path)])
    declaration = tmp_path / 'fifo-job.json'
    declaration.write_text(json.dumps([item]))
    argv = [sys.executable, str(SCRIPTS / 'verify_runner.py'), '--root', str(tmp_path),
            '--jobs', str(declaration), '--results', str(result_path(tmp_path)),
            '--wbs-id', 'task', '--run-id', 'run', '--generation', 'fifo', '--timeout', '5']
    proc = subprocess.run(argv, capture_output=True, text=True, timeout=15)
    assert proc.returncode == 1, proc.stderr
    failed = json.loads(proc.stdout)['commands'][0]
    assert failed['state'] == 'executor_error' and failed['status'] == 'FAIL'
    assert 'regular file' in failed['error']
    if os.name == "nt":
        (tmp_path / 'built').rmdir()
    else:
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
        jobs[0]["outputs"] = ["verification/results.json"]
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
        (result_path(tmp_path)).write_text(corrupt)
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
    args = [sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(result_path(tmp_path, "holder-result.json")), "--wbs-id", "other-task", "--run-id", "other-run", "--generation", "one", "--timeout", "10"]
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


def test_output_leases_conflict_only_at_ancestor_or_same_leaf(tmp_path, monkeypatch):
    home = tmp_path / "private-lease-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    first, second, shared = {}, {}, {}
    runner._path_leases(first, tmp_path / "build" / "a", True)
    runner._path_leases(second, tmp_path / "build" / "b", True)
    runner._path_leases(shared, tmp_path / "build", True)
    held = runner._lease(first)
    assert held is not None
    try:
        independent = runner._lease(second)
        assert independent is not None
        runner._close_leases(independent)
        assert runner._lease(shared) is None
        reader = {}
        runner._path_leases(reader, tmp_path / "build" / "a", False)
        assert runner._lease(reader) is None
    finally:
        runner._close_leases(held)


def test_scoped_toolchain_probe_and_malformed_success_are_not_false_reuse(tmp_path, monkeypatch):
    tool = tmp_path / "proxy"
    tool.write_text("#!" + sys.executable + "\nimport os\nprint(os.environ.get('UNDECLARED', 'scoped'))\n")
    tool.chmod(0o755)
    item = dict(job_id="tool", outcome_id="tool", kind="consumer", argv=["proxy"], inputs=dict(source=["proxy"]), toolchains=["proxy"])
    if os.name == "nt":
        # Native executables have no shebang dispatch. Keep the undeclared
        # environment probe in the script and fingerprint the real interpreter.
        item.update(argv=[sys.executable, "proxy"], toolchains=[sys.executable])
    monkeypatch.setenv("PATH", ".")
    monkeypatch.setenv("UNDECLARED", "ambient-one")
    assert execute(tmp_path, [item])["status"] == "PASS"
    monkeypatch.setenv("UNDECLARED", "ambient-two")
    assert execute(tmp_path, [item])["commands"][0]["reused"]
    for target in ("envelope", "record"):
        payload = json.loads((result_path(tmp_path)).read_text())
        if target == "envelope":
            payload.pop("execution_generation")
        else:
            payload["commands"][0]["execution_generation"] = None
        (result_path(tmp_path)).write_text(json.dumps(payload))
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
                records = json.loads((result_path(tmp_path)).read_text())["commands"]
                if records and records[0]["status"] == "PASS":
                    break
            except (OSError, ValueError):
                pass
            assert time.monotonic() < deadline
            time.sleep(.01)
        other = job(tmp_path, "replace", "from pathlib import Path\nPath('built').write_text('replacement')\n", kind="producer", outputs=["built"])
        assert runner.run_jobs([other], tmp_path, wbs_id="other", run_id="run", execution_generation="one", results_path=result_path(tmp_path, "other-results.json"), timeout=5)["status"] == "PASS"
    finally:
        runner._close_leases(held)
        thread.join(timeout=10)
    assert not thread.is_alive()
    assert result[0]["commands"][1]["state"] == "executor_error"
    assert "prepared artifact changed" in result[0]["commands"][1]["error"]


@pytest.mark.parametrize("termination", ["pid", "outer-timeout"] if os.name == "nt"
                         else ["pid", "group", "outer-timeout", "interrupt"])
@pytest.mark.parametrize("closed_stdout", [False, True])
def test_killed_runner_keeps_leases_until_supervisor_cleans_command(tmp_path, termination, closed_stdout):
    import signal

    name = "death-test:" + str(tmp_path)
    code = "from pathlib import Path\nimport os,time\nPath('worker.pid').write_text(str(os.getpid()))\n"
    if closed_stdout:
        code += "os.close(1)\nos.close(2)\n"
    code += "Path('ready').touch()\nwhile not Path('release').exists(): time.sleep(.01)\nPath('finished').touch()\n"
    item = job(tmp_path, "owner", code, kind="producer", outputs=["finished"], resources=[name])
    declaration = tmp_path / "jobs.json"
    declaration.write_text(json.dumps([item]))
    argv = [sys.executable, "-B", str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path),
            "--jobs", str(declaration), "--results", str(result_path(tmp_path, "result.json")),
            "--wbs-id", "t", "--run-id", "r", "--generation", "g", "--timeout", "15"]
    if termination == "outer-timeout":
        prefix = "import sys,json;sys.path.insert(0," + repr(str(SCRIPTS)) + ");import wbs;"
        argv = [sys.executable, "-B", "-c", prefix + "print(json.dumps(wbs._execute(" + repr(argv) + "," + repr(str(tmp_path)) + ",4)))"]
    proc = subprocess.Popen(argv, start_new_session=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    worker = None
    held = None
    try:
        deadline = time.monotonic() + 15
        while not (tmp_path / "ready").exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        worker = int((tmp_path / "worker.pid").read_text())
        assert runner._lease({"resource:" + name: True}) is None
        if termination == "pid":
            proc.kill()
        elif termination == "group":
            os.killpg(proc.pid, signal.SIGKILL)
        elif termination == "interrupt":
            proc.send_signal(signal.SIGINT)
        stdout, stderr = proc.communicate(timeout=10)
        if termination == "outer-timeout":
            assert json.loads(stdout)["error"] == "timeout", stderr
        while (held := runner._lease({"resource:" + name: True})) is None:
            assert time.monotonic() < deadline
            time.sleep(.01)
        # Once the next owner is admitted, the old command cannot still write.
        if os.name == "nt":
            from test_wiki_windows_native import _dead
            _dead(worker)
        else:
            state = Path('/proc') / str(worker) / 'stat'
            if state.exists():
                assert state.read_text().split()[2] == 'Z'
        (tmp_path / "release").touch()
        assert not (tmp_path / "finished").exists()
        interrupted = json.loads((result_path(tmp_path, "result.json")).read_text())
        assert interrupted["status"] == "FAIL" and interrupted["successful"] == []
        assert interrupted["commands"][0]["returncode"] is None
    finally:
        if held is not None:
            runner._close_leases(held)
        if proc.poll() is None:
            if os.name == "nt":
                proc.kill()
            else:
                os.killpg(proc.pid, signal.SIGKILL)
        if worker is not None and os.name != "nt":
            try:
                os.killpg(worker, signal.SIGKILL)
            except ProcessLookupError:
                pass
        proc.communicate(timeout=10)


def test_closed_parent_lifeline_refuses_command_before_spawn(tmp_path, monkeypatch):
    import wbs

    if os.name == "nt":
        from wiki_platform import execute_bounded
        # Native supervisors translate parent loss to this cancellation
        # predicate; POSIX supervisors watch the inherited pipe below.
        result = execute_bounded([sys.executable, '-c', "from pathlib import Path;Path('unexpected').touch()"],
                                 cwd=tmp_path, env=dict(os.environ), timeout=1,
                                 limit=1024, cancelled=lambda: True)
        assert result['returncode'] is None and result['error'] == 'parent execution ended'
        assert result['start_failed'] and not (tmp_path / 'unexpected').exists()
        return
    reader, writer = os.pipe()
    os.close(writer)
    try:
        monkeypatch.setattr(wbs, "_PARENT_LIFELINE", reader)
        result = wbs._execute([sys.executable, '-c', "from pathlib import Path;Path('unexpected').touch()"], str(tmp_path), 1)
        assert result['returncode'] is None and result['error'] == 'parent execution ended'
        assert not (tmp_path / 'unexpected').exists()
    finally:
        os.close(reader)


def test_real_cli_consumers_enter_in_parallel_after_shared_preparation(tmp_path, monkeypatch):
    from contextlib import ExitStack

    home = tmp_path / "private-lease-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
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
        with subprocess.Popen([sys.executable, str(SCRIPTS / "verify_runner.py"), "--root", str(tmp_path), "--jobs", str(declaration), "--results", str(result_path(tmp_path, "result.json")), "--wbs-id", "t", "--run-id", "r", "--generation", "g", "--max-workers", "2", "--timeout", "45"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as proc:
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
    assert data.startswith(b'EARLY failure evidence' + os.linesep.encode()) and len(data) <= 1048576
    assert reference['bytes'] == len(data) and reference['available']
    assert reference['truncated'] == (failure == 'overflow')
    if failure == 'overflow':
        assert record['error'] == 'output limit exceeded'


@pytest.mark.parametrize('unsafe', ['symlink', 'source', 'output'])
def test_diagnostic_destinations_reject_before_launch(tmp_path, unsafe):
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('ran').touch()\n")
    directory = result_path(tmp_path).with_name('results.json.logs')
    if unsafe == 'symlink':
        elsewhere = tmp_path / 'elsewhere'
        elsewhere.mkdir()
        directory.symlink_to(elsewhere, target_is_directory=True)
    elif unsafe == 'source':
        directory.mkdir()
        (directory / 'input').write_text('selected')
        item['inputs']['source'].append(directory.relative_to(tmp_path).as_posix())
    else:
        item['kind'] = 'producer'
        item['outputs'] = [directory.relative_to(tmp_path).as_posix()]
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
    assert missed['last_execution']['output'] == 'consumer failure' + os.linesep
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
    path = result_path(tmp_path)
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
    if corruption in ('utf8', 'oversized'):
        original = path.read_bytes()
        for reuse_only in (True, False):
            with pytest.raises(ValueError if os.name == "nt" else RuntimeError):
                execute(tmp_path, [item], 'unreadable', reuse_only=reuse_only)
            assert path.read_bytes() == original
        declaration = tmp_path / 'jobs.json'
        declaration.write_text(json.dumps([item]))
        cli = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'verify_runner.py'), '--root', str(tmp_path),
                              '--jobs', str(declaration), '--results', str(path), '--wbs-id', 'task',
                              '--run-id', 'run', '--generation', 'unreadable', '--reuse-only'],
                             capture_output=True, text=True, timeout=15)
        assert cli.returncode == 2 and 'Traceback' not in cli.stderr
        assert path.read_bytes() == original
        return
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
    payload = json.loads((result_path(tmp_path)).read_text())
    payload['successful'][0]['output_log'] = []
    (result_path(tmp_path)).write_text(json.dumps(payload))
    corrupted = execute(tmp_path, [item], 'malformed-log', reuse_only=True)
    assert corrupted['status'] == 'FAIL' and corrupted['successful'][0]['output_log'] == {'available': False}
    (tmp_path / 'check.py').write_text(original)
    reused = execute(tmp_path, [item], 'restored', reuse_only=True)['commands'][0]
    assert reused['reused'] and reused['output'] == 'bounded summary' + os.linesep
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
            '--jobs', str(declaration), '--results', str(result_path(tmp_path)),
            '--wbs-id', 'task', '--run-id', 'run', '--generation', 'interrupted', '--timeout', '8']
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 10
        while not (tmp_path / 'ready').exists():
            assert proc.poll() is None and time.monotonic() < deadline
            time.sleep(.01)
        pending = json.loads((result_path(tmp_path)).read_text())
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
    assert Path(evidence['output_log']['path']).read_text() == ''


def test_malformed_unicode_log_reference_does_not_repeat_valid_command(tmp_path):
    item = job(tmp_path, 'check', "print('valid summary')\n")
    assert execute(tmp_path, [item])['status'] == 'PASS'
    path = result_path(tmp_path)
    payload = json.loads(path.read_text())
    payload['successful'][0]['output_log']['path'] = '\ud800'
    path.write_text(json.dumps(payload))
    record = execute(tmp_path, [item], 'malformed-unicode', reuse_only=True)['commands'][0]
    assert record['status'] == 'PASS' and record['reused']
    assert record['output'] == 'valid summary' + os.linesep and record['output_log'] == {'available': False}


def test_retained_failure_survives_success_reuse_and_restart(tmp_path, monkeypatch):
    item = job(tmp_path, 'check', "import os,sys\nprint('early-'+os.environ['CHECK_EXIT'])\nprint('x'*9000)\nsys.exit(int(os.environ['CHECK_EXIT']))\n")
    item['environment'] = ['CHECK_EXIT']
    saved = []
    for code in ('7', '8'):
        monkeypatch.setenv('CHECK_EXIT', code)
        result = execute(tmp_path, [item], 'same-generation')
        assert result['status'] == 'FAIL'
        row = result['commands'][0]
        path = Path(row['output_log']['path'])
        saved.append((path, path.read_bytes()))
    assert len(result['failures']) == 2
    assert len({r['attempt_id'] for r in result['failures']}) == 2
    monkeypatch.setenv('CHECK_EXIT', '0')
    passed = execute(tmp_path, [item], 'success')
    assert passed['status'] == 'PASS' and len(passed['failures']) == 2
    assert execute(tmp_path, [item], 'reuse', reuse_only=True)['commands'][0]['reused']
    (tmp_path / 'jobs.json').write_text(json.dumps([item]))
    proc = subprocess.run([sys.executable, '-B', str(SCRIPTS / 'verify_runner.py'), '--root', str(tmp_path),
                           '--jobs', str(tmp_path / 'jobs.json'), '--results', str(result_path(tmp_path)),
                           '--wbs-id', 'task', '--run-id', 'run', '--generation', 'restart', '--reuse-only'],
                          capture_output=True, text=True, timeout=15)
    assert proc.returncode == 0, proc.stderr
    restored = json.loads(proc.stdout)
    assert restored['commands'][0]['reused'] and len(restored['failures']) == 2
    assert [r['returncode'] for r in restored['failures']] == [7, 8]
    for path, data in saved:
        assert path.read_bytes() == data and data.startswith(b'early-')
    assert all(r['output_log']['available'] for r in restored['failures'])


@pytest.mark.parametrize('limit', ['count', 'bytes', 'metadata'])
def test_retained_failure_budget_refuses_before_new_execution(tmp_path, monkeypatch, limit):
    item = job(tmp_path, 'check', "import os,sys\nfrom pathlib import Path\nwith Path('executed').open('a') as f:f.write(os.environ['CHECK_EXIT'])\nprint('original failure')\nsys.exit(int(os.environ['CHECK_EXIT']))\n")
    item['environment'] = ['CHECK_EXIT']
    monkeypatch.setenv('CHECK_EXIT', '7')
    first = execute(tmp_path, [item])
    log = Path(first['failures'][0]['output_log']['path'])
    data = log.read_bytes()
    name, value = {'count': ('MAX_FAILURES', 1),
                   'bytes': ('MAX_FAILURE_LOG_BYTES', 1048576 + len(data) - 1),
                   'metadata': ('MAX_RESULT_BYTES', result_path(tmp_path).stat().st_size + 200)}[limit]
    if os.name == "nt":
        marker = native_owner_fault(monkeypatch, tmp_path,
                                    f"{name} = {value}\nPath(_fixture_fault_marker).write_text('budget applied in child')\n")
    else:
        monkeypatch.setattr(runner, name, value)
    monkeypatch.setenv('CHECK_EXIT', '8')
    refused = execute(tmp_path, [item], 'refused')
    if os.name == "nt":
        assert marker.read_text() == 'budget applied in child'
    assert refused['status'] == 'FAIL' and 'budget' in refused['commands'][0]['error']
    assert refused['commands'][0]['returncode'] is None
    assert (tmp_path / 'executed').read_text() == '7'
    assert len(refused['failures']) == 1 and refused['failures'][0]['returncode'] == 7
    assert log.read_bytes() == data


def test_retained_failure_keeps_retired_jobs_and_previous_runs(tmp_path):
    failed = job(tmp_path, 'old', "print('old failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [failed])
    reference = first['failures'][0]['output_log']
    current = job(tmp_path, 'new', "print('new success')\n")
    changed = execute(tmp_path, [current], 'other-job', run_id='new-run')
    assert changed['status'] == 'PASS' and not changed['commands'][0]['reused']
    assert len(changed['failures']) == 1
    assert changed['failures'][0]['job_id'] == 'old' and changed['failures'][0]['run_id'] == 'run'
    assert Path(reference['path']).read_text() == 'old failure\n'


def test_retained_failure_imports_existing_version_one_fixed_slot(tmp_path):
    item = job(tmp_path, 'check', "print('original failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    stored = json.loads((result_path(tmp_path)).read_text())
    row = stored['commands'][0]
    fixed = result_path(tmp_path).with_name('results.json.logs') / (runner._digest('check') + '-attempt.log')
    Path(row['output_log']['path']).rename(fixed)
    row['output_log']['path'] = str(fixed)
    row.pop('attempt_id')
    stored.pop('failures')
    (result_path(tmp_path)).write_text(json.dumps(stored))
    (tmp_path / 'check.py').write_text("print('new success')\n")
    second = execute(tmp_path, [item], 'new')
    assert second['status'] == 'PASS' and len(second['failures']) == 1
    assert second['failures'][0]['returncode'] == 7
    assert second['failures'][0]['output_log']['available']
    assert fixed.read_text() == 'original failure\n'


def test_retained_failure_oversized_store_is_not_overwritten(tmp_path, monkeypatch):
    item = job(tmp_path, 'check', "print('authentic failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    result_file = result_path(tmp_path)
    original = result_file.read_bytes()
    log = Path(first['failures'][0]['output_log']['path'])
    if os.name == "nt":
        with result_file.open('r+b') as stream:
            stream.truncate(runner.MAX_RESULT_BYTES + 1)
        with result_file.open('rb') as stream:
            digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        (tmp_path / 'check.py').write_text("from pathlib import Path\nPath('unexpected').touch()\n")
        with pytest.raises((RuntimeError, ValueError), match='byte limit|size|large|budget'):
            execute(tmp_path, [item], 'unreadable')
        assert result_file.stat().st_size == runner.MAX_RESULT_BYTES + 1
        with result_file.open('rb') as stream:
            assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest
        assert not (tmp_path / 'unexpected').exists()
        assert log.read_text() == 'authentic failure\n'
        return
    monkeypatch.setattr(runner, 'MAX_RESULT_BYTES', len(original) - 1)
    with pytest.raises((RuntimeError, ValueError), match='(?:size|large|budget)'):
        execute(tmp_path, [item], 'unreadable')
    assert result_file.read_bytes() == original
    assert log.read_text() == 'authentic failure\n'


def test_retired_task_result_store_is_not_reused_or_overwritten(tmp_path):
    item = job(tmp_path, 'check', "from pathlib import Path\nPath('executed').touch()\n")
    path = result_path(tmp_path)
    legacy = dict(schema=runner.SCHEMA, version=1, task_id='TASK__old', run_id='same-run',
                  failures=[dict(status='FAIL', returncode=7, output='authentic old failure')])
    path.write_text(json.dumps(legacy))
    original = path.read_bytes()
    with pytest.raises(ValueError, match='retired verification result schema'):
        execute(tmp_path, [item])
    assert path.read_bytes() == original
    assert not (tmp_path / 'executed').exists()


def test_retained_failure_reserves_task_and_run_provenance_before_execution(tmp_path):
    item = job(tmp_path, 'check', "from pathlib import Path\nwith Path('executed').open('a') as f:f.write('x')\nprint('authentic failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    path = Path(first['failures'][0]['output_log']['path'])
    original = path.read_bytes()
    refused = execute(tmp_path, [item], 'large-provenance', wbs_id='t' * (9 * 1024 * 1024))
    assert refused['status'] == 'FAIL' and refused['commands'][0]['returncode'] is None
    assert 'budget' in refused['commands'][0]['error']
    assert (tmp_path / 'executed').read_text() == 'x'
    assert len(refused['failures']) == 1 and refused['failures'][0]['returncode'] == 7
    assert path.read_bytes() == original


@pytest.mark.skipif(os.name != 'posix' or os.geteuid() == 0,
                    reason='requires non-root POSIX file read permissions')
def test_unreadable_result_preserves_failure_without_execution(tmp_path):
    item = job(tmp_path, 'check', "print('authentic failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    result_file = result_path(tmp_path)
    original = result_file.read_bytes()
    log = Path(first['failures'][0]['output_log']['path'])
    (tmp_path / 'check.py').write_text("from pathlib import Path\nPath('executed').touch()\n")
    result_file.chmod(0)
    try:
        with pytest.raises((PermissionError, RuntimeError)):
            execute(tmp_path, [item], 'unreadable')
    finally:
        result_file.chmod(0o600)
    assert result_file.read_bytes() == original
    assert log.read_text() == 'authentic failure\n'
    assert not (tmp_path / 'executed').exists()


@pytest.mark.parametrize('pending,returncode,expected', [(False, 7, 1), (True, 7, 0), (False, None, 0)])
def test_retained_failure_imports_legacy_last_execution(tmp_path, pending, returncode, expected):
    item = job(tmp_path, 'check', "print('legacy failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    result_file = result_path(tmp_path)
    stored = json.loads(result_file.read_text())
    record = stored['commands'][0]
    fixed = result_path(tmp_path).with_name('results.json.logs') / (runner._digest('check') + '-attempt.log')
    Path(record['output_log']['path']).rename(fixed)
    record['output_log']['path'] = str(fixed)
    diagnostic = {k: record[k] for k in ('execution_generation', 'state', 'returncode', 'error', 'output', 'output_log')}
    diagnostic.update(returncode=returncode, execution_pending=pending)
    record.update(returncode=None, state='executor_error', last_execution=diagnostic)
    record.pop('attempt_id')
    record.pop('output_log')
    stored.pop('failures')
    result_file.write_text(json.dumps(stored))
    (tmp_path / 'check.py').write_text("print('new success')\n")
    second = execute(tmp_path, [item], 'new')
    assert second['status'] == 'PASS' and len(second['failures']) == expected
    if expected:
        row = second['failures'][0]
        assert row['returncode'] == 7 and row['wbs_id'] == 'task' and row['run_id'] == 'run'
        assert row['execution_generation'] == 'one'
        assert row['output_log']['available'] and fixed.read_text() == 'legacy failure\n'


def test_reuse_only_misses_do_not_duplicate_modern_failure_history(tmp_path):
    item = job(tmp_path, 'check', "print('one authentic failure')\nraise SystemExit(7)\n")
    first = execute(tmp_path, [item])
    identity = first['failures'][0]['attempt_id']
    reference = first['failures'][0]['output_log']
    original = Path(reference['path']).read_bytes()
    for index in range(4):
        with (tmp_path / 'check.py').open('a') as stream:
            stream.write(f'\n# changed input {index}\n')
        result = execute(tmp_path, [item], f'reuse-{index}', reuse_only=True)
        assert result['status'] == 'FAIL' and result['commands'][0]['returncode'] is None
        assert len(result['failures']) == 1
        assert result['failures'][0]['attempt_id'] == identity
        assert Path(reference['path']).read_bytes() == original


def test_producer_manifest_budget_refuses_before_execution(tmp_path, monkeypatch):
    outputs = ['/'.join(['d' * 200] * 8 + [f'{index:02d}' + 'f' * 200]) for index in range(32)]
    code = "from pathlib import Path\nPath('executed').touch()\nfor name in " + repr(outputs) + ":\n p=Path(name);p.parent.mkdir(parents=True,exist_ok=True);p.write_text('built')\n"
    item = job(tmp_path, 'prepare', code, kind='producer', outputs=outputs)
    if os.name == "nt":
        marker = native_owner_fault(monkeypatch, tmp_path,
                                    "MAX_RESULT_BYTES = 230000\nPath(_fixture_fault_marker).write_text('manifest budget applied in child')\n")
    else:
        monkeypatch.setattr(runner, 'MAX_RESULT_BYTES', 230000)
    result = execute(tmp_path, [item])
    if os.name == "nt":
        assert marker.read_text() == 'manifest budget applied in child'
    assert result['status'] == 'FAIL' and result['commands'][0]['returncode'] is None
    assert 'budget' in result['commands'][0]['error']
    assert not (tmp_path / 'executed').exists()
    assert all(not (tmp_path / name).exists() for name in outputs)
    assert not result['commands'][0].get('execution_pending')
