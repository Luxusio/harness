"""Compact summaries project owner facts without inventing evidence."""
import copy

from test_wbs import wbs
from test_wbs_input_policy import public


def test_summary_keeps_tiers_and_stored_outcomes_distinct():
    context = dict(task_id="TASK__sample", status="open", review_verdict="PASS",
                   runtime_verdict="PASS", verification_targets=dict(status="unmeasured"),
                   progress=dict(status="unmeasured", integration=dict(route="integration", proof_fresh=False),
                                 outcomes=[dict(id="A", title="Real outcome", verified=False, historical_verified=True)]))
    original = copy.deepcopy(context)
    result = wbs.summary(context)
    assert context == original
    assert result["advisory"] is True and result["task_id"] == "TASK__sample"
    assert result["evidence"] == dict(implemented="unknown", integrated="stored",
                                      software_verified="stored", actual_environment="unknown", complete="pending")
    assert result["outcomes"] == [dict(id="A", title="Real outcome", evidence="stored")]
    context.update(status="closed", verification_targets=dict(status="applicable"))
    context["progress"]["status"] = "measured"
    context["progress"]["integration"]["proof_fresh"] = True
    result = wbs.summary(context)
    assert result["evidence"]["software_verified"] == "current"
    assert result["evidence"]["integrated"] == "current"
    assert result["evidence"]["complete"] == "stored"
    assert result["evidence"]["actual_environment"] == "unknown"


def test_summary_never_promotes_fail_blocked_or_missing_facts():
    assert wbs.summary({})["task_id"] is None
    assert wbs.summary({})["outcomes"] == []
    for verdict, expected in [("FAIL", "failed"), ("BLOCKED_ENV", "blocked"), ("PENDING", "pending")]:
        result = wbs.summary(dict(review_verdict="PASS", runtime_verdict=verdict))
        assert result["evidence"]["software_verified"] == expected
        assert result["evidence"]["complete"] == "pending"


def test_public_context_exposes_summary_without_new_measurement(tmp_path, monkeypatch):
    _, _, call, _ = public(tmp_path)
    monkeypatch.setattr(wbs, "fingerprint", lambda *a, **k: (_ for _ in ()).throw(AssertionError("query hashed")))
    result = call("context")["structuredContent"]["task_context"]
    assert result["summary"] == wbs.summary(result)
    assert result["summary"]["outcomes"][0]["id"] == "A"
    assert result["summary"]["measurement"] == "unmeasured"
