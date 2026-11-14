"""Independent real-Git acceptance tests for the bounded S1 helper."""
from __future__ import annotations

from test_symlink_fixture import create_symlink

import copy
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def _helper():
    sys.path.insert(0, str(ROOT / "plugin/scripts"))
    return importlib.import_module("batch_submodules")


def _git(repo, *args):
    result = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *map(str, args)], cwd=repo,
        env={**os.environ, "GIT_AUTHOR_NAME": "S1 Test", "GIT_AUTHOR_EMAIL": "s1@example.test",
             "GIT_COMMITTER_NAME": "S1 Test", "GIT_COMMITTER_EMAIL": "s1@example.test"},
        capture_output=True, text=True,
    )
    assert result.returncode == 0, (args, result.stderr)
    return result.stdout.strip()


def _commit(repo, name="edit"):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", name)
    return _git(repo, "rev-parse", "HEAD")


def _repo(path):
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", "main")
    (path / "file.txt").write_text("initial\n")
    _commit(path, "initial")
    return path


def _setup(tmp_path, monkeypatch, count=1):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in list(os.environ):
        if key.startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    main = _repo(tmp_path / "main")
    (main / ".gitignore").write_text(".claude/worktrees/\n")
    paths = [f"libs/module{i}" for i in range(count)]
    for i, path in enumerate(paths):
        source = _repo(tmp_path / f"source{i}")
        _git(main, "submodule", "add", "-q", source, path)
    _commit(main, "modules")
    work = main / ".claude/worktrees/lead"
    _git(main, "worktree", "add", "-qb", "lead", work)
    return main, work, paths


def _manifest(helper, main, work, paths):
    return helper.manifest(main, work, helper.selection(main, paths),
                           batch_id="batch-s1", slug="lead", spawn_head=_git(work, "rev-parse", "HEAD"))


def _prepared(tmp_path, monkeypatch, count=1):
    main, work, paths = _setup(tmp_path, monkeypatch, count)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    snapshots = []
    helper.prepare(main, work, value, lambda item: snapshots.append(copy.deepcopy(item)))
    return helper, main, work, paths, value, snapshots


def _change(work, path, text="changed\n"):
    (work / path / "file.txt").write_text(text)
    tip = _commit(work / path)
    _commit(work, "module gitlink")
    return tip


def pytest_generate_tests(metafunc):
    import pytest
    posix_filename = pytest.mark.skipif(os.name == "nt", reason="Windows filenames cannot contain ':' or '*'")
    cases = {
        "selected_name": ["libs/모듈", "libs/[module]",
                          pytest.param("libs/[module]*", marks=posix_filename),
                          pytest.param(":(glob)module*", marks=posix_filename)],
        "bad_paths": [["../source0"], ["libs/module0/"], ["libs/./module0"],
                      ["libs/module0", "libs/module0"], ["missing"], ["file.txt"],
                      ["libs/module0/file.txt"], ["/tmp/foreign"]],
        "store_kind": ["alternates", "shallow", "promisor", "replace"],
        "drift": ["dirty", "untracked", "ignored", "detached", "gitfile", "branch"],
        "reconcile_drift": ["dirty", "wrong-ref", "wrong-tip", "foreign-module"],
        "conversion_boolean": [("true", "true"), ("yes", "true"), ("ON", "true"), ("1", "true"),
                               ("false", "false"), ("no", "false"), ("off", "false"), ("0", "false"),
                               ("", "false"), (None, "true"), ("input", "input")],
        "invalid_conversion_key": ["core.autocrlf", "core.eol"],
    }
    for name, values in cases.items():
        if name in metafunc.fixturenames:
            metafunc.parametrize(name, values)


def test_selection_rejects_noncanonical_or_nonmodule_paths(tmp_path, monkeypatch, bad_paths):
    import pytest
    main, _, _ = _setup(tmp_path, monkeypatch)
    helper = _helper()
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, bad_paths)


def test_selection_rejects_dirty_unpopulated_and_symlink(tmp_path, monkeypatch):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    module = main / paths[0]
    (module / "file.txt").write_text("dirty")
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)
    _git(module, "checkout", "--", "file.txt")
    _git(main, "submodule", "deinit", "-f", "--", paths[0])
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)
    module.rmdir()
    source_file = tmp_path / "source0/file.txt"
    before = source_file.read_bytes()
    create_symlink(module, tmp_path / "source0", target_is_directory=True)
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)
    assert module.is_symlink() and source_file.read_bytes() == before


def test_selection_rejects_unsupported_object_stores(tmp_path, monkeypatch, store_kind):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    module = main / paths[0]
    gitdir = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    if store_kind == "alternates":
        (gitdir / "objects/info/alternates").write_text(str(tmp_path / "source0/.git/objects") + "\n")
    elif store_kind == "shallow":
        (gitdir / "shallow").write_text(_git(module, "rev-parse", "HEAD") + "\n")
    elif store_kind == "promisor":
        _git(module, "config", "remote.origin.promisor", "true")
    else:
        _git(module, "update-ref", "refs/replace/" + _git(module, "rev-parse", "HEAD"), "HEAD")
    helper = _helper()
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)


