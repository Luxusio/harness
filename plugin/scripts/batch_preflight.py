#!/usr/bin/env python3
r"""Read-only repo-shape preflight for one `harness:batch` wave.

A batch lead works in a linked worktree of the main checkout. That is safe
only when the main checkout is a plain git work tree, is clean, and each
request's scope lies in the part of the tree a lead worktree carries.
Unselected submodules and nested repositories break those assumptions (see
`doc/harness/REQ__parallel-tasks-via-worktree-leads.md`, "Multi-repo and
submodules"), so the coordinator runs this report before spawning a wave.
Explicit --submodule SLUG=PATH selections admit supported direct populated
modules and reserve each selected module as a whole ownership boundary.

Usage:
  python3 plugin/scripts/batch_preflight.py [--repo <main checkout>] \
      [--request SLUG=PATH]...

One `--request` per declared scope path; repeating a slug adds another path to
that request. PATH is a single plain file or directory path, repo-relative or
absolute. A value with glob characters (`*?[`) or a comma that names no
existing path is refused as a glob or a comma-joined list; an existing path
with those characters (`app/[locale]`) is fine.

Prints one JSON report (usage errors exit 2 without one). Verdict precedence
is refuse > adjust > ok:
  ok      exit 0  the repo shape, cleanliness, and scopes allow this wave;
                  the coordinator's other preflight steps still apply.
  adjust  exit 1  drop every `excluded_requests` entry (run it outside the
                  wave), move one request of each `overlaps` pair to a later
                  wave, then rerun.
  refuse  exit 1  batch must not start: the control root is not a plain git
                  checkout; the main checkout, a populated submodule, or a
                  nested repository is dirty; a post-checkout hook would
                  initialize submodules in every lead worktree;
                  `.claude/worktrees/` is not gitignored at the path git sees
                  (a symlinked `.claude` leading outside the repository
                  passes); or something the report depends on could not be
                  read.

Read-only: it writes no file. Every git call runs with the trusted environment
(no ambient `GIT_*`), `--no-optional-locks` (no opportunistic index refresh),
and `core.fsmonitor=false` (no repository's configured monitor is launched,
the root's included); nothing touches the network. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _lib import _trusted_git_env, find_repo_root  # type: ignore  # noqa: E402

SLUG_RE = re.compile(r"^[A-Za-z0-9._-]+$")
# Where Claude Code `isolation: worktree` creates every lead worktree.
WORKTREES_DIR = ".claude/worktrees/"
GLOB_CHARS = frozenset("*?[")
MAX_LISTED_ENTRIES = 20
HOOK_READ_LIMIT = 256 * 1024
GIT_TIMEOUT_SECONDS = 120


# Git skips a directory or file it cannot open with only a warning and exit 0;
# the untracked listing and `status` would then silently miss what is inside.
UNREADABLE_WARNINGS = ("could not open directory", "Permission denied")


class PreflightError(RuntimeError):
    """Something the report depends on could not be read."""


def _run_git(cwd: str, *args: str) -> subprocess.CompletedProcess:
    command = ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", *args]
    env = _trusted_git_env()
    env["LC_ALL"] = "C"  # untranslated warnings, so UNREADABLE_WARNINGS match
    try:
        return subprocess.run(
            command, cwd=cwd, capture_output=True, check=False,
            env=env, timeout=GIT_TIMEOUT_SECONDS,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PreflightError(f"git {' '.join(args)} could not run in {cwd}: {exc}") from exc


def _stderr(result: subprocess.CompletedProcess) -> str:
    return result.stderr.decode("utf-8", "replace").strip()


def _git_text(cwd: str, *args: str, must_read_everything: bool = False) -> str:
    """Stdout of a successful git call.

    With ``must_read_everything``, an unreadable-path warning also fails: the
    call enumerates the work tree, and a skipped directory could hide a
    nested repository or uncommitted work.
    """
    result = _run_git(cwd, *args)
    if result.returncode != 0:
        raise PreflightError(f"git {' '.join(args)} failed in {cwd}: {_stderr(result)}")
    if must_read_everything:
        stderr = _stderr(result)
        if any(marker in stderr for marker in UNREADABLE_WARNINGS):
            raise PreflightError(
                f"git {' '.join(args)} could not read part of {cwd}: {stderr}; "
                "make it readable or move it out of the checkout"
            )
    return os.fsdecode(result.stdout)


def _is_within(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip(os.sep) + os.sep)


def _safe_relpath(value: str) -> str:
    """Normalize a repo-relative POSIX path; "" when empty, absolute, or escaping."""
    rel = posixpath.normpath(value) if value else ""
    if rel in ("", ".", "..") or rel.startswith(("/", "../")):
        return ""
    return rel


# ── Control root ─────────────────────────────────────────────────────────


def control_root_shape(root: str) -> tuple:
    """Return ``(shape, reason, common_dir)`` for the main checkout ``root``.

    The gitfile test comes last: a linked worktree and a submodule checkout
    also carry a `.git` file, and each has its own, more useful label.
    """
    if not os.path.isdir(root):
        return "non-git", "not a directory", ""
    top = _run_git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        return "non-git", _stderr(top), ""
    toplevel = os.path.realpath(os.fsdecode(top.stdout).strip())
    if toplevel != root:
        return "non-git", f"not the top of a git work tree (the top is {toplevel})", ""
    lines = _git_text(root, "rev-parse", "--git-dir", "--git-common-dir").splitlines()
    git_dir, common_dir = (os.path.realpath(os.path.join(root, line)) for line in lines[:2])
    if git_dir != common_dir:
        main_checkout = "that repository's main checkout"
        if os.path.basename(common_dir) == ".git":  # else a separate git dir: unknown
            main_checkout += f" ({os.path.dirname(common_dir)})"
        return (
            "linked-worktree",
            f"its git dir {git_dir} belongs to {common_dir}; run the batch from "
            f"{main_checkout}",
            common_dir,
        )
    superproject = _git_text(root, "rev-parse", "--show-superproject-working-tree").strip()
    if superproject:
        return "submodule-checkout", f"it is a submodule of {superproject}", common_dir
    if common_dir != os.path.realpath(os.path.join(root, ".git")):
        return (
            "separate-git-dir",
            f"its git dir {common_dir} is not <root>/.git, and lead workspaces "
            "must register under <root>/.git/worktrees",
            common_dir,
        )
    return "ok", "", common_dir


# ── Submodules, nested repositories, hooks ───────────────────────────────


def _gitmodules_paths(root: str) -> set:
    path = os.path.join(root, ".gitmodules")
    if not os.path.lexists(path):
        return set()
    # `git config -f` exits 1 ("no match") on an unreadable file, which would
    # read as "declares nothing"; prove it is a readable regular file first
    # (opening a FIFO would block forever).
    try:
        if not stat.S_ISREG(os.stat(path).st_mode):
            raise PreflightError(f"{path} is not a regular file")
        with open(path, "rb"):
            pass
    except OSError as exc:
        raise PreflightError(f"cannot read {path}: {exc.strerror}") from exc
    result = _run_git(
        root, "config", "-z", "-f", ".gitmodules", "--get-regexp", r"^submodule\..*\.path$",
    )
    if result.returncode == 1:  # no submodule.<name>.path keys
        return set()
    if result.returncode != 0:
        raise PreflightError(f"cannot read .gitmodules in {root}: {_stderr(result)}")
    paths = set()
    for record in os.fsdecode(result.stdout).split("\0"):
        _key, _sep, value = record.partition("\n")
        rel = _safe_relpath(value.strip())
        if rel:
            paths.add(rel)
    return paths


def _gitlink_paths(root: str) -> set:
    """Index paths with mode 160000, at any stage (a conflicted one is still a gitlink)."""
    paths = set()
    for record in _git_text(root, "ls-files", "--stage", "-z").split("\0"):
        metadata, sep, path = record.partition("\t")
        if sep and metadata.split(" ", 1)[0] == "160000":
            rel = _safe_relpath(path)
            if rel:
                paths.add(rel)
    return paths


def list_submodules(root: str) -> list:
    """Submodules from mode-160000 gitlinks and `.gitmodules`, with populated state."""
    gitlinks = _gitlink_paths(root)
    declared = _gitmodules_paths(root)
    return [
        {
            "path": rel,
            "gitlink": rel in gitlinks,
            "in_gitmodules": rel in declared,
            # Not lexists: it reads EACCES as "absent", and an untraversable
            # populated submodule would then skip its status check.
            "populated": _lstat_or_none(os.path.join(root, rel, ".git")) is not None,
        }
        for rel in sorted(gitlinks | declared)
    ]


def registered_worktrees(root: str) -> set:
    """Realpaths of the linked worktrees registered to ``root``'s repository."""
    paths = set()
    for line in _git_text(root, "worktree", "list", "--porcelain").splitlines():
        if line.startswith("worktree "):
            paths.add(os.path.realpath(line[len("worktree "):]))
    paths.discard(root)
    return paths


