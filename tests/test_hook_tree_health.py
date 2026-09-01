"""Detection of a loaded plugin tree that cannot record receipts.

Regression cover for 2026-08-26: the session loaded hooks from a tree that
predated the receipt subsystem, so no receipt was ever written and no gate
noticed. See doc/harness/REQ__harness-announces-lost-receipt-capability.md.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SPEC = importlib.util.spec_from_file_location(
    "hook_tree_health", ROOT / "plugin/scripts/hook_tree_health.py"
)
assert SPEC and SPEC.loader
mod = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mod
SPEC.loader.exec_module(mod)

WARNING_TEXT = "may not be able to record receipts"


def _config(tmp_path: Path, install_path, *, key="harness@harness") -> Path:
    """Write a plugins config registering `key` at `install_path`."""
    plugins = tmp_path / "plugins"
    plugins.mkdir(parents=True, exist_ok=True)
    record = {"scope": "user", "version": "x"}
    if install_path is not None:
        record["installPath"] = str(install_path)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"version": 2, "plugins": {key: [record]}}), encoding="utf-8"
    )
    return tmp_path


def _tree(root: Path, *, nested=True, scripts=mod.RECEIPT_MODULES,
          events=mod.RECEIPT_EVENTS) -> Path:
    """Build a plugin tree in either the current or the legacy layout."""
    base = root / "plugin" if nested else root
    scripts_dir = base / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    for name in scripts:
        (scripts_dir / name).write_text("# stub\n", encoding="utf-8")
    hooks_dir = base / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hooks = {event: [{"hooks": []}] for event in events}
    hooks["PreToolUse"] = [{"hooks": []}]
    (hooks_dir / "hooks.json").write_text(
        json.dumps({"hooks": hooks}), encoding="utf-8"
    )
    return root


def test_healthy_tree_produces_no_warning(tmp_path):
    root = _tree(tmp_path / "tree")
    cfg = _config(tmp_path / "cfg", root)
    assert mod.receipt_capability_warning(str(cfg)) == ""


def test_legacy_layout_with_receipts_is_still_healthy(tmp_path):
    """scripts/ at tree root must not be mistaken for a broken tree."""
    root = _tree(tmp_path / "tree", nested=False)
    cfg = _config(tmp_path / "cfg", root)
    assert mod.receipt_capability_warning(str(cfg)) == ""


def test_tree_missing_receipt_modules_warns(tmp_path):
    # The 2026-08-26 shape: gates present, receipt writer absent.
    root = _tree(tmp_path / "tree", scripts=("prewrite_gate.py", "stop_gate.py"))
    cfg = _config(tmp_path / "cfg", root)
    message = mod.receipt_capability_warning(str(cfg))
    assert WARNING_TEXT in message
    assert str(root) in message, "the warning must name the offending tree"
    assert "task_verify once" in message
    assert "task_blocked" in message
    assert "restart the session" not in message


def test_tree_not_registering_both_subagent_events_warns(tmp_path):
    """Either event missing is fatal: receipts need the ordered pair."""
    for index, events in enumerate([(), ("SubagentStart",), ("SubagentStop",)]):
        root = _tree(tmp_path / f"tree{index}", events=events)
        cfg = _config(tmp_path / f"cfg{index}", root)
        assert WARNING_TEXT in mod.receipt_capability_warning(str(cfg)), events


def test_unreadable_hooks_json_warns_even_with_all_modules_present(tmp_path):
    """Registration we cannot vouch for is treated as absent.

    Deliberately asymmetric: a false warning costs an unnecessary update, a
    false silence reproduces the 2026-08-26 outage. Pinned because the natural
    "relax it, the scripts are all there" edit would undo exactly that.
    """
    for index, payload in enumerate(["", "not json", "[]", '{"hooks": []}']):
        root = _tree(tmp_path / f"tree{index}")
        (root / "plugin" / "hooks" / "hooks.json").write_text(payload, encoding="utf-8")
        cfg = _config(tmp_path / f"cfg{index}", root)
        assert WARNING_TEXT in mod.receipt_capability_warning(str(cfg)), payload


def test_absent_hooks_json_warns_even_with_all_modules_present(tmp_path):
    root = _tree(tmp_path / "tree")
    (root / "plugin" / "hooks" / "hooks.json").unlink()
    cfg = _config(tmp_path / "cfg", root)
    assert WARNING_TEXT in mod.receipt_capability_warning(str(cfg))


def test_partial_receipt_modules_warn(tmp_path):
    root = _tree(tmp_path / "tree", scripts=("background_hook.py",))
    cfg = _config(tmp_path / "cfg", root)
    assert WARNING_TEXT in mod.receipt_capability_warning(str(cfg))


def test_unresolvable_registration_is_silent(tmp_path):
    """An unknown config shape must never be reported as a broken tree."""
    payloads = [
        "",                      # empty file
        "not json at all",       # unparseable
        "[]",                    # wrong top-level type
        '{"plugins": []}',       # wrong plugins type
        '{"plugins": {}}',       # harness not registered
        '{"plugins": {"harness@harness": []}}',        # no records
        '{"plugins": {"harness@harness": [{}]}}',      # record without installPath
    ]
    for index, payload in enumerate(payloads):
        config_dir = tmp_path / f"cfg{index}"
        plugins = config_dir / "plugins"
        plugins.mkdir(parents=True)
        (plugins / "installed_plugins.json").write_text(payload, encoding="utf-8")
        assert mod.receipt_capability_warning(str(config_dir)) == "", payload


def test_missing_config_dir_is_silent(tmp_path):
    assert mod.receipt_capability_warning(str(tmp_path / "absent")) == ""


def test_registered_path_that_does_not_exist_is_silent(tmp_path):
    """Cannot inspect it, so cannot indict it."""
    cfg = _config(tmp_path / "cfg", tmp_path / "gone")
    assert mod.receipt_capability_warning(str(cfg)) == ""


def test_dict_shaped_record_is_accepted(tmp_path):
    """Some configs store a single record rather than a list."""
    root = _tree(tmp_path / "tree", scripts=("prewrite_gate.py",))
    plugins = tmp_path / "cfg" / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "installed_plugins.json").write_text(
        json.dumps({"plugins": {"harness@harness": {"installPath": str(root)}}}),
        encoding="utf-8",
    )
    assert WARNING_TEXT in mod.receipt_capability_warning(
        str(tmp_path / "cfg")
    )


def test_registered_hook_root_reads_install_path(tmp_path):
    cfg = _config(tmp_path / "cfg", "/some/where/2.3.0")
    assert mod.registered_hook_root(str(cfg)) == "/some/where/2.3.0"


def test_warning_never_raises(tmp_path, monkeypatch):
    """Any internal failure degrades to silence, never an exception."""
    def boom(*_args, **_kwargs):
        raise RuntimeError("exploded")

    monkeypatch.setattr(mod, "registered_hook_root", boom)
    assert mod.receipt_capability_warning(str(tmp_path)) == ""


def test_codex_runtime_does_not_indict_stale_claude_registration(tmp_path, monkeypatch):
    """The active Codex watcher is unrelated to Claude's installed hook tree."""
    root = _tree(tmp_path / "stale", scripts=("prewrite_gate.py",))
    cfg = _config(tmp_path / "cfg", root)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setattr(mod, "_codex_registration_present", lambda: True)

    assert mod.receipt_capability_warning() == ""
    assert WARNING_TEXT in mod.receipt_capability_warning(str(cfg))


