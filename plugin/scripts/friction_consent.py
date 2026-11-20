"""Check the personal, destination-bound opt-in for automatic friction reports."""
import json

from document_destination import _yaml_mapping
from personal_feedback import FeedbackError, _destination, _read_private, load_destination


def status(home=None):
    denied = {"authorized": False}
    current = load_destination(home)
    if current["status"] != "configured":
        return denied
    try:
        raw = _read_private("friction-consent.yaml", home)
        consent = _yaml_mapping(raw.decode("utf-8"))
        if consent.pop("auto_report_friction", None) is not True:
            return denied
        approved = _destination(json.dumps(consent).encode("utf-8"))
    except (OSError, ValueError, TypeError, AttributeError, ImportError, RecursionError, FeedbackError):
        return denied
    if any(current[key] != approved[key] for key in ("backend", "site", "page_id")):
        return denied
    return {"authorized": True, **approved}


if __name__ == "__main__":
    print(json.dumps(status()))
