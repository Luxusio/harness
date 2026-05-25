from pathlib import Path

import sys


REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "plugin" / "scripts"))

from req_detector import detect_req_need  # type: ignore
from req_scaffold import write_req_doc  # type: ignore
from conftest import SCRIPTS_DIR, invoke_hook, parse_decision, scratch_task_in_real_repo  # type: ignore


def test_req_detector_flags_mobile_reader_back_stack_feedback():
    result = detect_req_need(
        texts=[
            "Browser mobile verification was done, but native back-stack "
            "behavior must be verified on Android APK/emulator for the reader."
        ],
        paths=[],
    )

    assert result["requires_req"] is True
    assert result["confidence"] == "high"
    assert "mobile-native" in result["surfaces"]
    assert result["suggested_area"] == "ui"


def test_req_scaffold_writes_observable_behavior_and_verification_cues(tmp_path):
    rel = write_req_doc(
        str(tmp_path),
        "ui",
        "mobile-reader-navigation",
        "Mobile reader navigation should preserve native back-stack expectations.",
        "Android back returns to the previous reader screen instead of exiting unexpectedly.",
        "Verify Android APK or emulator behavior separately from browser mobile.",
        "No full reader redesign.",
        "task: TASK__mobile-reader-polish-and-navigation-fixes",
    )

    path = tmp_path / rel
    body = path.read_text(encoding="utf-8")
    assert rel == "doc/ui/REQ__mobile-reader-navigation.md"
    assert "## Observable Behavior" in body
    assert "Android back returns" in body
    assert "## Verification Cues" in body
    assert "Android APK or emulator" in body


def test_req_detector_does_not_flag_internal_refactor_path():
    result = detect_req_need(texts=["Refactor internal helper implementation."], paths=["plugin/scripts/_lib.py"])

    assert result["requires_req"] is False


def test_prewrite_denies_observable_source_edit_without_req_link():
    with scratch_task_in_real_repo("req-prewrite-no-link") as task_dir:
        plan = Path(task_dir) / "PLAN.md"
        plan.write_text("# PLAN\n\n## Durable Docs Decision\nREQ: n/a\n", encoding="utf-8")
        target = REPO / "src/mobile/Reader.tsx"
        result = invoke_hook(
            str(Path(SCRIPTS_DIR) / "prewrite_gate.py"),
            "Write",
            {"file_path": str(target)},
        )

    decision, reason = parse_decision(result.stdout)
    assert decision == "deny"
    assert "C-REQ-observable-doc-required" in (reason or "")
    assert "write_req_doc" in (reason or "")
