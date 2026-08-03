from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import threading
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location("review_gate_lib", ROOT / "plugin/scripts/_lib.py")
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path, touched: list[str], project_type: str = "library") -> Path:
    if not (tmp_path / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.email", "review@test"], cwd=tmp_path, check=True)
        subprocess.run(["git", "config", "user.name", "Review Test"], cwd=tmp_path, check=True)
        (tmp_path / ".gitignore").write_text("doc/harness/tasks/\n", encoding="utf-8")
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(f"type: {project_type}\nqa:\n  browser_qa_supported: false\n", encoding="utf-8")
    task = tmp_path / "doc/harness/tasks/TASK__review"
    task.mkdir(parents=True)
    touched_yaml = "[]" if not touched else "\n" + "\n".join(f"  - {path}" for path in touched)
    (task / "TASK_STATE.yaml").write_text(
        "task_id: TASK__review\nstatus: created\nruntime_verdict: pending\n"
        f"touched_paths: {touched_yaml}\nplan_session_state: closed\nclosed_at: null\nupdated: now\n",
        encoding="utf-8",
    )
    (task / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    staged = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=tmp_path)
    if staged.returncode != 0:
        subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=tmp_path, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    (task / "TASK_BASELINE.json").write_text(
        json.dumps({
            "version": 1, "repo_root": str(tmp_path),
            "head_sha": head, "dirty_paths": {},
        }) + "\n",
        encoding="utf-8",
    )
    return task


def _record(
    task: Path,
    agent_type: str,
    agent_id: str,
    status: str,
    verdict: str = "",
    head_sha: str | None = None,
):
    receipt = {
        "source": "test_hook",
        "agent_type": agent_type,
        "agent_id": agent_id,
        "status": status,
        "verdict": verdict,
        "summary": (
            f"VERDICT: {verdict}\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
            if verdict else "started"
        ),
    }
    if head_sha is not None:
        receipt["head_sha"] = head_sha
    return lib.record_subagent_receipt(task, receipt)


def test_review_snapshot_scope_deduplicates_and_refreshes_fingerprints(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    original_paths = lib._reviewable_source_paths
    original_fingerprint = lib._fingerprint_path
    calls = 0

    def counted_fingerprint(repo_root, relpath):
        nonlocal calls
        calls += 1
        return original_fingerprint(repo_root, relpath)

    lib._reviewable_source_paths = lambda task_dir, state=None: ["src/main.py"]
    lib._fingerprint_path = counted_fingerprint
    try:
        with lib.review_snapshot_scope():
            first = lib.review_diff_fingerprint(task)
            assert lib.review_diff_fingerprint(task) == first
            assert calls == 2
            lib.refresh_review_snapshot()
            assert lib.review_diff_fingerprint(task) == first
            assert calls == 4

        try:
            with lib.review_snapshot_scope():
                lib.review_diff_fingerprint(task)
                raise RuntimeError("scope reset probe")
        except RuntimeError:
            pass

        with lib.review_snapshot_scope():
            assert lib.review_diff_fingerprint(task) == first
        assert calls == 8
    finally:
        lib._reviewable_source_paths = original_paths
        lib._fingerprint_path = original_fingerprint


def test_review_snapshot_scope_is_isolated_between_threads(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    original_paths = lib._reviewable_source_paths
    original_fingerprint = lib._fingerprint_path
    calls = 0
    lock = threading.Lock()

    def counted_fingerprint(repo_root, relpath):
        nonlocal calls
        with lock:
            calls += 1
        return original_fingerprint(repo_root, relpath)

    def fingerprint_twice():
        with lib.review_snapshot_scope():
            first = lib.review_diff_fingerprint(task)
            return first, lib.review_diff_fingerprint(task)

    lib._reviewable_source_paths = lambda task_dir, state=None: ["src/main.py"]
    lib._fingerprint_path = counted_fingerprint
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: fingerprint_twice(), range(2)))
        assert all(first == second for first, second in results)
        assert calls == 4
    finally:
        lib._reviewable_source_paths = original_paths
        lib._fingerprint_path = original_fingerprint


def test_git_changed_path_snapshot_fails_closed_on_command_error(tmp_path):
    from types import SimpleNamespace
    from unittest import mock

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    with mock.patch.object(
        lib.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")
    ):
        try:
            lib._uncached_git_changed_paths(str(tmp_path))
        except RuntimeError as exc:
            assert "snapshot unavailable" in str(exc)
        else:
            raise AssertionError("Git command failure must not produce an empty snapshot")


def test_git_failure_stays_fatal_if_metadata_disappears_mid_request(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=tmp_path, check=True)
    (tmp_path / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    head = tmp_path / ".git/HEAD"
    hidden = tmp_path / ".git/HEAD.hidden"

    with lib.review_snapshot_scope():
        assert lib._uncached_git_changed_paths(str(tmp_path)) == set()
        head.rename(hidden)
        try:
            lib._uncached_git_changed_paths(str(tmp_path))
        except RuntimeError as exc:
            assert "snapshot unavailable" in str(exc)
        else:
            raise AssertionError("A known Git root must not become a fake empty snapshot")
        finally:
            hidden.rename(head)


def test_git_submodule_snapshot_fails_closed_on_command_error(tmp_path):
    from types import SimpleNamespace
    from unittest import mock

    (tmp_path / ".git").mkdir()
    (tmp_path / ".git/HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    with mock.patch.object(
        lib.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="", stderr="boom")
    ):
        try:
            lib._initialized_submodule_paths(str(tmp_path))
        except RuntimeError as exc:
            assert "submodule snapshot unavailable" in str(exc)
        else:
            raise AssertionError("Git submodule failure must not produce an empty snapshot")


def test_initialized_submodule_snapshot_does_not_require_valid_gitmodules(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=tmp_path, check=True)
    submodule = tmp_path / "gstack"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=submodule, check=True)
    (submodule / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=submodule, check=True)
    subprocess.run(["git", "commit", "-qm", "submodule"], cwd=submodule, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},gstack"],
        cwd=tmp_path, check=True,
    )
    (tmp_path / ".gitmodules").write_text(
        '[submodule "gstack"]\n\tpath = gstack\n', encoding="utf-8",
    )

    assert lib._initialized_submodule_paths(str(tmp_path)) == ["gstack"]


