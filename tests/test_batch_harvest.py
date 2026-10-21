from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path



ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "batch_harvest", ROOT / "plugin/scripts/batch_harvest.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)


def test_tree_entries_bounds_pending_hashes_and_preserves_walk_order(tmp_path, monkeypatch):
    for index in range(9):
        path = tmp_path / ("nested" if index % 2 else "") / str(index)
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(bytes([index]) * 17)
    expected = {}
    for parent, dirs, files in os.walk(tmp_path):
        for name in dirs:
            expected[os.path.relpath(os.path.join(parent, name), tmp_path)] = None
        for name in files:
            path = os.path.join(parent, name)
            expected[os.path.relpath(path, tmp_path)] = mod._hash_file(path)

    class DeferredExecutor:
        pending = 0
        maximum = 0
        closed = False

        def __init__(self, max_workers):
            assert max_workers == 4

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True
            assert self.pending == 0

        def submit(self, function, path):
            self.pending += 1
            self.maximum = max(self.maximum, self.pending)
            owner = self

            class Future:
                def result(self):
                    owner.pending -= 1
                    return function(path)

            return Future()

    pool = DeferredExecutor(4)
    monkeypatch.setattr(mod, "ThreadPoolExecutor", lambda **kwargs: pool)
    assert list(mod._tree_entries(str(tmp_path)).items()) == list(expected.items())
    assert pool.maximum == 4
    assert pool.closed


def test_tree_entries_hashes_overlap_and_stream_identical_bytes(tmp_path, monkeypatch):
    import hashlib

    expected = {}
    for index in range(4):
        payload = bytes([index]) * (1024 * 1024 + 19)
        (tmp_path / str(index)).write_bytes(payload)
        expected[str(index)] = hashlib.sha256(payload).hexdigest()
    original = mod._hash_file
    barrier = threading.Barrier(4)

    def synchronized_hash(path):
        barrier.wait(timeout=10)
        return original(path)

    monkeypatch.setattr(mod, "_hash_file", synchronized_hash)
    assert mod._tree_entries(str(tmp_path)) == expected


def test_tree_entries_propagates_errors_and_joins_workers(tmp_path, monkeypatch):
    import pytest
    from concurrent.futures import ThreadPoolExecutor

    pools = []

    class TrackingExecutor(ThreadPoolExecutor):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            pools.append(self)

    monkeypatch.setattr(mod, "ThreadPoolExecutor", TrackingExecutor)
    (tmp_path / "file").write_bytes(b"evidence")

    def failed_hash(path):
        raise OSError("hash read failed")

    with monkeypatch.context() as patch:
        patch.setattr(mod, "_hash_file", failed_hash)
        with pytest.raises(OSError, match="hash read failed"):
            mod._tree_entries(str(tmp_path))

    original_scan = os.scandir
    denied = tmp_path / "denied"
    denied.mkdir()

    def failed_scan(path):
        if os.fspath(path) == str(denied):
            raise PermissionError("scan failed")
        return original_scan(path)

    monkeypatch.setattr(os, "scandir", failed_scan)
    with pytest.raises(PermissionError, match="scan failed"):
        mod._tree_entries(str(tmp_path))
    assert len(pools) == 2
    assert all(pool._shutdown for pool in pools)
    assert all(not thread.is_alive() for pool in pools for thread in pool._threads)


def _git(*args, cwd):
    result = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "test@example.com", cwd=repo)
    _git("config", "user.name", "Test", cwd=repo)
    (repo / "README.md").write_text("root\n", encoding="utf-8")
    (repo / "doc/harness").mkdir(parents=True, exist_ok=True)
    (repo / "doc/harness/manifest.yaml").write_text("version: 5\n", encoding="utf-8")
    _git("add", "README.md", "doc/harness/manifest.yaml", cwd=repo)
    _git("commit", "-q", "-m", "init", cwd=repo)


def _add_worktree(repo: Path, worktree: Path, branch: str) -> None:
    worktree.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-b", branch, str(worktree), cwd=repo)


def _write_task(root: Path, task_id: str, content: str = "plan\n") -> Path:
    """Write task evidence directly in the worktree's filesystem, the way a
    lead's gitignored doc/harness/tasks/ actually behaves: never git-added."""
    task_dir = root / "doc/harness/tasks" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "PLAN.md").write_text(content, encoding="utf-8")
    return task_dir


def _write_learnings(root: Path, rows: list) -> None:
    (root / "doc/harness/learnings.jsonl").write_text(
        "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8",
    )


def _rebase_and_fast_forward(repo: Path, worktree: Path, branch: str) -> None:
    """The batch skill's step d.1: rebase the lead branch onto the main
    checkout's HEAD inside the lead worktree, then fast-forward main."""
    base = _git("rev-parse", "HEAD", cwd=repo)
    _git("rebase", "-q", "--no-autostash", base, cwd=worktree)
    _git("merge", "-q", "--ff-only", branch, cwd=repo)


def _commit_and_merge(repo: Path, worktree: Path, branch: str) -> None:
    """Commit a real, git-tracked source change on `branch` in the worktree
    and integrate it into the main checkout's HEAD. Mirrors how a lead's actual
    code change is what gets integrated, while its gitignored task evidence and
    learnings stay worktree-local (never git-added, never touched by git)."""
    (worktree / "src.txt").write_text(f"change from {branch}\n", encoding="utf-8")
    _git("add", "src.txt", cwd=worktree)
    _git("commit", "-q", "-m", f"{branch} work", cwd=worktree)
    _rebase_and_fast_forward(repo, worktree, branch)


def _raises(exc, match=None):
    import pytest

    return pytest.raises(exc, match=match)


