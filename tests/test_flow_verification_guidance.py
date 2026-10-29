"""Execute delivered verification examples against harmless, isolated resources."""
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys

import pytest

from test_wbs_input_policy import public
import verification_target as targets


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module", params=["claude", "codex"])
def delivered_develop(request, tmp_path_factory):
    """Use the installer builders, without installing into any live runtime."""
    spec = importlib.util.spec_from_file_location("flow_guidance_install", REPO / "install.py")
    installer = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = installer
    spec.loader.exec_module(installer)
    root = tmp_path_factory.mktemp("guidance-payload")
    if request.param == "claude":
        installer._build_claude_payload(root)
        develop = root / "plugin/skills/develop"
    else:
        installer._build_codex_payload(root, root / "final")
        develop = root / "internal-skills/develop"
    for name in ("runtime-smoke.md", "verification-gate.md"):
        expected = (REPO / "plugin/skills/develop" / name).read_text()
        if request.param == "codex":
            expected = expected.replace("${CLAUDE_PLUGIN_ROOT}/skills/", "${HARNESS_PLUGIN_ROOT}/internal-skills/")
            expected = expected.replace("${CLAUDE_PLUGIN_ROOT}", "${HARNESS_PLUGIN_ROOT}")
        assert (develop / name).read_text() == expected
    return develop


def _bash_blocks(text):
    return re.findall(r"```bash\n(.*?)```", text, re.DOTALL)


def _cli_example(develop, kind, binary, command_args="command"):
    section = (develop / "runtime-smoke.md").read_text().split("**CLI projects", 1)[1]
    blocks = _bash_blocks(section)
    # Include the delivered helper itself, rather than a test-authored runner.
    definitions = [block for block in blocks if "smoke_capture()" in block]
    token = {"build": "<build_command>", "help": "--help", "command": "<command>"}[kind]
    if kind == "command" and "<documented_safe_args>" in section:
        token = "<documented_safe_args>"
    example = next(block for block in blocks if token in block and block not in definitions)
    replacements = {
        "<build_command>": f"{shlex.quote(str(binary))} build",
        "<binary_path>": shlex.quote(str(binary)),
        "<documented_safe_args>": command_args,
        "<command>": "command",
    }
    for placeholder, value in replacements.items():
        example = example.replace(placeholder, value)
    return "\n".join([*definitions, example])


@pytest.fixture
def harmless_cli(tmp_path):
    binary = tmp_path / "harmless-cli"
    binary.write_text(
        f"#!{sys.executable}\n"
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "with Path(os.environ['CALL_LOG']).open('a') as log:\n"
        "    log.write(json.dumps(sys.argv[1:]) + '\\n')\n"
        "if '--dry-run' in sys.argv:\n"
        "    print('unsupported --dry-run', file=sys.stderr)\n"
        "    sys.exit(7)\n"
        "for _ in range(int(os.environ.get('OUTPUT_LINES', '1'))):\n"
        "    print('harmless output ' + 'x' * 128)\n"
        "status = int(os.environ.get('COMMAND_STATUS', '0'))\n"
        "sys.exit(0 if '--version' in sys.argv else status)\n"
    )
    binary.chmod(0o700)
    return binary, tmp_path / "calls.jsonl"


def _run_cli(develop, harmless_cli, kind, *, status=0, lines=1, command_args="command", pipefail=False):
    binary, log = harmless_cli
    result = subprocess.run(
        ["bash", "-e", "-u", *(["-o", "pipefail"] if pipefail else []), "-c", _cli_example(develop, kind, binary, command_args)],
        cwd=binary.parent, env=dict(os.environ, CALL_LOG=str(log), COMMAND_STATUS=str(status), OUTPUT_LINES=str(lines)),
        capture_output=True, text=True, timeout=15,
    )
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    return result, calls