def list_nested_repos(root: str, submodule_paths: set, worktrees: set, *, inspection_scopes=None) -> list:
    """Directories under ``root`` that hold their own `.git` and git does not track.

    Candidates are the untracked and ignored directories git reports. Each is
    walked without following links, stopping at the first `.git` holder. A
    registered linked worktree of this repository is not a nested repository.
    A directory the walk cannot read could hide one, so it fails the scan.
    Ordinary managed admission walks its literal ownership roots directly;
    standalone and selected-module admission retain global Git discovery.
    """
    candidates = set()
    if inspection_scopes is not None:
        if not inspection_scopes:
            raise PreflightError("managed discovery requires nonempty ownership scopes")
        for scope in inspection_scopes:
            absolute = os.path.realpath(os.path.join(root, scope))
            if not _is_within(absolute, root):
                raise PreflightError("managed discovery scope is outside the control root")
            candidates.add(os.path.relpath(absolute, root).replace(os.sep, "/"))
    else:
        for extra in ((), ("--ignored",)):
            listing = _git_text(
                root, "ls-files", "-z", "--others", *extra, "--exclude-standard", "--directory",
                must_read_everything=True,
            )
            for entry in listing.split("\0"):
                if entry.endswith("/"):
                    rel = _safe_relpath(entry.rstrip("/"))
                    if rel:
                        candidates.add(rel)

    def unreadable(exc: OSError) -> None:
        raise PreflightError(
            f"cannot scan {exc.filename} for nested repositories ({exc.strerror}); "
            "make it readable or move it out of the checkout"
        )

    found = set()
    for rel in sorted(candidates):
        start = os.path.join(root, rel)
        info = _lstat_or_none(start)
        if info is None or not stat.S_ISDIR(info.st_mode):
            continue
        for dirpath, dirnames, filenames in os.walk(start, onerror=unreadable):
            if os.path.realpath(dirpath) == root:
                dirnames[:] = [name for name in dirnames if name != ".git"]
                continue
            if ".git" not in dirnames and ".git" not in filenames:
                continue
            dirnames[:] = []
            holder = os.path.relpath(dirpath, root).replace(os.sep, "/")
            if os.path.realpath(dirpath) not in worktrees and holder not in submodule_paths:
                found.add(holder)
    return sorted(found)


