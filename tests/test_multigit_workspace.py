from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / "plugin" / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _load(name: str, directory: Path = SCRIPTS):
    path = directory / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"{name}_multigit_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


lib = _load("_lib")
harness_server = _load("harness_server", REPO / "plugin" / "mcp")


def _git_repo(path: Path, filename: str) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=path, check=True)
    (path / filename).write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", filename], cwd=path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=path, check=True)


def _workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    root = tmp_path / "workspace"
    api = root / "pay-api"
    web = root / "pay-webapp"
    _git_repo(api, "api.py")
    _git_repo(web, "web.js")
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "version: 5\n"
        "name: pay\n"
        "type: saas\n"
        "source_git_roots: [pay-api, pay-webapp]\n"
        "test_command: echo ok\n"
        "verify_commands: [echo ok]\n"
        "qa:\n"
        "  browser_qa_supported: false\n",
        encoding="utf-8",
    )
    return root, api, web


def _git_backed_linked_workspace(tmp_path: Path) -> tuple[Path, Path, Path]:
    parent = tmp_path / "parent"
    service_repo = tmp_path / "service-repo"
    _git_repo(parent, "parent.txt")
    _git_repo(service_repo, "service.py")
    service = parent / "services/front"
    service.parent.mkdir()
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(service), "HEAD"],
        cwd=service_repo,
        check=True,
    )
    service_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{service_head},services/front"],
        cwd=parent,
        check=True,
    )
    manifest = parent / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "version: 5\n"
        "name: linked\n"
        "type: library\n"
        "source_git_roots: [services/front]\n"
        "test_command: echo ok\n"
        "verify_commands: [echo ok]\n"
        "qa:\n"
        "  browser_qa_supported: false\n",
        encoding="utf-8",
    )
    return parent, service_repo, service


def test_git_backed_source_roots_are_additive_and_linked_worktree_is_scanned_once(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)

    bindings = lib.configured_source_git_roots(str(parent))
    assert [(prefix, Path(root)) for prefix, root in bindings] == [
        ("", parent.resolve()),
        ("services/front/", service.resolve()),
    ]

    task = parent / "doc/harness/tasks/TASK__linked"
    lib.ensure_task_scaffold(str(task), "TASK__linked")
    baseline = json.loads((task / "TASK_BASELINE.json").read_text(encoding="utf-8"))
    assert set(baseline["source_heads"]) == {"", "services/front/"}
    registered_fp = baseline["dirty_paths"]["services/front"]
    assert ":registered-source:checkout:" in registered_fp
    assert ":worktree:" in registered_fp

    (service / "service.py").write_text("changed\n", encoding="utf-8")
    original = lib._uncached_git_changed_paths
    calls: list[str] = []

    def counted(root, **kwargs):
        calls.append(str(Path(root).resolve()))
        return original(root, **kwargs)

    with mock.patch.object(lib, "_uncached_git_changed_paths", side_effect=counted):
        assert "services/front/service.py" in lib._workspace_git_changed_paths(str(parent))
    assert calls.count(str(service.resolve())) == 1

    prefix, root, inner = lib._workspace_path_binding_with_prefix(
        str(parent), "services/front/service.py"
    )
    assert (prefix, Path(root), inner) == (
        "services/front/", service.resolve(), "service.py"
    )


def test_git_backed_source_root_must_be_direct_parent_gitlink(tmp_path):
    parent = tmp_path / "parent"
    nested = parent / "nested"
    _git_repo(parent, "parent.txt")
    _git_repo(nested, "nested.py")
    manifest = parent / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text(
        "version: 5\nsource_git_roots: [nested]\n", encoding="utf-8"
    )

    try:
        lib.configured_source_git_roots(str(parent))
    except lib.GitBindingError as exc:
        assert exc.code == "REGISTERED_SOURCE_NOT_DIRECT_GITLINK"
        assert exc.path == "nested"
    else:
        raise AssertionError("An arbitrary nested repository must not be registered")


def test_git_backed_old_replacement_baseline_requires_new_task_id(tmp_path):
    parent, _service_repo, _service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__old-binding"
    lib.ensure_task_scaffold(str(task), "TASK__old-binding")
    baseline_path = task / "TASK_BASELINE.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline["source_heads"].pop("")
    baseline["head_sha"] = lib._composite_source_heads(baseline["source_heads"])
    baseline_path.write_text(json.dumps(baseline) + "\n", encoding="utf-8")

    try:
        lib._read_task_baseline_snapshot(str(task), repo_root=str(parent))
    except lib.GitBindingError as exc:
        assert exc.code == "SOURCE_BINDINGS_CHANGED_RESTART_REQUIRED"
        assert "new Harness task ID" in exc.next_action
    else:
        raise AssertionError("Old replacement-semantics evidence must not be rewritten")