def test_preparation_is_private_named_and_does_not_change_shared_config(tmp_path, monkeypatch):
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    configs = [main / ".git/config", Path(_git(main / paths[0], "rev-parse", "--absolute-git-dir")) / "config"]
    before = {p: p.read_bytes() for p in configs}
    value = _manifest(helper, main, work, paths)
    snapshots = []
    helper.prepare(main, work, value, lambda item: snapshots.append(copy.deepcopy(item)))
    assert len(snapshots) >= 2
    assert snapshots[0] != snapshots[-1]
    module = work / paths[0]
    assert _git(module, "symbolic-ref", "--short", "HEAD")
    assert _git(module, "rev-parse", "HEAD") == _git(main / paths[0], "rev-parse", "HEAD")
    private = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    admin = Path(_git(work, "rev-parse", "--absolute-git-dir"))
    assert private.is_relative_to(admin)
    assert not (private / "objects/info/alternates").exists()
    source_objects = Path(_git(main / paths[0], "rev-parse", "--absolute-git-dir")) / "objects"
    for obj in (private / "objects").rglob("*"):
        peer = source_objects / obj.relative_to(private / "objects")
        if obj.is_file() and peer.is_file():
            assert (obj.stat().st_dev, obj.stat().st_ino) != (peer.stat().st_dev, peer.stat().st_ino)
    assert {p: p.read_bytes() for p in configs} == before
    stable = copy.deepcopy(value)
    helper.prepare(main, work, value, lambda _: None)
    assert value == stable
    helper.validate(main, work, value)


def test_preparation_persists_intent_before_any_clone(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    class Interrupted(Exception):
        pass
    def stop(item):
        assert not (work / paths[0] / ".git").exists()
        assert json.dumps(item)
        raise Interrupted
    with pytest.raises(Interrupted):
        helper.prepare(main, work, value, stop)
    helper.prepare(main, work, value, lambda _: None)
    helper.validate(main, work, value)


def test_preparation_retains_unknown_partial_directory(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    unknown = work / paths[0] / "valuable.txt"
    unknown.write_text("must survive")
    with pytest.raises(helper.SubmoduleError):
        helper.prepare(main, work, value, lambda _: None)
    assert unknown.read_text() == "must survive"


def test_resume_allows_edits_but_disposal_refuses_drift(tmp_path, monkeypatch, drift):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    helper.preserve(main, work, value, lambda _: None)
    module = work / paths[0]
    if drift == "dirty":
        (module / "file.txt").write_text("pending")
        helper.validate(main, work, value, phase="resume")
    elif drift == "untracked":
        (module / "unknown.txt").write_text("keep")
    elif drift == "ignored":
        gitdir = Path(_git(module, "rev-parse", "--absolute-git-dir"))
        (gitdir / "info").mkdir(exist_ok=True)
        (gitdir / "info/exclude").write_text("precious\n")
        (module / "precious").write_text("keep")
    elif drift == "detached":
        _git(module, "checkout", "--detach", "-q")
    elif drift == "branch":
        _git(module, "branch", "foreign")
    else:
        (module / ".git").write_text("gitdir: " + str(tmp_path / "source0/.git") + "\n")
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)
    assert work.exists()


def test_preserve_all_refs_tags_reflogs_and_intermediate_gitlinks_after_gc(tmp_path, monkeypatch):
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    module = work / paths[0]
    intermediate = _change(work, paths[0], "intermediate\n")
    _git(module, "tag", "-a", "annotated", "-m", "annotation")
    tag = _git(module, "rev-parse", "refs/tags/annotated")
    _git(module, "branch", "extra")
    (module / "file.txt").write_text("reflog-only\n")
    reflog_tip = _commit(module)
    _git(module, "reset", "--hard", intermediate)
    (module / "file.txt").write_text("amended\n")
    _git(module, "add", "-A")
    _git(module, "commit", "--amend", "-qm", "amend")
    final = _git(module, "rev-parse", "HEAD")
    _commit(work, "final gitlink")
    helper.preserve(main, work, value, lambda _: None)
    destination = main / paths[0]
    pins = _git(destination, "for-each-ref", "--format=%(objectname)", "refs/").splitlines()
    assert {intermediate, reflog_tip, final, tag}.issubset(set(pins))
    _git(destination, "reflog", "expire", "--expire=now", "--all")
    _git(destination, "gc", "--prune=now")
    for oid in (intermediate, reflog_tip, final):
        assert _git(destination, "cat-file", "-t", oid) == "commit"
        _git(destination, "rev-list", "--objects", oid)
    assert _git(destination, "cat-file", "-t", tag) == "tag"
    helper.removal_proof(main, work, value)


def _landed(tmp_path, monkeypatch, count=1):
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch, count)
    old = _git(main, "rev-parse", "HEAD")
    targets = [_change(work, p, f"changed {p}\n") for p in paths]
    helper.preserve(main, work, value, lambda _: None)
    tip = _git(work, "rev-parse", "HEAD")
    proof = helper.witness(main, work, value, "refs/heads/main", old, tip)
    _git(main, "merge", "--ff-only", "lead")
    return helper, main, work, paths, value, proof, targets