def test_initialized_submodule_snapshot_rejects_symlink_worktree(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    submodule = tmp_path / "gstack"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=submodule, check=True)
    (submodule / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=submodule, check=True)
    subprocess.run(["git", "commit", "-qm", "submodule"], cwd=submodule, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},gstack"],
        cwd=tmp_path, check=True,
    )
    submodule.rename(tmp_path / "gstack-real")
    submodule.symlink_to("gstack-real", target_is_directory=True)

    try:
        lib._initialized_submodule_paths(str(tmp_path))
    except RuntimeError as exc:
        assert "submodule snapshot unavailable" in str(exc)
    else:
        raise AssertionError("A symlinked gitlink worktree must fail closed")


def test_initialized_submodule_snapshot_rejects_external_gitdir(tmp_path):
    source = tmp_path / "source"
    external = tmp_path / "external"
    parent = tmp_path / "parent"
    for repo in (source, external, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    for repo, value in ((source, "source"), (external, "external")):
        (repo / "tracked.py").write_text(f"{value}\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", value], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub"],
        cwd=parent, check=True,
    )
    (parent / "sub/.git").write_text(
        f"gitdir: {external / '.git'}\n", encoding="utf-8",
    )

    try:
        lib._initialized_submodule_paths(str(parent))
    except RuntimeError as exc:
        assert "submodule snapshot unavailable" in str(exc)
    else:
        raise AssertionError("An external submodule gitdir must fail closed")


