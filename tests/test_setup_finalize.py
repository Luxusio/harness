import os
import importlib.util
import shutil
import stat
import subprocess
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin/scripts/setup_finalize.py"
SETUP_SOURCE = REPO / "plugin/skills/setup"


def make_plugin_root(tmp_path: Path) -> Path:
    root = tmp_path / "plugin"
    for rel in (
        "skills/run/SKILL.md",
        "skills/setup/SKILL.md",
        "skills/setup/repo-census.md",
        "skills/setup/project-interview.md",
        "skills/setup/bootstrap.md",
        "skills/setup/verify-report.md",
        "skills/setup/templates/CONTRACTS.md",
        "skills/setup/templates/CONTRACTS.local.md",
        "skills/setup/templates/hygiene.yaml",
        "scripts/contract_lint.py",
        "scripts/goal_queue_migrate.py",
        "scripts/setup_finalize.py",
    ):
        source = REPO / "plugin" / rel
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    run_policy = root / "skills/run/agents/openai.yaml"
    run_policy.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(REPO / "plugin-codex/skills/run/agents/openai.yaml", run_policy)
    return root


def make_repo(tmp_path: Path, *, manifest: str, project_doc: str = "AGENTS.md") -> Path:
    repo = tmp_path / "repo"
    critics = repo / "doc/harness/critics"
    critics.mkdir(parents=True)
    (repo / "doc/harness/manifest.yaml").write_text(manifest, encoding="utf-8")
    for name in ("plan.md", "runtime.md", "document.md"):
        lens = name.removesuffix(".md")
        (critics / name).write_text(
            f"# {lens} critic project playbook\n- Verify {lens} behavior.\n", encoding="utf-8"
        )
    shutil.copy2(REPO / "plugin/skills/setup/templates/CONTRACTS.md", repo / "CONTRACTS.md")
    shutil.copy2(REPO / "plugin/skills/setup/templates/CONTRACTS.local.md", repo / "CONTRACTS.local.md")
    (repo / project_doc).write_text(
        "@CONTRACTS.md\n<!-- harness:routing-injected -->\n"
        "Repository mutation -> invoke $harness:run before editing.\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    return repo


def run(
    repo: Path,
    plugin_root: Path,
    *extra: str,
    project_doc: str = "AGENTS.md",
    attest: bool = True,
):
    attestations = []
    if attest and not {"--check", "--prepare", "--gitignore-only"}.intersection(extra):
        attestations = ["--qa-verified", "--runtime-verified"]
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo", str(repo), "--plugin-root", str(plugin_root),
         "--project-doc", project_doc, *extra, *attestations],
        text=True,
        capture_output=True,
        timeout=20,
    )


def canonical_manifest(version: int = 5) -> str:
    return (
        f"version: {version}\n"
        "initialized_at: 2026-07-20\n"
        "name: demo\n"
        "type: api\n"
        "test_command: pytest\n"
        "qa:\n"
        "  default_mode: api\n"
        "  browser_qa_supported: false\n"
    )


def test_fresh_setup_ignores_all_operational_artifacts_and_stamps_version(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())

    result = run(repo, plugin_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / "doc/harness/.version").read_text() == "2.3.0\n"
    for rel in (
        "doc/harness/goals/current.json",
        "doc/harness/tasks/TASK__probe/STATE.json",
        "doc/harness/task-packs/current.json",
        "doc/harness/reviews/main-reviews.jsonl",
        "doc/harness/debug/goal-hook-payloads/prompt.json",
        "doc/harness/maintenance/probe.json",
        "doc/harness/archive/probe.json",
        "doc/harness/.routing-state.json",
        "doc/harness/timeline.jsonl",
        "doc/harness/health-history.jsonl",
        "doc/harness/benchmark/probe.json",
        "doc/harness/audits/probe.json",
        "doc/harness/quality-trend.jsonl",
    ):
        check = subprocess.run(["git", "-C", str(repo), "check-ignore", "-q", "--no-index", rel])
        assert check.returncode == 0, rel

    second = run(repo, plugin_root)
    assert second.returncode == 0


