"""Scratch-repo tests for plugin/scripts/batch_preflight.py.

Every test builds throwaway repositories under ``tmp_path`` and points HOME at
an empty directory there, so a developer's global git config (hooksPath,
status settings) cannot change what the preflight sees.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "plugin/scripts/batch_preflight.py"
SPEC = importlib.util.spec_from_file_location("batch_preflight", SCRIPT)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

IDENTITY = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _isolate(monkeypatch, tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(home / ".config"))
    for key in list(os.environ):
        if key.upper().startswith("GIT_"):
            monkeypatch.delenv(key, raising=False)


def _git(*args, cwd) -> str:
    result = subprocess.run(
        ["git", "-c", "protocol.file.allow=always", *args], cwd=cwd,
        capture_output=True, text=True, check=False, env={**os.environ, **IDENTITY},
    )
    assert result.returncode == 0, f"git {args} failed: {result.stderr}"
    return result.stdout.strip()


def _repo(path: Path, files: dict | None = None, *, ignore_worktrees: bool = True) -> Path:
    """A committed repository; like every harness project (manifest v7), it
    ignores `.claude/worktrees/` unless ``ignore_worktrees`` is False."""
    path.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", "-b", "main", cwd=path)
    files = dict(files or {"README.md": "root\n"})
    if ignore_worktrees:
        files[".gitignore"] = files.get(".gitignore", "") + ".claude/worktrees/\n"
    for rel, text in files.items():
        target = path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    _git("add", "-A", cwd=path)
    _git("commit", "-q", "-m", "init", cwd=path)
    return path


def _with_submodule(tmp_path: Path) -> Path:
    """Superproject `main` with a populated submodule at `libs/sub`."""
    source = _repo(tmp_path / "subsrc", {"s.txt": "s\n"})
    main = _repo(tmp_path / "main", {"README.md": "root\n", "libs/keep.txt": "k\n"})
    _git("submodule", "add", "-q", str(source), "libs/sub", cwd=main)
    _git("commit", "-q", "-m", "add submodule", cwd=main)
    return main


def _with_nested(tmp_path: Path) -> Path:
    """Repo `main` with an ignored nested repository at `repos/svc`."""
    main = _repo(tmp_path / "main", {"README.md": "root\n", ".gitignore": "repos/\n"})
    _repo(main / "repos/svc", {"svc.txt": "svc\n"})
    return main


def _write_hook(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _scope(report: dict, path: str) -> dict:
    return next(s for s in report["scopes"] if s["path"] == path)


def _tree_digest(root: Path) -> dict:
    digest = {}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            full = Path(dirpath) / name
            if full.is_symlink():
                digest[str(full)] = "link:" + os.readlink(full)
            else:
                digest[str(full)] = hashlib.sha256(full.read_bytes()).hexdigest()
    return digest


# ── Control-root shape ───────────────────────────────────────────────────


def pytest_generate_tests(metafunc):
    if "location" in metafunc.fixturenames:
        metafunc.parametrize("location", ["main", "nested", "submodule"])


def test_hidden_untracked_files_refuse_in_every_repository(monkeypatch, tmp_path, location):
    _isolate(monkeypatch, tmp_path)
    if location == "nested":
        main = _with_nested(tmp_path)
        target, kind, rel = main / "repos/svc", "nested-repo", "repos/svc"
    elif location == "submodule":
        main = _with_submodule(tmp_path)
        target, kind, rel = main / "libs/sub", "submodule", "libs/sub"
        _git("config", "submodule.libs/sub.ignore", "all", cwd=main)
    else:
        main = _repo(tmp_path / "main")
        target, kind, rel = main, "control-root", "."
    _git("config", "status.showUntrackedFiles", "no", cwd=target)
    (target / "untracked.txt").write_text("irreplaceable\n", encoding="utf-8")
    assert _git("status", "--porcelain", cwd=target) == ""
    before = _tree_digest(tmp_path)

    report = mod.preflight(str(main), {"a": ["README.md"]})

    assert report["verdict"] == "refuse", report
    assert (rel, kind) in {(item["repo"], item["kind"]) for item in report["dirty"]}
    assert _tree_digest(tmp_path) == before


def test_plain_repo_with_disjoint_scopes_is_ok(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"src/a.py": "a\n", "docs/b.md": "b\n"})
    report = mod.preflight(str(main), {"a": ["src/a.py", "src/new_file.py"], "b": ["docs"]})
    assert report["verdict"] == "ok", report
    assert report["control_root"]["shape"] == "ok"
    assert {s["class"] for s in report["scopes"]} == {"tracked-area"}
    assert report["submodules"] == [] and report["nested_repos"] == []
    assert report["off_limits"] == []
    assert report["dirty"] == [] and report["overlaps"] == [] and report["refusals"] == []
    assert report["worktrees_ignore"] == {
        "path": ".claude/worktrees/",
        "git_path": ".claude/worktrees/__harness_probe__",
        "status": "ignored",
    }


def test_linked_worktree_as_control_root_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    _git("worktree", "add", "-q", "-b", "side", str(tmp_path / "wt"), cwd=main)
    report = mod.preflight(str(tmp_path / "wt"), {"a": ["README.md"]})
    assert report["control_root"]["shape"] == "linked-worktree"
    assert report["verdict"] == "refuse" and report["refusals"]
    # The refusal points at the checkout to rerun from.
    assert f"main checkout ({os.path.realpath(main)})" in report["control_root"]["reason"]
    # Every key is present even on an early refusal.
    assert set(report) == set(mod._empty_report("/x"))


def test_submodule_checkout_as_control_root_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    report = mod.preflight(str(main / "libs/sub"), {})
    assert report["control_root"]["shape"] == "submodule-checkout"
    assert report["verdict"] == "refuse"


def test_non_git_roots_refuse(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    plain = tmp_path / "plain"
    _repo(plain / "child")  # a non-git parent holding a child repository
    for candidate in (plain, tmp_path / "missing"):
        report = mod.preflight(str(candidate), {"a": ["child"]})
        assert report["control_root"]["shape"] == "non-git", report
        assert report["verdict"] == "refuse"
    # A subdirectory of a checkout is not a control root either.
    main = _repo(tmp_path / "main", {"src/a.py": "a\n"})
    report = mod.preflight(str(main / "src"), {})
    assert report["control_root"]["shape"] == "non-git"
    assert "top of a git work tree" in report["control_root"]["reason"]


def test_separate_git_dir_checkout_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = tmp_path / "main"
    main.mkdir()
    _git("init", "-q", "--separate-git-dir", str(tmp_path / "store.git"), cwd=main)
    (main / "README.md").write_text("x\n", encoding="utf-8")
    _git("add", "README.md", cwd=main)
    _git("commit", "-q", "-m", "init", cwd=main)
    report = mod.preflight(str(main), {})
    assert report["control_root"]["shape"] == "separate-git-dir"
    assert report["verdict"] == "refuse"
    # A linked worktree of it names no guessed main-checkout path: the parent
    # of a separate git dir is not the main checkout.
    _git("worktree", "add", "-q", "-b", "side", str(tmp_path / "wt"), cwd=main)
    linked = mod.preflight(str(tmp_path / "wt"), {})
    assert linked["control_root"]["shape"] == "linked-worktree"
    assert "main checkout (" not in linked["control_root"]["reason"]


# ── Submodules and nested repositories ───────────────────────────────────


def test_submodule_is_reported_and_scope_inside_it_is_excluded(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    report = mod.preflight(
        str(main),
        {"inside": ["libs/sub/s.txt"], "around": ["libs/keep.txt"], "parent": ["libs"]},
    )
    assert report["submodules"] == [
        {"path": "libs/sub", "gitlink": True, "in_gitmodules": True, "populated": True}
    ]
    assert report["off_limits"] == ["libs/sub"]
    inside = _scope(report, "libs/sub/s.txt")
    assert (inside["class"], inside["owner"]) == ("inside-submodule", "libs/sub")
    assert _scope(report, "libs/keep.txt")["class"] == "tracked-area"
    assert _scope(report, "libs")["class"] == "tracked-area"  # an ancestor is not inside
    assert list(report["excluded_requests"]) == ["inside"]
    [reason] = report["excluded_requests"]["inside"]
    assert "ordinary task in the main checkout" in reason
    assert report["dirty"] == [] and report["refusals"] == []
    assert report["verdict"] == "adjust"
    # An excluded request never runs in a wave, so it is never paired; the
    # two tracked requests (`libs` contains `libs/keep.txt`) still are.
    assert report["overlaps"] == [
        {"requests": ["around", "parent"], "paths": ["libs/keep.txt", "libs"]}
    ]


def test_unpopulated_submodule_is_still_classified(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    clone = tmp_path / "clone"
    _git("clone", "-q", str(main), str(clone), cwd=tmp_path)
    report = mod.preflight(str(clone), {"a": ["libs/sub"]})
    assert report["submodules"][0]["populated"] is False
    assert _scope(report, "libs/sub")["class"] == "inside-submodule"
    assert report["dirty"] == [] and report["refusals"] == []


def test_submodule_sources_are_merged_and_unsafe_paths_dropped(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    (main / ".gitmodules").write_text(
        '[submodule "declared"]\n\tpath = vendor/declared\n\turl = ./x\n'
        '[submodule "evil"]\n\tpath = ../evil\n\turl = ./y\n',
        encoding="utf-8",
    )
    oid = _git("rev-parse", "HEAD", cwd=main)
    _git("update-index", "--add", "--cacheinfo", f"160000,{oid},libs/raw", cwd=main)
    (main / "libs/raw").mkdir(parents=True)  # what a clone leaves for an unpopulated gitlink
    _git("add", ".gitmodules", cwd=main)
    _git("commit", "-q", "-m", "gitlink and declaration", cwd=main)
    report = mod.preflight(str(main), {})
    assert report["submodules"] == [
        {"path": "libs/raw", "gitlink": True, "in_gitmodules": False, "populated": False},
        {"path": "vendor/declared", "gitlink": False, "in_gitmodules": True, "populated": False},
    ]
    assert report["verdict"] == "ok", report


def test_ignored_nested_repo_is_listed_and_scope_inside_it_is_excluded(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_nested(tmp_path)
    report = mod.preflight(str(main), {"svc": ["repos/svc/svc.txt"], "root": ["README.md"]})
    assert report["nested_repos"] == ["repos/svc"]
    assert report["off_limits"] == ["repos/svc"]
    scope = _scope(report, "repos/svc/svc.txt")
    assert (scope["class"], scope["owner"]) == ("inside-ignored-nested-repo", "repos/svc")
    assert list(report["excluded_requests"]) == ["svc"]
    assert report["dirty"] == []
    assert report["verdict"] == "adjust"


def test_deep_and_gitfile_nested_repos_are_found(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(
        tmp_path / "main",
        {"src/a.py": "a\n", ".gitignore": "src/vendor/\nign/\nlinked\n"},
    )
    _repo(main / "ign/deep/nested")
    other = _repo(tmp_path / "other")
    # A linked worktree of an unrelated repository: its `.git` is a gitfile.
    _git("worktree", "add", "-q", "-b", "x", str(main / "src/vendor/lib"), cwd=other)
    # An ignored symlink to a repository is never walked.
    (main / "linked").symlink_to(other)
    report = mod.preflight(str(main), {})
    assert report["nested_repos"] == ["ign/deep/nested", "src/vendor/lib"]
    assert report["verdict"] == "ok", report


def test_registered_lead_worktree_under_root_is_not_a_nested_repo(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "r\n", ".gitignore": ".claude/worktrees/\n"})
    lead = main / ".claude/worktrees/agent-x"
    _git("worktree", "add", "-q", "-b", "lead", str(lead), cwd=main)
    (lead / "wip.txt").write_text("lead work in progress\n", encoding="utf-8")
    report = mod.preflight(str(main), {"a": [".claude/worktrees/agent-x/README.md"]})
    assert report["nested_repos"] == []
    assert report["dirty"] == []
    assert _scope(report, ".claude/worktrees/agent-x/README.md")["class"] == (
        "inside-ignored-nested-repo"
    )


def test_post_checkout_hook_mentioning_submodule_refuses_only_with_submodules(
    monkeypatch, tmp_path,
):
    _isolate(monkeypatch, tmp_path)
    hook_text = "#!/bin/sh\ngit Submodule update --init --recursive\n"

    plain = _repo(tmp_path / "plain")
    _write_hook(plain / ".git/hooks/post-checkout", hook_text)
    report = mod.preflight(str(plain), {})
    assert report["post_checkout_hooks"][0]["mentions_submodule"] is True
    assert report["verdict"] == "ok", report

    main = _with_submodule(tmp_path)
    _write_hook(main / ".git/hooks/post-checkout", hook_text)
    report = mod.preflight(str(main), {})
    assert report["verdict"] == "refuse"
    assert report["dirty"] == []
    assert [r for r in report["refusals"] if "post-checkout" in r], report["refusals"]


def test_hooks_path_post_checkout_is_checked(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    _write_hook(main / ".githooks/post-checkout", "#!/bin/sh\ngit submodule update --init\n")
    _git("add", ".githooks", cwd=main)
    _git("commit", "-q", "-m", "hooks", cwd=main)
    _git("config", "core.hooksPath", ".githooks", cwd=main)
    report = mod.preflight(str(main), {})
    hooks = {Path(h["path"]).parent.name: h for h in report["post_checkout_hooks"]}
    assert hooks[".githooks"]["mentions_submodule"] is True
    assert report["dirty"] == []
    assert report["verdict"] == "refuse"
    assert [r for r in report["refusals"] if ".githooks" in r], report["refusals"]


# ── Cleanliness ──────────────────────────────────────────────────────────


def test_dirty_control_root_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    (main / "untracked.txt").write_text("x\n", encoding="utf-8")
    report = mod.preflight(str(main), {})
    assert [(d["repo"], d["kind"]) for d in report["dirty"]] == [(".", "control-root")]
    assert report["verdict"] == "refuse"


def test_dirty_ignored_nested_repo_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_nested(tmp_path)
    (main / "repos/svc/new.txt").write_text("uncommitted\n", encoding="utf-8")
    assert _git("status", "--porcelain", cwd=main) == ""  # plain status misses it
    report = mod.preflight(str(main), {})
    assert [(d["repo"], d["kind"]) for d in report["dirty"]] == [("repos/svc", "nested-repo")]
    assert report["verdict"] == "refuse"


def test_dirty_submodule_refuses_even_when_superproject_ignores_it(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    _git("config", "submodule.libs/sub.ignore", "all", cwd=main)
    (main / "libs/sub/s.txt").write_text("changed\n", encoding="utf-8")
    assert _git("status", "--porcelain", cwd=main) == ""  # plain status hides it
    report = mod.preflight(str(main), {})
    assert ("libs/sub", "submodule") in {(d["repo"], d["kind"]) for d in report["dirty"]}
    assert report["verdict"] == "refuse"


def test_nested_repo_with_broken_metadata_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "r\n", ".gitignore": "repos/\n"})
    (main / "repos/broken").mkdir(parents=True)
    (main / "repos/broken/.git").write_text("gitdir: /nonexistent/store\n", encoding="utf-8")
    # A lead worktree whose registration was pruned: its gitfile names a
    # missing `<repo>/.git/worktrees/<name>`.
    (main / "repos/pruned").mkdir(parents=True)
    (main / "repos/pruned/.git").write_text(
        f"gitdir: {main / '.git/worktrees/pruned'}\n", encoding="utf-8",
    )
    report = mod.preflight(str(main), {})
    assert report["nested_repos"] == ["repos/broken", "repos/pruned"]
    assert report["verdict"] == "refuse"
    [broken] = [r for r in report["refusals"] if "repos/broken" in r]
    # Only a gitfile that names a missing worktree registration earns the
    # "remove the directory" hint; anything else must not suggest deleting.
    assert "remove the directory" not in broken
    import pytest

    with pytest.raises(mod.PreflightError) as pruned:
        mod.status_entries(str(main / "repos/pruned"))
    assert "missing worktree registration" in str(pruned.value)
    assert "remove the directory" in str(pruned.value)


# ── Lead worktree directory ignore (SKILL step b.3) ──────────────────────


def _plain_check_ignore(repo: Path) -> int:
    return subprocess.run(
        ["git", "check-ignore", "-q", ".claude/worktrees/x"], cwd=repo,
        capture_output=True, check=False,
    ).returncode


def test_worktrees_dir_must_be_ignored(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", ignore_worktrees=False)
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["worktrees_ignore"]["status"] == "not-ignored"
    assert report["verdict"] == "refuse"
    [refusal] = report["refusals"]
    assert "`.claude/worktrees/` to .gitignore" in refusal
    assert "git sees" not in refusal  # no symlink: git sees the path itself
    # An ignore from .git/info/exclude counts, as it does for git.
    (main / ".git/info/exclude").write_text(".claude/worktrees/\n", encoding="utf-8")
    assert mod.preflight(str(main), {"a": ["README.md"]})["verdict"] == "ok"


def test_symlinked_claude_outside_the_repo_passes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    outside = tmp_path / "claude-config"
    (outside / "worktrees").mkdir(parents=True)
    main = _repo(tmp_path / "main", ignore_worktrees=False)
    (main / ".claude").symlink_to(outside)
    _git("add", ".claude", cwd=main)
    _git("commit", "-q", "-m", "shared claude config", cwd=main)
    assert _plain_check_ignore(main) == 128  # "beyond a symbolic link"
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["worktrees_ignore"] == {
        "path": ".claude/worktrees/", "git_path": None, "status": "outside-repo",
    }
    assert report["verdict"] == "ok", report


def test_symlinked_worktrees_dir_outside_the_repo_passes(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    outside = tmp_path / "lead-worktrees"
    outside.mkdir()
    main = _repo(tmp_path / "main", {"README.md": "r\n", ".claude/settings.json": "{}\n"},
                 ignore_worktrees=False)
    (main / ".claude/worktrees").symlink_to(outside)
    _git("add", ".claude/worktrees", cwd=main)
    _git("commit", "-q", "-m", "worktrees elsewhere", cwd=main)
    assert _plain_check_ignore(main) == 128
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["worktrees_ignore"]["status"] == "outside-repo"
    assert report["verdict"] == "ok", report


def test_symlinked_claude_inside_the_repo_is_checked_at_its_target(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "r\n", "config/claude/settings.json": "{}\n"},
                 ignore_worktrees=False)
    (main / ".claude").symlink_to("config/claude")
    _git("add", ".claude", cwd=main)
    _git("commit", "-q", "-m", "claude config lives in config/", cwd=main)
    assert _plain_check_ignore(main) == 128

    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["worktrees_ignore"] == {
        "path": ".claude/worktrees/",
        "git_path": "config/claude/worktrees/__harness_probe__",
        "status": "not-ignored",
    }
    assert report["verdict"] == "refuse"
    [refusal] = report["refusals"]
    assert "(git sees config/claude/worktrees/)" in refusal
    assert "`config/claude/worktrees/` to .gitignore" in refusal

    # The literal `.claude/worktrees/` entry does not cover what git sees.
    (main / ".gitignore").write_text(".claude/worktrees/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-q", "-m", "literal ignore", cwd=main)
    assert mod.preflight(str(main), {})["worktrees_ignore"]["status"] == "not-ignored"

    (main / ".gitignore").write_text("config/claude/worktrees/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-q", "-m", "ignore at the target", cwd=main)
    fixed = mod.preflight(str(main), {"a": ["README.md"]})
    assert fixed["worktrees_ignore"]["status"] == "ignored"
    assert fixed["verdict"] == "ok", fixed


def test_check_ignore_failure_refuses(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    real_run = mod._run_git

    def broken_check_ignore(cwd, *args):
        result = real_run(cwd, *args)
        if args[:1] == ("check-ignore",):
            result.returncode, result.stderr = 128, b"fatal: simulated\n"
        return result

    monkeypatch.setattr(mod, "_run_git", broken_check_ignore)
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["verdict"] == "refuse"
    assert report["worktrees_ignore"]["status"] == "unchecked"
    assert [r for r in report["refusals"] if "check-ignore" in r], report["refusals"]


def test_an_unloadable_setup_finalize_refuses_with_a_report(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    monkeypatch.setitem(sys.modules, "setup_finalize", None)  # import raises ImportError
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["verdict"] == "refuse"
    assert [r for r in report["refusals"] if "setup_finalize" in r], report["refusals"]


def test_worktrees_ignore_is_unchecked_when_the_control_root_is_not_ok(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    report = mod.preflight(str(tmp_path / "missing"), {})
    assert report["worktrees_ignore"]["status"] == "unchecked"


# ── Scopes ───────────────────────────────────────────────────────────────


def test_overlapping_scopes_between_requests_adjust(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"plugin/x.py": "x\n", "docs/y.md": "y\n"})
    report = mod.preflight(
        str(main),
        {"a": ["plugin"], "b": ["plugin/x.py", "docs/y.md"], "c": ["docs/other.md"]},
    )
    assert report["overlaps"] == [{"requests": ["a", "b"], "paths": ["plugin", "plugin/x.py"]}]
    assert report["verdict"] == "adjust"
    whole = mod.preflight(str(main), {"a": ["."], "c": ["docs/other.md"]})
    assert whole["overlaps"] and whole["verdict"] == "adjust"
    sibling = mod.preflight(str(main), {"a": ["docs/b"], "c": ["docs/bc"]})
    assert sibling["overlaps"] == [] and sibling["verdict"] == "ok"


def test_every_exclusion_reason_of_a_request_is_kept(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    for order in (["libs/sub/s.txt", "../elsewhere"], ["../elsewhere", "libs/sub/s.txt"]):
        report = mod.preflight(str(main), {"mixed": order})
        reasons = report["excluded_requests"]["mixed"]
        assert len(reasons) == 2, reasons
        assert any("inside-submodule" in r for r in reasons)
        assert any("outside-root" in r for r in reasons)


def test_refuse_takes_precedence_over_adjust(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"plugin/x.py": "x\n"})
    (main / "untracked.txt").write_text("x\n", encoding="utf-8")
    report = mod.preflight(str(main), {"a": ["plugin"], "b": ["plugin/x.py"]})
    assert report["overlaps"] and report["dirty"]
    assert report["verdict"] == "refuse"


def test_scopes_outside_the_work_tree(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (main / "escape").symlink_to(outside)
    report = mod.preflight(
        str(main),
        {"a": ["../elsewhere/x"], "b": [str(outside)], "c": ["escape/x"], "d": [".git/hooks"]},
    )
    assert {s["class"] for s in report["scopes"]} == {"outside-root"}
    assert set(report["excluded_requests"]) == {"a", "b", "c", "d"}
    [reason] = report["excluded_requests"]["a"]
    assert "cannot run in harness:batch" in reason
    # A symlink that stays inside the work tree classifies by its target.
    (main / "inside").symlink_to(main / "README.md")
    root = os.path.realpath(main)
    assert mod.classify_scope(root, "inside", set())["class"] == "tracked-area"


# ── Read-only, trusted environment, CLI ──────────────────────────────────


def test_preflight_writes_nothing(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    (main / ".gitignore").write_text("repos/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-q", "-m", "ignore repos", cwd=main)
    nested = _repo(main / "repos/svc", {"svc.txt": "svc\n"})
    (main / "libs/sub/s.txt").write_text("dirty\n", encoding="utf-8")
    # Stat-dirty but content-clean tracked files: a status that may take the
    # optional index lock would rewrite each index to refresh these entries.
    for tracked in (main / "README.md", nested / "svc.txt"):
        os.utime(tracked, (1_000_000_000, 1_000_000_000))
    before = _tree_digest(tmp_path)
    report = mod.preflight(str(main), {"a": ["libs/sub/s.txt"], "b": ["repos/svc"]})
    assert report["verdict"] == "refuse"  # the dirty submodule
    assert _tree_digest(tmp_path) == before


def test_no_configured_fsmonitor_is_launched(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)  # gitlink enumeration runs on the root
    (main / ".gitignore").write_text("repos/\n", encoding="utf-8")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-q", "-m", "ignore repos", cwd=main)
    _repo(main / "repos/svc")
    for repo in (main, main / "libs/sub", main / "repos/svc"):
        marker = tmp_path / f"fsmonitor-ran-{repo.name}"
        monitor = tmp_path / f"monitor-{repo.name}.sh"
        monitor.write_text(f"#!/bin/sh\ntouch '{marker}'\nexit 1\n", encoding="utf-8")
        monitor.chmod(0o755)
        _git("config", "core.fsmonitor", str(monitor), cwd=repo)
    mod.preflight(str(main), {"a": ["README.md"]})
    assert sorted(p.name for p in tmp_path.glob("fsmonitor-ran-*")) == []


def test_unreadable_directory_fails_closed(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "r\n", ".gitignore": "secret/\n"})
    _repo(main / "secret/hidden", {"important.txt": "x\n"})
    (main / "secret/hidden/uncommitted.txt").write_text("work\n", encoding="utf-8")
    (main / "secret").chmod(0)
    try:
        scan = mod.preflight(str(main), {})
        scoped = mod.preflight(str(main), {"a": ["secret/hidden/important.txt"]})
    finally:
        (main / "secret").chmod(0o755)
    assert scan["verdict"] == "refuse"
    assert [r for r in scan["refusals"] if "cannot scan" in r], scan["refusals"]
    assert scoped["verdict"] == "refuse"
    assert scoped["scopes"] == []  # never guessed tracked-area
    assert [r for r in scoped["refusals"] if "cannot classify scope" in r], scoped["refusals"]


def _skip_as_root() -> None:
    if os.geteuid() == 0:
        import pytest

        pytest.skip("root reads through mode-000 paths")


def test_unreadable_gitmodules_refuses(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    main = _repo(
        tmp_path / "main",
        {"README.md": "r\n", ".gitmodules": '[submodule "d"]\n\tpath = vendor/d\n\turl = ./x\n'},
    )
    (main / ".gitmodules").chmod(0)
    try:
        report = mod.preflight(str(main), {"a": ["vendor/d/x"]})
        # The .gitmodules read itself refuses, independent of the status check
        # (git status also flags the chmod, so the report alone cannot show it).
        import pytest

        with pytest.raises(mod.PreflightError, match=r"\.gitmodules"):
            mod._gitmodules_paths(os.path.realpath(main))
    finally:
        (main / ".gitmodules").chmod(0o644)
    assert report["verdict"] == "refuse"
    assert [r for r in report["refusals"] if ".gitmodules" in r], report["refusals"]


def test_non_regular_gitmodules_refuses_without_blocking(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    os.mkfifo(main / ".gitmodules")  # opening a FIFO would block forever
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(main)], capture_output=True, text=True,
        check=False, timeout=60, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert result.returncode == 1, result.stderr
    body = json.loads(result.stdout)
    assert [r for r in body["refusals"] if "not a regular file" in r], body["refusals"]


def test_untraversable_populated_submodule_refuses(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    # git's own root status is silent here (exit 0, no warning), so a
    # submodule read as "unpopulated" would hide its uncommitted work.
    main = _with_submodule(tmp_path)
    (main / "libs/sub/s.txt").write_text("changed\n", encoding="utf-8")
    (main / "libs/sub/wip.txt").write_text("uncommitted\n", encoding="utf-8")
    (main / "libs/sub").chmod(0)
    try:
        report = mod.preflight(str(main), {})
    finally:
        (main / "libs/sub").chmod(0o755)
    assert report["verdict"] == "refuse", report
    assert [r for r in report["refusals"] if "libs/sub" in r], report["refusals"]


def test_unreadable_directory_inside_a_nested_repo_refuses(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    # The root's listing and the scan both stop at the nested repo's `.git`,
    # so only that repo's own status sees the unreadable directory, and git
    # reports it with a warning and exit 0.
    main = _with_nested(tmp_path)
    svc = main / "repos/svc"
    (svc / "private").mkdir()
    (svc / "private/keep.txt").write_text("k\n", encoding="utf-8")
    _git("add", "private/keep.txt", cwd=svc)
    _git("commit", "-q", "-m", "private", cwd=svc)
    (svc / "private/new.txt").write_text("uncommitted\n", encoding="utf-8")
    (svc / "private").chmod(0)
    try:
        report = mod.preflight(str(main), {})
    finally:
        (svc / "private").chmod(0o755)
    assert report["verdict"] == "refuse", report
    [refusal] = [
        r for r in report["refusals"]
        if "nested-repo repos/svc" in r and "could not read part of" in r
    ]
    # The repo holds uncommitted work: never advise deleting it.
    assert "remove the directory" not in refusal


def test_unreadable_path_warnings_from_the_untracked_listing_refuse(monkeypatch, tmp_path):
    # git reports a directory it cannot open with a warning and exit 0; the
    # nested-repo listing must fail on it by itself, not only via status.
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    real_run = mod._run_git

    def warning_ls_files(cwd, *args):
        result = real_run(cwd, *args)
        if args[:1] == ("ls-files",) and "--others" in args:
            result.stderr = b"warning: could not open directory 'x/': Permission denied\n"
        return result

    monkeypatch.setattr(mod, "_run_git", warning_ls_files)
    import pytest

    with pytest.raises(mod.PreflightError, match="could not read part of"):
        mod.list_nested_repos(os.path.realpath(main), set(), set())


def test_unreadable_tracked_directory_refuses(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    # git skips a tracked directory it cannot open with only a warning and
    # exit 0; a dirty nested repo inside would otherwise go unseen.
    main = _repo(tmp_path / "main", {"src/a.py": "a\n", ".gitignore": "src/vendor/\n"})
    _repo(main / "src/vendor/lib")
    (main / "src/vendor/lib/dirty.txt").write_text("uncommitted\n", encoding="utf-8")
    (main / "src").chmod(0)
    try:
        report = mod.preflight(str(main), {})
    finally:
        (main / "src").chmod(0o755)
    assert report["verdict"] == "refuse", report
    assert [r for r in report["refusals"] if "could not read part of" in r], report["refusals"]


def test_ambient_git_environment_cannot_redirect_the_report(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")
    decoy = _repo(tmp_path / "decoy")
    (decoy / "dirty.txt").write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(decoy))
    report = mod.preflight(str(main), {"a": ["README.md"]})
    assert report["verdict"] == "ok", report


def _cli(*args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=cwd, capture_output=True, text=True,
        check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def test_cli_prints_json_and_exit_status_follows_the_verdict(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"src/a.py": "a\n", "docs/b.md": "b\n"})

    ok = _cli("--request", "a=src/a.py", "--request", "b=docs", cwd=main)
    assert ok.returncode == 0, ok.stderr
    assert json.loads(ok.stdout)["verdict"] == "ok"

    # A repeated slug adds a path; here it overlaps the other request.
    adjust = _cli(
        "--repo", str(main), "--request", "a=src/a.py", "--request", "a=docs/b.md",
        "--request", "b=docs", cwd=tmp_path,
    )
    assert adjust.returncode == 1
    body = json.loads(adjust.stdout)
    assert body["verdict"] == "adjust"
    assert [s["path"] for s in body["scopes"] if s["request"] == "a"] == ["src/a.py", "docs/b.md"]

    refuse = _cli("--repo", str(tmp_path), cwd=tmp_path)
    assert refuse.returncode == 1
    assert json.loads(refuse.stdout)["verdict"] == "refuse"

    for bad in ("no-equals-sign", "a=", "bad slug=x", "a=src/*.py", "a=src/a.py,docs"):
        usage = _cli("--repo", str(main), "--request", bad, cwd=main)
        assert usage.returncode == 2, bad
        assert usage.stdout == ""
    assert "repeat --request SLUG=PATH" in usage.stderr

    # An existing path with a comma or glob characters (a dynamic-route
    # directory) is a path, not a list or a glob.
    (main / "docs/notes,v").write_text("n\n", encoding="utf-8")
    (main / "app/[locale]").mkdir(parents=True)
    (main / "app/[locale]/page.tsx").write_text("p\n", encoding="utf-8")
    real_paths = _cli(
        "--repo", str(main), "--request", "a=docs/notes,v", "--request", "b=app/[locale]",
        cwd=main,
    )
    assert real_paths.returncode == 1  # the new untracked files make the root dirty
    body = json.loads(real_paths.stdout)
    assert [s["class"] for s in body["scopes"]] == ["tracked-area", "tracked-area"]


def test_unexpected_error_still_prints_a_refuse_report(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main")

    def boom(*_args, **_kwargs):
        raise ValueError("unexpected")

    monkeypatch.setattr(mod, "preflight", boom)
    assert mod.main(["--repo", str(main)]) == 1
    body = json.loads(capsys.readouterr().out)
    assert body["verdict"] == "refuse"
    assert any("ValueError: unexpected" in reason for reason in body["refusals"])


# Selected direct-module requests retain literal scopes but own whole gitlinks.
def _s1_two_modules(tmp_path):
    main = _with_submodule(tmp_path)
    other = _repo(tmp_path / "other-source", {"other.txt": "other\n"})
    _git("submodule", "add", "-q", str(other), "libs/other", cwd=main)
    _git("commit", "-qm", "second module", cwd=main)
    return main


def test_s1_empty_selection_preserves_exact_default_report(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    requests = {"a": ["libs/sub/s.txt"], "b": ["README.md"]}
    baseline = mod.preflight(str(main), requests)
    for selected in (None, {}, {"a": []}):
        assert mod.preflight(str(main), requests, submodules=selected) == baseline
    assert "ownership_scopes" not in baseline
    assert baseline["verdict"] == "adjust"


def test_s1_selected_descendant_retains_literal_scope_and_whole_ownership(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    report = mod.preflight(str(main), {"a": ["libs/sub/s.txt"]}, {"a": ["libs/sub"]})
    assert report["verdict"] == "ok", report
    assert report["excluded_requests"] == {}
    assert report["scopes"] == [{"path": "libs/sub/s.txt", "resolved": str(main / "libs/sub/s.txt"),
                                 "class": "selected-submodule", "owner": "libs/sub", "request": "a"}]
    assert report["ownership_scopes"] == {"a": [str(main / "libs/sub")]}
    assert [m["path"] for m in report["selected_modules"]["a"]] == ["libs/sub"]
    assert report["off_limits"] == ["libs/sub"]
    assert report["off_limits_by_request"] == {"a": []}


def test_s1_sibling_descendants_serialize_whole_module(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    requests = {"a": ["libs/sub/first.py"], "b": ["libs/sub/second.py"]}
    report = mod.preflight(str(main), requests, {slug: ["libs/sub"] for slug in requests})
    assert report["verdict"] == "adjust", report
    assert report["excluded_requests"] == {}
    assert report["overlaps"]
    assert {tuple(x["requests"]) for x in report["overlaps"]} == {("a", "b")}
    assert report["ownership_scopes"] == {slug: [str(main / "libs/sub")] for slug in requests}
    assert [s["resolved"] for s in report["scopes"]] == [str(main / paths[0]) for paths in requests.values()]


def test_s1_distinct_modules_parallel_and_other_modules_stay_offlimits(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _s1_two_modules(tmp_path)
    report = mod.preflight(str(main), {"a": ["libs/sub/s.txt"], "b": ["libs/other/other.txt"]},
                           {"a": ["libs/sub"], "b": ["libs/other"]})
    assert report["verdict"] == "ok", report
    assert report["overlaps"] == []
    assert report["off_limits_by_request"] == {"a": ["libs/other"], "b": ["libs/sub"]}
    assert report["ownership_scopes"] == {"a": [str(main / "libs/sub")], "b": [str(main / "libs/other")]}


def test_s1_parent_scope_remains_broad_and_conflicts_with_module(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    report = mod.preflight(str(main), {"a": ["libs"], "b": ["libs/sub/s.txt"]},
                           {"a": ["libs/sub"], "b": ["libs/sub"]})
    assert report["verdict"] == "adjust", report
    assert report["overlaps"]
    assert _scope(report, "libs")["class"] == "tracked-area"
    assert report["ownership_scopes"]["a"] == [str(main / "libs")]
    assert report["ownership_scopes"]["b"] == [str(main / "libs/sub")]


def test_s1_unselected_request_remains_excluded(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    report = mod.preflight(str(main), {"allowed": ["libs/sub/a"], "ordinary": ["libs/sub/b"]},
                           {"allowed": ["libs/sub"]})
    assert report["verdict"] == "adjust"
    assert set(report["excluded_requests"]) == {"ordinary"}
    assert report["off_limits_by_request"]["ordinary"] == ["libs/sub"]
    assert report["overlaps"] == []


def test_s1_nested_repository_stays_offlimits_for_every_request(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    with (main / ".gitignore").open("a") as handle:
        handle.write("repos/\n")
    _git("add", ".gitignore", cwd=main)
    _git("commit", "-qm", "ignore nested", cwd=main)
    _repo(main / "repos/svc")
    report = mod.preflight(str(main), {"a": ["libs/sub"], "b": ["README.md"]}, {"a": ["libs/sub"]})
    assert report["verdict"] == "ok", report
    assert report["off_limits_by_request"] == {"a": ["repos/svc"], "b": ["libs/sub", "repos/svc"]}


def test_s1_uncovered_and_unknown_request_selection_refuse(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    for selected in ({"a": ["libs/sub"]}, {"unknown": ["libs/sub"]}, {"unknown": []}):
        report = mod.preflight(str(main), {"a": ["README.md"]}, selected)
        assert report["verdict"] == "refuse", report
        assert report["refusals"]


def test_s1_malformed_selection_refuses_with_report(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    for selected in ({"a": ["libs/sub", "libs/sub"]}, {"a": ["../subsrc"]},
                     {"a": ["libs/./sub"]}, {"a": ["missing"]}, {"a": "libs/sub"}):
        report = mod.preflight(str(main), {"a": ["."]}, selected)
        assert report["verdict"] == "refuse", report
        assert report["refusals"]


def test_s1_dirty_and_unpopulated_modules_cannot_be_selected(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    module = main / "libs/sub"
    (module / "s.txt").write_text("pending\n")
    dirty = mod.preflight(str(main), {"a": ["libs/sub"]}, {"a": ["libs/sub"]})
    assert dirty["verdict"] == "refuse", dirty
    _git("checkout", "--", "s.txt", cwd=module)
    _git("submodule", "deinit", "-f", "--", "libs/sub", cwd=main)
    empty = mod.preflight(str(main), {"a": ["libs/sub"]}, {"a": ["libs/sub"]})
    assert empty["verdict"] == "refuse", empty


def test_s1_selected_preflight_is_readonly(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_submodule(tmp_path)
    before = _tree_digest(tmp_path)
    report = mod.preflight(str(main), {"a": ["libs/sub/s.txt"]}, {"a": ["libs/sub"]})
    assert report["verdict"] == "ok", report
    assert _tree_digest(tmp_path) == before


def test_s1_cli_repeat_submodule_selection_and_invalid_syntax(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _s1_two_modules(tmp_path)
    result = _cli("--repo", str(main), "--request", "a=libs",
                  "--submodule", "a=libs/sub", "--submodule", "a=libs/other", cwd=tmp_path)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["verdict"] == "ok", report
    assert {m["path"] for m in report["selected_modules"]["a"]} == {"libs/sub", "libs/other"}
    for value in ("no-equals", "a=", "bad slug=libs/sub"):
        bad = _cli("--repo", str(main), "--request", "a=libs", "--submodule", value, cwd=tmp_path)
        assert bad.returncode == 2
        assert bad.stdout == ""
    unknown = _cli("--repo", str(main), "--request", "a=libs", "--submodule", "unknown=libs/sub", cwd=tmp_path)
    assert unknown.returncode == 1
    assert json.loads(unknown.stdout)["verdict"] == "refuse"


def test_managed_discovery_never_walks_unrelated_ignored_history(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"owned/code.py": "pass\n", ".gitignore": "history/\n.claude/worktrees/\n"})
    history = main / "history"
    history.mkdir()
    real_walk = mod.os.walk

    def owned_walk(path, *args, **kwargs):
        assert not Path(path).is_relative_to(history), "unrelated history was inspected"
        return real_walk(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "walk", owned_walk)
    report = mod.preflight(str(main), {"a": ["owned"]}, dirty_scopes=[str(main / "owned")])
    assert report["verdict"] == "ok", report
    assert report["nested_repos"] == []


def test_managed_discovery_keeps_owned_hidden_git_dirs_and_gitfiles(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"owned/code.py": "pass\n", ".gitignore": "owned/.hidden/\n.claude/worktrees/\n"})
    _repo(main / "owned/.hidden/nested", {"code.py": "pass\n"})
    external = _repo(tmp_path / "external")
    linked = main / "owned/.hidden/linked"
    _git("worktree", "add", "-q", str(linked), cwd=external)
    found = mod.list_nested_repos(str(main), set(), set(), inspection_scopes=[str(main / "owned")])
    assert found == ["owned/.hidden/linked", "owned/.hidden/nested"]
    report = mod.preflight(str(main), {"a": ["owned/.hidden/nested/code.py"]},
                           dirty_scopes=[str(main / "owned/.hidden/nested/code.py")])
    assert report["verdict"] == "adjust", report
    assert report["scopes"][0]["class"] == "inside-ignored-nested-repo"


def test_managed_discovery_refuses_unreadable_owned_history(monkeypatch, tmp_path):
    _skip_as_root()
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"owned/code.py": "pass\n", ".gitignore": "owned/.hidden/\n"})
    hidden = main / "owned/.hidden"
    hidden.mkdir()
    hidden.chmod(0)
    try:
        report = mod.preflight(str(main), {"a": ["owned"]}, dirty_scopes=[str(main / "owned")])
    finally:
        hidden.chmod(0o755)
    assert report["verdict"] == "refuse", report
    assert any("could not read part of" in item or "cannot scan" in item for item in report["refusals"])


def test_managed_whole_checkout_ownership_keeps_global_discovery(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _with_nested(tmp_path)
    found = mod.list_nested_repos(str(main), set(), set(), inspection_scopes=[str(main)])
    assert found == ["repos/svc"]


def test_managed_discovery_does_not_expand_an_ignored_ancestor(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "root\n", ".gitignore": "ignored/\n.claude/worktrees/\n"})
    owned = main / "ignored/owned"
    owned.mkdir(parents=True)
    (owned / "code.py").write_text("pass\n", encoding="utf-8")
    unrelated = _repo(main / "ignored/unrelated/nested")
    (unrelated / "README.md").write_text("user changes\n", encoding="utf-8")
    real_walk = mod.os.walk

    def bounded_walk(path, *args, **kwargs):
        assert Path(path).is_relative_to(owned), "ignored ancestor escaped ownership"
        return real_walk(path, *args, **kwargs)

    monkeypatch.setattr(mod.os, "walk", bounded_walk)
    report = mod.preflight(str(main), {"a": ["ignored/owned"]}, dirty_scopes=[str(owned)])
    assert report["verdict"] == "ok", report
    assert report["nested_repos"] == []
    assert (unrelated / "README.md").read_text() == "user changes\n"


def test_managed_ignored_file_scope_requires_no_git_directory_discovery(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    main = _repo(tmp_path / "main", {"README.md": "root\n", ".gitignore": "ignored/\n"})
    owned = main / "ignored/owned/code.py"
    owned.parent.mkdir(parents=True)
    owned.write_text("pass\n", encoding="utf-8")

    def no_global_listing(*args, **kwargs):
        raise AssertionError("explicit ownership does not require global Git discovery")

    monkeypatch.setattr(mod, "_git_text", no_global_listing)
    assert mod.list_nested_repos(str(main), set(), set(), inspection_scopes=[str(owned)]) == []
