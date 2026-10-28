"""Health CLI previews must not execute manifest commands."""
from pathlib import Path
import os
import shlex
import subprocess
import sys

try:
    import pytest
except ModuleNotFoundError as error:
    if error.name != "pytest":
        raise
    from unittest import SkipTest
    raise SkipTest("pytest is required to collect this module")


HEALTH = Path(__file__).resolve().parents[1] / "plugin/scripts/health.py"


def invoke(repo, *args):
    return subprocess.run(
        [sys.executable, "-B", str(HEALTH), *args], cwd=repo / "nested",
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True, text=True, timeout=30,
    )


def configure(repo, manifest):
    (repo / ".git").mkdir()
    (repo / "nested").mkdir()
    path = repo / "doc/harness/manifest.yaml"
    path.parent.mkdir(parents=True)
    path.write_text(manifest)
    (repo / "sentinel.py").write_text(
        "from pathlib import Path\nimport sys\n"
        "Path(sys.argv[1]).write_text('ran')\nraise SystemExit(int(sys.argv[2]))\n"
    )


def sentinel_command(name, status=0):
    return f"{shlex.quote(sys.executable)} sentinel.py {name} {status}"


@pytest.mark.parametrize("fallback", [False, True])
def test_dry_run_lists_checks_without_executing_then_manual_run_executes(tmp_path, fallback):
    command = sentinel_command("executed")
    manifest = (
        f'test_command: "{command}"\n' if fallback else
        f'health_components:\n  - name: sentinel\n    command: "{command}"\n    weight: 2\n'
        'test_command: "touch fallback-must-not-run"\n'
    )
    configure(tmp_path, manifest)

    preview = invoke(tmp_path, "--dry-run")
    assert preview.returncode == 0, preview.stderr
    assert command in preview.stdout
    assert ("test" if fallback else "sentinel") in preview.stdout
    assert "weight=" in preview.stdout
    assert "health:" not in preview.stdout
    assert "PASS" not in preview.stdout and "FAIL" not in preview.stdout
    assert not (tmp_path / "executed").exists()
    assert not (tmp_path / "fallback-must-not-run").exists()

    measured = invoke(tmp_path)
    assert measured.returncode == 0, measured.stderr
    assert "health: 10.0/10" in measured.stdout and "PASS" in measured.stdout
    assert (tmp_path / "executed").read_text() == "ran"
    assert not (tmp_path / "nested/executed").exists()
    assert not (tmp_path / "fallback-must-not-run").exists()


def test_manual_run_scores_failed_component_with_existing_success_exit(tmp_path):
    configure(tmp_path, (
        'health_components:\n'
        f'  - name: good\n    command: "{sentinel_command("good")}"\n    weight: 1\n'
        f'  - name: bad\n    command: "{sentinel_command("bad", 7)}"\n    weight: 3\n'
    ))
    measured = invoke(tmp_path)
    assert measured.returncode == 0, measured.stderr
    assert "health: 2.5/10" in measured.stdout
    assert "PASS" in measured.stdout and "FAIL" in measured.stdout
    assert (tmp_path / "good").read_text() == (tmp_path / "bad").read_text() == "ran"


@pytest.mark.parametrize("args", [(), ("--dry-run",)])
def test_no_config_keeps_note_and_exit_one(tmp_path, args):
    configure(tmp_path, "type: cli\n")
    result = invoke(tmp_path, *args)
    assert result.returncode == 1
    assert "NOTE: no health_components and no test_command" in result.stdout
    assert "health:" not in result.stdout
