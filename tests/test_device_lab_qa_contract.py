"""Instruction-contract regressions; these do not claim live platform coverage."""

from pathlib import Path
import re

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
ROLES = [
    f"{runtime}/agents/{role}.md"
    for runtime in ("plugin", "plugin-codex")
    for role in ("qa-desktop", "ux-desktop")
]
SETUP = [
    "plugin/skills/setup/SKILL.md",
    "plugin-codex/skills/setup/SKILL.md",
    "plugin/skills/setup/bootstrap.md",
]


def text(path):
    return (ROOT / path).read_text(encoding="utf-8")


def normalized(path):
    return " ".join(text(path).split())


@pytest.mark.parametrize("path", ROLES)
def test_discovery_separates_availability_ownership_and_selected_readiness(path):
    body = normalized(path)
    for term in (
        "Device Lab", "`view: backends`", "`view: owned`", "`view: available`",
        "actual returned owner-scoped `deviceId`", "`status(deviceId)`",
        "readiness and effective capabilities", "on every device action",
        "not proof of an owned or running target",
        "Do not gate remote targets on local OS or `$DISPLAY`",
        "Missing unrelated platforms do not block",
    ):
        assert term in body, (path, term)


@pytest.mark.parametrize("path", ROLES)
def test_backend_operation_boundaries_prevent_universal_desktop_assumptions(path):
    body = normalized(path)
    for term in (
        "`window_list`", "`focus_window`", "opaque handle unchanged",
        "do not support mobile or container-QEMU", "`timeoutMs >= 30000`",
        "Remote `exec` requires backend support", "X11 has no `exec` or `ui`",
        "matching current X11 display", "Do not assume `launch_app` supports X11",
        "Android package or iOS bundle `appId`", "`back` is Android-only",
        "`wait_for_app` and `wait_for_text` are mobile-only",
        "not UI readiness", "`matched:false` is unmet",
        "no universal resize, evaluate, or wait tool",
        "UI hierarchy is optional when unsupported",
        "full returned screenshot dimensions", "input coordinates remain relative",
    ):
        assert term in body, (path, term)


@pytest.mark.parametrize("path", ROLES)
def test_real_gui_evidence_cannot_be_replaced_by_lower_tier_pass(path):
    body = normalized(path)
    for term in (
        "deviceId", "backend", "platform", "window or appId",
        "screenshot", "action", "observed", "Inspect returned screenshot images",
        "Lower-tier PASS covers non-GUI checks only",
        "Required unexercised GUI ACs remain `BLOCKED_ENV`",
    ):
        assert term in body, (path, term)
    assert "A PASS below `interactive-desktop` must say" not in body


@pytest.mark.parametrize("path", ROLES)
def test_device_authority_privacy_and_cleanup_survive_migration(path):
    body = normalized(path)
    for term in (
        "without actual authority", "owner-scoped devices", "paid resources",
        "clean up only task-created resources", "preserve pre-existing",
        "Do not stop or delete a shared existing device",
        "Inspect before attaching", "screenshot redacted — contained sensitive UI",
        "Minimize sensitive inventory/diagnostic output",
    ):
        assert term in body, (path, term)


@pytest.mark.parametrize("path", SETUP)
def test_setup_uses_device_lab_without_changing_manifest_or_project_config(path):
    body = normalized(path)
    for term in (
        "Device Lab", "`view: backends`", "`view: owned`", "`view: available`",
        "`deviceId`", "`status`", "`desktop_qa_supported`",
        "`app_launch_command`", "`display_command`", "`BLOCKED_ENV`",
    ):
        assert term in body, (path, term)
    for obsolete in ("uname -s", "command -v Xvfb", "sudo -n apt-get", "Linux-only"):
        # Other setup flows may inspect the local OS; only native section applies.
        start = body.index("Device Lab")
        assert obsolete not in body[start:], (path, obsolete)


@pytest.mark.parametrize("role", ["qa-desktop", "ux-desktop"])
def test_claude_tool_allowlist_reaches_actual_device_lab_actions(role):
    frontmatter = text(f"plugin/agents/{role}.md").split("---", 2)[1]
    tools = yaml.safe_load(frontmatter)["tools"].split(", ")
    for action in ("devices", "status", "window_list", "focus_window", "screenshot",
                   "click", "type", "key", "exec", "ui", "launch_app",
                   "wait_for_app", "wait_for_text", "back", "swipe", "long_press", "home"):
        assert f"mcp__device_lab__{action}" in tools
    assert not any("mcp__x11" in tool for tool in tools)
    assert not {"Write", "Edit", "MultiEdit"}.intersection(tools)


@pytest.mark.parametrize("path", SETUP[:2])
def test_native_setup_migration_preserves_other_project_qa_routes(path):
    body = text(path)
    for route in ("**API project:**", "**CLI/library:**", "**Fullstack (frontend + API):**"):
        assert route in body


@pytest.mark.parametrize("path", ROLES + SETUP + [
    "README.md", "doc/harness/GUIDE__how-harness-works.md", "doc/harness/PROVENANCE.md",
])
def test_active_native_docs_do_not_restore_obsolete_x11_contract(path):
    body = text(path)
    for obsolete in (
        "mcp__x11__", "Linux-only in v1", "v1 is Linux-only", "Linux 전용",
        "sudo -n apt-get", "sudo apt-get install -y xvfb",
        "drives an x11-mcp server", "Native GUI runtime QA via X11 tooling",
        "with a `.mcp.json` fix block",
    ):
        assert obsolete not in body, (path, obsolete)
    assert "Device Lab" in body


def test_qa_verdict_identity_and_requirement_provenance_are_preserved():
    for runtime in ("plugin", "plugin-codex"):
        body = text(f"{runtime}/agents/qa-desktop.md")
        assert "first line of the final response must be exactly" in body
        for verdict in ("PASS", "FAIL", "BLOCKED_ENV"):
            assert f"VERDICT: {verdict}" in body
        assert "different verdict voids your verdict entirely" in body
        assert yaml.safe_load(body.split("---", 2)[1])["name"] == "qa-desktop"
    req = text("doc/harness/REQ__device-lab-native-qa.md")
    assert "user_explicit" in req
    assert "qa-desktop도 device-lab mcp기준으로 싹 바꿔줘" in req
    assert "Derived execution and evidence contract" in req
    assert re.search(r"not additional quoted user requirements", req)
