"""Intent-based, real-Git checks for the durable batch coordinator CLI."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "plugin/scripts/batch_state.py"
RUN = "0198c349-5800-7000-8000-000000000001"


def _git(*args, **kwargs):
    from test_batch_finish import _git as git
    return git(*args, **kwargs)


def _head(repo):
    from test_batch_finish import _head as head
    return head(repo)


def _main(*args, **kwargs):
    from test_batch_finish import _main as main
    return main(*args, **kwargs)


def _write(path, text):
    from test_batch_finish import _write as write
    return write(path, text)


def pytest_generate_tests(metafunc):
    cases = {
        "s1_resume_worker": ["worker-a", "replacement-worker"],
        "capacity": [(None, None, 3), ("2", None, 2), ("99", None, 8),
                     (None, "2", 2), (None, "true", 3), (None, "'2'", 3),
                     (None, "0", 3), ("1", "4", 1)],
        "bad_cap": ["0", "-1", "true", "2.5"],
        "bad_requests": [[], [{"slug": "../bad", "request": "x", "scopes": ["x"]}],
                         [{"slug": "a", "request": "x", "scopes": ["x"], "depends_on": ["missing"]}],
                         [{"slug": "a", "request": "x", "scopes": ["x"], "depends_on": ["a"]}]],
        "unsafe": ["symlink", "hardlink", "directory", "schema", "duplicate"],
        "mismatch": ["worker", "task_id", "worktree", "branch", "run_id", "commit"],
        "stage": ["rebase", "integrated", "harvested"],
        "archive_corrupt": [False, True],
        "bad_marker": ["wrong", "symlink", "hardlink", "directory", "ignored", "tracked"],
        "sync_failure": [False, True],
        "literal_scope": ["literal[ab].txt", "literal*.txt", "literal,part.txt"],
    }
    for name, values in cases.items():
        if name in metafunc.fixturenames:
            metafunc.parametrize(name, values)


class Pool:
    def __init__(self, tmp_path, monkeypatch, batch="pool"):
        self.repo = _main(tmp_path, monkeypatch)
        self.tmp = tmp_path
        self.batch = batch
        ignore = self.repo / ".gitignore"
        ignore.write_text(ignore.read_text() + "doc/harness/runtime/\n")
        _git("add", ".gitignore", cwd=self.repo)
        _git("commit", "-qm", "ignore operational state", cwd=self.repo)

    @property
    def state(self):
        return self.repo / "doc/harness/runtime/batches" / (self.batch + ".json")

    def argv(self, *args):
        return [sys.executable, str(SCRIPT), "--repo", str(self.repo),
                "--batch-id", self.batch, *map(str, args)]

    def cli(self, *args, ok=True):
        result = subprocess.run(self.argv(*args), capture_output=True, text=True)
        if ok:
            assert result.returncode == 0, (args, result.stdout, result.stderr)
        else:
            assert result.returncode != 0, (args, result.stdout)
        if result.returncode == 2:
            return {}
        body = json.loads(result.stdout)
        assert isinstance(body, dict) and "status" in body, body
        return body

    def init(self, requests=None, cap=None, ok=True, owner=None):
        path = self.tmp / (self.batch + "-requests.json")
        path.write_text(json.dumps(requests if requests is not None else [request("a")]))
        return self.cli("init", "--requests-file", path,
                        *([] if cap is None else ["--max-leads", cap]),
                        *([] if owner is None else ["--owner-task-id", owner]), ok=ok)

    def claim(self):
        return self.cli("claim")["claims"]

    def bind(self, slug="a", worker="worker-a"):
        worktree = self.repo / ".claude/worktrees" / slug
        branch = "worktree-" + slug
        _git("worktree", "add", "-qb", branch, str(worktree), cwd=self.repo)
        self.cli("bind", "--slug", slug, "--worker-id", worker,
                 "--worktree", worktree, "--branch", branch)
        return worktree, branch

    def result(self, slug, actual_worktree, actual_branch, verdict="blocked", worker="worker-a", **extra):
        body = {"verdict": verdict, "task_id": "TASK__" + slug,
                "worktree": str(actual_worktree), "branch": actual_branch,
                "commit": _head(actual_worktree) if verdict == "closed" else None, **extra}
        path = self.tmp / (slug + "-result.json")
        path.write_text(json.dumps(body))
        return ["result", "--slug", slug, "--worker-id", worker, "--result-file", path]


def request(slug, scope=None, dependencies=()):
    return {"slug": slug, "request": "Implement " + slug,
            "scopes": [scope or slug + ".txt"], "depends_on": list(dependencies)}


def task(worktree, slug="a", closed=False):
    directory = worktree / "doc/harness/tasks" / ("TASK__" + slug)
    control = {"run_id": RUN, "execution_mode": "standard",
               "required_lenses": ["review-code", "qa-cli"],
               "close_receipt_fingerprint": None}
    if closed:
        # Same isolated fixture mechanism as test_prewrite_gate_dormant:
        # task_control_status validates the exact missing receipt-stream bytes.
        control["close_receipt_fingerprint"] = "sha256:" + hashlib.sha256(
            b"RECEIPTS.jsonl\0<missing>\0").hexdigest()
    _write(directory / "TASK.json", json.dumps(control))
    _write(directory / "PLAN.md", "# Fixture task\n")
    return directory


def complete(pool, slug="a", worker="worker-a"):
    worktree, branch = pool.bind(slug, worker)
    _write(worktree / (slug + ".txt"), slug + "\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", slug, "--trailer", "Harness-Task: TASK__" + slug, cwd=worktree)
    directory = task(worktree, slug, closed=True)
    pool.cli(*pool.result(slug, worktree, branch, "closed", worker, run_id=RUN))
    return worktree, branch, directory


def test_init_idempotence_and_conflicting_intake(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    first = pool.state.read_bytes()
    data = json.loads(first)
    assert data["schema_version"] == 1
    assert data["requests"][0]["task_id"] == "TASK__a"
    pool.init()
    assert pool.state.read_bytes() == first
    pool.init([request("different")], ok=False)
    assert pool.state.read_bytes() == first


def test_cap_precedence_and_types(tmp_path, monkeypatch, capacity):
    explicit, manifest, expected = capacity
    pool = Pool(tmp_path, monkeypatch)
    if manifest is not None:
        _write(pool.repo / "doc/harness/manifest.yaml", "version: 7\nbatch:\n  max_leads: " + manifest + "\n")
        _git("add", "-A", cwd=pool.repo)
        _git("commit", "-qm", "capacity", cwd=pool.repo)
    pool.init([request("r" + str(i)) for i in range(10)], cap=explicit)
    assert len(pool.claim()) == expected
    assert pool.claim() == []


def test_invalid_explicit_cap(tmp_path, monkeypatch, bad_cap):
    pool = Pool(tmp_path, monkeypatch)
    pool.init(cap=bad_cap, ok=False)
    assert not pool.state.exists()


def test_invalid_intake(tmp_path, monkeypatch, bad_requests):
    pool = Pool(tmp_path, monkeypatch)
    pool.init(bad_requests, ok=False)
    assert not pool.state.exists()


def test_duplicate_cycle_and_oversized_intake(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    for requests in ([request("a"), request("a")],
                     [request("a", dependencies=["b"]), request("b", dependencies=["a"])],
                     [request("r" + str(i)) for i in range(101)]):
        pool.init(requests, ok=False)
        assert not pool.state.exists()


def test_unsafe_or_corrupt_state_refuses_mutation(tmp_path, monkeypatch, unsafe):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    before = pool.state.read_bytes()
    other = tmp_path / "state-copy"
    other.write_bytes(before)
    if unsafe in ("symlink", "hardlink", "directory"):
        pool.state.unlink()
        if unsafe == "symlink":
            pool.state.symlink_to(other)
        elif unsafe == "hardlink":
            os.link(other, pool.state)
        else:
            pool.state.mkdir()
    else:
        data = json.loads(before)
        if unsafe == "schema":
            data["schema_version"] = 999
        else:
            data["requests"].append(data["requests"][0].copy())
        pool.state.write_text(json.dumps(data))
    pool.cli("claim", ok=False)
    assert other.read_bytes() == before


def test_reservations_dependencies_overlap_and_release(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("a", "src"), request("overlap", "src/child"),
               request("dependent", dependencies=["a"]), request("free")], cap="2")
    claims = pool.claim()
    assert {row["slug"] for row in claims} == {"a", "free"}
    assert all(row["spawn_head"] == _head(pool.repo) and "off_limits" in row for row in claims)
    assert pool.claim() == []
    pool.cli("release", "--slug", "free", "--worker-stopped", ok=False)
    pool.cli("release", "--slug", "free", "--worker-stopped", "--no-external-work")
    assert [row["slug"] for row in pool.claim()] == ["free"]
    pool.cli("close", ok=False)


def test_concurrent_claims_never_overbook(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("r" + str(i)) for i in range(12)], cap="3")
    jobs = [subprocess.Popen(pool.argv("claim"), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True) for _ in range(6)]
    claims = []
    for job in jobs:
        out, err = job.communicate(timeout=30)
        assert job.returncode in (0, 3), (out, err)
        body = json.loads(out)
        if job.returncode == 0:
            claims.extend(body["claims"])
    assert len(claims) == len({row["slug"] for row in claims}) == 3


def test_other_active_pool_refuses_dispatch(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    pool.batch = "other"
    # Intake may itself refuse; if accepted it must not dispatch.
    path = tmp_path / "other.json"
    path.write_text(json.dumps([request("b")]))
    result = subprocess.run(pool.argv("init", "--requests-file", path), capture_output=True, text=True)
    assert result.returncode in (0, 3), result.stderr
    if result.returncode == 0:
        pool.cli("claim", ok=False)


def test_bind_checks_starting_commit_and_registration(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    pool.cli("bind", "--slug", "a", "--worker-id", "w", "--worktree", pool.repo,
             "--branch", "main", ok=False)
    worktree = pool.repo / ".claude/worktrees/a"
    _git("worktree", "add", "-qb", "worktree-a", str(worktree), cwd=pool.repo)
    _write(worktree / "a.txt", "premature edit\n")
    _git("add", "-A", cwd=worktree)
    _git("commit", "-qm", "premature", cwd=worktree)
    pool.cli("bind", "--slug", "a", "--worker-id", "w", "--worktree", worktree,
             "--branch", "worktree-a", ok=False)


def test_result_identity_is_bound_to_worker_and_task(tmp_path, monkeypatch, mismatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree, closed=True)
    before = (directory / "TASK.json").read_bytes()
    args = {"task_id": "TASK__foreign", "worktree": str(pool.repo), "branch": "main",
            "run_id": "0198c349-5800-7000-8000-000000000002", "commit": "HEAD"}
    kwargs = {mismatch: args[mismatch]} if mismatch != "worker" else {}
    pool.cli(*pool.result("a", worktree, branch, "closed",
                         "other-worker" if mismatch == "worker" else "worker-a", **kwargs), ok=False)
    assert (directory / "TASK.json").read_bytes() == before
    assert worktree.exists()


def test_closed_label_without_valid_fingerprint_refuses(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    pool.cli(*pool.result("a", worktree, branch, "closed"), ok=False)
    task(worktree, closed=True)
    _write(directory / "RECEIPTS.jsonl", "{}\n")
    pool.cli(*pool.result("a", worktree, branch, "closed"), ok=False)


def test_blocked_resume_preserves_exact_worktree_task_and_run(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    _write(directory / "BLOCKED.md", "Waiting for dependency\n")
    _write(worktree / "a.txt", "uncommitted work\n")
    before = {p.name: p.read_bytes() for p in directory.iterdir()}
    pool.cli(*pool.result("a", worktree, branch))
    state = pool.state.read_bytes()
    pool.cli("status")
    assert pool.state.read_bytes() == state
    pool.cli("resume", "--slug", "a", ok=False)
    handoff = pool.cli("resume", "--slug", "a", "--worker-stopped")
    assert str(worktree) in json.dumps(handoff)
    pool.cli("bind", "--slug", "a", "--worker-id", "replacement",
             "--worktree", worktree, "--branch", branch)
    pool.cli(*pool.result("a", worktree, branch), ok=False)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == before
    assert (worktree / "a.txt").read_text() == "uncommitted work\n"


def test_abandon_retains_source_evidence_and_scope_across_batches(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    _write(worktree / "a.txt", "retained work\n")
    original = (directory / "TASK.json").read_bytes()
    pool.cli(*pool.result("a", worktree, branch))
    pool.cli("abandon", "--slug", "a", "--reason", "user choice", ok=False)
    pool.cli("abandon", "--slug", "a", "--reason", "user choice", "--worker-stopped")
    assert worktree.exists() and _git("branch", "--list", branch, cwd=pool.repo)
    assert (directory / "TASK.json").read_bytes() == original
    assert (worktree / "a.txt").read_text() == "retained work\n"
    report = json.dumps(pool.cli("status"))
    assert "abandon" in report and str(worktree) in report
    pool.cli("close")
    pool.batch = "next"
    pool.init([request("collision", "a.txt"), request("free")])
    assert [row["slug"] for row in pool.claim()] == ["free"]


def test_finished_dependency_refills_from_current_head_and_closes(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("a"), request("b", dependencies=["a"])], cap="1")
    assert [row["slug"] for row in pool.claim()] == ["a"]
    worktree, branch, _ = complete(pool)
    assert pool.claim() == []
    pool.cli("finish", "--slug", "a")
    assert not worktree.exists()
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    next_claim = pool.claim()
    assert next_claim[0]["slug"] == "b"
    assert next_claim[0]["spawn_head"] == _head(pool.repo)
    complete(pool, "b", "worker-b")
    pool.cli("finish", "--slug", "b")
    pool.cli("close")
    assert (pool.repo / "a.txt").exists() and (pool.repo / "b.txt").exists()
    assert _git("rev-list", "--merges", "HEAD", cwd=pool.repo) == ""


def test_close_proof_is_rechecked_before_finish(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    old_head = _head(pool.repo)
    _write(directory / "RECEIPTS.jsonl", "{}\n")
    pool.cli("finish", "--slug", "a", ok=False)
    assert _head(pool.repo) == old_head and worktree.exists()
    assert _git("branch", "--list", branch, cwd=pool.repo)


def test_checkpoint_interruption_preserves_then_recovers(tmp_path, monkeypatch, capsys, stage):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, _, _ = complete(pool)
    spec = importlib.util.spec_from_file_location("batch_state_intent_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    import batch_finish
    real_finish = batch_finish.finish

    def interrupt(repo, args, checkpoint=None, **kwargs):
        def callback(current_stage, result):
            checkpoint(current_stage, result)
            if current_stage == stage:
                raise RuntimeError("simulated checkpoint interruption")
        return real_finish(repo, args, checkpoint=callback, **kwargs)

    monkeypatch.setattr(batch_finish, "finish", interrupt)
    code = module.main(pool.argv("finish", "--slug", "a")[2:])
    capsys.readouterr()
    assert code != 0 and worktree.exists()
    monkeypatch.setattr(batch_finish, "finish", real_finish)
    pool.cli("recover", "--slug", "a", ok=False)
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    # Recovery can finish immediately or establish a safe retry checkpoint.
    if worktree.exists():
        pool.cli("finish", "--slug", "a", "--resume")
    assert not worktree.exists()
    pool.cli("close")


def test_process_death_after_cleanup_recovers_from_exact_archive(tmp_path, monkeypatch, archive_corrupt):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    original_control = (directory / "TASK.json").read_bytes()
    # Advance the destination first so the durable tip must be post-rebase.
    returned_tip = _head(worktree)
    _write(pool.repo / "main-only.txt", "destination advanced\n")
    _git("add", "main-only.txt", cwd=pool.repo)
    _git("commit", "-qm", "advance destination", cwd=pool.repo)
    program = """
