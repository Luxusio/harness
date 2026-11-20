"""Standing consent is private, explicit, and scoped to an exact inbox."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "plugin/scripts"))
from friction_consent import status
from wiki_platform import atomic_write, ensure_private_directory


def test_destination_bound_consent(tmp_path):
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    folder = ensure_private_directory(home / ".config/harness")
    page = "https://feedback.example.invalid/wiki/spaces/TEAM/pages/123"
    destination = {"backend": "atlassian", "page": page}
    atomic_write(folder / "feedback.yaml", json.dumps(destination).encode())
    assert status(home) == {"authorized": False}
    consent = folder / "friction-consent.yaml"
    for changes in ({"auto_report_friction": False}, {"auto_report_friction": "true"},
                    {"page": page + "4"}, {"page": page.replace("feedback.", "other.")},
                    {"unrecognized": True}):
        value = {**destination, "auto_report_friction": True, **changes}
        atomic_write(consent, json.dumps(value).encode())
        assert status(home) == {"authorized": False}
    atomic_write(consent, json.dumps({**destination, "auto_report_friction": True}).encode())
    approved = status(home)
    assert approved["authorized"] is True
    assert approved["page_id"] == "123"
    consent.unlink()
    assert status(home) == {"authorized": False}
