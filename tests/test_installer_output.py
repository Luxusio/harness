"""Compact feedback retains actionable failures; verbose retains the full trace."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module") from error


@pytest.fixture
def installer():
    spec = importlib.util.spec_from_file_location(
        "installer_output", Path(__file__).resolve().parents[1] / "install.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def run_main(installer, monkeypatch, capsys, results, *args, available=("codex", "claude")):
    calls = {}
    monkeypatch.setattr(sys, "argv", ["install.py", *args])
    monkeypatch.setattr(installer.shutil, "which", lambda name: f"/bin/{name}" if name in available else None)
    for runtime, result in results.items():
        def install(*, _runtime=runtime, _result=result, **kwargs):
            calls[_runtime] = kwargs
            if isinstance(_result, Exception):
                raise _result
            return _result
        monkeypatch.setattr(installer, f"install_{runtime}", install)
    rc = installer.main()
    return rc, capsys.readouterr(), calls


@pytest.mark.parametrize("verbose", [False, True])
def test_success_details_are_opt_in(installer, monkeypatch, capsys, verbose):
    results = {
        "codex": installer.InstallResult("codex", True, "Codex install complete", ["codex step"], "/backup"),
        "claude": installer.InstallResult(
            "claude", True,
            "Claude payload SYNCHRONIZED — install skipped (config/registry health not checked)",
            ["claude step"],
        ),
    }
    rc, captured, calls = run_main(installer, monkeypatch, capsys, results, *( ["--verbose"] if verbose else []))
    assert rc == 0 and not captured.err
    assert set(calls) == {"codex", "claude"}
    assert calls["codex"] == {"dry_run": False, "force": False, "config_path": None}
    assert calls["claude"] == {"dry_run": False, "force": False}
    assert "Codex: SUCCESS" in captured.out
    assert "Claude: SUCCESS" in captured.out
    for detail in ("repo root:", "codex step", "claude step", "backup: /backup"):
        assert (detail in captured.out) == verbose
    if not verbose:
        assert len(captured.out.splitlines()) == 2


def test_cache_refresh_remains_applied(installer, monkeypatch, capsys):
    result = installer.InstallResult("claude", True, "Claude payload SYNCHRONIZED — plugin cache refreshed (config/registry health not checked)")
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"claude": result}, "--claude-only")
    assert rc == 0
    assert captured.out == "Claude: SUCCESS\n"


@pytest.mark.parametrize("verbose", [False, True])
def test_dry_run_explains_apply_and_full_plan(installer, monkeypatch, capsys, verbose):
    result = installer.InstallResult("codex", True, "dry-run — would install Codex (steps above)", ["would sync plugin payload to /target"])
    rc, captured, calls = run_main(
        installer, monkeypatch, capsys, {"codex": result},
        "--codex-only", "--dry-run", "--force", "--config-path", "/test/config.toml",
        *(["--verbose"] if verbose else []),
    )
    assert rc == 0
    assert calls["codex"] == {"dry_run": True, "force": True, "config_path": "/test/config.toml"}
    assert "Codex: DRY RUN" in captured.out
    assert "SUCCESS" not in captured.out
    assert ("would sync plugin payload" in captured.out) == verbose
    assert "steps above" not in captured.out


@pytest.mark.parametrize("verbose", [False, True])
def test_partial_failure_keeps_cause_and_retry(installer, monkeypatch, capsys, verbose):
    results = {
        "codex": installer.InstallResult("codex", True, "Codex install complete", ["ordinary step"]),
        "claude": installer.InstallResult("claude", False, "marketplace failed: permission denied", ["ordinary step"]),
    }
    rc, captured, calls = run_main(installer, monkeypatch, capsys, results, *(["--verbose"] if verbose else []))
    assert rc == 1 and len(calls) == 2
    assert "Codex: SUCCESS" in captured.out
    assert "Claude: FAILED" in captured.out
    assert "Error: marketplace failed: permission denied" in captured.out
    assert ("python3 install.py --claude-only --force" in captured.out) == verbose
    assert not captured.err
    assert ("ordinary step" in captured.out) == verbose


@pytest.mark.parametrize("verbose", [False, True])
def test_unsafe_remedy_is_not_replaced_by_force(installer, monkeypatch, capsys, verbose):
    repair = "chmod go-w '/home/user/my config'"
    summary = f"unsafe payload path component: /home/user/my config; fix: {repair}"
    result = installer.InstallResult("claude", False, summary, repair=repair)
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"claude": result}, "--claude-only", *(["--verbose"] if verbose else []))
    assert rc == 1
    assert "unsafe payload path component: /home/user/my config" in captured.out
    assert (f"repair: {repair}, then re-run: python3 install.py --claude-only" in captured.out) == verbose
    assert "--force" not in captured.out
    assert captured.out.count(repair) == (2 if verbose else 1)


@pytest.mark.parametrize("verbose", [False, True])
def test_recovery_precedes_repair_and_retains_verification_order(installer, monkeypatch, capsys, verbose):
    recovery = "repair daemon; verify: codex app-server daemon version; only after it succeeds, retry: python3 install.py --codex-only --force --config-path '/my config.toml'"
    result = installer.InstallResult("codex", False, "daemon unavailable", repair="ignored repair", recovery=recovery)
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"codex": result}, "--codex-only", *(["--verbose"] if verbose else []))
    assert rc == 1
    assert (f"recovery: {recovery}" in captured.out) == verbose
    assert "ignored repair" not in captured.out


@pytest.mark.parametrize("verbose", [False, True])
def test_smoke_failures_keep_actual_probe_diagnostics(installer, monkeypatch, capsys, verbose):
    result = installer.InstallResult("claude", False, "installed Claude runtime failed its smoke test — hooks cannot record receipts from this tree", ["ordinary sync step", "runtime smoke: could not run /probe: Permission denied", "runtime smoke: failing registration details"])
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"claude": result}, "--claude-only", *(["--verbose"] if verbose else []))
    assert rc == 1
    assert "failed its smoke test" in captured.out
    assert ("runtime smoke: could not run /probe: Permission denied" in captured.out) == verbose
    assert ("runtime smoke: failing registration details" in captured.out) == verbose
    assert ("ordinary sync step" in captured.out) == verbose


def test_worker_exception_is_an_error(installer, monkeypatch, capsys):
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"codex": RuntimeError("broken pipe")}, "--codex-only")
    assert rc == 1 and "Codex: FAILED" in captured.out and "broken pipe" in captured.out


@pytest.mark.parametrize("verbose", [False, True])
def test_auto_detection_keeps_missing_runtime_notice(installer, monkeypatch, capsys, verbose):
    result = installer.InstallResult("codex", True, "Codex install complete")
    rc, captured, calls = run_main(installer, monkeypatch, capsys, {"codex": result}, *(["--verbose"] if verbose else []), available=("codex",))
    assert rc == 0 and set(calls) == {"codex"}
    assert ("skipping: claude (claude CLI not found in PATH)" in captured.out) == verbose


def test_no_runtimes_and_conflicting_flags_remain_usage_errors(installer, monkeypatch, capsys):
    rc, captured, calls = run_main(installer, monkeypatch, capsys, {}, available=())
    assert rc == 2 and not calls
    assert "codex CLI not found" in captured.err and "claude CLI not found" in captured.err
    rc, captured, calls = run_main(installer, monkeypatch, capsys, {}, "--codex-only", "--claude-only")
    assert rc == 2 and not calls and "mutually exclusive" in captured.err


@pytest.mark.parametrize("runtime", ["claude", "codex"])
@pytest.mark.parametrize("restart", [False, True])
def test_restart_guidance_names_changed_runtime(installer, monkeypatch, capsys, runtime, restart):
    result = installer.InstallResult(runtime, True, "complete", restart_required=restart)
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {runtime: result}, f"--{runtime}-only")
    assert rc == 0
    expected = "Restart Claude Code" if runtime == "claude" else "Start a new Codex session"
    assert (expected in captured.out) == restart
    assert ("Codex session" if runtime == "claude" else "Claude Code") not in captured.out


def test_custom_config_artifact_guidance_is_visible_without_verbose(installer, monkeypatch, capsys):
    note = "Custom Codex config artifact installed at /tmp/custom.toml; running sessions are unchanged. Merge it into /tmp/config.toml before starting a new session."
    result = installer.InstallResult("codex", True, "complete", activation_note=note)
    rc, captured, _ = run_main(installer, monkeypatch, capsys, {"codex": result}, "--codex-only")
    assert rc == 0 and note in captured.out
    assert "Start a new Codex session" not in captured.out
