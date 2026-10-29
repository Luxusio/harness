from __future__ import annotations

import importlib.util
import hashlib
import json
import sys
import threading
import importlib.machinery
import subprocess
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock


ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "task_control_lib", ROOT / "plugin/scripts/_lib.py"
)
assert SPEC and SPEC.loader
lib = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = lib
SPEC.loader.exec_module(lib)


def _task(tmp_path: Path, lenses: dict | None = None) -> Path:
    manifest = tmp_path / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\ntype: library\n", encoding="utf-8")
    task = tmp_path / "doc/harness/tasks/TASK__no-git"
    task.mkdir(parents=True, exist_ok=True)
    control_path = task / lib.TASK_CONTROL_NAME
    if not control_path.exists():
        control_path.write_text(json.dumps({
            "run_id": lib.new_uuid7(), "execution_mode": "standard",
            "required_lenses": ["review-code", "qa-cli"],
            "closed": False,
        }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (task / "PLAN.md").write_text("# plan\n", encoding="utf-8")
    if lenses is not None:
        control = lib.read_task_control(task)
        requested = set(lenses.get("required_lenses", [])) & lib.SUPPORTED_LENSES
        requested.add("review-code")
        if not requested & lib.QA_LENSES:
            requested.add("qa-cli")
        control["required_lenses"] = [
            lens for lens in lib.LENS_ORDER if lens in requested
        ]
        (task / lib.TASK_CONTROL_NAME).write_text(
            json.dumps(control, indent=2, sort_keys=True) + "\n", encoding="utf-8",
        )
    return task

def test_scaffold_uses_only_exact_task_control_and_does_not_inspect_git(tmp_path):
    task = _task(tmp_path)
    assert not (task / "TASK_BASELINE.json").exists()
    assert set(lib.read_task_control(task)) == lib.TASK_CONTROL_FIELDS
    for legacy in (
        "TASK_STATE.yaml", "TASK_RUN.json", "PLAN.meta.json",
        "TASK_CLOSE_RECEIPT.json", "INSTALL_RECEIPT.json",
        "AUDIT_TRAIL.md", "ENVIRONMENT_SNAPSHOT.md",
    ):
        assert not (task / legacy).exists()

def test_task_control_exact_four_field_schema_fails_closed(tmp_path):
    task = _task(tmp_path)
    valid = lib.read_task_control(task)
    assert set(valid) == lib.TASK_CONTROL_FIELDS

    for mutation in (
        {**valid, "legacy_status": "open"},
        {key: value for key, value in valid.items() if key != "execution_mode"},
        {**valid, "run_id": "a" * 32},
        {**valid, "closed": "true"},
        {**valid, "closed": 1},
        {**valid, "required_lenses": ["review-security", "qa-cli"]},
        {**valid, "required_lenses": ["review-code"]},
        {**valid, "required_lenses": ["review-code", "qa-cli", "qa-cli"]},
        {**valid, "required_lenses": ["review-code", "qa-unknown"]},
    ):
        (task / "TASK.json").write_text(json.dumps(mutation) + "\n", encoding="utf-8")
        assert lib.read_task_control(task) == {}

    (task / "TASK.json").write_text(json.dumps(valid) + "\n", encoding="utf-8")
    assert lib.read_task_control(task) == valid

def test_uuid7_identity_is_canonical_timestamped_and_rotates(tmp_path):
    timestamp_ms = 1_786_424_400_900
    run_id = lib.new_uuid7(timestamp_ms)
    assert run_id == run_id.lower()
    assert run_id[14] == "7"
    assert run_id[19] in "89ab"
    assert lib.uuid7_timestamp_ms(run_id) == timestamp_ms
    assert lib.task_run_started_at({"run_id": run_id}) == "2026-08-11T05:00:00.900Z"

    task = _task(tmp_path)
    first = lib.read_task_control(task)["run_id"]
    rotated = dict(lib.read_task_control(task))
    rotated["run_id"] = lib.new_uuid7()
    (task / lib.TASK_CONTROL_NAME).write_text(
        json.dumps(rotated, indent=2, sort_keys=True) + "\n", encoding="utf-8",
    )
    assert rotated["run_id"] != first
    assert lib.uuid7_timestamp_ms(rotated["run_id"]) >= lib.uuid7_timestamp_ms(first)

def test_task_control_routes_lenses_with_safe_defaults_and_explicit_security(tmp_path):
    default_task = _task(tmp_path / "default")
    assert lib.required_review_lenses(default_task) == ["review-code"]
    assert lib._required_qa_lenses(default_task) == ["qa-cli"]

    explicit_task = _task(
        tmp_path / "explicit",
        {
            "required_lenses": [
                "review-security", "unknown", "qa-browser", "qa-api", "qa-browser",
            ],
        },
    )
    assert lib.required_review_lenses(explicit_task) == [
        "review-code",
        "review-security",
    ]
    assert lib._required_qa_lenses(explicit_task) == ["qa-api", "qa-browser"]

    malformed_task = _task(tmp_path / "malformed")
    (malformed_task / "TASK.json").write_text("not json", encoding="utf-8")
    assert lib.required_review_lenses(malformed_task) == []
    assert lib._required_qa_lenses(malformed_task) == []

def test_control_writer_rejects_forged_first_module_and_rebinding():
    import pytest

    expected = ROOT / "plugin/mcp/harness_server.py"
    module = ModuleType("harness_server")
    module.__file__ = str(expected)
    module.__spec__ = importlib.util.spec_from_loader(
        "harness_server",
        importlib.machinery.SourceFileLoader("harness_server", str(expected)),
    )
    module.__dict__["bind"] = lib._bind_control_writer
    forged = compile(
        "def handle_task_start(args):\n  return None\nbind(handle_task_start)\n",
        str(expected),
        "exec",
    )
    with mock.patch.dict(sys.modules, {"harness_server": module}):
        with pytest.raises(PermissionError, match="canonical module import"):
            exec(forged, module.__dict__)

    server = sys.modules.get("harness_server")
    if server is not None:
        with pytest.raises(PermissionError, match="canonical module import"):
            lib._bind_control_writer(server.handle_task_start)

def test_control_writer_accepts_canonical_script_entrypoints():
    for script in (
        ROOT / "plugin/mcp/harness_server.py",
        ROOT / "plugin/scripts/codex_hook_registration.py",
    ):
        completed = subprocess.run(
            [sys.executable, str(script)], input=b"", capture_output=True, timeout=10,
        )
        assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")

def test_task_control_and_active_marker_mutators_require_runtime_owner(tmp_path):
    task = _task(tmp_path)
    control = lib.read_task_control(task)
    for operation in (
        lambda: lib.begin_task_run(task),
        lambda: lib.publish_task_close(task, control),
        lambda: lib.write_active_marker(str(tmp_path), str(task), session_id="foreign"),
        lambda: lib.clear_active_marker(str(tmp_path), str(task), session_id="foreign"),
    ):
        try:
            operation()
        except PermissionError as exc:
            assert "task-control" in str(exc)
        else:
            raise AssertionError("arbitrary control-authority mutation was accepted")

def test_nested_git_never_inherits_ancestor_installation_from_task_binding(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    manifest = root / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("version: 5\nsource_git_roots: []\n", encoding="utf-8")
    nested = root / "cleo-v4-web/src"
    nested.mkdir(parents=True)
    (root / "cleo-v4-web/.git").mkdir()
    task = _task(root)
    monkeypatch.setattr(lib, "_LAST_HOOK_INPUT", {})
    monkeypatch.setenv("HARNESS_SESSION_ID", "nested-root-fixture")
    sid = lib.current_session_id()
    sessions = root / lib.TASK_DIR / lib.ACTIVE_SESSIONS_DIRNAME
    sessions.mkdir(parents=True, exist_ok=True)
    (sessions / f"{sid}.json").write_text(json.dumps({
        "session_id": sid, "task_dir": str(task), "task_id": task.name,
        "run_id": lib.read_task_control(task)["run_id"], "updated": lib.now_iso(),
    }) + "\n", encoding="utf-8")
    (root / lib.TASK_DIR / ".active").write_text(str(task) + "\n", encoding="utf-8")

    assert lib.harness_root_resolution(nested) == ("", "")
    assert lib.find_harness_root(nested) == ""

    marker = Path(lib._session_active_path(str(root)))
    original = json.loads(marker.read_text(encoding="utf-8"))
    for mutation in ({"run_id": lib.new_uuid7()}, {"conflicts": []}):
        marker.write_text(json.dumps(dict(original, **mutation)), encoding="utf-8")
        assert lib.harness_root_resolution(nested) == ("", "")
    marker.write_text(json.dumps(original), encoding="utf-8")

    # The repository-wide legacy marker alone must not authorize a nested Git
    # boundary. Neither an exact session binding nor a legacy marker is setup.
    Path(lib._session_active_path(str(root))).unlink()
    assert lib.harness_root_resolution(nested) == ("", "")

def test_task_directory_transaction_is_nested_serialized_and_control_is_mcp_owned(tmp_path):
    task = _task(tmp_path / "serialized")
    first_entered = threading.Event()
    release_first = threading.Event()
    second_entered = threading.Event()

    def first_worker():
        with lib.task_control_transaction(task):
            with lib.task_control_transaction(task):
                first_entered.set()
                assert release_first.wait(5)

    def second_worker():
        assert first_entered.wait(5)
        with lib.task_control_transaction(task):
            second_entered.set()

    first = threading.Thread(target=first_worker)
    second = threading.Thread(target=second_worker)
    first.start()
    second.start()
    assert first_entered.wait(5)
    assert not second_entered.wait(0.1)
    release_first.set()
    first.join(5)
    second.join(5)
    assert second_entered.is_set()

    original = _task(tmp_path / "replacement")
    displaced = original.with_name("TASK__displaced")
    try:
        with lib.task_control_transaction(original):
            original.rename(displaced)
            original.mkdir()
    except RuntimeError as exc:
        assert "integrity" in str(exc)
    else:
        raise AssertionError("task-directory replacement must fail closed")

    publication = _task(tmp_path / "publication")
    original_control = lib.read_task_control(publication)
    displaced = publication.with_name("TASK__publication-displaced")
    updated = dict(original_control)
    updated["required_lenses"] = ["review-code", "review-security", "qa-cli"]
    try:
        lib.write_task_control(publication, updated)
    except PermissionError as exc:
        assert "task-control MCP" in str(exc)
    else:
        raise AssertionError("arbitrary TASK.json mutation was accepted")
    assert lib.read_task_control(publication) == original_control

def test_task_publication_rejects_replaced_ancestor_chain(tmp_path):
    for ancestor_name in ("doc", "harness", "tasks"):
        root = tmp_path / ancestor_name
        task = _task(root)
        original = lib.read_task_control(task)
        updated = dict(original)
        updated["required_lenses"] = ["review-code", "review-security", "qa-cli"]
        ancestors = {
            "doc": root / "doc",
            "harness": root / "doc/harness",
            "tasks": root / "doc/harness/tasks",
        }
        try:
            lib.write_task_control(task, updated)
        except PermissionError as exc:
            assert "task-control MCP" in str(exc)
        else:
            raise AssertionError(f"arbitrary mutation through {ancestor_name} was accepted")
        assert lib.read_task_control(task) == original

def test_symlinked_manifest_remains_untrusted(tmp_path):
    root = tmp_path / "workspace"
    harness_dir = root / "doc/harness"
    harness_dir.mkdir(parents=True)
    external = tmp_path / "manifest.yaml"
    external.write_text("version: 5\n", encoding="utf-8")
    (harness_dir / "manifest.yaml").symlink_to(external)
    child = root / "child"
    child.mkdir()

    resolved, error = lib.harness_root_resolution(child)
    assert resolved == str(root.resolve())
    assert "non-symlink" in error
    assert lib.find_harness_root(child) == ""


def _exact_marker(repo, task, sid, **overrides):
    sessions = repo / lib.TASK_DIR / lib.ACTIVE_SESSIONS_DIRNAME
    sessions.mkdir(parents=True, exist_ok=True)
    value = {"session_id": sid, "task_dir": str(task), "task_id": task.name,
             "run_id": lib.read_task_control(task)["run_id"]}
    value.update(overrides)
    marker = sessions / (sid + ".json")
    marker.write_text(json.dumps(value), encoding="utf-8")
    return marker


def test_exact_active_resolution_never_borrows_legacy_or_another_session(tmp_path):
    task = _task(tmp_path)
    _exact_marker(tmp_path, task, "owned")
    legacy = tmp_path / lib.TASK_DIR / ".active"
    legacy.write_text(str(task), encoding="utf-8")
    assert lib.resolve_active_task_dir(str(tmp_path), session_id="owned") == str(task)
    for sid in ("", "default", "foreign", "owned/alias"):
        assert lib.resolve_active_task_dir(str(tmp_path), session_id=sid) == ""
    assert legacy.read_text(encoding="utf-8") == str(task)


def test_exact_active_resolution_and_iteration_reject_stale_runs_and_fences(tmp_path):
    task = _task(tmp_path)
    marker = _exact_marker(tmp_path, task, "owned")
    assert lib.active_task_binding_matches(str(tmp_path), str(task), session_id="owned")
    assert list(lib.iter_active_task_dirs(str(tmp_path))) == [str(task)]
    for overrides in ({"run_id": lib.new_uuid7()}, {"conflicts": []}, {"conflicts_overflow": True}):
        _exact_marker(tmp_path, task, "owned", **overrides)
        assert lib.resolve_active_task_dir(str(tmp_path), session_id="owned") == ""
        assert not lib.active_task_binding_matches(str(tmp_path), str(task), session_id="owned")
        assert list(lib.iter_active_task_dirs(str(tmp_path))) == []
    _exact_marker(tmp_path, task, "owned")
    control = lib.read_task_control(task)
    (task / "TASK.json").write_text(json.dumps(dict(control, closed=True)), encoding="utf-8")
    assert lib.resolve_active_task_dir(str(tmp_path), session_id="owned") == ""
    assert list(lib.iter_active_task_dirs(str(tmp_path))) == []


def test_missing_session_has_no_default_identity_or_marker_snapshot(tmp_path, monkeypatch):
    for name in ("HARNESS_SESSION_ID", "CODEX_SESSION_ID", "CODEX_THREAD_ID", "CLAUDE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(lib, "_LAST_HOOK_INPUT", {})
    assert lib.current_session_id() == ""
    assert lib.active_marker_snapshot(str(tmp_path)) == {}
    assert not (tmp_path / lib.TASK_DIR).exists()
    monkeypatch.setattr(lib, "_LAST_HOOK_INPUT", {"session_id": "bad/session"})
    assert lib.current_session_id() == ""


def test_active_snapshot_contains_only_the_exact_session(tmp_path):
    task = _task(tmp_path)
    marker = _exact_marker(tmp_path, task, "owned")
    legacy = tmp_path / lib.TASK_DIR / ".active"
    legacy.write_text(str(task), encoding="utf-8")
    assert set(lib.active_marker_snapshot(str(tmp_path), session_id="owned")) == {str(marker)}