def test_codex_thread_identity_scopes_no_argument_check(tmp_path, monkeypatch):
    root = _tree(tmp_path / "stale", scripts=("prewrite_gate.py",))
    cfg = _config(tmp_path / "cfg", root)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.delenv("HARNESS_RUNTIME", raising=False)
    monkeypatch.setenv("CODEX_THREAD_ID", "01a04738-80e6-7851-833f-614d59ae3621")
    monkeypatch.setattr(mod, "_codex_registration_present", lambda: True)

    assert mod.receipt_capability_warning() == ""


def test_codex_without_positive_registration_is_not_clean(monkeypatch):
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.setattr(mod, "_codex_registration_present", lambda: False)

    message = mod.receipt_capability_warning()
    assert "not positively confirmed" in message
    assert "Continue substantive review and QA" in message
    assert "task_verify once" in message


def test_codex_mcp_uses_verified_session_hint_without_thread_env(tmp_path, monkeypatch):
    """The MCP host has HARNESS_RUNTIME but no CODEX_THREAD_ID of its own."""
    root = tmp_path / "repo"
    root.mkdir()
    thread_id = "01a04738-80e6-7851-833f-614d59ae3621"
    monkeypatch.setenv("HARNESS_RUNTIME", "codex")
    monkeypatch.delenv("CODEX_THREAD_ID", raising=False)
    monkeypatch.setitem(sys.modules, "_lib", types.SimpleNamespace(
        find_harness_root=lambda _cwd: str(root),
        read_session_hint=lambda _root: thread_id,
    ))
    monkeypatch.setitem(sys.modules, "codex_lifecycle_watcher", types.SimpleNamespace(
        registration_host_live=lambda _root, _thread_id: True,
        registrations=lambda _root: [{"thread_id": thread_id}],
    ))

    assert mod._codex_registration_present() is True
    assert mod.receipt_capability_warning() == ""


def test_explicit_claude_runtime_keeps_registered_tree_check(tmp_path, monkeypatch):
    root = _tree(tmp_path / "stale", scripts=("prewrite_gate.py",))
    cfg = _config(tmp_path / "cfg", root)
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(cfg))
    monkeypatch.setenv("HARNESS_RUNTIME", "claude")

    assert WARNING_TEXT in mod.receipt_capability_warning()
