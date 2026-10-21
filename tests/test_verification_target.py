"""Actual copied-source ABA, restoration and relevant-input reuse scenarios."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import unittest

from test_wbs_input_policy import public
import verification_target as targets


def specification(**extra):
    return dict(inputs={"source": ["src.txt"]}, commands=[[sys.executable, "-c", "print('verification')"]],
                lenses=["review-code", "qa-cli"], **extra)


def test_handoffs_round_trip_all_supported_lenses_without_persisting_names(tmp_path):
    from _lib import SUPPORTED_LENSES, _infer_receipt_lens
    repo, td, _, _ = public(tmp_path)
    spec = dict(specification(), lenses=sorted(SUPPORTED_LENSES))
    target = targets.issue_target(str(td), str(repo), spec)
    before = (td / "WBS.json").read_bytes()
    first = targets.role_handoffs(target)
    repeated = targets.issue_target(str(td), str(repo), spec)
    second = targets.role_handoffs(repeated)
    assert target["token"] == repeated["token"]
    assert (td / "WBS.json").read_bytes() == before
    assert {row["lens"] for row in first} == SUPPORTED_LENSES
    assert {row["task_name"] for row in first}.isdisjoint(row["task_name"] for row in second)
    for row in first + second:
        assert _infer_receipt_lens(row["task_name"]) == row["lens"]
        assert targets.TOKEN.search(row["task_name"])[1] == target["token"]
        assert row["task_name"].encode() not in (td / "WBS.json").read_bytes()
    assert targets.role_handoffs(dict(target, spec=dict(spec, purpose="diagnostic", lenses=[]))) == []


def test_unsupported_or_malformed_lens_refused_before_publication(tmp_path):
    repo, td, _, _ = public(tmp_path)
    before = (td / "WBS.json").read_bytes()
    for lenses in (["review-invented"], ["qa-unknown"], ["ux-browser"], [None], [[]], ["qa-cli", "qa-cli"]):
        with unittest.TestCase().assertRaisesRegex(ValueError, "supported lenses"):
            targets.issue_target(str(td), str(repo), dict(specification(), lenses=lenses))
        assert (td / "WBS.json").read_bytes() == before
        assert not (td / "work/verification-targets").exists()


def test_owner_private_source_is_copied_without_changing_original_permissions(tmp_path):
    from install_verified import _read_payload_file

    repo, td, _, _ = public(tmp_path)
    source = repo / "src.txt"
    for mode in (0o600, 0o640, 0o400, 0o700, 0o750):
        source.chmod(mode)
        target = targets.issue_target(str(td), str(repo), specification())
        copied = Path(target["snapshot_root"]) / "src.txt"
        assert copied.read_bytes() == source.read_bytes()
        assert copied.stat().st_mode & 0o777 == (0o555 if mode & 0o100 else 0o444)
        assert source.stat().st_mode & 0o777 == mode
        assert _read_payload_file(repo, "src.txt") is None  # installer stays strict
        assert targets.applicability(str(td), str(repo), measure=True)["targets"][0]["status"] == "applicable"
    for mode in (0o660, 0o666, 0o4600, 0o0410):
        source.chmod(mode)
        with unittest.TestCase().assertRaisesRegex(ValueError, "unsafe ownership/mode"):
            targets.issue_target(str(td), str(repo), specification())
        assert source.stat().st_mode & 0o7777 == mode
    source.chmod(0o600)


def test_private_source_view_survives_aba_and_content_restoration(tmp_path):
    repo, td, _, _ = public(tmp_path)
    target = targets.issue_target(str(td), str(repo), specification())
    snapshot = Path(target["snapshot_root"])
    assert (snapshot / "src.txt").stat().st_ino != (repo / "src.txt").stat().st_ino
    (repo / "src.txt").write_text("B")
    actual = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path('src.txt').read_text())"], cwd=snapshot, capture_output=True, text=True, check=True)
    assert actual.stdout.strip() == "ok"
    assert targets.applicability(str(td), str(repo), measure=True)["targets"][0]["status"] == "stale"
    (repo / "src.txt").write_text("ok")
    (repo / "unrelated-doc.md").write_text("documentation only")
    assert targets.applicability(str(td), str(repo), measure=True)["targets"][0]["status"] == "applicable"
    restored = tmp_path / "restored"
    restored.mkdir()
    shutil.copy2(repo / "src.txt", restored / "src.txt")
    assert targets.applicability(str(td), str(restored), measure=True)["targets"][0]["status"] == "applicable"


def test_context_is_stored_only_and_missing_snapshot_stays_unavailable(tmp_path, monkeypatch):
    repo, td, _, _ = public(tmp_path)
    target = targets.issue_target(str(td), str(repo), specification())
    with monkeypatch.context() as patch:
        patch.setattr(targets, "_manifest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("read source")))
        assert targets.applicability(str(td), str(repo))["status"] == "unmeasured"
    path = Path(target["snapshot_root"]) / "src.txt"
    path.parent.chmod(0o700)
    path.unlink()
    status = targets.applicability(str(td), str(repo), measure=True)
    assert status["missing_lenses"]
    assert status["targets"][0]["status"] == "applicable"


def test_delayed_binding_reuses_preissued_source_not_current_checkout(tmp_path):
    repo, td, _, _ = public(tmp_path)
    target = targets.issue_target(str(td), str(repo), specification())
    (repo / "src.txt").write_text("changed after spawn")
    name = "code_review_case_vt_" + target["token"]
    start = dict(event="started", source="codex_session_watcher:collaboration", task_run_id=target["run_id"], agent_id="/root/" + name, agent_type=name, lens="review-code", runtime_id="native-1")
    completion = dict(start, event="completed")
    assert targets._receipt_target(completion, [start, completion]) == target["token"]
    followup = dict(completion, runtime_id="native-2")
    assert targets._receipt_target(followup, [start, completion, dict(followup, event="started"), followup]) is None
    status = targets.applicability(str(td), str(repo), measure=True)
    assert status["targets"][0]["status"] == "stale"


def test_build_context_and_new_command_are_not_silently_reused(tmp_path, monkeypatch):
    repo, td, _, _ = public(tmp_path)
    monkeypatch.setenv("TARGET_BUILD_FLAGS", "-O1")
    first = targets.issue_target(str(td), str(repo), specification(environment=["TARGET_BUILD_FLAGS"]))
    assert targets.issue_target(str(td), str(repo), specification(environment=["TARGET_BUILD_FLAGS"]))["token"] == first["token"]
    monkeypatch.setenv("TARGET_BUILD_FLAGS", "-O2")
    assert targets.applicability(str(td), str(repo), measure=True)["targets"][0]["status"] == "stale"
    second = targets.issue_target(str(td), str(repo), specification(environment=["TARGET_BUILD_FLAGS"]))
    assert second["token"] != first["token"]
    stored = (td / "WBS.json").read_text()
    assert "-O1" not in stored and "-O2" not in stored
    with unittest.TestCase().assertRaisesRegex(ValueError, "explicit source"):
        targets.issue_target(str(td), str(repo), dict(specification(), inputs={}))


def test_diagnostic_does_not_certify_acceptance_or_enable_new_gate(tmp_path):
    repo, td, _, _ = public(tmp_path)
    assert targets.applicability(str(td), str(repo), measure=True)["status"] == "unbound"
    targets.issue_target(str(td), str(repo), specification(purpose="diagnostic"))
    assert not targets.applicability(str(td), str(repo), measure=True)["enforced"]


def test_symlink_and_task_runtime_inputs_fail_without_omission(tmp_path):
    repo, td, _, _ = public(tmp_path)
    (repo / "alias").symlink_to(repo / "src.txt")
    with unittest.TestCase().assertRaisesRegex(ValueError, "symlink"):
        targets.issue_target(str(td), str(repo), dict(specification(), inputs={"source": ["alias"]}))
    with unittest.TestCase().assertRaisesRegex(ValueError, "recursively captured"):
        targets.issue_target(str(td), str(repo), dict(specification(), inputs={"source": ["doc/harness/tasks/TASK__sample/PLAN.md"]}))


def parent_proof_fixture(tmp_path, monkeypatch):
    from test_codex_lifecycle_watcher import _load, _spawn_events
    mod = _load()
    child = "019f82a6-ce64-75a3-b01d-92f7b0b4fe6f"
    name = "lead_source_target"
    path = tmp_path / "parent.jsonl"
    events = _spawn_events("parent", child, name, "/root/" + name)
    path.write_text("".join(json.dumps(event) + "\n" for event in events))
    monkeypatch.setattr(mod, "_find_rollout", lambda _: path)
    monkeypatch.setattr(mod, "_sessions_root", lambda: tmp_path)
    return mod, path, child, "/root/" + name


def test_live_parent_append_does_not_starve_native_child_proof(tmp_path, monkeypatch):
    mod, path, child, agent = parent_proof_fixture(tmp_path, monkeypatch)
    original = mod._spawn_activity
    appended = []
    def while_parent_is_active(event, **kwargs):
        if not appended:
            with path.open("a") as stream:
                stream.write(json.dumps({"type": "event_msg", "payload": {"type": "token_count"}}) + "\n")
            appended.append(True)
        return original(event, **kwargs)
    monkeypatch.setattr(mod, "_spawn_activity", while_parent_is_active)
    assert mod._parent_spawn_proof("parent", child, agent)
    assert appended
    # A later exact duplicate remains invalid in the next coherent prefix.
    with path.open("a") as stream:
        from test_codex_lifecycle_watcher import _spawn_events
        stream.write("".join(json.dumps(event) + "\n" for event in _spawn_events("parent", child, agent.rsplit("/", 1)[1], agent)))
    assert not mod._parent_spawn_proof("parent", child, agent)


def test_native_parent_prefix_rewrite_or_replacement_still_rejected(tmp_path, monkeypatch):
    mod, path, child, agent = parent_proof_fixture(tmp_path, monkeypatch)
    original, changed = mod._spawn_activity, []
    def rewrite_during_proof(event, **kwargs):
        if not changed:
            path.write_bytes(path.read_bytes().replace(b"lead_source_target", b"lead_changed_value"))
            changed.append(True)
        return original(event, **kwargs)
    monkeypatch.setattr(mod, "_spawn_activity", rewrite_during_proof)
    with unittest.TestCase().assertRaises(mod._AncestryPending):
        mod._parent_spawn_proof("parent", child, agent)


def test_long_parent_has_separate_bounded_capacity(tmp_path, monkeypatch):
    mod, path, child, agent = parent_proof_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(mod, "MAX_CHILD_BYTES", 64)
    assert path.stat().st_size > mod.MAX_CHILD_BYTES
    assert mod._parent_spawn_proof("parent", child, agent)
    monkeypatch.setattr(mod, "MAX_PARENT_BYTES", 64)
    assert not mod._parent_spawn_proof("parent", child, agent)


def test_native_parent_path_replacement_is_rejected(tmp_path, monkeypatch):
    mod, path, child, agent = parent_proof_fixture(tmp_path, monkeypatch)
    original, replaced = mod._spawn_activity, []
    def replace_during_proof(event, **kwargs):
        if not replaced:
            replacement = path.with_suffix(".replacement")
            replacement.write_bytes(path.read_bytes())
            os.replace(replacement, path)
            replaced.append(True)
        return original(event, **kwargs)
    monkeypatch.setattr(mod, "_spawn_activity", replace_during_proof)
    with unittest.TestCase().assertRaises(mod._AncestryPending):
        mod._parent_spawn_proof("parent", child, agent)


def test_direct_diagnostic_has_no_native_lens_or_acceptance_side_effect(tmp_path):
    repo, td, _, _ = public(tmp_path)
    diagnostic = dict(specification(purpose="diagnostic"), lenses=[])
    issued = targets.issue_target(str(td), str(repo), diagnostic)
    assert issued["spec"]["lenses"] == []
    assert not targets.applicability(str(td), str(repo), measure=True)["enforced"]


def test_reissued_multi_lens_view_reactivates_every_requested_lens(tmp_path):
    repo, td, _, _ = public(tmp_path)
    original = specification()
    first = targets.issue_target(str(td), str(repo), original)
    narrower = targets.issue_target(str(td), str(repo), dict(original, lenses=["qa-cli"]))
    assert first["token"] != narrower["token"]
    reused = targets.issue_target(str(td), str(repo), original)
    assert reused["token"] == first["token"]
    index = json.loads((td / "WBS.json").read_text())[targets.FIELD]
    assert {row["token"] for row in index["required"].values()} == {first["token"]}


def test_missing_history_does_not_prevent_independent_replacement(tmp_path):
    repo, td, _, _ = public(tmp_path)
    old = targets.issue_target(str(td), str(repo), specification())
    old_metadata = Path(old["snapshot_root"]).parent / "target.json"
    old_metadata.unlink()
    changed = dict(specification(), lenses=["qa-cli"], commands=[[sys.executable, "--version"]])
    replacement = targets.issue_target(str(td), str(repo), changed)
    assert replacement["token"] != old["token"]
    result = targets.applicability(str(td), str(repo), measure=True)
    status = {row["token"]: row["status"] for row in result["targets"]}
    assert status[old["token"]] == "unavailable"
    assert status[replacement["token"]] == "applicable"
    assert "review-code" in result["missing_lenses"]


def test_runtime_outputs_are_writable_without_mutating_frozen_inputs(tmp_path):
    repo, td, _, _ = public(tmp_path)
    original = specification()
    first = targets.issue_target(str(td), str(repo), original, output_dirs=["doc/harness/tasks"])
    root = Path(first["snapshot_root"])
    (root / "doc/harness/tasks/.active.fixture.lock").write_text("runtime-only")
    assert (root / "src.txt").stat().st_mode & 0o222 == 0
    assert targets.issue_target(str(td), str(repo), original, output_dirs=["doc/harness/tasks"])["token"] == first["token"]
    assert targets.applicability(str(td), str(repo), measure=True)["targets"][0]["status"] == "applicable"
    with unittest.TestCase().assertRaisesRegex(ValueError, "overlaps selected input"):
        targets.issue_target(str(td), str(repo), original, output_dirs=["src.txt"])
    with unittest.TestCase().assertRaisesRegex(ValueError, "canonical relative"):
        targets.issue_target(str(td), str(repo), original, output_dirs=["../outside"])
