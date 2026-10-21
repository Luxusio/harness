"""Public target issuance and honest lifecycle/applicability distinction."""
from test_verification_target import specification
from test_wbs_input_policy import public
import verification_target as targets


def test_public_target_returns_persistent_frozen_view_and_context_does_not_measure(tmp_path):
    repo, td, call, opened = public(tmp_path)
    result = call("target", target=specification())
    assert not result.get("isError"), result
    target = result["structuredContent"]["target"]
    assert target["run_id"] == opened["run_id"]
    assert "/work/verification-targets/" in target["snapshot_root"]
    context = call("context")["structuredContent"]["task_context"]
    assert context["verification_targets"]["status"] == "unmeasured"
    assert targets.applicability(str(td), str(repo), measure=True)["missing_lenses"] == ["review-code", "qa-cli"]


def test_public_handoffs_are_fresh_on_reused_target_and_supported_before_publish(tmp_path):
    from _lib import _infer_receipt_lens
    _, td, call, _ = public(tmp_path)
    spec = dict(specification(), lenses=["review-code", "review-security", "qa-cli"])
    first = call("target", target=spec)["structuredContent"]
    second = call("target", target=spec)["structuredContent"]
    assert first["target"]["token"] == second["target"]["token"]
    assert first["role_handoffs"] != second["role_handoffs"]
    for response in (first, second):
        assert [row["lens"] for row in response["role_handoffs"]] == spec["lenses"]
        for row in response["role_handoffs"]:
            assert _infer_receipt_lens(row["task_name"]) == row["lens"]
            assert targets.TOKEN.search(row["task_name"])[1] == response["target"]["token"]
    before = (td / "WBS.json").read_bytes()
    assert call("target", target=dict(spec, lenses=["qa-invented"])).get("isError")
    assert (td / "WBS.json").read_bytes() == before


def test_target_action_rejects_extra_fields_with_actionable_error(tmp_path):
    _, _, call, _ = public(tmp_path)
    result = call("target", target=specification(), timeout=60)
    assert result.get("isError"), result


def native_receipts(td, target, *, followup=False):
    from test_harness_mcp_server import _record_receipt_fixture
    for number, (lens, prefix) in enumerate((("review-code", "code_review"), ("qa-cli", "qa_cli")), 1):
        name = prefix + "_case_vt_" + target["token"]
        identity = dict(source="codex_session_watcher:collaboration", agent_id="/root/" + name,
                        agent_type=name, lens=lens,
                        runtime_id=f"codex:01a151ba-9a9e-7471-840a-d2b50482db20:call_target{number}:01a15c0f-e97d-7237-b012-b9a04c0f6702")
        _record_receipt_fixture(str(td), dict(identity, event="started"))
        summary = "VERDICT: PASS"
        if lens.startswith("review-"):
            summary += "\nFINDING_COUNTS: FIX_NOW=0 INVESTIGATE=0 OPTIONAL=0"
        _record_receipt_fixture(str(td), dict(identity, event="completed", summary=summary))
        if followup:
            identity["runtime_id"] = identity["runtime_id"].replace("call_target", "call_followup")
            _record_receipt_fixture(str(td), dict(identity, event="started"))
            _record_receipt_fixture(str(td), dict(identity, event="completed", summary=summary))


def test_authentic_lifecycle_selection_and_target_gate_reuse(tmp_path):
    from test_wbs_input_policy import policy_plan
    from test_wbs import node
    from install_verified import _verification_state
    repo, td, call, _ = public(tmp_path)
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    issued = call("target", target=specification())["structuredContent"]["target"]
    native_receipts(td, issued)
    assert not call("check", node_id="A").get("isError")
    verdict = call("verify")["structuredContent"]
    assert verdict["runtime_verdict"] == "PASS", verdict
    assert verdict["verification_targets"]["status"] == "applicable"
    assert _verification_state(td)[0]
    (repo / "documentation.md").write_text("unrelated doc change")
    assert call("verify")["structuredContent"]["runtime_verdict"] == "PASS"
    # Lost copied source is regenerable; completed evidence stays valid.
    source = __import__("pathlib").Path(issued["snapshot_root"])
    source.chmod(0o700)
    (source / "src.txt").unlink()
    assert _verification_state(td)[0]
    restored = targets.ensure_snapshot(str(td), issued["token"], str(repo))
    assert restored["token"] == issued["token"] and restored["snapshot_root"] != str(source)
    (repo / "src.txt").write_text("changed execution source")
    blocked = call("verify")["structuredContent"]
    assert blocked["runtime_verdict"] == "PENDING"
    assert "Verification target" in blocked["next_action"]
    assert not _verification_state(td)[0]
    closed = call("close")
    assert closed.get("isError")
    assert "applicable verification target" in str(closed)