def test_git_backed_registered_normal_submodule_remains_supported(tmp_path):
    source = tmp_path / "source"
    parent = tmp_path / "parent"
    _git_repo(source, "source.py")
    _git_repo(parent, "parent.py")
    subprocess.run(
        [
            "git", "-c", "protocol.file.allow=always", "submodule", "add", "-q",
            str(source), "service",
        ],
        cwd=parent,
        check=True,
    )
    manifest = parent / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "version: 5\nsource_git_roots: [service]\n", encoding="utf-8"
    )

    assert [prefix for prefix, _root in lib.configured_source_git_roots(str(parent))] == [
        "", "service/"
    ]


def test_registered_gitlink_oid_drift_changes_review_fingerprint(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__oid-drift"
    lib.ensure_task_scaffold(str(task), "TASK__oid-drift")
    lib.sync_touched_paths(str(task), ["services/front"])
    first = lib.review_diff_fingerprint(str(task))
    (service / "service.py").write_text("next\n", encoding="utf-8")
    subprocess.run(["git", "add", "service.py"], cwd=service, check=True)
    subprocess.run(["git", "commit", "-qm", "next"], cwd=service, check=True)
    next_head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--cacheinfo", f"160000,{next_head},services/front"],
        cwd=parent, check=True,
    )

    assert lib.review_diff_fingerprint(str(task)) != first


def test_version_one_baseline_rejects_new_additive_binding(tmp_path):
    parent, _service_repo, _service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__v1-binding"
    lib.ensure_task_scaffold(str(task), "TASK__v1-binding")
    baseline_path = task / "TASK_BASELINE.json"
    baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline = {
        "version": 1,
        "captured_at": baseline["captured_at"],
        "repo_root": str(parent.resolve()),
        "head_sha": baseline["source_heads"][""],
        "dirty_paths": {},
    }
    baseline_path.write_text(json.dumps(baseline) + "\n", encoding="utf-8")

    with mock.patch.object(lib, "_review_snapshot_cache", return_value=None):
        try:
            lib._read_task_baseline_snapshot(str(task), repo_root=str(parent))
        except lib.GitBindingError as exc:
            assert exc.code == "SOURCE_BINDINGS_CHANGED_RESTART_REQUIRED"
        else:
            raise AssertionError("v1 evidence must not silently adopt additive roots")