@pytest.mark.parametrize("kind", ["build", "help", "command"])
def test_delivered_cli_examples_preserve_original_nonzero_status(delivered_develop, harmless_cli, kind):
    result, calls = _run_cli(delivered_develop, harmless_cli, kind, status=7)
    assert result.returncode == 7, (result.returncode, result.stdout, result.stderr, calls)
    assert len(calls) == 1  # A failed --help cannot be hidden by a successful --version.


@pytest.mark.parametrize("kind", ["build", "help", "command"])
def test_delivered_cli_examples_allow_long_successful_output(delivered_develop, harmless_cli, kind):
    result, _ = _run_cli(delivered_develop, harmless_cli, kind, lines=20000, pipefail=True)
    assert result.returncode == 0, result.stderr
    assert 0 < len(result.stdout.splitlines()) <= 40


def test_unsupported_dry_run_never_falls_back_to_live_command(delivered_develop, harmless_cli):
    result, calls = _run_cli(delivered_develop, harmless_cli, "command", command_args="command --dry-run")
    assert result.returncode == 7, (result.stdout, result.stderr, calls)
    assert calls == [["command", "--dry-run"]]


def _issued_git_target(tmp_path, monkeypatch):
    repo, task_dir, _, _ = public(tmp_path)
    (repo / "notes.md").write_text("user notes\n")
    subprocess.run(["git", "add", ".gitignore", "src.txt", "notes.md"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture"], cwd=repo, check=True)
    monkeypatch.setenv("FLOW_GUIDANCE_CONTEXT", "original")
    command = [sys.executable, "-B", "-c", "from pathlib import Path; assert Path('src.txt').read_text() == 'ok'; print('frozen QA PASS')"]
    target = targets.issue_target(str(task_dir), str(repo), dict(
        inputs={"source": ["src.txt"]}, commands=[command], lenses=["review-code", "qa-cli"],
        environment=["FLOW_GUIDANCE_CONTEXT"],
    ))
    return repo, task_dir, target


def test_frozen_qa_checks_selected_inputs_despite_dirty_parent_git(delivered_develop, tmp_path, monkeypatch):
    repo, task_dir, target = _issued_git_target(tmp_path, monkeypatch)
    notes = repo / "notes.md"
    notes.write_text("uncommitted user work\n")
    frozen = Path(target["snapshot_root"])
    # An ordinary Git invocation really does escape the issued source view.
    parent = subprocess.check_output(["git", "rev-parse", "--show-toplevel"], cwd=frozen, text=True).strip()
    assert Path(parent) == repo
    assert "notes.md" in subprocess.check_output(["git", "status", "--porcelain"], cwd=frozen, text=True)
    assert targets.ensure_snapshot(str(task_dir), target["token"], str(repo))["snapshot_root"] == str(frozen)
    assert targets.applicability(str(task_dir), str(repo), measure=True)["status"] == "applicable"
    result = subprocess.run(target["spec"]["commands"][0], cwd=frozen, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "frozen QA PASS"
    assert notes.read_text() == "uncommitted user work\n"
    step_zero = (delivered_develop / "verification-gate.md").read_text().split("## Step 0.5", 1)[0]
    assert "snapshot_root" in step_zero and "manifest" in step_zero and "context" in step_zero
    assert not any("git status" in block for block in _bash_blocks(step_zero))
    assert "integration" in step_zero.lower()


@pytest.mark.parametrize("drift", ["source", "context", "missing-target"])
def test_real_issued_target_still_blocks_relevant_drift_or_missing_target(tmp_path, monkeypatch, drift):
    repo, task_dir, target = _issued_git_target(tmp_path, monkeypatch)
    if drift == "source":
        (repo / "src.txt").write_text("relevant change")
    elif drift == "context":
        monkeypatch.setenv("FLOW_GUIDANCE_CONTEXT", "changed")
    else:
        (Path(target["snapshot_root"]).parent / "target.json").unlink()
        with pytest.raises((OSError, ValueError)):
            targets.load_target(str(task_dir), target["token"])
    state = targets.applicability(str(task_dir), str(repo), measure=True)
    assert state["status"] == "stale"
    assert state["missing_lenses"] == ["qa-cli", "review-code"]