def post_checkout_hooks(root: str, common_dir: str) -> list:
    """The default and the effective (`core.hooksPath`) post-checkout hook."""
    candidates = [os.path.join(common_dir, "hooks", "post-checkout")]
    effective = _git_text(root, "rev-parse", "--git-path", "hooks/post-checkout").strip()
    if effective:
        candidates.append(os.path.join(root, effective))
    hooks, seen = [], set()
    for path in candidates:
        real = os.path.realpath(path)
        if real in seen or not os.path.isfile(path):
            continue
        seen.add(real)
        try:
            with open(path, "rb") as handle:
                text = handle.read(HOOK_READ_LIMIT).decode("utf-8", "replace")
        except OSError as exc:
            raise PreflightError(f"cannot read post-checkout hook {path}: {exc}") from exc
        hooks.append({"path": path, "mentions_submodule": "submodule" in text.lower()})
    return hooks


def worktrees_ignore(root: str) -> dict:
    """Whether git ignores Claude Code's lead worktree directory, where git sees it.

    A symlinked `.claude` (or `.claude/worktrees`) moves that directory: one
    resolving inside the repository is checked at its target, and one leading
    outside it (or looping) is invisible to git, so it needs no ignore. Plain
    `git check-ignore .claude/worktrees/x` exits 128 through either link.
    Where git sees a path is `setup_finalize`'s rule (manifest v7 checks the
    same entry), so it is imported here rather than restated.
    """
    try:
        from setup_finalize import _git_visible_path, representative_path  # type: ignore
    except ImportError as exc:
        raise PreflightError(f"cannot load setup_finalize to place {WORKTREES_DIR}: {exc}") from exc
    probe = representative_path(WORKTREES_DIR)
    seen = _git_visible_path(Path(root), probe)
    info = {"path": WORKTREES_DIR, "git_path": seen, "status": "outside-repo"}
    if seen is None:
        return info
    result = _run_git(root, "check-ignore", "-q", "--no-index", "--", seen)
    if result.returncode not in (0, 1):
        raise PreflightError(f"git check-ignore {seen} failed in {root}: {_stderr(result)}")
    info["status"] = "ignored" if result.returncode == 0 else "not-ignored"
    return info


