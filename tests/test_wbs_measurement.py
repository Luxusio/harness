"""Bounded checkout observation and explicit non-attesting assertion results."""
import json
import os
from pathlib import Path
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error

from test_wbs import baseline, check, node, plan, progress, publish, wbs


def git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


def commit(repo):
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture")


def module(repo, name="module"):
    path = repo / name
    path.mkdir()
    git(path, "init", "-q")
    (path / ".gitignore").write_text("build/\n")
    (path / "module.txt").write_text("module source")
    commit(path)
    git(repo, "add", name)
    return path


@pytest.mark.parametrize("limit", ["bytes", "entries", "listing"])
def test_precheck_budget_is_not_run_and_preserves_prior_proofs(tmp_path, monkeypatch, limit):
    repo, td = baseline(tmp_path)
    assert check(repo, td)["passed"]
    before = (td / "WBS.json").read_bytes()
    monkeypatch.setattr(wbs, {"bytes": "MAX_EVIDENCE_BYTES", "entries": "MAX_EVIDENCE_ENTRIES",
                             "listing": "MAX_LISTING_BYTES"}[limit], 1)
    monkeypatch.setattr(wbs, "_execute", forbidden_assertion_runner(wbs._execute))
    observed = progress(repo, td)
    assert observed["status"] == "invalid" and observed["percentage"] is None
    result = check(repo, td)
    assert result["execution"] == "not_run" and result["passed"] is None
    assert result["returncode"] is None and result["measurement_phase"] == "before"
    assert result["measurement_error"]
    assert (td / "WBS.json").read_bytes() == before


def forbidden_assertion_runner(actual):
    def execute(argv, *args, **kwargs):
        assert argv[0] == "git", "assertion executed despite unavailable premeasurement"
        return actual(argv, *args, **kwargs)
    return execute


@pytest.mark.parametrize("code", [0, 7])
def test_postcheck_unavailable_preserves_history_without_resurrecting_pass(tmp_path, monkeypatch, code):
    repo, td = baseline(tmp_path, [node("A", 3), node("B", 7)])
    assert check(repo, td)["passed"]
    before = json.loads((td / "WBS.json").read_text())
    actual = wbs._execute
    def execute(argv, *args, **kwargs):
        if argv[0] == "git":
            return actual(argv, *args, **kwargs)
        monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 1)
        return dict(returncode=code, error=None, output="actual assertion result")
    monkeypatch.setattr(wbs, "_execute", execute)
    result = check(repo, td)
    assert result["execution"] == "completed" and result["returncode"] == code
    assert result["passed"] is None and result["measurement_phase"] == "after"
    assert result["progress"]["percentage"] is None
    after = json.loads((td / "WBS.json").read_text())
    assert {k: v for k, v in after.items() if k != "checks"} == {k: v for k, v in before.items() if k != "checks"}
    recorded = after["checks"]["A"]
    assert {k: v for k, v in recorded.items() if k != "last_attempt"} == before["checks"]["A"]
    assert recorded["last_attempt"]["result"]["returncode"] == code
    assert recorded["last_attempt"]["result"]["output"] == "actual assertion result"
    monkeypatch.undo()
    # A new reader after restart must not revive the historic PASS.
    assert wbs.read_state(str(td))["run_id"] == "run"
    assert progress(repo, td)["percentage"] == 0
    assert check(repo, td)["progress"]["percentage"] == 30
    assert "last_attempt" not in wbs.read_state(str(td))["checks"]["A"]


def test_post_failure_without_previous_proof_and_start_failure_are_distinct(tmp_path):
    repo, td = baseline(tmp_path)
    publish(td, [dict(node(), check=["/harness-missing-assertion"])])
    result = check(repo, td)
    assert result["execution"] == "start_failed" and result["passed"] is False
    assert result["measurement_status"] == "measured" and result["error"]
    publish(td, [dict(node(), check=[sys.executable, "-c", "raise SystemExit(9)"])])
    result = check(repo, td)
    assert result["execution"] == "completed" and result["returncode"] == 9
    assert result["passed"] is False and result["measurement_error"] is None


def test_source_change_is_failed_attestation_not_measurement_unavailable(tmp_path):
    repo, td = baseline(tmp_path)
    publish(td, [dict(node(), check=[sys.executable, "-c", "from pathlib import Path; Path('src.txt').write_text('changed')"])])
    result = check(repo, td)
    assert result["returncode"] == 0 and result["passed"] is False
    assert result["measurement_status"] == "measured"
    assert "source changed" in result["error"]
    assert result["progress"]["percentage"] == 0