def test_reconcile_exact_landed_checkout_and_partial_multiupdate(tmp_path, monkeypatch):
    helper, main, _, paths, value, proof, targets = _landed(tmp_path, monkeypatch, 2)
    _git(main / paths[0], "checkout", "--detach", targets[0])
    helper.reconcile_checkout(main, value, proof, target="main")
    assert [_git(main / p, "rev-parse", "HEAD") for p in paths] == targets
    assert _git(main, "status", "--porcelain") == ""
    helper.reconcile_checkout(main, value, proof, target="main")


def test_reconcile_refuses_foreign_state_without_discard(tmp_path, monkeypatch, reconcile_drift):
    import pytest
    helper, main, _, paths, value, proof, targets = _landed(tmp_path, monkeypatch)
    module = main / paths[0]
    if reconcile_drift == "dirty":
        (module / "file.txt").write_text("valuable edit")
    elif reconcile_drift == "wrong-ref":
        _git(main, "checkout", "-qb", "foreign")
    elif reconcile_drift == "wrong-tip":
        (main / "file.txt").write_text("later unrelated")
        _commit(main)
    else:
        (module / "file.txt").write_text("foreign")
        _commit(module)
    before = _git(module, "rev-parse", "HEAD")
    with pytest.raises(helper.SubmoduleError):
        helper.reconcile_checkout(main, value, proof, target="main")
    assert _git(module, "rev-parse", "HEAD") == before
    if reconcile_drift == "dirty":
        assert (module / "file.txt").read_text() == "valuable edit"


def test_preserve_rejects_temporary_reverted_topology_change(tmp_path, monkeypatch):
    import pytest
    helper, main, work, _, value, _ = _prepared(tmp_path, monkeypatch)
    original = (work / ".gitmodules").read_text()
    (work / ".gitmodules").write_text(original + "\n# temporary topology edit\n")
    _commit(work)
    (work / ".gitmodules").write_text(original)
    _commit(work)
    with pytest.raises(helper.SubmoduleError):
        helper.preserve(main, work, value, lambda _: None)


def test_manifest_shape_rejects_unknown_or_missing_identity(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    helper.validate_manifest(value)
    for key in value:
        broken = copy.deepcopy(value)
        del broken[key]
        with pytest.raises(helper.SubmoduleError):
            helper.validate_manifest(broken)
    broken = copy.deepcopy(value)
    broken["unknown_authority"] = True
    with pytest.raises(helper.SubmoduleError):
        helper.validate_manifest(broken)


def test_preserve_gitlink_history_without_any_remaining_private_ref_or_reflog(tmp_path, monkeypatch):
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    module = work / paths[0]
    intermediate = _change(work, paths[0], "history-only\n")
    (module / "file.txt").write_text("replacement\n")
    _git(module, "add", "-A")
    _git(module, "commit", "--amend", "-qm", "replacement")
    _commit(work, "replacement gitlink")
    _git(module, "reflog", "expire", "--expire=now", "--all")
    refs = _git(module, "for-each-ref", "--format=%(objectname)").splitlines()
    assert intermediate not in refs
    helper.preserve(main, work, value, lambda _: None)
    destination = main / paths[0]
    _git(destination, "gc", "--prune=now")
    assert _git(destination, "cat-file", "-t", intermediate) == "commit"
    assert intermediate in _git(destination, "for-each-ref", "--format=%(objectname)").splitlines()


def test_preparation_adopts_exact_completed_effect_after_persist_interruption(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    durable = copy.deepcopy(value)
    class Interrupted(Exception):
        pass
    def persist(item):
        nonlocal durable
        if all(module["phase"] == "prepared" for module in item["modules"]):
            raise Interrupted
        durable = copy.deepcopy(item)
    with pytest.raises(Interrupted):
        helper.prepare(main, work, value, persist)
    module_gitfile = work / paths[0] / ".git"
    assert module_gitfile.exists()
    identity = module_gitfile.stat().st_ino
    helper.prepare(main, work, durable, lambda _: None)
    helper.validate(main, work, durable)
    assert module_gitfile.stat().st_ino == identity


def test_removal_refuses_missing_or_changed_durable_pin(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    _change(work, paths[0])
    destination = main / paths[0]
    before = set(_git(destination, "for-each-ref", "--format=%(refname)").splitlines())
    helper.preserve(main, work, value, lambda _: None)
    added = set(_git(destination, "for-each-ref", "--format=%(refname)").splitlines()) - before
    assert added
    pin = sorted(added)[0]
    old = _git(destination, "rev-parse", pin)
    _git(destination, "update-ref", "-d", pin)
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)
    _git(destination, "update-ref", pin, old)
    helper.removal_proof(main, work, value)
    other = _git(destination, "rev-parse", "HEAD")
    changed_pin = next(p for p in added if _git(destination, "rev-parse", p) != other)
    _git(destination, "update-ref", changed_pin, other)
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)


def test_selection_refuses_nested_module_graph(tmp_path, monkeypatch):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    nested = _repo(tmp_path / "nested")
    _git(main / paths[0], "submodule", "add", "-q", nested, "nested")
    _commit(main / paths[0])
    _commit(main)
    helper = _helper()
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)