# ── Cleanliness ──────────────────────────────────────────────────────────


def _pruned_worktree_hint(repo_dir: str) -> str:
    """A fix-it hint only when `.git` is a gitfile naming a missing worktree registration."""
    git_path = os.path.join(repo_dir, ".git")
    try:
        if not stat.S_ISREG(os.stat(git_path).st_mode):  # a directory, FIFO, ...
            return ""
        with open(git_path, "rb") as handle:
            line = handle.read(4096).decode("utf-8", "replace").strip()
    except OSError:  # missing or unreadable: not a stale gitfile we can name
        return ""
    target = line[len("gitdir:"):].strip() if line.startswith("gitdir:") else ""
    if not target:
        return ""
    if not os.path.isabs(target):
        target = os.path.join(repo_dir, target)
    parent = os.path.basename(os.path.dirname(os.path.normpath(target)))
    if parent != "worktrees" or os.path.exists(target):
        return ""
    return (
        f" (its .git names the missing worktree registration {target}: a leftover "
        "worktree whose metadata was pruned; copy out anything still needed, then "
        "remove the directory)"
    )


def status_entries(repo_dir: str) -> list:
    """`git status --porcelain` lines of the repository rooted exactly at ``repo_dir``.

    An empty or invalid `.git`, or a `core.worktree` override, would make git
    report some other tree, so the toplevel must be ``repo_dir`` itself.
    """
    result = _run_git(repo_dir, "rev-parse", "--show-toplevel")
    if result.returncode != 0:
        raise PreflightError(
            f"git cannot open {repo_dir} as a repository: {_stderr(result)}"
            f"{_pruned_worktree_hint(repo_dir)}"
        )
    top = os.path.realpath(os.fsdecode(result.stdout).strip())
    if top != os.path.realpath(repo_dir):
        raise PreflightError(f"git resolves {repo_dir} to another work tree ({top})")
    listing = _git_text(
        repo_dir, "status", "--porcelain", "--ignore-submodules=none",
        "--untracked-files=all",
        must_read_everything=True,
    )
    return [line for line in listing.splitlines() if line]


# ── Scopes ───────────────────────────────────────────────────────────────


def _lstat_or_none(path: str):
    """``os.lstat(path)``, or None when it does not exist; any other error fails closed."""
    try:
        return os.lstat(path)
    except (FileNotFoundError, NotADirectoryError):
        return None
    except OSError as exc:
        raise PreflightError(f"cannot inspect {path}: {exc.strerror}") from exc