def test_registered_direct_linked_worktree_passes_but_unregistered_traversal_fails(tmp_path):
    service_repo = tmp_path / "service-repo"
    parent = tmp_path / "parent"
    for repo in (service_repo, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    (service_repo / "tracked.py").write_text("service\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=service_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "service"], cwd=service_repo, check=True)
    service = parent / "service"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(service), "HEAD"],
        cwd=service_repo,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},service"],
        cwd=parent,
        check=True,
    )

    assert lib._registered_source_metadata_binding(
        str(parent), str(service), "service"
    )
    snapshot = lib._gitlink_index_snapshot(
        str(parent), registered_leaves=("service",)
    )
    assert snapshot["service"] == (head, True)

    try:
        lib._initialized_submodule_paths(str(parent))
    except RuntimeError as exc:
        assert "submodule snapshot unavailable" in str(exc)
    else:
        raise AssertionError("The same external checkout must fail without registration")


def test_registered_linked_worktree_rejects_wrong_admin_backreference(tmp_path):
    service_repo = tmp_path / "service-repo"
    parent = tmp_path / "parent"
    for repo in (service_repo, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    (service_repo / "tracked.py").write_text("service\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=service_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "service"], cwd=service_repo, check=True)
    service = parent / "service"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(service), "HEAD"],
        cwd=service_repo,
        check=True,
    )
    gitdir = Path((service / ".git").read_text(encoding="utf-8").strip()[len("gitdir: "):])
    (gitdir / "gitdir").write_text(str(parent / "wrong/.git") + "\n", encoding="utf-8")

    try:
        lib._registered_source_metadata_binding(str(parent), str(service), "service")
    except lib.GitBindingError as exc:
        assert exc.code == "REGISTERED_WORKTREE_BINDING_MISMATCH"
        assert exc.invariant == "admin_gitdir_backreference"
    else:
        raise AssertionError("A forged admin backreference must fail closed")


def test_nested_submodule_snapshot_rejects_external_gitdir(tmp_path):
    inner_source = tmp_path / "inner-source"
    outer_source = tmp_path / "outer-source"
    external = tmp_path / "external"
    parent = tmp_path / "parent"
    for repo in (inner_source, outer_source, external, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    for repo in (inner_source, external):
        (repo / "tracked.py").write_text(f"{repo.name}\n", encoding="utf-8")
        subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-qm", repo.name], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(inner_source), "nested"],
        cwd=outer_source, check=True,
    )
    subprocess.run(["git", "commit", "-qm", "outer"], cwd=outer_source, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(outer_source), "outer"],
        cwd=parent, check=True,
    )
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "update", "--init", "--recursive"],
        cwd=parent, check=True,
    )
    (parent / "outer/nested/.git").write_text(
        f"gitdir: {external / '.git'}\n", encoding="utf-8",
    )

    try:
        lib._initialized_submodule_paths(str(parent))
    except RuntimeError as exc:
        assert "submodule snapshot unavailable" in str(exc)
    else:
        raise AssertionError("A nested external submodule gitdir must fail closed")


def test_submodule_worktree_binding_is_rechecked_after_gitdir_retarget(tmp_path):
    source = tmp_path / "source"
    parent = tmp_path / "parent"
    external_worktree = tmp_path / "external-worktree"
    for repo in (source, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    (source / "tracked.py").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub"],
        cwd=parent, check=True,
    )
    git_file = parent / "sub/.git"
    alternate = parent / ".git/modules/alternate"
    subprocess.run(["git", "init", "--bare", "-q", str(alternate)], check=True)
    external_worktree.mkdir()
    subprocess.run(
        ["git", f"--git-dir={alternate}", "config", "core.bare", "false"], check=True,
    )
    subprocess.run(
        ["git", f"--git-dir={alternate}", "config", "core.worktree", str(external_worktree)],
        check=True,
    )

    with lib.review_snapshot_scope():
        assert lib._initialized_submodule_paths(str(parent)) == ["sub"]
        git_file.write_text("gitdir: ../.git/modules/alternate\n", encoding="utf-8")
        try:
            lib._validated_submodule_root(str(parent), "sub")
        except RuntimeError as exc:
            assert "submodule snapshot unavailable" in str(exc)
        else:
            raise AssertionError("Retargeted gitdir worktree binding must be rechecked")


