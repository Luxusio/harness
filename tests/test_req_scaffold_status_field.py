"""AC-003 (CLI half): req_scaffold.py --status writes REQ frontmatter status line.

Calls the library function directly to keep the test deterministic and stdlib-only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
import json
import subprocess

import pytest


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
    body = render(status="", behavior="Assumed extra behavior")
    assert "status: candidate" in body
    assert "unattributed" in body and "candidate; non-binding" in body
    assert "None accepted by this scaffold" in body
    with pytest.raises(ValueError, match="per-item source evidence"):
        render(status="accepted", behavior="Unattributed behavior")


@pytest.mark.parametrize("origin", ["assumption", "proposal", "unattributed"])
def test_non_user_claims_cannot_be_promoted_by_setting_accepted(origin):
    with pytest.raises(ValueError, match="cannot become accepted"):
        render(status="accepted", requirements=[claim(origin)])


@pytest.mark.parametrize("field", ["source", "evidence"])
def test_source_label_without_relevant_evidence_is_not_grounding(field):
    row = claim()
    row[field] = "  "
    with pytest.raises(ValueError, match="source and relevant"):
        render(status="accepted", requirements=[row])


def test_confirmation_names_what_was_approved_not_every_assumption():
    with pytest.raises(ValueError, match="particular proposal"):
        render(status="accepted", requirements=[claim("user_confirmed")])
    body = render(status="accepted", requirements=[claim("user_confirmed", confirmed_scope="Use one WBS, as proposed in turn 2")])
    assert "confirmed scope: Use one WBS" in body


def test_derived_constraints_stay_separate_from_user_requests():
    explicit = claim()
    derived = dict(text="Preserve receipt-backed close", origin="derived", source="CONTRACTS.md:C-04",
                   evidence="Existing close safety must remain valid when replacing the public interface")
    body = render(status="accepted", requirements=[explicit, derived])
    users, constraints = body.split("### Derived constraints", 1)
    assert explicit["evidence"] in users
    assert derived["text"] not in users
    assert "accepted (derived)" in constraints and "CONTRACTS.md:C-04" in constraints


def test_mixed_candidate_claims_do_not_become_acceptance_criteria():
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
    mod = _load_req_scaffold()
    args = (str(tmp_path), "harness", "source", "Interpretation", "", "Check outcomes")
    rel = mod.create_req_doc(*args, status="accepted", requirements=[claim()])
    original = (tmp_path / rel).read_bytes()
    with pytest.raises(ValueError):
        mod.create_req_doc(str(tmp_path), "harness", "source", "Changed", "Invented behavior", "Check", status="accepted")
    assert (tmp_path / rel).read_bytes() == original


def test_cli_requires_classification_before_accepted_publication(tmp_path):
    common = [sys.executable, str(SCAFFOLD), "--repo-root", str(tmp_path), "--area", "harness", "--slug", "source",
              "--intent", "Interpretation", "--verification-cues", "Check", "--status", "accepted"]
    result = subprocess.run(common + ["--observable-behavior", "Invented behavior"], capture_output=True, text=True)
    assert result.returncode == 2 and not (tmp_path / "doc").exists()
    source = tmp_path / "requirements.json"
    source.write_text(json.dumps([claim()]))
    result = subprocess.run(common + ["--requirements-file", str(source)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    body = (tmp_path / result.stdout.strip()).read_text()
    assert "status: accepted" in body and "user_explicit" in body