import os, sys
sys.path.insert(0, sys.argv.pop(1))
import batch_state, batch_finish
original = batch_finish._cleanup
def cleanup_then_die(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(91)
batch_finish._cleanup = cleanup_then_die
batch_state.main(sys.argv[1:])
"""
    result = subprocess.run([sys.executable, "-c", program, str(SCRIPT.parent),
                             *pool.argv("finish", "--slug", "a")[2:]],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, (result.stdout, result.stderr)
    persisted = pool.state.read_bytes()
    pool.cli("status")
    assert pool.state.read_bytes() == persisted
    pool.cli("claim", ok=False)
    integrated_tip = _head(pool.repo)
    assert integrated_tip != returned_tip
    assert not worktree.exists()
    archive = pool.repo / "doc/harness/archive/batch/TASK__a"
    assert (archive / "TASK.json").read_bytes() == original_control
    if archive_corrupt:
        (archive / "PLAN.md").write_text("tampered after harvest\n")
        pool.cli("recover", "--slug", "a", "--worker-stopped", ok=False)
        pool.cli("close", ok=False)
        assert _head(pool.repo) == integrated_tip
        assert (archive / "TASK.json").read_bytes() == original_control
        return
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    assert _head(pool.repo) == integrated_tip
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    assert (archive / "TASK.json").read_bytes() == original_control
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    pool.cli("close")


def test_task_identity_collision_refuses_intake(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    task(pool.repo)
    pool.init(ok=False)
    assert not pool.state.exists()


def test_resume_refuses_changed_task_generation_and_release_keeps_bound_work(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    pool.cli("release", "--slug", "a", "--worker-stopped", "--no-external-work", ok=False)
    pool.cli(*pool.result("a", worktree, branch, run_id=RUN))
    control = json.loads((directory / "TASK.json").read_text())
    control["run_id"] = "0198c349-5800-7000-8000-000000000002"
    (directory / "TASK.json").write_text(json.dumps(control))
    pool.cli("resume", "--slug", "a", "--worker-stopped", ok=False)
    assert worktree.exists()


def test_writable_metadata_parent_refuses_mutation(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    metadata = pool.repo / "doc/harness"
    original_mode = metadata.stat().st_mode & 0o777
    metadata.chmod(0o777)
    try:
        pool.init(ok=False)
        assert not pool.state.exists()
    finally:
        metadata.chmod(original_mode)


def test_stopped_running_worker_resume_pins_existing_run_without_fabricated_result(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    _write(directory / "RECEIPTS.jsonl", "")
    _write(worktree / "a.txt", "interrupted work\n")
    evidence = {p.name: p.read_bytes() for p in directory.iterdir()}
    pool.cli("resume", "--slug", "a", ok=False)
    handoff = pool.cli("resume", "--slug", "a", "--worker-stopped")
    assert str(worktree) in json.dumps(handoff)
    assert RUN in json.dumps(handoff)
    pool.cli("bind", "--slug", "a", "--worker-id", "replacement",
             "--worktree", worktree, "--branch", branch)
    assert {p.name: p.read_bytes() for p in directory.iterdir()} == evidence
    assert (worktree / "a.txt").read_text() == "interrupted work\n"
    pool.cli(*pool.result("a", worktree, branch), ok=False)
    pool.cli(*pool.result("a", worktree, branch, worker="replacement", run_id=RUN))


def test_stopped_pretask_running_resume_rechecks_absence_before_bind(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    pool.cli("resume", "--slug", "a", "--worker-stopped")
    directory = task(worktree)
    before = pool.state.read_bytes()
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli("bind", "--slug", "a", "--worker-id", "replacement",
             "--worktree", worktree, "--branch", branch, ok=False)
    assert pool.state.read_bytes() == before
    assert (directory / "TASK.json").read_bytes() == evidence


def test_open_main_task_blocks_resume_without_mutation(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    pool.cli(*pool.result("a", worktree, branch, run_id=RUN))
    main_task = task(pool.repo, "integration")
    before = pool.state.read_bytes()
    lead_evidence = (directory / "TASK.json").read_bytes()
    main_evidence = (main_task / "TASK.json").read_bytes()
    pool.cli("resume", "--slug", "a", "--worker-stopped", ok=False)
    assert pool.state.read_bytes() == before
    assert (directory / "TASK.json").read_bytes() == lead_evidence
    assert (main_task / "TASK.json").read_bytes() == main_evidence


def test_open_main_task_appearing_after_claim_blocks_bind(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree = pool.repo / ".claude/worktrees/a"
    branch = "worktree-a"
    _git("worktree", "add", "-qb", branch, str(worktree), cwd=pool.repo)
    directory = task(pool.repo, "integration")
    before = pool.state.read_bytes()
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch, ok=False)
    assert pool.state.read_bytes() == before
    assert (directory / "TASK.json").read_bytes() == evidence


def test_open_main_task_appearing_after_resume_blocks_bind(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    task(worktree)
    pool.cli(*pool.result("a", worktree, branch, run_id=RUN))
    pool.cli("resume", "--slug", "a", "--worker-stopped")
    directory = task(pool.repo, "integration")
    before = pool.state.read_bytes()
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli("bind", "--slug", "a", "--worker-id", "replacement",
             "--worktree", worktree, "--branch", branch, ok=False)
    assert pool.state.read_bytes() == before
    assert (directory / "TASK.json").read_bytes() == evidence


def test_pretask_blocked_result_refuses_unexpected_task_at_resume(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    pool.cli(*pool.result("a", worktree, branch))
    directory = task(worktree)
    before = pool.state.read_bytes()
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli("resume", "--slug", "a", "--worker-stopped", ok=False)
    assert pool.state.read_bytes() == before
    assert (directory / "TASK.json").read_bytes() == evidence


def test_failed_resume_bootstrap_keeps_bound_reservation_for_stopped_retry(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    pool.cli(*pool.result("a", worktree, branch, run_id=RUN))
    pool.cli("resume", "--slug", "a", "--worker-stopped")
    # Replacement bootstrap dies before bind. Its retained reservation must
    # stay attached to the original source and cannot use unused-release.
    before = pool.state.read_bytes()
    evidence = (directory / "TASK.json").read_bytes()
    report = json.dumps(pool.cli("status"))
    assert pool.state.read_bytes() == before
    assert str(worktree) in report and "resume" in report
    assert "release" not in report
    pool.cli("release", "--slug", "a", "--worker-stopped", "--no-external-work", ok=False)
    pool.cli("resume", "--slug", "a", ok=False)
    handoff = pool.cli("resume", "--slug", "a", "--worker-stopped")
    assert str(worktree) in json.dumps(handoff) and RUN in json.dumps(handoff)
    pool.cli("bind", "--slug", "a", "--worker-id", "retry-worker",
             "--worktree", worktree, "--branch", branch)
    assert (directory / "TASK.json").read_bytes() == evidence


def unbound_worktree(pool, slug="a"):
    worktree = pool.repo / ".claude/worktrees" / slug
    branch = "worktree-" + slug
    _git("worktree", "add", "-qb", branch, str(worktree), cwd=pool.repo)
    return worktree, branch


def load_state_module():
    spec = importlib.util.spec_from_file_location("batch_state_remediation_tests", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_bootstrap_marker_is_exact_untracked_retention_without_control_writes(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    claim = pool.claim()[0]
    worktree, branch = unbound_worktree(pool)
    before = pool.state.read_bytes()
    command = ("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch)
    assert pool.cli(*command)["status"] == "bootstrapped"
    marker = worktree / ".harness-batch-bootstrap"
    payload = {"batch_id": pool.batch, "slug": "a", "spawn_head": claim["spawn_head"]}
    assert marker.read_bytes() == (json.dumps(payload, sort_keys=True) + "\n").encode()
    pool.cli(*command)
    assert pool.state.read_bytes() == before
    assert not (worktree / "doc/harness/tasks").exists()
    assert _git("status", "--porcelain", cwd=worktree) == "?? .harness-batch-bootstrap"
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch)
    pool.cli(*pool.result("a", worktree, branch))
    assert marker.exists()


def test_bootstrap_and_bind_refuse_unsafe_marker(tmp_path, monkeypatch, bad_marker):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    claim = pool.claim()[0]
    worktree, branch = unbound_worktree(pool)
    marker = worktree / ".harness-batch-bootstrap"
    content = json.dumps({"batch_id": pool.batch, "slug": "a", "spawn_head": claim["spawn_head"]}, sort_keys=True) + "\n"
    other = tmp_path / "marker-original"
    other.write_text(content)
    if bad_marker == "symlink":
        marker.symlink_to(other)
    elif bad_marker == "hardlink":
        os.link(other, marker)
    elif bad_marker == "directory":
        marker.mkdir()
    else:
        marker.write_text("wrong\n" if bad_marker == "wrong" else content)
        if bad_marker == "ignored":
            exclude = pool.repo / ".git/info/exclude"
            exclude.write_text(exclude.read_text() + "\n.harness-batch-bootstrap\n")
        elif bad_marker == "tracked":
            _git("add", ".harness-batch-bootstrap", cwd=worktree)
    before = pool.state.read_bytes()
    pool.cli("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch, ok=False)
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch, ok=False)
    assert pool.state.read_bytes() == before
    assert other.read_text() == content


def test_bootstrap_marker_does_not_permit_other_dirt(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = unbound_worktree(pool)
    pool.cli("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch)
    _write(worktree / "unexpected.txt", "unexpected\n")
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch, ok=False)
    pool.cli("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch, ok=False)
    assert (worktree / "unexpected.txt").read_text() == "unexpected\n"


def test_zero_source_closed_task_retains_marker_until_durable_cleanup(tmp_path, monkeypatch, capsys, sync_failure):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = unbound_worktree(pool)
    pool.cli("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch)
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch)
    directory = task(worktree, closed=True)
    _write(worktree / "doc/harness/learnings.jsonl", json.dumps({"key": "retained", "insight": "durable"}) + "\n")
    original_head = _head(pool.repo)
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli(*pool.result("a", worktree, branch, "closed", run_id=RUN))
    marker = worktree / ".harness-batch-bootstrap"
    assert marker.exists() and _head(worktree) == original_head
    module = load_state_module()
    import batch_finish
    original_fsync, original_cleanup = os.fsync, batch_finish._cleanup
    synced = set()
    cleaned = []
    archive = pool.repo / "doc/harness/archive/batch/TASK__a"

    def record_fsync(fd):
        path = Path(os.readlink("/proc/self/fd/" + str(fd)))
        if sync_failure and path == archive / "TASK.json":
            raise OSError("injected archive synchronization failure")
        original_fsync(fd)
        synced.add(path)

    def inspect_before_cleanup(*args, **kwargs):
        required = {archive / "TASK.json", archive / "PLAN.md", archive,
                    archive.parent, archive.parent.parent, archive.parent.parent.parent,
                    pool.repo / "doc/harness/learnings.jsonl"}
        assert required <= synced, (required - synced, synced)
        assert marker.exists()
        cleaned.append(True)
        return original_cleanup(*args, **kwargs)

    monkeypatch.setattr(os, "fsync", record_fsync)
    monkeypatch.setattr(batch_finish, "_cleanup", inspect_before_cleanup)
    code = module.main(pool.argv("finish", "--slug", "a")[2:])
    output = capsys.readouterr().out
    assert _head(pool.repo) == original_head
    if sync_failure:
        assert code != 0, output
        assert not cleaned and worktree.exists() and marker.exists()
        assert (directory / "TASK.json").read_bytes() == evidence
    else:
        assert code == 0, output
        assert cleaned and not worktree.exists()
        assert (archive / "TASK.json").read_bytes() == evidence
        assert not _git("branch", "--list", branch, cwd=pool.repo)


def test_explicit_queued_descendant_abandonment_allows_truthful_close(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("a"), request("b", dependencies=["a"])])
    pool.claim()
    worktree, branch = pool.bind()
    directory = task(worktree)
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli(*pool.result("a", worktree, branch, run_id=RUN))
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "user retains work")
    pool.cli("close", ok=False)
    pool.cli("abandon", "--slug", "b", "--worker-stopped", "--reason", "user cancels dependent")
    pool.cli("close")
    records = {row["slug"]: row for row in json.loads(pool.state.read_text())["requests"]}
    assert records["a"]["disposition"] == "retained-work"
    assert records["b"]["disposition"] == "never-dispatched"
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence


def test_unintegrated_conflict_halts_preclaimed_bind_until_explicit_abandonment(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("a", "shared.txt"), request("b")], cap="2")
    pool.claim()
    worktree, branch = pool.bind()
    _write(worktree / "shared.txt", "worker conflict\n")
    _git("add", "shared.txt", cwd=worktree)
    _git("commit", "-qm", "worker conflict", cwd=worktree)
    directory = task(worktree, closed=True)
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli(*pool.result("a", worktree, branch, "closed", run_id=RUN))
    other, other_branch = unbound_worktree(pool, "b")
    _write(pool.repo / "shared.txt", "main conflict\n")
    _git("add", "shared.txt", cwd=pool.repo)
    _git("commit", "-qm", "main conflict", cwd=pool.repo)
    pool.cli("finish", "--slug", "a", ok=False)
    pool.cli("bind", "--slug", "b", "--worker-id", "worker-b",
             "--worktree", other, "--branch", other_branch, ok=False)
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "user retains conflict")
    pool.cli("bind", "--slug", "b", "--worker-id", "worker-b",
             "--worktree", other, "--branch", other_branch)
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence
    assert (worktree / "shared.txt").read_text() == "worker conflict\n"


def test_postintegration_checkpoint_failure_cannot_be_abandoned(tmp_path, monkeypatch, capsys):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, _, directory = complete(pool)
    evidence = (directory / "TASK.json").read_bytes()
    module = load_state_module()
    import batch_finish
    original = batch_finish.finish

    def interrupt(repo, args, checkpoint=None, **kwargs):
        def callback(stage, result):
            checkpoint(stage, result)
            if stage == "integrated":
                raise RuntimeError("interrupted after fast-forward")
        return original(repo, args, checkpoint=callback, **kwargs)

    monkeypatch.setattr(batch_finish, "finish", interrupt)
    assert module.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    capsys.readouterr()
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "unsafe disposition", ok=False)
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence
    pool.cli("claim", ok=False)


def test_unknown_integration_outcome_cannot_be_abandoned(tmp_path, monkeypatch, capsys):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, _, directory = complete(pool)
    evidence = (directory / "TASK.json").read_bytes()
    module = load_state_module()
    original_run = subprocess.run
    interrupted = False

    def lose_git_after_merge(command, *args, **kwargs):
        nonlocal interrupted
        if interrupted:
            raise OSError("integration reconciliation unavailable")
        result = original_run(command, *args, **kwargs)
        if "merge" in command and "--ff-only" in command:
            assert result.returncode == 0
            interrupted = True
            raise subprocess.TimeoutExpired(command, 1)
        return result

    monkeypatch.setattr(subprocess, "run", lose_git_after_merge)
    assert module.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    capsys.readouterr()
    monkeypatch.setattr(subprocess, "run", original_run)
    assert interrupted
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "unknown outcome", ok=False)
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence
    pool.cli("claim", ok=False)


def test_native_remove_refusal_restores_retention_marker_then_retry_succeeds(tmp_path, monkeypatch, capsys):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch = unbound_worktree(pool)
    pool.cli("bootstrap", "--slug", "a", "--worktree", worktree, "--branch", branch)
    marker = worktree / ".harness-batch-bootstrap"
    marker_bytes = marker.read_bytes()
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a",
             "--worktree", worktree, "--branch", branch)
    directory = task(worktree, closed=True)
    evidence = (directory / "TASK.json").read_bytes()
    _git("worktree", "lock", "--reason", "native agent retention", str(worktree), cwd=pool.repo)
    pool.cli(*pool.result("a", worktree, branch, "closed", run_id=RUN))
    module = load_state_module()
    original_run = subprocess.run
    attempted = []

    def refuse_native_remove(command, *args, **kwargs):
        if "worktree" in command and "remove" in command:
            assert not marker.exists()
            attempted.append(True)
            # A real native removal refusal caused by a lock appearing at
            # the final boundary, after managed cleanup unlinked its marker.
            locked = original_run(["git", "worktree", "lock", "--reason", "native removal race",
                                   str(worktree)], cwd=pool.repo, capture_output=True, text=True)
            assert locked.returncode == 0, locked.stderr
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", refuse_native_remove)
    assert module.main(pool.argv("finish", "--slug", "a")[2:]) != 0
    capsys.readouterr()
    monkeypatch.setattr(subprocess, "run", original_run)
    assert attempted and worktree.exists()
    assert marker.read_bytes() == marker_bytes
    assert (directory / "TASK.json").read_bytes() == evidence
    assert str(worktree) in _git("worktree", "list", "--porcelain", cwd=pool.repo)
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    if worktree.exists():
        pool.cli("finish", "--slug", "a", "--resume")
    assert not worktree.exists()
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    pool.cli("close")


def test_process_death_after_ff_before_checkpoint_requires_integration_cleanup(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    evidence = (directory / "TASK.json").read_bytes()
    returned_tip = _head(worktree)
    _write(pool.repo / "main-only.txt", "force a distinct rebased tip\n")
    _git("add", "main-only.txt", cwd=pool.repo)
    _git("commit", "-qm", "advance destination", cwd=pool.repo)
    program = """
import os, subprocess, sys
sys.path.insert(0, sys.argv.pop(1))
import batch_state
original_run = subprocess.run
def die_after_successful_merge(command, *args, **kwargs):
    result = original_run(command, *args, **kwargs)
    if 'merge' in command and '--ff-only' in command:
        assert result.returncode == 0, result.stderr
        os._exit(91)
    return result
subprocess.run = die_after_successful_merge
batch_state.main(sys.argv[1:])
"""
    result = subprocess.run([sys.executable, "-c", program, str(SCRIPT.parent),
                             *pool.argv("finish", "--slug", "a")[2:]],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 91, (result.stdout, result.stderr)
    integrated_tip = _head(pool.repo)
    assert integrated_tip == _head(worktree) and integrated_tip != returned_tip
    assert not (pool.repo / "doc/harness/archive/batch/TASK__a").exists()
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "cannot skip cleanup", ok=False)
    pool.cli("close", ok=False)
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence
    pool.cli("finish", "--slug", "a", "--resume")
    assert not worktree.exists()
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    assert _head(pool.repo) == integrated_tip
    assert (pool.repo / "doc/harness/archive/batch/TASK__a/TASK.json").read_bytes() == evidence
    pool.cli("close")


def test_nonexistent_glob_or_comma_scope_refuses_intake(tmp_path, monkeypatch, literal_scope):
    pool = Pool(tmp_path, monkeypatch)
    pool.init([request("a", literal_scope)], ok=False)
    assert not pool.state.exists()


def test_existing_literal_glob_or_comma_filename_is_valid_scope(tmp_path, monkeypatch, literal_scope):
    pool = Pool(tmp_path, monkeypatch)
    _write(pool.repo / literal_scope, "literal path\n")
    _git("add", "-A", cwd=pool.repo)
    _git("commit", "-qm", "literal filename", cwd=pool.repo)
    pool.init([request("a", literal_scope)])
    assert [row["slug"] for row in pool.claim()] == ["a"]


def test_removed_literal_glob_or_comma_scope_refuses_claim(tmp_path, monkeypatch, literal_scope):
    pool = Pool(tmp_path, monkeypatch)
    path = pool.repo / literal_scope
    _write(path, "literal path\n")
    _git("add", "-A", cwd=pool.repo)
    _git("commit", "-qm", "literal filename", cwd=pool.repo)
    pool.init([request("a", literal_scope)])
    path.unlink()
    _git("add", "-A", cwd=pool.repo)
    _git("commit", "-qm", "remove literal filename", cwd=pool.repo)
    before = pool.state.read_bytes()
    pool.cli("claim", ok=False)
    assert pool.state.read_bytes() == before


def test_recovery_merge_base_error_preserves_unknown_integration_and_halts_claim(tmp_path, monkeypatch, capsys):
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, _, directory = complete(pool)
    evidence = (directory / "TASK.json").read_bytes()
    program = """
import os, subprocess, sys
sys.path.insert(0, sys.argv.pop(1))
import batch_state
original_run = subprocess.run
def die_after_merge(command, *args, **kwargs):
    result = original_run(command, *args, **kwargs)
    if 'merge' in command and '--ff-only' in command:
        assert result.returncode == 0
        os._exit(91)
    return result
subprocess.run = die_after_merge
batch_state.main(sys.argv[1:])
"""
    crashed = subprocess.run([sys.executable, "-c", program, str(SCRIPT.parent),
                              *pool.argv("finish", "--slug", "a")[2:]],
                             capture_output=True, text=True, timeout=30)
    assert crashed.returncode == 91, (crashed.stdout, crashed.stderr)
    module = load_state_module()
    original_run = subprocess.run
    queries = []

    def unavailable_ancestry(command, *args, **kwargs):
        if "merge-base" in command:
            queries.append(command)
            return subprocess.CompletedProcess(command, 128, "", "fatal: ancestry unavailable")
        return original_run(command, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", unavailable_ancestry)
    code = module.main(pool.argv("recover", "--slug", "a", "--worker-stopped")[2:])
    output = capsys.readouterr().out
    monkeypatch.setattr(subprocess, "run", original_run)
    assert queries and code != 0, output
    assert worktree.exists() and (directory / "TASK.json").read_bytes() == evidence
    state = json.loads(pool.state.read_text())
    assert state.get("halted") or state["requests"][0]["status"] == "integrating"
    pool.cli("claim", ok=False)


# S1 integration: these use real linked worktrees and independent module stores.
def _s1_pool(tmp_path, monkeypatch, count=1):
    pool = Pool(tmp_path, monkeypatch)
    for key, value in {"GIT_AUTHOR_NAME": "S1 Test", "GIT_AUTHOR_EMAIL": "s1@example.test",
                       "GIT_COMMITTER_NAME": "S1 Test", "GIT_COMMITTER_EMAIL": "s1@example.test"}.items():
        monkeypatch.setenv(key, value)
    from test_batch_preflight import _repo as make_repo
    for number in range(count):
        source = make_repo(tmp_path / f"module-source-{number}", {"code.txt": "initial\n"})
        _git("-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source),
             f"libs/module{number}", cwd=pool.repo)
    _git("commit", "-qm", "module topology", cwd=pool.repo)
    return pool


def _s1_request(slug="a", module="libs/module0", suffix="code.txt"):
    value = request(slug, module + "/" + suffix)
    value["submodules"] = [module]
    return value


def _s1_complete(pool, slug="a", worker="worker-a", module="libs/module0"):
    worktree, branch = pool.bind(slug, worker)
    _write(worktree / module / "code.txt", "implemented " + slug + "\n")
    _git("add", "-A", cwd=worktree / module)
    _git("commit", "-qm", "module implementation", cwd=worktree / module)
    module_tip = _head(worktree / module)
    _git("add", module, cwd=worktree)
    _git("commit", "-qm", "record module", "--trailer", "Harness-Task: TASK__" + slug, cwd=worktree)
    directory = task(worktree, slug, closed=True)
    pool.cli(*pool.result(slug, worktree, branch, "closed", worker, run_id=RUN))
    return worktree, branch, directory, module_tip


def test_s1_schema_intent_idempotence_and_empty_selection_v1(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    selection = _s1_request()
    pool.init([selection])
    original = pool.state.read_bytes()
    data = json.loads(original)
    assert data["schema_version"] == 2
    item = data["requests"][0]
    assert item["submodules"] == ["libs/module0"]
    assert item["resolved_scopes"] == [str(pool.repo / "libs/module0/code.txt")]
    assert item["ownership_scopes"] == [str(pool.repo / "libs/module0")]
    pool.init([selection])
    assert pool.state.read_bytes() == original
    pool.init([request("a", "libs/module0/code.txt")], ok=False)
    assert pool.state.read_bytes() == original
    pool.batch = "ordinary"
    pool.init([{**request("ordinary", "README.md"), "submodules": []}])
    ordinary = json.loads(pool.state.read_text())
    assert ordinary["schema_version"] == 1
    assert not {"submodules", "ownership_scopes", "submodule_manifest"} & ordinary["requests"][0].keys()


def test_s1_downgraded_or_malformed_state_refuses_without_mutation(tmp_path, monkeypatch):
    import copy
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    original = json.loads(pool.state.read_text())
    variants = []
    changed = copy.deepcopy(original)
    changed["schema_version"] = 1
    variants.append(changed)
    for key, value in (("submodules", []), ("submodules", ["../escape"]),
                       ("submodules", ["libs/module0", "libs/module0"]),
                       ("ownership_scopes", [str(pool.repo / "README.md")])):
        changed = copy.deepcopy(original)
        changed["requests"][0][key] = value
        variants.append(changed)
    for changed in variants:
        pool.state.write_text(json.dumps(changed))
        before = pool.state.read_bytes()
        pool.cli("claim", ok=False)
        assert pool.state.read_bytes() == before


def test_s1_invalid_or_uncovered_selection_refuses_intake(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    for selected in (["missing"], ["libs/module0", "libs/module0"], ["../escape"], "libs/module0"):
        pool.init([{**request("a", "."), "submodules": selected}], ok=False)
        assert not pool.state.exists()
    pool.init([{**request("a", "README.md"), "submodules": ["libs/module0"]}], ok=False)
    assert not pool.state.exists()


def test_s1_sibling_ownership_serializes_while_distinct_modules_parallel(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch, count=2)
    pool.init([_s1_request("a", suffix="first.py"), _s1_request("b", suffix="second.py"),
               _s1_request("c", "libs/module1")])
    assert [item["slug"] for item in pool.claim()] == ["a", "c"]
    assert pool.claim() == []


def test_s1_retained_whole_module_ownership_blocks_new_batch_sibling(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = pool.bind()
    task(worktree)
    _write(worktree / "libs/module0/code.txt", "unfinished")
    pool.cli(*pool.result("a", worktree, branch))
    pool.cli("abandon", "--slug", "a", "--worker-stopped", "--reason", "retained work")
    pool.cli("close")
    pool.batch = "second"
    pool.init([_s1_request("sibling", suffix="elsewhere.py"), request("free", "README.md")])
    assert [item["slug"] for item in pool.claim()] == ["free"]
    assert (worktree / "libs/module0/code.txt").read_text() == "unfinished"


def test_s1_bind_persists_reservation_before_prepare_and_retries_exact_identity(tmp_path, monkeypatch, capsys):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = unbound_worktree(pool)
    module = load_state_module()
    import batch_submodules
    original = batch_submodules.prepare
    observed = []
    def interrupt(repo, work, value, persist):
        saved = json.loads(pool.state.read_text())["requests"][0]
        assert saved["status"] == "reserved"
        assert saved["worker_id"] == "worker-a" and saved["worktree"] == str(worktree)
        assert saved["submodule_manifest"] == value
        observed.append(True)
        original(repo, work, value, persist)
        raise OSError("stop after complete preparation")
    monkeypatch.setattr(batch_submodules, "prepare", interrupt)
    args = pool.argv("bind", "--slug", "a", "--worker-id", "worker-a", "--worktree", worktree, "--branch", branch)[2:]
    assert module.main(args) != 0
    capsys.readouterr()
    assert observed
    item = json.loads(pool.state.read_text())["requests"][0]
    assert item["status"] == "reserved"
    private = Path(item["submodule_manifest"]["modules"][0]["private_gitdir"])
    inode = private.stat().st_ino
    pool.cli("bind", "--slug", "a", "--worker-id", "foreign", "--worktree", worktree, "--branch", branch, ok=False)
    monkeypatch.setattr(batch_submodules, "prepare", original)
    pool.cli("bind", "--slug", "a", "--worker-id", "worker-a", "--worktree", worktree, "--branch", branch)
    item = json.loads(pool.state.read_text())["requests"][0]
    assert item["status"] == "running"
    assert all(m["phase"] == "prepared" for m in item["submodule_manifest"]["modules"])
    assert private.stat().st_ino == inode


def test_s1_resume_preserves_dirty_module_worker_handoff_and_identity(tmp_path, monkeypatch, s1_resume_worker):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = pool.bind()
    task(worktree)
    gitfile = worktree / "libs/module0/.git"
    before = gitfile.read_bytes()
    _write(worktree / "libs/module0/code.txt", "uncommitted module work")
    pool.cli(*pool.result("a", worktree, branch))
    handoff = pool.cli("resume", "--slug", "a", "--worker-stopped")
    assert str(worktree) in json.dumps(handoff)
    assert "worker-a" in json.dumps(handoff)
    assert "libs/module0" in json.dumps(handoff)
    pool.cli("bind", "--slug", "a", "--worker-id", s1_resume_worker, "--worktree", worktree, "--branch", branch)
    assert gitfile.read_bytes() == before
    assert (worktree / "libs/module0/code.txt").read_text() == "uncommitted module work"


def test_s1_resume_refuses_replaced_module_metadata(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = pool.bind()
    task(worktree)
    pool.cli(*pool.result("a", worktree, branch))
    (worktree / "libs/module0/.git").write_text("gitdir: " + str(pool.repo / ".git/modules/libs/module0") + "\n")
    before = pool.state.read_bytes()
    pool.cli("resume", "--slug", "a", "--worker-stopped", ok=False)
    assert pool.state.read_bytes() == before
    assert worktree.exists()


def test_s1_closed_result_requires_module_commit_recorded_by_superproject(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch = pool.bind()
    task(worktree, closed=True)
    _write(worktree / "libs/module0/code.txt", "module only commit")
    _git("add", "-A", cwd=worktree / "libs/module0")
    _git("commit", "-qm", "unrecorded module change", cwd=worktree / "libs/module0")
    before = pool.state.read_bytes()
    pool.cli(*pool.result("a", worktree, branch, "closed", run_id=RUN), ok=False)
    assert pool.state.read_bytes() == before
    _git("add", "libs/module0", cwd=worktree)
    _git("commit", "-qm", "record module", "--trailer", "Harness-Task: TASK__a", cwd=worktree)
    pool.cli(*pool.result("a", worktree, branch, "closed", run_id=RUN))


def test_s1_cli_full_finish_preserves_objects_and_removes_worktree_and_branch(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    config = (pool.repo / ".git/config").read_bytes()
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch, directory, tip = _s1_complete(pool)
    evidence = (directory / "TASK.json").read_bytes()
    pool.cli("finish", "--slug", "a")
    pool.cli("close")
    assert not worktree.exists()
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    assert _head(pool.repo / "libs/module0") == tip
    assert _git("status", "--porcelain", cwd=pool.repo) == ""
    assert _git("rev-list", "--merges", "HEAD", cwd=pool.repo) == ""
    _git("gc", "--prune=now", cwd=pool.repo / "libs/module0")
    assert _git("cat-file", "-t", tip, cwd=pool.repo / "libs/module0") == "commit"
    assert (pool.repo / ".git/config").read_bytes() == config
    assert (pool.repo / "doc/harness/archive/batch/TASK__a/TASK.json").read_bytes() == evidence


def _s1_crash_finish(pool, program):
    result = subprocess.run([sys.executable, "-c", program, str(SCRIPT.parent),
                             *pool.argv("finish", "--slug", "a")[2:]],
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 91, (result.stdout, result.stderr)


def test_s1_recover_reconciles_landed_witness_before_cleanliness_guard(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch, _, tip = _s1_complete(pool)
    old_module = _head(pool.repo / "libs/module0")
    _s1_crash_finish(pool, """
import os, subprocess, sys
sys.path.insert(0, sys.argv.pop(1))
import batch_state
original = subprocess.run
def crash(command, *args, **kwargs):
    result = original(command, *args, **kwargs)
    if 'merge' in command and '--ff-only' in command and result.returncode == 0:
        os._exit(91)
    return result
subprocess.run = crash
batch_state.main(sys.argv[1:])
""")
    assert _head(pool.repo) == _head(worktree)
    assert _head(pool.repo / "libs/module0") == old_module != tip
    assert _git("status", "--porcelain", cwd=pool.repo)
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    assert _head(pool.repo / "libs/module0") == tip
    if worktree.exists():
        pool.cli("finish", "--slug", "a", "--resume")
    assert not worktree.exists()
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    pool.cli("close")


def test_s1_removed_worktree_recovery_requires_preserved_objects(tmp_path, monkeypatch):
    pool = _s1_pool(tmp_path, monkeypatch)
    pool.init([_s1_request()])
    pool.claim()
    worktree, branch, _, _ = _s1_complete(pool)
    _s1_crash_finish(pool, """
import os, sys
sys.path.insert(0, sys.argv.pop(1))
import batch_state, batch_finish
original = batch_finish._cleanup
def crash(*args, **kwargs):
    original(*args, **kwargs)
    os._exit(91)
batch_finish._cleanup = crash
batch_state.main(sys.argv[1:])
""")
    assert not worktree.exists()
    item = json.loads(pool.state.read_text())["requests"][0]
    pin = next(iter(item["submodule_manifest"]["modules"][0]["pins"].values()))
    oid = _git("rev-parse", pin, cwd=pool.repo / "libs/module0")
    _git("update-ref", "-d", pin, cwd=pool.repo / "libs/module0")
    pool.cli("recover", "--slug", "a", "--worker-stopped", ok=False)
    pool.cli("close", ok=False)
    _git("update-ref", pin, oid, cwd=pool.repo / "libs/module0")
    pool.cli("recover", "--slug", "a", "--worker-stopped")
    assert not _git("branch", "--list", branch, cwd=pool.repo)
    pool.cli("close")


def test_pool_owner_is_immutable_on_reinitialization(tmp_path, monkeypatch):
    pool = Pool(tmp_path, monkeypatch)
    pool.init(owner="TASK__parent")
    before = pool.state.read_bytes()
    refused = pool.init(owner="TASK__other", ok=False)
    assert "owner" in refused["reason"] and pool.state.read_bytes() == before
    assert pool.init(owner="TASK__parent")["batch"]["owner_task_id"] == "TASK__parent"


def test_sealed_verification_source_disposal_preserves_archive(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    import verification_target
    from test_wbs import wbs, plan, node
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    state = wbs.prepare(str(directory), plan([node()]), RUN, now=100)
    (directory / 'WBS.json').write_text(wbs.encode_state(state))
    target = verification_target.issue_target(str(directory), str(worktree), dict(
        inputs={'source': ['a.txt']}, commands=[], lenses=[], purpose='diagnostic'))
    view = Path(target['snapshot_root'])
    assert view.stat().st_mode & 0o200 == 0
    original = (view / 'a.txt').read_bytes()
    # A supported new run retains already issued source copies as history.
    current_control = (directory / 'TASK.json').read_bytes()
    old_run = '0198c349-5800-7000-8000-000000000002'
    old_control = json.loads(current_control)
    old_control['run_id'] = old_run
    (directory / 'TASK.json').write_text(json.dumps(old_control))
    state['run_id'] = old_run
    (directory / 'WBS.json').write_text(wbs.encode_state(state))
    historical = verification_target.issue_target(str(directory), str(worktree), dict(
        inputs={'source': ['a.txt']}, commands=[], lenses=[], purpose='diagnostic'))
    (directory / 'TASK.json').write_bytes(current_control)
    state['run_id'] = RUN
    (directory / 'WBS.json').write_text(wbs.encode_state(state))
    try:
        verification_target.load_target(str(directory), historical['token'])
        raise AssertionError('historical target must not certify current acceptance')
    except ValueError as exc:
        assert 'another run' in str(exc)
    historical_view = Path(historical['snapshot_root'])
    result = pool.cli('finish', '--slug', 'a')
    assert result['status'] == 'integrated', result
    archived = pool.repo / 'doc/harness/archive/batch/TASK__a' / view.relative_to(directory)
    assert (archived / 'a.txt').read_bytes() == original
    assert archived.stat().st_mode & 0o200 == 0
    historical_archive = pool.repo / 'doc/harness/archive/batch/TASK__a' / historical_view.relative_to(directory)
    assert (historical_archive / 'a.txt').read_bytes() == original
    assert historical_archive.stat().st_mode & 0o200 == 0
    assert not worktree.exists()
    assert not _git('branch', '--list', branch, cwd=pool.repo)


def test_partial_ordinary_residue_recovery_and_refusals(tmp_path, monkeypatch, capsys):
    monkeypatch.syspath_prepend(str(SCRIPT.parent))
    import batch_finish
    import batch_state
    pool = Pool(tmp_path, monkeypatch)
    pool.init()
    pool.claim()
    worktree, branch, directory = complete(pool)
    original_cleanup = batch_finish._cleanup
    original_task = (directory / 'PLAN.md').read_bytes()
    prior_row = json.dumps({'key': 'main-existing', 'insight': 'prior'}) + '\n'
    lead_row = json.dumps({'key': 'lead-new', 'insight': 'new'}) + '\n'
    _write(pool.repo / 'doc/harness/learnings.jsonl', prior_row)
    ledger = worktree / 'doc/harness/learnings.jsonl'
    _write(ledger, lead_row)
    def interrupted(repo, tree, name, result, *args, **kwargs):
        # Real Git successfully drops its registration; simulate the subset left
        # by an interrupted filesystem removal without editing Git metadata.
        _git('worktree', 'remove', str(worktree), cwd=pool.repo)
        _write(worktree / 'a.txt', 'a\n')
        _write(directory / 'PLAN.md', original_task.decode())
        _write(ledger, lead_row)
        directory.chmod(0o500)
        raise OSError('simulated partial removal')
    monkeypatch.setattr(batch_finish, '_cleanup', interrupted)
    assert batch_state.main(pool.argv('finish', '--slug', 'a')[2:]) != 0
    capsys.readouterr()
    monkeypatch.setattr(batch_finish, '_cleanup', original_cleanup)
    assert worktree.exists()
    archive = pool.repo / 'doc/harness/archive/batch/TASK__a'
    original_archive = (archive / 'PLAN.md').read_bytes()
    for kind in ('unknown-file', 'unknown-dir', 'modified', 'exec-bit', 'symlink', 'fifo', 'archive', 'missing-row', 'malformed-row'):
        path = worktree / 'valuable'
        if kind == 'unknown-file': path.write_text('keep me')
        elif kind == 'unknown-dir': path.mkdir()
        elif kind == 'modified': (worktree / 'a.txt').write_text('changed')
        elif kind == 'exec-bit': (worktree / 'a.txt').chmod(0o755)
        elif kind == 'symlink': path.symlink_to('a.txt')
        elif kind == 'fifo': os.mkfifo(path)
        elif kind == 'missing-row': ledger.write_text(lead_row + '{\"key\":\"unpreserved\"}\n')
        elif kind == 'malformed-row': ledger.write_text(lead_row + 'invalid-json\n')
        else: (archive / 'PLAN.md').write_text('changed archive')
        before_mode = directory.stat().st_mode
        pool.cli('recover', '--slug', 'a', '--worker-stopped', ok=False)
        assert directory.stat().st_mode == before_mode
        assert worktree.exists() and _git('branch', '--list', branch, cwd=pool.repo)
        if kind == 'unknown-dir': path.rmdir()
        elif kind in ('unknown-file', 'symlink', 'fifo'): path.unlink()
        elif kind == 'modified': (worktree / 'a.txt').write_text('a\n')
        elif kind == 'exec-bit': (worktree / 'a.txt').chmod(0o644)
        elif kind in ('missing-row', 'malformed-row'): ledger.write_text(lead_row)
        else: (archive / 'PLAN.md').write_bytes(original_archive)
    destination_ledger = pool.repo / 'doc/harness/learnings.jsonl'
    preserved_ledger = destination_ledger.read_bytes()
    assert prior_row.encode() in preserved_ledger and lead_row.encode() in preserved_ledger
    pool.cli('recover', '--slug', 'a', '--worker-stopped')
    assert destination_ledger.read_bytes() == preserved_ledger
    assert not worktree.exists() and not _git('branch', '--list', branch, cwd=pool.repo)
    # Idempotent retry after the branch has already disappeared.
    pool.cli('recover', '--slug', 'a', '--worker-stopped')
    assert (archive / 'PLAN.md').read_bytes() == original_archive
    pool.cli('close')