def test_submodule_head_is_bound_to_validated_gitdir(tmp_path, monkeypatch):
    source = tmp_path / "source"
    parent = tmp_path / "parent"
    for repo in (source, parent):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    (source / "tracked.py").write_text("source\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "source"], cwd=source, check=True)
    subprocess.run(
        ["git", "-c", "protocol.file.allow=always", "submodule", "add", "-q", str(source), "sub"],
        cwd=parent, check=True,
    )
    sub_root = parent / "sub"
    git_file = sub_root / ".git"
    alternate = parent / ".git/modules/alternate"
    subprocess.run(["git", "init", "--bare", "-q", str(alternate)], check=True)
    subprocess.run(
        ["git", f"--git-dir={alternate}", "config", "core.bare", "false"], check=True,
    )
    subprocess.run(
        ["git", f"--git-dir={alternate}", "config", "core.worktree", str(sub_root)], check=True,
    )
    original_head = lib._git_head_snapshot
    observed = {}

    def retarget_during_head(repo_root, *, git_dir=None, use_cache=True):
        observed.update(git_dir=git_dir, use_cache=use_cache)
        git_file.write_text("gitdir: ../.git/modules/alternate\n", encoding="utf-8")
        return original_head(repo_root, git_dir=git_dir, use_cache=use_cache)

    monkeypatch.setattr(lib, "_git_head_snapshot", retarget_during_head)
    try:
        lib._submodule_gitlink_fingerprint(str(parent), "sub")
    except RuntimeError as exc:
        assert "submodule snapshot unavailable" in str(exc)
    else:
        raise AssertionError("Submodule HEAD must remain bound to one gitdir")
    assert observed["git_dir"]
    assert observed["use_cache"] is False


def test_review_fingerprint_changes_with_clean_submodule_head(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=tmp_path, check=True)
    submodule = tmp_path / "gstack"
    submodule.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=submodule, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=submodule, check=True)
    source = submodule / "tracked.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=submodule, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=submodule, check=True)
    first_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{first_head},gstack"],
        cwd=tmp_path, check=True,
    )
    subprocess.run(["git", "commit", "-qm", "parent"], cwd=tmp_path, check=True)
    task = _task(tmp_path, ["gstack"])
    first = lib.review_diff_fingerprint(str(task))

    source.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "second"], cwd=submodule, check=True)
    second = lib.review_diff_fingerprint(str(task))

    assert first != second

    second_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=submodule, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(["git", "checkout", "-q", first_head], cwd=submodule, check=True)
    checkout_reset = lib.review_diff_fingerprint(str(task))
    assert checkout_reset == first
    subprocess.run(
        ["git", "update-index", "--cacheinfo", f"160000,{second_head},gstack"],
        cwd=tmp_path, check=True,
    )
    staged_gitlink = lib.review_diff_fingerprint(str(task))
    assert staged_gitlink != first


def test_uninitialized_gitlink_index_change_invalidates_review(tmp_path):
    source_repo = tmp_path / "source"
    parent = tmp_path / "parent"
    source_repo.mkdir()
    parent.mkdir()
    for repo in (source_repo, parent):
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "paths@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=repo, check=True)
    source = source_repo / "tracked.py"
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "first"], cwd=source_repo, check=True)
    first_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    source.write_text("VALUE = 2\n", encoding="utf-8")
    subprocess.run(["git", "commit", "-qam", "second"], cwd=source_repo, check=True)
    second_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source_repo, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{first_head},ghost-sub"],
        cwd=parent, check=True,
    )
    subprocess.run(["git", "commit", "-qm", "parent"], cwd=parent, check=True)
    task = _task(parent, ["ghost-sub"])
    first = lib.review_diff_fingerprint(str(task))

    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{second_head},ghost-sub"],
        cwd=parent, check=True,
    )

    assert lib._initialized_submodule_paths(str(parent)) == []
    assert lib.review_diff_fingerprint(str(task)) != first


