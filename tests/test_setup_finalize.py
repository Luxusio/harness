import os
import importlib.util
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from unittest import mock


REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "plugin/scripts/setup_finalize.py"
SETUP_SOURCE = REPO / "plugin/skills/setup"


def load_setup_finalize(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


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


def test_project_doc_helper_inserts_import_and_preserves_content(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin_root = make_plugin_root(tmp_path)
    project_doc = repo / "AGENTS.md"
    project_doc.write_text(
        "---\nname: pay\n---\n# Existing\nkeep me\n", encoding="utf-8"
    )

    result = run(
        repo,
        plugin_root,
        "--project-doc-only",
        "--ensure-contract-import",
        attest=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert project_doc.read_text(encoding="utf-8") == (
        "---\nname: pay\n---\n@CONTRACTS.md\n# Existing\nkeep me\n"
    )


def test_project_doc_helper_rejects_symlink(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    plugin_root = make_plugin_root(tmp_path)
    external = tmp_path / "external.md"
    external.write_text("# Outside\n", encoding="utf-8")
    (repo / "AGENTS.md").symlink_to(external)

    result = run(
        repo,
        plugin_root,
        "--project-doc-only",
        "--ensure-contract-import",
        attest=False,
    )

    assert result.returncode == 1
    assert "symlink" in result.stdout
    assert external.read_text(encoding="utf-8") == "# Outside\n"


def test_project_doc_import_preserves_no_newline_boundaries(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_no_newline_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"

    project_doc.write_text("# Existing", encoding="utf-8")
    setup_finalize.update_project_doc(
        repo, "AGENTS.md",
        ensure_routing=False, ensure_contract_import=True,
    )
    assert project_doc.read_text(encoding="utf-8") == (
        "# Existing\n@CONTRACTS.md\n"
    )

    project_doc.write_text("---\r\nname: pay\r\n---", encoding="utf-8", newline="")
    setup_finalize.update_project_doc(
        repo, "AGENTS.md",
        ensure_routing=False, ensure_contract_import=True,
    )
    assert project_doc.read_bytes() == (
        b"---\r\nname: pay\r\n---\r\n@CONTRACTS.md\r\n"
    )


def test_project_doc_routing_preserves_unmanaged_crlf_content(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_crlf_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_bytes(
        b"# User\r\nkeep-before\r\n\r\n"
        b"## Harness routing\r\n<!-- harness:routing-injected -->\r\nold\r\n"
        b"## User Tail\r\nkeep-after\r\n"
    )

    setup_finalize.update_project_doc(
        repo, "AGENTS.md",
        ensure_routing=True, ensure_contract_import=False,
    )
    result = project_doc.read_bytes()
    assert result.startswith(b"# User\r\nkeep-before\r\n\r\n")
    assert result.endswith(b"## User Tail\r\nkeep-after\r\n")
    assert b"$harness:run" in result
    assert b"\n" not in result.replace(b"\r\n", b"")


def test_project_doc_routing_preserves_unmarked_same_name_section(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_unmarked_routing_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text(
        "# User\n\n## Harness routing\n"
        "This heading and its user-owned instructions must remain.\n"
        "## User Tail\nkeep-after\n",
        encoding="utf-8",
    )

    setup_finalize.update_project_doc(
        repo, "AGENTS.md",
        ensure_routing=True, ensure_contract_import=False,
    )
    result = project_doc.read_text(encoding="utf-8")
    assert (
        "## Harness routing\n"
        "This heading and its user-owned instructions must remain.\n"
        "## User Tail\nkeep-after\n"
    ) in result
    assert result.count("<!-- harness:routing-injected -->") == 1
    assert "$harness:run" in result


def test_project_doc_routing_preserves_user_rule_after_legacy_eof_block(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_legacy_eof_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text(
        "## Harness routing\n"
        "<!-- harness:routing-injected -->\n"
        "- old generated rule\n"
        "- user rule appended at EOF\n",
        encoding="utf-8",
    )
    setup_finalize.update_project_doc(
        repo, "AGENTS.md",
        ensure_routing=True, ensure_contract_import=False,
    )
    result = project_doc.read_text(encoding="utf-8")
    assert "- user rule appended at EOF\n" in result
    assert "<!-- /harness:routing-injected -->" in result


def test_project_doc_update_aborts_on_same_inode_concurrent_edit(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_concurrent_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text("# Existing\n", encoding="utf-8")
    original_reader = setup_finalize._project_doc_text
    calls = 0

    def concurrent_reader(path):
        nonlocal calls
        calls += 1
        if calls == 3:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("concurrent user line\n")
        return original_reader(path)

    with mock.patch.object(
        setup_finalize, "_project_doc_text", side_effect=concurrent_reader
    ):
        try:
            setup_finalize.update_project_doc(
                repo, "AGENTS.md",
                ensure_routing=False, ensure_contract_import=True,
            )
        except ValueError as exc:
            assert "content changed" in str(exc)
        else:
            raise AssertionError("concurrent project-document edits must abort")

    assert project_doc.read_text(encoding="utf-8") == (
        "# Existing\nconcurrent user line\n"
    )


def test_project_doc_atomic_exchange_restores_concurrent_replacement(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_exchange_race_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text("# Existing\n", encoding="utf-8")
    real_exchange = setup_finalize._exchange_project_doc
    exchanged = False

    def racing_exchange(left, right):
        nonlocal exchanged
        if not exchanged:
            exchanged = True
            Path(right).write_text("# Concurrent replacement\n", encoding="utf-8")
        return real_exchange(left, right)

    with mock.patch.object(
        setup_finalize, "_exchange_project_doc", side_effect=racing_exchange
    ):
        try:
            setup_finalize.update_project_doc(
                repo, "AGENTS.md",
                ensure_routing=False, ensure_contract_import=True,
            )
        except ValueError as exc:
            assert "changed during setup" in str(exc)
        else:
            raise AssertionError("concurrent replacement must abort")
    assert project_doc.read_text(encoding="utf-8") == "# Concurrent replacement\n"


def test_project_doc_exchange_read_failure_rolls_back_original(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_exchange_read_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text("# Original\n", encoding="utf-8")
    real_reader = setup_finalize._project_doc_text

    def failing_displaced_reader(path):
        if Path(path) != project_doc:
            raise OSError("simulated displaced read failure")
        return real_reader(path)

    with mock.patch.object(
        setup_finalize, "_project_doc_text", side_effect=failing_displaced_reader
    ):
        try:
            setup_finalize.update_project_doc(
                repo, "AGENTS.md",
                ensure_routing=False, ensure_contract_import=True,
            )
        except OSError as exc:
            assert "displaced read failure" in str(exc)
        else:
            raise AssertionError("displaced read failure must abort")
    assert project_doc.read_text(encoding="utf-8") == "# Original\n"
    assert not list(repo.glob(".AGENTS.md.*.tmp"))


def test_project_doc_rollback_failure_preserves_displaced_original(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_rollback_failure_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text("# Original\n", encoding="utf-8")
    real_exchange = setup_finalize._exchange_project_doc
    exchanges = 0

    def failing_rollback(left, right):
        nonlocal exchanges
        exchanges += 1
        if exchanges == 1:
            return real_exchange(left, right)
        raise OSError("simulated rollback failure")

    real_reader = setup_finalize._project_doc_text

    def mismatched_displaced_reader(path):
        text, info = real_reader(path)
        if Path(path) != project_doc:
            return "# Concurrent original\n", info
        return text, info

    with mock.patch.object(
        setup_finalize, "_exchange_project_doc", side_effect=failing_rollback
    ), mock.patch.object(
        setup_finalize, "_project_doc_text", side_effect=mismatched_displaced_reader
    ):
        try:
            setup_finalize.update_project_doc(
                repo, "AGENTS.md",
                ensure_routing=False, ensure_contract_import=True,
            )
        except RuntimeError as exc:
            assert "original preserved at" in str(exc)
        else:
            raise AssertionError("rollback failure must fail closed")
    preserved = list(repo.glob(".AGENTS.md.*.tmp"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "# Original\n"


def test_project_doc_rollback_preserves_second_concurrent_replacement(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_second_race_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    project_doc = repo / "AGENTS.md"
    project_doc.write_text("# Original\n", encoding="utf-8")
    replaced = False

    def second_writer(*_args):
        nonlocal replaced
        if not replaced:
            replaced = True
            concurrent = repo / "concurrent.md"
            concurrent.write_text("# Second writer\n", encoding="utf-8")
            os.replace(concurrent, project_doc)
        return False

    with mock.patch.object(
        setup_finalize, "_same_project_doc_snapshot", side_effect=second_writer
    ):
        try:
            setup_finalize.update_project_doc(
                repo, "AGENTS.md",
                ensure_routing=False, ensure_contract_import=True,
            )
        except RuntimeError as exc:
            assert "original preserved at" in str(exc)
        else:
            raise AssertionError("a second replacement must be preserved")
    assert project_doc.read_text(encoding="utf-8") == "# Second writer\n"
    preserved = list(repo.glob(".AGENTS.md.*.tmp"))
    assert len(preserved) == 1
    assert preserved[0].read_text(encoding="utf-8") == "# Original\n"


def test_source_git_roots_reject_shell_metacharacters(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_shell_root_test")
    repo = tmp_path / "repo"
    repo.mkdir()
    unsafe = repo / "api;touch-pwned"
    unsafe.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=unsafe, check=True)

    errors = setup_finalize.source_git_root_errors(
        repo, "source_git_roots: ['api;touch-pwned']\n"
    )

    assert errors == ["invalid source_git_roots entry: api;touch-pwned"]


def test_git_backed_source_root_validation_accepts_direct_linked_worktree(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_linked_root_test")
    parent = tmp_path / "parent"
    service_repo = tmp_path / "service-repo"
    for repo in (parent, service_repo):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "setup@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Setup Test"], cwd=repo, check=True)
    (service_repo / "tracked.py").write_text("service\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=service_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "service"], cwd=service_repo, check=True)
    service = parent / "services/front"
    service.parent.mkdir()
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(service), "HEAD"],
        cwd=service_repo,
        check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},services/front"],
        cwd=parent,
        check=True,
    )

    assert setup_finalize.source_git_root_errors(
        parent, "source_git_roots: [services/front]\n"
    ) == []


def test_git_backed_source_root_validation_rejects_non_gitlink_with_stable_code(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_non_gitlink_root_test")
    parent = tmp_path / "parent"
    child = parent / "child"
    parent.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=parent, check=True)
    child.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=child, check=True)

    errors = setup_finalize.source_git_root_errors(
        parent, "source_git_roots: [child]\n"
    )

    assert len(errors) == 1
    assert "[REGISTERED_SOURCE_NOT_DIRECT_GITLINK]" in errors[0]


def test_git_backed_source_root_validation_reports_uninitialized_gitlink(tmp_path):
    setup_finalize = load_setup_finalize("setup_finalize_uninitialized_root_test")
    parent = tmp_path / "parent"
    source = tmp_path / "source"
    for repo in (parent, source):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "setup@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Setup Test"], cwd=repo, check=True)
    (source / "tracked.py").write_text("service\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source, check=True)
    subprocess.run(["git", "commit", "-qm", "service"], cwd=source, check=True)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=source, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},service"],
        cwd=parent, check=True,
    )

    errors = setup_finalize.source_git_root_errors(
        parent, "source_git_roots: [service]\n"
    )
    assert len(errors) == 1
    assert "[REGISTERED_SOURCE_UNINITIALIZED]" in errors[0]
    assert "path=service" in errors[0]
    assert "next_action=Restore the checkout" in errors[0]


def test_setup_ignores_ambient_alternate_git_index(tmp_path, monkeypatch):
    setup_finalize = load_setup_finalize("setup_finalize_alternate_index_test")
    parent = tmp_path / "parent"
    source_repo = tmp_path / "source-repo"
    for repo in (parent, source_repo):
        repo.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.email", "setup@test"], cwd=repo, check=True)
        subprocess.run(["git", "config", "user.name", "Setup Test"], cwd=repo, check=True)
    (source_repo / "tracked.py").write_text("service\n", encoding="utf-8")
    subprocess.run(["git", "add", "tracked.py"], cwd=source_repo, check=True)
    subprocess.run(["git", "commit", "-qm", "service"], cwd=source_repo, check=True)
    (parent / "parent.txt").write_text("parent\n", encoding="utf-8")
    subprocess.run(["git", "add", "parent.txt"], cwd=parent, check=True)
    subprocess.run(["git", "commit", "-qm", "parent"], cwd=parent, check=True)
    service = parent / "service"
    subprocess.run(
        ["git", "worktree", "add", "-q", "--detach", str(service), "HEAD"],
        cwd=source_repo, check=True,
    )
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=service, check=True,
        capture_output=True, text=True,
    ).stdout.strip()
    alternate_index = tmp_path / "alternate-index"
    alt_env = os.environ.copy()
    alt_env["GIT_INDEX_FILE"] = str(alternate_index)
    subprocess.run(["git", "read-tree", "HEAD"], cwd=parent, env=alt_env, check=True)
    subprocess.run(
        ["git", "update-index", "--add", "--cacheinfo", f"160000,{head},service"],
        cwd=parent, env=alt_env, check=True,
    )
    monkeypatch.setenv("GIT_INDEX_FILE", str(alternate_index))

    errors = setup_finalize.source_git_root_errors(
        parent, "source_git_roots: [service]\n"
    )
    assert len(errors) == 1
    assert "[REGISTERED_SOURCE_NOT_DIRECT_GITLINK]" in errors[0]


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
        "doc/harness/reviews/main-reviews.jsonl",
        "doc/harness/runbook_candidates.yaml",
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


def test_local_operational_files_are_rejected_when_already_tracked(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    for index, rel in enumerate(("doc/harness/runbook_candidates.yaml",)):
        repo = make_repo(tmp_path / str(index), manifest=canonical_manifest())
        artifact = repo / rel
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("local runtime state\n", encoding="utf-8")
        subprocess.run(
            ["git", "-C", str(repo), "add", "-f", rel],
            check=True,
        )

        result = run(repo, plugin_root)

        assert result.returncode == 1
        assert f"operational artifact is already tracked: {rel}" in result.stdout


def test_local_writer_paths_match_canonical_operational_ignores():
    def load(name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    setup = load("setup_finalize_contract", SCRIPT)
    runbooks = load("runbook_memory_contract", REPO / "plugin/scripts/runbook_memory.py")

    writer_paths = {runbooks.CANDIDATES_REL}
    assert writer_paths <= set(setup.OPERATIONAL_IGNORES)


def test_gitignore_render_drops_removed_orchestration_state():
    setup = load_setup_finalize("setup_finalize_removed_ignores")
    old = "\n".join(sorted(setup.OBSOLETE_OPERATIONAL_IGNORES)) + "\n# user\n"
    rendered = setup.render_gitignore(old)
    assert "# user" in rendered
    for removed in setup.OBSOLETE_OPERATIONAL_IGNORES:
        assert removed not in rendered


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
        assert _lib.read_manifest_field("type", str(repo)) == "api"
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
    (repo / "doc/harness/runbook_candidates.yaml").symlink_to(outside)

    result = run(repo, plugin_root)

    assert result.returncode == 1
    assert "managed path contains symlink: doc/harness/runbook_candidates.yaml" in result.stdout
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


def test_non_git_control_workspace_finalizes_against_explicit_source_roots(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    manifest = canonical_manifest() + "source_git_roots: [pay-api, pay-webapp]\n"
    repo = make_repo(tmp_path, manifest=manifest)
    shutil.rmtree(repo / ".git")
    for name in ("pay-api", "pay-webapp"):
        child = repo / name
        child.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=child, check=True)

    result = run(repo, plugin_root, "--prepare")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "SETUP_PREPARED" in result.stdout


def test_non_git_control_workspace_rejects_unregistered_or_missing_sources(tmp_path):
    plugin_root = make_plugin_root(tmp_path)
    repo = make_repo(tmp_path, manifest=canonical_manifest())
    shutil.rmtree(repo / ".git")

    result = run(repo, plugin_root, "--prepare")

    assert result.returncode == 1
    assert "requires source_git_roots" in result.stdout