def test_followup_lifecycle_pass_does_not_inherit_frozen_target(tmp_path):
    repo, td, call, _ = public(tmp_path)
    issued = call("target", target=specification())["structuredContent"]["target"]
    native_receipts(td, issued, followup=True)
    result = targets.applicability(str(td), str(repo), measure=True)
    assert set(result["missing_lenses"]) == {"review-code", "qa-cli"}


def test_direct_diagnostic_skips_native_registration_preflight(tmp_path, monkeypatch):
    from test_harness_mcp_server import harness_server as server
    _, _, call, _ = public(tmp_path)
    def forbidden(*args, **kwargs):
        raise AssertionError("direct diagnostic attempted native registration")
    monkeypatch.setattr(server, "_authenticate_native_session", forbidden)
    result = call("target", target=dict(specification(purpose="diagnostic"), lenses=[]))
    assert not result.get("isError"), result
    assert result["structuredContent"]["issued"] is True
    assert result["structuredContent"]["role_handoffs"] == []
    assert "manifest" not in result["structuredContent"]["target"]


def test_native_mcp_preflight_budget_is_finite_and_separate_from_hook(tmp_path, monkeypatch):
    from test_harness_mcp_server import harness_server as server
    from codex_hook_registration import REGISTRATION_BUDGET_SECONDS
    observed = []
    monkeypatch.setattr(server, "_server_runtime", lambda: "codex")
    monkeypatch.setattr(server, "_current_session_identity", lambda *a: "01a15c0f-e97d-7237-b012-b9a04c0f6702")
    def register(*args, **kwargs):
        observed.append(kwargs["budget_seconds"])
        kwargs["status_out"].update(status=server._REGISTRATION_REGISTERED)
        return True
    monkeypatch.setattr(server, "_register_native_task", register)
    result = server._register_task_start_watcher(str(tmp_path), str(tmp_path / "task"), {"run_id": "run"}, session_cwd=str(tmp_path))
    assert result["registered"] is True
    assert observed == [8.0]
    assert REGISTRATION_BUDGET_SECONDS == 2.0


def test_missing_completion_preserves_exact_attestation_park_guidance(tmp_path):
    import json
    from test_harness_mcp_server import harness_server as server
    repo, td, call, _ = public(tmp_path)
    issued = call("target", target=specification())["structuredContent"]["target"]
    native_receipts(td, issued)
    path = td / "RECEIPTS.jsonl"
    rows = path.read_text().splitlines(keepends=True)
    assert json.loads(rows[-1])["event"] == "completed"
    assert json.loads(rows[-1])["lens"] == "qa-cli"
    path.write_text("".join(rows[:-1]))
    result = call("verify")["structuredContent"]
    assert result["runtime_verdict"] == "PENDING"
    assert result["verification_targets"]["targets"][0]["status"] == "applicable"
    assert result["verification_targets"]["missing_lenses"] == ["qa-cli"]
    assert server.ATTESTATION_BLOCKED_REASON in result["next_action"]
    assert server.ATTESTATION_UNBLOCK_CONDITION in result["next_action"]
    assert "issue a target" not in result["next_action"]


def test_negative_review_preserves_remediation_or_block_guidance(tmp_path):
    from test_harness_mcp_server import _record_receipt_fixture
    for verdict in ("FAIL", "BLOCKED_ENV"):
        case = tmp_path / verdict
        case.mkdir()
        _, td, call, _ = public(case)
        issued = call("target", target=specification())["structuredContent"]["target"]
        name = "code_review_negative_vt_" + issued["token"]
        identity = dict(source="codex_session_watcher:collaboration", agent_id="/root/" + name,
                        agent_type=name, lens="review-code",
                        runtime_id="codex:01a151ba-9a9e-7471-840a-d2b50482db20:call_negative:01a15c0f-e97d-7237-b012-b9a04c0f6702")
        _record_receipt_fixture(str(td), dict(identity, event="started"))
        summary = "VERDICT: " + verdict + "\nFINDING_COUNTS: FIX_NOW=" + ("1" if verdict == "FAIL" else "0") + " INVESTIGATE=" + ("1" if verdict == "BLOCKED_ENV" else "0") + " OPTIONAL=0"
        _record_receipt_fixture(str(td), dict(identity, event="completed", summary=summary))
        result = call("verify")["structuredContent"]
        assert result["runtime_verdict"] == verdict
        assert "issue a target" not in result["next_action"]
        if verdict == "FAIL":
            assert "Remediate" in result["next_action"] and "do not start QA" in result["next_action"]
        else:
            assert "block" in result["next_action"] and "do not start QA" in result["next_action"]