def test_registered_binding_retarget_during_head_snapshot_fails_closed(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    original_gitfile = (service / ".git").read_text(encoding="utf-8")
    original_head = lib._git_head_snapshot
    service_calls = 0

    def retarget_on_evidence(root, **kwargs):
        nonlocal service_calls
        if Path(root).resolve() == service.resolve():
            service_calls += 1
            if service_calls == 2:
                (service / ".git").write_text("gitdir: /tmp/not-the-authorized-gitdir\n", encoding="utf-8")
        return original_head(root, **kwargs)

    try:
        with mock.patch.object(lib, "_git_head_snapshot", side_effect=retarget_on_evidence):
            try:
                lib._workspace_head_snapshot(str(parent))
            except lib.GitBindingError as exc:
                assert exc.code in {
                    "REGISTERED_WORKTREE_BINDING_CHANGED",
                    "REGISTERED_WORKTREE_BINDING_MISMATCH",
                }
            else:
                raise AssertionError("retargeted service metadata must fail closed")
    finally:
        (service / ".git").write_text(original_gitfile, encoding="utf-8")


def test_registered_binding_retarget_during_dirty_scan_fails_closed(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    original_gitfile = (service / ".git").read_text(encoding="utf-8")
    original_scan = lib._uncached_git_changed_paths

    def retarget_on_scan(root, **kwargs):
        if Path(root).resolve() == service.resolve():
            (service / ".git").write_text(
                "gitdir: /tmp/not-the-authorized-gitdir\n", encoding="utf-8"
            )
        return original_scan(root, **kwargs)

    try:
        with mock.patch.object(lib, "_uncached_git_changed_paths", side_effect=retarget_on_scan):
            try:
                lib._workspace_git_changed_paths(str(parent))
            except lib.GitBindingError as exc:
                assert exc.code in {
                    "REGISTERED_WORKTREE_BINDING_CHANGED",
                    "REGISTERED_WORKTREE_BINDING_MISMATCH",
                }
            else:
                raise AssertionError("retarget during dirty scan must fail closed")
    finally:
        (service / ".git").write_text(original_gitfile, encoding="utf-8")


def test_registered_binding_retarget_between_request_operations_fails_closed(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    gitfile = service / ".git"
    original_gitfile = gitfile.read_text(encoding="utf-8")
    original_admin = Path(original_gitfile.removeprefix("gitdir: ").strip())
    alternate_admin = original_admin.parent / "alternate-authority"
    shutil.copytree(original_admin, alternate_admin)
    (alternate_admin / "gitdir").write_text(str(gitfile) + "\n", encoding="utf-8")

    try:
        with lib.review_snapshot_scope():
            lib._workspace_source_heads(str(parent))
            gitfile.write_text(f"gitdir: {alternate_admin}\n", encoding="utf-8")
            try:
                lib._workspace_git_changed_paths(str(parent))
            except lib.GitBindingError as exc:
                assert exc.code == "REGISTERED_WORKTREE_BINDING_CHANGED"
                assert exc.invariant == "request_source_snapshot_binding"
            else:
                raise AssertionError("between-operation retarget must fail closed")
    finally:
        gitfile.write_text(original_gitfile, encoding="utf-8")


def test_registered_binding_pin_survives_request_snapshot_refresh(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    gitfile = service / ".git"
    original_gitfile = gitfile.read_text(encoding="utf-8")
    original_admin = Path(original_gitfile.removeprefix("gitdir: ").strip())
    alternate_admin = original_admin.parent / "alternate-after-refresh"
    shutil.copytree(original_admin, alternate_admin)
    (alternate_admin / "gitdir").write_text(str(gitfile) + "\n", encoding="utf-8")

    try:
        with lib.review_snapshot_scope():
            lib._workspace_source_heads(str(parent))
            lib.refresh_review_snapshot()
            gitfile.write_text(f"gitdir: {alternate_admin}\n", encoding="utf-8")
            try:
                lib._workspace_git_changed_paths(str(parent))
            except lib.GitBindingError as exc:
                assert exc.code == "REGISTERED_WORKTREE_BINDING_CHANGED"
                assert exc.invariant == "request_source_snapshot_binding"
            else:
                raise AssertionError("snapshot refresh must preserve the authority pin")
    finally:
        gitfile.write_text(original_gitfile, encoding="utf-8")


def test_committed_path_scan_uses_request_pinned_authority(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__committed-authority"
    lib.ensure_task_scaffold(str(task), "TASK__committed-authority")
    gitfile = service / ".git"
    original_gitfile = gitfile.read_text(encoding="utf-8")
    original_admin = Path(original_gitfile.removeprefix("gitdir: ").strip())
    alternate_admin = original_admin.parent / "alternate-committed-authority"
    shutil.copytree(original_admin, alternate_admin)
    (alternate_admin / "gitdir").write_text(str(gitfile) + "\n", encoding="utf-8")

    try:
        with lib.review_snapshot_scope():
            lib._workspace_source_heads(str(parent))
            gitfile.write_text(f"gitdir: {alternate_admin}\n", encoding="utf-8")
            try:
                lib._committed_paths_since_baseline(
                    str(task), str(service), workspace_prefix="services/front/"
                )
            except lib.GitBindingError as exc:
                assert exc.code == "REGISTERED_WORKTREE_BINDING_CHANGED"
                assert exc.invariant == "request_source_snapshot_binding"
            else:
                raise AssertionError("committed-path scan must use pinned authority")
    finally:
        gitfile.write_text(original_gitfile, encoding="utf-8")


def test_new_baseline_rechecks_authority_after_atomic_publication(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__retarget-on-publication"
    gitfile = service / ".git"
    original_gitfile = gitfile.read_text(encoding="utf-8")
    original_admin = Path(original_gitfile.removeprefix("gitdir: ").strip())
    alternate_admin = original_admin.parent / "alternate-on-publication"
    shutil.copytree(original_admin, alternate_admin)
    (alternate_admin / "gitdir").write_text(str(gitfile) + "\n", encoding="utf-8")
    original_replace = lib.os.replace

    def replace_then_retarget(source, destination):
        original_replace(source, destination)
        if Path(destination).name == "TASK_BASELINE.json":
            gitfile.write_text(f"gitdir: {alternate_admin}\n", encoding="utf-8")

    try:
        with mock.patch.object(lib.os, "replace", side_effect=replace_then_retarget):
            try:
                lib.ensure_task_scaffold(
                    str(task), "TASK__retarget-on-publication"
                )
            except RuntimeError as exc:
                assert "REGISTERED_WORKTREE_BINDING_CHANGED" in str(exc)
                assert isinstance(exc, lib.GitBindingError) or isinstance(
                    exc.__cause__, lib.GitBindingError
                )
            else:
                raise AssertionError("baseline publication retarget must fail closed")
        assert not (task / "TASK_BASELINE.json").exists()
        assert not (task / "TASK_STATE.yaml").exists()
    finally:
        gitfile.write_text(original_gitfile, encoding="utf-8")


def test_nongit_control_keeps_linked_worktree_source_compatibility(tmp_path):
    control = tmp_path / "control"
    source_repo = tmp_path / "source-repo"
    control.mkdir()
    _git_repo(source_repo, "source.py")
    source = control / "source"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(source), "HEAD"],
        cwd=source_repo, check=True,
    )
    manifest = control / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text(
        "version: 5\nsource_git_roots: [source]\n", encoding="utf-8",
    )

    assert lib.configured_source_git_roots(str(control)) == [
        ("source/", str(source.resolve()))
    ]
    task = control / "doc/harness/tasks/TASK__nongit-linked"
    lib.ensure_task_scaffold(str(task), "TASK__nongit-linked", repo_root=str(control))
    assert (task / "TASK_BASELINE.json").is_file()


def test_uninitialized_registered_gitlink_has_stable_runtime_error(tmp_path):
    parent, service_repo, service = _git_backed_linked_workspace(tmp_path)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(service)],
        cwd=service_repo, check=True,
    )

    try:
        lib.configured_source_git_roots(str(parent))
    except lib.GitBindingError as exc:
        assert exc.code == "REGISTERED_SOURCE_UNINITIALIZED"
        assert exc.path == "services/front"
        assert "Restore the checkout" in exc.next_action
    else:
        raise AssertionError("missing registered checkout must fail with recovery details")


def test_ambient_alternate_index_cannot_authorize_registered_source(tmp_path, monkeypatch):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    alternate_index = tmp_path / "alternate-index"
    alt_env = os.environ.copy()
    alt_env["GIT_INDEX_FILE"] = str(alternate_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=parent, env=alt_env, check=True)
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},services/front"],
        cwd=parent, env=alt_env, check=True,
    )
    subprocess.run(
        ["git", "update-index", "--force-remove", "services/front"],
        cwd=parent, check=True,
    )
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))

    try:
        lib.configured_source_git_roots(str(parent))
    except lib.GitBindingError as exc:
        assert exc.code == "REGISTERED_SOURCE_NOT_DIRECT_GITLINK"
    else:
        raise AssertionError("ambient alternate index must not grant authorization")