def test_selected_module_with_ignored_nested_repository_refuses(tmp_path, monkeypatch):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    module = main / paths[0]
    (module / ".gitignore").write_text("nested/\n")
    _commit(module)
    _commit(main)
    _repo(module / "nested")
    helper = _helper()
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)


def test_manifest_bound_refuses_more_than_sixteen_modules(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    value["modules"] = [copy.deepcopy(value["modules"][0]) for _ in range(17)]
    for i, module in enumerate(value["modules"]):
        module["path"] = f"libs/module{i}"
    with pytest.raises(helper.SubmoduleError):
        helper.validate_manifest(value)


def test_preserve_ref_inventory_limit_refuses_before_destination_writes(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    module = work / paths[0]
    tip = _git(module, "rev-parse", "HEAD")
    commands = "".join(f"create refs/heads/limit-{i} {tip}\n" for i in range(4097))
    result = subprocess.run(["git", "update-ref", "--stdin"], cwd=module,
                            input=commands.encode("utf-8"), capture_output=True)
    assert result.returncode == 0, result.stderr
    destination = main / paths[0]
    before = _git(destination, "for-each-ref", "--format=%(refname) %(objectname)")
    with pytest.raises(helper.SubmoduleError):
        helper.preserve(main, work, value, lambda _: None)
    assert _git(destination, "for-each-ref", "--format=%(refname) %(objectname)") == before


def test_reconcile_refuses_unrelated_superproject_dirt(tmp_path, monkeypatch):
    import pytest
    helper, main, _, paths, value, proof, _ = _landed(tmp_path, monkeypatch)
    (main / "file.txt").write_text("unrelated valuable dirt")
    old = _git(main / paths[0], "rev-parse", "HEAD")
    with pytest.raises(helper.SubmoduleError):
        helper.reconcile_checkout(main, value, proof, target="main")
    assert _git(main / paths[0], "rev-parse", "HEAD") == old
    assert (main / "file.txt").read_text() == "unrelated valuable dirt"


def test_selection_requires_checkout_at_recorded_gitlink(tmp_path, monkeypatch):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    (main / paths[0] / "file.txt").write_text("unrecorded commit")
    _commit(main / paths[0])
    helper = _helper()
    with pytest.raises(helper.SubmoduleError):
        helper.selection(main, paths)


def test_resume_rejects_replaced_private_gitdir_even_at_same_path(tmp_path, monkeypatch):
    import pytest
    import shutil
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    module = work / paths[0]
    gitdir = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    moved = gitdir.with_name(gitdir.name + "-original")
    gitdir.rename(moved)
    shutil.copytree(moved, gitdir)
    with pytest.raises(helper.SubmoduleError):
        helper.validate(main, work, value)
    assert moved.exists() and gitdir.exists()


def test_preserve_full_manifest_size_refuses_before_destination_writes(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    initial = value["modules"][0]["initial_gitlink"]
    changed = _change(work, paths[0], "new private object\n")
    module, destination = work / paths[0], main / paths[0]

    # Choose the boundary from actual Git inventory. The pending inventory fits,
    # but publishing its nonempty pin mapping must also fit before any effects.
    # No assumption about pin-name construction is needed.
    pending = copy.deepcopy(value)
    pending["modules"][0]["inventory"] = {
        "refs": dict(line.split(" ", 1) for line in
                     _git(module, "for-each-ref", "--format=%(refname) %(objectname)").splitlines()),
        "reflogs": sorted(set(_git(module, "reflog", "show", "--all", "--format=%H").splitlines())),
        "gitlinks": sorted({initial, changed}),
    }
    monkeypatch.setattr(helper, "MAX_MANIFEST_BYTES", len(json.dumps(pending).encode()))
    helper.validate_manifest(pending)
    before_refs = _git(destination, "for-each-ref", "--format=%(refname) %(objectname)")
    before_objects = _git(destination, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)")
    effects = []
    original_git = helper._git

    def observe_git(root, *args, **kwargs):
        if os.fspath(root) == os.fspath(destination) and args[0] in ("fetch", "update-ref"):
            effects.append(args[0])
        return original_git(root, *args, **kwargs)

    monkeypatch.setattr(helper, "_git", observe_git)
    with pytest.raises(helper.SubmoduleError, match="manifest exceeds bound"):
        helper.preserve(main, work, value, lambda _: None)
    assert effects == []
    assert _git(destination, "for-each-ref", "--format=%(refname) %(objectname)") == before_refs
    assert _git(destination, "cat-file", "--batch-all-objects", "--batch-check=%(objectname)") == before_objects


def test_embedded_main_module_store_prepares_preserves_and_reconciles(tmp_path, monkeypatch):
    import shutil
    main, work, paths = _setup(tmp_path, monkeypatch)
    module = main / paths[0]
    old_store = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    _git(module, "config", "--unset", "core.worktree")
    (module / ".git").unlink()
    shutil.move(str(old_store), str(module / ".git"))
    assert Path(_git(module, "rev-parse", "--absolute-git-dir")) == module / ".git"
    config = (module / ".git/config").read_bytes()
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    assert value["modules"][0]["main_gitdir"] == str(module / ".git")
    helper.prepare(main, work, value, lambda _: None)
    assert Path(_git(work / paths[0], "rev-parse", "--absolute-git-dir")).is_relative_to(
        Path(_git(work, "rev-parse", "--absolute-git-dir")))
    tip = _change(work, paths[0], "embedded module development\n")
    helper.preserve(main, work, value, lambda _: None)
    witness = helper.witness(main, work, value, "refs/heads/main",
                             _git(main, "rev-parse", "HEAD"), _git(work, "rev-parse", "HEAD"))
    _git(main, "merge", "--ff-only", "lead")
    helper.reconcile_checkout(main, value, witness)
    assert _git(module, "rev-parse", "HEAD") == tip
    assert _git(main, "status", "--porcelain") == ""
    helper.removal_proof(main, work, value)
    _git(module, "reflog", "expire", "--expire=now", "--all")
    _git(module, "gc", "--prune=now")
    assert _git(module, "cat-file", "-t", tip) == "commit"
    assert (module / ".git/config").read_bytes() == config


def test_main_module_external_gitdir_remains_unsupported(tmp_path, monkeypatch):
    import shutil
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    module = main / paths[0]
    old_store = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    _git(module, "config", "core.worktree", str(module))
    foreign_store = tmp_path / "foreign-module-store"
    shutil.move(str(old_store), str(foreign_store))
    # Git for Windows marks this pointer hidden. Update the existing fixture
    # in place so CREATE_ALWAYS does not reject or replace its file identity.
    with (module / ".git").open("r+", encoding="utf-8") as pointer:
        pointer.write(f"gitdir: {foreign_store}\n")
        pointer.truncate()
    # This is a working repository, not an incidental broken Git binding.
    assert Path(_git(module, "rev-parse", "--show-toplevel")) == module
    assert _git(module, "status", "--porcelain") == ""
    helper = _helper()
    with pytest.raises(helper.SubmoduleError, match="foreign main module gitdir"):
        helper.selection(main, paths)
    assert foreign_store.is_dir() and (module / ".git").is_file()


def test_selection_and_preparation_accept_literal_and_unicode_module_paths(tmp_path, monkeypatch, selected_name):
    main, work, paths = _setup(tmp_path, monkeypatch)
    _git(main, "--literal-pathspecs", "mv", "--", paths[0], selected_name)
    _commit(main, "literal module path")
    _git(work, "rebase", "main")
    helper = _helper()
    selected = helper.selection(main, [selected_name])
    assert [entry["path"] for entry in selected] == [selected_name]
    value = _manifest(helper, main, work, [selected_name])
    helper.prepare(main, work, value, lambda _: None)
    assert _git(work / selected_name, "rev-parse", "HEAD") == _git(main / selected_name, "rev-parse", "HEAD")
    helper.validate(main, work, value)


def test_removal_refuses_unknown_private_gitdir_file_without_discard(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    helper.preserve(main, work, value, lambda _: None)
    private = Path(_git(work / paths[0], "rev-parse", "--absolute-git-dir"))
    precious = private / "unpublished-design.txt"
    precious.write_bytes(b"unique bytes only in private administration\x00\xff")
    before = precious.read_bytes()
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)
    assert precious.read_bytes() == before and work.exists()


def test_preservation_fetch_failure_retains_source_and_retry_preserves_all_objects(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    tip = _change(work, paths[0])
    before = (work / paths[0] / "file.txt").read_bytes()
    original = subprocess.run
    seen = []
    def fail_fetch(command, *args, **kwargs):
        if Path(command[0]).name.lower() in {"git", "git.exe"} and "fetch" in command:
            seen.append(command)
            return subprocess.CompletedProcess(command, 128, b"", b"injected local fetch failure")
        return original(command, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", fail_fetch)
        with pytest.raises(helper.SubmoduleError):
            helper.preserve(main, work, value, lambda _: None)
    assert seen and work.exists()
    assert (work / paths[0] / "file.txt").read_bytes() == before
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)
    helper.preserve(main, work, value, lambda _: None)
    assert _git(main / paths[0], "cat-file", "-t", tip) == "commit"
    helper.removal_proof(main, work, value)


def test_preservation_collision_never_overwrites_existing_pin(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    tip = _change(work, paths[0])
    helper.preserve(main, work, value, lambda _: None)
    destination = main / paths[0]
    ref = value["modules"][0]["pins"][tip]
    other = _git(destination, "rev-parse", "HEAD")
    assert other != tip
    _git(destination, "update-ref", ref, other)
    with pytest.raises(helper.SubmoduleError):
        helper.preserve(main, work, value, lambda _: None)
    assert _git(destination, "rev-parse", ref) == other
    assert _git(work / paths[0], "rev-parse", "HEAD") == tip
    assert work.exists()


def test_partial_pin_publication_retries_exactly_without_overwriting_published_refs(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    first = _change(work, paths[0], "first\n")
    second = _change(work, paths[0], "second\n")
    original = subprocess.run
    published = []
    durable = copy.deepcopy(value)
    def persist(item):
        nonlocal durable
        durable = copy.deepcopy(item)
    def interrupt_second_pin(command, *args, **kwargs):
        if Path(command[0]).name.lower() in {"git", "git.exe"} and "update-ref" in command:
            if published:
                raise OSError("injected interruption after one durable pin")
            result = original(command, *args, **kwargs)
            assert result.returncode == 0, result.stderr
            position = command.index("update-ref")
            published.append((command[position + 1], command[position + 2]))
            return result
        return original(command, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", interrupt_second_pin)
        with pytest.raises(helper.SubmoduleError):
            helper.preserve(main, work, value, persist)
    assert len(published) == 1 and work.exists()
    ref, oid = published[0]
    assert _git(main / paths[0], "rev-parse", ref) == oid
    def disallow_rewrite(command, *args, **kwargs):
        if Path(command[0]).name.lower() in {"git", "git.exe"} and "update-ref" in command:
            assert command[command.index("update-ref") + 1] != ref, "already durable pin must not be rewritten"
        return original(command, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", disallow_rewrite)
        helper.preserve(main, work, durable, lambda _: None)
    helper.removal_proof(main, work, durable)
    for commit in (first, second):
        assert _git(main / paths[0], "cat-file", "-t", commit) == "commit"


def test_owned_but_incomplete_clone_is_retained_without_retry_overwrite(tmp_path, monkeypatch):
    import pytest
    main, work, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    durable = copy.deepcopy(value)
    original = subprocess.run
    clone_attempted = []
    class ProcessDied(Exception):
        pass
    def persist(item):
        nonlocal durable
        durable = copy.deepcopy(item)
    def crash_clone(command, *args, **kwargs):
        if Path(command[0]).name.lower() in {"git", "git.exe"} and "clone" in command:
            clone_attempted.append(command)
            # A real clone creates the owned metadata; interruption precedes
            # named-branch checkout and completion publication.
            result = original(command, *args, **kwargs)
            assert result.returncode == 0, result.stderr
            raise ProcessDied
        return original(command, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", crash_clone)
        with pytest.raises(ProcessDied):
            helper.prepare(main, work, value, persist)
    assert durable["modules"][0]["phase"] == "owned"
    assert len(clone_attempted) == 1
    gitdir = Path(durable["modules"][0]["private_gitdir"])
    precious = gitdir / "incomplete-clone-note"
    precious.write_bytes(b"never overwrite this interrupted clone")
    identity = gitdir.stat().st_ino
    gitfile = (work / paths[0] / ".git").read_bytes()
    with pytest.raises(helper.SubmoduleError):
        helper.prepare(main, work, durable, lambda _: None)
    assert gitdir.stat().st_ino == identity
    assert precious.read_bytes() == b"never overwrite this interrupted clone"
    assert (work / paths[0] / ".git").read_bytes() == gitfile


def test_removal_refuses_unknown_private_parent_sibling_without_discard(tmp_path, monkeypatch):
    import pytest
    helper, main, work, _, value, _ = _prepared(tmp_path, monkeypatch)
    helper.preserve(main, work, value, lambda _: None)
    private = Path(value["modules"][0]["private_gitdir"])
    precious = private.parent / "unpublished-parent-sibling"
    precious.write_bytes(b"unique private parent data")
    with pytest.raises(helper.SubmoduleError):
        helper.removal_proof(main, work, value)
    assert precious.read_bytes() == b"unique private parent data" and work.exists()


def test_full_batch_state_limit_is_checked_before_any_preservation_effect(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    import batch_state
    _change(work, paths[0])
    unpreserved = copy.deepcopy(value)
    # Establish the exact deterministic intent size using a complete scratch
    # preservation, then remove only this fixture's pins before the bounded run.
    helper.preserve(main, work, value, lambda _: None)
    full_intent = copy.deepcopy(value)
    destination = main / paths[0]
    for pin in value["modules"][0]["pins"].values():
        _git(destination, "update-ref", "-d", pin)
    inventory_only = copy.deepcopy(full_intent)
    for module in inventory_only["modules"]:
        module["pins"] = None
    limit = 1024 * 1024
    def payload(item, padding):
        return {"requests": [{"request": "x" * padding, "submodule_manifest": item}]}
    def encoded_size(body):
        return len((json.dumps(body, indent=2, sort_keys=True) + "\n").encode())
    padding = limit - encoded_size(payload(inventory_only, 0)) - 16
    assert padding > 0
    assert encoded_size(payload(inventory_only, padding)) <= limit
    assert encoded_size(payload(full_intent, padding)) > limit
    state_file = tmp_path / "bounded-batch-state.json"
    batch_state.write_json(str(state_file), payload(unpreserved, padding))
    before = state_file.read_bytes()
    refs_before = _git(destination, "for-each-ref", "--format=%(refname) %(objectname)")
    original = subprocess.run
    effects = []
    def observe(command, *args, **kwargs):
        if Path(command[0]).name.lower() in {"git", "git.exe"} and ("fetch" in command or "update-ref" in command):
            effects.append(command)
        return original(command, *args, **kwargs)
    def persist(item):
        batch_state.write_json(str(state_file), payload(item, padding))
    with monkeypatch.context() as patch:
        patch.setattr(subprocess, "run", observe)
        with pytest.raises(batch_state.Refusal):
            helper.preserve(main, work, unpreserved, persist)
    assert effects == []
    assert state_file.read_bytes() == before
    assert _git(destination, "for-each-ref", "--format=%(refname) %(objectname)") == refs_before
    assert work.exists()


def test_selection_refuses_hidden_tracked_module_edits(tmp_path, monkeypatch):
    import pytest
    main, _, paths = _setup(tmp_path, monkeypatch)
    helper = _helper()
    module = main / paths[0]
    for flag in ("assume-unchanged", "skip-worktree"):
        _git(module, "update-index", "--" + flag, "file.txt")
        precious = "unique hidden selection work " + flag
        (module / "file.txt").write_text(precious)
        assert _git(module, "status", "--porcelain") == ""
        with pytest.raises(helper.SubmoduleError):
            helper.selection(main, paths)
        assert (module / "file.txt").read_text() == precious
        _git(module, "update-index", "--no-" + flag, "file.txt")
        _git(module, "checkout", "--", "file.txt")


def test_removal_refuses_hidden_tracked_module_edits(tmp_path, monkeypatch):
    import pytest
    helper, main, work, paths, value, _ = _prepared(tmp_path, monkeypatch)
    helper.preserve(main, work, value, lambda _: None)
    module = work / paths[0]
    for flag in ("assume-unchanged", "skip-worktree"):
        _git(module, "update-index", "--" + flag, "file.txt")
        precious = "unique hidden disposable work " + flag
        (module / "file.txt").write_text(precious)
        assert _git(module, "status", "--porcelain") == ""
        with pytest.raises(helper.SubmoduleError):
            helper.removal_proof(main, work, value)
        assert work.exists() and (module / "file.txt").read_text() == precious
        _git(module, "update-index", "--no-" + flag, "file.txt")
        _git(module, "checkout", "--", "file.txt")


def test_landed_witness_refuses_missing_or_changed_pin_before_checkout(tmp_path, monkeypatch):
    import pytest
    helper, main, _, paths, value, proof, targets = _landed(tmp_path, monkeypatch)
    module = main / paths[0]
    target = targets[0]
    old_head = _git(module, "rev-parse", "HEAD")
    old_bytes = (module / "file.txt").read_bytes()
    pin = value["modules"][0]["pins"][target]
    assert old_head != target
    # The target still resolves throughout: only the authoritative pin is lost.
    for replacement in (None, old_head):
        if replacement is None:
            _git(module, "update-ref", "-d", pin)
        else:
            _git(module, "update-ref", pin, replacement)
        assert _git(module, "cat-file", "-t", target) == "commit"
        with pytest.raises(helper.SubmoduleError):
            helper.reconcile_checkout(main, value, proof)
        assert _git(module, "rev-parse", "HEAD") == old_head
        assert (module / "file.txt").read_bytes() == old_bytes
    _git(module, "update-ref", pin, target)
    helper.reconcile_checkout(main, value, proof)
    assert _git(module, "rev-parse", "HEAD") == target


def test_preexisting_annotated_tag_survives_independent_clone_and_normal_disposal_proof(tmp_path, monkeypatch):
    main, work, paths = _setup(tmp_path, monkeypatch)
    source = main / paths[0]
    _git(source, "tag", "-a", "before-preparation", "-m", "preexisting annotation")
    tag = _git(source, "rev-parse", "refs/tags/before-preparation")
    helper = _helper()
    value = _manifest(helper, main, work, paths)
    helper.prepare(main, work, value, lambda _: None)
    module = work / paths[0]
    tip = _change(work, paths[0])
    helper.preserve(main, work, value, lambda _: None)
    helper.removal_proof(main, work, value)
    assert _git(source, "cat-file", "-t", tag) == "tag"
    assert _git(source, "cat-file", "-t", tip) == "commit"
    private = Path(_git(module, "rev-parse", "--absolute-git-dir"))
    source_gitdir = Path(_git(source, "rev-parse", "--absolute-git-dir"))
    assert private != source_gitdir
    assert not (private / "objects/info/alternates").exists()
    for obj in (private / "objects").rglob("*"):
        peer = source_gitdir / "objects" / obj.relative_to(private / "objects")
        if obj.is_file() and peer.is_file():
            assert (obj.stat().st_dev, obj.stat().st_ino) != (peer.stat().st_dev, peer.stat().st_ino)


def _conversion_fixture(tmp_path, monkeypatch):
    """Fresh policy fixtures only; never alter retained Native diagnostic repos."""
    home = tmp_path / "conversion-home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in list(os.environ):
        if key.upper().startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)
    _git(tmp_path, "config", "--global", "core.autocrlf", "true")
    _git(tmp_path, "config", "--global", "core.eol", "crlf")
    return _helper(), _repo(tmp_path / "conversion-source"), home


def _conversion_flags(policy):
    return [arg for key, value in policy.items() for arg in ("-c", key + "=" + value)]


def _isolated_conversion_git(root, *args, policy=None):
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull,
               GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "--no-optional-locks", *_conversion_flags(policy or {}),
         "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull, *args],
        cwd=root, env=env, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_conversion_policy_preserves_crlf_cleanliness_and_detects_real_content_changes(tmp_path, monkeypatch):
    helper, source, home = _conversion_fixture(tmp_path, monkeypatch)
    clone = tmp_path / "ordinary-crlf-clone"
    _git(tmp_path, "clone", "--template=", "--no-local", "--no-hardlinks", str(source), str(clone))
    assert (clone / "file.txt").read_bytes() == b"initial\r\n"
    assert _isolated_conversion_git(clone, "show", "HEAD:file.txt") == b"initial\n"
    assert b"file.txt" in _isolated_conversion_git(clone, "status", "--porcelain")
    before = {path: path.read_bytes() for path in
              (source / ".git/config", clone / ".git/config", home / ".gitconfig")}
    policy = helper._conversion_policy(clone)
    assert policy == {"core.autocrlf": "true", "core.eol": "crlf"}
    assert _isolated_conversion_git(clone, "status", "--porcelain", policy=policy) == b""
    (clone / "file.txt").write_bytes(b"real content change\r\n")
    assert b"file.txt" in _isolated_conversion_git(clone, "status", "--porcelain", policy=policy)
    assert {path: path.read_bytes() for path in before} == before
    assert _isolated_conversion_git(clone, "show", "HEAD:file.txt") == b"initial\n"


def test_conversion_policy_uses_selected_source_for_clone_when_outer_repository_differs(tmp_path, monkeypatch):
    helper, source, home = _conversion_fixture(tmp_path, monkeypatch)
    outer = _repo(tmp_path / "outer-repository")
    _git(outer, "config", "core.autocrlf", "false")
    _git(outer, "config", "core.eol", "lf")
    source_policy = helper._conversion_policy(source)
    outer_policy = helper._conversion_policy(outer)
    assert source_policy == {"core.autocrlf": "true", "core.eol": "crlf"}
    assert outer_policy == {"core.autocrlf": "false", "core.eol": "lf"}
    before = {path: path.read_bytes() for path in
              (source / ".git/config", outer / ".git/config", home / ".gitconfig")}
    clone = tmp_path / "selected-source-clone"
    clone_config = [arg for key, value in source_policy.items() for arg in ("--config", key + "=" + value)]
    _git(outer, *_conversion_flags(outer_policy), *_conversion_flags(source_policy),
         "clone", "--template=", "--no-local", "--no-hardlinks", *clone_config, str(source), str(clone))
    assert (clone / "file.txt").read_bytes() == b"initial\r\n"
    assert _git(clone, "config", "--local", "--get", "core.autocrlf") == "true"
    assert _git(clone, "config", "--local", "--get", "core.eol") == "crlf"
    assert _isolated_conversion_git(clone, "status", "--porcelain") == b""
    assert {path: path.read_bytes() for path in before} == before


def test_conversion_policy_honors_git_boolean_aliases_implicit_and_empty_values(
        tmp_path, monkeypatch, conversion_boolean):
    helper, source, home = _conversion_fixture(tmp_path, monkeypatch)
    raw, expected = conversion_boolean
    if raw is None:
        with (source / ".git/config").open("a", encoding="utf-8") as stream:
            stream.write("\n[core]\n\tautocrlf\n")
    else:
        _git(source, "config", "core.autocrlf", raw)
    before = {path: path.read_bytes() for path in (source / ".git/config", home / ".gitconfig")}
    assert helper._conversion_policy(source)["core.autocrlf"] == expected
    assert {path: path.read_bytes() for path in before} == before


def test_conversion_policy_refuses_invalid_values_without_rewriting_config(
        tmp_path, monkeypatch, invalid_conversion_key):
    import pytest

    helper, source, home = _conversion_fixture(tmp_path, monkeypatch)
    _git(source, "config", invalid_conversion_key, "unrecognized-setting")
    before = {path: path.read_bytes() for path in (source / ".git/config", home / ".gitconfig")}
    with pytest.raises(helper.SubmoduleError, match="conversion policy"):
        helper._conversion_policy(source)
    assert {path: path.read_bytes() for path in before} == before