def classify_scope(root: str, path: str, submodule_paths: set) -> dict:
    """Place one declared scope path, resolved through symlinks, relative to ``root``.

    Raises PreflightError when an existing ancestor cannot be inspected: it
    could hide a nested repository, so the scope is never guessed tracked.
    """
    absolute = path if os.path.isabs(path) else os.path.join(root, path)
    real = os.path.realpath(absolute)
    info = {"path": path, "resolved": real, "class": "outside-root", "owner": None}
    if not _is_within(real, root):
        return info
    rel = os.path.relpath(real, root).replace(os.sep, "/")
    parts = [] if rel == "." else rel.split("/")
    if parts and parts[0] == ".git":
        return info  # git metadata, not the work tree
    for sub in sorted(submodule_paths):
        if rel == sub or rel.startswith(sub + "/"):
            info.update({"class": "inside-submodule", "owner": sub})
            return info
    current = root
    for index, part in enumerate(parts):
        current = os.path.join(current, part)
        info_stat = _lstat_or_none(current)
        if info_stat is None or not stat.S_ISDIR(info_stat.st_mode):
            break
        if _lstat_or_none(os.path.join(current, ".git")) is not None:
            info.update({
                "class": "inside-ignored-nested-repo",
                "owner": "/".join(parts[: index + 1]),
            })
            return info
    info["class"] = "tracked-area"
    return info


def scope_overlaps(scopes: list) -> list:
    """Pairs of different requests whose resolved scope paths nest or coincide."""
    overlaps = []
    for index, left in enumerate(scopes):
        for right in scopes[index + 1:]:
            if left["request"] == right["request"]:
                continue
            a, b = left["resolved"], right["resolved"]
            if _is_within(a, b) or _is_within(b, a):
                overlaps.append({
                    "requests": [left["request"], right["request"]],
                    "paths": [left["path"], right["path"]],
                })
    return overlaps


# ── Report ───────────────────────────────────────────────────────────────


def _empty_report(root: str) -> dict:
    return {
        "verdict": "refuse",
        "control_root": {"path": root, "shape": "non-git", "reason": ""},
        "submodules": [],
        "nested_repos": [],
        "off_limits": [],
        "post_checkout_hooks": [],
        "worktrees_ignore": {"path": WORKTREES_DIR, "git_path": None, "status": "unchecked"},
        "dirty": [],
        "scopes": [],
        "overlaps": [],
        "excluded_requests": {},
        "refusals": [],
    }


def _exclusion_reason(scope: dict) -> str:
    where = f"{scope['path']} is {scope['class']}"
    if scope["owner"]:
        where += f" ({scope['owner']})"
    if scope["class"] == "outside-root":
        return f"{where}; no batch lead can reach it, so it cannot run in harness:batch"
    return f"{where}; run it as an ordinary task in the main checkout, outside the wave"


def _worktrees_refusal(git_path: str) -> str:
    seen = posixpath.dirname(git_path) + "/"
    if seen == WORKTREES_DIR:
        where = WORKTREES_DIR
        fix = (
            f"add `{seen}` to .gitignore and commit it (setup's "
            "`--migrate-harness-version` to manifest v7 adds it)"
        )
    else:
        where = f"{WORKTREES_DIR} (git sees {seen})"
        fix = f"add `{seen}` to .gitignore and commit it"
    return (
        f"{where} is not gitignored, so every lead worktree would show up in the main "
        f"checkout as untracked; {fix}"
    )