def test_git_changed_paths_preserve_newline_filename(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "paths@test"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "Paths Test"], cwd=tmp_path, check=True)
    source = tmp_path / "src" / "line\nbreak.py"
    source.parent.mkdir()
    source.write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "src"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=tmp_path, check=True)
    source.write_text("VALUE = 2\n", encoding="utf-8")

    assert lib._uncached_git_changed_paths(str(tmp_path)) == {"src/line\nbreak.py"}


def test_changed_path_fingerprint_rejects_fifo_and_hashes_symlink_target(tmp_path):
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    try:
        lib._fingerprint_path(str(tmp_path), "pipe")
    except RuntimeError as exc:
        assert "fingerprint unavailable" in str(exc)
    else:
        raise AssertionError("FIFO fingerprinting must fail closed")

    link = tmp_path / "link"
    link.symlink_to("target-one")
    first = lib._fingerprint_path(str(tmp_path), "link")
    link.unlink()
    link.symlink_to("target-two")
    second = lib._fingerprint_path(str(tmp_path), "link")
    assert first.startswith("symlink-sha256:")
    assert first != second


def test_changed_path_fingerprint_rejects_rename_replacement(tmp_path, monkeypatch):
    target = tmp_path / "source.py"
    replacement = tmp_path / "replacement.py"
    target.write_text("VALUE = 1\n", encoding="utf-8")
    replacement.write_text("VALUE = 2\n", encoding="utf-8")
    original_fstat = os.fstat
    calls = 0

    def replace_after_read(fd):
        nonlocal calls
        result = original_fstat(fd)
        calls += 1
        if calls == 2:
            os.replace(replacement, target)
        return result

    monkeypatch.setattr(os, "fstat", replace_after_read)
    try:
        lib._fingerprint_path(str(tmp_path), "source.py")
    except RuntimeError as exc:
        assert "fingerprint unavailable" in str(exc)
    else:
        raise AssertionError("A path replacement during hashing must fail closed")


def test_receipt_fingerprint_rejects_rename_replacement(tmp_path, monkeypatch):
    task = tmp_path / "task"
    task.mkdir()
    target = task / lib.REVIEW_RECEIPTS_NAME
    replacement = task / "replacement.jsonl"
    target.write_text('{"verdict":"PASS"}\n', encoding="utf-8")
    replacement.write_text('{"verdict":"FAIL"}\n', encoding="utf-8")
    original_fstat = os.fstat
    calls = 0

    def replace_after_read(fd):
        nonlocal calls
        result = original_fstat(fd)
        calls += 1
        if calls == 2:
            os.replace(replacement, target)
        return result

    monkeypatch.setattr(os, "fstat", replace_after_read)
    try:
        lib.receipt_stream_fingerprint(str(task))
    except RuntimeError as exc:
        assert "receipt stream snapshot unavailable" in str(exc)
    else:
        raise AssertionError("A receipt replacement during hashing must fail closed")


def test_receipt_append_rejects_symlink_without_touching_target(tmp_path):
    task = tmp_path / "TASK__receipt-symlink-write"
    task.mkdir()
    external = tmp_path / "external.jsonl"
    external.write_text("keep\n", encoding="utf-8")
    (task / lib.SUBAGENT_RECEIPTS_NAME).symlink_to(external)

    try:
        lib.record_subagent_receipt(task, {
            "agent_id": "agent-1",
            "agent_type": "qa-cli",
            "status": "started",
        })
    except RuntimeError as exc:
        assert "integrity unavailable" in str(exc)
    else:
        raise AssertionError("symlink receipt append must fail closed")
    assert external.read_text(encoding="utf-8") == "keep\n"


def test_receipt_read_rejects_symlink(tmp_path):
    task = tmp_path / "TASK__receipt-symlink-read"
    task.mkdir()
    external = tmp_path / "external.jsonl"
    external.write_text("{}\n", encoding="utf-8")
    (task / lib.REVIEW_RECEIPTS_NAME).symlink_to(external)

    try:
        lib.list_review_receipts(task)
    except RuntimeError as exc:
        assert "integrity unavailable" in str(exc)
    else:
        raise AssertionError("symlink receipt read must fail closed")