def _main_repo(tmp_path: Path) -> Path:
    r = tmp_path / "main"
    _init_repo(r)
    return r


def test_happy_path_copy_and_append(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-1")

    task_id = "TASK__demo"
    _write_task(worktree, task_id, "plan body\n")
    _write_learnings(worktree, [
        {"key": "a", "insight": "one"},
        {"key": "b", "insight": "two"},
    ])
    _commit_and_merge(repo, worktree, "lead-1")

    summary = mod.harvest(str(repo), str(worktree), task_id)

    dest = repo / "doc/harness/archive/batch" / task_id
    assert dest.is_dir()
    assert (dest / "PLAN.md").read_text(encoding="utf-8") == "plan body\n"
    assert summary["archived"] == str(dest)
    assert summary["learnings_appended"] == 2

    learnings = (repo / "doc/harness/learnings.jsonl").read_text(encoding="utf-8")
    assert '"insight": "one"' in learnings
    assert '"insight": "two"' in learnings


def test_idempotent_second_run_appends_zero(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-1")
    task_id = "TASK__demo"
    _write_task(worktree, task_id)
    _write_learnings(worktree, [{"key": "a"}])
    _commit_and_merge(repo, worktree, "lead-1")

    first = mod.harvest(str(repo), str(worktree), task_id)
    assert first["learnings_appended"] == 1

    second = mod.harvest(str(repo), str(worktree), task_id)
    assert second["learnings_appended"] == 0
    assert second["archived"] == first["archived"]

    dest = Path(second["archived"])
    assert (dest / "PLAN.md").exists()


def test_refuses_plain_directory(tmp_path: Path):
    repo = _main_repo(tmp_path)
    plain = tmp_path / "not_a_worktree"
    plain.mkdir()
    _write_task(plain, "TASK__demo")

    with _raises(mod.HarvestError):
        mod.harvest(str(repo), str(plain), "TASK__demo")

    assert not (repo / "doc/harness/archive/batch").exists()


def test_refuses_worktree_of_different_repo(tmp_path: Path):
    repo = _main_repo(tmp_path)
    other_repo = tmp_path / "other"
    _init_repo(other_repo)
    other_worktree = tmp_path / "other_wt"
    _add_worktree(other_repo, other_worktree, "lead-x")
    _write_task(other_worktree, "TASK__demo")

    with _raises(mod.HarvestError):
        mod.harvest(str(repo), str(other_worktree), "TASK__demo")

    assert not (repo / "doc/harness/archive/batch").exists()


def test_refuses_forged_back_pointer(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-1")
    _write_task(worktree, "TASK__demo")
    _commit_and_merge(repo, worktree, "lead-1")

    # Forge the back-pointer inside .git/worktrees/<name>/gitdir so it no
    # longer names this worktree's .git file.
    gitfile_text = (worktree / ".git").read_text(encoding="utf-8")
    assert gitfile_text.startswith("gitdir: ")
    metadata_dir = Path(gitfile_text[len("gitdir: "):].strip())
    back_pointer = metadata_dir / "gitdir"
    back_pointer.write_text(str(tmp_path / "somewhere-else" / ".git") + "\n", encoding="utf-8")

    with _raises(mod.HarvestError, match="back-pointer"):
        mod.harvest(str(repo), str(worktree), "TASK__demo")

    assert not (repo / "doc/harness/archive/batch").exists()


def test_refuses_unmerged_branch(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-1")
    _write_task(worktree, "TASK__demo")
    (worktree / "src.txt").write_text("unmerged change\n", encoding="utf-8")
    subprocess.run(["git", "add", "src.txt"], cwd=worktree, check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "unmerged lead work"], cwd=worktree, check=True,
    )
    # Deliberately do not merge lead-1 into the main checkout's HEAD.

    with _raises(mod.HarvestError, match="not merged"):
        mod.harvest(str(repo), str(worktree), "TASK__demo")

    assert not (repo / "doc/harness/archive/batch").exists()


def _ignore_task_evidence(repo: Path) -> None:
    """Commit the ignores a real project has, so task evidence stays out of git
    and `git worktree remove` sees a clean lead worktree."""
    (repo / ".gitignore").write_text(
        "doc/harness/tasks/\ndoc/harness/learnings.jsonl\ndoc/harness/archive/\n",
        encoding="utf-8",
    )
    _git("add", ".gitignore", cwd=repo)
    _git("commit", "-q", "-m", "ignore task evidence", cwd=repo)


def _lead_commit(worktree: Path, name: str, body: str, message: str) -> None:
    (worktree / name).write_text(body, encoding="utf-8")
    _git("add", name, cwd=worktree)
    _git("commit", "-q", "-m", message, cwd=worktree)


def test_two_leads_rebased_and_fast_forwarded_are_harvested_and_removed(tmp_path: Path):
    repo = _main_repo(tmp_path)
    _ignore_task_evidence(repo)
    base = _git("rev-parse", "HEAD", cwd=repo)
    leads = []
    for n in (1, 2):
        worktree = tmp_path / f"wt{n}"
        branch = f"lead-{n}"
        _add_worktree(repo, worktree, branch)
        _git("worktree", "lock", str(worktree), cwd=repo)  # Claude's agent lock
        task_id = f"TASK__lead{n}"
        _write_task(worktree, task_id, f"plan {n}\n")
        _write_learnings(worktree, [{"key": f"k{n}", "insight": f"lead {n}"}])
        _lead_commit(worktree, f"file{n}.txt", f"lead {n}\n", f"lead {n} work")
        leads.append((worktree, branch, task_id))

    tip_before = _git("rev-parse", "HEAD", cwd=leads[1][0])
    for index, (worktree, branch, task_id) in enumerate(leads):
        if index == 1:
            # Rebased but not yet fast-forwarded: harvest must refuse.
            base_now = _git("rev-parse", "HEAD", cwd=repo)
            _git("rebase", "-q", "--no-autostash", base_now, cwd=worktree)
            with _raises(mod.HarvestError, match="not merged"):
                mod.harvest(str(repo), str(worktree), task_id)
        _rebase_and_fast_forward(repo, worktree, branch)
        assert _git("rev-parse", "HEAD", cwd=worktree) == _git("rev-parse", "HEAD", cwd=repo)
        summary = mod.harvest(str(repo), str(worktree), task_id)
        assert summary["learnings_appended"] == 1
        _git("worktree", "unlock", str(worktree), cwd=repo)
        _git("worktree", "remove", str(worktree), cwd=repo)
        _git("branch", "-d", branch, cwd=repo)

    # The second lead was rewritten onto the first; history is linear.
    assert _git("rev-parse", "HEAD", cwd=repo) != tip_before
    assert _git("rev-list", "--merges", f"{base}..HEAD", cwd=repo) == ""
    assert _git("log", "--format=%s", f"{base}..HEAD", cwd=repo).splitlines() == [
        "lead 2 work", "lead 1 work",
    ]
    for _, _, task_id in leads:
        assert (repo / "doc/harness/archive/batch" / task_id / "PLAN.md").is_file()
    assert _git("worktree", "list", "--porcelain", cwd=repo).count("worktree ") == 1


def test_rebase_conflict_abort_restores_the_lead_and_leaves_main(tmp_path: Path):
    repo = _main_repo(tmp_path)
    _ignore_task_evidence(repo)
    first, second = tmp_path / "wt1", tmp_path / "wt2"
    _add_worktree(repo, first, "lead-1")
    _add_worktree(repo, second, "lead-2")
    _lead_commit(first, "shared.txt", "from lead 1\n", "lead 1 work")
    _lead_commit(second, "shared.txt", "from lead 2\n", "lead 2 work")
    _rebase_and_fast_forward(repo, first, "lead-1")
    main_head = _git("rev-parse", "HEAD", cwd=repo)
    lead_tip = _git("rev-parse", "HEAD", cwd=second)

    conflict = subprocess.run(
        ["git", "rebase", "-q", "--no-autostash", main_head],
        cwd=second, capture_output=True, text=True, check=False,
    )
    assert conflict.returncode != 0
    _git("rebase", "--abort", cwd=second)

    assert _git("rev-parse", "HEAD", cwd=second) == lead_tip
    assert _git("status", "--porcelain", cwd=second) == ""
    assert _git("rev-parse", "HEAD", cwd=repo) == main_head
    with _raises(mod.HarvestError, match="not merged"):
        mod.harvest(str(repo), str(second), "TASK__lead2")


def test_rebase_stopped_at_a_conflict_is_refused_then_continued_and_integrated(tmp_path: Path):
    """A stopped rebase detaches the worktree HEAD at the main HEAD, which
    passes the ancestor check; harvest must refuse it. The integration task
    then resolves, continues, fast-forwards, harvests, and removes."""
    repo = _main_repo(tmp_path)
    _ignore_task_evidence(repo)
    first, second = tmp_path / "wt1", tmp_path / "wt2"
    _add_worktree(repo, first, "lead-1")
    _add_worktree(repo, second, "lead-2")
    _write_task(second, "TASK__lead2")
    _lead_commit(first, "shared.txt", "from lead 1\n", "lead 1 work")
    _lead_commit(second, "shared.txt", "from lead 2\n", "lead 2 work")
    _rebase_and_fast_forward(repo, first, "lead-1")
    main_head = _git("rev-parse", "HEAD", cwd=repo)

    stopped = subprocess.run(
        ["git", "rebase", "-q", "--no-autostash", main_head],
        cwd=second, capture_output=True, text=True, check=False,
    )
    assert stopped.returncode != 0
    assert _git("diff", "--name-only", "--diff-filter=U", cwd=second) == "shared.txt"
    assert _git("rev-parse", "HEAD", cwd=second) == main_head  # detached at main
    with _raises(mod.HarvestError, match="detached"):
        mod.harvest(str(repo), str(second), "TASK__lead2")
    assert not (repo / "doc/harness/archive/batch/TASK__lead2").exists()

    (second / "shared.txt").write_text("from lead 1\nfrom lead 2\n", encoding="utf-8")
    _git("add", "shared.txt", cwd=second)
    env = {**os.environ, "GIT_EDITOR": "true"}
    subprocess.run(["git", "rebase", "--continue"], cwd=second, env=env,
                   capture_output=True, check=True)
    _git("merge", "-q", "--ff-only", "lead-2", cwd=repo)
    mod.harvest(str(repo), str(second), "TASK__lead2")
    _git("worktree", "remove", str(second), cwd=repo)
    _git("branch", "-d", "lead-2", cwd=repo)

    assert (repo / "shared.txt").read_text(encoding="utf-8") == "from lead 1\nfrom lead 2\n"
    assert _git("rev-list", "--merges", "HEAD", cwd=repo) == ""
    assert (repo / "doc/harness/archive/batch/TASK__lead2/PLAN.md").is_file()


def test_refuses_invalid_task_id(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-1")
    _commit_and_merge(repo, worktree, "lead-1")

    with _raises(mod.HarvestError, match="invalid task_id"):
        mod.harvest(str(repo), str(worktree), "../escape")

    assert not (repo / "doc/harness/archive/batch").exists()


def _merged_lead(repo: Path, tmp_path: Path, branch: str = "lead-x") -> Path:
    worktree = tmp_path / branch
    _add_worktree(repo, worktree, branch)
    _commit_and_merge(repo, worktree, branch)
    return worktree


def test_refuses_symlink_inside_task_evidence(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = _merged_lead(repo, tmp_path)
    task_dir = _write_task(worktree, "TASK__demo")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret\n", encoding="utf-8")
    (task_dir / "leak").symlink_to(secret)
    with _raises(mod.HarvestError, match="non-regular entry"):
        mod.harvest(str(repo), str(worktree), "TASK__demo")
    assert not (repo / "doc/harness/archive/batch/TASK__demo").exists()


def test_refuses_symlinked_evidence_parent_and_fifo(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = _merged_lead(repo, tmp_path)
    outside = tmp_path / "outside-tasks"
    _write_task(outside, "TASK__demo")
    (worktree / "doc/harness/tasks").symlink_to(outside / "doc/harness/tasks")
    with _raises(mod.HarvestError, match="symlink"):
        mod.harvest(str(repo), str(worktree), "TASK__demo")

    worktree2 = _merged_lead(repo, tmp_path, "lead-y")
    task_dir = _write_task(worktree2, "TASK__demo")
    os.mkfifo(task_dir / "pipe")
    with _raises(mod.HarvestError, match="non-regular entry"):
        mod.harvest(str(repo), str(worktree2), "TASK__demo")


def test_learnings_links_refused_and_non_json_rows_skipped(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = _merged_lead(repo, tmp_path)
    _write_task(worktree, "TASK__demo")
    real = tmp_path / "rows.jsonl"
    real.write_text('{"k": 1}\n', encoding="utf-8")
    (worktree / "doc/harness/learnings.jsonl").symlink_to(real)
    with _raises(mod.HarvestError, match="symlink"):
        mod.harvest(str(repo), str(worktree), "TASK__demo")
    assert not (repo / "doc/harness/archive/batch/TASK__demo").exists()

    (worktree / "doc/harness/learnings.jsonl").unlink()
    (worktree / "doc/harness/learnings.jsonl").write_text(
        '{"k": 1}\nnot json\n["array"]\n{"k": 2}\n', encoding="utf-8",
    )
    summary = mod.harvest(str(repo), str(worktree), "TASK__demo")
    assert summary["learnings_appended"] == 2
    assert (repo / "doc/harness/learnings.jsonl").read_text(encoding="utf-8") == '{"k": 1}\n{"k": 2}\n'


def test_refuses_to_replace_a_different_archive_for_the_same_task_id(tmp_path: Path):
    repo = _main_repo(tmp_path)
    first = _merged_lead(repo, tmp_path, "lead-a")
    _write_task(first, "TASK__fix", "evidence from batch 1\n")
    mod.harvest(str(repo), str(first), "TASK__fix")
    archived = repo / "doc/harness/archive/batch/TASK__fix/PLAN.md"
    assert archived.read_text(encoding="utf-8") == "evidence from batch 1\n"

    second = _merged_lead(repo, tmp_path, "lead-b")
    _write_task(second, "TASK__fix", "evidence from batch 2\n")
    with _raises(mod.HarvestError, match="refusing to overwrite"):
        mod.harvest(str(repo), str(second), "TASK__fix")
    assert archived.read_text(encoding="utf-8") == "evidence from batch 1\n"


def test_ambient_git_env_cannot_redirect_the_merged_check(tmp_path: Path, monkeypatch):
    repo = _main_repo(tmp_path)
    decoy = tmp_path / "decoy"
    _init_repo(decoy)
    worktree = tmp_path / "wt"
    _add_worktree(repo, worktree, "lead-env")
    (worktree / "src.txt").write_text("unmerged\n", encoding="utf-8")
    _git("add", "src.txt", cwd=worktree)
    _git("commit", "-q", "-m", "unmerged", cwd=worktree)
    _write_task(worktree, "TASK__demo")
    # Pointing git at a decoy repository must not make an unmerged lead look merged.
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    with _raises(mod.HarvestError):
        mod.harvest(str(repo), str(worktree), "TASK__demo")
    assert not (repo / "doc/harness/archive/batch/TASK__demo").exists()


def test_non_canonical_worktree_path_is_canonicalized_before_use(tmp_path: Path):
    repo = _main_repo(tmp_path)
    worktree = _merged_lead(repo, tmp_path, "lead-c")
    _write_task(worktree, "TASK__demo")
    alias = str(tmp_path / "lead-c" / ".." / "lead-c")
    summary = mod.harvest(str(repo), alias, "TASK__demo")
    assert summary["archived"] == str(repo.resolve() / "doc/harness/archive/batch/TASK__demo")
    with _raises(mod.HarvestError, match="absolute"):
        mod.harvest(str(repo), "lead-c", "TASK__demo")


def _s1_removal_fixture(tmp_path, monkeypatch, count=1, tracked_link=False, preexisting_tag=False):
    import copy
    import test_batch_submodules as fixture
    import test_batch_state as state_fixture
    import batch_state

    main, work, paths = fixture._setup(tmp_path, monkeypatch, count)
    if preexisting_tag:
        fixture._git(main / paths[0], "tag", "-a", "preexisting-annotated", "-m", "valuable preexisting annotation")
    (main / "doc/harness").mkdir(parents=True)
    (main / "doc/harness/manifest.yaml").write_text("version: 5\n")
    if tracked_link:
        target = tmp_path / "external"
        target.mkdir()
        (target / "precious").write_text("external data")
        (main / "external-link").symlink_to(target, target_is_directory=True)
    with (main / ".gitignore").open("a") as handle:
        handle.write("doc/harness/tasks/\ndoc/harness/archive/\ndoc/harness/runtime/\ndoc/harness/learnings.jsonl\n")
    fixture._commit(main, "ignore operational evidence")
    fixture._git(work, "rebase", "main")
    helper = fixture._helper()
    manifest = fixture._manifest(helper, main, work, [paths[0]])
    helper.prepare(main, work, manifest, lambda _: None)
    old = fixture._git(main, "rev-parse", "HEAD")
    module_tip = fixture._change(work, paths[0])
    helper.preserve(main, work, manifest, lambda _: None)
    tip = fixture._git(work, "rev-parse", "HEAD")
    witness = helper.witness(main, work, manifest, "refs/heads/main", old, tip)
    manifest["witness"] = witness
    fixture._git(main, "merge", "--ff-only", "lead")
    helper.reconcile_checkout(main, manifest, witness)
    task_dir = state_fixture.task(work, slug="lead", closed=True)
    control = json.loads((task_dir / "TASK.json").read_text())
    harvested = mod.harvest(str(main), str(work), "TASK__lead")
    batch_state.sync_harvest(str(main), harvested["archived"])
    checkpoint = dict(stage="harvested", destination_ref="refs/heads/main", branch_tip=tip,
                      integrated_tip=tip, run_id=control["run_id"],
                      close_fingerprint=control["close_receipt_fingerprint"], harvest=harvested,
                      archive_fingerprint=batch_state.fingerprint(harvested["archived"]))
    request = dict(slug="lead", request="module edit", scopes=[paths[0]], depends_on=[],
                   resolved_scopes=[str(main / paths[0])], ownership_scopes=[str(main / paths[0])],
                   submodules=[paths[0]], submodule_manifest=copy.deepcopy(manifest),
                   status="integrating", task_id="TASK__lead", worker_id="worker-lead",
                   worktree=str(work), branch="lead", spawn_head=manifest["spawn_head"],
                   run_id=control["run_id"], close_fingerprint=control["close_receipt_fingerprint"],
                   checkpoint=copy.deepcopy(checkpoint),
                   result=dict(verdict="closed", task_id="TASK__lead", worktree=str(work), branch="lead", commit=tip))
    state = dict(schema_version=2, batch_id=manifest["batch_id"], repo=str(main),
                 destination_ref="refs/heads/main", initial_head=manifest["spawn_head"],
                 max_leads=3, cap_source="default", status="open", halted=False,
                 created_at="2026-09-29T00:00:00Z", updated_at="2026-09-29T00:00:00Z", requests=[request])
    state_path = main / "doc/harness/runtime/batches" / (manifest["batch_id"] + ".json")
    state_path.parent.mkdir(parents=True, exist_ok=True)
    batch_state.write_json(str(state_path), state)
    return main, work, paths[0], manifest, checkpoint, state_path, state, module_tip


def test_s1_guarded_removal_uses_single_force_and_preserves_objects(tmp_path, monkeypatch):
    main, work, path, manifest, checkpoint, _, _, tip = _s1_removal_fixture(tmp_path, monkeypatch)
    calls = []
    def runner(repo, *args):
        calls.append(args)
        return subprocess.run(["git", *map(str, args)], cwd=repo, capture_output=True, text=True)
    result = mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint, git_runner=runner)
    assert result.returncode == 0, result.stderr
    assert calls == [("worktree", "remove", "--force", str(work))]
    assert not work.exists()
    assert not Path(manifest["admin_dir"]).exists()
    _git("gc", "--prune=now", cwd=main / path)
    assert _git("cat-file", "-t", tip, cwd=main / path) == "commit"
    assert (main / "doc/harness/archive/batch/TASK__lead/PLAN.md").exists()
    # The helper removes only the tree: the finish owner still owns safe -d.
    assert _git("rev-parse", "refs/heads/lead", cwd=main) == checkpoint["integrated_tip"]


def test_s1_guarded_removal_requires_exact_persisted_state(tmp_path, monkeypatch):
    import copy
    import batch_state
    main, work, _, manifest, checkpoint, state_path, state, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    variants = []
    for key, value in (("schema_version", 1), ("batch_id", "foreign"), ("repo", str(tmp_path)),
                       ("destination_ref", "refs/heads/foreign")):
        changed = copy.deepcopy(state)
        changed[key] = value
        variants.append(changed)
    for key, value in (("status", "returned"), ("task_id", "TASK__foreign"), ("spawn_head", "0" * 40),
                       ("worktree", str(tmp_path / "foreign")), ("branch", "foreign"),
                       ("run_id", "foreign"), ("close_fingerprint", "sha256:" + "0" * 64)):
        changed = copy.deepcopy(state)
        changed["requests"][0][key] = value
        variants.append(changed)
    changed = copy.deepcopy(state)
    changed["requests"][0]["submodule_manifest"]["slug"] = "foreign"
    variants.append(changed)
    changed = copy.deepcopy(state)
    changed["requests"][0]["checkpoint"]["stage"] = "integrated"
    variants.append(changed)
    for changed in variants:
        batch_state.write_json(str(state_path), changed)
        with _raises(mod.HarvestError):
            mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
        assert work.exists()
    state_path.unlink()
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert work.exists()


def test_s1_guarded_removal_rejects_changed_evidence_and_archive(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    archive = Path(checkpoint["harvest"]["archived"])
    for path in (archive / "PLAN.md", work / "doc/harness/tasks/TASK__lead/PLAN.md",
                 archive / "TASK.json", work / "doc/harness/tasks/TASK__lead/TASK.json"):
        original = path.read_bytes()
        path.write_bytes(original + b"changed")
        with _raises(mod.HarvestError):
            mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
        assert work.exists()
        path.write_bytes(original)


def test_s1_guarded_removal_rejects_unknown_ignored_and_untracked_content(tmp_path, monkeypatch):
    main, work, module, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    for path in (work / "unknown.txt", work / "doc/harness/tasks/unknown.bin",
                 work / "doc/harness/runtime/precious.bin", work / module / "unknown.txt"):
        missing_parents = []
        parent = path.parent
        while not parent.exists():
            missing_parents.append(parent)
            parent = parent.parent
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("irreplaceable")
        with _raises(mod.HarvestError):
            mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
        assert path.read_text() == "irreplaceable"
        path.unlink()
        for parent in missing_parents:
            parent.rmdir()
    gitdir = Path(_git("rev-parse", "--absolute-git-dir", cwd=work / module))
    (gitdir / "info").mkdir(exist_ok=True)
    (gitdir / "info/exclude").write_text("private-data\n")
    hidden = work / module / "private-data"
    hidden.write_text("irreplaceable")
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert hidden.read_text() == "irreplaceable"


def test_s1_guarded_removal_rejects_dirty_superproject_and_module(tmp_path, monkeypatch):
    main, work, module, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    for path in (work / "file.txt", work / module / "file.txt"):
        before = path.read_bytes()
        path.write_text("keep edits")
        with _raises(mod.HarvestError):
            mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
        assert path.read_text() == "keep edits"
        path.write_bytes(before)


def test_s1_guarded_removal_rejects_new_refs_and_missing_pins(tmp_path, monkeypatch):
    main, work, module, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    _git("branch", "new-work", cwd=work / module)
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    _git("branch", "-d", "new-work", cwd=work / module)
    pin = next(iter(manifest["modules"][0]["pins"].values()))
    _git("update-ref", "-d", pin, cwd=main / module)
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert work.exists()


def test_s1_guarded_removal_rejects_ignored_nested_repository(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    hidden = work / "doc/harness/runtime/nested"
    _init_repo(hidden)
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert (hidden / ".git").exists()


def test_s1_guarded_removal_rejects_symlinked_state(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, state_path, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    saved = tmp_path / "redirected-state.json"
    state_path.rename(saved)
    state_path.symlink_to(saved)
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert work.exists()


def test_s1_guarded_removal_rejects_extra_initialized_unselected_module(tmp_path, monkeypatch):
    import test_batch_submodules as fixture
    main, work, _, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch, count=2)
    extra = work / "libs/module1"
    fixture._git(main, "clone", "--no-hardlinks", str(main / "libs/module1"), str(extra))
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert (extra / ".git").exists()


def test_s1_guarded_removal_accepts_tracked_directory_symlink_without_following(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch, tracked_link=True)
    result = mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert result.returncode == 0, result.stderr
    assert not work.exists()
    assert (tmp_path / "external/precious").read_text() == "external data"


def test_s1_guarded_removal_rejects_staged_change_hidden_by_restored_worktree(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    path = work / "file.txt"
    head_bytes = path.read_bytes()
    path.write_text("valuable staged version\n")
    _git("add", "--", "file.txt", cwd=work)
    staged_oid = _git("rev-parse", ":file.txt", cwd=work)
    path.write_bytes(head_bytes)
    # The worktree equals HEAD, but the index contains distinct user work.
    assert _git("diff", "--name-only", "HEAD", cwd=work) == ""
    assert _git("diff", "--cached", "--name-only", "HEAD", cwd=work) == "file.txt"
    index = Path(_git("rev-parse", "--absolute-git-dir", cwd=work)) / "index"
    index_bytes = index.read_bytes()
    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert work.is_dir()
    assert index.read_bytes() == index_bytes
    assert _git("rev-parse", ":file.txt", cwd=work) == staged_oid
    assert path.read_bytes() == head_bytes


def _s1_reharvest_learnings(main, work, checkpoint, state_path, state):
    import copy
    import batch_state
    harvested = mod.harvest(str(main), str(work), "TASK__lead")
    batch_state.sync_harvest(str(main), harvested["archived"])
    checkpoint["harvest"] = harvested
    state["requests"][0]["checkpoint"] = copy.deepcopy(checkpoint)
    batch_state.write_json(str(state_path), state)
    return harvested


def test_s1_guarded_removal_accepts_exact_harvested_learning_lines(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, state_path, state, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    source = work / "doc/harness/learnings.jsonl"
    destination = main / "doc/harness/learnings.jsonl"
    existing = b'{"key": "already-present", "insight": "preserve spacing"}\n'
    added = b'{"key":"new","insight":"preserve bytes"}\n'
    destination.write_bytes(existing)
    source.write_bytes(existing + added)
    harvested = _s1_reharvest_learnings(main, work, checkpoint, state_path, state)
    assert harvested["learnings_appended"] == 1
    assert destination.read_bytes() == source.read_bytes()
    preserved = destination.read_bytes()
    result = mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert result.returncode == 0 and not work.exists()
    assert destination.read_bytes() == preserved


def test_s1_guarded_removal_retains_learning_bytes_omitted_by_harvest(tmp_path, monkeypatch):
    main, work, _, manifest, checkpoint, state_path, state, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    source = work / "doc/harness/learnings.jsonl"
    destination = main / "doc/harness/learnings.jsonl"
    valid = b'{"key":"kept","insight":"valid row"}\n'
    for omitted in (b'{malformed but valuable bytes}\n', b'["unique non-object learning"]\n'):
        original = valid + omitted
        source.write_bytes(original)
        _s1_reharvest_learnings(main, work, checkpoint, state_path, state)
        assert destination.read_bytes() == valid
        with _raises(mod.HarvestError, match="learning content not preserved"):
            mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
        assert work.is_dir() and source.read_bytes() == original
        assert destination.read_bytes() == valid


def pytest_generate_tests(metafunc):
    if "hidden_index_case" in metafunc.fixturenames:
        metafunc.parametrize("hidden_index_case", [
            ("outer", "--assume-unchanged"), ("outer", "--skip-worktree"),
            ("module", "--assume-unchanged"), ("module", "--skip-worktree"),
        ], ids=["outer-assumed", "outer-skipped", "module-assumed", "module-skipped"])


def test_s1_guarded_removal_retains_edits_hidden_by_index_flags(tmp_path, monkeypatch, hidden_index_case):
    main, work, module, manifest, checkpoint, _, _, _ = _s1_removal_fixture(tmp_path, monkeypatch)
    location, flag = hidden_index_case
    checkout = work if location == "outer" else work / module
    path = checkout / "file.txt"
    _git("update-index", flag, "--", "file.txt", cwd=checkout)
    precious = b"unique source hidden from ordinary Git dirt checks\n"
    path.write_bytes(precious)
    assert _git("status", "--porcelain", cwd=checkout) == ""
    index = Path(_git("rev-parse", "--absolute-git-dir", cwd=checkout)) / "index"
    original_index = index.read_bytes()
    remove_calls = []

    def refuse_unexpected_removal(*args):
        remove_calls.append(args)
        raise AssertionError("guard reached destructive removal with hidden source edits")

    with _raises(mod.HarvestError):
        mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint,
                              git_runner=refuse_unexpected_removal)
    assert remove_calls == []
    assert work.is_dir() and path.read_bytes() == precious
    assert index.read_bytes() == original_index


def test_s1_full_removal_supports_preexisting_annotated_module_tag(tmp_path, monkeypatch):
    main, work, module, manifest, checkpoint, _, _, tip = _s1_removal_fixture(
        tmp_path, monkeypatch, preexisting_tag=True)
    destination = main / module
    tag = _git("rev-parse", "refs/tags/preexisting-annotated", cwd=destination)
    assert _git("cat-file", "-t", tag, cwd=destination) == "tag"
    annotation = _git("cat-file", "-p", tag, cwd=destination)
    result = mod.remove_s1_worktree(str(main), str(work), "TASK__lead", manifest, checkpoint)
    assert result.returncode == 0 and not work.exists()
    assert not Path(manifest["admin_dir"]).exists()
    _git("reflog", "expire", "--expire=now", "--all", cwd=destination)
    _git("gc", "--prune=now", cwd=destination)
    assert _git("cat-file", "-p", tag, cwd=destination) == annotation
    assert _git("cat-file", "-t", tip, cwd=destination) == "commit"
    assert (main / "doc/harness/archive/batch/TASK__lead/TASK.json").is_file()


def _ordinary_snapshot_fixture(tmp_path):
    import test_batch_state as fixture
    repo = _main_repo(tmp_path)
    _ignore_task_evidence(repo)
    with (repo / '.gitignore').open('a') as handle:
        handle.write('doc/harness/checkpoints/\ndoc/harness/.watcher-diagnostics.json\n')
    _git('add', '.gitignore', cwd=repo)
    _git('commit', '-q', '-m', 'ignore runtime snapshots', cwd=repo)
    work = tmp_path / 'work'
    _add_worktree(repo, work, 'lead-snapshots')
    task = fixture.task(work, slug='snapshots', closed=True)
    sid = 'session-snapshots'
    sessions = task.parent / '.active_sessions'
    sessions.mkdir()
    (sessions / (sid + '.json')).write_text(json.dumps(dict(
        session_id=sid, task_dir=str(task), task_id=task.name,
        run_id=fixture.RUN, updated='2026-10-21T00:00:00Z')))
    diagnostics = work / 'doc/harness/.watcher-diagnostics.json'
    diagnostics.write_bytes(b'{"session_id":"session-snapshots", "updated":"2026-10-21T00:00:00Z", "registration_present":true}\n')
    checkpoint = work / 'doc/harness/checkpoints' / (task.name + '.md')
    checkpoint.parent.mkdir()
    checkpoint.write_text('# Checkpoint — ' + task.name + '\n\n- written: 2026-10-21T00:00:00Z\n\n## Task state\n\n- status: open\n\n## Progress\n\n- phase: implementation\n\n## Next action\n\nHistorical resume note\n')
    mod.harvest(str(repo), str(work), task.name)
    sidecar = repo / 'doc/harness/archive/worktree-recovery' / task.name / fixture.RUN
    return repo, work, task, diagnostics, checkpoint, sidecar


def test_ordinary_removal_preserves_typed_snapshots_exactly_and_idempotently(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    original = {p: p.read_bytes() for p in (diagnostics, checkpoint)}
    archive = repo / 'doc/harness/archive/batch' / task.name
    archived = mod._tree_entries(str(archive))
    for _ in range(2):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
        assert diagnostics.read_bytes() == original[diagnostics]
        assert checkpoint.read_bytes() == original[checkpoint]
        assert (sidecar / 'watcher-diagnostics.json').read_bytes() == original[diagnostics]
        assert (sidecar / 'checkpoint.md').read_bytes() == original[checkpoint]
        assert mod._tree_entries(str(archive)) == archived
    _git('worktree', 'remove', str(work), cwd=repo)
    assert not work.exists()
    assert (sidecar / 'checkpoint.md').read_bytes() == original[checkpoint]


def test_ordinary_snapshots_never_authorize_precious_ignored_content(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    precious = work / 'doc/harness/checkpoints/precious.bin'
    precious.write_bytes(b'unique user data')
    with _raises(mod.HarvestError, match='unknown ignored or untracked'):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    assert precious.read_bytes() == b'unique user data'
    assert checkpoint.exists() and diagnostics.exists()


def test_ordinary_snapshots_refuse_foreign_malformed_and_unsafe_inputs(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    raw = diagnostics.read_bytes()
    for replacement in (b'{}', raw.replace(b'session-snapshots', b'foreign/invalid'),
                        raw.replace(b'true', b'"true"'),
                        raw[:-2] + b',"unexpected":"precious"}\n',
                        raw[:-2] + b',"session_id":"session-snapshots"}\n'):
        diagnostics.write_bytes(replacement)
        with _raises(mod.HarvestError):
            mod.ordinary_removal_proof(str(repo), str(work), task.name)
        assert work.exists() and diagnostics.read_bytes() == replacement
        assert not sidecar.exists()
    diagnostics.write_bytes(raw)
    ckraw = checkpoint.read_bytes()
    checkpoint.write_bytes(ckraw.replace(task.name.encode(), b'TASK__foreign'))
    with _raises(mod.HarvestError, match='foreign task checkpoint'):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    checkpoint.write_bytes(ckraw)
    external = tmp_path / 'outside'
    external.write_bytes(raw)
    diagnostics.unlink()
    diagnostics.symlink_to(external)
    with _raises(mod.HarvestError):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    diagnostics.unlink()
    os.link(external, diagnostics)
    with _raises(mod.HarvestError):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    diagnostics.unlink()
    diagnostics.write_bytes(raw)
    diagnostics.chmod(0o666)
    with _raises(mod.HarvestError):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    assert external.read_bytes() == raw and not sidecar.exists()


def test_ordinary_snapshot_collisions_and_sidecar_links_never_overwrite(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    sidecar.mkdir(parents=True)
    destination = sidecar / 'watcher-diagnostics.json'
    destination.write_bytes(b'previous irreplaceable snapshot')
    with _raises(mod.HarvestError, match='collision'):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    assert destination.read_bytes() == b'previous irreplaceable snapshot'
    destination.unlink()
    outside = tmp_path / 'outside'
    outside.write_bytes(diagnostics.read_bytes())
    destination.symlink_to(outside)
    with _raises(mod.HarvestError):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    destination.unlink()
    os.link(outside, destination)
    with _raises(mod.HarvestError):
        mod.ordinary_removal_proof(str(repo), str(work), task.name)
    assert work.exists() and checkpoint.exists()


def test_ordinary_diagnostics_after_close_need_no_removed_session_marker(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    # task_close removes the owning session marker; diagnostics carry no task/run
    # and remain a nonattesting checkout advisory, never closed task evidence.
    sessions = task.parent / '.active_sessions'
    (sessions / 'session-snapshots.json').unlink()
    sessions.rmdir()
    raw = diagnostics.read_bytes()
    mod.ordinary_removal_proof(str(repo), str(work), task.name)
    _git('worktree', 'remove', str(work), cwd=repo)
    assert not work.exists()
    assert (sidecar / 'watcher-diagnostics.json').read_bytes() == raw


def test_partial_residue_requires_already_preserved_ancillary_bytes(tmp_path):
    repo, work, task, diagnostics, checkpoint, sidecar = _ordinary_snapshot_fixture(tmp_path)
    original = {str(p.relative_to(work)): p.read_bytes() for p in (diagnostics, checkpoint)}
    task_id = task.name
    mod.ordinary_removal_proof(str(repo), str(work), task_id)
    archive = repo / 'doc/harness/archive/batch' / task_id
    entries = mod._tree_entries(str(archive))
    tip = _git('rev-parse', 'HEAD', cwd=repo)
    proof = dict(stage='harvested', integrated_tip=tip, harvest={'archived': str(archive)},
                 archive_fingerprint='sha256:' + mod.hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest())
    _git('worktree', 'remove', str(work), cwd=repo)
    # Model a native interrupted removal with only typed ancillary remainder;
    # source task control can already be gone, so immutable archive is authority.
    for rel, raw in original.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    assert mod.ordinary_unregistered(str(repo), str(work))
    saved = (sidecar / 'watcher-diagnostics.json').read_bytes()
    (sidecar / 'watcher-diagnostics.json').unlink()
    with _raises(mod.HarvestError, match='not preserved'):
        mod.remove_ordinary_residue(str(repo), str(work), task_id, tip, proof)
    assert not (sidecar / 'watcher-diagnostics.json').exists()
    (sidecar / 'watcher-diagnostics.json').write_bytes(saved)
    diagnostics.write_bytes(original[str(diagnostics.relative_to(work))] + b' ')
    with _raises(mod.HarvestError, match='collision'):
        mod.remove_ordinary_residue(str(repo), str(work), task_id, tip, proof)
    diagnostics.write_bytes(original[str(diagnostics.relative_to(work))])
    precious = checkpoint.parent / 'precious.bin'
    precious.write_bytes(b'unique ignored user bytes')
    with _raises(mod.HarvestError, match='unpreserved'):
        mod.remove_ordinary_residue(str(repo), str(work), task_id, tip, proof)
    assert precious.read_bytes() == b'unique ignored user bytes'
    precious.unlink()
    mod.remove_ordinary_residue(str(repo), str(work), task_id, tip, proof)
    assert not work.exists()
    assert (sidecar / 'watcher-diagnostics.json').read_bytes() == saved
    assert (sidecar / 'checkpoint.md').read_bytes() == original[str(checkpoint.relative_to(work))]
    assert mod._tree_entries(str(archive)) == entries
    # Files may disappear before an interrupted removal leaves their known
    # empty checkpoint parent. Already-preserved typed copies still account for it.
    checkpoint.parent.mkdir(parents=True)
    mod.remove_ordinary_residue(str(repo), str(work), task_id, tip, proof)
    assert not work.exists()
    assert (sidecar / 'watcher-diagnostics.json').read_bytes() == saved