def test_codex_finalize_rejects_missing_public_run_policy(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    (plugin_root / "skills/run/agents/openai.yaml").unlink()
    repo = make_repo(tmp_path, manifest=canonical_manifest())

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "skills/run/agents/openai.yaml" in result.stdout


def test_codex_finalize_rejects_disabled_implicit_run_policy(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    (plugin_root / "skills/run/agents/openai.yaml").write_text(
        "policy:\n  allow_implicit_invocation: false\n", encoding="utf-8"
    )
    repo = make_repo(tmp_path, manifest=canonical_manifest())

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "policy.allow_implicit_invocation: true" in result.stdout


def test_codex_finalize_rejects_nested_implicit_run_policy(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    (plugin_root / "skills/run/agents/openai.yaml").write_text(
        "policy:\n  nested:\n    allow_implicit_invocation: true\n", encoding="utf-8"
    )
    repo = make_repo(tmp_path, manifest=canonical_manifest())

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "policy.allow_implicit_invocation: true" in result.stdout


def test_codex_finalize_rejects_duplicate_implicit_run_policy(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    (plugin_root / "skills/run/agents/openai.yaml").write_text(
        "policy:\n"
        "  allow_implicit_invocation: true\n"
        "  allow_implicit_invocation: false\n",
        encoding="utf-8",
    )
    repo = make_repo(tmp_path, manifest=canonical_manifest())

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "policy.allow_implicit_invocation: true" in result.stdout


def test_codex_finalize_rejects_marker_without_public_run_route(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    (repo / "AGENTS.md").write_text(
        "@CONTRACTS.md\n<!-- harness:routing-injected -->\nRead-only help is direct.\n",
        encoding="utf-8",
    )

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "route repository mutation to $harness:run" in result.stdout


def test_prepare_migrates_legacy_manifest_but_does_not_stamp(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(
        tmp_path,
        manifest=(
            "project: demo\nproject_type: api\nharness_version: 2\n"
            "browser_qa_supported: true\ntest_command: pytest\n"
            "custom_field: keep-me\ncreated: 2026-01-01\n"
        ),
    )

    result = run(repo, plugin_root, "--prepare")

    assert result.returncode == 0, result.stdout + result.stderr
    body = (repo / "doc/harness/manifest.yaml").read_text()
    assert "version: 5" in body and "name: demo" in body and "type: api" in body
    assert "project_type:" not in body and "harness_version:" not in body
    assert "custom_field: keep-me" in body
    assert "qa:\n  default_mode: browser\n  browser_qa_supported: true" in body
    assert not (repo / "doc/harness/.version").exists()


def test_future_schema_and_legacy_collision_fail_without_mutation(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    future = make_repo(tmp_path / "future", manifest=canonical_manifest(6))
    before = (future / "doc/harness/manifest.yaml").read_text()
    result = run(future, plugin_root)
    assert result.returncode == 1
    assert "newer than supported schema 5" in result.stdout
    assert (future / "doc/harness/manifest.yaml").read_text() == before
    assert not (future / "doc/harness/.version").exists()

    collision = make_repo(
        tmp_path / "collision",
        manifest="project: old\nname: new\nproject_type: api\ntype: api\ntest_command: pytest\n",
    )
    before = (collision / "doc/harness/manifest.yaml").read_text()
    result = run(collision, plugin_root)
    assert result.returncode == 1
    assert "both legacy project and canonical name" in result.stdout
    assert (collision / "doc/harness/manifest.yaml").read_text() == before


def test_ambiguous_yaml_and_missing_contract_import_are_rejected(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(
        tmp_path,
        manifest=canonical_manifest() + "qa:\n  browser_qa_supported: true\n",
    )
    (repo / "AGENTS.md").write_text("<!-- harness:routing-injected -->\n")

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "manifest duplicate qa section" in result.stdout
    assert "AGENTS.md is missing @CONTRACTS.md import" in result.stdout
    assert not (repo / "doc/harness/.version").exists()


def test_placeholder_critic_and_noncanonical_qa_indentation_are_rejected(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    manifest = canonical_manifest().replace(
        "  browser_qa_supported: false", "    browser_qa_supported: false"
    )
    repo = make_repo(tmp_path, manifest=manifest)
    (repo / "doc/harness/critics/plan.md").write_text("# placeholder\n")

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "manifest qa.browser_qa_supported is missing" in result.stdout
    assert "plan.md is a placeholder" in result.stdout


def test_runtime_top_level_reader_does_not_accept_nested_type(tmp_path):
    sys.path.insert(0, str(REPO / "plugin/scripts"))
    try:
        import _lib

        repo = tmp_path / "repo"
        manifest = repo / "doc/harness/manifest.yaml"
        manifest.parent.mkdir(parents=True)
        manifest.write_text("metadata:\n  type: cli\ntype: api\n")
        assert _lib._read_top_manifest_field(str(repo), "type") == "api"
    finally:
        sys.path.pop(0)


def test_empty_verify_item_comment_import_and_missing_attestations_are_rejected(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(
        tmp_path,
        manifest=canonical_manifest().replace("test_command: pytest\n", "verify_commands:\n  - \n"),
    )
    (repo / "AGENTS.md").write_text(
        "# @CONTRACTS.md intentionally disabled\n<!-- harness:routing-injected -->\n"
    )
    result = run(repo, plugin_root)
    assert result.returncode == 1
    assert "requires test_command or a non-empty verify_commands list" in result.stdout
    assert "missing @CONTRACTS.md import" in result.stdout

    valid = make_repo(tmp_path / "attestation", manifest=canonical_manifest())
    result = run(valid, plugin_root, attest=False)
    assert result.returncode == 1
    assert "finalization requires --qa-verified" in result.stdout
    assert "finalization requires --runtime-verified" in result.stdout
    assert not (valid / "doc/harness/.version").exists()


def test_missing_resource_or_contract_does_not_stamp_or_mutate_manifest(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    (plugin_root / "skills/setup/bootstrap.md").unlink()
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    (repo / "CONTRACTS.local.md").unlink()
    (repo / "doc/harness/.version").write_text("2.2.0\n")
    before = (repo / "doc/harness/manifest.yaml").read_text()

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "installed setup resource is missing or empty: skills/setup/bootstrap.md" in result.stdout
    assert "CONTRACTS.local.md is missing or empty" in result.stdout
    assert (repo / "doc/harness/.version").read_text() == "2.2.0\n"
    assert (repo / "doc/harness/manifest.yaml").read_text() == before
    assert not (repo / ".gitignore").exists()


def test_broad_negation_is_overridden_and_tracked_artifact_is_blocked(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    original = (
        "doc/harness/debug/goal-hook-payloads/\n"
        "!doc/harness/debug/goal-hook-payloads/\n"
        "!doc/harness/debug/goal-hook-payloads/**\n"
    )
    (repo / ".gitignore").write_text(original)

    result = run(repo, plugin_root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert (repo / ".gitignore").read_text().splitlines()[-1] == "doc/harness/.maintain-pending.json"

    tracked_repo = make_repo(tmp_path / "tracked", manifest=canonical_manifest())
    payload = tracked_repo / "doc/harness/debug/goal-hook-payloads/tracked.json"
    payload.parent.mkdir(parents=True)
    payload.write_text("secret")
    subprocess.run(
        ["git", "-C", str(tracked_repo), "add", "-f", str(payload.relative_to(tracked_repo))],
        check=True,
    )
    result = run(tracked_repo, plugin_root)
    assert result.returncode == 1
    assert "operational artifact is already tracked" in result.stdout


def test_selective_payload_negation_is_overridden_by_final_managed_block(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    (repo / ".gitignore").write_text(
        "doc/harness/debug/goal-hook-payloads/\n"
        "!doc/harness/debug/goal-hook-payloads/\n"
        "doc/harness/debug/goal-hook-payloads/*\n"
        "!doc/harness/debug/goal-hook-payloads/codex_*\n"
    )
    payload = repo / "doc/harness/debug/goal-hook-payloads/codex_UserPromptSubmit__secret.json"
    payload.parent.mkdir(parents=True)
    payload.write_text("secret")

    result = run(repo, plugin_root)

    assert result.returncode == 0, result.stdout + result.stderr
    check = subprocess.run(
        ["git", "-C", str(repo), "check-ignore", "-q", "--no-index", str(payload.relative_to(repo))]
    )
    assert check.returncode == 0
    lines = (repo / ".gitignore").read_text().splitlines()
    assert lines[-1] == "doc/harness/.maintain-pending.json"
    assert lines.count("doc/harness/debug/goal-hook-payloads/") == 1


def test_check_is_read_only_and_atomic_write_preserves_modes(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    result = run(repo, plugin_root, "--check")
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert not (repo / ".gitignore").exists()

    (repo / ".gitignore").write_text("# user\n")
    os.chmod(repo / ".gitignore", 0o644)
    os.chmod(repo / "doc/harness/manifest.yaml", 0o640)
    result = run(repo, plugin_root)
    assert result.returncode == 0, result.stdout + result.stderr
    assert stat.S_IMODE((repo / ".gitignore").stat().st_mode) == 0o644
    assert stat.S_IMODE((repo / "doc/harness/manifest.yaml").stat().st_mode) == 0o640
    assert stat.S_IMODE((repo / "doc/harness/.version").stat().st_mode) == 0o644


def test_symlinked_managed_directory_is_rejected(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = tmp_path / "repo"
    outside = tmp_path / "outside"
    outside.mkdir()
    repo.mkdir()
    (repo / "doc").mkdir()
    (repo / "doc/harness").symlink_to(outside, target_is_directory=True)
    subprocess.run(["git", "init", "-q", str(repo)], check=True)

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "managed path contains symlink: doc/harness" in result.stdout
    assert not (outside / ".version").exists()


def test_exact_operational_file_symlink_is_rejected(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    outside = tmp_path / "outside.json"
    outside.write_text("{}")
    (repo / "doc/harness/goal-queue.json").symlink_to(outside)

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "managed path contains symlink: doc/harness/goal-queue.json" in result.stdout
    assert not (repo / "doc/harness/.version").exists()


def test_unexpected_second_write_failure_rolls_back_first_write(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("setup_finalize_under_test", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    (repo / ".gitignore").write_text("# user\n")
    original_gitignore = (repo / ".gitignore").read_text()
    original_manifest = (repo / "doc/harness/manifest.yaml").read_text()
    real_write = module.atomic_write
    calls = 0

    def fail_second(path, text, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated manifest replace failure")
        return real_write(path, text, **kwargs)

    monkeypatch.setattr(module, "atomic_write", fail_second)
    rc = module.main([
        "--repo", str(repo), "--plugin-root", str(plugin_root),
        "--project-doc", "AGENTS.md", "--qa-verified", "--runtime-verified",
    ])
    assert rc == 1
    assert (repo / ".gitignore").read_text() == original_gitignore
    assert (repo / "doc/harness/manifest.yaml").read_text() == original_manifest


def test_codex_installed_mirror_prepare_and_finalize_end_to_end(tmp_path):
    install_spec = importlib.util.spec_from_file_location("install_for_setup_e2e", REPO / "install.py")
    assert install_spec and install_spec.loader
    installer = importlib.util.module_from_spec(install_spec)
    sys.modules[install_spec.name] = installer
    install_spec.loader.exec_module(installer)
    home = tmp_path / "home"
    mirror = installer.sync_codex_payload(home / ".codex/harness")
    assert (mirror / ".codex-plugin/plugin.json").is_file()
    assert (mirror / "skills/setup/bootstrap.md").is_file()

    repo = make_repo(tmp_path, manifest=canonical_manifest())
    ignore = run(repo, mirror, "--gitignore-only")
    prepared = run(repo, mirror, "--prepare")
    assert ignore.returncode == 0, ignore.stdout + ignore.stderr
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    assert not (repo / "doc/harness/.version").exists()

    finalized = run(repo, mirror)
    assert finalized.returncode == 0, finalized.stdout + finalized.stderr
    assert (repo / "doc/harness/.version").read_text() == "2.3.0\n"


def test_canonical_setup_resources_exist_in_source_tree():
    for rel in (
        "repo-census.md", "project-interview.md", "bootstrap.md", "verify-report.md",
        "templates/CONTRACTS.md", "templates/CONTRACTS.local.md", "templates/hygiene.yaml",
    ):
        assert (SETUP_SOURCE / rel).is_file(), rel