def _inspect(report: dict, root: str, common_dir: str, dirty_scopes=None) -> None:
    submodules = list_submodules(root)
    report["submodules"] = submodules
    sub_paths = {entry["path"] for entry in submodules}
    report["nested_repos"] = list_nested_repos(
        root, sub_paths, registered_worktrees(root), inspection_scopes=dirty_scopes,
    )
    report["off_limits"] = sorted(sub_paths | set(report["nested_repos"]))
    report["post_checkout_hooks"] = post_checkout_hooks(root, common_dir)

    checks = [(".", "control-root")]
    checks += [(entry["path"], "submodule") for entry in submodules if entry["populated"]]
    checks += [(rel, "nested-repo") for rel in report["nested_repos"]]
    for rel, kind in checks:
        try:
            entries = status_entries(root if rel == "." else os.path.join(root, rel))
        except PreflightError as exc:
            raise PreflightError(f"cannot read {kind} {rel}: {exc}") from exc
        if entries:
            report["dirty"].append({
                "repo": rel, "kind": kind, "count": len(entries),
                "entries": entries[:MAX_LISTED_ENTRIES],
            })
            noun = "entry" if len(entries) == 1 else "entries"
            if rel == '.' and dirty_scopes is not None:
                from batch_finish import FinishError, require_disjoint_dirty
                try:
                    require_disjoint_dirty(root, dirty_scopes)
                except FinishError as exc:
                    report['refusals'].append(str(exc))
            else:
                report["refusals"].append(
                    f"{kind} {rel} is not clean ({len(entries)} status {noun}); "
                    "preserve changes and resolve the existing work before retrying"
                )
    if submodules:
        for hook in report["post_checkout_hooks"]:
            if hook["mentions_submodule"]:
                report["refusals"].append(
                    f"post-checkout hook {hook['path']} mentions submodule and the "
                    "repository has submodules: creating a lead worktree would "
                    "initialize them; make the hook skip linked worktrees, or run "
                    "these requests one at a time as ordinary tasks"
                )


def preflight(repo_root: str, requests: dict, submodules=None, *, dirty_scopes=None) -> dict:
    """Inspect literal scopes and optional per-request direct-module selections."""
    root = os.path.realpath(repo_root)
    report = _empty_report(root)
    try:
        shape, reason, common_dir = control_root_shape(root)
    except PreflightError as exc:
        report["refusals"].append(str(exc))
        return report
    report["control_root"].update({"shape": shape, "reason": reason})
    if shape != "ok":
        report["refusals"].append(f"control root is {shape}: {reason}")
        return report

    try:
        _inspect(report, root, common_dir, dirty_scopes if not submodules else None)
    except PreflightError as exc:
        report["refusals"].append(str(exc))
    try:
        report["worktrees_ignore"] = worktrees_ignore(root)
    except PreflightError as exc:
        report["refusals"].append(str(exc))
    if report["worktrees_ignore"]["status"] == "not-ignored":
        report["refusals"].append(_worktrees_refusal(report["worktrees_ignore"]["git_path"]))

    selections = {}
    if submodules is not None:
        if not isinstance(submodules, dict):
            report["refusals"].append("submodules must map request slugs to path lists")
        else:
            from batch_submodules import SubmoduleError, selection
            for slug, paths in submodules.items():
                if slug not in requests:
                    report["refusals"].append(f"unknown submodule request {slug}")
                    continue
                if not isinstance(paths, list):
                    report["refusals"].append(f"submodules of {slug} must be a path list")
                    continue
                if not paths:
                    continue
                try:
                    selections[slug] = selection(root, paths)
                except (SubmoduleError, OSError, ValueError) as exc:
                    report["refusals"].append(f"invalid submodule selection of {slug}: {exc}")
    selected_paths = {slug: {entry["path"] for entry in entries}
                      for slug, entries in selections.items()}
    selected_mode = isinstance(submodules, dict) and any(submodules.values())
    if selected_mode:
        report["selected_modules"] = selections
        report["off_limits_by_request"] = {
            slug: [path for path in report["off_limits"]
                   if path not in selected_paths.get(slug, set())]
            for slug in requests
        }
        report["ownership_scopes"] = {slug: [] for slug in requests}
    sub_paths = {entry["path"] for entry in report["submodules"]}
    ownership = []
    for slug, paths in requests.items():
        for path in paths:
            try:
                scope = classify_scope(root, path, sub_paths)
            except PreflightError as exc:
                report["refusals"].append(f"cannot classify scope {path} of {slug}: {exc}")
                continue
            scope["request"] = slug
            if scope["class"] == "inside-submodule" and scope["owner"] in selected_paths.get(slug, set()):
                scope["class"] = "selected-submodule"
            report["scopes"].append(scope)
            boundary = dict(scope)
            if scope["class"] == "selected-submodule":
                boundary["resolved"] = os.path.join(root, scope["owner"])
            ownership.append(boundary)
            if selected_mode and boundary["resolved"] not in report["ownership_scopes"][slug]:
                report["ownership_scopes"][slug].append(boundary["resolved"])
            if scope["class"] not in ("tracked-area", "selected-submodule"):
                report["excluded_requests"].setdefault(slug, []).append(
                    _exclusion_reason(scope)
                )
    for slug, paths in selected_paths.items():
        scopes = [scope["resolved"] for scope in report["scopes"]
                  if scope["request"] == slug and scope["class"] in ("tracked-area", "selected-submodule")]
        for path in paths:
            selected = os.path.join(root, path)
            if not any(_is_within(selected, scope) or _is_within(scope, selected) for scope in scopes):
                report["refusals"].append(f"selected submodule {path} is not covered by a literal scope of {slug}")
    # An excluded request never runs in any wave, so pairing it would only
    # advise deferring something that cannot be deferred.
    report["overlaps"] = scope_overlaps(
        [s for s in ownership if s["request"] not in report["excluded_requests"]]
    )

    if report["refusals"]:
        report["verdict"] = "refuse"
    elif report["excluded_requests"] or report["overlaps"]:
        report["verdict"] = "adjust"
    else:
        report["verdict"] = "ok"
    return report


