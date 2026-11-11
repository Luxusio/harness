"""Purpose-specific WBS execution, promotion and real prewrite boundaries."""
from __future__ import annotations

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

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin/scripts'))
import work_policy
import wbs
from test_wbs import node, plan
from test_wbs_input_policy import public, policy_plan


def work_plan(kind="document"):
    item = node()
    item["check"] = [sys.executable, "-c", "from pathlib import Path; assert Path('doc/common/note.md').read_text() == 'scope: documentation only\\n'"]
    return policy_plan([item], {"source": ["doc/common/note.md"]}) + "\n```wbs-work\n" + json.dumps({"kind": kind}) + "\n```\n"


def document_fixture(tmp_path, *, workflow=None):
    repo, td, call, opened = public(tmp_path)
    note = repo / "doc/common/note.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("scope: documentation only\n")
    if workflow:
        set_workflow(repo, workflow)
    result = call("plan", plan=work_plan())
    assert not result.get("isError"), result
    return repo, td, call, opened


def set_workflow(repo, settings):
    # JSON is a valid YAML mapping and preserves the actual production parser.
    (repo / "doc/harness/manifest.yaml").write_text(json.dumps({"version": 7, "type": "library", "workflow": settings}))


def test_unconfigured_policy_loads_without_optional_parser_or_git(tmp_path):
    repo = tmp_path / "repo"
    manifest = repo / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    manifest.write_text("version: 7\ntype: library\n")
    scripts = str(Path(work_policy.__file__).parent)
    code = "import sys; sys.path.insert(0,sys.argv[1]); import work_policy; p=work_policy.load(sys.argv[3],sys.argv[2],plan=''); assert p['kind']=='development'; assert p['install']=='changed'; assert 'yaml' not in sys.modules"
    result = subprocess.run([sys.executable, "-S", "-B", "-c", code, scripts, str(repo), str(repo / "unused-wbs")], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert not (repo / "unused-wbs").exists()


@pytest.mark.parametrize("body", [
    '{"kind":"document","kind":"development"}', '{"kind":"unknown"}',
    '{"kind":true}', '{"kind":"document","skip_qa":true}', '[]',
])
def test_invalid_work_declaration_is_not_a_lightweight_fallback(body):
    with pytest.raises((ValueError, TypeError)):
        work_policy.kind_from_plan("```wbs-work\n" + body + "\n```\n")


def test_multiple_work_fences_are_rejected():
    text = '```wbs-work\n{"kind":"document"}\n```\n'
    with pytest.raises(ValueError, match="one wbs-work"):
        work_policy.kind_from_plan(text + text)


def test_quoted_declaration_examples_do_not_change_purpose_outcomes_or_inputs():
    quoted = ('````markdown\n```wbs-work\n{"kind":"document"}\n```\n'
              '```wbs\n[{"id":"injected","title":"Example only","weight":1}]\n```\n'
              '```wbs-inputs\n{"source":["example.md"]}\n```\n````\n')
    assert work_policy.kind_from_plan(quoted) == 'development'
    assert wbs.from_plan(quoted) is None
    assert wbs.inputs_from_plan(quoted) is None
    assert work_policy.kind_from_plan(quoted + '```wbs-work\n{"kind":"plan"}\n```\n') == 'plan'


def test_unterminated_work_declaration_is_refused():
    with pytest.raises(ValueError, match='unterminated'):
        work_policy.kind_from_plan('```wbs-work\n{"kind":"document"}\n')


def test_document_check_does_not_wait_for_independent_unselected_inputs(tmp_path, monkeypatch):
    repo, td, call, _ = document_fixture(tmp_path)
    a, b = node('A'), node('B')
    a['inputs'] = {'source': ['doc/common/note.md']}
    text = policy_plan([a, b], None) + '\n```wbs-work\n{"kind":"document"}\n```\n'
    # Leave B's eventual input scope undecided; A can still be checked now.
    text = text.replace('\n```wbs-inputs\nnull\n```\n', '')
    assert not call('plan', plan=text).get('isError')
    original = wbs.fingerprint
    def scoped_fingerprint(repo, policy=None, **kwargs):
        assert policy is not None and 'source' in policy, 'unselected checkout was scanned'
        return original(repo, policy, **kwargs)
    monkeypatch.setattr(wbs, 'fingerprint', scoped_fingerprint)
    result = call('check', node_id='A')['structuredContent']
    assert result['passed'] is True, result
    assert result['progress']['percentage'] is None
    assert 'explicit wbs-inputs' in result['progress']['reason']
    assert wbs.read_state(str(td))['checks']['A']['passed']


@pytest.mark.parametrize("settings", [
    {"document_review": True}, {"verification": "skip"}, {"install": "always"},
    {"unknown": True}, {"document_paths": ["docs/"]}, {"document_paths": ["../docs"]},
    {"document_paths": ["/docs"]}, {"document_paths": ["docs", "docs"]},
    {"executable_paths": "docs/runtime.md"},
])
def test_bad_project_policy_refuses_before_plan_or_control_publication(tmp_path, settings):
    repo, td, call, _ = public(tmp_path)
    before = {name: (td / name).read_bytes() for name in ("PLAN.md", "WBS.control.json", "WBS.json")}
    set_workflow(repo, settings)
    result = call("plan", plan=work_plan())
    assert result.get("isError"), result
    assert before == {name: (td / name).read_bytes() for name in before}


@pytest.mark.parametrize("text", [
    'version: 7\nworkflow: {install: manual}\nworkflow: {install: changed}\n',
    'version: 7\nworkflow:\n  document_review: self\n  document_review: independent\n',
])
def test_duplicate_project_policy_keys_cannot_select_weaker_settings(tmp_path, text):
    repo, td, call, _ = public(tmp_path)
    (repo / "doc/harness/manifest.yaml").write_text(text)
    before = (td / "WBS.control.json").read_bytes()
    assert call("plan", plan=work_plan()).get("isError")
    assert (td / "WBS.control.json").read_bytes() == before


@pytest.mark.parametrize("path", [
    "docs/AGENTS.md", "docs/CLAUDE.md", "docs/CONTRACTS.md", "docs/SKILL.md",
    "docs/prompts/normal.md", "docs/skills/normal.md", "docs/config.yaml",
    "docs/build.sh", "plugin/README.md", "doc/harness/manifest.yaml",
    "docs/runtime.md", "../docs/normal.md",
    "docs/CMakeLists.txt", "docs/CMakeCache.txt", "docs/requirements.txt",
    "docs/requirements-dev.txt", "docs/constraints.txt",
])
def test_broad_document_roles_do_not_admit_runtime_or_config_paths(tmp_path, path):
    repo = tmp_path / "repo"
    manifest = repo / "doc/harness/manifest.yaml"
    manifest.parent.mkdir(parents=True)
    set_workflow(repo, {"document_paths": ["doc", "docs", "plugin"], "executable_paths": ["docs/runtime.md"]})
    policy = work_policy.load(repo / "unused-wbs", repo, plan='```wbs-work\n{"kind":"document"}\n```\n')
    assert work_policy.document_path(policy, "docs/normal.md")
    assert not work_policy.document_path(policy, path)
    with pytest.raises(ValueError, match="promote"):
        work_policy.require_write(policy, path)


def test_document_only_repository_can_select_root_without_weakening_instruction_boundaries(tmp_path):
    repo = tmp_path / 'repo'
    (repo / 'doc/harness').mkdir(parents=True)
    set_workflow(repo, {'document_paths': ['.']})
    policy = work_policy.load(repo / 'unused', repo, plan='```wbs-work\n{"kind":"document"}\n```\n')
    assert work_policy.document_path(policy, 'topic.md')
    for path in ('agents.md', 'CLAUDE.MD', '.git/config.md', '.ClAuDe/rules.md', 'topic.py', 'settings.yaml'):
        assert not work_policy.document_path(policy, path)


def test_executable_markdown_cannot_be_edited_as_ordinary_prose(tmp_path):
    repo, td, _, _ = document_fixture(tmp_path)
    (repo / 'doc/common/note.md').chmod(0o755)
    with pytest.raises(ValueError, match='executable.*development'):
        wbs.require_source_assignment(str(repo), 'sample', 'doc/common/note.md')


@pytest.mark.parametrize("kind,field", [("document", "document_verified"), ("plan", "planning_verified")])
def test_narrative_outcome_finishes_without_product_qa_or_fake_software_pass(tmp_path, kind, field):
    from _lib import emit_compact_context
    repo, td, call, opened = document_fixture(tmp_path)
    if kind != "document":
        assert not call("plan", plan=work_plan(kind)).get("isError")
    context = call("context")["structuredContent"]["context"]
    assert context["routing"]["work_kind"] == kind
    assert context["required_review_lenses"] == context["required_qa_lenses"] == []
    assert not wbs.read_state(str(td)).get("execution_admission")
    checked = call("check", node_id="A")
    assert not checked.get("isError") and checked["structuredContent"]["passed"], checked
    measured = emit_compact_context(str(td), measure_wbs=True)
    assert measured["summary"]["evidence"]["software_verified"] == "not_applicable"
    assert measured["summary"]["evidence"][field] == "current"
    assert not call("verify").get("isError")
    assert call("close")["structuredContent"]["closed"]
    assert json.loads((td / "WBS.control.json").read_text())["run_id"] == opened["run_id"]


def test_document_assertion_is_bound_to_actual_document_bytes(tmp_path):
    repo, td, call, _ = document_fixture(tmp_path)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    (repo / "doc/common/note.md").write_text("changed after the check\n")
    assert call("close").get("isError")
    assert not json.loads((td / "WBS.control.json").read_text())["closed"]


def test_document_measurement_requires_explicit_inputs_before_any_checkout_hash(tmp_path, monkeypatch):
    repo, td, call, opened = document_fixture(tmp_path)
    text = plan([node()]) + '\n```wbs-work\n{"kind":"document"}\n```\n'
    assert not call('plan', plan=text).get('isError')
    def forbidden(*args, **kwargs):
        pytest.fail('document checks must not implicitly hash the checkout')
    monkeypatch.setattr(wbs, 'fingerprint', forbidden)
    result = call('check', node_id='A')
    assert result.get('isError')
    progress = wbs.wbs_progress(str(td), str(repo), opened['run_id'], measure=True)
    assert progress['percentage'] is None and 'explicit wbs-inputs' in progress['reason']


def test_development_cannot_explicitly_remove_its_review_and_qa(tmp_path):
    repo, td, call, _ = public(tmp_path)
    before = {name: (td / name).read_bytes() for name in ("PLAN.md", "WBS.control.json", "WBS.json")}
    result = call("plan", plan=policy_plan([node()], {"source": ["src.txt"]}), required_lenses=[])
    assert result.get("isError"), result
    assert before == {name: (td / name).read_bytes() for name in before}


def test_independent_document_policy_requires_only_document_review(tmp_path):
    repo, td, call, _ = document_fixture(tmp_path, workflow={"document_review": "independent"})
    context = call("context")["structuredContent"]["context"]
    assert context["required_review_lenses"] == ["review-document"]
    assert context["required_qa_lenses"] == []
    before = (td / "WBS.control.json").read_bytes()
    assert call("plan", plan=work_plan(), required_lenses=[]).get("isError")
    assert (td / "WBS.control.json").read_bytes() == before


def test_promotion_keeps_run_and_history_but_not_document_completion_credit(tmp_path):
    repo, td, call, opened = document_fixture(tmp_path)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    prior = wbs.read_state(str(td))["checks"]["A"]
    result = call("plan", plan=work_plan("development"))
    assert not result.get("isError"), result
    state = wbs.read_state(str(td))
    assert state["checks"]["A"]["checked_at"] == prior["checked_at"]
    assert state["run_id"] == opened["run_id"]
    progress = wbs.wbs_progress(str(td), str(repo), opened["run_id"], measure=True)
    assert progress["percentage"] == 0
    context = call("context")["structuredContent"]["context"]
    assert context["required_review_lenses"] == ["review-code"]
    assert context["required_qa_lenses"] == ["qa-cli"]


def test_completed_development_cannot_be_relabelled_as_documents(tmp_path):
    repo, td, call, _ = public(tmp_path)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    before = {name: (td / name).read_bytes() for name in ("PLAN.md", "WBS.control.json", "WBS.json")}
    assert call("plan", plan=work_plan()).get("isError")
    assert before == {name: (td / name).read_bytes() for name in before}


def test_delivery_preference_does_not_invalidate_document_proof(tmp_path):
    repo, td, call, opened = document_fixture(tmp_path)
    assert call("check", node_id="A")["structuredContent"]["passed"]
    set_workflow(repo, {"install": "manual"})
    assert wbs.wbs_progress(str(td), str(repo), opened["run_id"], measure=True)["percentage"] == 100
    set_workflow(repo, {"install": "manual", "document_review": "independent"})
    assert wbs.wbs_progress(str(td), str(repo), opened["run_id"], measure=True)["percentage"] == 0


def test_target_policy_is_scoped_and_delivery_only_change_reuses_target(tmp_path):
    import verification_target as targets
    repo, td, call, _ = document_fixture(tmp_path, workflow={"document_review": "independent"})
    spec = {"inputs": {"source": ["doc/common/note.md"]}, "commands": [], "lenses": ["review-document"], "purpose": "acceptance"}
    issued = call("target", target=spec)
    assert not issued.get("isError"), issued
    first = issued["structuredContent"]["target"]["token"]
    set_workflow(repo, {"document_review": "independent", "install": "manual"})
    assert targets.applicability(str(td), str(repo), measure=True)["status"] == "applicable"
    assert call("target", target=spec)["structuredContent"]["target"]["token"] == first
    set_workflow(repo, {"document_review": "independent", "install": "manual", "executable_paths": ["docs/runtime.md"]})
    assert targets.applicability(str(td), str(repo), measure=True)["status"] == "stale"
    assert call("target", target=spec)["structuredContent"]["target"]["token"] != first


@pytest.mark.parametrize("path", ["doc/common/AGENTS.md", "doc/common/runtime.json", "src.txt"])
def test_actual_prewrite_rejects_execution_inside_lightweight_work(tmp_path, path):
    from conftest import invoke_hook, parse_decision
    from test_harness_mcp_server import _write_marker_fixture
    repo, td, _, _ = document_fixture(tmp_path)
    _write_marker_fixture(str(repo), str(td), "work-policy-native")
    gate = str(Path(work_policy.__file__).with_name("prewrite_gate.py"))
    result = invoke_hook(gate, "Write", {"file_path": str(repo / path)}, cwd=str(repo), env_extra={"HARNESS_SESSION_ID": "work-policy-native", "HARNESS_SKIP_PREWRITE": "0"})
    assert result.returncode == 0
    decision, reason = parse_decision(result.stdout)
    assert decision == "deny", (result.stdout, result.stderr)
    assert "development" in reason


def test_actual_prewrite_accepts_exact_bound_document_without_host_dispatch(tmp_path):
    from conftest import invoke_hook, parse_decision
    from test_harness_mcp_server import _write_marker_fixture
    repo, td, _, _ = document_fixture(tmp_path)
    _write_marker_fixture(str(repo), str(td), "work-policy-native")
    gate = str(Path(work_policy.__file__).with_name("prewrite_gate.py"))
    result = invoke_hook(gate, "Write", {"file_path": str(repo / "doc/common/note.md")}, cwd=str(repo), env_extra={"HARNESS_SESSION_ID": "work-policy-native", "HARNESS_SKIP_PREWRITE": "0"})
    assert result.returncode == 0 and parse_decision(result.stdout)[0] != "deny", (result.stdout, result.stderr)
    assert not wbs.read_state(str(td)).get("execution_admission")


@pytest.mark.parametrize('name', ['CMakeLists.txt', 'requirements.txt', 'constraints-dev.txt'])
@pytest.mark.parametrize('kind', ['document', 'plan'])
def test_actual_prewrite_requires_development_for_known_text_configuration(tmp_path, name, kind):
    from conftest import invoke_hook, parse_decision
    from test_harness_mcp_server import _write_marker_fixture
    repo, td, call, _ = document_fixture(tmp_path)
    if kind == 'plan':
        assert not call('plan', plan=work_plan(kind)).get('isError')
    _write_marker_fixture(str(repo), str(td), 'work-policy-native')
    gate = str(Path(work_policy.__file__).with_name('prewrite_gate.py'))
    result = invoke_hook(gate, 'Write', {'file_path': str(repo / 'docs' / name)}, cwd=str(repo),
                         env_extra={'HARNESS_SESSION_ID': 'work-policy-native', 'HARNESS_SKIP_PREWRITE': '0'})
    assert result.returncode == 0
    assert parse_decision(result.stdout)[0] == 'deny', (result.stdout, result.stderr)
    assert 'promote' in result.stdout


@pytest.mark.parametrize("binding", ["missing", "stale", "foreign"])
def test_document_write_does_not_borrow_missing_stale_or_foreign_focus(tmp_path, binding):
    from conftest import invoke_hook, parse_decision
    from test_harness_mcp_server import _write_marker_fixture
    repo, td, _, _ = document_fixture(tmp_path)
    (repo / "doc/harness/manifest.yaml").write_text("version: 7\ntype: library\ncapabilities:\n  strict_compliance_requires_delegation: true\n")
    sid = "work-policy-native"
    _write_marker_fixture(str(repo), str(td), sid)
    marker = repo / "doc/harness/wbs/.active_sessions" / (sid + ".json")
    if binding == "missing":
        marker.unlink()
    else:
        data = json.loads(marker.read_text())
        if binding == "stale":
            from _lib import new_uuid7
            data["run_id"] = new_uuid7()
        else:
            data["wbs_dir"] = str(tmp_path / "foreign/doc/harness/wbs/WBS__sample")
        marker.write_text(json.dumps(data))
    gate = str(Path(work_policy.__file__).with_name("prewrite_gate.py"))
    result = invoke_hook(gate, "Write", {"file_path": str(repo / "doc/common/note.md")}, cwd=str(repo), env_extra={"HARNESS_SESSION_ID": sid, "HARNESS_SKIP_PREWRITE": "0"})
    assert result.returncode == 0
    assert parse_decision(result.stdout)[0] == "deny", (result.stdout, result.stderr)


def test_source_base_unknown_is_not_silently_recaptured_on_replan(tmp_path, monkeypatch):
    repo, td, call, _ = public(tmp_path)
    assert wbs.read_state(str(td))["source_base"] is None
    def recapture(_):
        pytest.fail("an unknown recorded baseline must stay unknown within this run")
    monkeypatch.setattr(work_policy, "capture_source_base", recapture)
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assert wbs.read_state(str(td))["source_base"] is None
    historical = wbs.read_state(str(td))
    historical.pop("source_base")
    (td / "WBS.json").write_text(wbs.encode_state(historical))
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assert wbs.read_state(str(td)).get("source_base") is None


def test_new_run_captures_actual_commit_once_and_keeps_committed_work_in_range(tmp_path):
    repo, td, call, opened = public(tmp_path)
    def git(*args):
        return subprocess.check_output(["git", "-C", str(repo), *args], text=True).strip()
    git("config", "user.name", "Harness Fixture")
    git("config", "user.email", "fixture@example.invalid")
    git("add", "src.txt", ".gitignore", "doc/harness/manifest.yaml")
    git("commit", "-qm", "fixture baseline")
    initial = git("rev-parse", "HEAD")
    renewed = call("open", fresh_run=True)
    assert not renewed.get("isError"), renewed
    assert renewed["structuredContent"]["run_id"] != opened["run_id"]
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assert wbs.read_state(str(td))["source_base"] == initial
    (repo / "src.txt").write_text("committed implementation\n")
    git("add", "src.txt")
    git("commit", "-qm", "fixture implementation")
    assert git("rev-parse", "HEAD") != initial
    assert not call("plan", plan=policy_plan([node()], {"source": ["src.txt"]})).get("isError")
    assert wbs.read_state(str(td))["source_base"] == initial
    assert git("diff", "--name-only", initial, "HEAD") == "src.txt"


@pytest.mark.parametrize('setting,passed', [('document_review: independent', False), ('install: manual', True)])
def test_policy_change_during_real_assertion_only_invalidates_related_proof(tmp_path, setting, passed):
    repo, td, call, _ = document_fixture(tmp_path)
    item = node()
    script = ("from pathlib import Path; assert Path('doc/common/note.md').read_text().startswith('scope:'); "
              "Path('doc/harness/manifest.yaml').write_text(" + repr('version: 7\nworkflow:\n  ' + setting + '\n') + ")")
    item['check'] = [sys.executable, '-c', script]
    text = policy_plan([item], {'source': ['doc/common/note.md']}) + '\n```wbs-work\n{"kind":"document"}\n```\n'
    assert not call('plan', plan=text).get('isError')
    result = call('check', node_id='A')['structuredContent']
    assert result['passed'] is passed, result
    if not passed:
        assert 'policy changed' in result['error']
        assert not wbs.read_state(str(td))['checks']['A']['passed']


def test_admission_tracks_permission_policy_but_not_delivery_preference(tmp_path):
    from test_managed_wbs import host_snapshot
    repo, td, call, _ = document_fixture(tmp_path)
    prepared = call('dispatch', operation='prepare', ownership={'A': ['doc/common/note.md']}, host=host_snapshot())
    assert not prepared.get('isError'), prepared
    wbs.require_source_assignment(str(repo), 'sample', 'doc/common/note.md')
    set_workflow(repo, {'install': 'manual'})
    wbs.require_source_assignment(str(repo), 'sample', 'doc/common/note.md')
    set_workflow(repo, {'install': 'manual', 'executable_paths': ['docs/runtime.md']})
    with pytest.raises(ValueError, match='stale'):
        wbs.require_source_assignment(str(repo), 'sample', 'doc/common/note.md')


def test_promotion_cannot_reuse_lightweight_execution_admission(tmp_path):
    from test_managed_wbs import host_snapshot
    repo, td, call, opened = document_fixture(tmp_path)
    assert not call('dispatch', operation='prepare', ownership={'A': ['doc/common/note.md']}, host=host_snapshot()).get('isError')
    assert not call('plan', plan=work_plan('development')).get('isError')
    assert wbs.read_state(str(td))['run_id'] == opened['run_id']
    with pytest.raises(ValueError, match='stale'):
        wbs.require_source_assignment(str(repo), 'sample', 'doc/common/note.md')


def test_policy_drift_during_real_target_copy_does_not_publish_a_target(tmp_path, monkeypatch):
    import verification_target as targets
    repo, td, call, _ = document_fixture(tmp_path, workflow={'document_review': 'independent'})
    original = targets._manifest
    def racing(*args, **kwargs):
        result = original(*args, **kwargs)
        if kwargs.get('destination') is not None:
            set_workflow(repo, {'document_review': 'independent', 'executable_paths': ['docs/runtime.md']})
        return result
    before = (td / 'WBS.json').read_bytes()
    monkeypatch.setattr(targets, '_manifest', racing)
    spec = {'inputs': {'source': ['doc/common/note.md']}, 'commands': [], 'lenses': ['review-document']}
    result = call('target', target=spec)
    assert result.get('isError'), result
    assert (td / 'WBS.json').read_bytes() == before
