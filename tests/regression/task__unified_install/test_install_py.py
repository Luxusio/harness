"""AC-004 regression: install.py CLI surface + behavior.

Tests:
1. --dry-run prints non-empty plan and does NOT mutate any config
2. --codex-only flag suppresses claude steps
3. --claude-only flag suppresses codex steps
4. --codex-only + --claude-only is rejected (exit 2)
5. Missing CLI is handled cleanly (no crash, auto-detect skips unavailable runtimes)
6. Parallel execution: both runtimes are attempted in one run when both CLIs exist
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import importlib.util
import json
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore

REPO_ROOT = Path(__file__).resolve().parents[3]
INSTALL_PY = REPO_ROOT / "install.py"


def _load_install_module():
    spec = importlib.util.spec_from_file_location("harness_install_py", INSTALL_PY)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(args: list[str], extra_path: str | None = None) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    if extra_path is not None:
        # Replace PATH so neither codex nor claude is discoverable
        env["PATH"] = extra_path
    return subprocess.run(
        [sys.executable, str(INSTALL_PY)] + args,
        capture_output=True, text=True, timeout=30, env=env,
    )


def _run_with_env(args: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    merged = os.environ.copy()
    merged.update(env)
    return subprocess.run(
        [sys.executable, str(INSTALL_PY)] + args,
        capture_output=True, text=True, timeout=180, env=merged,
    )


def test_install_py_exists():
    assert INSTALL_PY.is_file()
    # Shebang for direct invocation
    assert INSTALL_PY.read_text().startswith("#!/usr/bin/env python3")


def test_dry_run_prints_plan_without_mutation(tmp_path):
    # Sentinel config path that should remain untouched on dry-run
    cfg = tmp_path / "fake-codex-config.toml"
    cfg.write_text("# untouched\n")
    pre_size = cfg.stat().st_size
    r = _run(["--dry-run", "--config-path", str(cfg)])
    if r.returncode != 0:
        assert "not found in PATH" in (r.stdout + r.stderr), f"dry-run exit {r.returncode}: {r.stderr}"
    # Output mentions both runtimes (whichever exist) and "dry-run"
    assert "dry-run" in r.stdout.lower()
    # Config file untouched
    assert cfg.stat().st_size == pre_size
    assert cfg.read_text() == "# untouched\n"


def test_codex_only_skips_claude(tmp_path):
    cfg = tmp_path / "fake-codex-config.toml"
    r = _run(["--dry-run", "--codex-only", "--config-path", str(cfg)])
    assert r.returncode == 0, r.stderr
    assert "[claude]" not in r.stdout
    # codex section present (or "codex CLI not found" if codex unavailable in env)
    assert "[codex]" in r.stdout or "codex CLI not found" in r.stdout


def test_claude_only_skips_codex(tmp_path):
    cfg = tmp_path / "fake-codex-config.toml"
    r = _run(["--dry-run", "--claude-only", "--config-path", str(cfg)])
    if r.returncode != 0:
        assert "claude CLI not found" in r.stdout
    assert "[codex]" not in r.stdout
    assert "[claude]" in r.stdout or "claude CLI not found" in r.stdout


def test_mutual_exclusion_rejected():
    r = _run(["--codex-only", "--claude-only"])
    assert r.returncode == 2
    assert "mutually exclusive" in r.stderr


def test_missing_cli_handled_gracefully(tmp_path):
    """When BOTH CLIs are missing, default auto-detect exits before install work."""
    cfg = tmp_path / "fake-codex-config.toml"
    # Empty PATH — no codex, no claude discoverable
    r = _run(["--dry-run", "--config-path", str(cfg)], extra_path=str(tmp_path))
    assert r.returncode == 2
    assert "no supported runtime CLI found" in r.stderr
    assert "codex CLI not found in PATH" in r.stderr
    assert "claude CLI not found in PATH" in r.stderr
    assert "Traceback" not in r.stderr
    assert "Traceback" not in r.stdout


def test_default_auto_detect_skips_missing_claude_when_codex_available(tmp_path):
    """Default install should target detected CLIs only, not fail because Claude is absent."""
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text("#!/bin/sh\necho 'codex 0.130.0'\n")
    fake_codex.chmod(0o755)
    cfg = tmp_path / "fake-codex-config.toml"

    r = _run(["--dry-run", "--config-path", str(cfg)], extra_path=str(fake_bin))

    assert r.returncode == 0, r.stderr
    assert "runtimes: codex" in r.stdout
    assert "skipping: claude (claude CLI not found in PATH)" in r.stdout
    assert "[codex]" in r.stdout
    assert "[claude]" not in r.stdout


def test_dry_run_parallel_attempts_both_when_available():
    """When both CLIs are present, dry-run output mentions both [codex] and [claude]."""
    if not (shutil.which("codex") and shutil.which("claude")):
        # Skip when env lacks one — test is environment-conditional
        return
    r = _run(["--dry-run"])
    assert r.returncode == 0, r.stderr
    assert "[codex]" in r.stdout
    assert "[claude]" in r.stdout


def test_help_lists_all_flags():
    r = _run(["--help"])
    assert r.returncode == 0
    for flag in ["--codex-only", "--claude-only", "--dry-run", "--force", "--config-path"]:
        assert flag in r.stdout, f"missing flag in --help: {flag}"


def test_sync_codex_payload_copies_runtime_under_codex_root(tmp_path):
    module = _load_install_module()
    plugin_root = module.sync_codex_payload(tmp_path / "codex" / "harness")

    assert plugin_root == tmp_path / "codex" / "harness" / "plugins" / "harness"
    assert (plugin_root / "mcp" / "harness_server.py").is_file()
    assert (plugin_root / "scripts" / "stop_gate.py").is_file()
    assert not (plugin_root / ".claude-plugin").exists()
    assert not (tmp_path / "codex" / "harness" / "plugin").exists()
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    assert manifest_path.is_file()
    assert (tmp_path / "codex" / "harness" / ".agents" / "plugins" / "marketplace.json").is_file()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["hooks"] == "./hooks.json"
    assert manifest["mcpServers"] == "./.mcp.json"
    hooks_path = tmp_path / "codex" / "harness" / "plugins" / "harness" / "hooks.json"
    assert hooks_path.is_file()
    hooks = json.loads(hooks_path.read_text())["hooks"]
    assert set(hooks) == {"SessionStart", "PreToolUse", "UserPromptSubmit", "PostToolUse"}
    assert "hook_session_start.py" in hooks_path.read_text()
    assert "hook_session_start.sh" not in hooks_path.read_text()
    assert str(plugin_root / "scripts" / "hook_session_start.py") in hooks_path.read_text()
    mcp_path = tmp_path / "codex" / "harness" / "plugins" / "harness" / ".mcp.json"
    assert mcp_path.is_file()
    mcp = json.loads(mcp_path.read_text())["mcpServers"]["harness"]
    assert mcp["command"] == module._python_cmd()
    assert mcp["args"] == [str(plugin_root / "mcp" / "harness_server.py")]
    assert mcp["env"]["HARNESS_PLUGIN_ROOT"] == str(plugin_root)
    assert str(REPO_ROOT) not in mcp_path.read_text()


def test_codex_manifest_declares_all_plugin_local_capabilities():
    manifest = json.loads((REPO_ROOT / "plugin-codex" / ".codex-plugin" / "plugin.json").read_text())

    assert manifest["name"] == "harness"
    assert manifest["version"] == "2.3.0"
    assert manifest["skills"] == "./skills/"
    assert manifest["hooks"] == "./hooks.json"
    assert manifest["mcpServers"] == "./.mcp.json"
    assert "2.3.0-codex" not in json.dumps(manifest)


def test_sync_codex_payload_produces_complete_plugin_bundle(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    plugin_root = module.sync_codex_payload(install_root)
    codex_plugin = install_root / "plugins" / "harness"

    manifest = json.loads((codex_plugin / ".codex-plugin" / "plugin.json").read_text())
    for rel in [manifest["skills"], manifest["hooks"], manifest["mcpServers"]]:
        assert (codex_plugin / rel).exists(), f"manifest path must exist: {rel}"

    skill_names = sorted(path.parent.name for path in (codex_plugin / "skills").glob("*/SKILL.md"))
    assert skill_names == ["setup"]

    internal_skill_names = sorted(
        path.parent.name for path in (codex_plugin / "internal-skills").glob("*/SKILL.md")
    )
    assert internal_skill_names == [
        "develop",
        "goal-queue",
        "plan",
        "plan-ceo-review",
        "plan-design-review",
        "plan-devex-review",
        "plan-eng-review",
        "run",
    ]

    hooks_text = (codex_plugin / "hooks.json").read_text()
    mcp_text = (codex_plugin / ".mcp.json").read_text()
    assert str(REPO_ROOT) not in hooks_text
    assert str(REPO_ROOT) not in mcp_text
    assert str(plugin_root) in mcp_text
    assert (codex_plugin / "scripts" / "hook_pre_tool_use.py").is_file()
    assert (codex_plugin / "mcp" / "harness_server.py").is_file()
    assert (codex_plugin / "agents" / "code-reviewer.md").is_file()
    assert (codex_plugin / "agents" / "security-reviewer.md").is_file()
    assert not list((codex_plugin / "scripts").glob("hook_*.sh"))


def test_codex_marketplace_points_at_installed_plugin_tree(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    module.sync_codex_payload(install_root)

    marketplace = json.loads((install_root / ".agents" / "plugins" / "marketplace.json").read_text())

    assert marketplace["name"] == "harness"
    assert marketplace["plugins"] == [
        {
            "name": "harness",
            "source": "./plugins/harness",
            "category": "productivity",
            "version": "2.3.0",
            "description": "MCP-backed task planning, development, QA, and docs sync workflow.",
        }
    ]


def test_codex_hooks_config_uses_absolute_cache_python_and_reviews_all_events(tmp_path):
    module = _load_install_module()
    plugin_root = tmp_path / ".codex" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"

    hooks = module._codex_hooks_config(plugin_root)["hooks"]

    assert set(hooks) == {"SessionStart", "PreToolUse", "UserPromptSubmit", "PostToolUse"}
    expected_commands = {
        "SessionStart": str(plugin_root / "scripts" / "hook_session_start.py"),
        "PreToolUse": str(plugin_root / "scripts" / "hook_pre_tool_use.py"),
        "UserPromptSubmit": str(plugin_root / "scripts" / "hook_user_prompt_submit.py"),
        "PostToolUse": str(plugin_root / "scripts" / "hook_post_tool_use.py"),
    }
    for event, command in expected_commands.items():
        hook = hooks[event][0]["hooks"][0]
        assert hook["type"] == "command"
        assert hook["command"] == f"{module._python_cmd()} {command}"
        assert hook["command"].startswith("/")
        assert hook["timeout"] > 0
        assert hook["statusMessage"]
    assert hooks["PostToolUse"][0]["matcher"] == (
        "Bash|.*spawn_agent|.*wait_agent|.*list_agents"
    )


def test_codex_hook_trust_state_matches_normalized_codex_identity(tmp_path):
    module = _load_install_module()
    plugin_root = tmp_path / ".codex" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    hooks_config = module._codex_hooks_config(plugin_root)

    state = module._codex_hook_trust_state("harness@harness", hooks_config)

    assert set(state) == {
        "harness@harness:hooks.json:session_start:0:0",
        "harness@harness:hooks.json:pre_tool_use:0:0",
        "harness@harness:hooks.json:user_prompt_submit:0:0",
        "harness@harness:hooks.json:post_tool_use:0:0",
    }
    assert all(value.startswith("sha256:") for value in state.values())
    assert len({value for value in state.values()}) == 4


def test_install_codex_hook_trust_state_preserves_user_state_and_enabled_flag(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[hooks.state]\n"
        "\n"
        '[hooks.state."harness@harness:hooks.json:pre_tool_use:0:0"]\n'
        "enabled = false\n"
        'trusted_hash = "sha256:old"\n'
        "\n"
        '[hooks.state."custom:/hooks.json:stop:0:0"]\n'
        'trusted_hash = "sha256:user"\n'
    )
    plugin_root = tmp_path / ".codex" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    hooks_config = module._codex_hooks_config(plugin_root)

    result = module.install_codex_hook_trust_state(cfg, hooks_config)

    assert result["ok"] is True
    content = cfg.read_text()
    assert 'trusted_hash = "sha256:old"' not in content
    assert '[hooks.state."custom:/hooks.json:stop:0:0"]' in content
    assert 'trusted_hash = "sha256:user"' in content
    assert '[hooks.state."harness@harness:hooks.json:pre_tool_use:0:0"]' in content
    assert content.count("[hooks.state]") == 1
    assert "enabled = false" in content
    parsed = tomllib.loads(content)
    harness_state = parsed["hooks"]["state"]["harness@harness:hooks.json:pre_tool_use:0:0"]
    assert harness_state["enabled"] is False
    assert harness_state["trusted_hash"].startswith("sha256:")


def test_codex_mcp_config_uses_absolute_python_and_installed_plugin_root(tmp_path):
    module = _load_install_module()
    plugin_root = tmp_path / ".codex" / "harness" / "plugin"

    mcp = module._codex_mcp_config(plugin_root)["mcpServers"]["harness"]

    assert mcp["command"] == module._python_cmd()
    assert Path(mcp["command"]).is_absolute()
    assert mcp["args"] == [str(plugin_root.resolve() / "mcp" / "harness_server.py")]
    assert mcp["env"] == {
        "HARNESS_PLUGIN_ROOT": str(plugin_root.resolve()),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root.resolve()),
    }
    assert mcp["command"] != "python3"


def test_install_codex_plugin_cache_uses_manifest_version_not_codex_suffix(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    module.sync_codex_payload(install_root)
    source_root = install_root / "plugins" / "harness"
    codex_home = tmp_path / "codex-home"
    stale_suffix = codex_home / "plugins" / "cache" / "harness" / "harness" / "2.3.0-codex"
    stale_suffix.mkdir(parents=True)
    (stale_suffix / "stale.txt").write_text("old")

    cached = module.install_codex_plugin_cache(source_root, codex_home)

    assert cached.name == "2.3.0"
    assert cached == codex_home / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    assert not stale_suffix.exists()
    assert (cached / "hooks.json").is_file()
    assert (cached / ".mcp.json").is_file()
    assert (cached / "scripts" / "hook_pre_tool_use.py").is_file()
    assert (cached / "mcp" / "harness_server.py").is_file()
    assert str(cached / "scripts" / "hook_pre_tool_use.py") in (cached / "hooks.json").read_text()
    assert str(cached / "mcp" / "harness_server.py") in (cached / ".mcp.json").read_text()


def test_emit_codex_config_rehomes_everything_to_installed_codex_root(tmp_path):
    module = _load_install_module()
    installed_plugin_root = tmp_path / ".codex" / "harness" / "plugin"
    installed_plugin_root.mkdir(parents=True)

    parsed = tomllib.loads(module.emit_codex_config(str(installed_plugin_root)))

    assert parsed["plugins"]["harness@harness"]["enabled"] is True
    assert parsed["marketplaces"]["harness"]["source"] == str(module.CODEX_INSTALL_ROOT)
    harness = parsed["mcp_servers"]["harness"]
    assert harness["command"] == module._python_cmd()
    assert harness["args"] == [str(installed_plugin_root / "mcp" / "harness_server.py")]
    assert harness["env"]["HARNESS_PLUGIN_ROOT"] == str(installed_plugin_root)
    assert harness["env"]["CLAUDE_PLUGIN_ROOT"] == str(installed_plugin_root)
    assert str(REPO_ROOT) not in json.dumps(parsed)


def test_codex_force_merge_dedupes_duplicate_user_mcp_tables(tmp_path):
    module = _load_install_module()
    cfg = tmp_path / "config.toml"
    plugin_root = tmp_path / ".codex" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    plugin_root.mkdir(parents=True)
    cfg.write_text(
        '[mcp_servers.chrome-devtools]\n'
        'command = "old-chrome"\n'
        "\n"
        '[mcp_servers.x11-display]\n'
        'command = "old-x11"\n'
        "\n"
        '[mcp_servers.chrome-devtools]\n'
        'command = "new-chrome"\n'
        'args = ["latest"]\n'
        "\n"
        '[mcp_servers.x11-display]\n'
        'command = "new-x11"\n'
        'args = ["latest"]\n'
    )

    result = module.emit_and_install_codex_config(str(plugin_root), config_path=cfg, force=True)

    assert result["ok"] is True
    content = cfg.read_text()
    assert content.count("[mcp_servers.chrome-devtools]") == 1
    assert content.count("[mcp_servers.x11-display]") == 1
    assert 'command = "old-chrome"' not in content
    assert 'command = "old-x11"' not in content
    parsed = tomllib.loads(content)
    assert parsed["mcp_servers"]["chrome-devtools"]["command"] == "new-chrome"
    assert parsed["mcp_servers"]["x11-display"]["command"] == "new-x11"
    assert parsed["mcp_servers"]["harness"]["enabled"] is True


def test_codex_install_with_fake_cli_recovers_duplicate_mcp_config(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text(
        '[mcp_servers.chrome-devtools]\n'
        'command = "old-chrome"\n'
        "\n"
        '[mcp_servers.chrome-devtools]\n'
        'command = "new-chrome"\n'
        "\n"
        '[mcp_servers.x11-display]\n'
        'command = "old-x11"\n'
        "\n"
        '[mcp_servers.x11-display]\n'
        'command = "new-x11"\n'
    )
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(config_path))

    assert result.ok is True, result.summary
    parsed = tomllib.loads(config_path.read_text())
    assert parsed["mcp_servers"]["chrome-devtools"]["command"] == "new-chrome"
    assert parsed["mcp_servers"]["x11-display"]["command"] == "new-x11"
    assert parsed["mcp_servers"]["harness"]["args"] == [
        str(codex_install_root / "plugins" / "harness" / "mcp" / "harness_server.py")
    ]


def test_real_codex_install_with_fake_cli_enables_plugin_hooks_and_cache(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "codex.log"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CODEX_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    codex_install_root = tmp_path / ".codex" / "harness"
    config_path = tmp_path / ".codex" / "config.toml"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_LOG", str(log))
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(config_path))

    assert result.ok is True, result.summary
    lines = log.read_text().splitlines()
    assert "--version" in lines
    assert "features enable plugin_hooks" in lines
    assert f"plugin marketplace add {codex_install_root}" in lines
    assert lines.index("features enable plugin_hooks") < lines.index(f"plugin marketplace add {codex_install_root}")

    cached = tmp_path / ".codex" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    assert (cached / ".codex-plugin" / "plugin.json").is_file()
    assert (cached / "hooks.json").is_file()
    assert (cached / ".mcp.json").is_file()
    assert not (cached / "skills" / "run" / "SKILL.md").exists()
    assert (cached / "internal-skills" / "run" / "SKILL.md").is_file()
    assert (cached / "scripts" / "hook_pre_tool_use.py").is_file()
    assert (cached / "mcp" / "harness_server.py").is_file()
    parsed = tomllib.loads(config_path.read_text())
    assert parsed["plugins"]["harness@harness"]["enabled"] is True
    assert parsed["marketplaces"]["harness"]["source"] == str(codex_install_root)
    assert parsed["mcp_servers"]["harness"]["args"] == [
        str(codex_install_root / "plugins" / "harness" / "mcp" / "harness_server.py")
    ]
    assert parsed["hooks"]["state"]["harness@harness:hooks.json:pre_tool_use:0:0"][
        "trusted_hash"
    ].startswith("sha256:")
    assert parsed["hooks"]["state"]["harness@harness:hooks.json:post_tool_use:0:0"][
        "trusted_hash"
    ].startswith("sha256:")


def test_codex_install_rejects_version_below_pin_before_mutating_payload(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text("#!/bin/sh\nif [ \"$1\" = \"--version\" ]; then echo 'codex 0.1.0'; exit 0; fi\nexit 0\n")
    fake_codex.chmod(0o755)
    pin = tmp_path / ".codex-version"
    pin.write_text("0.130.0\n")
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setattr(module, "CODEX_VERSION_PIN_FILE", pin)
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(tmp_path / ".codex" / "config.toml"))

    assert result.ok is False
    assert "codex 0.1.0 < pin 0.130.0" in result.summary
    assert not codex_install_root.exists()


def test_codex_install_fails_if_plugin_hooks_feature_cannot_be_enabled(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "codex.log"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CODEX_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "if [ \"$1 $2 $3\" = \"features enable plugin_hooks\" ]; then echo 'feature unavailable' >&2; exit 42; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_LOG", str(log))
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(tmp_path / ".codex" / "config.toml"))

    assert result.ok is False
    assert "plugin_hooks failed" in result.summary
    lines = log.read_text().splitlines()
    assert "features enable plugin_hooks" in lines
    assert not any(line.startswith("plugin marketplace add ") for line in lines)


def test_codex_install_fails_if_marketplace_add_fails_after_plugin_hooks(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "codex.log"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CODEX_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "if [ \"$1 $2 $3\" = \"plugin marketplace add\" ]; then echo 'add failed' >&2; exit 43; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_LOG", str(log))
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(tmp_path / ".codex" / "config.toml"))

    assert result.ok is False
    assert "plugin marketplace add failed" in result.summary
    lines = log.read_text().splitlines()
    assert lines.index("features enable plugin_hooks") < lines.index(f"plugin marketplace add {codex_install_root}")


def test_codex_install_treats_existing_marketplace_as_success(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "if [ \"$1 $2 $3\" = \"plugin marketplace add\" ]; then echo 'already exists' >&2; exit 9; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=True, config_path=str(tmp_path / ".codex" / "config.toml"))

    assert result.ok is True, result.summary
    assert result.summary == "Codex install complete"


def test_codex_install_without_force_stops_before_feature_enable_when_config_exists(tmp_path, monkeypatch):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "codex.log"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CODEX_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    config_path = tmp_path / ".codex" / "config.toml"
    config_path.parent.mkdir()
    config_path.write_text('[mcp_servers.harness]\ncommand = "old"\n')
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("CODEX_LOG", str(log))
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)

    result = module.install_codex(dry_run=False, force=False, config_path=str(config_path))

    assert result.ok is False
    assert "already present" in result.summary
    lines = log.read_text().splitlines()
    assert "features enable plugin_hooks" not in lines
    assert not any(line.startswith("plugin marketplace add ") for line in lines)


def test_sync_codex_payload_removes_legacy_top_level_plugin_shapes(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    (install_root / "plugin-codex").mkdir(parents=True)
    (install_root / "plugin-codex" / "old.txt").write_text("old")
    (install_root / ".codex-plugin").mkdir()
    (install_root / ".codex-plugin" / "old.json").write_text("{}")
    (install_root / "marketplace.json").write_text("{}")

    module.sync_codex_payload(install_root)

    assert not (install_root / "plugin-codex").exists()
    assert not (install_root / ".codex-plugin").exists()
    assert not (install_root / "marketplace.json").exists()
    assert (install_root / "plugins" / "harness" / ".codex-plugin" / "plugin.json").is_file()


def test_codex_plugin_cache_removes_all_stale_versions(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "codex" / "harness"
    module.sync_codex_payload(install_root)
    codex_home = tmp_path / "codex-home"
    cache_parent = codex_home / "plugins" / "cache" / "harness" / "harness"
    for version in ["2.2.0", "2.3.0-codex", "local"]:
        stale = cache_parent / version
        stale.mkdir(parents=True)
        (stale / "stale.txt").write_text("old")

    cached = module.install_codex_plugin_cache(install_root / "plugins" / "harness", codex_home)

    assert cached == cache_parent / "2.3.0"
    assert sorted(path.name for path in cache_parent.iterdir()) == ["2.3.0"]


def test_sync_claude_payload_copies_runtime_under_claude_root_without_git(tmp_path):
    module = _load_install_module()
    plugin_root = module.sync_claude_payload(tmp_path / "claude" / "harness-dev")

    assert plugin_root == tmp_path / "claude" / "harness-dev" / "plugin"
    assert (plugin_root / ".claude-plugin" / "plugin.json").is_file()
    assert (plugin_root / "mcp" / "harness_server.py").is_file()
    assert not (tmp_path / "claude" / "harness-dev" / ".git").exists()
    marketplace = tmp_path / "claude" / "harness-dev" / ".claude-plugin" / "marketplace.json"
    assert marketplace.is_file()
    assert json.loads(marketplace.read_text())["plugins"][0]["source"] == "./plugin"


def test_sync_claude_payload_preserves_claude_marketplace_contract(tmp_path):
    module = _load_install_module()
    install_root = tmp_path / "claude" / "harness-dev"
    plugin_root = module.sync_claude_payload(install_root)

    marketplace = json.loads((install_root / ".claude-plugin" / "marketplace.json").read_text())
    manifest = json.loads((plugin_root / ".claude-plugin" / "plugin.json").read_text())

    assert marketplace["plugins"][0]["name"] == "harness"
    assert marketplace["plugins"][0]["source"] == "./plugin"
    assert manifest["name"] == "harness"
    assert manifest["version"] == "2.3.0"
    assert manifest["features"]["codex_enabled"] is False
    assert manifest["features"]["codex_marketplace_separate"] is True
    assert not (install_root / "plugin-codex").exists()
    assert not (plugin_root / ".codex-plugin").exists()


def test_install_codex_plugin_cache_marks_plugin_installed(tmp_path):
    module = _load_install_module()
    source_root = tmp_path / "codex" / "harness" / "plugins" / "harness"
    module.sync_codex_payload(tmp_path / "codex" / "harness")
    stale = tmp_path / "codex-home" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    stale.mkdir(parents=True)
    (stale / "stale.txt").write_text("old")

    cached = module.install_codex_plugin_cache(source_root, tmp_path / "codex-home")

    assert cached == tmp_path / "codex-home" / "plugins" / "cache" / "harness" / "harness" / "2.3.0"
    assert (cached / ".codex-plugin" / "plugin.json").is_file()
    assert (cached / ".mcp.json").is_file()
    assert (cached / "hooks.json").is_file()
    assert not (cached / "skills" / "run" / "SKILL.md").exists()
    assert (cached / "internal-skills" / "run" / "SKILL.md").is_file()
    assert not (cached / "stale.txt").exists()


def test_codex_skill_files_start_with_yaml_frontmatter():
    skill_paths = list((REPO_ROOT / "plugin-codex" / "skills").glob("*/SKILL.md"))
    skill_paths += list((REPO_ROOT / "plugin-codex" / "internal-skills").glob("*/SKILL.md"))
    for skill_path in sorted(skill_paths):
        text = skill_path.read_text()
        assert text.startswith("---\n"), f"{skill_path} must start with YAML frontmatter"
        assert "\n---\n" in text[4:], f"{skill_path} must close YAML frontmatter"


def test_dry_run_mentions_codex_install_root_when_codex_available(tmp_path):
    if not shutil.which("codex"):
        return
    cfg = tmp_path / "fake-codex-config.toml"
    r = _run(["--dry-run", "--codex-only", "--config-path", str(cfg)])
    assert r.returncode == 0, r.stderr
    assert "would sync plugin payload to" in r.stdout
    assert "would install Codex plugin cache entry harness@harness" in r.stdout
    assert "would install Codex plugin-local hooks.json" in r.stdout
    assert "codex features enable plugin_hooks" in r.stdout
    assert "codex plugin marketplace add" in r.stdout
    assert ".codex/harness" in r.stdout
    assert "[hooks.*]" not in r.stdout


def test_dry_run_mentions_claude_install_root_when_claude_available(tmp_path):
    if not shutil.which("claude"):
        return
    r = _run(["--dry-run", "--claude-only"])
    assert r.returncode == 0, r.stderr
    assert "would sync plugin payload to" in r.stdout
    assert "claude plugin marketplace add" in r.stdout
    assert "claude plugin marketplace update harness" in r.stdout
    assert ".claude/harness-dev" in r.stdout


def test_claude_install_rehomes_stale_marketplace_source(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "claude.log"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CLAUDE_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then\n"
        "  echo 'Configured marketplaces:'\n"
        "  echo '  harness'\n"
        "  echo '    Source: Directory (/stale/project/checkout)'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)
    harness_dest = tmp_path / "claude" / "harness-dev"

    r = _run_with_env(
        ["--claude-only"],
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HARNESS_DEST": str(harness_dest),
            "CLAUDE_LOG": str(log),
        },
    )

    assert r.returncode == 0, r.stderr + r.stdout
    lines = log.read_text().splitlines()
    assert "plugin marketplace remove harness" in lines
    assert f"plugin marketplace add {harness_dest}" in lines
    assert "plugin install harness@harness" in lines
    assert any(
        line.endswith(f"-- python3 {harness_dest / 'plugin' / 'mcp' / 'harness_server.py'}")
        for line in lines
    )
    assert (harness_dest / "plugin" / "mcp" / "harness_server.py").is_file()
    assert not (harness_dest / ".git").exists()


def test_claude_install_updates_marketplace_when_registered_to_install_root(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "claude.log"
    harness_dest = tmp_path / "claude" / "harness-dev"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CLAUDE_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then\n"
        f"  echo 'harness Source: Directory ({harness_dest})'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)

    r = _run_with_env(
        ["--claude-only"],
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HARNESS_DEST": str(harness_dest),
            "CLAUDE_LOG": str(log),
        },
    )

    assert r.returncode == 0, r.stderr + r.stdout
    lines = log.read_text().splitlines()
    assert "plugin marketplace update harness" in lines
    assert "plugin marketplace remove harness" not in lines
    assert not any(line.startswith("plugin install harness@harness") for line in lines)


def test_claude_install_adds_marketplace_when_not_registered(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "claude.log"
    harness_dest = tmp_path / "claude" / "harness-dev"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CLAUDE_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then\n"
        "  echo 'Configured marketplaces:'\n"
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)

    r = _run_with_env(
        ["--claude-only"],
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HARNESS_DEST": str(harness_dest),
            "CLAUDE_LOG": str(log),
        },
    )

    assert r.returncode == 0, r.stderr + r.stdout
    lines = log.read_text().splitlines()
    assert f"plugin marketplace add {harness_dest}" in lines
    assert "plugin install harness@harness" in lines
    assert "plugin marketplace update harness" not in lines


def test_claude_install_accepts_existing_plugin_and_mcp_messages(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    log = tmp_path / "claude.log"
    harness_dest = tmp_path / "claude" / "harness-dev"
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CLAUDE_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"install\" ]; then echo 'already installed' >&2; exit 17; fi\n"
        "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"add\" ]; then echo 'already exists' >&2; exit 18; fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)

    r = _run_with_env(
        ["--claude-only"],
        {
            "PATH": f"{fake_bin}:/usr/bin:/bin",
            "HARNESS_DEST": str(harness_dest),
            "CLAUDE_LOG": str(log),
        },
    )

    assert r.returncode == 0, r.stderr + r.stdout
    assert "Claude install complete" in r.stdout


def test_claude_install_fails_when_marketplace_list_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then echo 'boom' >&2; exit 22; fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)

    r = _run_with_env(
        ["--claude-only"],
        {"PATH": f"{fake_bin}:/usr/bin:/bin", "HARNESS_DEST": str(tmp_path / "claude" / "harness-dev")},
    )

    assert r.returncode == 1
    assert "claude plugin marketplace list failed: boom" in r.stdout


def test_claude_install_fails_when_mcp_add_hard_fails(tmp_path):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then exit 0; fi\n"
        "if [ \"$1\" = \"mcp\" ] && [ \"$2\" = \"add\" ]; then echo 'permission denied' >&2; exit 23; fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)

    r = _run_with_env(
        ["--claude-only"],
        {"PATH": f"{fake_bin}:/usr/bin:/bin", "HARNESS_DEST": str(tmp_path / "claude" / "harness-dev")},
    )

    assert r.returncode == 1
    assert "claude mcp add failed: permission denied" in r.stdout


def test_main_attempts_both_runtimes_and_reports_partial_failure(tmp_path, monkeypatch, capsys):
    module = _load_install_module()
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    codex_log = tmp_path / "codex.log"
    claude_log = tmp_path / "claude.log"
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CODEX_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'codex 0.130.0'; exit 0; fi\n"
        "exit 0\n"
    )
    fake_codex.chmod(0o755)
    fake_claude = fake_bin / "claude"
    fake_claude.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$*\" >> \"$CLAUDE_LOG\"\n"
        "if [ \"$1\" = \"--version\" ]; then echo 'claude 1.2.3'; exit 0; fi\n"
        "if [ \"$1\" = \"plugin\" ] && [ \"$2\" = \"marketplace\" ] && [ \"$3\" = \"list\" ]; then echo 'list failed' >&2; exit 31; fi\n"
        "exit 0\n"
    )
    fake_claude.chmod(0o755)
    cfg = tmp_path / ".codex" / "config.toml"
    codex_install_root = tmp_path / ".codex" / "harness"
    monkeypatch.setenv("PATH", f"{fake_bin}:/usr/bin:/bin")
    monkeypatch.setenv("HARNESS_DEST", str(tmp_path / "claude" / "harness-dev"))
    monkeypatch.setenv("CODEX_LOG", str(codex_log))
    monkeypatch.setenv("CLAUDE_LOG", str(claude_log))
    monkeypatch.setattr(module, "CODEX_INSTALL_ROOT", codex_install_root)
    monkeypatch.setattr(sys, "argv", [str(INSTALL_PY), "--config-path", str(cfg), "--force"])

    rc = module.main()
    captured = capsys.readouterr()

    assert rc == 1
    assert "runtimes:" in captured.out
    assert "codex" in captured.out
    assert "claude" in captured.out
    assert "Codex install complete" in captured.out
    assert "claude plugin marketplace list failed: list failed" in captured.out
    assert "features enable plugin_hooks" in codex_log.read_text().splitlines()
    assert "--version" in claude_log.read_text().splitlines()


def test_codex_hook_wrappers_emit_empty_or_valid_json():
    payload = "{}"
    for script in [
        "hook_pre_tool_use.py",
        "hook_session_start.py",
        "hook_user_prompt_submit.py",
        "hook_post_tool_use.py",
    ]:
        r = subprocess.run(
            [sys.executable, str(REPO_ROOT / "plugin" / "scripts" / script)],
            input=payload,
            capture_output=True,
            text=True,
            timeout=15,
        )
        assert r.returncode == 0, r.stderr
        if r.stdout.strip():
            parsed = json.loads(r.stdout)
            assert "hookSpecificOutput" in parsed
            assert "hookEventName" in parsed["hookSpecificOutput"]
