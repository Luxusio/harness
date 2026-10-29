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
    assert targets.applicability(str(td), str(repo), measure=True)["status"] == "applicable"


def test_target_action_rejects_extra_fields_with_actionable_error(tmp_path):
    _, _, call, _ = public(tmp_path)
    result = call("target", target=specification(), timeout=60)
    assert result.get("isError"), result


def test_authentic_lifecycle_selection_and_target_gate_reuse(tmp_path):
    from test_wbs_input_policy import policy_plan
    from test_wbs import node
    from install_verified import _verification_state
    repo, td, call, _ = public(tmp_path)
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    issued = call("target", target=specification())["structuredContent"]["target"]
    assert not call("check", node_id="A").get("isError")
    verdict = call("verify")["structuredContent"]
    assert verdict["runtime_verdict"] == "PASS", verdict
    assert verdict["verification_targets"]["status"] == "applicable"
    assert _verification_state(td)[0]
    (repo / "documentation.md").write_text("unrelated doc change")
    assert call("verify")["structuredContent"]["runtime_verdict"] == "PASS"
    # Missing copies are regenerable; present tampered bytes remain invalid.
    source = __import__("pathlib").Path(issued["snapshot_root"])
    source.chmod(0o700)
    (source / "src.txt").unlink()
    assert _verification_state(td)[0]
    restored = targets.ensure_snapshot(str(td), issued["token"], str(repo))
    assert restored["token"] == issued["token"] and restored["snapshot_root"] != str(source)
    assert _verification_state(td)[0]
    (repo / "src.txt").write_text("changed execution source")
    blocked = call("verify")["structuredContent"]
    assert blocked["runtime_verdict"] == "PENDING"
    assert "applicable verification target inputs" in blocked["missing_for_close"]
    assert not _verification_state(td)[0]
    closed = call("close")
    assert closed.get("isError")
    assert "applicable verification target" in str(closed)


def test_direct_diagnostic_needs_no_native_registration(tmp_path):
    from test_harness_mcp_server import harness_server as server
    _, _, call, _ = public(tmp_path)
    # Receipt collectors and native registration are absent from target issuance.
    assert not hasattr(server, "_register_task_start_watcher")
    result = call("target", target=dict(specification(purpose="diagnostic"), lenses=[]))
    assert not result.get("isError"), result
    assert result["structuredContent"]["issued"] is True
    assert "role_handoffs" not in result["structuredContent"]
    assert "manifest" not in result["structuredContent"]["target"]


def test_retired_lens_uses_current_obligations_and_reintroduction(tmp_path):
    from test_wbs_input_policy import policy_plan
    from test_wbs import node
    from pathlib import Path

    import sys

    item = node()
    item['check'] = [sys.executable, '-c', "from pathlib import Path;assert Path('src.txt').read_text() in ('ok','changed')"]
    repo, td, call, _ = public(tmp_path)
    plan = policy_plan([item], {'source': ['src.txt']})
    all_lenses = ['review-code', 'review-security', 'qa-cli']
    current = ['review-code', 'qa-cli']
    assert not call('plan', plan=plan, required_lenses=all_lenses).get('isError')
    old = call('target', target=dict(specification(), lenses=all_lenses))['structuredContent']['target']
    metadata = Path(old['snapshot_root']).parent / 'target.json'
    original = metadata.read_bytes()
    (repo / 'src.txt').write_text('changed')
    assert not call('plan', plan=plan, required_lenses=current).get('isError')
    latest = call('target', target=dict(specification(), lenses=current))
    assert not latest.get('isError'), latest
    assert call('check', node_id='A')['structuredContent']['passed']
    verified = call('verify')['structuredContent']
    assert verified['runtime_verdict'] == 'PASS', verified
    assert verified['verification_targets']['missing_lenses'] == []
    assert {r['token'] for r in verified['verification_targets']['targets']} == {latest['structuredContent']['target']['token']}
    assert metadata.read_bytes() == original
    assert not call('plan', plan=plan, required_lenses=all_lenses).get('isError')
    reintroduced = call('verify')['structuredContent']
    assert reintroduced['runtime_verdict'] == 'PENDING'
    assert reintroduced['verification_targets']['missing_lenses'] == ['review-security']
    assert not call('target', target=dict(specification(), lenses=['review-security'])).get('isError')
    assert call('verify')['structuredContent']['runtime_verdict'] == 'PASS'
    assert metadata.read_bytes() == original


def test_retired_lens_missing_metadata_is_not_read_until_reintroduced(tmp_path, monkeypatch):
    from test_wbs_input_policy import policy_plan
    from test_wbs import node
    from pathlib import Path

    repo, td, call, _ = public(tmp_path)
    plan = policy_plan([node()], {'source': ['src.txt']})
    all_lenses = ['review-code', 'review-security', 'qa-cli']
    current = ['review-code', 'qa-cli']
    assert not call('plan', plan=plan, required_lenses=all_lenses).get('isError')
    old = call('target', target=dict(specification(), lenses=all_lenses))['structuredContent']['target']
    assert not call('plan', plan=plan, required_lenses=current).get('isError')
    assert not call('target', target=dict(specification(), lenses=current)).get('isError')
    (Path(old['snapshot_root']).parent / 'target.json').unlink()
    assert call('check', node_id='A')['structuredContent']['passed']
    real = targets.load_target
    def load(task_dir, token):
        assert token != old['token'], 'retired target was read'
        return real(task_dir, token)
    with monkeypatch.context() as guard:
        guard.setattr(targets, 'load_target', load)
        assert call('verify')['structuredContent']['runtime_verdict'] == 'PASS'
    assert not call('plan', plan=plan, required_lenses=all_lenses).get('isError')
    result = call('verify')['structuredContent']
    assert result['runtime_verdict'] == 'PENDING'
    assert result['verification_targets']['missing_lenses'] == ['review-security']
