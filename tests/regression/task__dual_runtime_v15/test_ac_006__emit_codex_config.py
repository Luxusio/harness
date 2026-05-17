"""AC-006 regression: install.py produces valid Codex TOML.

Tests:
1. emit_codex_config() returns valid TOML parseable by tomllib
2. Required top-level keys present (plugins, mcp_servers)
3. Codex hooks stay plugin-local; install.py must not write global [hooks]
4. Install with existing mcp_servers.harness refuses without force
5. Install with force writes backup file
6. Fresh install writes the file directly
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_PY = REPO_ROOT / "install.py"
PLUGIN_ROOT = REPO_ROOT / "plugin"


def _load_install_module():
    spec = importlib.util.spec_from_file_location("harness_install_py_ac006", INSTALL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_dry_run_emits_valid_toml():
    module = _load_install_module()
    parsed = tomllib.loads(module.emit_codex_config(str(PLUGIN_ROOT)))
    assert parsed["plugins"]["harness@harness"]["enabled"] is True
    assert parsed["marketplaces"]["harness"]["source"] == str(module.CODEX_INSTALL_ROOT)
    assert "mcp_servers" in parsed
    assert "harness" in parsed["mcp_servers"]
    assert parsed["mcp_servers"]["harness"]["command"] == module._python_cmd()
    assert "hooks" not in parsed


def test_codex_config_does_not_install_global_hooks():
    module = _load_install_module()
    parsed = tomllib.loads(module.emit_codex_config(str(PLUGIN_ROOT)))
    assert set(parsed) == {"plugins", "marketplaces", "mcp_servers"}


def test_codex_plugin_manifest_points_at_plugin_local_hooks(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    plugin_root = module.sync_codex_payload(install_root)
    hooks_path = install_root / "plugins" / "harness" / "hooks.json"
    assert plugin_root == install_root / "plugin"
    assert hooks_path.is_file()
    manifest = module.json.loads(
        (install_root / "plugins" / "harness" / ".codex-plugin" / "plugin.json").read_text()
    )
    assert manifest["hooks"] == "./hooks.json"
    assert manifest["mcpServers"] == "./.mcp.json"
    hooks = module.json.loads(hooks_path.read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "UserPromptSubmit", "PostToolUse"}
    assert "./scripts/hook_session_start.sh" in hooks_path.read_text()
    wrapper = install_root / "plugins" / "harness" / "scripts" / "hook_session_start.sh"
    assert wrapper.is_file()
    assert str(plugin_root / "scripts" / "hook_session_start.py") in wrapper.read_text()
    mcp = module.json.loads((install_root / "plugins" / "harness" / ".mcp.json").read_text())
    assert mcp["mcpServers"]["harness"]["command"] == module._python_cmd()
    assert mcp["mcpServers"]["harness"]["args"] == [str(plugin_root / "mcp" / "harness_server.py")]
    assert mcp["mcpServers"]["harness"]["env"]["HARNESS_PLUGIN_ROOT"] == str(plugin_root)


def test_install_refuses_existing_without_force(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text('[mcp_servers.harness]\ncommand = "preexisting"\n')
    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg)
    assert result["ok"] is False
    assert "already present" in result["message"]


def test_install_force_merge_writes_backup(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model = "gpt-5.5"\n\n'
        '# harness v2.3.0 — emitted by install.py\n'
        '[mcp_servers.harness]\ncommand = "preexisting"\n'
        '\n# ccc-managed-mcp begin\n'
        '[mcp_servers.chrome-devtools]\ncommand = "mise"\n'
        '\n[marketplaces.harness]\n'
        'last_updated = "stale"\n'
        'source_type = "local"\n'
        'source = "/old"\n'
        '\n[projects."/project/example"]\n'
        'trust_level = "trusted"\n'
    )
    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)
    assert result["ok"] is True, result["message"]
    # Backup file exists
    backups = list(tmp_path.glob("config.toml.bak.*"))
    assert len(backups) == 1, f"expected exactly one backup, got {backups}"
    # New content has the harness snippet appended
    new_content = cfg.read_text()
    assert "harness v2.3.0" in new_content
    assert '[plugins."harness@harness"]' in new_content
    assert "[marketplaces.harness]" in new_content
    assert "[mcp_servers.harness]" in new_content
    assert "[hooks]" not in new_content
    assert 'command = "preexisting"' not in new_content
    assert "# ccc-managed-mcp begin" in new_content
    assert "[mcp_servers.chrome-devtools]" in new_content
    assert new_content.count("[marketplaces.harness]") == 1
    assert 'source = "/old"' not in new_content
    parsed = tomllib.loads(new_content)
    assert parsed["model"] == "gpt-5.5"


def test_force_merge_removes_orphan_harness_tables(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mcp_servers.harness]\n'
        'command = "orphan"\n'
        '\n# ccc-managed-mcp begin\n'
        '[mcp_servers.chrome-devtools]\n'
        'command = "mise"\n'
        '\n# ccc-managed-mcp end\n'
        '\n# harness v2.3.0 — emitted by install.py\n'
        '[plugins."harness@harness"]\n'
        'enabled = true\n'
        '\n[mcp_servers.harness]\n'
        'command = "duplicate"\n'
    )
    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)
    assert result["ok"] is True, result["message"]
    new_content = cfg.read_text()
    assert new_content.count("[mcp_servers.harness]") == 1
    assert 'command = "orphan"' not in new_content
    assert 'command = "duplicate"' not in new_content
    assert "[mcp_servers.chrome-devtools]" in new_content
    tomllib.loads(new_content)


def test_force_merge_removes_orphan_plugin_and_marketplace_tables(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model = "gpt-5.5"\n'
        '\n[plugins."harness@harness"]\n'
        'enabled = false\n'
        '\n[marketplaces.harness]\n'
        'source = "/stale/project/checkout"\n'
        'source_type = "local"\n'
        '\n[mcp_servers.harness]\n'
        'command = "python3"\n'
        'args = ["/stale/project/checkout/plugin/mcp/harness_server.py"]\n'
        '\n[projects."/project/example"]\n'
        'trust_level = "trusted"\n'
    )

    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)

    assert result["ok"] is True, result["message"]
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["model"] == "gpt-5.5"
    assert parsed["plugins"]["harness@harness"]["enabled"] is True
    assert parsed["marketplaces"]["harness"]["source"] == str(module.CODEX_INSTALL_ROOT)
    assert parsed["mcp_servers"]["harness"]["command"] == module._python_cmd()
    assert "/stale/project/checkout" not in cfg.read_text()
    assert '[projects."/project/example"]' in cfg.read_text()


def test_force_merge_is_idempotent_for_harness_owned_blocks(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"

    first = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg)
    second = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)

    assert first["ok"] is True, first["message"]
    assert second["ok"] is True, second["message"]
    content = cfg.read_text()
    assert content.count('[plugins."harness@harness"]') == 1
    assert content.count("[marketplaces.harness]") == 1
    assert content.count("[mcp_servers.harness]") == 1
    tomllib.loads(content)


def test_force_merge_keeps_unrelated_mcp_servers(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[mcp_servers.filesystem]\n'
        'command = "node"\n'
        'args = ["server.js"]\n'
        '\n[mcp_servers.harness]\n'
        'command = "stale"\n'
        '\n[mcp_servers.github]\n'
        'command = "gh-mcp"\n'
    )

    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)

    assert result["ok"] is True, result["message"]
    content = cfg.read_text()
    assert "[mcp_servers.filesystem]" in content
    assert "[mcp_servers.github]" in content
    assert content.count("[mcp_servers.harness]") == 1
    parsed = tomllib.loads(content)
    assert parsed["mcp_servers"]["filesystem"]["command"] == "node"
    assert parsed["mcp_servers"]["github"]["command"] == "gh-mcp"


def test_non_force_append_without_existing_harness_preserves_existing_config_and_backs_up(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'model = "gpt-5.5"\n'
        '\n[mcp_servers.github]\n'
        'command = "gh-mcp"\n'
    )

    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=False)

    assert result["ok"] is True, result["message"]
    backups = list(tmp_path.glob("config.toml.bak.*"))
    assert len(backups) == 1
    content = cfg.read_text()
    assert 'model = "gpt-5.5"' in content
    assert "[mcp_servers.github]" in content
    assert "[mcp_servers.harness]" in content
    parsed = tomllib.loads(content)
    assert parsed["mcp_servers"]["github"]["command"] == "gh-mcp"
    assert parsed["mcp_servers"]["harness"]["enabled"] is True


def test_force_merge_preserves_tui_sections_when_stripping_generated_block(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '# ───────────────────────────────────────────────────────────────────\n'
        '# harness v2.3.0 — emitted by install.py\n'
        '# Plugin root: /old\n'
        '# Generated: 2026-01-01T00:00:00Z\n'
        '# ───────────────────────────────────────────────────────────────────\n'
        '\n[plugins."harness@harness"]\n'
        'enabled = true\n'
        '\n[tui]\n'
        'notifications = true\n'
    )

    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)

    assert result["ok"] is True, result["message"]
    content = cfg.read_text()
    assert "[tui]" in content
    assert "notifications = true" in content
    assert content.count('[plugins."harness@harness"]') == 1
    tomllib.loads(content)


def test_force_merge_preserves_comments_around_unrelated_sections(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "# user comment\n"
        "[mcp_servers.github]\n"
        "# keep this comment\n"
        'command = "gh-mcp"\n'
        "\n[mcp_servers.harness]\n"
        'command = "old"\n'
    )

    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg, force=True)

    assert result["ok"] is True, result["message"]
    content = cfg.read_text()
    assert "# user comment" in content
    assert "# keep this comment" in content
    assert "[mcp_servers.github]" in content
    assert 'command = "old"' not in content
    tomllib.loads(content)


def test_emit_codex_config_has_no_deprecated_global_hook_tables():
    module = _load_install_module()
    snippet = module.emit_codex_config(str(PLUGIN_ROOT))

    assert "[hooks]" not in snippet
    assert "[hooks.SessionStart]" not in snippet
    assert "[hooks.PreToolUse]" not in snippet
    assert "hook_session_start.sh" not in snippet
    assert "hook_pre_tool_use.sh" not in snippet


def test_fresh_install_writes_file(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    assert not cfg.exists()
    result = module.emit_and_install_codex_config(str(PLUGIN_ROOT), config_path=cfg)
    assert result["ok"] is True, result["message"]
    assert cfg.exists()
    # Parses as valid TOML
    parsed = tomllib.loads(cfg.read_text())
    assert parsed["plugins"]["harness@harness"]["enabled"] is True
    assert parsed["mcp_servers"]["harness"]["enabled"] is True
    assert "hooks" not in parsed


def test_missing_plugin_root_errors():
    module = _load_install_module()
    result = module.emit_and_install_codex_config("/nonexistent/path")
    assert result["ok"] is False
    assert "not a directory" in result["message"]