def test_gitlink_ignores_build_artifacts_but_measures_dirty_and_new_source(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    mod = module(repo)
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 1024)
    assert check(repo, td)["passed"]
    original = wbs.fingerprint(str(repo))
    (mod / "build").mkdir()
    (mod / "build" / "large.bin").write_bytes(b"x" * 8192)
    (mod / "build" / "link").symlink_to(tmp_path)
    assert wbs.fingerprint(str(repo)) == original
    assert progress(repo, td)["percentage"] == 100
    # Index flags must never hide bytes from measurement.
    git(mod, "update-index", "--assume-unchanged", "module.txt")
    (mod / "module.txt").write_text("changed module source")
    assert progress(repo, td)["percentage"] == 0
    assert check(repo, td)["passed"]
    (mod / "new.txt").write_text("new module source")
    assert progress(repo, td)["percentage"] == 0
    assert check(repo, td)["passed"]
    git(mod, "update-index", "--no-assume-unchanged", "module.txt")
    commit(mod)
    assert progress(repo, td)["percentage"] == 0  # HEAD itself is evidence too.


def test_gitlink_missing_rebound_depth_and_global_budgets(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    mod = module(repo)
    original = wbs.fingerprint(str(repo))
    monkeypatch.setattr(wbs, "MAX_MODULE_DEPTH", 0)
    assert progress(repo, td)["percentage"] is None
    monkeypatch.undo()
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_ENTRIES", 3)
    assert progress(repo, td)["percentage"] is None
    monkeypatch.undo()
    moved = tmp_path / "original-module"
    mod.rename(moved)
    assert wbs.fingerprint(str(repo)) != original  # Explicit unpopulated identity.
    mod.mkdir()
    assert wbs.fingerprint(str(repo)) != original
    (mod / "data.txt").write_text("unregistered payload")
    assert progress(repo, td)["percentage"] is None  # Never adopt parent repository.
    (mod / "data.txt").unlink()
    git(moved, "config", "core.worktree", str(moved))
    (mod / ".git").write_text("gitdir: " + str(moved / ".git") + "\n")
    assert progress(repo, td)["percentage"] is None  # Foreign module root refused.


def test_arbitrary_directory_and_in_root_symlink_ancestor_are_unmeasurable(tmp_path):
    repo, td = baseline(tmp_path)
    git(repo, "add", "src.txt")
    (repo / "src.txt").unlink()
    (repo / "src.txt").mkdir()
    (repo / "src.txt" / "ignored-content").write_text("not a tracked source file")
    assert progress(repo, td)["percentage"] is None


def test_current_nodes_schema_public_context_check_preserves_identity(tmp_path, monkeypatch):
    from test_harness_mcp_server import HarnessMcpServerTests
    server = HarnessMcpServerTests()
    repo, td = baseline(tmp_path)
    (td / "WBS.json").unlink()
    def call(action, **kwargs):
        return server._call_in_repo(str(repo), "wbs", dict(action=action, wbs_id="sample", **kwargs))
    opened = call("open")["structuredContent"]
    assert not call("plan", plan=plan([node("A", 3), node("B", 7)])).get("isError")
    assert call("check", node_id="A")["structuredContent"]["progress"]["percentage"] == 30
    before = (td / "WBS.json").read_bytes()
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 1)
    ctx = call("context")["structuredContent"]
    assert ctx["progress"]["status"] == "unmeasured" and ctx["progress"]["percentage"] is None
    assert ctx["progress"]["historical_percentage"] == 30
    checked = call("check", node_id="A")
    assert not checked.get("isError"), checked
    assert checked["structuredContent"]["execution"] == "not_run"
    assert checked["structuredContent"]["passed"] is None
    assert (td / "WBS.json").read_bytes() == before
    monkeypatch.undo()
    assert call("open")["structuredContent"]["run_id"] == opened["run_id"]
    state = wbs.read_state(str(td))
    assert "nodes" in state and "outcomes" not in state
    assert call("context")["structuredContent"]["progress"]["historical_percentage"] == 30