def test_registered_service_gitlink_scan_retarget_fails_closed(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    original_gitfile = (service / ".git").read_text(encoding="utf-8")
    original_entries = lib._direct_gitlink_index_entries

    def retarget_on_service_scan(root, **kwargs):
        if Path(root).resolve() == service.resolve():
            (service / ".git").write_text(
                "gitdir: /tmp/not-the-authorized-gitdir\n", encoding="utf-8"
            )
        return original_entries(root, **kwargs)

    try:
        with mock.patch.object(
            lib, "_direct_gitlink_index_entries", side_effect=retarget_on_service_scan
        ):
            try:
                lib._workspace_gitlink_paths(str(parent))
            except lib.GitBindingError:
                pass
            else:
                raise AssertionError("retarget during gitlink scan must fail closed")
    finally:
        (service / ".git").write_text(original_gitfile, encoding="utf-8")


def test_security_signal_diff_uses_explicit_registered_gitdir(tmp_path):
    parent, _service_repo, service = _git_backed_linked_workspace(tmp_path)
    task = parent / "doc/harness/tasks/TASK__security-diff-binding"
    lib.ensure_task_scaffold(str(task), "TASK__security-diff-binding")
    original_run = lib.subprocess.run
    observed: list[list[str]] = []

    def observe(command, *args, **kwargs):
        if "--unified=0" in command:
            observed.append(list(command))
        return original_run(command, *args, **kwargs)

    with mock.patch.object(lib.subprocess, "run", side_effect=observe):
        lib._path_has_security_signal(
            str(task), str(parent), "services/front/service.py"
        )
    assert len(observed) == 1
    assert any(arg.startswith("--git-dir=") for arg in observed[0])
    assert f"--work-tree={service.resolve()}" in observed[0]


def test_workspace_baseline_and_fingerprint_cover_all_registered_git_roots(tmp_path):
    root, api, web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__multi"

    lib.ensure_task_scaffold(str(task), "TASK__multi")
    baseline = json.loads((task / "TASK_BASELINE.json").read_text(encoding="utf-8"))
    assert baseline["version"] == 2
    assert set(baseline["source_heads"]) == {"pay-api/", "pay-webapp/"}
    assert set(baseline["control_paths"]) == {
        "AGENTS.md",
        "CLAUDE.md",
        "CONTRACTS.md",
        "CONTRACTS.local.md",
        "CONTRACTS.user.md",
        "doc/harness/manifest.yaml",
    }
    first_head = lib._git_head_for_receipt(str(task))

    (api / "api.py").write_text("changed\n", encoding="utf-8")
    (web / "new.js").write_text("new\n", encoding="utf-8")
    touched = lib.sync_from_git_diff(str(task))
    assert set(touched) == {"pay-api/api.py", "pay-webapp/new.js"}
    first_fingerprint = lib.review_diff_fingerprint(str(task))

    (web / "new.js").write_text("newer\n", encoding="utf-8")
    assert lib.review_diff_fingerprint(str(task)) != first_fingerprint
    subprocess.run(["git", "add", "api.py"], cwd=api, check=True)
    subprocess.run(["git", "commit", "-qm", "change api"], cwd=api, check=True)
    assert lib._git_head_for_receipt(str(task)) != first_head


def test_new_multigit_baseline_reuses_source_heads_without_head_rescan(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__single-head-snapshot"
    original = lib._workspace_source_heads
    calls = 0

    def counted(control_root):
        nonlocal calls
        calls += 1
        return original(control_root)

    with (
        mock.patch.object(lib, "_workspace_source_heads", side_effect=counted),
        mock.patch.object(
            lib,
            "_workspace_head_snapshot",
            side_effect=AssertionError("baseline must reuse captured source heads"),
        ),
    ):
        lib.ensure_task_scaffold(str(task), "TASK__single-head-snapshot")

    assert calls == 1
    baseline = json.loads((task / "TASK_BASELINE.json").read_text(encoding="utf-8"))
    assert baseline["head_sha"] == lib._composite_source_heads(baseline["source_heads"])


def test_workspace_missing_task_baseline_fails_closed(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__missing-baseline"
    lib.ensure_task_scaffold(str(task), "TASK__missing-baseline")
    (task / "TASK_BASELINE.json").unlink()

    try:
        lib.sync_from_git_diff(str(task))
    except RuntimeError as exc:
        assert "required task baseline missing" in str(exc)
    else:
        raise AssertionError("multi-Git tasks must require their baseline")

    try:
        lib.ensure_task_scaffold(str(task), "TASK__missing-baseline")
    except RuntimeError as exc:
        assert "required task baseline missing" in str(exc)
    else:
        raise AssertionError("resuming a multi-Git task without a baseline must fail")


def test_resumed_task_fails_when_registered_source_root_moved(tmp_path):
    root, api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__moved-root"
    lib.ensure_task_scaffold(
        str(task), "TASK__moved-root", repo_root=str(root)
    )
    api.rename(root / "pay-api-moved")

    with mock.patch.object(
        harness_server, "_control_root", return_value=str(root)
    ):
        result = harness_server.call_tool(
            "task_start", {"task_id": "TASK__moved-root"}
        )

    assert result.get("isError")
    assert "source_git_roots" in result["content"][0]["text"]
    assert not (root / "doc/harness/tasks/.active").exists()

    moved = root / "pay-api-moved"
    prewrite = subprocess.run(
        [sys.executable, str(SCRIPTS / "prewrite_gate.py")],
        cwd=moved,
        input=json.dumps({
            "cwd": str(moved),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(moved / "api.py")},
        }),
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"permissionDecision": "deny"' in prewrite.stdout
    assert "invalid" in prewrite.stdout.lower()

    previous_cwd = os.getcwd()
    try:
        os.chdir(moved)
        try:
            harness_server._control_root()
        except RuntimeError as exc:
            assert "invalid Harness workspace" in str(exc)
        else:
            raise AssertionError("MCP control-root resolution must fail closed")
    finally:
        os.chdir(previous_cwd)


def test_control_behavior_symlink_fails_baseline_capture(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    external = tmp_path / "external-agents.md"
    external.write_text("outside\n", encoding="utf-8")
    (root / "AGENTS.md").symlink_to(external)
    task = root / "doc/harness/tasks/TASK__control-symlink"

    try:
        lib.ensure_task_scaffold(
            str(task), "TASK__control-symlink", repo_root=str(root)
        )
    except RuntimeError as exc:
        assert "must not be a symlink" in str(exc)
    else:
        raise AssertionError("control-root behavior symlinks must fail closed")


def test_workspace_resolution_rejects_unregistered_nested_repo(tmp_path):
    root, api, _web = _workspace(tmp_path)
    rogue = root / "rogue"
    _git_repo(rogue, "rogue.txt")

    assert lib.find_harness_root(str(api)) == str(root.resolve())
    assert lib.find_harness_root(str(rogue)) == ""


def test_current_nongit_manifest_requires_explicit_source_roots(tmp_path):
    root = tmp_path / "workspace"
    api = root / "pay-api"
    _git_repo(api, "api.py")
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 5\ntype: api\n", encoding="utf-8")

    resolved, error = lib.harness_root_resolution(str(api))
    assert resolved == str(root.resolve())
    assert "source_git_roots is required" in error
    assert lib.find_harness_root(str(api)) == ""

    manifest.write_text("version: 4\ntype: api\n", encoding="utf-8")
    assert lib.find_harness_root(str(api)) == str(root.resolve())


def test_harness_root_rejects_symlinked_manifest_ancestor(tmp_path):
    root = tmp_path / "workspace"
    root.mkdir()
    external = tmp_path / "external-doc"
    (external / "harness").mkdir(parents=True)
    (external / "harness/manifest.yaml").write_text(
        "version: 5\nsource_git_roots: ['api']\n", encoding="utf-8"
    )
    (root / "doc").symlink_to(external, target_is_directory=True)

    resolved, error = lib.harness_root_resolution(str(root))
    assert resolved == str(root.resolve())
    assert "must not be symlinks" in error


def test_workspace_source_roots_fail_closed_for_symlink_and_missing_root(tmp_path):
    root, api, _web = _workspace(tmp_path)
    manifest = root / "doc/harness/manifest.yaml"
    link = root / "linked-api"
    link.symlink_to(api, target_is_directory=True)
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "[pay-api, pay-webapp]", "[linked-api, missing]"
        ),
        encoding="utf-8",
    )

    try:
        lib.configured_source_git_roots(str(root))
    except RuntimeError as exc:
        assert "symlink" in str(exc)
    else:
        raise AssertionError("unsafe workspace roots must fail closed")


def test_codex_registration_uses_parent_control_root_for_child_cwd(tmp_path, monkeypatch):
    root, api, _web = _workspace(tmp_path)
    registration = _load("codex_hook_registration")
    calls: list[tuple[str, str]] = []
    payload = json.dumps({
        "cwd": str(api),
        "session_id": "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    }).encode()
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)

    assert registration.restore_watcher_registration(
        payload,
        ensure_fn=lambda control_root, thread_id: (
            calls.append((control_root, thread_id)) or True
        ),
    )
    assert calls == [(
        str(root.resolve()),
        "019f825b-f25f-70c3-8ee8-071f79fa1c42",
    )]


def test_child_cwd_uses_parent_control_root_for_claude_gates(tmp_path):
    root, api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__child-gates"
    lib.ensure_task_scaffold(str(task), "TASK__child-gates")
    (task / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    (root / "doc/harness/tasks/.active").write_text(
        str(task) + "\n", encoding="utf-8"
    )

    prewrite = subprocess.run(
        [sys.executable, str(SCRIPTS / "prewrite_gate.py")],
        cwd=api,
        input=json.dumps({
            "cwd": str(api),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(task / "PLAN.md")},
        }),
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"permissionDecision": "deny"' in prewrite.stdout

    alias = api / "notes.txt"
    alias.symlink_to(
        Path("../doc/harness/tasks/TASK__child-gates/PLAN.md")
    )
    alias_prewrite = subprocess.run(
        [sys.executable, str(SCRIPTS / "prewrite_gate.py")],
        cwd=api,
        input=json.dumps({
            "cwd": str(api),
            "tool_name": "Edit",
            "tool_input": {"file_path": str(alias)},
        }),
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"permissionDecision": "deny"' in alias_prewrite.stdout

    bash_guard = subprocess.run(
        [sys.executable, str(SCRIPTS / "mcp_bash_guard.py")],
        cwd=api,
        input=json.dumps({
            "cwd": str(api),
            "tool_name": "Bash",
            "tool_input": {
                "command": "touch ../doc/harness/tasks/TASK__child-gates/PLAN.md"
            },
        }),
        text=True,
        capture_output=True,
        check=True,
    )
    assert '"permissionDecision": "deny"' in bash_guard.stdout

    stop_env = os.environ.copy()
    stop_env["HARNESS_BACKGROUND_WAIT_SECS"] = "0"
    stop = subprocess.run(
        [sys.executable, str(SCRIPTS / "stop_gate.py")],
        cwd=api,
        input=json.dumps({"cwd": str(api), "session_id": "sess-child"}),
        text=True,
        capture_output=True,
        check=True,
        env=stop_env,
    )
    assert json.loads(stop.stdout)["decision"] == "block"


def test_child_cwd_claude_lifecycle_records_parent_task_receipt(tmp_path):
    root, api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__child-receipt"
    lib.ensure_task_scaffold(str(task), "TASK__child-receipt")
    (root / "doc/harness/tasks/.active").write_text(
        str(task) + "\n", encoding="utf-8"
    )

    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "background_hook.py"), "--event", "start"],
        cwd=api,
        input=json.dumps({
            "cwd": str(api),
            "hook_event_name": "SubagentStart",
            "session_id": "sess-child",
            "agent_id": "agent-child",
            "agent_type": "harness:qa-cli",
        }),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == ""
    receipts = [
        json.loads(line)
        for line in (task / "SUBAGENT_RECEIPTS.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
    ]
    assert receipts[-1]["agent_id"] == "agent-child"
    assert receipts[-1]["lens"] == "qa-cli"


def test_verify_runner_executes_parent_workspace_manifest_command(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__verify-multigit"
    lib.ensure_task_scaffold(str(task), "TASK__verify-multigit")

    result = harness_server._run_verify_runner(str(task), parallel=False)

    assert result["returncode"] == 0
    assert len(result["commands"]) == 1
    assert result["commands"][0]["command"] == "echo ok"
    assert result["commands"][0]["returncode"] == 0


def test_task_close_compares_workspace_snapshots_across_final_gate(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__close-multigit"
    lib.ensure_task_scaffold(str(task), "TASK__close-multigit")
    (task / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    clean = {"missing_for_close": [], "next_action": "close"}

    with mock.patch.object(
        harness_server, "canonical_task_dir", return_value=str(task)
    ), mock.patch.object(
        harness_server, "sync_from_git_diff", return_value=[]
    ), mock.patch.object(
        harness_server, "emit_compact_context", return_value=clean
    ), mock.patch.object(
        harness_server, "_runtime_is_stale", return_value=(False, "")
    ), mock.patch.object(
        harness_server, "_checks_gate_status", return_value=("passed", [])
    ), mock.patch.object(
        harness_server, "_git_head_for_receipt", return_value="a" * 40
    ), mock.patch.object(
        harness_server,
        "_workspace_changed_path_fingerprints",
        side_effect=[
            {"pay-api/api.py": "sha256:" + "1" * 64},
            {"pay-api/api.py": "sha256:" + "2" * 64},
        ],
    ) as snapshots:
        result = harness_server.handle_task_close(
            {"task_id": "TASK__close-multigit"}
        )

    assert result.get("isError")
    assert result["structuredContent"]["snapshot_changed"]
    assert [call.args[0] for call in snapshots.call_args_list] == [
        str(root.resolve()),
        str(root.resolve()),
    ]


def test_task_close_detects_control_root_file_change_across_final_gate(tmp_path):
    root, _api, _web = _workspace(tmp_path)
    task = root / "doc/harness/tasks/TASK__close-control-race"
    lib.ensure_task_scaffold(str(task), "TASK__close-control-race")
    (task / "PLAN.md").write_text("# Plan\n", encoding="utf-8")
    project_doc = root / "AGENTS.md"
    project_doc.write_text("@CONTRACTS.md\n", encoding="utf-8")
    touched = lib.sync_from_git_diff(str(task))
    assert "AGENTS.md" in touched
    assert "AGENTS.md" in lib._reviewable_source_paths(str(task))
    clean = {"missing_for_close": [], "next_action": "close"}
    context_calls = 0

    def context_with_race(_task_dir):
        nonlocal context_calls
        context_calls += 1
        if context_calls == 2:
            project_doc.write_text("@CONTRACTS.md\nchanged\n", encoding="utf-8")
        return clean

    with mock.patch.object(
        harness_server, "canonical_task_dir", return_value=str(task)
    ), mock.patch.object(
        harness_server, "sync_from_git_diff", return_value=[]
    ), mock.patch.object(
        harness_server, "emit_compact_context", side_effect=context_with_race
    ), mock.patch.object(
        harness_server, "_runtime_is_stale", return_value=(False, "")
    ), mock.patch.object(
        harness_server, "_checks_gate_status", return_value=("passed", [])
    ), mock.patch.object(
        harness_server, "_git_head_for_receipt", return_value="a" * 40
    ), mock.patch.object(
        harness_server, "_workspace_changed_path_fingerprints", return_value={}
    ):
        result = harness_server.handle_task_close(
            {"task_id": "TASK__close-control-race"}
        )

    assert result.get("isError")
    assert result["structuredContent"]["control_snapshot_changed"]


def test_setup_skills_apply_fixed_defaults_without_policy_questions():
    codex = (REPO / "plugin-codex/skills/setup/SKILL.md").read_text(encoding="utf-8")
    claude = (REPO / "plugin/skills/setup/SKILL.md").read_text(encoding="utf-8")
    interview = (REPO / "plugin/skills/setup/project-interview.md").read_text(
        encoding="utf-8"
    )

    for skill, project_doc in ((codex, "AGENTS.md"), (claude, "CLAUDE.md")):
        section = skill.split("### Proactive Toggle + Routing Injection", 1)[1].split(
            "\n---", 1
        )[0]
        assert "Do not ask" in section
        assert "_harness_config_set proactive true" in section
        assert "_harness_config_set routing_declined false" in section
        assert f"routing block in {project_doc}" in section
        assert "Reply `A`" not in section
        assert "AskUserQuestion" not in section

    assert "Q2–Q4 — Fixed operating defaults (never ask)" in interview
    assert "Q6 — Fixed failure mode (never ask)" in interview
    assert "말하지 않은 범위도 멋대로 수정하는 것" in interview


def test_setup_automates_contract_import_and_health_scoring():
    codex = (REPO / "plugin-codex/skills/setup/SKILL.md").read_text(encoding="utf-8")
    claude = (REPO / "plugin/skills/setup/SKILL.md").read_text(encoding="utf-8")
    bootstrap = (REPO / "plugin/skills/setup/bootstrap.md").read_text(
        encoding="utf-8"
    )

    for skill in (codex, claude):
        health_default = skill.split(
            "### Health scoring default (never ask)", 1
        )[1].split("## Phase 2.5", 1)[0]
        health_detection = skill.split(
            "## Phase 2.5: Health Stack Auto-Detection", 1
        )[1].split("## Phase 3:", 1)[0]
        assert "Do not ask whether to enable health scoring" in health_default
        assert "Stage every census test command" in health_detection
        assert "Never ask for confirmation" in health_detection
        assert "Detected health tooling. Write" not in health_detection
        assert "HARNESS_SPAWNED" not in health_detection

    contract_import = bootstrap.split(
        "### 3.7.3 Runtime project-document import line", 1
    )[1].split("### 3.7.4", 1)[0]
    assert "When missing, do not ask" in contract_import
    assert "immediately after the closing delimiter" in contract_import
    assert "after the first H1" in contract_import
    assert "preserves\nall existing bytes outside the insertion" in contract_import
    assert "rejects symlinked project documents" in contract_import
    assert "AskUserQuestion" not in contract_import
    local_contract = bootstrap.split(
        "### 3.7.2 CONTRACTS.local.md", 1
    )[1].split("### 3.7.3", 1)[0]
    assert "replace only" in local_contract
    assert "setup-owned C-100 block" in local_contract
    assert "never bulk-rewrite or modify any other content" in local_contract
    contract_template = (
        REPO / "plugin/skills/setup/templates/CONTRACTS.md"
    ).read_text(encoding="utf-8")
    c15 = contract_template.split("### C-15", 1)[1].split("### C-16", 1)[0]
    assert "idempotently add that missing import without asking" in c15
    assert "only the setup-owned C-100 block" in c15
    root_contract = (REPO / "CONTRACTS.md").read_text(encoding="utf-8")
    root_c15 = root_contract.split("### C-15", 1)[1].split("### C-16", 1)[0]
    assert root_c15 == c15