def test_receipt_read_rejects_malformed_nonempty_record(tmp_path):
    task = tmp_path / "TASK__receipt-malformed"
    task.mkdir()
    path = task / lib.REVIEW_RECEIPTS_NAME
    path.write_text(
        '{"kind":"review","verdict":"PASS"}\n{"kind":',
        encoding="utf-8",
    )

    try:
        lib.list_review_receipts(task)
    except RuntimeError as exc:
        assert "integrity unavailable" in str(exc)
    else:
        raise AssertionError("malformed receipt records must fail closed")


def test_receipt_write_rejects_symlinked_task_directory(tmp_path):
    tasks = tmp_path / "doc/harness/tasks"
    tasks.mkdir(parents=True)
    external = tmp_path / "external-task"
    external.mkdir()
    linked = tasks / "TASK__linked"
    linked.symlink_to(external, target_is_directory=True)

    try:
        lib.record_subagent_receipt(linked, {
            "agent_id": "agent-linked",
            "agent_type": "qa-cli",
            "status": "started",
        })
    except RuntimeError as exc:
        assert "integrity unavailable" in str(exc)
    else:
        raise AssertionError("symlinked receipt ancestors must fail closed")
    assert not (external / lib.SUBAGENT_RECEIPTS_NAME).exists()


def test_git_path_map_keeps_backslash_and_slash_names_distinct(tmp_path):
    backslash = tmp_path / "a\\b"
    nested = tmp_path / "a" / "b"
    nested.parent.mkdir()
    backslash.write_text("one\n", encoding="utf-8")
    nested.write_text("two\n", encoding="utf-8")

    paths = {"a\\b", "a/b"}
    fingerprints = {
        path: lib._fingerprint_path(str(tmp_path), path) for path in paths
    }
    assert set(fingerprints) == paths
    assert fingerprints["a\\b"] != fingerprints["a/b"]


def test_review_paths_preserve_posix_backslash_identity(tmp_path):
    if os.sep != "/":
        return
    backslash = tmp_path / "a\\b.py"
    nested = tmp_path / "a" / "b.py"
    hidden = tmp_path / ".hidden.py"
    nested.parent.mkdir()
    backslash.write_text("VALUE = 1\n", encoding="utf-8")
    nested.write_text("VALUE = 2\n", encoding="utf-8")
    hidden.write_text("VALUE = 3\n", encoding="utf-8")
    task = _task(tmp_path, ["a\\b.py", "a/b.py", ".hidden.py"])

    backslash.write_text("VALUE = 4\n", encoding="utf-8")
    hidden.write_text("VALUE = 5\n", encoding="utf-8")
    assert set(lib._reviewable_source_paths(task)) == {"a\\b.py", "a/b.py", ".hidden.py"}
    assert not lib._stale_skip("doc\\harness\\payload.py")


def test_docs_only_task_has_explicit_review_exemption(tmp_path):
    task = _task(tmp_path, ["doc/designs/change.md"])
    assert lib.required_review_lenses(task) == []
    assert lib.receipt_review_verdict(task) == "NOT_APPLICABLE"


def test_completion_fingerprint_covers_nonreviewable_contract_markdown(tmp_path):
    contract = tmp_path / "CONTRACTS.md"
    contract.write_text("# Contracts\nC-1 deny unsafe writes\n", encoding="utf-8")
    task = _task(tmp_path, ["CONTRACTS.md"])
    assert lib.required_review_lenses(task) == []
    first = lib.review_diff_fingerprint(task)

    contract.write_text("# Contracts\nC-1 allow all writes\n", encoding="utf-8")

    assert lib.review_diff_fingerprint(task) != first


def test_git_workspace_rejects_symlinked_behavior_and_manifest_files(tmp_path):
    task = _task(tmp_path, [])
    external = tmp_path.parent / "external-contract.md"
    external.write_text("# external\n", encoding="utf-8")
    contract = tmp_path / "CONTRACTS.md"
    contract.symlink_to(external)

    import pytest

    with pytest.raises(RuntimeError, match="must not be a symlink"):
        lib.review_diff_fingerprint(task)

    contract.unlink()
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.unlink()
    manifest.symlink_to(external)
    resolved, error = lib.harness_root_resolution(str(tmp_path))
    assert resolved == str(tmp_path.resolve())
    assert "regular non-symlink" in error