def test_listing_limit_refuses_truncation_and_time_budget(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    monkeypatch.setattr(wbs, "MAX_LISTING_BYTES", 4)
    result = check(repo, td)
    assert result["execution"] == "not_run" and "output limit" in result["measurement_error"]
    monkeypatch.undo()
    times = iter([0, 31])
    monkeypatch.setattr(wbs.time, "monotonic", lambda: next(times))
    with pytest.raises(ValueError, match="measurement budget.*time"):
        wbs.fingerprint(str(repo))


def test_malformed_inconclusive_marker_is_invalid(tmp_path):
    repo, td = baseline(tmp_path)
    assert check(repo, td)["passed"]
    state = wbs.read_state(str(td))
    state["checks"]["A"]["last_attempt"] = None
    with pytest.raises(ValueError, match="inconclusive"):
        wbs.validate_state(state)


def test_file_growth_during_streaming_is_bounded(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    actual = wbs.hashlib.sha256
    class GrowingHash:
        def __init__(self):
            self.inner = actual()
        def update(self, chunk):
            self.inner.update(chunk)
            if chunk == b"ok":
                with (repo / "src.txt").open("ab") as stream:
                    stream.write(b"x" * 1024)
        def hexdigest(self):
            return self.inner.hexdigest()
    monkeypatch.setattr(wbs.hashlib, "sha256", lambda *a, **k: actual(*a, **k) if a or k else GrowingHash())
    monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 128)
    result = check(repo, td)
    assert result["execution"] == "not_run" and result["passed"] is None
    assert "measurement budget" in result["measurement_error"]


def test_inconclusive_first_attempt_never_creates_passing_evidence(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    actual = wbs._execute
    def execute(argv, *args, **kwargs):
        if argv[0] == "git":
            return actual(argv, *args, **kwargs)
        monkeypatch.setattr(wbs, "MAX_EVIDENCE_BYTES", 1)
        return dict(returncode=4, error=None, output="failed assertion")
    monkeypatch.setattr(wbs, "_execute", execute)
    result = check(repo, td)
    assert result["execution"] == "completed" and result["passed"] is None
    monkeypatch.undo()
    assert not wbs.read_state(str(td))["checks"]["A"]["passed"]
    assert progress(repo, td)["percentage"] == 0


def test_in_root_symlink_ancestor_never_attests_an_alias(tmp_path):
    repo, td = baseline(tmp_path)
    directory = repo / "source"
    directory.mkdir()
    (directory / "file.txt").write_text("ok")
    git(repo, "add", "source/file.txt")
    target = repo / "actual"
    directory.rename(target)
    directory.symlink_to(target, target_is_directory=True)
    assert progress(repo, td)["percentage"] is None


def test_ancestor_swap_after_path_validation_cannot_attest_external_bytes(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    source = repo / "source"
    source.mkdir()
    (source / "file.txt").write_text("inside")
    git(repo, "add", "source/file.txt")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside")
    actual = Path.is_symlink
    swapped = False
    def probe(path):
        nonlocal swapped
        answer = actual(path)
        if path == source and not swapped:
            swapped = True
            source.rename(repo / "saved-source")
            source.symlink_to(outside, target_is_directory=True)
        return answer
    monkeypatch.setattr(Path, "is_symlink", probe)
    assert progress(repo, td)["percentage"] is None
    assert swapped


def test_ignored_module_root_does_not_use_eager_listdir(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    mod = module(repo)
    with (mod / ".gitignore").open("a") as stream:
        stream.write("*.artifact\n")
    for n in range(32):
        (mod / f"{n}.artifact").write_text("ignored")
    original = os.listdir
    def listdir(path):
        assert Path(path) != mod, "module emptiness allocated an eager directory listing"
        return original(path)
    monkeypatch.setattr(os, "listdir", listdir)
    assert check(repo, td)["passed"]


def test_module_rebinding_during_git_enumeration_is_unavailable(tmp_path, monkeypatch):
    repo, td = baseline(tmp_path)
    mod = module(repo)
    outside = tmp_path / "outside-module"
    outside.mkdir()
    actual = wbs._execute
    swapped = False
    def execute(argv, cwd, *args, **kwargs):
        nonlocal swapped
        if argv[:3] == ["git", "ls-files", "--stage"] and Path(cwd).resolve() == mod and not swapped:
            swapped = True
            mod.rename(repo / "saved-module")
            mod.symlink_to(outside, target_is_directory=True)
        return actual(argv, cwd, *args, **kwargs)
    monkeypatch.setattr(wbs, "_execute", execute)
    assert progress(repo, td)["percentage"] is None
    assert swapped


def test_near_full_evidence_reserves_inconclusive_capacity_before_execution(tmp_path, monkeypatch):
    nodes = [node(f"A{n}", 1) for n in range(256)]
    repo, td = baseline(tmp_path, nodes)
    state = wbs.read_state(str(td))
    source = wbs.fingerprint(str(repo))
    for n in state["nodes"]:
        state["checks"][n["id"]] = dict(passed=True, source=source, definition=wbs.digest(n), checked_at=100,
                                        result=dict(returncode=0, error=None, output=""))
    target = 1024 * 1024 - 100
    size = len(wbs.encode_state(state).encode())
    for proof in state["checks"].values():
        amount = min(4000, target - size)
        proof["result"]["output"] = "a" * amount
        size += amount
    assert size == target
    (td / "WBS.json").write_text(wbs.encode_state(state))
    assert progress(repo, td)["percentage"] == 100
    before = (td / "WBS.json").read_bytes()
    monkeypatch.setattr(wbs, "_execute", forbidden_assertion_runner(wbs._execute))
    result = check(repo, td, "A0")
    assert result["execution"] == "not_run" and result["passed"] is None
    assert "storage budget" in result["measurement_error"]
    assert (td / "WBS.json").read_bytes() == before


def test_deleted_tracked_parent_is_measurable_as_missing_source(tmp_path):
    repo, td = baseline(tmp_path)
    (repo / "source").mkdir()
    (repo / "source" / "file.txt").write_text("source")
    git(repo, "add", "source/file.txt")
    assert check(repo, td)["passed"]
    (repo / "source" / "file.txt").unlink()
    (repo / "source").rmdir()
    assert progress(repo, td)["percentage"] == 0