def _request_arg(value: str) -> tuple:
    slug, sep, path = value.partition("=")
    if not sep or not SLUG_RE.match(slug) or not path:
        raise argparse.ArgumentTypeError(f"expected SLUG=PATH, got {value!r}")
    return slug, path


def _globs_and_lists(root: str, requests: dict) -> list:
    """Scope values with glob characters or a comma that name no existing path.

    Such a value would read as one nonexistent path and hide the overlaps and
    submodule scopes of what it stands for. An existing path containing those
    characters (`app/[locale]`, `CHANGELOG,v`) is an ordinary path.
    """
    return [
        path
        for paths in requests.values()
        for path in paths
        if (GLOB_CHARS & set(path) or "," in path)
        and not os.path.lexists(path if os.path.isabs(path) else os.path.join(root, path))
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--repo", default=None, help="main checkout root (default: discover from cwd)",
    )
    parser.add_argument(
        "--request", action="append", default=[], type=_request_arg, metavar="SLUG=PATH",
        help="one declared scope path of one request; repeat per path and per request",
    )
    parser.add_argument(
        "--submodule", action="append", default=[], type=_request_arg, metavar="SLUG=PATH",
        help="select a supported direct populated submodule for a request; repeat per module",
    )
    args = parser.parse_args(argv)
    requests: dict = {}
    for slug, path in args.request:
        requests.setdefault(slug, []).append(path)
    selections: dict = {}
    for slug, path in args.submodule:
        selections.setdefault(slug, []).append(path)
    repo = args.repo or find_repo_root()
    unusable = _globs_and_lists(os.path.realpath(repo), requests)
    if unusable:
        parser.error(
            f"scope {unusable[0]!r} names no existing path and looks like a glob or "
            "a comma list; give one plain file or directory path per --request "
            "(repeat --request SLUG=PATH for each path)"
        )
    try:
        report = preflight(repo, requests, selections) if selections else preflight(repo, requests)
    except Exception as exc:  # a crash must never read as a pass
        report = _empty_report(os.path.realpath(repo))
        report["refusals"].append(f"preflight failed: {type(exc).__name__}: {exc}")
    print(json.dumps(report, indent=2))
    return 0 if report["verdict"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
