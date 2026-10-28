"""AC-003 (CLI half): req_scaffold.py --status writes REQ frontmatter status line.

Calls the library function directly to keep the test deterministic and stdlib-only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import json
import subprocess


REPO = Path(__file__).resolve().parents[1]
SCAFFOLD = REPO / "plugin" / "scripts" / "req_scaffold.py"


def _load_req_scaffold():
    """Import req_scaffold under a hermetic module name."""
    scripts_dir = str(REPO / "plugin" / "scripts")
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location("req_scaffold_test", SCAFFOLD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_render_req_doc_status_candidate(tmp_path):
    mod = _load_req_scaffold()
    body = mod.render_req_doc(
        title="Sample",
        intent="Test status field.",
        observable_behaviors="- file has status line",
        verification_cues="- assert frontmatter",
        status="candidate",
    )
    assert "status: candidate" in body, body[:400]
    # Status line must precede the Intent section
    assert body.index("status: candidate") < body.index("## Intent")


def test_render_req_doc_status_default_candidate(tmp_path):
    mod = _load_req_scaffold()
    body = mod.render_req_doc(
        title="Sample",
        intent="Default check.",
        observable_behaviors="- default behavior",
        verification_cues="- check default",
    )
    assert "status: candidate" in body, body[:400]


def test_create_req_doc_status_lands_on_disk(tmp_path):
    mod = _load_req_scaffold()
    repo = tmp_path / "repo"
    repo.mkdir()
    rel = mod.create_req_doc(
        repo_root=str(repo),
        area="common",
        slug="status-disk",
        intent="Write check.",
        observable_behaviors="- file exists",
        verification_cues="- read it",
        status="candidate",
    )
    written = (repo / rel).read_text(encoding="utf-8")
    assert "status: candidate" in written


def test_create_req_doc_status_default_candidate(tmp_path):
    mod = _load_req_scaffold()
    repo = tmp_path / "repo"
    repo.mkdir()
    rel = mod.create_req_doc(
        repo_root=str(repo),
        area="common",
        slug="status-default",
        intent="Default disk check.",
        observable_behaviors="- file exists",
        verification_cues="- read it",
    )
    written = (repo / rel).read_text(encoding="utf-8")
    assert "status: candidate" in written


def claim(origin="user_explicit", **extra):
    return dict(text="Show measurable WBS outcomes", origin=origin,
                source="conversation:user-turn-1", evidence="WBS기반으로 싹 바꿔줘", **extra)


def render(*, status="candidate", requirements=None, behavior=""):
    return _load_req_scaffold().render_req_doc("Outcomes", "Writer interpretation", behavior,
                                             "Check actual outcomes", status=status, requirements=requirements)


def test_blank_status_and_legacy_text_never_invent_user_acceptance():
    import pytest
    body = render(status="", behavior="Assumed extra behavior")
    assert "status: candidate" in body
    assert "unattributed" in body and "candidate; non-binding" in body
    assert "None accepted by this scaffold" in body
    with pytest.raises(ValueError, match="per-item source evidence"):
        render(status="accepted", behavior="Unattributed behavior")


def test_non_user_claims_cannot_be_promoted_by_setting_accepted(origin):
    import pytest
    with pytest.raises(ValueError, match="cannot become accepted"):
        render(status="accepted", requirements=[claim(origin)])


def test_source_label_without_relevant_evidence_is_not_grounding(field):
    import pytest
    row = claim()
    row[field] = "  "
    with pytest.raises(ValueError, match="source and relevant"):
        render(status="accepted", requirements=[row])


def test_confirmation_names_what_was_approved_not_every_assumption():
    import pytest
    with pytest.raises(ValueError, match="particular proposal"):
        render(status="accepted", requirements=[claim("user_confirmed")])
    body = render(status="accepted", requirements=[claim("user_confirmed", confirmed_scope="Use one WBS, as proposed in turn 2")])
    assert "Confirmed proposal (writer interpretation)" in body
    assert "| WBS기반으로 싹 바꿔줘 | Use one WBS, as proposed in turn 2 | accepted |" in body


def test_derived_constraints_stay_separate_from_user_requests():
    explicit = claim()
    derived = dict(text="Preserve receipt-backed close", origin="derived", source="CONTRACTS.md:C-04",
                   evidence="Existing close safety must remain valid when replacing the public interface")
    body = render(status="accepted", requirements=[explicit, derived])
    users, constraints = body.split("### Derived constraints", 1)
    assert explicit["evidence"] in users
    assert derived["text"] not in users
    assert "implementation-only; not an acceptance requirement" in constraints
    assert "accepted (derived)" not in body and "CONTRACTS.md:C-04" in constraints
    assert "Only accepted user-sourced rows are binding" in body


def test_accepted_document_can_keep_candidates_without_accepting_them():
    body = render(status="accepted", requirements=[claim(), claim("assumption"), claim("proposal"), claim("unattributed")])
    users, candidates = body.split("### Assumptions / proposals", 1)
    assert users.count("| accepted |") == 1
    assert candidates.count("candidate; non-binding") == 3
    assert "| accepted |" not in candidates


def test_derived_only_document_cannot_claim_accepted_requirements():
    import pytest
    with pytest.raises(ValueError, match="at least one grounded user claim"):
        render(status="accepted", requirements=[claim("derived")])


def test_missing_grounding_stays_unattributed_and_nonbinding(origin, field):
    row = claim(origin)
    row[field] = " "
    body = render(requirements=[row])
    assert "unattributed" in body and "candidate; non-binding" in body
    assert "| " + origin + " |" not in body
    assert "implementation-only" not in body
    if origin == "derived":
        accepted = render(status="accepted", requirements=[claim(), row])
        assert accepted.count("| accepted |") == 1
        assert "unattributed" in accepted and "candidate; non-binding" in accepted


def test_source_quote_and_authored_interpretation_are_separate_escaped_cells():
    row = dict(text="Claim|interpretation\nextra", origin="user_confirmed", source="turn|2\nlocation",
               evidence="yes|go\non", confirmed_scope="Proposal|A\nonly")
    body = render(status="accepted", requirements=[row])
    assert "| Source | Exact quote / derivation basis | Confirmed proposal (writer interpretation) |" in body
    assert "| Claim\\|interpretation<br>extra | user_confirmed | turn\\|2<br>location | yes\\|go<br>on | Proposal\\|A<br>only | accepted |" in body


def test_mixed_candidate_claims_do_not_become_acceptance_criteria():
    import pytest
    body = render(requirements=[claim(), claim("assumption"), claim("proposal")])
    assert "### User-sourced claims" in body
    assert "### Assumptions / proposals / unattributed claims (non-binding)" in body
    assert body.count("candidate; non-binding") == 3
    assert "None accepted by this scaffold" in body
    with pytest.raises(ValueError, match="competing claim lists"):
        render(status="accepted", requirements=[claim()], behavior="Unclassified extra demand")


def test_candidate_append_does_not_inherit_existing_acceptance(tmp_path):
    mod = _load_req_scaffold()
    args = (str(tmp_path), "harness", "source", "Interpretation", "", "Check outcomes")
    rel = mod.create_req_doc(*args, status="accepted", requirements=[claim()])
    mod.create_req_doc(str(tmp_path), "harness", "source", "New interpretation", "Invented extra constraint", "Proposed check")
    body = (tmp_path / rel).read_text()
    old, new = body.split("## Candidate Update", 1)
    assert "status: accepted" in old
    assert "earlier acceptance does not apply" in new
    assert "status: candidate" in new and "unattributed" in new
    assert "None accepted by this scaffold" in new


def test_invalid_accepted_write_preserves_existing_document(tmp_path):
    import pytest
    mod = _load_req_scaffold()
    args = (str(tmp_path), "harness", "source", "Interpretation", "", "Check outcomes")
    rel = mod.create_req_doc(*args, status="accepted", requirements=[claim()])
    original = (tmp_path / rel).read_bytes()
    with pytest.raises(ValueError):
        mod.create_req_doc(str(tmp_path), "harness", "source", "Changed", "Invented behavior", "Check", status="accepted")
    assert (tmp_path / rel).read_bytes() == original
    with pytest.raises(ValueError, match="at least one grounded user claim"):
        mod.create_req_doc(*args, status="accepted", requirements=[claim("derived")])
    assert (tmp_path / rel).read_bytes() == original


def test_accepted_append_only_accepts_its_user_rows(tmp_path):
    mod = _load_req_scaffold()
    args = (str(tmp_path), "harness", "source", "Interpretation", "", "Check outcomes")
    rel = mod.create_req_doc(*args, status="accepted", requirements=[claim()])
    mod.create_req_doc(*args, status="accepted", requirements=[claim(), claim("derived"), claim("proposal")])
    old, new = (tmp_path / rel).read_text().split("## Sourced Accepted Update", 1)
    assert old.count("| accepted |") == new.count("| accepted |") == 1
    assert "implementation-only; not an acceptance requirement" in new
    assert "candidate; non-binding" in new


def test_cli_requires_classification_before_accepted_publication(tmp_path):
    common = [sys.executable, str(SCAFFOLD), "--repo-root", str(tmp_path), "--area", "harness", "--slug", "source",
              "--intent", "Interpretation", "--verification-cues", "Check", "--status", "accepted"]
    result = subprocess.run(common + ["--observable-behavior", "Invented behavior"], capture_output=True, text=True)
    assert result.returncode == 2 and not (tmp_path / "doc").exists()
    source = tmp_path / "requirements.json"
    source.write_text(json.dumps([claim(), claim("derived"), claim("proposal")]))
    result = subprocess.run(common + ["--requirements-file", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    body = (tmp_path / result.stdout.strip()).read_text()
    assert "status: accepted" in body and "user_explicit" in body
    assert body.count("| accepted |") == 1
    assert "implementation-only; not an acceptance requirement" in body
    assert "candidate; non-binding" in body


def test_both_runtime_reviewers_check_source_entailment_not_only_labels():
    for runtime in ("plugin", "plugin-codex"):
        body = (REPO / runtime / "agents/critic-document.md").read_text()
        assert "## Conversation requirement pass" in body
        assert "compare its source with the actual" in body
        assert "authentic quote does not prove" in body
        assert "grounding is unverified" in body
        assert "candidate assumptions promoted into mandatory WBS/acceptance/close gates" in body
        assert "An accepted document does not accept derived rows or candidate rows" in body
        assert "Supported derived work may be implemented, tested and verified" in body
        assert "cannot authorize a new product outcome, service, cost" in body


def test_planning_keeps_derived_implementation_autonomous_without_expanding_scope():
    for path in ("write-artifacts.md", "review-phases.md"):
        body = (REPO / "plugin/skills/plan" / path).read_text()
        assert "Only user_explicit/scoped user_confirmed rows" in body
        assert "standing constraint" in body
        assert "autonomously" in body and "user-facing constraint" in body
        assert "development discoveries" in body


def pytest_generate_tests(metafunc):
    cases = {'test_non_user_claims_cannot_be_promoted_by_setting_accepted': [('origin',
                                                                      ['assumption',
                                                                       'proposal',
                                                                       'unattributed'])],
     'test_source_label_without_relevant_evidence_is_not_grounding': [('field',
                                                                       ['source', 'evidence'])],
     'test_missing_grounding_stays_unattributed_and_nonbinding': [
         ('origin', ['user_explicit', 'derived']), ('field', ['source', 'evidence'])]}
    for argument, values in cases.get(metafunc.function.__name__, []):
        metafunc.parametrize(argument, values)


def test_setup_defaults_cannot_seed_accepted_user_goals():
    body = (REPO / "plugin/skills/setup/project-interview.md").read_text()
    assert '"source": "setup_default"' in body
    assert "seed with Q1 + Q2" not in body
    assert "never as user-selected intent" in body
    assert "never seed goals or accepted user requirements from them" in body
    assert "actual Q1, the request or user-authored requirements with their provenance" in body
    assert "If actual intent is absent or skipped, do not invent a project goal" in body
