"""AC-003 (CLI half): req_scaffold.py --status writes REQ frontmatter status line.

Calls the library function directly to keep the test deterministic and stdlib-only.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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


def test_render_req_doc_status_default_accepted(tmp_path):
    mod = _load_req_scaffold()
    body = mod.render_req_doc(
        title="Sample",
        intent="Default check.",
        observable_behaviors="- default behavior",
        verification_cues="- check default",
    )
    assert "status: accepted" in body, body[:400]


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


def test_create_req_doc_status_default_accepted(tmp_path):
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
    assert "status: accepted" in written