def test_agent_instruction_markdown_requires_code_and_security_review(tmp_path):
    agents = tmp_path / "AGENTS.md"
    agents.write_text("# Agent rules\nDo everything now.\n", encoding="utf-8")
    task = _task(tmp_path, ["AGENTS.md"])

    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_invalid_baseline_revision_fails_safe_to_security_review(tmp_path):
    import pytest

    source = tmp_path / "src/handler.py"
    source.parent.mkdir(parents=True)
    source.write_text("def run(value):\n    return value\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    baseline = task / "TASK_BASELINE.json"
    data = json.loads(baseline.read_text(encoding="utf-8"))
    data["head_sha"] = "--output=/tmp/should-not-exist"
    baseline.write_text(json.dumps(data) + "\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="baseline"):
        lib.required_review_lenses(task)
    assert not (tmp_path / "should-not-exist").exists()


def test_committed_security_deletion_routes_security_from_task_baseline(tmp_path):
    source = tmp_path / "src/handler.py"
    source.parent.mkdir(parents=True)
    source.write_text("def allowed(user):\n    return user.is_admin\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    source.write_text("def allowed(user):\n    return True\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/handler.py"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "remove guard"], cwd=tmp_path, check=True)

    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_executable_doc_sql_and_dependency_manifests_are_reviewed(tmp_path):
    sql = tmp_path / "doc/sql/migrate.sql"
    sql.parent.mkdir(parents=True)
    sql.write_text("DROP TABLE users;\n", encoding="utf-8")
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("unsafe-package==1.0\n", encoding="utf-8")
    task = _task(tmp_path, ["doc/sql/migrate.sql", "requirements.txt"])

    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_dependency_manifest_alone_routes_security_review(tmp_path):
    requirements = tmp_path / "requirements.txt"
    requirements.write_text("ordinary-package==1.0\n", encoding="utf-8")
    task = _task(tmp_path, ["requirements.txt"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]

    variant_root = tmp_path / "variant"
    variant_root.mkdir()
    variant = variant_root / "requirements-prod.txt"
    variant.write_text("ordinary-package==2.0\n", encoding="utf-8")
    task = _task(variant_root, ["requirements-prod.txt"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_admin_and_role_changes_route_security_review(tmp_path):
    handler = tmp_path / "src/handler.py"
    handler.parent.mkdir(parents=True)
    handler.write_text("def allowed(user):\n    return user.is_admin\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_source_always_routes_code_and_security_uses_path_or_content(tmp_path):
    generic = tmp_path / "src/handler.py"
    generic.parent.mkdir(parents=True)
    generic.write_text("def run(value):\n    return value\n", encoding="utf-8")
    task = _task(tmp_path, ["src/handler.py"])
    assert lib.required_review_lenses(task) == ["review-code"]

    generic.write_text("def run(user_token):\n    return user_token\n", encoding="utf-8")
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_security_router_scans_large_files_and_common_tls_configuration(tmp_path):
    generic = tmp_path / "src/client.py"
    generic.parent.mkdir(parents=True)
    generic.write_text(("# padding\n" * 25000) + "verify=False\n", encoding="utf-8")
    task = _task(tmp_path, ["src/client.py"])
    assert lib.required_review_lenses(task) == ["review-code", "review-security"]


def test_verdict_must_be_unique_and_exactly_on_first_line():
    assert lib.extract_qa_verdict("VERDICT: PASS\n12 tests passed") == "PASS"
    assert lib.extract_qa_verdict("preamble\nVERDICT: PASS") == ""
    assert lib.extract_qa_verdict("**VERDICT: PASS**") == ""
    assert lib.extract_qa_verdict("VERDICT: PASS\nVERDICT: FAIL") == ""


def test_review_completion_without_structured_finding_counts_stays_pending(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    _record(task, "harness:code-reviewer", "review", "started")
    lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS",
    })
    assert lib.receipt_review_verdict(task) == "PENDING"


def test_review_verdict_and_finding_counts_must_be_consistent(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])
    _record(task, "harness:code-reviewer", "review", "started")
    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0",
    })
    assert entry["verdict"] == "PENDING"

    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review-conflict", "status": "completed", "verdict": "PASS",
        "summary": (
            "VERDICT: PASS\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0\n"
            "FINDING_COUNTS: FIX_NOW=1 INVESTIGATE=0 OPTIONAL=0"
        ),
    })
    assert entry["verdict"] == "PENDING"

    for verdict, counts in (
        ("FAIL", "FIX_NOW=0 INVESTIGATE=0 OPTIONAL=1"),
        ("BLOCKED_ENV", "FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"),
    ):
        entry = lib.record_subagent_receipt(task, {
            "source": "test_hook", "agent_type": "harness:code-reviewer",
            "agent_id": f"review-{verdict}", "status": "completed", "verdict": verdict,
            "summary": f"VERDICT: {verdict}\nFINDING_COUNTS: {counts}",
        })
        assert entry["verdict"] == "PENDING"

    entry = lib.record_subagent_receipt(task, {
        "source": "test_hook", "agent_type": "harness:code-reviewer",
        "agent_id": "review-third-line", "status": "completed", "verdict": "PASS",
        "summary": "VERDICT: PASS\npreamble\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0",
    })
    assert entry["verdict"] == "PENDING"


def test_review_requires_correlated_start_and_current_diff(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:code-reviewer", "review-1", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-2", "started")
    source.write_text("VALUE = 2\n", encoding="utf-8")
    _record(task, "harness:code-reviewer", "review-2", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-3", "started")
    _record(task, "harness:code-reviewer", "review-3", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"

    completed = lib.list_review_receipts(task)[-1]
    assert completed["event"] == "review_completed"
    assert completed["base_sha"] == completed["head_sha"]
    assert completed["finished_at"]
    assert completed["finding_counts"] == {"fix_now": 0, "investigate": 0, "optional": 0}


def test_review_requires_correlated_and_current_head(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:code-reviewer", "review-old", "started", head_sha="old-head")
    _record(task, "harness:code-reviewer", "review-old", "completed", "PASS", head_sha="old-head")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-mixed", "started", head_sha="old-head")
    _record(task, "harness:code-reviewer", "review-mixed", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"

    _record(task, "harness:code-reviewer", "review-current", "started")
    _record(task, "harness:code-reviewer", "review-current", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"


def test_security_lens_is_separate_and_required_when_routed(tmp_path):
    source = tmp_path / "src/auth.py"
    source.parent.mkdir(parents=True)
    source.write_text("def authorize():\n    return True\n", encoding="utf-8")
    task = _task(tmp_path, ["src/auth.py"])
    _record(task, "harness:code-reviewer", "code", "started")
    _record(task, "harness:code-reviewer", "code", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PENDING"
    _record(task, "harness:security-reviewer", "security", "started")
    _record(task, "harness:security-reviewer", "security", "completed", "PASS")
    assert lib.receipt_review_verdict(task) == "PASS"


def test_qa_must_start_after_latest_review_pass(tmp_path):
    source = tmp_path / "src/main.py"
    source.parent.mkdir(parents=True)
    source.write_text("VALUE = 1\n", encoding="utf-8")
    task = _task(tmp_path, ["src/main.py"])

    _record(task, "harness:qa-cli", "qa-old", "started")
    _record(task, "harness:qa-cli", "qa-old", "completed", "PASS")
    _record(task, "harness:code-reviewer", "review", "started")
    _record(task, "harness:code-reviewer", "review", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PENDING"

    _record(task, "harness:qa-cli", "qa-new", "started")
    _record(task, "harness:qa-cli", "qa-new", "completed", "PASS")
    assert lib.receipt_runtime_verdict(task) == "PASS"
