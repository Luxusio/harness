from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SETUP_SKILL = REPO / "plugin" / "skills" / "setup" / "SKILL.md"
CODEX_SETUP_SKILL = REPO / "plugin-codex" / "skills" / "setup" / "SKILL.md"
BOOTSTRAP = REPO / "plugin" / "skills" / "setup" / "bootstrap.md"
VERIFY_REPORT = REPO / "plugin" / "skills" / "setup" / "verify-report.md"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_setup_bootstrap_does_not_write_project_mcp_json():
    body = _text(BOOTSTRAP)

    forbidden = [
        "cat > .mcp.json",
        "with open('.mcp.json', 'w')",
        'with open(".mcp.json", "w")',
        "Chrome DevTools MCP added to .mcp.json",
        "x11-mcp placeholder added to .mcp.json",
        "FAILED: could not update .mcp.json",
    ]
    for phrase in forbidden:
        assert phrase not in body

    assert "Treat project-root `.mcp.json` as" in body
    assert "user-owned configuration" in body


def test_setup_questions_do_not_recommend_project_mcp_json_edits():
    body = _text(SETUP_SKILL) + "\n" + _text(CODEX_SETUP_SKILL)

    forbidden = [
        "Add to .mcp.json",
        "Add Chrome DevTools MCP to .mcp.json",
        "Add placeholder to .mcp.json",
        "Append to .mcp.json",
    ]
    for phrase in forbidden:
        assert phrase not in body

    assert "Already configured globally/session tools available" in body
    assert "preserve project `.mcp.json` as user-owned configuration" in body


def test_codex_setup_explicitly_uses_runtime_config_instead_of_project_mcp_json():
    body = _text(CODEX_SETUP_SKILL)

    assert "Setup reads MCP availability from current session tools" in body
    assert "current session tools or Codex/global runtime config" in body
    assert "project setup preserves project-root `.mcp.json` as user-owned configuration" in body


def test_setup_verify_report_points_to_global_runtime_mcp_config():
    body = _text(VERIFY_REPORT)

    assert "Setup reads MCP availability from global/runtime settings" in body
    assert "project-root .mcp.json as user-owned configuration" in body
    assert "global/runtime MCP settings" in body
    assert "Re-run setup and select" not in body
    assert ".mcp.json — Chrome DevTools MCP configured" not in body


def test_shared_setup_files_discover_codex_install_without_shell_env_injection():
    bootstrap = _text(BOOTSTRAP)
    verify = _text(VERIFY_REPORT)
    codex = _text(CODEX_SETUP_SKILL)

    for body in (bootstrap, verify):
        assert '$HOME/.codex/harness/plugins/harness' in body
        assert '$_PLUGIN_ROOT/.codex-plugin/plugin.json' in body
        assert '_PROJECT_DOC="AGENTS.md"' in body
    assert "does not inject plugin-root variables into ordinary shell commands" in codex
    assert "The harness env wires `${HARNESS_PLUGIN_ROOT}`" not in codex
